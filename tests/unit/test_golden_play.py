"""playbooks/golden.yml and golden_build.yml against the fake Proxmox (docs/architecture/lifecycle.md §4.14): which
template each new VM clones, the build VM made and never left, the golden made, and the old goldens deleted. What
reaches the build VM is the image scenario's (molecule/image): claude_vm_golden_image false skips it here."""

from __future__ import annotations

import json

import pytest
import yaml
from harness import (
    PVE_SECRET,
    REPO,
    _failures,
    _fake_pve,
    _messages,
    _pve,
    _pve_requests,
    _pve_vars,
    _pve_vm,
    _results,
    _run,
)

from claude_vm import proxmox

GOLDEN = REPO / "playbooks" / "golden.yml"
BUILD = REPO / "playbooks" / "golden_build.yml"
PREFIX = "agent-warehouse-golden-"
BUILD_PREFIX = "agent-warehouse-build-"
# A play to run after both, which says what each new VM clones.
READ = [
    {
        "name": "Read what each VM clones",
        "hosts": "claude_vm_golden_pending",
        "gather_facts": False,
        "tasks": [
            {"name": "Say the template", "ansible.builtin.debug": {"msg": "chose {{ claude_vm_golden_template }}"}}
        ],
    }
]


@pytest.fixture
def fake(tmp_path):
    yield from _fake_pve(tmp_path)


def _golden(tmp_path, fake, *names, on=True, then=(), **extra):
    """Run golden.yml and golden_build.yml, as just deploy does around its VMs, for the names against the fake, and then
    the plays given; the report."""
    root = tmp_path / "run"
    _pve_vars(root, fake, {"proxmox_template_golden": on})
    playbook = root / "both.yml"
    playbook.write_text(
        yaml.safe_dump([{"ansible.builtin.import_playbook": str(each)} for each in (GOLDEN, BUILD)] + list(then))
    )
    args = {
        "vm_name": ",".join(names),
        "claude_vm_golden_image": False,
        "proxmox_template_build_agent_delay": 0.2,
        **extra,
    }
    report = _run(root, playbook, {}, "-e", json.dumps(args), timeout=240)
    assert report["rc"] == 0, (_failures(report), report["stderr"][-1500:])
    return report


def _vms(fake) -> list[dict]:
    return _pve(fake, "GET", "cluster/resources", type="vm")["data"]


def _config(fake, vmid) -> dict:
    return _pve(fake, "GET", f"nodes/pve/qemu/{vmid}/config")["data"]


def _choices(report) -> dict:
    return {
        host: result["ansible_facts"]["proxmox_template_golden_choice"]
        for name, host, result in _results(report)
        if name.endswith("golden_select | Choose the template") and "ansible_facts" in result
    }


def test_a_new_vm_with_none_of_its_key_gets_a_golden_built_and_no_build_vm_is_left(tmp_path, fake):
    report = _golden(tmp_path, fake, "alpha")
    (choice,) = _choices(report).values()
    assert choice["wanted"] and choice["golden"] == "" and choice["base"] == "9000"
    goldens = [vm for vm in _vms(fake) if vm.get("name", "").startswith(PREFIX)]
    assert [vm["name"] for vm in goldens] == [PREFIX + choice["key"][:12]]
    (made,) = goldens
    assert made["template"] == 1
    config = _config(fake, made["vmid"])
    # Nothing a clone would inherit and take as its own: no cloud-init user, no keys, no tag.
    assert not {"ciuser", "cipassword", "sshkeys", "tags", "nameserver", "searchdomain"} & set(config)
    record = proxmox.golden_read(config)
    assert record["key"] == choice["key"] and record["node"] == "pve" and record["disk"] == "12G"
    assert config["description"].count("\n") == 0 and proxmox._template_record(config) is None
    assert not [vm for vm in _vms(fake) if vm.get("name", "").startswith(BUILD_PREFIX)]


