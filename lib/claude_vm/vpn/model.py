"""The VPNs' records: a row of claude_vpns, the VMs a run plans and the hub it reads, and the plan of who is on the
network a family returns. Stdlib only; every record holds its fields to their annotations as it is built
(docs/architecture/custom-code.md §7.11)."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Literal

from ..catalogue import rows_from
from ..records import Checked, plain
from ..refs import NAME, NextStep, VmRef

FamilyName = Literal["tailscale", "wireguard"]  # one value per family module
Reach = Literal["lan", "tunnel"]  # the guest agent's address, or the VPN's
HubKind = Literal["none", "manual", "opnsense"]  # who administers the members: no one the plan writes to, or a hub
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# A mark: a fact about a VM that the VM cannot change, kept beside the ownership tag as <tag>-<mark>.
MARK = re.compile(r"tailscale|wg-(?:[0-9]{1,3}\.){3}[0-9]{1,3}")


def ipv4_network(value: str, what: str) -> ipaddress.IPv4Network:
    """An IPv4 network as written, its host bits zero: 198.51.100.128/25."""
    try:
        return ipaddress.IPv4Network(value)
    except ValueError as error:
        raise ValueError(f"{what} is not an IPv4 network such as 198.51.100.128/25: {value!r}") from error


@dataclass(frozen=True, slots=True, kw_only=True)
class Vpn(Checked):
    """One row of the inventory defaults' claude_vpns."""

    key: str
    name: str
    family: FamilyName
    role: str  # the role that runs it on each VM
    sign_in: bool  # a sign-in of its own, a stage of just login and just deploy
    reach: tuple[Reach, ...]  # the addresses an SSH alias can reach it at

    def validate(self) -> None:
        if not WORD.fullmatch(self.key) or not WORD.fullmatch(self.role):
            raise ValueError(f"VPN {self.key!r}: its key and role are plain words")
        if "lan" not in self.reach:
            raise ValueError(f"VPN {self.key!r}: every VPN is reached at the guest agent's address too")


