"""A run in the command's own process, and its stated end (lib/claude_vm/runs/attached.py and runs/__init__.py's run,
lib/claude_vm/proc.py's end_under; docs/architecture/engine.md §16.8).

Each run here is a child process of the test's, made by runs.run as a verb makes one, whose body starts four stand-in
Ansible children through runs/children.py, each in a session of its own: one that ends on SIGINT, one that ignores it,
one that puts a grandchild in a session of its own, as ansible-core's task worker is (tests/unit/test_processes.py's
HOLDS), and one that records each SIGINT, SIGTERM and SIGHUP it gets, with when, and ends on none. Each way it ends is
held to its exit record, its process's status, the signals the recorder got, and the process table after it, read
once the run has ended and before anything of the test's own ends what is left. The rule with a test of its own: every
way out of a run but SIGKILL writes an exit record, and the ways walk every signal whose handler is the ladder's while a
body runs, and every outcome. SIGKILL writes none, and the job reads lost. Ctrl-C twice ends the tree from the handler
while the loop is held; SIGHUP and SIGTERM reach each child as themselves; a call still in a thread holds the process
no longer than the ladder takes, on a daemon thread of the ladder's own that blocks the signals it takes, so the
interrupts stay held off while the table is read; an Ansible child started inside a shield has the Ctrl-C's SIGINT
once the shield is done; a SIGTERM after a Ctrl-C is what the record says, within SETTLE; one Ctrl-C at a question ends
the run there; a ladder that cannot be built leaves no job; Python's and asyncio's own reports stay off the terminal. A
shielded phase finishes before the stop; at a pseudo-terminal the echo is back after each end; a signal ignored on the
way in stays ignored; a child meant to outlive the run, as a page's opener is, lives on unmarked after every end; a
child the terminal stopped for asking a question there is said within a second and ended, and its run ends; and
end_under ends what is under a process, never the process, nor what it is told to leave.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple

import harness
import pytest
from aw_kit import environment
from harness import REPO
from test_processes import HOLDS, _by_argv, _until

from claude_vm import proc
from claude_vm.fleet import paths
from claude_vm.runs import attached, rundir
from claude_vm.runs import job as jobs

LIB = REPO / "lib"

# A run, in a child process, as a verb makes one: argv is the lib folder, the jobs folder, the body's way, the grace in
# seconds, the test's folder, whether the body turns the terminal's echo off, and the stand-in of HOLDS's shape. Its
# body starts the four stand-ins, says what it started as one line of JSON, and then ends its way: waits (and is ended
# from outside), stops at the Ctrl-C, raises, returns a status, holds a shield across the Ctrl-C, waits on a call in a
# thread, holds the loop, or asks a question.
RUN = r"""
import asyncio, concurrent.futures, json, os, signal, sys, termios, threading, time
lib, jobs_dir, way, grace, folder, quiet, holds = sys.argv[1:8]
sys.path.insert(0, lib)
from pathlib import Path
from claude_vm import proc, runs
from claude_vm.runs import attached, children

attached.STOP_GRACE = float(grace)
folder = Path(folder)
ENDS = "import time\nprint('ready', flush=True)\ntime.sleep(120)\n"
IGNORES = (
    "import signal, time\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\nprint('ready', flush=True)\ntime.sleep(120)\n"
)
# Each SIGINT, SIGTERM and SIGHUP it gets written to the file it is given, with when, and none of them ends it.
RECORDS = (
    "import signal, sys, time\n"
    "out = open(sys.argv[1], 'a')\n"
    "def got(signum, frame):\n"
    "    out.write(signal.Signals(signum).name + ' ' + repr(time.time()) + '\\n')\n"
    "    out.flush()\n"
    "for each in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):\n"
    "    signal.signal(each, got)\n"
    "print('ready', flush=True)\n"
    "while True:\n"
    "    time.sleep(1)\n"
)
PIPE, DEVNULL = asyncio.subprocess.PIPE, asyncio.subprocess.DEVNULL
# How long a shield that holds an Ansible child lasts: well inside its test's grace.
SHIELD = 1.5
# A page's opener, meant to outlive the run: it writes its pid, its session and the job's marker it carries into the
# file its environment names - never its command line, which the test's look for what a run left reads - then waits.
OPENS = (
    "import json, os, time\n"
    "out = os.environ['OPENED']\n"
    "said = {'pid': os.getpid(), 'session': os.getsid(0), 'marker': os.environ.get('AGENT_WAREHOUSE_JOB')}\n"
    "open(out + '.part', 'w').write(json.dumps(said))\n"
    "os.rename(out + '.part', out)\n"
    "time.sleep(120)\n"
)
# A child that asks a question at the terminal, as the ssh of a person's own ProxyJump asks a passphrase: it writes
# when it asks into the file named, then reads the terminal.
ASKS = (
    "import sys, time\n"
    "open(sys.argv[1], 'w').write(repr(time.time()))\n"
    "with open('/dev/tty') as tty:\n"
    "    tty.readline()\n"
)

if way == "slow-table":
    # The process table read slowly once, when the test asks, with a mark where a read begins inside another.
    table, depth = proc._table, [0]

    def slow():
        depth[0] += 1
        try:
            if depth[0] > 1:
                (folder / "nested").write_text("a read inside a read")
            if (folder / "slow").exists():
                (folder / "slow").unlink()
                (folder / "reading").write_text("reading")
                time.sleep(1.5)
            return table()
        finally:
            depth[0] -= 1

    proc._table = slow
if way == "no-loop":

    def failing():
        raise OSError(24, "Too many open files")

    asyncio.new_event_loop = failing


async def stand_in(ctx, code, name, stdout):
    argv = [sys.executable, "-I", "-c", code, str(folder / name)]
    return await children.start(
        ctx.job, argv, children.SESSION, dict(os.environ), DEVNULL, stdout, DEVNULL, children.ANSIBLE
    )


async def ready(ctx, code, name):
    process = await stand_in(ctx, code, name, PIPE)
    await asyncio.wait_for(process.stdout.readline(), 60)
    return process.pid


def blocked():
    (folder / "calling").write_text("in a call")
    time.sleep(600)


def probe():
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    return threading.current_thread().daemon, sorted(int(each) for each in mask)


def say(said):
    print(json.dumps(said), flush=True)


