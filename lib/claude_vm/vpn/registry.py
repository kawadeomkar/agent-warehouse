"""Every family's plan passes here, and leaves only as the core allows: a refusal alone; marks only for the inputs'
VMs, only of the family's own, never the Tailscale record taken off, and a WireGuard address only inside the VMs'
range; one tunnel per VM that stays, at an address of the range no other VM holds, to the hub the inputs name; a
peer only by the name of a VM it was given, with that VM's own key and address alone; nothing a family does not
write; NEXT STEPS of the family's own matters, for the inputs' VMs alone. Anything else is a family's bug, a
ValueError, which fails the first task that reads the plan, before any write."""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any, Final

from ..catalogue import pick
from ..wire import to_wire
from . import common, tailscale, wireguard
from .family import Family
from .model import FamilyName, MemberInputs, MemberPlan, Vpn, inputs_from, ipv4_network, vpns_from

FAMILIES: Final[Mapping[FamilyName, Family]] = {"tailscale": tailscale, "wireguard": wireguard}  # the check
_tailscale: Family = tailscale  # one line per family, so a type checker names the member that does not conform
_wireguard: Family = wireguard


def vpn_member_plan(key: object, rows: object, inputs: object) -> dict[str, Any]:
    """The plan of who is on the network of VPN key, from claude_vpns and members.yml's inputs, as the wire carries
    it."""
    vpns, given = vpns_from(rows), inputs_from(inputs)
    vpn = pick(key, vpns, "a VPN this project joins VMs to")
    if given.reach not in vpn.reach:
        # A refusal of the core's own, before any family plans: the row says what the VPN offers.
        offers = [other.key for other in vpns.values() if given.reach in other.reach]
        return to_wire(
            MemberPlan(
                vpn=vpn.key,
                refusals=(
                    f"vpn_reach is {given.reach}, which {vpn.name} does not offer: an SSH alias reaches its VMs at"
                    f" their LAN address (vpn_reach: lan). {', '.join(offers) or 'No VPN'} offers it.",
                ),
            )
        )
    return to_wire(checked(vpn, given, FAMILIES[vpn.family].plan_members(vpn, given)))


def checked(vpn: Vpn, inputs: MemberInputs, plan: MemberPlan) -> MemberPlan:
    if plan.vpn != vpn.key:
        raise ValueError(f"{vpn.key}'s plan is {plan.vpn}'s")
    if plan.refusals:  # a refusal marks, writes and says nothing else
        return MemberPlan(vpn=vpn.key, refusals=plan.refusals)
    members = {member.vm.name: member for member in inputs.members}

    def member_of(vm: object) -> None:
        # By name and VM ID: Proxmox's, as the inputs carry them.
        if getattr(vm, "name", None) not in members or members[vm.name].vm != vm:
            raise ValueError(f"{vpn.key}'s plan names {vm!r}, which is no VM it was given")

    seen = set()
    for change in plan.marks:
        member_of(change.vm)
        if change.vm.name in seen:
            raise ValueError(f"{vpn.key}'s plan changes {change.vm.name}'s marks twice")
        seen.add(change.vm.name)
        if not all(common.marks_of(vpn.family, mark) for mark in (*change.add, *change.remove)):
            raise ValueError(f"{vpn.key}'s plan changes a mark that is not its own on {change.vm.name}")
        if common.TAILSCALE in change.remove:
            raise ValueError(f"{vpn.key}'s plan takes {change.vm.name}'s Tailscale record off, which stays for good")
        after = (set(members[change.vm.name].marks) - set(change.remove)) | set(change.add)
        if sum(mark.startswith(common.WIREGUARD) for mark in after) > 1:
            raise ValueError(f"{vpn.key}'s plan leaves {change.vm.name} more than one WireGuard address")
        for mark in change.add:
            if mark.startswith(common.WIREGUARD) and not _in_range(inputs, common.mark_address(mark)):
                raise ValueError(f"{vpn.key}'s plan marks {change.vm.name} with {mark}, outside the VMs' range")
        if change.matter not in common.MATTERS[vpn.family]:
            raise ValueError(f"{vpn.key}'s plan keeps NEXT STEPS under {change.matter}, which is not its own")
    if vpn.family == "tailscale" and (plan.peers or plan.tunnels or plan.aliases):
        raise ValueError(f"{vpn.key}'s plan writes peers or tunnels, which Tailscale's coordination server keeps")
    if vpn.family == "wireguard":
        _tunnels(vpn, inputs, plan, member_of)
        _peers(vpn, inputs, plan, member_of)
    for said in plan.report:
        # A line of no VM is about a hub's peer, where the hub is read: one pruned, or one with some marks only.
        if said.vm not in members and (said.vm or inputs.hub.kind != "opnsense"):
            raise ValueError(f"{vpn.key}'s plan says something of {said.vm!r}, which is no VM it was given")
    for step in plan.next_steps:
        member_of(step.vm)
        if step.matter not in common.MATTERS[vpn.family]:
            raise ValueError(f"{vpn.key}'s plan keeps NEXT STEPS under {step.matter}, which is not its own")
    return plan


