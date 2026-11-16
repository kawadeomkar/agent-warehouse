"""Ansible's filters of lib/claude_vm/route.py (docs/architecture/custom-code.md §7.17): vm_hop, a VM's hop through the
Proxmox host - the words its ssh_common_args gain, its socket's part of the name, a person's ssh's -J; ssh_jump and
ssh_proxy_any, the proxy options among ssh arguments; vm_route_vms, which VMs about to be published go through the
hop, and vm_route_unheld, which published ones would not go where the route says; jump_spec, the hop as a ProxyJump
line names it, and hop_key_offered, whether ssh would offer the hop the key the inventory names; route_said and
vm_unanswered, the words a run says of it. An adapter, which imports the core only when a filter is called."""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(
            _LIB,
            "route",
            (
                "vm_hop",
                "ssh_jump",
                "ssh_proxy_any",
                "vm_route_vms",
                "vm_route_unheld",
                "jump_spec",
                "route_said",
                "vm_unanswered",
                "hop_key_offered",
            ),
        )
