"""What is the fleet's: the one rule a member of a host's pool is judged by, and the type a change is made on
(docs/architecture/engine.md §16.5).

Ownership is pool membership and a valid record of this engine's in the VM's notes, never a label. `refusal(member)` is
the one check: why a member read from a host's pool is not the fleet's to change, "" where it is, decided from what was
read of it alone (a `Member`), whichever reader read it - engine/fleet.py's, which every changing workflow reads
through, and status's own (card G16), which may not import the engine (card G15's boundary) and judges by this same
function, so that the two never part. It refuses a guest that is no Proxmox VM; a template, which is never a target; a
member whose configuration or record could not be read, one the token holds NoAccess on among them, as its reader says
why; one whose notes hold no record of this engine's, whatever tags it carries; a record of kind template, golden or
build on a VM that is no template, and one that names another VM's UUID, each a copy (a clone sent without a description
carries its source's notes); a record that names no UUID, but the first form of the clone a run made, which the run
whose job the record names may own by naming that job (`job`), before it writes the rest; a VM with no SMBIOS UUID,
which is its identity; and two members with one UUID, both (a restore without unique keeps its source's).

`owned(member)` returns an `Owned`, which nothing else makes: the VM a change may be made on, by its node and VMID, with
its name, its SMBIOS UUID and the digest of the configuration the decision was made from. Made by hand it is a
TypeError, and so is a copy made by `dataclasses.replace`; `NotOwned`, saying why, where `refusal` refuses the member.
The client's writes of a VM (pve/client.py) and engine/record.py's writer take an Owned and refuse anything else, a
read's target above all: the client holds the VM's identity to the Owned's again wherever it reads the VM's
configuration, and before a deletion holds both its identity and its digest to a configuration read again, since
Proxmox's DELETE takes no digest and a VMID can be another VM's between a read and the deletion
(docs/architecture/failure-modes.md, "VMID allocation is racy").

Plain code: the standard library, and of the package pve/record.py's rules alone; nothing of engine/, and not the
client, which imports it (tests/unit/test_aw_ownership.py).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from . import record as block

__all__ = ["IDENTITY", "KIND", "Member", "NotOwned", "Owned", "owned", "refusal"]

# The kind of guest the fleet's members are: a Proxmox VM (card J3 adds the machine that is none).
KIND = "vm"
# A VM's identity, its SSH host key's name: this prefix and its SMBIOS UUID (proxmox.ssh_host_key_alias).
IDENTITY = "agent-warehouse-"
# What an Owned made by anything but owned() is told.
MADE_BY_OWNED = (
    "an Owned is made by ownership.owned alone, from a member the one check passed: never by hand, nor by"
    " dataclasses.replace of one"
)
# owned()'s own key to the constructor, which nothing else holds.
_MINT = object()


class NotOwned(Exception):
    """The member is not the fleet's to change: why, as refusal() says it, one sentence."""

    def __init__(self, sentence: str) -> None:
        self.sentence = sentence
        super().__init__(sentence)


@dataclasses.dataclass(frozen=True)
class Member:
    """A member of a host's pool as a reader read it, which is all the one check reads: its name, node and VMID; its
    SMBIOS UUID as its configuration gives it ("" for none) and that configuration's digest ("" where it was not read);
    its kind (vm, a Proxmox VM) and whether it is a template; why its configuration or record could not be read (""
    where they were); what its record names - its kind ("" where its notes hold no record of this engine's), its UUID,
    stage and job; and the VMIDs of the other members that carry its UUID."""

    name: str
    node: str
    vmid: int
    uuid: str = ""
    digest: str = ""
    kind: str = KIND
    template: bool = False
    unread: str = ""
    recorded: str = ""
    record_uuid: str = ""
    stage: str = ""
    job: str = ""
    twins: tuple[int, ...] = ()

    @classmethod
    def of(
        cls,
        name: str,
        node: str,
        vmid: int,
        *,
        found: Mapping[str, object] | None,
        uuid: str = "",
        digest: str = "",
        kind: str = KIND,
        template: bool = False,
        unread: str = "",
        twins: tuple[int, ...] = (),
    ) -> Member:
        """The member whose record's fields are found, as pve/record.py's parse gives them or engine/record.py's
        Record.fields renders them (None where its notes hold no record of this engine's), with the rest of what was
        read of it."""
        fields: Mapping[str, object] = found or {}
        return cls(
            name,
            node,
            vmid,
            uuid=uuid,
            digest=digest,
            kind=kind,
            template=template,
            unread=unread,
            recorded=str(fields.get("kind") or ""),
            record_uuid=str(fields.get("uuid") or ""),
            stage=str(fields.get("stage") or ""),
            job=str(fields.get("job") or ""),
            twins=twins,
        )

    @property
    def said(self) -> str:
        """The member as a sentence names it: its name and VMID."""
        return f"{self.name} (VMID {self.vmid})" if self.name else f"the VM with no name (VMID {self.vmid})"


