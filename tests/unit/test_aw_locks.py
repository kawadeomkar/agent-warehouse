"""The kernel's file locks a run holds: the fleet's, each machine's by its host and its name, and the short sections it
waits for (lib/claude_vm/runs/locks.py, and runs/__init__.py's run, which takes them; docs/architecture/engine.md
§16.8).

Each run here is a child process of the test's, made by runs.run as a verb makes one, with the state folder in the
test's own home: two runs on two machines go side by side; a second on one machine is refused at once, naming the
first's job, its command, its start and the line that cancels it as the printer writes it, and leaves no job; a run that
makes alpha and one that configures it take one lock; a VM, an adopted machine and one name on two hosts take
different locks; a run that names no machine and the runs that name one refuse each other; the sections wait, a build
for the clones and the clones for it, two creates one after the other, and a wait ends at the run's stop; a holder
killed with SIGKILL holds nothing and nothing is cleared; twenty runs at once on one machine let exactly one through;
run_lock: fleet refuses any second run, --no-lock none, and the sections still wait; exclusive and the runs refuse each
other, and nothing is left held after its block. A refusal never fails on what it reads. A signal between the job and
the ladder's handlers ends the run as the ladder says, and what a refused run's drop left is said.

The rules with a test of their own: no child of a run holds a lock (the child of a killed run, and C2's refusal of each
lock's descriptor); no module of runs/ keeps a pid or reads a process to decide a lock; only this module and job.py
take a flock or name the locks' folder; a machine's lock is named from its host and name alone; nothing asks F_GETLK;
the holder's record is never truncated; take of twenty machines beside 400 lock files and 400 ended jobs stays under a
tenth of a second; and no module but cli/before.py and schema/words.py names RUN_LOCK or ATTENDED in code.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import datetime
import errno
import fcntl
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import harness
import pytest
from aw_kit import environment
from harness import REPO, repo_files
from test_aw_job import _reads_a_process
from test_aw_layout import NEW
from test_processes import _until

from claude_vm import runs
from claude_vm.cli import inverse
from claude_vm.fleet import paths
from claude_vm.hosts import model as hosts
from claude_vm.runs import children, locks
from claude_vm.runs import job as jobs
from claude_vm.schema import patterns

LIB = REPO / "lib"
LOCKS_PY = LIB / "claude_vm" / "runs" / "locks.py"
DEVNULL = subprocess.DEVNULL

# A run in a child process, as a verb makes one through runs.run, from a spec in JSON: the test's folder and the run's
# tag there, the jobs folder, the command, the kind, the machines and the policy of its locks. Its body says what it
# took and goes through its steps: a section (shared or not, with a counter of the next number inside it, held until the
# test's word), a child of its own in a session of its own, or a hold until the test's word. Each thing it says is one
# line of JSON in <tag>.out; a refusal says its sentence, the lock and the holder's job, and exits 1.
RUN = r"""
import asyncio, json, os, signal, sys, time
lib, spec = sys.argv[1], json.loads(sys.argv[2])
sys.path.insert(0, lib)
from pathlib import Path
from claude_vm import runs
from claude_vm.runs import attached, children, locks, rundir
from claude_vm.runs import job as jobs

folder, tag = Path(spec["folder"]), spec["tag"]


def say(**said):
    with open(folder / f"{tag}.out", "a") as out:
        out.write(json.dumps(said) + "\n")


async def word(name):
    # The test's word to go on: a file of the run's own in the test's folder.
    while not (folder / f"{tag}.{name}").exists():
        await asyncio.sleep(0.01)


async def body(ctx):
    say(taken=ctx.job.id, locks=ctx.job.read().get("locks"))
    if spec.get("stopped"):
        await asyncio.wait_for(ctx.cancelled.wait(), 60)
        say(stopped=True)
        return 0
    for at, step in enumerate(spec.get("steps", [])):
        if "section" in step:
            async with locks.section(ctx, step["section"], shared=step.get("shared", False)):
                said = {}
                if step.get("counter"):
                    counter = folder / "counter"
                    number = int(counter.read_text()) if counter.exists() else 100
                    await asyncio.sleep(0.3)
                    counter.write_text(str(number + 1))
                    said["next"] = number
                say(entered=step["section"], at=at, **said)
                if step.get("hold"):
                    await word(f"go{at}")
            say(left=step["section"], at=at)
        elif step.get("child"):
            argv = [sys.executable, "-I", "-c", "import time; time.sleep(300)"]
            null = asyncio.subprocess.DEVNULL
            process = await children.start(
                ctx.job, argv, children.SESSION, dict(os.environ), null, null, null, children.PROGRAM
            )
            say(child=process.pid)
        else:
            say(holding=at)
            await word(f"go{at}")
    return 0


def after(module, name, then):
    real = getattr(module, name)

    def wrapped(*args, **kwargs):
        result = real(*args, **kwargs)
        then(result, *args)
        return result

    setattr(module, name, wrapped)


signum = getattr(signal, spec.get("signal", "SIGINT"))
if spec.get("signal_at") == "made":
    after(jobs, "create", lambda *_: os.kill(os.getpid(), signum))
elif spec.get("signal_at") == "taken":
    after(locks, "take", lambda *_: os.kill(os.getpid(), signum))
elif spec.get("signal_at") == "ladder":
    terminal = attached._terminal

    def signalled_then_terminal():
        os.kill(os.getpid(), signum)
        return terminal()

    attached._terminal = signalled_then_terminal
after(jobs, "drop", lambda left, job: say(dropped=job.id, left=left))
if spec.get("stuck") == "refused":
    # The job's folder read-only once its directory is made: the drop of a refused run cannot unlink its record.
    after(rundir, "make", lambda made, job: os.chmod(job.folder, 0o500))
elif spec.get("stuck") == "make":
    def failing(job):
        os.chmod(job.folder, 0o500)
        raise OSError(28, "No space left on device")

    rundir.make = failing
if spec.get("vanish"):
    # The holder's record gone between its state and its read, as a drop beside the refusal leaves it.
    jobs.Job.read = lambda self: {}
if spec.get("start_at"):
    say(ready=True)
    while not os.path.exists(spec["start_at"]):
        time.sleep(0.005)

draft = runs.Draft(
    Path(spec["jobs"]),
    spec.get("verb", "deploy"),
    spec["command"],
    "pve",
    [],
    {"kind": "terminal"},
    True,
    lock_kind=spec["kind"],
    lock_machines=[tuple(pair) for pair in spec.get("machines", [])],
    lock_policy=spec.get("policy", "machine"),
)
try:
    status = runs.run(draft, body)
