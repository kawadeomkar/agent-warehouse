"""Ansible's filters of lib/claude_vm/route.py (docs/architecture/custom-code.md §7.17): vm_hop, a VM's hop through the
Proxmox host - the words its ssh_common_args gain, its socket's part of the name, a person's ssh's -J - and
ssh_jump and ssh_proxy_any, the proxy options among ssh arguments; an adapter, which imports the core only when a
filter is called."""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(_LIB, "route", ("vm_hop", "ssh_jump", "ssh_proxy_any"))
