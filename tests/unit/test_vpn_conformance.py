"""The VPNs' plans in the core (lib/claude_vm/vpn/): every family conforms to the seam, every family's real plans pass
the registry, each rule of the registry bites on a plan built to break it, a refusal leaves nothing else, and the
filter takes Ansible's own values (docs/architecture/custom-code.md §7.11). The registry runs its rules on every call;
this shows they hold and that they bite."""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import typing
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from harness import ANSIBLE_PLAYBOOK, ansible_config

from claude_vm.refs import NextStep, VmRef
from claude_vm.vpn import model, registry
from claude_vm.vpn.family import Family
from claude_vm.vpn.model import FamilyName, MarkChange, MemberPlan, PeerWrite, Said, Tunnel

REPO = Path(__file__).resolve().parents[2]
ROWS = yaml.safe_load((REPO / "inventory/group_vars/all/defaults.yml").read_text())["claude_vpns"]
ALPHA, BETA = {"name": "alpha", "vmid": 4013}, {"name": "beta", "vmid": 4014}


def given(*members, **over):
    return {"phase": "configure", "tag": "claude-on-proxmox", "members": list(members)} | over


SEEN = {"vm": ALPHA, "login_seen": True}
RECORDED = {"vm": BETA, "login_seen": True, "marks": ["tailscale"]}


def conforms(module: object, protocol: type) -> list[str]:
    """The members a type checker would refuse: missing, or another shape of parameters or return."""
    members = (
        typing.get_protocol_members(protocol)
        if hasattr(typing, "get_protocol_members")
        else protocol.__protocol_attrs__
    )

    def shape(fn, drop):
        return [(p.name, p.kind) for p in list(inspect.signature(fn).parameters.values())[drop:]]

    return [
        m
        for m in sorted(members)
        if not callable(getattr(module, m, None)) or shape(getattr(protocol, m), 1) != shape(getattr(module, m), 0)
    ]


def test_every_family_conforms_and_every_row_parses():
    assert set(registry.FAMILIES) == set(typing.get_args(FamilyName))
    assert [name for name, module in registry.FAMILIES.items() if conforms(module, Family)] == []
    assert {row.family for row in model.vpns_from(ROWS).values()} <= set(registry.FAMILIES)


# ------------------------------------------------------------------- Tailscale ---
def test_tailscale_marks_each_vm_that_has_had_a_login_and_has_no_record_yet():
    plan = registry.vpn_member_plan(
        "tailscale", ROWS, given(SEEN, RECORDED, {"vm": {"name": "nologin", "vmid": 4015}, "login_seen": False})
    )
    assert plan["kind"] == "member_plan" and not plan["refusals"]
    (mark,) = plan["marks"]
    assert mark["vm"] == ALPHA and mark["add"] == ["tailscale"] and mark["remove"] == []
    assert mark["matter"] == "tailscale_record"
    assert mark["unwritten"] == (
        "alpha: Proxmox could not record that it joined Tailscale ({reason}), so vault_tailscale_auth_key could be"
        " sent to it again if it is logged out. The next just deploy alpha --only configure records it."
    )
    assert not any(plan[name] for name in ("peers", "tunnels", "aliases", "report", "next_steps"))


def test_tailscale_plans_nothing_at_a_release_or_for_a_vm_leaving():
    assert registry.vpn_member_plan("tailscale", ROWS, given(SEEN, phase="release"))["marks"] == []
    assert registry.vpn_member_plan("tailscale", ROWS, given(SEEN | {"leaving": True}))["marks"] == []


def test_a_vpn_that_is_no_row_is_refused_naming_the_rows():
    with pytest.raises(ValueError, match=r"^openvpn is not a VPN this project joins VMs to: tailscale"):
        registry.vpn_member_plan("openvpn", ROWS, given(SEEN))


