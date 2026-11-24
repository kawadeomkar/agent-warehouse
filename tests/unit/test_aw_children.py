"""The one way a run starts a child (lib/claude_vm/runs/children.py; docs/architecture/engine.md §16.8).

Each child here is a real process, a probe that reports what it was given: its environment's marker, its process group
and session, the signals it ignores or blocks (as Python reads them, and on Linux as the kernel shows a program that is
not Python), and the descriptors it has open. A child meant to outlive its run is outlive's: unmarked, in a session of
its own, its standard streams /dev/null, in no registry. A run started with another job's marker carries none from the
moment its program hands the verb on, and nor do the children it starts outside start (route.py's ssh, proc.py's ps). A
child the terminal stopped is asking's, route.py's ssh in a process group of its own among them, and one stopped for any
other reason is not. The rules with a test of their own: what a forked child runs before its exec is signal's two calls
alone; in the new engine's code a child process is started only through children.py, the SSH runner's among them, by a
scan for every way Python starts one, the standard library's own in platform among them; close_fds is never turned
off; no process is forked without an exec (a forked child keeps every lock the run holds, since a descriptor that is
not inheritable is closed only at an exec); pass_fds and set_inheritable(..., True), and every other way to make a
descriptor inheritable, appear only in children.start; and every module of runs/ that takes a kernel lock is one
children.HOLDERS reads, so that its locks are refused too.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from aw_kit import STOP_GONE
from harness import REPO, repo_files
from test_aw_layout import NEW_MODULES
from test_aw_locks import _flocks_and_lock_folders

from claude_vm import proc, route
from claude_vm.cli import main
from claude_vm.fleet import paths
from claude_vm.runs import children
from claude_vm.runs import job as jobs
from claude_vm.schema import patterns

AW = REPO / "tools" / "aw"
PIPE, DEVNULL = asyncio.subprocess.PIPE, asyncio.subprocess.DEVNULL
OTHER_JOB = "0123456789abcdef"

# A child that says what it was given, as JSON on its standard output: its pid, process group and session, the job's
# marker, which signals it ignores or blocks, by number (every one a handler can be given, but SIGPIPE and SIGXFSZ,
# which Python ignores itself at its start), every descriptor it has open past its standard streams, and what it read
# from the descriptor named as its argument, where one is.
PROBE = r"""
import json, os, resource, signal, sys
SIGNALS = sorted(
    set(signal.valid_signals())
    - {signal.SIGKILL, signal.SIGSTOP, signal.SIGPIPE, getattr(signal, "SIGXFSZ", signal.SIGPIPE)}
)
soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
limit = soft if 0 < soft < 65536 else 65536
fds = []
for fd in range(3, limit):
    try:
        os.fstat(fd)
    except OSError:
        continue
    fds.append(fd)
read = None
if len(sys.argv) > 1:
    with os.fdopen(int(sys.argv[1]), "rb") as stream:
        read = stream.read().decode()
blocked = signal.pthread_sigmask(signal.SIG_BLOCK, [])
print(json.dumps({
    "pid": os.getpid(),
    "group": os.getpgrp(),
    "session": os.getsid(0),
    "marker": os.environ.get("AGENT_WAREHOUSE_JOB"),
    "ignored": [int(one) for one in SIGNALS if signal.getsignal(one) == signal.SIG_IGN],
    "blocked": sorted(int(one) for one in SIGNALS if one in blocked),
    "fds": fds,
    "read": read,
}))
"""
# A child that waits until its standard input closes.
WAITS = "import sys; sys.stdin.read()"
# A child meant to outlive its run, as an editor or a page's opener is: it writes what it was given into the file named
# as its argument - its pid, session, the job's marker, and whether each standard stream is /dev/null - and then waits.
OPENS = r"""
import json, os, sys, time
null = os.stat(os.devnull)
streams = []
for fd in (0, 1, 2):
    found = os.fstat(fd)
    streams.append((found.st_dev, found.st_ino) == (null.st_dev, null.st_ino))
with open(sys.argv[1] + ".part", "w") as out:
    json.dump({
        "pid": os.getpid(),
        "session": os.getsid(0),
        "marker": os.environ.get("AGENT_WAREHOUSE_JOB"),
        "null": streams,
    }, out)
os.rename(sys.argv[1] + ".part", sys.argv[1])
time.sleep(300)
"""
# A stand-in that writes the job's marker its environment holds, or "none", into the file named by its own name and
# a suffix, then prints nothing and exits 0: what route.py's ssh -G and proc.py's ps are, in a test.
RECORDS_MARKER = '#!/bin/sh\nprintf "%s\\n" "${AGENT_WAREHOUSE_JOB:-none}" >> "$0.marker"\nexit 0\n'


@pytest.fixture
def made(home: Path) -> Iterator[jobs.Job]:
    """A job of this process's own, as the run that starts the children has; dropped after the test."""
    job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True
    )
    yield job
    jobs.drop(job)


def _probe(
    job: jobs.Job,
    own: str,
    *,
    kind: str = children.PROGRAM,
    env: dict[str, str] | None = None,
    pass_fds: tuple[int, ...] = (),
    argument: tuple[str, ...] = (),
) -> dict:
    """What a probe started through children.start says it was given."""

    async def run() -> dict:
        process = await children.start(
            job,
            [sys.executable, "-I", "-c", PROBE, *argument],
            own,
            dict(os.environ) if env is None else env,
            DEVNULL,
            PIPE,
            PIPE,
            kind,
            pass_fds,
        )
        out, err = await asyncio.wait_for(process.communicate(), 60)
        assert process.returncode == 0, err.decode(errors="replace")
        return json.loads(out)

    return asyncio.run(run())


