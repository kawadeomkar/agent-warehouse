"""Ansible's filter of lib/claude_vm/openpgp.py (docs/architecture/custom-code.md §7.15): openpgp_verified, a vendor's
detached signature checked with a vendored key on this machine; an adapter, which imports the core only when the
filter is called (§7.10)."""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(_LIB, "openpgp", ("openpgp_verified",))
