"""The VPNs' plans in the core (lib/claude_vm/vpn/): every family conforms to the seam, every family's real plans pass
the registry, each rule of the registry bites on a plan built to break it, a refusal leaves nothing else, and the
filter takes Ansible's own values (docs/architecture/custom-code.md §7.11). The registry runs its rules on every call;
this shows they hold and that they bite."""

from __future__ import annotations

import base64
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

from claude_vm import proxmox
from claude_vm.refs import NextStep, VmRef
from claude_vm.vpn import common, model, registry
from claude_vm.vpn.family import Family
from claude_vm.vpn.model import AliasAddress, FamilyName, MarkChange, MemberPlan, PeerWrite, Said, Tunnel

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


# ------------------------------------------------------------------- WireGuard ---
# Keys of no real peer: 32 bytes each, in base64 - the hub's and two VMs'.
HUB_KEY, KEY_A, KEY_B = (base64.b64encode(bytes([n] * 32)).decode() for n in (1, 2, 3))
HUB = {"kind": "manual", "network": "198.51.100.0/24", "public_key": HUB_KEY, "endpoint": "vpn.example.net:51820"}
WG = {"hub": HUB, "vm_prefix": "198.51.100.128/25"}
WG_A = {"vm": ALPHA, "public_key": KEY_A, "lan": "192.0.2.0/24"}
WG_B = {"vm": BETA, "public_key": KEY_B, "lan": "192.0.2.0/24"}


def wg(*members, **over):
    return given(*members, **(WG | over))


def test_wireguard_gives_each_vm_the_lowest_free_address_of_the_range_and_marks_it():
    plan = registry.vpn_member_plan("wireguard", ROWS, wg(WG_A, WG_B, taken=["198.51.100.129"]))
    assert not plan["refusals"]
    assert [(t["vm"]["name"], t["address"]) for t in plan["tunnels"]] == [
        ("alpha", "198.51.100.130/24"),
        ("beta", "198.51.100.131/24"),
    ]
    assert [(m["vm"]["name"], m["add"], m["matter"]) for m in plan["marks"]] == [
        ("alpha", ["wg-198.51.100.130"], "wireguard_address"),
        ("beta", ["wg-198.51.100.131"], "wireguard_address"),
    ]
    assert plan["tunnels"][0] | {"vm": None} == {
        "vm": None,
        "address": "198.51.100.130/24",
        "hub_public_key": HUB_KEY,
        "endpoint": "vpn.example.net:51820",
        "allowed_ips": ["198.51.100.0/24"],
        "keepalive": 25,
        "reach": "lan",
    }
    assert [(p["kind"], p["name"], p["public_key"], p["address"]) for p in plan["peers"]] == [
        ("add", "claude-on-proxmox-alpha", KEY_A, "198.51.100.130/32"),
        ("add", "claude-on-proxmox-beta", KEY_B, "198.51.100.131/32"),
    ]
    # The peer to add, said and kept for NEXT STEPS until the VM reaches its hub.
    assert "PublicKey = " + KEY_A in plan["report"][0]["text"]
    assert [s["matter"] for s in plan["next_steps"]] == ["wireguard_peer", "wireguard_peer"]
    assert KEY_A in plan["next_steps"][0]["text"]


def test_a_vm_keeps_its_marked_address_and_a_vm_that_reached_its_hub_has_nothing_left():
    kept = WG_A | {"marks": ["tailscale", "wg-198.51.100.200-reached"], "handshake": True}
    plan = registry.vpn_member_plan("wireguard", ROWS, wg(kept))
    assert plan["tunnels"][0]["address"] == "198.51.100.200/24" and plan["marks"] == []
    assert plan["next_steps"][0]["text"] == "" and plan["next_steps"][0]["action"] is None


def test_an_address_two_vms_are_marked_with_stays_the_lower_vm_id_s_and_one_outside_the_range_is_given_again():
    plan = registry.vpn_member_plan(
        "wireguard",
        ROWS,
        wg(WG_B | {"marks": ["wg-198.51.100.129"]}, WG_A | {"marks": ["wg-198.51.100.129"]}),
    )
    given_to = {t["vm"]["name"]: t["address"] for t in plan["tunnels"]}
    assert given_to == {"alpha": "198.51.100.129/24", "beta": "198.51.100.130/24"}
    (change,) = plan["marks"]
    assert change["vm"] == BETA and change["add"] == ["wg-198.51.100.130"] and change["remove"] == ["wg-198.51.100.129"]
    moved = registry.vpn_member_plan("wireguard", ROWS, wg(WG_A | {"marks": ["wg-203.0.113.9"]}))
    assert moved["marks"][0]["remove"] == ["wg-203.0.113.9"] and moved["marks"][0]["add"] == ["wg-198.51.100.129"]


