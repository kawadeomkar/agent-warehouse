"""What is the fleet's (lib/claude_vm/pve/ownership.py; docs/architecture/engine.md §16.5), and where a host is opened
(lib/claude_vm/fleet/opened.py, §16.9).

The one check is a pure function of what was read of a member of the pool, whichever reader read it: engine/fleet.py's,
whose cases against the fake are tests/unit/test_aw_fleet.py's, and status's (card G16), which reads the record by
pve/record.py's plain parse. Here: each refusal on what was read; a record judged alike by either reader, the records
the engine's typed reader refuses among them (card G28, review 51's 5); the first form of a clone, owned only by the
run that cloned it, by its own job and its own clone's task, never by the job its record names (review 51's 3); an
Owned made by owned() alone, and owned() and building() judging a Member a reader made alone, never one made by hand or
changed after its read (review 51's 2); a Building made by building() alone, of a build VM of the asking run's own and
nothing else (review 51's 4); a Member read with the twins its reader read beside it, a keyword with no default
(review 51b's M-A); and the rules with a test of their own: that a Member is marked read in its readers alone (a scan,
which allows the kit's one helper by name), that Member.read is reached in lib/ by the reader of the pool alone,
engine/fleet.py's (a scan, review 51b's M-A), that the module the one check lives in imports nothing of the
client, and that it, and the one a host is opened by, import nothing of what status may not (card G15's boundary: the
engine, pydantic, Ansible, Jinja2, the MCP SDK), so that status judges by the same rule and opens a host the same way.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
from pathlib import Path

import pytest
from aw_kit import imports_of
from harness import REPO

from claude_vm.engine import record as engine_record
from claude_vm.pve import answers, ownership
from claude_vm.pve import record as block
from claude_vm.pve.ownership import Building, Member, NotOwned, Owned, build_refusal, building, owned, refusal

UUID = "5b0c1d2e-3f40-4a5b-8c6d-7e8f90a1b2c3"
OTHER = "0f0e0d0c-0b0a-4908-8706-050403020100"
DIGEST = "a" * 40
JOB = "2b3c4d5e6f708192"
ANOTHER_JOB = "0123456789abcdef"
UPID = "UPID:pve:00000001:00000000:00000000:qmclone:9000:root@pam:"
# The task the asking run's own clone call answered: the clone of the template 9000 onto alpha's VMID.
CLONE = answers.task(UPID, "pve", "clone", 9010, 9000)
# What status may not import (card G15's boundary, tests/unit/test_aw_cli_imports.py's HEAVY).
HEAVY = ("pydantic", "pydantic_core", "ansible", "jinja2", "mcp")


def _config(notes: str = "", *, uuid: str = UUID, template: bool = False, vmid: int = 9010) -> answers.VmConfig:
    """alpha's configuration as the client reads it: its notes, its SMBIOS UUID, whether it is a template."""
    given: dict = {"digest": DIGEST, "name": "alpha", "description": notes}
    if uuid:
        given["smbios1"] = f"uuid={uuid}"
    if template:
        given["template"] = 1
    return answers.config(given, "pve", vmid, "net0")


def _block(*lines: str) -> str:
    """A block of this engine's, written by hand: its lines as given, whatever their values."""
    return "\n".join([block.BEGIN.format(format=block.FORMAT), *lines, block.END])


def _notes(**fields) -> str:
    """Notes holding the record fields make, a VM's own finished one unless they say otherwise."""
    return block.render("", {"kind": "vm", "uuid": UUID, "stage": "finished"} | fields)


def _read(notes: str = "", **config) -> Member:
    """alpha as a reader read it, from its configuration with notes, no other member of the pool carrying its UUID."""
    return Member.read(_config(notes, **config), twins=())


def _alpha(**read) -> Member:
    """alpha's fields as a reader would give them, made by hand: for what refusal() says, which reads fields alone."""
    given = {"uuid": UUID, "digest": DIGEST, "recorded": "vm", "record_uuid": UUID, "stage": "finished"}
    return Member("alpha", "pve", 9010, **(given | read))