async def prompted(ctx, said):
    if way == "prompt-late":
        say(said)
        await ctx.cancelled.wait()
        try:
            await ctx.typed(0)
        except asyncio.CancelledError:
            (folder / "asked").write_text("not asked")
            raise
        (folder / "asked").write_text("asked")
        return 0
    if way == "prompt-raw":
        settings = termios.tcgetattr(0)
        settings[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG)
        termios.tcsetattr(0, termios.TCSANOW, settings)
    say(said)
    got = await ctx.typed(0)
    (folder / "answer").write_bytes(got)
    if way == "prompt-raw" and got == b"\x03":
        # Ctrl-C read as a byte: the prompt sends the run its SIGINT, and asks nothing more.
        os.kill(os.getpid(), signal.SIGINT)
        await ctx.typed(0)
    return 0


async def outlives(ctx, said):
    env = {**os.environ, children.MARKER: ctx.job.id, "OPENED": str(folder / "opened")}
    said["opener"] = children.outlive(ctx.job, [sys.executable, "-I", "-c", OPENS], env).pid
    while not (folder / "opened").exists():
        await asyncio.sleep(0.02)
    say(said)
    if way == "outlive-error":
        raise RuntimeError("the body's own error")
    if way == "outlive-waits":
        await asyncio.sleep(600)
    return 0


async def asks(ctx, said):
    argv = [sys.executable, "-I", "-c", ASKS, str(folder / "asking")]
    asking = await children.start(
        ctx.job, argv, children.GROUP, dict(os.environ), DEVNULL, DEVNULL, DEVNULL, children.SSH
    )
    said["asks"] = asking.pid
    say(said)
    status = await asking.wait()
    (folder / "asked").write_text(str(status))
    return 0 if status == 0 else 1


async def body(ctx):
    said = {"job": ctx.job.id, "pid": os.getpid(), "directory": str(ctx.directory)}
    if way.startswith("prompt"):
        return await prompted(ctx, said)
    if way.startswith("outlive"):
        return await outlives(ctx, said)
    if way == "asks":
        return await asks(ctx, said)
    for name, code in (("ends", ENDS), ("ignores", IGNORES)):
        said[name] = await ready(ctx, code, name)
    process = await stand_in(ctx, holds, "holds", DEVNULL)
    pids = folder / "holds"
    while len(pids.read_text().split() if pids.is_file() else ()) != 2:
        await asyncio.sleep(0.05)
    said["holds"], said["grandchild"] = process.pid, int(pids.read_text().split()[1])
    if way != "shield-ansible":
        said["records"] = await ready(ctx, RECORDS, "records")
    if quiet:
        settings = termios.tcgetattr(0)
        settings[3] &= ~termios.ECHO
        termios.tcsetattr(0, termios.TCSANOW, settings)
    if way == "handlers":
        said["taken"] = sorted(
            int(each)
            for each in signal.valid_signals()
            if isinstance(getattr(signal.getsignal(each), "__self__", None), attached._Ladder)
        )
        said["unraisable"] = getattr(sys.unraisablehook, "__qualname__", "")
        say(said)
        return 0
    if way in ("thread", "thread-error", "slow-table"):
        said["daemon"], said["blocked"] = await asyncio.to_thread(probe)
    if way == "shield":
        async with ctx.shield("the deletion already sent"):
            say(said)
            await asyncio.sleep(6 * float(grace))
            (folder / "shielded").write_text("finished")
        try:
            async with ctx.shield("another deletion"):
                (folder / "another").write_text("begun")
        except asyncio.CancelledError:
            (folder / "another").write_text("not begun")
            raise
        await asyncio.sleep(600)
    if way == "shield-ansible":
        async with ctx.shield("the deletion already sent"):
            said["records"] = await ready(ctx, RECORDS, "records")
            say(said)
            await asyncio.sleep(SHIELD)
            (folder / "shielded").write_text(repr(time.time()))
        return 0
    say(said)
    if way == "error":
        raise RuntimeError("the body's own error")
    if way == "keyboard":
        raise KeyboardInterrupt
    if way == "exit4":
        raise SystemExit(4)
    if way == "cancel":
        raise asyncio.CancelledError
    if way == "unwritable":
        os.chmod(ctx.job.folder, 0o500)
        return 0
    if way == "callback":
        asyncio.get_running_loop().call_soon(lambda: 1 / 0)
        await asyncio.sleep(0.5)
        return 0
    if way == "stops":
        await ctx.cancelled.wait()
        (folder / "stopped").write_text("saw the stop")
        return 0
    if way == "stuck":
        # The loop held by the body once it has seen the stop, through the second Ctrl-C's end and for a while after.
        await ctx.cancelled.wait()
        (folder / "stuck").write_text("holding the loop")
        try:
            time.sleep(30)
        except KeyboardInterrupt:
            (folder / "abandoned").write_text(repr(time.time()))
            time.sleep(8)
        return 0
    if way == "swallows":
        while True:
            try:
                await asyncio.sleep(600)
            except asyncio.CancelledError:
                (folder / "swallowed").write_text("again")
    if way in ("thread", "slow-table"):
        await asyncio.to_thread(blocked)
    if way == "own-pool":
        # A pool of the body's own, whose thread Python waits for at its exit: only the ladder's end at once ends it.
        await asyncio.get_running_loop().run_in_executor(concurrent.futures.ThreadPoolExecutor(1), blocked)
    if way == "thread-error":
        calling = asyncio.ensure_future(asyncio.to_thread(blocked))
        while not (folder / "calling").exists():
            await asyncio.sleep(0.05)
        raise RuntimeError("the body's own error")
    if way.isdigit():
        return int(way)
    await asyncio.sleep(600)


