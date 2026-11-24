"""A VM's record as text: the one block of a VM's notes in Proxmox that this engine writes, parsed and rendered plainly
(docs/architecture/engine.md §16.5).

The block is `key: value` lines between a line that names the format they are written in and a line that ends it:

    agent-warehouse record (format 1)
    kind: vm
    uuid: 5b0c1d2e-3f40-4a5b-8c6d-7e8f90a1b2c3
    stage: unfinished
    repositories: my-app,my-lib
    end of agent-warehouse record

Only a block under that first line with a format number this module reads is a record. Anything else in the notes is a
person's text, kept as it is and never parsed here: a person's own lines, and the lines of today's product. A block
that is this engine's and that it cannot read - a format number it does not read, a line that is no `key: value` of the
format's, a key twice, a second block, an end with no start - is Unreadable: refused, and never rewritten, so that what
a newer program or a person wrote is never lost to an older one.

`parse(notes)` gives the block's fields, or None where the notes hold none: each value as a str, but the three lists, a
tuple each. `copied(fields, identity)` says whether they name a UUID that is not the VM's own: a clone made without a
description of its own carries its source's notes, record and all (experiment 6), so such a record is a copy, which
engine/record.py's read refuses (Copy) and status reads by the same function. A VM's first form alone names no UUID,
and cannot be told from a copy of one. `repositories` keeps today's rule for a VM's own list
(lib/claude_vm/proxmox.py's line of repositories): names split on commas, each kept only where it is a repository's
name, so that a mangled line is still the VM's list and clones nothing it does not name; an empty value is a list too,
of none, where a line left out is no list.
`render(notes, fields)` gives the notes with that block alone written from fields, every other line byte for byte as it
was, and the block added last, after a blank line, where there was none; a field given None is left out. Both read the
notes as Proxmox gives them back, which takes the white space off their end (`kept`), so a block rendered again from
the same fields is the same text and a second run sends no write. What `render` writes `parse` reads back as it was
given, and `render` refuses what it could not: a value on more than one line or with a character that is not plain text
(a control, a format or a line separator), white space at either end of one, a list's item with a comma, a repository's
that is no name, a kind or a stage that is no word of the format's, a flag that is not `yes`.

Every field's value is held to its form here, by both: the UUID to the form Proxmox gives a VM's (lower case,
8-4-4-4-12), the name, the size and the job to the command line's patterns (schema/patterns.py), a template's build to a
whole number from 1, the digests to SHA-256's hex, its image's URL to an http or https URL, its packages to Debian's
names and the VPNs to today's mark of one (vpn/model.py's, which a test holds VPN equal to), each list naming each of
its items once. So `parse` refuses, Unreadable, every value engine/record.py's typed Record refuses, and status, which
reads a record by `parse` alone, judges what the engine judges (card G28). The rules across fields - a VM's record names
its stage, and every record but a VM's first form names its UUID (`first_form`, `NO_UUID`) - are each reader's judge's:
pve/ownership.py's one check and pve/template.py's judge hold them, and the typed Record holds them for the engine.

A VM's notes hold at most MOST characters, Proxmox's bound on a configuration's description, counted in characters and
not bytes, shared with whatever a person typed there (experiment 6, on Proxmox VE 9.2.21): `render` refuses notes past
it, TooLong, so nothing is sent that Proxmox would refuse, and nothing a clone could carry that no later write could.

What a value means - a size as a number, a stage beside its kind - is engine/record.py's typed record, which reads and
writes through these two functions. Standard library only, and of the package text.py and schema/patterns.py alone
(itself the standard library's alone), so that status reads a record with no other part of the engine loaded.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence

from ..schema.patterns import full
from ..text import one_line

# The format this engine writes, and every format it reads. Until the engine's first release a field added to FIELDS
# is format 1's; after it, a change to what a block holds is a new number, which a program that does not read it
# refuses and never rewrites.
FORMAT = 1
FORMATS = frozenset({FORMAT})
_NUMBERS = frozenset(str(each) for each in FORMATS)
# The line that starts the block, with the format its lines are written in, and the line that ends it. A line is one
# of them only whole, white space at either end aside, so a person's sentence that names them is a person's text.
BEGIN = "agent-warehouse record (format {format})"
_BEGIN = re.compile(r"agent-warehouse record \(format ([^()]*)\)")
END = "end of agent-warehouse record"
# The fields of format 1, in the order render writes them: what the record is of; the SMBIOS UUID of the VM it was
# written for (none in the first form a clone carries); the name the run asked for; the stage; whether the first start
# went well (today's removal of the unfinished tag); the VM's own list of repositories; the size it was created with,
# each part where the run gave one; the version of the build steps a template was built by; the digest of the image it
# was made from, the URL that image was fetched from and the packages put into it, which a template's configuration
# cannot show (pve/template.py judges them); the VPNs a login of which was seen on it; the digest it last converged
# to; the mark that an agent's run made it; and the run on it now, as information.
FIELDS = (
    "kind",
    "uuid",
    "name",
    "stage",
    "provisioned",
    "repositories",
    "cores",
    "memory",
    "disk",
    "build",
    "image",
    "image_url",
    "packages",
    "joined",
    "converged",
    "made_by_agent",
    "job",
)
# The fields whose value is a list, comma-joined on its line.
LISTS = frozenset({"repositories", "packages", "joined"})
# The fields that are a mark: written as YES where set, and left out where not.
FLAGS = frozenset({"provisioned", "made_by_agent"})
YES = "yes"
# What a record is of: a VM of the fleet, a VM a golden image is built in, a golden image, the base template.
KINDS = ("vm", "build", "golden", "template")
# A VM's stage: cloned with the record's first form and its UUID not yet read; made and not yet configured; configured.
STAGES = ("cloning", "unfinished", "finished")
# The words a field takes, where the format names them.
WORDS = {"kind": KINDS, "stage": STAGES}
# A repository's name on a VM's own list: today's rule, as lib/claude_vm/proxmox.py's _REPO_NAME_RE holds one, and
# each name of the command line's repos pattern (schema/patterns.py), which a test holds both equal to.
REPOSITORY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,99}")
# A VM's identity as Proxmox gives it in smbios1 (proxmox.smbios_uuid): lower case, 8-4-4-4-12.
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
# A SHA-256 digest in lower-case hex: an image's, and what a VM last converged to.
DIGEST = re.compile(r"[0-9a-f]{64}")
# A template's build, the version of the build steps it was built by (pve/template.py's BUILD), as its line writes it:
# a whole number from 1, with no leading 0.
BUILD_FORM = re.compile(r"[1-9][0-9]{0,8}")
# The URL a template's image was fetched from: http or https, with no white space, as a setting gives one.
URL = re.compile(r"https?://\S{1,2040}")
# A package put into a template's image: a Debian package's name (Debian Policy, 5.6.1), lower case.
PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.-]{1,99}")
# A VPN a login of which was seen on a VM: today's mark of one, vpn/model.py's MARK, which a test holds this equal to
# (that module is not this one's to import: status reads a record with the standard library alone).
VPN = re.compile(r"tailscale")
# The most characters a VM's notes hold, the record with whatever a person typed there: Proxmox VE's bound on a
# configuration write's description (qemu-server's schema, maxLength 8192, counted in characters), which a clone's does
# not have.
MOST = 8192
# The Unicode categories no value may hold: controls, format characters, surrogates and the two separators, each of
# which ends a line or makes it read in an order of its own.
NOT_PLAIN = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


class Unreadable(ValueError):
    """The notes hold a block of this engine's that it cannot read, said with why: refused, and never rewritten."""

    def __init__(self, why: str, whose: str = "a VM's") -> None:
        self.why = why
        super().__init__(
            f"the record in {whose} notes cannot be read: {why}; it is left as it is, never rewritten, until a person "
            "puts it right in Proxmox's page or removes it"
        )


