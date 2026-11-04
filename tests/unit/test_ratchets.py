"""What one editor or one hypervisor is to the rest of the code, held to what it is now: never more.

The code is being made to take more than VS Code and more than Proxmox (docs/architecture/decisions.md §15.4). Each
editor's own files and each hypervisor's own roles may name it as they like; anywhere else, a mention is one more
place a second editor or hypervisor has to be threaded through. So each table below is how many lines name it in each
file outside its own, today. A file whose count grows, or a file new to the table, fails; one whose count falls fails
too, until its number here is lowered to match, so the table only ever tightens.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# The code: what runs, and what decides how it runs. Not the docs, the tests or the scenarios, which describe it.
CODE = [
    "playbooks",
    "roles",
    "lib",
    "filter_plugins",
    "callback_plugins",
    "tools",
    "justfile",
    "deploy.yml",
    "inventory",
]


def _lines_naming(pattern: str, own: re.Pattern, kept: str = "") -> dict[str, int]:
    """Each file of the code outside `own` that has lines matching pattern, and how many; a line's names that 1.x
    promises (kept), which can never go, are taken out of it before it is matched."""
    tracked = subprocess.run(
        ["git", "ls-files", "--", *CODE], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    found = {}
    for path in tracked:
        if own.search(path) or "/molecule/" in path:
            continue
        try:
            text = (REPO / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        count = sum(
            1 for line in text.splitlines() if re.search(pattern, re.sub(kept, "", line) if kept else line, re.I)
        )
        if count:
            found[path] = count
    return found


def _held(found: dict[str, int], allowed: dict[str, int], what: str) -> None:
    grown = {path: (count, allowed.get(path, 0)) for path, count in found.items() if count > allowed.get(path, 0)}
    fallen = {path: (found.get(path, 0), count) for path, count in allowed.items() if found.get(path, 0) < count}
    assert grown == {}, f"more lines name {what} than before, (now, allowed): {grown}"
    assert fallen == {}, f"fewer lines name {what} than the table allows, so lower it, (now, allowed): {fallen}"


# VS Code's own: its role, its two task files of just code's and its variables, and its settings filter. Elsewhere
# a line counts that names it or one of its own terms - its CLI's, its workspace files', its task runner's - once the
# names 1.x promises are taken out of it: the vscode_* settings, the vscode_server role and its variables, its
# files on every VM, the vscode_windows key of just status --json.
VSCODE_OWN = re.compile(
    r"^(roles/vscode_server/|playbooks/tasks/vscode_[a-z_]+\.yml$|playbooks/vars/vscode\.yml$|filter_plugins/vscode\.py$|lib/claude_vm/editors/vscode\.py$)"
)
VSCODE_ELSEWHERE = {
    "deploy.yml": 4,
    "inventory/group_vars/all/defaults.yml": 22,
    "inventory/group_vars/all/local.yml.example": 11,
    "justfile": 2,
    "lib/claude_vm/ansible/engine.py": 1,
    "lib/claude_vm/run/recipes.py": 2,
    "playbooks/code.yml": 27,
    "playbooks/configure.yml": 5,
    "playbooks/destroy.yml": 1,
    "playbooks/files/claude-terminal.sh": 2,
    "playbooks/files/session-notes.sh": 3,
    "playbooks/init_settings.yml": 1,
    "playbooks/library/vm_status_info.py": 4,
    "playbooks/tasks/session_read.yml": 1,
    "playbooks/tasks/session_write.yml": 1,
    "playbooks/tasks/ssh_config.yml": 2,
    "playbooks/tasks/zed_check.yml": 2,
    "playbooks/templates/vm_session.md.j2": 6,
    "roles/github_projects/vars/main.yml": 1,
    "roles/tailscale/tasks/main.yml": 1,
}


VSCODE_TERMS = r"vscode|vs code|\.code-workspace|ssh-remote\+|code --remote|\bcode -r\b|tasks: run task|remote - ssh"
VSCODE_PROMISED = (
    r"vscode_(claude_terminal|workspaces_dir|session_notes|claude_sessions|remote|user_settings_file|windows)\b"
    r"|vscode_server\w*|[0-9]{2}-vscode-server\.conf"
)


def test_vs_code_is_named_outside_its_own_files_no_more_than_it_is_now() -> None:
    _held(_lines_naming(VSCODE_TERMS, VSCODE_OWN, VSCODE_PROMISED), VSCODE_ELSEWHERE, "VS Code")


# Proxmox's own: its two roles, its filters and the script just init runs on the host as root. Anywhere else, a line
# that names its API's settings, its collection or its raw cluster listing reads Proxmox directly - the reads a
# second hypervisor would have to be put behind (docs/architecture/decisions.md §15.4).
PROXMOX_OWN = re.compile(
    r"^(roles/proxmox_vm/|roles/proxmox_template/|filter_plugins/proxmox\.py$|lib/claude_vm/proxmox\.py$|tools/proxmox-access\.sh$)"
)
PROXMOX_ELSEWHERE = {
    "deploy.yml": 2,
    "inventory/group_vars/all/defaults.yml": 30,
    "inventory/group_vars/all/local.yml.example": 4,
    "inventory/group_vars/all/vault.yml.example": 2,
    "lib/claude_vm/ansible/engine.py": 4,
    "lib/claude_vm/refs.py": 3,
    "lib/claude_vm/run/model.py": 4,
    "lib/claude_vm/status/model.py": 18,
    "lib/claude_vm/tui/run.py": 3,
    "lib/claude_vm/tui/status.py": 3,
    "lib/claude_vm/wiring.py": 1,
    "playbooks/code.yml": 2,
    "playbooks/configure.yml": 1,
    "playbooks/destroy.yml": 17,
    "playbooks/discover.yml": 3,
    "playbooks/init_check.yml": 38,
    "playbooks/init_settings.yml": 16,
    "playbooks/provision.yml": 23,
    "playbooks/ssh_config.yml": 3,
    "playbooks/status.yml": 23,
    "playbooks/tailscale_login.yml": 1,
    "playbooks/tasks/cluster_vms.yml": 17,
    "playbooks/tasks/init_choose.yml": 2,
    "playbooks/tasks/init_local_setting.yml": 1,
    "playbooks/tasks/tailscale_record.yml": 6,
    "playbooks/tasks/vm_addresses.yml": 11,
    "playbooks/tasks/vm_stop.yml": 4,
    "playbooks/templates/vm_session.md.j2": 2,
}


# Its API's settings, its collection, its raw listing and the paths of its API, and the VMID, Proxmox's own ID of a
# VM, which the fleet's records are to carry as a provider's id (plan §7.1).
PROXMOX_TERMS = r"proxmox_api_|community\.proxmox|claude_vm_all|proxmox_vm_id|/cluster/|/qemu/|\bvmid\b"


def test_proxmox_is_read_outside_its_own_files_no_more_than_it_is_now() -> None:
    _held(_lines_naming(PROXMOX_TERMS, PROXMOX_OWN), PROXMOX_ELSEWHERE, "Proxmox's API")


# Zed's own: its two task files of just code's. Elsewhere, a line that names it.
ZED_OWN = re.compile(r"^playbooks/tasks/zed_[a-z_]+\.yml$")
ZED_ELSEWHERE = {
    "inventory/group_vars/all/defaults.yml": 5,
    "inventory/group_vars/all/local.yml.example": 3,
    "justfile": 1,
    "lib/claude_vm/run/recipes.py": 1,
    "playbooks/code.yml": 4,
    "playbooks/library/vm_status_info.py": 7,
    "playbooks/tasks/session_read.yml": 2,
    "roles/vscode_server/meta/argument_specs.yml": 1,
    "roles/vscode_server/tasks/main.yml": 1,
}


def test_zed_is_named_outside_its_own_files_no_more_than_it_is_now() -> None:
    _held(_lines_naming(r"\bzed\b|zeditor", ZED_OWN), ZED_ELSEWHERE, "Zed")
