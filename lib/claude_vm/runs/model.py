"""A run as a person reads it: a verb's declared stages (runs/stages.py) and the events its run emits (runs/job.py's
emit) made into stages, steps, each machine's state in each, the waits, the reports, the failures, each machine's
outcome and NEXT STEPS, reading and drawing nothing (docs/architecture/engine.md §16.8).

`Run(declared, now, machines)` is fed each event in the run's own process, with the time given (`feed(event, now)`),
by the listener the run registers with the job (the live view); nothing here reads a clock, a file or the terminal, and
nothing is kept between two processes. What it gives a drawer (runs/view.py, which draws it with tui/): each stage's
state and its steps', each machine's mark in a stage or a step, elapsed times, the waits and a person's time in them,
the reports and failures in the order said, each machine's outcome and name, the run's own, and `next_steps()`. No
count of tasks is guessed: a stage or a step is done when its event says so.

A machine is keyed by its identity (`agent-warehouse-<SMBIOS UUID>`), as `job.json`'s `machines`, a machine's `end` and
layer 1's step events name it: one key for every event of a machine. The name a person chose rides beside it, as an
event's `name` (layer 1's steps carry it) or as the run's machines given as a mapping of identity to name; the model
keeps each identity's name (`names`, `name(machine)`), and a drawer says the name wherever it says a machine.

The events, as the run emits them (job.KINDS; their strings are plain, as emit makes them, and a drawer makes them
visible: a tab, a line break and Unicode's format characters stay here):

- `stage`, its text a declared stage's key: with no `outcome` it begins; with `outcome` done, skipped or failed it ends,
  with `why` where it failed, and so does whatever of its steps is still open for it. For one machine, or with no
  machine for every machine of the run: a machine with no part of its own in a stage reads every machine's, where that
  began before the machine's own end. A stage that ended reads failed for a machine where a step of it failed for that
  machine (and for the run, where one did for any): the failure wins, as today's.
- `step`, its text a step's key and `stage` the key of the stage that declares it: it begins or ends as a stage does,
  and its start begins its stage where it had not begun. A `step` that names no stage is a line of a machine's work,
  such as each of layer 1's events (guest/events.py, its text a task's name): kept as that machine's latest line, it
  begins and ends nothing.
- `report`: kept whole, in the order said, to be printed above the region.
- `waiting`, its text what the run waits for: it opens the wait of its machine and its `section` (a short section's
  wait is another run's, runs/locks.py; one with no section is the person's), in place of one that was open; an empty
  text ends that wait alone, of that machine and that section. A wait also ends when its machine ends, at the cancel
  (`cancel`, when a person stops the run) and at the run's end.
- `next`, a line of NEXT STEPS for its machine and `matter`; an empty line removes it.
- `end`: with a machine, that machine's outcome (succeeded, failed or cancelled), the stage it reached (`stage`, else
  the last of its own `stage` events, as the job's record keeps it) and why; what of it is still open in the stages that
  are each machine's ends as its outcome says (done, failed, stopped), and so do its waits. With none, the run's end:
  whatever is still open ends, done where the run succeeded and stopped where it failed or was cancelled (cut short,
  never failed), every wait with it, and the run takes no event after it.

An event of a stage the declaration does not have, a step its stage does not declare, a kind the job has not, an
ending that is not done, skipped or failed, a NEXT STEPS line with no matter, a name that is no str, and any event
after the run's end are refused before anything is kept. A failure is said once: each step that ended failed; a stage
that ended failed for a machine where no failure of it was said for that machine, and one that ended failed for every
machine where none of it was said at all or where it cut short a part still open; a machine that ended failed where
nothing of it had; and the run that ended failed, its own `why`, where nothing had failed or where it cut short a part
still open (so a run that ends failed only because its machines did says nothing twice).

Reused as it stands: run/next_steps.py's ordering. Not reused: run/model.py, whose input is Ansible's plays and which
stays today's product's. It imports nothing of tui/ (tests/unit/test_aw_run_model.py).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from claude_vm.run.next_steps import NEXT_STATS
from claude_vm.run.next_steps import next_steps as ordered
from claude_vm.runs.job import KINDS, OUTCOMES
from claude_vm.runs.stages import Declared, Stage, Step

if TYPE_CHECKING:
    from claude_vm.runs.job import Event

# How a stage or a step ends, as its event says.
ENDS = ("done", "skipped", "failed")
# What of a machine is still open when it ends ends as its outcome says.
BY_OUTCOME = {"succeeded": "done", "failed": "failed", "cancelled": "stopped"}
# What is still open when the run ends: done where it succeeded, else cut short - stopped, never failed: what failed
# said so itself, and the run's own why is said once as its failure.
BY_RUN_OUTCOME = {"succeeded": "done", "failed": "stopped", "cancelled": "stopped"}
# Every state a stage, a step or a machine's mark in one reads: not begun, at it, at it and waiting for the person, and
# each way of ending.
STATES = ("to come", "running", "waiting", *ENDS, "stopped")
# The states of a stage that has ended, which a failed step of it turns to failed.
ENDED = ("done", "skipped", "stopped")


@dataclass
class _Book:
    """What every Progress of one run reads: the number of the event being kept, in the order the events were fed (two
    may share a now), and the number of each machine's end."""

    order: int = 0
    ends: dict[str, int] = field(default_factory=dict)