draft = runs.Draft(
    Path(jobs_dir),
    "deploy",
    "aw deploy alpha",
    "pve",
    ["alpha"],
    {"kind": "terminal"},
    True,
    values={"names": ["alpha"]},
    lock_machines=[("pve", "alpha")],
)
status = runs.run(draft, body)
print(json.dumps({"returned": status}), flush=True)
sys.exit(status)
"""

# At a pseudo-terminal: the run as a child in the terminal's foreground group, and once it has ended, whether the
# terminal echoes and how the run ended, as one line of JSON. A Ctrl-C typed reaches both: this one takes it with a
# handler that does nothing, never SIG_IGN, which the run would inherit.
WATCHES = r"""
import json, signal, subprocess, sys, termios
signal.signal(signal.SIGINT, lambda *_: None)
run = subprocess.Popen(sys.argv[1:])
status = run.wait()
print(json.dumps({"echo": bool(termios.tcgetattr(0)[3] & termios.ECHO), "status": status}), flush=True)
"""
REPORT = re.compile(rb'\{"job": "[0-9a-f]{16}"[^}]*\}')
WATCHED = re.compile(rb'\{"echo": (true|false), "status": (-?\d+)\}')
STOPPING = b"aw: stopping"
# How long what a run started may take to be gone once the run has ended: a kill's own end, and its reaping.
GONE = 3


class Way(NamedTuple):
    """One way out of a run: the body's way, the signals sent (each once the run took the one before), the grace, and
    what it leaves: the record's outcome, its exit status and why, the process's status (negative: its death by that
    signal), and the signals the recorder got, by name."""

    body: str
    sent: tuple[int, ...]
    grace: float
    outcome: str
    exit: int
    why: str
    status: int
    recorded: frozenset[str] = frozenset()


UNHANDLED = "an error the run did not handle: RuntimeError: the body's own error"
INT, TERM, HUP = frozenset({"SIGINT"}), frozenset({"SIGTERM"}), frozenset({"SIGHUP"})
# Every way out of a run but SIGKILL.
WAYS = {
    "Ctrl-C once": Way("waits", (signal.SIGINT,), 2, "cancelled", 130, "Ctrl-C", 130, INT),
    "Ctrl-C twice": Way("waits", (signal.SIGINT, signal.SIGINT), 60, "cancelled", 130, "Ctrl-C twice", 130, INT),
    "SIGHUP": Way("waits", (signal.SIGHUP,), 60, "cancelled", 129, "SIGHUP", -signal.SIGHUP, HUP),
    "SIGTERM": Way("waits", (signal.SIGTERM,), 60, "cancelled", 143, "SIGTERM", -signal.SIGTERM, TERM),
    "an error": Way("error", (), 60, "failed", 1, UNHANDLED, 1),
    "returns 0": Way("0", (), 60, "succeeded", 0, "done", 0),
    "returns 3": Way("3", (), 60, "failed", 3, "exit 3", 3),
    "returns 130": Way("130", (), 60, "cancelled", 130, "Ctrl-C", 130),
    "a KeyboardInterrupt of the body's own": Way("keyboard", (), 60, "cancelled", 130, "Ctrl-C", 130),
    "a SystemExit of the body's own": Way("exit4", (), 60, "failed", 4, "exit 4", 4),
    "a CancelledError of the body's own": Way("cancel", (), 60, "failed", 1, "the run's work ended unfinished", 1),
    "Ctrl-C once, the body in a thread": Way("thread", (signal.SIGINT,), 2, "cancelled", 130, "Ctrl-C", 130, INT),
    "Ctrl-C twice, the body in a thread": Way(
        "thread", (signal.SIGINT, signal.SIGINT), 60, "cancelled", 130, "Ctrl-C twice", 130, INT
    ),
    "an error, a thread in a call": Way("thread-error", (), 60, "failed", 1, UNHANDLED, 1),
    "Ctrl-C twice, a call on a pool of the body's own": Way(
        "own-pool", (signal.SIGINT, signal.SIGINT), 60, "cancelled", 130, "Ctrl-C twice", 130, INT
    ),
}


@pytest.fixture
def jobs_dir(home: Path) -> Path:
    """The jobs folder of the test's own fleet home, as fleet/paths.py names it."""
    return paths.jobs_dir(paths.home(REPO))


def _ended(pid: int) -> bool:
    """Whether pid has ended: no such process, or one dead whose parent has not reaped it yet."""
    found = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return not found or found.startswith("Z")


def _tree(said: dict) -> list[int]:
    """What the run's body started, its grandchild among it."""
    return [said[name] for name in ("ends", "ignores", "holds", "grandchild", "records") if name in said]


def _report(out: Path) -> dict | None:
    """What the run said it started, once it has said it."""
    found = REPORT.search(out.read_bytes()) if out.is_file() else None
    return json.loads(found.group(0)) if found else None


def _argv(folder: Path, jobs_dir: Path, way: str, grace: float, quiet: bool = False) -> list[str]:
    return [sys.executable, "-I", "-c", RUN, str(LIB), str(jobs_dir), way, str(grace), str(folder), "1" * quiet, HOLDS]


def _env(home: Path, folder: Path) -> dict[str, str]:
    """The run's environment, its per-user temporary directory a folder of the test's own."""
    temporary = folder / "tmp"
    temporary.mkdir(exist_ok=True)
    return environment(home, {"TMPDIR": str(temporary)})


def _signals(folder: Path) -> list[tuple[str, float]]:
    """Each signal the recorder got, by name, with when, in order."""
    found = folder / "records"
    lines = found.read_text().split("\n") if found.is_file() else []
    return [(name, float(at)) for name, at in (line.split() for line in lines if line)]


def _running(folder: Path) -> list[int]:
    """What the run started that still runs: every process that names the test's folder, and the grandchild."""
    found = [pid for pid in _by_argv(str(folder)) if not _ended(pid)]
    holds = folder / "holds"
    if holds.is_file() and len(holds.read_text().split()) == 2:
        grandchild = int(holds.read_text().split()[1])
        command = subprocess.run(["ps", "-o", "command=", "-p", str(grandchild)], capture_output=True, text=True)
        if "time.sleep(120)" in command.stdout and not _ended(grandchild):
            found.append(grandchild)
    return found


def _left(folder: Path) -> list[int]:
    """What a run that has ended left running, once GONE seconds have let what it ended be gone."""
    deadline = time.monotonic() + GONE
    while (found := _running(folder)) and time.monotonic() < deadline:
        time.sleep(0.1)
    return found


def _end_left(folder: Path) -> None:
    """Whatever a run left, ended: every process that names the test's folder, and the grandchild."""
    for pid in _running(folder):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


def _ignoring_hangups() -> None:
    """In the run, before it starts: the interrupts at their defaults, then SIGHUP ignored, as nohup leaves it."""
    harness._default_signals()
    signal.signal(signal.SIGHUP, signal.SIG_IGN)


