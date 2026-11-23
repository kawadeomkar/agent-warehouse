"""A VM's record: the block of its notes pve/record.py parses and renders, and engine/record.py's typed record, read
and written under Proxmox's digest (docs/architecture/engine.md §16.4 and §16.5).

The block alone is this engine's: every field round-trips, a person's lines beside it are kept byte for byte, today's
record lines among them, and notes with no block of the engine's hold no record. A block it cannot read - an unknown
format number, a line of no field, a key twice, two blocks, a start with no end - is refused and never rewritten. A
value with a line break, or anything else that is not plain text on one line, is refused; so are notes past Proxmox's
8192 characters, counted in characters, before anything is sent. A VM's own list of repositories keeps today's rule,
carried case by case from tests/unit/test_vm_repos.py, which stays as it is.

Against the fake Proxmox (tests/molecule/fake_pve_api.py), with the engine's token held to its table of privileges: a
record written reads back field for field; of two writers the second is refused for its stale digest, reads again and
lands with the first's change kept, and so does a write a person's edit came between; a second write of the same record
sends nothing; a block the engine cannot read, a record naming another VM's UUID and notes past the bound send nothing;
and the label a VM is given is the setting vm_label's value, agent-warehouse by default, beside a person's tags.

A record that names a UUID that is not the VM's own is a copy, which a clone made without a description of its own
carries from its source (experiment 6): pve/record.py's copied says so, for status's reader too, and engine/record.py's
read refuses it (Copy, an Unreadable of its own kind), so no reader takes it for the VM's own and no write is sent over
it; a VM's first form, which names no UUID, is no copy by this (review 19, G1.md 2).

The rules with a test of their own: no module of the new engine but engine/record.py names a VM's description to send
it, the client, its table and its reader of answers aside; and importing claude_vm.pve.record loads nothing but the
standard library and claude_vm.text.
"""

from __future__ import annotations

import ast
import dataclasses
import sys
from pathlib import Path

import pytest
from aw_kit import imports_of
from harness import ENGINE_POOL, _pve, _pve_requests, engine_token, fake_fleet
from pki import proxmox_pki
from test_aw_transport import _new_engine, _rel

from claude_vm import proxmox
from claude_vm.engine import record
from claude_vm.engine.record import Copy, Record, TooLong, Unreadable
from claude_vm.fleet import registry
from claude_vm.pve import pin
from claude_vm.pve import record as block
from claude_vm.pve.client import EDITS, DigestMismatch, Proxmox, Token
from claude_vm.schema.patterns import PATTERNS
from claude_vm.secret import Secret

# The module's certificate authority, which signs the fake's certificate: cheap enough to make again on each worker
# (tests/unit/conftest.py).
EACH_WORKER = {"pki"}
UUID = "5b0c1d2e-3f40-4a5b-8c6d-7e8f90a1b2c3"
DIGEST = "3f9a0c1e7b2d4a58" * 4
# Every field of format 1 set.
WHOLE = Record(
    kind="vm",
    uuid=UUID,
    name="alpha",
    stage="finished",
    provisioned=True,
    repositories=("my-app", "my_lib.2"),
    cores=8,
    memory="16G",
    disk="128G",
    build=1,
    image=DIGEST,
    image_url="https://cloud-images.ubuntu.com/minimal/releases/noble/release/ubuntu-24.04-minimal-cloudimg-amd64.img",
    packages=("qemu-guest-agent", "libc6"),
    joined=("tailscale",),
    converged="0" * 64,
    made_by_agent=True,
    job="3f9a0c1e7b2d4a58 pid 4242 on laptop since 2026-11-23T10:00:00Z",
)
# Today's record line, as the fake's template carries it and every clone of it inherits: a person's text here.
TODAY = proxmox.TEMPLATE_RECORD_1X + " build=1; sha256=" + "0" * 64


def _block(*lines: str, number: str = "1") -> str:
    return "\n".join([f"agent-warehouse record (format {number})", *lines, block.END])


def _fields(made: Record) -> dict:
    return {key: value for key, value in made.fields().items() if value is not None}


# ------------------------------------------------------------------------------------------ the block ----


