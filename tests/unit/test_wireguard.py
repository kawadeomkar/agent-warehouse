"""The wireguard role and its place in the playbooks (docs/architecture/roles.md §5.9): the VM's private key is never
read by Ansible and never leaves the VM; the role touches only its own names; configure runs its key before the
members' plan and its link after it, each in a block whose rescue records a failure; and destroy says the peer each
deleted VM leaves. The role's run on a VM is its Molecule scenario's (roles/wireguard/molecule/default/), which makes
a WireGuard server beside it and waits for the handshake; the plan is test_vpn_conformance.py's."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template
from harness import ANSIBLE_PLAYBOOK, _every_task, ansible_config

from claude_vm.vpn import model

REPO = Path(__file__).resolve().parents[2]
ROLE = REPO / "roles" / "wireguard"
TASKS = sorted((ROLE / "tasks").glob("*.yml"))
VARS = yaml.safe_load((ROLE / "vars" / "main.yml").read_text())
DEFAULTS = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())


def _tasks(path: Path) -> list[dict]:
    """Every task of a file, blocks opened."""
    return list(_every_task(yaml.safe_load(path.read_text())))


def test_the_private_key_is_never_read_by_ansible():
    # Only wg reads it, on the VM: genkey writes it, pubkey prints the public half. No slurp, fetch, copy from it,
    # lookup or template of it, and no task prints it.
    for path in TASKS:
        for task in _tasks(path):
            text = json.dumps(task)
            if "wireguard_key_path" not in text:
                continue
            module = next(key for key in task if key.startswith("ansible.builtin."))
            assert module in {
                "ansible.builtin.command",
                "ansible.builtin.file",
                "ansible.builtin.copy",
            }, (path.name, task["name"])
            if module == "ansible.builtin.copy":
                # The netdev names the key's path for networkd, and nothing of the key itself.
                assert task["ansible.builtin.copy"]["dest"] == "{{ wireguard_netdev_path }}", task["name"]
                assert "src" not in task["ansible.builtin.copy"]
            if module == "ansible.builtin.command":
                argv = task["ansible.builtin.command"]["argv"]
                assert argv[:2] == ["sh", "-c"] and argv[-1] == "{{ wireguard_key_path }}", task["name"]
                assert ("wg genkey" in argv[2] and "umask 077" in argv[2]) or argv[2] == 'wg pubkey < "$1"'


def test_the_key_is_root_s_and_systemd_network_s_alone():
    (made,) = [task for task in _tasks(ROLE / "tasks" / "key.yml") if task["name"] == "key | Make the VM's key, once"]
    script = made["ansible.builtin.command"]["argv"][2]
    assert script.index("umask 077") < script.index("wg genkey") < script.index("chgrp systemd-network")
    assert "chmod 0640" in script and script.endswith('mv "$1.new" "$1"')
    (kept,) = [
        task
        for task in _tasks(ROLE / "tasks" / "key.yml")
        if task["name"] == "key | Keep the key readable by root and systemd-networkd alone"
    ]
    assert kept["ansible.builtin.file"] | {"path": None} == {
        "path": None,
        "owner": "root",
        "group": "systemd-network",
        "mode": "0640",
    }


def test_the_role_touches_only_names_of_its_own_and_has_no_handlers():
    assert not (ROLE / "handlers").exists()
    assert VARS["wireguard_link"] == "claude-wg"
    for name in ("wireguard_key_path", "wireguard_netdev_path", "wireguard_network_path"):
        assert "claude-wg" in VARS[name], name
    every = " ".join(path.read_text() for path in TASKS)
    assert "wg0" not in every and "/etc/wireguard" not in every and "wg-quick" not in every
    # The package goes only where the marker says the role installed it.
    (removal,) = [task for task in _tasks(ROLE / "tasks" / "absent.yml") if "ansible.builtin.apt" in task]
    assert "wireguard_marker_installed" in removal["when"]


def test_the_inventory_hands_the_role_what_the_vpn_setting_decided():
    assert DEFAULTS["wireguard_wanted"] == "{{ claude_vpn_key == 'wireguard' }}"
    assert DEFAULTS["claude_vpns"]["wireguard"]["role"] == "wireguard"


def test_configure_makes_the_key_before_the_plan_and_the_link_after_it():
    plays = yaml.safe_load((REPO / "playbooks" / "configure.yml").read_text())
    names = [play.get("name") for play in plays]
    configure, plan, join, alias = (
        names.index("Configure Claude development VMs"),
        names.index("Plan each VM's place on the WireGuard network"),
        names.index("Join each VM to the WireGuard network"),
        names.index("Keep an SSH alias for each configured VM"),
    )
    assert configure < plan < join < alias
    (block,) = [
        task
        for task in plays[configure]["tasks"]
        if task.get("name") == "Make the VM's WireGuard key, or take WireGuard off"
    ]
    assert block["tags"] == ["wireguard"] and "when" not in block
    marker, key, off = block["block"]
    assert marker["when"] == "not (wireguard_wanted | bool)"
    assert key["ansible.builtin.import_role"] == {"name": "wireguard", "tasks_from": "key"}
    assert key["when"] == "wireguard_wanted | bool"
    assert off["ansible.builtin.import_role"] == {"name": "wireguard"}
    assert off["when"] == "not (wireguard_wanted | bool) and claude_vm_wireguard_marker.stat.exists | default(false)"
    assert any("claude_vm_configure_failures" in json.dumps(task) for task in block["rescue"])
    # The plan in a block whose rescue drops it and records the failure on every VM: a refusal or a hub that cannot
    # be read never costs a VM its alias, since the alias play runs on localhost too.
    (planned,) = plays[plan]["tasks"]
    assert "when" not in planned
    planning = planned["block"]
    assert planning[0]["ansible.builtin.import_tasks"] == "tasks/vpn_members.yml"
    assert planning[0]["vars"]["claude_vpn_planned"] == "wireguard"
    assert planning[1]["ansible.builtin.import_tasks"] == "tasks/vpn_apply.yml"
    dropped, recorded, said = planned["rescue"]
    assert "'wireguard': {}" in dropped["ansible.builtin.set_fact"]["claude_vpn_plans"]
    assert "claude_vm_configure_failures" in recorded["ansible.builtin.set_fact"]
    assert recorded["delegate_facts"] is True and "groups['claude_vms']" in recorded["loop"]
    assert "ansible.builtin.debug" in said
    (joining,) = plays[join]["tasks"]
    (link,) = joining["block"]
    assert link["ansible.builtin.import_role"] == {"name": "wireguard"} and link["when"] == "wireguard_wanted | bool"
    assert "claude_vpn_plans" in link["vars"]["wireguard_tunnel"]
    assert any("claude_vm_configure_failures" in json.dumps(task) for task in joining["rescue"])
    assert plays[join]["tags"] == plays[plan]["tags"] == ["wireguard"]


def test_destroy_says_the_peer_each_deleted_vm_leaves_whatever_the_vpn_is():
    plays = yaml.safe_load((REPO / "playbooks" / "destroy.yml").read_text())
    (play,) = [
        play for play in plays if play.get("name") == "Say the WireGuard peers each deleted VM leaves on its server"
    ]
    (block,) = play["tasks"]
    plan, said = block["block"]
    assert plan["vars"]["claude_vpn_planned"] == "wireguard" and plan["vars"]["claude_vpn_phase"] == "release"
    assert "claude_vm_deleted" in block["vars"]["claude_vm_wireguard_gone"]
    assert "when" not in block and "when" not in plan and "when" not in said
    assert said["ansible.builtin.import_tasks"] == "tasks/vpn_apply.yml"
    # A hub that cannot be read, or a plan that fails, is said for each deleted VM with an address, and kept for NEXT
    # STEPS: the play after this one must still run, to stop again what the run started and forget the aliases.
    report, record = block["rescue"]
    assert "wg-" in report["when"] and "ansible.builtin.debug" in report
    assert record["ansible.builtin.set_stats"]["data"]["vpn_next"].count("'wireguard_peer'") == 1
    names = [play.get("name") for play in plays]
    assert names.index(play["name"]) == names.index("Forget the SSH alias of each deleted VM") - 1


def test_the_plan_s_inputs_are_the_core_s_fields():
    # What vpn_members.yml hands vpn_member_plan is read by model.inputs_from as MemberInputs and its Hub: a key at
    # the wrong level is a TypeError there, or - where both have defaults - an input silently lost, as the addresses
    # other VMs hold (taken) once were, which gave two VMs one address.
    tasks = _tasks(REPO / "playbooks" / "tasks" / "vpn_members.yml")
    (plan,) = [task for task in tasks if task.get("name") == "vpn_members | Plan each VM's place on the network"]
    given = plan["vars"]["claude_vpn_wireguard"]
    assert {"hub", "vm_prefix", "keepalive", "taken"} <= set(given) <= set(model.MemberInputs.__dataclass_fields__)
    hubs = [given["hub"]] if isinstance(given["hub"], dict) else []
    hubs += [
        value for name, value in plan["vars"].items() if name.startswith("claude_vpn_hub") and isinstance(value, dict)
    ]
    assert hubs
    for hub in hubs:
        assert set(hub) <= set(model.Hub.__dataclass_fields__), sorted(set(hub) - set(model.Hub.__dataclass_fields__))


def test_the_plan_gives_no_vm_an_address_another_vm_holds(tmp_path):
    # vpn_members.yml itself, with the inventory's defaults: gamma, not among the VMs planned, holds .129 in
    # Proxmox's listing, so alpha - planned alone, as `just deploy alpha` plans it - is given .130.
    key = base64.b64encode(bytes([2] * 32)).decode()
    given = {
        "vm_name": ["alpha"],
        "vpn": "wireguard",
        "wireguard_hub_network": "198.51.100.0/24",
        "wireguard_vm_prefix": "198.51.100.128/25",
        "wireguard_hub_public_key": base64.b64encode(bytes([1] * 32)).decode(),
        "wireguard_hub_endpoint": "vpn.example.net:51820",
        "claude_vm_all": {
            "json": {
                "data": [
                    {
                        "vmid": 102,
                        "name": "gamma",
                        "type": "qemu",
                        "tags": "claude-on-proxmox;claude-on-proxmox-wg-198.51.100.129",
                    },
                    {"vmid": 101, "name": "alpha", "type": "qemu", "tags": "claude-on-proxmox"},
                ]
            }
        },
    }
    play = {
        "hosts": "localhost",
        "gather_facts": False,
        "vars_files": [str(REPO / "inventory" / "group_vars" / "all" / "defaults.yml")],
        "tasks": [
            {
                "name": "Add alpha as discovery would",
                "ansible.builtin.add_host": {
                    "name": "alpha",
                    "claude_vm_listed_id": 101,
                    "claude_vm_marks": [],
                    "wireguard_public_key": key,
                },
            },
            {
                "ansible.builtin.import_tasks": str(REPO / "playbooks" / "tasks" / "vpn_members.yml"),
                "vars": {
                    "claude_vpn_planned": "wireguard",
                    "claude_vpn_hosts": ["alpha"],
                    "claude_vpn_phase": "configure",
                },
            },
            {
                "name": "Say",
                "ansible.builtin.debug": {"msg": "{{ claude_vpn_plan.tunnels | map(attribute='address') }}"},
            },
        ],
    }
    (tmp_path / "plan.yml").write_text(yaml.safe_dump([play]))
    (tmp_path / "hosts").write_text(f"localhost ansible_connection=local ansible_python_interpreter={sys.executable}\n")
    env = {
        "PATH": "/usr/bin:/bin",
        "ANSIBLE_CONFIG": ansible_config(tmp_path),
        "ANSIBLE_FILTER_PLUGINS": str(REPO / "filter_plugins"),
        "ANSIBLE_LIBRARY": str(REPO / "playbooks" / "library"),
        "ANSIBLE_STDOUT_CALLBACK": "ansible.builtin.json",
    } | {name: os.environ[name] for name in ("ANSIBLE_COLLECTIONS_PATH",) if name in os.environ}
    run = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", str(tmp_path / "hosts"), "-e", json.dumps(given), str(tmp_path / "plan.yml")],
        env=env,
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    tasks = json.loads(run.stdout)["plays"][0]["tasks"]
    said = {task["task"]["name"]: task["hosts"].get("localhost", {}).get("msg") for task in tasks}
    assert said["Say"] == ["198.51.100.130/24"]


@pytest.mark.parametrize(
    ("found", "kept"),
    [
        (["claude-on-proxmox", "claude-on-proxmox-wg-198.51.100.129"], ["claude-on-proxmox-wg-198.51.100.129"]),
        (
            ["claude-on-proxmox", "claude-on-proxmox-tailscale", "claude-on-proxmox-wg-198.51.100.130", "mine"],
            ["claude-on-proxmox-tailscale", "claude-on-proxmox-wg-198.51.100.130"],
        ),
        (["claude-on-proxmox", "other-wg-198.51.100.129", "claude-on-proxmoxxwg-1.2.3.4"], []),
    ],
)
def test_proxmox_vm_keeps_the_wireguard_address_whenever_it_replaces_a_vm_s_tags(found, kept):
    # A repair or proxmox_vm_force_update replaces the tag list: without the address there, the next configure gives
    # the VM another, and its old one to whichever VM asks next (test_tailscale.py holds every write to the kept tags).
    role_vars = yaml.safe_load((REPO / "roles" / "proxmox_vm" / "vars" / "main.yml").read_text())
    variables = {
        "proxmox_vm_sent_tags": ["claude-on-proxmox"],
        "proxmox_vm_found_tags": found,
        **{name: trust_as_template(role_vars[name]) for name in ("proxmox_vm_tailscale_tag", "proxmox_vm_kept_tags")},
    }
    assert Templar(loader=DataLoader(), variables=variables).template(variables["proxmox_vm_kept_tags"]) == kept


def test_under_tunnel_reach_the_vm_answers_from_its_tunnel_address_through_the_tunnel():
    (network,) = [
        task
        for task in _tasks(ROLE / "tasks" / "configure.yml")
        if task.get("name") == "configure | Write the link's network"
    ]
    text = network["ansible.builtin.copy"]["content"]
    rule = text.index("[RoutingPolicyRule]")
    assert text.index("{% if wireguard_tunnel.reach | default('lan') == 'tunnel' %}") < rule < text.index("{% endif %}")
    assert "From={{ wireguard_tunnel.address | split('/') | first }}/32" in text
    assert text.count("Table={{ wireguard_route_table }}") == 2 and "Destination=0.0.0.0/0" in text
    assert VARS["wireguard_route_table"] not in (253, 254, 255)  # default, main, local


def test_every_alias_writer_uses_the_tunnel_address_under_tunnel_reach():
    for playbook in ("playbooks/configure.yml", "playbooks/ssh_config.yml", "playbooks/code.yml"):
        assert "vpn_alias_address(" in (REPO / playbook).read_text(), playbook
