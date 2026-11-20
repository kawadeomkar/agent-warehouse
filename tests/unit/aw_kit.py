"""What the new engine's tests share: each test kept off this machine in its own process, and tools/aw run as a person
runs it.

A test of the new engine is a file tests/unit/test_aw_*.py. tests/unit/conftest.py marks every test there `engine`,
which `just test engine` runs alone, and registers this kit, so that its fixtures are every test's:

- `engine_guard`, which no test asks for: for every test the marker selects, and no other, its own process gets HOME
  and the XDG folders in the test's own home (conftest.py's `aw_home`, AGENT_WAREHOUSE_HOME), failing stand-ins first
  on PATH and BROWSER one of them, every variable gone but harness.PASSED_ON's and the kit's own, and no connection
  over IP but to loopback; once the test ends, a stand-in that was reached or a connection that was refused fails it;
- `home`: that home, made.

A test reaches the program through this kit and no other way (tests/unit/test_aw_guards.py reads every test file's
syntax tree for one):

- `run_aw(*words, home=...)`: tools/aw under `python -I`, in an environment built from a list of names
  (`harness.PASSED_ON`) and the home's own, never this process's whole one; with failing stand-ins first on PATH, for
  a browser opener, a clipboard and this machine's tailscale, gh and claude (`harness._desktop`), and for its ssh,
  ansible-playbook and login keychain (`harness._refused`), and a test's own programs ahead of them only by
  `first_on_path`; stdin /dev/null unless a test gives one, a process group of its own, ended whole on a timeout; and a
  test that fails if the run reached a stand-in;
- `aw_at_a_terminal(*words, home=...)`: the same at a pseudo-terminal of its own (`harness._at_a_pseudo_terminal`);
- `imports_of(*words, home=...)`: the modules a run loaded, from python's `-X importtime`; and with `module=`, what
  importing that one module of this checkout's lib/ loaded;
- `searched_for(secrets, *paths)`: which files hold one of the secrets, failing a search that read nothing.

docs/architecture/testing.md §11.1 says how the suite holds them.
"""

from __future__ import annotations

import dataclasses
import errno
import ipaddress
import os
import re
import socket
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
# A module imports_of is asked about: a dotted name, each part an identifier, held whole before it goes into -c.
MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
# What a test's env= never sets, beside every XDG_ variable: what points a run at its home or finds the stand-ins. The
# home is home=, and a program of the test's own goes first on PATH by first_on_path.
HELD = frozenset({"PATH", "HOME", "BROWSER", paths.VARIABLE})
# pytest's own, which it sets again for each phase of a test: kept in an engine test's process, never passed to a run.
PYTEST_OWN = "PYTEST_CURRENT_TEST"


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


def _passed_on(name: str) -> bool:
    """Whether a run, and an engine test's own process, keeps name from the shell running pytest: harness.PASSED_ON's
    names and the locale's."""
    return name in harness.PASSED_ON or name.startswith("LC_")


def stand_ins(home: Path) -> Path:
    """The folder of the stand-ins of every run from home - its .desktop and its .refused - made on first use: beside
    the home, never in it, so that the home holds only what a run made."""
    root = home.parent / f"{home.name}.stand-ins"
    root.mkdir(exist_ok=True)
    harness._desktop(root)
    harness._refused(root)
    return root


def _path(root: Path, first: Iterable[str | Path], inherited: str) -> str:
    """A PATH: first, then the stand-ins under root, then the rest of inherited."""
    standing = [str(root / ".refused"), str(root / ".desktop")]
    rest = [entry for entry in inherited.split(os.pathsep) if entry not in standing]
    return os.pathsep.join([*map(str, first), *standing, *rest])


# ---------------------------------------------------------------- the guard of an engine test's own process ----


class Offline(OSError):
    """What an engine test's own process meets when it looks up a name or connects past loopback."""


@dataclasses.dataclass
class Guard:
    """An engine test's guard: the folder of its stand-ins, and each lookup or connection it refused, said."""

    stand_ins: Path
    refused: list[str]


def _loopback(host: object) -> bool:
    """Whether host, as getaddrinfo or connect is given it, is this machine's loopback: none, localhost, or a loopback
    address. Any other name is not: looking it up is already the network's."""
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    if host in (None, "", "localhost"):
        return True
    try:
        address = ipaddress.ip_address(str(host).strip("[]"))
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


