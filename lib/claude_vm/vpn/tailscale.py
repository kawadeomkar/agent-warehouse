"""Tailscale's family: the record, kept in Proxmox, that a VM's Tailscale has held a login. Tailscale's coordination
server keeps its own list of members, so nothing else is planned here: the role on each VM joins it, and its release
deletes the device (docs/architecture/roles.md §5.8). A module of free functions, never a class."""

from __future__ import annotations

import re
from typing import Final

from . import common
from .model import MarkChange, MemberInputs, MemberPlan, Vpn

# The matter a record Proxmox could not write is kept under for NEXT STEPS, beside the role's own report.
MATTER: Final = "tailscale_record"


def tailscale_label(name: str) -> str:
    """A VM's name made one DNS label, the machine name the role asks Tailscale for (tailscale_hostname's default):
    lowercased, each run of anything but a letter, a digit or "-" made one "-", no "-" at either end, at most 63."""
    label = re.sub(r"[^a-z0-9-]+", "-", str(name).lower())
    return re.sub(r"-+\Z", "", re.sub(r"^-+|-+\Z", "", label)[:63])


def tailscale_sibling_labels(others, hostname: str, name: str) -> list[str]:
    """The machine names the VMs named ``others`` join as, each made from its name as this VM's ``hostname`` is made
    from its own ``name``: with whatever this VM's machine name has around its name's label (dev-web for web gives
    dev-web-2 for web-2), each place it has it there.

    ``hostname`` itself among them when nothing tells the devices apart: a machine name with no label of the VM's name
    in it (one name for every VM), or another VM's coming out the same (alpha.lab and alpha-lab). None with no others.
    """
    if not isinstance(others, (list, tuple)):
        raise ValueError("tailscale_sibling_labels expects the other VMs' names as a list")
    own, label = str(hostname).lower(), tailscale_label(name)
    names = {tailscale_label(other) for other in others} - {""}
    if not names:
        return []
    around = [(own[:at], own[at + len(label) :]) for at in range(len(own)) if label and own.startswith(label, at)]
    if not around:
        return [own]
    return sorted({tailscale_label(before + other + after) for before, after in around for other in names})


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