def _marks_after(inputs: MemberInputs, plan: MemberPlan, vm: str) -> set[str]:
    """A VM's marks once the plan's are applied."""
    marks = {mark for member in inputs.members if member.vm.name == vm for mark in member.marks}
    for change in plan.marks:
        if change.vm.name == vm:
            marks = (marks - set(change.remove)) | set(change.add)
    return marks


def _in_range(inputs: MemberInputs, address: str) -> bool:
    """A host of the VMs' range: neither its network's address nor its broadcast's, unless it has no others."""
    if not address or not inputs.vm_prefix:
        return False
    prefix, host = ipv4_network(inputs.vm_prefix, "the VM prefix"), ipaddress.IPv4Address(address)
    return host in prefix and (prefix.prefixlen >= 31 or host not in (prefix.network_address, prefix.broadcast_address))


def _tunnels(vpn: Vpn, inputs: MemberInputs, plan: MemberPlan, member_of) -> None:
    held, taken = set(), set(inputs.taken)
    for tunnel in plan.tunnels:
        member_of(tunnel.vm)
        name = tunnel.vm.name
        if any(member.vm.name == name and member.leaving for member in inputs.members):
            raise ValueError(f"{vpn.key}'s plan sets {name} up on the tunnel as it leaves")
        address = tunnel.address.partition("/")[0]
        if name in {vm for vm, _ in held} or not _in_range(inputs, address) or address in taken | {a for _, a in held}:
            raise ValueError(f"{vpn.key}'s plan gives {name} {tunnel.address}: not a free address of the VMs' range")
        held.add((name, address))
        if (tunnel.hub_public_key, tunnel.endpoint) != (inputs.hub.public_key, inputs.hub.endpoint):
            raise ValueError(f"{vpn.key}'s plan points {name} at a hub it was not given")
        routed = (str(common.hub_network(inputs.hub.network)),) if inputs.reach == "lan" else ("0.0.0.0/0",)
        if tunnel.keepalive != inputs.keepalive or tunnel.allowed_ips != routed or tunnel.reach != inputs.reach:
            raise ValueError(f"{vpn.key}'s plan routes {name} other than vpn_reach says")
    tunnels = {tunnel.vm.name: tunnel.address.partition("/")[0] for tunnel in plan.tunnels}
    for alias in plan.aliases:
        member_of(alias.vm)
        if inputs.reach != "tunnel" or tunnels.get(alias.vm.name) != alias.address:
            raise ValueError(f"{vpn.key}'s plan points {alias.vm.name}'s alias at {alias.address}, not its tunnel")
        # Only to an address the VM has reached its hub at, as its marks say once the plan is applied.
        if common.wireguard_mark(alias.address, reached=True) not in _marks_after(inputs, plan, alias.vm.name):
            raise ValueError(f"{vpn.key}'s plan points {alias.vm.name}'s alias at {alias.address}, not yet reached")
    if len({alias.vm.name for alias in plan.aliases}) != len(plan.aliases):
        raise ValueError(f"{vpn.key}'s plan points an alias twice")


