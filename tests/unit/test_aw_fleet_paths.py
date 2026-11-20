"""Where a fleet's files and its state are, and that no test reaches the developer's (lib/claude_vm/fleet/paths.py;
docs/architecture/engine.md §16.9).

A checkout drives a fleet of its own, in its `.aw/`, so a new worktree has none and changes nothing; the installed
program drives the one in the computer's config folder; AGENT_WAREHOUSE_HOME moves either, and every test sets it to a
directory of its own. The key that opens the vault is in neither the folder that travels nor the state folder a cleanup
may remove, and only two modules name it. Nothing here touches the developer's folders: every home is a directory the
test made, and the session's guard (tests/unit/conftest.py) fails the run if one of the developer's changed.
"""

from __future__ import annotations

import ast
import fnmatch
import inspect
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import conftest
import harness
import live_kit
import pytest
import yaml
from harness import GIT_ENV, PASSED_ON, repo_files, repo_texts

from claude_vm import route
from claude_vm.fleet import paths

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "tests" / "check_no_local_files.sh"
VARIABLE = paths.VARIABLE

# The one module that may name the fleet's folders, and the modules that may name the key file: no cleanup, prune or
# sweep elsewhere can then reach the key by its name (docs/architecture/engine.md §16.9).
NAME_THE_FOLDERS = ("lib/claude_vm/fleet/paths.py",)
NAME_THE_KEY = ("lib/claude_vm/fleet/paths.py", "lib/claude_vm/fleet/vaultkey.py")
FOLDERS = re.compile(
    r"XDG_CONFIG_HOME|XDG_STATE_HOME|AGENT_WAREHOUSE_HOME"
    r"|\.config\W{1,6}agent-warehouse(?![\w-])|\.local\W{1,6}state\W{1,6}agent-warehouse(?![\w-])"
)
# The code that runs on a machine, the one place the folder there is named: paths.ON_A_MACHINE, or its literal,
# followed by a name RESERVED_ON_A_MACHINE reserves, and nothing else.
GUEST = "lib/claude_vm/guest/"
ON_A_MACHINE_NAMED = re.compile(re.escape(paths.ON_A_MACHINE) + r"/(?P<name>[\w.-]+)(?![\w./{$*-])")
# The key file's name, which --vault-key-file is not; the fleet's own package, the one place KEY and OWN are named and
# a removal may reach the key; and the ways a removal is called.
KEY_NAMED = re.compile(r"\bvault-key\b(?!-)")
FLEET = "lib/claude_vm/fleet/"
REMOVES = frozenset({"unlink", "remove", "rmtree", "rmdir"})
# Each module outside fleet/ that reads key_file, with why. A card that writes one adds it here in the same commit
# (E4's engine/init.py, G8's engine/controller_writes.py, K4's migration, and D5 where its reader is outside fleet/).
READ_THE_KEY: dict[str, str] = {}


def _checkout(where: Path, *, worktree: bool = False) -> Path:
    """A program root that is a checkout: a justfile and a .git, a folder or, as in a worktree, a file."""
    where.mkdir(parents=True)
    (where / "justfile").write_text("")
    if worktree:
        (where / ".git").write_text("gitdir: /elsewhere\n")
    else:
        (where / ".git").mkdir()
    return where


def _release(where: Path) -> Path:
    """A program root that is an installed release: a clone of its tag, with release.json."""
    _checkout(where)
    (where / "release.json").write_text("{}\n")
    return where


def _environ(tmp_path: Path, **more: str) -> dict:
    """A computer's environment with its home in the test's own directory, and no AGENT_WAREHOUSE_HOME."""
    return {"HOME": str(tmp_path / "user"), **more}


def _installed(tmp_path: Path) -> tuple[Path, Path, Path]:
    user = tmp_path / "user"
    return (
        user / ".config/agent-warehouse",
        user / ".local/state/agent-warehouse",
        user / ".local/share/agent-warehouse",
    )


def _tree(top: Path) -> list[str]:
    return sorted(str(path.relative_to(top)) for path in top.rglob("*"))


# ---------------------------------------------------------------- the three homes ----


@pytest.mark.parametrize("worktree", [False, True])
def test_a_checkout_drives_its_own_aw_folder(tmp_path, worktree) -> None:
    program = _checkout(tmp_path / "checkout", worktree=worktree)
    where = paths.home(program, _environ(tmp_path))
    assert (where.root, where.how, where.pointer) == (program / ".aw", "checkout", None)
    assert paths.state(where) == program / ".aw" / "state"
    assert paths.key_file(where) == program / ".aw" / "vault-key"
    assert paths.client_file(where) == program / ".aw" / "client.yml"
    assert paths.fleet_dir(where) == program / ".aw" / "fleet"


def test_the_installed_program_drives_the_config_folder_and_keeps_state_and_key_apart(tmp_path) -> None:
    config, state, data = _installed(tmp_path)
    where = paths.home(_release(tmp_path / "release"), _environ(tmp_path))
    assert (where.root, where.how) == (config, "installed")
    assert (paths.state(where), paths.key_file(where)) == (state, data / "vault-key")
    assert paths.fleet_dir(where) == config / "fleet"