@contextlib.contextmanager
def _run(
    home: Path,
    folder: Path,
    jobs_dir: Path,
    way: str,
    grace: float,
    preexec: Callable[[], None] = harness._default_signals,
) -> Iterator[tuple[subprocess.Popen, dict]]:
    """A run in a child process, its standard output and error in the test's folder, and what it said it started.
    Where the run has ended by the block's end, nothing it started may still run: that is read before anything of the
    test's own ends what is left, and fails the test. Where it still runs, or the block failed, it is ended whole, and
    anything it left ended."""
    with open(folder / "out", "wb") as stdout, open(folder / "err", "wb") as stderr:
        process = subprocess.Popen(
            _argv(folder, jobs_dir, way, grace),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=_env(home, folder),
            preexec_fn=preexec,  # noqa: PLW1509 - only signal, safe beside xdist's thread
        )
    try:
        _until(lambda: _report(folder / "out") is not None or process.poll() is not None, "the run's report", 60)
        said = _report(folder / "out")
        if said is None:
            pytest.fail(f"the run said nothing it started:\n{(folder / 'err').read_text()}")
        yield process, said
    except BaseException:
        if process.poll() is None:
            harness._end(process)
        _end_left(folder)
        raise
    if process.poll() is None:
        harness._end(process)
        _end_left(folder)
        return
    left = _left(folder)
    _end_left(folder)
    assert left == [], f"the run left {left} running:\n{(folder / 'err').read_text()}"


def _send(process: subprocess.Popen, sent: tuple[int, ...], err: Path) -> None:
    """Each signal to the run, the next once the run has taken the one before: a second Ctrl-C once it says it is
    stopping. Each lands while the run waits and nothing forks."""
    for at, signum in enumerate(sent):
        if at:
            _until(lambda: STOPPING in err.read_bytes(), "the run's first Ctrl-C taken")
        os.kill(process.pid, signum)


def _record(jobs_dir: Path, said: dict) -> dict:
    job = jobs.find(jobs_dir, said["job"])
    assert job is not None, said
    return job.read()


def _ended_as(jobs_dir: Path, said: dict) -> tuple[str, int, str]:
    record = _record(jobs_dir, said)
    return record["outcome"], record["exit"], record["why"]


# ---------------------------------------------------------------- every way out ----


@pytest.mark.parametrize("way", WAYS)
def test_every_way_out_of_a_run_but_sigkill_writes_its_exit_record_and_leaves_nothing_running(
    way, home, jobs_dir, tmp_path
) -> None:
    given = WAYS[way]
    with _run(home, tmp_path, jobs_dir, given.body, given.grace) as (process, said):
        if given.sent:
            # A body that waits holds all of it until the signals come; one that returns or raises may have ended.
            assert all(not _ended(pid) for pid in _tree(said)), said
        _send(process, given.sent, tmp_path / "err")
        process.wait(timeout=90)
    assert process.returncode == given.status, (tmp_path / "err").read_text()
    assert _ended_as(jobs_dir, said) == (given.outcome, given.exit, given.why)
    job = jobs.find(jobs_dir, said["job"])
    assert job is not None and jobs.state(job) == given.outcome
    assert not Path(said["directory"]).exists()
    said_on_err = (tmp_path / "err").read_bytes()
    assert (STOPPING in said_on_err) is (signal.SIGINT in given.sent)
    # A hangup and a kill reach each child as themselves, from the handler; a Ctrl-C as SIGINT; any other end only as
    # the SIGKILL nothing records.
    assert {name for name, _ in _signals(tmp_path)} == given.recorded


def test_the_ladder_takes_exactly_the_signals_the_ways_walk_and_the_ways_walk_every_outcome(
    home, jobs_dir, tmp_path
) -> None:
    # The handlers as a running body finds them, whatever TAKEN says: one the ladder takes that no way walks fails here.
    with _run(home, tmp_path, jobs_dir, "handlers", 60) as (process, said):
        process.wait(timeout=60)
    assert process.returncode == 0
    assert set(said["taken"]) == {signum for given in WAYS.values() for signum in given.sent}
    assert {given.outcome for given in WAYS.values()} == set(jobs.OUTCOMES)
    # Python's report of an end it dropped is the ladder's while it runs.
    assert said["unraisable"] == attached._dropping.__qualname__ + ".<locals>.hook"


def test_a_record_that_cannot_be_written_is_said_and_the_job_reads_lost(home, jobs_dir, tmp_path) -> None:
    try:
        with _run(home, tmp_path, jobs_dir, "unwritable", 60) as (process, said):
            process.wait(timeout=60)
        assert process.returncode == 0
        assert (
            f"aw: the end of the run could not be written into job {said['job']}'s record"
            in (tmp_path / "err").read_text()
        )
        job = jobs.find(jobs_dir, said["job"])
        assert job is not None and jobs.state(job) == "lost"
    finally:
        for folder in Path(jobs_dir).iterdir() if Path(jobs_dir).is_dir() else ():
            os.chmod(folder, 0o700)


def test_sigkill_writes_no_exit_record_and_the_job_reads_lost(home, jobs_dir, tmp_path) -> None:
    with _run(home, tmp_path, jobs_dir, "waits", 60) as (process, said):
        remembered = proc.snapshot(process.pid)
        try:
            os.kill(process.pid, signal.SIGKILL)
            process.wait(timeout=60)
            assert process.returncode == -signal.SIGKILL
            record = _record(jobs_dir, said)
            assert record.get("outcome") is None and record.get("exit") is None
            job = jobs.find(jobs_dir, said["job"])
            assert job is not None and jobs.state(job) == "lost"
            # What it started is left, for the next command to find: X39's question (card C5); and so is its
            # directory, which no other process's rundir.remove takes (review/code/C3.md 1).
            assert not _ended(said["ignores"])
            assert Path(said["directory"]).is_dir()
            with pytest.raises(RuntimeError, match="not this process's"):
                rundir.remove(job)
            assert Path(said["directory"]).is_dir()
        finally:
            proc.end_left(remembered)
            shutil.rmtree(said["directory"], ignore_errors=True)


# ---------------------------------------------------------------- Ctrl-C ----