@dataclass
class Part:
    """One machine's time in a stage or a step, or every machine's (the part of machine None): when it began, and once
    it has ended, when, how (one of ENDS, or stopped) and why; order, the number of the event that made it."""

    began: float
    ended: float | None = None
    outcome: str | None = None
    why: str = ""
    order: int = 0

    @property
    def state(self) -> str:
        return "running" if self.ended is None else str(self.outcome)

    def close(self, outcome: str, why: str, now: float) -> None:
        self.ended, self.outcome, self.why = now, outcome, why

    def seconds(self, now: float) -> float:
        return (self.ended if self.ended is not None else now) - self.began


class Progress:
    """How far a stage or a step is: a part for each machine that began or ended it on its own, and one for every
    machine (None), which a machine with no part of its own reads where it began before the machine's own end."""

    def __init__(self, spec: Stage | Step, book: _Book) -> None:
        self.spec = spec
        self.book = book
        self.parts: dict[str | None, Part] = {}

    def of(self, machine: str | None) -> Part | None:
        """The part a machine reads: its own, else every machine's where that began before the machine's end (a
        machine that has ended took no part in what began after it); with None, every machine's."""
        if machine is not None and machine in self.parts:
            return self.parts[machine]
        every = self.parts.get(None)
        if machine is None or every is None:
            return every
        end = self.book.ends.get(machine)
        return every if end is None or every.order < end else None

    def open_for(self, machine: str | None) -> bool:
        """Whether it is under way for machine; with None, for any machine at all."""
        if machine is None:
            return any(part.ended is None for part in self.parts.values())
        part = self.of(machine)
        return part is not None and part.ended is None

    def begin(self, machine: str | None, now: float) -> None:
        """Begun for machine, or for every machine: begun again where it had ended, left as it is where it runs."""
        part = self.parts.get(machine)
        if part is None or part.ended is not None:
            self.parts[machine] = Part(now, order=self.book.order)

    def close_open(self, outcome: str, why: str, now: float) -> bool:
        """Every part still open ended so; whether there was one."""
        found = [part for part in self.parts.values() if part.ended is None]
        for part in found:
            part.close(outcome, why, now)
        return bool(found)

    def end(self, machine: str | None, outcome: str, why: str, now: float) -> bool:
        """Ended for machine, or with None for every machine; whether anything ended. A machine with no part of its own
        gets one from when every machine's began, or from now; a part that has ended is left as it is."""
        if machine is None:
            closed = self.close_open(outcome, why, now)
            if None in self.parts:
                return closed
            began = min((part.began for part in self.parts.values()), default=now)
            self.parts[None] = Part(began, now, outcome, why, order=self.book.order)
            return True
        own = self.parts.get(machine)
        if own is not None:
            if own.ended is not None:
                return False
            own.close(outcome, why, now)
            return True
        every = self.parts.get(None)
        began = every.began if every is not None else now
        self.parts[machine] = Part(began, now, outcome, why, order=self.book.order)
        return True

    @property
    def state(self) -> str:
        """to come, running, or how it ended: failed or stopped where any part did, skipped where every part was, else
        done; its own parts alone. Waiting, and a stage's failed step, are the run's to say (Run.state), from its waits
        and its steps."""
        if not self.parts:
            return "to come"
        states = {part.state for part in self.parts.values()}
        if "running" in states:
            return "running"
        for each in ("failed", "stopped"):
            if each in states:
                return each
        return "skipped" if states == {"skipped"} else "done"

    def began(self) -> float | None:
        return min((part.began for part in self.parts.values()), default=None)

    def ended(self) -> float | None:
        if not self.parts or self.open_for(None):
            return None
        return max(part.ended for part in self.parts.values() if part.ended is not None)

    def seconds(self, now: float) -> float:
        """From its first part's start to its last part's end, or to now while it runs; 0 before it begins."""
        began, ended = self.began(), self.ended()
        if began is None:
            return 0.0
        return (ended if ended is not None else now) - began