def _offline(monkeypatch: pytest.MonkeyPatch, guard: Guard) -> None:
    """socket.getaddrinfo and a socket's connect and connect_ex over IP refused in this process, for the test's length,
    for anything that is not loopback; each refusal kept in guard. A local socket by its path is not refused."""
    lookup, connect, connect_ex = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex

    def refuse(what: str) -> None:
        said = (
            f"{what}: an engine test reaches nothing past loopback - start a fake of tests/molecule/ on loopback"
            " (harness._serve) and point the code at it"
        )
        guard.refused.append(said)
        raise Offline(errno.ENETUNREACH, said)

    def getaddrinfo(host, port, *args, **kwargs):
        if not _loopback(host):
            refuse(f"looking up {host!r}")
        return lookup(host, port, *args, **kwargs)

    def held(original):
        def connecting(sock, address):
            host = address[0] if isinstance(address, tuple) and address else address
            if sock.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(host):
                refuse(f"connecting to {host!r}")
            return original(sock, address)

        return connecting

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", held(connect))
    monkeypatch.setattr(socket.socket, "connect_ex", held(connect_ex))


@pytest.fixture(autouse=True)
def engine_guard(request, aw_home: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Guard | None]:
    """An engine test's own process kept off this machine, as a run of tools/aw is: for every test the `engine` marker
    selects, and no other, so that no test asks for it. HOME and the XDG folders in aw_home; the stand-ins first on PATH
    and BROWSER one of them; every variable removed but harness.PASSED_ON's, the locale's, the kit's own and pytest's
    PYTEST_CURRENT_TEST; no lookup or connection over IP but to loopback. Once the test ends, a stand-in that was
    reached, or a connection that was refused, fails it. PATH is what holds a program here: one started by its absolute
    path, /usr/bin/security say, is not caught."""
    if request.node.get_closest_marker("engine") is None:
        yield None
        return
    root = stand_ins(aw_home)
    kept = {*home_variables(aw_home), "BROWSER", PYTEST_OWN}
    for name in [name for name in os.environ if not _passed_on(name) and name not in kept]:
        monkeypatch.delenv(name)
    for name, value in home_variables(aw_home).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PATH", _path(root, (), os.environ.get("PATH", os.defpath)))
    monkeypatch.setenv("BROWSER", str(root / ".desktop" / "open"))
    guard = Guard(root, [])
    _offline(monkeypatch, guard)
    yield guard
    harness._fail_if_the_desktop_was_reached(root)
    if guard.refused:
        pytest.fail("this test reached past loopback:\n" + "\n".join(guard.refused))


@pytest.fixture
def home(aw_home: Path) -> Path:
    """This test's own home, made: the directory conftest.py's aw_home names AGENT_WAREHOUSE_HOME, which engine_guard
    has made HOME and the XDG folders' home in this process too. No second mechanism: the home is aw_home's."""
    aw_home.mkdir()
    return aw_home


# ---------------------------------------------------------------- tools/aw run as a person runs it ----


