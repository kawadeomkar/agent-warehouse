"""What the new engine's tests share: tools/aw run as a person runs it, kept off this machine.

A test of the new engine is a file tests/unit/test_aw_*.py. tests/unit/conftest.py marks every test there `engine`,
which `just test engine` runs alone, and registers this kit, so that its fixture is every test's. A test reaches the
program through this kit and no other way (tests/unit/test_aw_guards.py reads every test file's syntax tree for one):

- `home`, a fixture: the test's own fleet home, conftest.py's `aw_home` (AGENT_WAREHOUSE_HOME), made, with HOME and the
  XDG folders in it too, set in this process for the test's length, so that code run in process reaches none of the
  developer's folders either;
- `run_aw(*words, home=...)`: tools/aw under `python -I`, in an environment built from a list of names
  (`harness.PASSED_ON`) and the home's own, never this process's whole one; with failing stand-ins first on PATH, for
  a browser opener, a clipboard and this machine's tailscale, gh and claude (`harness._desktop`), and for its ssh,
  ansible-playbook and login keychain (`harness._refused`); stdin /dev/null unless a test gives one, a process group of
  its own, ended whole on a timeout; and a test that fails if the run reached a stand-in;
- `aw_at_a_terminal(*words, home=...)`: the same at a pseudo-terminal of its own (`harness._at_a_pseudo_terminal`);
- `imports_of(*words, home=...)`: the modules a run loaded, from python's `-X importtime`;
- `searched_for(secrets, *paths)`: which files hold one of the secrets, failing a search that read nothing.

docs/architecture/testing.md §11.1 says how the suite holds them.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import IO

import harness
import pytest

from claude_vm.fleet import paths
from claude_vm.secret import Secret

AW = harness.REPO / "tools" / "aw"
# The module every run of tools/aw imports, whatever it was asked: a list of imports without it read nothing.
ENTRY = "claude_vm.cli.main"


def home_variables(home: Path) -> dict[str, str]:
    """What points a run at home: the fleet's home (AGENT_WAREHOUSE_HOME), the person's (HOME), its XDG folders."""
    return {
        paths.VARIABLE: str(home),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
    }


@pytest.fixture
def home(aw_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """This test's own home: the directory conftest.py's aw_home names AGENT_WAREHOUSE_HOME, made, and HOME and the XDG
    folders set into it in this process too, for the test's length. No second mechanism: the home is aw_home's."""
    aw_home.mkdir()
    for name, value in home_variables(aw_home).items():
        monkeypatch.setenv(name, value)
    return aw_home


def stand_ins(home: Path) -> Path:
    """The folder of the stand-ins of every run from home - its .desktop and its .refused - made on first use: beside
    the home, never in it, so that the home holds only what a run made."""
    root = home.parent / f"{home.name}.stand-ins"
    root.mkdir(exist_ok=True)
    harness._desktop(root)
    harness._refused(root)
    return root


def environment(home: Path, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment of a run of tools/aw from home: the names harness.PASSED_ON lists (and LC_*) from this process,
    home's variables over them, the stand-ins first on PATH and BROWSER one of them; then env, a test's own."""
    root = stand_ins(home)
    built = {name: value for name, value in os.environ.items() if name in harness.PASSED_ON or name.startswith("LC_")}
    built.update(home_variables(home))
    first = [str(root / ".refused"), str(root / ".desktop")]
    built["PATH"] = os.pathsep.join([*first, os.environ.get("PATH", os.defpath)])
    built["BROWSER"] = str(root / ".desktop" / "open")
    built.update(env or {})
    return built


def _argv(words: Iterable[str], program: str | Path, cwd: Path, interpreter: Iterable[str] = ()) -> list[str]:
    assert (cwd / program).resolve() == AW.resolve(), f"{program} from {cwd} is not tools/aw"
    return [sys.executable, "-I", *interpreter, str(program), *words]


