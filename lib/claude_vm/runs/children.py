"""The one way a run starts a child: in a process group or a session of its own, with the job's marker, with the
signals at their defaults, handed no descriptor but those its caller names and never a lock (docs/architecture/
engine.md §16.8).

`MARKER`, `AGENT_WAREHOUSE_JOB`, carries the job's ID into every process a run starts, as today's MCP jobs set it.
`start` gives each child the marker itself, whatever environment it is handed, so no caller can forget it; `unmarked`
takes it out, for the two kinds of child that must not carry it, which a run never ends: a shared SSH master, which
other runs may ride after this one ends, and a child meant to outlive its run (`outlive`). A run carries no marker of
its own: the program takes an inherited one out of its environment at its start (cli/main.py), so that a child started
outside `start` (route.py's `ssh`, proc.py's `ps`) carries none either.

`start` is a coroutine: the child is in a process group of its own (`group`), so that the terminal's Ctrl-C reaches the
run alone, or in a session of its own (`session`), its standard input on `/dev/null`. In the child, before it runs,
every signal a handler can be given is put back to its default and none is blocked: a process starts with what its
parent ignored still ignored, a run started under `nohup` or with `&` ignores SIGINT, and Python ignores SIGPIPE. Every
descriptor is closed in the child but its standard streams and those `pass_fds` names, which is how a pipe is handed
over on purpose. A descriptor that holds one of the run's locks, its number or any duplicate of it, is refused by the
lock's name before anything starts, a standard stream the child inherits among them: the kernel lets a lock go only
when every descriptor of it is closed, so a child that held one would keep it past its run's end.

The children a run started are kept, by kind, in memory for the run's end (runs/attached.py): `start` enters each, the
SSH runner's `ssh` children among them, and `live` gives those still running. `watched` gives them with route.py's
`ssh` that runs in a process group of its own (the new engine's calls), this process's children too though `start`
starts none of them, and `asking` those of them the terminal stopped for reading it or changing it. A shared master is
entered nowhere, since no run ever ends one; while its open runs it is kept apart
(`opening`), so that a run's end, which remembers the tree under it at a Ctrl-C, remembers nothing of the open's: the
master it leaves behind keeps its `ProxyCommand` in the open's group, and ending that ends the master. A child meant to
outlive its run - the editor, a page's opener - is `outlive`'s: unmarked, in a session of its own, entered in no
registry, and left out of the tree a run's end remembers and ends (`outliving`), so that no end of the run reaches it.
Nothing here reads the process table, and nothing is written to a file.

Standard library only; asyncio and subprocess are imported inside the functions that start a child.
"""

from __future__ import annotations

import os
import signal
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from claude_vm.runs import job as jobs
from claude_vm.runs import locks
from claude_vm.schema import patterns

if TYPE_CHECKING:
    import subprocess
    from asyncio.subprocess import Process

# The variable that carries the job's ID into every process a run starts: today's MCP jobs' name. Whatever reads it
# matches the job's ID, never the name alone, since today's jobs and the new engine's live in different folders.
MARKER = "AGENT_WAREHOUSE_JOB"

# How a child is kept from the run's terminal: a process group of its own, or a session of its own.
GROUP = "group"
SESSION = "session"
OWN = (GROUP, SESSION)

# The kinds of child `start` starts: an `ssh` of the SSH runner's, bounded or held (a forward among them); layer 1's
# `ansible-playbook`; a program of the computer's own that ends with the run (`rsync`); and a shared SSH master, which
# carries no marker and is entered in no registry. A run's end ends the first three, which carry its marker.
SSH = "ssh"
ANSIBLE = "ansible"
PROGRAM = "program"
MASTER = "master"
KINDS = (SSH, ANSIBLE, PROGRAM, MASTER)
# The kind of a child meant to outlive its run - the editor, a page's opener - which `outlive` starts: like a master,
# unmarked, in a session of its own and in no registry, and no end of the run reaches it.
OUTLIVING = "outliving"

# Every signal a handler can be given, SIGKILL and SIGSTOP being none's to catch or ignore: in each child, at its
# default, and none of them blocked.
CATCHABLE = tuple(sorted(set(signal.valid_signals()) - {signal.SIGKILL, signal.SIGSTOP}))
# What the terminal stops a process with that reads it (SIGTTIN) or changes or writes to it (SIGTTOU) from a process
# group that is not the terminal's own: a child in a group of its own that asks a question there.
AT_THE_TERMINAL = (signal.SIGTTIN, signal.SIGTTOU)

