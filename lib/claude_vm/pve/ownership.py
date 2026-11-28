"""What is the fleet's: the one rule a member of a host's pool is judged by, and the types a change is made on
(docs/architecture/engine.md §16.5).

Ownership is pool membership and a valid record of this engine's in the VM's notes, never a label. `refusal(member)` is
the one check: why a member read from a host's pool is not the fleet's to change, "" where it is, decided from what was
read of it alone (a `Member`), whichever reader read it - engine/fleet.py's, which every changing workflow reads
through, and status's own (card G16), which may not import the engine (card G15's boundary) and judges by this same
function, so that the two never part. It refuses a guest that is no Proxmox VM; a template, which is never a target; a
member whose configuration or record could not be read, one the token holds NoAccess on among them, as its reader says
why; one whose notes hold no record of this engine's, whatever tags it carries (but for a deletion under destroy's
override, `unrecorded`, which lifts that one refusal and no other: card G5); a record of kind template, golden or
build on a VM that is no template, and one that names another VM's UUID, each a copy (a clone sent without a description
carries its source's notes); a VM's record that names no stage; a record that names no UUID, but the first form of the
clone a run made, which that run alone may own, by its own job, which the record names, and its own clone's task, whose
VMID is the member's (`job`, `clone`), before it writes the rest; a VM with no SMBIOS UUID, which is its identity; and
two members with one UUID, both (a restore without unique keeps its source's).

A `Member` the rule may pass is one a reader made from what it read: `Member.read(config, twins=)`, from a VM's
configuration as the client read it (answers.VmConfig) and the record its notes hold, as pve/record.py's parse reads it,
whichever reader asks, and the other members of the pool that carry its UUID, which its reader read beside it and must
say (no default: one configuration knows nothing of the pool). It marks the member read (a field no constructor takes,
which dataclasses.replace and copy.replace leave unset), and `owned` and `building` refuse a Member without that mark,
TypeError: one made by hand, or changed after its read, holds whatever its maker wrote, and the record is the whole of
the rule, so writing a field would be writing the verdict. A Member made by hand still reads as refusal() judges it, for
a sentence. The mark says a configuration was read, not that the pool was, so in lib/ only the reader of the pool calls
Member.read, engine/fleet.py's (and status's, card G16): a workflow owns a VM again through that reader, never through
Member.read of one configuration (tests/unit/test_aw_ownership.py's scan).

`owned(member)` returns an `Owned`, which nothing else makes: the VM a change may be made on, by its node and VMID, with
its name, its SMBIOS UUID and the digest of the configuration the decision was made from. Made by hand it is a
TypeError, and so is a copy made by `dataclasses.replace`; `NotOwned`, saying why, where `refusal` refuses the member.
The client's writes of a VM (pve/client.py) and engine/record.py's writer take an Owned and refuse anything else, a
read's target above all, and hold the VM at its VMID to the Owned's: each write that reads the VM's configuration first
holds its identity to the Owned's, a deletion its digest too, and a configuration's write sends the Owned's digest, so
that Proxmox refuses it where the configuration is no longer the one the decision was made on, another VM's at that
VMID above all (docs/architecture/failure-modes.md, "VMID allocation is racy").

`building(member, job=, clone=)` returns a `Building`, which nothing else makes either: the VM a golden image is built
in (card E18), the one thing a conversion to a template is made on (pve/client.py's `to_template`), from a fresh read of
a VM whose record is of kind build, or golden once the run has written the golden's record on it, and names its own
UUID, whose job is the asking run's own and whose VMID its own clone's task made; never a template, never a VM of the
fleet (a record of kind vm), never another run's build VM, never a clone of a golden (whose record names the golden's
UUID). E18's order is today's: the golden's record written on the build VM, read back as a clone would read it, the
Building made from that read, and converted, so that the conversion holds the digest of the configuration that holds
the golden's record.
`build_refusal` says why not, as `refusal` does for `owned`. The two never make one another: an Owned is never
converted, and a Building is never written as a VM of the fleet is.

Plain code: the standard library, and of the package pve/record.py's rules and pve/answers.py's records of what was read
alone; nothing of engine/, and not the client, which imports it (tests/unit/test_aw_ownership.py).
"""