def test_every_field_round_trips() -> None:
    notes = block.render("", WHOLE.fields())
    assert notes.splitlines()[0] == "agent-warehouse record (format 1)" and notes.splitlines()[-1] == block.END
    assert [line.partition(":")[0] for line in notes.splitlines()[1:-1]] == list(block.FIELDS)
    assert block.parse(notes) == _fields(WHOLE)
    assert Record.of(block.parse(notes)) == WHOLE
    first = Record(kind="vm", stage="cloning", name="alpha", job="3f9a0c1e7b2d4a58")
    assert Record.of(block.parse(block.render("", first.fields()))) == first


def test_the_typed_record_has_every_field_of_the_block_in_its_order() -> None:
    assert [field.name for field in dataclasses.fields(Record)] == list(block.FIELDS)
    assert list(WHOLE.fields()) == list(block.FIELDS)


def test_an_own_list_of_no_repositories_is_a_list_and_a_line_left_out_is_none() -> None:
    own = dataclasses.replace(WHOLE, repositories=())
    notes = block.render("", own.fields())
    assert "\nrepositories:\n" in notes and block.parse(notes)["repositories"] == ()
    assert Record.of(block.parse(notes)).repositories == ()
    follows = block.render("", dataclasses.replace(WHOLE, repositories=None).fields())
    assert "repositories" not in block.parse(follows) and Record.of(block.parse(follows)).repositories is None


def test_a_person_s_lines_beside_the_block_are_kept_byte_for_byte() -> None:
    before = f"My notes\r\nwith a tab\tand é ü 日本 ✓\r\n{TODAY}\n\n"
    after = "\n\n  and after it, indented\n# a heading of mine"
    first = block.render(before + _block("kind: template", f"uuid: {UUID}") + after, WHOLE.fields())
    assert first == before + block.render("", WHOLE.fields()) + after
    again = block.render(first, dataclasses.replace(WHOLE, stage="unfinished", provisioned=False).fields())
    assert again.startswith(before) and again.endswith(after)
    assert block.parse(again)["stage"] == "unfinished" and "provisioned" not in block.parse(again)


def test_the_block_s_own_line_ending_is_kept_where_it_was_written() -> None:
    notes = "mine\r\n" + _block("kind: template", f"uuid: {UUID}").replace("\n", "\r\n") + "\r\nafter"
    assert block.render(notes, WHOLE.fields()).endswith(f"{block.END}\r\nafter")


def test_a_block_is_added_last_after_a_blank_line() -> None:
    alone = block.render("", WHOLE.fields())
    assert block.render("mine", WHOLE.fields()) == f"mine\n\n{alone}"
    assert block.render("mine\n\n\n  \n", WHOLE.fields()) == f"mine\n\n{alone}"
    assert block.render(" \n\t\n", WHOLE.fields()) == block.render(None, WHOLE.fields()) == alone


def test_notes_are_rendered_as_proxmox_gives_them_back_so_a_second_render_is_the_same() -> None:
    notes = block.render(f"mine\n\n{_block('kind: template', f'uuid: {UUID}')}\n\n\n", WHOLE.fields())
    assert notes == block.kept(notes) and not notes.endswith("\n")
    assert block.render(notes, WHOLE.fields()) == notes


@pytest.mark.parametrize(
    "notes",
    [
        None,
        "",
        "mine",
        TODAY,
        f"{proxmox.VM_REPOS_RECORD} one,two",
        "Golden by agent-warehouse: key=k; build=1",
        # A person's sentence that names the block's first line is not it.
        "see agent-warehouse record (format 1) for what this is",
        "agent-warehouse record",
        "Agent-Warehouse Record (Format 1)",
    ],
)
def test_notes_with_no_block_of_this_engine_s_hold_no_record(notes) -> None:
    assert block.parse(notes) is None
    assert block.render(notes, WHOLE.fields()).startswith(block.kept(notes))


@pytest.mark.parametrize("number", ["2", "0", "01", "x", "", "1.0"])
def test_a_block_of_a_format_this_program_does_not_read_is_refused_and_never_rewritten(number: str) -> None:
    notes = "mine\n" + _block("kind: vm", f"uuid: {UUID}", "stage: finished", "colour: blue", number=number)
    with pytest.raises(Unreadable) as refused:
        block.parse(notes)
    assert f"format {number}," in str(refused.value) and "never rewritten" in str(refused.value)
    with pytest.raises(Unreadable):
        block.render(notes, WHOLE.fields())