def test_a_member_the_fleet_made_is_owned_with_what_the_decision_was_made_on() -> None:
    made = owned(_read(_notes()))
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
        ({"stage": ""}, "alpha (VMID 9010)'s record names no stage, which a VM's record does"),
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


# Each record a reader may meet, as the notes hold it, and whether the fleet owns the VM that holds it: those the
# engine's typed reader refuses (Record) among them, review 51's P3 first.
JUDGED = [
    (_notes(), True),
    (_notes(uuid=OTHER), False),
    (_notes(kind="template", stage=None, build="1"), False),
    (_notes(uuid=None, stage="cloning", job=JOB), False),
    (_block("kind: vm", f"uuid: {UUID.upper()}", "stage: finished"), False),
    (_block("kind: vm", f"uuid: {UUID}", "stage: finished", "cores: 0"), False),
    (_block("kind: vm", f"uuid: {UUID}", "stage: finished", "memory: 16GB"), False),
    (_block("kind: vm", f"uuid: {UUID}", "stage: finished", "disk: 64"), False),
    (_block("kind: vm", f"uuid: {UUID}", "stage: finished", "image: abc"), False),
    (_block("kind: vm", f"uuid: {UUID}", "stage: finished", "build: 0"), False),
    (_block("kind: vm", f"uuid: {UUID}", "stage: finished", "image_url: ftp://example.invalid/x"), False),
    (_block("kind: vm", f"uuid: {UUID}", "stage: finished", "packages: Qemu"), False),
    (_block("kind: vm", f"uuid: {UUID}", "stage: finished", "joined: wireguard"), False),
    (_block("kind: vm", f"uuid: {UUID}", "stage: finished", "name: -alpha"), False),
    (_block("kind: vm", f"uuid: {UUID}"), False),
    (_block("kind: vm", "stage: finished"), False),
    (_block("kind: template", "stage: cloning", "build: 1"), False),
]


@pytest.mark.parametrize(("notes", "ours"), JUDGED)
def test_a_record_is_judged_alike_whichever_reader_read_it_those_one_reader_refuses_among_them(notes, ours) -> None:
    """status reads the record by pve/record.py's plain parse (Member.read), the engine by its typed Record first, whose
    refusal it hands the member as it is read (engine/fleet.py's _member): the one check says the same of both, the
    first form's run asking as its own, with its job and its clone's task."""
    config = _config(notes)
    try:
        engine_record.read(config)
        typed = ""
    except engine_record.Unreadable as refused:
        typed = f"{refused}: it is never changed or deleted"
    engine = refusal(Member.read(config, unread=typed, twins=()), ANOTHER_JOB, CLONE)
    status = refusal(Member.read(config, twins=()), ANOTHER_JOB, CLONE)
    assert (engine == "", status == "") == (ours, ours), (engine, status)


def test_the_first_form_of_a_clone_is_owned_only_by_the_run_that_cloned_it() -> None:
    """The job a first form names is no secret: anyone reads it in the VM's notes. So the run owns it by its own job
    and by the task its own clone call answered (or its own job record keeps), whose VMID is the member's: never by
    echoing the record it judges (review 51's P5)."""
    first = _read(_notes(uuid=None, stage="cloning", job=JOB))
    # No job, another, or the record's own echoed with no clone's task: P5's road.
    for job, clone in (("", CLONE), (ANOTHER_JOB, CLONE), (first.job, None)):
        assert "still the first form its clone carries" in refusal(first, job, clone)
        with pytest.raises(NotOwned, match="still the first form its clone carries"):
            owned(first, job=job, clone=clone)
    # The run's job with a task that is not its clone of this VM: another VMID, another node, another kind of task.
    for clone in (
        answers.task(UPID, "pve", "clone", 9011, 9000),
        answers.task(UPID, "pve2", "clone", 9010, 9000),
        answers.task(UPID, "pve", "start", 9010),
        (9010, "pve"),
    ):
        with pytest.raises(NotOwned, match="still the first form its clone carries"):
            owned(first, job=JOB, clone=clone)
    made = owned(first, job=JOB, clone=CLONE)
    assert (made.vmid, made.uuid) == (9010, UUID)
    # Not a record of another kind or stage that names no UUID, nor a VM with no SMBIOS UUID to write it for.
    assert "names no UUID, which only a VM's first form" in refusal(_alpha(record_uuid="", job=JOB), JOB, CLONE)
    nameless = _read(_notes(uuid=None, stage="cloning", job=JOB), uuid="")
    assert "has no SMBIOS UUID in its configuration" in refusal(nameless, JOB, CLONE)
    # Nor two VMs of one UUID.
    twin = Member.read(_config(_notes(uuid=None, stage="cloning", job=JOB)), twins=(9011,))
    assert "carries one UUID with VMID 9011" in refusal(twin, JOB, CLONE)


