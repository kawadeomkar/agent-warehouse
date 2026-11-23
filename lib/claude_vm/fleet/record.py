"""The computer's own record of a fleet, `fleet/fleet.json`: the fleet's ID and the format number of every store
(docs/architecture/engine.md §16.9).

`FleetRecord(format, fleet, stores)` is the record: `format`, the format of `fleet.json` itself; `fleet`, the fleet's
ID, a UUID in its lowercase canonical form held to `schema/patterns.py`'s `fleet_id`, the one pattern the pool's mark
and an adopted machine's marker share; and `stores`, the format number of each store this computer keeps of the fleet
(`STORES`: the vault, the settings, the hosts, and the jobs, whose `job.json` carries no number of its own). `KNOWN`
is the format of each that this program reads and writes, and `FORMAT` that of `fleet.json`. The record holds nothing
more: no controller, since the pool's mark says that (mark.py), and nothing of an export, which is the add-on's.

**A newer format is refused by name.** An older program reads no store whose number is above what it reads:
`refuse_newer(record, known)` returns the one sentence it says, naming each such store, for the stores a caller reads
with the number it reads of each, and None where every one is one it reads. `read` refuses a `fleet.json` whose own
format is newer (`Newer`), so a field a later release adds, with a raise of `FORMAT`, is never read as something else.
That is how the add-on adds what it needs later: every store has its number from the start.

`read(where)` returns the record of the fleet whose home is `where` (a `paths.Home`), or None where `fleet.json` is not
there yet, before `init`; anything there that is no record is `Unreadable`, said by its path and what is wrong, never by
what it holds. `write(where, record)` writes it through `files.write` under `store.locked`, and reads what is there
under the same lock first, so that of two writers neither undoes the other: it is refused where `fleet.json` names
another fleet, since a fleet's ID never changes; where it cannot be read, or is newer, since nothing is written over
what this program cannot read; and where the record would lower or drop the number of a store `fleet.json` holds, since
an older program never undoes a newer one's. `new_id()` makes a fleet's ID, `uuid.uuid4()` in its canonical form, and
`first(fleet)` is the record a new fleet starts with.

Standard library only, with `store`, `files`, `paths` and the patterns. Nothing here reads Proxmox: the pool's mark is
mark.py's, and no record ever takes a copy of it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from claude_vm import store
from claude_vm.fleet import files, paths
from claude_vm.schema import patterns
from claude_vm.text import plain_text

# The record's file, in the fleet's folder, which a person copies to a second computer.
FILE = "fleet.json"
# The format of fleet.json that this program reads and writes.
FORMAT = 1
# The stores this computer keeps of a fleet, each with a format number in the record, in the order a sentence names
# them: the vault (vault.age), the settings (settings.yml and each host's), the hosts (hosts/<name>/), and the jobs
# (the state folder's jobs/, whose job.json carries no number of its own).
STORES = ("vault", "settings", "hosts", "jobs")
# The format of each store that this program reads and writes. A release that changes one raises its number here.
KNOWN: Mapping[str, int] = MappingProxyType({"vault": 1, "settings": 1, "hosts": 1, "jobs": 1})
# A store's name, as the record keeps it: a later release's store, which this program reads nothing of, among them.
STORE_NAME = r"[a-z][a-z0-9_]{0,31}"
# The most of fleet.json that is read: a record is a few lines.
MOST = 64 * 1024


class Refused(ValueError):
    """The record cannot be read or written as asked: the sentence says why, and never what the file holds."""


class Unreadable(Refused):
    """fleet.json is there and is no record this program can read: nothing is written over it."""


class Newer(Unreadable):
    """fleet.json, or a store it numbers, is in a format newer than this program reads."""


def _number(value: object) -> bool:
    """Whether value is a format number: a whole number from 1, never a bool."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


@dataclass(frozen=True)
class FleetRecord:
    """The record of one fleet: the format of fleet.json, the fleet's ID, and each store's format number. Every field
    is held as it is made: a ValueError says which."""

    format: int
    fleet: str
    stores: Mapping[str, int]

    def __post_init__(self) -> None:
        if not _number(self.format):
            raise ValueError("a fleet's record has a format number: a whole number from 1")
        if not isinstance(self.fleet, str) or not patterns.full("fleet_id", self.fleet):
            raise ValueError("a fleet's ID is a UUID in its lowercase canonical form")
        if not isinstance(self.stores, Mapping):
            raise ValueError("a fleet's record maps each store to its format number")
        stores = dict(self.stores)
        lacking = [name for name in STORES if name not in stores]
        if lacking:
            raise ValueError(f"a fleet's record numbers every store: it lacks {', '.join(lacking)}")
        for name, number in stores.items():
            if not isinstance(name, str) or not _store_name(name) or not _number(number):
                raise ValueError("a fleet's record numbers a store by its name, with a whole number from 1")
        object.__setattr__(self, "stores", MappingProxyType(stores))


def _store_name(name: str) -> bool:
    return re.fullmatch(STORE_NAME, name) is not None


def new_id() -> str:
    """A new fleet's ID: a random UUID in its lowercase canonical form, held to the pattern fleet_id."""
    import uuid

    made = str(uuid.uuid4())
    if not patterns.full("fleet_id", made):
        raise RuntimeError("uuid made an ID that is not in its lowercase canonical form")
    return made