@pytest.mark.parametrize(
    ("notes", "said"),
    [
        (_block("kind: vm", "colour: blue"), "line 3 of the notes is no field of format 1"),
        (_block("kind: vm", "a line of mine"), "line 3 of the notes is no field of format 1"),
        (_block("kind: vm", "Kind: vm"), "line 3 of the notes is no field of format 1"),
        (_block("kind: vm", "kind: template"), "it names kind twice"),
        (_block(f"uuid: {UUID}"), "it does not say what it is a record of"),
        (_block("kind: machine"), "kind is one of vm, build, golden, template"),
        (_block("kind: vm", "stage: done"), "stage is one of cloning, unfinished, finished"),
        (_block("kind: vm", "provisioned: no"), "provisioned is yes, or left out"),
        (_block("kind: vm", "job: one‮two"), "job must be one line of plain text"),
        (_block("kind: vm", "joined: tail​scale"), "joined must be one line of plain text"),
        (_block("kind: vm") + "\n" + _block("kind: vm"), "they hold two records"),
        (_block("kind: vm").rsplit("\n", 1)[0], "does not end"),
        ("mine\n" + block.END, "with no record before it"),
        (block.END + "\n" + _block("kind: vm"), "does not end"),
    ],
)
def test_a_block_this_engine_cannot_read_is_refused_and_never_rewritten(notes: str, said: str) -> None:
    with pytest.raises(Unreadable) as refused:
        block.parse(notes)
    assert said in str(refused.value)
    with pytest.raises(Unreadable):
        block.render(notes, WHOLE.fields())


def test_a_blank_line_and_white_space_inside_the_block_are_read_through() -> None:
    notes = f"  agent-warehouse record (format 1)  \nkind :  vm\n\n  stage:unfinished \nuuid: {UUID}\n{block.END}  "
    assert block.parse(notes) == {"kind": "vm", "stage": "unfinished", "uuid": UUID}


# Every way a value could end its line, or read in an order of its own.
BREAKS = ["\n", "\r", "\r\n", "\x0b", "\x0c", "\x1c", "\x85", " ", " ", "\x00", "\x1b[31m", "\t", "‮"]


@pytest.mark.parametrize("brk", BREAKS)
def test_a_value_with_a_line_break_or_any_character_that_is_not_plain_text_is_refused(brk: str) -> None:
    for field, value in [("job", f"one{brk}two"), ("name", f"alpha{brk}"), ("repositories", (f"a{brk}b",))]:
        with pytest.raises(ValueError, match=f"the record's {field}"):
            block.render("", {"kind": "vm", field: value})
    with pytest.raises(ValueError):
        dataclasses.replace(WHOLE, job=f"one{brk}two")


@pytest.mark.parametrize("value", [" alpha", "alpha ", " alpha"])
def test_white_space_at_either_end_of_a_value_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match="white space"):
        block.render("", {"kind": "vm", "job": value})


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "vm", "colour": "blue"},
        {"stage": "finished"},
        {"kind": "vm", "repositories": ("a,b",)},
        {"kind": "vm", "repositories": ("../x",)},
        {"kind": "vm", "repositories": "a,b"},
        {"kind": "vm", "joined": ("",)},
        {"kind": "vm", "provisioned": "no"},
        {"kind": "vm", "made_by_agent": "true"},
    ],
)
def test_render_refuses_fields_it_could_not_read_back(fields: dict) -> None:
    with pytest.raises((TypeError, ValueError)):
        block.render("", fields)


def test_notes_past_8192_characters_are_refused_counted_in_characters_not_bytes() -> None:
    alone = block.render("", WHOLE.fields())
    # Two-byte characters: 8192 of them in all is 8192 characters and more than 8192 bytes, which Proxmox takes.
    room = block.MOST - len(alone) - 2
    fits = block.render("é" * room, WHOLE.fields())
    assert len(fits) == block.MOST == 8192 and len(fits.encode()) > block.MOST
    with pytest.raises(TooLong) as refused:
        block.render("é" * (room + 1), WHOLE.fields())
    assert refused.value.length == block.MOST + 1
    assert f"{block.MOST + 1} characters" in str(refused.value) and "nothing was sent" in str(refused.value)


# ---------------------------------------------------- a VM's own list of repositories, as today ----


def _repositories(value: str) -> tuple[str, ...]:
    return block.parse(_block("kind: vm", f"repositories: {value}"))["repositories"]


def test_the_repositories_rule_is_today_s_and_the_command_line_s() -> None:
    one = block.REPOSITORY.pattern
    assert one == proxmox._REPO_NAME_RE.pattern
    assert PATTERNS["repos"].text == f"({one}(,{one})*)?"