def test_the_installed_program_follows_the_xdg_folders_and_ignores_a_relative_one(tmp_path) -> None:
    xdg = {name: str(tmp_path / name.lower()) for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")}
    where = paths.home(_release(tmp_path / "release"), _environ(tmp_path, **xdg))
    assert where.root == tmp_path / "xdg_config_home" / "agent-warehouse"
    assert where.state == tmp_path / "xdg_state_home" / "agent-warehouse"
    assert where.key_file == tmp_path / "xdg_data_home" / "agent-warehouse" / "vault-key"
    # The XDG specification: a relative or empty value is ignored, and the default under the home is used.
    relative = {name: "relative/folder" for name in xdg} | {"XDG_DATA_HOME": ""}
    assert paths.installed(_environ(tmp_path, **relative)) == _installed(tmp_path)


def test_a_release_is_told_by_release_json_before_its_git(tmp_path) -> None:
    # A release is a clone of its tag: a justfile and a .git, which alone would make it a checkout.
    assert paths.kind(_release(tmp_path / "release")) == "installed"
    assert paths.kind(_checkout(tmp_path / "checkout")) == "checkout"


@pytest.mark.parametrize("program", ["checkout", "release"])
def test_the_variable_outranks_both_and_moves_the_state_and_the_key_with_it(tmp_path, program) -> None:
    root = (_checkout if program == "checkout" else _release)(tmp_path / program)
    # A pointer in the default home's client.yml is not read under the variable.
    default = root / ".aw" if program == "checkout" else _installed(tmp_path)[0]
    default.mkdir(parents=True)
    (default / "client.yml").write_text(f"fleet_home: {tmp_path / 'elsewhere'}\n")
    where = paths.home(root, _environ(tmp_path, **{VARIABLE: str(tmp_path / "fleet-home")}))
    assert (where.root, where.how, where.pointer) == (tmp_path / "fleet-home", "variable", None)
    assert (where.state, where.key_file) == (tmp_path / "fleet-home/state", tmp_path / "fleet-home/vault-key")


def test_the_variable_naming_the_installed_home_keeps_its_state_and_key(tmp_path) -> None:
    # One fleet, one table of jobs and locks, one key: never a key in the config folder, which people publish.
    config, state, data = _installed(tmp_path)
    where = paths.home(_checkout(tmp_path / "checkout"), _environ(tmp_path, **{VARIABLE: str(config) + "/"}))
    assert (where.root, where.state, where.key_file) == (config, state, data / "vault-key")


def test_the_variable_takes_a_home_folder_and_refuses_a_relative_one(tmp_path) -> None:
    program = _checkout(tmp_path / "checkout")
    assert paths.home(program, _environ(tmp_path, **{VARIABLE: "~/fleet"})).root == tmp_path / "user" / "fleet"
    with pytest.raises(paths.Refused, match="AGENT_WAREHOUSE_HOME is 'fleet', which is not an absolute path"):
        paths.home(program, _environ(tmp_path, **{VARIABLE: "fleet"}))


def test_a_program_that_is_neither_is_refused_with_the_way_out(tmp_path) -> None:
    # A copy with no .git is not a checkout, and never drives the installed program's fleet by default.
    (tmp_path / "copy").mkdir()
    (tmp_path / "copy" / "justfile").write_text("")
    with pytest.raises(paths.Refused, match=f"neither a checkout .* nor an installed release .* set {VARIABLE}"):
        paths.home(tmp_path / "copy", _environ(tmp_path))


def test_a_relative_program_root_is_made_absolute(tmp_path, monkeypatch) -> None:
    program = _checkout(tmp_path / "checkout")
    monkeypatch.chdir(program)
    where = paths.home(".", _environ(tmp_path))
    assert where.root == program / ".aw" and where.root.is_absolute() and paths.kind(".") == "checkout"
    # It stays where it was worked out, whatever the process's folder is later.
    monkeypatch.chdir(tmp_path)
    assert paths.home("checkout", _environ(tmp_path)).root == where.root


def test_no_home_and_no_account_entry_is_refused_not_a_traceback(monkeypatch) -> None:
    # A container run as a uid the password database lacks, with HOME unset or relative.
    import pwd

    def unknown(uid: int):
        raise KeyError(f"getpwuid(): uid not found: {uid}")

    monkeypatch.setattr(pwd, "getpwuid", unknown)
    for environ in ({}, {"HOME": "relative/home"}):
        with pytest.raises(paths.Refused, match="not an absolute path, and this account .* set HOME"):
            paths.installed(environ)


def test_the_installed_home_spelled_through_a_link_keeps_its_state_and_key(tmp_path) -> None:
    # ~/.config is a link into a dotfiles folder, as dotfiles managers make it: the installed program's home spelled
    # by its real path, or a link to it, is still that home, never a fleet whose key sits in the published folder.
    config, state, data = _installed(tmp_path)
    dotfiles = tmp_path / "dotfiles" / "config"
    dotfiles.mkdir(parents=True)
    (tmp_path / "user").mkdir()
    (tmp_path / "user" / ".config").symlink_to(dotfiles)
    (tmp_path / "linked").symlink_to(tmp_path / "user" / ".config")
    program = _checkout(tmp_path / "checkout")
    for spelled in (dotfiles / "agent-warehouse", tmp_path / "linked" / "agent-warehouse"):
        where = paths.home(program, _environ(tmp_path, **{VARIABLE: str(spelled)}))
        assert (where.root, where.state, where.key_file) == (spelled, state, data / "vault-key")
        (program / ".aw").mkdir(exist_ok=True)
        (program / ".aw" / "client.yml").write_text(f"fleet_home: {spelled}\n")
        where = paths.home(program, _environ(tmp_path))
        assert (where.root, where.state, where.key_file) == (spelled, state, data / "vault-key")
    # The run's XDG_CONFIG_HOME spelled otherwise than the shell's that wrote the pointer.
    environ = _environ(tmp_path, XDG_CONFIG_HOME=str(dotfiles))
    (program / ".aw" / "client.yml").write_text(f"fleet_home: {config}\n")
    assert (paths.home(program, environ).state, paths.home(program, environ).key_file) == (state, data / "vault-key")


def test_a_home_inside_the_installed_state_folder_is_refused(tmp_path) -> None:
    # A cleanup may remove the state folder: a fleet's files and its key are never kept there (O21).
    config, state, data = _installed(tmp_path)
    program = _checkout(tmp_path / "checkout")
    for inside in (state, state / "second", tmp_path / "user/.local/state/../state/agent-warehouse/third"):
        with pytest.raises(paths.Refused, match=f"{VARIABLE} is .*, inside .*, the installed program's state folder"):
            paths.home(program, _environ(tmp_path, **{VARIABLE: str(inside)}))
    # Through a link, and by fleet_home, too.
    state.mkdir(parents=True)
    (tmp_path / "linked-state").symlink_to(state)
    with pytest.raises(paths.Refused, match=f"{VARIABLE} is .*linked-state/second, inside"):
        paths.home(program, _environ(tmp_path, **{VARIABLE: str(tmp_path / "linked-state" / "second")}))
    (program / ".aw").mkdir()
    (program / ".aw" / "client.yml").write_text(f"fleet_home: {state / 'second'}\n")
    with pytest.raises(paths.Refused, match="client.yml's fleet_home is .*, inside .* state folder"):
        paths.home(program, _environ(tmp_path))
    # A folder beside it is not inside it.
    beside = state.parent / "agent-warehouse-work"
    assert paths.home(program, _environ(tmp_path, **{VARIABLE: str(beside)})).key_file == beside / "vault-key"


def test_a_named_home_keeps_its_key_at_its_top(tmp_path) -> None:
    # V2-2: a person who names a home chooses where its key lives, a second fleet's beside the config folder included.
    work = tmp_path / "user" / ".config" / "aw-work"
    where = paths.home(_checkout(tmp_path / "checkout"), _environ(tmp_path, **{VARIABLE: "~/.config/aw-work"}))
    assert (where.root, where.state, where.key_file) == (work, work / "state", work / "vault-key")


# ---------------------------------------------------------------- fleet_home ----


def test_fleet_home_points_a_checkout_at_the_installed_home_once(tmp_path) -> None:
    config, state, data = _installed(tmp_path)
    program = _checkout(tmp_path / "checkout")
    (program / ".aw").mkdir()
    (program / ".aw" / "client.yml").write_text(f"editor: zed\nfleet_home: {config}\n")
    where = paths.home(program, _environ(tmp_path))
    assert (where.root, where.state, where.key_file) == (config, state, data / "vault-key")
    assert (where.how, where.pointer) == ("checkout", program / ".aw" / "client.yml")


def test_fleet_home_points_the_installed_program_at_a_checkout(tmp_path) -> None:
    config = _installed(tmp_path)[0]
    program = _checkout(tmp_path / "checkout")
    config.mkdir(parents=True)
    (config / "client.yml").write_text(f"fleet_home: '{program / '.aw'}'  # the checkout's\n")
    where = paths.home(_release(tmp_path / "release"), _environ(tmp_path))
    assert (where.root, where.state, where.key_file) == (
        program / ".aw",
        program / ".aw/state",
        program / ".aw/vault-key",
    )
    assert (where.how, where.pointer) == ("installed", config / "client.yml")


def test_fleet_home_is_never_followed_through_a_second_pointer(tmp_path) -> None:
    config = _installed(tmp_path)[0]
    program = _checkout(tmp_path / "checkout")
    (program / ".aw").mkdir()
    (program / ".aw" / "client.yml").write_text(f"fleet_home: {config}\n")
    config.mkdir(parents=True)
    (config / "client.yml").write_text(f"fleet_home: {tmp_path / 'third'}\n")
    with pytest.raises(paths.Refused, match="points again: fleet_home is followed once"):
        paths.home(program, _environ(tmp_path))


@pytest.mark.parametrize(
    ("text", "followed"),
    [
        ("fleet_home: /fleets/one", "/fleets/one"),
        ('fleet_home: "/fleets/one two"', "/fleets/one two"),
        ("fleet_home: /fleets/one   # the shared one", "/fleets/one"),
        ("fleet_home: /fleets/one#two", "/fleets/one#two"),
        ("fleet_home: ~/fleets/one", "~/fleets/one"),
        ("fleet_home:", None),
        ("fleet_home: ~", None),
        ("fleet_home: null", None),
        ("fleet_home:   # none yet", None),
        ("hosts:\n  fleet_home: /nested/under/another/key", None),
        ("# fleet_home: /a/comment", None),
        ("fleet_home:/not/a/key", None),
    ],
)
def test_fleet_home_reads_one_line_as_yaml_does(tmp_path, text, followed) -> None:
    program = _checkout(tmp_path / "checkout")
    (program / ".aw").mkdir()
    (program / ".aw" / "client.yml").write_text(f"editor: vscode\n{text}\n")
    environ = _environ(tmp_path)
    expected = Path(followed.replace("~", environ["HOME"], 1)) if followed else program / ".aw"
    assert paths.home(program, environ).root == expected
    # A YAML reader agrees, where the file is YAML at all (a key needs a space after its colon).
    try:
        read = yaml.safe_load((program / ".aw" / "client.yml").read_text()).get("fleet_home")
    except yaml.YAMLError:
        assert text == "fleet_home:/not/a/key"
        return
    assert (read if read is None else read.replace("~", environ["HOME"], 1)) == (str(expected) if followed else None)


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("fleet_home: /a\nfleet_home: /b\n", "says fleet_home more than once"),
        ("fleet_home: [/a, /b]\n", "is not one plain path"),
        ("fleet_home: *anchor\n", "is not one plain path"),
        ('fleet_home: "/a\\"b"\n', "is not one plain path"),
        ("fleet_home: fleets/one\n", "which is not an absolute path"),
        ("fleet_home: ''\n", "which is not an absolute path"),
    ],
)
def test_fleet_home_refuses_what_is_not_one_absolute_path(tmp_path, text, said) -> None:
    program = _checkout(tmp_path / "checkout")
    (program / ".aw").mkdir()
    (program / ".aw" / "client.yml").write_text(text)
    with pytest.raises(paths.Refused, match=re.escape(said)):
        paths.home(program, _environ(tmp_path))