class Copy(Unreadable):
    """The notes hold a record written for another VM: it names a UUID that is not this VM's own, as a clone carries
    its source's notes. Refused as one Unreadable is, and never rewritten: this engine never takes it for the VM's
    own."""

    def __init__(self, whose: str = "a VM's") -> None:
        self.why = "it names another VM's UUID"
        ValueError.__init__(
            self,
            f"the record in {whose} notes was written for another VM, whose UUID it names, as a clone carries its "
            "source's notes: it is not this VM's record, and is left as it is, never rewritten",
        )


# Why a record that names no UUID is refused, whichever reader reads it: only a VM's first form names none (first_form).
NO_UUID = "a record names the UUID of the VM it is written for, but a VM's first form, at the stage cloning"


def first_form(fields: Mapping[str, str | tuple[str, ...] | None]) -> bool:
    """Whether fields are a VM's first form, of kind vm at the stage cloning: the one record that may name no UUID,
    which a clone carries before its UUID can be read. A record of any other kind or stage that names none is refused
    (NO_UUID), by engine/record.py's Record and pve/template.py's judge alike."""
    return fields.get("kind") == "vm" and fields.get("stage") == "cloning"


def copied(fields: Mapping[str, str | tuple[str, ...]], identity: str) -> bool:
    """Whether the record parse gave names a UUID that is not identity, the SMBIOS UUID of the VM whose notes hold it
    ("" for a VM that has none): a copy, which a clone made without a description of its own carries from its source.
    UUIDs are compared without regard to case. A VM's first form at the stage cloning alone names no UUID, and cannot
    be told from a copy of one by this: a record of any other kind or stage that names none is refused (first_form)."""
    uuid = fields.get("uuid")
    return isinstance(uuid, str) and bool(uuid) and uuid.lower() != (identity or "").lower()


