"""WireGuard's family: each VM a peer of a WireGuard server the user already runs, its hub. The plan gives each VM an
address in the range of the hub's network reserved for the VMs, kept for its life as a mark in Proxmox; the tunnel
its role writes on the VM; and the hub's peers to add, change and remove, which a hub of kind manual says and the
user adds (docs/guides/wireguard.md). A module of free functions, never a class."""

from __future__ import annotations

import ipaddress
import re
from typing import Final

from ..refs import NextStep
from . import common
from .model import (
    AliasAddress,
    HubPeer,
    MarkChange,
    Member,
    MemberInputs,
    MemberPlan,
    PeerWrite,
    Said,
    Tunnel,
    Vpn,
    ipv4_network,
)

# What an endpoint's host may be, as it goes into the VM's networkd file: a name or an IPv4 address, nothing else.
HOST: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
ADDRESS: Final = "wireguard_address"  # the NEXT STEPS matter of a mark Proxmox could not write
PEER: Final = "wireguard_peer"  # the NEXT STEPS matter of a peer the user is to add to their server


def _refusal(inputs: MemberInputs) -> str:
    """The first of the settings' refusals: what no VM can join with."""
    hub = inputs.hub
    try:
        network = common.hub_network(hub.network)
        prefix = ipv4_network(inputs.vm_prefix, "wireguard_vm_prefix")
    except ValueError as error:
        return f"{error}: set it in local.yml (docs/guides/wireguard.md)."
    if not prefix.subnet_of(network):
        return (
            f"wireguard_vm_prefix ({prefix}) is not inside wireguard_hub_network ({network}): the VMs' range is a part"
            " of the server's tunnel network, kept for them (docs/guides/wireguard.md)."
        )
    if not common.public_key(hub.public_key):
        return (
            "wireguard_hub_public_key is not a WireGuard public key (44 characters of base64): it is the server's, as"
            " `wg show <its interface> public-key` prints it there."
        )
    host, _, port = hub.endpoint.rpartition(":")
    if not HOST.fullmatch(host) or not port.isdigit() or not 0 < int(port) < 65536:
        return (
            f"wireguard_hub_endpoint is {hub.endpoint!r}, not host:port: the address or name the VMs dial, and the"
            " server's listen port, such as vpn.example.net:51820."
        )
    for member in inputs.members:
        if member.lan and ipaddress.IPv4Network(member.lan, strict=False).overlaps(network):
            return (
                f"wireguard_hub_network ({network}) overlaps {member.vm.name}'s own network ({member.lan}): a VM would"
                " send its LAN's traffic into the tunnel. Choose a tunnel network apart from the LAN."
            )
        name = common.peer_name(inputs.tag, member.vm.name)
        if not common.PEER_NAME.fullmatch(name):
            return (
                f"{member.vm.name}'s peer would be named {name}, which a WireGuard hub such as OPNsense refuses: at"
                " most 64 letters, digits, '.', '_' and '-'. Give the VM a shorter name."
            )
    return ""


def _by_id(members) -> list[Member]:
    """The members, the lowest VM ID first: the order in which they win an address, or a key."""
    return sorted(members, key=lambda member: member.vm.vmid)


def _held_on_hub(inputs: MemberInputs) -> list[ipaddress.IPv4Network]:
    """What the hub holds already, which no VM may be given: every address of its peers but the members' own - one
    of a VM gone, whose peer a run could not remove, or someone else's inside the range - and its own."""
    own = {common.peer_name(inputs.tag, member.vm.name) for member in inputs.members if not member.leaving}
    held = []
    for peer in inputs.hub.peers:
        if peer.name in own and _owned(inputs, peer):
            continue
        for address in peer.addresses:
            try:
                held.append(ipaddress.IPv4Network(address, strict=False))
            except ValueError:
                continue  # an IPv6 address: never one of the range's
    hub = common.hub_address(inputs.hub.network)
    return held + ([ipaddress.IPv4Network(hub)] if hub is not None else [])


