"""Every family's plan passes here, and leaves only as the core allows: a refusal alone; marks only for the inputs'
VMs, only of the family's own, never the Tailscale record taken off; NEXT STEPS of the family's own matters, for the
inputs' VMs alone. Anything else is a family's bug, a ValueError, which fails the first task that reads the plan,
before any write."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from ..catalogue import pick
from ..wire import to_wire
from . import common, tailscale
from .family import Family
from .model import FamilyName, MemberInputs, MemberPlan, Vpn, inputs_from, vpns_from

FAMILIES: Final[Mapping[FamilyName, Family]] = {"tailscale": tailscale}  # the check
_tailscale: Family = tailscale  # one line per family, so a type checker names the member that does not conform


def vpn_member_plan(key: object, rows: object, inputs: object) -> dict[str, Any]:
    """The plan of who is on the network of VPN key, from claude_vpns and members.yml's inputs, as the wire carries
    it."""
    vpns, given = vpns_from(rows), inputs_from(inputs)
    vpn = pick(key, vpns, "a VPN this project joins VMs to")
    return to_wire(checked(vpn, given, FAMILIES[vpn.family].plan_members(vpn, given)))


def checked(vpn: Vpn, inputs: MemberInputs, plan: MemberPlan) -> MemberPlan:
    if plan.vpn != vpn.key:
        raise ValueError(f"{vpn.key}'s plan is {plan.vpn}'s")
    if plan.refusals:  # a refusal marks and says nothing else
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
        if change.matter not in common.MATTERS[vpn.family]:
            raise ValueError(f"{vpn.key}'s plan keeps NEXT STEPS under {change.matter}, which is not its own")
    for said in plan.report:
        if said.vm not in members:
            raise ValueError(f"{vpn.key}'s plan says something of {said.vm!r}, which is no VM it was given")
    for step in plan.next_steps:
        member_of(step.vm)
        if step.matter not in common.MATTERS[vpn.family]:
            raise ValueError(f"{vpn.key}'s plan keeps NEXT STEPS under {step.matter}, which is not its own")
    return plan