# ------------------------------------------------------------ the core's rules ---
VPN = model.vpns_from(ROWS)["tailscale"]
INPUTS = model.inputs_from(given(SEEN, RECORDED))
A, B = VmRef(**ALPHA), VmRef(**BETA)
OK = MarkChange(vm=A, add=("tailscale",), unwritten="alpha: ({reason})", matter="tailscale_record")
SLIPS = {
    "another VPN's plan": MemberPlan(vpn="wireguard"),
    "a VM it was not given": MemberPlan(vpn="tailscale", marks=(replace(OK, vm=VmRef(name="gamma", vmid=4016)),)),
    "a VM by its name under another VM ID": MemberPlan(
        vpn="tailscale", marks=(replace(OK, vm=VmRef(name="alpha", vmid=4099)),)
    ),
    "a VM's marks twice": MemberPlan(vpn="tailscale", marks=(OK, OK)),
    "a mark not its own": MemberPlan(vpn="tailscale", marks=(replace(OK, add=("wg-198.51.100.130",)),)),
    "the record taken off": MemberPlan(vpn="tailscale", marks=(replace(OK, vm=B, add=(), remove=("tailscale",)),)),
    "a matter not its own": MemberPlan(vpn="tailscale", marks=(replace(OK, matter="editor"),)),
    "a peer": MemberPlan(vpn="tailscale", peers=(PeerWrite(kind="add", name="claude-on-proxmox-alpha", vm=A),)),
    "a tunnel": MemberPlan(
        vpn="tailscale",
        tunnels=(
            Tunnel(vm=A, address="198.51.100.130/24", hub_public_key="", endpoint="", allowed_ips=(), keepalive=0),
        ),
    ),
    "a line of a VM it was not given": MemberPlan(vpn="tailscale", report=(Said(vm="gamma", text="hello"),)),
    "NEXT STEPS of a VM it was not given": MemberPlan(
        vpn="tailscale",
        next_steps=(NextStep(vm=VmRef(name="gamma", vmid=4016), matter="tailscale_record", action=None),),
    ),
    "NEXT STEPS under a matter not its own": MemberPlan(
        vpn="tailscale", next_steps=(NextStep(vm=A, matter="editor", action=None),)
    ),
}


@pytest.mark.parametrize("slip", sorted(SLIPS))
def test_the_core_refuses_a_family_s_slip(slip):
    with pytest.raises(ValueError):
        registry.checked(VPN, INPUTS, SLIPS[slip])


def test_a_refusal_leaves_nothing_but_itself():
    plan = registry.checked(VPN, INPUTS, MemberPlan(vpn="tailscale", refusals=("no",), marks=(OK,)))
    assert plan == MemberPlan(vpn="tailscale", refusals=("no",))


@pytest.mark.parametrize(
    "bad",
    [
        {"tag": "Claude-On-Proxmox"},  # as Proxmox stores it: lowercased
        {"members": [SEEN, SEEN]},  # a VM twice
        {"members": [SEEN | {"marks": ["tailscale;keepme"]}]},  # not a mark
        {"vm_prefix": "198.51.100.129/25"},  # host bits set
        {"phase": "prune"},
        {"members": [{"vm": "alpha"}]},  # a bare name is no VmRef
    ],
)
def test_inputs_are_held_to_their_types(bad):
    with pytest.raises((ValueError, TypeError)):
        model.inputs_from(given(SEEN) | bad)


def test_an_unwritten_mark_has_a_place_for_proxmox_s_reason():
    with pytest.raises(ValueError, match="no place for Proxmox's reason"):
        MarkChange(vm=A, add=("tailscale",), unwritten="alpha: could not", matter="tailscale_record")


def test_the_filter_takes_ansible_s_own_values(tmp_path):
    # Lazy and tagged values, as tasks/vpn_members.yml hands them.
    inputs = given({"vm": {"name": "alpha", "vmid": "{{ listed.vmid }}"}, "login_seen": "{{ seen }}"})
    play = {
        "hosts": "localhost",
        "gather_facts": False,
        "vars": {"rows": ROWS, "inputs": inputs, "seen": True, "listed": {"vmid": 4013}},
        "tasks": [
            {"ansible.builtin.set_fact": {"kept": "{{ 'tailscale' | vpn_member_plan(rows, inputs) }}"}},
            {"name": "Say", "ansible.builtin.debug": {"msg": "{{ [kept.kind] + kept.marks | map(attribute='add') }}"}},
        ],
    }
    (tmp_path / "plan.yml").write_text(yaml.safe_dump([play]))
    (tmp_path / "hosts").write_text(f"localhost ansible_connection=local ansible_python_interpreter={sys.executable}\n")
    env = {
        "PATH": "/usr/bin:/bin",
        "ANSIBLE_CONFIG": ansible_config(tmp_path),
        "ANSIBLE_FILTER_PLUGINS": str(REPO / "filter_plugins"),
        "ANSIBLE_STDOUT_CALLBACK": "ansible.builtin.json",
    } | {name: os.environ[name] for name in ("ANSIBLE_COLLECTIONS_PATH",) if name in os.environ}
    run = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", str(tmp_path / "hosts"), str(tmp_path / "plan.yml")],
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
    said = {task["task"]["name"]: task["hosts"]["localhost"].get("msg") for task in tasks}
    assert said["Say"] == ["member_plan", ["tailscale"]]