@pytest.fixture
def nothing_starts(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Every child asyncio was asked to start, by any module, recorded and none started."""
    asked: list[tuple] = []

    async def recorded(*argv, **given):
        asked.append((argv, given))
        raise AssertionError("a child was started")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorded)
    return asked


# ---------------------------------------------------------------- the environment ----


def test_unmarked_takes_the_job_s_marker_away(made) -> None:
    # The program's own name for it, which it takes out of its environment at its start, is the same.
    assert children.MARKER == "AGENT_WAREHOUSE_JOB" == main.MARKER and patterns.full("job_id", made.id)
    marked = {"PATH": "/usr/bin", children.MARKER: made.id}
    assert children.unmarked(marked) == {"PATH": "/usr/bin"}
    # A copy: what was given is left as it was.
    assert marked == {"PATH": "/usr/bin", children.MARKER: made.id}


def test_only_the_run_that_made_a_job_marks_a_child_with_it(made, nothing_starts) -> None:
    other = jobs.Job(made.folder.parent / OTHER_JOB)
    with pytest.raises(RuntimeError, match="not this process's"):
        asyncio.run(children.start(other, ["true"], children.GROUP, {}, DEVNULL, DEVNULL, DEVNULL, children.PROGRAM))
    assert nothing_starts == [] and children.live(other) == []


# ---------------------------------------------------------------- a child, started each way ----


@pytest.mark.parametrize("kind", [children.SSH, children.ANSIBLE, children.PROGRAM])
def test_a_child_in_a_group_of_its_own_carries_the_job_s_marker(made, kind) -> None:
    # Even given another job's marker: the child carries its own run's.
    found = _probe(made, children.GROUP, kind=kind, env={**os.environ, children.MARKER: OTHER_JOB})
    assert found["marker"] == made.id
    # A group of its own, which the terminal's Ctrl-C never reaches, in the run's own session.
    assert found["group"] == found["pid"] != os.getpgrp()
    assert found["session"] == os.getsid(0)


@pytest.mark.parametrize("kind", [children.SSH, children.ANSIBLE, children.PROGRAM])
def test_a_child_in_a_session_of_its_own_carries_the_job_s_marker(made, kind) -> None:
    found = _probe(made, children.SESSION, kind=kind)
    assert found["marker"] == made.id
    assert found["session"] == found["group"] == found["pid"] != os.getsid(0)


@pytest.mark.parametrize("own", children.OWN)
def test_a_shared_master_carries_no_marker_and_is_entered_in_no_registry(made, own) -> None:
    # Even handed an environment that carries the run's marker.
    found = _probe(made, own, kind=children.MASTER, env={**os.environ, children.MARKER: made.id})
    assert found["marker"] is None
    assert children.live(made) == []


# What the run ignores and blocks in the signals test: as a run started under nohup or with & ignores SIGINT, SIGHUP
# and SIGQUIT, and more besides, which a child would keep (review/code/C2.md 4). Never SIGCHLD, which the run's own
# asyncio needs.
IGNORED = (
    signal.SIGINT,
    signal.SIGHUP,
    signal.SIGQUIT,
    signal.SIGUSR1,
    signal.SIGALRM,
    signal.SIGTSTP,
    signal.SIGTTIN,
    signal.SIGTTOU,
)
BLOCKED = (signal.SIGTERM, signal.SIGUSR2, signal.SIGALRM)


def _ignoring_and_blocking():
    """IGNORED ignored and BLOCKED blocked in this process, and a function that puts them back."""
    previous = {one: signal.getsignal(one) for one in IGNORED}
    assert None not in previous.values(), "a handler this test could not put back"
    for one in IGNORED:
        signal.signal(one, signal.SIG_IGN)
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, set(BLOCKED))

    def put_back() -> None:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        for one, handler in previous.items():
            signal.signal(one, handler)

    return put_back


@pytest.mark.parametrize("own", children.OWN)
def test_a_child_has_every_signal_at_its_default_even_where_the_run_ignores_or_blocks_them(made, own) -> None:
    put_back = _ignoring_and_blocking()
    try:
        # A child started any other way keeps them so: the probe sees what this test set.
        plain = json.loads(
            subprocess.run([sys.executable, "-I", "-c", PROBE], capture_output=True, check=True, timeout=60).stdout
        )
        found = _probe(made, own)
    finally:
        put_back()
    assert (plain["ignored"], plain["blocked"]) == (sorted(map(int, IGNORED)), sorted(map(int, BLOCKED)))
    assert (found["ignored"], found["blocked"]) == ([], [])


def _status_masks(text: str) -> tuple[int, int]:
    """SigIgn and SigBlk of a process's status, as Linux writes them."""
    masks = dict(line.split(":\t", 1) for line in text.splitlines() if line.startswith(("SigIgn:", "SigBlk:")))
    return int(masks["SigIgn"], 16), int(masks["SigBlk"], 16)


@pytest.mark.skipif(not Path("/proc/self/status").is_file(), reason="the kernel's own masks are Linux's to show")
@pytest.mark.parametrize("own", children.OWN)
def test_a_program_that_is_not_python_has_no_signal_ignored_or_blocked(made, own) -> None:
    # Python ignores SIGPIPE itself at its start, so only a program that is not Python shows that a child has it at
    # its default: with it ignored, a pipe that closes under a child never ends it.
    cat = shutil.which("cat")
    assert cat is not None
    put_back = _ignoring_and_blocking()
    try:
        plain = subprocess.run([cat, "/proc/self/status"], capture_output=True, text=True, check=True, timeout=60)

        async def run() -> str:
            process = await children.start(
                made, [cat, "/proc/self/status"], own, dict(os.environ), DEVNULL, PIPE, PIPE, children.PROGRAM
            )
            out, _ = await asyncio.wait_for(process.communicate(), 60)
            return out.decode()

        found = asyncio.run(run())
    finally:
        put_back()
    ignored, blocked = _status_masks(plain.stdout)
    assert ignored and blocked, plain.stdout
    assert _status_masks(found) == (0, 0), found


@pytest.mark.parametrize("own", children.OWN)
def test_a_pipe_named_in_pass_fds_reaches_the_child_and_no_other_descriptor_does(made, own) -> None:
    read, write = os.pipe()
    # A descriptor any child inheriting this process's would get: it never reaches one of a run's.
    other = os.open(os.devnull, os.O_RDONLY)
    os.set_inheritable(other, True)
    try:
        os.write(write, b"handed on purpose")
        os.close(write)
        found = _probe(made, own, pass_fds=(read,), argument=(str(read),))
    finally:
        os.close(read)
        os.close(other)
    assert found["read"] == "handed on purpose"
    # The job's lock, the other descriptor and the loop's own are all closed in it: only the pipe is open.
    assert found["fds"] == [read]


@pytest.mark.parametrize("own", children.OWN)
def test_with_nothing_named_no_descriptor_but_the_standard_streams_reaches_the_child(made, own) -> None:
    # Python keeps close_fds on where pass_fds names any: with none named, nothing else does.
    other = os.open(os.devnull, os.O_RDONLY)
    os.set_inheritable(other, True)
    try:
        found = _probe(made, own)
    finally:
        os.close(other)
    assert found["fds"] == []


def _lock(job: jobs.Job) -> int:
    held = jobs.locks_held()
    [descriptor] = [descriptor for descriptor, name in held.items() if name == f"job {job.id}'s lock"]
    return descriptor


@pytest.mark.parametrize("way", ["pass_fds", "a duplicate in pass_fds", "standard output", "standard input"])
def test_a_child_is_never_handed_a_lock_and_nothing_starts(made, nothing_starts, way) -> None:
    lock = _lock(made)
    duplicate = os.dup(lock)
    streams = {"stdin": DEVNULL, "stdout": DEVNULL, "stderr": DEVNULL}
    handed: tuple[int, ...] = ()
    if way == "pass_fds":
        handed = (lock,)
    elif way == "a duplicate in pass_fds":
        handed = (duplicate,)
    else:
        streams["stdout" if way == "standard output" else "stdin"] = lock
    try:
        with pytest.raises(children.LockRefused) as refused:
            asyncio.run(
                children.start(
                    made,
                    [sys.executable, "-I", "-c", "pass"],
                    children.GROUP,
                    dict(os.environ),
                    streams["stdin"],
                    streams["stdout"],
                    streams["stderr"],
                    children.ANSIBLE,
                    handed,
                )
            )
    finally:
        os.close(duplicate)
    # By the lock's name.
    assert f"job {made.id}'s lock" in str(refused.value)
    assert nothing_starts == [] and children.live(made) == []


@pytest.mark.parametrize(
    ("given", "error", "said"),
    [
        ({"kind": "editor"}, ValueError, "no kind of child"),
        ({"own": "pgrp"}, ValueError, "no way to keep a child"),
        ({"argv": "true"}, TypeError, "list of str"),
        ({"argv": []}, TypeError, "list of str"),
        ({"stdin": None}, ValueError, "never reads the run's own standard input"),
        ({"stdin": 0}, ValueError, "never reads the run's own standard input"),
        ({"stdin": "the run's own"}, ValueError, "never reads the run's own standard input"),
        ({"kind": children.OUTLIVING}, ValueError, "started by outlive, never by start"),
        ({"own": children.SESSION, "stdin": PIPE}, ValueError, "/dev/null"),
        ({"pass_fds": ("3",)}, TypeError, "descriptors"),
        ({"pass_fds": (True,)}, TypeError, "descriptors"),
    ],
)
def test_a_child_asked_for_wrongly_is_refused_and_nothing_starts(made, nothing_starts, given, error, said) -> None:
    words = {
        "argv": ["true"],
        "own": children.GROUP,
        "env": {},
        "stdin": DEVNULL,
        "stdout": DEVNULL,
        "stderr": DEVNULL,
        "kind": children.PROGRAM,
    }
    words.update(given)
    if words["stdin"] == "the run's own":
        # A file whose descriptor is this process's own standard input, as sys.stdin's is.
        words["stdin"] = _Fileno(0)
    with pytest.raises(error, match=said):
        asyncio.run(children.start(made, **words))
    assert nothing_starts == [] and children.live(made) == []


class _Fileno:
    """A stream that is the descriptor it names."""

    def __init__(self, fd: int) -> None:
        self.fd = fd

    def fileno(self) -> int:
        return self.fd


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_a_lock_on_a_standard_stream_the_child_would_inherit_is_refused_and_nothing_starts(
    made, nothing_starts, stream
) -> None:
    # A stream given as None is this process's own descriptor 1 or 2, inherited: a lock's duplicate there would reach
    # the child, and hold the lock past the run's kill (review/code/C2.md 3).
    number = 1 if stream == "stdout" else 2
    saved = os.dup(number)
    os.dup2(_lock(made), number)
    try:
        with pytest.raises(children.LockRefused) as refused:
            asyncio.run(
                children.start(
                    made,
                    [sys.executable, "-I", "-c", "pass"],
                    children.GROUP,
                    dict(os.environ),
                    DEVNULL,
                    None if stream == "stdout" else DEVNULL,
                    None if stream == "stderr" else DEVNULL,
                    children.ANSIBLE,
                )
            )
    finally:
        os.dup2(saved, number)
        os.close(saved)
    assert f"descriptor {number} holds job {made.id}'s lock" in str(refused.value)
    assert nothing_starts == [] and children.live(made) == []


def test_a_standard_stream_the_child_inherits_that_is_no_lock_is_handed_over(made) -> None:
    async def run() -> int:
        process = await children.start(
            made,
            [sys.executable, "-I", "-c", "pass"],
            children.GROUP,
            dict(os.environ),
            DEVNULL,
            None,
            None,
            children.PROGRAM,
        )
        return await asyncio.wait_for(process.wait(), 60)

    assert asyncio.run(run()) == 0


# ---------------------------------------------------------------- the registry ----


def test_the_registry_holds_the_run_s_live_children_by_kind_until_each_ends(made) -> None:
    async def run() -> None:
        waits = [sys.executable, "-I", "-c", WAITS]
        started = await children.start(
            made, waits, children.SESSION, dict(os.environ), DEVNULL, DEVNULL, DEVNULL, children.ANSIBLE
        )
        held = await children.start(
            made, waits, children.GROUP, dict(os.environ), PIPE, DEVNULL, DEVNULL, children.PROGRAM
        )
        runner = await children.start(
            made, waits, children.GROUP, dict(os.environ), PIPE, DEVNULL, DEVNULL, children.SSH
        )
        try:
            # A session child with /dev/null for its input has ended already, or soon will.
            await asyncio.wait_for(started.wait(), 60)
            assert [child.process for child in children.live(made)] == [held, runner]
            assert [child.process for child in children.live(made, children.SSH)] == [runner]
            assert [child.pid for child in children.live(made, children.PROGRAM)] == [held.pid]
            assert children.live(made, children.ANSIBLE) == []
            assert held.stdin is not None
            held.stdin.close()
            await asyncio.wait_for(held.wait(), 60)
            assert [child.process for child in children.live(made)] == [runner]
        finally:
            for process in (held, runner):
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        assert children.live(made) == []

    asyncio.run(run())


# ---------------------------------------------------------------- a child meant to outlive its run ----


def _opened(out: Path) -> dict:
    """What an outliving stand-in said it was given, once it has written it."""
    deadline = time.monotonic() + 60
    while not out.is_file():
        if time.monotonic() > deadline:
            pytest.fail("the outliving child said nothing within a minute")
        time.sleep(0.02)
    return json.loads(out.read_text())


def test_a_child_meant_to_outlive_its_run_is_unmarked_in_a_session_of_its_own_and_in_no_registry(
    made, tmp_path
) -> None:
    out = tmp_path / "opened"
    # Even handed the run's marker, and another job's: it carries none.
    for given in ({children.MARKER: made.id}, {children.MARKER: OTHER_JOB}):
        if out.exists():
            out.unlink()
        process = children.outlive(made, [sys.executable, "-I", "-c", OPENS, str(out)], {**os.environ, **given})
        try:
            said = _opened(out)
            assert said["pid"] == process.pid and said["marker"] is None
            assert said["session"] == process.pid != os.getsid(0)
            assert said["null"] == [True, True, True]
            # Entered in no registry the run's end ends, and known to the end as one it leaves.
            assert children.live(made) == [] and process.pid in children.outliving()
        finally:
            process.kill()
            process.wait(timeout=60)
        assert process.pid not in children.outliving()


def test_a_child_meant_to_outlive_its_run_has_every_signal_at_its_default(made, tmp_path) -> None:
    # As every child has: what the run ignored or blocked is not the editor's to keep. Its standard output is
    # /dev/null, so the probe's answer goes to a file it is given.
    out = tmp_path / "probed"
    to_a_file = PROBE.replace("print(json.dumps(", f"open({str(out)!r}, 'w').write(json.dumps(", 1)
    assert to_a_file != PROBE
    put_back = _ignoring_and_blocking()
    try:
        process = children.outlive(made, [sys.executable, "-I", "-c", to_a_file], dict(os.environ))
    finally:
        put_back()
    try:
        assert process.wait(timeout=60) == 0
    finally:
        if process.poll() is None:
            process.kill()
    found = json.loads(out.read_text())
    assert (found["ignored"], found["blocked"], found["marker"]) == ([], [], None)
    assert found["session"] == found["pid"] != os.getsid(0)
    assert process.pid not in children.outliving()


def test_only_the_run_that_made_a_job_starts_a_child_meant_to_outlive_it(made) -> None:
    other = jobs.Job(made.folder.parent / OTHER_JOB)
    with pytest.raises(RuntimeError, match="not this process's"):
        children.outlive(other, ["true"], {})
    with pytest.raises(TypeError, match="list of str"):
        children.outlive(made, "true", {})
    assert children.outliving() == []


# ---------------------------------------------------------------- a run carries no marker it inherited ----


def test_a_run_started_with_another_job_s_marker_carries_none_and_nor_do_children_start_does_not_start(
    made, monkeypatch, engine_guard, tmp_path, capsys
) -> None:
    # A run started by a marked process - an agent's job, a terminal of an editor a run opened - is handed another
    # job's ID; its children started through start carry its own, and those started any other way carry what its
    # environment holds (review/code/C2.md 2). route.py's ssh -G and proc.py's ps, each a stand-in that says the
    # marker it was given.
    folder = tmp_path / "bin"
    folder.mkdir()
    for name in ("ps", "ssh"):
        (folder / name).write_text(RECORDS_MARKER)
        (folder / name).chmod(0o755)
    engine_guard.first_on_path(folder)
    monkeypatch.setenv(children.MARKER, OTHER_JOB)

    def children_said() -> list[str]:
        proc.snapshot(os.getpid())
        route.ssh_shown(folder / "ssh", [], "alpha")
        return [(folder / f"{name}.marker").read_text().split()[-1] for name in ("ps", "ssh")]

    # The control: a run that keeps what it was handed hands it on.
    assert children_said() == [OTHER_JOB, OTHER_JOB]
    # The program takes it out of its environment at its start, as tools/aw runs it: read at the moment main hands the
    # verb on, where children may start, so a drop made only as main returns fails (review/code/C23.md 4).
    handed_on: list[list[str]] = []
    dispatch = main.dispatch.dispatch

    def handing_on(*given, **named):
        handed_on.append(children_said())
        return dispatch(*given, **named)

    monkeypatch.setattr(main.dispatch, "dispatch", handing_on)
    assert main.main([str(AW), "help"]) == 0
    capsys.readouterr()
    assert handed_on == [["none", "none"]]
    assert children.MARKER not in os.environ
    assert children_said() == ["none", "none"]
    # A child started through start carries the run's own marker.
    assert _probe(made, children.GROUP)["marker"] == made.id


# ---------------------------------------------------------------- a child the terminal stopped ----


@pytest.mark.skipif(not hasattr(os, "waitid"), reason="Python before 3.13 on macOS has no waitid")
@STOP_GONE
def test_a_child_the_terminal_stopped_is_asking_and_one_stopped_otherwise_is_not(made) -> None:
    def stopped_by(pid: int) -> int | None:
        seen = os.waitid(os.P_PID, pid, os.WSTOPPED | os.WNOHANG | os.WNOWAIT)
        return None if seen is None else seen.si_status

    async def until(check, what: str) -> None:
        deadline = time.monotonic() + 30
        while not check():
            if time.monotonic() > deadline:
                pytest.fail(f"{what} within 30 s")
            await asyncio.sleep(0.02)

    async def run() -> None:
        process = await children.start(
            made,
            [sys.executable, "-I", "-c", WAITS],
            children.GROUP,
            dict(os.environ),
            PIPE,
            DEVNULL,
            DEVNULL,
            children.SSH,
        )
        try:
            [child] = children.live(made)
            assert child.program == os.path.basename(sys.executable)
            assert children.asking(made) == []
            # Stopped by a person's SIGSTOP, as a debugger stops one: no question, and not the run's to end.
            os.kill(process.pid, signal.SIGSTOP)
            await until(lambda: stopped_by(process.pid) == signal.SIGSTOP, "the child stopped")
            assert children.asking(made) == []
            os.kill(process.pid, signal.SIGCONT)
            # Named here, never read off the module's own tuple, which a test reading it would shrink with: SIGTTOU is
            # the stop OpenSSH's readpassphrase makes, turning the echo off before it reads (review/code/C23.md 1).
            assert children.AT_THE_TERMINAL == (signal.SIGTTIN, signal.SIGTTOU)
            for signum in (signal.SIGTTIN, signal.SIGTTOU):
                # What the terminal sends a group that reads it, or writes or changes it, not being its own.
                os.killpg(process.pid, signum)
                await until(lambda signum=signum: children.asking(made) == [(child, signum)], "the child asking")
                # Read and left as it was: still stopped, still the run's, never reaped.
                assert stopped_by(process.pid) == signum and process.returncode is None
                assert children.asking(made) == [(child, signum)] and children.live(made) == [child]
                os.kill(process.pid, signal.SIGCONT)
                await until(lambda: children.asking(made) == [], "the child going on")
        finally:
            process.kill()
            await process.wait()
        assert children.asking(made) == []

    asyncio.run(run())


SLEEPS = [sys.executable, "-c", "import time; time.sleep(60)"]


@pytest.mark.skipif(not hasattr(os, "waitid"), reason="Python before 3.13 on macOS has no waitid")
def test_route_s_ssh_in_a_group_of_its_own_is_watched_and_said_asking_and_one_in_this_group_is_not(
    made, monkeypatch
) -> None:
    # route.py's ssh, which the new engine starts in a process group of its own (process_group=0) and start never
    # starts, is stopped by a question at the terminal as the registry's children are (review/code/C23.md 2); one of
    # today's callers, in this process's group, is not the run's to watch.
    monkeypatch.setattr(route, "_OVER", threading.Event())
    leading = route._started(SLEEPS, process_group=0)
    alone = route._started(SLEEPS)
    ours = (leading, alone)

    def watched() -> list[children.Child]:
        return [child for child in children.watched(made) if child.process in ours]

    def asking() -> list[tuple[children.Child, int]]:
        return [(child, signum) for child, signum in children.asking(made) if child.process in ours]

    try:
        child = children.Child(children.SSH, leading, os.path.basename(sys.executable))
        assert leading in route.leading() and alone not in route.leading()
        assert watched() == [child]
        assert asking() == []
        for signum in (signal.SIGTTIN, signal.SIGTTOU):
            os.killpg(leading.pid, signum)
            deadline = time.monotonic() + 30
            while asking() != [(child, signum)]:
                assert time.monotonic() < deadline, "route.py's ssh never read as asking"
                time.sleep(0.02)
            os.kill(leading.pid, signal.SIGCONT)
            while asking():
                assert time.monotonic() < deadline, "route.py's ssh never going on"
                time.sleep(0.02)
    finally:
        for process in ours:
            process.kill()
            process.wait()
            route._forget(process)
    assert watched() == [] and leading not in route.leading()


# ---------------------------------------------------------------- the rules ----

# The modules of the new engine's code that start a child process, each with why: the SSH runner starts its ssh
# children through children.start too. route.py, which opens and checks the hop to a host's VMs, is today's and lies
# outside it.
STARTERS = {
    "lib/claude_vm/runs/children.py": "the one way a run starts a child",
}
# What starts a child process, by the name it is imported under: each of subprocess's and asyncio's ways, the os
# module's and posix's under it, pty's spawn, the fork and exec under subprocess, a browser's opener, the standard
# library's own starts of `file` (platform.architecture) and of `uname -p` (platform.processor, and platform.platform,
# which asks it), and a pool of processes or multiprocessing's, which fork by default on Linux before 3.14.
STARTS = {
    "subprocess.Popen",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "asyncio.subprocess.create_subprocess_exec",
    "asyncio.subprocess.create_subprocess_shell",
    "os.system",
    "os.popen",
    "os.posix_spawn",
    "os.posix_spawnp",
    "pty.spawn",
    "concurrent.futures.ProcessPoolExecutor",
    "concurrent.futures.process.ProcessPoolExecutor",
    "_posixsubprocess.fork_exec",
    "platform.architecture",
    "platform.processor",
    "platform.platform",
}
STARTS_PREFIXES = ("os.spawn", "multiprocessing.", "posix.", "webbrowser.")
# The modules that start a child, which a name built at run time could reach: imported by a name (importlib's, or
# __import__), or an attribute of one asked of getattr. The new engine imports none of them so, and asks none so.
STARTING_MODULES = {
    "subprocess",
    "asyncio",
    "asyncio.subprocess",
    "os",
    "posix",
    "pty",
    "multiprocessing",
    "concurrent.futures",
    "_posixsubprocess",
    "webbrowser",
}
# What starts a child by its name on any object: a loop's own, and the two names under any alias of their module.
STARTS_ANYWHERE = {"Popen", "create_subprocess_exec", "create_subprocess_shell", "subprocess_exec", "subprocess_shell"}
# What forks a process with no exec after it.
FORKS = {"os.fork", "os.forkpty", "pty.fork", "posix.fork", "posix.forkpty"}


def _code() -> list[Path]:
    """The new engine's code: every Python file of its subpackages and its modules (secret.py), and tools/aw."""
    wanted = [where for name in NEW_MODULES for where in (f"lib/claude_vm/{name}", f"lib/claude_vm/{name}.py")]
    found = [path for path in repo_files(*wanted, untracked=True)]
    return [*(path for path in found if path.suffix == ".py"), AW]


def _names(tree: ast.AST) -> dict[str, str]:
    """Each name a module's imports bind, with the dotted name it stands for."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # `import a.b` binds a; `import a.b as c` binds c to a.b.
                bound = alias.name if alias.asname else alias.name.split(".")[0]
                names[alias.asname or bound] = bound
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _dotted(node: ast.AST, names: dict[str, str]) -> str | None:
    """The dotted name an attribute or a name stands for, its head read through the imports; None for anything else."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return ".".join([names.get(node.id, node.id), *reversed(parts)])


def _starts(text: str) -> list[str]:
    """Each place source starts a child process, by its line."""
    tree = ast.parse(text)
    names, found = _names(tree), []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Attribute, ast.Name)):
            continue
        dotted = _dotted(node, names) or ""
        last = node.attr if isinstance(node, ast.Attribute) else node.id
        if dotted in STARTS or dotted.startswith(STARTS_PREFIXES) or last in STARTS_ANYWHERE:
            found.append(f"line {node.lineno}: {dotted or last}")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        called = _dotted(node.func, names) or ""
        first = node.args[0]
        if called in ("importlib.import_module", "__import__"):
            if isinstance(first, ast.Constant) and first.value in STARTING_MODULES:
                found.append(f"line {node.lineno}: {first.value} imported by its name")
        elif called == "getattr" and (_dotted(first, names) or "") in STARTING_MODULES:
            found.append(f"line {node.lineno}: an attribute of {_dotted(first, names)} by its name")
    return found


