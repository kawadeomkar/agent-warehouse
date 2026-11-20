"""A job: one run's folder, its record and its own kernel lock (lib/claude_vm/runs/job.py; docs/architecture/engine.md
§16.8).

Each state is made by a run of a child process of the test's, as a real run is a process of its own: the job's lock is
held while that process lives and let go by the kernel when it ends, however it ends. The rules with a test of their
own: a job's state is derived from its record and that lock alone, never from a pid, a process's start or the process
table (a scan of runs/, and a record whose pid lies); a child that outlives its killed run holds no lock; a job dropped
leaves no folder, and a reader never fails on one half removed.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import re
import select
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import harness
import pytest
from aw_kit import environment, imports_of, searched_for
from harness import REPO, repo_files
from test_aw_layout import _imported, _package

from claude_vm.fleet import paths
from claude_vm.runs import job as jobs
from claude_vm.schema import patterns
from claude_vm.secret import Secret

LIB = REPO / "lib"
RUNS = LIB / "claude_vm" / "runs"
SECRET = "unit-test-aw-job-secret"

# The modules of runs/ that may import claude_vm.proc, the walk of the process table, each with why: to end a run's
# tree, never to decide a state. Empty until a card needs one (C4's runs/attached.py, which ends the tree on Ctrl-C).
PROCESS_TABLE: dict[str, str] = {}
# What reads a process's start or the process table, in any module of runs/: ps's start column, Linux's /proc, a
# library of processes, today's MCP jobs' reading of a start (mcp/jobs.py's started, desktop.py's PS_ENV), a pidfd.
READS_A_PROCESS = re.compile(r"lstart|/proc\b|\bpsutil\b|\bPS_ENV\b|\bpidfd")
NEVER_IMPORTED = ("claude_vm.mcp", "claude_vm.desktop")

# A run in a child process: it makes its job, says the job's ID (and a child's pid of its own where asked), waits for
# the test's line, then ends as asked: with an outcome, or with none.
CHILD = """
import subprocess, sys
sys.path.insert(0, {lib!r})
from pathlib import Path
from claude_vm.runs import job as jobs

then = sys.argv[2]
made = jobs.create(Path(sys.argv[1]), "deploy", "aw deploy alpha", "pve", ["alpha"], {{"kind": "terminal"}}, True)
said = [made.id]
if then == "sleeper":
    # A child of the run's that outlives it: started with close_fds off even, in a session of its own, so that the
    # run's death does not reach it.
    said.append(str(subprocess.Popen(["sleep", "300"], close_fds=False, start_new_session=True).pid))
if then == "next":
    print(*said, flush=True)
    sys.stdin.readline()
    for i in range(100):
        made.emit("next", "alpha", f"line {{i}}", matter=f"m{{i}}")
    sys.exit(0)
print(*said, flush=True)
sys.stdin.readline()
if then in jobs.OUTCOMES:
    made.emit("end", None, "as asked", outcome=then, exit=0 if then == "succeeded" else 1)
"""


@pytest.fixture
def jobs_dir(home: Path) -> Path:
    """The jobs folder of the test's own fleet home, as fleet/paths.py names it."""
    return paths.jobs_dir(paths.home(REPO))


