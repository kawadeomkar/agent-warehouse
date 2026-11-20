"""The new engine's tests and what keeps them off this machine (docs/architecture/testing.md §11.1).

Each test here is a rule a later change could break. Every test of a file tests/unit/test_aw_*.py is marked `engine`,
which `just test engine` runs alone, and runs with AGENT_WAREHOUSE_HOME in its own directory (conftest.py's aw_home).
No test runs tools/aw but through tests/unit/aw_kit.py, whose runs inherit a list of names, start with failing
stand-ins first on PATH, and fail their test when one was reached. A secret the engine holds is a `Secret`, never
said, and only the modules REVEALS names read its value.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import io
import json
import logging
import os
import pickle
import re
import shutil
import subprocess
import sys
from pathlib import Path

import harness
import pytest
from aw_kit import AW, ENTRY, aw_at_a_terminal, environment, imports_of, run_aw, searched_for, stand_ins
from harness import PASSED_ON, REPO, repo_files

from claude_vm.fleet import paths
from claude_vm.secret import MARK, Secret

UNIT = REPO / "tests" / "unit"
# A 2.x test file, which tests/unit/conftest.py must leave unmarked: one that is cheap to collect.
UNMARKED = UNIT / "test_anchors.py"
SECRET = "unit-test-aw-secret-value"

# The modules that read a Secret's value, each with why: the one place it is sent on. Empty until a card sends one.
REVEALS: dict[str, str] = {}


def _engine_files() -> list[Path]:
    return [path for path in repo_files("tests/unit", untracked=True) if re.fullmatch(r"test_aw_\w+\.py", path.name)]


# ---------------------------------------------------------------- the marker and the home ----

# Loaded into a collection of its own (`pytest --collect-only -p`): each test it collected, the file it is in, whether
# it is marked engine, and which fixture gives it aw_home, at what scope.
PROBE = """
import json
import os


def pytest_collection_finish(session):
    seen = []
    for item in session.items:
        defs = item._fixtureinfo.name2fixturedefs.get("aw_home", ())
        seen.append(
            {
                "file": item.path.name,
                "engine": item.get_closest_marker("engine") is not None,
                "home": [defs[-1].func.__module__, defs[-1].scope] if defs else None,
            }
        )
    with open(os.environ["AW_PROBE"], "w") as out:
        json.dump(seen, out)
"""


def test_every_engine_test_is_marked_and_runs_in_its_own_home(tmp_path) -> None:
    # Collected as the suite collects them, by tests/unit/conftest.py's hook: every test_aw_*.py file on the branch,
    # those written before the marker included, and a 2.x file that must stay unmarked.
    files = _engine_files()
    assert UNIT / "test_aw_guards.py" in files and UNMARKED.exists(), files
    (tmp_path / "aw_probe.py").write_text(PROBE)
    env = {name: value for name, value in os.environ.items() if name in PASSED_ON or name.startswith("LC_")}
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-p", "aw_probe"]
        + ["-c", str(REPO / "pyproject.toml"), "--rootdir", str(REPO), *map(str, files), str(UNMARKED)],
        cwd=tmp_path,
        env={**env, "AW_PROBE": str(tmp_path / "seen.json")},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    seen = json.loads((tmp_path / "seen.json").read_text())
    assert {item["file"] for item in seen} == {path.name for path in files} | {UNMARKED.name}
    engine = [item for item in seen if item["file"] != UNMARKED.name]
    assert [item for item in engine if not item["engine"]] == [], "every test of a test_aw_*.py file is marked engine"
    assert [item for item in seen if item["file"] == UNMARKED.name and item["engine"]] == []
    # conftest.py's own aw_home, made for each test: never one a file defines over it, nor one shared by a module.
    assert [item for item in engine if item["home"] != ["conftest", "function"]] == []


def test_the_home_is_the_tests_own_and_so_are_home_and_the_xdg_folders(home, aw_home, tmp_path) -> None:
    assert home == aw_home == tmp_path / "aw-home" and home.is_dir()
    for name in (paths.VARIABLE, "HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        assert Path(os.environ[name]).is_relative_to(home), name
    # The engine's own resolver, in this process, finds the fleet there.
    where = paths.home(REPO)
    assert where.root == home and paths.state(where).is_relative_to(home) and paths.key_file(where).is_relative_to(home)


# ---------------------------------------------------------------- tools/aw only through the kit ----

# What starts a program, or runs a file as one, by the name it is called by: subprocess's, os's, pty's, asyncio's,
# runpy's, and the harness's own way to a terminal.
STARTS = {
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
    "getoutput",
    "getstatusoutput",
    "system",
    "popen",
    "spawn",
    "posix_spawn",
    "posix_spawnp",
    "create_subprocess_exec",
    "create_subprocess_shell",
    "run_path",
    "_at_a_pseudo_terminal",
}
# os.execv and its kind, os.spawnv and its kind.
STARTS_LIKE = ("exec", "spawn")
# The kit's ways to the program, which a test calls.
KIT = {"run_aw", "aw_at_a_terminal", "imports_of"}
# tools/aw in a string: alone, at the end of a path, or in a command line.
NAMES_AW = re.compile(r"(?<![\w-])tools/aw(?![\w.-])")


def _callee(node: ast.Call) -> str:
    return getattr(node.func, "attr", getattr(node.func, "id", ""))


def _parts(node: ast.AST) -> list[str | None]:
    """The parts of a path built with /, each string split at its own /: None for a part that is no string."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _parts(node.left) + _parts(node.right)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.split("/")
    return [None]