from __future__ import annotations

import dataclasses

from . import record as block
from .answers import Task, VmConfig

__all__ = [
    "IDENTITY",
    "KIND",
    "Building",
    "Member",
    "NotOwned",
    "Owned",
    "build_refusal",
    "building",
    "owned",
    "refusal",
]

# The kind of guest the fleet's members are: a Proxmox VM (card J3 adds the machine that is none).
KIND = "vm"
# The record's kind of a VM a golden image is built in, and of a golden image (pve/record.py's KINDS): the build VM
# carries the first until the run writes the second on it, reads it back as a clone would, and converts (card E18).
BUILD = "build"
GOLDEN = "golden"
# A VM's identity, its SSH host key's name: this prefix and its SMBIOS UUID (proxmox.ssh_host_key_alias).
IDENTITY = "agent-warehouse-"
# What an Owned made by anything but owned() is told.
MADE_BY_OWNED = (
    "an Owned is made by ownership.owned alone, from a member the one check passed: never by hand, nor by"
    " dataclasses.replace of one"
)
# What a Building made by anything but building() is told.
MADE_BY_BUILDING = (
    "a Building is made by ownership.building alone, from a build VM of the asking run's own: never by hand, nor by"
    " dataclasses.replace of one"
)
# What owned() and building() tell a Member no reader made, which nothing else refuses.
NOT_READ = (
    "a member is judged for a change only as a reader made it from what it read (Member.read): never one made by"
    " hand, nor one dataclasses.replace or copy.replace changed after its read"
)
# owned()'s and building()'s own keys to their types' constructors, which nothing else holds.
_MINT = object()
_BUILDING = object()


class NotOwned(Exception):
    """The member is not the fleet's to change, or not the asking run's build VM to make a template: why, as refusal()
    or build_refusal() says it, one sentence."""

    def __init__(self, sentence: str) -> None:
        self.sentence = sentence
        super().__init__(sentence)


@dataclasses.dataclass(frozen=True)
class Member:
    """A member of a host's pool as a reader read it, which is all the one check reads: its name, node and VMID; its
    SMBIOS UUID as its configuration gives it ("" for none) and that configuration's digest ("" where it was not read);
    its kind (vm, a Proxmox VM) and whether it is a template; why its configuration or record could not be read (""
    where they were); what its record names - its kind ("" where its notes hold no record of this engine's), its UUID,
    stage and job; and the VMIDs of the other members that carry its UUID. Whether a reader made it (Member.read) is
    its mark, which no constructor takes and a copy's change leaves unset: owned() and building() ask it."""

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
    _read: bool = dataclasses.field(default=False, init=False, repr=False, compare=False)

    @classmethod
    def read(cls, config: VmConfig, *, unread: str = "", twins: tuple[int, ...]) -> Member:
        """The member a reader read: its name, node, VMID, SMBIOS UUID, digest and template flag from its configuration
        as the client read it, and its record from its notes there, as pve/record.py's parse reads them (none for a
        template, which is never a target), a record parse cannot read making it unread. unread, why the reader could
        not use what it read where it knows (its own typed read refused the record, say), which only ever refuses it;
        twins, the VMIDs of the other members of the pool that carry its UUID, which the reader read beside it: a
        keyword with no default, since one configuration says nothing of the pool beside it, so that its reader says
        what it read there, () where no other member carries the UUID. The one maker of a Member owned() and building()
        judge, whichever reader asks: engine/fleet.py's reader of the pool alone in lib/, and status's (card G16)."""
        if not isinstance(config, VmConfig):
            raise TypeError("a member is read from its configuration as the client read it, an answers.VmConfig")
        if not isinstance(unread, str):
            raise TypeError("why a member could not be used is a sentence")
        if not isinstance(twins, tuple) or not all(isinstance(vmid, int) for vmid in twins):
            raise TypeError("a member's twins are the VMIDs that carry its UUID")
        found: dict[str, str | tuple[str, ...]] | None = None
        if not unread and not config.template:
            whose = f"VM {config.vmid}'s"
            try:
                found = block.parse(config.description)
            except block.Unreadable as refused:
                unread = f"{block.Unreadable(refused.why, whose)}: it is never changed or deleted"
        fields = found or {}
        member = cls(
            config.name,
            config.node,
            config.vmid,
            uuid=config.identity,
            digest=config.digest,
            kind=KIND,
            template=config.template,
            unread=unread,
            recorded=str(fields.get("kind") or ""),
            record_uuid=str(fields.get("uuid") or ""),
            stage=str(fields.get("stage") or ""),
            job=str(fields.get("job") or ""),
            twins=twins,
        )
        object.__setattr__(member, "_read", True)
        return member

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