def test_the_build_vm_never_carries_the_ownership_tag(tmp_path, fake):
    _golden(tmp_path, fake, "alpha")
    tagged = [
        call
        for call in _pve_requests(tmp_path)
        if call["method"] in ("PUT", "POST") and "tags" in call["params"] and call["path"].endswith("/config")
    ]
    assert tagged and all(call["params"]["tags"] == "agent-warehouse-build" for call in tagged), tagged
    started = [
        call for call in _pve_requests(tmp_path) if call["path"].endswith("/config") and "description" in call["params"]
    ]
    assert any(proxmox.build_read(call["params"]["description"]) for call in started)
    # No password hash enters the disk.
    assert not [call for call in _pve_requests(tmp_path) if "cipassword" in call["params"]]


def test_a_second_vm_of_the_key_clones_the_golden_and_nothing_is_built(tmp_path, fake):
    _golden(tmp_path, fake, "alpha")
    (made,) = [vm for vm in _vms(fake) if vm.get("name", "").startswith(PREFIX)]
    report = _golden(tmp_path, fake, "beta")
    (choice,) = _choices(report).values()
    assert choice["golden"] == str(made["vmid"]) and not choice["wanted"]
    assert len([vm for vm in _vms(fake) if vm.get("name", "").startswith(PREFIX)]) == 1


def test_a_vm_that_exists_gets_none_and_nothing_is_built(tmp_path, fake):
    _pve_vm(fake, 9100, "alpha")
    report = _golden(tmp_path, fake, "alpha")
    assert _choices(report)["alpha"]["exists"] is True
    assert not [vm for vm in _vms(fake) if vm.get("name", "").startswith((PREFIX, BUILD_PREFIX))]


def test_off_nothing_is_chosen_or_built(tmp_path, fake):
    report = _golden(tmp_path, fake, "alpha", on=False)
    assert _choices(report) == {}
    assert not [vm for vm in _vms(fake) if vm.get("name", "").startswith((PREFIX, BUILD_PREFIX))]
    # Only reads reach Proxmox: collection lists, and finds nothing to delete.
    assert {call["method"] for call in _pve_requests(tmp_path)} <= {"GET"}


def test_a_build_of_the_key_another_run_has_under_way_leaves_the_vm_on_the_base(tmp_path, fake):
    first = _golden(tmp_path / "first", fake, "alpha")
    key = next(iter(_choices(first).values()))["key"]
    # Put the golden aside, and a build VM of that key, started just now, in its place.
    for vm in _vms(fake):
        if vm.get("name", "").startswith(PREFIX):
            _pve(fake, "DELETE", f"nodes/pve/qemu/{vm['vmid']}")
    _pve(fake, "POST", "nodes/pve/qemu/9000/clone", newid=9200, name=BUILD_PREFIX + key[:12])
    _pve(
        fake,
        "PUT",
        "nodes/pve/qemu/9200/config",
        tags="agent-warehouse-build",
        description=proxmox.build_record({"key": key, "started": _now()}),
    )
    report = _golden(tmp_path / "second", fake, "alpha")
    assert any("Another run is building" in str(result.get("msg", "")) for _, _, result in _results(report))
    assert [vm["vmid"] for vm in _vms(fake) if vm.get("name", "").startswith(BUILD_PREFIX)] == [9200]
    assert not [vm for vm in _vms(fake) if vm.get("name", "").startswith(PREFIX)]


def test_a_build_vm_a_run_cut_short_left_is_deleted_and_nothing_else(tmp_path, fake):
    _pve(fake, "POST", "nodes/pve/qemu/9000/clone", newid=9201, name=BUILD_PREFIX + "0" * 12)
    _pve(fake, "PUT", "nodes/pve/qemu/9201/config", tags="agent-warehouse-build", description="")
    # Named as one, but not tagged as one: someone's, never deleted.
    _pve(fake, "POST", "nodes/pve/qemu/9000/clone", newid=9202, name=BUILD_PREFIX + "1" * 12)
    _golden(tmp_path, fake, "alpha")
    ids = {vm["vmid"] for vm in _vms(fake)}
    assert 9201 not in ids and 9202 in ids