@contextlib.contextmanager
def _run(jobs_dir: Path, then: str, home: Path) -> Iterator[tuple[subprocess.Popen, list[str]]]:
    """A run in a child process, which has made its job, and what it said: the job's ID, then a child's pid where
    asked. Ended whole on the way out where it still runs."""
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", CHILD.format(lib=str(LIB)), str(jobs_dir), then],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment(home),
        preexec_fn=harness._default_signals,
    )
    try:
        assert process.stdout is not None
        if not select.select([process.stdout], [], [], 60)[0]:
            pytest.fail("the run made no job within a minute")
        said = process.stdout.readline().split()
        if not said:
            pytest.fail(f"the run made no job: {process.communicate(timeout=30)[1]}")
        yield process, said
    finally:
        if process.poll() is None:
            harness._end(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _go(process: subprocess.Popen) -> int:
    """The test's line to a run, and how it ended."""
    assert process.stdin is not None
    process.stdin.write("go\n")
    process.stdin.close()
    return process.wait(timeout=60)


def _made(jobs_dir: Path, **given) -> jobs.Job:
    """A job of this process's own, for what a run does in its process."""
    words = {
        "verb": "deploy",
        "command": "aw deploy alpha",
        "host": "pve",
        "machines": ["alpha"],
        "starter": {"kind": "terminal"},
        "attached": True,
    }
    words.update(given)
    return jobs.create(jobs_dir, **words)


@pytest.fixture
def made(jobs_dir: Path) -> Iterator[jobs.Job]:
    """A job of this process's own, dropped after the test: its lock let go before the next."""
    job = _made(jobs_dir)
    yield job
    if job._held_here() is not None:
        jobs.drop(job)


def _folder(job: jobs.Job) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(job.folder.iterdir())}


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _free(lock: Path) -> bool:
    """Whether an exclusive lock on the file is free, asked from a process of its own."""
    script = (
        "import fcntl, os, sys\n"
        "held = os.open(sys.argv[1], os.O_RDWR)\n"
        "try:\n    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)\nexcept BlockingIOError:\n    sys.exit(3)\n"
    )
    found = subprocess.run([sys.executable, "-I", "-c", script, str(lock)], timeout=60, check=False)
    assert found.returncode in (0, 3), found
    return found.returncode == 0


# ---------------------------------------------------------------- the folder and the record ----


def test_a_job_is_a_folder_of_its_own_its_files_kept_to_the_owner(jobs_dir, made) -> None:
    assert jobs.find(jobs_dir, made.id) is not None and patterns.full("job_id", made.id)
    assert made.folder == jobs_dir / made.id
    # The state folder and the jobs folder made with it, where there were none.
    assert [_mode(folder) for folder in (jobs_dir.parent, jobs_dir, made.folder)] == [0o700] * 3
    assert sorted(path.name for path in made.folder.iterdir()) == ["job.json", "lock"]
    assert [_mode(made.folder / name) for name in ("job.json", "lock")] == [0o600, 0o600]
    # Its lock is held, by a descriptor no child inherits.
    held = made._held_here()
    assert held is not None and not os.get_inheritable(held)
    assert not _free(made.lock)


def test_the_record_holds_what_the_run_is(jobs_dir) -> None:
    before = time.time()
    job = _made(
        jobs_dir,
        command="aw deploy alpha --set vm_cores=4",
        machines=["alpha", "beta"],
        starter={"kind": "mcp", "server": "0f1e2d3c"},
        attached=False,
        inputs={"vm_cores": 4, "names": ["alpha", "beta"]},
    )
    try:
        record = json.loads(job.record.read_text())
        assert list(record) == [name for name in jobs.FIELDS if name not in ("outcome", "exit", "ended", "why")]
        assert record == {
            "id": job.id,
            "verb": "deploy",
            "command": "aw deploy alpha --set vm_cores=4",
            "inputs": {"vm_cores": 4, "names": ["alpha", "beta"]},
            "starter": {"kind": "mcp", "server": "0f1e2d3c"},
            "host": "pve",
            "machines": ["alpha", "beta"],
            "locks": [],
            "attached": False,
            "pid": os.getpid(),
            "started": record["started"],
            "deadline": None,
            "stop": None,
            "directory": None,
            "log": None,
            "waiting": None,
            "next": [],
            "machine_outcomes": {},
        }
        assert before <= record["started"] <= time.time()
        # What later cards write: the locks held, the run's directory and log, a deadline, a stop.
        job.update(locks=["fleet", "machine-pve@alpha"], directory=jobs_dir / "x", log=Path("/l/run.log"), deadline=9.5)
        assert {name: job.read()[name] for name in ("locks", "directory", "log", "deadline")} == {
            "locks": ["fleet", "machine-pve@alpha"],
            "directory": str(jobs_dir / "x"),
            "log": "/l/run.log",
            "deadline": 9.5,
        }
        for field in ("outcomes", "id"):
            with pytest.raises(ValueError, match="no field"):
                job.update(**{field: "x"})
    finally:
        jobs.drop(job)