def test_a_vm_whose_key_was_not_read_is_said_and_not_joined():
    plan = registry.vpn_member_plan("wireguard", ROWS, wg(WG_A | {"public_key": ""}))
    assert not plan["tunnels"] and not plan["peers"] and not plan["marks"]
    assert "its WireGuard key was not read" in plan["report"][0]["text"]


@pytest.mark.parametrize(
    ("over", "said"),
    [
        ({"vm_prefix": ""}, "wireguard_vm_prefix is not an IPv4 network"),
        ({"vm_prefix": "203.0.113.0/25"}, "is not inside wireguard_hub_network"),
        ({"hub": HUB | {"network": ""}}, "wireguard_hub_network is not an IPv4 network"),
        ({"hub": HUB | {"public_key": "not a key"}}, "wireguard_hub_public_key is not a WireGuard public key"),
        ({"hub": HUB | {"endpoint": "vpn.example.net"}}, "not host:port"),
        # Nothing but a name or an address goes into the VM's networkd file.
        ({"hub": HUB | {"endpoint": "a\n[WireGuardPeer]:51820"}}, "not host:port"),
    ],
)
def test_settings_that_cannot_work_are_refused_before_anything(over, said):
    plan = registry.vpn_member_plan("wireguard", ROWS, wg(WG_A, **over))
    assert len(plan["refusals"]) == 1 and said in plan["refusals"][0], plan["refusals"]
    assert not any(plan[name] for name in ("marks", "peers", "tunnels", "report", "next_steps"))


@pytest.mark.parametrize(
    ("odd", "said"),
    [
        # A tagged VM someone else named on a shared Proxmox.
        ({"vm": {"name": "v" * 50, "vmid": 4020}}, "give the VM a shorter name"),
        # What a VM's root can make wg print, or its facts say.
        ({"public_key": "not a key"}, "is not one (44 characters of base64)"),
        ({"public_key": HUB_KEY}, "is the server's own"),
        ({"lan": "198.51.100.0/24"}, "overlaps wireguard_hub_network (198.51.100.0/24)"),
        ({"lan": "192.0.2.0/99"}, "its own network could not be read"),
    ],
)
def test_one_vm_that_cannot_join_is_said_and_costs_no_other_vm_its_place(odd, said):
    plan = registry.vpn_member_plan("wireguard", ROWS, wg(WG_A, WG_B | odd))
    assert not plan["refusals"]
    assert [t["vm"]["name"] for t in plan["tunnels"]] == ["alpha"]
    assert [p["name"] for p in plan["peers"]] == ["claude-on-proxmox-alpha"]
    odd_vm = (WG_B | odd)["vm"]["name"]
    lines = {line["vm"]: line["text"] for line in plan["report"]}
    steps = {step["vm"]["name"]: step["text"] for step in plan["next_steps"]}
    assert said in lines[odd_vm] and lines[odd_vm].endswith("so it is not joined.")
    assert said in steps[odd_vm]


def test_tailscale_s_record_is_made_on_a_vm_someone_tagged_with_two_wireguard_addresses():
    # Two wg- tags by hand are no slip of Tailscale's: its record goes on, and the core raises nothing.
    tagged = {"vm": ALPHA, "login_seen": True, "marks": ["wg-198.51.100.130", "wg-198.51.100.131"]}
    plan = registry.vpn_member_plan("tailscale", ROWS, given(tagged))
    assert [change["add"] for change in plan["marks"]] == [["tailscale"]]


def test_at_destroy_a_peer_a_partial_listing_could_not_find_is_said_and_kept_for_next_steps():
    leaving = {"vm": ALPHA, "leaving": True, "marks": ["wg-198.51.100.129"]}
    plan = registry.vpn_member_plan(
        "wireguard",
        ROWS,
        given(leaving, phase="release", vm_prefix="198.51.100.128/25", hub=OPN | {"peers": [], "listed": False}),
    )
    assert plan["peers"] == []
    assert "listed only in part" in plan["report"][0]["text"] and "listed only in part" in plan["next_steps"][0]["text"]


def test_at_destroy_each_leaving_vm_with_an_address_has_its_peer_to_remove_said_and_kept():
    leaving = {"vm": ALPHA, "leaving": True, "marks": ["wg-198.51.100.140"]}
    plan = registry.vpn_member_plan("wireguard", ROWS, given(leaving, {"vm": BETA, "leaving": True}, phase="release"))
    assert [(p["kind"], p["name"], p["address"]) for p in plan["peers"]] == [
        ("delete", "claude-on-proxmox-alpha", "198.51.100.140/32")
    ]
    assert "remove its peer" in plan["report"][0]["text"]
    assert plan["next_steps"][0]["vm"] == ALPHA and "198.51.100.140/32" in plan["next_steps"][0]["text"]