def test_a_build_of_another_key_another_run_has_under_way_is_left_alone(tmp_path, fake):
    other = "a" * 64
    _pve(fake, "POST", "nodes/pve/qemu/9000/clone", newid=9203, name=BUILD_PREFIX + other[:12])
    _pve(
        fake,
        "PUT",
        "nodes/pve/qemu/9203/config",
        tags="agent-warehouse-build",
        description=proxmox.build_record({"key": other, "started": _now()}),
    )
    report = _golden(tmp_path, fake, "alpha")
    assert 9203 in {vm["vmid"] for vm in _vms(fake)}
    (choice,) = _choices(report).values()
    # And this run still built its own.
    assert [vm["name"] for vm in _vms(fake) if vm.get("name", "").startswith(PREFIX)] == [PREFIX + choice["key"][:12]]


def test_a_vm_too_small_for_a_golden_clones_the_base_and_none_is_built(tmp_path, fake):
    report = _golden(tmp_path, fake, "alpha", vm_disk_size="8G")
    (choice,) = _choices(report).values()
    assert not choice["wanted"] and "smaller" in choice["said"]
    assert not [vm for vm in _vms(fake) if vm.get("name", "").startswith((PREFIX, BUILD_PREFIX))]


def test_a_base_with_a_larger_disk_keeps_it_and_is_never_shrunk(tmp_path, fake):
    _pve(fake, "PUT", "nodes/pve/qemu/9000/config", scsi0="local-lvm:base-9000-disk-0,discard=on,size=40G")
    _golden(tmp_path, fake, "alpha", vm_disk_size="64G")
    (made,) = [vm for vm in _vms(fake) if vm.get("name", "").startswith(PREFIX)]
    assert proxmox.golden_read(_config(fake, made["vmid"]))["disk"] == "40G"
    assert not [call for call in _pve_requests(tmp_path) if call["path"].endswith("/resize")]


def test_a_vm_that_finds_no_golden_clones_the_base_and_the_golden_is_the_next_vm_s(tmp_path, fake):
    report = _golden(tmp_path, fake, "alpha", then=READ)
    (made,) = [vm for vm in _vms(fake) if vm.get("name", "").startswith(PREFIX)]
    assert _messages(report, "Say the template") == {"alpha": "chose "}
    next_one = _golden(tmp_path / "next", fake, "beta", then=READ)
    assert _messages(next_one, "Say the template") == {"beta": f"chose {made['vmid']}"}


def test_a_template_step_that_fails_deletes_the_renamed_vm_and_says_so(tmp_path, fake):
    # By then golden_template has renamed the VM and deleted its build tag.
    _pve(fake, "PUT", "fake/refuse", suffix="/template")
    report = _golden(tmp_path, fake, "alpha")
    assert any("could not be made" in str(result.get("msg", "")) for _, _, result in _results(report))
    assert not [vm for vm in _vms(fake) if vm.get("name", "").startswith((PREFIX, BUILD_PREFIX))]


def test_a_build_that_fails_before_its_vm_is_tagged_deletes_it(tmp_path, fake):
    _pve(fake, "PUT", "fake/refuse", suffix="/config")
    report = _golden(tmp_path, fake, "alpha")
    assert any("could not be built" in str(result.get("msg", "")) for _, _, result in _results(report))
    assert not [vm for vm in _vms(fake) if vm.get("name", "").startswith((PREFIX, BUILD_PREFIX))]


def test_a_build_vm_left_over_is_deleted_with_golden_templates_off(tmp_path, fake):
    _pve(fake, "POST", "nodes/pve/qemu/9000/clone", newid=9204, name=BUILD_PREFIX + "2" * 12)
    _pve(fake, "PUT", "nodes/pve/qemu/9204/config", tags="agent-warehouse-build", description="")
    _golden(tmp_path, fake, "alpha", on=False)
    assert 9204 not in {vm["vmid"] for vm in _vms(fake)}


def test_a_cleanup_that_cannot_read_the_vm_says_so_and_does_not_forget_it_silently(tmp_path, fake):
    _pve(fake, "PUT", "fake/refuse", suffix="/template")
    _pve(fake, "PUT", "fake/refuse", suffix="/config", methods="GET")
    report = _golden(tmp_path, fake, "alpha")
    said = [str(result.get("msg", "")) for _, _, result in _results(report)]
    assert any("was not deleted: its config could not be read" in each for each in said), said