def _peers(vpn: Vpn, inputs: MemberInputs, plan: MemberPlan, member_of) -> None:
    tunnels = {tunnel.vm.name: tunnel.address.partition("/")[0] for tunnel in plan.tunnels}
    keys = {peer.public_key for peer in inputs.hub.peers}
    hub = inputs.hub
    # The hub's peers that are the project's: every mark of it, by its handle on the hub.
    ours = {
        peer.ref: peer
        for peer in hub.peers
        if common.owned(inputs.tag, inputs.vm_prefix, hub.instance, peer.name, peer.addresses, peer.instances)
    }
    alive = {vm.name for vm in inputs.fleet} | {member.vm.name for member in inputs.members}
    named = set()
    for peer in plan.peers:
        if peer.name in named:
            raise ValueError(f"{vpn.key}'s plan changes {peer.name} twice")
        if hub.kind == "opnsense" and not hub.listed:
            # A peer not read may be the very one: nothing is written on a hub read only in part.
            raise ValueError(f"{vpn.key}'s plan changes {peer.name} on a hub it could not read whole")
        if peer.kind != "add" and hub.kind == "opnsense":
            # A change to a peer the hub has: only one of the project's, by its own handle and name, read whole.
            if peer.ref not in ours or ours[peer.ref].name != peer.name or not hub.listed:
                raise ValueError(f"{vpn.key}'s plan changes {peer.name}, which is no peer of the project's on the hub")
        elif peer.kind == "update":
            raise ValueError(f"{vpn.key}'s plan updates {peer.name} on a hub it cannot read")
        if peer.vm is None:
            # A peer pruned: its VM gone from a complete listing of the fleet.
            if peer.kind != "delete" or hub.kind != "opnsense" or not inputs.fleet:
                raise ValueError(f"{vpn.key}'s plan changes {peer.name}, a peer of no VM it was given")
            if peer.name[len(inputs.tag) + 1 :] in alive:
                raise ValueError(f"{vpn.key}'s plan removes {peer.name}, whose VM is there")
            named.add(peer.name)
            continue
        member_of(peer.vm)
        member = next(member for member in inputs.members if member.vm == peer.vm)
        if peer.name != common.peer_name(inputs.tag, peer.vm.name) or not common.PEER_NAME.fullmatch(peer.name):
            raise ValueError(f"{vpn.key}'s plan names {peer.vm.name}'s peer {peer.name}")
        named.add(peer.name)
        if peer.kind == "delete":
            if not member.leaving:
                raise ValueError(f"{vpn.key}'s plan removes {peer.name}, whose VM stays")
            continue
        if member.leaving:
            raise ValueError(f"{vpn.key}'s plan adds {peer.name}, whose VM is leaving")
        if peer.public_key != member.public_key or not common.public_key(peer.public_key):
            raise ValueError(f"{vpn.key}'s plan gives {peer.name} a key that is not its VM's")
        others = keys - ({ours[peer.ref].public_key} if peer.ref in ours else set())
        if (
            peer.public_key == hub.public_key
            or (peer.kind == "add" and peer.public_key in keys)
            or (peer.kind == "update" and peer.public_key in others)
        ):
            raise ValueError(f"{vpn.key}'s plan gives {peer.name} a key the hub has already")
        if peer.kind == "add" and any(other.name == peer.name for other in hub.peers):
            raise ValueError(f"{vpn.key}'s plan adds {peer.name} beside a peer of that name")
        if peer.address != f"{tunnels.get(peer.vm.name)}/32":
            raise ValueError(f"{vpn.key}'s plan allows {peer.name} more, or other, than its VM's own address")