def test_fleet_home_naming_its_own_home_is_no_pointer(tmp_path) -> None:
    program = _checkout(tmp_path / "checkout")
    (program / ".aw").mkdir()
    (program / ".aw" / "client.yml").write_text(f"fleet_home: {program / '.aw'}/\n")
    where = paths.home(program, _environ(tmp_path))
    assert (where.root, where.pointer) == (program / ".aw", None)


# ---------------------------------------------------------------- reads only ----


def test_a_new_worktree_has_no_folder_and_a_read_makes_none(tmp_path) -> None:
    program = _checkout(tmp_path / "checkout", worktree=True)
    before = _tree(tmp_path)
    for environ in (_environ(tmp_path), _environ(tmp_path, **{VARIABLE: str(tmp_path / "moved")})):
        where = paths.home(program, environ)
        for function in (
            paths.state,
            paths.key_file,
            paths.fleet_dir,
            paths.client_file,
            paths.jobs_dir,
            paths.locks_dir,
            paths.logs_dir,
            paths.status_cache,
        ):
            function(where)
        paths.installed(environ)
        paths.control_dir(environ)
    assert _tree(tmp_path) == before
    assert not (program / ".aw").exists()


# ---------------------------------------------------------------- the key file ----


def _homes(tmp_path: Path) -> dict[str, paths.Home]:
    """Every way to a home: a checkout's, the variable's, the installed program's, and each fleet_home pointer."""
    config = _installed(tmp_path)[0]
    checkout, pointing = _checkout(tmp_path / "checkout"), _checkout(tmp_path / "pointing")
    (pointing / ".aw").mkdir()
    (pointing / ".aw" / "client.yml").write_text(f"fleet_home: {config}\n")
    release = _release(tmp_path / "release")
    environ = _environ(tmp_path)
    homes = {
        "checkout": paths.home(checkout, environ),
        "variable": paths.home(checkout, _environ(tmp_path, **{VARIABLE: str(tmp_path / "moved")})),
        "installed": paths.home(release, environ),
        "checkout to installed": paths.home(pointing, environ),
    }
    config.mkdir(parents=True)
    (config / "client.yml").write_text(f"fleet_home: {checkout / '.aw'}\n")
    homes["installed to checkout"] = paths.home(release, environ)
    return homes