# Carried from test_vm_repos.py: test_a_vm_with_no_line_has_no_list_and_notes_are_read_as_they_are,
# test_the_line_is_read_wherever_it_is_and_this_run_s_names_join_it,
# test_a_mangled_line_is_still_the_vm_s_list_and_keeps_only_what_is_a_name and
# test_writing_the_list_changes_that_line_alone; and a value read as today's line of repositories reads it, case for
# case.
@pytest.mark.parametrize("value", ["one", "one,two", " ../x, ok ,.claude", "/etc", "", "a,,b", "-x,_x,A9", "a" * 101])
def test_a_list_keeps_only_what_is_a_name_as_today_s_line_does(value: str) -> None:
    today = proxmox.proxmox_notes_repos(f"{proxmox.VM_REPOS_RECORD} {value}")
    assert today["own"] and _repositories(value) == tuple(today["names"])


def test_a_mangled_list_is_still_the_vm_s_own() -> None:
    assert _repositories("../x, ok ,.claude") == ("ok",)
    assert _repositories("/etc") == ()
    assert Record.of({"kind": "vm", "uuid": UUID, "stage": "finished", "repositories": ()}).repositories == ()


def test_notes_with_no_line_of_repositories_have_no_list_and_today_s_line_is_a_person_s_text() -> None:
    assert "repositories" not in block.parse(_block("kind: vm"))
    notes = f"{proxmox.VM_REPOS_RECORD} one,two\n{TODAY}"
    assert block.parse(notes) is None
    assert block.render(notes, WHOLE.fields()).startswith(notes + "\n\n")


def test_the_list_is_read_wherever_the_block_is() -> None:
    notes = f"mine\n{_block('kind: vm', 'repositories: one,two')}\n{TODAY}"
    assert block.parse(notes)["repositories"] == ("one", "two")


def test_writing_the_list_changes_that_line_alone() -> None:
    notes = f"mine\n{_block('kind: vm', f'uuid: {UUID}', 'stage: finished', 'repositories: one')}\n{TODAY}"
    made = Record.of(block.parse(notes))
    written = block.render(notes, dataclasses.replace(made, repositories=("one", "two")).fields())
    assert written == notes.replace("repositories: one", "repositories: one,two")


# ------------------------------------------------------------------------------------ the typed record ----


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"kind": "machine"}, "kind is one of"),
        ({"stage": ""}, "a VM's record names its stage"),
        ({"stage": "done"}, "stage is one of"),
        ({"uuid": ""}, "names the UUID of the VM it is written for"),
        ({"uuid": UUID.upper()}, "SMBIOS UUID, in lower case"),
        ({"uuid": UUID[:-1]}, "SMBIOS UUID"),
        ({"name": "alpha,beta"}, "one VM's name"),
        ({"name": "-alpha"}, "one VM's name"),
        ({"cores": 0}, "cores"),
        ({"cores": 1000}, "cores"),
        ({"cores": True}, "cores"),
        ({"memory": "16GB"}, "memory"),
        ({"memory": "0G"}, "memory"),
        ({"disk": "128"}, "disk"),
        ({"image": DIGEST.upper()}, "image is a SHA-256"),
        ({"converged": "abc"}, "converged is a SHA-256"),
        ({"build": 0}, "build is a whole number from 1"),
        ({"build": True}, "build is a whole number from 1"),
        ({"image_url": "ftp://example.invalid/x.img"}, "image_url is an http or https URL"),
        ({"image_url": "https://"}, "image_url is an http or https URL"),
        ({"build": "1"}, "build is a whole number from 1"),
        ({"packages": ("Qemu",)}, "name each Debian package once"),
        ({"packages": ("a",)}, "name each Debian package once"),
        ({"packages": ("vim", "vim")}, "name each Debian package once"),
        ({"packages": ("a,b",)}, "packages is a word with no comma"),
        ({"joined": ("wireguard",)}, "names each VPN once"),
        ({"joined": ("tailscale", "tailscale")}, "names each VPN once"),
        ({"job": "a‮b"}, "job"),
        ({"repositories": ("../x",)}, "repositories"),
    ],
)
def test_a_record_holds_each_field_to_what_it_may_be(changes: dict, said: str) -> None:
    with pytest.raises(ValueError, match=said):
        dataclasses.replace(WHOLE, **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"provisioned": 1},
        {"made_by_agent": "yes"},
        {"repositories": ["a"]},
        {"joined": ["tailscale"]},
        {"packages": ["vim"]},
    ],
)
def test_a_record_s_flags_and_lists_are_of_their_own_types(changes: dict) -> None:
    with pytest.raises(TypeError):
        dataclasses.replace(WHOLE, **changes)


