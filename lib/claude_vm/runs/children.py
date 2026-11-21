"""The one way a run starts a child: in a process group or a session of its own, with the job's marker, with the
signals at their defaults, handed no descriptor but those its caller names and never a lock (docs/architecture/
engine.md §16.8).

`MARKER`, `AGENT_WAREHOUSE_JOB`, carries the job's ID into every process a run starts, as today's MCP jobs set it.
`env(base, job)` is a copy of `base` with it, for a caller that needs the environment a child is given; `unmarked`
takes it out, for the one kind of child that must not carry it: a shared SSH master, which other runs may ride after
this one ends. `start` gives each child the marker itself, or for a master takes it away, whatever environment it is
handed, so no caller can forget it.

`start` is a coroutine: the child is in a process group of its own (`group`), so that the terminal's Ctrl-C reaches the
run alone, or in a session of its own (`session`), its standard input on `/dev/null`. In the child, before it runs, the
signals a Ctrl-C, a closing terminal and a kill send are put back to their defaults and unblocked: a process starts
with what its parent ignored still ignored, and a run started under `nohup` or with `&` ignores SIGINT. Every
descriptor is closed in the child but its standard streams and those `pass_fds` names, which is how a pipe is handed
over on purpose. A descriptor that holds one of the run's locks, its number or any duplicate of it, is refused by the
lock's name before anything starts: the kernel lets a lock go only when every descriptor of it is closed, so a child
that held one would keep it past its run's end.

The children a run started are kept, by kind, in memory for the run's end: `start` enters each with `register`, the SSH
runner's `ssh` children among them, and `live` gives those still running. A shared master is
entered nowhere, since no run ever ends one. Nothing here reads the process table, and nothing is written to a file.

Standard library only; asyncio and subprocess are imported inside the functions that start a child.
"""

from __future__ import annotations

import os
import signal
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from claude_vm.runs import job as jobs
from claude_vm.schema import patterns

if TYPE_CHECKING:
    from asyncio.subprocess import Process

# The variable that carries the job's ID into every process a run starts: today's MCP jobs' name. Whatever reads it
# matches the job's ID, never the name alone, since today's jobs and the new engine's live in different folders.
MARKER = "AGENT_WAREHOUSE_JOB"

# How a child is kept from the run's terminal: a process group of its own, or a session of its own.
GROUP = "group"
SESSION = "session"
OWN = (GROUP, SESSION)

# The kinds of child a run starts: an `ssh` of the SSH runner's, bounded or held (a forward among them); layer 1's
# `ansible-playbook`; a program of the computer's own (`rsync`, an opener, the editor); and a shared SSH master, the one
# kind that carries no marker and is entered in no registry.
SSH = "ssh"
ANSIBLE = "ansible"
PROGRAM = "program"
MASTER = "master"
KINDS = (SSH, ANSIBLE, PROGRAM, MASTER)

# The signals a person's Ctrl-C, a closing terminal and a kill send: in each child, at their defaults and unblocked.
SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)

# Where each lock the run holds is found: a function of the module that takes it, giving the descriptor that holds
# each lock with its name. Every module of runs/ that takes a kernel lock has its function here, which a test holds.
HOLDERS: tuple[Callable[[], dict[int, str]], ...] = (jobs.locks_held,)


class LockRefused(ValueError):
    """A child was to be handed a descriptor of one of the run's locks: nothing was started."""


@dataclass(frozen=True)
class Child:
    """One child of a run: its kind, and its process."""

    kind: str
    process: Process

    @property
    def pid(self) -> int:
        return self.process.pid


# The children each job of this process started, by the job's ID, in the order they were started.
_CHILDREN: dict[str, list[Child]] = {}


def _job_id(job: jobs.Job) -> str:
    """The job's ID, which is sixteen hex digits, where this process made the job: a child carries only the marker of a
    run of this process's own."""
    if not patterns.full("job_id", job.id):
        raise ValueError(f"{job.folder} is no job: a job's ID is sixteen hex digits")
    if job._held_here() is None:
        raise RuntimeError(f"job {job.id} is not this process's: only the run that made a job starts its children")
    return job.id


def env(base: Mapping[str, str], job: jobs.Job) -> dict[str, str]:
    """A copy of base with the job's marker."""
    return {**base, MARKER: _job_id(job)}


def unmarked(env: Mapping[str, str]) -> dict[str, str]:
    """A copy of env without the job's marker: for a shared SSH master alone."""
    return {name: value for name, value in env.items() if name != MARKER}


