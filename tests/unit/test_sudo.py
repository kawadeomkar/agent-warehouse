"""common_user_passwordless_sudo: false leaves the VM user no route to root but sudo with its password.

The user cloud-init creates from Ubuntu's cloud image gets a sudo rule of cloud-init's own, NOPASSWD:ALL in
/etc/sudoers.d/90-cloud-init-users, and the groups adm, cdrom, dip, lxd and sudo. The setting used to remove only the
common role's drop-in, so the user - and an agent running as it - stayed root. Off now:

- the user's password is set first, from common_user_password, and unlocked, and the playbooks become root with the
  same value (ansible_become_password, group_vars/all, which sudo is handed only when it asks: on the run that turns
  the setting back on too), refused while either is missing or they differ, or while no sudo rule that takes it
  would be left;
- both rules go, each change checked by visudo, and a cloud.cfg.d drop-in keeps a new cloud-init instance from
  writing cloud-init's again;
- the user leaves the groups that reach root or the password's hash without sudo;
- the run fails on anything sudo -l -U still lists as running without a password.

dev_tools_install_docker: false takes the user out of the docker group an earlier run put it in. And every play
becomes root through /usr/bin/sudo by its path: the user's ssh sessions put ~/.local/bin first on PATH. None of it
can run against this machine, so the role's files are held to it here; a real VM ran it before it merged.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jinja2
import pytest
import yaml
from harness import REPO, _every_task, _run

COMMON = REPO / "roles" / "common"
SUDO = COMMON / "tasks" / "sudo.yml"
DEFAULTS = REPO / "inventory" / "group_vars" / "all" / "defaults.yml"


def _module(task: dict) -> str:
    return next(key for key in task if key.startswith("ansible.builtin."))


def _off() -> list:
    """The tasks of the block that takes passwordless sudo away."""
    block = next(task for task in yaml.safe_load(SUDO.read_text()) if "block" in task)
    assert block["when"] == "not common_user_passwordless_sudo"
    return block["block"]


def _named(tasks: list, words: str) -> dict:
    return next(task for task in tasks if words in task["name"])


def test_off_takes_away_cloud_init_s_rule_as_well_as_the_role_s() -> None:
    off = _off()
    own = _named(off, "Revoke the role's")
    assert own["ansible.builtin.file"] == {"path": "/etc/sudoers.d/90-{{ common_user }}", "state": "absent"}
    theirs = _named(off, "cloud-init's sudoers file")["ansible.builtin.lineinfile"]
    assert theirs["path"] == "/etc/sudoers.d/90-cloud-init-users"
    assert theirs["state"] == "absent"
    # The user's lines only, from the start of the line, and never a sudoers file visudo would refuse.
    assert theirs["regexp"] == r"^{{ common_user | regex_escape }}\s"
    assert theirs["validate"] == "/usr/sbin/visudo -cf %s"


def test_a_new_cloud_init_instance_gets_no_rule_and_only_the_role_s_groups() -> None:
    path = yaml.safe_load((COMMON / "vars" / "main.yml").read_text())["common_cloud_init_user_cfg"]
    # cloud-init merges its drop-ins with the last in order first, keeping what it has: this one must sort last.
    assert path.startswith("/etc/cloud/cloud.cfg.d/99-")
    drop_in = _named(_off(), "giving the rule back")["ansible.builtin.copy"]
    assert drop_in["dest"] == "{{ common_cloud_init_user_cfg }}"
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
    environment.filters["to_json"] = json.dumps
    rendered = environment.from_string(drop_in["content"]).render(common_user_groups=["sudo"])
    # sudo null, never false, which cloud-init 22.2 deprecated; an absent key would take cloud.cfg's NOPASSWD:ALL.
    # lock_passwd false: Ubuntu's true locks the password at every new instance, leaving sudo nothing to take.
    assert yaml.safe_load(rendered) == {
        "system_info": {"default_user": {"sudo": None, "lock_passwd": False, "groups": ["sudo"]}}
    }
    on = [task for task in yaml.safe_load(SUDO.read_text()) if task.get("when") == "common_user_passwordless_sudo"]
    assert {"path": "{{ common_cloud_init_user_cfg }}", "state": "absent"} in [
        task.get("ansible.builtin.file") for task in on
    ]


def test_off_takes_the_user_out_of_the_groups_that_reach_root_or_the_password() -> None:
    defaults = yaml.safe_load((COMMON / "defaults" / "main.yml").read_text())
    specs = yaml.safe_load((COMMON / "meta" / "argument_specs.yml").read_text())["argument_specs"]["main"]["options"]
    assert set(defaults["common_user_groups_revoked"]) == {"adm", "cdrom", "lxd"}
    assert specs["common_user_groups_revoked"]["default"] == defaults["common_user_groups_revoked"]
    leaving = _named(_off(), "groups that reach root")
    assert leaving["ansible.builtin.command"]["argv"][:2] == ["gpasswd", "--delete"]
    # A group the person asked for in common_user_groups stays.
    assert leaving["loop"] == "{{ common_user_groups_revoked | difference(common_user_groups) }}"


def test_the_password_is_the_user_s_before_any_rule_goes() -> None:
    off = _off()
    order = [task["name"] for task in off]
    refusal = _named(off, "Refuse to take it away")
    assert order.index(refusal["name"]) == 0
    # Both set, and the same: every play after the rules go becomes root with ansible_become_password.
    assert refusal["ansible.builtin.assert"]["that"] == [
        "common_user_password | length > 0",
        "ansible_become_password | default('') == common_user_password",
    ]
    given = _named(off, "the password sudo will ask for")
    # Unlocked as well: a new cloud-init instance locks it and keeps the hash, which the module compares unlocked.
    assert given["ansible.builtin.user"]["password_lock"] is False
    password = order.index(given["name"])
    # And a rule that takes it must be left, or the run locks itself and every later one out.
    left = _named(off, "while no other takes the password")
    assert (
        left["ansible.builtin.assert"]["that"].split()
        == (
            "common_user_sudo_before.stdout_lines | select('match', '\\s+[(]') | reject('search', 'NOPASSWD') | list"
            " | length > 0"
        ).split()
    )
    for rule in ("Revoke the role's", "cloud-init's sudoers file"):
        assert password < order.index(left["name"]) < order.index(_named(off, rule)["name"])


def test_every_task_that_renders_the_password_is_no_log() -> None:
    for task in _every_task(_off()):
        module = _module(task)
        arguments = yaml.safe_dump({key: value for key, value in task[module].items() if key != "that"})
        # The password or its hash; common_user_passwordless_sudo is only a name.
        if re.search(r"common_user_password(?![a-z])", arguments):
            assert task.get("no_log") is True, task["name"]
    hashed = _named(_off(), "Hash the user's password")
    # On stdin, never on the command line.
    assert "{{ common_user_password }}" not in hashed["ansible.builtin.command"]["argv"]
    assert hashed["ansible.builtin.command"]["stdin"] == "{{ common_user_password }}"
    specs = yaml.safe_load((COMMON / "meta" / "argument_specs.yml").read_text())["argument_specs"]["main"]["options"]
    assert specs["common_user_password"]["no_log"] is True


def test_off_fails_on_any_rule_that_still_runs_without_a_password() -> None:
    off = _off()
    rules = _named(off, "Read what sudo lets the user run in the end")["ansible.builtin.command"]["argv"]
    assert rules == ["/usr/bin/sudo", "-l", "-U", "{{ common_user }}"]
    check = _named(off, "still runs without a password")["ansible.builtin.assert"]["that"]
    assert check == [
        "common_user_sudo_rules.stdout is not search('NOPASSWD')",
        "common_user_sudo_rules.stdout is not search('!authenticate')",
    ]


def test_docker_off_takes_the_user_out_of_the_docker_group() -> None:
    tasks = yaml.safe_load((REPO / "roles" / "dev_tools" / "tasks" / "main.yml").read_text())
    leaving = _named(tasks, "out of the docker group")
    assert leaving["ansible.builtin.command"]["argv"] == ["gpasswd", "--delete", "{{ dev_tools_user }}", "docker"]
    assert "not dev_tools_install_docker" in leaving["when"]


@pytest.mark.parametrize(
    ("settings", "password"),
    [
        ({"common_user_passwordless_sudo": False}, "console-password-for-tests"),
        # On, sudo never asks; but the run that turns it back on meets the VM still without its passwordless rule.
        ({"common_user_passwordless_sudo": True}, "console-password-for-tests"),
        ({"vault_vm_password": ""}, ""),
    ],
)
def test_every_play_becomes_root_through_sudo_s_path_with_the_vm_password(
    tmp_path: Path, settings: dict, password: str
) -> None:
    playbook = tmp_path / "become.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "claude_vms",
                    "gather_facts": False,
                    "vars_files": [str(DEFAULTS)],
                    "tasks": [
                        {
                            "name": "Show how the play would become root",
                            "ansible.builtin.debug": {"msg": "{{ ansible_become_exe }}|{{ ansible_become_password }}"},
                        }
                    ],
                }
            ]
        )
    )
    report = _run(
        tmp_path,
        playbook,
        {"alpha": {"ansible_connection": "local"}},
        "-e",
        json.dumps({"vault_vm_password": "console-password-for-tests", **settings}),
    )
    assert report["rc"] == 0, report["stderr"]
    shown = report["plays"][0]["tasks"][0]["hosts"]["alpha"]["msg"]
    assert shown == f"/usr/bin/sudo|{password}"