def _addresses(inputs: MemberInputs) -> dict[str, str]:
    """Each member's address: its mark's, while the mark is a host of the range and no VM of a lower VM ID holds it,
    else the lowest one free - outside every address another VM of the project holds, and every one the hub has."""
    prefix = ipv4_network(inputs.vm_prefix, "wireguard_vm_prefix")
    taken = set()
    for address in inputs.taken:
        try:
            taken.add(ipaddress.IPv4Address(address))
        except ValueError:
            continue
    held = _held_on_hub(inputs)

    def free(address: ipaddress.IPv4Address) -> bool:
        ends = () if prefix.prefixlen >= 31 else (prefix.network_address, prefix.broadcast_address)
        return (
            address in prefix and address not in ends and address not in taken and not any(address in n for n in held)
        )

    kept: dict[str, str] = {}
    staying = [member for member in inputs.members if not member.leaving]
    for member in _by_id(staying):
        marked = [common.mark_address(mark) for mark in member.marks if common.mark_address(mark)]
        address = ipaddress.IPv4Address(marked[0]) if marked else None
        if address is not None and free(address):
            kept[member.vm.name] = str(address)
            taken.add(address)
    # The range walked, not listed: a wide one holds millions of addresses.
    for member in _by_id(staying):
        if member.vm.name not in kept and member.public_key:
            found = next((host for host in (prefix.hosts() if prefix.prefixlen < 31 else prefix) if free(host)), None)
            if found is not None:
                kept[member.vm.name] = str(found)
                taken.add(found)
    return kept


def _peer_block(inputs: MemberInputs, member: Member, address: str) -> str:
    return (
        f"[Peer] # {common.peer_name(inputs.tag, member.vm.name)} / PublicKey = {member.public_key} /"
        f" AllowedIPs = {address}/32"
    )


def _owned(inputs: MemberInputs, peer: HubPeer) -> bool:
    hub = inputs.hub
    return common.owned(inputs.tag, inputs.vm_prefix, hub.instance, peer.name, peer.addresses, peer.instances)


def _hub_peer(inputs: MemberInputs, member: Member, address: str) -> tuple[PeerWrite | None, str, str]:
    """The change a hub with an API needs for a VM's peer, what is said of it, and what is left to do: an add, an
    update of the peer it has by the VM's name and every mark of the project's, or nothing where it is as the plan has
    it - or where the hub's peers were read only in part, since one not read may be the VM's."""
    vm, name, instance = member.vm.name, common.peer_name(inputs.tag, member.vm.name), inputs.hub.instance
    mine = [peer for peer in inputs.hub.peers if peer.name == name and _owned(inputs, peer)]
    if mine and mine[0].public_key == member.public_key and mine[0].addresses == (f"{address}/32",):
        return None, f"its peer {name} on {instance} is as it should be", ""
    if not inputs.hub.listed:
        return (
            None,
            f"its peer {name} is not written this run: the router listed {instance}'s peers only in part",
            f"{vm}: its peer {name} on {instance} is not written yet: just deploy {vm} --only configure tries again.",
        )
    if not mine:
        wanted = PeerWrite(kind="add", name=name, public_key=member.public_key, address=f"{address}/32", vm=member.vm)
        return wanted, f"its peer {name} is added to {instance}", ""
    return (
        PeerWrite(
            kind="update",
            name=name,
            ref=mine[0].ref,
            public_key=member.public_key,
            address=f"{address}/32",
            vm=member.vm,
        ),
        f"its peer {name} on {instance} is updated",
        "",
    )