def _forks(text: str) -> list[str]:
    """Each place source forks with no exec: os.fork, os.forkpty, pty.fork, and a fork start method or context."""
    tree = ast.parse(text)
    names, found = _names(tree), []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Name)) and _dotted(node, names) in FORKS:
            found.append(f"line {node.lineno}: {_dotted(node, names)}")
        called = (_dotted(node.func, names) or "").rsplit(".", 1)[-1] if isinstance(node, ast.Call) else ""
        if called in ("set_start_method", "get_context"):
            given = [*node.args[:1], *(keyword.value for keyword in node.keywords if keyword.arg == "method")]
            if any(isinstance(each, ast.Constant) and each.value == "fork" for each in given):
                found.append(f"line {node.lineno}: a fork start method")
            elif any(not isinstance(each, ast.Constant) for each in given):
                found.append(f"line {node.lineno}: a start method from a name")
    return found


def _closes_no_fds(text: str) -> list[str]:
    """Each call that passes close_fds anything but True, a constant: a false one, a name or an expression, or one in a
    mapping spread into the call."""
    found = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "close_fds" and not (
                    isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                ):
                    found.append(f"line {node.lineno}: close_fds={ast.unparse(keyword.value)}")
        elif isinstance(node, ast.Dict):
            if any(isinstance(key, ast.Constant) and key.value == "close_fds" for key in node.keys):
                found.append(f"line {node.lineno}: close_fds in a mapping")
    return found