def environment(
    home: Path, env: Mapping[str, str] | None = None, first_on_path: Iterable[str | Path] = ()
) -> dict[str, str]:
    """The environment of a run of tools/aw from home: the names harness.PASSED_ON lists (and LC_*) from this process,
    home's variables over them, first_on_path's folders first on PATH, the stand-ins next and BROWSER one of them; then
    env, a test's own, which never sets what HELD names or an XDG_ variable: that would lift a guard unsaid."""
    given = dict(env or {})
    held = sorted(name for name in given if name in HELD or name.startswith("XDG_"))
    if held:
        pytest.fail(
            f"env= never sets {', '.join(held)}, which would take the kit's home or stand-ins away: a program of the"
            " test's own goes first on PATH by first_on_path=(dir, ...), ahead of the stand-ins, which it keeps,"
            " and the home is home="
        )
    root = stand_ins(home)
    built = {name: value for name, value in os.environ.items() if _passed_on(name)}
    built.update(home_variables(home))
    built["PATH"] = _path(root, first_on_path, os.environ.get("PATH", os.defpath))
    built["BROWSER"] = str(root / ".desktop" / "open")
    built.update(given)
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
    code: str | None = None,
    first_on_path: Iterable[str | Path] = (),
) -> subprocess.CompletedProcess[str]:
    assert stdin is not None, "a run of tools/aw never reads this terminal: give it /dev/null, a file or its text"
    where = Path(cwd) if cwd is not None else home
    if code is not None:
        # python runs code (-c) in tools/aw's place: imports_of's one module.
        argv = [sys.executable, "-I", *interpreter, "-c", code]
    else:
        argv = _argv(words, program, where, interpreter)
    text = stdin if isinstance(stdin, str) else None
    process = subprocess.Popen(
        argv,
        cwd=where,
        env=environment(home, env, first_on_path),
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
    first_on_path: Iterable[str | Path] = (),
) -> subprocess.CompletedProcess[str]:
    """Run tools/aw with words from cwd (home unless given) and return its status, stdout and stderr, as text.

    stdin is /dev/null, a file, or text the run is sent; never this terminal. env sets variables over the kit's
    (environment), never PATH, HOME, BROWSER, AGENT_WAREHOUSE_HOME or an XDG_ one; first_on_path puts a test's own
    folders of programs ahead of the stand-ins, which stay. program is tools/aw by its absolute path, unless a test
    names it as typed from cwd ("tools/aw" from the checkout); it is never another program. A run that outlives timeout
    seconds fails the test, ended whole, and one that reached a stand-in fails it once it ends.
    """
    return _run(
        words,
        home=home,
        stdin=stdin,
        env=env,
        cwd=cwd,
        program=program,
        timeout=timeout,
        first_on_path=first_on_path,
    )


def aw_at_a_terminal(
    *words: str,
    home: Path,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    first_on_path: Iterable[str | Path] = (),
    **terminal,
) -> tuple[int, bytes]:
    """Run tools/aw with words at a pseudo-terminal of its own, from cwd (home unless given), in run_aw's environment
    (env and first_on_path as run_aw takes them); its status and every byte it wrote. terminal goes to
    harness._at_a_pseudo_terminal: the window's size, what to type (act), the timeout, the poll and what to explain of a
    run that hung. One that reached a stand-in fails the test."""
    where = Path(cwd) if cwd is not None else home
    built = environment(home, env, first_on_path)
    rc, output = harness._at_a_pseudo_terminal(_argv(words, AW, where), where, built, **terminal)
    harness._fail_if_the_desktop_was_reached(stand_ins(home))
    return rc, output


def imports_of(
    *words: str,
    home: Path,
    env: Mapping[str, str] | None = None,
    module: str | None = None,
    first_on_path: Iterable[str | Path] = (),
) -> frozenset[str]:
    """Every module a run of tools/aw with words loaded, by its dotted name, from python's -X importtime: the
    interpreter's own start among them. Whatever the run's status; env and first_on_path as run_aw takes them. A list
    without the program's entry read nothing, and fails the test.

    With module, a dotted name and no words: every module that `python -I -X importtime -c "import <module>"` loaded,
    with this checkout's lib/ first on its path, in the same environment; the interpreter's start among them, so a
    test that wants what the module alone adds takes away what importing its package loaded. A name that is not a
    dotted name of identifiers, an import that failed, or a list without the module fails the test."""
    code, entry = None, ENTRY
    if module is not None:
        if words or not MODULE.fullmatch(module):
            pytest.fail(f"imports_of takes one module by its dotted name, and no words with it: {module!r}, {words!r}")
        code, entry = f"import sys; sys.path.insert(0, {str(harness.REPO / 'lib')!r}); import {module}", module
    result = _run(
        words,
        home=home,
        stdin=subprocess.DEVNULL,
        env=env,
        cwd=None,
        program=AW,
        timeout=60,
        interpreter=("-X", "importtime"),
        code=code,
        first_on_path=first_on_path,
    )
    if code is not None and result.returncode != 0:
        pytest.fail(f"importing {module} failed:\n{result.stderr}")
    loaded = frozenset(
        line.rsplit("|", 1)[1].strip()
        for line in result.stderr.splitlines()
        if line.startswith("import time:") and line.count("|") == 2 and not line.endswith("| imported package")
    )
    if entry not in loaded:
        pytest.fail(f"-X importtime named no {entry}, so nothing was read of what the run loaded:\n{result.stderr}")
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
