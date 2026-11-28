"""A run's plain-text log: each event of the run as the lines a person reads, with a time, one file a run
(docs/architecture/engine.md §16.8).

`start(job, logs, verb, attached)` opens the run's log and makes it a listener of the job's events (job.py's `emit`):
`<folder>/<stamp>-<verb>[-<part>].log`, the stamp the run's start (`YYYYMMDD-HHMMSS`, as tools/run-logged's), the verb a
row of the table whose run is a job, and the part a word its pattern has checked (`PARTS`), never a machine's name, so
nothing a person typed as a name becomes a file's. The folder is made where it is missing and kept at mode 0700, the
file made new at 0600, never through a link; a second run of the same verb in the same second takes the next free name
(`-2.log`, `-3.log`). Each line is written whole with one `os.write`, so it is on disk as soon as it is said, through
any end of the process, `os._exit` and a kill among them; the descriptor is non-inheritable, as Python opens every
one, so no child holds it. The record's `log` names the file.

Where the folder is (`logs`): LOG_DIR as the environment gives it, a relative one from the folder the run started in;
LOG_DIR empty, no text log for a run at a terminal, and its job's folder for a detached one, which nobody could follow
otherwise; LOG_DIR not set, a checkout's `.logs/` where the fleet's home is the checkout's own (as today's justfile has
it), and else the state folder's `logs/` (fleet/paths.py's logs_dir). LOG_RETENTION_DAYS is the prune's
(runs/prune.py), 14 where it is not set.

A machine is said by its name: the record's `names` and `making`, a machine's first event's `name`, and a join's, never
its identity or a stand-in where a name is known. Every string the log is handed is plain already (`emit`), and is made
so again. The listener never raises: a log that cannot be written is said once on standard error, and the run goes on
without it. `note(job, text)` writes a line of the program's own, for what the run keeps off the person's terminal
(runs/attached.py's loop's own reports).

Standard library only, with the patterns, the table and the fleet's paths.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from claude_vm.fleet import files, paths
from claude_vm.runs import job as jobs
from claude_vm.schema import patterns
from claude_vm.text import one_line, plain_text

# A checkout's own folder of run logs, at its top, as today's justfile's LOG_DIR: git-ignored, and refused by
# tests/check_no_local_files.sh.
CHECKOUT_LOGS = ".logs"
# How many days logs and ended jobs are kept where LOG_RETENTION_DAYS is not set, as today's justfile keeps them.
RETENTION_DAYS = "14"
# The patterns a log's part is held to: deploy's --only and login's --only; and the one flag that names a part, deploy's
# --check. Never a pattern a machine's name is held to.
PARTS = ("stage", "sign_in")
FLAG_PARTS = ("check",)
# The stamp of a run's start in its log's name, and the time on each line.
STAMP = "%Y%m%d-%H%M%S"
CLOCK = "%Y-%m-%d %H:%M:%S"
# How many names a log tries in one second for one verb and part before it gives up.
TRIES = 100
# The opening of a log's file: made new, appended to, never through a link, never inherited.
OPENING = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC

# The log each run of this process writes, by its job's folder's real path: what note() writes into.
_OPEN: dict[str, TextLog] = {}


@dataclass(frozen=True)
class Logs:
    """Where a run's text log goes and how long logs are kept, as the run is given them: folder None where LOG_DIR is
    empty (a run at a terminal writes none, a detached one writes into its job's folder); retention LOG_RETENTION_DAYS
    as given, the prune's to read; part the part of the log's name, or None."""

    folder: Path | None
    retention: str = RETENTION_DAYS
    part: str | None = None


def logs(home: paths.Home, run: object, *, part: str | None = None, cwd: str | None = None) -> Logs:
    """The run's Logs, from its own words (cli/before.py's RunWords: log_dir and log_retention_days, None where the
    environment does not set them) and the fleet's home: LOG_DIR as given, from cwd (the folder the run started in)
    where it is relative; else a checkout's `.logs/` where the home is the checkout's own, and the state folder's
    `logs/` for any other."""
    given = getattr(run, "log_dir", None)
    kept = getattr(run, "log_retention_days", None)
    retention = kept if isinstance(kept, str) else RETENTION_DAYS
    if isinstance(given, str):
        if not given:
            return Logs(None, retention, part)
        return Logs(Path(os.path.join(cwd or os.getcwd(), given)), retention, part)
    return Logs(default_folder(home), retention, part)


def default_folder(home: paths.Home) -> Path:
    """Where the run logs go where LOG_DIR is not set: a checkout's `.logs/`, where the home is the checkout's own (its
    `.aw/`, or the home its client.yml points at); the state folder's `logs/` for the installed program and for a home
    the variable names."""
    if home.how == "checkout":
        own = home.pointer.parent if home.pointer is not None else home.root
        return own.parent / CHECKOUT_LOGS
    return paths.logs_dir(home)


def name(started: float, verb: str, part: str | None = None, again: int = 1) -> str:
    """The log's file name: the run's start, the verb and the part, and from the second try in one second its number.
    A verb whose run is no job, and a part that none of PARTS holds and FLAG_PARTS lacks, are refused: no name is ever
    made from a word a person typed as a machine's."""
    from claude_vm.schema.words import VERBS

    row = VERBS.get(verb)
    if row is None or not row.changes:
        raise ValueError(f"{verb!r} is no verb whose run is a job: it has no text log")
    if part is not None and not (
        part in FLAG_PARTS or (part and any(patterns.full(pattern, part) for pattern in PARTS))
    ):
        raise ValueError(f"{part!r} is no part of a log's name: one of {', '.join(PARTS)}'s values, or check")
    if not isinstance(again, int) or isinstance(again, bool) or again < 1:
        raise ValueError("a log's number in its second is a whole number from 1")
    stamp = time.strftime(STAMP, time.localtime(started))
    return f"{stamp}-{verb}{f'-{part}' if part else ''}{f'-{again}' if again > 1 else ''}.log"