@pytest.mark.parametrize(
    ("given", "error"),
    [
        ({"starter": {"kind": "mcp"}}, ValueError),
        ({"starter": {"kind": "terminal", "server": "0f1e"}}, ValueError),
        ({"starter": {"kind": "cron"}}, ValueError),
        ({"starter": {"kind": "mcp", "server": "a\nb"}}, ValueError),
        ({"verb": ""}, ValueError),
        ({"machines": "alpha"}, TypeError),
        ({"attached": "yes"}, TypeError),
        ({"inputs": {"token": Secret(SECRET)}}, TypeError),
        ({"command": Secret(SECRET)}, TypeError),
        ({"inputs": {"when": object()}}, TypeError),
    ],
)
def test_a_job_is_refused_before_anything_is_made(jobs_dir, given, error) -> None:
    with pytest.raises(error):
        _made(jobs_dir, **given)
    assert not jobs_dir.exists()


def test_two_writers_of_one_record_lose_neither_change(jobs_dir, home) -> None:
    # The run's events and another process's writes (a cancel recording its stop) at once, each read, changed and
    # replaced whole: unlocked, one would replace the other's with what it read before.
    writer = (
        "import sys\n"
        f"sys.path.insert(0, {str(LIB)!r})\n"
        "from claude_vm.runs import job as jobs\n"
        "found = jobs.find(sys.argv[1], sys.argv[2])\n"
        "sys.stdin.readline()\n"
        "for i in range(100):\n    found.update(deadline=i)\n"
    )
    with _run(jobs_dir, "next", home) as (run, (job_id,)):
        other = subprocess.Popen(
            [sys.executable, "-I", "-c", writer, str(jobs_dir), job_id],
            stdin=subprocess.PIPE,
            text=True,
            env=environment(home),
        )
        try:
            assert other.stdin is not None
            other.stdin.write("go\n")
            other.stdin.close()
            assert _go(run) == 0 and other.wait(timeout=120) == 0
        finally:
            if other.poll() is None:
                harness._end(other)
    record = jobs.find(jobs_dir, job_id).read()
    assert record["deadline"] == 99
    assert record["next"] == [{"machine": "alpha", "matter": f"m{i}", "line": f"line {i}"} for i in range(100)]


# ---------------------------------------------------------------- the states ----


def test_a_living_run_reads_running_and_cancelling_once_a_stop_is_recorded(jobs_dir, home) -> None:
    with _run(jobs_dir, "cancelled", home) as (run, (job_id,)):
        found = jobs.find(jobs_dir, job_id)
        assert jobs.state(found) == "running"
        found.update(stop={"by": "cancel", "at": time.time()})
        assert jobs.state(found) == "cancelling"
        assert _go(run) == 0
    assert jobs.state(found) == "cancelled"


@pytest.mark.parametrize(("then", "read"), [(o, o) for o in jobs.OUTCOMES] + [("exit", "lost")])
def test_an_ended_run_reads_its_outcome_and_one_with_none_is_lost(jobs_dir, home, then, read) -> None:
    with _run(jobs_dir, then, home) as (run, (job_id,)):
        assert _go(run) == 0
    found = jobs.find(jobs_dir, job_id)
    assert jobs.state(found) == read and _free(found.lock)
    if then == "exit":
        assert "outcome" not in found.read()


def test_a_run_killed_outright_reads_lost_with_nothing_written_after_it_died(jobs_dir, home) -> None:
    with _run(jobs_dir, "succeeded", home) as (run, (job_id,)):
        found = jobs.find(jobs_dir, job_id)
        assert jobs.state(found) == "running"
        before = _folder(found)
        # While the run waits on its stdin: nothing forks.
        os.kill(run.pid, signal.SIGKILL)
        assert run.wait(timeout=60) == -signal.SIGKILL
    assert jobs.state(found) == "lost" and _folder(found) == before and _free(found.lock)


