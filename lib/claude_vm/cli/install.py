"""aw install's handler (card H18; docs/architecture/engine.md §16.3): in a checkout, the launcher's own work as a run.

`install` has one home, the launcher's `install_env` at the checkout's top (the launcher's paragraph of §16.3): the
locked packages synced into the environment `tools/env-run` finds, then git's pre-commit hook. In a checkout (D1's tell,
fleet/paths.py's `kind`: a `justfile` and a `.git` at the program's root) this handler runs it as a run like any other,
through `runs.run`, under the lock the verb's row names, the fleet's exclusively, so that it is refused, naming the run
that holds it, while any other run that changes something lives, as today's `just install` is under the run lock; and
the job it makes has its record and its text log. The run's one step is a child started through `runs/children.py`,
which marks it with the job's ID: `/bin/sh <checkout>/aw --install-env`, and `--lock` where it was given, the
launcher's one word of its own, which it refuses but from a child that carries that marker. Each line the child says
is an event of the run, which its text log writes, and is said on the stream the child said it on, but in a detached
run, whose output is its log already; the run's outcome is the child's: `succeeded`, or `failed` with EXITS' failed.

Before the run, and with no job made: `--version`, `--newer`, `--rollback` and `--without` are refused in a checkout,
in one sentence: they are the installed program's, after 3.0 (H14, H15), and a checkout takes a newer version with
`git pull` and then `aw install`. An installed program (its `release.json`) is brought to a release by H14's
`installed`, which no program has before H14 exists: refused as not built, naming the card. `--detach` is C13's, from
the row's `changes`, with nothing of this module's. Nothing here installs a Galaxy collection or writes anything but
what the child writes: the environment, the checkout's hook and the run's job.

Standard library, schema/ and fleet/paths.py at import; runs/ and asyncio inside the handler.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from claude_vm.fleet import paths
from claude_vm.schema.printer import line, said
from claude_vm.schema.words import EXITS, Line

if TYPE_CHECKING:
    from asyncio import StreamReader

    from claude_vm.runs.attached import Context

# The program's root, where fleet/paths.py tells a checkout from an installed release: the launcher it runs is there.
PROGRAM_ROOT = Path(__file__).resolve().parents[3]
# The shell that runs the launcher, by its path, as the launcher's own first line names it.
SHELL = "/bin/sh"
# The launcher's one word of its own, which only a child that carries a job's marker may give it.
INSTALL_ENV = "--install-env"
# The options of the installed program (H14, H15), each by the field the handler receives it by, as typed.
INSTALLED = {"version": "--version", "newer": "--newer", "rollback": "--rollback", "without": "--without"}
INSTALLED_CARDS = "cards H14 and H15"
# How much of a line the child says is read at once, and so the longest one said as one event.
CHUNK = 64 * 1024

IN_A_CHECKOUT = (
    "error: {flags} {are} the installed program's, after 3.0 ({cards}): a checkout gets a newer version with git pull,"
    " and then {line}"
)
NOT_YET = "error: {line} for an installed program is not built yet (card H14 builds it)"
NEITHER = (
    "error: {root} is neither a checkout of agent-warehouse (a justfile and a .git) nor an installed release"
    " (release.json): {line} has nothing to install"
)


def _refused(sentence: str) -> int:
    print(sentence, file=sys.stderr)
    return EXITS["refused"]


def _given(values: Mapping[str, object], field: str) -> bool:
    return values.get(field) not in (None, False, "")


def install(run: Any, **values: object) -> int:
    """install's handler (words.py's row): refused before the run as the module says, then the launcher's install_env
    as its run, under the fleet's lock. The exit status."""
    again = Line("install", {})
    found = paths.kind(PROGRAM_ROOT)
    if found == "installed":
        return _refused(said(NOT_YET, {"line": again}))
    if found != "checkout":
        return _refused(said(NEITHER, {"root": PROGRAM_ROOT, "line": again}))
    flags = [typed for field, typed in INSTALLED.items() if _given(values, field)]
    if flags:
        are = "is" if len(flags) == 1 else "are"
        flagged = ", ".join(flags)
        return _refused(said(IN_A_CHECKOUT, {"flags": flagged, "are": are, "cards": INSTALLED_CARDS, "line": again}))
    environ = dict(os.environ)
    try:
        home = paths.home(PROGRAM_ROOT, environ)
    except paths.Refused as error:
        return _refused(f"error: {error}")

    from claude_vm import runs
    from claude_vm.runs import textlog

    lock = values.get("lock") is True
    away = getattr(run, "detached_run", False) is True
    argv = [SHELL, str(PROGRAM_ROOT / "aw"), INSTALL_ENV, *(["--lock"] if lock else [])]

    async def body(ctx: Context) -> int:
        import asyncio

        from claude_vm.runs import children

        process = await children.start(
            ctx.job,
            argv,
            children.GROUP,
            environ,
            asyncio.subprocess.DEVNULL,
            asyncio.subprocess.PIPE,
            asyncio.subprocess.PIPE,
            children.PROGRAM,
            cwd=str(PROGRAM_ROOT),
        )
        assert process.stdout is not None and process.stderr is not None
        out = None if away else sys.stdout
        err = None if away else sys.stderr
        await asyncio.gather(_relay(ctx, process.stdout, out), _relay(ctx, process.stderr, err))
        status = await process.wait()
        return EXITS["done"] if status == 0 else EXITS["failed"]

    draft = runs.Draft(
        paths.jobs_dir(home),
        "install",
        line("install", values, refused=True),
        None,
        [],
        {"kind": "detach" if away else "terminal"},
        True,
        values=values,
        logs=textlog.logs(home, run),
    )
    return runs.run(draft, body)


async def _relay(ctx: Context, stream: StreamReader, shown: TextIO | None) -> None:
    """Each line stream gives, to its end: an event of the run, and said on shown where there is one."""
    kept = b""
    while True:
        read = await stream.read(CHUNK)
        if not read:
            break
        kept += read
        *lines, kept = kept.split(b"\n")
        for each in lines:
            _said(ctx, each, shown)
        while len(kept) >= CHUNK:
            _said(ctx, kept[:CHUNK], shown)
            kept = kept[CHUNK:]
    if kept:
        _said(ctx, kept, shown)


def _said(ctx: Context, raw: bytes, shown: TextIO | None) -> None:
    text = raw.decode("utf-8", "replace").rstrip("\r")
    ctx.emit("report", None, text)
    if shown is not None:
        print(text, file=shown, flush=True)
