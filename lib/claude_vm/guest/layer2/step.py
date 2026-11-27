"""One shape for a step of layer 2, the context it is handed, and the runner of a machine's steps (docs/architecture/
engine.md §16.10).

A step is a small object with a `name` (the key its events name it by, lower-case words joined by a hyphen, as
runs/stages.py holds a step's key), a `part` (the part of a configure it belongs to, one of today's six), and three
methods: `probe(ctx)`, which reads the machine and returns the step's state, changing nothing; `apply(ctx, state)`,
which brings the machine from that state to what the settings name and returns whether it changed anything, nothing
where the state is what they name already, so that a second run applies nothing; and `difference(state)`, what `apply`
would change, as a `Difference` (what, as it is now, as it would be), or None where it would change nothing: F21's
`--check --diff` says it, and runs no `apply`. A step that cannot go on raises `Failed`, its sentence in this engine's
words; anything else it raises is a fault of the engine's and goes up as it is.

The context (`Context`) is one machine's in one run: the run's job, the machine's `Target` (the route's), the key its
events name it by (its identity), the settings (each name's value, the run's resolved ones), the environment every
child is started with, and the cancel flag (the run's `cancelled`, an object whose `is_set()` says the run is
stopping). `run(command, ...)` is the SSH runner's one bounded call on the machine, as its account or, with `as_root`,
through the machine's sudo by its path under `sudo -n` (`become_exe`, the stand-in's by name in a test): where sudo
wants a password, the call fails, since a VM the product makes has a passwordless sudo and no secret follows a step's
command. `emit(kind, text, **data)` is one event of the run about the machine.

`run_steps(ctx, steps)` runs a machine's steps in their order: for each, unless the run is stopping, a `step` event
begins it, its probe reads the state and its apply brings the machine to its settings, and a `step` event ends it,
`done` (with `changed`, whether its apply changed anything) or `failed` with why. The first that fails stops the
machine: no later step is probed. Each event carries the step's part, and the run's stage where the context names one.
It returns a `Ran`: the machine's outcome (`succeeded`, `failed` or `cancelled`), each step's that ran, and why it
failed.

`check_steps(ctx, steps)` is layer 2's part of F21's deep diff, `deploy --check`'s: layer 2 has no check mode, so each
step answers from its probe and its difference, and no apply runs and no event is emitted. It returns one `Checked` a
step, in their order: `would change` with the difference, `would not change` where there is none, `would apply` for a
step that has no read of its own (its `reads` False), never probed, and `not evaluated`, with why, where its probe
could not go on. Each step reads the machine as it is: a later step's read never sees what an earlier one would write.

Standard library only; asyncio is imported inside the coroutines that start a child.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

from claude_vm.guest import remote
from claude_vm.schema.rules import CONFIGURE_ROLES
from claude_vm.ssh import runner

if TYPE_CHECKING:
    from claude_vm.runs.job import Job

# A step's key, as runs/stages.py holds one: lower-case words of letters and digits joined by one hyphen.
NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
# The parts of a configure a step belongs to: today's six, by their roles' names.
PARTS = CONFIGURE_ROLES
# What a machine's steps end as: today's outcomes of a machine (runs/job.py's OUTCOMES) that a step can give.
OUTCOMES = ("succeeded", "failed", "cancelled")


class Failed(Exception):
    """A step could not bring the machine to its settings: why, one sentence in this engine's words."""


@dataclasses.dataclass(frozen=True)
class Difference:
    """What a step's apply would change: what it is about, as it is now, and as it would be, each as a person reads
    it."""

    what: str
    now: str
    wanted: str


class Cancelled(Protocol):
    """What says the run is stopping: the run's Context.cancelled (runs/attached.py)."""

    def is_set(self) -> bool: ...


class Step(Protocol):
    """A step of layer 2: see the module's docstring."""

    name: str
    part: str

    async def probe(self, ctx: Context) -> object: ...

    async def apply(self, ctx: Context, state: object) -> bool: ...

    def difference(self, state: object) -> Difference | None: ...