def test_ctrl_c_once_lets_the_body_stop_and_gives_what_ignores_it_the_grace(home, jobs_dir, tmp_path) -> None:
    grace = 6
    with _run(home, tmp_path, jobs_dir, "stops", grace) as (process, said):
        sent = time.monotonic()
        os.kill(process.pid, signal.SIGINT)
        # The body saw the stop and returned; the child that ends on SIGINT ended, and the child of HOLDS's shape.
        _until(lambda: (tmp_path / "stopped").is_file(), "the body's stop")
        _until(lambda: _ended(said["ends"]) and _ended(said["holds"]), "the children that end on SIGINT gone")
        # The stop is recorded: whoever reads the job through the grace reads it cancelling.
        job = jobs.find(jobs_dir, said["job"])
        assert job is not None and jobs.state(job) == "cancelling"
        stop = job.read()["stop"]
        assert stop["by"] == attached.BY_TERMINAL and isinstance(stop["at"], float), stop
        # What ignores SIGINT, and what left the group, live until the grace's end, and then no longer.
        time.sleep(1)
        assert time.monotonic() - sent < grace
        assert not _ended(said["ignores"]) and not _ended(said["grandchild"])
        process.wait(timeout=60)
        assert time.monotonic() - sent >= grace
    assert process.returncode == 130
    assert _ended_as(jobs_dir, said) == ("cancelled", 130, "Ctrl-C")
    err = (tmp_path / "err").read_text()
    assert "aw: stopping: what the run started has 6.0 s to end" in err, err


def test_ctrl_c_twice_ends_the_tree_at_once(home, jobs_dir, tmp_path) -> None:
    with _run(home, tmp_path, jobs_dir, "waits", 60) as (process, said):
        _send(process, (signal.SIGINT,), tmp_path / "err")
        _until(lambda: STOPPING in (tmp_path / "err").read_bytes(), "the first Ctrl-C taken")
        assert not _ended(said["ignores"])
        second = time.monotonic()
        os.kill(process.pid, signal.SIGINT)
        process.wait(timeout=60)
        # Long before the grace's 60 s.
        assert time.monotonic() - second < 30
    assert process.returncode == 130


def test_ctrl_c_twice_ends_the_tree_from_the_handler_itself_while_the_loop_is_held(home, jobs_dir, tmp_path) -> None:
    with _run(home, tmp_path, jobs_dir, "stuck", 60) as (process, said):
        os.kill(process.pid, signal.SIGINT)
        _until(lambda: (tmp_path / "stuck").is_file(), "the body holding the loop after the first Ctrl-C")
        os.kill(process.pid, signal.SIGINT)
        _until(lambda: (tmp_path / "abandoned").is_file(), "the second Ctrl-C's end raised where the body was")
        # The body holds the loop for seconds more, so nothing but the handler can have ended the tree by now.
        _until(lambda: all(_ended(pid) for pid in _tree(said)), "the tree ended from the handler", timeout=3)
        assert process.poll() is None
        process.wait(timeout=60)
    assert process.returncode == 130
    assert _ended_as(jobs_dir, said) == ("cancelled", 130, "Ctrl-C twice")


def test_a_sigterm_after_a_ctrl_c_whose_body_has_returned_ends_the_run_as_that_signal_within_settle(
    home, jobs_dir, tmp_path
) -> None:
    grace = 20
    with _run(home, tmp_path, jobs_dir, "stops", grace) as (process, said):
        os.kill(process.pid, signal.SIGINT)
        _until(lambda: (tmp_path / "stopped").is_file(), "the body's stop")
        time.sleep(0.5)
        sent = time.monotonic()
        os.kill(process.pid, signal.SIGTERM)
        process.wait(timeout=60)
        took = time.monotonic() - sent
    # The recorder, which ends on neither, was killed SETTLE seconds after the SIGTERM, not at the Ctrl-C's grace.
    assert took < attached.SETTLE + 2 < grace, took
    assert process.returncode == -signal.SIGTERM
    assert _ended_as(jobs_dir, said) == ("cancelled", 143, "SIGTERM")
    assert {name for name, _ in _signals(tmp_path)} == {"SIGINT", "SIGTERM"}


def test_a_shielded_phase_finishes_before_the_stop_and_none_begins_after_it(home, jobs_dir, tmp_path) -> None:
    grace = 1
    with _run(home, tmp_path, jobs_dir, "shield", grace) as (process, said):
        sent = time.monotonic()
        os.kill(process.pid, signal.SIGINT)
        finishing = b"aw: stopping once the deletion already sent is finished"
        _until(lambda: finishing in (tmp_path / "err").read_bytes(), "the run's line that the phase finishes first")
        # Past the grace, inside the shield: nothing of the tree is ended while the phase runs.
        time.sleep(max(0.0, sent + 2 * grace - time.monotonic()))
        assert not (tmp_path / "shielded").exists()
        assert not _ended(said["ignores"]) and not _ended(said["grandchild"])
        process.wait(timeout=60)
    assert (tmp_path / "shielded").read_text() == "finished"
    assert (tmp_path / "another").read_text() == "not begun"
    assert process.returncode == 130
    assert _ended_as(jobs_dir, said) == ("cancelled", 130, "Ctrl-C")


def test_an_ansible_child_started_inside_a_shield_has_the_ctrl_c_s_sigint_once_the_shield_is_done(
    home, jobs_dir, tmp_path
) -> None:
    with _run(home, tmp_path, jobs_dir, "shield-ansible", 6) as (process, said):
        os.kill(process.pid, signal.SIGINT)
        # The Ansible child started before the shield has its SIGINT at once, and ends of it, inside the phase.
        _until(lambda: _ended(said["ends"]), "the child started outside the shield ended by its SIGINT")
        assert not (tmp_path / "shielded").exists()
        assert _signals(tmp_path) == []
        process.wait(timeout=60)
    shielded = float((tmp_path / "shielded").read_text())
    got = _signals(tmp_path)
    assert {name for name, _ in got} == {"SIGINT"}, got
    assert min(at for _, at in got) >= shielded, (got, shielded)
    assert process.returncode == 130
    assert _ended_as(jobs_dir, said) == ("cancelled", 130, "Ctrl-C")


