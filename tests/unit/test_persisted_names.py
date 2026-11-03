"""The names runs have left on VMs, on the Proxmox host and on this machine, which no release may change.

Each is data the project finds again on a later run: a tag on a VM, a marker file, a drop-in it owns, a block in the
managed SSH config, a path inside a workspace file VS Code reopens. Renamed in the code, every VM and machine already
deployed would be stranded - a VM no longer found as this project's, a drop-in written twice under two names, a marker
read where none was ever written - whatever the project is called. So each is listed here as the code spells it, and a
change to one fails this test, which is the place to stop and write the migration instead (docs/architecture/
decisions.md §15.4; docs/guides/upgrading.md).

A name is held whole: a variable's value as YAML reads it, or the exact line that spells it. A substring would pass a
new name that contains the old one (gh-login-v2 for gh-login), which strands as surely as any other rename.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]

# (file, variable, its value): a variable of a vars, defaults or inventory file, compared as YAML reads it.
VALUES = [
    # On every VM, in Proxmox: the ownership tag, the mark of a VM whose provisioning did not finish, the default name.
    ("inventory/group_vars/all/defaults.yml", "claude_vm_tag", "claude-on-proxmox"),
    ("inventory/group_vars/all/defaults.yml", "vm_default_name", "claude-on-proxmox-default"),
    ("roles/proxmox_vm/vars/main.yml", "proxmox_vm_unfinished_tag", "{{ proxmox_vm_sent_tags[0] }}-unfinished"),
    # On the Proxmox host: where the template's image is built.
    ("roles/proxmox_template/defaults/main.yml", "proxmox_template_image_dir", "/var/lib/vz/claude-on-proxmox"),
    # On a VM: the roles' markers and drop-ins.
    ("roles/tailscale/defaults/main.yml", "tailscale_marker_dir", "/var/lib/claude-on-proxmox"),
    (
        "roles/tailscale/vars/main.yml",
        "tailscale_installed_marker_path",
        "{{ tailscale_marker_dir }}/tailscale-installed",
    ),
    ("roles/tailscale/vars/main.yml", "tailscale_joined_marker_path", "{{ tailscale_marker_dir }}/tailscale-joined"),
    (
        "roles/tailscale/vars/main.yml",
        "tailscale_dropin_path",
        "/etc/systemd/system/tailscaled.service.d/10-claude-on-proxmox.conf",
    ),
    (
        "roles/github_projects/vars/main.yml",
        "github_projects_marker_file",
        "{{ github_projects_home_dir }}/.config/claude-on-proxmox/gh-login",
    ),
    (
        "roles/github_projects/vars/main.yml",
        "github_projects_device_marker_file",
        "{{ github_projects_home_dir }}/.config/claude-on-proxmox/gh-device-login",
    ),
    ("roles/claude_code/vars/main.yml", "claude_code_login_dir", "{{ claude_code_home }}/.config/claude-on-proxmox"),
    ("roles/common/vars/main.yml", "common_ssh_path_dropin", "/etc/ssh/sshd_config.d/15-claude-on-proxmox-path.conf"),
    # Inside every workspace file VS Code reopens from its recent list: VS Code's helpers, by path.
    ("playbooks/vars/vscode.yml", "code_helpers_dir", "{{ code_ws_dir }}/helpers"),
    ("playbooks/vars/vscode.yml", "code_helpers", ["claude-terminal.sh", "session-notes.sh", "session_notes.py"]),
    ("playbooks/vars/vscode.yml", "code_terminal_helper", "{{ code_helpers_dir }}/claude-terminal.sh"),
    ("playbooks/vars/vscode.yml", "code_notes_helper", "{{ code_helpers_dir }}/session-notes.sh"),
    # On this machine: the managed SSH config.
    (
        "inventory/group_vars/all/defaults.yml",
        "claude_ssh_config_file",
        "{{ lookup('ansible.builtin.env', 'HOME') }}/.ssh/claude-on-proxmox.conf",
    ),
]

# (file, the line as it is there, stripped): where the name is a task's argument, a play's variable, code or text.
LINES = [
    # On a VM: common's and vscode_server's files, the latter VS Code's name on any editor's VM through 1.x.
    ("roles/common/tasks/main.yml", "dest: /etc/ssh/sshd_config.d/10-claude-on-proxmox.conf"),
    ("roles/common/tasks/main.yml", "dest: /etc/apt/apt.conf.d/90claude-on-proxmox-downloads"),
    ("roles/vscode_server/tasks/main.yml", "dest: /etc/ssh/sshd_config.d/20-vscode-server.conf"),
    ("roles/vscode_server/tasks/main.yml", "sysctl_file: /etc/sysctl.d/60-vscode-server.conf"),
    # just code's own directory on a VM, and what is in it: the sessions' mirror and manifest, written by just code and
    # read by destroy's save; the notes; the workspace files.
    ("filter_plugins/sessions.py", 'path = str(given or "") or f"{str(projects).rstrip(\'/\')}/.claude-on-proxmox"'),
    ("playbooks/code.yml", 'code_session_dir: "{{ code_ws_dir }}/claude-sessions"'),
    ("playbooks/tasks/session_write.yml", 'mirror: "{{ code_session_dir }}/mirror"'),
    ("playbooks/tasks/session_write.yml", 'manifest: "{{ code_session_dir }}/manifest.json"'),
    (
        "playbooks/tasks/session_save.yml",
        "{{ claude_vm_save_projects | just_code_dir(editor_files_dir | default(vscode_workspaces_dir"
        " | default('', true))) }}/claude-sessions/manifest.json",
    ),
    ("playbooks/code.yml", "code_notes_dir: \"{{ code_ws_dir_given ~ '/session' }}\""),
    (
        "playbooks/code.yml",
        "code_notes_file: \"{{ code_ws_dir_given ~ '/session/' ~ claude_vm_names[0] ~ '-session.md' }}\"",
    ),
    ("playbooks/code.yml", "~ ('/project/' ~ code_project_given if code_project_given | length > 0"),
    ("playbooks/code.yml", "~ '.code-workspace' }}"),
    # The notes' live section, as the template writes it and the helper finds it.
    ("playbooks/templates/vm_session.md.j2", "<!-- claude-on-proxmox:live -->"),
    ("playbooks/templates/vm_session.md.j2", "<!-- /claude-on-proxmox:live -->"),
    (
        "playbooks/files/session_notes.py",
        'START, END = "<!-- claude-on-proxmox:live -->", "<!-- /claude-on-proxmox:live -->"',
    ),
    # On the Proxmox host: the record of the template's build in its notes.
    ("filter_plugins/proxmox.py", 'TEMPLATE_RECORD = "Built by claude-on-proxmox:"'),
    # On this machine: the managed SSH config's blocks, written and read, and the checkout's own folder.
    ("playbooks/tasks/ssh_config.yml", 'marker: "# {mark} claude-on-proxmox: {{ claude_ssh_vm.name }}"'),
    (
        "filter_plugins/proxmox.py",
        'r"^# BEGIN claude-on-proxmox: (?P<name>\\S+)$\\n(?P<body>.*?)^# END claude-on-proxmox: (?P=name)$",',
    ),
    (
        "tools/env-run",
        "data=${XDG_DATA_HOME:-$HOME/.local/share}/claude-on-proxmox/checkouts/"
        '$(basename "$here")-$(printf %s "$here" | cksum | cut -d \' \' -f 1)',
    ),
]


def _value(where: str, name: str):
    data = yaml.safe_load((REPO / where).read_text())
    assert isinstance(data, dict) and name in data, f"{where} no longer sets {name}"
    return data[name]


def _spells(where: str, line: str) -> bool:
    return line in {each.strip() for each in (REPO / where).read_text().splitlines()}


@pytest.mark.parametrize(("where", "name", "value"), VALUES, ids=[f"{w}: {n}" for w, n, _ in VALUES])
def test_a_name_a_run_left_somewhere_is_the_value_it_was(where: str, name: str, value) -> None:
    assert _value(where, name) == value, f"{where}: {name} changed, and deployed VMs and machines have the old one"


@pytest.mark.parametrize(("where", "line"), LINES, ids=[f"{w}: {line[:48]}" for w, line in LINES])
def test_a_name_a_run_left_somewhere_is_spelled_on_its_line_as_it_was(where: str, line: str) -> None:
    assert _spells(where, line), f"{where} no longer has the line {line!r}: deployed VMs and machines have it"


def test_a_name_kept_inside_a_longer_one_is_a_rename_too(tmp_path, monkeypatch) -> None:
    # What a substring would pass: the old name still inside the new.
    (tmp_path / "vars.yml").write_text('marker: "{{ home }}/.config/claude-on-proxmox/gh-login-v2"\n')
    (tmp_path / "tasks.yml").write_text("    dest: /etc/ssh/sshd_config.d/10-claude-on-proxmox.conf.d\n")
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    assert _value("vars.yml", "marker") != "{{ home }}/.config/claude-on-proxmox/gh-login"
    assert not _spells("tasks.yml", "dest: /etc/ssh/sshd_config.d/10-claude-on-proxmox.conf")