@dataclasses.dataclass(frozen=True, init=False)
class Building:
    """A build VM of the asking run's own, which building() alone makes: the VM a golden image is built in, the one
    thing a conversion to a template is made on. Its name, its node and VMID, its SMBIOS UUID and the digest of the
    configuration it was judged on (the module's docstring)."""

    name: str
    node: str
    vmid: int
    uuid: str
    digest: str

    def __init__(self, *made: object, **given: object) -> None:
        if given or len(made) != 2 or made[1] is not _BUILDING or not isinstance(made[0], Member):
            raise TypeError(MADE_BY_BUILDING)
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
    def said(self) -> str:
        """The VM as a sentence names it: its name and VMID."""
        return f"{self.name} (VMID {self.vmid})" if self.name else f"the VM with no name (VMID {self.vmid})"


def _cloned(member: Member, clone: Task | None) -> bool:
    """Whether clone is the task of a clone that made member: a clone's Task, as the asking run's own clone call gave
    it (pve/client.py's clone) or its own job record keeps it, for member's node and VMID. Never read from the member
    or its record, which anyone may write."""
    return isinstance(clone, Task) and clone.kind == "clone" and clone.vmid == member.vmid and clone.node == member.node


def _twins(member: Member) -> str:
    """The sentence that refuses a member that carries one UUID with others, "" for one that carries its own alone."""
    if not member.twins:
        return ""
    others = ", ".join(str(vmid) for vmid in member.twins)
    return (
        f"{member.said} carries one UUID with VMID {others}, as a restore without unique leaves them, so nothing tells"
        " them apart: none of them is changed"
    )


