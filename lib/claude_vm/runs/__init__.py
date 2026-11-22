"""A run of the new engine: its one record and its text log, the children it starts, the short-lived directory of its
generated files, the kernel's file locks it holds, its end on a signal, and a run detached as the same program in a
session of its own (docs/architecture/engine.md §16.8).

`run(draft, body)` is the one entry a verb whose run is a job calls (the table's column `changes`): it makes the job the
draft describes (job.py), which takes the job's own lock, makes the run's directory (rundir.py), takes the locks the
table gives the verb with its values (lock_sets.py's locks_for, then locks.py's take), and runs the body in this process
to its stated end (attached.py). A verb that is no run makes no job: run refuses it before anything. A run refused at
its locks, or whose directory or ladder cannot be made, leaves no job, and what its drop left is said with the refusal
or the error. From before the job is made until the ladder's handlers are set, the signals the ladder takes are held
off, so that a Ctrl-C, a hangup or a kill in between reaches the ladder once it can take it, never leaving a job that
reads lost. Its modules are imported only when it is called, so that what reads a job (logs, cancel) loads none of
them.

It imports schema/, the printer of a command among it for the line that cancels a run, and fleet/, and of today's
lib/claude_vm/ only what tests/unit/test_aw_layout.py's REUSED names; nothing of cli/, engine/ or control/, no
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
    resolved inputs; and what its locks are taken from, as runs/locks.py's take takes them: the verb's values as the
    dispatch hands them (only what was given), from which lock_sets.locks_for works out the kind with the verb's row
    of the table, the machines it names by their host and name, in the words' order, the default VM's among them, and
    the policy (locks.policy_of: the setting run_lock's). The values are always given: a draft without them could
    take the locks of other words."""

    jobs_dir: Path
    verb: str
    command: str
    host: str | None
    machines: Sequence[str]
    starter: Mapping[str, str]
    attached: bool
    inputs: Mapping[str, object] | None = None
    values: Mapping[str, object] = field(kw_only=True)
    lock_machines: Sequence[tuple[str, str]] = field(default=(), kw_only=True)
    lock_policy: str = field(default="machine", kw_only=True)


def run(draft: Draft, body: Body) -> int:
    """The verb's body run as the draft's job, in this process, to its stated end; its exit status. Where the run's
    directory cannot be made, its locks are refused (locks.Held), or its ladder cannot be built (attached.run), the job
    is dropped, as a run that never began, and the error raised, with the line that says what the drop left where it
    left anything. A verb whose run is no job, or whose values come to no kind of lock, is refused with ValueError
    before anything is made."""
    import contextlib
    import signal

    from claude_vm.runs import attached, lock_sets, locks, rundir
    from claude_vm.runs import job as jobs
    from claude_vm.schema.words import VERBS

    row = VERBS.get(draft.verb)
    if row is None or not row.changes:
        raise ValueError(f"{draft.verb!r} is no verb whose run is a job: it makes no job and takes no lock of a run")
    kind = lock_sets.locks_for(draft.verb, draft.values)
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
                locks.take(made, kind, draft.lock_machines, draft.lock_policy)
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