def _as_it_was(flag: ast.expr, call: ast.Call) -> bool:
    """Whether a dup2's inheritable flag is the one its target has already, get_inheritable of the same descriptor, as
    runs/live.py points a broken pipe's standard output at /dev/null: the copy no more inheritable than the descriptor
    it replaces, so nothing is handed to a child that it was not handed before."""
    return (
        isinstance(flag, ast.Call)
        and getattr(flag.func, "attr", getattr(flag.func, "id", "")) == "get_inheritable"
        and len(flag.args) == 1
        and len(call.args) >= 2
        and ast.dump(flag.args[0]) == ast.dump(call.args[1])
    )


def _hands_over(text: str) -> list[tuple[int, str]]:
    """Each place source names pass_fds, or makes a descriptor inheritable, by its line."""
    found = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.keyword) and node.arg == "pass_fds":
            found.append((node.value.lineno, "pass_fds="))
        elif isinstance(node, ast.arg) and node.arg == "pass_fds":
            found.append((node.lineno, "a parameter pass_fds"))
        elif isinstance(node, ast.Name) and node.id == "pass_fds":
            found.append((node.lineno, "pass_fds"))
        elif isinstance(node, ast.Attribute) and node.attr == "pass_fds":
            found.append((node.lineno, ".pass_fds"))
        elif (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", getattr(node.func, "id", "")) == "set_inheritable"
            and not (node.args and isinstance(node.args[-1], ast.Constant) and node.args[-1].value is False)
        ):
            found.append((node.lineno, "set_inheritable"))
        elif isinstance(node, ast.Dict) and any(
            isinstance(key, ast.Constant) and key.value == "pass_fds" for key in node.keys
        ):
            found.append((node.lineno, "pass_fds in a mapping"))
        elif (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", getattr(node.func, "id", "")) == "dup2"
            and not any(
                keyword.arg == "inheritable"
                and (
                    (isinstance(keyword.value, ast.Constant) and keyword.value.value is False)
                    or _as_it_was(keyword.value, node)
                )
                for keyword in node.keywords
            )
        ):
            # os.dup2 makes the copy inheritable unless told not to, or told to keep the flag its target has.
            found.append((node.lineno, "dup2"))
        elif isinstance(node, (ast.Attribute, ast.Name)) and (
            node.attr if isinstance(node, ast.Attribute) else node.id
        ) in ("F_SETFD", "F_DUPFD", "pipe2"):
            # A descriptor's close-on-exec cleared, a copy made without it, or a pipe made with what flags say.
            found.append((node.lineno, node.attr if isinstance(node, ast.Attribute) else node.id))
    return found


