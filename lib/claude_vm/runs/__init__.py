"""A run of the new engine: its one record and its text log, the children it starts, the short-lived directory of its
generated files, the kernel's file locks it holds, its end on a signal, and a run detached as the same program in a
session of its own (docs/architecture/engine.md §16.8).

`run(draft, body)` is the one entry a verb calls: it makes the job the draft describes (job.py), which takes the job's
own lock, makes the run's directory (rundir.py), and runs the body in this process to its stated end (attached.py).
It takes no other lock: the locks the run is given go in between the job and the body (card C6). Its modules are
imported only when it is called, so that what reads a job (logs, cancel) loads none of them.

It imports schema/ and fleet/, and of today's lib/claude_vm/ only what tests/unit/test_aw_layout.py's REUSED names;
nothing of engine/, cli/ or control/, no pydantic, and never today's claude_vm.ansible. Imported by cli/ and engine/;
nothing of today's product imports it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_vm.runs.attached import Body


@dataclass(frozen=True)
class Draft:
    """The job a verb's run makes, as runs/job.py's create takes it: where the jobs are, the verb, the command as typed
    with no secret, the host, the machines by identity, what started it, whether it runs at a terminal, and its
    resolved inputs."""

    jobs_dir: Path
    verb: str
    command: str
    host: str | None
    machines: Sequence[str]
    starter: Mapping[str, str]
    attached: bool
    inputs: Mapping[str, object] | None = None


def run(draft: Draft, body: Body) -> int:
    """The verb's body run as the draft's job, in this process, to its stated end; its exit status. Where the run's
    directory cannot be made the job is dropped, as a run that never began, and the error raised."""
    from claude_vm.runs import attached, rundir
    from claude_vm.runs import job as jobs

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
    except BaseException:
        jobs.drop(made)
        raise
    return attached.run(made, body)
