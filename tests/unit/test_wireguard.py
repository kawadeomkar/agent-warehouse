"""The wireguard role and its place in the playbooks (docs/architecture/roles.md §5.9): the VM's private key is never
read by Ansible and never leaves the VM; the role touches only its own names; configure runs its key before the
members' plan and its link after it, each in a block whose rescue records a failure; and destroy says the peer each
deleted VM leaves. The role's run on a VM is its Molecule scenario's (roles/wireguard/molecule/default/), which makes
a WireGuard server beside it and waits for the handshake; the plan is test_vpn_conformance.py's."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template
from harness import _every_task, _messages, _pve, _run, fake_fleet
from test_status import status_root

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


def test_a_new_key_makes_the_link_again():
    # networkd reads PrivateKeyFile= only when it makes the link, and the netdev - whose change alone makes the link
    # again - names the file, not the key: a key made anew (a clone's, deleted as its line says) never reached the
    # running link. The public key, only ever a key's shape, is in the netdev for that.
    (netdev,) = [
        task
        for task in _tasks(ROLE / "tasks" / "configure.yml")
        if task.get("name") == "configure | Write the link's netdev"
    ]
    text = netdev["ansible.builtin.copy"]["content"]
    assert (
        "# {{ wireguard_public_key | default('') | regex_search('^[A-Za-z0-9+/]{43}=$') | default('', true) }}" in text
    )
    assert text.index("wireguard_public_key") < text.index("PrivateKeyFile=")
    (remade,) = [
        task
        for task in _tasks(ROLE / "tasks" / "configure.yml")
        if task.get("name") == "configure | Make the link again where its netdev changed"
    ]
    assert remade["when"] == "wireguard_netdev is changed"


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


def test_every_playbook_read_of_wireguard_wanted_has_a_default():
    # The inventory's defaults set it; a run without them - Vagrant's, whose inventory loads no group_vars - fails
    # on the name: configure's first WireGuard task failed every just vagrant.
    for path in sorted((REPO / "playbooks").glob("*.yml")) + [REPO / "deploy.yml"]:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            code = line.split("#", 1)[0]
            for at in [i for i in range(len(code)) if code.startswith("wireguard_wanted", i)]:
                assert code[at:].startswith("wireguard_wanted | default(false)"), f"{path.name}:{number}"
    assert "tailscale_wanted | default(" in (REPO / "playbooks" / "status.yml").read_text()


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
    assert marker["when"] == "not (wireguard_wanted | default(false) | bool)"
    assert key["ansible.builtin.import_role"] == {"name": "wireguard", "tasks_from": "key"}
    assert key["when"] == "wireguard_wanted | default(false) | bool"
    assert off["ansible.builtin.import_role"] == {"name": "wireguard"}
    assert (
        off["when"]
        == "not (wireguard_wanted | default(false) | bool) and claude_vm_wireguard_marker.stat.exists | default(false)"
    )
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
    assert (
        link["ansible.builtin.import_role"] == {"name": "wireguard"}
        and link["when"] == "wireguard_wanted | default(false) | bool"
    )
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


def test_a_plan_that_fails_is_dropped_and_raised_on_every_vm_at_the_end(tmp_path):
    # configure.yml's plan play, as it is, with the inventory's defaults: an OPNsense hub whose router cannot be read
    # (none in the inventory) fails the plan. The rescue drops it - the play after it changes no VM's link - and
    # records the failure on each VM, which the last play raises: the alias play between them still runs.
    plays = yaml.safe_load((REPO / "playbooks" / "configure.yml").read_text())
    (plan,) = [play for play in plays if play.get("name") == "Plan each VM's place on the WireGuard network"]
    plan = json.loads(json.dumps(plan).replace('"tasks/', f'"{REPO / "playbooks" / "tasks"}/'))
    plan = {key: value for key, value in plan.items() if key != "module_defaults"}
    plan["vars_files"] = [str(REPO / "inventory" / "group_vars" / "all" / "defaults.yml")]
    said = {
        "name": "Say what is left",
        "hosts": "claude_vms:localhost",
        "gather_facts": False,
        "tasks": [
            {
                "name": "Say the failures",
                "ansible.builtin.debug": {
                    "msg": "{{ claude_vm_configure_failures | default(hostvars['localhost'].claude_vpn_plans) }}"
                },
            }
        ],
    }
    (tmp_path / "plan.yml").write_text(yaml.safe_dump([plan, said]))
    key = base64.b64encode(bytes([2] * 32)).decode()
    hosts = {
        "alpha": {
            "ansible_connection": "local",
            "ansible_python_interpreter": sys.executable,
            "claude_vm_listed_id": 4013,
            "claude_vm_marks": [],
            "wireguard_public_key": key,
        }
    }
    extra = {"vpn": "wireguard", "wireguard_hub": "opnsense", "wireguard_hub_instance": "home"}
    report = _run(tmp_path, tmp_path / "plan.yml", hosts, "-e", json.dumps(extra))
    assert report["rc"] == 0, report["stderr"]
    messages = _messages(report, "Say the failures")
    (failure,) = messages["alpha"]
    assert failure.startswith('alpha: WireGuard failed at "vpn_members | Read the hub\'s instance and its peers":')
    assert failure.endswith("Retry it with: just deploy alpha --tags wireguard")
    assert messages["localhost"] == {"wireguard": {}}


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


def test_a_single_name_deploy_gives_no_vm_an_address_another_vm_holds(tmp_path):
    # configure.yml's plan play as `just deploy alpha` runs it: no listing before it (provision lists the cluster only
    # for several new VMs, and deploy configures with discovery off). gamma, not among the VMs planned, holds .129 in
    # Proxmox alone; the plan lists the cluster itself, so alpha is given .130, and Proxmox records it.
    for base in fake_fleet(tmp_path):
        _pve(base, "PUT", "nodes/pve/qemu/9012/config", tags="claude-on-proxmox;claude-on-proxmox-wg-198.51.100.129")
        settings = {
            "vpn": "wireguard",
            "wireguard_hub_network": "198.51.100.0/24",
            "wireguard_vm_prefix": "198.51.100.128/25",
            "wireguard_hub_public_key": base64.b64encode(bytes([1] * 32)).decode(),
            "wireguard_hub_endpoint": "vpn.example.net:51820",
        }
        root, _ = status_root(tmp_path, base, settings)
        plays = yaml.safe_load((REPO / "playbooks" / "configure.yml").read_text())
        (plan,) = [play for play in plays if play.get("name") == "Plan each VM's place on the WireGuard network"]
        said = {
            "name": "Say the plan",
            "hosts": "localhost",
            "gather_facts": False,
            "tasks": [{"name": "Say", "ansible.builtin.debug": {"msg": "{{ claude_vpn_plans.wireguard.tunnels }}"}}],
        }
        playbook = root / "plan.yml"
        playbook.write_text(yaml.safe_dump([plan, said]).replace("tasks/vpn_", f"{REPO / 'playbooks' / 'tasks'}/vpn_"))
        key = base64.b64encode(bytes([2] * 32)).decode()
        alpha = {
            "ansible_connection": "local",
            "ansible_python_interpreter": sys.executable,
            "claude_vm_listed_id": 9010,
            "claude_vm_marks": [],
            "wireguard_public_key": key,
        }
        report = _run(root, playbook, {"alpha": alpha}, "-e", json.dumps({"vm_name": ["alpha"]}), timeout=180)
        assert report["rc"] == 0, report["stderr"]
        (tunnel,) = _messages(report, "Say")["localhost"]
        assert tunnel["address"] == "198.51.100.130/24"
        tags = _pve(base, "GET", "nodes/pve/qemu/9010/config")["data"]["tags"]
        assert "claude-on-proxmox-wg-198.51.100.130" in tags.replace(",", ";").split(";")


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


def test_the_vm_answers_from_its_tunnel_address_through_the_tunnel_and_from_its_lan_address_on_the_lan():
    # No prefix route in the main table: a reply to a device of the tunnel's that reached the VM at its LAN address
    # would go into the tunnel from an address the hub's peer for the VM does not hold, and be dropped. What reached
    # the tunnel address goes back through a rule and a table, in both reaches: the hub's network under lan, every
    # address under tunnel (the plan's AllowedIPs, which the core holds to the reach).
    (network,) = [
        task
        for task in _tasks(ROLE / "tasks" / "configure.yml")
        if task.get("name") == "configure | Write the link's network"
    ]
    text = network["ansible.builtin.copy"]["content"]
    assert "[Network]" not in text and "{% if" not in text
    assert "[Address]\nAddress={{ wireguard_tunnel.address }}" in text and "AddPrefixRoute=no" in text
    assert "[RoutingPolicyRule]\nFrom={{ wireguard_tunnel.address | split('/') | first }}/32" in text
    assert "[Route]\nDestination={{ wireguard_tunnel.allowed_ips | first }}" in text
    assert text.count("Table={{ wireguard_route_table }}") == 2
    # Not wg-quick's table, which a full tunnel of the user's own takes, nor Tailscale's, nor the kernel's own.
    assert VARS["wireguard_route_table"] not in (51820, 52, 253, 254, 255)


def test_every_alias_writer_uses_the_tunnel_address_under_tunnel_reach():
    for playbook in ("playbooks/configure.yml", "playbooks/ssh_config.yml", "playbooks/code.yml"):
        text = (REPO / playbook).read_text()
        # Held to the VMs' range, as each writer passes it.
        assert "vpn_alias_address(" in text and "wireguard_vm_prefix | default(''))" in text, playbook


def test_configure_never_takes_the_marks_for_a_vm_this_run_s_plan_gave_a_tunnel():
    # Its address may have changed this run: the marks read before the plan would name the old one.
    plays = yaml.safe_load((REPO / "playbooks" / "configure.yml").read_text())
    (alias,) = [play for play in plays if play.get("name") == "Keep an SSH alias for each configured VM"]
    collect = alias["tasks"][0]
    assert "'tunnels'" in collect["vars"]["claude_ssh_planned_tunnel"]
    address = collect["ansible.builtin.set_fact"]["claude_ssh_configured"]
    assert address.index("claude_ssh_planned_alias") < address.index("claude_ssh_planned_tunnel | bool")
    assert address.index("claude_ssh_planned_tunnel | bool") < address.index("vpn_alias_address(")