def test_the_key_file_is_at_the_top_of_the_home_or_in_the_data_folder(tmp_path) -> None:
    homes = _homes(tmp_path)
    data = _installed(tmp_path)[2]
    assert {name: paths.key_file(where) for name, where in homes.items()} == {
        "checkout": tmp_path / "checkout/.aw/vault-key",
        "variable": tmp_path / "moved/vault-key",
        "installed": data / "vault-key",
        "checkout to installed": data / "vault-key",
        "installed to checkout": tmp_path / "checkout/.aw/vault-key",
    }


def test_no_folder_that_travels_or_that_a_cleanup_may_remove_holds_the_key(tmp_path) -> None:
    for name, where in _homes(tmp_path).items():
        key = paths.key_file(where)
        cleaned = [paths.fleet_dir(where), paths.state(where)] + [
            function(where) for function in (paths.jobs_dir, paths.locks_dir, paths.logs_dir, paths.status_cache)
        ]
        assert [folder for folder in cleaned if key.is_relative_to(folder)] == [], name
        assert not paths.fleet_dir(where).is_relative_to(paths.state(where)), name


def test_the_names_beneath_the_state_folder(tmp_path) -> None:
    for where in _homes(tmp_path).values():
        state = paths.state(where)
        names = (paths.jobs_dir, paths.locks_dir, paths.logs_dir, paths.status_cache)
        assert [function(where) for function in names] == [state / name for name in paths.BENEATH_STATE]


def test_two_fleets_share_no_state_folder(tmp_path) -> None:
    environ = _environ(tmp_path)
    one, two = (paths.home(_checkout(tmp_path / name), environ) for name in ("one", "two"))
    assert paths.locks_dir(one) != paths.locks_dir(two) and paths.jobs_dir(one) != paths.jobs_dir(two)


# ---------------------------------------------------------------- the control directory ----


def test_the_control_directory_is_the_hops_own_and_every_fleets(tmp_path) -> None:
    environ = _environ(tmp_path)
    default = inspect.signature(route.hop_settings).parameters["control_dir"].default
    assert paths.control_dir(environ) == Path(default.replace("~", environ["HOME"], 1))
    # The same for every fleet: it takes no home.
    assert list(inspect.signature(paths.control_dir).parameters) == ["environ"]


# ---------------------------------------------------------------- the names ----


def test_own_and_the_names_reserved_on_a_machine_never_meet() -> None:
    own = (*paths.OWN, *paths.BENEATH_STATE)
    assert len(set(own)) == len(own) and not set(own) & set(paths.RESERVED_ON_A_MACHINE)
    for name in own:
        for reserved in paths.RESERVED_ON_A_MACHINE:
            assert not fnmatch.fnmatchcase(name, reserved), (name, reserved)
            assert not fnmatch.fnmatchcase(reserved, name), (name, reserved)


def test_every_file_a_role_writes_into_the_folder_on_a_machine_is_reserved() -> None:
    # The installed program's home is ~/.config/agent-warehouse/, where today's roles keep a sign-in's files on every
    # machine the product configured: a new one there is reserved, or a later installed program could take it.
    written = {}
    for role, folder in (("claude_code", "claude_code_login_dir"), ("github_projects", "github_projects_state_dir")):
        values = yaml.safe_load((REPO / "roles" / role / "vars" / "main.yml").read_text())
        assert values[folder].endswith(paths.ON_A_MACHINE.removeprefix("~")), (role, values[folder])
        written[role] = {
            name
            for _, text in repo_texts("roles", "playbooks")
            for name in re.findall(rf"{{{{ {folder} }}}}/([^\"'\s]+)", text)
        }
    assert written == {
        "claude_code": {f"claude-login.{end}" for end in ("out", "in", "pid", "run", "browser", "page", "forward.pid")},
        "github_projects": {"gh-login", "gh-login.out", "gh-login.pid", "gh-device-login"},
    }
    unreserved = [
        name
        for names in written.values()
        for name in names
        if not any(fnmatch.fnmatchcase(name, reserved) for reserved in paths.RESERVED_ON_A_MACHINE)
    ]
    assert unreserved == [], "reserve each in paths.RESERVED_ON_A_MACHINE"


# ---------------------------------------------------------------- who names them ----


def _engine_texts() -> list[tuple[str, str]]:
    """lib/claude_vm/ and tools/aw*, each file's text, new files included."""
    found = []
    for path in repo_files("lib/claude_vm", "tools", untracked=True):
        rel = str(path.relative_to(REPO))
        if rel.startswith("lib/claude_vm/") or rel.startswith("tools/aw"):
            try:
                found.append((rel, path.read_text(encoding="utf-8")))
            except UnicodeDecodeError:
                continue
    return found