def test_an_owned_is_made_by_owned_alone() -> None:
    made = owned(_read(_notes()))
    for making in (
        lambda: Owned("alpha", "pve", 9010, UUID, DIGEST),
        lambda: Owned(_read(_notes())),
        lambda: Owned(_read(_notes()), object()),
        lambda: Owned(member=_read(_notes())),
        lambda: dataclasses.replace(made, vmid=9011),
        lambda: copy.replace(made, digest="b" * 40),
    ):
        with pytest.raises(TypeError) as refused:
            making()
        assert str(refused.value) == ownership.MADE_BY_OWNED
    with pytest.raises(dataclasses.FrozenInstanceError):
        made.vmid = 9011
    # A copy of one is that one, and equal to it.
    assert copy.copy(made) == made == owned(_read(_notes()))
    with pytest.raises(TypeError, match="judges a Member"):
        owned(made)


def test_a_member_no_reader_made_is_never_owned_nor_made_a_building() -> None:
    """What is judged is what a reader read (review 51's P1): a Member made by hand, or a read one changed after its
    read - by dataclasses.replace or copy.replace, a record given to a VM that holds none among them - is a TypeError
    to owned() and building(), whatever its fields say. A copy of a read one is that read."""
    unrecorded = _read("mine")
    assert "no record of this engine's" in refusal(unrecorded)
    forged = (
        _alpha(),
        dataclasses.replace(unrecorded, recorded="vm", record_uuid=UUID, stage="finished"),
        copy.replace(unrecorded, recorded="vm", record_uuid=UUID, stage="finished"),
        dataclasses.replace(_read(_notes()), digest="b" * 40),
    )
    for member in forged:
        assert refusal(member) == ""
        with pytest.raises(TypeError) as refused:
            owned(member)
        assert str(refused.value) == ownership.NOT_READ
    built = _read(_notes(kind="build", stage=None, job=JOB))
    by_hand = Member(**{field.name: getattr(built, field.name) for field in dataclasses.fields(Member) if field.init})
    for member in (dataclasses.replace(built), by_hand):
        assert build_refusal(member, JOB, CLONE) == ""
        with pytest.raises(TypeError, match="made it from what it read"):
            building(member, job=JOB, clone=CLONE)
    assert owned(copy.copy(_read(_notes()))).uuid == UUID
    with pytest.raises(TypeError, match="answers.VmConfig"):
        Member.read({"digest": DIGEST, "smbios1": f"uuid={UUID}"}, twins=())


def test_a_member_is_read_with_the_twins_its_reader_read_beside_it() -> None:
    """The mark says a configuration was read, not the pool beside it (review 51b's M-A): owned(Member.read(config)) of
    one configuration owned one of two VMs with one UUID. A reader says which other members carry the UUID, twins, a
    keyword with no default, so that the road is a TypeError, and a twin it read is refused by both judges."""
    config = _config(_notes())
    for reading in (lambda: Member.read(config), lambda: Member.read(config, unread="")):
        with pytest.raises(TypeError, match="twins"):
            reading()
    with pytest.raises(NotOwned, match="alpha \\(VMID 9010\\) carries one UUID with VMID 9011"):
        owned(Member.read(config, twins=(9011,)))
    built = _config(_notes(kind="build", stage=None, job=JOB))
    with pytest.raises(NotOwned, match="carries one UUID with VMID 9011, 9014"):
        building(Member.read(built, twins=(9011, 9014)), job=JOB, clone=CLONE)
    for twins in ([9011], (9011, "9014"), None):
        with pytest.raises(TypeError, match="the VMIDs that carry its UUID"):
            Member.read(config, twins=twins)
    assert owned(Member.read(config, twins=())).vmid == 9010