def _configure(vpn: Vpn, inputs: MemberInputs) -> MemberPlan:
    network = common.hub_network(inputs.hub.network)
    addresses = _addresses(inputs)
    marks, peers, tunnels, aliases, report, steps = [], [], [], [], [], []
    api = inputs.hub.kind == "opnsense"
    # A key a peer of someone else's holds already: the hub would refuse a second peer with it.
    foreign = {peer.public_key for peer in inputs.hub.peers if not _owned(inputs, peer)}
    for peer in inputs.hub.peers:
        if api and peer.name.startswith(f"{inputs.tag}-") and not _owned(inputs, peer):
            report.append(
                Said(
                    vm="",
                    text=f"{peer.name} on {inputs.hub.instance} carries the project's name but not every mark of its"
                    " own (an address in wireguard_vm_prefix, the instance), so it is left as it is.",
                )
            )
    # One key, one peer: a VM cloned with another's key, before either has a peer, is the lower VM ID's alone.
    first: dict[str, str] = {}
    for member in _by_id(inputs.members):
        if member.public_key and not member.leaving:
            first.setdefault(member.public_key, member.vm.name)
    for member in inputs.members:
        vm = member.vm.name
        if member.leaving:
            continue
        name = common.peer_name(inputs.tag, vm)
        if member.public_key and first.get(member.public_key) != vm:
            clone = first[member.public_key]
            report.append(
                Said(
                    vm=vm,
                    text=f"{vm}: its WireGuard key is {clone}'s too, so it is not joined: a clone of {clone} carries"
                    f" its key. Delete /etc/systemd/network/claude-wg.key on {vm} and run again.",
                )
            )
            steps.append(
                NextStep(
                    vm=member.vm,
                    matter=PEER,
                    action=None,
                    text=f"{vm}: its WireGuard key is {clone}'s: delete /etc/systemd/network/claude-wg.key on {vm}"
                    " and run again.",
                )
            )
            continue
        if api and any(peer.name == name and not _owned(inputs, peer) for peer in inputs.hub.peers):
            report.append(
                Said(
                    vm=vm,
                    text=f"{vm}: a peer named {name} on {inputs.hub.instance} is not the project's (said above),"
                    " so it is not joined beside it.",
                )
            )
            continue
        # Its key on a peer of the project's under another VM's name: a VM renamed, its old peer still there, or one
        # cloned with another's key. The hub refuses a second peer with one key, so it is not joined beside that one.
        twin = next(
            (
                peer
                for peer in inputs.hub.peers
                if api
                and member.public_key
                and peer.public_key == member.public_key
                and peer.name != name
                and _owned(inputs, peer)
            ),
            None,
        )
        if twin is not None:
            report.append(
                Said(
                    vm=vm,
                    text=f"{vm}: its WireGuard key is {twin.name}'s on {inputs.hub.instance} already, so it is not"
                    f" joined beside it. If {vm} was renamed, a fleet-wide just deploy --only configure removes"
                    f" {twin.name} once its VM is gone, and the next run joins {vm}; if {vm} is a clone of that VM,"
                    f" delete /etc/systemd/network/claude-wg.key on {vm} and run again.",
                )
            )
            steps.append(
                NextStep(
                    vm=member.vm,
                    matter=PEER,
                    action=None,
                    text=f"{vm}: its WireGuard key is {twin.name}'s on {inputs.hub.instance}: renamed, a fleet-wide"
                    f" just deploy --only configure removes the old peer; a clone, delete"
                    f" /etc/systemd/network/claude-wg.key on {vm} and run again.",
                )
            )
            continue
        if api and member.public_key in foreign:
            report.append(
                Said(
                    vm=vm,
                    text=f"{vm}: its WireGuard key is a peer's on {inputs.hub.instance} already, one not the"
                    " project's, so it is not joined: remove that peer, or the VM's key on it, and run again.",
                )
            )
            continue
        if not member.public_key:
            report.append(
                Said(
                    vm=vm,
                    text=f"{vm}: its WireGuard key was not read, so it is not joined this run: just deploy {vm}"
                    " --only configure --tags wireguard tries again.",
                )
            )
            continue
        address = addresses.get(vm, "")
        if not address:
            report.append(
                Said(
                    vm=vm,
                    text=f"{vm}: wireguard_vm_prefix ({inputs.vm_prefix}) has no address left for it: widen"
                    " the range, or destroy a VM it no longer needs.",
                )
            )
            continue
        old = [mark for mark in member.marks if mark.startswith(common.WIREGUARD)]
        kept = [mark for mark in old if common.mark_address(mark) == address]
        # Reached: the VM has shaken hands with its hub at this address, this run or a run before it.
        reached = bool(kept) and (member.handshake or any(common.mark_reached(mark) for mark in kept))
        new = common.wireguard_mark(address, reached)
        if old != [new]:
            marks.append(
                MarkChange(
                    vm=member.vm,
                    add=(new,),
                    remove=tuple(mark for mark in old if mark != new),
                    # Proxmox's record is what holds the address: a run that cannot write it says that another
                    # VM may be given the address until it is written.
                    unwritten=(
                        f"{vm}: Proxmox could not record its WireGuard address, {address} ({{reason}}). Until it does,"
                        f" the address is not held for {vm}, and a VM deployed meanwhile may be given it too: run"
                        f" just deploy {vm} --only configure first."
                    ),
                    matter=ADDRESS,
                )
            )
        # Under vpn_reach: tunnel the VM answers whatever reached it at its tunnel address, from wherever, back
        # through the tunnel, so the hub's peer is every address; under lan, only the hub's network is routed to it.
        tunnels.append(
            Tunnel(
                vm=member.vm,
                address=f"{address}/{network.prefixlen}",
                hub_public_key=inputs.hub.public_key,
                endpoint=inputs.hub.endpoint,
                allowed_ips=(str(network),) if inputs.reach == "lan" else ("0.0.0.0/0",),
                keepalive=inputs.keepalive,
                reach=inputs.reach,
            )
        )
        # Under tunnel, the alias only once the VM has reached its hub at the address: before, it would lead nowhere,
        # and ssh and the editor with it.
        moves = ""
        if inputs.reach == "tunnel" and reached:
            aliases.append(AliasAddress(vm=member.vm, address=address))
        elif inputs.reach == "tunnel":
            moves = f"just deploy {vm} --only configure moves its SSH alias to {address} once it has reached the server"
        if api:
            change, said, left = _hub_peer(inputs, member, address)
            peers += [change] if change else []
            report.append(
                Said(vm=vm, text=f"{vm}: on WireGuard at {address}; {said}." + (f" {moves}." if moves else ""))
            )
            left = left or (f"{vm}: {moves}." if moves else "")
            steps.append(NextStep(vm=member.vm, matter=PEER, action=None, text=left))
            continue
        peers.append(
            PeerWrite(
                kind="add",
                name=common.peer_name(inputs.tag, vm),
                public_key=member.public_key,
                address=f"{address}/32",
                vm=member.vm,
            )
        )
        if member.handshake:
            report.append(
                Said(vm=vm, text=f"{vm}: on WireGuard at {address}, its hub reached." + (f" {moves}." if moves else ""))
            )
            steps.append(NextStep(vm=member.vm, matter=PEER, action=None, text=f"{vm}: {moves}." if moves else ""))
        else:
            report.append(
                Said(
                    vm=vm,
                    text=f"{vm}: on WireGuard at {address} once your server has its peer. Add it there:"
                    f" {_peer_block(inputs, member, address)}. just status {vm} says when it has connected"
                    + (f"; {moves}." if moves else "."),
                )
            )
            steps.append(
                NextStep(
                    vm=member.vm,
                    matter=PEER,
                    action=None,
                    text=f"{vm}: add its peer to your WireGuard server - PublicKey {member.public_key}, AllowedIPs"
                    f" {address}/32 - then just status {vm} says when it has connected"
                    + (f"; {moves}." if moves else "."),
                )
            )
    if api:
        peers += _pruned(inputs, report)
    return MemberPlan(
        vpn=vpn.key,
        marks=tuple(marks),
        peers=tuple(peers),
        tunnels=tuple(tunnels),
        aliases=tuple(aliases),
        report=tuple(report),
        next_steps=tuple(steps),
    )


