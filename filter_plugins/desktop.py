"""Ansible's filters of lib/claude_vm/desktop.py: page_opener, controller_run and controller_run_state
(docs/architecture/custom-code.md §7.4): an adapter, which imports the core only when a filter is called (§7.10)."""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(_LIB, "desktop", ("page_opener", "controller_run", "controller_run_state"))
