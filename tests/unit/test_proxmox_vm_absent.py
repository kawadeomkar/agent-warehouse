"""Deleting a VM forgets no SSH host key, anywhere.

What this guards: the key used to be filed under the address the VM's guest agent reported, and forgotten by it at
deletion, so a VM that reported the Proxmox host's address, or a NAS's, just before its deletion had this machine
forget that host's key and take whatever answered there next. Then it was filed under agent-warehouse-<SMBIOS UUID>,
from the config Proxmox gives, and forgotten by that: but a backup restored without --unique keeps its UUID, so
deleting one of two such VMs forgot the other's key, and any VM later given the deleted one's UUID - which anyone who
may change a VM's hardware on a shared host can set - was let in on first use. Kept, the key only ever matches that VM
again, and refuses every other.
"""

from __future__ import annotations

import yaml
from harness import REPO

from claude_vm import proxmox

ROLE = REPO / "roles" / "proxmox_vm"
UUID = "0b5f5bbc-4a1f-4c1e-9d6b-6f1c2d3e4f50"
ALIAS = f"agent-warehouse-{UUID}"


def test_deleting_a_vm_touches_no_known_hosts_file():
    tasks = yaml.safe_load((ROLE / "tasks" / "absent.yml").read_text())
    modules = {key for task in tasks for key in task if "." in key}
    assert modules == {"community.proxmox.proxmox_kvm"}, modules
    # Nothing of the role names a known_hosts file, nor a key to forget, nor an address to forget it by.
    role = "\n".join(path.read_text() for path in ROLE.rglob("*.yml") if "molecule" not in path.parts)
    for name in ("known_hosts", "ssh-keygen", "forget_address", "host_key_alias"):
        assert name not in role, name


def test_the_inventory_wires_no_known_hosts_file_into_the_role():
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert not [name for name in defaults if name.startswith("proxmox_vm_known_hosts")]
    assert (
        defaults["claude_ssh_known_hosts_file"] == "{{ claude_ssh_config_file | dirname }}/agent-warehouse.known_hosts"
    )


def test_the_alias_is_the_uuid_proxmox_gives_or_none():
    assert proxmox.ssh_host_key_alias({"smbios1": f"uuid={UUID.upper()},manufacturer=QEMU"}) == ALIAS
    assert proxmox.ssh_host_key_alias({"smbios1": f"manufacturer=x,uuid={UUID}"}) == ALIAS
    for config in ({}, {"smbios1": ""}, {"smbios1": "uuid=not-a-uuid"}, {"smbios1": f"uuid={UUID}x"}, None, "x"):
        assert proxmox.ssh_host_key_alias(config) == "", config
