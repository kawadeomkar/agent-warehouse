"""A pool of the project's own (proxmox_pool): the VMs just deploy creates are cloned into it, so that a token granted
the role on that pool alone, in place of /vms, reaches them and nothing else on the host
(docs/getting-started/proxmox-access.md, "A pool of the project's own"). The runs go against the fake Proxmox
(tests/molecule/fake_pve_api.py), as test_vm_names.py's do; tools/proxmox-access.sh's --pool is
test_proxmox_access.py's, and just init's check of the pool test_init_check.py's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from harness import _failures, _pve, fake_fleet
from test_vm_names import FROM_THE_HOST, PLAYBOOKS
from test_vm_names import _playbook as _run_playbook

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyDoNotUse test"
POOL = "agent-warehouse"


@pytest.fixture
def fleet(tmp_path):
    yield from fake_fleet(tmp_path)


def _provision(tmp_path: Path, base: str, name: str, **settings) -> dict:
    given = {**FROM_THE_HOST, "vm_ssh_public_keys": [KEY], **settings}
    extra = json.dumps({"vm_name": name, "deploy_sign_in": False})
    return _run_playbook(tmp_path, base, PLAYBOOKS / "provision.yml", "-e", extra, settings=given)


def _listing(base: str) -> list[dict]:
    return _pve(base, "GET", "cluster/resources")["data"]


def test_a_vm_is_cloned_into_the_pool_and_a_token_of_the_pool_alone_finds_it_again(tmp_path: Path, fleet: str) -> None:
    report = _provision(tmp_path, fleet, "pooled", proxmox_pool=POOL)
    assert report["rc"] == 0, _failures(report)
    (vm,) = [vm for vm in _listing(fleet) if vm.get("name") == "pooled"]
    assert vm["pool"] == POOL
    # The token then holds the role on the pool alone, the template made a member by root, as the guide says: it
    # lists the pool's members only, and finds the VM it made, changing nothing.
    template = next(vm for vm in _listing(fleet) if vm.get("template"))
    _pve(fleet, "PUT", "fake/pool-member", vmid=template["vmid"], pool=POOL)
    _pve(fleet, "PUT", "fake/pool-scope", token="ansible", pool=POOL)
    assert sorted(vm["name"] for vm in _listing(fleet)) == ["pooled", template["name"]]
    again = _provision(tmp_path, fleet, "pooled", proxmox_pool=POOL)
    assert again["rc"] == 0, _failures(again)
    assert again["stats"]["pooled"]["changed"] == 0 and len(_listing(fleet)) == 2
    # A second VM goes into the pool too, cloned from the template the pool holds.
    second = _provision(tmp_path, fleet, "second", proxmox_pool=POOL)
    assert second["rc"] == 0, _failures(second)
    assert next(vm for vm in _listing(fleet) if vm.get("name") == "second")["pool"] == POOL


def test_without_a_pool_a_vm_is_a_member_of_none(tmp_path: Path, fleet: str) -> None:
    report = _provision(tmp_path, fleet, "plain")
    assert report["rc"] == 0, _failures(report)
    assert "pool" not in next(vm for vm in _listing(fleet) if vm.get("name") == "plain")


@pytest.mark.parametrize("pool", ["-x", "a pool", "pool;rm"])
def test_a_pool_that_is_no_pool_s_name_is_refused_before_anything_is_created(
    tmp_path: Path, fleet: str, pool: str
) -> None:
    report = _provision(tmp_path, fleet, "refused", proxmox_pool=pool)
    assert report["rc"] != 0
    said = " ".join(_failures(report).values())
    assert "proxmox_pool is the name of a pool on the Proxmox host" in said, said
    assert all(vm.get("name") != "refused" for vm in _listing(fleet))
