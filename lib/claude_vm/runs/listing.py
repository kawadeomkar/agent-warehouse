"""aw logs: the jobs, newest first; one job's text log; and that log followed until its job ends
(docs/architecture/engine.md §16.8).

`logs` is the handler words.py's row of logs names. With no job, every job newest first (job.py's listed), each with
its ID, its state (job.py's state: derived from its record and its own lock, never written), when it started, how long
it ran or has run, its command and its text log's path: a lost job is said so and its record is left as it is. With a
job, by its ID, its text log as written, every line made plain. With --follow, its log and then each line added, read
as the file grows, until the job's lock is free (the run writes its end before the lock goes, so what is read after
it is the whole log), then its outcome and each machine's, said by name (the record's `names`, and `making` for one
the run was still making, never a stand-in's key); Ctrl-C stops the following, says the job goes on, and exits 130.
With --save, where its log already is on this computer: a run's log is written as it goes, so nothing is fetched.

A job whose lock cannot be read is said so, by its ID, never as lost and never as free. A fleet's record (fleet.json)
whose jobs store is newer than this program reads is refused by name before any job is read (fleet/record.py's
refuse_newer). Nothing is written. Standard library only, with the job's reader, the fleet's paths and record, the
printer of a command, and text: no pydantic, Ansible, asyncio, workflow or MCP module
(tests/unit/test_aw_cli_imports.py).
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from claude_vm.fleet import paths, record
from claude_vm.runs import job as jobs
from claude_vm.schema import printer
from claude_vm.schema.words import EXITS
from claude_vm.text import one_line, plain_text

# The program's root, where fleet/paths.py works out the fleet's home from: a checkout or an installed release.
PROGRAM_ROOT = Path(__file__).resolve().parents[3]
# How often a follow looks for what its log has gained, and whether its job still runs, in seconds.
POLL = 0.2
# How much of a log is read at once.
CHUNK = 1 << 16
# The list's columns.
COLUMNS = ("JOB", "STATE", "STARTED", "LENGTH", "COMMAND", "LOG")
# When a job started, as the list says it.
CLOCK = "%Y-%m-%d %H:%M:%S"
# What the list says of a state it cannot read, and of a length or a log it cannot tell.
UNREAD = "unknown"
NONE = "-"


class _Refused(Exception):
    """A sentence that ends the command before it reads or prints anything of a job."""


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _length(seconds: float) -> str:
    whole = max(0, int(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _state(job: jobs.Job) -> tuple[str | None, str | None]:
    """job's state, and a warning where its lock cannot be read: (None, None) where it is no job any more."""
    try:
        return jobs.state(job), None
    except OSError as error:
        why = plain_text(error.strerror or str(error))
        return UNREAD, f"warning: job {job.id}'s lock cannot be read ({why}): its state is not known"


def _row(job: jobs.Job, state: str, now: float) -> tuple[str, ...]:
    found = job.read()
    started, ended = _number(found.get("started")), _number(found.get("ended"))
    when = time.strftime(CLOCK, time.localtime(started)) if started is not None else NONE
    if started is not None and ended is not None:
        length = _length(ended - started)
    elif started is not None and state in ("running", "cancelling"):
        length = _length(now - started)
    else:
        length = NONE
    log = found.get("log")
    return (
        job.id,
        state,
        when,
        length,
        one_line(found.get("command") or found.get("verb") or NONE, None),
        one_line(log, None) if isinstance(log, str) and log else NONE,
    )