except locks.Held as held:
    say(refused=held.sentence, lock=held.lock, job=held.job)
    sys.exit(1)
except OSError as error:
    say(error=str(error), notes=getattr(error, "__notes__", []))
    sys.exit(2)
say(ended=status)
sys.exit(status)
"""

# A lock held by hand, as no run of the engine's holds one: its file opened, taken exclusively, and the record given
# written at offset 0, until the test closes standard input.
HOLD = r"""
import fcntl, os, sys
path, record = sys.argv[1], sys.argv[2].encode()
held = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(held, fcntl.LOCK_EX)
os.pwrite(held, record, 0)
print("held", flush=True)
sys.stdin.read()
"""

# take timed in a child process: its job made, then twenty of the machines whose lock files the test made taken.
TIMED = r"""
import json, sys, time
lib, jobs_dir, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
sys.path.insert(0, lib)
from pathlib import Path
from claude_vm.runs import locks
from claude_vm.runs import job as jobs

made = jobs.create(Path(jobs_dir), "deploy", "aw deploy", "pve", [], {"kind": "terminal"}, True)
machines = [("pve", f"m{at:03d}") for at in range(0, 400, 400 // count)]
began = time.perf_counter()
taken = locks.take(made, "machines", machines, "machine")
print(json.dumps({"seconds": time.perf_counter() - began, "taken": len(taken)}), flush=True)
"""

STARTING_OR_ENDING = "error: alpha on pve is held by a run that is starting or ending: try again"


@pytest.fixture
def state(home: Path) -> Path:
    """The state folder of the test's own fleet home, as fleet/paths.py names it."""
    return paths.state(paths.home(REPO))


class Run:
    """A run started in a child process, and what it said."""

    def __init__(self, process: subprocess.Popen, folder: Path, tag: str) -> None:
        self.process, self.folder, self.tag = process, folder, tag

    def said(self) -> list[dict]:
        out = self.folder / f"{self.tag}.out"
        text = out.read_text() if out.is_file() else ""
        return [json.loads(line) for line in text.split("\n")[:-1]]

    def first(self, *keys: str, timeout: float = 60) -> dict:
        """The first thing it said that holds one of keys, waited for."""
        deadline = time.monotonic() + timeout
        while True:
            found = next((said for said in self.said() if any(key in said for key in keys)), None)
            if found is not None:
                return found
            if self.process.poll() is not None or time.monotonic() > deadline:
                err = (self.folder / f"{self.tag}.err").read_text()
                pytest.fail(f"{self.tag} said none of {keys}: {self.said()}\n{err}")
            time.sleep(0.02)

    def entered(self) -> bool:
        return any("entered" in said for said in self.said())

    def go(self, at: int) -> None:
        (self.folder / f"{self.tag}.go{at}").touch()

    def wait(self, timeout: float = 60) -> int:
        return self.process.wait(timeout=timeout)

    @property
    def err(self) -> str:
        return (self.folder / f"{self.tag}.err").read_text()


@pytest.fixture
def start(home: Path, state: Path, tmp_path: Path) -> Iterator[Callable[..., Run]]:
    """Starts a run in a child process: start(tag, kind=..., machines=[(host, name)], command=..., **spec). Every run
    still going at the end is ended whole, and every child one said it started."""
    started: list[Run] = []
    temporary = tmp_path / "tmp"
    temporary.mkdir()

    def starting(
        tag: str, kind: str = "machines", machines: tuple | list = (), command: str = "aw deploy alpha", **spec: object
    ) -> Run:
        given = {
            "folder": str(tmp_path),
            "tag": tag,
            "jobs": str(state / paths.JOBS),
            "kind": kind,
            "machines": [list(pair) for pair in machines],
            "command": command,
            **spec,
        }
        with open(tmp_path / f"{tag}.err", "wb") as err:
            process = subprocess.Popen(
                [sys.executable, "-I", "-c", RUN, str(LIB), json.dumps(given)],
                stdin=DEVNULL,
                stdout=DEVNULL,
                stderr=err,
                env=environment(home, {"TMPDIR": str(temporary)}),
                preexec_fn=harness._default_signals,  # noqa: PLW1509 - only signal, safe beside xdist's thread
            )
        run = Run(process, tmp_path, tag)
        started.append(run)
        return run

    try:
        yield starting
    finally:
        for run in started:
            if run.process.poll() is None:
                harness._end(run.process)
        for run in started:
            for said in run.said():
                if "child" in said:
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.kill(said["child"], signal.SIGKILL)


@pytest.fixture
def own_job(state: Path) -> Iterator[jobs.Job]:
    """A job made in the test's own process, for what a run's process alone can ask; its locks closed and the job
    dropped at the end."""
    made = jobs.create(state / paths.JOBS, "deploy", "aw deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True)
    try:
        yield made
    finally:
        for _, held, _ in locks._TAKEN.pop(jobs._key(made.folder), []):
            os.close(held)
        if made._held_here() is not None:
            jobs.drop(made)


@contextlib.contextmanager
def _held_by_hand(home: Path, path: Path, record: bytes) -> Iterator[None]:
    """path's lock held exclusively by a process of the test's that is no run, with record as its first line."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", HOLD, str(path), record.decode()],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=environment(home),
    )
    try:
        assert process.stdout is not None and process.stdout.readline().strip() == "held"
        yield
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=30)
        if process.stdout is not None:
            process.stdout.close()


def _record(state: Path, job_id: str) -> dict:
    found = jobs.find(state / paths.JOBS, job_id)
    return found.read() if found is not None else {}


def _waiting_for(state: Path, job_id: str) -> str | None:
    """The section a run's record says it waits for, if any."""
    waiting = _record(state, job_id).get("waiting") or {}
    return waiting.get("section")


def _started(started: float) -> str:
    """A start as a refusal says it, worked out here on its own: UTC, as ps says it, its spaces collapsed."""
    moment = datetime.datetime.fromtimestamp(started, datetime.timezone.utc)
    return f"{moment:%a %b} {moment.day} {moment:%H:%M:%S %Y} UTC"


def _ended(pid: int) -> bool:
    found = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return not found or found.startswith("Z")


def _files_open(pid: int) -> list[str]:
    """The files a process has open, by their real paths: /proc's on Linux, lsof's elsewhere."""
    fds = Path(f"/proc/{pid}/fd")
    if fds.is_dir():
        found = []
        for descriptor in fds.iterdir():
            with contextlib.suppress(OSError):
                found.append(os.readlink(descriptor))
        return found
    listed = subprocess.run(["lsof", "-a", "-p", str(pid), "-F", "n"], capture_output=True, text=True).stdout
    return [os.path.realpath(line[1:]) for line in listed.splitlines() if line.startswith("n/")]


def _named(holder: str, command: str, state: Path) -> str:
    """The sentence a lock refused says of a holder that runs, as the module and the printer write it."""
    record = _record(state, holder)
    cancel = inverse.line("cancel", {"job": holder})
    return f"held by job {holder}, `{command}`, started {_started(record['started'])}: `{cancel}` stops it"


# ---------------------------------------------------------------- side by side, or refused ----


def test_two_runs_on_two_machines_go_side_by_side_each_holding_its_own(start, state) -> None:
    alpha = start("alpha", machines=[("pve", "alpha")], steps=[{}])
    beta = start("beta", machines=[("pve", "beta")], command="aw deploy beta", steps=[{}])
    assert alpha.first("taken")["locks"] == ["fleet:shared", "machine-pve@alpha:exclusive"]
    assert beta.first("taken")["locks"] == ["fleet:shared", "machine-pve@beta:exclusive"]
    # Both under way at once, each holding what it took until it ends.
    alpha.first("holding")
    beta.first("holding")
    for run in (alpha, beta):
        record = _record(state, run.first("taken")["taken"])
        assert record["locks"] == run.first("taken")["locks"]
        run.go(0)
    assert alpha.wait() == 0 and beta.wait() == 0
    # Each lock file is the state folder's, at 0600 in a folder at 0700.
    folder = state / paths.LOCKS
    assert oct(folder.stat().st_mode & 0o777) == oct(paths.DIR_MODE)
    assert sorted(path.name for path in folder.iterdir()) == ["fleet", "machine-pve@alpha", "machine-pve@beta"]
    assert {oct(path.stat().st_mode & 0o777) for path in folder.iterdir()} == {oct(paths.FILE_MODE)}


def test_a_second_run_on_one_machine_is_refused_at_once_naming_the_first_and_leaves_no_job(start, state) -> None:
    first = start("first", machines=[("pve", "alpha")], steps=[{}])
    holder = first.first("taken")["taken"]
    second = start("second", machines=[("pve", "alpha")], command="aw deploy --only=configure alpha")
    refused = second.first("refused")
    assert second.wait() == 1
    assert refused["refused"] == f"error: alpha on pve is {_named(holder, 'aw deploy alpha', state)}"
    assert refused["lock"] == "machine-pve@alpha" and refused["job"] == holder
    # It dropped its job once, and none is left of it.
    assert len([said for said in second.said() if "dropped" in said]) == 1
    assert [job.id for job in jobs.listed(state / paths.JOBS)] == [holder]
    assert "Traceback" not in second.err
    first.go(0)
    assert first.wait() == 0


def test_a_run_that_makes_a_machine_and_one_that_configures_it_take_one_lock(start, state) -> None:
    # The first is inside the next VM ID's section: the VM does not exist yet, and its lock is its host's and name's.
    making = start("making", machines=[("pve", "alpha")], steps=[{"section": "vmid-pve", "hold": True}])
    making.first("entered")
    holder = making.first("taken")["taken"]
    configuring = start("configuring", machines=[("pve", "alpha")], command="aw deploy --only=configure alpha")
    refused = configuring.first("refused")
    assert refused["lock"] == locks.machine_lock("pve", "alpha") == "machine-pve@alpha"
    assert refused["job"] == holder
    making.go(0)
    assert making.wait() == 0


def test_a_vm_an_adopted_machine_and_one_name_on_two_hosts_take_different_locks(start, state) -> None:
    given = {"vm": ("pve", "alpha"), "adopted": ("machines", "pi"), "elsewhere": ("nuc", "alpha")}
    started = {
        tag: start(tag, machines=[pair], command=f"aw deploy {pair[1]}", steps=[{}]) for tag, pair in given.items()
    }
    held = {tag: run.first("taken")["locks"] for tag, run in started.items()}
    assert held == {
        "vm": ["fleet:shared", "machine-pve@alpha:exclusive"],
        "adopted": ["fleet:shared", "machine-machines@pi:exclusive"],
        "elsewhere": ["fleet:shared", "machine-nuc@alpha:exclusive"],
    }
    again = start("again", machines=[("machines", "pi")], command="aw release pi")
    holder = started["adopted"].first("taken")["taken"]
    assert (
        again.first("refused")["refused"] == f"error: the adopted machine pi is {_named(holder, 'aw deploy pi', state)}"
    )
    for run in started.values():
        run.go(0)
        assert run.wait() == 0


def test_the_adopted_host_s_word_is_no_host_s_and_neither_name_holds_an_at() -> None:
    assert locks.ADOPTED in hosts.RESERVED
    for pattern in ("current_host", "host_name", "vm_name"):
        assert not patterns.full(pattern, "a@b")
    assert locks.machine_lock("pve", "alpha") == "machine-pve@alpha"
    assert locks.machine_lock("machines", "pi") == "machine-machines@pi"
    assert locks.machine_lock("nuc", "2nd-vm.lab") == "machine-nuc@2nd-vm.lab"


@pytest.mark.parametrize(
    ("host", "name"),
    [
        ("pve", "a@b"),
        ("p@ve", "alpha"),
        ("pve", "alpha,beta"),
        ("pve", "-alpha"),
        ("pve", "../alpha"),
        ("PVE", "a"),
        ("pve", ""),
        ("", "alpha"),
        ("pve", "alpha\n"),
    ],
)
def test_a_machine_s_lock_takes_one_host_and_one_name(host, name) -> None:
    with pytest.raises(ValueError):
        locks.machine_lock(host, name)


def test_a_run_naming_no_machine_is_refused_while_either_runs_and_refuses_both_while_it_runs(start, state) -> None:
    alpha = start("alpha", machines=[("pve", "alpha")], steps=[{}])
    beta = start("beta", machines=[("pve", "beta")], command="aw deploy beta", steps=[{}])
    alpha.first("taken")
    beta.first("taken")
    listing = inverse.line("logs", {})
    under_way = (
        "error: runs on machines of this fleet are under way, and this run needs the whole fleet:"
        f" `{listing}` lists them"
    )
    for at, still in enumerate((alpha, beta)):
        # A run of the whole fleet (no names), and a run whose kind is the fleet's (init), each refused.
        for kind in ("machines", "fleet"):
            whole = start(f"whole-{kind}-{at}", kind=kind, command="aw deploy")
            refused = whole.first("refused")
            assert (refused["refused"], refused["lock"], refused["job"]) == (under_way, "fleet", None)
            assert whole.wait() == 1
        still.go(0)
        assert still.wait() == 0
    whole = start("whole", command="aw deploy", steps=[{}])
    taken = whole.first("taken")
    assert taken["locks"] == ["fleet:exclusive"]
    for name in ("alpha", "beta"):
        refused = start(f"{name}-again", machines=[("pve", name)], command=f"aw deploy {name}").first("refused")
        assert refused["refused"] == f"error: the fleet is {_named(taken['taken'], 'aw deploy', state)}"
        assert refused["lock"] == "fleet"
    whole.go(0)
    assert whole.wait() == 0


# ---------------------------------------------------------------- the sections ----


def test_clones_share_the_template_s_section_and_a_build_waits_for_them_and_they_for_it(start, state) -> None:
    clone = {"section": "template-pve", "shared": True, "hold": True}
    first = start("first", machines=[("pve", "a")], steps=[clone])
    second = start("second", machines=[("pve", "b")], steps=[clone])
    first.first("entered")
    second.first("entered")
    build = start("build", machines=[("pve", "c")], steps=[{"section": "template-pve", "hold": True}])
    built = build.first("taken")["taken"]
    _until(lambda: _waiting_for(state, built) == "template-pve", "the build waiting for the clones")
    assert _record(state, built)["waiting"]["text"] == "waiting for another run to leave the section template-pve"
    first.go(0)
    assert first.wait() == 0
    time.sleep(0.3)
    assert not build.entered()
    second.go(0)
    build.first("entered")
    assert _record(state, built)["waiting"] is None
    # A clone that comes while the template is built waits for the build.
    later = start("later", machines=[("pve", "d")], steps=[clone])
    waits = later.first("taken")["taken"]
    _until(lambda: _waiting_for(state, waits) == "template-pve", "the clone waiting for the build")
    assert not later.entered()
    build.go(0)
    later.first("entered")
    later.go(0)
    assert [run.wait() for run in (second, build, later)] == [0, 0, 0]


def test_two_creates_take_the_next_vm_id_one_after_the_other(start, state, tmp_path) -> None:
    # One holds the section with its number taken: the other waits, says so, and takes the next.
    held = start("held", machines=[("pve", "x1")], steps=[{"section": "vmid-pve", "counter": True, "hold": True}])
    assert held.first("entered")["next"] == 100
    waits = start("waits", machines=[("pve", "x2")], steps=[{"section": "vmid-pve", "counter": True}])
    waiting = waits.first("taken")["taken"]
    _until(lambda: _waiting_for(state, waiting) == "vmid-pve", "the second create waiting")
    held.go(0)
    assert waits.first("entered")["next"] == 101
    assert held.wait() == 0 and waits.wait() == 0
    # Two started at once: each reads the next number and writes it back, inside the section, never both at once.
    go = tmp_path / "start"
    together = [
        start(tag, machines=[("pve", tag)], start_at=str(go), steps=[{"section": "vmid-pve", "counter": True}])
        for tag in ("y1", "y2")
    ]
    for run in together:
        run.first("ready")
    go.touch()
    assert sorted(run.first("entered")["next"] for run in together) == [102, 103]
    assert [run.wait() for run in together] == [0, 0]


def test_a_wait_for_a_section_ends_once_the_run_is_stopped(start, state) -> None:
    holder = start("holder", machines=[("pve", "x1")], steps=[{"section": "vmid-pve", "hold": True}])
    holder.first("entered")
    waiter = start("waiter", machines=[("pve", "x2")], steps=[{"section": "vmid-pve"}])
    waiting = waiter.first("taken")["taken"]
    _until(lambda: _waiting_for(state, waiting) == "vmid-pve", "the run waiting for the section")
    # Sent while it waits on something held and nothing forks.
    os.kill(waiter.process.pid, signal.SIGINT)
    # Well inside the stop's grace: the wait ended at the stop, and the section was never entered.
    assert waiter.wait(timeout=20) == 130
    record = _record(state, waiting)
    assert (record["outcome"], record["why"], record["waiting"]) == ("cancelled", "Ctrl-C", None)
    assert not waiter.entered()
    holder.go(0)
    assert holder.wait() == 0


# ---------------------------------------------------------------- a holder that dies, and many at once ----


def test_a_holder_killed_with_sigkill_holds_nothing_and_nothing_is_cleared(start, state) -> None:
    killed = start("killed", machines=[("pve", "alpha")], steps=[{"child": True}, {}])
    holder = killed.first("taken")["taken"]
    child = killed.first("child")["child"]
    killed.first("holding")
    lock = state / paths.LOCKS / "machine-pve@alpha"
    before = lock.stat()
    os.kill(killed.process.pid, signal.SIGKILL)
    assert killed.wait() == -signal.SIGKILL
    # Its child outlives it, and holds no lock: no file of the locks' folder or of a job's folder is open in it.
    assert not _ended(child)
    kept = [
        name
        for name in _files_open(child)
        if name.startswith(os.path.realpath(state / paths.LOCKS))
        or name.startswith(os.path.realpath(state / paths.JOBS))
    ]
    assert kept == []
    after = start("after", machines=[("pve", "alpha")], steps=[{}])
    assert after.first("taken")["locks"] == ["fleet:shared", "machine-pve@alpha:exclusive"]
    # Nothing was cleared: the same file, and the killed run's job reads lost.
    assert (lock.stat().st_ino, lock.stat().st_dev) == (before.st_ino, before.st_dev)
    found = jobs.find(state / paths.JOBS, holder)
    assert found is not None and jobs.state(found) == "lost"
    after.go(0)
    assert after.wait() == 0


def test_twenty_runs_at_once_on_one_machine_let_exactly_one_through(start, state, tmp_path) -> None:
    go = tmp_path / "start"
    started = [start(f"r{at:02d}", machines=[("pve", "alpha")], start_at=str(go), steps=[{}]) for at in range(20)]
    for run in started:
        run.first("ready", timeout=120)
    go.touch()
    answers = {run.tag: run.first("taken", "refused") for run in started}
    through = [run for run in started if "taken" in answers[run.tag]]
    assert len(through) == 1, answers
    winner = answers[through[0].tag]["taken"]
    named = f"error: alpha on pve is {_named(winner, 'aw deploy alpha', state)}"
    for run in started:
        if run is through[0]:
            continue
        refused = answers[run.tag]
        assert refused["lock"] == "machine-pve@alpha"
        assert refused["refused"] in (named, STARTING_OR_ENDING), refused
        assert run.wait() == 1 and "Traceback" not in run.err
    assert [job.id for job in jobs.listed(state / paths.JOBS)] == [winner]
    through[0].go(0)
    assert through[0].wait() == 0


# ---------------------------------------------------------------- the policies ----


def test_under_run_lock_fleet_any_second_run_is_refused(start, state) -> None:
    first = start("first", machines=[("pve", "alpha")], policy="fleet", steps=[{}])
    taken = first.first("taken")
    assert taken["locks"] == ["fleet:exclusive"]
    for tag, policy, name in (("beside", "machine", "beta"), ("also", "fleet", "gamma")):
        refused = start(tag, machines=[("pve", name)], policy=policy, command=f"aw deploy {name}").first("refused")
        assert refused["refused"] == f"error: the fleet is {_named(taken['taken'], 'aw deploy alpha', state)}"
    first.go(0)
    assert first.wait() == 0


def test_under_no_lock_no_run_is_refused_and_the_sections_still_wait(start, state) -> None:
    one = start("one", machines=[("pve", "alpha")], policy="none", steps=[{"section": "vmid-pve", "hold": True}])
    assert one.first("taken")["locks"] == []
    one.first("entered")
    two = start("two", machines=[("pve", "alpha")], policy="none", steps=[{"section": "vmid-pve"}])
    waiting = two.first("taken")["taken"]
    _until(lambda: _waiting_for(state, waiting) == "vmid-pve", "the second run waiting for the section")
    assert not two.entered()
    # A run that takes its locks goes on beside them: they hold none.
    beside = start("beside", machines=[("pve", "alpha")], steps=[{}])
    assert beside.first("taken")["locks"] == ["fleet:shared", "machine-pve@alpha:exclusive"]
    one.go(0)
    two.first("entered")
    beside.go(0)
    assert [run.wait() for run in (one, two, beside)] == [0, 0, 0]


def test_the_policy_is_none_for_no_lock_else_run_lock_s() -> None:
    assert [locks.policy_of(run_lock, False) for run_lock in ("machine", "fleet")] == ["machine", "fleet"]
    assert [locks.policy_of(run_lock, True) for run_lock in ("machine", "fleet")] == ["none", "none"]
    for wrong in ("none", "machines", ""):
        with pytest.raises(ValueError):
            locks.policy_of(wrong, False)


def test_a_draft_always_names_its_kind_of_lock() -> None:
    with pytest.raises(TypeError):
        runs.Draft(Path("jobs"), "deploy", "aw deploy", None, [], {"kind": "terminal"}, True)  # type: ignore[call-arg]
    draft = runs.Draft(Path("jobs"), "deploy", "aw deploy", None, [], {"kind": "terminal"}, True, lock_kind="none")
    assert (draft.lock_machines, draft.lock_policy) == ((), "machine")


# ---------------------------------------------------------------- exclusive, and whether a run lives ----


def test_exclusive_and_the_runs_refuse_each_other_and_nothing_is_left_held_after_its_block(start, state) -> None:
    alpha = start("alpha", machines=[("pve", "alpha")], steps=[{}])
    alpha.first("taken")
    listing = inverse.line("logs", {})
    with pytest.raises(locks.Held) as refused, locks.exclusive(state, "aw host nuc"):
        pytest.fail("the fleet taken for one write beside a run")
    assert refused.value.sentence == (
        f"error: runs on machines of this fleet are under way, and `aw host nuc` needs the whole fleet: `{listing}`"
        " lists them"
    )
    alpha.go(0)
    assert alpha.wait() == 0
    whole = start("whole", command="aw deploy", steps=[{}])
    taken = whole.first("taken")["taken"]
    with pytest.raises(locks.Held) as refused, locks.exclusive(state, "aw vault --rotate"):
        pytest.fail("the fleet taken for one write beside a run of the whole fleet")
    assert refused.value.sentence == f"error: the fleet is {_named(taken, 'aw deploy', state)}"
    whole.go(0)
    assert whole.wait() == 0
    before = dict(locks.locks_held())
    with locks.exclusive(state, "aw host nuc"):
        assert locks.changing_run_lives(state)
        for tag, kind, machines in (("named", "machines", [("pve", "alpha")]), ("fleetwide", "fleet", [])):
            run = start(tag, kind=kind, machines=machines)
            said = run.first("refused")
            assert said["refused"] == "error: `aw host nuc` holds the fleet for one write: try again when it ends"
            assert said["job"] is None and "cancel" not in said["refused"]
            assert run.wait() == 1
    # Let go at the block's end, in this process that still runs: a run of the whole fleet goes on.
    assert locks.locks_held() == before and not locks.changing_run_lives(state)
    after = start("after", command="aw deploy", steps=[{}])
    assert after.first("taken")["locks"] == ["fleet:exclusive"]
    after.go(0)
    assert after.wait() == 0


def test_whether_a_changing_run_lives_is_asked_of_the_fleet_s_lock_and_the_probe_let_go(start, state) -> None:
    assert not locks.changing_run_lives(state)
    assert not (state / paths.LOCKS).exists()
    for tag, machines in (("named", [("pve", "alpha")]), ("whole", [])):
        run = start(tag, machines=machines, steps=[{}])
        run.first("taken")
        assert locks.changing_run_lives(state)
        run.go(0)
        assert run.wait() == 0
        assert not locks.changing_run_lives(state)
    # Its probe let go at once: a run of the whole fleet goes on just after it.
    whole = start("after", command="aw deploy", steps=[{}])
    assert whole.first("taken")["locks"] == ["fleet:exclusive"]
    whole.go(0)
    assert whole.wait() == 0
    # A run under --no-lock holds none, and is not seen.
    unlocked = start("unlocked", machines=[("pve", "alpha")], policy="none", steps=[{}])
    unlocked.first("taken")
    assert not locks.changing_run_lives(state)
    unlocked.go(0)
    assert unlocked.wait() == 0


# ---------------------------------------------------------------- a refusal never fails on what it reads ----


@pytest.mark.parametrize("record", ["not a record", "no such job", "an ended job", "a job that holds another lock"])
def test_a_refusal_whose_holder_cannot_be_told_says_so_and_never_fails(start, home, state, record) -> None:
    other = None
    if record == "not a record":
        written = b"{not json\n"
    elif record == "no such job":
        written = json.dumps({"job": "0123456789abcdef"}).encode() + b"\n"
    elif record == "an ended job":
        ended = start("ended", machines=[("pve", "gamma")], command="aw deploy gamma")
        assert ended.wait() == 0
        written = json.dumps({"job": ended.first("taken")["taken"]}).encode() + b"\n"
    else:
        other = start("other", machines=[("pve", "beta")], command="aw deploy beta", steps=[{}])
        written = json.dumps({"job": other.first("taken")["taken"]}).encode() + b"\n"
    with _held_by_hand(home, state / paths.LOCKS / "machine-pve@alpha", written):
        refused = start("refused", machines=[("pve", "alpha")])
        said = refused.first("refused")
        assert (said["refused"], said["job"]) == (STARTING_OR_ENDING, None)
        assert refused.wait() == 1 and "Traceback" not in refused.err
    if other is not None:
        other.go(0)
        assert other.wait() == 0


def test_a_refusal_whose_holder_s_record_vanishes_while_it_is_read_names_no_one(start, state) -> None:
    # The holder runs, so its job's own lock is held; then its record is gone by the time it is read, as a drop beside
    # the refusal leaves it (experiment 40, c16).
    holder = start("holder", machines=[("pve", "alpha")], steps=[{}])
    holder.first("taken")
    refused = start("refused", machines=[("pve", "alpha")], vanish=True)
    said = refused.first("refused")
    assert (said["refused"], said["job"]) == (STARTING_OR_ENDING, None)
    assert refused.wait() == 1 and "Traceback" not in refused.err
    holder.go(0)
    assert holder.wait() == 0


def test_a_lock_file_that_is_a_link_is_never_followed_and_the_run_leaves_no_job(start, state, tmp_path) -> None:
    target = tmp_path / "elsewhere"
    target.write_text("left as it is\n")
    folder = state / paths.LOCKS
    folder.mkdir(mode=0o700, parents=True)
    (folder / "machine-pve@alpha").symlink_to(target)
    run = start("linked", machines=[("pve", "alpha")])
    said = run.first("error", "taken", "refused")
    assert "error" in said and run.wait() == 2
    assert target.read_text() == "left as it is\n"
    assert jobs.listed(state / paths.JOBS) == []


# ---------------------------------------------------------------- the window before the ladder ----


@pytest.mark.parametrize("at", ["made", "taken", "ladder"])
@pytest.mark.parametrize(
    ("signame", "status", "recorded"),
    [("SIGINT", 130, (130, "Ctrl-C")), ("SIGTERM", -signal.SIGTERM, (143, "SIGTERM"))],
)
def test_a_signal_between_the_job_and_the_ladder_ends_the_run_as_the_ladder_says(
    start, state, at, signame, status, recorded
) -> None:
    # Sent by the run to itself just after its job is made, just after its locks are taken, or as the ladder starts,
    # before its handlers are set: held off until they are, never leaving a job that reads lost.
    run = start("signalled", machines=[("pve", "alpha")], signal_at=at, signal=signame, stopped=True)
    assert run.wait() == status, run.err
    [found] = jobs.listed(state / paths.JOBS)
    record = found.read()
    assert (record["outcome"], record["exit"], record["why"]) == ("cancelled", *recorded)
    assert jobs.state(found) == "cancelled"
    # Its locks went with its process.
    after = start("after", machines=[("pve", "alpha")])
    after.first("taken")
    assert after.wait() == 0


# ---------------------------------------------------------------- what a drop left ----


@pytest.mark.skipif(os.geteuid() == 0, reason="root removes what a folder's mode forbids")
def test_a_refusal_says_what_the_refused_run_s_drop_left(start, state) -> None:
    holder = start("holder", machines=[("pve", "alpha")], steps=[{}])
    held = holder.first("taken")["taken"]
    refused = start("refused", machines=[("pve", "alpha")], stuck="refused")
    said = refused.first("refused")
    assert refused.wait() == 1
    [dropped] = [each for each in refused.said() if "dropped" in each]
    folder = state / paths.JOBS / dropped["dropped"]
    try:
        assert dropped["left"] is not None
        assert (
            said["refused"]
            == f"error: alpha on pve is {_named(held, 'aw deploy alpha', state)}; this run's {dropped['left']}"
        )
        assert dropped["left"].startswith(f"job {dropped['dropped']} could not be removed: ")
        assert dropped["left"].split(": ", 1)[1].startswith(f"{folder / 'job.json'} is left (")
    finally:
        folder.chmod(0o700)
    holder.go(0)
    assert holder.wait() == 0


@pytest.mark.skipif(os.geteuid() == 0, reason="root removes what a folder's mode forbids")
def test_a_run_whose_directory_cannot_be_made_says_what_its_drop_left(start, state) -> None:
    run = start("unmade", machines=[("pve", "alpha")], stuck="make")
    said = run.first("error")
    assert run.wait() == 2
    [dropped] = [each for each in run.said() if "dropped" in each]
    try:
        assert said["error"] == "[Errno 28] No space left on device"
        assert said["notes"] == [dropped["left"]] and dropped["left"].startswith(f"job {dropped['dropped']} could not")
    finally:
        (state / paths.JOBS / dropped["dropped"]).chmod(0o700)
    # It took no lock: a run on alpha goes on.
    after = start("after", machines=[("pve", "alpha")])
    after.first("taken")
    assert after.wait() == 0


# ---------------------------------------------------------------- take, as a run's own process asks it ----


def test_a_child_is_never_handed_one_of_the_run_s_locks(own_job, state) -> None:
    locks.take(own_job, "machines", [("pve", "alpha")], "machine")

    async def handed_each() -> set[str]:
        ctx = SimpleNamespace(job=own_job, emit=own_job.emit, cancelled=asyncio.Event())
        refused = set()
        async with locks.section(ctx, "template-pve", shared=True):  # type: ignore[arg-type]
            for descriptor, name in locks.locks_held().items():
                with pytest.raises(children.LockRefused) as raised:
                    await children.start(
                        own_job,
                        [sys.executable, "-I", "-c", "pass"],
                        children.GROUP,
                        dict(os.environ),
                        DEVNULL,
                        DEVNULL,
                        DEVNULL,
                        children.PROGRAM,
                        (descriptor,),
                    )
                assert name in str(raised.value)
                refused.add(name)
        return refused

    assert asyncio.run(handed_each()) == {
        "the lock of the fleet",
        "the lock of alpha on pve",
        "the lock of the section template-pve",
    }
    assert locks.locks_held.__module__ in {holder.__module__ for holder in children.HOLDERS}


def test_a_take_refused_part_way_holds_nothing_of_what_it_took(start, own_job, state) -> None:
    # All or none: refused at beta, the fleet's lock and alpha's, taken before it, are let go before the refusal is
    # said, so that no run beside this one is refused for a lock this one no longer wants.
    beta = start("beta", machines=[("pve", "beta")], command="aw deploy beta", steps=[{}])
    beta.first("taken")
    before = dict(locks.locks_held())
    with pytest.raises(locks.Held) as refused:
        locks.take(own_job, "machines", [("pve", "beta"), ("pve", "alpha")], "machine")
    assert refused.value.lock == "machine-pve@beta"
    assert locks.locks_held() == before and jobs._key(own_job.folder) not in locks._TAKEN
    alpha = start("alpha", machines=[("pve", "alpha")])
    alpha.first("taken")
    assert alpha.wait() == 0
    beta.go(0)
    assert beta.wait() == 0
    # Nothing of the refusal is in the job's record: its locks are those it holds, none.
    assert own_job.read()["locks"] == []


def test_take_is_asked_once_by_the_run_that_made_the_job_and_held_to_its_words(own_job, state) -> None:
    for kind, machines, policy, error in (
        ("everything", [], "machine", ValueError),
        ("machines", [], "machines", ValueError),
        ("machines", "pve@alpha", "machine", TypeError),
        ("machines", [("pve",)], "machine", TypeError),
        ("machines", [("pve", "a@b")], "machine", ValueError),
    ):
        with pytest.raises(error):
            locks.take(own_job, kind, machines, policy)
    assert locks.take(own_job, "none", [("pve", "alpha")], "machine") == []
    with pytest.raises(RuntimeError, match="once"):
        locks.take(own_job, "machines", [("pve", "alpha")], "machine")
    with pytest.raises(RuntimeError, match="not this process's"):
        locks.take(jobs.Job(own_job.folder.parent / ("0" * 16)), "none", [], "machine")


def test_take_of_twenty_machines_beside_400_lock_files_and_400_ended_jobs_stays_under_a_tenth_of_a_second(
    home, state
) -> None:
    folder, jobs_dir = state / paths.LOCKS, state / paths.JOBS
    folder.mkdir(mode=0o700, parents=True)
    for at in range(400):
        (folder / f"machine-pve@m{at:03d}").touch(mode=0o600)
        ended = jobs_dir / f"{at:016x}"
        ended.mkdir(mode=0o700, parents=True)
        (ended / "lock").touch(mode=0o600)
        record = {"id": ended.name, "started": 1.0, "outcome": "succeeded", "exit": 0, "locks": []}
        (ended / "job.json").write_text(json.dumps(record))
    seconds = []
    for _ in range(5):
        timed = subprocess.run(
            [sys.executable, "-I", "-c", TIMED, str(LIB), str(jobs_dir), "20"],
            capture_output=True,
            text=True,
            env=environment(home),
            timeout=60,
            check=True,
        )
        said = json.loads(timed.stdout)
        assert said["taken"] == 21
        seconds.append(said["seconds"])
    assert statistics.median(seconds) < 0.1, seconds


# ---------------------------------------------------------------- the rules, by scans ----


def _docstrings(tree: ast.AST) -> set[int]:
    """The ids of the string nodes that are docstrings, or a string standing alone as a statement."""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    }


def _code_words(source: str) -> list[tuple[int, str]]:
    """What a module names in code, by line: its names, attributes, arguments, functions and strings, never a
    docstring or a comment."""
    tree = ast.parse(source)
    skipped = _docstrings(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            found.append((node.lineno, node.attr))
        elif isinstance(node, ast.arg):
            found.append((node.lineno, node.arg))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.append((node.lineno, node.name))
        elif isinstance(node, ast.alias):
            found.append((getattr(node, "lineno", 0), node.asname or node.name))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skipped:
            found.append((node.lineno, node.value))
    return found


def _new_modules() -> list[Path]:
    return sorted(path for sub in NEW for path in (LIB / "claude_vm" / sub).rglob("*.py"))


def test_the_locks_keep_no_pid_and_read_no_process() -> None:
    text = LOCKS_PY.read_text()
    kept = [(line, word) for line, word in _code_words(text) if re.search(r"pid|kill|lstart|getppid", word, re.I)]
    assert kept == [], "a lock is the kernel's: no pid kept, none signalled, no process read to decide one"
    assert _reads_a_process("claude_vm/runs/locks.py", text) == []
    # test_aw_job.py's scan of every module of runs/ reaches this one too.
    assert LOCKS_PY in [path for path in repo_files("lib/claude_vm/runs", untracked=True) if path.suffix == ".py"]


# What names the locks' folder in fleet/paths.py: the name beneath the state folder, and the function that joins it.
FOLDER_NAMES = ("LOCKS", "locks_dir")


def _flocks_and_lock_folders(rel: str, source: str) -> set[str]:
    """What a module does that only the locks may: a flock taken, the locks' folder named, by paths.py's names (an
    attribute of the module, however it is imported, or a name imported from it). paths.py itself names its own."""
    tree = ast.parse(source)
    modules, names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "claude_vm.fleet":
            modules |= {alias.asname or alias.name for alias in node.names if alias.name == "paths"}
        if isinstance(node, ast.ImportFrom) and node.module == "claude_vm.fleet.paths":
            names |= {alias.asname or alias.name for alias in node.names if alias.name in FOLDER_NAMES}
        if isinstance(node, ast.Import):
            modules |= {alias.asname or alias.name for alias in node.names if alias.name == "claude_vm.fleet.paths"}
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "flock":
            found.add("a flock")
        if isinstance(node, ast.Attribute) and node.attr in FOLDER_NAMES and ast.unparse(node.value) in modules:
            found.add("the locks' folder")
        if isinstance(node, ast.Name) and (node.id in names or (rel.endswith("fleet/paths.py") and node.id == "LOCKS")):
            found.add("the locks' folder")
    return found


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("fcntl.flock(fd, fcntl.LOCK_EX)", {"a flock"}),
        ("from fcntl import flock\nflock(fd, 2)", {"a flock"}),
        ("from claude_vm.fleet import paths\nfolder = state / paths.LOCKS", {"the locks' folder"}),
        ("from claude_vm.fleet import paths as p\nfolder = p.locks_dir(where)", {"the locks' folder"}),
        ("import claude_vm.fleet.paths\nclaude_vm.fleet.paths.locks_dir(where)", {"the locks' folder"}),
        ("from claude_vm.fleet.paths import locks_dir\nlocks_dir(where)", {"the locks' folder"}),
        ("from claude_vm.fleet.paths import LOCKS as THERE\nfolder = state / THERE", {"the locks' folder"}),
        # The table's column of the locks a verb takes is no folder.
        ("LOCKS = ('machines', 'fleet', 'none', 'words')\nx = words.LOCKS", set()),
        ("store.locked(path)", set()),
    ],
)
def test_the_scan_sees_a_flock_and_the_locks_folder(source, found) -> None:
    assert _flocks_and_lock_folders("lib/claude_vm/x.py", source) == found


def test_only_the_locks_and_the_job_take_a_flock_and_only_the_locks_name_their_folder() -> None:
    flocks, folders = set(), set()
    for path in _new_modules():
        rel = path.relative_to(REPO).as_posix()
        found = _flocks_and_lock_folders(rel, path.read_text())
        if "a flock" in found:
            flocks.add(rel)
        if "the locks' folder" in found:
            folders.add(rel)
    assert flocks == {"lib/claude_vm/runs/job.py", "lib/claude_vm/runs/locks.py"}
    assert folders == {"lib/claude_vm/fleet/paths.py", "lib/claude_vm/runs/locks.py"}


def test_a_machine_s_lock_is_named_from_its_host_and_its_name_alone() -> None:
    tree = ast.parse(LOCKS_PY.read_text())
    [function] = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "machine_lock"]
    assert [arg.arg for arg in function.args.args] == ["host", "name"]
    named = [
        (line, word)
        for line, word in _code_words(LOCKS_PY.read_text())
        if re.search(r"identity|uuid|smbios|\bvmid\b|alias|address|resolved", word, re.I)
    ]
    assert named == [], "a machine's lock is its host's and its name's: never an identity, an ID or an address"


