"""A VM's own list of repositories - just deploy alpha --repos a,b - in place of the github_projects setting.

The list reaches the role as vm_repos and is written as one line of the VM's notes in Proxmox, where the VM cannot
change it; both plays that publish a VM read it from there. Another --repos adds to it, a VM with no list is refused
one, and a repair keeps it. What the github_projects role then clones is test_github_auth.py's. The runs go against the
fake Proxmox (tests/molecule/fake_pve_api.py), as test_vm_size.py's do.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from harness import _failures, _pve, _results, fake_fleet
from test_justfile import REPO, _just, _output, _playbook, _recording
from test_status import _status_run
from test_vm_names import FROM_THE_HOST, PLAYBOOKS
from test_vm_names import _playbook as _run_playbook

from claude_vm import proxmox
from claude_vm.tui.status import WIDTH, status_text

CHECK = REPO / "tools" / "check"
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyDoNotUse test"
ADD = "present | Add to the VM's own list of repositories"
READ = "present | Read the notes the new VM was cloned with"
MARK = proxmox.VM_REPOS_RECORD


@pytest.fixture
def fleet(tmp_path):
    yield from fake_fleet(tmp_path)


# --------------------------------------------------------------- the recipe's option ---
@pytest.mark.parametrize(
    ("given", "names"),
    [("one", ["one"]), ("one,two.2,three-3", ["one", "two.2", "three-3"]), ("_x,A9", ["_x", "A9"])],
)
def test_deploy_passes_the_list_as_json(tmp_path: Path, given: str, names: list[str]) -> None:
    run = _playbook(tmp_path, "deploy", "alpha", "--repos", given)
    assert json.loads(run[run.index('{"vm_name": "alpha"}') + 2]) == {"vm_repos": names}, run


@pytest.mark.parametrize(
    "args",
    [
        ["alpha", "--repos", "a", "--check"],
        ["alpha", "--repos", "a", "--tags", "claude_code"],
        ["alpha", "--repos", "a", "--only", "configure"],
        ["--repos", "a", "--only", "template"],
    ],
)
def test_a_list_is_refused_where_no_vm_is_provisioned(tmp_path: Path, args: list[str]) -> None:
    result = _just("deploy", *args, settings=_recording(tmp_path))
    assert result.returncode != 0 and "--repos gives a VM its list" in result.stderr, _output(result)


@pytest.mark.parametrize("given", ["owner/name", ".claude", "-x", "a,,b", ",a", "a,", "a b", "a" * 101])
def test_just_refuses_what_is_not_a_list_of_names(tmp_path: Path, given: str) -> None:
    result = _just("deploy", "alpha", f"--repos={given}", settings=_recording(tmp_path))
    assert result.returncode != 0, _output(result)


@pytest.mark.parametrize(
    ("given", "ok"),
    [
        ("", True),
        ("one", True),
        ("one,two.2,_three-3", True),
        ("a" * 100, True),
        ("a" * 101, False),
        (",".join(["a" * 100] * 20), False),
        ("owner/name", False),
        (".claude", False),
        ("-x", False),
        ("a,,b", False),
        (",a", False),
        ("a,", False),
        ("a b", False),
        ('a", "b', False),
        ("a\nb", False),
    ],
)
def test_tools_check_holds_the_list_again_before_it_goes_into_json(given: str, ok: bool) -> None:
    # just holds it to its pattern first; this is what deploy's script runs before the JSON, as names has.
    result = subprocess.run([str(CHECK), "repos", given], capture_output=True, text=True)
    assert (result.returncode == 0) == ok, result.stderr


# ------------------------------------------------------------------------- the notes ---
RECORD = "Built by agent-warehouse: build=1; image=x"


def test_a_vm_with_no_line_has_no_list_and_notes_are_read_as_they_are() -> None:
    assert proxmox.proxmox_notes_repos(RECORD) == {"own": False, "names": []}
    assert proxmox.proxmox_notes_repos(None) == {"own": False, "names": []}
    # Said of a person's notes, a line that only mentions the marker further along is not the record.
    assert proxmox.proxmox_notes_repos(f"see {MARK} a") == {"own": False, "names": []}


def test_the_line_is_read_wherever_it_is_and_this_run_s_names_join_it() -> None:
    notes = f"mine\n{MARK} one,two\n{RECORD}"
    assert proxmox.proxmox_notes_repos(notes) == {"own": True, "names": ["one", "two"]}
    assert proxmox.proxmox_notes_repos(notes, ["two", "three"])["names"] == ["one", "two", "three"]
    assert proxmox.proxmox_notes_repos(RECORD, ["one"]) == {"own": True, "names": ["one"]}


def test_a_mangled_line_is_still_the_vm_s_list_and_keeps_only_what_is_a_name() -> None:
    assert proxmox.proxmox_notes_repos(f"{MARK} ../x, ok ,.claude") == {"own": True, "names": ["ok"]}
    assert proxmox.proxmox_notes_repos(f"{MARK} /etc") == {"own": True, "names": []}


def test_writing_the_list_changes_that_line_alone() -> None:
    assert proxmox.proxmox_notes_with_repos(RECORD, ["one"]) == f"{RECORD}\n{MARK} one"
    assert proxmox.proxmox_notes_with_repos("", ["one"]) == f"{MARK} one"
    assert proxmox.proxmox_notes_with_repos(f"mine\n{MARK} one\n{RECORD}", ["two"]) == f"mine\n{MARK} one,two\n{RECORD}"


def _reply(data) -> dict:
    return {"json": {"data": data}}


def test_guest_addresses_carries_a_vm_s_list_and_only_where_it_has_one() -> None:
    net0 = "virtio=BC:24:11:0E:72:04,bridge=vmbr0"
    agent = _reply(
        {
            "result": [
                {
                    "hardware-address": "bc:24:11:0e:72:04",
                    "ip-addresses": [{"ip-address-type": "ipv4", "ip-address": "192.0.2.51"}],
                }
            ]
        }
    )
    listed = {"net0": net0, "description": f"{MARK} one"}
    vm = {"name": "a"}
    assert proxmox.guest_addresses([vm], [_reply(listed)], [agent])[0]["repos"] == {"own": True, "names": ["one"]}
    assert "repos" not in proxmox.guest_addresses([vm], [_reply({"net0": net0})], [agent])[0]
    # A config that could not be read gives no address, so the VM is not published, and no fleet's list can be cloned.
    row = proxmox.guest_addresses([vm], [{"skipped": True}], [agent])[0]
    assert row["address"] == "" and "repos" not in row


# ---------------------------------------------------------------------- on the fake ---
def _provision(tmp_path: Path, base: str, name: str, repos: list[str] | None = None, **extra) -> dict:
    settings = {**FROM_THE_HOST, "vm_ssh_public_keys": [KEY], **extra.pop("settings", {})}
    given = {"vm_name": name, "deploy_sign_in": False, **({"vm_repos": repos} if repos else {}), **extra}
    return _run_playbook(tmp_path, base, PLAYBOOKS / "provision.yml", "-e", json.dumps(given), settings=settings)


def _notes(base: str, name: str) -> str:
    listed = next(vm for vm in _pve(base, "GET", "cluster/resources")["data"] if vm.get("name") == name)
    return _pve(base, "GET", f"nodes/{listed['node']}/qemu/{listed['vmid']}/config")["data"].get("description", "")


def _line(base: str, name: str) -> list[str]:
    return [line for line in _notes(base, name).splitlines() if line.startswith(MARK)]


def _ran(report: dict, name: str, vm: str) -> dict:
    return next((result for task, host, result in _results(report) if task.endswith(name) and host == vm), {})


def _published(report: dict, task: str, name: str) -> dict:
    for ran, _, result in _results(report):
        if ran.endswith(task):
            for each in result.get("results") or [result]:
                added = each.get("add_host") or {}
                if added.get("host_name") == name:
                    return added["host_vars"]["claude_vm_repos"]
    raise AssertionError(f"{name} was not published by {task}")


PROVISION_PUBLISH = "Add each VM to the claude_vms group"


def test_a_vm_is_created_with_its_list_beside_the_notes_it_was_cloned_with(tmp_path: Path, fleet: str) -> None:
    report = _provision(tmp_path, fleet, "listed", ["one", "two"])
    assert report["rc"] == 0, _failures(report)
    assert _line(fleet, "listed") == [f"{MARK} one,two"]
    assert "Built by" in _notes(fleet, "listed")
    assert _published(report, PROVISION_PUBLISH, "listed") == {"own": True, "names": ["one", "two"]}


GOLDEN_NAME = "agent-warehouse-golden-" + "a" * 12


def _a_golden(fleet: str) -> str:
    """A golden template at 9200, as golden_template leaves one; its record."""
    record = proxmox.golden_record(
        {"key": "a" * 64, "build": 1, "base": "x", "node": "pve", "built": "2026-11-10T00:00:00Z", "disk": "12G"}
    )
    _pve(fleet, "POST", "nodes/pve/qemu/9000/clone", newid=9200, name=GOLDEN_NAME)
    _pve(fleet, "PUT", "nodes/pve/qemu/9200/config", description=record)
    _pve(fleet, "POST", "nodes/pve/qemu/9200/template")
    return record


def test_a_golden_s_clone_gets_its_list_beside_the_golden_s_record(tmp_path: Path, fleet: str) -> None:
    # The notes a golden's clone carries are the golden's record, as a base's clone carries its build record.
    record = _a_golden(fleet)
    report = _provision(tmp_path, fleet, "listed", ["one"], claude_vm_golden_template="9200")
    assert report["rc"] == 0, _failures(report)
    assert _notes(fleet, "listed").splitlines() == [record, f"{MARK} one"]
    assert _published(report, PROVISION_PUBLISH, "listed") == {"own": True, "names": ["one"]}


def test_a_golden_s_clone_is_said_with_the_golden_s_name(tmp_path: Path, fleet: str) -> None:
    # Cloned by its VMID, a golden lent the clone task no name of its own, and the run said the base's.
    _a_golden(fleet)
    choice = {"golden": "9200", "golden_name": GOLDEN_NAME}
    report = _provision(tmp_path, fleet, "golden", proxmox_template_golden_choice=choice)
    assert report["rc"] == 0, _failures(report)
    (cloned,) = [task for task, vm, _ in _results(report) if vm == "golden" and "| Clone from" in task]
    assert cloned.endswith(f"present | Clone from template {GOLDEN_NAME}"), cloned
    # With none chosen, the base's.
    base = _provision(tmp_path, fleet, "base")
    assert base["rc"] == 0, _failures(base)
    (cloned,) = [task for task, vm, _ in _results(base) if vm == "base" and "| Clone from" in task]
    assert cloned.endswith("present | Clone from template ubuntu-24.04-cloudinit"), cloned


def test_the_same_list_again_changes_nothing_and_a_new_name_is_added(tmp_path: Path, fleet: str) -> None:
    assert _provision(tmp_path, fleet, "listed", ["one", "two"])["rc"] == 0
    again = _provision(tmp_path, fleet, "listed", ["two", "one"])
    assert again["rc"] == 0, _failures(again)
    assert _ran(again, ADD, "listed").get("skipped") is True
    assert _line(fleet, "listed") == [f"{MARK} one,two"]
    more = _provision(tmp_path, fleet, "listed", ["three"])
    assert more["rc"] == 0, _failures(more)
    assert _line(fleet, "listed") == [f"{MARK} one,two,three"]
    assert _published(more, PROVISION_PUBLISH, "listed")["names"] == ["one", "two", "three"]


def test_without_a_list_a_vm_s_notes_are_left_alone_and_read_no_sooner(tmp_path: Path, fleet: str) -> None:
    report = _provision(tmp_path, fleet, "plain")
    assert report["rc"] == 0, _failures(report)
    assert _line(fleet, "plain") == [] and _ran(report, READ, "plain").get("skipped") is True
    assert _published(report, PROVISION_PUBLISH, "plain") == {"own": False, "names": []}


def test_a_finished_vm_with_no_list_is_refused_one(tmp_path: Path, fleet: str) -> None:
    assert _provision(tmp_path, fleet, "plain")["rc"] == 0
    before = _notes(fleet, "plain")
    report = _provision(tmp_path, fleet, "plain", ["one"])
    assert report["rc"] != 0
    said = " ".join(_failures(report).values())
    assert "plain exists and follows the github_projects setting" in said, said
    assert _notes(fleet, "plain") == before


def test_a_repair_keeps_a_vm_s_list(tmp_path: Path, fleet: str) -> None:
    assert _provision(tmp_path, fleet, "repaired", ["one"])["rc"] == 0
    report = _provision(tmp_path, fleet, "repaired", proxmox_vm_force_update=True)
    assert report["rc"] == 0, _failures(report)
    assert _line(fleet, "repaired") == [f"{MARK} one"]
    assert _published(report, PROVISION_PUBLISH, "repaired") == {"own": True, "names": ["one"]}


def test_a_run_by_hand_is_held_to_repository_names_before_anything_is_created(tmp_path: Path, fleet: str) -> None:
    for bad in (["a/b"], [".claude"], ["one\nFAKE"]):
        assert _provision(tmp_path, fleet, "held", bad)["rc"] != 0, bad
    assert not [vm for vm in _pve(fleet, "GET", "cluster/resources")["data"] if vm.get("name") == "held"]


def test_discovery_publishes_a_vm_with_the_list_in_its_notes(tmp_path: Path, fleet: str) -> None:
    assert _provision(tmp_path, fleet, "listed", ["one"])["rc"] == 0
    assert _provision(tmp_path, fleet, "plain")["rc"] == 0
    given = json.dumps({"vm_name": "listed,plain"})
    report = _run_playbook(tmp_path, fleet, PLAYBOOKS / "discover.yml", "-e", given, settings=FROM_THE_HOST)
    assert report["rc"] == 0, _failures(report)
    task = "Add the reachable ones to the inventory"
    assert _published(report, task, "listed") == {"own": True, "names": ["one"]}
    assert _published(report, task, "plain") == {}


# ------------------------------------------------------------------------- the report ---
def test_just_status_says_a_vm_s_own_list_and_keeps_its_notes_out_of_the_report(tmp_path: Path, fleet: str) -> None:
    assert _provision(tmp_path, fleet, "listed", ["one", "two"])["rc"] == 0
    assert _provision(tmp_path, fleet, "plain")["rc"] == 0
    listed = next(vm for vm in _pve(fleet, "GET", "cluster/resources")["data"] if vm.get("name") == "listed")
    path = f"nodes/{listed['node']}/qemu/{listed['vmid']}/config"
    notes = _pve(fleet, "GET", path)["data"]["description"]
    _pve(fleet, "PUT", path, description=f"{notes}\na person's private note")
    report, model = _status_run(tmp_path / "status", fleet, "-e", json.dumps({"status_only": "vms"}))
    assert report["rc"] in (0, 1, 2), report["stderr"][-2000:]
    by = {vm["name"]: vm for vm in model["vms"]}
    assert by["listed"]["own_repos"] == ["one", "two"] and by["plain"]["own_repos"] is None
    assert "private note" not in json.dumps(model)


def _work_report(own: list[str] | None, there: list[str]) -> str:
    work = {"dir": "~/projects", "checkouts": len(there), "repositories": [{"name": name} for name in there]}
    vm = {"name": "alpha", "needs_ssh": True, "own_repos": own, "ssh": {"work": work}}
    return status_text(
        {
            "title": "agent-warehouse",
            "proxmox": {"nodes": ["pve"]},
            "settings": {},
            "vms": [vm],
            "sections": ["work"],
            "attention": [],
            "counts": {"problem": 0, "unread": 0, "todo": 0},
            "exit": 0,
        }
    )


def test_the_work_section_says_a_vm_s_list_and_what_of_it_has_no_checkout() -> None:
    assert "its own list: one, two; no checkout of two" in _work_report(["one", "two"], ["one"])
    assert "its own list: one\n" in _work_report(["one"], ["one", "older"]) + "\n"
    assert "its own list" not in _work_report(None, ["one"])


def test_a_long_list_wraps_inside_the_report() -> None:
    text = _work_report([f"repository-{n}" for n in range(40)], [])
    assert all(len(line) <= WIDTH for line in text.splitlines()), text
    assert "repository-39" in text