WVPN = model.vpns_from(ROWS)["wireguard"]
WINPUTS = model.inputs_from(wg(WG_A, WG_B | {"leaving": True}, taken=["198.51.100.140"]))
TUNNEL = Tunnel(
    vm=A,
    address="198.51.100.130/24",
    hub_public_key=HUB_KEY,
    endpoint="vpn.example.net:51820",
    allowed_ips=("198.51.100.0/24",),
    keepalive=25,
)
PEER = PeerWrite(kind="add", name="claude-on-proxmox-alpha", public_key=KEY_A, address="198.51.100.130/32", vm=A)
WSLIPS = {
    "a tunnel outside the range": MemberPlan(vpn="wireguard", tunnels=(replace(TUNNEL, address="203.0.113.5/24"),)),
    "a tunnel at the range's network address": MemberPlan(
        vpn="wireguard", tunnels=(replace(TUNNEL, address="198.51.100.128/24"),)
    ),
    "a tunnel at an address another VM holds": MemberPlan(
        vpn="wireguard", tunnels=(replace(TUNNEL, address="198.51.100.140/24"),)
    ),
    "a tunnel for a VM that is leaving": MemberPlan(vpn="wireguard", tunnels=(replace(TUNNEL, vm=B),)),
    "two tunnels at one address": MemberPlan(vpn="wireguard", tunnels=(TUNNEL, TUNNEL)),
    "a tunnel to another hub": MemberPlan(vpn="wireguard", tunnels=(replace(TUNNEL, hub_public_key=KEY_B),)),
    "a tunnel routing more than the hub's network": MemberPlan(
        vpn="wireguard", tunnels=(replace(TUNNEL, allowed_ips=("0.0.0.0/0",)),)
    ),
    "a peer of no VM": MemberPlan(vpn="wireguard", tunnels=(TUNNEL,), peers=(replace(PEER, vm=None),)),
    "a peer by another name": MemberPlan(
        vpn="wireguard", tunnels=(TUNNEL,), peers=(replace(PEER, name="claude-on-proxmox-beta"),)
    ),
    "a peer with another VM's key": MemberPlan(
        vpn="wireguard", tunnels=(TUNNEL,), peers=(replace(PEER, public_key=KEY_B),)
    ),
    "a peer with the hub's key": MemberPlan(
        vpn="wireguard", tunnels=(TUNNEL,), peers=(replace(PEER, public_key=HUB_KEY),)
    ),
    "a peer allowed more than its address": MemberPlan(
        vpn="wireguard", tunnels=(TUNNEL,), peers=(replace(PEER, address="198.51.100.0/24"),)
    ),
    "a peer twice": MemberPlan(vpn="wireguard", tunnels=(TUNNEL,), peers=(PEER, PEER)),
    "a peer removed whose VM stays": MemberPlan(vpn="wireguard", peers=(replace(PEER, kind="delete"),)),
    "a peer added for a VM that is leaving": MemberPlan(
        vpn="wireguard", peers=(replace(PEER, vm=B, name="claude-on-proxmox-beta", public_key=KEY_B),)
    ),
    "a mark outside the range": MemberPlan(
        vpn="wireguard",
        marks=(MarkChange(vm=A, add=("wg-203.0.113.5",), unwritten="({reason})", matter="wireguard_address"),),
    ),
    "a Tailscale mark": MemberPlan(
        vpn="wireguard",
        marks=(MarkChange(vm=A, add=("tailscale",), unwritten="({reason})", matter="wireguard_address"),),
    ),
    "two WireGuard addresses on one VM": MemberPlan(
        vpn="wireguard",
        marks=(
            MarkChange(
                vm=A, add=("wg-198.51.100.130", "wg-198.51.100.131"), unwritten="({reason})", matter="wireguard_address"
            ),
        ),
    ),
}


@pytest.mark.parametrize("slip", sorted(WSLIPS))
def test_the_core_refuses_wireguard_s_slips(slip):
    with pytest.raises(ValueError):
        registry.checked(WVPN, WINPUTS, WSLIPS[slip])


def test_the_core_takes_wireguard_s_own_plan_whole():
    plan = MemberPlan(
        vpn="wireguard",
        marks=(MarkChange(vm=A, add=("wg-198.51.100.130",), unwritten="({reason})", matter="wireguard_address"),),
        tunnels=(TUNNEL,),
        peers=(PEER, PeerWrite(kind="delete", name="claude-on-proxmox-beta", address="198.51.100.140/32", vm=B)),
    )
    assert registry.checked(WVPN, WINPUTS, plan) is plan