def _run(
    words: tuple[str, ...],
    *,
    home: Path,
    stdin: int | IO | str,
    env: Mapping[str, str] | None,
    cwd: Path | None,
    program: str | Path,
    timeout: float,
    interpreter: Iterable[str] = (),
) -> subprocess.CompletedProcess[str]:
    assert stdin is not None, "a run of tools/aw never reads this terminal: give it /dev/null, a file or its text"
    where = Path(cwd) if cwd is not None else home
    argv = _argv(words, program, where, interpreter)
    text = stdin if isinstance(stdin, str) else None
    process = subprocess.Popen(
        argv,
        cwd=where,
        env=environment(home, env),
        stdin=subprocess.PIPE if text is not None else stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # A group of its own, so that a run cut short ends with what it started.
        process_group=0,
    )
    try:
        stdout, stderr = process.communicate(text, timeout=timeout)
    except subprocess.TimeoutExpired:
        harness._end(process)
        pytest.fail(f"tools/aw did not finish within {timeout} seconds")
    except BaseException:
        harness._end(process)
        raise
    harness._fail_if_the_desktop_was_reached(stand_ins(home))
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def run_aw(
    *words: str,
    home: Path,
    stdin: int | IO | str = subprocess.DEVNULL,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    program: str | Path = AW,
    timeout: float = 60,
) -> subprocess.CompletedProcess[str]:
    """Run tools/aw with words from cwd (home unless given) and return its status, stdout and stderr, as text.

    stdin is /dev/null, a file, or text the run is sent; never this terminal. env sets variables over the kit's
    (environment). program is tools/aw by its absolute path, unless a test names it as typed from cwd ("tools/aw" from
    the checkout); it is never another program. A run that outlives timeout seconds fails the test, ended whole, and
    one that reached a stand-in fails it once it ends.
    """
    return _run(words, home=home, stdin=stdin, env=env, cwd=cwd, program=program, timeout=timeout)


def aw_at_a_terminal(
    *words: str, home: Path, env: Mapping[str, str] | None = None, cwd: Path | None = None, **terminal
) -> tuple[int, bytes]:
    """Run tools/aw with words at a pseudo-terminal of its own, from cwd (home unless given), in run_aw's environment;
    its status and every byte it wrote. terminal goes to harness._at_a_pseudo_terminal: the window's size, what to
    type (act), the timeout, the poll and what to explain of a run that hung. One that reached a stand-in fails the
    test."""
    where = Path(cwd) if cwd is not None else home
    rc, output = harness._at_a_pseudo_terminal(_argv(words, AW, where), where, environment(home, env), **terminal)
    harness._fail_if_the_desktop_was_reached(stand_ins(home))
    return rc, output


def imports_of(*words: str, home: Path, env: Mapping[str, str] | None = None) -> frozenset[str]:
    """Every module a run of tools/aw with words loaded, by its dotted name, from python's -X importtime: the
    interpreter's own start among them. Whatever the run's status. A list without the program's entry read nothing,
    and fails the test."""
    result = _run(
        words,
        home=home,
        stdin=subprocess.DEVNULL,
        env=env,
        cwd=None,
        program=AW,
        timeout=60,
        interpreter=("-X", "importtime"),
    )
    loaded = frozenset(
        line.rsplit("|", 1)[1].strip()
        for line in result.stderr.splitlines()
        if line.startswith("import time:") and line.count("|") == 2 and not line.endswith("| imported package")
    )
    if ENTRY not in loaded:
        pytest.fail(f"-X importtime named no {ENTRY}, so nothing was read of what the run loaded:\n{result.stderr}")
    return loaded


def _files(path: Path) -> Iterator[Path]:
    """path, or every file under it, as a walk finds them: no link followed."""
    if path.is_file() and not path.is_symlink():
        yield path
    elif path.is_dir() and not path.is_symlink():
        for root, _dirs, files in os.walk(path):
            for name in sorted(files):
                file = Path(root) / name
                if file.is_file() and not file.is_symlink():
                    yield file


def searched_for(secrets: Iterable[str | bytes | Secret], *paths: Path) -> list[tuple[Path, str | bytes | Secret]]:
    """Each file under paths - a file, or a folder read whole, no link followed - that holds one of secrets, with the
    secret it holds: an empty list where none does. A canary, never a search that passes for having read nothing: no
    secret to look for, or a path that gave no byte to read - not there, empty, or a folder of empty files - fails the
    test."""
    needles = []
    for secret in secrets:
        value = secret.reveal() if isinstance(secret, Secret) else secret
        needles.append((secret, value.encode() if isinstance(value, str) else bytes(value)))
    if not needles or not all(needle for _, needle in needles):
        pytest.fail("a search needs at least one secret to look for, and none of them empty")
    if not paths:
        pytest.fail("a search needs somewhere to look")
    found = []
    for path in paths:
        read = 0
        for file in _files(Path(path)):
            data = file.read_bytes()
            read += len(data)
            found += [(file, secret) for secret, needle in needles if needle in data]
        if not read:
            pytest.fail(f"searched nothing: no byte to read at {path}")
    return found
