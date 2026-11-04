"""Ansible's filters of lib/claude_vm/text.py: plain_text, one_line and banner (docs/architecture/custom-code.md §7.4).

An adapter: it imports the core only when a filter is called (§7.10).
"""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(_LIB, "text", ("plain_text", "one_line", "banner"))
