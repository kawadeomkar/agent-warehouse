"""What more than one VPN family, or the registry, knows: the marks a family may keep, and the NEXT STEPS matters its
records go under."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
from collections.abc import Mapping
from typing import Final

from .model import FamilyName

# The mark a Tailscale that has held a login leaves, <tag>-tailscale: the vault's key is never sent to its VM again.
# Written on VMs, so it stays (tests/unit/test_persisted_names.py).
TAILSCALE: Final = "tailscale"
# Each family's NEXT STEPS matters (lib/claude_vm/run/next_steps.py's MATTERS), the only ones its plans record under.
MATTERS: Final[Mapping[FamilyName, frozenset[str]]] = {
    "tailscale": frozenset({"tailscale_record"}),
    "wireguard": frozenset({"wireguard_address", "wireguard_peer"}),
}
# The mark of a VM's WireGuard address, wg-<address>: the address it keeps for its life, wherever its hub is; and
# wg-<address>-reached once the VM has reached its hub at that address, from when an SSH alias may use it.
WIREGUARD: Final = "wg-"
REACHED: Final = "-reached"
# A peer's name as every hub takes it (OPNsense's Client.xml: 1 to 64 of these).
PEER_NAME: Final = re.compile(r"[0-9A-Za-z._-]{1,64}")


def wireguard_mark(address: str, reached: bool = False) -> str:
    return f"{WIREGUARD}{address}{REACHED if reached else ''}"


def mark_address(mark: str) -> str:
    """The address a WireGuard mark holds, or '' for a mark that holds none."""
    if not mark.startswith(WIREGUARD):
        return ""
    try:
        return str(ipaddress.IPv4Address(mark[len(WIREGUARD) :].removesuffix(REACHED)))
    except ValueError:
        return ""


def mark_reached(mark: str) -> bool:
    """Whether a WireGuard mark says the VM has reached its hub at the address it holds."""
    return bool(mark_address(mark)) and mark.endswith(REACHED)


def public_key(value: str) -> bool:
    """A WireGuard public key: 32 bytes, in base64 (44 characters)."""
    try:
        return len(value) == 44 and len(base64.b64decode(value, validate=True)) == 32
    except (binascii.Error, ValueError):
        return False


def peer_name(tag: str, vm: str) -> str:
    """The name of a VM's peer on its hub: the ownership tag, a dash, the VM's name."""
    return f"{tag}-{vm}"


def marks_of(family: FamilyName, mark: str) -> bool:
    """Whether a mark is one the family keeps."""
    match family:
        case "tailscale":
            return mark == TAILSCALE
        case "wireguard":
            return bool(mark_address(mark))


def vpn_alias_address(lan: str, marks: object, vpn: str, reach: str, prefix: str = "") -> str:
    """The address a VM's SSH alias uses: its WireGuard address, from its mark in Proxmox, under vpn: wireguard and
    vpn_reach: tunnel once the VM has reached its hub there; its LAN address otherwise - before then too, when the
    address leads nowhere yet. Only an address of the VMs' range (prefix), and never one guest_ipv4 would refuse: a
    tag is whoever made the VM's to write, and an alias at 127.0.0.1 would send ssh to this machine."""
    if str(vpn) != "wireguard" or str(reach) != "tunnel":
        return lan
    try:
        range_ = ipaddress.IPv4Network(str(prefix))
    except ValueError:
        return lan
    for mark in marks if isinstance(marks, (list, tuple)) else ():
        if mark_reached(str(mark)):
            address = ipaddress.IPv4Address(mark_address(str(mark)))
            usable = not (
                address.is_loopback
                or address.is_link_local
                or address.is_unspecified
                or address.is_multicast
                or address.is_reserved
            )
            return str(address) if usable and address in range_ else lan
    return lan


def hub_network(value: str) -> ipaddress.IPv4Network:
    """The hub's tunnel network, from the network or from the hub's own address on it (198.51.100.1/24, as OPNsense
    keeps an instance's tunnel address)."""
    try:
        return ipaddress.IPv4Interface(value).network if "/" in value else ipaddress.IPv4Network(value)
    except ValueError as error:
        raise ValueError(f"wireguard_hub_network is not an IPv4 network such as 198.51.100.0/24: {value!r}") from error


def hub_address(value: str) -> ipaddress.IPv4Address | None:
    """The hub's own address on its tunnel network, where the network is given by it (198.51.100.1/24, as OPNsense
    keeps an instance's tunnel address): no VM is given it."""
    try:
        interface = ipaddress.IPv4Interface(value) if "/" in value else None
    except ValueError:
        return None
    if interface is None or interface.ip == interface.network.network_address:
        return None
    return interface.ip


def owned(
    tag: str, prefix: str, instance: str, name: str, addresses: tuple[str, ...], instances: tuple[str, ...]
) -> bool:
    """Whether a hub's peer is the project's: every mark of it - its name the ownership tag's, every address it holds
    inside the VMs' range, on the user's instance where the hub has instances. A peer with some marks but not all is
    nobody's to change."""
    if not name.startswith(f"{tag}-") or not addresses or not prefix:
        return False
    range_ = ipaddress.IPv4Network(prefix)
    try:
        inside = all(ipaddress.IPv4Network(address, strict=False).subnet_of(range_) for address in addresses)
    except (ValueError, TypeError):
        return False
    return inside and (not instance or instance in instances)