def test_a_child_that_outlives_its_killed_run_holds_no_lock(jobs_dir, home) -> None:
    # Started as Python starts any child, close_fds off even: the job's descriptor is not inheritable, so the kernel
    # lets the lock go with the run, while the child still sleeps.
    with _run(jobs_dir, "sleeper", home) as (run, (job_id, sleeper)):
        try:
            os.kill(run.pid, signal.SIGKILL)
            run.wait(timeout=60)
            os.kill(int(sleeper), 0)  # still there
            found = jobs.find(jobs_dir, job_id)
            assert jobs.state(found) == "lost" and _free(found.lock)
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(sleeper), signal.SIGKILL)


def test_the_pid_in_the_record_never_decides_a_state(jobs_dir, home, made) -> None:
    # A record naming a living process (this one) whose lock is free is lost; one naming a process long gone whose
    # lock is held runs.
    with _run(jobs_dir, "exit", home) as (run, (job_id,)):
        assert _go(run) == 0
    found = jobs.find(jobs_dir, job_id)
    found.update(pid=os.getpid(), started=time.time())
    assert jobs.state(found) == "lost"
    gone = subprocess.Popen(["true"])
    gone.wait()
    made.update(pid=gone.pid, started=0)
    assert jobs.state(made) == "running" and jobs.state(jobs.find(jobs_dir, made.id)) == "running"


def test_a_job_of_this_process_s_own_is_never_probed(made, monkeypatch) -> None:
    # On NFS Linux emulates flock with POSIX locks, which a process holds once per file: a probe's close would let the
    # run's own lock go. So the run's own job is read from what it holds.
    tried = []
    monkeypatch.setattr(jobs.fcntl, "flock", lambda *given: tried.append(given))
    assert jobs.state(jobs.find(made.folder.parent, made.id)) == "running" and tried == []


def test_only_the_run_that_made_a_job_emits_listens_or_drops(jobs_dir, home) -> None:
    with _run(jobs_dir, "succeeded", home) as (run, (job_id,)):
        found = jobs.find(jobs_dir, job_id)
        before = _folder(found)
        for act in (
            lambda: found.emit("report", "alpha", "x"),
            lambda: found.listen(print),
            lambda: jobs.drop(found),
        ):
            with pytest.raises(RuntimeError, match="not this process's"):
                act()
        assert _folder(found) == before and jobs.state(found) == "running"
        assert _go(run) == 0


# ---------------------------------------------------------------- events ----


