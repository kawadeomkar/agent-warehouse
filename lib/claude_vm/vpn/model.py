"""The VPNs' records: a row of claude_vpns, the VMs a run plans, and the plan of who is on the network a family
returns. Stdlib only; every record holds its fields to their annotations as it is built
(docs/architecture/custom-code.md §7.11)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from ..catalogue import rows_from
from ..records import Checked, plain
from ..refs import NAME, NextStep, VmRef

FamilyName = Literal["tailscale"]  # one value per family module
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# A mark: a fact about a VM that the VM cannot change, kept beside the ownership tag as <tag>-<mark>. A tag of the
# project's that is no mark - such as the WireGuard address 2.0 kept, wg-<address> - is read as none.
MARK: Final = re.compile(r"tailscale")


@dataclass(frozen=True, slots=True, kw_only=True)
class Vpn(Checked):
    """One row of the inventory defaults' claude_vpns."""

    key: str
    name: str
    family: FamilyName
    role: str  # the role that runs it on each VM
    sign_in: bool  # a sign-in of its own, a stage of just login and just deploy

    def validate(self) -> None:
        if not WORD.fullmatch(self.key) or not WORD.fullmatch(self.role):
            raise ValueError(f"VPN {self.key!r}: its key and role are plain words")


@dataclass(frozen=True, slots=True, kw_only=True)
class Member(Checked):
    """One VM a run plans, as Proxmox and its roles left it: its name and VM ID from Proxmox, never from the VM."""

    vm: VmRef
    leaving: bool = False  # being destroyed
    marks: tuple[str, ...] = ()  # its marks as Proxmox holds them, the ownership tag's prefix taken off
    login_seen: bool = (
        False  # Tailscale: its role saw a login on the Tailscale it installed (tailscale_login_to_record)
    )

    def validate(self) -> None:
        if not all(map(MARK.fullmatch, self.marks)):
            raise ValueError(f"{self.vm.name}: not a mark among {self.marks!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class MemberInputs(Checked):
    """What a run plans with: grows by fields with defaults, never by new parameters."""

    phase: Literal["configure", "release"]
    tag: str  # the ownership tag, as Proxmox stores it: lowercased
    members: tuple[Member, ...]

    def validate(self) -> None:
        if not NAME.fullmatch(self.tag) or self.tag != self.tag.lower():
            raise ValueError(f"not an ownership tag as Proxmox stores it: {self.tag!r}")
        names = [member.vm.name for member in self.members]
        if len(set(names)) != len(names):
            raise ValueError(f"a VM named twice among the members: {names!r}")


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
class Said(Checked):
    """One line of the run's report, about one VM by name."""

    vm: str
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MemberPlan(Checked):
    """Who is on the network, as data: what is refused, marked and said. YAML applies it."""

    vpn: str
    refusals: tuple[str, ...] = ()  # beside a refusal the registry keeps nothing else
    marks: tuple[MarkChange, ...] = ()
    report: tuple[Said, ...] = ()
    next_steps: tuple[NextStep, ...] = ()


def vpns_from(raw: object) -> dict[str, Vpn]:
    """The inventory defaults' claude_vpns, as tools/settings reads it: parsed on every call."""
    return rows_from(raw, "claude_vpns", lambda key, row: Vpn(key=key, **row))


def inputs_from(raw: object) -> MemberInputs:
    """members.yml's inputs, copied out of Ansible."""
    fields = plain(raw, "inputs")
    members = tuple(Member(**(member | {"vm": VmRef(**member["vm"])})) for member in fields.get("members", ()))
    return MemberInputs(**(fields | {"members": members}))
