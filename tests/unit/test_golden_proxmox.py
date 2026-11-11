"""The Proxmox side of a golden template: its record, its base's identity, which golden a VM clones, which build VMs
are left over, and which goldens are old enough to delete (lib/claude_vm/proxmox.py; docs/architecture/lifecycle.md
§4.14). The playbook that applies them reaches Proxmox only through proxmox_template's entry points."""

from __future__ import annotations

import re

import pytest
import yaml
from harness import REPO

from claude_vm import golden, proxmox

NOW = "2026-11-09T12:00:00Z"
DAY = 86400
KEY = "a" * 64
DIGEST = "d" * 64


def _golden(vmid, key=KEY, node="pve", built="2026-11-08T12:00:00Z", disk="12G", name=None):
    record = proxmox.golden_record(
        {"key": key, "build": 1, "base": "9000:sha:dig", "node": node, "built": built, "disk": disk}
    )
    return {
        "vmid": str(vmid),
        "node": node,
        "name": name or f"agent-warehouse-golden-{key[:12]}",
        "config": {"description": record},
    }


def _listing(*entries):
    return [{"type": "qemu", "template": 0, "node": "pve", **entry} for entry in entries]


BASE_CONFIG = {
    "description": proxmox.proxmox_template_record({"build": 1, "image": "u", "packages": "q", "sha256": "abc"}),
    "digest": "f00d",
}


def test_a_golden_s_record_reads_back_and_is_never_a_base_template_s():
    golden_config = {"description": _golden(1)["config"]["description"]}
    assert proxmox.golden_read(golden_config)["key"] == KEY
    assert proxmox._template_record(golden_config) is None
    assert proxmox.golden_read(BASE_CONFIG) is None


def test_a_build_vm_s_record_reads_back():
    notes = proxmox.build_record({"key": KEY, "started": NOW})
    assert proxmox.build_read(notes) == {"key": KEY, "started": NOW}


def test_a_base_s_identity_is_its_vmid_its_image_and_its_config_digest():
    assert proxmox.golden_base(BASE_CONFIG, 9000) == "9000:abc:f00d"
    # A rebuilt base: a new digest, so a new key, so not the old golden.
    assert proxmox.golden_base({**BASE_CONFIG, "digest": "beef"}, 9000) != proxmox.golden_base(BASE_CONFIG, 9000)
    assert proxmox.golden_base({"description": BASE_CONFIG["description"]}, 9000) == ""


def test_the_newest_fresh_golden_of_the_key_on_the_node_is_picked():
    goldens = [_golden(101, built="2026-11-07T12:00:00Z"), _golden(102), _golden(103, key="b" * 64)]
    assert proxmox.golden_pick(goldens, KEY, "pve", NOW, 7 * DAY, "32G") == "102"


@pytest.mark.parametrize(
    ("golden_args", "disk"),
    [
        ({"node": "pve2"}, "32G"),
        ({"built": "2026-11-01T11:00:00Z"}, "32G"),
        ({"key": "b" * 64}, "32G"),
        ({"disk": "40G"}, "32G"),
        ({"built": "not a time"}, "32G"),
    ],
)
def test_no_golden_is_picked_on_another_node_too_old_of_another_key_or_with_a_larger_disk(golden_args, disk):
    assert proxmox.golden_pick([_golden(101, **golden_args)], KEY, "pve", NOW, 7 * DAY, disk) == ""


def test_goldens_past_the_collection_age_are_collected():
    goldens = [_golden(101, built="2026-10-20T12:00:00Z"), _golden(102), _golden(103, built="2026-10-01T12:00:00Z")]
    assert proxmox.golden_collectable(goldens, NOW, 14 * DAY) == ["101", "103"]
    # A template named as a golden with no record is never collected: nothing says how old it is.
    assert proxmox.golden_collectable([{"vmid": "104", "config": {"description": "mine"}}], NOW, 14 * DAY) == []


def test_a_fresh_build_of_the_key_is_another_run_s_and_every_other_is_left_over():
    builds = [
        {
            "vmid": "201",
            "config": {"description": proxmox.build_record({"key": KEY, "started": "2026-11-09T11:50:00Z"})},
        },
        {
            "vmid": "202",
            "config": {"description": proxmox.build_record({"key": KEY, "started": "2026-11-09T10:00:00Z"})},
        },
        {"vmid": "203", "config": {"description": proxmox.build_record({"key": "b" * 64, "started": NOW})}},
        {"vmid": "204", "config": {"description": ""}},
    ]
    for build in builds:
        build["config"] |= {"name": "agent-warehouse-build-aaaa", "tags": "agent-warehouse-build"}
    assert _leftovers(builds) == {"busy": True, "stale": ["202", "204"]}