def test_only_the_first_form_has_no_uuid_and_a_template_may_name_no_stage() -> None:
    assert Record(kind="vm", stage="cloning").uuid == ""
    assert Record(kind="template", uuid=UUID).stage == ""
    with pytest.raises(ValueError):
        Record(kind="golden", stage="unfinished")


@pytest.mark.parametrize("kind", ["template", "golden", "build"])
def test_a_record_of_any_kind_but_a_vm_names_its_uuid_even_at_the_stage_cloning(kind: str) -> None:
    # Only a VM's first form names no UUID, which its clone carries before its UUID can be read: a template, a golden
    # and a build VM have no such moment, and a block of theirs with none is never taken for this engine's own.
    with pytest.raises(ValueError, match="a VM's first form"):
        Record(kind=kind, stage="cloning")
    with pytest.raises(Unreadable) as refused:
        record.read(_Config(block.render("mine", {"kind": kind, "stage": "cloning"}), 9010))
    assert not isinstance(refused.value, Copy)
    assert "VM 9010's notes" in str(refused.value) and "a VM's first form" in str(refused.value)


def test_sized_is_any_part_of_the_size_the_vm_was_created_with() -> None:
    bare = Record(kind="vm", uuid=UUID, stage="unfinished")
    assert not bare.sized
    assert all(dataclasses.replace(bare, **part).sized for part in ({"cores": 2}, {"memory": "2048"}, {"disk": "1T"}))


@dataclasses.dataclass(frozen=True)
class _Config:
    """The three fields of a VM's configuration that read reads: its notes, its number and its own UUID."""

    description: str
    vmid: int
    identity: str = UUID


@pytest.mark.parametrize(
    "line",
    ["cores: 0", "cores: 08", "cores: eight", "memory: 16GB", "disk: 64", f"uuid: {UUID.upper()}", "joined: wireguard"],
)
def test_a_block_whose_values_are_not_what_their_fields_may_be_is_unreadable_and_names_the_vm(line: str) -> None:
    lines = {"kind": "kind: vm", "uuid": f"uuid: {UUID}", "stage": "stage: finished"}
    lines[line.partition(":")[0]] = line
    with pytest.raises(Unreadable) as refused:
        record.read(_Config(f"mine\n{_block(*lines.values())}", 9010))
    assert "VM 9010's notes" in str(refused.value) and "never rewritten" in str(refused.value)


def test_read_gives_none_for_notes_with_no_record_of_the_engine_s() -> None:
    assert record.read(_Config(TODAY, 9010)) is None
    assert record.read(_Config(block.render(TODAY, WHOLE.fields()), 9010)) == WHOLE


# Another VM's UUID, as a clone carries its source's record.
SOURCE = "9a0e1c2d-3b4f-4a5b-8c6d-7e8f90a1b2c3"


@pytest.mark.parametrize(
    ("uuid", "identity", "copy"),
    [
        (UUID, UUID, False),
        (UUID, UUID.upper(), False),
        (UUID.upper(), UUID, False),
        (SOURCE, UUID, True),
        (UUID, "", True),
        (None, UUID, False),
        (None, "", False),
    ],
)
def test_a_record_that_names_a_uuid_not_the_vm_s_own_is_a_copy(uuid, identity: str, copy: bool) -> None:
    fields = {"kind": "vm", "stage": "cloning" if uuid is None else "finished", "uuid": uuid}
    assert block.copied({key: value for key, value in fields.items() if value is not None}, identity) is copy


@pytest.mark.parametrize("kind", ["vm", "template", "golden", "build"])
def test_read_refuses_a_copy_of_another_vm_s_record_of_any_kind(kind: str) -> None:
    copied = dataclasses.replace(WHOLE, kind=kind, uuid=SOURCE)
    with pytest.raises(Copy) as refused:
        record.read(_Config(block.render("mine", copied.fields()), 9010))
    assert isinstance(refused.value, Unreadable)
    assert "VM 9010's notes" in str(refused.value) and "another VM" in str(refused.value)
    assert SOURCE not in str(refused.value) and UUID not in str(refused.value)
    # The same record, in the notes of the VM it names, is that VM's own.
    assert record.read(_Config(block.render("mine", copied.fields()), 9011, SOURCE)) == copied