def _two(parts: list[str | None]) -> bool:
    return any(parts[i : i + 2] == ["tools", "aw"] for i in range(len(parts) - 1))


def _names_aw(node: ast.AST, bound: set[str]) -> bool:
    """Whether node, or anything in it, names tools/aw: a string, a path built of its parts with / or as a call's
    arguments (Path("tools", "aw")), a name bound to one, or the kit's AW."""
    for inner in ast.walk(node):
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str) and NAMES_AW.search(inner.value):
            return True
        if isinstance(inner, ast.BinOp) and _two(_parts(inner)):
            return True
        if isinstance(inner, ast.Call) and _two([part for arg in inner.args for part in _parts(arg)]):
            return True
        if isinstance(inner, ast.Name) and inner.id in bound:
            return True
        if isinstance(inner, ast.Attribute) and inner.attr == "AW" and getattr(inner.value, "id", "") == "aw_kit":
            return True
    return False


def _bound(tree: ast.AST) -> set[str]:
    """The names a file binds to tools/aw's path, wherever: an assignment, a loop's target, a parameter's default, the
    kit's AW imported; and so on, through names bound to those."""
    bound: set[str] = set()
    while True:
        before = set(bound)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "aw_kit":
                bound |= {alias.asname or alias.name for alias in node.names if alias.name == "AW"}
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.For)):
                value = node.iter if isinstance(node, ast.For) else node.value
                targets = [node.target] if isinstance(node, (ast.AnnAssign, ast.For)) else node.targets
                if value is not None and _names_aw(value, bound):
                    bound |= {name.id for target in targets for name in ast.walk(target) if isinstance(name, ast.Name)}
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args = node.args
                positional = [*args.posonlyargs, *args.args]
                pairs = list(zip(positional[len(positional) - len(args.defaults) :], args.defaults, strict=True))
                pairs += [(arg, default) for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)]
                bound |= {arg.arg for arg, default in pairs if default is not None and _names_aw(default, bound)}
        if bound == before:
            return bound


def _runs_aw(source: str) -> list[int]:
    """The lines where source starts tools/aw other than through the kit: a call that starts a program, given
    anything that names it."""
    tree = ast.parse(source)
    bound = _bound(tree)
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (_callee(node) in STARTS or _callee(node).startswith(STARTS_LIKE))
        and _callee(node) not in KIT
        and any(_names_aw(arg, bound) for arg in [*node.args, *(keyword.value for keyword in node.keywords)])
    )


