"""What the roles say of a VM's sign-ins (lib/claude_vm/status/lines.py), which just status shows too: an adapter, which
imports the core only when a filter is called (docs/architecture/custom-code.md §7.10)."""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)

LINES = ("github_too_old_said", "github_login_report", "tailscale_report")


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(_LIB, "status.lines", LINES)