def test_a_first_form_names_no_uuid_and_is_read_whatever_the_vm_s_own() -> None:
    first = Record(kind="vm", stage="cloning", name="alpha", job="3f9a0c1e7b2d4a58")
    assert record.read(_Config(block.render("", first.fields()), 9010)) == first
    assert record.read(_Config(block.render("", first.fields()), 9010, "")) == first


# --------------------------------------------------------------------------- against the fake Proxmox ----


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    return proxmox_pki(tmp_path_factory.mktemp("pki"), key_usage=True)


@dataclasses.dataclass(frozen=True)
class At:
    """A VM where Proxmox keeps it."""

    node: str
    vmid: int


ALPHA = At("pve", 9010)


@dataclasses.dataclass
class Fleet:
    """The fake at base, its state in directory, the engine's token, and the certificates it serves (pki)."""

    base: str
    directory: Path
    token: Token
    pki: dict

    def client(self) -> Proxmox:
        return Proxmox(self.base, pin.kept(self.pki["ca"]), self.token)

    def config(self, vmid: int = 9010) -> dict:
        """A VM's configuration, as today's token reads it."""
        return _pve(self.base, "GET", f"nodes/pve/qemu/{vmid}/config")["data"]

    def notes(self) -> str:
        return self.config().get("description", "")

    def identity(self, vmid: int = 9010) -> str:
        return proxmox.smbios_uuid(self.config(vmid))

    def puts(self) -> list[dict]:
        """The parameters of every PUT the client sent to alpha's configuration."""
        return [
            entry["params"]
            for entry in _pve_requests(self.directory)
            if entry["agent"] == pin.USER_AGENT
            and entry["method"] == "PUT"
            and entry["path"] == "/api2/json/nodes/pve/qemu/9010/config"
        ]

    def plant(self, **config) -> None:
        """alpha's configuration changed as a person changes it in Proxmox's page."""
        _pve(self.base, "PUT", "nodes/pve/qemu/9010/config", **config)


@pytest.fixture
def fleet(tmp_path, pki):
    """The fake with the engine's token held to its table, alpha the pool's one member."""
    certificate = (pki["cert"], pki["key"])
    for base in fake_fleet(tmp_path, engine=True, certificate=certificate, enforce=True):
        _pve(base, "PUT", "pools", poolid=ENGINE_POOL, vms="9010")
        token_id, secret = engine_token(tmp_path)
        yield Fleet(base, tmp_path, Token(token_id, Secret(secret)), pki)


def _made(identity: str, **fields) -> Record:
    return Record(kind="vm", uuid=identity, stage="unfinished", **fields)


def test_a_record_written_reads_back_field_for_field_beside_the_notes_the_vm_had(fleet) -> None:
    # alpha was cloned from the fake's template, so its notes began as the template's: today's record line, which is a
    # person's text here, kept.
    had = fleet.notes()
    assert had.startswith(proxmox.TEMPLATE_RECORD_1X) and block.parse(had) is None
    whole = dataclasses.replace(WHOLE, uuid=fleet.identity())
    with fleet.client() as client:
        assert record.write(client, ALPHA, lambda current: whole) == whole
        assert record.read(client.config("pve", 9010)) == whole
    assert fleet.notes() == f"{had}\n\n{block.render('', whole.fields())}"
    (sent,) = fleet.puts()
    assert set(sent) == {"description", "digest"}


def test_of_two_writers_the_second_is_refused_reads_again_and_lands_with_the_first_s_change(fleet) -> None:
    identity = fleet.identity()
    seen: list[Record | None] = []
    with fleet.client() as client:

        def second(current: Record | None) -> Record:
            seen.append(current)
            if len(seen) == 1:
                # The first writer lands between this read and its write.
                record.write(client, ALPHA, lambda now: _made(identity, job="3f9a0c1e7b2d4a58"))
            return dataclasses.replace(current or _made(identity), provisioned=True)

        written = record.write(client, ALPHA, second)
        assert record.read(client.config("pve", 9010)) == written
    assert seen == [None, _made(identity, job="3f9a0c1e7b2d4a58")]
    assert (written.job, written.provisioned) == ("3f9a0c1e7b2d4a58", True)
    first, refused, landed = fleet.puts()
    assert refused["digest"] == first["digest"] != landed["digest"]