@dataclass(frozen=True, slots=True, kw_only=True)
class Member(Checked):
    """One VM a run plans, as Proxmox and its roles left it: its name and VM ID from Proxmox, never from the VM."""

    vm: VmRef
    leaving: bool = False  # being destroyed
    marks: tuple[str, ...] = ()  # its marks as Proxmox holds them, the ownership tag's prefix taken off
    login_seen: bool = (
        False  # Tailscale: its role saw a login on the Tailscale it installed (tailscale_login_to_record)
    )
    public_key: str = ""  # WireGuard: what its role's key entry point read on the VM; '' where it was not read
    handshake: bool = False  # WireGuard: its link has shaken hands with the hub, as its role read it
    lan: str = ""  # WireGuard: the network of the VM's own address, where it was read

    def validate(self) -> None:
        if not all(map(MARK.fullmatch, self.marks)):
            raise ValueError(f"{self.vm.name}: not a mark among {self.marks!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class HubPeer(Checked):
    """A peer as the hub listed it: never a private key, which the hub's reader drops."""

    ref: str  # the hub's own handle for it
    name: str
    public_key: str
    addresses: tuple[str, ...]  # its allowed IPs on the hub, as networks
    instances: tuple[str, ...] = ()  # the instances it is a peer of, by name, where the hub has them


@dataclass(frozen=True, slots=True, kw_only=True)
class Hub(Checked):
    """Where the members are administered, as this run read it."""

    kind: HubKind = "none"
    listed: bool = False  # every peer was read: no peer is removed without it
    instance: str = ""  # the user's instance, by name, where the hub has them
    network: str = ""  # the tunnel network the VMs route into it
    public_key: str = ""
    endpoint: str = ""  # host:port, what the VMs dial
    peers: tuple[HubPeer, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class MemberInputs(Checked):
    """What a run plans with: grows by fields with defaults, never by new parameters."""

    phase: Literal["configure", "release"]
    tag: str  # the ownership tag, as Proxmox stores it: lowercased
    members: tuple[Member, ...]
    fleet: tuple[VmRef, ...] = ()  # every tagged VM of a complete listing, for pruning; empty, none is pruned
    hub: Hub = Hub()
    vm_prefix: str = ""  # the range of the hub's network reserved for the VMs
    taken: tuple[str, ...] = ()  # the addresses VMs outside the members hold, from Proxmox's listing
    reach: Reach = "lan"
    keepalive: int = 25

    def validate(self) -> None:
        if not NAME.fullmatch(self.tag) or self.tag != self.tag.lower():
            raise ValueError(f"not an ownership tag as Proxmox stores it: {self.tag!r}")
        names = [member.vm.name for member in self.members]
        if len(set(names)) != len(names):
            raise ValueError(f"a VM named twice among the members: {names!r}")
        if self.vm_prefix:
            ipv4_network(self.vm_prefix, "the VM prefix")


@dataclass(frozen=True, slots=True, kw_only=True)
class MarkChange(Checked):
    """One VM's marks to add and to take off, and what is said, and kept for NEXT STEPS, if Proxmox cannot write them:
    unwritten with {reason} where Proxmox's own words go."""

    vm: VmRef
    add: tuple[str, ...] = ()
    remove: tuple[str, ...] = ()
    unwritten: str
    matter: str  # the NEXT STEPS matter it is kept under

    def validate(self) -> None:
        if not all(map(MARK.fullmatch, (*self.add, *self.remove))) or set(self.add) & set(self.remove):
            raise ValueError(f"{self.vm.name}: not a change of marks: +{self.add!r} -{self.remove!r}")
        if "{reason}" not in self.unwritten:
            raise ValueError(f"{self.vm.name}: what an unwritten mark says has no place for Proxmox's reason")


@dataclass(frozen=True, slots=True, kw_only=True)
class PeerWrite(Checked):
    """A change to the hub's peers: an add, an update of the peer the hub calls ref, or a delete of it."""

    kind: Literal["add", "update", "delete"]
    name: str
    ref: str = ""
    public_key: str = ""
    address: str = ""  # the one address it is allowed, as a /32
    vm: VmRef | None = None  # the VM it is for; None for a peer pruned because its VM is gone


@dataclass(frozen=True, slots=True, kw_only=True)
class Tunnel(Checked):
    """What a VM's VPN role writes on it: its address on the tunnel and the one peer it has, the hub."""

    vm: VmRef
    address: str  # the VM's, as an interface address: 198.51.100.130/24
    hub_public_key: str
    endpoint: str
    allowed_ips: tuple[str, ...]
    keepalive: int


@dataclass(frozen=True, slots=True, kw_only=True)
class AliasAddress(Checked):
    """The address a VM's SSH alias uses, under vpn_reach: tunnel."""

    vm: VmRef
    address: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Said(Checked):
    """One line of the run's report, about one VM by name."""

    vm: str
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MemberPlan(Checked):
    """Who is on the network, as data: what is refused, marked, written on the hub, set up on each VM and said. YAML
    applies it."""

    vpn: str
    refusals: tuple[str, ...] = ()  # beside a refusal the registry keeps nothing else
    marks: tuple[MarkChange, ...] = ()
    peers: tuple[PeerWrite, ...] = ()
    tunnels: tuple[Tunnel, ...] = ()
    aliases: tuple[AliasAddress, ...] = ()
    report: tuple[Said, ...] = ()
    next_steps: tuple[NextStep, ...] = ()


def vpns_from(raw: object) -> dict[str, Vpn]:
    """The inventory defaults' claude_vpns, as tools/settings reads it: parsed on every call."""
    return rows_from(raw, "claude_vpns", lambda key, row: Vpn(key=key, **row))


def inputs_from(raw: object) -> MemberInputs:
    """members.yml's inputs, copied out of Ansible."""
    fields = plain(raw, "inputs")
    hub = plain(fields.get("hub", {}), "inputs.hub")
    records = {
        "members": tuple(Member(**(member | {"vm": VmRef(**member["vm"])})) for member in fields.get("members", ())),
        "fleet": tuple(VmRef(**vm) for vm in fields.get("fleet", ())),
        "hub": Hub(**(hub | {"peers": tuple(HubPeer(**peer) for peer in hub.get("peers", ()))})),
    }
    return MemberInputs(**(fields | records))
