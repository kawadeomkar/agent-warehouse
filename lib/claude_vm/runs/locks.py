"""The kernel's file locks a run holds: the fleet's, each machine's by its host and its name, and the short sections it
waits for (docs/architecture/engine.md §16.8).

Every lock is `fcntl.flock` on a file of the state folder's `locks/` (fleet/paths.py's `locks_dir`), opened here alone
at mode 0600, never through a link, and kept open for as long as it is held. As Python opens every descriptor, no child
inherits one, and the one way a run starts a child refuses one (runs/children.py's HOLDERS reads `locks_held`). The
kernel lets a lock go when the process that holds it ends, however it ends: nothing is ever released by hand, nothing
is left to clear, and no pid or process's start is read to decide a lock. The files:

- `fleet`: shared by a run that names machines, exclusive for one that names none, and for `exclusive`.
- `machine-<host>@<name>`, one for each machine, exclusive: `<host>` is the Proxmox host's name, or for an adopted
  machine the word `machines`, which hosts/model.py's RESERVED keeps from every host, and `@` is in neither name's
  pattern. A host and a name exist before the VM does, so a run that makes alpha and one that configures it take one
  lock, from before the VM exists to its deletion.
- A short section, `<kind>-<host>`, named by its owner (C7 lists them): the next VM ID with its clone, exclusive; the
  host's template, exclusive around a build, a rebuild or a collection and shared around a clone.

`take(job, kind, machines, policy)` takes a run's locks once, after its job is made and before its body starts
(runs.run): for the kind `machines`, the fleet's lock shared where machines are named and exclusive where none are,
then each machine's exclusive; for `fleet`, the fleet's exclusive whatever is named; for `none`, nothing. The policy is
the setting run_lock's, `machine` or `fleet` (under which every run that takes a lock takes the fleet's exclusively,
today's one lock), or `none` for --no-lock, which takes neither the fleet's nor a machine's, at the person's own risk.
Nothing waits: each lock is tried without waiting, in the order of the files' names, so no two runs ever wait on each
other, and all or none: on the first refused, every lock taken is let go and `Held` is raised with its sentence. A run
writes its job's ID into each lock file it holds exclusively, one line at offset 0 by one pwrite, never truncated (on
NFS a truncate costs about 3 ms a lock): a reader takes the first line, and believes it only for a job that still runs
by its own lock (job.py's `state`) and names the lock in its record. So a refusal names the holder's job, its command,
its start and the line that cancels it, printed by the one printer of a command; and where it cannot tell (the
holder's record unread, or its job starting or ending), it says so and never fails on what it reads. A fleet's lock held
shared names no one holder: runs on machines of this fleet are under way. The names of the locks a run holds go into
its record.

`exclusive(state, command)` holds the fleet's lock exclusively for one write of a verb that is no run (`host <name>`,
`vault --rotate`): its command, as typed, is the holder's record, and a run refused while it is held names that command
and no line to cancel it, since there is no job; the lock is let go at the block's end. Its record may name the command
before it for the microseconds before it is written, which touches only a refusal's sentence.
`changing_run_lives(state)` says whether any run holds the fleet's lock, for `clean`.

`section(ctx, name)` takes a short section's lock and waits for it, with a `waiting` event that names it, and ends its
wait once the run is stopping. It waits by trying again every POLL seconds, since a thread blocked in flock cannot be
cancelled: so the sections keep no queue, and a stream of clones can keep a template's build waiting, which the
sections' shortness makes acceptable. --no-lock does not touch them. The writes of the managed SSH config, the
known-hosts file, the overrides and the vault are short sections too, under store.locked, which their owners take.

Standard library only, with job.py, fleet/paths.py, the patterns, text.py and the printer of a command
(cli/inverse.py); asyncio is imported inside `section`.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import stat
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from claude_vm.cli import inverse
from claude_vm.fleet import paths
from claude_vm.runs import job as jobs
from claude_vm.schema import patterns
from claude_vm.text import plain_text

if TYPE_CHECKING:
    from claude_vm.runs.attached import Context

# The kinds of lock a run takes, as C7 works one out from the table's column `locks` and the run's words: the fleet's
# shared and each machine's (with none named, the fleet's exclusive); the fleet's exclusive; none.
KINDS = ("machines", "fleet", "none")
MACHINES, FLEET_KIND, NONE = KINDS
# The policies a run takes its locks under: the setting run_lock's two values, machine (the default) and fleet (every
# run that takes a lock takes the fleet's exclusively, today's one lock); and none, --no-lock's, which outranks it.
POLICIES = ("machine", "fleet", "none")
# The fleet's lock file, and the start of a machine's.
FLEET = "fleet"
MACHINE = "machine-"
# The host of an adopted machine, which no Proxmox host may be named (hosts/model.py's RESERVED).
ADOPTED = "machines"
# A short section's lock file: a word, a dash and the host's name, as its owner names it.
SECTION = re.compile(rf"[a-z]+-(?:{patterns.PATTERNS['current_host']})")
# How each lock is held, as the record names it.
HOW = {fcntl.LOCK_SH: "shared", fcntl.LOCK_EX: "exclusive"}
# How often a section that waits tries again.
POLL = 0.02
# The most of a holder's record that is read: its one line.
RECORD_MOST = 4096
# The states of a job that still runs by its own lock.
RUNNING = ("running", "cancelling")

# The locks each job of this process took, by the job's folder's real path: each lock's file name, the descriptor that
# holds it and how. Kept open until this process ends: nothing lets a run's locks go but take's all or none.
_TAKEN: dict[str, list[tuple[str, int, int]]] = {}
# The descriptors of the fleet's lock `exclusive` holds and of the sections this process is in, each with the lock's
# name, for as long as each block lasts.
_IN_BLOCKS: dict[int, str] = {}


class Held(Exception):
    """A lock that another run, or a verb's one write, holds: refused at once, and nothing of this run's is held. The
    sentence says who holds it: its job, its command, its start and the line that cancels it, where that can be told."""

    def __init__(self, sentence: str, lock: str, job: str | None = None) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.lock = lock
        # The holder's job, where the sentence names one.
        self.job = job

    def leaving(self, line: str) -> Held:
        """The same refusal, saying too what this run's dropped job left (job.py's drop)."""
        return Held(f"{self.sentence}; this run's {plain_text(line)}", self.lock, self.job)


def policy_of(run_lock: str, no_lock: bool) -> str:
    """The policy a run takes its locks under: none for --no-lock, which outranks the setting; else run_lock's."""
    if run_lock not in POLICIES[:2]:
        raise ValueError(f"run_lock is one of {', '.join(POLICIES[:2])}, not {run_lock!r}")
    return "none" if no_lock else run_lock


