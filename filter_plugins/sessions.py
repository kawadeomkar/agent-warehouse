"""Ansible's filters of lib/claude_vm/sessions.py, the session handoff's decisions (docs/architecture/custom-code.md
§7.6): an adapter, which imports the core only when a filter is called (§7.10)."""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)

FILTERS = (
    "claude_project_folder",
    "claude_session_dirs",
    "claude_sessions_listed",
    "claude_sessions_plan",
    "claude_sessions_saved",
    "just_code_dir",
)


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(_LIB, "sessions", FILTERS)
