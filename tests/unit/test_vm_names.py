"""A VM is never named after a host of the inventory (claude_vm_inventory_hosts, group_vars/all).

add_host merges a VM into the host of its name. A VM named pve - the example inventory's name for the Proxmox host -
gave that host the address its own guest agent reported, and the API token went there in the same run; one named
localhost made this machine a VM for configure to run every role on. On a Proxmox others use too, anyone can make
and tag a VM of either name. Each is run here against the fake Proxmox (tests/molecule/fake_pve_api.py) with an
inventory whose Proxmox host is pve, as hosts.yml.example has it, and whose API address is read from that host, as
group_vars/all reads it:

- discovery leaves such a VM out, and says so;
- just status shows it from the listing, read no further, and reads every other VM through the API's own address;
- a name given that is one is refused before Proxmox is asked anything, by every playbook that takes names.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from harness import REPO, _pve, _run
from test_status import _status_run, fake_fleet, status_root

PLAYBOOKS = REPO / "playbooks"
# The Proxmox host's address read from the inventory's host, as group_vars/all/defaults.yml has it: a VM merged into
# that host would move the API there.
FROM_THE_HOST = {"proxmox_api_host": "{{ hostvars[groups['proxmox'][0]]['ansible_host'] }}"}


@pytest.fixture
def fleet(tmp_path):
    """The fake fleet - alpha and beta running, gamma stopped - and two VMs more, tagged and running, named pve and
    localhost."""
    for base in fake_fleet(tmp_path):
        for vmid, name in [(9013, "pve"), (9014, "localhost")]:
            _pve(base, "POST", "nodes/pve/qemu/9000/clone", newid=vmid, name=name)
            _pve(base, "PUT", f"nodes/pve/qemu/{vmid}/config", tags="claude-on-proxmox", ciuser="dev", onboot="1")
            _pve(base, "POST", f"nodes/pve/qemu/{vmid}/status/start")
            for _ in range(3):
                try:
                    _pve(base, "GET", f"nodes/pve/qemu/{vmid}/agent/network-get-interfaces")
                except Exception:  # noqa: BLE001 - the fake refuses the first polls on purpose
                    pass
        yield base


def _proxmox_inventory(root: Path) -> list[str]:
    """The Proxmox host as hosts.yml.example names it, at the fake's address: -i arguments for a run."""
    inventory = root / "proxmox.yml"
    inventory.write_text(yaml.safe_dump({"proxmox": {"hosts": {"pve": {"ansible_host": "127.0.0.1"}}}}))
    return ["-i", str(inventory)]


def _discover(tmp_path: Path, base: str, *extra: str) -> dict:
    root, _ = status_root(tmp_path, base, FROM_THE_HOST)
    playbook = root / "discover.yml"
    playbook.write_text((PLAYBOOKS / "discover.yml").read_text().replace("tasks/", f"{PLAYBOOKS / 'tasks'}/"))
    return _run(root, playbook, {}, *_proxmox_inventory(root), *extra, timeout=180)


def _task(report: dict, name: str) -> dict:
    for play in report["plays"]:
        for task in play["tasks"]:
            if task["task"]["name"] == name:
                return task["hosts"]["localhost"]
    return {}


def test_discovery_leaves_out_a_vm_named_after_a_host_of_the_inventory_and_says_so(tmp_path, fleet) -> None:
    report = _discover(tmp_path, fleet)
    assert report["rc"] == 0, report["stderr"][-2000:]
    added = _task(report, "Add the reachable ones to the inventory")
    names = [r["claude_vm_found"]["name"] for r in added["results"] if not r.get("skipped")]
    assert sorted(names) == ["alpha", "beta"]
    said = _task(report, "Say which tagged VMs are left out for their name")["msg"]
    assert "localhost" in said and "pve" in said


def test_a_name_given_that_is_a_host_of_the_inventory_is_refused_before_proxmox_is_asked(tmp_path, fleet) -> None:
    calls = tmp_path / "pve" / "calls.log"
    before = calls.read_text() if calls.exists() else ""
    report = _discover(tmp_path, fleet, "-e", json.dumps({"vm_name": "alpha,pve"}))
    assert report["rc"] != 0
    refused = _task(report, "vm_names | Refuse a VM named after a host of the inventory")
    assert refused.get("failed") and "pve is the name of a host in the inventory" in refused["msg"]
    assert calls.read_text() == before


def test_status_shows_such_a_vm_unread_and_reads_the_rest_through_the_api_s_own_address(tmp_path, fleet) -> None:
    root, _ = status_root(tmp_path, fleet, FROM_THE_HOST)
    report, model = _status_run(tmp_path, fleet, *_proxmox_inventory(root), settings=FROM_THE_HOST)
    assert report["rc"] == 0, report["stderr"][-2000:]
    by = {vm["name"]: vm for vm in model["vms"]}
    for name in ("pve", "localhost"):
        assert by[name]["read"] is False and by[name]["skipped"] == "name"
        assert any(it["vm"] == name and it["level"] == "unread" for it in model["attention"])
    # Play 2 asks Proxmox about each VM at proxmox_api_url: had pve been merged, at the address pve reported.
    assert by["alpha"]["api"]["read"] and by["beta"]["api"]["read"]


@pytest.mark.parametrize("playbook", ["provision.yml", "destroy.yml", "code.yml", "discover.yml", "ssh_config.yml"])
def test_every_playbook_that_takes_names_refuses_a_host_of_the_inventory(playbook: str) -> None:
    text = (PLAYBOOKS / playbook).read_text()
    assert "import_tasks: tasks/vm_names.yml" in text
