"""A run's short-lived directory: where its generated files go - the layer-1 child's configuration, its inventory and
its variables - for as long as the run lives (docs/architecture/engine.md §16.8).

`make(job)` makes one directory for a run with `tempfile.mkdtemp`, named `aw-run-<the job's ID>-<random>`, at mode
0700, under the per-user temporary directory (`tempfile.gettempdir()`: `TMPDIR` where it is set, as on macOS, else
`/tmp`), and writes its path into the job's record. Never inside `XDG_RUNTIME_DIR`: systemd removes that directory at
the person's last logout while a detached run may still live, so where the temporary directory lies inside it, `/tmp`
is used. `made(job)` gives it back to the process that made it, and `path_for(job, name)` names a file in it and
writes nothing. `remove(job)` deletes it, and is safe to call
twice; a process that did not make the job removes only the directory the record names, held to the form `make`
gives it, and only once the run no longer holds the job's lock.

No secret is ever written here: a named secret, the vault's key, a token, Claude's code and a Tailscale key travel on
standard input or stay in memory, as today, and every workflow's tests search this directory for the secrets they gave
(`aw_kit.searched_for`). No control socket is made here either: every shared connection lives in the one control
directory (`paths.control_dir`). This module makes and removes the directory, and writes no file in it.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

from claude_vm.fleet import paths
from claude_vm.runs import job as jobs
from claude_vm.schema import patterns

# The start of a run's directory's name, before the job's ID: the ID is in the name, so that a directory a killed run
# left is told by its job, and an Ansible child's command line that holds the path names its run.
PREFIX = "aw-run-"
# Where the directory goes when the per-user temporary directory lies inside XDG_RUNTIME_DIR.
FALLBACK = "/tmp"

# The directory this process made for each of its jobs, by the job's folder's real path: what the run itself names a
# file in and removes, read from what it holds, as its job's lock is (job.py's _HELD).
_MADE: dict[str, Path] = {}


def _inside(path: str, folder: str) -> bool:
    """Whether path is folder or lies under it, by their real paths."""
    real, top = os.path.realpath(path), os.path.realpath(folder)
    return os.path.commonpath([real, top]) == top


def parent() -> str:
    """Where a run's directory is made: the per-user temporary directory, or FALLBACK where that lies inside
    XDG_RUNTIME_DIR, which is only read where it is an absolute path, as the XDG specification says."""
    found = tempfile.gettempdir()
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if os.path.isabs(runtime) and _inside(found, runtime):
        return FALLBACK
    return found


def _own(job: jobs.Job, what: str) -> str:
    """The key of a job this process made; refused for any other, as job.py refuses another's emit."""
    if job._held_here() is None:
        raise RuntimeError(f"job {job.id} is not this process's: only the run that made a job {what}")
    return jobs._key(job.folder)


def make(job: jobs.Job) -> Path:
    """The run's one directory, made at mode 0700 under `parent()` with the job's ID in its name, and its path written
    into the job's record. A job this process did not make, a second directory for one job, and a job whose record is
    gone are refused; where the record cannot take the path, the directory is removed again."""
    key = _own(job, "makes its directory")
    if not patterns.full("job_id", job.id):
        raise ValueError(f"{job.id!r} is no job's ID")
    if key in _MADE:
        raise RuntimeError(f"job {job.id} has its directory already: a run has one")
    made = Path(tempfile.mkdtemp(prefix=f"{PREFIX}{job.id}-", dir=parent()))
    try:
        # mkdtemp asks for 0700, from which the umask may take bits: the mode is set, not asked for.
        os.chmod(made, paths.DIR_MODE)
        job.update(directory=str(made))
    except BaseException:
        shutil.rmtree(made, ignore_errors=True)
        raise
    _MADE[key] = made
    return made


def made(job: jobs.Job) -> Path | None:
    """The directory this process made for the job's run, which its body is given (runs/attached.py); None where it made
    none."""
    return _MADE.get(jobs._key(job.folder))


def path_for(job: jobs.Job, name: str) -> Path:
    """name's path in the run's directory: the generated configuration, the inventory, the variables. Nothing is
    written. name is one plain file name: not empty, not '.' or '..', no separator, every character printable."""
    key = _own(job, "names a file in its directory")
    if not isinstance(name, str):
        raise TypeError(f"a file's name is a str, not a {type(name).__name__}")
    if name in ("", ".", "..") or "/" in name or not name.isprintable():
        raise ValueError(f"{name!r} is not one plain file name")
    made = _MADE.get(key)
    if made is None:
        raise RuntimeError(f"job {job.id} has no directory: make(job) first")
    return made / name


def _recorded(job: jobs.Job) -> Path | None:
    """The directory the job's record names, held to the form make gives it: an absolute path whose name is PREFIX,
    the job's ID, and mkdtemp's letters. None where the record names none. Any other path is refused."""
    where = job.read().get("directory")
    if where is None:
        return None
    form = rf"{re.escape(PREFIX + job.id)}-[A-Za-z0-9_]+"
    if not (
        isinstance(where, str)
        and os.path.isabs(where)
        and os.path.normpath(where) == where
        and re.fullmatch(form, os.path.basename(where))
    ):
        raise ValueError(f"job {job.id}'s record names no directory of its run's: {where!r}")
    return Path(where)


def remove(job: jobs.Job) -> bool:
    """The run's directory deleted, with everything in it: True where it is gone or was never made, False where
    something in it could not be removed, which is left. Safe to call twice. The run that made the job removes the
    directory it made, even once the job is dropped; any other process removes only the one the record names, held to
    make's form - a real directory, not a link, of this account's - and never while the job's lock is held: a living
    run's directory is its own. Nor where the job is no job by the time its state is asked (`state` is None): its run
    dropped it since the record was read, and removes what it made itself."""
    key = jobs._key(job.folder)
    made = _MADE.pop(key, None)
    if made is None:
        made = _recorded(job)
        if made is None:
            return True
        if job._held_here() is None:
            now = jobs.state(job)
            if now in ("running", "cancelling"):
                raise RuntimeError(f"job {job.id} still runs: its directory is its run's to remove")
            if now is None:
                # The record read above is gone since: the run dropped its job, and removes the directory it made.
                raise RuntimeError(f"job {job.id} was dropped by its run: its directory is its run's to remove")
        try:
            found = os.lstat(made)
        except FileNotFoundError:
            return True
        if not stat.S_ISDIR(found.st_mode) or found.st_uid != os.getuid():
            raise ValueError(f"{made} is not a directory of this account's: it is left as it is")
    shutil.rmtree(made, ignore_errors=True)
    return not os.path.lexists(made)