def test_a_write_survives_a_person_s_edit_between_its_read_and_its_write(fleet) -> None:
    identity, had = fleet.identity(), fleet.notes()
    applied = []
    with fleet.client() as client:

        def change(current: Record | None) -> Record:
            applied.append(current)
            if len(applied) == 1:
                # A person adds a line to the notes and a tag in Proxmox's page meanwhile.
                fleet.plant(description=f"{had}\nadded by a person", tags="agent-warehouse;mine")
            return _made(identity)

        assert record.write(client, ALPHA, change) == _made(identity)
    assert applied == [None, None]
    notes = fleet.notes()
    assert notes.startswith(f"{had}\nadded by a person\n\n")
    assert block.parse(notes) == _fields(_made(identity)) and fleet.config()["tags"] == "agent-warehouse;mine"
    assert len(fleet.puts()) == 2


def test_a_writer_that_always_loses_raises_after_the_client_s_tries_and_writes_nothing(fleet) -> None:
    identity = fleet.identity()
    applied = []

    def loses(current: Record | None) -> Record:
        applied.append(current)
        fleet.plant(cores=len(applied))
        return _made(identity)

    with fleet.client() as client, pytest.raises(DigestMismatch):
        record.write(client, ALPHA, loses)
    assert len(applied) == EDITS and block.parse(fleet.notes()) is None


def test_a_second_write_of_the_same_record_sends_nothing(fleet) -> None:
    made = _made(fleet.identity(), repositories=("one",))
    with fleet.client() as client:
        record.write(client, ALPHA, lambda current: made)
        # Proxmox takes the white space off the notes' end: a person's trailing lines change nothing either.
        fleet.plant(description=fleet.notes() + "\n\n  \n")
        assert record.write(client, ALPHA, lambda current: current) == made
        assert record.write(client, ALPHA, lambda current: None) == made
        assert record.write(client, ALPHA, lambda current: dataclasses.replace(current)) == made
    assert len(fleet.puts()) == 1


@pytest.mark.parametrize("number", ["2", "x"])
def test_a_block_the_engine_cannot_read_is_never_rewritten_and_nothing_is_sent(fleet, number: str) -> None:
    planted = f"mine\n{_block('kind: vm', 'stage: finished', 'colour: blue', number=number)}"
    fleet.plant(description=planted)
    applied = []
    with fleet.client() as client, pytest.raises(Unreadable) as refused:
        record.write(client, ALPHA, lambda current: applied.append(current) or _made(fleet.identity()))
    assert "VM 9010's notes" in str(refused.value)
    assert applied == [] and fleet.puts() == [] and fleet.notes() == planted


def test_a_copy_s_record_is_never_written_over_and_change_is_never_applied(fleet) -> None:
    # As a clone made without a description of its own carries its source's record: beta's, in alpha's notes.
    copied = block.render("mine", _made(fleet.identity(9011)).fields())
    fleet.plant(description=copied)
    applied = []
    with fleet.client() as client:
        with pytest.raises(Copy) as refused:
            record.read(client.config("pve", 9010))
        assert "VM 9010's notes" in str(refused.value)
        with pytest.raises(Copy):
            record.write(client, ALPHA, lambda current: applied.append(current) or current)
        with pytest.raises(Copy):
            record.write(client, ALPHA, lambda current: applied.append(current) or _made(fleet.identity()))
    assert applied == [] and fleet.puts() == [] and fleet.notes() == copied


def test_a_record_naming_another_vm_s_uuid_is_never_written(fleet) -> None:
    other = fleet.identity(9011)
    with fleet.client() as client, pytest.raises(ValueError, match="written only for the VM it names"):
        record.write(client, ALPHA, lambda current: _made(other))
    assert fleet.puts() == [] and block.parse(fleet.notes()) is None


def test_notes_past_8192_characters_are_refused_before_anything_is_sent(fleet) -> None:
    _pve(fleet.base, "PUT", "fake/notes-limit", bytes=block.MOST)
    fleet.plant(description="x" * (block.MOST - 20))
    with fleet.client() as client, pytest.raises(TooLong) as refused:
        record.write(client, ALPHA, lambda current: _made(fleet.identity()))
    assert "VM 9010's notes" in str(refused.value) and "nothing was sent" in str(refused.value)
    assert fleet.puts() == []