def test_a_listener_hears_each_event_in_order_and_the_record_keeps_what_an_agent_reads(made) -> None:
    heard: list[tuple[str, jobs.Event]] = []
    made.listen(lambda event: heard.append(("view", event)))
    made.listen(lambda event: heard.append(("log", event)))
    made.emit("stage", "alpha", "provision")
    made.emit("step", "alpha", "clone the template")
    made.emit("waiting", "alpha", "Approve the sign-in in your browser", page="https://claude.ai/oauth/authorize")
    waiting = made.read()["waiting"]
    assert waiting == {
        "page": "https://claude.ai/oauth/authorize",
        "machine": "alpha",
        "text": "Approve the sign-in in your browser",
        "since": waiting["since"],
    }
    made.emit("child", "alpha", "ansible-playbook")
    made.emit("next", "alpha", "aw login alpha --only claude", matter="claude")
    made.emit("stage", "alpha", "configure")
    made.emit("waiting", "alpha", "")
    assert made.read()["waiting"] is None
    made.emit("next", "alpha", "aw login alpha --only github", matter="github")
    made.emit("next", "alpha", "aw login alpha", matter="claude")
    assert [step["line"] for step in made.read()["next"]] == [
        "aw login alpha",
        "aw login alpha --only github",
    ]
    made.emit("next", "alpha", "", matter="claude")
    made.emit("end", "alpha", "configured", outcome="succeeded")
    made.emit("end", "beta", "the clone failed", outcome="failed", stage="provision")
    made.emit("report", None, "alpha is ready")
    made.emit("end", None, "one machine failed", outcome="failed", exit=1)
    kinds = ["stage", "step", "waiting", "child", "next", "stage", "waiting", "next", "next", "next", "end", "end"]
    kinds += ["report", "end"]
    assert [event.kind for who, event in heard if who == "view"] == kinds
    # Each event to each listener, in the order they registered.
    assert [who for who, _ in heard] == ["view", "log"] * len(kinds)
    assert [a[1] for a in heard[::2]] == [b[1] for b in heard[1::2]]
    record = made.read()
    assert record["waiting"] is None
    assert record["next"] == [{"machine": "alpha", "matter": "github", "line": "aw login alpha --only github"}]
    assert record["machine_outcomes"] == {
        "alpha": {"outcome": "succeeded", "stage": "configure", "why": "configured"},
        "beta": {"outcome": "failed", "stage": "provision", "why": "the clone failed"},
    }
    assert {name: record[name] for name in ("outcome", "exit", "why")} == {
        "outcome": "failed",
        "exit": 1,
        "why": "one machine failed",
    }
    assert heard[-1][1].time <= record["ended"] <= time.time()


def test_a_string_with_escape_sequences_is_plain_in_the_record_and_to_a_listener(jobs_dir) -> None:
    job = _made(jobs_dir, command="aw deploy \x1b[31malpha\x1b[0m")
    try:
        heard = []
        job.listen(heard.append)
        # A clipboard write (OSC 52), a title, colours, a NUL: what a machine could print into a sign-in's page.
        job.emit(
            "waiting",
            "al\x1b]0;x\x07pha",
            "\x1b]52;c;aGk=\x07Open \x1b[31mhttps://claude.ai/x\x1b[0m\x00 in your browser",
            page="https://claude.ai/x\x1b]2;title\x07",
        )
        job.emit("next", "alpha", "\x1b[1mrun\x1b[0m aw login alpha", matter="claude\x07")
        record = job.read()
        assert record["command"] == "aw deploy alpha"
        assert {name: record["waiting"][name] for name in ("machine", "text", "page")} == {
            "machine": "alpha",
            "text": "Open https://claude.ai/x in your browser",
            "page": "https://claude.ai/x",
        }
        assert record["next"] == [{"machine": "alpha", "matter": "claude", "line": "run aw login alpha"}]
        assert heard[0].text == "Open https://claude.ai/x in your browser" and heard[0].machine == "alpha"
        assert "\x1b" not in job.record.read_text() and "\\u001b" not in job.record.read_text()
    finally:
        jobs.drop(job)


@pytest.mark.parametrize(
    "event",
    [
        ("waiting", "alpha", Secret(SECRET)),
        ("report", Secret(SECRET), "x"),
        ("report", "alpha", "x", {"token": Secret(SECRET)}),
        ("next", "alpha", "x", {"matter": "claude", "with": [{"deep": Secret(SECRET)}]}),
        ("end", None, "x", {"outcome": "failed", "exit": 1, "key": Secret(SECRET)}),
    ],
)
def test_a_secret_given_to_emit_raises_and_nothing_is_written_or_heard(made, event) -> None:
    heard = []
    made.listen(heard.append)
    before = _folder(made)
    kind, machine, text, *data = event
    with pytest.raises(TypeError, match="Secret"):
        made.emit(kind, machine, text, **(data[0] if data else {}))
    with pytest.raises(TypeError, match="Secret"):
        made.update(inputs={"token": Secret(SECRET)})
    assert heard == [] and _folder(made) == before
    assert searched_for([SECRET], made.folder) == []


