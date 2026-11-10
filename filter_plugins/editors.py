"""Ansible's filters of lib/claude_vm/editors/registry.py (docs/architecture/custom-code.md §7.10): editor_open_plan,
the plan of just code's window that tasks/editor_open.yml applies, and editor_servers, each editor's window as just
status's probe knows it; an adapter, which imports the core only when a filter is called."""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(_LIB, "editors.registry", ("editor_open_plan", "editor_servers"))