def test_a_signal_ignored_on_the_way_in_stays_ignored(home, jobs_dir, tmp_path) -> None:
    # nohup's SIGHUP: the run goes on through a hangup, and still stops at a Ctrl-C.
    with _run(home, tmp_path, jobs_dir, "waits", 1, preexec=_ignoring_hangups) as (process, said):
        os.kill(process.pid, signal.SIGHUP)
        time.sleep(1)
        assert process.poll() is None and not _ended(said["ignores"])
        os.kill(process.pid, signal.SIGINT)
        process.wait(timeout=60)
    assert process.returncode == 130
    assert _record(jobs_dir, said)["why"] == "Ctrl-C"


def test_the_grace_is_today_s_stop_grace() -> None:
    from claude_vm.mcp import jobs as mcp_jobs

    assert attached.STOP_GRACE == mcp_jobs.STOP_GRACE == 30


# ---------------------------------------------------------------- a call in a thread ----

THREADED = (
    "Ctrl-C once, the body in a thread",
    "Ctrl-C twice, the body in a thread",
    "an error, a thread in a call",
    "Ctrl-C twice, a call on a pool of the body's own",
)


@pytest.mark.parametrize("way", THREADED)
def test_a_call_still_in_a_thread_holds_the_process_no_longer_than_the_ladder_takes(
    way, home, jobs_dir, tmp_path
) -> None:
    given = WAYS[way]
    with _run(home, tmp_path, jobs_dir, given.body, given.grace) as (process, said):
        _until(lambda: (tmp_path / "calling").is_file(), "the body's call in a thread")
        _send(process, given.sent, tmp_path / "err")
        last = time.monotonic()
        process.wait(timeout=90)
        took = time.monotonic() - last
    # The call sleeps for ten minutes: the process waits for the grace where a Ctrl-C once is all, and a step's own end.
    bound = (given.grace if given.sent == (signal.SIGINT,) else 0) + attached.SETTLE
    assert took < bound, took
    assert process.returncode == given.status
    assert _ended_as(jobs_dir, said) == (given.outcome, given.exit, given.why)
    # The call ran on a daemon thread of the ladder's own, which blocks the signals the ladder takes.
    if given.body != "own-pool":
        assert said["daemon"] is True
        assert {int(each) for each in attached.TAKEN} <= set(said["blocked"]), said["blocked"]
    if given.body == "thread-error":
        # Said as Python says an error it ends on, though the process ended at once.
        assert "RuntimeError: the body's own error" in (tmp_path / "err").read_text()


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="asyncio waits for each child in a thread of its own on macOS, which the ladder does not start, and the"
    " kernel may hand a signal to it: the hold is real on Linux",
)
def test_with_a_call_in_a_thread_the_interrupts_stay_held_off_while_the_table_is_read(home, jobs_dir, tmp_path) -> None:
    with _run(home, tmp_path, jobs_dir, "slow-table", 60) as (process, said):
        _until(lambda: (tmp_path / "calling").is_file(), "the body's call in a thread")
        (tmp_path / "slow").write_text("the next read of the table is slow")
        os.kill(process.pid, signal.SIGINT)
        _until(lambda: (tmp_path / "reading").is_file(), "the first Ctrl-C's read of the table begun")
        time.sleep(0.3)
        os.kill(process.pid, signal.SIGTERM)
        process.wait(timeout=90)
    # The SIGTERM waited for the read's end: its handler read the table after it, never inside it.
    assert not (tmp_path / "nested").exists()
    assert process.returncode == -signal.SIGTERM
    assert _ended_as(jobs_dir, said) == ("cancelled", 143, "SIGTERM")


# ---------------------------------------------------------------- a question ----

# A question at a pseudo-terminal: the body's way, the keys typed once it asks, its status, its outcome, and what the
# question read (None: nothing, the run ended at it).
ASKED = {
    "an answer": ("prompt", b"yes\n", 0, "succeeded", b"yes\n"),
    "Ctrl-C": ("prompt", b"\x03", 130, "cancelled", None),
    "Ctrl-C read raw": ("prompt-raw", b"\x03", 130, "cancelled", b"\x03"),
}


@pytest.mark.parametrize("way", ASKED)
def test_at_a_terminal_a_question_is_read_on_the_loop_and_one_ctrl_c_there_ends_the_run(
    way, home, jobs_dir, tmp_path
) -> None:
    body, keys, status, outcome, answer = ASKED[way]
    state: dict = {"said": None, "typed": None}

    def act(chunk: bytes, output: bytes, master: int, process: subprocess.Popen) -> None:
        if state["said"] is None:
            found = REPORT.search(output)
            if found is None:
                return
            state["said"] = json.loads(found.group(0))
            state["typed"] = time.monotonic()
            os.write(master, keys)

    # A grace of a minute: a Ctrl-C at a question ends the run there, never left waiting for an answer.
    argv = [sys.executable, "-I", "-c", WATCHES, *_argv(tmp_path, jobs_dir, body, 60)]
    try:
        rc, output = harness._at_a_pseudo_terminal(argv, tmp_path, _env(home, tmp_path), act=act, timeout=90)
        took = time.monotonic() - state["typed"]
    finally:
        _end_left(tmp_path)
    watched = WATCHED.search(output)
    assert rc == 0 and watched is not None, output.decode(errors="replace")
    assert (watched.group(1), int(watched.group(2))) == (b"true", status)
    assert took < 10, took
    assert _record(jobs_dir, state["said"])["outcome"] == outcome
    answered = tmp_path / "answer"
    assert (answered.read_bytes() if answered.is_file() else None) == answer
    # The line that the run is stopping begins below the question, never after what the terminal echoed.
    assert b"^Caw:" not in output


def test_a_question_is_never_begun_once_the_run_is_stopping(home, jobs_dir, tmp_path) -> None:
    with _run(home, tmp_path, jobs_dir, "prompt-late", 60) as (process, said):
        os.kill(process.pid, signal.SIGINT)
        process.wait(timeout=60)
    assert (tmp_path / "asked").read_text() == "not asked"
    assert process.returncode == 130
    assert _ended_as(jobs_dir, said) == ("cancelled", 130, "Ctrl-C")


# ---------------------------------------------------------------- the ladder's own ----


