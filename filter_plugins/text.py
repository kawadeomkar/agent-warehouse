"""Ansible's filters of lib/claude_vm/text.py: plain_text, one_line and banner, and the words messages are built with,
full_stop, retry_line and and_list (docs/architecture/custom-code.md §7.4).

An adapter: it imports the core only when a filter is called (§7.10).
"""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)

FILTERS = ("plain_text", "one_line", "banner", "full_stop", "retry_line", "and_list")


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(_LIB, "text", FILTERS)
