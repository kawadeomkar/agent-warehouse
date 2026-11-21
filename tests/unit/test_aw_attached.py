"""A run in the command's own process, and its stated end (lib/claude_vm/runs/attached.py and runs/__init__.py's run,
lib/claude_vm/proc.py's end_under; docs/architecture/engine.md §16.8).

Each run here is a child process of the test's, made by runs.run as a verb makes one, whose body starts three stand-in
Ansible children through runs/children.py, each in a session of its own: one that ends on SIGINT, one that ignores it,
and one that puts a grandchild in a session of its own, as ansible-core's task worker is (tests/unit/test_processes.py's
HOLDS). Each way it ends is held to its exit record, its process's status and the process table after it. The rule
with a test of its own: every way out of a run but SIGKILL writes an exit record, and the ways walk every signal the
ladder takes and every outcome. SIGKILL writes none, and the job reads lost. A shielded phase finishes before the stop;
at a pseudo-terminal the echo is back after each end; a signal ignored on the way in stays ignored; and end_under ends
what is under a process, never the process.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
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
# body starts the three stand-ins, says what it started as one line of JSON, and then ends its way: waits (and is ended
# from outside), stops at the Ctrl-C, raises, returns a status, or holds a shield across the Ctrl-C.
RUN = r"""
import asyncio, json, os, sys, termios
lib, jobs_dir, way, grace, folder, quiet, holds = sys.argv[1:8]
sys.path.insert(0, lib)
from pathlib import Path
from claude_vm import runs
from claude_vm.runs import attached, children

attached.STOP_GRACE = float(grace)
folder = Path(folder)
ENDS = "import time\nprint('ready', flush=True)\ntime.sleep(120)\n"
IGNORES = (
    "import signal, time\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\nprint('ready', flush=True)\ntime.sleep(120)\n"
)
PIPE, DEVNULL = asyncio.subprocess.PIPE, asyncio.subprocess.DEVNULL


async def stand_in(ctx, code, name, stdout):
    argv = [sys.executable, "-I", "-c", code, str(folder / name)]
    return await children.start(
        ctx.job, argv, children.SESSION, dict(os.environ), DEVNULL, stdout, DEVNULL, children.ANSIBLE
    )


async def body(ctx):
    said = {"job": ctx.job.id, "pid": os.getpid(), "directory": str(ctx.directory)}
    for name, code in (("ends", ENDS), ("ignores", IGNORES)):
        process = await stand_in(ctx, code, name, PIPE)
        await asyncio.wait_for(process.stdout.readline(), 60)
        said[name] = process.pid
    process = await stand_in(ctx, holds, "holds", DEVNULL)
    pids = folder / "holds"
    while len(pids.read_text().split() if pids.is_file() else ()) != 2:
        await asyncio.sleep(0.05)
    said["holds"], said["grandchild"] = process.pid, int(pids.read_text().split()[1])
    if quiet:
        settings = termios.tcgetattr(0)
        settings[3] &= ~termios.ECHO
        termios.tcsetattr(0, termios.TCSANOW, settings)
    if way == "shield":
        async with ctx.shield("the deletion already sent"):
            print(json.dumps(said), flush=True)
            await asyncio.sleep(6 * float(grace))
            (folder / "shielded").write_text("finished")
        try:
            async with ctx.shield("another deletion"):
                (folder / "another").write_text("begun")
        except asyncio.CancelledError:
            (folder / "another").write_text("not begun")
            raise
        await asyncio.sleep(600)
    print(json.dumps(said), flush=True)
    if way == "error":
        raise RuntimeError("the body's own error")
    if way == "stops":
        await ctx.cancelled.wait()
        (folder / "stopped").write_text("saw the stop")
        return 0
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
    lock_kind="machines",
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


class Way(NamedTuple):
    """One way out of a run: the body's way, the signals sent (each once the run took the one before), the grace, and
    what it leaves: the record's outcome, its exit status and why, and the process's status (negative: its death by
    that signal)."""

    body: str
    sent: tuple[int, ...]
    grace: float
    outcome: str
    exit: int
    why: str
    status: int