def test_a_golden_already_gone_when_it_is_deleted_fails_nothing(tmp_path, fake):
    # proxmox_kvm says of a VM already gone only changed=false, with no msg to read.
    old = proxmox.golden_record(
        {"key": "e" * 64, "build": 1, "base": "x", "node": "pve", "built": "2020-01-01T00:00:00Z", "disk": "12G"}
    )
    _pve(fake, "POST", "nodes/pve/qemu/9000/clone", newid=9300, name=PREFIX + "e" * 12)
    _pve(fake, "PUT", "nodes/pve/qemu/9300/config", description=old)
    _pve(fake, "POST", "nodes/pve/qemu/9300/template")
    _pve(fake, "PUT", "fake/vanish", vmid=9300)
    _golden(tmp_path, fake, "alpha", on=False)


def test_a_node_with_no_room_for_the_build_vm_builds_none_and_says_so(tmp_path, fake):
    _pve(fake, "PUT", "fake/privileges", token="ansible", path="/nodes/pve", privs="Sys.Audit")
    report = _golden(tmp_path, fake, "alpha", proxmox_template_golden_memory_mb=99999999)
    said = [str(result.get("msg", "")) for _, _, result in _results(report)]
    assert any("GiB free" in each for each in said), said
    assert not [vm for vm in _vms(fake) if vm.get("name", "").startswith((PREFIX, BUILD_PREFIX))]


def test_an_image_option_holding_a_vault_value_makes_no_golden_and_says_the_option_not_the_value(tmp_path, fake):
    report = _golden(tmp_path, fake, "alpha", common_extra_packages=[f"pkg-{PVE_SECRET}"], then=READ)
    assert _choices(report) == {}
    said = [str(result.get("msg", "")) for _, _, result in _results(report)]
    assert any("common.common_extra_packages holds a vault value" in each for each in said), said
    assert not any(PVE_SECRET in each for each in said)
    assert not [vm for vm in _vms(fake) if vm.get("name", "").startswith((PREFIX, BUILD_PREFIX))]


def test_only_provisioning_says_a_vm_was_cloned_from_a_golden():
    # Configure refuses a golden clone whose options differ. A set_fact of the name it reads would outrank
    # provisioning's add_host, and hold VMs that never touched a golden to that.
    written = [
        path.relative_to(REPO).as_posix()
        for path in [*(REPO / "playbooks").rglob("*.yml"), *(REPO / "roles").rglob("*.yml")]
        if "claude_vm_golden_cloned:" in path.read_text()
    ]
    assert written == ["playbooks/provision.yml"]


def test_goldens_past_the_collection_age_are_deleted_but_never_the_base_or_a_vm(tmp_path, fake):
    old = proxmox.golden_record(
        {"key": "e" * 64, "build": 1, "base": "x", "node": "pve", "built": "2020-01-01T00:00:00Z", "disk": "12G"}
    )
    _pve(fake, "POST", "nodes/pve/qemu/9000/clone", newid=9300, name=PREFIX + "e" * 12)
    _pve(fake, "PUT", "nodes/pve/qemu/9300/config", description=old)
    _pve(fake, "POST", "nodes/pve/qemu/9300/template")
    # A VM named as a golden, not a template, with no golden's record: no golden, never deleted.
    _pve(fake, "POST", "nodes/pve/qemu/9000/clone", newid=9301, name=PREFIX + "f" * 12)
    _pve(fake, "PUT", "nodes/pve/qemu/9301/config", description="mine")
    _golden(tmp_path, fake, "alpha", on=False)
    ids = {vm["vmid"] for vm in _vms(fake)}
    assert 9300 not in ids and {9000, 9301} <= ids


def test_a_build_that_fails_leaves_the_vms_on_the_base_and_deletes_what_it_made(tmp_path, fake):
    # An agent that never answers: the build VM never reports an address.
    report = _golden(tmp_path, fake, "alpha", proxmox_template_build_agent_retries=1)
    assert any("could not be built" in str(result.get("msg", "")) for _, _, result in _results(report))
    assert not [vm for vm in _vms(fake) if vm.get("name", "").startswith((PREFIX, BUILD_PREFIX))]


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