def test_nothing_of_the_new_engine_asks_whether_a_lock_is_held_with_f_getlk() -> None:
    # F_GETLK does not see a flock on Linux, but on NFSv3 (experiment 40): a LOCK_NB attempt, /proc/locks or lsof.
    asking = {
        path.relative_to(REPO).as_posix()
        for path in _new_modules()
        if any(re.fullmatch(r"F_(OFD_)?GETLK(64)?|lockf", word) for _, word in _code_words(path.read_text()))
    }
    assert asking == set()


def test_the_holder_s_record_is_written_without_a_truncate(state) -> None:
    source = LOCKS_PY.read_text()
    assert not [word for _, word in _code_words(source) if re.search(r"truncate|O_TRUNC", word)]
    opened = [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"
    ]
    assert opened == [], "a lock file is opened with os.open alone, which truncates nothing"
    # A longer record, then a shorter one: the first line is the shorter, and the longer's tail is left after it.
    with locks.exclusive(state, "aw vault --rotate"):
        pass
    with locks.exclusive(state, "aw host a"):
        written = (state / paths.LOCKS / "fleet").read_bytes()
    assert written.startswith(b'{"command": "aw host a"}\n') and len(written) > len(b'{"command": "aw host a"}\n')


# The modules that name RUN_LOCK or ATTENDED in code, each with why; and mcp/, the 2.x agents' server, which sets both
# on the just line it runs. Anywhere else an exported one could change a run past the dispatch (review/code/H20.md 2).
NAMES_THE_TWO = {
    "lib/claude_vm/cli/before.py": "the dispatch's door: it reads neither from the environment, and says so",
    "lib/claude_vm/schema/words.py": "the help of --attended, whose fate for an agent is ATTENDED",
}


