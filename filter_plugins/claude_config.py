"""Ansible's filters of lib/claude_vm/claude_config.py, what just code carries of this machine's Claude Code beside the
sessions (docs/architecture/custom-code.md §7.6): an adapter, which imports the core only when a filter is called
(§7.10)."""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)

FILTERS = (
    "claude_config_paths",
    "claude_config_plan",
    "claude_config_rsync",
    "claude_memory_plan",
)


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(_LIB, "claude_config", FILTERS)
