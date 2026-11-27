"""What a run tells a person about a machine, and what it leaves to do (card G6; docs/architecture/engine.md §16.4).

`say(run, machine, matter, text, line)` is the one way a workflow or a layer-2 step says something: it emits the
`report` (text, printed above the live region and kept for the summary), and straight after it the `next` record of
that machine and matter: line while something is left to do, "" once nothing is, which takes back a line an earlier
report of the same machine and matter left; `also` gives a report's further matters, each its line after the first,
where one report records several (Remote Control's, card F11). So NEXT STEPS is recorded by each report and never
read off what was said, as today's report tasks record theirs with set_stats (docs/architecture/custom-code.md
§7.5). The run's model folds the records by machine and matter in today's order (runs/model.py's `next_steps`, by
run/next_steps.py's MATTERS), and the run's end prints them after the summary, which leaves them out, under the heading
NEXT STEPS (runs/live.py).

The machine is the one every event of it names (its identity, or a VM's stand-in key until its join), or None for the
run's own. The matter is one of MATTERS: today's, and the engine's own, which no report of today's records, each at a
named place of that list. The line is the printer's whole line (schema/printer.py's `line`), or a sentence `said` writes
that names its command, made by the caller with the host's flag (`host_flag`), and handed on here whole: one line, never
a form (no placeholder), never added to. A line or a text a machine printed reaches the run only made plain, as every
event's text is (runs/job.py's emit).

Standard library and run/next_steps.py alone; nothing read or written but the run's two events.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Protocol

from ..run.next_steps import MATTERS

# A placeholder as the printer writes one in a form (schema/words.py's `placeholder`): <name>, <name>..., <cores>,
# <name>=<value>; a word of its own, never part of another.
PLACEHOLDER = re.compile(r"(?<!\S)<[a-z][a-z0-9_-]*>")


class Run(Protocol):
    """What a report is emitted on: the run's context or its job (runs/attached.py's Context, runs/job.py's Job)."""

    def emit(self, kind: str, machine: str | None, text: str, **data: object) -> object: ...


def say(
    run: Run, machine: str | None, matter: str, text: str, line: str, *, also: Mapping[str, str] | None = None
) -> None:
    """text said of machine (None: the run's own), then what is left of matter for it: line, "" once nothing is; and
    of each matter of also, after it, its own line, where one report records several, as today's remote_control.yml
    records Remote Control's and the sign-in's (card F11). Refused before anything is emitted: a matter not in MATTERS
    or named twice, a text or a line that is no str, a line of more than one line, and a line that holds a
    placeholder, which is a form and never the command to run."""
    records = [(matter, line), *(also or {}).items()]
    if not isinstance(text, str):
        raise TypeError("a report's text and its NEXT STEPS line are each a str, the line '' once nothing is left")
    for each, left in records:
        if each not in MATTERS:
            raise ValueError(f"{each!r} is no matter of NEXT STEPS: one of {', '.join(MATTERS)}")
        if not isinstance(left, str):
            raise TypeError("a report's text and its NEXT STEPS line are each a str, the line '' once nothing is left")
        if left.splitlines() not in ([], [left]):
            raise ValueError("a NEXT STEPS line is one line")
        if PLACEHOLDER.search(left):
            raise ValueError("a NEXT STEPS line is the command to run, never a form: it holds no placeholder")
    if len({each for each, _ in records}) != len(records):
        raise ValueError("a report records each matter of NEXT STEPS once")
    run.emit("report", machine, text)
    for each, left in records:
        run.emit("next", machine, left, matter=each)