def _names_the_two(source: str) -> list[int]:
    return [line for line, word in _code_words(source) if re.search(r"\bRUN_LOCK\b|\bATTENDED\b", word)]


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ('os.environ.get("RUN_LOCK")', [1]),
        ('no_lock = environ.get("ATTENDED") == "1"', [1]),
        ("ATTENDED = fixed(None)", [1]),
        ("x = run.attended", []),
        ('"""An exported RUN_LOCK or ATTENDED changes nothing."""', []),
        ("# RUN_LOCK", []),
    ],
)
def test_the_scan_sees_run_lock_and_attended_in_code(source, found) -> None:
    assert _names_the_two(source) == found


def test_no_module_of_the_new_engine_names_run_lock_or_attended_in_code_but_the_two_listed() -> None:
    naming = set()
    for path in repo_files("lib/claude_vm", untracked=True):
        rel = path.relative_to(REPO).as_posix()
        if path.suffix != ".py" or rel.startswith("lib/claude_vm/mcp/"):
            continue
        if _names_the_two(path.read_text()):
            naming.add(rel)
    assert naming <= set(NAMES_THE_TWO), "an exported RUN_LOCK or ATTENDED changes nothing past the dispatch"
    assert all(reason.strip() for reason in NAMES_THE_TWO.values())
    assert [rel for rel in NAMES_THE_TWO if not (REPO / rel).is_file()] == []