def test_proxmox_s_tags_give_each_vm_its_marks_and_the_addresses_taken():
    tags = (
        "claude-on-proxmox;claude-on-proxmox-tailscale;Claude-On-Proxmox-WG-198.51.100.130;keepme;claude-on-proxmox-x"
    )
    assert proxmox.proxmox_marks(tags, "claude-on-proxmox") == ["tailscale", "wg-198.51.100.130"]
    assert proxmox.proxmox_marks(["claude-on-proxmox-wg-198.51.100.131"], "claude-on-proxmox") == ["wg-198.51.100.131"]
    assert proxmox.proxmox_marks("claude-on-proxmox-wg-198.51.100.131-reached", "claude-on-proxmox") == [
        "wg-198.51.100.131-reached"
    ]
    assert proxmox.proxmox_marks("claude-on-proxmox-wg-198.51.100.131-other", "claude-on-proxmox") == []
    listing = [
        {"vmid": 4013, "tags": "claude-on-proxmox;claude-on-proxmox-wg-198.51.100.130-reached"},
        {"vmid": 4014, "tags": "claude-on-proxmox,claude-on-proxmox-wg-198.51.100.131"},
        # Not the project's: its mark is no mark.
        {"vmid": 4015, "tags": "claude-on-proxmox-wg-198.51.100.132"},
        {"type": "storage", "storage": "local"},
    ]
    assert proxmox.proxmox_wireguard_taken(listing, "claude-on-proxmox") == ["198.51.100.130", "198.51.100.131"]
    assert proxmox.proxmox_wireguard_taken(listing, "claude-on-proxmox", [4013]) == ["198.51.100.131"]


# ------------------------------------------------------- one address at home and away ---
def test_under_vpn_reach_tunnel_each_vm_s_alias_is_its_tunnel_address_and_it_answers_through_the_tunnel():
    reached = WG_A | {"marks": ["wg-198.51.100.129"], "handshake": True}
    plan = registry.vpn_member_plan("wireguard", ROWS, wg(reached, reach="tunnel"))
    (tunnel,) = plan["tunnels"]
    assert tunnel["reach"] == "tunnel" and tunnel["allowed_ips"] == ["0.0.0.0/0"]
    assert plan["aliases"] == [{"vm": ALPHA, "address": "198.51.100.129"}]
    (mark,) = plan["marks"]
    assert mark["add"] == ["wg-198.51.100.129-reached"] and mark["remove"] == ["wg-198.51.100.129"]
    lan = registry.vpn_member_plan("wireguard", ROWS, wg(reached))
    assert lan["aliases"] == [] and lan["tunnels"][0]["allowed_ips"] == ["198.51.100.0/24"]


@pytest.mark.parametrize(
    ("member", "taken", "alias"),
    [
        (WG_A, [], False),  # a first run: no address recorded yet, and no link to shake hands
        (WG_A | {"marks": ["wg-198.51.100.129"]}, [], False),  # recorded, but the server has no peer yet
        (WG_A | {"handshake": True}, [], False),  # a handshake, but at no address recorded yet
        (WG_A | {"marks": ["wg-198.51.100.129"], "handshake": True}, [], True),
        (WG_A | {"marks": ["wg-198.51.100.129-reached"]}, [], True),  # reached before, quiet this run: it stays
        # Its address is another VM's now: the one it is given has never been reached.
        (WG_A | {"marks": ["wg-198.51.100.129-reached"], "handshake": True}, ["198.51.100.129"], False),
    ],
)
def test_an_alias_moves_to_the_tunnel_only_once_the_vm_has_reached_its_hub_there(member, taken, alias):
    plan = registry.vpn_member_plan("wireguard", ROWS, wg(member, reach="tunnel", taken=taken))
    (tunnel,) = plan["tunnels"]
    address = tunnel["address"].partition("/")[0]
    assert plan["aliases"] == ([{"vm": ALPHA, "address": address}] if alias else [])
    marks = set(member.get("marks", []))
    for change in plan["marks"]:
        marks = (marks - set(change["remove"])) | set(change["add"])
    assert marks == {f"wg-{address}-reached" if alias else f"wg-{address}"}
    (said,) = plan["report"]
    (step,) = plan["next_steps"]
    moves = f"just deploy alpha --only configure moves its SSH alias to {address} once it has reached the server"
    assert (moves in said["text"]) is (moves in step["text"]) is (not alias)


