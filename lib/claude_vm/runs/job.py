"""A job: one run's folder, its one record `job.json`, and a kernel lock of its own that says whether the run lives
(docs/architecture/engine.md §16.8).

Every run is a job, at a terminal or detached. `create` makes `jobs/<id>/` in the state folder, the ID sixteen hex
digits as today's MCP jobs have, opens its `lock` and takes `flock(LOCK_EX)` on it before anything else is written,
then writes `job.json`. The descriptor stays open, non-inheritable (PEP 446), for the life of the process that made the
job: no child holds the lock, and the kernel lets it go when that process ends, however it ends. So a job's state is
derived, never stored: `running` while that lock is held, `cancelling` while it is held and a stop is recorded, the
outcome the record holds (`succeeded`, `failed` or `cancelled`), and `lost` where the record has none and the lock is
free. Nothing here keeps a pid to decide it, reads a process's start or reads the process table: the pid and the
start in the record are information for a person and for a machine's `job` line.

`emit` hands one event of the run to the listeners the run registered (the live view, the text log), in order and in
the run's own process; no event is written to a file of its own. What an agent and `logs` need of a run while it runs
is kept in the record: what it waits for, NEXT STEPS, each machine's outcome, and at the end the run's. Every string
the record and an event hold is made plain (`text.plain_text`), and a `Secret` is refused before anything is written.

Standard library only; `store` replaces the record whole under a lock on its folder, so two writers (the run, and a
`cancel` recording a stop) lose neither change. Only `create` writes a record from nothing, and only it writes the
job's ID, its pid and its start. A change reads the record strictly: where it is gone (the job dropped) or is no
record, the change is refused with `Gone`, and any other error of the read is raised as itself, never said as a job
that is gone. `drop` unlinks the record under the same lock, so a writer either lands its change before the drop or is
refused after it, and says what it could not remove. A reader is never refused: from the record's unlink on, `state`
says there is no such job (None), as `find` and `listed` pass over it.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from claude_vm import store
from claude_vm.fleet import paths
from claude_vm.schema import patterns
from claude_vm.secret import Secret
from claude_vm.text import plain_text

# A job's ID, as today's MCP jobs' (lib/claude_vm/mcp/jobs.py): sixteen lowercase hex digits, the pattern `job_id`.
ID_HEX = 16
# The two files of a job's folder: the record, and the lock that says whether the run lives.
RECORD = "job.json"
LOCK = "lock"
# The kinds of event a run emits: its declared stages and their steps, a report, what it waits for, a NEXT STEPS line,
# and an end (a machine's, or with no machine the run's). A child the run started is its registry's (runs/children.py).
KINDS = ("stage", "step", "report", "waiting", "next", "end")
# The outcomes a run or a machine ends with, written; and every state a reader tells, derived.
OUTCOMES = ("succeeded", "failed", "cancelled")
STATES = ("running", "cancelling", *OUTCOMES, "lost")
# What started a run: a terminal, `--detach`, or an MCP server, named by that server's own ID.
STARTERS = ("terminal", "detach", "mcp")
# The record's fields, in the order it is written. Those after `log` are written while the run runs and at its end.
FIELDS = (
    "id",  # the job's ID, the folder's name
    "verb",  # the verb run
    "command",  # the command as typed, with no secret
    "inputs",  # the run's resolved inputs, a typed --set value among them
    "starter",  # what started it: {"kind": terminal | detach | mcp, "server": the MCP server's own ID}
    "host",  # the host the run is on, or None
    "machines",  # the machines it names, by identity
    "locks",  # the names of the locks it holds
    "attached",  # whether it runs at a terminal
    "pid",  # the run's process: information, never read to decide a state
    "started",  # when it started, seconds since the epoch: information, and the order `listed` gives
    "deadline",  # when a detached run is stopped, or None
    "stop",  # a stop asked for, {"by": who, "at": when}, or None
    "directory",  # the run's short-lived directory
    "log",  # the run's plain-text log
    "waiting",  # what it waits for, {"machine", "text", "since", ...}, or None
    "next",  # NEXT STEPS: [{"machine", "matter", "line"}], a line removed once nothing is left
    "machine_outcomes",  # each machine's end: {machine: {"outcome", "stage", "why"}}
    "outcome",  # the run's outcome, at its end
    "exit",  # its exit status
    "ended",  # when it ended
    "why",  # why it ended as it did
)

# What only `create` writes: the job's ID, and the run's process and start, which a person reads and `cancel` signals.
CREATED = ("id", "pid", "started")

# The jobs this process made, by their folder's real path, each with the descriptor that holds its lock: only the
# process that made a job emits its events or drops it, and `state` reads its own job from what it holds, a shortcut
# that saves a probe. A probe would read the same: closing a second descriptor of a lock file does not let the first's
# lock go, since a flock belongs to its open file description, as experiment 40 measured on Linux, on NFSv3 and
# NFSv4.2 as on a local disk (macOS's half is not measured yet).
_HELD: dict[str, int] = {}


class Gone(LookupError):
    """A change asked of a job whose record is gone (the job dropped) or is no record (it does not parse as one):
    nothing was written. Any other error reading the record is raised as itself."""


@dataclass(frozen=True)
class Event:
    """One event of a run, made plain: when, its kind, the machine it is about (None for the whole run), its text, and
    what its kind carries."""

    time: float
    kind: str
    machine: str | None
    text: str
    data: Mapping[str, object]


Listener = Callable[[Event], None]


def _plain(value: object) -> object:
    """value as the record and an event hold it: every string made plain, a path a string, JSON's types alone. A
    Secret is refused wherever it is, so that it reaches neither the record nor a listener."""
    if isinstance(value, Secret):
        raise TypeError("a Secret is never written into a job's record or its events")
    if isinstance(value, str):
        return plain_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, os.PathLike):
        return plain_text(os.fspath(value))
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("a job's record is keyed by strings")
        return {plain_text(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    raise TypeError(f"a job's record holds JSON's values, not a {type(value).__name__}")


def _key(folder: Path) -> str:
    return os.path.realpath(folder)


def _is_id(name: object) -> bool:
    return isinstance(name, str) and patterns.full("job_id", name)


class Job:
    """A job's folder: one this process made (`create`), or one it read (`find`, `listed`)."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self._listeners: list[Listener] = []
        # The stage each machine last reached, from its stage events: written with its outcome.
        self._stages: dict[str, str] = {}

    def __repr__(self) -> str:
        return f"Job({str(self.folder)!r})"

    @property
    def id(self) -> str:
        return self.folder.name

    @property
    def record(self) -> Path:
        return self.folder / RECORD

    @property
    def lock(self) -> Path:
        return self.folder / LOCK

    def _held_here(self) -> int | None:
        """The descriptor holding this job's lock, where this process made the job; else None."""
        return _HELD.get(_key(self.folder))

    def _load(self) -> dict:
        """The record as it is on disk, read strictly: FileNotFoundError or NotADirectoryError where there is none
        (not yet written, or the job dropped), ValueError where what is there is no record (it does not parse, or is
        not a mapping), and any other error of the read as itself. It is replaced whole, so it is never half of one."""
        with open(self.record, encoding="utf-8") as stream:
            record = json.load(stream)
        if not isinstance(record, dict):
            raise ValueError(f"job {self.id}'s record is not a mapping")
        return record

    def read(self) -> dict:
        """The record as it is on disk; {} where there is none (not yet written, or the job dropped) or it cannot be
        read: a reader is never refused."""
        try:
            return self._load()
        except (OSError, ValueError):
            return {}

    def _change(self, change: Callable[[dict], dict], *, first: bool = False) -> dict:
        """The record read, changed and replaced whole, under the lock on its folder: of two writers, neither loses
        the other's change. Only `create` writes a record from nothing (`first`). Any other change reads it strictly:
        where it is gone, or is no record, `Gone` is raised and nothing is written, so a writer that comes after a
        drop's unlink, which takes the same lock, lands no `job.json` in a folder being removed; any other error of the
        read (a descriptor limit, a disk's error, NFS's ESTALE) is raised as itself, never said as a job that is
        gone."""
        try:
            with store.locked(self.record):
                if first:
                    record = {}
                else:
                    try:
                        record = self._load()
                    except (FileNotFoundError, NotADirectoryError):
                        raise Gone(f"job {self.id} has no record to change: it was dropped") from None
                    except ValueError:
                        raise Gone(f"job {self.id}'s record cannot be read: nothing was written") from None
                record = change(record)
                store.replace(self.record, json.dumps(record, indent=2) + "\n", mode_if_new=paths.FILE_MODE)
        except (FileNotFoundError, NotADirectoryError):
            if first:
                raise
            raise Gone(f"job {self.id} has no folder: it was dropped") from None
        return record

    def update(self, **fields: object) -> dict:
        """fields written into the record, and the record as written. Every string is made plain, and a Secret is
        refused before anything is written; the fields `create` alone writes are refused; a job whose record is gone
        or is no record raises `Gone`, and any other error reading it is raised as itself."""
        fixed = sorted(name for name in fields if name in CREATED)
        if fixed:
            raise ValueError(f"a job's {', '.join(fixed)} is written by create alone, never changed")
        unknown = sorted(name for name in fields if name not in FIELDS)
        if unknown:
            raise ValueError(f"a job's record has no field {', '.join(unknown)} that may be written")
        given = _plain(fields)
        assert isinstance(given, dict)
        return self._change(lambda record: {**record, **given})

    def listen(self, listener: Listener) -> None:
        """listener is handed every event this job's run emits from now on, after those registered before it."""
        if self._held_here() is None:
            raise RuntimeError(f"job {self.id} is not this process's: only the run that made a job hears its events")
        self._listeners.append(listener)

    def emit(self, kind: str, machine: str | None, text: str, **data: object) -> Event:
        """One event of this run: checked, made plain, kept in the record where its kind is one an agent or `logs`
        reads (`waiting`, `next`, an `end`), then handed to each listener in order. Refused before anything is written
        or heard: a kind not in KINDS, a Secret anywhere in it, and an end with no outcome."""
        if self._held_here() is None:
            raise RuntimeError(f"job {self.id} is not this process's: only the run that made a job emits its events")
        if kind not in KINDS:
            raise ValueError(f"{kind!r} is no kind of event: one of {', '.join(KINDS)}")
        for value, what in ((machine, "the machine"), (text, "the text")):
            if isinstance(value, Secret):
                raise TypeError(f"a Secret is never {what} of an event")
        if machine is not None and not isinstance(machine, str):
            raise TypeError(f"an event's machine is a str or None, not a {type(machine).__name__}")
        if not isinstance(text, str):
            raise TypeError(f"an event's text is a str, not a {type(text).__name__}")
        carried = _plain(data)
        assert isinstance(carried, dict)
        about = plain_text(machine) if machine is not None else None
        event = Event(time.time(), kind, about, plain_text(text), carried)
        change = self._recorded(event)
        if change is not None:
            self._change(change)
        if kind == "stage" and event.machine is not None:
            self._stages[event.machine] = event.text
        for listener in self._listeners:
            listener(event)
        return event

    def _recorded(self, event: Event) -> Callable[[dict], dict] | None:
        """How event changes the record, checked before anything is written; None for a kind the record keeps
        nothing of."""
        machine, text, data = event.machine, event.text, dict(event.data)
        if event.kind == "waiting":
            waiting = {**data, "machine": machine, "text": text, "since": event.time} if text else None
            return lambda record: {**record, "waiting": waiting}
        if event.kind == "next":
            matter = data.get("matter")
            if not isinstance(matter, str) or not matter:
                raise ValueError("a NEXT STEPS line names its matter: emit('next', machine, line, matter=...)")

            def next_steps(record: dict) -> dict:
                steps, found = [], False
                for step in record.get("next") or []:
                    if isinstance(step, dict) and (step.get("machine"), step.get("matter")) == (machine, matter):
                        found = True
                        if text:
                            steps.append({"machine": machine, "matter": matter, "line": text})
                    else:
                        steps.append(step)
                if text and not found:
                    steps.append({"machine": machine, "matter": matter, "line": text})
                return {**record, "next": steps}

            return next_steps
        if event.kind == "end":
            outcome = data.get("outcome")
            if outcome not in OUTCOMES:
                raise ValueError(f"an end says its outcome: one of {', '.join(OUTCOMES)}")
            if machine is not None:
                stage = data.get("stage", self._stages.get(machine))
                ended = {"outcome": outcome, "stage": stage, "why": text}

                def machine_end(record: dict) -> dict:
                    return {**record, "machine_outcomes": {**(record.get("machine_outcomes") or {}), machine: ended}}

                return machine_end
            status = data.get("exit")
            if not isinstance(status, int) or isinstance(status, bool):
                raise ValueError("the run's end says its exit status: emit('end', None, why, outcome=..., exit=...)")
            return lambda record: {
                **record,
                "outcome": outcome,
                "exit": status,
                "ended": event.time,
                "why": text,
                "waiting": None,
            }
        return None