def _default_signals() -> None:
    """In the child, before it runs: each of SIGNALS at its default and unblocked, whatever the run ignored or held."""
    for number in SIGNALS:
        signal.signal(number, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, SIGNALS)


def _descriptor(stream: object) -> int | None:
    """The descriptor a standard stream given to a child is, where it is one: an int that names one, or a file's."""
    if isinstance(stream, bool):
        return None
    if isinstance(stream, int):
        return stream if stream >= 0 else None
    fileno = getattr(stream, "fileno", None)
    return fileno() if callable(fileno) else None


def _refuse_locks(handed: Sequence[int]) -> None:
    """LockRefused, naming the lock, where any descriptor handed is one of a lock the run holds or a duplicate of one:
    the same file, by its device and inode, whatever its number."""
    held: dict[tuple[int, int], str] = {}
    for holder in HOLDERS:
        for descriptor, name in holder().items():
            found = os.fstat(descriptor)
            held[(found.st_dev, found.st_ino)] = name
    for descriptor in handed:
        found = os.fstat(descriptor)
        name = held.get((found.st_dev, found.st_ino))
        if name is not None:
            raise LockRefused(f"a child is never handed a lock: descriptor {descriptor} holds {name}")


def register(job: jobs.Job, kind: str, process: Process) -> Child:
    """process, a child of the job's run, entered under its kind for the run's end: start enters each child it starts.
    A shared master is never entered: no run ends one."""
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is no kind of child: one of {', '.join(KINDS)}")
    if kind == MASTER:
        raise ValueError("a shared SSH master is no run's child to end: it is entered in no registry")
    child = Child(kind, process)
    _CHILDREN.setdefault(_job_id(job), []).append(child)
    return child


def live(job: jobs.Job, kind: str | None = None) -> list[Child]:
    """The job's children still running, of that kind or of every kind, in the order they were started. One that has
    ended is let go of."""
    running = [child for child in _CHILDREN.get(job.id, []) if child.process.returncode is None]
    if running:
        _CHILDREN[job.id] = running
    else:
        _CHILDREN.pop(job.id, None)
    return [child for child in running if kind is None or child.kind == kind]


async def start(
    job: jobs.Job,
    argv: Sequence[str],
    own: str,
    env: Mapping[str, str],
    stdin: object,
    stdout: object,
    stderr: object,
    kind: str,
    pass_fds: Sequence[int] = (),
) -> Process:
    """argv started as a child of the job's run, in a group (`group`) or a session (`session`) of its own, with env and
    the job's marker (a master: without it), the signals at their defaults, and no descriptor open but its standard
    streams and pass_fds; entered under its kind, but a master. The streams are asyncio's: DEVNULL, PIPE, a descriptor
    or a file; standard input is always named, and in a session of its own it is DEVNULL. Everything is checked before
    anything starts, and a descriptor of one of the run's locks is refused by the lock's name."""
    import asyncio
    import subprocess

    job_id = _job_id(job)
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is no kind of child: one of {', '.join(KINDS)}")
    if own not in OWN:
        raise ValueError(f"{own!r} is no way to keep a child from the terminal: one of {', '.join(OWN)}")
    if isinstance(argv, str) or not argv or not all(isinstance(word, str) for word in argv):
        raise TypeError("a child's command is a list of str, its program first")
    if stdin is None:
        raise ValueError(
            "a child never reads the run's own standard input: one in a group of its own would be stopped reading the"
            " terminal, and one in a session of its own would take what the person types"
        )
    if own == SESSION and stdin != subprocess.DEVNULL:
        raise ValueError("a child in a session of its own has /dev/null for its standard input")
    if isinstance(pass_fds, (str, bytes)) or not all(
        isinstance(each, int) and not isinstance(each, bool) and each >= 0 for each in pass_fds
    ):
        raise TypeError("pass_fds names descriptors, by their numbers")
    handed = [*pass_fds, *(found for found in map(_descriptor, (stdin, stdout, stderr)) if found is not None)]
    _refuse_locks(handed)
    environment = unmarked(env) if kind == MASTER else {**env, MARKER: job_id}
    kept = {"process_group": 0} if own == GROUP else {"start_new_session": True}
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        env=environment,
        close_fds=True,
        pass_fds=tuple(pass_fds),
        preexec_fn=_default_signals,
        **kept,
    )
    if kind != MASTER:
        register(job, kind, process)
    return process
