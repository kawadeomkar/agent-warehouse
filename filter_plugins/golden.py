"""Ansible's filters of lib/claude_vm/golden.py (docs/architecture/custom-code.md §7.10): an adapter, which imports the
core only when a filter is called."""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)

GOLDEN = ("toolchain_digest", "secret_options", "option_differences")


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(_LIB, "golden", GOLDEN)
