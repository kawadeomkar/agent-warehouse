"""The one way a run starts a child (lib/claude_vm/runs/children.py; docs/architecture/engine.md §16.8).

Each child here is a real process, a probe that reports what it was given: its environment's marker, its process group
and session, the signals it ignores or blocks, and the descriptors it has open. The rules with a test of their own: in
the new engine's code a child process is started only through children.py, the SSH runner's among them, by a scan for
every way Python starts one; close_fds=False appears nowhere there; no process is forked without an exec (a forked child
keeps every lock the run holds, since a descriptor that is not inheritable is closed only at an exec); pass_fds and
set_inheritable(..., True) appear only in children.start; and every module of runs/ that takes a kernel lock is one
children.HOLDERS reads, so that its locks are refused too.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from harness import REPO, repo_files
from test_aw_layout import NEW

from claude_vm.fleet import paths
from claude_vm.runs import children
from claude_vm.runs import job as jobs
from claude_vm.schema import patterns

AW = REPO / "tools" / "aw"
PIPE, DEVNULL = asyncio.subprocess.PIPE, asyncio.subprocess.DEVNULL
OTHER_JOB = "0123456789abcdef"

# A child that says what it was given, as JSON on its standard output: its pid, process group and session, the job's
# marker, which of the four signals it ignores or blocks, every descriptor it has open past its standard streams, and
# what it read from the descriptor named as its argument, where one is.
PROBE = r"""
import json, os, resource, signal, sys
SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)
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
    "ignored": [one.name for one in SIGNALS if signal.getsignal(one) == signal.SIG_IGN],
    "blocked": sorted(one.name for one in SIGNALS if one in blocked),
    "fds": fds,
    "read": read,
}))
"""
# A child that waits until its standard input closes.
WAITS = "import sys; sys.stdin.read()"


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


def test_env_gives_the_job_s_marker_and_unmarked_takes_it_away(made) -> None:
    assert children.MARKER == "AGENT_WAREHOUSE_JOB" and patterns.full("job_id", made.id)
    base = {"PATH": "/usr/bin", children.MARKER: OTHER_JOB}
    marked = children.env(base, made)
    assert marked == {"PATH": "/usr/bin", children.MARKER: made.id}
    assert children.unmarked(marked) == {"PATH": "/usr/bin"}
    # Copies, each: what was given is left as it was.
    assert base == {"PATH": "/usr/bin", children.MARKER: OTHER_JOB} and children.MARKER in marked


def test_only_the_run_that_made_a_job_marks_a_child_with_it(made, nothing_starts) -> None:
    other = jobs.Job(made.folder.parent / OTHER_JOB)
    with pytest.raises(RuntimeError, match="not this process's"):
        children.env({}, other)
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
    found = _probe(made, own, kind=children.MASTER, env=children.env(os.environ, made))
    assert found["marker"] is None
    assert children.live(made) == []
    with pytest.raises(ValueError, match="no registry"):
        children.register(made, children.MASTER, object())  # type: ignore[arg-type]


@pytest.mark.parametrize("own", children.OWN)
def test_a_child_has_the_signals_at_their_defaults_even_where_the_run_ignores_them(made, own) -> None:
    # As a run started under nohup or with & is: SIGINT, SIGHUP and SIGQUIT ignored; SIGTERM blocked besides.
    previous = {one: signal.getsignal(one) for one in (signal.SIGINT, signal.SIGHUP, signal.SIGQUIT)}
    assert None not in previous.values(), "a handler this test could not put back"
    ignored = {one: signal.signal(one, signal.SIG_IGN) for one in previous}
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    try:
        # A child started any other way keeps them so: the probe sees what this test set.
        plain = json.loads(
            subprocess.run([sys.executable, "-I", "-c", PROBE], capture_output=True, check=True, timeout=60).stdout
        )
        found = _probe(made, own)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        for one, handler in ignored.items():
            signal.signal(one, handler)
    assert (plain["ignored"], plain["blocked"]) == (["SIGINT", "SIGHUP", "SIGQUIT"], ["SIGTERM"])
    assert (found["ignored"], found["blocked"]) == ([], [])


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
    with pytest.raises(error, match=said):
        asyncio.run(children.start(made, **words))
    assert nothing_starts == [] and children.live(made) == []


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
        # One started another way, entered by hand.
        runner = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", WAITS, stdin=PIPE, env=children.env(os.environ, made), process_group=0
        )
        entered = children.register(made, children.SSH, runner)
        assert (entered.kind, entered.pid) == (children.SSH, runner.pid)
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


def test_register_takes_only_a_kind_it_knows_and_a_job_of_this_process_s(made) -> None:
    with pytest.raises(ValueError, match="no kind of child"):
        children.register(made, "editor", object())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="not this process's"):
        children.register(jobs.Job(made.folder.parent / OTHER_JOB), children.SSH, object())  # type: ignore[arg-type]
    assert children.live(made) == []


# ---------------------------------------------------------------- the rules ----

# The modules of the new engine's code that start a child process, each with why: the SSH runner starts its ssh
# children through children.start too. route.py, which opens and checks the hop to a host's VMs, is today's and lies
# outside it.
STARTERS = {
    "lib/claude_vm/runs/children.py": "the one way a run starts a child",
}
# What starts a child process, by the name it is imported under: each of subprocess's and asyncio's ways, the os
# module's, pty's spawn, and a pool of processes or multiprocessing's, which fork by default on Linux before 3.14.
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
}
STARTS_PREFIXES = ("os.spawn", "multiprocessing.")
# What starts a child by its name on any object: a loop's own, and the two names under any alias of their module.
STARTS_ANYWHERE = {"Popen", "create_subprocess_exec", "create_subprocess_shell", "subprocess_exec", "subprocess_shell"}
# What forks a process with no exec after it.
FORKS = {"os.fork", "os.forkpty", "pty.fork"}


def _code() -> list[Path]:
    """The new engine's code: every Python file of its subpackages, and tools/aw."""
    found = [path for path in repo_files(*(f"lib/claude_vm/{name}" for name in NEW), untracked=True)]
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
    return found


def _forks(text: str) -> list[str]:
    """Each place source forks with no exec: os.fork, os.forkpty, pty.fork, and a fork start method or context."""
    tree = ast.parse(text)
    names, found = _names(tree), []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Name)) and _dotted(node, names) in FORKS:
            found.append(f"line {node.lineno}: {_dotted(node, names)}")
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in (
            "set_start_method",
            "get_context",
        ):
            given = [*node.args[:1], *(keyword.value for keyword in node.keywords if keyword.arg == "method")]
            if any(isinstance(each, ast.Constant) and each.value == "fork" for each in given):
                found.append(f"line {node.lineno}: a fork start method")
    return found


def _closes_no_fds(text: str) -> list[str]:
    """Each call that passes close_fds a false constant."""
    return [
        f"line {node.lineno}: close_fds={keyword.value.value!r}"
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "close_fds" and isinstance(keyword.value, ast.Constant) and not keyword.value.value
    ]


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


def test_every_module_of_runs_that_takes_a_lock_is_one_holders_reads() -> None:
    # A lock taken in a module whose locks the refusal never reads could be handed to a child.
    taking = set()
    for path in repo_files("lib/claude_vm/runs", untracked=True):
        if path.suffix != ".py":
            continue
        tree = ast.parse(path.read_text())
        if any(isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "flock" for node in ast.walk(tree)):
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
        ("os.set_inheritable(fd, False)", []),
    ],
)
def test_the_scan_sees_each_descriptor_handed_over(source, found) -> None:
    assert _hands_over(source) == found
