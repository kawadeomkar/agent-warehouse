"""What is the fleet's (lib/claude_vm/pve/ownership.py; docs/architecture/engine.md §16.5), and where a host is opened
(lib/claude_vm/fleet/opened.py, §16.9).

The one check is a pure function of what was read of a member of the pool, whichever reader read it: engine/fleet.py's,
whose cases against the fake are tests/unit/test_aw_fleet.py's, and status's (card G16), which reads the record by
pve/record.py's plain parse. Here: each refusal on what was read, the same for a record's fields as parse gives them
and as the engine's Record renders them; the first form of a clone, owned only with the job it names; an Owned made by
owned() alone; and the rule with a test of its own, that the module the one check lives in, and the one a host is
opened by, import nothing of what status may not (card G15's boundary: the engine, pydantic, Ansible, Jinja2, the MCP
SDK), so that status judges by the same rule and opens a host the same way.
"""

from __future__ import annotations

import copy
import dataclasses

import pytest
from aw_kit import imports_of

from claude_vm.engine.record import Record
from claude_vm.pve import ownership
from claude_vm.pve import record as block
from claude_vm.pve.ownership import Member, NotOwned, Owned, owned, refusal

UUID = "5b0c1d2e-3f40-4a5b-8c6d-7e8f90a1b2c3"
OTHER = "0f0e0d0c-0b0a-4908-8706-050403020100"
DIGEST = "a" * 40
JOB = "2b3c4d5e6f708192"
# What status may not import (card G15's boundary, tests/unit/test_aw_cli_imports.py's HEAVY).
HEAVY = ("pydantic", "pydantic_core", "ansible", "jinja2", "mcp")


def _alpha(**read) -> Member:
    """alpha as a reader read it: a VM of the fleet's, its record naming its own UUID, unless read says otherwise."""
    given = {"uuid": UUID, "digest": DIGEST, "recorded": "vm", "record_uuid": UUID, "stage": "finished"}
    return Member("alpha", "pve", 9010, **(given | read))


def test_a_member_the_fleet_made_is_owned_with_what_the_decision_was_made_on() -> None:
    assert refusal(_alpha()) == ""
    made = owned(_alpha())
    assert (made.name, made.node, made.vmid, made.uuid, made.digest) == ("alpha", "pve", 9010, UUID, DIGEST)
    assert made.identity == f"agent-warehouse-{UUID}" and made.said == "alpha (VMID 9010)"
    # Its record's UUID in another case is the same UUID, as pve/record.py's copied reads one.
    assert refusal(_alpha(record_uuid=UUID.upper())) == ""


@pytest.mark.parametrize(
    ("read", "says"),
    [
        ({"kind": "machine"}, "alpha (VMID 9010) is no Proxmox VM, which is all this host's runs change"),
        ({"template": True}, "alpha (VMID 9010) is a template, which is never a target"),
        ({"unread": "the token may not read it: it is never changed or deleted"}, "the token may not read it"),
        ({"recorded": "", "record_uuid": ""}, "is a member of the pool with no record of this engine's in its notes"),
        ({"recorded": "template"}, "holds a template's record and is no template, so it is a copy"),
        ({"recorded": "golden"}, "holds a golden's record and is no template"),
        ({"recorded": "build"}, "holds a build's record and is no template"),
        ({"record_uuid": OTHER}, f"alpha (VMID 9010)'s record names another VM's UUID ({OTHER})"),
        ({"uuid": ""}, f"alpha (VMID 9010)'s record names another VM's UUID ({UUID})"),
        ({"record_uuid": "", "stage": "cloning"}, "alpha (VMID 9010)'s record is still the first form its clone"),
        ({"record_uuid": ""}, "alpha (VMID 9010)'s record names no UUID, which only a VM's first form"),
        ({"twins": (9011, 9014)}, "alpha (VMID 9010) carries one UUID with VMID 9011, 9014"),
    ],
)
def test_each_refusal_is_said_and_raised_and_makes_no_owned(read, says) -> None:
    member = _alpha(**read)
    assert says in refusal(member)
    with pytest.raises(NotOwned) as refused:
        owned(member)
    assert refused.value.sentence == refusal(member)