def _in_start(text: str) -> range:
    """The lines of children.py's own start."""
    [start] = [
        node
        for node in ast.parse(text).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "start"
    ]
    assert start.end_lineno is not None
    return range(start.lineno, start.end_lineno + 1)


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO))


def test_a_child_process_is_started_only_through_children_py() -> None:
    code = _code()
    assert REPO / "lib" / "claude_vm" / "runs" / "children.py" in code, code
    assert REPO / "lib" / "claude_vm" / "secret.py" in code, code
    found = {_rel(path): where for path in code if _rel(path) not in STARTERS and (where := _starts(path.read_text()))}
    assert found == {}, "a child of a run is started by runs/children.py's start, the SSH runner's too"
    assert all(reason.strip() for reason in STARTERS.values())


def test_close_fds_is_never_turned_off() -> None:
    found = {_rel(path): where for path in _code() if (where := _closes_no_fds(path.read_text()))}
    assert found == {}, "a child of a run has every descriptor closed but those it is handed"


def test_no_process_is_forked_without_an_exec() -> None:
    # A forked child keeps every descriptor, a lock's among them, until it execs: one that never does holds the lock
    # past its run's end.
    found = {_rel(path): where for path in _code() if (where := _forks(path.read_text()))}
    assert found == {}, "a process is forked only to exec at once, as children.start's does"


