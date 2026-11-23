"""Shared fixtures for testing custom Ansible modules without a live API."""

from __future__ import annotations

import functools
import inspect
import json
import os
import re
import shutil
import statistics
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import ansible.module_utils
import interrupts
import pytest
from ansible.module_utils import basic
from ansible.module_utils.testing import patch_module_args

from claude_vm.fleet import paths

ROOT = Path(__file__).resolve().parents[2]
# The new engine's test kit, whose fixture `home` every test may ask for (tests/unit/aw_kit.py).
pytest_plugins = ("aw_kit",)
# The new engine's tests: every test of a file named so, which `just test engine` runs alone (-m engine).
ENGINE_FILES = "test_aw_"
# The tests of today's suite that read the tree, whose result a new file or a changed page can change, marked `tree`:
# `just test --short` runs them beside the engine's (-m "engine or tree"), for a change that touches no file of main's
# but CLAUDE.md, docs/ and zensical.toml (docs/architecture/testing.md §11.1). Each a file, every test of it, or a file
# and a test's name, every case of that test. Found by planting the faults such a change can make and running the whole
# suite over them once, and by reading which of today's tests read a file only the new engine's branch has (the
# launcher aw): test_aw_guards.py's FOUND holds what each fault failed, and what reading found, and each entry here to
# one of them. A test of today's that a short-set change broke all the same joins it, and FOUND with it. A set literal:
# test_aw_guards.py reads it with ast.literal_eval.
TREE = {
    "test_anchors.py",
    "test_ci.py",
    "test_claude_vm.py",
    "test_commands.py",
    # What the project writes on this machine: every file that makes a write, CLAUDE.md's invariant, the security model.
    "test_controller_writes.py",
    # Every tracked file, for the live output's variable: a new module of the engine's could name it.
    "test_deploy_live.py::test_no_other_test_or_scenario_draws_a_run",
    "test_docs.py",
    "test_gathered_facts.py",
    # Every file of tests/unit/, for a golden template turned on.
    "test_golden.py::test_golden_templates_stay_off_wherever_a_test_or_the_vagrantfile_runs_a_deploy",
    # The launcher at the checkout's top, aw, names the managed Python the justfile and CI's setup do.
    "test_justfile.py::test_the_managed_python_is_one_version",
    # Every test file named as a live test, test_*_live.py, an engine's among them.
    "test_live_recipes.py::test_every_live_test_runs_a_real_playbook_only_as_its_own_tests_do",
    # Every module of the MCP server's package, a new one among them.
    "test_mcp_tools.py::test_the_server_reads_nothing_of_its_environment_but_through_the_allow_list",
    # The key files, and the new engine's table of them, lib/claude_vm/vendors/keys.py.
    "test_pins.py",
    "test_ratchets.py",
    "test_shards.py",
    # Every test file that names tools/status.
    "test_status.py::test_every_status_run_in_a_test_writes_into_its_own_directory",
    "test_tracked_files.py",
}
# What the modules in playbooks/library/ share, for the tests that load one by its path: Ansible puts a playbook's
# module_utils folder on the path it builds the playbook's modules from, and nothing does in this process.
ansible.module_utils.__path__.append(str(ROOT / "playbooks" / "module_utils"))
# The Galaxy collections every Ansible a test starts reads: this checkout's own, as every recipe points Ansible at
# them (tools/env-run --collections). just test exports the same; one set by hand is kept.
os.environ.setdefault(
    "ANSIBLE_COLLECTIONS_PATH",
    subprocess.run(
        [str(ROOT / "tools" / "env-run"), "--collections"], capture_output=True, text=True, check=True
    ).stdout.strip(),
)


class AnsibleExitJson(Exception):
    """Raised instead of sys.exit() when a module calls exit_json."""


class AnsibleFailJson(Exception):
    """Raised instead of sys.exit() when a module calls fail_json."""


def _exit_json(*args, **kwargs):
    kwargs.setdefault("changed", False)
    raise AnsibleExitJson(kwargs)