def test_a_record_s_fields_are_judged_alike_whichever_reader_read_them() -> None:
    # status reads the record by pve/record.py's plain parse, the engine by its typed Record: the one check is the same.
    for made in (
        Record(kind="vm", uuid=UUID, stage="finished"),
        Record(kind="vm", uuid=OTHER, stage="finished"),
        Record(kind="vm", stage="cloning", job=JOB),
        Record(kind="template", uuid=UUID, build=1),
    ):
        parsed = block.parse(block.render("", made.fields()))
        assert parsed is not None
        typed = Member.of("alpha", "pve", 9010, found=made.fields(), uuid=UUID, digest=DIGEST)
        plain = Member.of("alpha", "pve", 9010, found=parsed, uuid=UUID, digest=DIGEST)
        assert typed == plain and refusal(typed) == refusal(plain)
        assert refusal(typed, JOB) == refusal(plain, JOB)
    assert Member.of("alpha", "pve", 9010, found=None, uuid=UUID).recorded == ""


def test_the_first_form_of_a_clone_is_owned_only_with_the_job_it_names() -> None:
    first = _alpha(record_uuid="", stage="cloning", job=JOB)
    for job in ("", "0123456789abcdef"):
        assert "still the first form its clone carries" in refusal(first, job)
    assert owned(first, job=JOB).uuid == UUID
    # Not a record of another kind or stage that names no UUID, nor a VM with no SMBIOS UUID to write it for.
    assert "names no UUID, which only a VM's first form" in refusal(_alpha(record_uuid="", job=JOB), JOB)
    assert "has no SMBIOS UUID in its configuration" in refusal(dataclasses.replace(first, uuid=""), JOB)
    # Nor two VMs of one UUID.
    assert "carries one UUID with VMID 9011" in refusal(dataclasses.replace(first, twins=(9011,)), JOB)


def test_an_owned_is_made_by_owned_alone() -> None:
    made = owned(_alpha())
    for making in (
        lambda: Owned("alpha", "pve", 9010, UUID, DIGEST),
        lambda: Owned(_alpha()),
        lambda: Owned(_alpha(), object()),
        lambda: Owned(member=_alpha()),
        lambda: dataclasses.replace(made, vmid=9011),
        lambda: copy.replace(made, digest="b" * 40),
    ):
        with pytest.raises(TypeError) as refused:
            making()
        assert str(refused.value) == ownership.MADE_BY_OWNED
    with pytest.raises(dataclasses.FrozenInstanceError):
        made.vmid = 9011
    # A copy of one is that one, and equal to it.
    assert copy.copy(made) == made == owned(_alpha())
    with pytest.raises(TypeError, match="judges a Member"):
        owned(made)


@pytest.mark.parametrize("module", ["claude_vm.pve.ownership", "claude_vm.fleet.opened"])
def test_the_one_rule_and_the_way_a_host_is_opened_import_nothing_status_may_not(home, module) -> None:
    # So that status (card G16), which may not import the engine (card G15's boundary), judges a member by the same
    # rule and opens a host the same way as every workflow: never a second.
    added = imports_of(module=module, home=home) - imports_of(module="claude_vm", home=home)
    assert module in added, sorted(added)
    ours = sorted(name for name in added if name.split(".")[0] == "claude_vm")
    assert [name for name in ours if name == "claude_vm.engine" or name.startswith("claude_vm.engine.")] == [], ours
    assert {name.split(".")[0] for name in added} & set(HEAVY) == set(), sorted(added)
    if module == "claude_vm.pve.ownership":
        # The rule alone: pve/record.py's, and not the client, which imports it.
        assert set(ours) - {"claude_vm.pve", "claude_vm.pve.record", "claude_vm.text", module} == set(), ours
