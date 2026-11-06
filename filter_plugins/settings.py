"""Ansible's filters of just init's answers (lib/claude_vm/init.py) and of a name Proxmox gives (checks.py): an
adapter, which imports the core only when a filter is called (docs/architecture/custom-code.md §7.10)."""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)

INIT = (
    "init_answer",
    "init_answer_rule",
    "init_pick_question",
    "init_pick",
    "with_setting",
    "init_candidates",
    "init_choose_rows",
)
CHECKS = ("proxmox_name", "proxmox_names", "proxmox_names_refused")


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(_LIB, "init", INIT) | plugin.filters(_LIB, "checks", CHECKS)