def _reserved(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, reserved) for reserved in paths.RESERVED_ON_A_MACHINE)


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    return {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _text(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _joined_to(node: ast.AST, up: dict[int, ast.AST]) -> str | None:
    """The one name joined to the folder node stands for, in the four ways to join one: folder + "/name",
    f"{folder}/name", PurePosixPath(folder) / "name", or a join(folder, "name"); None for any other use."""
    parent = up.get(id(node))
    if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Add) and parent.left is node:
        joined = _text(parent.right)
        return joined[1:] if joined and joined.startswith("/") else None
    if isinstance(parent, ast.FormattedValue) and parent.conversion == -1 and parent.format_spec is None:
        whole = up.get(id(parent))
        if isinstance(whole, ast.JoinedStr) and len(whole.values) == 2 and whole.values[0] is parent:
            joined = _text(whole.values[1])
            return joined[1:] if joined and joined.startswith("/") else None
        return None
    if isinstance(parent, ast.Call) and parent.args and parent.args[0] is node and not parent.keywords:
        called = parent.func.attr if isinstance(parent.func, ast.Attribute) else getattr(parent.func, "id", None)
        if called == "join" and len(parent.args) == 2:
            return _text(parent.args[1])
        divided = up.get(id(parent))
        if len(parent.args) == 1 and isinstance(divided, ast.BinOp) and isinstance(divided.op, ast.Div):
            return _text(divided.right) if divided.left is parent else None
    return None


def _on_a_machine_misused(text: str) -> list[int]:
    """The lines where paths.ON_A_MACHINE is used other than joined to one reserved name, by Python's syntax tree;
    in a file that is not Python, every line that names it."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [number for number, line in enumerate(text.splitlines(), 1) if "ON_A_MACHINE" in line]
    up = _parents(tree)
    names = {"ON_A_MACHINE"} | {
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "ON_A_MACHINE" and alias.asname
    }
    lines = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and node.id in names) or (
            isinstance(node, ast.Attribute) and node.attr == "ON_A_MACHINE"
        ):
            joined = _joined_to(node, up)
            if joined is None or "/" in joined or not _reserved(joined):
                lines.append(node.lineno)
    return lines


def _folder_problems(rel: str, text: str) -> list[str]:
    """What a file of the new engine names of the fleet's folders, where only paths.py names them, and of the folder
    on a machine other than as guest/ may."""
    if rel in NAME_THE_FOLDERS:
        return []
    problems, rest = [], text
    if rel.startswith(GUEST):
        rest = ON_A_MACHINE_NAMED.sub(lambda found: "" if _reserved(found["name"]) else found[0], text)
        problems += [f"line {line}: ON_A_MACHINE not before one reserved name" for line in _on_a_machine_misused(text)]
    elif re.search(r"\bON_A_MACHINE\b", text):
        problems.append("ON_A_MACHINE outside guest/")
    if FOLDERS.search(rest):
        problems.append(f"names {FOLDERS.search(rest)[0]!r}")
    return problems


def _key_problems(rel: str, text: str, readers: Mapping[str, str] = READ_THE_KEY) -> set[str]:
    """What a file of the new engine does that could reach the key file: "the name", which only paths.py and
    vaultkey.py say; and outside fleet/, "KEY or OWN" named, "a removal" that reaches key_file, KEY or OWN, and "a
    read" of key_file by a module readers does not list."""
    found = set() if rel in NAME_THE_KEY or not KEY_NAMED.search(text) else {"the name"}
    if rel.startswith(FLEET):
        return found
    try:
        tree = ast.parse(text)
    except SyntaxError:
        found |= {"KEY or OWN"} if re.search(r"\bpaths\.(?:KEY|OWN)\b", text) else set()
        found |= {"a read"} if re.search(r"\bkey_file\b", text) and rel not in readers else set()
        return found
    # The names the paths module, its KEY and OWN, and its key_file are known by here, as imported.
    modules, constants, readings = {"paths"}, set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.asname for alias in node.names if alias.asname and alias.name.endswith("paths")}
        elif isinstance(node, ast.ImportFrom):
            from_paths = (node.module or "").endswith("paths")
            for alias in node.names:
                if alias.name == "paths":
                    modules.add(alias.asname or alias.name)
                elif from_paths and alias.name in ("KEY", "OWN"):
                    constants.add(alias.asname or alias.name)
                    found.add("KEY or OWN")
                elif from_paths and alias.name == "key_file":
                    readings.add(alias.asname or alias.name)
                    found.add("a read")

    def direct(node: ast.AST) -> str | None:
        """ "KEY or OWN" for the paths module's, "a read" for a key_file (a Home's, the paths module's, a call of
        one), each also by the name it was imported as; None for anything else."""
        if isinstance(node, ast.Attribute) and node.attr == "key_file":
            return "a read"
        if isinstance(node, ast.Attribute) and node.attr in ("KEY", "OWN"):
            value = node.value
            if (isinstance(value, ast.Name) and value.id in modules) or (
                isinstance(value, ast.Attribute) and value.attr == "paths"
            ):
                return "KEY or OWN"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "key_file":
            return "a read"
        if isinstance(node, ast.Name):
            return "KEY or OWN" if node.id in constants else "a read" if node.id in readings else None
        return None

    found |= {kind for node in ast.walk(tree) if (kind := direct(node))}
    # What reaches the key: those, and every name bound from them, through an assignment, a loop or a with.
    tainted: set[str] = set()

    def reaches(node: ast.AST | None) -> bool:
        return node is not None and any(
            direct(part) or (isinstance(part, ast.Name) and part.id in tainted) for part in ast.walk(node)
        )

    bindings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            bindings += [(target, node.value) for target in node.targets]
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            bindings.append((node.target, node.value))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            bindings.append((node.target, node.iter))
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            bindings.append((node.optional_vars, node.context_expr))
    while True:
        more = {
            part.id
            for target, value in bindings
            if reaches(value)
            for part in ast.walk(target)
            if isinstance(part, ast.Name) and part.id not in tainted
        }
        if not more:
            break
        tainted |= more
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
            given = [*node.args, *(keyword.value for keyword in node.keywords), receiver]
            if called in REMOVES and any(reaches(part) for part in given):
                found.add("a removal")
    if "a read" in found and rel in readers:
        found.discard("a read")
    return found


