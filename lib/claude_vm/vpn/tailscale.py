"""Tailscale's family: the record, kept in Proxmox, that a VM's Tailscale has held a login. Tailscale's coordination
server keeps its own list of members, so nothing else is planned here: the role on each VM joins it, and its release
deletes the device (docs/architecture/roles.md §5.8). A module of free functions, never a class."""

from __future__ import annotations

from typing import Final

from . import common
from .model import MarkChange, MemberInputs, MemberPlan, Vpn

# The matter a record Proxmox could not write is kept under for NEXT STEPS, beside the role's own report.
MATTER: Final = "tailscale_record"


def _unwritten(vm: str) -> str:
    return (
        f"{vm}: Proxmox could not record that it joined Tailscale ({{reason}}), so vault_tailscale_auth_key could be"
        f" sent to it again if it is logged out. The next just deploy {vm} --only configure records it."
    )


def plan_members(vpn: Vpn, inputs: MemberInputs) -> MemberPlan:
    if inputs.phase != "configure":
        return MemberPlan(vpn=vpn.key)
    # Each VM whose role saw a login on the Tailscale it installed, its own, one made by hand or one expired, or a
    # sign-in under way, and that has no record yet: a login made by hand and then logged out reads like none too.
    return MemberPlan(
        vpn=vpn.key,
        marks=tuple(
            MarkChange(vm=member.vm, add=(common.TAILSCALE,), unwritten=_unwritten(member.vm.name), matter=MATTER)
            for member in inputs.members
            if member.login_seen and not member.leaving and common.TAILSCALE not in member.marks
        ),
    )