def test_a_building_is_made_of_a_build_vm_of_the_asking_run_s_own_alone() -> None:
    """A conversion to a template cannot be undone (review 51's P4): only a VM whose record is of kind build and names
    its own UUID and the asking run's job, at the VMID the run's own clone made, never a template and never a VM of the
    fleet, is made a Building, which building() alone makes. An Owned and a Building never make one another."""
    build = _notes(kind="build", stage=None, job=JOB)
    made = building(_read(build), job=JOB, clone=CLONE)
    assert isinstance(made, Building) and not isinstance(made, Owned)
    assert (made.name, made.node, made.vmid, made.uuid, made.digest) == ("alpha", "pve", 9010, UUID, DIGEST)
    for member, job, clone, says in (
        (_read(build, template=True), JOB, CLONE, "is a template already"),
        (_read(_notes(job=JOB)), JOB, CLONE, "holds a VM's record, a VM of the fleet, which is never made a template"),
        (_read(_notes(uuid=None, stage="cloning", job=JOB)), JOB, CLONE, "a VM of the fleet"),
        (_read(_notes(kind="template", stage=None, job=JOB)), JOB, CLONE, "holds a template's record and is no"),
        (_read("mine"), JOB, CLONE, "no record of this engine's"),
        (_read(_notes(kind="build", stage=None, uuid=OTHER, job=JOB)), JOB, CLONE, "does not name its own UUID"),
        (_read(build, uuid=""), JOB, CLONE, "has no SMBIOS UUID"),
        (_read(build), ANOTHER_JOB, CLONE, "the build VM of another run's job"),
        (_read(build), "", CLONE, "the build VM of another run's job"),
        (_read(_notes(kind="build", stage=None)), "", CLONE, "the build VM of another run's job"),
        (_read(build), JOB, answers.task(UPID, "pve", "clone", 9011, 9000), "not the VM the asking run's own clone"),
        (_read(build), JOB, None, "not the VM the asking run's own clone made"),
        (Member.read(_config(build), twins=(9011,)), JOB, CLONE, "carries one UUID with VMID 9011"),
        (_read(_block("kind: build", f"uuid: {UUID}", f"job: {JOB}", "cores: 0")), JOB, CLONE, "cores"),
    ):
        assert says in build_refusal(member, job, clone), (says, build_refusal(member, job, clone))
        with pytest.raises(NotOwned):
            building(member, job=job, clone=clone)
    # A build VM is no VM of the fleet: owned() refuses it as a copy.
    with pytest.raises(NotOwned, match="holds a build's record and is no template"):
        owned(_read(build), job=JOB, clone=CLONE)
    for making in (
        lambda: Building("alpha", "pve", 9010, UUID, DIGEST),
        lambda: Building(_read(build)),
        lambda: Building(_read(build), ownership._MINT),
        lambda: dataclasses.replace(made, vmid=9011),
        lambda: copy.replace(made, digest="b" * 40),
    ):
        with pytest.raises(TypeError) as refused:
            making()
        assert str(refused.value) == ownership.MADE_BY_BUILDING
    with pytest.raises(TypeError, match="judges a Member"):
        building(made, job=JOB, clone=CLONE)