def first(fleet: str) -> FleetRecord:
    """The record a new fleet starts with: its ID, fleet.json's format, and this program's format of every store."""
    return FleetRecord(FORMAT, fleet, KNOWN)


def path(where: paths.Home) -> Path:
    """fleet.json's place: the top of the fleet's folder."""
    return paths.fleet_dir(where) / FILE


def _newer(found: list[tuple[str, int, int]]) -> str:
    """The sentence of an older program: each (what, its number, the most this program reads), and what to do."""
    each = [f"{what} in format {number}, where this program reads {most} at most" for what, number, most in found]
    them = "it" if len(each) == 1 else "them"
    return (
        f"error: this fleet keeps {_joined(each)}: run the release of agent-warehouse that wrote {them}, or a newer one"
    )


def _joined(each: list[str]) -> str:
    """Parts of a sentence joined as a person writes a list: a, b and c."""
    return each[0] if len(each) == 1 else ", ".join(each[:-1]) + " and " + each[-1]


def refuse_newer(record: FleetRecord, known: Mapping[str, int]) -> str | None:
    """The sentence an older program says where a store it reads is in a format newer than it reads, naming each such
    store; None where every store known names is one it reads. known maps each store the caller reads, a name of
    STORES, to the most it reads of it (KNOWN, or the part of it the caller reads)."""
    found = []
    for name, most in known.items():
        if name not in STORES or not _number(most):
            raise ValueError(f"{name!r} is no store of a fleet's record, with the most a program reads of it")
        if record.stores[name] > most:
            found.append((f"its {name}", record.stores[name], most))
    return _newer(found) if found else None


def _parsed(where: Path, text: str) -> FleetRecord:
    """text, fleet.json's, as a record; Unreadable or Newer where it is none this program reads."""
    said = plain_text(str(where))
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        raise Unreadable(f"error: {said} is not a fleet's record: it does not read as JSON") from None
    if not isinstance(data, dict):
        raise Unreadable(f"error: {said} is not a fleet's record: it is not a mapping")
    if not _number(data.get("format")):
        raise Unreadable(f"error: {said} is not a fleet's record: it has no format number")
    if data["format"] > FORMAT:
        raise Newer(_newer([(f"its record ({FILE})", data["format"], FORMAT)]))
    extra = sorted(set(data) - {"format", "fleet", "stores"})
    if extra:
        raise Unreadable(f"error: {said} is not a fleet's record of format {FORMAT}: it holds a field that one has not")
    fleet, stores = data.get("fleet"), data.get("stores")
    if not isinstance(fleet, str) or not patterns.full("fleet_id", fleet):
        raise Unreadable(f"error: {said} is not a fleet's record: its fleet is no fleet's ID, a lowercase UUID")
    if not isinstance(stores, dict):
        raise Unreadable(f"error: {said} is not a fleet's record: it numbers no store")
    try:
        return FleetRecord(data["format"], fleet, stores)
    except ValueError as error:
        raise Unreadable(f"error: {said} is not a fleet's record: {error}") from None


def read(where: paths.Home) -> FleetRecord | None:
    """The record of the fleet whose home is where; None where fleet.json is not there. Unreadable where what is there
    is no record, Newer where its format is newer than this program reads; any other error of the read as itself."""
    found = path(where)
    try:
        with open(found, encoding="utf-8") as stream:
            text = stream.read(MOST + 1)
    except FileNotFoundError:
        return None
    except (IsADirectoryError, UnicodeDecodeError) as error:
        why = "it is a folder" if isinstance(error, IsADirectoryError) else "it is not text"
        raise Unreadable(f"error: {plain_text(str(found))} is not a fleet's record: {why}") from None
    if len(text) > MOST:
        raise Unreadable(f"error: {plain_text(str(found))} is not a fleet's record: it is larger than one is")
    return _parsed(found, text)


def _text(record: FleetRecord) -> str:
    return json.dumps({"format": record.format, "fleet": record.fleet, "stores": dict(record.stores)}, indent=2) + "\n"


def write(where: paths.Home, record: FleetRecord) -> None:
    """record written as fleet.json, under the lock on its folder, read there first: refused where what is there names
    another fleet, cannot be read or is newer, or numbers a store that record would lower or drop. The folder is made
    at DIR_MODE where it is missing, the file at FILE_MODE (files.py)."""
    if not isinstance(record, FleetRecord):
        raise TypeError(f"a fleet's record is a FleetRecord, not a {type(record).__name__}")
    found = path(where)
    files.folder(found.parent)
    with store.locked(found):
        there = read(where)
        if there is not None:
            if there.fleet != record.fleet:
                raise Refused(
                    f"error: {plain_text(str(found))} is the record of the fleet {there.fleet}, and a fleet's ID never"
                    f" changes: the record of {record.fleet} is not written over it"
                )
            lowered = [
                f"its {name} in format {number}"
                for name, number in there.stores.items()
                if record.stores.get(name, 0) < number
            ]
            if lowered:
                raise Refused(
                    f"error: {plain_text(str(found))} keeps {_joined(lowered)}, which the record to be written would"
                    " lower or drop: nothing is written, since no program undoes the format a newer one wrote"
                )
        files.write(found, _text(record))