def test_a_reach_a_vpn_does_not_offer_is_refused_naming_the_ones_that_do():
    plan = registry.vpn_member_plan("tailscale", ROWS, given(SEEN, reach="tunnel"))
    (refusal,) = plan["refusals"]
    assert "vpn_reach is tunnel, which Tailscale does not offer" in refusal and "wireguard offers it" in refusal
    assert plan["marks"] == []


TINPUTS = model.inputs_from(wg(WG_A, reach="tunnel"))
TTUNNEL = replace(TUNNEL, allowed_ips=("0.0.0.0/0",), reach="tunnel")
REACHED = MarkChange(
    vm=A, add=("wg-198.51.100.130-reached",), unwritten="alpha: ({reason})", matter="wireguard_address"
)


def test_the_core_takes_an_alias_to_an_address_the_vm_has_reached():
    plan = MemberPlan(
        vpn="wireguard", tunnels=(TTUNNEL,), aliases=(AliasAddress(vm=A, address="198.51.100.130"),), marks=(REACHED,)
    )
    assert registry.checked(WVPN, TINPUTS, plan) is plan


@pytest.mark.parametrize(
    "slip",
    [
        MemberPlan(vpn="wireguard", tunnels=(TTUNNEL,), aliases=(AliasAddress(vm=A, address="198.51.100.131"),)),
        MemberPlan(vpn="wireguard", tunnels=(replace(TTUNNEL, allowed_ips=("198.51.100.0/24",)),)),
        MemberPlan(vpn="wireguard", tunnels=(replace(TTUNNEL, reach="lan"),)),
        MemberPlan(
            vpn="wireguard",
            tunnels=(TTUNNEL,),
            aliases=(AliasAddress(vm=A, address="198.51.100.130"), AliasAddress(vm=A, address="198.51.100.130")),
            marks=(REACHED,),
        ),
        MemberPlan(vpn="wireguard", tunnels=(TTUNNEL,), aliases=(AliasAddress(vm=A, address="198.51.100.130"),)),
        MemberPlan(
            vpn="wireguard",
            tunnels=(TTUNNEL,),
            aliases=(AliasAddress(vm=A, address="198.51.100.130"),),
            marks=(replace(REACHED, add=("wg-198.51.100.130",)),),
        ),
    ],
    ids=[
        "an alias at another address",
        "a tunnel routing too little",
        "a tunnel of the other reach",
        "two aliases",
        "an alias with no address recorded",
        "an alias to an address not reached",
    ],
)
def test_the_core_refuses_a_tunnel_reach_slip(slip):
    with pytest.raises(ValueError):
        registry.checked(WVPN, TINPUTS, slip)


def test_an_alias_under_lan_reach_is_refused():
    plan = MemberPlan(vpn="wireguard", tunnels=(TUNNEL,), aliases=(AliasAddress(vm=A, address="198.51.100.130"),))
    with pytest.raises(ValueError):
        registry.checked(WVPN, WINPUTS, plan)


@pytest.mark.parametrize(
    ("marks", "vpn", "reach", "address"),
    [
        (["tailscale", "wg-198.51.100.130-reached"], "wireguard", "tunnel", "198.51.100.130"),
        (["wg-198.51.100.130"], "wireguard", "tunnel", "192.0.2.61"),  # recorded, but never reached: leads nowhere
        (["wg-198.51.100.130-reached"], "wireguard", "lan", "192.0.2.61"),
        (["wg-198.51.100.130-reached"], "tailscale", "tunnel", "192.0.2.61"),
        (["tailscale"], "wireguard", "tunnel", "192.0.2.61"),  # no address marked yet
        ("wg-198.51.100.130", "wireguard", "tunnel", "192.0.2.61"),  # not a list: nothing read from it
    ],
)
def test_an_alias_uses_the_marked_tunnel_address_only_under_wireguard_s_tunnel_reach(marks, vpn, reach, address):
    assert common.vpn_alias_address("192.0.2.61", marks, vpn, reach) == address


# ------------------------------------------------------- a hub with an API: OPNsense ---
KEY_C, KEY_D = (base64.b64encode(bytes([n] * 32)).decode() for n in (4, 5))
OPN = {
    "kind": "opnsense",
    "listed": True,
    "instance": "home",
    # As OPNsense keeps an instance's tunnel network: the hub's own address on it.
    "network": "198.51.100.1/24",
    "public_key": HUB_KEY,
    "endpoint": "192.0.2.1:51820",
}


def opn_peer(ref, name, key, address, instances=("home",)):
    return {"ref": ref, "name": name, "public_key": key, "addresses": [address], "instances": list(instances)}