def _kept(folder: Path) -> None:
    """folder made where it is missing (fleet/files.py's folder: never through a link), and kept at 0700, as
    tools/run-logged keeps LOG_DIR; an error is raised."""
    files.folder(folder)
    os.chmod(folder, paths.DIR_MODE)


def _open(folder: Path, started: float, verb: str, part: str | None) -> tuple[Path, int]:
    for again in range(1, TRIES + 1):
        path = folder / name(started, verb, part, again)
        try:
            held = os.open(path, OPENING, paths.FILE_MODE)
        except FileExistsError:
            continue
        try:
            os.fchmod(held, paths.FILE_MODE)
        except BaseException:
            os.close(held)
            raise
        return path, held
    raise FileExistsError(f"no free name for a log in {folder}: {TRIES} runs of {verb} in one second")


def _warn(sentence: str) -> None:
    with contextlib.suppress(Exception):
        print(f"warning: {sentence}", file=sys.stderr, flush=True)


class TextLog:
    """One run's text log: a listener of its job's events, and the one descriptor it writes through."""

    def __init__(self, path: Path, held: int, names: Mapping[str, str] | None = None) -> None:
        self.path = path
        self._held: int | None = held
        self._lock = threading.Lock()
        # Each machine's name by the key its events name it by, a stand-in's before its join, then its identity's.
        self.names: dict[str, str] = dict(names or {})

    @property
    def fd(self) -> int | None:
        """The descriptor the log is written through, None once it is closed: what a detached run points its standard
        output and error at (C13)."""
        return self._held

    def __call__(self, event: jobs.Event) -> None:
        try:
            self._write(self.lines(event))
        except Exception as error:  # the listener never raises: the run goes on without its log
            self._broken(error)

    def lines(self, event: jobs.Event) -> list[str]:
        """The lines a person reads of one event: its time, its kind, its machine by name, its text a line each, and
        what it carries on the first, each value on one line."""
        machine = event.machine
        if machine is not None:
            given = event.data.get("name")
            if event.kind == "join":
                known = self.names.pop(event.text, None)
                if known is not None:
                    self.names[machine] = known
            elif isinstance(given, str) and given and machine not in self.names:
                self.names[machine] = given
        said = self.names.get(machine, machine) if machine is not None else None
        head = f"{_clock(event.time)} {event.kind}{f' {one_line(said, None)}' if said is not None else ''}"
        # A join's text is the stand-in it joins, never said: its identity is.
        text = (machine or "") if event.kind == "join" else plain_text(event.text)
        rest = []
        for field, value in event.data.items():
            if event.kind != "join" and field == "name":
                continue
            shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
            rest.append(f"{field}={one_line(shown, None)}")
        each = text.split("\n") if text else [""]
        first = " · ".join([each[0], *rest]) if each[0] or rest else ""
        found = [f"{head}: {first}" if first else head]
        found.extend(f"{head}: {line}" for line in each[1:])
        return found

    def note(self, text: str) -> None:
        """One line of the program's own, never raising."""
        try:
            self._write([f"{_clock(time.time())} aw: {one_line(text, None)}"])
        except Exception as error:
            self._broken(error)

    def _write(self, lines: list[str]) -> None:
        data = "".join(f"{line}\n" for line in lines).encode("utf-8", "replace")
        with self._lock:
            if self._held is None:
                return
            view = memoryview(data)
            while view:
                view = view[os.write(self._held, view) :]

    def _broken(self, error: Exception) -> None:
        with self._lock:
            if self._held is None:
                return
            with contextlib.suppress(OSError):
                os.close(self._held)
            self._held = None
        _warn(f"the run's text log {self.path} could not be written ({error}): the run goes on without it")

    def close(self) -> None:
        """The descriptor closed; anything heard after is not written."""
        with self._lock:
            if self._held is not None:
                with contextlib.suppress(OSError):
                    os.close(self._held)
                self._held = None


