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
CODE = ["playbooks", "roles", "filter_plugins", "callback_plugins", "tools", "justfile", "deploy.yml", "inventory"]


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
    r"^(roles/vscode_server/|playbooks/tasks/vscode_[a-z_]+\.yml$|playbooks/vars/vscode\.yml$|filter_plugins/vscode\.py$)"
)
VSCODE_ELSEWHERE = {
    "deploy.yml": 5,
    "filter_plugins/live_ansible.py": 1,
    "filter_plugins/live_recipes.py": 2,
    "filter_plugins/status.py": 3,
    "inventory/group_vars/all/defaults.yml": 20,
    "inventory/group_vars/all/local.yml.example": 10,
    "justfile": 2,
    "playbooks/code.yml": 29,
    "playbooks/configure.yml": 5,
    "playbooks/destroy.yml": 1,
    "playbooks/files/claude-terminal.sh": 2,
    "playbooks/files/session-notes.sh": 3,
    "playbooks/init_settings.yml": 1,
    "playbooks/library/vm_status_info.py": 5,
    "playbooks/tasks/session_write.yml": 1,
    "playbooks/tasks/ssh_config.yml": 2,
    "playbooks/templates/vm_session.md.j2": 5,
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
