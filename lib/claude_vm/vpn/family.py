"""The seam every VPN family meets: one function, the plan of who is on the network from the row and the inputs."""

from __future__ import annotations

from typing import Protocol

from .model import MemberInputs, MemberPlan, Vpn


class Family(Protocol):
    """A module of free functions, never a class; not @runtime_checkable: nothing checks it at run time, and the
    registry's annotation is the declaration a type checker reads."""

    def plan_members(self, vpn: Vpn, inputs: MemberInputs) -> MemberPlan: ...
