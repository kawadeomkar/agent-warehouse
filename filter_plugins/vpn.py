"""Ansible's filters of lib/claude_vm/vpn/ (docs/architecture/custom-code.md §7.11): vpn_member_plan, the plan of who is
on a VPN's network that tasks/vpn_apply.yml applies, tailscale_label, the machine name the tailscale role asks for, and
tailscale_sibling_labels, those the fleet's other VMs join as, which its release compares a device's name with; an
adapter, which imports the core only when a filter is called."""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(_LIB, "vpn.registry", ("vpn_member_plan",)) | plugin.filters(
            _LIB, "vpn.tailscale", ("tailscale_label", "tailscale_sibling_labels")
        )
