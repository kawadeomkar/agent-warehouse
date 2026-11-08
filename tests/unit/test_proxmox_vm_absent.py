"""Deleting a VM forgets its SSH host key under every name this machine may hold it by.

The task runs for real, delegated to localhost as in destroy.yml, against a
known_hosts file in a scratch HOME: never the developer's own.

What this guards: with claude_ssh_host_domain set, the SSH aliases and just code
connect by <name>.<domain>, so that is a name known_hosts records the key under.
Deleting the VM forgot only its address, and a VM created again under the same
name failed its first connection by name with "Host key verification failed".

And what it never forgets: the address is the VM's own report, from its guest
agent, so a VM that reported the Proxmox host's address (or another host's of
the inventory) just before its deletion had this machine forget that host's
key, and accept whatever answered there next.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from harness import REPO, _run

ROLE = REPO / "roles" / "proxmox_vm"
FORGET = "absent | Forget the VM's SSH host key"


def _forget(tmp_path: Path, **extra) -> set[str]:
    """Run absent.yml's forget task with the role's vars and `extra`; return the hosts left in known_hosts."""
    extra = {"proxmox_vm_found_tags": ["agent-warehouse"], **extra}
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / "key")], check=True)
    key = (tmp_path / "key.pub").read_text().split()[:2]
    known = home / ".ssh" / "known_hosts"
    hosts = ("192.0.2.10", "alpha.lan", "beta.lan", "198.51.100.129")
    known.write_text("".join(f"{host} {key[0]} {key[1]}\n" for host in hosts))

    task = next(t for t in yaml.safe_load((ROLE / "tasks" / "absent.yml").read_text()) if t["name"] == FORGET)
    playbook = tmp_path / "forget.yml"
    vars_files = [str(ROLE / "defaults" / "main.yml"), str(ROLE / "vars" / "main.yml")]
    play = {"hosts": "localhost", "gather_facts": False, "vars_files": vars_files, "tasks": [task]}
    playbook.write_text(yaml.safe_dump([play]))
    # The module expands ~/.ssh/known_hosts from HOME: the scratch one.
    report = _run(tmp_path, playbook, {}, "-e", json.dumps(extra), env={"HOME": str(home)})
    assert report["rc"] == 0, report["stderr"][-2000:]
    return {line.split()[0] for line in known.read_text().splitlines() if line.strip()}


VM = {"proxmox_vm_name": "alpha", "proxmox_vm_forget_address": "192.0.2.10"}


def test_with_a_domain_the_name_is_forgotten_with_the_address(tmp_path):
    assert _forget(tmp_path, **VM, proxmox_vm_known_hosts_domain="lan") == {"beta.lan", "198.51.100.129"}


def test_without_a_domain_only_the_address_is_forgotten(tmp_path):
    assert _forget(tmp_path, **VM, proxmox_vm_known_hosts_domain="") == {"alpha.lan", "beta.lan", "198.51.100.129"}


@pytest.mark.parametrize("domain", ["lan", ""])
def test_a_vm_whose_address_is_unknown_still_loses_its_name(tmp_path, domain):
    left = _forget(tmp_path, **{**VM, "proxmox_vm_forget_address": ""}, proxmox_vm_known_hosts_domain=domain)
    assert left == {"198.51.100.129"} | (
        {"192.0.2.10", "beta.lan"} if domain else {"192.0.2.10", "alpha.lan", "beta.lan"}
    )


def test_the_inventory_wires_the_domain_into_the_role():
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert defaults["proxmox_vm_known_hosts_domain"] == "{{ claude_ssh_host_domain }}"


def test_the_proxmox_host_s_key_is_never_forgotten(tmp_path):
    left = _forget(tmp_path, **VM, proxmox_vm_api_host="192.0.2.10", proxmox_vm_known_hosts_domain="lan")
    assert left == {"192.0.2.10", "beta.lan", "198.51.100.129"}


def test_an_address_kept_for_the_inventory_is_never_forgotten(tmp_path):
    kept = {"proxmox_vm_api_host": "198.51.100.1", "proxmox_vm_known_hosts_kept": ["192.0.2.10"]}
    assert _forget(tmp_path, **VM, **kept, proxmox_vm_known_hosts_domain="") == {
        "192.0.2.10",
        "alpha.lan",
        "beta.lan",
        "198.51.100.129",
    }


@pytest.mark.parametrize("kept", [{"proxmox_vm_known_hosts_kept": ["beta.lan"]}, {"proxmox_vm_api_host": "beta.lan"}])
def test_a_kept_name_is_never_forgotten_as_the_vm_s_name_either(tmp_path, kept):
    # The Proxmox host reached as beta.lan, under another name in the inventory, and a VM named beta deleted.
    vm = {**VM, "proxmox_vm_name": "beta"}
    left = _forget(
        tmp_path, **{"proxmox_vm_api_host": "198.51.100.1", **kept}, **vm, proxmox_vm_known_hosts_domain="lan"
    )
    assert left == {"alpha.lan", "beta.lan", "198.51.100.129"}


def test_the_inventory_wires_its_own_hosts_addresses_into_the_role():
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert defaults["proxmox_vm_known_hosts_kept"] == "{{ claude_vm_inventory_addresses }}"
    assert "claude_vm_inventory_hosts" in defaults["claude_vm_inventory_addresses"]
    assert "ansible_host" in defaults["claude_vm_inventory_addresses"]
    # The same list an SSH alias may never take.
    assert "claude_vm_inventory_addresses" in defaults["claude_vm_inventory_names"]


@pytest.mark.parametrize("mark", ["agent-warehouse-wg-198.51.100.129", "agent-warehouse-wg-198.51.100.129-reached"])
def test_the_tunnel_address_its_mark_holds_is_forgotten_too(tmp_path, mark):
    # Under vpn_reach: tunnel the alias connects to the WireGuard address, so known_hosts holds the key there, and
    # the next VM is soon given the address: the lowest free one.
    left = _forget(tmp_path, **VM, proxmox_vm_found_tags=["agent-warehouse", mark], proxmox_vm_known_hosts_domain="")
    assert left == {"alpha.lan", "beta.lan"}


def test_another_tag_s_wireguard_address_is_not_the_vm_s(tmp_path):
    found = ["agent-warehouse", "someone-wg-198.51.100.129"]
    left = _forget(tmp_path, **VM, proxmox_vm_found_tags=found, proxmox_vm_known_hosts_domain="")
    assert left == {"alpha.lan", "beta.lan", "198.51.100.129"}