def test_a_ladder_that_cannot_be_built_leaves_no_job_and_no_directory(home, jobs_dir, tmp_path) -> None:
    ended = subprocess.run(
        _argv(tmp_path, jobs_dir, "no-loop", 60),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=_env(home, tmp_path),
        timeout=60,
        preexec_fn=harness._default_signals,  # noqa: PLW1509 - only signal, safe beside xdist's thread
    )
    assert ended.returncode == 1, ended.stderr
    assert b"Too many open files" in ended.stderr
    assert jobs.listed(jobs_dir) == []
    assert list((tmp_path / "tmp").glob("aw-run-*")) == []


def test_asyncio_s_own_reports_stay_off_the_terminal(home, jobs_dir, tmp_path) -> None:
    with _run(home, tmp_path, jobs_dir, "callback", 60) as (process, said):
        process.wait(timeout=60)
    assert process.returncode == 0
    err = (tmp_path / "err").read_text()
    assert "ZeroDivisionError" not in err and "Exception in callback" not in err, err


def test_work_that_will_not_take_its_cancel_gets_one_line_of_aw_s_own_and_no_report_of_python_s(
    home, jobs_dir, tmp_path
) -> None:
    with _run(home, tmp_path, jobs_dir, "swallows", 60) as (process, said):
        _send(process, (signal.SIGINT, signal.SIGINT), tmp_path / "err")
        process.wait(timeout=60)
    assert (tmp_path / "swallowed").is_file()
    assert process.returncode == 130
    assert _ended_as(jobs_dir, said) == ("cancelled", 130, "Ctrl-C twice")
    err = (tmp_path / "err").read_text()
    assert attached.LEFT.format(settle=attached.SETTLE) in err, err
    assert "Task was destroyed" not in err, err


def test_python_s_report_of_an_end_it_dropped_is_dropped_and_no_other() -> None:
    class Unraisable(NamedTuple):
        exc_value: BaseException

    seen: list[Unraisable] = []
    hook = attached._dropping(seen.append)
    hook(Unraisable(attached.Abandoned(signal.SIGTERM)))
    hook(Unraisable(ValueError("another")))
    assert [type(each.exc_value) for each in seen] == [ValueError]


# ---------------------------------------------------------------- at a terminal ----

AT_A_TERMINAL = ("Ctrl-C once", "Ctrl-C twice", "SIGHUP", "SIGTERM", "an error")


@pytest.mark.parametrize("way", AT_A_TERMINAL)
def test_at_a_terminal_the_echo_is_back_after_each_end(way, home, jobs_dir, tmp_path) -> None:
    given = WAYS[way]
    state: dict = {"said": None, "sent": 0}

    def act(chunk: bytes, output: bytes, master: int, process: subprocess.Popen) -> None:
        if state["said"] is None:
            found = REPORT.search(output)
            if found is None:
                return
            state["said"] = json.loads(found.group(0))
        if state["sent"] == len(given.sent) or (state["sent"] and STOPPING not in output):
            return
        signum = given.sent[state["sent"]]
        state["sent"] += 1
        if signum == signal.SIGINT:
            os.write(master, b"\x03")
        else:
            os.kill(state["said"]["pid"], signum)

    argv = [sys.executable, "-I", "-c", WATCHES, *_argv(tmp_path, jobs_dir, given.body, given.grace, quiet=True)]
    try:
        rc, output = harness._at_a_pseudo_terminal(argv, tmp_path, _env(home, tmp_path), act=act, timeout=90)
        left = _left(tmp_path)
    finally:
        _end_left(tmp_path)
    watched = WATCHED.search(output)
    assert rc == 0 and watched is not None, output.decode(errors="replace")
    assert (watched.group(1), int(watched.group(2))) == (b"true", given.status)
    assert state["said"] is not None and _record(jobs_dir, state["said"])["outcome"] == given.outcome
    assert left == []


# ---------------------------------------------------------------- a child meant to outlive the run ----

# Each way a run ends with an opener meant to outlive it: the body's way, the signals sent, the grace, the run's status
# and its record's outcome. Each but the return ends the tree under the run, which leaves the opener out.
OUTLIVES = {
    "returns 0": ("outlive-0", (), 60, 0, "succeeded"),
    "an error": ("outlive-error", (), 60, 1, "failed"),
    "Ctrl-C once": ("outlive-waits", (signal.SIGINT,), 2, 130, "cancelled"),
    "Ctrl-C twice": ("outlive-waits", (signal.SIGINT, signal.SIGINT), 60, 130, "cancelled"),
    "SIGTERM": ("outlive-waits", (signal.SIGTERM,), 60, -signal.SIGTERM, "cancelled"),
}


@pytest.mark.parametrize("way", OUTLIVES)
def test_a_child_meant_to_outlive_the_run_lives_on_unmarked_after_each_end(way, home, jobs_dir, tmp_path) -> None:
    # The editor and a page's opener: before, a program child, marked and ended with its run (review/code/C2.md 1).
    body, sent, grace, status, outcome = OUTLIVES[way]
    opener = None
    try:
        with _run(home, tmp_path, jobs_dir, body, grace) as (process, said):
            opener = said["opener"]
            _send(process, sent, tmp_path / "err")
            assert process.wait(timeout=90) == status, (tmp_path / "err").read_text()
        # Time for a kill the ladder sent to have ended it: it lives on, carrying no job's marker, in its own session.
        time.sleep(1)
        assert not _ended(opener)
        opened = json.loads((tmp_path / "opened").read_text())
        assert (opened["pid"], opened["session"], opened["marker"]) == (opener, opener, None)
        assert _record(jobs_dir, said)["outcome"] == outcome
    finally:
        if opener is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(opener, signal.SIGKILL)


# ---------------------------------------------------------------- a child the terminal stopped ----

ASKED_LINE = re.compile(rb"aw: (\S+) stopped at the terminal to ask a question there")