def test_a_build_vm_the_run_wrote_a_golden_s_record_on_is_a_building_and_a_copy_of_one_is_not() -> None:
    """Card E18's order, today's (the coordinator's ruling on review 51b's M-B): the run writes the golden's record on
    its own build VM, reads it back as a clone would, makes the Building from that read and converts. A record of kind
    golden is taken where one of kind build is, on the same terms and no others: never on a template, nor on another
    run's VM, nor on one at another clone's VMID, nor on a clone of a golden, whose record, copied with the golden's
    notes, names the golden's UUID; and owned() still refuses it, a golden's record on a VM that is no template."""
    golden = _notes(kind="golden", stage=None, job=JOB, build="1")
    made = building(_read(golden), job=JOB, clone=CLONE)
    assert (made.name, made.vmid, made.uuid, made.digest) == ("alpha", 9010, UUID, DIGEST)
    another = answers.task(UPID, "pve", "clone", 9011, 9000)
    for member, job, clone, says in (
        (_read(golden, template=True), JOB, CLONE, "is a template already"),
        (_read(golden), ANOTHER_JOB, CLONE, "the build VM of another run's job"),
        (_read(_notes(kind="golden", stage=None, job=ANOTHER_JOB)), JOB, CLONE, "the build VM of another run's job"),
        (_read(_notes(kind="golden", stage=None)), JOB, CLONE, "the build VM of another run's job, or of none"),
        (_read(golden), JOB, another, "not the VM the asking run's own clone made"),
        (_read(golden), JOB, None, "not the VM the asking run's own clone made"),
        (_read(golden, uuid=OTHER), JOB, CLONE, "golden record does not name its own UUID"),
        (_read(_notes(kind="golden", stage=None, uuid=None, job=JOB)), JOB, CLONE, "does not name its own UUID"),
        (_read(golden, uuid=""), JOB, CLONE, "has no SMBIOS UUID"),
        (Member.read(_config(golden), twins=(9011,)), JOB, CLONE, "carries one UUID with VMID 9011"),
    ):
        assert says in build_refusal(member, job, clone), (says, build_refusal(member, job, clone))
        with pytest.raises(NotOwned):
            building(member, job=job, clone=clone)
    with pytest.raises(NotOwned, match="holds a golden's record and is no template"):
        owned(_read(golden), job=JOB, clone=CLONE)


# ------------------------------------------------------------------------------------- the rules by scans ----


# Where a Member is marked read (its _read), and a target given the Member its read made (engine/fleet.py's member),
# function by function: the one reader of a member's own, the engine's reader and its own change of a target, and the
# kit's one helper for the tests of what a write sends. Nothing else marks one.
MARKED = {
    ("lib/claude_vm/pve/ownership.py", "read"),
    ("lib/claude_vm/engine/fleet.py", "_member"),
    ("lib/claude_vm/engine/fleet.py", "_kept"),
    ("tests/unit/aw_kit.py", "owned_as_recorded"),
}
MARKS = frozenset({"_read", "member"})


class _Marking(ast.NodeVisitor):
    """The functions of a module, by name ("" for its top), that set a mark: object.__setattr__ or setattr of one named
    in MARKS, or a store into an object's __dict__, or vars() of it, under one."""

    def __init__(self) -> None:
        self.found: set[str] = set()
        self.functions: list[str] = []

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _function

    def visit_Call(self, node: ast.Call) -> None:
        named = ast.unparse(node.func)
        if named in ("object.__setattr__", "setattr") and len(node.args) > 1:
            mark = node.args[1]
            if isinstance(mark, ast.Constant) and mark.value in MARKS:
                self.found.add(self.functions[-1] if self.functions else "")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, ast.Store) and isinstance(node.slice, ast.Constant) and node.slice.value in MARKS:
            self.found.add(self.functions[-1] if self.functions else "")
        self.generic_visit(node)


def _marking(source: str) -> set[str]:
    visitor = _Marking()
    visitor.visit(ast.parse(source))
    return visitor.found


def _marked_in(root: Path) -> set[tuple[str, str]]:
    return {
        (path.relative_to(REPO).as_posix(), function)
        for folder in ("lib/claude_vm", "tests/unit")
        for path in sorted((root / folder).rglob("*.py"))
        for function in _marking(path.read_text())
    }


