"""Ansible's filters of lib/claude_vm/repo_sync.py, the repository sync's decisions (docs/architecture/custom-code.md
§7.6): an adapter, which imports the core only when a filter is called (§7.10)."""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)

FILTERS = (
    "repo_sync_match",
    "repo_sync_plan",
    "repo_sync_refs",
    "repo_sync_said",
    "repo_sync_sources",
)


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(_LIB, "repo_sync", FILTERS)
