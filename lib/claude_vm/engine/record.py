"""A VM's record, typed: the one place a VM's stage and choices are kept, read from its notes in Proxmox and written
back there under Proxmox's digest (docs/architecture/engine.md §16.4).

`Record` is a frozen dataclass over the fields of pve/record.py's block, each held as it is made to what it may be:
the kind and the stage to the format's words, the UUID to the form Proxmox gives a VM's (lower case, 8-4-4-4-12), a
name, the size and the repositories to the command line's patterns (schema/patterns.py), the digests to SHA-256's hex,
the VPNs to today's mark of one (vpn/model.py), and every text to one line of plain text. A VM's record names its stage,
and every record but a VM's first form, which its clone carries before its UUID can be read, names the UUID it is
written for. A record that is not this engine's is no record here: `read(config)` gives None for notes with no block of
the engine's, whatever else they hold, and `Unreadable` for a block it cannot read, a value that is not what its field
may be included.

`write(client, vm, change)` is the one writer of a VM's record, and the only module of the new engine that sends a
description to Proxmox: it reads the VM's configuration, applies `change` to the record its notes hold (None where they
hold none), renders the notes with that block alone changed, and writes them with the digest of that read through the
client's `edit_config`, so that where another writer, a person in Proxmox's page among them, changed the configuration
in between, the write is refused and read and applied again, `client.EDITS` times at most. Notes as Proxmox will give
them back that equal the notes read send nothing, so a second run of the same change sends no write. It never writes a
record that names a UUID other than the VM's own, nor rewrites a block it cannot read, nor sends notes past Proxmox's
8192 characters (`TooLong`, said before anything is sent).

`label(client, vm, value)` writes the one label a VM carries in Proxmox's list, the setting `vm_label`'s value, beside
whatever tags a person gave it, under the same digest; nothing decides anything from it.

`change` may be applied more than once, so it is a function of the record it is given alone, with no other effect.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Mapping
from typing import Protocol

from ..pve import record as block
from ..pve.answers import VmConfig
from ..pve.client import TAG, Proxmox
from ..pve.record import TooLong, Unreadable
from ..schema.patterns import full
from ..vpn.model import MARK

__all__ = ["Record", "TooLong", "Unreadable", "Vm", "label", "read", "write"]

# A VM's identity as answers.config reads it from smbios1 (proxmox.smbios_uuid): lower case, 8-4-4-4-12.
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
# A SHA-256 digest in lower-case hex: an image's, and what a VM last converged to.
DIGEST = re.compile(r"[0-9a-f]{64}")


class Vm(Protocol):
    """A VM where Proxmox keeps it: the node it is on and its number there, as a VmConfig, a listing's entry or a
    workflow's target give them."""

    @property
    def node(self) -> str: ...

    @property
    def vmid(self) -> int: ...


@dataclasses.dataclass(frozen=True)
class Record:
    """A VM's record: what it is of (kind), the SMBIOS UUID it was written for, the name the run asked for, the stage,
    whether its first start went well (provisioned), its own list of repositories (None where it follows the fleet's
    setting, () where its list names none), the size it was created with (cores, memory, disk, each where the run gave
    one), the digest of the image it was made from, the VPNs a login of which was seen on it (joined), the digest it
    last converged to, whether an agent's run made it, and the run on it now (job). A text left "" is not written."""

    kind: str
    uuid: str = ""
    name: str = ""
    stage: str = ""
    provisioned: bool = False
    repositories: tuple[str, ...] | None = None
    cores: int | None = None
    memory: str = ""
    disk: str = ""
    image: str = ""
    joined: tuple[str, ...] = ()
    converged: str = ""
    made_by_agent: bool = False
    job: str = ""

    def __post_init__(self) -> None:
        for field in ("provisioned", "made_by_agent"):
            if not isinstance(getattr(self, field), bool):
                raise TypeError(f"the record's {field} is True or False")
        for field in ("repositories", "joined"):
            value = getattr(self, field)
            if value is not None and not isinstance(value, tuple):
                raise TypeError(f"the record's {field} is a tuple")
        if self.cores is not None and (
            isinstance(self.cores, bool) or not isinstance(self.cores, int) or not full("cores", str(self.cores))
        ):
            raise ValueError("the record's cores are a whole number from 1 to 999")
        # Each value as one line of the block holds it, the kind and the stage among the format's words.
        block.lines(self.fields())
        if self.kind == "vm" and not self.stage:
            raise ValueError("a VM's record names its stage")
        if self.uuid and not UUID.fullmatch(self.uuid):
            raise ValueError("the record's uuid is a VM's SMBIOS UUID, in lower case")
        if not self.uuid and self.stage != "cloning":
            raise ValueError("a record names the UUID of the VM it is written for, but at the stage cloning")
        if self.name and not (full("vm_name", self.name) and "," not in self.name):
            raise ValueError("the record's name is one VM's name")
        if self.repositories is not None and not full("repos", ",".join(self.repositories)):
            raise ValueError("the record's repositories are repositories' names")
        if self.memory and not full("memory", self.memory):
            raise ValueError("the record's memory is a whole number of MiB, or one with M or G after it")
        if self.disk and not full("disk", self.disk):
            raise ValueError("the record's disk is a whole number with G or T after it")
        for field in ("image", "converged"):
            if getattr(self, field) and not DIGEST.fullmatch(getattr(self, field)):
                raise ValueError(f"the record's {field} is a SHA-256 digest in lower-case hex")
        if any(not MARK.fullmatch(vpn) for vpn in self.joined) or len(set(self.joined)) != len(self.joined):
            raise ValueError("the record's joined names each VPN once")
        if self.job and not full("text", self.job):
            raise ValueError("the record's job is one line of text")

    @property
    def sized(self) -> bool:
        """Whether the VM was created with a size of its own: today's sized tag."""
        return self.cores is not None or bool(self.memory) or bool(self.disk)

    def fields(self) -> dict[str, str | tuple[str, ...] | None]:
        """The block's fields, as pve/record.py's render takes them: what is not set given None, and left out."""
        return {
            "kind": self.kind,
            "uuid": self.uuid or None,
            "name": self.name or None,
            "stage": self.stage or None,
            "provisioned": block.YES if self.provisioned else None,
            "repositories": self.repositories,
            "cores": None if self.cores is None else str(self.cores),
            "memory": self.memory or None,
            "disk": self.disk or None,
            "image": self.image or None,
            "joined": self.joined or None,
            "converged": self.converged or None,
            "made_by_agent": block.YES if self.made_by_agent else None,
            "job": self.job or None,
        }

    @classmethod
    def of(cls, fields: Mapping[str, str | tuple[str, ...]]) -> Record:
        """The record pve/record.py's parse gave: ValueError where a value is not what its field may be."""
        cores = fields.get("cores")
        if cores is not None and not (isinstance(cores, str) and full("cores", cores) and cores):
            raise ValueError("the record's cores are a whole number from 1 to 999")
        return cls(
            kind=str(fields["kind"]),
            uuid=str(fields.get("uuid", "")),
            name=str(fields.get("name", "")),
            stage=str(fields.get("stage", "")),
            provisioned=fields.get("provisioned") == block.YES,
            repositories=_names(fields.get("repositories")),
            cores=None if cores is None else int(cores),
            memory=str(fields.get("memory", "")),
            disk=str(fields.get("disk", "")),
            image=str(fields.get("image", "")),
            joined=_names(fields.get("joined")) or (),
            converged=str(fields.get("converged", "")),
            made_by_agent=fields.get("made_by_agent") == block.YES,
            job=str(fields.get("job", "")),
        )


