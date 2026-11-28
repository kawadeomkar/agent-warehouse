"""A run's short-lived directory: where its generated files go - the layer-1 child's configuration, its inventory and
its variables - for as long as the run lives (docs/architecture/engine.md §16.8).

`make(job)` makes one directory for a run with `tempfile.mkdtemp`, named `aw-run-<the job's ID>-<random>`, at mode
0700, under the per-user temporary directory (`tempfile.gettempdir()`: the first of `TMPDIR`, `TEMP` and `TMP` that
names a directory it can write in, else `/tmp`, `/var/tmp` or `/usr/tmp`, and at last the folder the run started in),
and writes its path into the job's record. Never inside `XDG_RUNTIME_DIR`: systemd removes that directory at the
person's last logout while a detached run may still live, so where the temporary directory lies inside it, `/tmp` is
used. `made(job)` gives it back to the process that made it, and `path_for(job, name)` names a file in it and writes
nothing. `remove(job)` deletes it, and is safe to call twice: only the process that made the directory removes it, even
once its job is dropped, and a call for another process's job is refused, as `make` and `path_for` refuse one.

What goes there: the layer-1 child's generated files, and a machine's first use's scratch known-hosts file
(ssh/connections.py). No secret is ever written here: a named secret, the vault's key, a token, Claude's code and a
Tailscale key travel on standard input or stay in memory, as today. No control socket is made here either: every
shared connection lives in the one control directory (`paths.control_dir`). This module makes and removes the
directory, and writes no file in it.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from claude_vm.fleet import paths
from claude_vm.runs import job as jobs
from claude_vm.schema import patterns

# The start of a run's directory's name, before the job's ID: the ID is in the name, so that a directory a killed run
# left is told by its job, and an Ansible child's command line that holds the path names its run.
PREFIX = "aw-run-"
# Where the directory goes when the per-user tempfile directory lies inside XDG_RUNTIME_DIR.
FALLBACK = "/tmp"

# The directory this process made for each of its jobs, by the job's folder's real path: what the run itself names a
# file in and removes, read from what it holds, as its job's lock is (job.py's _HELD); and the jobs whose directory it
# has removed, whose removal is done, their jobs dropped or not.
_MADE: dict[str, Path] = {}
_REMOVED: set[str] = set()


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


def remove(job: jobs.Job) -> bool:
    """The run's directory deleted, with everything in it, by the process that made it, even once its job is dropped:
    True where it is gone or was never made, False where something in it could not be removed, which is left and
    tried again, at the same path whatever the record says, by the next call. Safe to call twice, its job dropped or
    not. Never read from the record: a job this process neither made a directory for nor holds is refused, as `make`
    and `path_for` refuse one, since a living run's directory is its own and one a killed run left is no other run's to
    judge."""
    key = jobs._key(job.folder)
    made = _MADE.get(key)
    if made is None:
        if key not in _REMOVED:
            _own(job, "removes its directory")
        return True
    shutil.rmtree(made, ignore_errors=True)
    if os.path.lexists(made):
        return False
    del _MADE[key]
    _REMOVED.add(key)
    return True