def _fail_json(*args, **kwargs):
    kwargs["failed"] = True
    raise AnsibleFailJson(kwargs)


@pytest.fixture
def patch_module(monkeypatch):
    """Make AnsibleModule raise instead of exiting so tests can inspect results."""
    monkeypatch.setattr(basic.AnsibleModule, "exit_json", _exit_json)
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", _fail_json)


def run_main(main, args):
    """Run a module's main() with the given args; return its exit_json payload."""
    with patch_module_args(args), pytest.raises(AnsibleExitJson) as exc:
        main()
    return exc.value.args[0]


def fail_main(main, args):
    """Run a module's main() with the given args; return its fail_json payload."""
    with patch_module_args(args), pytest.raises(AnsibleFailJson) as exc:
        main()
    return exc.value.args[0]


# Each test's serial duration, by node ID, that --shard balances by; a test it lacks counts as the mean.
DURATIONS = ROOT / "tests" / "unit" / "durations.json"


# The developer's own fleet folders, as the run found them, and what of them a test made, changed or removed; and the
# directory of the session's own that AGENT_WAREHOUSE_HOME names before a test's own does (docs/development/testing.md).
DEVELOPERS = pytest.StashKey[tuple]()
TOUCHED = pytest.StashKey[list]()
SESSION_HOME = pytest.StashKey[str]()


# The names the session watches at the top of a folder of the developer's: every name paths owns, and the names
# beneath the state folder, which are at the top of the installed program's state folder (WHOLE). In a checkout's own
# .aw/, its fleet's files, client.yml and the key alone (CHECKOUT): not its state folder, which the developer's own aw
# run from that checkout writes its jobs, locks and logs into while a suite runs, and which can be made again
# (review/code/D1.md, finding 11). The installed program's folders stay watched whole until H14 decides for them.
WHOLE = (*paths.OWN, *paths.BENEATH_STATE)
CHECKOUT = tuple(name for name in paths.OWN if name != paths.STATE)


def footprint(watched) -> dict[str, tuple]:
    """watched maps each folder to the names watched there: each of those names in its folder, and everything beneath
    one that is a folder, as lstat sees it, so that a name made, changed or removed shows as a difference. Nothing
    else there is looked at (a sign-in on a machine the product configured writes the names
    paths.RESERVED_ON_A_MACHINE reserves into the same folder), and no file is opened: the key file is the
    developer's secret."""
    found = {}
    for top in (Path(folder) / name for folder, names in watched.items() for name in names):
        beneath = [Path(root) / entry for root, dirs, files in os.walk(top) for entry in (*dirs, *files)]
        for path in (top, *beneath):
            try:
                seen = os.lstat(path)
            except FileNotFoundError:
                continue
            found[str(path)] = (seen.st_mode, seen.st_size, seen.st_mtime_ns, seen.st_ctime_ns, seen.st_ino)
    return found


def _main_worktree(root: Path) -> Path | None:
    """The main worktree of the checkout at root, which differs from it where the suite runs from another worktree:
    the folder of git's common directory. None where git cannot say."""
    try:
        found = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            env={name: value for name, value in os.environ.items() if not name.startswith("GIT_")},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    common = Path(found.stdout.strip())
    return common.parent if found.returncode == 0 and common.is_absolute() and common.name == ".git" else None


def developers_folders(environ, root: Path = ROOT) -> dict[Path, tuple[str, ...]]:
    """The developer's own fleet folders, which no test touches, each with the names watched there: the installed
    program's three (the config, the state and the data folder's agent-warehouse/) whole, this checkout's .aw/ and the
    main worktree's but for their state folders, and the folder AGENT_WAREHOUSE_HOME names where the developer
    exported it, whole unless it is one of those .aw/ folders. Each by its name alone: no file of theirs is opened, so
    no fleet_home is followed."""
    watched = dict.fromkeys(paths.installed(environ), WHOLE)
    watched[root / paths.CHECKOUT_HOME] = CHECKOUT
    main = _main_worktree(root)
    if main is not None:
        watched[main / paths.CHECKOUT_HOME] = CHECKOUT
    if environ.get(paths.VARIABLE):
        try:
            watched.setdefault(paths.home(root, environ).root, WHOLE)
        except paths.Refused:
            pass  # A home paths refuses is one the new engine never writes in.
    return watched