def test_an_opnsense_hub_gets_a_peer_added_one_as_it_should_be_left_and_one_changed_updated():
    peers = [
        opn_peer("u-beta", "claude-on-proxmox-beta", KEY_B, "198.51.100.130/32"),
        opn_peer("u-gamma", "claude-on-proxmox-gamma", KEY_C, "198.51.100.140/32"),
    ]
    gamma = {"vm": {"name": "gamma", "vmid": 4016}, "public_key": KEY_D, "marks": ["wg-198.51.100.140"]}
    plan = registry.vpn_member_plan(
        "wireguard",
        ROWS,
        wg(WG_A, WG_B | {"marks": ["wg-198.51.100.130"]}, gamma, hub=OPN | {"peers": peers}),
    )
    assert not plan["refusals"]
    changes = [(p["kind"], p["name"], p["ref"], p["public_key"], p["address"]) for p in plan["peers"]]
    assert changes == [
        ("add", "claude-on-proxmox-alpha", "", KEY_A, "198.51.100.129/32"),
        ("update", "claude-on-proxmox-gamma", "u-gamma", KEY_D, "198.51.100.140/32"),
    ]
    # Routed into the network the router keeps, from its own address on it; nothing left for the person to add.
    assert {t["allowed_ips"][0] for t in plan["tunnels"]} == {"198.51.100.0/24"}
    assert all(step["text"] == "" for step in plan["next_steps"])


def test_a_peer_of_a_vm_gone_is_pruned_only_from_a_complete_listing():
    peers = [opn_peer("u-old", "claude-on-proxmox-old", KEY_C, "198.51.100.150/32")]
    fleet = [{"name": "alpha", "vmid": 4013, "tags": "claude-on-proxmox"}]
    plan = registry.vpn_member_plan("wireguard", ROWS, wg(WG_A, hub=OPN | {"peers": peers}, fleet=fleet))
    assert [(p["kind"], p["ref"], p["vm"]) for p in plan["peers"] if p["kind"] == "delete"] == [
        ("delete", "u-old", None)
    ]
    for partial in ({"fleet": []}, {"hub": OPN | {"peers": peers, "listed": False}, "fleet": fleet}):
        kept = registry.vpn_member_plan("wireguard", ROWS, wg(WG_A, **({"hub": OPN | {"peers": peers}} | partial)))
        assert not [p for p in kept["peers"] if p["kind"] == "delete"]


def test_a_peer_with_some_marks_but_not_all_is_said_and_never_touched():
    peers = [
        # The project's name, but an address outside the VMs' range: someone else's.
        opn_peer("u-x", "claude-on-proxmox-alpha", KEY_C, "198.51.100.10/32"),
        # The project's name and range, but another instance.
        opn_peer("u-y", "claude-on-proxmox-beta", KEY_D, "198.51.100.131/32", instances=("work",)),
    ]
    fleet = [{"name": "alpha", "vmid": 4013}]
    plan = registry.vpn_member_plan("wireguard", ROWS, wg(WG_A, hub=OPN | {"peers": peers}, fleet=fleet))
    assert plan["peers"] == [] and plan["tunnels"] == []
    said = " ".join(line["text"] for line in plan["report"])
    assert "claude-on-proxmox-alpha on home carries the project's name but not every mark" in said
    assert "alpha: a peer named claude-on-proxmox-alpha on home is not the project's" in said


def test_a_key_someone_else_s_peer_holds_is_not_joined():
    peers = [opn_peer("u-z", "laptop", KEY_A, "198.51.100.5/32")]
    plan = registry.vpn_member_plan("wireguard", ROWS, wg(WG_A, hub=OPN | {"peers": peers}))
    assert plan["peers"] == [] and "is a peer's on home already" in plan["report"][0]["text"]


def test_a_renamed_vm_is_not_joined_beside_its_old_peer_which_a_fleet_wide_run_removes():
    # alpha renamed alpha2 in Proxmox: its key, on the VM's disk, is the old peer's, which the hub would refuse twice.
    peers = [opn_peer("u-alpha", "claude-on-proxmox-alpha", KEY_A, "198.51.100.129/32")]
    renamed = WG_A | {"vm": {"name": "alpha2", "vmid": 4013}, "marks": ["wg-198.51.100.129"]}
    named = registry.vpn_member_plan("wireguard", ROWS, wg(renamed, hub=OPN | {"peers": peers}))
    assert named["peers"] == [] and named["tunnels"] == []
    assert "its WireGuard key is claude-on-proxmox-alpha's on home already" in named["report"][0]["text"]
    assert "a fleet-wide just deploy --only configure removes the old peer" in named["next_steps"][0]["text"]
    fleet = [{"name": "alpha2", "vmid": 4013}]
    whole = registry.vpn_member_plan("wireguard", ROWS, wg(renamed, hub=OPN | {"peers": peers}, fleet=fleet))
    assert [(p["kind"], p["ref"]) for p in whole["peers"]] == [("delete", "u-alpha")]