def _table(rows: list[tuple[str, ...]]) -> list[str]:
    widths = [max(len(row[at]) for row in rows) for at in range(len(COLUMNS))]
    return ["  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip() for row in rows]


def _listed(jobs_dir: Path) -> int:
    rows: list[tuple[str, ...]] = [COLUMNS]
    now = time.time()
    for job in jobs.listed(jobs_dir):
        state, warning = _state(job)
        if warning is not None:
            print(warning, file=sys.stderr)
        if state is None:
            continue  # dropped since it was listed
        rows.append(_row(job, state, now))
    if len(rows) == 1:
        print(f"No jobs in {plain_text(str(jobs_dir))}.")
        return EXITS["done"]
    for line in _table(rows):
        print(line)
    return EXITS["done"]


def _names(found: Mapping[str, Any]) -> dict[str, str]:
    """Each machine's name by the key the record keeps it under: an identity's from `names`, a stand-in's from
    `making`."""
    named: dict[str, str] = {}
    for field in ("making", "names"):
        kept = found.get(field)
        if isinstance(kept, dict):
            named.update({key: name for key, name in kept.items() if isinstance(key, str) and isinstance(name, str)})
    return named


def _log_of(found: Mapping[str, Any]) -> str | None:
    log = found.get("log")
    return log if isinstance(log, str) and log else None


def _say(data: bytes, rest: bytearray) -> None:
    """What a log gained, its whole lines printed plain; a part of a line kept in rest for the next read."""
    rest.extend(data)
    end = rest.rfind(b"\n")
    if end < 0:
        return
    whole, left = bytes(rest[: end + 1]), bytes(rest[end + 1 :])
    rest[:] = left
    sys.stdout.write(plain_text(whole.decode("utf-8", "replace")))
    sys.stdout.flush()


def _rest(rest: bytearray) -> None:
    if rest:
        sys.stdout.write(plain_text(bytes(rest).decode("utf-8", "replace")) + "\n")
        rest.clear()
    sys.stdout.flush()


def _printed(path: str, job: jobs.Job) -> int:
    try:
        with open(path, "rb") as log:
            rest = bytearray()
            while data := log.read(CHUNK):
                _say(data, rest)
            _rest(rest)
    except OSError as error:
        raise _Refused(f"error: job {job.id}'s text log {plain_text(path)} cannot be read: {_why(error)}") from None
    return EXITS["done"]


def _why(error: OSError) -> str:
    return plain_text(error.strerror or str(error))


def _ended(job: jobs.Job) -> None:
    """The job's outcome and each machine's, said by name."""
    found = job.read()
    state, warning = _state(job)
    if warning is not None:
        print(warning, file=sys.stderr)
    exit_status = found.get("exit")
    said = f"Job {job.id} {state}" + (f", exit {exit_status}" if isinstance(exit_status, int) else "")
    why = found.get("why")
    print(f"{said}: {one_line(why, None)}" if isinstance(why, str) and why else f"{said}.")
    named = _names(found)
    outcomes = found.get("machine_outcomes")
    for machine, each in (outcomes if isinstance(outcomes, dict) else {}).items():
        if not isinstance(each, dict):
            continue
        name = one_line(named.get(machine) or (NONE if jobs.is_stand_in(machine) else machine), None)
        stage = each.get("stage")
        where = f" at {one_line(stage, None)}" if isinstance(stage, str) and stage else ""
        print(f"  {name}: {one_line(each.get('outcome') or UNREAD, None)}{where}")


def _held(job: jobs.Job) -> bool:
    try:
        return jobs.lock_held(job)
    except OSError as error:
        raise _Refused(f"error: job {job.id}'s lock cannot be read ({_why(error)}): it cannot be followed") from None


def _followed(job: jobs.Job, path: str | None) -> int:
    if path is None:
        unlogged = f"Job {job.id} keeps no text log (its run had LOG_DIR empty): its outcome is said once it ends."
        print(unlogged, file=sys.stderr)
        while _held(job):
            time.sleep(POLL)
        _ended(job)
        return EXITS["done"]
    try:
        log = open(path, "rb")
    except OSError as error:
        raise _Refused(f"error: job {job.id}'s text log {plain_text(path)} cannot be read: {_why(error)}") from None
    with log:
        rest = bytearray()
        while True:
            data = log.read(CHUNK)
            if data:
                _say(data, rest)
                continue
            if not _held(job):
                # Its end is written before its lock goes: what is read now is the rest of the log.
                while data := log.read(CHUNK):
                    _say(data, rest)
                break
            time.sleep(POLL)
        _rest(rest)
    _ended(job)
    return EXITS["done"]


def _jobs_dir() -> Path:
    try:
        home = paths.home(PROGRAM_ROOT, os.environ)
        found = record.read(home)
    except (paths.Refused, record.Refused) as error:
        raise _Refused(f"error: {plain_text(str(error)).removeprefix('error: ')}") from None
    if found is not None:
        refusal = record.refuse_newer(found, {"jobs": record.KNOWN["jobs"]})
        if refusal is not None:
            raise _Refused(f"error: {refusal.removeprefix('error: ')}")
    return paths.jobs_dir(home)


def logs(run: Any, *, job: str | None = None, follow: bool = False, save: bool = False) -> int:
    """aw logs, aw logs <job>, aw logs <job> --follow and aw logs <job> --save: the exit status."""
    try:
        if job is None and (follow or save):
            flag = "--follow" if follow else "--save"
            listing = printer.line("logs", {}, refused=False)
            raise _Refused(f"error: {flag} is for one job: give its ID, as {listing} lists the jobs")
        jobs_dir = _jobs_dir()
        if job is None:
            return _listed(jobs_dir)
        try:
            found = jobs.find(jobs_dir, job)
        except OSError as error:
            raise _Refused(f"error: job {job} cannot be read: {_why(error)}") from None
        if found is None:
            listing = printer.line("logs", {}, refused=False)
            raise _Refused(f"error: there is no job {job} in {plain_text(str(jobs_dir))}: {listing} lists the jobs")
        path = _log_of(found.read())
        if save:
            if path is None:
                raise _Refused(f"error: job {found.id} keeps no text log: its run had LOG_DIR empty")
            print(f"Job {found.id}'s log is kept on this computer already, at {plain_text(path)}")
            return EXITS["done"]
        if follow:
            try:
                return _followed(found, path)
            except KeyboardInterrupt:
                sys.stdout.flush()
                again = printer.line("logs", {"job": found.id, "follow": True}, refused=False)
                print(f"\nStopped following job {found.id}: it goes on. Follow it again: {again}", file=sys.stderr)
                return EXITS["cancelled"]
        if path is None:
            raise _Refused(f"error: job {found.id} keeps no text log: its run had LOG_DIR empty")
        return _printed(path, found)
    except _Refused as refused:
        print(str(refused), file=sys.stderr)
        return EXITS["refused"]