@dataclasses.dataclass(frozen=True, init=False)
class Owned:
    """A VM of the fleet a change may be made on, which owned() alone makes: its name, its node and VMID, its SMBIOS
    UUID and the digest of the configuration the one check passed it on. Every write of a VM's takes one (the module's
    docstring)."""

    name: str
    node: str
    vmid: int
    uuid: str
    digest: str

    def __init__(self, *made: object, **given: object) -> None:
        if given or len(made) != 2 or made[1] is not _MINT or not isinstance(made[0], Member):
            raise TypeError(MADE_BY_OWNED)
        member = made[0]
        for field, value in (
            ("name", member.name),
            ("node", member.node),
            ("vmid", member.vmid),
            ("uuid", member.uuid),
            ("digest", member.digest),
        ):
            object.__setattr__(self, field, value)

    @property
    def identity(self) -> str:
        """The VM's identity, the name its SSH host key is filed under: agent-warehouse-<its SMBIOS UUID>."""
        return f"{IDENTITY}{self.uuid}"

    @property
    def said(self) -> str:
        """The VM as a sentence names it: its name and VMID."""
        return f"{self.name} (VMID {self.vmid})" if self.name else f"the VM with no name (VMID {self.vmid})"


def refusal(member: Member, job: str = "") -> str:
    """Why member is not the fleet's to change, "" where it is: the one check, decided from what was read of it alone,
    never from a label (see the module's docstring). job is the job of the run that asks, given only by the run that
    cloned the member (or one that takes a lost run's clone over), whose first form names it."""
    said = member.said
    if member.kind != KIND:
        return f"{said} is no Proxmox VM, which is all this host's runs change"
    if member.template:
        return f"{said} is a template, which is never a target"
    if member.unread:
        return member.unread
    if not member.recorded:
        return (
            f"{said} is a member of the pool with no record of this engine's in its notes, so this fleet did not make"
            " it, whatever tags it carries: it is never changed or deleted"
        )
    if member.recorded != KIND:
        return (
            f"{said} holds a {member.recorded}'s record and is no template, so it is a copy of another's notes (a clone"
            " sent without a description carries its source's record): it is never changed or deleted"
        )
    if not member.record_uuid:
        first = block.first_form({"kind": member.recorded, "stage": member.stage})
        if not first:
            return (
                f"{said}'s record names no UUID, which only a VM's first form, at the stage cloning, may: it is never"
                " changed or deleted"
            )
        if not (job and member.job == job):
            return (
                f"{said}'s record is still the first form its clone carries, which names no UUID: the run that clones"
                " a VM writes the rest, and until then nothing tells it from a copy"
            )
        if not member.uuid:
            return (
                f"{said} has no SMBIOS UUID in its configuration, which is a VM's identity here: set an smbios1 UUID"
                " for it in Proxmox"
            )
    elif not member.uuid or block.copied({"uuid": member.record_uuid}, member.uuid):
        return (
            f"{said}'s record names another VM's UUID ({member.record_uuid}), so it is a copy of that VM's notes, as a"
            " clone carries its source's: it is never changed or deleted"
        )
    if member.twins:
        others = ", ".join(str(vmid) for vmid in member.twins)
        return (
            f"{said} carries one UUID with VMID {others}, as a restore without unique leaves them, so nothing tells"
            " them apart: none of them is changed"
        )
    return ""


def owned(member: Member, *, job: str = "") -> Owned:
    """The Owned a change on member is made through, where the fleet may change it: NotOwned, saying why, where
    refusal() says it may not. The one gate every change passes, and the one maker of an Owned."""
    if not isinstance(member, Member):
        raise TypeError("owned() judges a Member, what a reader read of a member of the pool")
    why = refusal(member, job)
    if why:
        raise NotOwned(why)
    return Owned(member, _MINT)
