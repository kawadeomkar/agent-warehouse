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


def vpn_alias_address(lan: str, marks: object, vpn: str, reach: str) -> str:
    """The address a VM's SSH alias uses: its WireGuard address, from its mark in Proxmox, under vpn: wireguard and
    vpn_reach: tunnel once the VM has reached its hub there; its LAN address otherwise - before then too, when the
    address leads nowhere yet."""
    if str(vpn) == "wireguard" and str(reach) == "tunnel":
        for mark in marks if isinstance(marks, (list, tuple)) else ():
            if mark_reached(str(mark)):
                return mark_address(str(mark))
    return lan