class TooLong(ValueError):
    """Notes that would be longer than MOST characters with the record: nothing is sent."""

    def __init__(self, length: int, whose: str = "a VM's") -> None:
        self.length = length
        super().__init__(
            f"{whose} notes would hold {length} characters with the record, past the {MOST} Proxmox takes: nothing was "
            "sent; make the notes outside the record shorter in Proxmox's page"
        )


def kept(notes: str | None) -> str:
    """notes as Proxmox gives them back: the white space at their end taken off, as qemu-server reads a description."""
    return (notes or "").rstrip()


def _pieces(notes: str | None) -> list[str]:
    """notes' lines, each with its own ending, so that joined they are the notes again, byte for byte."""
    return (notes or "").splitlines(keepends=True)


def _span(pieces: Sequence[str]) -> tuple[int, int, str] | None:
    """Where the block is among the lines: its first and last line, and its format number as written; None where there
    is none. Unreadable where there are two, or one is started and not ended, or ended and not started."""
    begins = [(at, found.group(1)) for at, line in enumerate(pieces) if (found := _BEGIN.fullmatch(line.strip()))]
    ends = [at for at, line in enumerate(pieces) if line.strip() == END]
    if not begins and not ends:
        return None
    if len(begins) > 1:
        raise Unreadable("they hold two records")
    if not begins:
        raise Unreadable(f'they hold "{END}" with no record before it')
    if len(ends) != 1 or ends[0] < begins[0][0]:
        raise Unreadable(f'its record does not end with one "{END}"')
    return begins[0][0], ends[0], begins[0][1]


def _plain(field: str, value: str) -> str:
    """value, where one line of a block can hold it as it is: one line, no character of NOT_PLAIN, no white space at
    either end; else ValueError, naming the field and never the value."""
    if not isinstance(value, str):
        raise TypeError(f"the record's {field} is text")
    if any(unicodedata.category(ch) in NOT_PLAIN for ch in value):
        raise ValueError(f"the record's {field} must be one line of plain text")
    if value != value.strip():
        raise ValueError(f"the record's {field} has white space at its start or its end")
    return value


def _form(field: str, text: str) -> str:
    """Why text, one line of plain text, is not what field's value may be, "" where it is: see the module's docstring.
    An empty value is no size's cores and no build, and any other field's empty value is left to the rules across
    fields."""
    if field == "uuid" and text and not UUID.fullmatch(text):
        return "the record's uuid is a VM's SMBIOS UUID, in lower case"
    if field == "name" and text and not (full("vm_name", text) and "," not in text):
        return "the record's name is one VM's name"
    if field == "cores" and not (text and full("cores", text)):
        return "the record's cores are a whole number from 1 to 999"
    if field == "memory" and text and not full("memory", text):
        return "the record's memory is a whole number of MiB, or one with M or G after it"
    if field == "disk" and text and not full("disk", text):
        return "the record's disk is a whole number with G or T after it"
    if field == "build" and not BUILD_FORM.fullmatch(text):
        return "the record's build is a whole number from 1"
    if field in ("image", "converged") and text and not DIGEST.fullmatch(text):
        return f"the record's {field} is a SHA-256 digest in lower-case hex"
    if field == "image_url" and text and not URL.fullmatch(text):
        return "the record's image_url is an http or https URL"
    if field == "job" and text and not full("text", text):
        return "the record's job is one line of text"
    return ""


# Each list's items' form, where the format holds one beyond a word with no comma, and what the list may be: each item
# of the form, and none twice.
ITEMS = {
    "packages": (PACKAGE, "the record's packages name each Debian package once"),
    "joined": (VPN, "the record's joined names each VPN once"),
}


