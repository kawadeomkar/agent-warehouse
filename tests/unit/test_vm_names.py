"""A VM is never named after a host of the inventory (claude_vm_inventory_hosts, group_vars/all).

add_host merges a VM into the host of its name. A VM named pve - the example inventory's name for the Proxmox host -
gave that host the address its own guest agent reported, and the API token went there in the same run; one named
localhost made this machine a VM for configure to run every role on. On a Proxmox others use too, anyone can make
and tag a VM of either name. Each is run here against the fake Proxmox (tests/molecule/fake_pve_api.py) with an
inventory whose Proxmox host is pve, as hosts.yml.example has it, and whose API address is read from that host, as
group_vars/all reads it:

- discovery leaves such a VM out, and says so;
- just status shows it from the listing, read no further, and reads every other VM through the API's own address;
- a name given that is one is refused before Proxmox is asked anything, by every playbook that takes names;
- a fleet-wide alias refresh removes an alias of such a name, or of an address, that an earlier version wrote.
"""

from __future__ import annotations

import json
import re
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


def _refresh(tmp_path: Path, base: str, *extra: str) -> dict:
    """playbooks/ssh_config.yml against the fake, its managed file the test's own (tmp_path / "ssh.conf")."""
    settings = {**FROM_THE_HOST, "claude_ssh_user_config": str(tmp_path / "config"), "claude_ssh_config_include": False}
    root, _ = status_root(tmp_path, base, settings)
    playbook = root / "ssh_config.yml"
    playbook.write_text((PLAYBOOKS / "ssh_config.yml").read_text().replace("tasks/", f"{PLAYBOOKS / 'tasks'}/"))
    return _run(root, playbook, {}, *_proxmox_inventory(root), *extra, timeout=180)


def _creating(tmp_path: Path, base: str, playbook: str, names: str) -> dict:
    """deploy.yml or provision.yml against the fake, with names given, from a run directory of its own."""
    root, _ = status_root(tmp_path, base, FROM_THE_HOST)
    if playbook == "deploy.yml":
        text = (REPO / "deploy.yml").read_text().replace(" playbooks/", f" {PLAYBOOKS}/")
    else:
        text = (PLAYBOOKS / playbook).read_text().replace("tasks/", f"{PLAYBOOKS / 'tasks'}/")
    (root / playbook).write_text(text)
    extra = json.dumps({"vm_name": names, "deploy_sign_in": False})
    return _run(root, root / playbook, {}, *_proxmox_inventory(root), "-e", extra, timeout=180)


def _block(name: str) -> str:
    return f"# BEGIN claude-on-proxmox: {name}\nHost {name}\n    HostName 192.0.2.99\n# END claude-on-proxmox: {name}\n"


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


def test_a_fleet_wide_refresh_removes_an_alias_no_vm_may_have_and_leaves_a_dotted_one(tmp_path, fleet) -> None:
    # An earlier version wrote an alias for any tagged VM, so the managed file may hold one for the VM named pve - the
    # Proxmox host's root SSH - or for one named like an address. Tagged VMs still carry both names here.
    _pve(fleet, "POST", "nodes/pve/qemu/9000/clone", newid=9015, name="3232235777")
    _pve(fleet, "PUT", "nodes/pve/qemu/9015/config", tags="claude-on-proxmox", ciuser="dev")
    # A dotted name: the user's own, written by a run that named it. Running, so the refresh has its address.
    _pve(fleet, "POST", "nodes/pve/qemu/9000/clone", newid=9016, name="alpha.lab")
    _pve(fleet, "PUT", "nodes/pve/qemu/9016/config", tags="claude-on-proxmox", ciuser="dev")
    _pve(fleet, "POST", "nodes/pve/qemu/9016/status/start")
    for _ in range(3):
        try:
            _pve(fleet, "GET", "nodes/pve/qemu/9016/agent/network-get-interfaces")
        except Exception:  # noqa: BLE001 - the fake refuses the first polls on purpose
            pass
    managed = tmp_path / "ssh.conf"
    managed.write_text(_block("pve") + _block("3232235777") + _block("alpha.lab"))
    report = _refresh(tmp_path, fleet)
    assert report["rc"] == 0, report["stderr"][-2000:]
    text = managed.read_text()
    assert re.findall(r"^# BEGIN claude-on-proxmox: (\S+)$", text, flags=re.M) == ["alpha.lab", "alpha", "beta"]
    # Left as it was: neither refreshed nor removed by a run that did not name it.
    assert _block("alpha.lab") in text
    said = _task(report, "Report what was kept, pruned and left alone")["msg"]
    # alpha and beta: alpha.lab had an address, and was left out.
    assert said.startswith("2 aliases refreshed in ")
    assert "which an earlier version wrote: pve, 3232235777" in said
    assert "just deploy alpha.lab --only ssh-config" in _task(report, "ssh_config | Say which VMs get no alias")["msg"]


@pytest.mark.parametrize("playbook", ["deploy.yml", "provision.yml"])
@pytest.mark.parametrize(
    ("name", "said"),
    [
        ("pve", "pve is the name of a host in the inventory"),
        ("3232235777", "ssh reads it as an address"),
        # The address the inventory reaches the Proxmox host at.
        ("127.0.0.1", "ssh reads it as an address"),
    ],
)
def test_a_name_no_vm_may_have_is_refused_before_proxmox_is_asked(tmp_path, fleet, playbook, name, said) -> None:
    # deploy.yml before its template play, which may build a template over root SSH; provision.yml before a clone.
    calls = tmp_path / "pve" / "calls.log"
    before = calls.read_text() if calls.exists() else ""
    report = _creating(tmp_path, fleet, playbook, f"alpha,{name}")
    assert report["rc"] != 0
    refused = [
        task["hosts"]["localhost"]
        for play in report["plays"]
        for task in play["tasks"]
        if task["task"]["name"].startswith("vm_names | ") and task["hosts"]["localhost"].get("failed")
    ]
    assert len(refused) == 1 and said in refused[0]["msg"], refused
    assert calls.read_text() == before


def test_a_name_with_a_dot_passes_the_checks_deploy_makes_first(tmp_path, fleet) -> None:
    root, _ = status_root(tmp_path, fleet, FROM_THE_HOST)
    play = yaml.safe_load((REPO / "deploy.yml").read_text())[0]
    tasks = [{**task, "ansible.builtin.import_tasks": f"{PLAYBOOKS / 'tasks'}/vm_names.yml"} for task in play["tasks"]]
    (root / "first.yml").write_text(yaml.safe_dump([{**play, "tasks": tasks}]))
    extra = json.dumps({"vm_name": "alpha.lab,beta"})
    report = _run(root, root / "first.yml", {}, *_proxmox_inventory(root), "-e", extra, timeout=120)
    assert report["rc"] == 0, report["stderr"][-2000:]


@pytest.mark.parametrize("playbook", ["provision.yml", "destroy.yml", "code.yml", "discover.yml", "ssh_config.yml"])
def test_every_playbook_that_takes_names_refuses_a_host_of_the_inventory(playbook: str) -> None:
    text = (PLAYBOOKS / playbook).read_text()
    assert "import_tasks: tasks/vm_names.yml" in text


def test_deploy_refuses_a_name_in_its_first_play() -> None:
    # Before the template play, whose build needs root SSH: tested against the fake above.
    first = yaml.safe_load((REPO / "deploy.yml").read_text())[0]
    assert [task["ansible.builtin.import_tasks"] for task in first["tasks"]] == ["playbooks/tasks/vm_names.yml"]
    assert first["tasks"][0]["vars"] == {"claude_vm_names_alias": True}