def test_only_paths_names_the_fleets_folders() -> None:
    texts = _engine_texts()
    assert len(texts) > 80 and "tools/aw" in dict(texts), len(texts)
    assert FOLDERS.search(dict(texts)[NAME_THE_FOLDERS[0]]), "the scan finds the folders where they are named"
    found = {rel: problems for rel, text in texts if (problems := _folder_problems(rel, text))}
    assert found == {}, "ask fleet/paths.py where they are; on a machine, paths.ON_A_MACHINE + a reserved name"


def test_only_paths_and_vaultkey_name_the_key_file_and_nothing_outside_fleet_removes_it() -> None:
    texts = dict(_engine_texts())
    assert KEY_NAMED.search(texts[NAME_THE_KEY[0]]), "the scan finds the key's name where it is named"
    found = {rel: problems for rel, text in texts.items() if (problems := _key_problems(rel, text))}
    assert found == {}, "the key file is reached through fleet/ alone, or by a reader READ_THE_KEY lists"
    # Each reader listed is outside fleet/, exists, and reads the key: the list holds no stale line.
    assert {rel: _key_problems(rel, texts.get(rel, ""), readers={}) for rel in READ_THE_KEY} == {
        rel: {"a read"} for rel in READ_THE_KEY
    }
    assert all(reason.strip() for reason in READ_THE_KEY.values())


@pytest.mark.parametrize(
    ("source", "names"),
    [
        ("os.environ.get('XDG_CONFIG_HOME')", True),
        ("os.environ['XDG_STATE_HOME']", True),
        ("HOME = os.environ['AGENT_WAREHOUSE_HOME']", True),
        ("Path.home() / '.config' / 'agent-warehouse'", True),
        ("'~/.config/agent-warehouse/client.yml'", True),
        ("Path('.local', 'state', 'agent-warehouse')", True),
        ("os.environ['XDG_DATA_HOME']", False),
        ("'agent-warehouse-' + uuid", False),
        ("'.config/agent-warehouse-staging'", False),
    ],
)
def test_the_scan_for_the_folders_sees_each_way_to_name_one(source, names) -> None:
    assert bool(FOLDERS.search(source)) is names
    assert bool(_folder_problems("lib/claude_vm/engine/deploy.py", source)) is names


ON_A_MACHINE_CASES = [
    # paths.ON_A_MACHINE joined to a reserved name, each way to join one, or its literal so followed.
    ('MARKER = paths.ON_A_MACHINE + "/gh-login"', True),
    ('PAGE = f"{paths.ON_A_MACHINE}/claude-login.page"', True),
    ('PID = PurePosixPath(paths.ON_A_MACHINE) / "claude-login.pid"', True),
    ('CODE = posixpath.join(paths.ON_A_MACHINE, "gh-device-login")', True),
    ('from claude_vm.fleet.paths import ON_A_MACHINE\nOUT = ON_A_MACHINE + "/gh-login.out"', True),
    ('MARKER = "~/.config/agent-warehouse/gh-login"', True),
    ('"""The marker, `~/.config/agent-warehouse/gh-login`, holds the digest."""', True),
    # Anything else: a name of the fleet's, a name made at run time, the folder alone, a way out of it.
    ('MARKER = paths.ON_A_MACHINE + "/elsewhere"', False),
    ('SETTINGS = paths.ON_A_MACHINE + "/client.yml"', False),
    ('PAGE = f"{paths.ON_A_MACHINE}/{name}"', False),
    ("FOLDER = paths.ON_A_MACHINE", False),
    ('MARKER = paths.ON_A_MACHINE + "/gh-login/../client.yml"', False),
    ('from claude_vm.fleet.paths import ON_A_MACHINE as THERE\nOUT = THERE + "/vault-key"', False),
    ('MARKER = "~/.config/agent-warehouse/client.yml"', False),
    ("MARKER = f'~/.config/agent-warehouse/{name}'", False),
    ('MARKER = "~/.config/agent-warehouse/claude-login.out/x"', False),
    ("os.environ['XDG_CONFIG_HOME']", False),
]


@pytest.mark.parametrize(("source", "allowed"), ON_A_MACHINE_CASES)
def test_guest_names_the_folder_on_a_machine_only_before_a_reserved_name(source, allowed) -> None:
    assert (_folder_problems("lib/claude_vm/guest/layer2/github.py", source) == []) is allowed
    # Outside guest/, never: the computer's folders are paths.py's to say.
    assert _folder_problems("lib/claude_vm/engine/status.py", source) != []