def _leftovers(builds, *configs):
    return proxmox.build_leftovers(
        builds, KEY, NOW, 1800, "agent-warehouse-build-", "agent-warehouse-golden-", "agent-warehouse-build", *configs
    )


OLD = "2026-11-09T10:00:00Z"


@pytest.mark.parametrize(
    ("config", "stale"),
    [
        # golden_template deleted its tag, then a run was cut short.
        ({"name": "agent-warehouse-build-a", "description": proxmox.build_record({"key": "x", "started": OLD})}, True),
        # Renamed and noted, never converted.
        (
            {
                "name": "agent-warehouse-golden-a",
                "description": proxmox.golden_record({"key": "x", "built": OLD}),
            },
            True,
        ),
        # The same, moments ago: another run is at it.
        (
            {
                "name": "agent-warehouse-golden-a",
                "description": proxmox.golden_record({"key": "x", "built": NOW}),
            },
            False,
        ),
        # The listing still showed a build VM, and its own config says it is a template by now.
        ({"name": "agent-warehouse-golden-a", "template": 1, "description": ""}, False),
        # Named as one and nothing else says so: someone's.
        ({"name": "agent-warehouse-build-a", "description": ""}, False),
        ({"name": "agent-warehouse-golden-a", "description": ""}, False),
        ({"name": "someones-vm", "tags": "agent-warehouse-build", "description": ""}, False),
    ],
)
def test_a_left_over_vm_is_told_from_its_own_config(config, stale):
    assert _leftovers([{"vmid": "201", "config": config}]) == {"busy": False, "stale": ["201"] if stale else []}


def test_a_build_vm_whose_notes_could_not_be_read_is_neither_busy_nor_left_over():
    reads = [{"item": {"vmid": "201"}, "failed": True}]
    assert _leftovers([{"vmid": "201"}], reads) == {"busy": False, "stale": []}


@pytest.mark.parametrize(
    ("config", "ours"),
    [
        ({"name": "agent-warehouse-build-aaaa", "tags": "agent-warehouse-build"}, True),
        # Before golden_build tags it, and after golden_template deletes the tag and renames it.
        ({"name": "agent-warehouse-build-aaaa"}, True),
        ({"name": "agent-warehouse-golden-aaaa"}, True),
        ({"name": "agent-warehouse-golden-aaaa", "template": 1}, False),
        ({"name": "someones-vm"}, False),
        ({}, False),
    ],
)
def test_a_rescue_deletes_only_a_vm_named_for_a_build_or_as_a_golden_and_never_a_template(config, ours):
    assert proxmox.build_vm_ours(config, ["agent-warehouse-build-", "agent-warehouse-golden-"]) is ours


def test_build_vms_are_those_named_for_a_build_or_as_a_golden_and_never_a_template():
    listing = _listing(
        {"vmid": 301, "name": "agent-warehouse-build-aaaa"},
        {"vmid": 302, "name": "agent-warehouse-golden-bbbb"},
        {"vmid": 303, "name": "someones-vm"},
        {"vmid": 304, "name": "agent-warehouse-build-cccc", "template": 1},
        {"vmid": 305, "name": "agent-warehouse-build-dddd", "node": "pve2"},
    )
    found = proxmox.build_vms(listing, "pve", ["agent-warehouse-build-", "agent-warehouse-golden-"])
    assert [vm["vmid"] for vm in found] == ["301", "302"]


def test_the_build_tag_is_never_the_ownership_tag():
    # Discovery, configure, destroy and the aliases find VMs by the ownership tag, matched whole: a build VM is none.
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    pattern = re.compile(r"(^|[;,])(agent-warehouse|claude-on-proxmox)([;,]|$)")
    assert "claude_vm_tag_pattern" in defaults
    for tags in ("agent-warehouse-build", "x;agent-warehouse-build", "agent-warehouse-build,y"):
        assert not pattern.search(tags)
    vars_main = yaml.safe_load((REPO / "roles" / "proxmox_template" / "vars" / "main.yml").read_text())
    assert vars_main["proxmox_template_build_tag"] == "{{ claude_vm_tag | lower }}-build"


def _choice(listing, configs, **kwargs):
    args = {
        "vm": "alpha",
        "base": "base",
        "node": "pve",
        "prefix": "agent-warehouse-golden-",
        "now": NOW,
        "max_age": 7 * DAY,
        "disk": "32G",
        "boot_disk": "scsi0",
        "least": "12G",
        "digest": DIGEST,
        "build": 1,
        "shared": True,
    }
    args.update(kwargs)
    return proxmox.golden_choice(listing, configs, **args)


