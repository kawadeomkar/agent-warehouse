"""The environment a run gets: built from an allow list, never inherited.

A project's .claude/settings.json `env` reaches the server (verified on 2.1.309), and so would a direnv or mise
environment of the folder Claude Code was started in. Inherited, ANSIBLE_CONFIG would outrank the checkout's
ansible.cfg, PYTHONPATH would decide what ansible-playbook imports with .vault_pass in reach, PATH which git, ssh
and uv run, and HOME or XDG_DATA_HOME which collections it loads (tools/env-run --collections). So a run gets the few
variables a login needs from the server's environment, the PATH, HOME and XDG_DATA_HOME just mcp recorded, and what
the server's own arguments say - nothing of Claude Code's (CLAUDECODE, CLAUDE_CODE_*: the messaging token
among them, which an editor's terminals would inherit) and nothing of Ansible's, Python's or just's.
"""

from __future__ import annotations

import os

from claude_vm.mcp.config import Config

# Taken from the server's environment as they are.
PASSED = (
    "USER",
    "LOGNAME",
    "SHELL",
    "TMPDIR",
    "LANG",
    # The keys ssh offers the Proxmox host and the VMs.
    "SSH_AUTH_SOCK",
    # What xdg-open needs on Linux to open a sign-in page.
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
)
# And every variable of the locale's.
PASSED_PREFIX = "LC_"


def run_env(config: Config, environ: dict[str, str] | None = None) -> dict[str, str]:
    """A run's environment: PASSED from the server's, the recorded PATH, HOME and XDG_DATA_HOME, no colour, and the
    vault password file or browser just mcp recorded."""
    environ = dict(os.environ if environ is None else environ)
    env = {name: environ[name] for name in PASSED if name in environ}
    env |= {name: value for name, value in environ.items() if name.startswith(PASSED_PREFIX)}
    env |= {"PATH": config.path, "HOME": config.home, "TERM": "dumb", "NO_COLOR": "1"}
    if config.data_home:
        env["XDG_DATA_HOME"] = config.data_home
    if config.vault_password_file:
        env["ANSIBLE_VAULT_PASSWORD_FILE"] = config.vault_password_file
    if config.browser:
        env["BROWSER"] = config.browser
    return env