def _pruned(inputs: MemberInputs, report: list[Said]) -> list[PeerWrite]:
    """The project's peers on the hub whose VM is gone: only from a complete listing of the hub and of the fleet,
    since a VM missing from a partial one is not gone."""
    if not inputs.hub.listed or not inputs.fleet:
        return []
    alive = {vm.name for vm in inputs.fleet} | {member.vm.name for member in inputs.members}
    gone = []
    for peer in inputs.hub.peers:
        vm = peer.name[len(inputs.tag) + 1 :]
        if _owned(inputs, peer) and vm not in alive:
            gone.append(PeerWrite(kind="delete", name=peer.name, ref=peer.ref, address=peer.addresses[0]))
            report.append(
                Said(vm="", text=f"{peer.name}: its VM is gone, so its peer is removed from {inputs.hub.instance}.")
            )
    return gone


def _release(vpn: Vpn, inputs: MemberInputs) -> MemberPlan:
    peers, report, steps = [], [], []
    for member in inputs.members:
        marked = [common.mark_address(mark) for mark in member.marks if common.mark_address(mark)]
        if not member.leaving or not marked:
            continue
        name = common.peer_name(inputs.tag, member.vm.name)
        if inputs.hub.kind == "opnsense":
            # Removed on the hub, by the peer it has under the VM's name with every mark of the project's; one it
            # does not have is gone already, and the next fleet-wide configure removes one this run could not read.
            mine = [peer for peer in inputs.hub.peers if peer.name == name and _owned(inputs, peer)]
            if inputs.hub.listed and mine:
                peers.append(
                    PeerWrite(kind="delete", name=name, ref=mine[0].ref, address=f"{marked[0]}/32", vm=member.vm)
                )
                report.append(
                    Said(
                        vm=member.vm.name,
                        text=f"{member.vm.name}: its peer {name} is removed from {inputs.hub.instance}.",
                    )
                )
            steps.append(NextStep(vm=member.vm, matter=PEER, action=None))
            continue
        peers.append(PeerWrite(kind="delete", name=name, address=f"{marked[0]}/32", vm=member.vm))
        report.append(
            Said(
                vm=member.vm.name,
                text=f"{member.vm.name}: remove its peer from your WireGuard server, {name}, AllowedIPs {marked[0]}/32:"
                " its key went with the VM, so the peer can never connect again, but it holds the address.",
            )
        )
        steps.append(
            NextStep(
                vm=member.vm,
                matter=PEER,
                action=None,
                text=f"{member.vm.name}: remove its peer, {name} (AllowedIPs {marked[0]}/32), from your WireGuard"
                " server.",
            )
        )
    return MemberPlan(vpn=vpn.key, peers=tuple(peers), report=tuple(report), next_steps=tuple(steps))


def plan_members(vpn: Vpn, inputs: MemberInputs) -> MemberPlan:
    if inputs.phase == "release":
        return _release(vpn, inputs)
    refused = _refusal(inputs)
    if refused:
        return MemberPlan(vpn=vpn.key, refusals=(refused,))
    return _configure(vpn, inputs)