BASE = {"vmid": 9000, "name": "base", "template": 1}


def test_a_vm_that_exists_gets_no_golden():
    choice = _choice(_listing(BASE, {"vmid": 100, "name": "alpha"}), {"9000": BASE_CONFIG})
    assert choice["exists"] and not choice["wanted"] and choice["golden"] == ""


def test_a_new_vm_with_no_golden_wants_one_of_its_key():
    choice = _choice(_listing(BASE), {"9000": BASE_CONFIG})
    key = golden.golden_key(1, "9000:abc:f00d", DIGEST)
    assert choice == {
        "exists": False,
        "base": "9000",
        "key": key,
        "disk": "12G",
        "golden": "",
        "golden_name": "",
        "wanted": True,
        "said": "",
    }


def test_a_new_vm_clones_a_fresh_golden_of_its_key():
    key = golden.golden_key(1, "9000:abc:f00d", DIGEST)
    made = _golden(150, key=key)
    listing = _listing(BASE, {"vmid": 150, "name": made["name"], "template": 1})
    choice = _choice(listing, {"9000": BASE_CONFIG, "150": made["config"]})
    assert choice["golden"] == "150" and choice["golden_name"] == made["name"] and not choice["wanted"]


def test_the_reads_a_loop_registered_are_taken_as_configs():
    reads = [{"item": {"vmid": "9000"}, "json": {"data": BASE_CONFIG}}, {"item": {"vmid": "1"}, "failed": True}]
    assert proxmox.read_configs(reads) == {"9000": BASE_CONFIG}
    assert _choice(_listing(BASE), reads)["wanted"] is True


def test_a_vm_with_an_image_option_of_its_own_clones_the_base():
    choice = _choice(_listing(BASE), {"9000": BASE_CONFIG}, shared=False)
    assert not choice["wanted"] and "an image option of its own" in choice["said"]


def test_a_vm_with_no_base_template_or_no_digest_gets_none():
    assert "not on pve once" in _choice(_listing(), {})["said"]
    assert "could not be read" in _choice(_listing(BASE), {"9000": {"description": "x"}})["said"]


def test_a_vm_whose_disk_is_smaller_than_a_golden_s_clones_the_base_and_wants_none():
    choice = _choice(_listing(BASE), {"9000": BASE_CONFIG}, disk="8G")
    assert not choice["wanted"] and choice["golden"] == "" and "smaller" in choice["said"]


@pytest.mark.parametrize(
    ("boot_disk", "disk"),
    [
        ("local-lvm:base-9000-disk-0,discard=on,size=3584M", "12G"),
        ("local-lvm:base-9000-disk-0,discard=on,size=12288M", "12288M"),
        ("local-lvm:base-9000-disk-0,discard=on,size=40G", "40G"),
        ("local-lvm:base-9000-disk-0,discard=on", "12G"),
    ],
)
def test_a_golden_is_built_at_the_least_size_or_at_a_larger_base_s_own(boot_disk, disk):
    choice = _choice(_listing(BASE), {"9000": {**BASE_CONFIG, "scsi0": boot_disk}}, disk="64G")
    assert choice["wanted"] and choice["disk"] == disk


def test_a_vm_smaller_than_a_larger_base_s_disk_wants_no_golden():
    config = {**BASE_CONFIG, "scsi0": "local-lvm:base-9000-disk-0,size=40G"}
    choice = _choice(_listing(BASE), {"9000": config}, disk="32G")
    assert not choice["wanted"] and "40G" in choice["said"]


def test_the_first_vm_that_wants_a_golden_names_what_is_built():
    choices = {
        "a": {"wanted": False, "key": "k1", "base": "9000", "disk": "12G"},
        "b": {"wanted": True, "key": "k2", "base": "9000", "disk": "12G"},
        "c": {"wanted": True, "key": "k2", "base": "9000", "disk": "12G"},
    }
    assert proxmox.golden_targets(choices) == {"key": "k2", "base": "9000", "disk": "12G", "vms": ["b", "c"]}
    assert proxmox.golden_targets({"a": choices["a"]}) == {}