class StageProgress(Progress):
    """A stage's progress, with each of its declared steps', in their order."""

    def __init__(self, spec: Stage, book: _Book) -> None:
        super().__init__(spec, book)
        self.steps: dict[str, Progress] = {step.key: Progress(step, book) for step in spec.steps}


@dataclass
class Wait:
    """A wait: whose (machine None: the run's), the short section it is for (None: the person's), what is waited for,
    what its event carried (a page, a section), the stage and the step its machine was at when it began, and when it
    began and ended."""

    machine: str | None
    section: str | None
    text: str
    data: Mapping[str, object]
    stage: str | None
    step: str | None
    since: float
    until: float | None = None

    @property
    def yours(self) -> bool:
        """Whether it waits for the person: every wait but a short section's, which waits for another run."""
        return self.section is None

    def seconds(self, now: float) -> float:
        return (self.until if self.until is not None else now) - self.since


@dataclass(frozen=True)
class Report:
    """A report, whole, as said: about which machine (None: the run), its text, what its event carried, and when."""

    machine: str | None
    text: str
    data: Mapping[str, object]
    at: float


@dataclass(frozen=True)
class Failure:
    """A failure, said once: the stage and the step (None: the stage's, or the machine's or the run's own) it was in,
    whose (None: every machine's, or the run's), why, how long the failed part had run, and when."""

    stage: str | None
    step: str | None
    machine: str | None
    why: str
    seconds: float
    at: float


@dataclass(frozen=True)
class MachineEnd:
    """A machine's outcome: succeeded, failed or cancelled, the stage it reached, why, and when."""

    outcome: str
    stage: str | None
    why: str
    at: float


@dataclass(frozen=True)
class Line:
    """The latest line of a machine's work (a `step` that names no stage): its text, the stage and step the machine was
    at, and when."""

    text: str
    stage: str | None
    step: str | None
    at: float