def machine_lock(host: str, name: str) -> str:
    """A machine's lock file's name, from its host and its name in the fleet alone: never from what Proxmox or the
    machine says of it. The host is a host's name or ADOPTED, and the name one name of the command line's."""
    if not isinstance(host, str) or not (host == ADOPTED or patterns.full("current_host", host)):
        raise ValueError(f"{host!r} is no host's name, nor {ADOPTED!r} for an adopted machine")
    if not isinstance(name, str) or "," in name or not patterns.full("vm_name", name):
        raise ValueError(f"{name!r} is not one machine's name")
    return f"{MACHINE}{host}@{name}"


def described(name: str) -> str:
    """A lock file's name as a sentence says it."""
    if name == FLEET:
        return "the fleet"
    if name.startswith(MACHINE) and "@" in name:
        host, machine = name.removeprefix(MACHINE).split("@", 1)
        return f"the adopted machine {machine}" if host == ADOPTED else f"{machine} on {host}"
    return f"the section {name}"


def _one_line(value: str) -> str:
    """value made plain and kept to one line, as a sentence quotes it."""
    return " ".join(plain_text(value).split())


def _key(job: jobs.Job) -> str:
    """The key of a job this process made; refused for any other."""
    if job._held_here() is None:
        raise RuntimeError(f"job {job.id} is not this process's: only the run that made a job takes its locks")
    return jobs._key(job.folder)


def _state_of(job: jobs.Job) -> Path:
    """The state folder a job is in: its folder is jobs/<id> there, beside locks/."""
    if job.folder.parent.name != paths.JOBS:
        raise ValueError(f"job {job.id} is not in a state folder's {paths.JOBS}/: its locks are beside it there")
    return job.folder.parent.parent


def _folder(state: str | os.PathLike) -> Path:
    """The state folder's locks/, made at DIR_MODE where it is missing."""
    folder = Path(state) / paths.LOCKS
    jobs._make_folders(folder)
    return folder


def _open(path: Path) -> int:
    """A lock file opened for writing, as an exclusive lock needs on NFS: made at 0600 where it is missing, put back to
    0600 where it is not, and refused where it is a link. Non-inheritable, as Python opens every descriptor."""
    held = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, paths.FILE_MODE)
    try:
        if stat.S_IMODE(os.fstat(held).st_mode) != paths.FILE_MODE:
            os.fchmod(held, paths.FILE_MODE)
    except BaseException:
        os.close(held)
        raise
    return held