@pytest.mark.parametrize(
    ("rel", "source", "found"),
    [
        ("lib/claude_vm/engine/init.py", "paths.key_file(where)", {"a read"}),
        ("lib/claude_vm/engine/init.py", "from claude_vm.fleet.paths import key_file", {"a read"}),
        ("lib/claude_vm/engine/init.py", "key = where.key_file", {"a read"}),
        ("lib/claude_vm/engine/init.py", "key_file(where)", {"a read"}),
        ("lib/claude_vm/engine/init.py", "ssh_key_file = '~/.ssh/id_ed25519'", set()),
        ("lib/claude_vm/engine/init.py", "'ansible_ssh_private_key_file'", set()),
        # I6's option, and D4's setting, are not the file's name.
        ("lib/claude_vm/mcp/register.py", 'parser.add_argument("--vault-key-file")', set()),
        ("lib/claude_vm/schema/words.py", "'vault_key_file'", set()),
        ("lib/claude_vm/fleet/overrides.py", "KEY = 'vault-key'", {"the name"}),
        ("lib/claude_vm/runs/sweep.py", "where.root / 'vault-key'", {"the name"}),
        # The forms a cleanup would use.
        ("lib/claude_vm/runs/sweep.py", "shutil.rmtree(where.root / paths.KEY)", {"KEY or OWN", "a removal"}),
        (
            "lib/claude_vm/runs/sweep.py",
            "for name in paths.OWN:\n    shutil.rmtree(where.root / name)",
            {"KEY or OWN", "a removal"},
        ),
        (
            "lib/claude_vm/runs/sweep.py",
            "from claude_vm.fleet import paths as p\nfor name in p.OWN:\n    (where.root / name).unlink()",
            {"KEY or OWN", "a removal"},
        ),
        (
            "lib/claude_vm/runs/sweep.py",
            "from claude_vm.fleet.paths import KEY\nos.remove(KEY)",
            {"KEY or OWN", "a removal"},
        ),
        ("lib/claude_vm/runs/prune.py", "where.key_file.unlink(missing_ok=True)", {"a read", "a removal"}),
        ("lib/claude_vm/runs/prune.py", "key = paths.key_file(where)\nos.remove(path=key)", {"a read", "a removal"}),
        ("lib/claude_vm/runs/prune.py", "shutil.rmtree(paths.logs_dir(where))", set()),
        ("lib/claude_vm/runs/prune.py", "for name in paths.BENEATH_STATE:\n    print(name)", set()),
        # The fleet's own package reads and writes its key.
        ("lib/claude_vm/fleet/overrides.py", "default = paths.key_file(where)", set()),
        ("lib/claude_vm/fleet/vaultkey.py", "KEY = paths.KEY\nos.replace(new, where.root / KEY)", set()),
    ],
)
def test_the_scan_for_the_key_sees_each_way_to_reach_it(rel, source, found) -> None:
    assert _key_problems(rel, source) == found


def test_a_listed_reader_may_read_the_key_and_never_remove_it() -> None:
    readers = {"lib/claude_vm/engine/init.py": "init makes the key file at its place"}
    assert _key_problems("lib/claude_vm/engine/init.py", "key = paths.key_file(where)", readers) == set()
    removes = "key = paths.key_file(where)\nkey.unlink()"
    assert _key_problems("lib/claude_vm/engine/init.py", removes, readers) == {"a removal"}


# ---------------------------------------------------------------- the checkout's folder in git ----


def _git(repo: Path, *words: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *words],
        env={**os.environ, **GIT_ENV, "HOME": str(repo.parent)},
        capture_output=True,
        text=True,
        check=True,
    )


def test_the_checkouts_folder_is_ignored_by_git_and_refused_by_the_guard(tmp_path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    shutil.copy(REPO / ".gitignore", repo / ".gitignore")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore")
    for name in ("fleet/vault.age", "vault-key", "client.yml", "fleet/settings.yml", "state/jobs/1/job.json"):
        (repo / ".aw" / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / ".aw" / name).write_text("made\n")
    assert _git(repo, "status", "--porcelain", "--untracked-files=all").stdout == ""
    # Staged by force, the tracked-file guard refuses them.
    _git(repo, "add", "-f", ".aw/fleet/settings.yml", ".aw/vault-key")
    guarded = subprocess.run(
        ["bash", str(GUARD)], cwd=repo, env={**os.environ, **GIT_ENV}, capture_output=True, text=True, check=False
    )
    assert guarded.returncode == 1, guarded.stdout + guarded.stderr
    assert guarded.stdout.splitlines()[:2] == [".aw/fleet/settings.yml", ".aw/vault-key"]


# ---------------------------------------------------------------- tests never reach the developer's ----


@pytest.fixture(scope="module")
def module_runs_environment(tmp_path_factory) -> dict:
    """What a module's own run, made once before its tests, is given."""
    return harness._env(tmp_path_factory.mktemp("module-run"))


EACH_WORKER = {"module_runs_environment"}


def test_every_environment_the_harness_builds_carries_the_tests_own_home(tmp_path, aw_home) -> None:
    assert VARIABLE in PASSED_ON
    assert os.environ[VARIABLE] == str(aw_home) and aw_home == tmp_path / "aw-home"
    for env in (
        harness._env(tmp_path),
        harness._refusing_ssh(tmp_path, harness._env(tmp_path)),
        live_kit.environment(tmp_path, "deploy"),
    ):
        assert env[VARIABLE] == str(aw_home)
    assert not aw_home.exists()


def test_a_run_made_before_the_tests_own_home_gets_the_sessions(module_runs_environment) -> None:
    # Not the developer's, whatever their shell exported: a directory of the session's own, in the temporary folder.
    session = Path(module_runs_environment[VARIABLE])
    assert session.parent == Path(tempfile.gettempdir()) and session.name.startswith("aw-home-")
    assert not any(session.is_relative_to(folder) for folder in paths.installed(os.environ))


def _three(tmp_path: Path) -> tuple[Path, Path, Path]:
    folders = paths.installed({"HOME": str(tmp_path / "developer")})
    for folder in folders:
        folder.mkdir(parents=True)
    return folders


def _aged(folders) -> dict:
    """The footprint, every name in it given a time long past first: a write the same second still shows."""
    for path in conftest.footprint(folders):
        os.utime(path, ns=(10**9, 10**9), follow_symlinks=False)
    return conftest.footprint(folders)


def test_the_footprint_sees_a_name_paths_owns_made_changed_or_removed(tmp_path) -> None:
    config, state, data = folders = _three(tmp_path)
    (config / "fleet").mkdir()
    (config / "fleet" / "settings.yml").write_text("a: 1\n")
    (data / "vault-key").write_text("AGE-SECRET-KEY-1FAKE\n")
    before = _aged(folders)
    assert str(data / "vault-key") in before and str(config / "fleet" / "settings.yml") in before
    for touch in (
        lambda: (config / "client.yml").write_text("editor: zed\n"),
        lambda: (state / "jobs").mkdir(),
        lambda: (config / "fleet" / "settings.yml").write_text("a: 2\n"),
        lambda: (data / "vault-key").chmod(0o644),
        lambda: (data / "vault-key").unlink(),
    ):
        touch()
        assert conftest.footprint(folders) != before, touch
        before = _aged(folders)


def test_the_footprint_watches_nothing_else_there(tmp_path) -> None:
    config, state, data = _three(tmp_path)
    before = conftest.footprint((config, state, data))
    # A sign-in on a machine the product configured, and today's per-checkout folder (tools/env-run --data).
    (config / "claude-login.out").write_text("")
    (config / "gh-login").write_text("digest\n")
    (data / "checkouts" / "agent-warehouse-1").mkdir(parents=True)
    assert conftest.footprint((config, state, data)) == before


def _session(tmp_path: Path, root: Path) -> SimpleNamespace:
    """A session that read the developer's folders, its home in the test's own directory, run from the checkout root."""
    session = SimpleNamespace(config=SimpleNamespace(stash=pytest.Stash()), exitstatus=pytest.ExitCode.OK)
    conftest.watch_the_developers_folders(session.config, {"HOME": str(tmp_path / "developer")}, root)
    return session


def test_the_session_fails_when_a_test_touched_the_developers_folders(tmp_path) -> None:
    config, state, data = _three(tmp_path)
    checkout = _checkout(tmp_path / "checkout")
    session = _session(tmp_path, checkout)
    conftest.pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.OK
    (data / "vault-key").write_text("made by a test\n")
    conftest.pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert session.config.stash[conftest.TOUCHED] == [str(data / "vault-key")]
    # The checkout's own .aw/: the developer's fleet, its key included, whenever they work from a checkout.
    session = _session(tmp_path, checkout)
    (checkout / ".aw").mkdir()
    (checkout / ".aw" / "client.yml").write_text("editor: zed\n")
    conftest.pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert session.config.stash[conftest.TOUCHED] == [str(checkout / ".aw" / "client.yml")]
    # An xdist worker watches nothing: the process that ends the session does, once all are done.
    worker = SimpleNamespace(stash=pytest.Stash(), workerinput={})
    conftest.watch_the_developers_folders(worker, {"HOME": str(tmp_path / "developer")}, checkout)
    assert conftest.DEVELOPERS not in worker.stash


def _worktree_of(tmp_path: Path) -> tuple[Path, Path]:
    """A main checkout and a worktree of it, made by git in the test's own directory."""
    main, checkout = tmp_path / "main", tmp_path / "checkout"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "commit", "-q", "--allow-empty", "-m", "first")
    _git(main, "worktree", "add", "-q", str(checkout))
    return main, checkout


