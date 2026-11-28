"""aw install in a checkout: the launcher's install_env run as a job under the fleet's lock
(lib/claude_vm/cli/install.py, aw, tools/aw; docs/architecture/engine.md §16.3).

In checkouts the test makes, with the program's own lib/ copied in, a stand-in uv that records its words and the
variables it is given, and the state folder in the test's own home: aw install runs the launcher's step once, as a child
that carries its job's marker, with the words today's just install gives uv for each kind of environment, says the
child's lines and writes them to the run's text log, and leaves a job whose record says succeeded; it is refused, the
holder named, while another run holds the fleet's lock, and runs nothing; --lock is uv lock alone, here detached; each
of the installed program's four options is refused in a checkout in one sentence, and an installed program as not built
yet; a failing uv fails the run with EXITS' failed. A Python that says it is below 3.13, given as PYTHON or found as
python3, is refused by install_env before uv starts, in just install's sentence; one that says 3.13 goes on; tools/aw
refuses a real interpreter below the floor before it imports anything of the checkout's. The launcher's two fixes of
review/code/H20.md: a relative XDG_DATA_HOME names the checkout's environment, never the working folder's; and a copy of
the launcher outside its checkout says so in one sentence.
"""

from __future__ import annotations

import ast
import contextlib
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import harness
import pytest
from aw_kit import LAUNCHER, environment, run_launcher
from harness import GIT_ENV, REPO

from claude_vm.cli import install, main
from claude_vm.fleet import paths
from claude_vm.runs import job as jobs
from claude_vm.runs import locks
from claude_vm.runs.attached import BY_CTRL_C, STOP_GRACE
from claude_vm.schema.words import EXITS

# git that reads no config of the developer's.
NO_GIT_CONFIG = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
# A tools/aw that says which checkout it is and with which Python, for the launcher's own cases.
PROGRAM = """#!/usr/bin/env python3
import json
import sys

print(json.dumps({{"checkout": {name!r}, "prefix": sys.prefix}}))
"""
# A python3 first on PATH that is no virtualenv's: tools/env-run then names the checkout's own environment.
NO_VIRTUALENV = "#!/bin/sh\nexit 1\n"
# A job's marker as the launcher takes it, for install_env run directly.
JOB = "0123456789abcdef"
# The launcher's Python version for the checkout's own environment (held equal to the justfile's by test_aw_cli_parse).
MANAGED = re.search(r"^managed_python=(\S+)$", LAUNCHER.read_text(), re.MULTILINE)[1]
# The floor's sentence, just install's (tests/unit/test_justfile.py's BELOW_THE_FLOOR), with aw install's program.
BELOW_THE_FLOOR = (
    "error: [{named}] is Python {version}, and this project needs Python 3.13 or later, as its program aw and"
    " ansible-core 2.22 do: make a virtualenv of Python 3.13 or later and activate it or name it with PYTHON, or bring"
    " none - PYTHON unset and python3 no virtualenv's - and {program} install makes this checkout's own with uv."
)
# install's refusals, as a person reads them.
IN_A_CHECKOUT = (
    "error: {flags} {are} the installed program's, after 3.0 (cards H14 and H15): a checkout gets a newer version with"
    " git pull, and then aw install"
)
NOT_YET = "error: aw install for an installed program is not built yet (card H14 builds it)"
COPIED = (
    "error: {launcher} is a copy of aw outside its checkout, so it finds none of the checkout's tools: link to the aw"
    " at the top of your checkout with ln -s, never copy it"
)


def _git(checkout: Path, *args: str) -> None:
    env = {**os.environ, **GIT_ENV}
    subprocess.run(["git", *args], cwd=checkout, env=env, check=True, capture_output=True, timeout=60)