class Run:
    """A run as a person reads it, from its declared stages and the events it emits, fed in order with the time given.
    machines are those the run was given, by identity, in their order: an iterable of identities, or a mapping of each
    identity to the name a person chose. A machine an event names that is not one is added after them, where it names
    it in work that is each machine's."""

    def __init__(self, declared: Declared, now: float, machines: Iterable[str] | Mapping[str, str] = ()) -> None:
        if not isinstance(declared, Declared):
            raise TypeError("a run is made from a Declared")
        self.declared = declared
        self.began = now
        self.machines: list[str] = []
        # The name a person chose for each identity, as given or as an event's `name` said it.
        self.names: dict[str, str] = {}
        given = machines.items() if isinstance(machines, Mapping) else ((machine, None) for machine in machines)
        for machine, name in given:
            if not isinstance(machine, str) or not isinstance(name, (str, type(None))):
                raise TypeError("a run's machines are identities, each a str, with a name, a str, where one is given")
            self._learn(machine)
            if name:
                self.names[machine] = name
        self._book = _Book()
        self.stages: dict[str, StageProgress] = {
            stage.key: StageProgress(stage, self._book) for stage in declared.stages
        }
        self.waits: list[Wait] = []
        self.reports: list[Report] = []
        self.failures: list[Failure] = []
        self.outcomes: dict[str, MachineEnd] = {}
        self.latest: dict[str | None, Line] = {}
        # NEXT STEPS: (machine, matter): line, in the order recorded.
        self._next: dict[tuple[str | None, str], str] = {}
        # The stage of each machine's own last `stage` event, as the job's record keeps it.
        self._reached: dict[str, str] = {}
        # When a person stopped the run (cancel), and how the run ended (its end event).
        self.stopping: float | None = None
        self.ended: float | None = None
        self.outcome: str | None = None
        self.exit: int | None = None
        self.why = ""

    # --- the events ----------------------------------------------------------------------------------------------
    def feed(self, event: Event, now: float) -> None:
        """One event of the run, at now: checked, then kept. Refused, with nothing kept, as the module says."""
        if self.ended is not None:
            raise ValueError("the run has ended: it takes no event after its end")
        if event.kind not in KINDS:
            raise ValueError(f"{event.kind!r} is no kind of event: one of {', '.join(KINDS)}")
        if event.machine is not None and not isinstance(event.machine, str):
            raise TypeError("an event's machine is a str or None")
        if not isinstance(event.text, str):
            raise TypeError("an event's text is a str")
        name = event.data.get("name")
        if name is not None and not isinstance(name, str):
            raise TypeError("a machine's name is a str")
        getattr(self, f"_on_{event.kind}")(event.machine, event.text, event.data, now)
        if name and event.machine is not None:
            self.names[event.machine] = name
        self._book.order += 1

    def cancel(self, now: float) -> None:
        """A person stopped the run (a Ctrl-C, a cancel): every wait ends now, since nobody is asked any more, and the
        run reads stopping until its end. Nothing once it has ended."""
        if self.ended is not None:
            return
        if self.stopping is None:
            self.stopping = now
        for wait in self.waiting():
            wait.until = now

    def _on_stage(self, machine: str | None, key: str, data: Mapping[str, object], now: float) -> None:
        stage = self._declared(key)
        outcome, why = _ending(data), _why(data)
        self._learn_in(stage.spec, machine)
        if machine is not None:
            self._reached[machine] = key
        if outcome is None:
            stage.begin(machine, now)
            return
        # Ended for every machine, it cuts short what is still open of it; its failure is then said for those parts.
        cut = machine is None and (stage.open_for(None) or any(step.open_for(None) for step in stage.steps.values()))
        for step in stage.steps.values():
            if machine is None:
                step.close_open(outcome, why, now)
            elif step.open_for(machine):
                step.end(machine, outcome, why, now)
        part = stage.of(machine)
        if stage.end(machine, outcome, why, now) and outcome == "failed" and (cut or not self._failed(key, machine)):
            seconds = part.seconds(now) if part is not None else 0.0
            self.failures.append(Failure(key, None, machine, why, seconds, now))

    def _on_step(self, machine: str | None, key: str, data: Mapping[str, object], now: float) -> None:
        stage_key = data.get("stage")
        if stage_key is None:
            self._learn(machine)
            self.latest[machine] = Line(key, *self._where(machine), now)
            return
        if not isinstance(stage_key, str):
            raise TypeError("a step names its stage by its key, a str")
        stage = self._declared(stage_key)
        step = stage.steps.get(key)
        if step is None:
            declared = ", ".join(stage.steps) or "none"
            raise ValueError(f"{key!r} is no step of stage {stage_key!r}, which declares {declared}")
        outcome, why = _ending(data), _why(data)
        self._learn_in(stage.spec, machine)
        if outcome is None:
            if not stage.open_for(machine):
                stage.begin(machine, now)
            step.begin(machine, now)
            return
        part = step.of(machine)
        if step.end(machine, outcome, why, now) and outcome == "failed":
            seconds = part.seconds(now) if part is not None else 0.0
            self.failures.append(Failure(stage_key, key, machine, why, seconds, now))

    def _on_report(self, machine: str | None, text: str, data: Mapping[str, object], now: float) -> None:
        self._learn(machine)
        if text:
            self.reports.append(Report(machine, text, dict(data), now))

    def _on_waiting(self, machine: str | None, text: str, data: Mapping[str, object], now: float) -> None:
        section = data.get("section")
        if section is not None and not isinstance(section, str):
            raise TypeError("a wait's section is named by a str")
        self._learn(machine)
        for wait in self.waiting():
            if (wait.machine, wait.section) == (machine, section):
                wait.until = now
        if text:
            self.waits.append(Wait(machine, section, text, dict(data), *self._where(machine), now))

    def _on_next(self, machine: str | None, text: str, data: Mapping[str, object], now: float) -> None:
        matter = data.get("matter")
        if not isinstance(matter, str) or not matter:
            raise ValueError("a NEXT STEPS line names its matter")
        self._learn(machine)
        if text:
            self._next[(machine, matter)] = text
        else:
            self._next.pop((machine, matter), None)

    def _on_end(self, machine: str | None, text: str, data: Mapping[str, object], now: float) -> None:
        outcome = data.get("outcome")
        if outcome not in OUTCOMES:
            raise ValueError(f"an end says its outcome: one of {', '.join(OUTCOMES)}")
        assert isinstance(outcome, str)
        if machine is not None:
            closed = BY_OUTCOME[outcome]
            stage = data.get("stage", self._reached.get(machine))
            if stage is not None and not isinstance(stage, str):
                raise TypeError("a machine's end names its stage by its key, a str")
            self._learn(machine)
            # From here the machine reads no part of every machine's that begins later (Progress.of).
            self._book.ends.setdefault(machine, self._book.order)
            # The host's own stages (the template's) are no machine's to end.
            for progress in (each for each in self.stages.values() if each.spec.each_machine):
                for step in progress.steps.values():
                    if step.open_for(machine):
                        step.end(machine, closed, text, now)
                if progress.open_for(machine):
                    progress.end(machine, closed, text, now)
            for wait in self.waiting():
                if wait.machine == machine:
                    wait.until = now
            self.outcomes[machine] = MachineEnd(outcome, stage, text, now)
            if outcome == "failed" and not any(failure.machine == machine for failure in self.failures):
                self.failures.append(Failure(stage, None, machine, text, now - self.began, now))
            return
        status = data.get("exit")
        if status is not None and (not isinstance(status, int) or isinstance(status, bool)):
            raise TypeError("the run's exit status is an int")
        closed = BY_RUN_OUTCOME[outcome]
        where = self.running_stage()
        cut = False
        for progress in self.stages.values():
            for step in progress.steps.values():
                cut = step.close_open(closed, text, now) or cut
            cut = progress.close_open(closed, text, now) or cut
        for wait in self.waiting():
            wait.until = now
        # The run's own why, said where nothing had failed, or where it cut short a part still open: a run that fails
        # only because its machines did has said why already.
        if outcome == "failed" and (cut or not self.failures):
            self.failures.append(Failure(where, None, None, text, now - self.began, now))
        self.ended, self.outcome, self.exit, self.why = now, outcome, status, text

    # --- what it gives a drawer ------------------------------------------------------------------------------
    def progress(self, stage: str, step: str | None = None) -> Progress:
        """A stage's progress, or one of its steps'; KeyError for one the declaration does not have."""
        found = self.stages[stage]
        return found if step is None else found.steps[step]

    def state(self, stage: str, step: str | None = None) -> str:
        """A stage's or a step's state, one of STATES: waiting where it runs and a wait for the person is open there;
        failed where a stage has ended and a step of it failed for any machine."""
        found = self.progress(stage, step).state
        if found == "running" and any(self._at(wait, stage, step) for wait in self.waiting() if wait.yours):
            return "waiting"
        if step is None and found in ENDED and self._step_failed(stage, None):
            return "failed"
        return found

    def mark(self, machine: str, stage: str, step: str | None = None) -> str:
        """machine's state in a stage or a step, one of STATES: its own part's, else every machine's where that began
        before the machine's end, else to come; waiting where it runs and a wait of its own for the person is open
        there; failed where a stage has ended for it and a step of it failed for it."""
        part = self.progress(stage, step).of(machine)
        if part is None:
            return "to come"
        if part.ended is None:
            waits = (wait for wait in self.waiting() if wait.yours and wait.machine == machine)
            return "waiting" if any(self._at(wait, stage, step) for wait in waits) else "running"
        if step is None and part.state in ENDED and self._step_failed(stage, machine):
            return "failed"
        return part.state

    def name(self, machine: str) -> str:
        """The name a person chose for a machine, which a drawer says: as given or as an event said it, else its
        key."""
        return self.names.get(machine, machine)

    def marks(self, stage: str, step: str | None = None) -> dict[str, str]:
        """Each machine of the run's mark in a stage or a step, in the run's order; none for a stage whose work is not
        each machine's."""
        if not self.stages[stage].spec.each_machine:
            return {}
        return {machine: self.mark(machine, stage, step) for machine in self.machines}

    def running(self) -> list[str]:
        """The stages under way, in their declared order."""
        return [key for key, progress in self.stages.items() if progress.state == "running"]

    def running_stage(self) -> str | None:
        """The stage the run is at: the last under way that is not quiet, else the last under way, else None."""
        found = self.running()
        loud = [key for key in found if not self.stages[key].spec.quiet]
        return (loud or found or [None])[-1]

    def seconds(
        self, now: float, stage: str | None = None, step: str | None = None, machine: str | None = None
    ) -> float:
        """Elapsed time: the run's, from its start to its end or now; or a stage's or a step's, every machine's or one
        machine's (0 where it has not begun)."""
        if stage is None:
            return (self.ended if self.ended is not None else now) - self.began
        progress = self.progress(stage, step)
        if machine is None:
            return progress.seconds(now)
        part = progress.of(machine)
        return part.seconds(now) if part is not None else 0.0

    def waiting(self) -> list[Wait]:
        """The waits still open, in the order they began."""
        return [wait for wait in self.waits if wait.until is None]

    def waiting_for_you(self) -> bool:
        """Whether the run waits for the person now."""
        return any(wait.yours for wait in self.waiting())

    def waited(self, now: float, stage: str | None = None, step: str | None = None) -> float:
        """The person's time: every wait for the person, or those that began at a stage or a step, to their end or
        now."""
        return sum(
            wait.seconds(now) for wait in self.waits if wait.yours and (stage is None or self._at(wait, stage, step))
        )

    def next_records(self) -> list[dict[str, str | None]]:
        """NEXT STEPS' records, `{machine, matter, line}`, in the order first recorded, as the job's record keeps
        them."""
        return [{"machine": machine, "matter": matter, "line": line} for (machine, matter), line in self._next.items()]

    def next_steps(self) -> list[str]:
        """NEXT STEPS' lines in today's order (run/next_steps.py's function, by its MATTERS): by machine, by the name a
        person chose as today's orders by VM name, the run's own first, then by matter, each line once."""
        records: dict[str, dict[str, str]] = {}
        for (machine, matter), line in self._next.items():
            records.setdefault("" if machine is None else self.name(machine), {})[matter] = line
        return ordered({NEXT_STATS[0]: records})

    # --- within ----------------------------------------------------------------------------------------------
    def _declared(self, key: str) -> StageProgress:
        found = self.stages.get(key)
        if found is None:
            raise ValueError(f"{key!r} is no stage of {self.declared.title}: one of {', '.join(self.stages)}")
        return found

    def _learn(self, machine: str | None) -> None:
        if machine is not None and machine not in self.machines:
            self.machines.append(machine)

    def _learn_in(self, stage: Stage | Step, machine: str | None) -> None:
        """A machine a stage's or a step's event names is the run's where the stage's work is each machine's."""
        if isinstance(stage, Stage) and stage.each_machine:
            self._learn(machine)

    def _where(self, machine: str | None) -> tuple[str | None, str | None]:
        """The stage and the step machine is at (None: the run): the last stage under way for it, in declared order,
        and in it the last step under way for it."""
        for key, progress in reversed(self.stages.items()):
            if progress.open_for(machine):
                steps = [step for step, found in progress.steps.items() if found.open_for(machine)]
                return key, (steps[-1] if steps else None)
        return None, None

    def _step_failed(self, stage: str, machine: str | None) -> bool:
        """Whether a step of the stage ended failed for machine (the part it reads), or with None for any machine."""
        for progress in self.stages[stage].steps.values():
            parts = progress.parts.values() if machine is None else [progress.of(machine)]
            if any(part is not None and part.outcome == "failed" for part in parts):
                return True
        return False

    def _failed(self, stage: str, machine: str | None) -> bool:
        """Whether a failure in the stage is already said for machine (None: for any machine)."""
        return any(
            failure.stage == stage and (machine is None or failure.machine in (machine, None))
            for failure in self.failures
        )

    @staticmethod
    def _at(wait: Wait, stage: str, step: str | None) -> bool:
        return wait.stage == stage and (step is None or wait.step == step)


def _ending(data: Mapping[str, object]) -> str | None:
    """How a stage's or a step's event ends it: None where it begins it."""
    outcome = data.get("outcome")
    if outcome is not None and outcome not in ENDS:
        raise ValueError(f"a stage or a step ends {', '.join(ENDS[:-1])} or {ENDS[-1]}, not {outcome!r}")
    return outcome  # type: ignore[return-value]


def _why(data: Mapping[str, object]) -> str:
    why = data.get("why", "")
    if not isinstance(why, str):
        raise TypeError("why a stage or a step ended is a str")
    return why