def _clock(when: float) -> str:
    return time.strftime(CLOCK, time.localtime(when))


def start(job: jobs.Job, given: Logs, verb: str, attached: bool) -> TextLog | None:
    """The run's text log opened, listening to its job's events, its first line the job and its command, and its path
    written into the record's `log`; None where the run writes none (LOG_DIR empty at a terminal) or it cannot be made,
    which is said on standard error, the run going on without it."""
    folder = given.folder
    if folder is None:
        if attached:
            return None
        folder = job.folder
    record = job.read()
    started = record.get("started")
    when = started if isinstance(started, (int, float)) and not isinstance(started, bool) else time.time()
    try:
        if folder != job.folder:
            _kept(folder)
        path, held = _open(folder, when, verb, given.part)
    except (OSError, ValueError) as error:
        _warn(f"no text log for this run: {plain_text(str(error))}")
        return None
    names = {**_mapping(record.get("making")), **_mapping(record.get("names"))}
    log = TextLog(path, held, names)
    try:
        job.update(log=str(path))
    except (OSError, jobs.Gone) as error:
        log.close()
        _warn(f"no text log for this run: its record cannot name it ({plain_text(str(error))})")
        return None
    log.note(f"job {job.id}: {record.get('command') or verb}")
    job.listen(log)
    _OPEN[jobs._key(job.folder)] = log
    return log


def _mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {key: str(name) for key, name in value.items() if isinstance(key, str) and isinstance(name, str)}


def of(job: jobs.Job) -> TextLog | None:
    """The text log this process writes for job's run, or None."""
    return _OPEN.get(jobs._key(job.folder))


def note(job: jobs.Job, text: str) -> None:
    """A line of the program's own in job's text log, where this process writes one; nothing where it writes none."""
    log = of(job)
    if log is not None:
        log.note(text)


def finish(job: jobs.Job) -> None:
    """The run's log closed once the run has ended; removed where its job is gone (a run whose ladder never began, so
    dropped, leaves no log of nothing)."""
    log = _OPEN.pop(jobs._key(job.folder), None)
    if log is None:
        return
    log.close()
    if log.path.parent != job.folder and not os.path.lexists(job.record):
        with contextlib.suppress(OSError):
            os.unlink(log.path)