def test_a_vm_cloned_with_another_s_key_is_not_joined_and_the_other_keeps_its_peer():
    peers = [opn_peer("u-alpha", "claude-on-proxmox-alpha", KEY_A, "198.51.100.129/32")]
    alpha = WG_A | {"marks": ["wg-198.51.100.129"]}
    clone = WG_A | {"vm": BETA}
    fleet = [{"name": "alpha", "vmid": 4013}, {"name": "beta", "vmid": 4014}]
    plan = registry.vpn_member_plan("wireguard", ROWS, wg(alpha, clone, hub=OPN | {"peers": peers}, fleet=fleet))
    assert plan["peers"] == [] and [t["vm"]["name"] for t in plan["tunnels"]] == ["alpha"]
    said = {line["vm"]: line["text"] for line in plan["report"]}
    assert "is as it should be" in said["alpha"] and "/etc/systemd/network/claude-wg.key on beta" in said["beta"]


def test_nothing_is_written_on_a_hub_whose_peers_were_read_only_in_part():
    # A peer not read may be the VM's own: an add would make a second, an update would touch one the core never saw.
    peers = [opn_peer("u-beta", "claude-on-proxmox-beta", KEY_C, "198.51.100.130/32")]
    beta = WG_B | {"marks": ["wg-198.51.100.130"]}
    plan = registry.vpn_member_plan("wireguard", ROWS, wg(WG_A, beta, hub=OPN | {"peers": peers, "listed": False}))
    assert plan["peers"] == [] and len(plan["tunnels"]) == 2
    for step in plan["next_steps"]:
        assert step["text"].endswith(f"just deploy {step['vm']['name']} --only configure tries again.")


def test_the_core_refuses_any_write_on_a_hub_read_only_in_part():
    partial = model.inputs_from(wg(WG_A, hub=OPN | {"listed": False}))
    add = PeerWrite(kind="add", name="claude-on-proxmox-alpha", public_key=KEY_A, address="198.51.100.129/32", vm=A)
    tunnel = replace(TUNNEL, address="198.51.100.129/24", endpoint="192.0.2.1:51820")
    with pytest.raises(ValueError, match="could not read whole"):
        registry.checked(WVPN, partial, MemberPlan(vpn="wireguard", tunnels=(tunnel,), peers=(add,)))


def test_no_vm_is_given_an_address_the_hub_holds_already():
    # Someone else's peer inside the range, a peer of a VM gone that a run could not remove, and the router's own
    # address: none is given to a VM. alpha's own peer is its own.
    peers = [
        opn_peer("u-laptop", "laptop", KEY_C, "198.51.100.129/32"),
        opn_peer("u-old", "claude-on-proxmox-old", KEY_D, "198.51.100.130/32"),
        opn_peer("u-alpha", "claude-on-proxmox-alpha", KEY_A, "198.51.100.131/32"),
    ]
    plan = registry.vpn_member_plan("wireguard", ROWS, wg(WG_A, WG_B, hub=OPN | {"peers": peers}))
    assert {t["vm"]["name"]: t["address"] for t in plan["tunnels"]} == {
        "alpha": "198.51.100.131/24",
        "beta": "198.51.100.132/24",
    }
    whole = registry.vpn_member_plan("wireguard", ROWS, wg(WG_A, hub=OPN | {"peers": []}, vm_prefix="198.51.100.0/24"))
    assert whole["tunnels"][0]["address"] == "198.51.100.2/24"  # .1 is the router's own


def test_two_vms_with_one_key_are_one_peer_the_lower_vm_id_s():
    # A clone made before either VM had a peer carries the other's key: the hub would get two peers with one key.
    clone = WG_A | {"vm": BETA}
    plan = registry.vpn_member_plan("wireguard", ROWS, wg(WG_A, clone))
    assert [t["vm"]["name"] for t in plan["tunnels"]] == ["alpha"]
    assert [p["name"] for p in plan["peers"]] == ["claude-on-proxmox-alpha"]
    said = {line["vm"]: line["text"] for line in plan["report"]}
    assert "its WireGuard key is alpha's too" in said["beta"]