@pytest.mark.skipif(not hasattr(os, "waitid"), reason="Python before 3.13 on macOS has no waitid")
def test_a_child_stopped_asking_at_the_terminal_is_said_within_a_second_and_ended_and_its_run_ends(
    home, jobs_dir, tmp_path
) -> None:
    # A group child that reads the terminal is stopped by it (SIGTTIN), and would wait for an answer no run gives,
    # silent, until the run's end (the owner's 8, review/code/SUMMARY-5.md; C2.md, "the terminal").
    state: dict = {"said": None, "seen": None}

    def act(chunk: bytes, output: bytes, master: int, process: subprocess.Popen) -> None:
        if state["said"] is None and (found := REPORT.search(output)) is not None:
            state["said"] = json.loads(found.group(0))
        if state["seen"] is None and ASKED_LINE.search(output):
            state["seen"] = time.time()

    argv = [sys.executable, "-I", "-c", WATCHES, *_argv(tmp_path, jobs_dir, "asks", 60)]
    try:
        rc, output = harness._at_a_pseudo_terminal(argv, tmp_path, _env(home, tmp_path), act=act, timeout=90, poll=0.05)
        left = _left(tmp_path)
    finally:
        _end_left(tmp_path)
    watched = WATCHED.search(output)
    assert rc == 0 and watched is not None, output.decode(errors="replace")
    asked = ASKED_LINE.search(output)
    assert asked is not None and asked.group(1) == os.path.basename(sys.executable).encode()
    assert state["seen"] is not None and state["seen"] - float((tmp_path / "asking").read_text()) < 1.0
    # Ended: killed, so the step that waited on it failed, and the run ended with the body's own status.
    assert (tmp_path / "asked").read_text() == str(-signal.SIGKILL)
    assert int(watched.group(2)) == 1
    assert _record(jobs_dir, state["said"])["outcome"] == "failed"
    assert left == []


# ---------------------------------------------------------------- end_under ----

# A child with one grandchild in a session of its own and one in the child's own group, both pids to the file named.
TWO = (
    "import subprocess, sys, time\n"
    "away = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], start_new_session=True)\n"
    "kept = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "open(sys.argv[1], 'w').write(f'{away.pid} {kept.pid}')\n"
    "time.sleep(120)\n"
)
# A child that starts HOLDS's shape under it, which writes its own pid and its grandchild's to the file named.
NESTS = "import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-I', '-c', sys.argv[1], sys.argv[2]])\n"
NESTS += "time.sleep(120)\n"


def test_end_under_ends_what_is_under_a_process_and_never_the_process(tmp_path) -> None:
    pids = tmp_path / "pids"
    child = subprocess.Popen([sys.executable, "-I", "-c", TWO, str(pids)], process_group=0)
    try:
        _until(lambda: pids.is_file() and len(pids.read_text().split()) == 2, "the grandchildren's pids written")
        away, kept = map(int, pids.read_text().split())
        assert os.getpgid(kept) == child.pid != os.getpgid(away)
        assert sorted(proc.end_under(child.pid)) == sorted([away, kept])
        _until(lambda: _ended(away) and _ended(kept), "both grandchildren ended")
        # The child itself, and its group, which the grandchild in it shares, are never signalled.
        time.sleep(0.3)
        assert child.poll() is None
    finally:
        harness._end(child)


def test_end_under_ends_what_was_remembered_by_its_start_and_never_another_at_its_pid(tmp_path) -> None:
    pids = tmp_path / "pids"
    root = subprocess.Popen([sys.executable, "-I", "-c", NESTS, HOLDS, str(pids)], process_group=0)
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], process_group=0)
    try:
        _until(lambda: pids.is_file() and len(pids.read_text().split()) == 2, "the pids written")
        middle, grandchild = map(int, pids.read_text().split())
        remembered = proc.snapshot(root.pid)
        assert {middle, grandchild} <= set(remembered)
        # The middle of the tree killed alone: its grandchild, in a session of its own, leaves the tree for PID 1.
        os.kill(middle, signal.SIGKILL)
        _until(lambda: grandchild not in proc.descendants(root.pid), "the grandchild out of the tree")
        remembered[bystander.pid] = "Thu Jan  1 00:00:00 1970"
        signalled = proc.end_under(root.pid, remembered)
        assert grandchild in signalled and bystander.pid not in signalled and root.pid not in signalled
        _until(lambda: _ended(grandchild), "the remembered grandchild ended")
        assert root.poll() is None and bystander.poll() is None
    finally:
        harness._end(root)
        bystander.kill()
        bystander.wait()


# A child that starts two of its own: one in a session of its own that starts NESTS's shape under it, as a child meant
# to outlive its run may start its own, and a sleeper in its group; their two pids to the last file named.
SPLITS = (
    "import subprocess, sys, time\n"
    "left = subprocess.Popen([sys.executable, '-I', '-c', *sys.argv[1:4]], start_new_session=True)\n"
    "other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "open(sys.argv[4], 'w').write(f'{left.pid} {other.pid}')\n"
    "time.sleep(120)\n"
)


def test_end_under_never_signals_what_it_is_told_to_leave_nor_what_is_under_it(tmp_path) -> None:
    # A child meant to outlive the run, as the run's end leaves it (children.outliving), and what it started with it.
    nested, split = tmp_path / "nested", tmp_path / "split"
    root = subprocess.Popen(
        [sys.executable, "-I", "-c", SPLITS, NESTS, HOLDS, str(nested), str(split)], process_group=0
    )
    found: list[int] = []
    try:
        _until(
            lambda: all(each.is_file() and len(each.read_text().split()) == 2 for each in (nested, split)),
            "the pids written",
        )
        left, other = map(int, split.read_text().split())
        middle, grandchild = map(int, nested.read_text().split())
        found = [left, middle, grandchild]
        remembered = proc.snapshot(root.pid)
        assert {left, middle, other} <= set(remembered)
        signalled = proc.end_under(root.pid, remembered, leaving=[left])
        assert other in signalled and not set(found) & set(signalled)
        _until(lambda: _ended(other), "the other child ended")
        time.sleep(0.3)
        assert not any(_ended(each) for each in found) and root.poll() is None
        # And a snapshot told to leave it remembers nothing of it.
        assert not {left, middle} & set(proc.snapshot(root.pid, leaving=[left]))
    finally:
        for each in found:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(each, signal.SIGKILL)
        harness._end(root)


def test_end_under_with_a_table_it_cannot_read_says_so_and_signals_nothing(tmp_path, monkeypatch) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], process_group=0)
    said: list[str] = []
    try:
        monkeypatch.setattr(proc.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no ps")))
        assert proc.end_under(child.pid, blind=said.append) == []
        monkeypatch.undo()
        assert len(said) == 1 and "no ps" in said[0], said
        assert child.poll() is None
    finally:
        harness._end(child)