def test_a_write_of_the_most_notes_proxmox_takes_lands(fleet) -> None:
    _pve(fleet.base, "PUT", "fake/notes-limit", bytes=block.MOST)
    made = _made(fleet.identity())
    fleet.plant(description="x" * (block.MOST - len(block.render("", made.fields())) - 2))
    with fleet.client() as client:
        record.write(client, ALPHA, lambda current: made)
    assert len(fleet.notes()) == block.MOST and block.parse(fleet.notes()) == _fields(made)


def test_the_label_written_is_the_setting_vm_label_s_value_beside_a_person_s_tags(fleet) -> None:
    default = registry.row("vm_label").default
    assert default == "agent-warehouse" and registry.row("vm_label").form.allows(default)
    fleet.plant(tags="mine")
    with fleet.client() as client:
        record.label(client, ALPHA, default)
        assert client.config("pve", 9010).tags == ("agent-warehouse", "mine")
        # Carried already, in any case: nothing is sent.
        for again in (default, "Agent-Warehouse"):
            record.label(client, ALPHA, again)
        record.label(client, ALPHA, "lab-fleet")
        assert client.config("pve", 9010).tags == ("agent-warehouse", "lab-fleet", "mine")
        for refused in ("two words", "", "a,b", "a;b", "-dash"):
            with pytest.raises(ValueError, match="vm_label is one Proxmox tag"):
                record.label(client, ALPHA, refused)
    puts = fleet.puts()
    assert [sorted(sent) for sent in puts] == [["digest", "tags"]] * 2
    assert [sent["tags"] for sent in puts] == ["mine,agent-warehouse", "agent-warehouse,mine,lab-fleet"]
    assert block.parse(fleet.notes()) is None


# ------------------------------------------------------------------------------------------- the rules ----

# The modules of the new engine that name a VM's description, each with why: no other may, so that no module but
# engine/record.py sends one to Proxmox.
NAMES_A_DESCRIPTION = {
    "lib/claude_vm/engine/record.py": "the one writer of a VM's notes",
    "lib/claude_vm/pve/client.py": "the client, which sends a clone's description as its caller gives it",
    "lib/claude_vm/pve/privileges.py": "the table, whose row for a configuration's write names the key it covers",
    "lib/claude_vm/pve/answers.py": "a configuration read back, its description among its fields",
    "lib/claude_vm/fleet/passthrough.py": "a role's spec read, whose description of an option is what a person reads of"
    " it: no VM's, and nothing sent",
    "lib/claude_vm/pve/template.py": "a template's record handed to today's proxmox_template_drift as the line of notes"
    " it reads, in a configuration of its own: the judgement, which holds no client and sends nothing"
    " (tests/unit/test_aw_engine_template.py)",
}


def _names_a_description(tree: ast.AST) -> list[int]:
    """The lines of tree that name a description to send: the key as a string, or a keyword of a call."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if (isinstance(node, ast.Constant) and node.value == "description")
        or (isinstance(node, ast.keyword) and node.arg == "description")
    ]


def test_no_module_but_engine_record_sends_a_description_to_proxmox() -> None:
    naming = {_rel(path) for path in _new_engine() if _names_a_description(ast.parse(path.read_text()))}
    assert naming == set(NAMES_A_DESCRIPTION), "a VM's description is sent by engine/record.py alone"
    assert all(reason.strip() for reason in NAMES_A_DESCRIPTION.values())


@pytest.mark.parametrize(
    "source",
    [
        'client.write_config("pve", 9010, {"description": notes}, digest)',
        'client.edit_config("pve", 9010, lambda config: dict(description=notes))',
        'client.clone("pve", 9000, 9020, "delta", pool, description=notes)',
        'changes["description"] = notes',
    ],
)
def test_the_scan_finds_a_description_named_to_send(source: str) -> None:
    assert _names_a_description(ast.parse(source)) == [1]


def test_the_record_s_text_imports_nothing_but_the_standard_library_and_text(home) -> None:
    module = "claude_vm.pve.record"
    added = imports_of(module=module, home=home) - imports_of(module="claude_vm", home=home)
    assert module in added, sorted(added)
    assert {name for name in added if name.split(".")[0] == "claude_vm"} == {"claude_vm.pve", module, "claude_vm.text"}
    assert sorted({name.split(".")[0] for name in added} - set(sys.stdlib_module_names) - {"claude_vm"}) == []
