"""The fleet's mark in its pool's comment, and the one rule a changing run reads it by (docs/architecture/engine.md
§16.9).

The pool's comment carries one mark, `agent-warehouse fleet=<id> controller=none`: the fleet's ID, as `fleet.json`
holds it (record.py), and whether the fleet has a control VM, `none` or that VM's ID, each ID held to
`schema/patterns.py`'s `fleet_id`. It names the fleet and its mode, never a computer, so a second computer that holds
the fleet's folder is not refused. Only an administrator of the host can write the comment (E's script); the fleet's
token reads it, with one call of the Proxmox client its caller makes, and no machine can touch it.

`parse(comment)` finds the mark wherever it stands in the comment, so that a note of the person's own beside it, on
its line or on another, changes nothing: its three words joined by one space, with nothing but white space or the
comment's start before it and white space or the comment's end after it. A comment with no mark is None. One whose
mark does not read as one (an ID outside the pattern, a word missing or out of place, a line broken inside it), or
that holds the mark's first words twice, is `Unreadable`, never a comment with no mark: what cannot be read is never
taken for a pool nobody marked. A sentence says what is wrong, and never a byte of the comment but an ID that matched
its pattern.

`render(mark, comment)` puts the mark in the comment, `controller=none` always, and keeps the rest byte for byte: a
comment that already holds it is returned as it is, and one with no mark gains it at its end, after one space where
the comment does not end in white space, since Proxmox's page edits a pool's comment on one line. It never writes over
another mark, of another fleet or naming a control VM, and never over a comment it cannot read.

`may_change(mark, record)` is the rule every changing run reads before its first change: None where the mark is this
fleet's, as `fleet.json` names it, with no control VM; else one sentence: the pool carries no mark; the mark names
another fleet; or it names a control VM, any value but `none`, which the base never writes and always refuses ("this
fleet has a control VM: change it there, or retire it"). Writing the comment is the administrator's, in E; nothing in
the base copies the mark into a record.

Standard library only, with the patterns and record.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from claude_vm.fleet.record import FleetRecord
from claude_vm.schema import patterns

# The mark's first word, and the value of `controller=` of a fleet with no control VM.
WORD = "agent-warehouse"
NONE = "none"
_ID = patterns.PATTERNS["fleet_id"].text
# The mark whole: its three words joined by one space, white space or the comment's ends around it. Every value is held
# again with patterns.full after it is found.
_MARK = re.compile(rf"(?<!\S){WORD} fleet=(?P<fleet>{_ID}) controller=(?P<controller>{NONE}|{_ID})(?!\S)")
# Where a mark begins, however it is written after: the first word, then one of its fields, which a reader takes for an
# attempt at a mark, read or not.
_BEGINS = re.compile(rf"{WORD}\s+(?:fleet|controller)=")


class Refused(ValueError):
    """The comment cannot be read or written as asked: the sentence says why, never what the comment holds."""


class Unreadable(Refused):
    """The comment holds what begins a mark and does not read as one, or begins one twice."""


@dataclass(frozen=True)
class Mark:
    """The fleet's mark: its ID, and its control VM's ID or `none`. Each is held to the pattern fleet_id as it is
    made."""

    fleet: str
    controller: str

    def __post_init__(self) -> None:
        if not isinstance(self.fleet, str) or not patterns.full("fleet_id", self.fleet):
            raise ValueError("a mark's fleet is a fleet's ID, a UUID in its lowercase canonical form")
        if not isinstance(self.controller, str) or not (
            self.controller == NONE or patterns.full("fleet_id", self.controller)
        ):
            raise ValueError(
                f"a mark's controller is {NONE} or a control VM's ID, a UUID in its lowercase canonical form"
            )

    @property
    def line(self) -> str:
        """The mark as the comment holds it."""
        return f"{WORD} fleet={self.fleet} controller={self.controller}"


def _found(comment: str) -> tuple[Mark, re.Match[str]] | None:
    """The comment's one mark and where it stands; None where nothing in it begins one."""
    if not isinstance(comment, str):
        raise TypeError(f"a pool's comment is a str, not a {type(comment).__name__}")
    begun = len(_BEGINS.findall(comment))
    if begun == 0:
        return None
    if begun > 1:
        raise Unreadable(
            "error: the pool's comment holds the fleet's mark more than once, so which fleet it names cannot be told:"
            " an administrator of the host keeps one in it"
        )
    match = _MARK.search(comment)
    if match is None:
        raise Unreadable(
            f"error: the pool's comment holds a mark that does not read as one: it is written {WORD} fleet=<id>"
            f" controller={NONE}, each ID a lowercase UUID, and an administrator of the host writes it so"
        )
    fleet, controller = match["fleet"], match["controller"]
    # The pattern is held again whole, as every reader of an ID holds it.
    if not patterns.full("fleet_id", fleet) or not (controller == NONE or patterns.full("fleet_id", controller)):
        raise Unreadable("error: the pool's comment holds a mark whose ID is no lowercase UUID")
    return Mark(fleet, controller), match


def parse(comment: str) -> Mark | None:
    """The mark wherever it stands in the pool's comment; None where the comment holds none. Unreadable where it holds
    one that does not read as one, or the mark's first words twice."""
    found = _found(comment)
    return None if found is None else found[0]


def _another_fleet(found: Mark, fleet: str) -> str:
    return (
        f"error: the pool's mark names another fleet, {found.fleet}, where this computer's folder is the fleet"
        f" {fleet}'s: nothing of it is changed from here; drive it from that fleet's own folder, or give this fleet a"
        " pool of its own"
    )


def _controlled(found: Mark) -> str:
    return f"error: this fleet has a control VM ({found.controller}): change it there, or retire it"


def render(mark: Mark, comment: str) -> str:
    """comment with the mark in place, controller=none, and the rest kept byte for byte: as it is where it holds that
    mark already, else with the mark at its end, after one space where it does not end in white space. Refused where
    the mark names a control VM, and where the comment holds another mark or one that cannot be read."""
    if not isinstance(mark, Mark):
        raise TypeError(f"a mark is a Mark, not a {type(mark).__name__}")
    if mark.controller != NONE:
        raise ValueError(f"the base writes controller={NONE} alone, never a control VM's ID")
    found = _found(comment)
    if found is not None:
        there = found[0]
        if there.fleet != mark.fleet:
            raise Refused(_another_fleet(there, mark.fleet))
        if there.controller != NONE:
            raise Refused(_controlled(there))
        return comment
    space = "" if not comment or comment[-1].isspace() else " "
    return f"{comment}{space}{mark.line}"


def may_change(mark: Mark | None, record: FleetRecord) -> str | None:
    """None where a run may change the fleet record names: the pool's mark is that fleet's, with no control VM. Else the
    one sentence that says why not: the pool carries no mark; its mark names another fleet; or it names a control VM,
    any value but none."""
    if not isinstance(record, FleetRecord):
        raise TypeError(f"a fleet's record is a FleetRecord, not a {type(record).__name__}")
    if mark is None:
        return (
            f"error: the pool's comment carries no mark of a fleet, where this computer's folder is the fleet"
            f" {record.fleet}'s: nothing is changed until an administrator of the host puts the mark back"
        )
    if not isinstance(mark, Mark):
        raise TypeError(f"a mark is a Mark or None, not a {type(mark).__name__}")
    if mark.fleet != record.fleet:
        return _another_fleet(mark, record.fleet)
    if mark.controller != NONE:
        return _controlled(mark)
    return None
