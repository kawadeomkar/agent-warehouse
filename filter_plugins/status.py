"""Ansible's filters of `just status` (lib/claude_vm/status/model.py, docs/architecture/lifecycle.md §4.6): an adapter,
which imports the core only when a filter is called (docs/architecture/custom-code.md §7.10)."""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)

MODEL = (
    "status_clean",
    "status_sections",
    "status_needs_ssh",
    "status_fleet",
    "status_aliases",
    "status_storages",
    "status_vm_api",
    "status_system_read",
    "status_refusal",
    "status_work_read",
    "status_report",
)
# What the roles say of a VM's sign-ins (lib/claude_vm/status/lines.py), which just status shows too.
LINES = ("github_too_old_said", "github_login_report", "tailscale_report")


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(_LIB, "status.model", MODEL) | plugin.filters(_LIB, "status.lines", LINES)
