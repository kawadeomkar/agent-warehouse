"""The prune: run logs and ended jobs older than LOG_RETENTION_DAYS deleted when a run that writes a log starts
(docs/architecture/engine.md §16.8).

`prune(log_dir, jobs_dir, retention)` deletes three kinds of thing and nothing else, each older than the retention as
`find -mtime +<days>` counts it (whole days past, so 0 keeps a day):

- a log file in LOG_DIR named as runs/textlog.py and tools/run-logged name one (`LOG_NAME`, today's `[0-9]*-*.log`),
  at its top, a file and never a link, as tools/run-logged prunes them;
- a job's folder in the jobs folder, named by a job's ID, a directory and never a link, whose lock is free (job.py's
  lock_held): a job whose lock is held is a run that lives, and is never pruned, whatever its age. Its age is its end
  where the record has one, else the record's last change (a run killed outright), else the folder's own (a folder a
  drop could not empty, which has no record and no end of its own);
- that job's short-lived directory (runs/rundir.py), which a killed run leaves and nothing else removes: only where
  the record names one of its form, `aw-run-<that job's ID>-…`, a directory and never a link, this account's own.

Each path is held, before it goes, to be what it is said to be: a log inside LOG_DIR, a job's folder inside the jobs
folder, a run's directory of its job's name, each by its real path. So the vault's key file, client.yml and the
fleet's folder, which lie outside every one of them, are never reached, whatever a record says. A job whose lock cannot
be read (a descriptor limit, a folder's mode, a disk's error) is said, by its ID, and kept: never read as free, never
as lost. Nothing is ever raised: what cannot be read or removed is left as it is.

Standard library only, with the job's reader and the run directory's name.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from claude_vm.runs import job as jobs
from claude_vm.runs import rundir
from claude_vm.text import plain_text

# A day, as find's -mtime counts one.
DAY = 86400
# A run log's name, as tools/run-logged's prune takes one (find -name '[0-9]*-*.log'): a digit first, a hyphen, .log.
LOG_NAME = re.compile(r"[0-9][^/]*-[^/]*\.log")
# A run's directory's name for one job: the prefix, the job's ID, a hyphen and mkdtemp's own letters.
RUN_DIRECTORY = re.compile(r"[A-Za-z0-9_]+")


@dataclass
class Pruned:
    """What a prune deleted, and what it said: logs by path, jobs by ID, run directories by path."""

    logs: list[Path] = field(default_factory=list)
    jobs: list[str] = field(default_factory=list)
    directories: list[Path] = field(default_factory=list)
    said: list[str] = field(default_factory=list)


def days_of(retention: str) -> int | None:
    """LOG_RETENTION_DAYS as a whole number of days, None where it is none: then nothing is pruned, and the run says
    so, as tools/run-logged does."""
    return int(retention) if isinstance(retention, str) and retention.isascii() and retention.isdigit() else None


def _older(when: float, days: int, now: float) -> bool:
    """Whether when is more than days whole days ago, as find -mtime +days reads a file's time."""
    return (now - when) // DAY > days


def _inside(path: Path, folder: Path) -> bool:
    """Whether path is one entry at folder's top, by their real paths: path's real parent is folder's real path."""
    return os.path.realpath(path.parent) == os.path.realpath(folder) and path.name not in ("", ".", "..")


def _entries(folder: Path) -> list[os.DirEntry]:
    try:
        with os.scandir(folder) as found:
            return list(found)
    except OSError:
        return []


def _logs(log_dir: Path, days: int, now: float, pruned: Pruned) -> None:
    for entry in _entries(log_dir):
        if not LOG_NAME.fullmatch(entry.name):
            continue
        path = log_dir / entry.name
        try:
            found = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(found.st_mode) or not _older(found.st_mtime, days, now) or not _inside(path, log_dir):
            continue
        try:
            os.unlink(path)
        except OSError:
            continue
        pruned.logs.append(path)


def _age(job: jobs.Job, record: dict, folder: os.stat_result) -> float:
    """When the job last did anything: its end, else its record's last change, else its folder's."""
    ended = record.get("ended")
    if isinstance(ended, (int, float)) and not isinstance(ended, bool):
        return float(ended)
    try:
        return os.lstat(job.record).st_mtime
    except OSError:
        return folder.st_mtime


def _run_directory(job: jobs.Job, named: object) -> Path | None:
    """The run's directory the record names, where it is one of the job's form, a directory and no link, this
    account's: None for anything else, which is never touched."""
    if not isinstance(named, str) or not os.path.isabs(named):
        return None
    path = Path(named)
    start = f"{rundir.PREFIX}{job.id}-"
    if not path.name.startswith(start) or not RUN_DIRECTORY.fullmatch(path.name[len(start) :]):
        return None
    try:
        found = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISDIR(found.st_mode) or found.st_uid != os.getuid():
        return None
    return path


def _jobs(jobs_dir: Path, days: int, now: float, pruned: Pruned) -> None:
    for entry in _entries(jobs_dir):
        if not jobs._is_id(entry.name):
            continue
        path = jobs_dir / entry.name
        try:
            folder = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISDIR(folder.st_mode) or not _inside(path, jobs_dir):
            continue
        job = jobs.Job(path)
        try:
            if jobs.lock_held(job):
                continue
        except OSError as error:
            why = plain_text(error.strerror or str(error))
            pruned.said.append(f"warning: job {job.id}'s lock cannot be read ({why}): it is not pruned")
            continue
        record = job.read()
        if not _older(_age(job, record, folder), days, now):
            continue
        directory = _run_directory(job, record.get("directory"))
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
            if not os.path.lexists(directory):
                pruned.directories.append(directory)
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.lexists(path):
            pruned.jobs.append(job.id)


def prune(log_dir: Path | None, jobs_dir: Path, retention: str, *, now: float | None = None) -> Pruned:
    """The logs of log_dir (None: none) and the jobs of jobs_dir older than retention days deleted, with the
    directories their runs left; a retention that is no whole number prunes nothing and says so."""
    pruned = Pruned()
    days = days_of(retention)
    if days is None:
        pruned.said.append("warning: LOG_RETENTION_DAYS must be a number of days; nothing pruned")
        return pruned
    when = time.time() if now is None else now
    if log_dir is not None:
        _logs(Path(log_dir), days, when, pruned)
    _jobs(Path(jobs_dir), days, when, pruned)
    return pruned