def _make_folders(folder: Path) -> None:
    """folder and each folder above it that is missing, made at DIR_MODE from the top down. One that exists is left
    as it is."""
    missing = []
    while not folder.is_dir():
        missing.append(folder)
        if folder.parent == folder:
            break
        folder = folder.parent
    for made in reversed(missing):
        try:
            os.mkdir(made, paths.DIR_MODE)
        except FileExistsError:
            continue
        os.chmod(made, paths.DIR_MODE)


def create(
    jobs_dir: str | os.PathLike,
    verb: str,
    command: str,
    host: str | None,
    machines: Sequence[str],
    starter: Mapping[str, str],
    attached: bool,
    *,
    inputs: Mapping[str, object] | None = None,
) -> Job:
    """A new job of this process's own: `jobs/<id>/` at mode 0700, its lock taken before anything else is written and
    held until this process ends, then `job.json` at 0600. Every value is checked before anything is made."""
    if not isinstance(verb, str) or not verb:
        raise ValueError("a job names its verb")
    if not isinstance(command, str):
        raise TypeError("a job's command is the line as typed, a str")
    if host is not None and not isinstance(host, str):
        raise TypeError("a job's host is a str or None")
    if isinstance(machines, str) or not all(isinstance(machine, str) for machine in machines):
        raise TypeError("a job's machines are a list of str")
    if not isinstance(attached, bool):
        raise TypeError("whether a job is attached is a bool")
    if not isinstance(starter, Mapping) or starter.get("kind") not in STARTERS:
        raise ValueError(f"a job's starter is {{'kind': one of {', '.join(STARTERS)}}}")
    server = starter.get("server")
    if (starter["kind"] == "mcp") != (server is not None) or set(starter) - {"kind", "server"}:
        raise ValueError("an MCP server's job names that server's own ID, and no other starter names one")
    if server is not None and (
        not isinstance(server, str) or server.split() != [server] or plain_text(server) != server
    ):
        raise ValueError("an MCP server's ID is one plain word")
    record = {
        "id": "",
        "verb": verb,
        "command": command,
        "inputs": dict(inputs or {}),
        "starter": dict(starter),
        "host": host,
        "machines": list(machines),
        "locks": [],
        "attached": attached,
        "pid": os.getpid(),
        "started": time.time(),
        "deadline": None,
        "stop": None,
        "directory": None,
        "log": None,
        "waiting": None,
        "next": [],
        "machine_outcomes": {},
    }
    record = _plain(record)
    assert isinstance(record, dict)
    jobs = Path(os.path.abspath(jobs_dir))
    _make_folders(jobs)
    for _ in range(8):
        folder = jobs / secrets.token_hex(ID_HEX // 2)
        try:
            os.mkdir(folder, paths.DIR_MODE)
            break
        except FileExistsError:
            continue
    else:
        raise FileExistsError(f"no free job ID in {jobs}")
    os.chmod(folder, paths.DIR_MODE)
    # The lock first: from here the job reads `running`, and a reader passes over the folder until job.json is there.
    # Opened for writing, as an exclusive lock needs on NFS; non-inheritable, as Python opens every descriptor.
    held = os.open(folder / LOCK, os.O_RDWR | os.O_CREAT | os.O_EXCL, paths.FILE_MODE)
    try:
        os.fchmod(held, paths.FILE_MODE)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(held)
        shutil.rmtree(folder, ignore_errors=True)
        raise
    _HELD[_key(folder)] = held
    job = Job(folder)
    try:
        job._change(lambda _: {**record, "id": job.id}, first=True)
    except BaseException:
        drop(job)
        raise
    return job


def locks_held() -> dict[int, str]:
    """The descriptors by which this process holds a job's lock, each with the lock's name: what a child of the run is
    never handed (runs/children.py)."""
    return {held: f"job {os.path.basename(key)}'s lock" for key, held in _HELD.items()}


def _lock_held(job: Job) -> bool:
    """Whether the job's lock is held: by this process where it made the job (a shortcut: a probe would find the
    same), else as a shared lock tried without waiting finds it, let go at once where it was taken. A lock file that
    is gone is free."""
    if job._held_here() is not None:
        return True
    try:
        probe = os.open(job.lock, os.O_RDONLY)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(probe, fcntl.LOCK_UN)
        return False
    finally:
        os.close(probe)


def state(job: Job) -> str | None:
    """One of STATES, derived from the record and the job's own lock alone; None, no such job, where the record is
    gone (not yet written, or the job dropped), as `find` and `listed` pass over it, whatever the lock says: from a
    drop's unlink on, a handle taken before it reads neither `running` nor `lost`. The lock is asked first: a run
    writes its outcome before it ends, so a lock found free means any outcome is already in the record read after it.
    A record that is there and cannot be read is read as {}, as `read` reads it."""
    held = _lock_held(job)
    try:
        record = job._load()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (OSError, ValueError):
        record = {}
    if record.get("outcome") in OUTCOMES:
        return record["outcome"]
    if held:
        return "cancelling" if record.get("stop") else "running"
    return "lost"


def find(jobs_dir: str | os.PathLike, job_id: str) -> Job | None:
    """The job of that ID, which is sixteen hex digits and nothing else; None where there is none, or its record is
    not written yet."""
    if not _is_id(job_id):
        return None
    job = Job(Path(jobs_dir) / job_id)
    return job if job.record.is_file() else None


def listed(jobs_dir: str | os.PathLike) -> list[Job]:
    """Every job, newest first. A folder with no record, not yet written or being dropped, is passed over, and a job
    that vanishes while it is read is never an error."""
    try:
        names = [entry.name for entry in os.scandir(jobs_dir)]
    except OSError:
        return []
    found = []
    for name in names:
        if not _is_id(name):
            continue
        job = Job(Path(jobs_dir) / name)
        started = job.read().get("started")
        if started is None:
            continue
        if not isinstance(started, (int, float)) or isinstance(started, bool):
            started = 0
        found.append((started, name, job))
    found.sort(key=lambda each: (each[0], each[1]), reverse=True)
    return [job for _, _, job in found]


def drop(job: Job) -> str | None:
    """A job this process made removed, as a run refused at its locks leaves none, and never failing on what it cannot
    remove, since the run's refusal is still to be said. `job.json` first, unlinked under the lock `_change` takes, so
    that a writer holding the record lands its change before it and one after it is refused, and readers pass over the
    folder from then on; then its lock closed; then the folder, what cannot be removed left as it is (a folder with no
    record is no job). Removing the folder while the lock file is still open would make NFS rename that file
    `.nfsXXXX`, and the folder would stay (experiment 40, item 3).

    None where no record of the job is left; else one line that says the record is left and why, for the refusal to
    say: a record left whole is a job that reads `lost` for good, a run that never ran. Only a record already gone is
    no error of the unlink's."""
    key = _key(job.folder)
    held = _HELD.get(key)
    if held is None:
        raise RuntimeError(f"job {job.id} is not this process's: only the run that made a job drops it")
    kept: OSError | None = None
    try:
        with store.locked(job.record):
            job.record.unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        kept = error
    del _HELD[key]
    os.close(held)
    shutil.rmtree(job.folder, ignore_errors=True)
    if kept is None:
        return None
    try:
        os.lstat(job.record)
    except (FileNotFoundError, NotADirectoryError):
        return None  # what the unlink could not remove, the folder's removal did
    except OSError:
        pass  # whether it is left cannot be told: the unlink's error stands
    return plain_text(f"job {job.id} could not be removed: {job.record} is left ({kept.strerror or kept})")
