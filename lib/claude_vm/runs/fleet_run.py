"""A run over several machines: one task for each machine, side by side under a bound, each machine with its own events
and its own outcome (docs/architecture/engine.md §16.8).

`each(context, machines, body, most)` starts `body(machine)` for each machine, by the key its events name it by (its
identity, or a VM's stand-in until its join, runs/job.py), never more than `most` at once: the setting `run_forks`, 20
by default as today's `forks` (ansible.cfg), which `--set run_forks=30` changes for one run and the verb passes. The
machines begin in the order given, one as soon as another ends. A machine's failure (an `Exception` its body raised) is
caught and recorded, and ends no other machine; a body that raises `asyncio.CancelledError` by itself (a section's wait
the run's stop ended) is that machine cancelled. Once the run is cancelled (`context.cancelled`, a person's Ctrl-C or
`cancel`), no machine begins: each left is recorded cancelled, not begun, and those under way end by their own steps.
Where `each` itself is cancelled (the stop's grace ended) or the runner's own record fails, every machine still under
way is cancelled and waited for before it returns or raises (a `TaskGroup`), so that nothing of the run, a question
among it, is left running past the loop's end.

Each machine's end is one `end` event, emitted here, with its outcome, its why and the stage its own last `stage`
event named (a stand-in's moved to its identity by the join): what the job's record keeps in `machine_outcomes`, the
exit record, and what `each` returns, by the machine's key at its end. The body emits its own `stage` events, the first
of a stand-in with its name (`name=`) and before any `locks.section` for it, and its join; it returns what its end
says where it went well, or nothing. A stand-in's end carries its name (`names`), so that one cancelled before its
body began is said and kept by name. `one_at_a_time(context, machines, body)` is the same with a bound of one, for the
sign-ins. `status(ended)` is the run's exit status from its machines' outcomes.

The standard library alone, with runs/job.py and text.py; asyncio is imported where it is used.
"""

from __future__ import annotations

import dataclasses
import weakref
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from claude_vm.runs import job as jobs
from claude_vm.text import one_line

if TYPE_CHECKING:
    from claude_vm.runs.attached import Context

# What a machine's work is: given its key, it returns what the machine's end says where it went well, or nothing.
Body = Callable[[str], Awaitable[str | None]]

# The why of a machine cancelled before its body began, and of one whose body was cancelled and said nothing.
NOT_BEGUN = "not begun: the run is stopping"
STOPPED = "stopped"


@dataclasses.dataclass(frozen=True)
class Ended:
    """A machine's end, as its `end` event wrote it into the job's record: its outcome (succeeded, failed or
    cancelled), the stage its own last stage event named, or None, and why."""

    outcome: str
    stage: str | None
    why: str


class _Heard:
    """What a job's events have said of its machines since a runner first ran on it: each stand-in joined, with its
    identity, and each machine's last stage, by the key its events name it by. A listener of the job's (runs/job.py)."""

    def __init__(self) -> None:
        self.joined: dict[str, str] = {}
        self.stages: dict[str, str] = {}

    def __call__(self, event: jobs.Event) -> None:
        if event.machine is None:
            return
        if event.kind == "join":
            self.joined[event.text] = event.machine
            if event.text in self.stages:
                self.stages[event.machine] = self.stages.pop(event.text)
        elif event.kind == "stage":
            self.stages[event.machine] = event.text

    def key(self, machine: str) -> str:
        """The key machine's events name it by now: its identity once a stand-in is joined."""
        return self.joined.get(machine, machine)


# One listener a job, however many runners run on it.
_HEARD: weakref.WeakKeyDictionary[jobs.Job, _Heard] = weakref.WeakKeyDictionary()


def _heard(job: jobs.Job) -> _Heard:
    heard = _HEARD.get(job)
    if heard is None:
        heard = _HEARD[job] = _Heard()
        job.listen(heard)
    return heard


def _checked(machines: Sequence[str], most: int, names: Mapping[str, str]) -> list[str]:
    """machines as a list, each checked before anything begins: a key once each, and a stand-in with its name."""
    if isinstance(most, bool) or not isinstance(most, int) or most < 1:
        raise ValueError(f"a run over several machines takes them at least one at a time, never {most!r} at once")
    if isinstance(machines, str):
        raise TypeError("a run over several machines is given its machines' keys, never one string")
    given = list(machines)
    seen: set[str] = set()
    for machine in given:
        if not isinstance(machine, str) or not machine:
            raise TypeError(f"a machine is named by its key, a string: not {machine!r}")
        if machine in seen:
            raise ValueError(f"{machine!r} is named twice: a run over several machines starts one task for each")
        seen.add(machine)
        if jobs.is_stand_in(machine) and not names.get(machine):
            raise ValueError(f"{machine!r} is a machine not yet made: its name is given (names=), for its end")
    return given


async def each(
    context: Context,
    machines: Sequence[str],
    body: Body,
    most: int,
    *,
    names: Mapping[str, str] | None = None,
) -> dict[str, Ended]:
    """body(machine) for each machine, never more than most at once, each machine's end emitted and returned, by its
    key at its end, in the order given (the module says how). names gives each stand-in's name, the one its first event
    says. Refused before anything begins: a bound under one, a machine named twice, and a stand-in with no name."""
    import asyncio

    named = dict(names or {})
    given = _checked(machines, most, named)
    heard = _heard(context.job)
    gate = asyncio.Semaphore(most)
    ended: dict[str, Ended] = {}

    def end(machine: str, outcome: str, why: str) -> None:
        key = heard.key(machine)
        stage = heard.stages.get(key)
        extra: dict[str, object] = {"outcome": outcome, "stage": stage}
        if jobs.is_stand_in(key):
            extra["name"] = named[key]
        context.emit("end", key, why, **extra)
        ended[machine] = Ended(outcome, stage, why)

    async def one(machine: str) -> None:
        try:
            async with gate:
                if context.cancelled.is_set():
                    end(machine, "cancelled", NOT_BEGUN)
                    return
                try:
                    said = await body(machine)
                except asyncio.CancelledError as stopped:
                    end(machine, "cancelled", one_line(str(stopped), None) or STOPPED)
                    task = asyncio.current_task()
                    if task is not None and task.cancelling():
                        raise
                    return
                except Exception as error:
                    end(machine, "failed", one_line(str(error), None) or type(error).__name__)
                    return
                end(machine, "succeeded", one_line(said, None) if isinstance(said, str) else "")
        except asyncio.CancelledError:
            # Cancelled from outside while it waited for its turn: never begun.
            if machine not in ended:
                end(machine, "cancelled", NOT_BEGUN)
            raise

    try:
        async with asyncio.TaskGroup() as group:
            for machine in given:
                group.create_task(one(machine))
    except BaseExceptionGroup as failed:
        # The runner's own error (its record refused): every other machine was cancelled and waited for first.
        raise failed.exceptions[0] from None
    return {heard.key(machine): ended[machine] for machine in given}


async def one_at_a_time(
    context: Context, machines: Sequence[str], body: Body, *, names: Mapping[str, str] | None = None
) -> dict[str, Ended]:
    """each with a bound of one: one machine after another, as the sign-ins go."""
    return await each(context, machines, body, 1, names=names)


def status(ended: Mapping[str, Ended]) -> int:
    """The exit status of a run over several machines from their outcomes: 1 where one failed, 130 where one was
    cancelled and none failed, else 0."""
    outcomes = {each.outcome for each in ended.values()}
    if "failed" in outcomes:
        return 1
    if "cancelled" in outcomes:
        return 130
    return 0