def _checkout(where: Path, name: str, *, program: str = "real") -> Path:
    """A checkout a test makes: a git repository with the launcher and the tools it runs, copied, and this checkout's
    tools/aw with its lib/ copied too (so the handler's program root is this one, never the suite's checkout) - or,
    with program="stand-in", a tools/aw that says which checkout it is."""
    checkout = where / name
    (checkout / "tools").mkdir(parents=True)
    _git(checkout, "init", "--quiet")
    shutil.copy2(LAUNCHER, checkout / "aw")
    for tool in ("env-run", "git-hook", "stale_scripts.py"):
        shutil.copy2(REPO / "tools" / tool, checkout / "tools" / tool)
    shutil.copy2(REPO / "uv.lock", checkout / "uv.lock")
    (checkout / "justfile").write_text("# a justfile above, which aw never runs\n")
    if program == "real":
        shutil.copy2(REPO / "tools" / "aw", checkout / "tools" / "aw")
        shutil.copytree(REPO / "lib", checkout / "lib", ignore=shutil.ignore_patterns("__pycache__"))
    else:
        (checkout / "tools" / "aw").write_text(PROGRAM.format(name=name))
    return checkout.resolve()


def _uv(where: Path) -> dict[str, str]:
    """A uv that records each run - its words, the variables it is given and where it ran - says one line on each
    stream, and ends with UV_STAND_IN_EXIT's status (0 where it is not set): the variable that names it, for env."""
    uv = where / "uv-bin" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text(
        "#!/bin/sh\n"
        f'{{ for word in "$@"; do printf "%s\\n" "$word"; done; echo --end--; }} >> "{where}/uv.args"\n'
        f'echo "UV_PROJECT_ENVIRONMENT=${{UV_PROJECT_ENVIRONMENT:-}}" >> "{where}/uv.env"\n'
        f'echo "UV_PYTHON_DOWNLOADS=${{UV_PYTHON_DOWNLOADS:-}}" >> "{where}/uv.env"\n'
        f'echo "AGENT_WAREHOUSE_JOB=${{AGENT_WAREHOUSE_JOB:-}}" >> "{where}/uv.env"\n'
        f'echo "PWD=$(pwd -P)" >> "{where}/uv.env"\n'
        'echo "uv stand-in: $1"\n'
        'echo "uv stand-in says on stderr: $1" >&2\n'
        'exit "${UV_STAND_IN_EXIT:-0}"\n'
    )
    uv.chmod(0o755)
    return {"UV": str(uv)}


def _uv_runs(where: Path) -> list[list[str]]:
    record = where / "uv.args"
    text = record.read_text() if record.exists() else ""
    return [run.splitlines() for run in text.split("--end--\n")[:-1]]


def _uv_env(where: Path) -> list[str]:
    """The variables the first run of uv was given, and the folder it ran in."""
    return (where / "uv.env").read_text().splitlines()[:4]


def _no_virtualenv(where: Path) -> Path:
    folder = where / "no-virtualenv"
    folder.mkdir(exist_ok=True)
    (folder / "python3").write_text(NO_VIRTUALENV)
    (folder / "python3").chmod(0o755)
    return folder


def _managed(home: Path, checkout: Path) -> Path:
    """The checkout's own environment, made as uv makes it, before the run: its python a wrapper of the suite's own,
    so the program runs and the launcher has nothing to make first. The environment's folder."""
    data = subprocess.check_output([checkout / "tools" / "env-run", "--data"], env=environment(home), text=True)
    venv = Path(data.strip()) / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (venv / "bin" / "python").chmod(0o755)
    return venv


def _python_reporting(where: Path, version: str, name: str = "python") -> Path:
    """A Python that says it is `version` when asked as a person asks one (`python --version`), and is the suite's
    own, a virtualenv's, for anything else."""
    python = where / f"reports-{version}" / name
    python.parent.mkdir()
    python.write_text(
        "#!/bin/sh\n"
        f'if [ "$#" -eq 1 ] && [ "$1" = --version ]; then echo "Python {version}"; exit 0; fi\n'
        f'exec "{sys.executable}" "$@"\n'
    )
    python.chmod(0o755)
    return python


def _program(checkout: Path) -> str:
    """The program as the launcher says it, run by its path with no aw on PATH that is it."""
    return shlex.quote(str(checkout / "aw"))


