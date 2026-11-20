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
import tempfile
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

# The modules that may name the key file, and the one that may name the folders: no cleanup, prune or sweep elsewhere
# can then reach the key by its name (docs/architecture/engine.md §16.9).
NAME_THE_KEY = ("lib/claude_vm/fleet/paths.py", "lib/claude_vm/fleet/vaultkey.py")
NAME_THE_FOLDERS = ("lib/claude_vm/fleet/paths.py",)
FOLDERS = re.compile(
    r"XDG_CONFIG_HOME|XDG_STATE_HOME|AGENT_WAREHOUSE_HOME"
    r"|\.config\W{1,6}agent-warehouse(?![\w-])|\.local\W{1,6}state\W{1,6}agent-warehouse(?![\w-])"
)
KEY_NAMED = re.compile(r"vault-key")


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
        assert values[folder].endswith("/.config/agent-warehouse"), (role, values[folder])
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


def _calls_key_file(text: str) -> bool:
    """A call of key_file, or its value read as an attribute (a Home's .key_file), in Python's syntax tree."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return bool(re.search(r"(?<![\w.])key_file\s*\(|\.key_file\b", text))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "key_file":
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "key_file":
            return True
        if isinstance(node, ast.ImportFrom) and any(alias.name == "key_file" for alias in node.names):
            return True
    return False


def test_only_paths_names_the_fleets_folders() -> None:
    texts = _engine_texts()
    assert len(texts) > 80 and "tools/aw" in dict(texts), len(texts)
    found = sorted(rel for rel, text in texts if FOLDERS.search(text))
    assert found == list(NAME_THE_FOLDERS), "ask fleet/paths.py where they are"


def test_only_paths_and_vaultkey_name_the_key_file() -> None:
    texts = _engine_texts()
    named = sorted(rel for rel, text in texts if KEY_NAMED.search(text) or _calls_key_file(text))
    assert named and set(named) <= set(NAME_THE_KEY), "no module but these two may reach the key file by its name"


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


@pytest.mark.parametrize(
    ("source", "names"),
    [
        ("paths.key_file(where)", True),
        ("from claude_vm.fleet.paths import key_file", True),
        ("key = where.key_file", True),
        ("key_file(where)", True),
        ("ssh_key_file = '~/.ssh/id_ed25519'", False),
        ("'ansible_ssh_private_key_file'", False),
    ],
)
def test_the_scan_for_the_key_sees_each_way_to_reach_it(source, names) -> None:
    assert (bool(KEY_NAMED.search(source)) or _calls_key_file(source)) is names


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


def test_the_session_fails_when_a_test_touched_the_developers_folders(tmp_path) -> None:
    config, state, data = _three(tmp_path)
    session = SimpleNamespace(config=SimpleNamespace(stash=pytest.Stash()), exitstatus=pytest.ExitCode.OK)
    conftest.watch_the_developers_folders(session.config, {"HOME": str(tmp_path / "developer")})
    conftest.pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.OK
    (data / "vault-key").write_text("made by a test\n")
    conftest.pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert session.config.stash[conftest.TOUCHED] == [str(data / "vault-key")]
    # An xdist worker watches nothing: the process that ends the session does, once all are done.
    worker = SimpleNamespace(stash=pytest.Stash(), workerinput={})
    conftest.watch_the_developers_folders(worker, {"HOME": str(tmp_path / "developer")})
    assert conftest.DEVELOPERS not in worker.stash
