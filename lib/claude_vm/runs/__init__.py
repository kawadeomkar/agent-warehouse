"""A run of the new engine: its one record and its text log (textlog.py), pruned when a run that writes one starts
(prune.py) and read by logs (listing.py), the children it starts, the short-lived directory of its
generated files, the kernel's file locks it holds, its end on a signal, a run detached as the same program in a
session of its own, and the run as a person reads it, from its verb's declared stages and its events (stages.py,
model.py), drawn with tui/ (view.py), live in the run's own process as it emits them (live.py), and the questions it
asks the person at its terminal, with the safe answer where nobody is there (ask.py), and a run over several machines,
side by side under a bound, each with its own outcome (fleet_run.py) (docs/architecture/engine.md §16.8).

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
    from claude_vm.runs.job import Job
    from claude_vm.runs.textlog import Logs


@dataclass(frozen=True)
class Draft:
    """The job a verb's run makes, as runs/job.py's create takes it: where the jobs are, the verb, the command as typed
    with no secret, the host, the machines by identity, what started it, whether it runs at a terminal, and its
    resolved inputs; and what its locks are taken from, as runs/locks.py's take takes them: the verb's values as the
    dispatch hands them (only what was given), from which lock_sets.locks_for works out the kind with the verb's row
    of the table, the machines it names by their host and name, in the words' order, the default VM's among them, and
    the policy (locks.policy_of: the setting run_lock's). The values are always given: a draft without them could
    take the locks of other words. names is the name a person chose for each of machines, by identity, which the
    record keeps; logs where the run's text log goes and how long logs are kept (runs/textlog.py's logs), None for a
    run that writes none and prunes nothing."""

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
    names: Mapping[str, str] | None = field(default=None, kw_only=True)
    logs: Logs | None = field(default=None, kw_only=True)


def run(draft: Draft, body: Body) -> int:
    """The verb's body run as the draft's job, in this process, to its stated end; its exit status. Where the run's
    directory cannot be made, its locks are refused (locks.Held), or its ladder cannot be built (attached.run), the job
    is dropped, as a run that never began, and the error raised, with the line that says what the drop left where it
    left anything. A verb whose run is no job, or whose values come to no kind of lock, is refused with ValueError
    before anything is made. A run that writes a text log prunes first, once its locks are taken (runs/prune.py), and
    then opens its log (runs/textlog.py), each saying on standard error what it could not do, never stopping the run;
    the log is closed once the run has ended."""
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
            names=draft.names,
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
        if draft.logs is not None:
            _logged(made, draft)
        try:
            return attached.run(made, body, mask=found)
        finally:
            if draft.logs is not None:
                from claude_vm.runs import textlog

                textlog.finish(made)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, found)


def _logged(made: Job, draft: Draft) -> None:
    """The prune, then the run's text log, for a run that writes one: LOG_DIR's, or a detached run's own in its job's
    folder where LOG_DIR is empty. LOG_DIR is held to be a folder of its own (textlog's _kept: never a link) before
    anything in it is pruned: one that is refused has nothing in it pruned and gets no log, and the jobs are pruned
    still. What could not be done is said on standard error, and the run goes on."""
    import sys

    from claude_vm.runs import prune, textlog

    logs = draft.logs
    if logs is None or (logs.folder is None and draft.attached):
        return
    folder = logs.folder
    if folder is not None:
        try:
            textlog._kept(folder)
        except Exception as error:  # a LOG_DIR refused is pruned through never, and the run goes on with no log
            print(f"warning: no text log for this run: {error}", file=sys.stderr)
            for said in prune.prune(None, draft.jobs_dir, logs.retention).said:
                print(said, file=sys.stderr)
            return
    try:
        for said in prune.prune(folder, draft.jobs_dir, logs.retention).said:
            print(said, file=sys.stderr)
        textlog.start(made, logs, draft.verb, draft.attached)
    except Exception as error:  # neither stops the run: its record is written whatever its log
        print(f"warning: no text log for this run: {error}", file=sys.stderr)