def test_no_test_runs_tools_aw_but_through_the_kit() -> None:
    # The kit's runs inherit a list of names and find the stand-ins first; a run of the test's own would inherit this
    # process's environment, the developer's ssh agent and keychain among it, and reach their real programs.
    files = [path for path in repo_files("tests", untracked=True) if path.suffix == ".py"]
    assert UNIT / "test_aw_layout.py" in files and UNIT / "aw_kit.py" in files, len(files)
    found = {
        str(path.relative_to(REPO)): lines
        for path in files
        if path.name != "aw_kit.py" and (lines := _runs_aw(path.read_text()))
    }
    assert found == {}, "run tools/aw through aw_kit's run_aw, aw_at_a_terminal or imports_of"


@pytest.mark.parametrize(
    ("source", "runs"),
    [
        ('subprocess.run([sys.executable, "-I", str(REPO / "tools" / "aw"), "help"])', True),
        ('AW = REPO / "tools" / "aw"\nsubprocess.run([sys.executable, str(AW), "status"])', True),
        ('def go(program=REPO / "tools" / "aw"):\n    return subprocess.Popen([sys.executable, program])', True),
        ('subprocess.check_output("python3 -I tools/aw deploy", shell=True)', True),
        ('os.execv(sys.executable, [sys.executable, "tools/aw"])', True),
        ('harness._at_a_pseudo_terminal([sys.executable, str(Path("tools", "aw"))], cwd, env)', True),
        ("from aw_kit import AW\nsubprocess.run([sys.executable, AW])", True),
        ("import aw_kit\nos.system(f'{sys.executable} {aw_kit.AW} help')", True),
        ('program = REPO / "tools/aw"\nwhere = program\nsubprocess.run([where])', True),
        ('for program in (AW_PATH, "tools/aw"):\n    subprocess.run([program])', True),
        ('runpy.run_path(str(REPO / "tools" / "aw"))', True),
        ('run_aw("--version", home=home, cwd=REPO, program="tools/aw")', False),
        ('aw_kit.aw_at_a_terminal("help", home=home)', False),
        ('AW = REPO / "tools" / "aw"\nassert AW in new', False),
        ('subprocess.run(["git", "ls-files", "tools"])', False),
        ("pytest.mark.parametrize('source', [\"COMMAND = ['tools/aw', 'deploy']\"])", False),
        ('subprocess.run(["ls", "tools/awful"])', False),
    ],
)
def test_the_scan_for_a_run_of_tools_aw_sees_each_way(source, runs) -> None:
    assert bool(_runs_aw(source)) is runs


# ---------------------------------------------------------------- the kit's runs ----


def test_run_aw_runs_the_program_and_says_what_it_said(home) -> None:
    ran = run_aw("--version", home=home)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, f"aw from the checkout {REPO}\n", "")
    # Isolated, by its absolute path, from the home.
    assert ran.args[:3] == [sys.executable, "-I", str(AW)]
    # Text on its standard input, which today's aw reads none of.
    assert run_aw("--version", home=home, stdin="typed\n").returncode == 0


def test_run_aw_never_reads_this_terminal_or_runs_another_program(home, tmp_path) -> None:
    with pytest.raises(AssertionError, match="never reads this terminal"):
        run_aw("--version", home=home, stdin=None)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "aw").write_text("print('another')\n")
    with pytest.raises(AssertionError, match="is not tools/aw"):
        run_aw("--version", home=home, cwd=tmp_path, program="tools/aw")