def _jobs(home: Path) -> list[jobs.Job]:
    return jobs.listed(paths.jobs_dir(paths.home(REPO)))


def _said(stderr: str) -> list[str]:
    """What a run said on standard error but the dispatch's line of which fleet it drives, the test's own home."""
    return [line for line in stderr.splitlines() if not line.startswith("aw drives the fleet in ")]


def _ended(job: jobs.Job, within: float = 60) -> dict:
    """The record of job once its run has ended, its lock free."""
    end = time.monotonic() + within
    while jobs.lock_held(job):
        if time.monotonic() > end:
            pytest.fail(f"job {job.id} did not end within {within} seconds")
        time.sleep(0.1)
    return job.read()


# ---------------------------------------------------------------- the run ----


@pytest.mark.parametrize("kind", ["your virtualenv", "the checkout's own"])
def test_install_runs_the_launcher_s_step_once_as_a_job_that_succeeds(home, tmp_path, kind) -> None:
    checkout = _checkout(tmp_path, "co")
    env = {**_uv(tmp_path), **NO_GIT_CONFIG}
    first_on_path: tuple[Path, ...] = ()
    if kind == "your virtualenv":
        env["PYTHON"] = sys.executable
    else:
        venv = _managed(home, checkout)
        env["PYTHON"], first_on_path = "", (_no_virtualenv(tmp_path),)
    result = run_launcher(
        "install", home=home, launcher=checkout / "aw", cwd=tmp_path, env=env, first_on_path=first_on_path
    )
    assert result.returncode == EXITS["done"], result.stderr
    # Today's just install's words for each kind of environment, from the checkout's top, once.
    [ran] = _uv_runs(tmp_path)
    [made] = _jobs(home)
    if kind == "your virtualenv":
        assert ran[:5] == ["sync", "--locked", "--inexact", "--python", sys.executable]
        given = [f"UV_PROJECT_ENVIRONMENT={sys.prefix}", "UV_PYTHON_DOWNLOADS=never"]
    else:
        assert ran == ["sync", "--locked", "--managed-python", "--python", MANAGED]
        given = [f"UV_PROJECT_ENVIRONMENT={venv}", "UV_PYTHON_DOWNLOADS=automatic"]
    # The child carries the job's marker (runs/children.py), as the launcher's --install-env asks.
    assert _uv_env(tmp_path) == [*given, f"AGENT_WAREHOUSE_JOB={made.id}", f"PWD={checkout}"]
    hook = checkout / ".git" / "hooks" / "pre-commit"
    assert hook.read_bytes() == (REPO / "tools" / "git-hook").read_bytes() and os.access(hook, os.X_OK)
    # The child's lines on the stream it said them on, the hint to link aw among them.
    assert "uv stand-in: sync" in result.stdout.splitlines()
    assert "Installed git's pre-commit hook." in result.stdout.splitlines()
    assert "To run aw from any folder, link it into ~/.local/bin" in result.stdout
    assert _said(result.stderr) == ["uv stand-in says on stderr: sync"]
    # A job, its record succeeded, its text log holding the child's lines.
    record = made.read()
    # The command as typed: the launcher by its path, as the program says it.
    assert (record["verb"], record["outcome"]) == ("install", "succeeded")
    assert record["command"] == f"{_program(checkout)} install"
    log = Path(record["log"]).read_text()
    for line in ("uv stand-in: sync", "uv stand-in says on stderr: sync", "Installed git's pre-commit hook."):
        assert f": {line}\n" in log, log


@contextlib.contextmanager
def _a_run(home: Path) -> Iterator[jobs.Job]:
    """A run made in the test's own process that holds the fleet's lock exclusively, as an init does."""
    made = jobs.create(paths.jobs_dir(paths.home(REPO)), "init", "aw init", None, [], {"kind": "terminal"}, True)
    try:
        locks.take(made, "fleet", [], "machine")
        yield made
    finally:
        for _, held, _ in locks._TAKEN.pop(jobs._key(made.folder), []):
            os.close(held)
        if made._held_here() is not None:
            jobs.drop(made)


