"""Layer 1 is held to state modules: every task the system play reaches (playbooks/system.yml; docs/architecture/
engine.md §16.10) is a module of ansible.builtin that converges a state, or one of the commands below with its reason.

The walk is golden.reached's: the play's own tasks and the task files they import or include, then each role the play
runs from its main entry point, every task file it includes by a literal name, with the role's handlers. Three tests
hold what it finds. A command, shell, raw, script or expect task that REASONS does not list fails by its file and name,
and one REASONS lists that is no longer reached fails too, so that the list tightens by itself. A module outside
ansible.builtin fails by name, but for the three OUTSIDE lists, which F6a replaces with built-in ones, so that the
product needs no Galaxy collection: then OUTSIDE is empty. And nothing the play reaches sets a variable whose name
begins ansible_, which would outrank the connection the engine wrote (the play's own check sees every such source but
a task's own vars and a fact set during the run, which this holds).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from harness import REPO

from claude_vm import golden

PLAY = REPO / "playbooks" / "system.yml"
# What runs a command of the machine's rather than converge a state: by every name Ansible takes for it.
COMMANDS = frozenset(
    f"{prefix}{name}"
    for prefix in ("", "ansible.builtin.", "ansible.legacy.")
    for name in ("command", "shell", "raw", "script", "expect")
)
# Where a variable is set by name, in a task: its own vars, a fact, a register, an include of variables.
SET_FACT = ("ansible.builtin.set_fact", "set_fact", "ansible.legacy.set_fact")
INCLUDE_VARS = ("ansible.builtin.include_vars", "include_vars", "ansible.legacy.include_vars")
INCLUDES = ("ansible.builtin.include_tasks", "ansible.builtin.import_tasks")
ROLE_INCLUDES = ("ansible.builtin.include_role", "ansible.builtin.import_role")

# Every command the play reaches, by its file and name, with why no state module serves it. Those of today's roles stay
# as they stand until the cutover trims the roles (K28); the 1.x move is a no-op on a new machine until then.
REASONS = {
    ("playbooks/system.yml", "Read what the VM's base has"): (
        "raw, before anything needs Python: what the base lacks and what it can never run, read once"
    ),
    ("playbooks/system.yml", "Wait for cloud-init to finish"): "a read that waits: cloud-init status --wait",
    ("playbooks/system.yml", "Read what cloud-init reported"): "a read: cloud-init status --long, for the failure",
    ("roles/common/tasks/names.yml", "names | Move each file to its name from 2.0"): (
        "the 1.x moves, mv -n with creates and removes: a no-op on a new machine until K28 deletes it"
    ),
    ("roles/common/tasks/sudo.yml", "sudo | Hash the user's password"): (
        "openssl passwd, the hash the user module is given: Python's crypt is gone from the controller"
    ),
    ("roles/common/tasks/sudo.yml", "sudo | Read what sudo lets the user run now"): "a read: sudo -l -U",
    ("roles/common/tasks/sudo.yml", "sudo | Take the user out of the groups that reach root or the password"): (
        "gpasswd --delete, one group at a time: the user module sets a whole list of groups or appends"
    ),
    ("roles/common/tasks/sudo.yml", "sudo | Read what sudo lets the user run in the end"): "a read: sudo -l -U",
    ("roles/common/tasks/ssh_path.yml", "ssh_path | Find where the connections come from, for {{ common_user }}"): (
        "a read: printenv SSH_CONNECTION, which no fact carries"
    ),
    ("roles/common/tasks/ssh_path.yml", "ssh_path | Read the PATH sshd gives the sessions of {{ common_user }}"): (
        "a read: sshd -T -C, what sshd applies, Match blocks included"
    ),
    ("roles/common/tasks/masked_units.yml", "masked_units | List which of the units to mask the VM has"): (
        "a read: systemctl list-unit-files, the units the machine has"
    ),
    ("roles/common/tasks/masked_units.yml", "masked_units | Read which of the units failed"): (
        "a read: systemctl is-failed"
    ),
    ("roles/common/tasks/masked_units.yml", "masked_units | Clear the failed state a unit was left in"): (
        "systemctl reset-failed, which systemd_service has no state for, only where a unit failed"
    ),
    ("roles/vscode_server/tasks/main.yml", "Look for the tools the server installer needs"): (
        "a read: command -v bash tar curl wget"
    ),
    ("roles/vscode_server/tasks/main.yml", "Find where the editor's connection will come from"): (
        "a read: printenv SSH_CONNECTION, which no fact carries"
    ),
    ("roles/vscode_server/tasks/main.yml", "Read the configuration sshd applies to the editor's connection"): (
        "a read: sshd -T -C, what sshd applies, Match blocks included"
    ),
    ("roles/dev_tools/tasks/main.yml", "Take the user out of the docker group"): (
        "gpasswd --delete, one group at a time: the user module sets a whole list of groups or appends"
    ),
    ("roles/dev_tools/tasks/uv.yml", "uv | Check installed version"): "a read: uv --version",
    ("roles/dev_tools/tasks/rust.yml", "rust | Read the components installed for {{ dev_tools_rust_toolchain }}"): (
        "a read: rustup component list, from the toolchain's own manifest"
    ),
    ("roles/dev_tools/tasks/rust.yml", "rust | Install Rust {{ dev_tools_rust_toolchain }}"): (
        "rustup toolchain install, only where the toolchain is missing: rustup has no module"
    ),
    ("roles/dev_tools/tasks/rust.yml", "rust | Add the components the toolchain is missing"): (
        "rustup component add, only of those missing: rustup has no module"
    ),
}

# The modules outside ansible.builtin the play reaches, by file and name: three, each replaced by a built-in one in
# F6a, which takes them off this list.
OUTSIDE = {
    ("roles/common/tasks/main.yml", "Authorise SSH keys"): ("ansible.posix.authorized_key", "replaced by F6a"),
    ("roles/vscode_server/tasks/main.yml", "Raise the inotify watch limit for the editor's file watcher"): (
        "ansible.posix.sysctl",
        "replaced by F6a",
    ),
    ("roles/dev_tools/tasks/node.yml", "node | Install global npm packages"): (
        "community.general.npm",
        "replaced by F6a",
    ),
}


def reached(play: Path = PLAY, repo: Path = REPO) -> list[Path]:
    """Every task file play reaches: itself, the task files its plays import or include (by literal names, from the
    including file's folder), and for each role it runs or includes, every task file golden.reached walks from the
    entry point, and the role's handlers."""
    files = [play]
    roles: list[tuple[str, str]] = []
    queue = [play]
    while queue:
        path = queue.pop(0)
        for task in _tasks(path):
            for key in INCLUDES:
                if key in task:
                    given = task[key] if isinstance(task[key], str) else task[key]["file"]
                    if "{{" in given:
                        raise ValueError(f"{path}: {key} of a templated file name, {given!r}: the walk cannot see it")
                    found = Path(os.path.normpath(path.parent / given))
                    if found not in files:
                        files.append(found)
                        queue.append(found)
            for key in ROLE_INCLUDES:
                if key in task:
                    roles.append((task[key]["name"], task[key].get("tasks_from", "main")))
        if path == play:
            for each in yaml.safe_load(play.read_text()):
                for role in each.get("roles") or []:
                    roles.append((role if isinstance(role, str) else role.get("role", role.get("name")), "main"))
    for role, entry in roles:
        files += [path for path in golden.reached(role, entry, repo) if path.parent.name == "tasks"]
        handlers = repo / "roles" / role / "handlers" / "main.yml"
        if handlers.is_file():
            files.append(handlers)
    return list(dict.fromkeys(files))


def _tasks(path: Path) -> Iterator[dict]:
    """Every task of a playbook's plays (their pre_tasks, tasks, post_tasks and handlers) or of a task file, those of a
    block's block, rescue and always included, a block itself left out."""
    parsed = yaml.safe_load(path.read_text()) or []
    lists = [parsed]
    if parsed and isinstance(parsed[0], dict) and "hosts" in parsed[0]:
        lists = [play.get(key) for play in parsed for key in ("pre_tasks", "tasks", "post_tasks", "handlers")]
    for each in lists:
        for task in golden.tasks(each):
            if "block" not in task:
                yield task


def _module(task: dict) -> str:
    """The module or action a task calls, as written, as Ansible's own parser finds it among the task's keys."""
    from ansible.parsing.mod_args import ModuleArgsParser

    action, _, _ = ModuleArgsParser(task_ds=task).parse(skip_action_validation=True)
    return action


def _where(path: Path, repo: Path) -> str:
    return str(path.relative_to(repo))


def commands(play: Path = PLAY, repo: Path = REPO) -> dict[tuple[str, str], str]:
    """Each task the play reaches that runs a command, by its file and name: the module it calls."""
    return {
        (_where(path, repo), task.get("name", "")): module
        for path in reached(play, repo)
        for task in _tasks(path)
        if (module := _module(task)) in COMMANDS
    }


def outside(play: Path = PLAY, repo: Path = REPO) -> dict[tuple[str, str], str]:
    """Each task the play reaches whose module is no module of ansible.builtin, by its file and name: the module."""
    return {
        (_where(path, repo), task.get("name", "")): module
        for path in reached(play, repo)
        for task in _tasks(path)
        if not (module := _module(task)).startswith("ansible.builtin.")
    }


def _variables_files(play: Path, repo: Path) -> list[Path]:
    """The roles' defaults and internals the play reaches: those of each role whose task files the walk found."""
    found = []
    for path in reached(play, repo):
        if path.parent.name in ("tasks", "handlers") and path.parent.parent.parent == repo / "roles":
            for each in ("defaults/main.yml", "vars/main.yml"):
                if (path.parent.parent / each).is_file():
                    found.append(path.parent.parent / each)
    return list(dict.fromkeys(found))


def setting_connection_variables(play: Path = PLAY, repo: Path = REPO) -> list[str]:
    """Each place what the play reaches sets a variable whose name begins ansible_: a play's vars, a role's, a task's
    vars, a fact, a register and an include of variables, said as the file, the task or the play, and the name."""
    found = []
    for each in yaml.safe_load(play.read_text()):
        found += [f"{_where(play, repo)}: the play's vars: {name}" for name in each.get("vars") or {}]
        for role in each.get("roles") or []:
            if isinstance(role, dict):
                found += [f"{_where(play, repo)}: a role's vars: {name}" for name in role.get("vars") or {}]
    for path in _variables_files(play, repo):
        found += [f"{_where(path, repo)}: {name}" for name in yaml.safe_load(path.read_text()) or {}]
    for path in reached(play, repo):
        for task in _tasks(path):
            named = [*(task.get("vars") or {})]
            module = _module(task)
            if module in SET_FACT and isinstance(task[module], dict):
                named += list(task[module])
            if isinstance(task.get("register"), str):
                named.append(task["register"])
            if module in INCLUDE_VARS:
                named.append(f"(an include of variables, {task[module]!r})")
            found += [f"{_where(path, repo)}: {task.get('name', '')}: {name}" for name in named]
    return [each for each in found if each.rsplit(": ", 1)[1].startswith(("ansible_", "(an include"))]


# ---------------------------------------------------------------------------------------------------- the rules ----


def test_every_command_shell_or_raw_the_play_reaches_has_its_reason() -> None:
    found = commands()
    new = sorted(set(found) - set(REASONS))
    gone = sorted(set(REASONS) - set(found))
    assert new == [], f"a command, shell or raw task layer 1 reaches with no reason listed in REASONS: {new}"
    assert gone == [], f"REASONS lists a task the play no longer reaches, so take it out: {gone}"
    assert all(reason for reason in REASONS.values())


def test_every_module_the_play_reaches_is_ansible_builtin_but_the_three_f6a_replaces() -> None:
    found = outside()
    assert found == {key: module for key, (module, _) in OUTSIDE.items()}, (
        "a module outside ansible.builtin that layer 1 reaches, which a Galaxy collection would have to bring: "
        f"{sorted(set(found.items()) - {(key, module) for key, (module, _) in OUTSIDE.items()})}"
    )
    assert {reason for _, reason in OUTSIDE.values()} == {"replaced by F6a"}


def test_nothing_the_play_reaches_sets_an_ansible_variable() -> None:
    assert setting_connection_variables() == []


def test_the_walk_reaches_what_the_play_runs() -> None:
    files = {_where(path, REPO) for path in reached()}
    # The play, its shared task file, each role's entry point and what it includes, and the handlers.
    assert {
        "playbooks/system.yml",
        "playbooks/tasks/remote_tmp.yml",
        "roles/common/tasks/main.yml",
        "roles/common/tasks/sudo.yml",
        "roles/common/tasks/masked_units.yml",
        "roles/common/handlers/main.yml",
        "roles/vscode_server/tasks/main.yml",
        "roles/vscode_server/handlers/main.yml",
        "roles/dev_tools/tasks/main.yml",
        "roles/dev_tools/tasks/node.yml",
        "roles/dev_tools/tasks/rust.yml",
    } <= files, files
    # Nothing of the roles the play does not run, nor an image's entry point.
    assert not [each for each in files if each.startswith(("roles/claude_code/", "roles/github_projects/"))]
    assert "roles/common/tasks/image.yml" not in files
    # The roles' defaults and internals, whose variables the third rule reads.
    assert {_where(path, REPO) for path in _variables_files(PLAY, REPO)} == {
        "roles/common/defaults/main.yml",
        "roles/common/vars/main.yml",
        "roles/vscode_server/defaults/main.yml",
        "roles/dev_tools/defaults/main.yml",
    }


# ------------------------------------------------------------------------------- the scans see what they hold ----


def _planted(root: Path, play: list, tasks: dict[str, list], defaults: dict | None = None) -> Path:
    """A tree of root's own: playbooks/system.yml holding play, and a role named one with tasks/<file>.yml for each
    of tasks and defaults/main.yml."""
    shared = root / "playbooks" / "tasks" / "shared.yml"
    shared.parent.mkdir(parents=True)
    shared.write_text(yaml.safe_dump([{"name": "shared", "ansible.builtin.ping": {}}]))
    role = root / "roles" / "one"
    (role / "tasks").mkdir(parents=True)
    (role / "defaults").mkdir()
    for name, listed in tasks.items():
        (role / "tasks" / f"{name}.yml").write_text(yaml.safe_dump(listed))
    (role / "defaults" / "main.yml").write_text(yaml.safe_dump(defaults or {"one_x": 1}))
    play_file = root / "playbooks" / "system.yml"
    play_file.write_text(yaml.safe_dump(play))
    return play_file


def _play(**extra) -> list:
    return [
        {
            "name": "p",
            "hosts": "all",
            "pre_tasks": [{"name": "shared tasks", "ansible.builtin.import_tasks": "tasks/shared.yml"}],
            "roles": [{"role": "one", "tags": ["one"]}],
            **extra,
        }
    ]


@pytest.mark.parametrize(
    ("task", "module"),
    [
        ({"name": "c", "ansible.builtin.command": {"argv": ["true"]}}, "ansible.builtin.command"),
        ({"name": "c", "command": "true"}, "command"),
        ({"name": "c", "ansible.legacy.shell": "true"}, "ansible.legacy.shell"),
        ({"name": "c", "ansible.builtin.raw": "true"}, "ansible.builtin.raw"),
        ({"name": "c", "ansible.builtin.script": "x.sh"}, "ansible.builtin.script"),
        ({"name": "c", "action": {"module": "ansible.builtin.shell", "cmd": "true"}}, "ansible.builtin.shell"),
    ],
    ids=["command", "short", "legacy shell", "raw", "script", "action"],
)
def test_a_command_anywhere_the_play_reaches_is_found(tmp_path: Path, task: dict, module: str) -> None:
    # In a block's rescue, in a task file the role's entry point includes, with a loop and a condition.
    nested = {"name": "b", "block": [{"name": "ok", "ansible.builtin.ping": {}}], "rescue": [{**task, "loop": [1]}]}
    play = _planted(
        tmp_path,
        _play(),
        {"main": [{"name": "inc", "ansible.builtin.include_tasks": "deep.yml", "when": "x"}], "deep": [nested]},
    )
    assert commands(play, tmp_path) == {("roles/one/tasks/deep.yml", "c"): module}


def test_a_module_outside_ansible_builtin_is_found_by_any_spelling(tmp_path: Path) -> None:
    tasks = {
        "main": [
            {"name": "a", "community.general.npm": {"name": "x"}},
            {"name": "b", "ansible.legacy.copy": {"src": "x", "dest": "y"}},
            {"name": "c", "copy": {"src": "x", "dest": "y"}},
            {"name": "d", "ansible.builtin.copy": {"src": "x", "dest": "y"}},
        ]
    }
    play = _planted(tmp_path, _play(), tasks)
    assert outside(play, tmp_path) == {
        ("roles/one/tasks/main.yml", "a"): "community.general.npm",
        ("roles/one/tasks/main.yml", "b"): "ansible.legacy.copy",
        ("roles/one/tasks/main.yml", "c"): "copy",
    }


def test_a_handler_and_a_role_the_play_includes_are_walked(tmp_path: Path) -> None:
    play = _planted(tmp_path, _play(), {"main": [{"name": "ok", "ansible.builtin.ping": {}}]})
    (tmp_path / "roles" / "one" / "handlers").mkdir()
    (tmp_path / "roles" / "one" / "handlers" / "main.yml").write_text(
        yaml.safe_dump([{"name": "h", "ansible.builtin.shell": "true"}])
    )
    two = tmp_path / "roles" / "two" / "tasks"
    two.mkdir(parents=True)
    (two / "other.yml").write_text(yaml.safe_dump([{"name": "r", "ansible.builtin.raw": "true"}]))
    with_two = _play(tasks=[{"name": "two", "ansible.builtin.include_role": {"name": "two", "tasks_from": "other"}}])
    play.write_text(yaml.safe_dump(with_two))
    assert commands(play, tmp_path) == {
        ("roles/one/handlers/main.yml", "h"): "ansible.builtin.shell",
        ("roles/two/tasks/other.yml", "r"): "ansible.builtin.raw",
    }


def test_an_include_the_walk_cannot_follow_fails_it(tmp_path: Path) -> None:
    play = _planted(tmp_path, _play(), {"main": [{"name": "i", "ansible.builtin.include_tasks": "{{ which }}.yml"}]})
    with pytest.raises(ValueError, match="templated file name"):
        commands(play, tmp_path)


@pytest.mark.parametrize(
    ("where", "said"),
    [
        ("play", "playbooks/system.yml: the play's vars: ansible_ssh_common_args"),
        ("role", "playbooks/system.yml: a role's vars: ansible_ssh_common_args"),
        ("defaults", "roles/one/defaults/main.yml: ansible_ssh_common_args"),
        ("task", "roles/one/tasks/main.yml: t: ansible_ssh_common_args"),
        ("fact", "roles/one/tasks/main.yml: t: ansible_ssh_common_args"),
        ("register", "roles/one/tasks/main.yml: t: ansible_ssh_common_args"),
        ("include", "roles/one/tasks/main.yml: t: (an include of variables, 'x.yml')"),
    ],
)
def test_a_variable_that_would_outrank_the_connection_is_found_wherever_it_is_set(
    tmp_path: Path, where: str, said: str
) -> None:
    name = "ansible_ssh_common_args"
    ping = {"name": "t", "ansible.builtin.ping": {}}
    task = {
        "task": {**ping, "vars": {name: "-o StrictHostKeyChecking=no"}},
        "fact": {"name": "t", "ansible.builtin.set_fact": {name: "-o StrictHostKeyChecking=no"}},
        "register": {**ping, "register": name},
        "include": {"name": "t", "ansible.builtin.include_vars": "x.yml"},
    }.get(where, ping)
    play = _play(vars={name: "x"}) if where == "play" else _play()
    if where == "role":
        play[0]["roles"] = [{"role": "one", "vars": {name: "x"}}]
    defaults = {name: "x"} if where == "defaults" else None
    planted = _planted(tmp_path, play, {"main": [task]}, defaults)
    assert setting_connection_variables(planted, tmp_path) == [said]
