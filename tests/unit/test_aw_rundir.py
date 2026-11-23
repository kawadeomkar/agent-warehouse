"""A run's short-lived directory (lib/claude_vm/runs/rundir.py; docs/architecture/engine.md §16.8).

Each test's per-user temporary directory is a folder of its own (TMPDIR, read again by tempfile), but where the rule
sends the directory to /tmp, from which the test removes it. The rules with a test of their own: the directory is the
run's alone (0700 whatever the umask, of this account's, its name holding the job's ID) and named in the job's record;
it is never made inside XDG_RUNTIME_DIR, which a logout removes under a detached run, by the real paths of both; only
the run that made a job makes its directory or names a file in it, and the module writes no file (a scan); `remove` is
safe twice, still removes a dropped job's directory, and from another process removes only a directory of the form
`make` gives, of this account's and not a link, and never while the run that made it lives, nor once that run has
dropped the job since its record was read.
"""

from __future__ import annotations

import ast
import contextlib
import os
import select
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import harness
import pytest
from aw_kit import environment, imports_of, searched_for
from harness import REPO

from claude_vm.fleet import paths
from claude_vm.runs import job as jobs
from claude_vm.runs import rundir

LIB = REPO / "lib"
SECRET = "unit-test-aw-rundir-secret"
# Names of the files a run generates there (F3, F4): its configuration, inventory and variables. None is an Ansible
# configuration's own name, which tests/unit/test_gathered_facts.py keeps to harness.ansible_config.
GENERATED = ("layer1.cfg", "inventory.yml", "vars.json")

# A run in a child process: it makes its job and its directory, says both, waits for the test's line, then ends: with
# its directory removed and its outcome written, or with neither, as a run killed outright leaves them; or it drops its
# job, says so, and ends on the test's next line with its directory left.
CHILD = """
import os, sys
sys.path.insert(0, {lib!r})
from claude_vm.runs import job as jobs, rundir

made = jobs.create(sys.argv[1], "deploy", "aw deploy alpha", "pve", ["alpha"], {{"kind": "terminal"}}, True)
print(made.id, rundir.make(made), flush=True)
sys.stdin.readline()
if sys.argv[2] == "dropped":
    jobs.drop(made)
    print("dropped", flush=True)
    sys.stdin.readline()
if sys.argv[2] == "removed":
    rundir.remove(made)
    made.emit("end", None, "as asked", outcome="succeeded", exit=0)
os._exit(0)
"""


@pytest.fixture
def temporary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The per-user temporary directory, of this test's own: TMPDIR, with tempfile's answer forgotten so that it is
    read again, and both put back after the test."""
    where = tmp_path / "tmp"
    where.mkdir()
    monkeypatch.setenv("TMPDIR", str(where))
    monkeypatch.setattr(tempfile, "tempdir", None)
    return where


@pytest.fixture
def jobs_dir(home: Path) -> Path:
    """The jobs folder of the test's own fleet home, as fleet/paths.py names it."""
    return paths.jobs_dir(paths.home(REPO))


def _create(jobs_dir: Path) -> jobs.Job:
    return jobs.create(jobs_dir, "deploy", "aw deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True)


@pytest.fixture
def made(jobs_dir: Path, temporary: Path) -> Iterator[jobs.Job]:
    """A job of this process's own; after the test its directory removed, wherever the test made it, and the job
    dropped."""
    job = _create(jobs_dir)
    yield job
    rundir.remove(job)
    if job._held_here() is not None:
        jobs.drop(job)


@contextlib.contextmanager
def _run(jobs_dir: Path, then: str, home: Path) -> Iterator[tuple[subprocess.Popen, str, Path]]:
    """A run in a child process, which has made its job and its directory, with the job's ID and the directory it
    said. Ended whole on the way out where it still runs."""
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
        if len(said) != 2:
            pytest.fail(f"the run made no job and directory: {process.communicate(timeout=30)[1]}")
        yield process, said[0], Path(said[1])
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


def _said(process: subprocess.Popen) -> str:
    """The test's line to a run that goes on, and the next line it says."""
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write("go\n")
    process.stdin.flush()
    if not select.select([process.stdout], [], [], 60)[0]:
        pytest.fail("the run said nothing within a minute")
    return process.stdout.readline().strip()


def _runs(where: Path) -> list[str]:
    """The run directories in where, by name."""
    return sorted(path.name for path in where.iterdir() if path.name.startswith(rundir.PREFIX))


# ---------------------------------------------------------------- the directory and the record ----


def test_the_directory_is_the_run_s_own_whatever_the_umask_its_name_holding_the_job_s_id(made, temporary) -> None:
    before = os.umask(0o277)
    try:
        where = rundir.make(made)
    finally:
        os.umask(before)
    assert where.parent == temporary and rundir.parent() == str(temporary)
    assert where.name.startswith(f"aw-run-{made.id}-") and len(where.name) > len(f"aw-run-{made.id}-")
    found = os.lstat(where)
    assert stat.S_ISDIR(found.st_mode) and stat.S_IMODE(found.st_mode) == 0o700 and found.st_uid == os.getuid()
    # Made empty: the module writes none of the files that go there.
    assert list(where.iterdir()) == [] and _runs(temporary) == [where.name]


def test_the_record_names_the_directory(jobs_dir, made) -> None:
    where = rundir.make(made)
    assert made.read()["directory"] == str(where)
    found = jobs.find(jobs_dir, made.id)
    assert found is not None and found.read()["directory"] == str(where)


@pytest.mark.parametrize(
    ("runtime", "tmpdir", "fallback"),
    [
        # TMPDIR inside XDG_RUNTIME_DIR, or the folder itself, or inside it through a link: never there.
        ("run/user", "run/user/tmp", True),
        ("run/user", "run/user", True),
        ("run/user", "link/tmp", True),
        # XDG_RUNTIME_DIR beside it, or a relative path, which the XDG specification says is ignored: TMPDIR it is.
        ("run/user", "tmp", False),
        ("relative", "tmp", False),
    ],
)
def test_never_inside_xdg_runtime_dir_which_a_logout_removes_under_a_detached_run(
    made, tmp_path, monkeypatch, runtime, tmpdir, fallback
) -> None:
    (tmp_path / "run" / "user" / "tmp").mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "run" / "user")
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "tmp" if runtime == "relative" else str(tmp_path / runtime))
    monkeypatch.setenv("TMPDIR", str(tmp_path / tmpdir))
    monkeypatch.setattr(tempfile, "tempdir", None)
    # The control: tempfile alone would put it in TMPDIR.
    assert tempfile.gettempdir() == str(tmp_path / tmpdir)
    where = rundir.make(made)
    if fallback:
        assert where.parent == Path("/tmp") == Path(rundir.FALLBACK)
        real, runtime_dir = where.resolve(), (tmp_path / "run" / "user").resolve()
        assert runtime_dir not in real.parents
    else:
        assert where.parent == tmp_path / tmpdir
    assert made.read()["directory"] == str(where)