@dataclasses.dataclass(frozen=True)
class Context:
    """One machine's in one run: see the module's docstring. stage, the run's stage the steps' events belong to, or
    None for none; become_exe, the machine's sudo, by its path."""

    job: Job
    target: runner.Target
    machine: str
    settings: Mapping[str, object]
    env: Mapping[str, str]
    cancelled: Cancelled
    stage: str | None = None
    become_exe: str = remote.SUDO

    def __post_init__(self) -> None:
        if not isinstance(self.target, runner.Target) or self.target.identity is None:
            raise runner.Refused("a step's machine is a machine's Target, never the Proxmox host's")
        if not isinstance(self.machine, str) or not self.machine:
            raise runner.Refused("a step's events name its machine by its key, a str")
        if not isinstance(self.settings, Mapping) or not all(isinstance(name, str) for name in self.settings):
            raise runner.Refused("a step's settings are each name's value, keyed by the name")
        if not callable(getattr(self.cancelled, "is_set", None)):
            raise runner.Refused("a step's cancel flag says whether the run is stopping (is_set)")

    def emit(self, kind: str, text: str, **data: object) -> None:
        """One event of the run about this machine."""
        self.job.emit(kind, self.machine, text, **data)

    def command(self, command: Sequence[str], as_root: bool = False) -> list[str]:
        """command as the machine runs it: as its account, or as root through its sudo, by its path, in the C locale
        so that sudo's words are its own, never asking (-n)."""
        words = list(command)
        if not words or not all(isinstance(word, str) for word in words):
            raise runner.Refused("a step's command is a list of words")
        if not as_root:
            return words
        return [remote.ENV, remote.LOCALE, self.become_exe, "-n", "-H", "--", *words]

    async def run(
        self, command: Sequence[str], *, as_root: bool = False, limits: runner.Limits, stdin: bytes = b""
    ) -> runner.Result:
        """command run once on the machine through the SSH runner, under limits: see command. Failed where sudo wants
        a password, which a step never sends."""
        result = await runner.run(self.job, self.target, self.command(command, as_root), stdin, limits, dict(self.env))
        if as_root and isinstance(result, runner.Done) and result.status != 0 and _wants_a_password(result.said):
            raise Failed(
                f"sudo on the machine asks {self.target.account} for a password, and a step sends none: a VM this"
                " engine makes has a passwordless sudo"
            )
        return result


def _wants_a_password(said: str) -> bool:
    """Whether sudo refused under -n for want of a password, in sudo's words or sudo-rs's."""
    return any(remote.WANTS_A_PASSWORD.fullmatch(line.strip()) for line in said.splitlines())


@dataclasses.dataclass(frozen=True)
class Checked:
    """One step of a check: its name, its part, its verdict (one of VERDICTS), its difference where it would change
    something, and why it could not be evaluated ("" otherwise)."""

    name: str
    part: str
    verdict: str
    difference: Difference | None = None
    why: str = ""


# What a check makes of a step.
VERDICTS = ("would change", "would not change", "would apply", "not evaluated")


async def check_steps(ctx: Context, steps: Sequence[Step]) -> tuple[Checked, ...]:
    """steps answered on ctx's machine from their probes and differences, none applied: see the module's docstring."""
    made = []
    for step in _held(steps):
        if ctx.cancelled.is_set():
            break
        if getattr(step, "reads", True) is False:
            made.append(Checked(step.name, step.part, "would apply"))
            continue
        try:
            difference = step.difference(await step.probe(ctx))
        except Failed as failed:
            made.append(Checked(step.name, step.part, "not evaluated", why=str(failed)))
            continue
        if difference is not None and not isinstance(difference, Difference):
            raise TypeError(f"step {step.name}'s difference is a Difference, or None for none")
        verdict = "would not change" if difference is None else "would change"
        made.append(Checked(step.name, step.part, verdict, difference))
    return tuple(made)


@dataclasses.dataclass(frozen=True)
class Ran:
    """A machine's steps run: its outcome (one of OUTCOMES), each step that ran with whether it changed anything (in
    the order they ran), and why it failed ("" where it did not)."""

    outcome: str
    changed: tuple[tuple[str, bool], ...] = ()
    why: str = ""


def _held(steps: object) -> tuple[Step, ...]:
    """steps as run_steps takes them: a sequence of steps, each named as a step is and of a part of a configure, no
    name twice."""
    if isinstance(steps, (str, bytes)) or not isinstance(steps, Sequence):
        raise runner.Refused("a machine's steps are a list of them")
    names = []
    for step in steps:
        name, part = getattr(step, "name", None), getattr(step, "part", None)
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise runner.Refused(f"a step is named by lower-case words joined by a hyphen, not {name!r}")
        if part not in PARTS:
            raise runner.Refused(f"step {name}'s part is one of a configure's, {', '.join(PARTS)}, not {part!r}")
        names.append(name)
    if len(set(names)) != len(names):
        raise runner.Refused("a machine's steps name each step once")
    return tuple(steps)


def _event(ctx: Context, step: Step, **data: object) -> None:
    extra: dict[str, object] = {"part": step.part, **data}
    if ctx.stage is not None:
        extra["stage"] = ctx.stage
    ctx.emit("step", step.name, **extra)


async def run_steps(ctx: Context, steps: Sequence[Step]) -> Ran:
    """steps run on ctx's machine in their order, each probed and applied, the first that fails stopping the machine:
    see the module's docstring."""
    held = _held(steps)
    changed: list[tuple[str, bool]] = []
    for step in held:
        if ctx.cancelled.is_set():
            return Ran("cancelled", tuple(changed))
        _event(ctx, step)
        try:
            state = await step.probe(ctx)
            did = await step.apply(ctx, state)
        except Failed as failed:
            why = str(failed)
            _event(ctx, step, outcome="failed", why=why)
            return Ran("failed", tuple(changed), why)
        if not isinstance(did, bool):
            raise TypeError(f"step {step.name}'s apply says whether it changed anything, a bool")
        changed.append((step.name, did))
        _event(ctx, step, outcome="done", changed=did)
    return Ran("succeeded", tuple(changed))