@pytest.mark.parametrize(
    ("kind", "machine", "data", "said"),
    [
        ("progress", "alpha", {}, "no kind of event"),
        ("end", "alpha", {}, "outcome"),
        ("end", "alpha", {"outcome": "lost"}, "outcome"),
        ("end", None, {"outcome": "failed"}, "exit status"),
        ("end", None, {"outcome": "failed", "exit": True}, "exit status"),
        ("next", "alpha", {}, "matter"),
    ],
)
def test_an_event_that_is_not_one_is_refused_before_anything_is_written_or_heard(made, kind, machine, data, said):
    heard = []
    made.listen(heard.append)
    before = _folder(made)
    with pytest.raises(ValueError, match=said):
        made.emit(kind, machine, "x", **data)
    assert heard == [] and _folder(made) == before


# ---------------------------------------------------------------- reading and dropping ----


def test_listed_is_newest_first_and_find_takes_only_sixteen_hex_digits(jobs_dir) -> None:
    made = [_made(jobs_dir) for _ in range(3)]
    try:
        for when, job in zip((20.0, 30.0, 10.0), made, strict=True):
            job.update(started=when)
        assert [job.id for job in jobs.listed(jobs_dir)] == [made[1].id, made[0].id, made[2].id]
        one = made[0].id
        for asked in (one.upper(), one[:15], one + "0", "../" + one[3:], "0123456789abcdeg", "", None, 7):
            assert jobs.find(jobs_dir, asked) is None, asked
        assert jobs.find(jobs_dir, "0123456789abcdef") is None
        assert jobs.listed(jobs_dir / "none") == []
    finally:
        for job in made:
            jobs.drop(job)


def test_drop_removes_the_record_then_closes_the_lock_then_the_folder(jobs_dir, made, monkeypatch) -> None:
    # Removing the folder while the lock file is still open makes NFS rename that file .nfsXXXX, and the folder stays
    # (experiment 40, item 3).
    held, seen = made._held_here(), []
    close = os.close

    def closing(descriptor: int) -> None:
        # The lock's close alone: its number is free afterwards, and the removal's own descriptors may take it.
        if descriptor == held and not seen:
            seen.append((made.record.exists(), made.lock.exists(), made.folder.exists()))
        close(descriptor)

    monkeypatch.setattr(jobs.os, "close", closing)
    jobs.drop(made)
    assert seen == [(False, True, True)]
    assert not made.folder.exists() and list(jobs_dir.iterdir()) == []
    assert made._held_here() is None


def test_a_reader_passes_over_a_folder_half_made_or_half_removed(jobs_dir, made) -> None:
    # The steps of a create and of a drop, each as a reader may meet it: a folder with no record, with a lock and no
    # record, with a record and no lock (the drop's last moment, never read as alive), and one that is no job.
    empty, locked, unlocked = (jobs_dir / f"{n:016x}" for n in (1, 2, 3))
    empty.mkdir()
    locked.mkdir()
    (locked / "lock").touch()
    unlocked.mkdir()
    (unlocked / "job.json").write_text(json.dumps({"id": unlocked.name, "started": 5.0}))
    (jobs_dir / "not-a-job").mkdir()
    (jobs_dir / f"{4:016x}").mkdir()
    (jobs_dir / f"{4:016x}" / "job.json").write_text("{")
    assert [job.id for job in jobs.listed(jobs_dir)] == [made.id, unlocked.name]
    assert [jobs.find(jobs_dir, folder.name) for folder in (empty, locked)] == [None, None]
    assert jobs.state(jobs.find(jobs_dir, unlocked.name)) == "lost"