def _try(held: int, how: int) -> bool:
    """Whether the lock was taken, tried without waiting."""
    try:
        fcntl.flock(held, how | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _write(held: int, record: dict) -> None:
    """The holder's record: one line at offset 0, never truncated, so that a reader that takes the first line never
    reads what a longer record before it left after it."""
    line = (json.dumps(record, sort_keys=True) + "\n").encode()
    written = 0
    while written < len(line):
        written += os.pwrite(held, line[written:], written)


def _record(path: Path) -> dict:
    """The first line of a lock file, as its holder wrote it; {} where it cannot be read or is no record."""
    try:
        reading = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return {}
    try:
        first = os.pread(reading, RECORD_MOST, 0).split(b"\n", 1)[0]
    except OSError:
        return {}
    finally:
        os.close(reading)
    try:
        record = json.loads(first)
    except ValueError:
        return {}
    return record if isinstance(record, dict) else {}


def _held_exclusively(path: Path) -> bool:
    """Whether another holds path's lock exclusively: a shared lock tried without waiting is refused, and let go at
    once where it is taken."""
    try:
        probe = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        return not _try(probe, fcntl.LOCK_SH)
    finally:
        os.close(probe)


def _when(started: float) -> str:
    """When a run started, as today's run lock says it: in UTC, its spaces collapsed."""
    moment = time.gmtime(started)
    return f"{time.strftime('%a %b', moment)} {moment.tm_mday} {time.strftime('%H:%M:%S %Y', moment)} UTC"


def _running(state: Path, name: str, record: dict) -> tuple[str, str, float] | None:
    """The job a lock's record names, its command and its start, where that job still runs by its own lock and its
    record names the lock; None for anything else: no job, one that is starting or ending, or a record that vanished
    while it was read."""
    job_id = record.get("job")
    if not isinstance(job_id, str) or not patterns.full("job_id", job_id):
        return None
    found = jobs.find(state / paths.JOBS, job_id)
    if found is None or jobs.state(found) not in RUNNING:
        return None
    read = found.read()
    command, started, held = read.get("command"), read.get("started"), read.get("locks")
    if not isinstance(command, str) or not command.strip():
        return None
    if not isinstance(started, (int, float)) or isinstance(started, bool):
        return None
    if not isinstance(held, list) or not any(isinstance(each, str) and each.split(":")[0] == name for each in held):
        return None
    return job_id, command, started


def _refusal(state: Path, name: str, how: int, who: str) -> Held:
    """The sentence of a lock refused: who holds it, as far as can be told, read now and never failing on what it
    reads. who is what was refused: this run, or an exclusive's command."""
    what = described(name)
    path = state / paths.LOCKS / name
    if name == FLEET and how == fcntl.LOCK_EX and not _held_exclusively(path):
        listing = inverse.line("logs", {})
        return Held(
            f"error: runs on machines of this fleet are under way, and {who} needs the whole fleet: `{listing}` lists"
            " them",
            name,
        )
    record = _record(path)
    command = record.get("command")
    if name == FLEET and isinstance(command, str) and command.strip() and "job" not in record:
        return Held(f"error: `{_one_line(command)}` holds the fleet for one write: try again when it ends", name)
    running = _running(state, name, record)
    if running is None:
        return Held(f"error: {what} is held by a run that is starting or ending: try again", name)
    job_id, command, started = running
    cancel = inverse.line("cancel", {"job": job_id})
    return Held(
        f"error: {what} is held by job {job_id}, `{_one_line(command)}`, started {_when(started)}: `{cancel}` stops it",
        name,
        job_id,
    )


def _wanted(kind: str, names: Sequence[str], policy: str) -> list[tuple[str, int]]:
    """The locks a run takes, each with how, in the order of their files' names."""
    if policy == "none" or kind == NONE:
        return []
    if policy == "fleet" or kind == FLEET_KIND or not names:
        return [(FLEET, fcntl.LOCK_EX)]
    return sorted([(FLEET, fcntl.LOCK_SH), *((name, fcntl.LOCK_EX) for name in set(names))])


def _let_go(key: str) -> None:
    for _, held, _ in reversed(_TAKEN.pop(key, [])):
        os.close(held)


def take(job: jobs.Job, kind: str, machines: Sequence[tuple[str, str]], policy: str) -> list[str]:
    """A run's locks, taken once for the job this process made, without waiting and all or none: the names of those
    taken, each with how (`fleet:shared`), also written into the job's record. Held where one is refused, naming its
    holder, with nothing of this run's held. kind is one of KINDS, machines the (host, name) pairs the run names, and
    policy one of POLICIES."""
    key = _key(job)
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is no kind of lock: one of {', '.join(KINDS)}")
    if policy not in POLICIES:
        raise ValueError(f"{policy!r} is no policy of the locks: one of {', '.join(POLICIES)}")
    if isinstance(machines, (str, bytes)) or not all(
        isinstance(pair, (tuple, list)) and len(pair) == 2 for pair in machines
    ):
        raise TypeError("the machines a run locks are (host, name) pairs")
    names = [machine_lock(host, name) for host, name in machines]
    if key in _TAKEN:
        raise RuntimeError(f"job {job.id} has taken its locks already: a run takes them once")
    state = _state_of(job)
    wanted = _wanted(kind, names, policy)
    taken: list[tuple[str, int, int]] = []
    _TAKEN[key] = taken
    refused: tuple[str, int] | None = None
    try:
        folder = _folder(state) if wanted else None
        for name, how in wanted:
            assert folder is not None
            held = _open(folder / name)
            try:
                got = _try(held, how)
            except BaseException:
                os.close(held)
                raise
            if not got:
                os.close(held)
                refused = (name, how)
                break
            taken.append((name, held, how))
            if how == fcntl.LOCK_EX:
                _write(held, {"job": job.id})
        if refused is None:
            listed = [f"{name}:{HOW[how]}" for name, _, how in taken]
            if listed:
                job.update(locks=listed)
            return listed
    except BaseException:
        _let_go(key)
        raise
    # All or none: what was taken is let go before the holder is read, so that a run beside this one is not refused
    # for a lock this one no longer wants.
    _let_go(key)
    assert refused is not None
    raise _refusal(state, *refused, "this run")


@contextlib.contextmanager
def exclusive(state: str | os.PathLike, command: str) -> Iterator[None]:
    """The fleet's lock, exclusively, for one write of a verb that is no run (host <name>, vault --rotate): command, as
    typed, written as the holder's record (no job, no pid), and the lock let go at the block's end. Held where any run,
    or another such write, holds the fleet's lock."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("an exclusive write names its command, as typed")
    where = Path(state)
    held = _open(_folder(where) / FLEET)
    _IN_BLOCKS[held] = described(FLEET)
    try:
        if not _try(held, fcntl.LOCK_EX):
            raise _refusal(where, FLEET, fcntl.LOCK_EX, f"`{_one_line(command)}`")
        _write(held, {"command": _one_line(command)})
        yield
    finally:
        del _IN_BLOCKS[held]
        os.close(held)


def changing_run_lives(state: str | os.PathLike) -> bool:
    """Whether any run holds the fleet's lock, or an exclusive write does: an exclusive lock tried without waiting is
    refused, and let go at once where it is taken. A run under --no-lock holds none, and is not seen."""
    try:
        probe = os.open(Path(state) / paths.LOCKS / FLEET, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return False
    try:
        return not _try(probe, fcntl.LOCK_EX)
    finally:
        os.close(probe)


@contextlib.asynccontextmanager
async def section(ctx: Context, name: str, *, shared: bool = False, machine: str | None = None) -> AsyncIterator[None]:
    """One short section of the run ctx gives, by its lock file's name (`<kind>-<host>`): its lock taken, shared or
    exclusively, waiting for it where another run holds it, with a `waiting` event that names it (about machine, or the
    whole run), and the event cleared once it is taken. A wait ends once the run is stopping: asyncio.CancelledError.
    Taken whatever the policy: --no-lock still waits."""
    if not isinstance(name, str) or not SECTION.fullmatch(name):
        raise ValueError(f"{name!r} is no short section's name: a word, a dash and a host's name")
    _key(ctx.job)
    held = _open(_folder(_state_of(ctx.job)) / name)
    _IN_BLOCKS[held] = described(name)
    try:
        how = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        if not _try(held, how):
            import asyncio

            ctx.emit("waiting", machine, f"waiting for another run to leave {described(name)}", section=name)
            while not _try(held, how):
                if ctx.cancelled.is_set():
                    raise asyncio.CancelledError(f"the run is stopping: it no longer waits for {described(name)}")
                await asyncio.sleep(POLL)
            ctx.emit("waiting", machine, "")
        yield
    finally:
        del _IN_BLOCKS[held]
        os.close(held)


def locks_held() -> dict[int, str]:
    """The descriptors by which this process holds a run's lock, an exclusive write's or a section's, each with the
    lock's name: what a child of the run is never handed (runs/children.py)."""
    held = {each: f"the lock of {described(name)}" for taken in _TAKEN.values() for name, each, _ in taken}
    held.update({each: f"the lock of {name}" for each, name in _IN_BLOCKS.items()})
    return held
