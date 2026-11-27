"""The one way the new engine makes a folder of the fleet's and writes a file in it (docs/architecture/engine.md §16.9).

`folder(path)` makes a folder of the fleet's, or of its state: where it is missing, it and each folder above it that is
missing are made at `paths.DIR_MODE` (0700), from the top down, the mode set and never left to the umask. Where it is
there it must be a folder itself, never a link to one, and is refused otherwise (`Unsafe`): a link where `fleet/`,
`jobs/` or `locks/` goes would send what the engine writes beneath it somewhere nobody chose for it. A folder that is
there keeps its mode: the data folder already holds tools/env-run's `checkouts/`, at the umask's mode, so a looser
mode is never a refusal. The folders above the one asked for that are there are the person's and taken as they are,
links among them (a `~/.config` kept in a dotfiles folder, macOS's `/var`); one this module had to make is held as
the folder asked for is, since it was not there to be chosen.

`sweep(path)` takes out the scratch files `store.replace` left beside path where a write was killed between its
scratch file and its rename, which nothing else would remove and which would travel with `fleet/`: its caller holds
`store.locked(path)`, so that no write under way loses its scratch file.

`write(path, text)` writes a file of the fleet's whole: its folder made by `folder`, then `store.replace`, which
writes beside it, flushes and renames, so a reader finds the old file or the new one and never a part, a new file at
`paths.FILE_MODE` (0600). A file that is there keeps its mode, and through a link the file it names is the one
replaced, as `store.replace` does for today's settings: a file is one a person may keep elsewhere on purpose, a folder
is everything beneath it. A caller that reads the file and writes it from what it read holds `store.locked` across
both.

Every writer of a fleet's folders uses this module: `record.py`'s `fleet.json`, `sealed.py`'s `vault.age`,
`overrides.py`'s folders of the overrides, `hosts.py`'s folders of a host, and `runs/job.py`'s and `runs/locks.py`'s
folders of the state. Standard library only, with `store` and `text`.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from claude_vm import store
from claude_vm.fleet import paths
from claude_vm.text import plain_text


class Unsafe(ValueError):
    """Where a folder of the fleet's goes stands a link or something that is not a folder: nothing was written. The
    sentence names the path and what to do."""


def _held(where: Path) -> None:
    """where, which is there, held to be a folder itself: never a link, never anything else."""
    info = os.lstat(where)
    said = plain_text(str(where))
    if stat.S_ISLNK(info.st_mode):
        raise Unsafe(
            f"error: {said} is a link, where a folder of the fleet's goes: nothing is written through it; put the"
            " folder itself there in its place"
        )
    if not stat.S_ISDIR(info.st_mode):
        raise Unsafe(f"error: {said} is not a folder, where a folder of the fleet's goes: nothing is written there")


def folder(path: str | os.PathLike) -> Path:
    """path as a folder of the fleet's, made where it is missing: it and each missing folder above it at DIR_MODE,
    from the top down. One that is there is held to be a folder itself, never a link (`Unsafe`), and keeps its mode.
    Returns path, made absolute."""
    where = Path(os.path.abspath(path))
    missing: list[Path] = []
    here = where
    while True:
        try:
            os.lstat(here)
        except FileNotFoundError:
            missing.append(here)
            if here.parent == here:
                break
            here = here.parent
            continue
        except NotADirectoryError:
            # A file stands above it, where a folder would have to be.
            raise Unsafe(
                f"error: {plain_text(str(here))} cannot be made: a file stands where one of the folders above it goes"
            ) from None
        break
    if not missing:
        _held(where)
        return where
    if not here.is_dir():
        # The first folder above that is there, which may be a link to one: it must lead to a folder.
        raise Unsafe(f"error: {plain_text(str(here))} is not a folder, so nothing can be made inside it")
    for made in reversed(missing):
        try:
            os.mkdir(made, paths.DIR_MODE)
        except FileExistsError:
            # Made meanwhile by another run: held as the one asked for is, since it was not there to be chosen.
            _held(made)
            continue
        # The umask may take bits from mkdir's mode: the mode is set, never asked for.
        os.chmod(made, paths.DIR_MODE)
    return where


# The scratch file store.replace writes beside a file it replaces, by the file's name: .<name>.<random>.tmp, its
# random part the eight characters tempfile.mkstemp makes one of (its _RandomNameSequence's), so that a person's own
# .<name>.<anything>.tmp is never taken for one.
SCRATCH = ".{name}.*.tmp"
MADE_BY_MKSTEMP = "[a-z0-9_]{8}"


def sweep(path: str | os.PathLike) -> list[str]:
    """Take out each scratch file store.replace left beside path, where a write was killed before its rename; the
    caller holds store.locked(path). Returns their names. Only a regular file of the name mkstemp gives a scratch is
    taken out; a scratch stays until the file's next write."""
    where = Path(os.path.abspath(path))
    made = re.compile(rf"\.{re.escape(where.name)}\.{MADE_BY_MKSTEMP}\.tmp")
    taken = []
    for scratch in sorted(where.parent.glob(SCRATCH.format(name=where.name))):
        if made.fullmatch(scratch.name) is None:
            continue
        try:
            if stat.S_ISREG(os.lstat(scratch).st_mode):
                os.unlink(scratch)
                taken.append(scratch.name)
        except FileNotFoundError:
            continue
    return taken


def write(path: str | os.PathLike, text: str) -> None:
    """path written whole with text: its folder made by `folder`, then replaced as `store.replace` replaces a file, a
    new one at FILE_MODE and one that is there keeping its mode."""
    where = Path(os.path.abspath(path))
    folder(where.parent)
    store.replace(where, text, mode_if_new=paths.FILE_MODE)