def _names(value: str | tuple[str, ...] | None) -> tuple[str, ...] | None:
    """A list field as parse gave it: a tuple, or None where the line was left out."""
    if value is None:
        return None
    if not isinstance(value, tuple):
        raise ValueError("a list of the record's is a list")
    return value


def _whose(config: VmConfig) -> str:
    return f"VM {config.vmid}'s"


def _edit(client: Proxmox, vm: Vm, edit: Callable[[VmConfig], Mapping[str, object] | None]) -> None:
    """edit applied to vm's configuration, and written under the digest of the read it was applied to."""
    client.edit_config(vm.node, vm.vmid, edit)


def read(config: VmConfig) -> Record | None:
    """The record a VM's notes hold, from the configuration the client read; None where they hold no block of this
    engine's. Unreadable where they hold one it cannot read, or one whose values are not what its fields may be."""
    try:
        fields = block.parse(config.description)
    except Unreadable as refused:
        raise Unreadable(refused.why, _whose(config)) from None
    if fields is None:
        return None
    try:
        return Record.of(fields)
    except (TypeError, ValueError) as refused:
        raise Unreadable(str(refused), _whose(config)) from None


def write(client: Proxmox, vm: Vm, change: Callable[[Record | None], Record | None]) -> Record | None:
    """Apply change to the record in vm's notes and write it there under the digest of the read it was made from: the
    record the notes then hold. change is given the record read (None where there is none) and gives the record to
    write, or None, or the same, for no change: it may be applied again, after another writer's change was read, up to
    the client's EDITS times, after which the last DigestMismatch is raised. Nothing is sent where the notes, as Proxmox
    will give them back, would be what they are. Unreadable, before anything is sent, where the notes hold a block this
    engine cannot read; ValueError where change gives a record that names a UUID other than the VM's own; TooLong where
    the notes would pass 8192 characters."""
    if not callable(change):
        raise TypeError("change is a function of the record read")
    outcome: list[Record | None] = [None]

    def edit(config: VmConfig) -> dict[str, str] | None:
        current = read(config)
        new = change(current)
        if new is None:
            outcome[0] = current
            return None
        if not isinstance(new, Record):
            raise TypeError("change gives a Record, or None for no change")
        if new.uuid and new.uuid != config.identity:
            raise ValueError(
                f"the record would name a UUID that is not {_whose(config)} own: a record is written only for the VM"
                " it names"
            )
        try:
            notes = block.render(config.description, new.fields())
        except Unreadable as refused:
            raise Unreadable(refused.why, _whose(config)) from None
        except TooLong as refused:
            raise TooLong(refused.length, _whose(config)) from None
        outcome[0] = new
        return None if notes == config.description else {"description": notes}

    _edit(client, vm, edit)
    return outcome[0]


def label(client: Proxmox, vm: Vm, value: str) -> None:
    """Give vm the label value, the setting vm_label's, beside the tags it has, under the digest of the read: nothing
    sent where it carries it already, as Proxmox gives tags back, in lower case. ValueError for a value that is not one
    tag Proxmox takes, as the setting's own rule holds it (fleet/registry.py's PROXMOX_TAG)."""
    if not isinstance(value, str):
        raise TypeError("a label is text")
    if not TAG.fullmatch(value):
        raise ValueError(
            "the setting vm_label is one Proxmox tag: letters, digits, _, +, . and -, starting with a letter, a digit"
            " or _, at most 100 characters"
        )

    def edit(config: VmConfig) -> dict[str, list[str]] | None:
        if value.lower() in (tag.lower() for tag in config.tags):
            return None
        return {"tags": [*config.tags, value]}

    _edit(client, vm, edit)