def test_the_sections_are_their_owners_and_a_section_s_name_is_a_word_and_a_host(own_job) -> None:
    ctx = SimpleNamespace(job=own_job, emit=own_job.emit, cancelled=asyncio.Event())

    async def enter(name: str) -> None:
        async with locks.section(ctx, name):  # type: ignore[arg-type]
            pass

    for wrong in ("fleet", "machine-pve@alpha", "template-", "-pve", "template-PVE", "template-pve\n", "a/b-pve"):
        with pytest.raises(ValueError):
            asyncio.run(enter(wrong))
    asyncio.run(enter("template-pve"))
    assert oct((own_job.folder.parent.parent / paths.LOCKS / "template-pve").stat().st_mode & 0o777) == oct(0o600)


def test_a_flock_refusal_is_the_only_error_read_as_held(tmp_path) -> None:
    # Anything but EAGAIN/EWOULDBLOCK from flock is the filesystem's error, raised as itself, never a refusal.
    path = tmp_path / "lock"
    held = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        assert locks._try(held, fcntl.LOCK_EX)
        other = os.open(path, os.O_RDWR)
        try:
            assert not locks._try(other, fcntl.LOCK_EX)
        finally:
            os.close(other)
    finally:
        os.close(held)
    closed = os.open(path, os.O_RDONLY)
    os.close(closed)
    with pytest.raises(OSError) as raised:
        locks._try(closed, fcntl.LOCK_EX)
    assert raised.value.errno == errno.EBADF