def watch_the_developers_folders(config, environ, root: Path = ROOT) -> None:
    """Read the developer's fleet folders (developers_folders) before any test runs, with what environ exported: once,
    in the process that ends the session, never in an xdist worker."""
    if not hasattr(config, "workerinput"):
        folders = developers_folders(environ, root)
        config.stash[DEVELOPERS] = (folders, footprint(folders))


def pytest_configure(config):
    # A kill or a hangup ends this process as Ctrl-C would, in the controller and in every worker: the test running
    # gets a KeyboardInterrupt, its run and everything under it ended by the harness on the way out, where the default
    # ended the process at once and left them (tests/unit/interrupts.py).
    interrupts.install()
    # First, while AGENT_WAREHOUSE_HOME is still what the developer exported: a folder of theirs to watch.
    watch_the_developers_folders(config, os.environ)
    # Before a test's own directory (aw_home), what runs first - a module's playbook run, made once for its tests -
    # gets a directory of the session's own, never the developer's fleet, whatever their shell exported.
    config.stash[SESSION_HOME] = os.environ[paths.VARIABLE] = tempfile.mkdtemp(prefix="aw-home-")


def pytest_unconfigure(config):
    shutil.rmtree(config.stash.get(SESSION_HOME, ""), ignore_errors=True)


def pytest_sessionfinish(session, exitstatus):
    """Fail the session if a test made, changed or removed a name the new engine owns in the developer's folders."""
    watched = session.config.stash.get(DEVELOPERS, None)
    if watched is None:
        return
    folders, before = watched
    after = footprint(folders)
    touched = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    if touched:
        session.config.stash[TOUCHED] = touched
        if session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    touched = config.stash.get(TOUCHED, [])
    if touched:
        terminalreporter.section("the developer's fleet folders", red=True)
        terminalreporter.line("A test made, changed or removed these, where the tests never reach (fleet/paths.py):")
        for path in touched:
            terminalreporter.line(f"  {path}")


@pytest.fixture(autouse=True)
def aw_home(tmp_path) -> Iterator[Path]:
    """AGENT_WAREHOUSE_HOME in this test's own directory, which nothing here makes: the new engine's fleet, its state
    and its key are there and never the developer's, and the harness passes it on to every run (PASSED_ON). Function
    scoped, as a session-scoped autouse fixture would join every test to one xdist group (_shared). Set by a
    MonkeyPatch of its own, undone at its own teardown: a test's monkeypatch.undo() never puts the developer's back."""
    home = tmp_path / "aw-home"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(paths.VARIABLE, str(home))
        yield home


def pytest_addoption(parser):
    # Registered only when this conftest is an initial one: `pytest tests/unit`, as just test unit runs it.
    # A bare `pytest --shard` is refused as an unknown option, never run as the whole suite.
    parser.addoption("--shard", metavar="I/N", help="run only the I-th of N shards, each group of tests whole")


@functools.cache
def _asks_by_name(function) -> bool:
    """Whether a test function calls request.getfixturevalue; a function whose source cannot be read is taken to."""
    try:
        return "getfixturevalue" in inspect.getsource(function)
    except (OSError, TypeError):
        return True