UNHANDLED = "an error the run did not handle: RuntimeError: the body's own error"
# Every way out of a run but SIGKILL.
WAYS = {
    "Ctrl-C once": Way("waits", (signal.SIGINT,), 2, "cancelled", 130, "Ctrl-C", 130),
    "Ctrl-C twice": Way("waits", (signal.SIGINT, signal.SIGINT), 60, "cancelled", 130, "Ctrl-C twice", 130),
    "SIGHUP": Way("waits", (signal.SIGHUP,), 60, "cancelled", 129, "SIGHUP", -signal.SIGHUP),
    "SIGTERM": Way("waits", (signal.SIGTERM,), 60, "cancelled", 143, "SIGTERM", -signal.SIGTERM),
    "an error": Way("error", (), 60, "failed", 1, UNHANDLED, 1),
    "returns 0": Way("0", (), 60, "succeeded", 0, "done", 0),
    "returns 3": Way("3", (), 60, "failed", 3, "exit 3", 3),
    "returns 130": Way("130", (), 60, "cancelled", 130, "Ctrl-C", 130),
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
    return [said[name] for name in ("ends", "ignores", "holds", "grandchild")]


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


def _end_left(folder: Path) -> None:
    """Whatever a run that failed its test left: every process that names the test's folder, and the grandchild."""
    holds = folder / "holds"
    named = _by_argv(str(folder))
    if holds.is_file() and len(holds.read_text().split()) == 2:
        grandchild = int(holds.read_text().split()[1])
        command = subprocess.run(["ps", "-o", "command=", "-p", str(grandchild)], capture_output=True, text=True)
        if "time.sleep(120)" in command.stdout:
            named.append(grandchild)
    for pid in named:
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
    Ended whole on the way out where it still runs, and anything it left ended."""
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
    finally:
        if process.poll() is None:
            harness._end(process)
        _end_left(folder)


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
    record = _record(jobs_dir, said)
    assert (record["outcome"], record["exit"], record["why"]) == (given.outcome, given.exit, given.why)
    job = jobs.find(jobs_dir, said["job"])
    assert job is not None and jobs.state(job) == given.outcome
    _until(lambda: all(_ended(pid) for pid in _tree(said)), "everything the run started ended with it")
    assert not Path(said["directory"]).exists()
    said_on_err = (tmp_path / "err").read_bytes()
    assert (STOPPING in said_on_err) is (signal.SIGINT in given.sent)


def test_the_ways_out_walk_every_signal_the_run_takes_and_every_outcome() -> None:
    # A signal the ladder takes, or an outcome a run can have, that no way above walks fails here.
    assert {signum for given in WAYS.values() for signum in given.sent} == set(attached.TAKEN)
    assert {given.outcome for given in WAYS.values()} == set(jobs.OUTCOMES)


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
            # What it started is left, for the next command to find: X39's question (card C5).
            assert not _ended(said["ignores"])
        finally:
            proc.end_left(remembered)
            job = jobs.find(jobs_dir, said["job"])
            if job is not None:
                rundir.remove(job)
    assert not Path(said["directory"]).exists()


# ---------------------------------------------------------------- Ctrl-C ----


def test_ctrl_c_once_lets_the_body_stop_and_gives_what_ignores_it_the_grace(home, jobs_dir, tmp_path) -> None:
    grace = 6
    with _run(home, tmp_path, jobs_dir, "stops", grace) as (process, said):
        sent = time.monotonic()
        os.kill(process.pid, signal.SIGINT)
        # The body saw the stop and returned; the child that ends on SIGINT ended, and the child of HOLDS's shape.
        _until(lambda: (tmp_path / "stopped").is_file(), "the body's stop")
        _until(lambda: _ended(said["ends"]) and _ended(said["holds"]), "the children that end on SIGINT gone")
        # What ignores SIGINT, and what left the group, live until the grace's end, and then no longer.
        time.sleep(1)
        assert time.monotonic() - sent < grace
        assert not _ended(said["ignores"]) and not _ended(said["grandchild"])
        process.wait(timeout=60)
        assert time.monotonic() - sent >= grace
    assert process.returncode == 130
    record = _record(jobs_dir, said)
    assert (record["outcome"], record["exit"], record["why"]) == ("cancelled", 130, "Ctrl-C")
    _until(lambda: all(_ended(pid) for pid in _tree(said)), "what was left ended at the grace's end")
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
    _until(lambda: all(_ended(pid) for pid in _tree(said)), "the tree ended at the second Ctrl-C")


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
    record = _record(jobs_dir, said)
    assert (record["outcome"], record["exit"], record["why"]) == ("cancelled", 130, "Ctrl-C")
    _until(lambda: all(_ended(pid) for pid in _tree(said)), "the tree ended once the phase was done")


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
    finally:
        _end_left(tmp_path)
    watched = WATCHED.search(output)
    assert rc == 0 and watched is not None, output.decode(errors="replace")
    assert (watched.group(1), int(watched.group(2))) == (b"true", given.status)
    assert state["said"] is not None and _record(jobs_dir, state["said"])["outcome"] == given.outcome


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