def test_install_is_refused_naming_the_run_that_holds_the_fleet_and_runs_nothing(home, tmp_path) -> None:
    checkout = _checkout(tmp_path, "held")
    env = {"PYTHON": sys.executable, **_uv(tmp_path), **NO_GIT_CONFIG}
    with _a_run(home) as holder:
        result = run_launcher("install", home=home, launcher=checkout / "aw", env=env)
        assert [each.id for each in _jobs(home)] == [holder.id]
    assert (result.returncode, result.stdout) == (EXITS["refused"], "")
    [said] = _said(result.stderr)
    assert said.startswith(f"error: the fleet is held by job {holder.id}, `aw init`, started "), said
    assert said.endswith(f": `{_program(checkout)} cancel {holder.id}` stops it"), said
    assert _uv_runs(tmp_path) == [] and not (checkout / ".git" / "hooks" / "pre-commit").exists()


def test_install_lock_detached_is_uv_lock_alone_in_a_job(home, tmp_path) -> None:
    checkout = _checkout(tmp_path, "lock")
    env = {"PYTHON": sys.executable, **_uv(tmp_path), **NO_GIT_CONFIG}
    result = run_launcher("install", "--lock", "--detach", home=home, launcher=checkout / "aw", env=env)
    assert result.returncode == EXITS["done"], result.stderr
    [made] = _jobs(home)
    # The starter's lines: the job, and how to follow and cancel it.
    assert result.stdout.splitlines()[0] == made.id, result.stdout
    record = _ended(made)
    assert (record["outcome"], record["starter"]) == ("succeeded", {"kind": "detach"})
    assert _uv_runs(tmp_path) == [["lock"]]
    marked = f"AGENT_WAREHOUSE_JOB={made.id}"
    assert _uv_env(tmp_path)[:3] == ["UV_PROJECT_ENVIRONMENT=", "UV_PYTHON_DOWNLOADS=never", marked]
    assert not (checkout / ".git" / "hooks" / "pre-commit").exists()
    # Its output is its log alone: each line the child said, once.
    log = Path(record["log"]).read_text()
    assert log.count("uv stand-in: lock") == 1 and log.count("uv stand-in says on stderr: lock") == 1, log


def test_a_failing_uv_fails_the_run_with_exit_status_two(home, tmp_path) -> None:
    checkout = _checkout(tmp_path, "fails")
    env = {"PYTHON": sys.executable, **_uv(tmp_path), "UV_STAND_IN_EXIT": "3", **NO_GIT_CONFIG}
    result = run_launcher("install", home=home, launcher=checkout / "aw", env=env)
    assert result.returncode == EXITS["failed"] == 2, result.stderr
    [made] = _jobs(home)
    assert made.read()["outcome"] == "failed"
    assert len(_uv_runs(tmp_path)) == 1 and not (checkout / ".git" / "hooks" / "pre-commit").exists()


def _sleeping_uv(where: Path) -> dict[str, str]:
    """A uv that writes its pid once it has begun, says one line and sleeps a minute; at a SIGINT it writes that it had
    one and ends as uv does at a Ctrl-C, 130: the variable that names it, for env."""
    uv = where / "uv-bin" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text(
        "#!/bin/sh\n"
        f"trap 'echo interrupted > \"{where}/uv.stopped\"; exit 130' INT\n"
        f'echo "$$" > "{where}/uv.pid"\n'
        'echo "uv stand-in: $1"\n'
        "sleep 60\n"
    )
    uv.chmod(0o755)
    return {"UV": str(uv)}