def refusal(member: Member, job: str = "", clone: Task | None = None, *, unrecorded: bool = False) -> str:
    """Why member is not the fleet's to change, "" where it is: the one check, decided from what was read of it alone,
    never from a label (see the module's docstring). job and clone are the asking run's own: its job's ID, and the task
    its own clone call answered (or its own job record keeps), given only by the run that cloned the member, whose
    first form names that job and is at that clone's VMID; never read from the member or its record. unrecorded is
    destroy's override alone (destroy_allow_unrecorded, card G5): it lifts the one refusal of a member whose notes hold
    no record of this engine's, and nothing else - a template, a member unread, one with no SMBIOS UUID, which its
    deletion is held to, and twins are refused all the same, and a record of any form is judged as ever."""
    said = member.said
    if member.kind != KIND:
        return f"{said} is no Proxmox VM, which is all this host's runs change"
    if member.template:
        return f"{said} is a template, which is never a target"
    if member.unread:
        return member.unread
    if not member.recorded:
        if unrecorded is not True:
            return (
                f"{said} is a member of the pool with no record of this engine's in its notes, so this fleet did not"
                " make it, whatever tags it carries: it is never changed or deleted"
            )
        if not member.uuid:
            return (
                f"{said} has no record of this engine's and no SMBIOS UUID in its configuration, which its deletion is"
                " held to: it is never deleted"
            )
        return _twins(member)
    if member.recorded != KIND:
        return (
            f"{said} holds a {member.recorded}'s record and is no template, so it is a copy of another's notes (a clone"
            " sent without a description carries its source's record): it is never changed or deleted"
        )
    if not member.stage:
        return f"{said}'s record names no stage, which a VM's record does: it is never changed or deleted"
    if not member.record_uuid:
        first = block.first_form({"kind": member.recorded, "stage": member.stage})
        if not first:
            return (
                f"{said}'s record names no UUID, which only a VM's first form, at the stage cloning, may: it is never"
                " changed or deleted"
            )
        if not (job and member.job == job and _cloned(member, clone)):
            return (
                f"{said}'s record is still the first form its clone carries, which names no UUID: only the run that"
                " cloned it, by its own job and its own clone's task, writes the rest, and nothing else tells it from a"
                f" copy. Once that run has ended, nothing will: delete VMID {member.vmid} in Proxmox, then run this"
                " again"
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
    return _twins(member)


def owned(member: Member, *, job: str = "", clone: Task | None = None, unrecorded: bool = False) -> Owned:
    """The Owned a change on member is made through, where the fleet may change it: NotOwned, saying why, where
    refusal() says it may not; TypeError for a member no reader made (Member.read), which it passes. job and clone: the
    asking run's own job and its own clone's task, which own the first form of the clone it made (refusal());
    unrecorded, destroy's override alone (refusal()), whose Owned is a deletion's: engine/destroy.py asks for it, and
    engine/fleet.py carries it here, the one road in lib/ (tests/unit/test_aw_destroy.py's scan). The one gate every
    change passes, and the one maker of an Owned."""
    if not isinstance(member, Member):
        raise TypeError("owned() judges a Member, what a reader read of a member of the pool")
    why = refusal(member, job, clone, unrecorded=unrecorded)
    if why:
        raise NotOwned(why)
    if not member._read:
        raise TypeError(NOT_READ)
    return Owned(member, _MINT)


def build_refusal(member: Member, job: str = "", clone: Task | None = None) -> str:
    """Why member is not a build VM of the asking run's own, which it may make a template, "" where it is: a VM that is
    no template, whose record is of kind build, or golden once the run has written the golden's record on it (card
    E18's order: the golden's record written, read back as a clone would read it, the Building made from that read,
    then the conversion, which holds that read's digest), and names its own UUID and the asking run's job, at the VMID
    the asking run's own clone made (job and clone, as refusal() takes them), carrying its UUID alone. A clone of a
    golden carries the golden's record, which names the golden's UUID, not its own: a copy, refused."""
    said = member.said
    if member.kind != KIND:
        return f"{said} is no Proxmox VM, which is all this host's runs change"
    if member.template:
        return f"{said} is a template already, which is never made one again"
    if member.unread:
        return member.unread
    if not member.recorded:
        return (
            f"{said} has no record of this engine's in its notes, so it is no build VM of this engine's: it is never"
            " made a template"
        )
    if member.recorded == KIND:
        return f"{said} holds a VM's record, a VM of the fleet, which is never made a template"
    if member.recorded not in (BUILD, GOLDEN):
        return (
            f"{said} holds a {member.recorded}'s record and is no template, so it is a copy of another's notes: it is"
            " never made a template"
        )
    if not member.uuid:
        return (
            f"{said} has no SMBIOS UUID in its configuration, which is a VM's identity here: set an smbios1 UUID for it"
            " in Proxmox"
        )
    if not member.record_uuid or block.copied({"uuid": member.record_uuid}, member.uuid):
        return (
            f"{said}'s {member.recorded} record does not name its own UUID, so it is a copy of another's notes (a clone"
            " of a golden carries the golden's): it is never made a template"
        )
    if not (job and member.job == job):
        return (
            f"{said} is the build VM of another run's job, or of none: only the run whose job its record names makes"
            " it a template"
        )
    if not _cloned(member, clone):
        return f"{said} is not the VM the asking run's own clone made: it is never made a template"
    return _twins(member)


def building(member: Member, *, job: str, clone: Task) -> Building:
    """The Building a conversion of member to a template is made on, where it is a build VM of the asking run's own:
    NotOwned, saying why, where build_refusal() says it is not; TypeError for a member no reader made (Member.read).
    job and clone: the asking run's own job and its own clone's task (refusal()). The one maker of a Building."""
    if not isinstance(member, Member):
        raise TypeError("building() judges a Member, what a reader read of a member of the pool")
    why = build_refusal(member, job, clone)
    if why:
        raise NotOwned(why)
    if not member._read:
        raise TypeError(NOT_READ)
    return Building(member, _BUILDING)