def test_a_reader_never_fails_while_jobs_are_made_and_dropped_beside_it(jobs_dir, home) -> None:
    churn = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(LIB)!r})\n"
        "from claude_vm.runs import job as jobs\n"
        "end = time.monotonic() + 3\n"
        "while time.monotonic() < end:\n"
        "    jobs.drop(jobs.create(sys.argv[1], 'deploy', 'aw deploy a', None, ['a'], {'kind': 'terminal'}, True))\n"
    )
    jobs_dir.mkdir(parents=True)
    other = subprocess.Popen([sys.executable, "-I", "-c", churn, str(jobs_dir)], env=environment(home))
    seen = set()
    try:
        while other.poll() is None:
            for job in jobs.listed(jobs_dir):
                seen.add(jobs.state(job))
                job.read()
    finally:
        if other.poll() is None:
            harness._end(other)
    assert other.returncode == 0 and seen <= set(jobs.STATES)
    assert list(jobs_dir.iterdir()) == []


# ---------------------------------------------------------------- the rules ----


def _reads_a_process(rel: str, text: str) -> list[str]:
    """What a module of runs/ does that reads a process's start or the process table: by its text, and by its syntax
    tree a ps run, a pid probed with signal 0, and an import of the walk (unless PROCESS_TABLE lists the module) or
    of today's jobs and their reading of a start."""
    found = [f"names {match[0]!r}" for match in READS_A_PROCESS.finditer(text)]
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.fullmatch(r"ps(\s.*)?", node.value):
            found.append("runs ps")
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", getattr(node.func, "id", "")) == "kill"
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == 0
        ):
            found.append("probes a pid")
    for module in _imported(tree, _package(LIB / rel) if rel.startswith("claude_vm/") else "claude_vm.runs"):
        if module == "claude_vm.proc" and rel not in PROCESS_TABLE:
            found.append("imports proc")
        if module.startswith(NEVER_IMPORTED):
            found.append(f"imports {module}")
    return found


def test_no_module_of_runs_reads_a_process_s_start_or_the_process_table() -> None:
    modules = [path for path in repo_files("lib/claude_vm/runs", untracked=True) if path.suffix == ".py"]
    assert RUNS / "job.py" in modules, modules
    found = {
        str(path.relative_to(LIB)): problems
        for path in modules
        if (problems := _reads_a_process(str(path.relative_to(LIB)), path.read_text()))
    }
    assert found == {}, "a job's state is its record and its own lock: never a pid, a start or the process table"
    assert all(reason.strip() for reason in PROCESS_TABLE.values())
    assert [rel for rel in PROCESS_TABLE if not (LIB / rel).is_file()] == []


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("subprocess.run(['ps', '-o', 'lstart=', '-p', str(pid)])", ["names 'lstart'", "runs ps"]),
        ("open(f'/proc/{pid}/stat').read()", ["names '/proc'"]),
        ("subprocess.run(['ps', '-axo', 'pid,ppid'])", ["runs ps"]),
        ("os.kill(record['pid'], 0)", ["probes a pid"]),
        ("from claude_vm import proc", ["imports proc"]),
        ("from .. import proc", ["imports proc"]),
        ("from claude_vm.mcp.jobs import started", ["imports claude_vm.mcp.jobs"]),
        ("from claude_vm.desktop import PS_ENV", ["names 'PS_ENV'", "imports claude_vm.desktop"]),
        ("import psutil", ["names 'psutil'"]),
        ("os.kill(pid, signal.SIGINT)", []),
        ("fcntl.flock(held, fcntl.LOCK_SH | fcntl.LOCK_NB)", []),
        ('"the process\'s start is never read"', []),
    ],
)
def test_the_scan_of_runs_sees_each_way_to_read_a_process(source, found) -> None:
    assert _reads_a_process("claude_vm/runs/x.py", source) == found


def test_importing_the_job_module_loads_no_pydantic_and_nothing_heavy(home) -> None:
    # logs, logs <job>, --follow and cancel read a job with the standard library alone.
    loaded = imports_of(module="claude_vm.runs.job", home=home)
    assert {
        "claude_vm.store",
        "claude_vm.text",
        "claude_vm.secret",
        "claude_vm.fleet.paths",
        "claude_vm.schema.patterns",
    } <= loaded
    heavy = {name for name in loaded if name.split(".")[0] in ("pydantic", "ansible", "yaml", "asyncio", "subprocess")}
    assert heavy == set()