def _gone(pid: int, within: float = 10) -> bool:
    end = time.monotonic() + within
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_a_ctrl_c_stops_uv_at_once_and_the_run_is_cancelled(home, tmp_path) -> None:
    # The child's group is its own, so a terminal's Ctrl-C, sent to the program's group, reaches uv only through the
    # body (and C14's cancel is the same SIGINT): well inside the grace, never killed at its end.
    checkout = _checkout(tmp_path, "stopped")
    env = environment(home, {"PYTHON": sys.executable, **_sleeping_uv(tmp_path), **NO_GIT_CONFIG})
    begun = tmp_path / "uv.pid"
    with open(tmp_path / "err", "wb") as err:
        process = subprocess.Popen(
            [str(checkout / "aw"), "install"],
            cwd=tmp_path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err,
            env=env,
            process_group=0,
            preexec_fn=harness._default_signals,  # noqa: PLW1509 - only signal, safe beside xdist's thread
        )
    try:
        end = time.monotonic() + 60
        while not begun.exists() or not begun.read_text().strip():
            assert process.poll() is None, (tmp_path / "err").read_text()
            assert time.monotonic() < end, "the stand-in uv never began"
            time.sleep(0.05)
        uv = int(begun.read_text())
        sent = time.monotonic()
        os.killpg(process.pid, signal.SIGINT)
        try:
            status = process.wait(timeout=STOP_GRACE / 2)
        except subprocess.TimeoutExpired:
            pytest.fail(f"aw install did not end within {STOP_GRACE / 2} s of a Ctrl-C: uv was never stopped")
        took = time.monotonic() - sent
    finally:
        if process.poll() is None:
            harness._end(process)
    said = (tmp_path / "err").read_text()
    assert status == EXITS["cancelled"] == 130, said
    assert took < STOP_GRACE / 3, took
    # uv had the SIGINT, and ended of it, by itself.
    assert (tmp_path / "uv.stopped").read_text() == "interrupted\n"
    assert _gone(uv)
    [made] = _jobs(home)
    record = made.read()
    assert (record["outcome"], record["why"], record["exit"]) == ("cancelled", BY_CTRL_C, 130)
    assert "uv stand-in: sync" in Path(record["log"]).read_text()


# ---------------------------------------------------------------- refused before the run ----


def _in_process(monkeypatch, capsys, root: Path, *words: str) -> tuple[int, list[str]]:
    """aw install with words in this process, its program root root: the status and what it said on standard error."""
    monkeypatch.setattr(install, "PROGRAM_ROOT", root)
    status = main.main([str(REPO / "tools" / "aw"), "install", *words])
    return status, _said(capsys.readouterr().err)


def _a_checkout(where: Path) -> Path:
    """A tree D1 tells for a checkout: a justfile and a .git."""
    (where / ".git").mkdir(parents=True)
    (where / "justfile").write_text("")
    return where


@pytest.mark.parametrize(
    ("words", "flags"),
    [
        (("--version", "v3.0.0"), "--version is"),
        (("--newer",), "--newer is"),
        (("--rollback",), "--rollback is"),
        (("--without", "mcp"), "--without is"),
        (("--version=",), "--version is"),
        (("--version", ""), "--version is"),
        (("--without=",), "--without is"),
        (("--without", ""), "--without is"),
        (("--newer", "--lock", "--without", "mcp"), "--newer, --without are"),
    ],
)
def test_the_installed_program_s_options_are_refused_in_a_checkout(monkeypatch, capsys, home, tmp_path, words, flags):
    status, said = _in_process(monkeypatch, capsys, _a_checkout(tmp_path / "co"), *words)
    flag, are = flags.rsplit(" ", 1)
    assert (status, said) == (EXITS["refused"], [IN_A_CHECKOUT.format(flags=flag, are=are)])
    assert _jobs(home) == []