def test_a_run_has_one_directory(made, temporary) -> None:
    where = rundir.make(made)
    with pytest.raises(RuntimeError, match="has its directory already"):
        rundir.make(made)
    assert _runs(temporary) == [where.name] and made.read()["directory"] == str(where)


def test_no_directory_is_left_where_the_record_cannot_name_it(jobs_dir, temporary) -> None:
    job = _create(jobs_dir)
    try:
        job.record.unlink()
        with pytest.raises(jobs.Gone):
            rundir.make(job)
        assert _runs(temporary) == []
        with pytest.raises(RuntimeError, match="has no directory"):
            rundir.path_for(job, "vars.json")
    finally:
        jobs.drop(job)


# ---------------------------------------------------------------- what goes there ----


def test_path_for_names_each_generated_file_there_and_writes_nothing(made) -> None:
    where = rundir.make(made)
    assert [rundir.path_for(made, name) for name in GENERATED] == [where / name for name in GENERATED]
    assert list(where.iterdir()) == []


@pytest.mark.parametrize(
    "name", ["", ".", "..", "a/b", "/etc/passwd", "../vars.json", "a\0b", "a\nb", "a\tb", "\x1b[31m"]
)
def test_path_for_takes_one_plain_file_name(made, name) -> None:
    where = rundir.make(made)
    with pytest.raises(ValueError, match="not one plain file name"):
        rundir.path_for(made, name)
    assert list(where.iterdir()) == []


def test_path_for_takes_a_name_as_a_str(made) -> None:
    rundir.make(made)
    with pytest.raises(TypeError, match="is a str"):
        rundir.path_for(made, Path("vars.json"))  # type: ignore[arg-type]


def test_only_the_run_that_made_a_job_makes_its_directory_or_names_a_file_in_it(jobs_dir, home, temporary) -> None:
    with _run(jobs_dir, "removed", home) as (run, job_id, where):
        found = jobs.find(jobs_dir, job_id)
        assert found is not None and where.parent == temporary
        for act in (lambda: rundir.make(found), lambda: rundir.path_for(found, "vars.json")):
            with pytest.raises(RuntimeError, match="not this process's"):
                act()
        # A living run's directory is its own: another process removes nothing of it.
        with pytest.raises(RuntimeError, match="still runs"):
            rundir.remove(found)
        assert where.is_dir() and _runs(temporary) == [where.name]
        assert _go(run) == 0
    # The run removed its own at its end; the record still says where it was.
    assert _runs(temporary) == [] and jobs.state(found) == "succeeded"
    assert found.read()["directory"] == str(where)


# ---------------------------------------------------------------- its removal ----


def test_remove_deletes_the_directory_with_what_it_holds_and_is_safe_twice(made) -> None:
    assert rundir.remove(made) is True  # none made yet: nothing to remove
    where = rundir.make(made)
    rundir.path_for(made, "layer1.cfg").write_text("[defaults]\nforks = 1\n")
    (where / "ansible-local" / "ansible-local-1").mkdir(parents=True)
    # What every workflow's tests ask of this directory: none of the secrets the run held is in it.
    assert searched_for([SECRET], where) == []
    assert rundir.remove(made) is True and not os.path.lexists(where)
    assert rundir.remove(made) is True
    with pytest.raises(RuntimeError, match="has no directory"):
        rundir.path_for(made, "layer1.cfg")
    assert made.read()["directory"] == str(where)