def test_pass_fds_and_set_inheritable_appear_only_in_children_start() -> None:
    found: dict[str, list] = {}
    for path in _code():
        text = path.read_text()
        allowed = _in_start(text) if _rel(path) == "lib/claude_vm/runs/children.py" else range(0)
        if outside := [where for where in _hands_over(text) if where[0] not in allowed]:
            found[_rel(path)] = outside
    assert found == {}, "a descriptor is handed to a child on purpose only through children.start's pass_fds"
    # And there, it is: the scan sees start's own.
    children_py = REPO / "lib" / "claude_vm" / "runs" / "children.py"
    assert _hands_over(children_py.read_text())


def test_what_a_forked_child_runs_before_its_exec_is_two_calls_of_signal_s_and_nothing_else() -> None:
    # children._default_signals is every start's preexec_fn: Python runs it in a child forked beside the run's threads,
    # where a lock another thread held at the fork stays held for good, so it calls signal.signal and
    # signal.pthread_sigmask alone, which take none - no import, no log, no print, no file (review/code/C23.md 3).
    children_py = REPO / "lib" / "claude_vm" / "runs" / "children.py"
    tree = ast.parse(children_py.read_text())
    names = _names(tree)
    [function] = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == children._default_signals.__name__
    ]
    calls = {
        _dotted(node.func, names) or ast.unparse(node.func) for node in ast.walk(function) if isinstance(node, ast.Call)
    }
    assert calls == {"signal.signal", "signal.pthread_sigmask"}, calls
    assert not [
        node for node in ast.walk(function) if isinstance(node, (ast.Import, ast.ImportFrom, ast.With, ast.Global))
    ]
    # And it is what every start in the module hands Python to run there: no other function is run before an exec.
    given = [
        keyword.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "preexec_fn"
    ]
    assert len(given) == 2 and all(isinstance(each, ast.Name) and each.id == function.name for each in given), given