def test_an_installed_program_is_refused_as_not_built_yet(monkeypatch, capsys, home, tmp_path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    (release / "release.json").write_text("{}\n")
    # Told by its release.json before its .git, as a release is a clone of its tag.
    _a_checkout(release)
    assert _in_process(monkeypatch, capsys, release) == (EXITS["refused"], [NOT_YET])
    assert _in_process(monkeypatch, capsys, release, "--newer") == (EXITS["refused"], [NOT_YET])
    neither = tmp_path / "neither"
    neither.mkdir()
    assert _in_process(monkeypatch, capsys, neither) == (
        EXITS["refused"],
        [
            f"error: {neither} is neither a checkout of agent-warehouse (a justfile and a .git) nor an installed"
            " release (release.json): aw install has nothing to install"
        ],
    )
    assert _jobs(home) == []


# ---------------------------------------------------------------- the floor ----


@pytest.mark.parametrize("version", ["3.12.7", "3.10.14", "3.9.6"])
@pytest.mark.parametrize("given", ["PYTHON", "python3"])
def test_install_env_refuses_a_python_below_the_floor_before_uv_starts(home, tmp_path, version, given) -> None:
    checkout = _checkout(tmp_path, "floor", program="stand-in")
    python = _python_reporting(tmp_path, version, "python" if given == "PYTHON" else "python3")
    env = {**_uv(tmp_path), "AGENT_WAREHOUSE_JOB": JOB, **NO_GIT_CONFIG}
    if given == "PYTHON":
        env["PYTHON"], first_on_path, named = str(python), (), str(python)
    else:
        env["PYTHON"], first_on_path, named = "", (python.parent,), "python3"
    result = run_launcher("--install-env", home=home, launcher=checkout / "aw", env=env, first_on_path=first_on_path)
    assert (result.returncode, result.stdout) == (1, "")
    said = BELOW_THE_FLOOR.format(named=named, version=version, program=_program(checkout))
    assert result.stderr == f"{said}\n"
    assert _uv_runs(tmp_path) == [] and not (checkout / ".git" / "hooks" / "pre-commit").exists()


@pytest.mark.parametrize("version", ["3.13.0", "3.14.2"])
def test_install_env_takes_a_python_at_or_above_the_floor(home, tmp_path, version) -> None:
    checkout = _checkout(tmp_path, "floor", program="stand-in")
    python = _python_reporting(tmp_path, version)
    env = {"PYTHON": str(python), **_uv(tmp_path), "AGENT_WAREHOUSE_JOB": JOB, **NO_GIT_CONFIG}
    result = run_launcher("--install-env", home=home, launcher=checkout / "aw", env=env)
    assert result.returncode == 0, result.stderr
    assert "needs Python" not in result.stdout + result.stderr
    # The Python it named, as that Python says it is: the suite's own, behind the stand-in.
    assert _uv_runs(tmp_path)[0][:5] == ["sync", "--locked", "--inexact", "--python", sys.executable]


def test_aw_install_s_run_fails_with_the_floor_s_sentence_and_no_uv(home, tmp_path) -> None:
    checkout = _checkout(tmp_path, "floor")
    python = _python_reporting(tmp_path, "3.12.7")
    env = {"PYTHON": str(python), **_uv(tmp_path), **NO_GIT_CONFIG}
    result = run_launcher("install", home=home, launcher=checkout / "aw", env=env)
    assert result.returncode == EXITS["failed"], result.stderr
    # The child is the launcher by its path, so it says the program so.
    said = BELOW_THE_FLOOR.format(named=python, version="3.12.7", program=_program(checkout))
    assert _said(result.stderr) == [said]
    [made] = _jobs(home)
    assert made.read()["outcome"] == "failed" and _uv_runs(tmp_path) == []


def _floor_of_the_project() -> tuple[int, ...]:
    found = re.search(r'^requires-python = ">=(\d+)\.(\d+)"$', (REPO / "pyproject.toml").read_text(), re.MULTILINE)
    assert found, "pyproject.toml's requires-python is >=<major>.<minor>"
    return tuple(int(part) for part in found.groups())


def test_tools_aw_refuses_below_the_floor_before_it_imports_anything_of_the_checkout_s() -> None:
    tree = ast.parse((REPO / "tools" / "aw").read_text())
    body = [node for node in tree.body if not isinstance(node, ast.Expr)]
    [floor] = [node for node in body if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "FLOOR"]
    assert ast.literal_eval(floor.value) == _floor_of_the_project()
    # The first if of the file, before the program's own at its end.
    check = next(node for node in body if isinstance(node, ast.If))
    assert ast.unparse(check.test) == "sys.version_info < FLOOR"
    assert ast.unparse(check.body[-1]).startswith("sys.exit(")
    # Before the checkout's lib/ is put on the path, and before any import but the standard library's.
    first_of_ours = next(
        index
        for index, node in enumerate(body)
        if "sys.path" in ast.unparse(node) or (isinstance(node, ast.ImportFrom) and node.module == "claude_vm")
    )
    assert body.index(check) < first_of_ours
    imports = [node for node in body[: body.index(check)] if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert {alias.name for node in imports for alias in node.names} <= {"annotations", "os", "shlex", "sys", "Path"}


def _below_the_floor() -> tuple[str, str] | None:
    """A real Python below 3.13 on this computer, as the launcher runs it: its path as it says it, and its version."""
    for name in ("python3.12", "python3.11", "python3.10", "python3.9", "/usr/bin/python3"):
        found = shutil.which(name)
        if not found:
            continue
        said = subprocess.run(
            [found, "-I", "-c", "import sys; print(sys.executable); print(*sys.version_info[:3], sep='.')"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if said.returncode != 0:
            continue
        executable, version = said.stdout.split()
        if tuple(int(part) for part in version.split(".")[:2]) < _floor_of_the_project():
            return executable, version
    return None


def test_tools_aw_refuses_a_real_python_below_the_floor_in_just_install_s_sentence(home, tmp_path) -> None:
    old = _below_the_floor()
    if old is None:
        pytest.skip("no Python below 3.13 on this computer")
    executable, version = old
    # A folder a shell would split and that holds a ': the program said as the launcher quotes it.
    checkout = _checkout(tmp_path, "it's old", program="stand-in")
    shutil.copy2(REPO / "tools" / "aw", checkout / "tools" / "aw")
    result = run_launcher("status", home=home, launcher=checkout / "aw", env={"PYTHON": executable})
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == BELOW_THE_FLOOR.format(named=executable, version=version, program=_program(checkout)) + "\n"


# ---------------------------------------------------------------- the launcher's two fixes (review/code/H20.md) ----


def test_a_relative_data_home_names_the_checkout_s_environment_never_the_working_folder_s(home, tmp_path) -> None:
    checkout = _checkout(tmp_path, "relative", program="stand-in")
    # The checkout's folder outside it, under XDG_DATA_HOME, as tools/env-run names it from the kit's home.
    data = subprocess.check_output([checkout / "tools" / "env-run", "--data"], env=environment(home), text=True)
    relative = Path("share") / Path(data.strip()).relative_to(environment(home)["XDG_DATA_HOME"])
    ours = checkout / relative / "venv" / "bin" / "python"
    ours.parent.mkdir(parents=True)
    ours.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    ours.chmod(0o755)
    # The same relative path in the folder aw is run from holds a planted program, which must never run.
    work = tmp_path / "work"
    planted = work / relative / "venv" / "bin" / "python"
    planted.parent.mkdir(parents=True)
    mark = tmp_path / "planted-ran"
    planted.write_text(f"#!/bin/sh\necho planted > '{mark}'\n")
    planted.chmod(0o755)
    env = {"PYTHON": "", **_uv(tmp_path), **NO_GIT_CONFIG}
    result = run_launcher(
        "status",
        home=home,
        launcher=checkout / "aw",
        cwd=work,
        env=env,
        first_on_path=(_no_virtualenv(tmp_path),),
        data_home="share",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f'{{"checkout": "relative", "prefix": "{sys.prefix}"}}'
    assert not mark.exists() and _uv_runs(tmp_path) == [] and result.stderr == ""


def test_a_copy_of_the_launcher_outside_its_checkout_says_so_in_one_sentence(home, tmp_path) -> None:
    folder = tmp_path / "bin"
    folder.mkdir()
    copy = folder / "aw"
    shutil.copy2(LAUNCHER, copy)
    for words in (("status",), ("install",), ("--install-env",)):
        result = run_launcher(*words, home=home, launcher=copy, cwd=tmp_path)
        assert (result.returncode, result.stdout) == (1, "")
        assert result.stderr == COPIED.format(launcher=folder.resolve() / "aw") + "\n"