def _real(folders) -> list[str]:
    return [os.path.realpath(folder) for folder in folders]


def test_the_guard_watches_the_checkouts_fleet_the_main_worktrees_and_the_exported_home(tmp_path) -> None:
    main, checkout = _worktree_of(tmp_path)
    developer = {"HOME": str(tmp_path / "developer")}
    installed = paths.installed(developer)
    from_main = _real([*installed, main / ".aw"])
    # From a worktree, its own .aw/ and the main worktree's, where the developer's fleet is; from the main, one.
    assert (
        _real(conftest.developers_folders(developer, checkout)) == _real([*installed, checkout / ".aw"]) + from_main[3:]
    )
    assert _real(conftest.developers_folders(developer, main)) == from_main
    # The home the developer exported, read as paths reads it; none it refuses, which the new engine never writes in.
    exported = conftest.developers_folders({**developer, VARIABLE: "~/fleets/work"}, main)
    assert _real(exported) == from_main + _real([tmp_path / "developer/fleets/work"])
    for refused in ("relative/fleet", str(installed[1] / "inside")):
        assert _real(conftest.developers_folders({**developer, VARIABLE: refused}, main)) == from_main
    # Where git cannot say, the checkout's own .aw/ is still watched.
    plain = _checkout(tmp_path / "plain")
    assert _real(conftest.developers_folders(developer, plain)) == _real([*installed, plain / ".aw"])


PROBE = """
from pathlib import Path


def test_writes_where_the_developers_fleet_is():
    for name in {written!r}:
        Path(name).parent.mkdir(parents=True, exist_ok=True)
        Path(name).write_text("editor: zed\\n")
"""


def test_a_test_that_writes_in_the_checkouts_fleet_folder_fails_the_session(tmp_path) -> None:
    # The review's probe (review/code/D1.md, finding 1), in a child pytest whose checkout is a worktree of the test's
    # own: this conftest.py copied there, so that its ROOT is that worktree, and every other helper linked. The probe
    # writes client.yml in the worktree's .aw/, the main worktree's, and the home the developer exported; the session
    # fails, naming each, though the probe's one test passed.
    main, checkout = _worktree_of(tmp_path)
    unit = checkout / "tests" / "unit"
    unit.mkdir(parents=True)
    shutil.copy(REPO / "tests" / "unit" / "conftest.py", unit / "conftest.py")
    for helper in (REPO / "tests" / "unit").glob("*.py"):
        if helper.name != "conftest.py" and not helper.name.startswith("test_"):
            (unit / helper.name).symlink_to(helper)
    shutil.copy(REPO / "pyproject.toml", checkout / "pyproject.toml")
    for name in ("lib", "tools"):
        (checkout / name).symlink_to(REPO / name)
    (checkout / "justfile").write_text("")
    exported = tmp_path / "exported"
    written = [checkout / ".aw" / "client.yml", main / ".aw" / "client.yml", exported / "client.yml"]
    (unit / "test_probe.py").write_text(PROBE.format(written=[str(path) for path in written]))
    env = {name: value for name, value in os.environ.items() if name in PASSED_ON or name.startswith("LC_")}
    env.update({"HOME": str(tmp_path / "developer"), VARIABLE: str(exported)})
    ran = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-c", str(checkout / "pyproject.toml")]
        + ["--rootdir", str(checkout), str(unit / "test_probe.py")],
        cwd=checkout,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    said = ran.stdout + ran.stderr
    assert ran.returncode == pytest.ExitCode.TESTS_FAILED and "1 passed" in said, said
    named = said.split("the developer's fleet folders", 1)[-1]
    assert [path for path in written if os.path.realpath(path) not in named and str(path) not in named] == [], said
    for path in written:
        path.unlink()