def _held(field: str, value: str | Sequence[str]) -> str:
    """field's value as its line writes it, held to what the format takes of it, its form among it (_form, ITEMS);
    ValueError otherwise."""
    if field in LISTS:
        if isinstance(value, str) or not isinstance(value, Sequence):
            raise TypeError(f"the record's {field} is a list")
        for item in value:
            if not _plain(field, item) or "," in item:
                raise ValueError(f"each of the record's {field} is a word with no comma")
            if field == "repositories" and not REPOSITORY.fullmatch(item):
                raise ValueError("each of the record's repositories is a repository's name")
        if field in ITEMS:
            form, said = ITEMS[field]
            if any(not form.fullmatch(item) for item in value) or len(set(value)) != len(value):
                raise ValueError(said)
        return ",".join(value)
    text = _plain(field, value)
    if field in FLAGS and text != YES:
        raise ValueError(f"the record's {field} is {YES}, or left out")
    if field in WORDS and text not in WORDS[field]:
        raise ValueError(f"the record's {field} is one of {', '.join(WORDS[field])}")
    why = _form(field, text)
    if why:
        raise ValueError(why)
    return text


def _read(field: str, value: str) -> str | tuple[str, ...]:
    """A line's value as parse gives it: a list split on its commas - a repository's kept only where it is a name, as
    today's line of repositories is read - and any other held as render would write it."""
    if field == "repositories":
        return tuple(name for name in (part.strip() for part in value.split(",")) if REPOSITORY.fullmatch(name))
    read: str | tuple[str, ...] = value
    if field in LISTS:
        read = tuple(part.strip() for part in value.split(",") if part.strip())
    try:
        _held(field, read)
    except ValueError as refused:
        raise Unreadable(str(refused)) from None
    return read


def parse(notes: str | None) -> dict[str, str | tuple[str, ...]] | None:
    """The fields of the record in a VM's notes, by name, in the order its lines give them; None where the notes hold
    no block of this engine's. Unreadable where they hold one it cannot read. A blank line inside the block is passed
    over; a record names its kind."""
    pieces = _pieces(notes)
    span = _span(pieces)
    if span is None:
        return None
    first, last, number = span
    if number not in _NUMBERS:
        raise Unreadable(f"it is written in format {one_line(number, 20)}, which this program does not read")
    fields: dict[str, str | tuple[str, ...]] = {}
    for at in range(first + 1, last):
        line = pieces[at]
        if not line.strip():
            continue
        key, colon, value = line.partition(":")
        key = key.strip()
        if not colon or key not in FIELDS:
            raise Unreadable(f"line {at + 1} of the notes is no field of format {number}")
        if key in fields:
            raise Unreadable(f"it names {key} twice")
        fields[key] = _read(key, value.strip())
    if "kind" not in fields:
        raise Unreadable("it does not say what it is a record of")
    return fields


def lines(fields: Mapping[str, str | Sequence[str] | None]) -> list[str]:
    """The block's lines that fields make, in FIELDS' order, a field given None left out: ValueError for a field the
    format does not have, a value no line can hold, or no kind."""
    unknown = sorted(set(fields) - set(FIELDS))
    if unknown:
        raise ValueError(f"format {FORMAT} has no field {', '.join(unknown)}")
    if fields.get("kind") is None:
        raise ValueError("a record names its kind")
    written = []
    for field in FIELDS:
        value = fields.get(field)
        if value is not None:
            text = _held(field, value)
            written.append(f"{field}: {text}" if text else f"{field}:")
    return written


def render(notes: str | None, fields: Mapping[str, str | Sequence[str] | None]) -> str:
    """notes with the record written from fields, as Proxmox will give them back: the block replaced where it is, or
    added last after a blank line, and every other line as it was. Unreadable where the notes hold a block this engine
    cannot read, which is never rewritten; ValueError for fields no block can hold (lines); TooLong where the notes
    would pass MOST characters."""
    block = "\n".join([BEGIN.format(format=FORMAT), *lines(fields), END])
    parse(notes)
    pieces = _pieces(notes)
    span = _span(pieces)
    if span is None:
        before = kept(notes)
        text = f"{before}\n\n{block}" if before else block
    else:
        first, last, _ = span
        ending = pieces[last][len(pieces[last].splitlines()[0]) :]
        text = "".join(pieces[:first]) + block + ending + "".join(pieces[last + 1 :])
    text = kept(text)
    if len(text) > MOST:
        raise TooLong(len(text))
    return text