# Where each lock the run holds is found: a function of the module that takes it, giving the descriptor that holds
# each lock with its name. Every module of runs/ that takes a kernel lock has its function here, which a test holds.
HOLDERS: tuple[Callable[[], dict[int, str]], ...] = (jobs.locks_held, locks.locks_held)


class LockRefused(ValueError):
    """A child was to be handed a descriptor of one of the run's locks: nothing was started."""


@dataclass(frozen=True)
class Child:
    """One child of a run: its kind, its process, and its program's name, as a sentence says it. The process is
    asyncio's for one `start` started, and subprocess's for route.py's `ssh` (`watched`)."""

    kind: str
    process: Process | subprocess.Popen
    program: str

    @property
    def pid(self) -> int:
        return self.process.pid


# The children each job of this process started, by the job's ID, in the order they were started.
_CHILDREN: dict[str, list[Child]] = {}
# The shared masters' opens this process started, entered in no registry: opening() gives those still running.
_OPENING: list[Process] = []
# The children meant to outlive their run that this process started, entered in no registry: outliving() gives those
# still running. Kept, so that none is collected while it runs.
_OUTLIVING: list[subprocess.Popen] = []


def _job_id(job: jobs.Job) -> str:
    """The job's ID, which is sixteen hex digits, where this process made the job: a child carries only the marker of a
    run of this process's own."""
    if not patterns.full("job_id", job.id):
        raise ValueError(f"{job.folder} is no job: a job's ID is sixteen hex digits")
    if job._held_here() is None:
        raise RuntimeError(f"job {job.id} is not this process's: only the run that made a job starts its children")
    return job.id


def unmarked(env: Mapping[str, str]) -> dict[str, str]:
    """A copy of env without the job's marker: for a shared SSH master and a child meant to outlive its run."""
    return {name: value for name, value in env.items() if name != MARKER}


def _default_signals() -> None:
    """In the child, before it runs: every signal of CATCHABLE at its default, and none blocked, whatever the run
    ignored or held. One the system will not take a handler for is passed over: nothing here may stop the start.

    It is a preexec_fn, so it runs in a child forked beside the run's threads, before the exec, where only the forking
    thread lives on: a lock another thread held at the fork stays held there for good. So it calls signal.signal and
    signal.pthread_sigmask alone, which take no lock (two system calls each), and nothing else: no import, no log, no
    print, no file. Python makes the forking thread the child's main thread, so signal.signal works there whatever
    thread forked. tests/unit/test_aw_children.py holds it to those two calls."""
    for number in CATCHABLE:
        try:
            signal.signal(number, signal.SIG_DFL)
        except (OSError, ValueError):
            continue
    signal.pthread_sigmask(signal.SIG_SETMASK, ())


def _descriptor(stream: object) -> int | None:
    """The descriptor a standard stream given to a child is, where it is one: an int that names one, or a file's."""
    if isinstance(stream, bool):
        return None
    if isinstance(stream, int):
        return stream if stream >= 0 else None
    fileno = getattr(stream, "fileno", None)
    return fileno() if callable(fileno) else None


def _inherited(stdout: object, stderr: object) -> list[int]:
    """This process's own standard output and error, where a child is given either as None and so inherits it: what
    the child is handed by them, which may be a duplicate of a lock's as much as a descriptor named. One that is not
    open is handed nothing."""
    handed = []
    for stream, number in ((stdout, 1), (stderr, 2)):
        if stream is None:
            try:
                os.fstat(number)
            except OSError:
                continue
            handed.append(number)
    return handed


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


def _program(argv: Sequence[str]) -> str:
    """A child's program, as a sentence names it: the last part of its path."""
    return os.path.basename(argv[0]) or argv[0]


def _argv(argv: object) -> list[str]:
    """argv held to a child's command: a list of str, its program first."""
    if isinstance(argv, str) or not isinstance(argv, Sequence) or not argv or not all(isinstance(w, str) for w in argv):
        raise TypeError("a child's command is a list of str, its program first")
    return list(argv)


def live(job: jobs.Job, kind: str | None = None) -> list[Child]:
    """The job's children still running, of that kind or of every kind, in the order they were started. One that has
    ended is let go of."""
    running = [child for child in _CHILDREN.get(job.id, []) if child.process.returncode is None]
    if running:
        _CHILDREN[job.id] = running
    else:
        _CHILDREN.pop(job.id, None)
    return [child for child in running if kind is None or child.kind == kind]