def test_a_member_is_marked_read_by_its_readers_alone() -> None:
    """So that owned() and building() judge only what a reader read (review 51's 2): no module of the engine, and no
    test but through the kit's one helper, marks a Member read or gives a target a member."""
    assert _marked_in(REPO) == MARKED


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ('def f(m):\n    object.__setattr__(m, "_read", True)', {"f"}),
        ('def f(t, m):\n    setattr(t, "member", m)', {"f"}),
        ('def f(m):\n    vars(m)["_read"] = True', {"f"}),
        ('def f(m):\n    m.__dict__["_read"] = True', {"f"}),
        ('class C:\n    def g(self, m):\n        object.__setattr__(m, "_read", 1)', {"g"}),
        ('object.__setattr__(m, "_read", True)', {""}),
        ('def f(m):\n    object.__setattr__(m, "name", "x")', set()),
        ("def f(m):\n    return m._read", set()),
    ],
)
def test_the_scan_sees_a_member_marked(source, found) -> None:
    assert _marking(source) == found


# Where Member.read is reached in lib/, function by function, and why: the reader of a host's pool alone, which reads
# every member's configuration first and gives each the twins it read beside it (review 51b's M-A). Status's reader of
# the pool joins it when card G16 builds one. A workflow owns a VM again through that reader - requested, or members and
# fleet.owned - never by Member.read of one configuration, which knows nothing of the pool.
READERS = {
    ("lib/claude_vm/engine/fleet.py", "_member"): "the fleet's reader of the pool, which gives each member its twins",
}


class _Reading(ast.NodeVisitor):
    """The functions of a module, by name ("" for its top), that reach Member.read: read as an attribute of a class
    named Member, by any name a `from` import gave it, or of an attribute named Member, called or not; or got by
    getattr(<Member>, "read")."""

    def __init__(self) -> None:
        self.found: set[str] = set()
        self.functions: list[str] = []
        self.names = {"Member"}

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _function

    def _take(self) -> None:
        self.found.add(self.functions[-1] if self.functions else "")

    def _member(self, node: ast.expr) -> bool:
        return (isinstance(node, ast.Name) and node.id in self.names) or (
            isinstance(node, ast.Attribute) and node.attr == "Member"
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names |= {alias.asname or alias.name for alias in node.names if alias.name == "Member"}
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "read" and self._member(node.value):
            self._take()
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        got = node.args[1] if ast.unparse(node.func) == "getattr" and len(node.args) > 1 else None
        if isinstance(got, ast.Constant) and got.value == "read" and self._member(node.args[0]):
            self._take()
        self.generic_visit(node)


def _reading(source: str) -> set[str]:
    visitor = _Reading()
    visitor.visit(ast.parse(source))
    return visitor.found


def test_member_read_is_reached_in_lib_by_the_reader_of_the_pool_alone() -> None:
    """So that no workflow owns a VM from one configuration (review 51b's M-A): G5 owns again after its stop through
    requested, G3 its new member through members and fleet.owned, never through Member.read of what client.config
    gave, which owned one of two twins and a VM outside the pool. engine/ above all, and every other package of lib/."""
    found = {
        (path.relative_to(REPO).as_posix(), function)
        for path in sorted((REPO / "lib" / "claude_vm").rglob("*.py"))
        for function in _reading(path.read_text())
    }
    assert found == set(READERS)


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("def f(c):\n    return ownership.Member.read(c, twins=())", {"f"}),
        ("from ..pve.ownership import Member\ndef f(c):\n    return Member.read(c, twins=())", {"f"}),
        ("from ..pve.ownership import Member as M\ndef f(c):\n    return M.read(c, twins=())", {"f"}),
        ("def f(c):\n    read = ownership.Member.read\n    return read(c, twins=())", {"f"}),
        ('def f(c):\n    return getattr(ownership.Member, "read")(c, twins=())', {"f"}),
        ("class C:\n    def g(self, c):\n        return Member.read(c, twins=())", {"g"}),
        ("reader = pve.ownership.Member.read", {""}),
        ("def f(c):\n    return record.read(c)", set()),
        ("def f(c):\n    return c.read()", set()),
        ("def f(m):\n    return Member(m.name, m.node, m.vmid)", set()),
    ],
)
def test_the_scan_sees_member_read_reached(source, found) -> None:
    assert _reading(source) == found


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
        # The rule alone: pve/record.py's, and the records of what a reader read (answers.py), and not the client,
        # which imports it.
        pve = {name for name in ours if name.startswith("claude_vm.pve.")}
        assert pve == {"claude_vm.pve.record", "claude_vm.pve.answers", module}, ours
