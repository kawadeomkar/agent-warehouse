"""aw install's handler (card H18; docs/architecture/engine.md §16.3): in a checkout, the launcher's own work as a run.

It runs the launcher's `install_env` as one child under the fleet's lock, its lines the run's events and its end the
run's outcome; the installed program's options are refused in a checkout before any job is made, and an installed
program as not built yet (H14)."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
from collections.abc import Awaitable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from claude_vm.fleet import paths
from claude_vm.schema.printer import line, said
from claude_vm.schema.words import EXITS, Line

if TYPE_CHECKING:
    from asyncio import StreamReader
    from asyncio.subprocess import Process

    from claude_vm.runs.attached import Context

PROGRAM_ROOT = Path(__file__).resolve().parents[3]
SHELL = "/bin/sh"
INSTALL_ENV = "--install-env"
INSTALLED = {"version": "--version", "newer": "--newer", "rollback": "--rollback", "without": "--without"}
INSTALLED_CARDS = "cards H14 and H15"
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
    """Whether field was typed: the dispatch hands only what was given, so an empty value (`--version=`) was."""
    return values.get(field) not in (None, False)


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

        if ctx.cancelled.is_set():
            return EXITS["cancelled"]
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
        relayed = asyncio.gather(_relay(ctx, process.stdout, out), _relay(ctx, process.stderr, err))
        ended = asyncio.ensure_future(_ended(process, relayed))
        stopped = asyncio.ensure_future(ctx.cancelled.wait())
        try:
            await asyncio.wait((ended, stopped), return_when=asyncio.FIRST_COMPLETED)
            if not ended.done():
                # §16.3
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGINT)
            status = await ended
        finally:
            stopped.cancel()
        if ctx.cancelled.is_set():
            return EXITS["cancelled"]
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


async def _ended(process: Process, relayed: Awaitable[object]) -> int:
    """The child's exit status, once both its streams are said to their end."""
    await relayed
    return await process.wait()


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