def test_a_run_inherits_the_names_listed_and_the_home_s_alone(home, monkeypatch) -> None:
    # What would point the program at the developer's own: their ssh agent, a token, a config, a module path.
    for name in ("SSH_AUTH_SOCK", "GH_TOKEN", "GIT_CONFIG_GLOBAL", "ANSIBLE_HOME", "PYTHONPATH", "CLAUDE_CONFIG_DIR"):
        monkeypatch.setenv(name, "/developer/own")
    env = environment(home)
    kit = {paths.VARIABLE, "HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "BROWSER"}
    assert [name for name in env if name not in PASSED_ON | kit and not name.startswith("LC_")] == []
    assert env[paths.VARIABLE] == env["HOME"] == str(home)
    assert all(Path(env[name]).is_relative_to(home) for name in kit - {"BROWSER"})


@pytest.mark.parametrize("tool", [*harness.DESKTOP_TOOLS, *harness.GUARDED_TOOLS, *harness.REFUSED_TOOLS])
def test_a_stand_in_is_first_on_path_and_a_run_that_reached_it_fails_its_test(home, tool) -> None:
    env = environment(home)
    found = shutil.which(tool, path=env["PATH"])
    assert found and Path(found).parent.parent == stand_ins(home), found
    reached = subprocess.run([found, "--asked"], env=env, stdin=subprocess.DEVNULL, capture_output=True, check=False)
    assert reached.returncode != 0
    with pytest.raises(pytest.fail.Exception, match=rf"(?m)^{re.escape(tool)} --asked$"):
        harness._fail_if_the_desktop_was_reached(stand_ins(home))
    # And the next run of the kit's fails the test for it.
    with pytest.raises(pytest.fail.Exception):
        run_aw("--version", home=home)


def test_the_browser_is_a_stand_in_too(home) -> None:
    assert environment(home)["BROWSER"] == str(stand_ins(home) / ".desktop" / "open")


def test_aw_at_a_terminal_runs_the_program_at_a_terminal_of_its_own(home) -> None:
    rc, output = aw_at_a_terminal("--version", home=home, timeout=60)
    assert rc == 0 and f"aw from the checkout {REPO}" in output.decode(), output


def test_imports_of_lists_what_a_run_loaded(home) -> None:
    loaded = imports_of("--version", home=home)
    assert ENTRY in loaded and "claude_vm.fleet" not in loaded
    assert not {name for name in loaded if name.split(".")[0] in ("ansible", "yaml", "asyncio", "subprocess")}


def test_imports_of_lists_what_importing_one_module_loaded(home) -> None:
    loaded = imports_of(module="claude_vm.fleet.paths", home=home)
    assert {"claude_vm", "claude_vm.fleet", "claude_vm.fleet.paths", "dataclasses"} <= loaded, sorted(loaded)
    assert ENTRY not in loaded and "claude_vm.secret" not in loaded


@pytest.mark.parametrize(
    ("module", "words"),
    [
        pytest.param("claude_vm.fleet; import os", (), id="code-after-the-name"),
        pytest.param("claude_vm..fleet", (), id="an-empty-part"),
        pytest.param("claude_vm.fleet\n", (), id="a-newline"),
        pytest.param("", (), id="empty"),
        pytest.param("claude_vm.fleet", ("--version",), id="words-beside-it"),
        pytest.param("claude_vm.no_such_module", (), id="an-import-that-fails"),
    ],
)
def test_imports_of_fails_a_module_it_cannot_list(home, module: str, words: tuple[str, ...]) -> None:
    with pytest.raises(pytest.fail.Exception):
        imports_of(*words, module=module, home=home)


# ---------------------------------------------------------------- the canary search ----


def test_the_search_finds_a_secret_in_a_file_or_a_folder(tmp_path) -> None:
    (tmp_path / "log").write_text(f"token={SECRET}\n")
    (tmp_path / "folder" / "deep").mkdir(parents=True)
    (tmp_path / "folder" / "deep" / "state.json").write_text('{"ok": true}\n')
    assert searched_for([SECRET], tmp_path / "log") == [(tmp_path / "log", SECRET)]
    assert searched_for([SECRET, b"other"], tmp_path / "folder") == []
    wrapped = Secret(SECRET)
    assert searched_for([wrapped], tmp_path) == [(tmp_path / "log", wrapped)]


def test_the_search_fails_when_it_searched_nothing(tmp_path) -> None:
    (tmp_path / "empty").write_text("")
    (tmp_path / "folder").mkdir()
    (tmp_path / "log").write_text("something\n")
    for secrets, where in (
        ([SECRET], [tmp_path / "missing"]),
        ([SECRET], [tmp_path / "log", tmp_path / "empty"]),
        ([SECRET], [tmp_path / "folder"]),
        ([], [tmp_path / "log"]),
        ([""], [tmp_path / "log"]),
        ([SECRET], []),
    ):
        with pytest.raises(pytest.fail.Exception):
            searched_for(secrets, *where)


# ---------------------------------------------------------------- Secret ----


def test_a_secret_is_never_said() -> None:
    secret = Secret(SECRET)
    shown = [
        repr(secret),
        str(secret),
        f"{secret}",
        f"{secret!r}",
        f"{secret!s}",
        f"{secret:>40}",
        "{}".format(secret),  # noqa: UP032 - format is one way a secret is shown
        "%s %r" % (secret, secret),  # noqa: UP031 - the old way is one way a secret is shown
        json.dumps({"token": secret}, default=str),
        repr([secret, {"token": secret}]),
    ]
    stream = io.StringIO()
    logger = logging.getLogger("aw-guards")
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    try:
        logger.warning("token %s", secret)
    finally:
        logger.removeHandler(handler)
    shown.append(stream.getvalue())
    assert [text for text in shown if SECRET in text] == []
    assert all(MARK in text for text in shown), shown


def test_a_secret_refuses_json_and_pickle_and_copies_as_itself() -> None:
    secret = Secret(SECRET)
    with pytest.raises(TypeError):
        json.dumps({"token": secret})
    for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
        with pytest.raises(TypeError):
            pickle.dumps(secret, protocol=protocol)
    assert copy.copy(secret) is secret and copy.deepcopy(secret) is secret

    @dataclasses.dataclass
    class Login:
        name: str
        token: Secret

    held = dataclasses.asdict(Login("dev", secret))
    assert held["token"] is secret
    with pytest.raises(TypeError):
        json.dumps(held)


def test_a_secret_holds_a_string_and_reveal_alone_reads_it() -> None:
    assert Secret(SECRET).reveal() == SECRET
    for value in (SECRET.encode(), 42, None, Secret(SECRET)):
        with pytest.raises(TypeError):
            Secret(value)
    assert not hasattr(Secret(SECRET), "__dict__")


def _reveals(source: str) -> list[int]:
    """The lines where source reads a Secret's value: a call of reveal, or reveal reached by its name (getattr)."""
    return sorted(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and (
            _callee(node) == "reveal"
            or (_callee(node) == "getattr" and any(getattr(arg, "value", None) == "reveal" for arg in node.args[1:2]))
        )
    )


def _product_python() -> list[Path]:
    """The product's Python: lib/ and tools/, a file without .py known by its first line."""
    found = []
    for path in repo_files("lib", "tools", untracked=True):
        if path.suffix == ".py" or (path.suffix == "" and path.read_bytes()[:64].startswith(b"#!/usr/bin/env python")):
            found.append(path)
    return found


def test_only_the_modules_reveals_names_read_a_secret_s_value() -> None:
    files = _product_python()
    assert AW in files and REPO / "lib" / "claude_vm" / "secret.py" in files, len(files)
    found = sorted(str(path.relative_to(REPO)) for path in files if _reveals(path.read_text()))
    assert found == sorted(REVEALS), "a module that sends a secret on is added to REVEALS, with why"
    assert all(reason.strip() for reason in REVEALS.values())


@pytest.mark.parametrize(
    ("source", "reads"),
    [
        ("token.reveal()", True),
        ("headers = {'Authorization': f'PVEAPIToken={secret.reveal()}'}", True),
        ("getattr(secret, 'reveal')()", True),
        ("def reveal(self):\n    return self.value", False),
        # A word of VS Code's tasks, which lib/claude_vm/editors/vscode.py writes.
        ("presentation = {'reveal': 'always'}", False),
        ("Secret(value)", False),
    ],
)
def test_the_scan_for_reveal_sees_each_way(source, reads) -> None:
    assert bool(_reveals(source)) is reads