def test_the_core_refuses_two_peers_with_one_key_and_a_tunnel_at_another_vm_s_address():
    twice = PeerWrite(kind="add", name="claude-on-proxmox-beta", public_key=KEY_A, address="198.51.100.131/32", vm=B)
    with pytest.raises(ValueError, match="one key"):
        plan = MemberPlan(vpn="wireguard", tunnels=(TUNNEL,), peers=(PEER, twice))
        registry.checked(WVPN, model.inputs_from(wg(WG_A, WG_B)), plan)
    inputs = model.inputs_from(wg(WG_A, WG_B | {"marks": ["wg-198.51.100.130"]}))
    with pytest.raises(ValueError, match="not a free address"):
        # alpha at .130, which beta - no tunnel this run - still holds.
        registry.checked(WVPN, inputs, MemberPlan(vpn="wireguard", tunnels=(TUNNEL,)))


@pytest.mark.parametrize("tag", ["wg-999.0.0.1", "wg-010.0.0.1", "wg-198.51.100.130-other", "wg-198.51.100"])
def test_a_tag_that_holds_no_address_is_no_mark(tag):
    # Anyone who may tag a VM can add one: it is left out, never handed to the core, where it would fail the plan.
    tags = f"claude-on-proxmox;claude-on-proxmox-{tag}"
    assert proxmox.proxmox_marks(tags, "claude-on-proxmox") == []
    assert proxmox.proxmox_wireguard_taken([{"vmid": 4020, "tags": tags}], "claude-on-proxmox") == []
    assert proxmox.MARK_RE is model.MARK


def test_a_wide_range_is_walked_never_listed():
    # A /8 holds 16 million addresses: the plan takes the first free ones without listing them all.
    hub = HUB | {"network": "10.0.0.0/8"}
    plan = registry.vpn_member_plan("wireguard", ROWS, wg(WG_A, hub=hub, vm_prefix="10.0.0.0/8", taken=["10.0.0.1"]))
    assert plan["tunnels"][0]["address"] == "10.0.0.2/8"


def test_at_destroy_an_opnsense_hub_has_the_leaving_vm_s_own_peer_removed_by_its_ref():
    peers = [opn_peer("u-alpha", "claude-on-proxmox-alpha", KEY_A, "198.51.100.129/32")]
    leaving = {"vm": ALPHA, "leaving": True, "marks": ["wg-198.51.100.129"]}
    plan = registry.vpn_member_plan(
        "wireguard", ROWS, given(leaving, phase="release", vm_prefix="198.51.100.128/25", hub=OPN | {"peers": peers})
    )
    assert [(p["kind"], p["ref"]) for p in plan["peers"]] == [("delete", "u-alpha")]
    assert plan["next_steps"][0]["text"] == ""


OINPUTS = model.inputs_from(
    wg(
        WG_A,
        hub=OPN
        | {
            "peers": [
                opn_peer("u-alpha", "claude-on-proxmox-alpha", KEY_C, "198.51.100.130/32"),
                opn_peer("u-laptop", "laptop", KEY_D, "198.51.100.5/32"),
                opn_peer("u-old", "claude-on-proxmox-old", KEY_B, "198.51.100.150/32"),
            ]
        },
        fleet=[{"name": "alpha", "vmid": 4013}, {"name": "old", "vmid": 4030}],
    )
)
OTUNNEL = replace(TUNNEL, endpoint="192.0.2.1:51820")
OUPDATE = PeerWrite(
    kind="update", name="claude-on-proxmox-alpha", ref="u-alpha", public_key=KEY_A, address="198.51.100.130/32", vm=A
)
OSLIPS = {
    "an update of a peer that is not the project's": replace(OUPDATE, ref="u-laptop"),
    "an update by another peer's name": replace(OUPDATE, name="claude-on-proxmox-old"),
    "an add beside a peer of the same name": replace(OUPDATE, kind="add", ref=""),
    "an update to a key another peer holds": replace(OUPDATE, public_key=KEY_D),
    "a prune of a VM that is there": PeerWrite(kind="delete", name="claude-on-proxmox-old", ref="u-old", address=""),
    "a prune of someone else's peer": PeerWrite(kind="delete", name="laptop", ref="u-laptop", address=""),
}


@pytest.mark.parametrize("slip", sorted(OSLIPS))
def test_the_core_refuses_an_opnsense_hub_s_slips(slip):
    with pytest.raises(ValueError):
        registry.checked(WVPN, OINPUTS, MemberPlan(vpn="wireguard", tunnels=(OTUNNEL,), peers=(OSLIPS[slip],)))


def test_the_core_takes_an_update_of_the_vm_s_own_peer():
    plan = MemberPlan(vpn="wireguard", tunnels=(OTUNNEL,), peers=(OUPDATE,))
    assert registry.checked(WVPN, OINPUTS, plan) is plan