def test_the_run_removes_its_directory_once_its_job_is_dropped(jobs_dir, temporary) -> None:
    # A run refused at its locks drops its job; its directory is still its to remove.
    job = _create(jobs_dir)
    where = rundir.make(job)
    jobs.drop(job)
    assert jobs.find(jobs_dir, job.id) is None and where.is_dir()
    assert rundir.remove(job) is True and _runs(temporary) == []
    # Read again with the job gone: its record reads {}, which names no directory, and nothing fails.
    assert job.read() == {} and rundir.remove(job) is True


def test_another_process_removes_a_dead_run_s_directory_and_nothing_but_what_make_makes(
    jobs_dir, home, temporary, tmp_path
) -> None:
    with _run(jobs_dir, "left", home) as (run, job_id, where):
        assert _go(run) == 0
    found = jobs.find(jobs_dir, job_id)
    assert found is not None and jobs.state(found) == "lost" and where.is_dir()
    kept = tmp_path / "kept"
    kept.mkdir()
    (kept / "file").write_text("kept")
    link = temporary / f"aw-run-{job_id}-link"
    link.symlink_to(kept)
    for named in (
        str(kept),  # no run's directory
        str(temporary / f"aw-run-{'0' * 16}-abcdefgh"),  # another job's
        f"{temporary}/../tmp/{where.name}",  # not as make writes it
        f"tmp/{where.name}",  # not absolute
        42,
    ):
        found.update(directory=named)
        with pytest.raises(ValueError, match="names no directory of its run's"):
            rundir.remove(found)
    found.update(directory=str(link))
    with pytest.raises(ValueError, match="not a directory of this account's"):
        rundir.remove(found)
    assert (kept / "file").read_text() == "kept" and link.is_symlink() and where.is_dir()
    found.update(directory=str(where))
    assert rundir.remove(found) is True and not os.path.lexists(where)
    assert rundir.remove(found) is True


def test_another_process_removes_nothing_of_a_job_dropped_since_its_record_was_read(
    jobs_dir, home, temporary, monkeypatch
) -> None:
    # The record names the directory when it is read, and the run drops its job before the job's state is asked: no
    # such job, whatever its lock, and the directory is still its run's, which removes what it made once its job is
    # dropped.
    with _run(jobs_dir, "dropped", home) as (run, job_id, where):
        found = jobs.find(jobs_dir, job_id)
        recorded = rundir._recorded

        def read_then_dropped(job: jobs.Job) -> Path | None:
            named = recorded(job)
            assert _said(run) == "dropped"
            return named

        monkeypatch.setattr(rundir, "_recorded", read_then_dropped)
        with pytest.raises(RuntimeError, match="dropped by its run"):
            rundir.remove(found)
        assert jobs.state(found) is None and where.is_dir() and _runs(temporary) == [where.name]
        assert _go(run) == 0


@pytest.mark.skipif(os.geteuid() == 0, reason="root removes what a folder's mode forbids")
def test_what_cannot_be_removed_is_left_and_said_and_removed_on_the_next_call(made) -> None:
    where = rundir.make(made)
    stuck = where / "stuck"
    stuck.mkdir()
    (stuck / "file").write_text("x")
    stuck.chmod(0o500)
    try:
        assert rundir.remove(made) is False and (stuck / "file").exists()
    finally:
        stuck.chmod(0o700)
    assert rundir.remove(made) is True and not os.path.lexists(where)


# ---------------------------------------------------------------- the rules ----


# What writes a file, or makes a socket: none of them in the module, which makes and removes the directory alone.
WRITERS = frozenset(
    {"open", "write_text", "write_bytes", "write", "touch", "mkstemp", "NamedTemporaryFile", "TemporaryFile", "socket"}
)


def _writes(source: str) -> list[str]:
    """Each call in source to something that writes a file or makes a socket, by its name."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name in WRITERS:
                found.append(name)
    return found


def test_the_module_writes_no_file_and_makes_no_socket() -> None:
    assert _writes((LIB / "claude_vm" / "runs" / "rundir.py").read_text()) == []


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("open(path, 'w').write(text)", ["write", "open"]),
        ("rundir.path_for(job, 'vars.json').write_text(text)", ["write_text"]),
        ("os.open(path, os.O_CREAT)", ["open"]),
        ("socket.socket(socket.AF_UNIX)", ["socket"]),
        ("tempfile.mkdtemp(prefix=p, dir=d)", []),
    ],
)
def test_the_scan_sees_each_way_to_write(source, found) -> None:
    assert sorted(_writes(source)) == sorted(found)


def test_importing_the_module_loads_no_pydantic_and_nothing_heavy(home) -> None:
    loaded = imports_of(module="claude_vm.runs.rundir", home=home)
    assert {"claude_vm.runs.job", "claude_vm.fleet.paths", "claude_vm.schema.patterns"} <= loaded
    heavy = {name for name in loaded if name.split(".")[0] in ("pydantic", "ansible", "yaml", "asyncio", "subprocess")}
    assert heavy == set()