def _shared(item) -> set[str] | None:
    """This project's module-scoped fixtures item reaches but its module's EACH_WORKER; None when no look can tell.

    A test reaches a fixture through request.getfixturevalue only by a `fixture` parameter naming it, this suite's one
    way; any other call to it leaves what it asks for unknown.
    """
    names = list(item._fixtureinfo.names_closure)
    if _asks_by_name(item.function):
        named = item.callspec.params.get("fixture") if hasattr(item, "callspec") else None
        if not isinstance(named, str):
            return None
        names.append(named)
    each_worker = getattr(item.module, "EACH_WORKER", frozenset())
    manager, found, seen = item.session._fixturemanager, set(), set()
    while names:
        name = names.pop()
        if name in seen:
            continue
        seen.add(name)
        defs = manager.getfixturedefs(name, item)
        if not defs or defs[-1].func.__module__.startswith("_pytest"):
            continue
        if defs[-1].scope != "function" and name not in each_worker:
            found.add(name)
        names.extend(defs[-1].argnames)
    return found


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Keep the tests that share a module-scoped fixture on one xdist worker; with --shard, in one shard.

    Such a fixture is a whole playbook run, made once for its module: split across workers or shards, each would make
    it again. The tests that reach one, directly or through the fixtures they ask for, are its group, and two fixtures
    one test reaches are one group, so every run is still made once; the module's other tests go wherever there is
    room. Its whole module was one group until 2026-11, which left a shard's last minutes to one worker. A module with
    a test that asks for a fixture by name any other way than a `fixture` parameter stays whole. A module's
    EACH_WORKER names the fixtures cheap enough to make again on each worker that needs one - a fake server - which
    join nothing. Only this project's fixtures count: pytest's own tmp_path rests on a session-scoped one, made per
    worker anyway. First, so that xdist's own hook, which turns the marks into groups, sees them, and pytest's own,
    which selects by -m, sees the new engine's tests marked `engine` (every test of a file ENGINE_FILES names) and the
    tests of today's that read the tree marked `tree` (TREE).
    """
    for item in items:
        if item.path.name.startswith(ENGINE_FILES):
            item.add_marker(pytest.mark.engine)
        if {item.path.name, f"{item.path.name}::{getattr(item, 'originalname', item.name)}"} & TREE:
            item.add_marker(pytest.mark.tree)
    reached = {id(item): _shared(item) for item in items}
    whole = {item.module.__name__ for item in items if reached[id(item)] is None}
    parent: dict[str, str] = {}

    def root(key: str) -> str:
        while parent.setdefault(key, key) != key:
            key = parent[key]
        return key

    def keys(item) -> list[str]:
        # Words only, as xdist's @<group> on a test's name must be for tools/unit_durations.py to take it off.
        module = item.module.__name__
        return [module] if module in whole else [f"{module}__{name}" for name in sorted(reached[id(item)])]

    for item in items:
        joined = keys(item)
        for key in joined[1:]:
            parent[root(key)] = root(joined[0])
    units: dict[str, list] = {}
    for item in items:
        group = root(keys(item)[0]) if keys(item) else None
        if group:
            item.add_marker(pytest.mark.xdist_group(group))
        units.setdefault(group or item.nodeid, []).append(item)
    shard = config.getoption("--shard", None)
    if not shard:
        return
    match = re.fullmatch(r"([1-9][0-9]*)/([1-9][0-9]*)", shard)
    if not match or int(match[1]) > int(match[2]):
        raise pytest.UsageError(f"--shard takes I/N, two whole numbers with 1 <= I <= N, not {shard}")
    index, count = int(match[1]), int(match[2])
    durations = json.loads(DURATIONS.read_text()) if DURATIONS.exists() else {}
    mean = statistics.mean(durations.values()) if durations else 1.0
    weight = {key: sum(durations.get(item.nodeid, mean) for item in members) for key, members in units.items()}
    loads, owner = [0.0] * count, {}
    for key in sorted(units, key=lambda key: (-weight[key], key)):
        slot = loads.index(min(loads))
        loads[slot] += weight[key]
        owner[key] = slot
    kept = {id(item) for key, members in units.items() if owner[key] == index - 1 for item in members}
    config.hook.pytest_deselected(items=[item for item in items if id(item) not in kept])
    items[:] = [item for item in items if id(item) in kept]