def test_every_module_of_runs_that_takes_a_lock_is_one_holders_reads() -> None:
    # A lock taken in a module whose locks the refusal never reads could be handed to a child. A flock is found as the
    # locks' own scan finds one, by any import of it (review/code/C6.md 6).
    taking = set()
    for path in repo_files("lib/claude_vm/runs", untracked=True):
        if path.suffix != ".py":
            continue
        rel = path.relative_to(REPO).as_posix()
        if "a flock" in _flocks_and_lock_folders(rel, path.read_text()):
            taking.add(".".join(path.relative_to(REPO / "lib").with_suffix("").parts))
    assert "claude_vm.runs.job" in taking
    assert {holder.__module__ for holder in children.HOLDERS} == taking


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("import subprocess\nsubprocess.Popen(['x'])", ["line 2: subprocess.Popen"]),
        ("import subprocess as sp\nsp.run(['x'])", ["line 2: subprocess.run"]),
        ("from subprocess import check_output\ncheck_output(['x'])", ["line 2: subprocess.check_output"]),
        ("import asyncio\nasyncio.create_subprocess_exec('x')", ["line 2: asyncio.create_subprocess_exec"]),
        (
            "from asyncio import subprocess as s\ns.create_subprocess_shell('x')",
            ["line 2: asyncio.subprocess.create_subprocess_shell"],
        ),  # noqa: E501
        ("loop.subprocess_exec(factory, 'x')", ["line 1: loop.subprocess_exec"]),
        ("import os\nos.system('x')", ["line 2: os.system"]),
        ("import os\nos.posix_spawn('x', ['x'], {})", ["line 2: os.posix_spawn"]),
        ("import os\nos.spawnv(os.P_WAIT, 'x', ['x'])", ["line 2: os.spawnv"]),
        ("import pty\npty.spawn('x')", ["line 2: pty.spawn"]),
        ("from multiprocessing import Process\nProcess(target=f)", ["line 2: multiprocessing.Process"]),
        (
            "import concurrent.futures\nconcurrent.futures.ProcessPoolExecutor()",
            ["line 2: concurrent.futures.ProcessPoolExecutor"],
        ),  # noqa: E501
        ("import posix\nposix.system('x')", ["line 2: posix.system"]),
        ("import posix\nposix.posix_spawn('x', ['x'], {})", ["line 2: posix.posix_spawn"]),
        ("import _posixsubprocess\n_posixsubprocess.fork_exec()", ["line 2: _posixsubprocess.fork_exec"]),
        ("import webbrowser\nwebbrowser.open(url)", ["line 2: webbrowser.open"]),
        ("import webbrowser\nwebbrowser.get().open(url)", ["line 2: webbrowser.get"]),
        ("import platform\nplatform.architecture()", ["line 2: platform.architecture"]),
        ("from platform import architecture\narchitecture(sys.executable)", ["line 2: platform.architecture"]),
        ("import platform\nplatform.processor()", ["line 2: platform.processor"]),
        ("import platform\nplatform.platform()", ["line 2: platform.platform"]),
        ("import platform\nplatform.machine()", []),
        (
            "import importlib\nimportlib.import_module('subprocess').run(['x'])",
            ["line 2: subprocess imported by its name"],
        ),
        ("__import__('os').system('x')", ["line 1: os imported by its name"]),
        ("import os\ngetattr(os, 'system')('x')", ["line 2: an attribute of os by its name"]),
        ("import importlib\nimportlib.import_module(f'claude_vm.{module}')", []),
        ("import importlib\nimportlib.import_module('claude_vm.runs')", []),
        ("getattr(module, function)", []),
        ("import asyncio\nasyncio.run(main())", []),
        ("from claude_vm import platform\nplatform.system()", []),
        ("runs.run(draft, body)", []),
    ],
)
def test_the_scan_sees_each_way_to_start_a_child(source, found) -> None:
    assert _starts(source) == found


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("import os\nos.fork()", ["line 2: os.fork"]),
        ("from os import forkpty\nforkpty()", ["line 2: os.forkpty"]),
        ("import pty\npty.fork()", ["line 2: pty.fork"]),
        ("import multiprocessing\nmultiprocessing.set_start_method('fork')", ["line 2: a fork start method"]),
        ("import multiprocessing as mp\nmp.get_context(method='fork')", ["line 2: a fork start method"]),
        ("import posix\nposix.fork()", ["line 2: posix.fork"]),
        ("import posix\nposix.forkpty()", ["line 2: posix.forkpty"]),
        ("from multiprocessing import get_context as g\ng('fork')", ["line 2: a fork start method"]),
        ("import multiprocessing\nmultiprocessing.get_context(method)", ["line 2: a start method from a name"]),
        ("import multiprocessing\nmultiprocessing.get_context('spawn')", []),
        ("import os\nos.register_at_fork(after_in_child=f)", []),
    ],
)
def test_the_scan_sees_each_fork_with_no_exec(source, found) -> None:
    assert _forks(source) == found


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("subprocess.Popen(['x'], close_fds=False)", ["line 1: close_fds=False"]),
        ("subprocess.Popen(['x'], close_fds=0)", ["line 1: close_fds=0"]),
        ("subprocess.Popen(['x'], close_fds=flag)", ["line 1: close_fds=flag"]),
        ("subprocess.Popen(['x'], close_fds=not True)", ["line 1: close_fds=not True"]),
        ("subprocess.Popen(['x'], **{'close_fds': False})", ["line 1: close_fds in a mapping"]),
        ("subprocess.Popen(['x'], close_fds=True)", []),
    ],
)
def test_the_scan_sees_close_fds_turned_off(source, found) -> None:
    assert _closes_no_fds(source) == found


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("subprocess.Popen(['x'], pass_fds=(3,))", [(1, "pass_fds=")]),
        ("def f(pass_fds=()):\n    pass", [(1, "a parameter pass_fds")]),
        ("given = dict(pass_fds=fds)", [(1, "pass_fds=")]),
        ("handed = self.pass_fds", [(1, ".pass_fds")]),
        ("os.set_inheritable(fd, True)", [(1, "set_inheritable")]),
        ("os.set_inheritable(fd, flag)", [(1, "set_inheritable")]),
        ("sock.set_inheritable(True)", [(1, "set_inheritable")]),
        ("subprocess.Popen(['x'], **{'pass_fds': (3,)})", [(1, "pass_fds in a mapping")]),
        ("os.dup2(lock, 9)", [(1, "dup2")]),
        ("os.dup2(lock, 9, inheritable=True)", [(1, "dup2")]),
        ("os.dup2(null, fd, inheritable=os.get_inheritable(lock))", [(1, "dup2")]),
        ("fcntl.fcntl(fd, fcntl.F_SETFD, 0)", [(1, "F_SETFD")]),
        ("fcntl.fcntl(fd, fcntl.F_DUPFD, 10)", [(1, "F_DUPFD")]),
        ("os.pipe2(0)", [(1, "pipe2")]),
        ("os.dup2(lock, 9, inheritable=False)", []),
        ("os.dup2(null, fd, inheritable=os.get_inheritable(fd))", []),
        ("fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10)", []),
        ("os.set_inheritable(fd, False)", []),
    ],
)
def test_the_scan_sees_each_descriptor_handed_over(source, found) -> None:
    assert _hands_over(source) == found