def test_only_goldens_on_the_node_by_name_are_collection_candidates():
    listing = _listing(
        {"vmid": 150, "name": "agent-warehouse-golden-aaaa", "template": 1},
        {"vmid": 151, "name": "agent-warehouse-golden-bbbb", "template": 0},
        {"vmid": 152, "name": "base", "template": 1},
        {"vmid": 153, "name": "agent-warehouse-golden-cccc", "template": 1, "node": "pve2"},
    )
    assert [g["vmid"] for g in proxmox.golden_templates(listing, "pve", "agent-warehouse-golden-")] == ["150"]


def test_a_template_carrying_the_ownership_tag_is_no_vm_of_the_fleet():
    listing = _listing(
        {"vmid": 1, "name": "a"}, {"vmid": 2, "name": "t", "template": 1}, {"vmid": 3, "name": "b", "template": "0"}
    )
    assert [guest["vmid"] for guest in proxmox.not_templates(listing)] == [1, 3]


def _custom(config, entry=None, **kwargs):
    entry = {"vmid": 500, "name": "mine", "template": 1, "node": "pve", "type": "qemu", **(entry or {})}
    args = {"name": "mine", "node": "pve", "disk": "scsi0", "nic": "net0", "owned": ["agent-warehouse"], **kwargs}
    return proxmox.custom_template_problems([entry], {"500": config}, **args)


GOOD_TEMPLATE = {
    "ide2": "local-lvm:vm-500-cloudinit,media=cdrom",
    "agent": "enabled=1",
    "net0": "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0",
    "scsi0": "local-lvm:base-500-disk-0,size=8G",
    "cpu": "host",
    "digest": "abc",
}


def test_a_template_with_all_a_clone_needs_passes():
    assert _custom(GOOD_TEMPLATE) == []
    assert _custom({**GOOD_TEMPLATE, "cpu": "x86-64-v3"}) == []


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"ide2": "none,media=cdrom"}, "no cloud-init drive"),
        ({"agent": "0"}, "guest agent is off"),
        ({"cpu": "kvm64"}, "no AVX2"),
        ({"cpu": "x86-64-v2-AES"}, "no AVX2"),
        ({"ciuser": "dev"}, "cloud-init user"),
        ({"tags": "agent-warehouse-unfinished;mine"}, "this project's tags"),
        ({"tags": "agent-warehouse"}, "this project's tags"),
    ],
)
def test_each_thing_a_template_lacks_is_said_with_its_fix(change, said):
    (problem,) = _custom({**GOOD_TEMPLATE, **change})
    assert said in problem


def test_a_template_with_no_cpu_type_has_no_avx2():
    template = {k: v for k, v in GOOD_TEMPLATE.items() if k != "cpu"}
    assert "no AVX2" in _custom(template)[0]


@pytest.mark.parametrize("cpu", ["kvm64", "Nehalem-IBRS", "SandyBridge", "Opteron_G5", "x86-64-v2-AES", ""])
def test_a_template_whose_cpu_has_no_avx2_is_refused(cpu):
    assert any("AVX2" in problem for problem in _custom({**GOOD_TEMPLATE, "cpu": cpu}))


def test_a_vm_that_shares_the_template_s_name_is_refused():
    listing = _listing({"vmid": 500, "name": "mine", "template": 1}, {"vmid": 501, "name": "mine"})
    (problem,) = proxmox.custom_template_problems(
        listing, {"500": GOOD_TEMPLATE}, "mine", "pve", "scsi0", "net0", ["agent-warehouse"]
    )
    assert "2 VMs and templates" in problem


def test_a_template_with_another_boot_disk_or_nic_names_the_setting():
    assert "proxmox_vm_disk" in _custom(GOOD_TEMPLATE, disk="virtio0")[0]
    assert "proxmox_nic" in _custom(GOOD_TEMPLATE, nic="net1")[0]


def test_a_tag_that_only_resembles_the_project_s_is_the_user_s():
    assert _custom({**GOOD_TEMPLATE, "tags": "not-agent-warehouse;agent-warehousex"}) == []


def test_a_missing_template_a_vm_of_that_name_another_node_and_two_of_a_name_are_said():
    assert "no template named" in proxmox.custom_template_problems([], {}, "mine", "pve", "scsi0", "net0", [])[0]
    assert "qm template 500" in _custom(GOOD_TEMPLATE, entry={"template": 0})[0]
    assert "on node pve2" in _custom(GOOD_TEMPLATE, entry={"node": "pve2"})[0]
    two = [{"vmid": n, "name": "mine", "template": 1, "node": "pve", "type": "qemu"} for n in (500, 501)]
    assert "2 VMs and templates" in proxmox.custom_template_problems(two, {}, "mine", "pve", "scsi0", "net0", [])[0]