def opening() -> list[int]:
    """The pids of the shared masters' opens still running, each the root of a tree a run's end remembers nothing of
    (attached.py's remember). One that has ended is let go of: the master it left is no child of the run."""
    running = [process for process in _OPENING if process.returncode is None]
    _OPENING[:] = running
    return [process.pid for process in running]


def outliving() -> list[int]:
    """The pids of the children meant to outlive their run that still run, each the root of a tree no end of the run
    remembers or ends (attached.py's remember and end_tree). One that has ended is reaped and let go of: what it
    started has left the run's tree."""
    running = [process for process in _OUTLIVING if process.poll() is None]
    _OUTLIVING[:] = running
    return [process.pid for process in running]


def watched(job: jobs.Job) -> list[Child]:
    """The job's children still running (`live`), then route.py's `ssh` still running in a process group of its own -
    the hop's and its `ssh -G`, which the new engine starts there (route.leading) - each as a child of kind `ssh`: the
    children of this process that a question at the terminal can stop, which `asking` looks at."""
    from claude_vm import route

    hop = [Child(SSH, process, _program(process.args)) for process in route.leading()]
    return [*live(job), *hop]


def asking(job: jobs.Job) -> list[tuple[Child, int]]:
    """The children `watched` gives that the terminal has stopped, each with the signal that stopped it (one of
    AT_THE_TERMINAL): a child in a group of its own, or its group's, that asked a question at the terminal - a
    passphrase asked by the `ssh` of a person's own ProxyJump, which BatchMode does not reach, a question of `git`'s or
    `sudo`'s - and waits, stopped, for an answer no run gives: SIGTTIN where it read the terminal, SIGTTOU where it
    changed it first, as OpenSSH's readpassphrase turns the echo off before it reads. Read without reaping it or
    changing anything (waitid with WNOWAIT): asyncio or route.py still reaps it, and a child stopped for any other
    reason is not one. Where Python has no waitid (macOS before Python 3.13), none is found."""
    waitid = getattr(os, "waitid", None)
    if waitid is None:
        return []
    found = []
    for child in watched(job):
        try:
            seen = waitid(os.P_PID, child.pid, os.WSTOPPED | os.WNOHANG | os.WNOWAIT)
        except OSError:
            continue
        if seen is not None and seen.si_code == os.CLD_STOPPED and seen.si_status in AT_THE_TERMINAL:
            found.append((child, seen.si_status))
    return found


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
    if kind == OUTLIVING:
        raise ValueError(
            "a child meant to outlive its run is started by outlive, never by start: no end of a run ends it"
        )
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is no kind of child: one of {', '.join(KINDS)}")
    if own not in OWN:
        raise ValueError(f"{own!r} is no way to keep a child from the terminal: one of {', '.join(OWN)}")
    words = _argv(argv)
    if stdin is None or _descriptor(stdin) == 0:
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
    _refuse_locks([*handed, *_inherited(stdout, stderr)])
    environment = unmarked(env) if kind == MASTER else {**env, MARKER: job_id}
    kept = {"process_group": 0} if own == GROUP else {"start_new_session": True}
    process = await asyncio.create_subprocess_exec(
        *words,
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
        # Entered under its kind for the run's end; a shared master never, since no run ends one.
        _CHILDREN.setdefault(job_id, []).append(Child(kind, process, _program(words)))
    else:
        _OPENING.append(process)
    return process


def outlive(job: jobs.Job, argv: Sequence[str], env: Mapping[str, str]) -> subprocess.Popen:
    """argv started as a child of the job's run that is meant to outlive it - the editor, a page's opener - and its
    process. It carries no marker (unmarked), as a shared master does, runs in a session of its own, so the terminal
    neither stops it nor hangs it up, with the signals at their defaults, its standard streams on /dev/null and no other
    descriptor open; and it is entered in no registry, so the run's end neither waits for it nor ends it, and is left
    out of the tree a run's end remembers and ends (`outliving`). Started by subprocess itself, never through asyncio,
    whose transport kills a child it still holds once it is closed or collected. Nothing ends it: a caller that wants
    to know how it ended polls it. Everything is checked before anything starts."""
    import subprocess

    _job_id(job)
    words = _argv(argv)
    process = subprocess.Popen(
        words,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=unmarked(env),
        close_fds=True,
        start_new_session=True,
        preexec_fn=_default_signals,
    )
    _OUTLIVING.append(process)
    return process
