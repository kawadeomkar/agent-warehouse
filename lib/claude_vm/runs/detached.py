"""A run detached: the same program started in a session of its own, which makes its own job, takes its own locks,
writes its own record and keeps its own limit, with no supervisor (docs/architecture/engine.md §16.8).

The starter. `--detach` is the parser's for every verb whose run is a job (the table's column `changes`), and this
module serves them all alike, with nothing of one verb's. The command is parsed, held by the one check and refused at
once where it would be refused, as any command is (cli/main.py, cli/dispatch.py); then `start` starts the same program
with the same values, the words before the verb `--detached-run` and `--attended 1` where it was given, through
runs/children.py's `start_detached`: in a session of its own, its standard input /dev/null, its standard output and
error the write end of one pipe, no other descriptor and never a lock, and no job's marker, since it makes its own job
and marks its own children. Its environment is the caller's, the proxy's variables (HTTPS_PROXY, ALL_PROXY, NO_PROXY
and their lower-case forms) among them, since a proxy that needs a user and a password is given there alone
(network_proxy refuses one), with the program as it was started handed on (schema/printer.py's `handed`), so that the
lines the run prints say it: the run is the person's own command, as it would be at their terminal, and the allow list
of today's MCP server (lib/claude_vm/mcp/environment.py) stays that server's own. `detach` then reads what the run
says, within STARTING seconds: each line it says before it is under way is said again on the starter's standard
error, as it came (a refusal, a lock another run holds among them, which leaves no job, or a line the printer would not
write); the line `started <job id>` ends the read, and the starter prints the job's ID and the two lines that follow it
and stop it, each a whole line of the printer's, and exits 0. A run that ends before it is under way ends the starter
with its status, 1 where it said nothing and ended otherwise. With no `started` in time, or at a Ctrl-C of the
starter's, the run is ended with everything under it (claude_vm.proc, never a signal to its group alone), and the
starter says so and exits 1, or 130 for the Ctrl-C.

The run. cli/dispatch.py calls `begin` where `--detached-run` was given, before the verb's handler; the handler's run
goes through runs.run as any run does, which asks `running`: the job is made with the starter `detach` (an MCP server's
own, where the server named itself, I3) and not attached, its locks taken by the run itself, its text log opened (the
job's own folder where LOG_DIR is empty, which nobody could follow otherwise), and then `under_way` writes `started
<id>` on its standard output and points its standard output and error at that log, or /dev/null where there is none.
From there it runs with no terminal: it asks nothing (runs/ask.py), and signs in and opens pages only where
`--attended 1` was given. A starter that has gone by then is no reason to stop: the run outlives it, the terminal and
an MCP server.

The limit is the setting run_detached_limit, in minutes (120 by default, 0 for none), which a verb gives runs.run as
seconds (`limit_of`), counted by the run itself on the monotonic clock from its ladder's start (runs/attached.py): at
it the run stops as a SIGTERM stops it, its record `failed` with the deadline as why, and the record's `deadline` says
when, by the computer's clock. A SIGKILL leaves no exit record: the job reads `lost` once its lock is free.

Standard library only; select, subprocess and claude_vm.proc are imported inside the functions that use them.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol, TextIO

from claude_vm.runs import children
from claude_vm.schema import patterns, printer
from claude_vm.schema.words import EXITS
from claude_vm.text import plain_text

if TYPE_CHECKING:
    from claude_vm.runs.job import Job

# How long the starter waits for the run to say it is under way, in seconds: a Python started, the table read, the
# fleet's settings and the run's locks taken, none of which waits.
STARTING = 60
# How long a run that has closed its answer is given to end, before the starter ends it.
SETTLE = 5
# What the run says once it is under way, with its job's ID.
STARTED = "started"
# The most the starter holds of one line the run says before it says the line again.
MOST = 65536
# What the starter says where the run did not get under way.
LATE = "error: the detached run was not under way within {within:g} s of its start, and was ended with what it started"
SILENT = "error: the detached run ended before it was under way ({how}), and said nothing"
STOPPED = "aw: the detached run was ended before it was under way: Ctrl-C"


class _Started(Protocol):
    """What the starter reads of the run children.start_detached started: named so, since a child is started by
    children.py alone (test_aw_children.py's scan)."""

    pid: int
    returncode: int | None

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


# Whether this process is a detached run, which cli/dispatch.py's begin says once `--detached-run` was given.
_RUNNING = False


def begin() -> None:
    """This process is a detached run: runs.run makes its job detached and says when it is under way."""
    global _RUNNING
    _RUNNING = True


def running() -> bool:
    """Whether this process is a detached run (begin)."""
    return _RUNNING


def limit_of(minutes: object) -> float:
    """The setting run_detached_limit's minutes as the seconds runs.Draft takes: 0, no limit, stays 0."""
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 0:
        raise ValueError("run_detached_limit is a whole number of minutes, 0 for none")
    return minutes * 60.0


# ---------------------------------------------------------------- the starter ----


def argv_of(program: str, verb: str, run: object, values: Mapping[str, object]) -> list[str]:
    """The detached run's command: this Python, isolated, the program as this one was started by its path, and the
    printer's words of verb with values and the run's own words (-v, --set), after `--detached-run` and, where it was
    given, `--attended 1`; the host's flag only where the values give one, as they were typed."""
    given = dict(values)
    verbosity = getattr(run, "verbosity", 0)
    settings = list(getattr(run, "set", ()))
    # Only where given, as the parser took them: a verb that has neither option is never handed one.
    if verbosity:
        given["verbosity"] = verbosity
    if settings:
        given["set"] = settings
    before: dict[str, object] = {"detached_run": True}
    if getattr(run, "attended", False) is True:
        before["attended"] = "1"
    words = printer.argv(verb, given, refused=False, host_flag="", before=before)
    return [sys.executable, "-I", os.path.abspath(program), *words[1:]]


def start(
    program: str,
    verb: str,
    run: object,
    values: Mapping[str, object],
    env: Mapping[str, str],
    *,
    within: float = STARTING,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """verb with values started detached from the program at program, in env with the program as started handed on;
    the starter's exit status (detach)."""
    return detach(argv_of(program, verb, run, values), {**env, **printer.handed()}, within=within, out=out, err=err)


def detach(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    within: float = STARTING,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """argv started as a detached run (children.start_detached), read until it says it is under way, within seconds:
    then its job's ID and the lines that follow and stop it printed on out, and 0; each line it says before that said
    again on err; a run that ended first, its status (1 where it said nothing and ended otherwise); none in time, the
    run ended with its tree, said, and 1; a Ctrl-C meanwhile, the same, and 130."""
    said = sys.stdout if out is None else out
    told = sys.stderr if err is None else err
    reading, writing = os.pipe()
    try:
        try:
            process = children.start_detached(argv, env, writing)
        finally:
            os.close(writing)
        try:
            return _answered(process, reading, within, said, told)
        except KeyboardInterrupt:
            _end(process)
            _say(told, STOPPED)
            return EXITS["cancelled"]
    finally:
        os.close(reading)


def _answered(process: _Started, reading: int, within: float, out: TextIO, err: TextIO) -> int:
    import select

    end = time.monotonic() + within
    held, said = b"", False
    while True:
        left = end - time.monotonic()
        if left <= 0:
            _end(process)
            _say(err, LATE.format(within=within))
            return EXITS["refused"]
        if not select.select([reading], [], [], left)[0]:
            continue
        chunk = os.read(reading, MOST)
        if not chunk:
            if held:
                _say(err, _decoded(held))
            return _ended(process, said=said or bool(held), err=err)
        held += chunk
        while b"\n" in held or len(held) >= MOST:
            line, _, held = held.partition(b"\n") if b"\n" in held else (held, b"", b"")
            if line in (b"", b"\r"):
                # The newline under_way writes before its word, which ends a line said without one.
                continue
            job_id = _started(line)
            if job_id is not None:
                _under_way_said(job_id, out)
                return EXITS["done"]
            _say(err, _decoded(line))
            said = True


def _started(line: bytes) -> str | None:
    """The job's ID where line is the run's `started <id>`, else None."""
    word, _, job_id = line.decode("ascii", "replace").partition(" ")
    return job_id if word == STARTED and patterns.full("job_id", job_id) else None


def _decoded(line: bytes) -> str:
    return plain_text(line.decode("utf-8", "replace").rstrip("\r"))


def _say(err: TextIO, line: str) -> None:
    with contextlib.suppress(Exception):
        print(line, file=err, flush=True)


def _under_way_said(job_id: str, out: TextIO) -> None:
    """The job's ID, then the line that follows its log and the line that stops it, each a whole line of the printer's,
    each printed by itself."""
    print(job_id, file=out)
    print(printer.line("logs", {"job": job_id, "follow": True}, refused=False), file=out)
    print(printer.line("cancel", {"job": job_id}, refused=False), file=out, flush=True)


def _ended(process: _Started, *, said: bool, err: TextIO) -> int:
    """The status of a run that closed its answer before it was under way, once it has ended; one that does not end
    within SETTLE seconds is ended with its tree."""
    import subprocess

    try:
        status = process.wait(timeout=SETTLE)
    except subprocess.TimeoutExpired:
        _end(process)
        status = process.returncode
    if status == 0:
        return EXITS["done"]
    if not said:
        _say(err, SILENT.format(how=_how(status)))
    return status if isinstance(status, int) and 0 < status < 256 else EXITS["refused"]


def _how(status: object) -> str:
    """How a run ended, as SILENT says it: its exit status, or the signal that killed it."""
    if isinstance(status, int) and status < 0:
        with contextlib.suppress(ValueError):
            return f"killed by {signal.Signals(-status).name}"
        return f"killed by signal {-status}"
    return f"exit {status}"


def _end(process: _Started) -> None:
    """The run ended with everything under it, each found before any is signalled (claude_vm.proc): never a signal to
    its group alone, which a process that left the group would outlive."""
    from claude_vm import proc

    if process.poll() is None:
        proc.end(process.pid)
    process.wait()


# ---------------------------------------------------------------- the run ----


def under_way(job: Job) -> None:
    """The detached run's word that it is under way, `started <id>` on a line of its own on its standard output, then
    its standard output and error pointed at its text log (runs/textlog.py), or /dev/null where it has none: from here
    nothing it says reaches its starter. A starter that has gone is no reason to stop."""
    from claude_vm.runs import textlog

    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()
    # On a line of its own: a newline first ends whatever was said before without one, which would hide the word.
    word = f"\n{STARTED} {job.id}\n".encode("ascii")
    with contextlib.suppress(OSError):
        view = memoryview(word)
        while view:
            view = view[os.write(1, view) :]
    log = textlog.of(job)
    target = log.fd if log is not None else None
    null = os.open(os.devnull, os.O_WRONLY) if target is None else None
    try:
        for fd in (1, 2):
            os.dup2(null if target is None else target, fd, inheritable=os.get_inheritable(fd))
    finally:
        if null is not None:
            os.close(null)
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(line_buffering=True)
