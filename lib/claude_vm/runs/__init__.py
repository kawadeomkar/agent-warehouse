"""A run of the new engine: its one record and its text log, the children it starts, the short-lived directory of its
generated files, the kernel's file locks it holds, its end on a signal, and a run detached as the same program in a
session of its own (docs/architecture/engine.md §16.8).

`run(draft, body)` is the one entry a verb calls: it makes the job the draft describes (job.py), which takes the job's
own lock, makes the run's directory (rundir.py), takes the locks the draft names (locks.py), and runs the body in this
process to its stated end (attached.py). A run refused at its locks, or whose directory or ladder cannot be made,
leaves no job, and what its drop left is said with the refusal or the error. From before the job is made until the
ladder's handlers are set, the signals the ladder takes are held off, so that a Ctrl-C, a hangup or a kill in between
reaches the ladder once it can take it, never leaving a job that reads lost. Its modules are imported only when it is
called, so that what reads a job (logs, cancel) loads none of them.

It imports schema/ and fleet/, of cli/ only the printer of a command (cli/inverse.py) for the line that cancels a run,
and of today's lib/claude_vm/ only what tests/unit/test_aw_layout.py's REUSED names; nothing of engine/ or control/, no
pydantic, and never today's claude_vm.ansible. Imported by cli/ and engine/; nothing of today's product imports it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_vm.runs.attached import Body


@dataclass(frozen=True)
class Draft:
    """The job a verb's run makes, as runs/job.py's create takes it: where the jobs are, the verb, the command as typed
    with no secret, the host, the machines by identity, what started it, whether it runs at a terminal, and its
    resolved inputs; and the locks it takes, as runs/locks.py's take takes them: the kind (C7's locks_for works it out
    from the table, and a verb's card before it passes its own), the machines it names by their host and name, the
    default VM's among them, and the policy (locks.policy_of: the setting run_lock's, or none for --no-lock). The kind
    is always given: no default could stand for a verb's."""

    jobs_dir: Path
    verb: str
    command: str
    host: str | None
    machines: Sequence[str]
    starter: Mapping[str, str]
    attached: bool
    inputs: Mapping[str, object] | None = None
    lock_kind: str = field(kw_only=True)
    lock_machines: Sequence[tuple[str, str]] = field(default=(), kw_only=True)
    lock_policy: str = field(default="machine", kw_only=True)


def run(draft: Draft, body: Body) -> int:
    """The verb's body run as the draft's job, in this process, to its stated end; its exit status. Where the run's
    directory cannot be made, its locks are refused (locks.Held), or its ladder cannot be built (attached.run), the job
    is dropped, as a run that never began, and the error raised, with the line that says what the drop left where it
    left anything."""
    import contextlib
    import signal

    from claude_vm.runs import attached, locks, rundir
    from claude_vm.runs import job as jobs

    # Held off until the ladder's handlers are set: in between, a signal would leave the job made, never ended.
    found = signal.pthread_sigmask(signal.SIG_BLOCK, attached.TAKEN)
    try:
        made = jobs.create(
            draft.jobs_dir,
            draft.verb,
            draft.command,
            draft.host,
            draft.machines,
            draft.starter,
            draft.attached,
            inputs=draft.inputs,
        )
        try:
            rundir.make(made)
            try:
                locks.take(made, draft.lock_kind, draft.lock_machines, draft.lock_policy)
            except BaseException:
                with contextlib.suppress(Exception):
                    rundir.remove(made)
                raise
        except BaseException as error:
            left = jobs.drop(made)
            if left is not None:
                if isinstance(error, locks.Held):
                    raise error.leaving(left) from None
                error.add_note(left)
            raise
        return attached.run(made, body, mask=found)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, found)
