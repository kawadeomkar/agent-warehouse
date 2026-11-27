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
  with `background=True`, in a process group of its own that is never the terminal's foreground, as a shell's `aw init
  &` starts it, under a session leader of the kit's own that ends it, and fails the test, where the terminal stops it;
  with `layer1=Layer1(play, connections)`, the run's system play swapped for a play of the test's own and each machine
  reached over F3's test-only local connection, as test_aw_configure.py's Swapped does in the test's own process, so
  that a stop of layer 1's child goes through the run's own ladder (`SWAPPED`, the kit's alone to start);
- `run_launcher(*words, home=...)`: the launcher at the checkout's top, aw, which runs tools/aw, run by its path as a
  person runs it, in run_aw's environment and with the suite's own Python as PYTHON unless a test names another: this
  checkout's, a link to it, or a copy of it in a checkout a test made, and never this checkout's asked to install;
  `on_path(home)` is a folder of the test's own whose aw is a link to a launcher, for `first_on_path`, so that aw on
  PATH is that launcher and every line it prints says aw, as a person who linked it there reads them;
- `imports_of(*words, home=...)`: the modules a run loaded, from python's `-X importtime`; and with `module=`, what
  importing that one module of this checkout's lib/ loaded;
- `searched_for(secrets, *paths)`: which files hold one of the secrets, failing a search that read nothing;
- `owned_as_recorded(config)`: the Owned of a VM whose configuration a test read, as though its notes held its own
  record, for the tests of what a write sends: the one place outside pve/ownership.py's Member.read that marks a
  Member read (tests/unit/test_aw_ownership.py's scan names it); every other test owns through a read.

`STOP_UNSEEN` (strict) and `STOP_GONE` mark, on macOS with Python 3.14 alone, the tests of a stop at the terminal that
fail there for a cause card C28 finds.

docs/architecture/testing.md §11.1 says how the suite holds them.
"""

from __future__ import annotations

import dataclasses
import errno
import ipaddress
import json
import os
import re
import shutil
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
# The launcher at the checkout's top, which runs tools/aw with the Python of the environment the checkout runs.
LAUNCHER = harness.REPO / "aw"
# The module every run of tools/aw imports, whatever it was asked: a list of imports without it read nothing.
ENTRY = "claude_vm.cli.main"
# A module imports_of is asked about: a dotted name, each part an identifier, held whole before it goes into -c.
MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
# What a test's env= never sets, beside every XDG_ variable: what points a run at its home or finds the stand-ins. The
# home is home=, and a program of the test's own goes first on PATH by first_on_path.
HELD = frozenset({"PATH", "HOME", "BROWSER", paths.VARIABLE})
# pytest's own, which it sets again for each phase of a test: kept in an engine test's process, never passed to a run.
PYTEST_OWN = "PYTEST_CURRENT_TEST"
# What macOS with Python 3.14 fails for a cause the code does not show, in CI's two runs on the draft pull request #310
# (2026-11-22), where 3.13 on the same runner passes: each test marked by name, on that system and version alone, its
# reason the card that finds the cause on a macOS run, C28. Every other system and version holds them whole.
ON_MACOS_314 = sys.platform == "darwin" and sys.version_info[:2] == (3, 14)
# The stopped-asking test's two rows of a child the run started itself, stopped by SIGTTIN or by SIGTTOU: the watch said
# no stop in either run, where it said route.py's ssh in both. Strict, so a pass there fails the mark and is seen.
STOP_UNSEEN = pytest.mark.xfail(
    ON_MACOS_314,
    reason="card C28: on macOS with Python 3.14 the watch said no stop of a child the run started itself",
    strict=True,
)
# The children's test of a stopped child, in one of the two runs: the watch saw its SIGTTOU stop, and the test's own
# waitid, which leaves a stop as it is, found none at once after. Not strict, since it passed in the other run.
STOP_GONE = pytest.mark.xfail(
    ON_MACOS_314,
    reason="card C28: on macOS with Python 3.14 a stop the watch saw was gone at the next look, once in two runs",
    strict=False,
)


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


def _own_folder(folder: str | Path, own: Path) -> Path:
    """folder, a test's own folder of programs, held to the test's own directory own: a system folder put first would
    give the test this machine's real ssh, security or browser opener ahead of the stand-ins."""
    path = Path(folder)
    if not path.is_absolute() or not path.resolve().is_relative_to(own.resolve()):
        pytest.fail(
            f"first_on_path takes a folder of the test's own directory, {own}: {folder} is not one, and a folder put"
            " ahead of the stand-ins must never hold this machine's own programs"
        )
    return path


# Every tool a stand-in stands in for, in a test's own process and in a run: the desktop's, the guarded and the refused.
STOOD_IN = (*harness.DESKTOP_TOOLS, *harness.GUARDED_TOOLS, *harness.REFUSED_TOOLS)


def off_path(path: str, root: Path, first: Iterable[Path] = ()) -> list[str]:
    """Each tool a stand-in stands in for that path finds first in a folder that is neither a stand-in's under root nor
    one of first, a test's own put first on purpose, where it was found: none, while the stand-ins hold."""
    allowed = {root / ".refused", root / ".desktop", *map(Path, first)}
    found = {tool: shutil.which(tool, path=path) for tool in STOOD_IN}
    return [f"{tool} at {where}" for tool, where in found.items() if where and Path(where).parent not in allowed]


# ---------------------------------------------------------------- the guard of an engine test's own process ----


class Offline(OSError):
    """What an engine test's own process meets when it looks up a name or connects past loopback."""


@dataclasses.dataclass
class Guard:
    """An engine test's guard: the folder of its stand-ins, and each lookup or connection it refused, said. Its changes
    are a MonkeyPatch of its own, which a test's monkeypatch.undo() never reaches; own is the test's own directory, and
    first the folders of its own programs a test put first on PATH (first_on_path)."""

    stand_ins: Path
    refused: list[str]
    own: Path
    patch: pytest.MonkeyPatch
    first: list[Path] = dataclasses.field(default_factory=list)

    def first_on_path(self, folder: str | Path) -> Path:
        """A folder of the test's own programs - a stand-in ssh, say - first on PATH in the test's own process, ahead of
        the stand-ins, which stay: held to the test's own directory, and undone with the guard."""
        path = _own_folder(folder, self.own)
        self.patch.setenv("PATH", os.pathsep.join([str(path), os.environ.get("PATH", os.defpath)]))
        self.first.append(path)
        return path

    def held(self, when: str) -> None:
        """Fail the test where its own process's PATH no longer finds every tool a stand-in stands in for at the
        stand-in, or at a folder the test put first: a PATH of the test's own took the stand-ins away."""
        found = off_path(os.environ.get("PATH", ""), self.stand_ins, self.first)
        if found:
            pytest.fail(
                f"{when}, this test's own PATH found this machine's own programs ahead of the stand-ins ("
                + ", ".join(found)
                + "): never set PATH in an engine test - put a folder of its own first with"
                " engine_guard.first_on_path(dir), or first_on_path=(dir, ...) for a run of the kit's"
            )


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


# Where an engine test's guard is kept on its item, for the check at the end of the test's call.
GUARD = pytest.StashKey[Guard]()


@pytest.fixture(autouse=True)
def engine_guard(request, aw_home: Path) -> Iterator[Guard | None]:
    """An engine test's own process kept off this machine, as a run of tools/aw is: for every test the `engine` marker
    selects, and no other, so that no test asks for it. HOME and the XDG folders in aw_home; the stand-ins first on PATH
    and BROWSER one of them; every variable removed but harness.PASSED_ON's, the locale's, the kit's own and pytest's
    PYTEST_CURRENT_TEST; no lookup or connection over IP but to loopback. All of it is a MonkeyPatch of the guard's own,
    undone at its own teardown, so a test's monkeypatch.undo() leaves it in place. Once the test's call ends, and again
    at teardown, a PATH that finds this machine's own programs ahead of the stand-ins fails it; once the test ends, a
    stand-in that was reached, or a connection that was refused, fails it. PATH is what holds a program here: one
    started by its absolute path, /usr/bin/security say, is not caught. pytest makes a fixture of a wider scope before
    this and ends it after: tests/unit/test_aw_guards.py holds every such fixture of an engine file to a list."""
    if request.node.get_closest_marker("engine") is None:
        yield None
        return
    root = stand_ins(aw_home)
    kept = {*home_variables(aw_home), "BROWSER", PYTEST_OWN}
    patch = pytest.MonkeyPatch()
    try:
        for name in [name for name in os.environ if not _passed_on(name) and name not in kept]:
            patch.delenv(name)
        for name, value in home_variables(aw_home).items():
            patch.setenv(name, value)
        patch.setenv("PATH", _path(root, (), os.environ.get("PATH", os.defpath)))
        patch.setenv("BROWSER", str(root / ".desktop" / "open"))
        guard = Guard(root, [], aw_home.parent, patch)
        _offline(patch, guard)
        request.node.stash[GUARD] = guard
        yield guard
        guard.held("at its teardown")
        harness._fail_if_the_desktop_was_reached(root)
        if guard.refused:
            pytest.fail("this test reached past loopback:\n" + "\n".join(guard.refused))
    finally:
        patch.undo()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item):
    """An engine test's own PATH checked as its call ends, before the test's own monkeypatch is undone: a PATH it set
    there that finds this machine's own programs ahead of the stand-ins fails it."""
    result = yield
    guard = item.stash.get(GUARD, None)
    if guard is not None:
        guard.held("as its call ended")
    return result


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
    home's variables over them, first_on_path's folders first on PATH - each a folder of the test's own directory,
    home's parent - the stand-ins next and BROWSER one of them; then env, a test's own, which never sets what HELD
    names or an XDG_ variable: that would lift a guard unsaid."""
    first_on_path = [_own_folder(folder, home.parent) for folder in first_on_path]
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
    argv: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    assert stdin is not None, "a run of tools/aw never reads this terminal: give it /dev/null, a file or its text"
    where = Path(cwd) if cwd is not None else home
    # argv given is run_launcher's: the launcher, which runs tools/aw.
    if argv is None and code is not None:
        # python runs code (-c) in tools/aw's place: imports_of's one module.
        argv = [sys.executable, "-I", *interpreter, "-c", code]
    elif argv is None:
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


def run_launcher(
    *words: str,
    home: Path,
    launcher: str | Path = LAUNCHER,
    stdin: int | IO | str = subprocess.DEVNULL,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    timeout: float = 120,
    first_on_path: Iterable[str | Path] = (),
) -> subprocess.CompletedProcess[str]:
    """Run the launcher with words from cwd (home unless given), by its path as a person runs it, and return its
    status, stdout and stderr, as text: run_aw's environment (env and first_on_path as run_aw takes them), PYTHON the
    suite's own Python unless env names another.

    launcher is this checkout's, a link to it, or a copy of it byte for byte in a checkout a test made: never another
    program. This checkout's is never asked to install (--install-env, install --lock), and always runs with the
    suite's own Python as PYTHON, a virtualenv, so that it never makes an environment or puts a git hook here: a test
    that installs makes a checkout of its own."""
    path = Path(launcher) if Path(launcher).is_absolute() else (cwd or home) / launcher
    assert path.read_bytes() == LAUNCHER.read_bytes(), f"{launcher} is not the launcher"
    given = {"PYTHON": sys.executable, **(env or {})}
    if path.resolve() == LAUNCHER.resolve():
        assert words[:1] != ("--install-env",) and words[:2] != ("install", "--lock"), "this checkout never installs"
        assert given["PYTHON"] == sys.executable, "this checkout's launcher runs the suite's own environment"
    return _run(
        words,
        home=home,
        stdin=stdin,
        env=given,
        cwd=cwd,
        program=AW,
        timeout=timeout,
        first_on_path=first_on_path,
        argv=[str(launcher), *words],
    )


def on_path(home: Path, launcher: Path = LAUNCHER) -> Path:
    """A folder of the test's own directory, for first_on_path, whose aw is a link to launcher: aw on PATH is that
    launcher, so the program a run of it says is aw, as for a person who linked it there."""
    folder = home.parent / "aw-on-path"
    folder.mkdir(exist_ok=True)
    link = folder / "aw"
    if not link.is_symlink():
        link.symlink_to(launcher)
    return folder


# A session leader of the kit's own at the pseudo-terminal, for aw_at_a_terminal's background: the program and its words
# in a process group of their own, never the terminal's foreground, as a shell's `aw init &` starts them; waited for to
# their end or to a stop (WUNTRACED), which the leader says on one line (STOPPED) and ends, the group killed, since a
# stopped run would wait there till a person brought it to the foreground. Its status is the run's.
BACKGROUND = r"""
import os, signal, sys
child = os.fork()
if child == 0:
    try:
        os.setpgid(0, 0)
        os.execv(sys.argv[1], sys.argv[1:])
    finally:
        os._exit(127)
try:
    os.setpgid(child, child)
except OSError:
    pass
_, status = os.waitpid(child, os.WUNTRACED)
if os.WIFSTOPPED(status):
    print(f"aw_kit: the run in the background was stopped by signal {os.WSTOPSIG(status)}", flush=True)
    os.killpg(child, signal.SIGKILL)
    os.waitpid(child, 0)
    sys.exit(125)
code = os.waitstatus_to_exitcode(status)
sys.exit(code if code >= 0 else 128 - code)
"""
STOPPED = re.compile(rb"aw_kit: the run in the background was stopped by signal (\d+)")
# tools/aw run with its system play swapped and layer 1 handed F3's test-only local connection (Layer1): the words after
# the given JSON are tools/aw's path and its own, which it is run with, as itself, once the two are patched in.
SWAPPED = r"""
import json, runpy, sys
from pathlib import Path

given, program = json.loads(sys.argv[1]), sys.argv[2]
lib = str(Path(program).resolve().parents[1] / "lib")
sys.path.insert(0, lib)
from claude_vm import plugin

plugin.own(lib)
from claude_vm.guest import layer1, system

play = Path(given["play"])
system.PLAY = play
layer1.PLAY_NAMES = {**layer1.PLAY_NAMES, play: system.NAMES}
real = layer1.run


async def run(job, target, play, variables_file=None, **more):
    assert "test_connection" not in more and "test_collections" not in more, more
    more["test_connection"] = given["connections"][target.identity]
    return await real(job, target, play, variables_file, **more)


layer1.run = run
sys.argv = sys.argv[2:]
runpy.run_path(program, run_name="__main__")
"""


@dataclasses.dataclass(frozen=True)
class Layer1:
    """Layer 1 as a run of aw_at_a_terminal takes it: play, a play of the test's own in its own folder, in the system
    play's place, and by each machine's identity the host variables of F3's test-only local connection on it
    (aw_machine.layer1_host). The real roles converge only in molecule/system/, never here."""

    play: Path
    connections: Mapping[str, Mapping[str, object]]


def aw_at_a_terminal(
    *words: str,
    home: Path,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    first_on_path: Iterable[str | Path] = (),
    background: bool = False,
    layer1: Layer1 | None = None,
    **terminal,
) -> tuple[int, bytes]:
    """Run tools/aw with words at a pseudo-terminal of its own, from cwd (home unless given), in run_aw's environment
    (env and first_on_path as run_aw takes them); its status and every byte it wrote. terminal goes to
    harness._at_a_pseudo_terminal: the window's size, what to type (act), the timeout, the poll and what to explain of a
    run that hung. With background, the run is in a process group of its own, never the terminal's foreground, under a
    session leader of the kit's own (BACKGROUND): one the terminal stops (SIGTTOU, SIGTTIN) is ended, and fails the
    test. With layer1, its system play and layer 1's connection are the test's (Layer1, SWAPPED). One that reached a
    stand-in fails the test."""
    where = Path(cwd) if cwd is not None else home
    built = environment(home, env, first_on_path)
    argv = _argv(words, AW, where)
    if layer1 is not None:
        given = {
            "play": str(layer1.play),
            "connections": {key: dict(value) for key, value in layer1.connections.items()},
        }
        argv = [sys.executable, "-I", "-c", SWAPPED, json.dumps(given), *argv[2:]]
    if background:
        argv = [sys.executable, "-I", "-c", BACKGROUND, *argv]
    rc, output = harness._at_a_pseudo_terminal(argv, where, built, **terminal)
    harness._fail_if_the_desktop_was_reached(stand_ins(home))
    stopped = STOPPED.search(output) if background else None
    if stopped is not None:
        said = output.decode(errors="replace")
        pytest.fail(f"the run in the background was stopped by the terminal, signal {int(stopped.group(1))}:\n{said}")
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


def owned_as_recorded(config):
    """The Owned of the VM whose configuration a test read (an answers.VmConfig, as the client's read or answers.config
    of the root token's gives it), as though its notes held its own record of this engine's - kind vm, its own UUID,
    finished - whatever they hold: for the tests of what the client's writes and the record's writer send, whose VMs
    hold no record. Whether a member is the fleet's is the one check's (tests/unit/test_aw_ownership.py,
    test_aw_fleet.py), which reads the record a reader read. The one place outside pve/ownership.py's Member.read that
    marks a Member read, which test_aw_ownership.py's scan allows by its name: every other test owns through a read."""
    from claude_vm.pve import answers, ownership

    if not isinstance(config, answers.VmConfig):
        raise TypeError("owned_as_recorded takes a VM's configuration as read, an answers.VmConfig")
    member = ownership.Member(
        config.name,
        config.node,
        config.vmid,
        uuid=config.identity,
        digest=config.digest,
        recorded="vm",
        record_uuid=config.identity,
        stage="finished",
    )
    object.__setattr__(member, "_read", True)
    return ownership.owned(member)
