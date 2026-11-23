"""The fleet's ID, its record and its mark (lib/claude_vm/fleet/record.py, mark.py and files.py; docs/architecture/
engine.md §16.9).

A fleet's ID is a random UUID in its lowercase canonical form, held to `schema/patterns.py`'s `fleet_id`, the one
pattern the pool's mark and an adopted machine's marker share. `fleet.json` holds it with the format number of every
store: it reads back as written, before `init` it is not there, anything there that is no record is refused by its path
and never by what it holds, and a format newer than this program reads is refused by the store's name, as
`refuse_newer` refuses a store a caller reads. A write reads what is there under the lock on its folder first: never
over another fleet's record, one it cannot read or a newer one, and never lowering or dropping a store's number, so two
writers lose neither change.

The mark is found wherever it stands in the pool's comment, a person's note beside it on its line or another; twice,
or begun and not read as one, it is refused, never taken for a comment with no mark, and no refusal says the comment.
`render` keeps a person's note byte for byte and writes `controller=none` alone, never over another mark. `may_change`
refuses with one sentence a pool with no mark, a mark of another fleet and a mark that names any control VM.

Every folder of the fleet's is made through `files.folder`, at 0700 whatever the umask, and refused where a link stands
in its place, the jobs' and the locks' among them, and a scan holds that nothing else of `fleet/` or `runs/` makes one.
The rule with a test of its own, beside test_anchors.py's scan of these modules: every pattern ends at the end of the
text, so a value with a newline after it is refused. The three modules import the standard library alone.
"""

from __future__ import annotations

import ast
import os
import stat
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from aw_kit import environment, imports_of
from harness import REPO

from claude_vm.fleet import files, mark, paths, record
from claude_vm.fleet.mark import Mark
from claude_vm.fleet.record import FleetRecord
from claude_vm.runs import job as jobs
from claude_vm.runs import locks
from claude_vm.schema import patterns

LIB = REPO / "lib"
FLEET = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
OTHER = "0d6c9b1e-2f3a-4b5c-8d7e-6f5a4b3c2d1e"
CONTROLLER = "a1b2c3d4-e5f6-4a7b-9c8d-0e1f2a3b4c5d"
LINE = f"agent-warehouse fleet={FLEET} controller=none"
# A note of the person's own, which no refusal ever says back.
NOTE = "unit-test-aw-fleet-mark-note"


def _home() -> paths.Home:
    """The test's own fleet home, as fleet/paths.py names it (AGENT_WAREHOUSE_HOME, set by conftest.py's aw_home)."""
    return paths.home(REPO)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ---------------------------------------------------------------- the fleet's ID ----


def test_a_new_fleet_s_id_is_a_random_uuid_in_its_lowercase_canonical_form() -> None:
    made = {record.new_id() for _ in range(20)}
    assert len(made) == 20
    for each in made:
        assert patterns.full("fleet_id", each) and str(uuid.UUID(each)) == each and uuid.UUID(each).version == 4


def test_the_mark_and_the_record_hold_an_id_to_the_one_pattern_the_marker_shares() -> None:
    # One pattern of schema/patterns.py, by its name: the mark finds its IDs with its text, and every reader holds the
    # value again whole with patterns.full.
    assert mark._ID == patterns.PATTERNS["fleet_id"].text
    capitals = [FLEET[:start] + FLEET[start:end].upper() + FLEET[end:] for start, end in ((0, 8), (24, 36))]
    for given in (FLEET.upper(), *capitals, FLEET + "\n", "\n" + FLEET, FLEET[:-1], "{" + FLEET + "}", ""):
        assert not patterns.full("fleet_id", given)
        with pytest.raises(ValueError, match="lowercase canonical form"):
            FleetRecord(1, given, record.KNOWN)
        with pytest.raises(ValueError, match="lowercase canonical form"):
            Mark(given, "none")
        with pytest.raises(ValueError, match="lowercase canonical form"):
            Mark(FLEET, given)


# ---------------------------------------------------------------- fleet.json ----


def test_a_record_reads_back_as_written_and_its_file_is_the_owner_s_alone() -> None:
    # The home is not made yet: the write makes it, and the fleet's folder in it, at 0700.
    where = _home()
    assert record.read(where) is None and not where.root.exists(), "a read makes nothing"
    written = record.first(FLEET)
    assert written == FleetRecord(1, FLEET, {"vault": 1, "settings": 1, "hosts": 1, "jobs": 1})
    record.write(where, written)
    found = record.path(where)
    assert found == paths.fleet_dir(where) / "fleet.json"
    assert record.read(where) == written
    assert found.read_text() == (
        '{\n  "format": 1,\n  "fleet": "' + FLEET + '",\n  "stores": {\n    "vault": 1,\n    "settings": 1,\n'
        '    "hosts": 1,\n    "jobs": 1\n  }\n}\n'
    )
    assert [_mode(each) for each in (where.root, found.parent, found)] == [0o700, 0o700, 0o600]
    assert sorted(path.name for path in found.parent.iterdir()) == ["fleet.json"], "no scratch file is left"


def test_a_record_s_numbers_cannot_be_changed_by_a_reader() -> None:
    made = record.first(FLEET)
    with pytest.raises(TypeError):
        made.stores["jobs"] = 2  # type: ignore[index]
    assert record.KNOWN == {"vault": 1, "settings": 1, "hosts": 1, "jobs": 1} and record.STORES == tuple(record.KNOWN)


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ("{", "it does not read as JSON"),
        ("[1, 2]", "it is not a mapping"),
        ('{"fleet": "' + FLEET + '", "stores": {}}', "it has no format number"),
        ('{"format": 0, "fleet": "' + FLEET + '", "stores": {}}', "it has no format number"),
        ('{"format": true, "fleet": "' + FLEET + '", "stores": {}}', "it has no format number"),
        ('{"format": "1", "fleet": "' + FLEET + '", "stores": {}}', "it has no format number"),
        ('{"format": 1, "fleet": "' + FLEET + '", "stores": {}, "controller": "none"}', "a field that one has not"),
        ('{"format": 1, "fleet": "' + FLEET.upper() + '", "stores": {}}', "its fleet is no fleet's ID"),
        ('{"format": 1, "fleet": "' + FLEET + '\\n", "stores": {}}', "its fleet is no fleet's ID"),
        ('{"format": 1, "fleet": 7, "stores": {}}', "its fleet is no fleet's ID"),
        ('{"format": 1, "fleet": "' + FLEET + '", "stores": [1]}', "it numbers no store"),
        ('{"format": 1, "fleet": "' + FLEET + '", "stores": {"vault": 1, "settings": 1, "hosts": 1}}', "lacks jobs"),
        (
            '{"format": 1, "fleet": "' + FLEET + '", "stores": {"vault": 1, "settings": 1, "hosts": 1, "jobs": 0}}',
            "with a whole number from 1",
        ),
        (
            '{"format": 1, "fleet": "' + FLEET + '", "stores": {"vault": 1, "settings": 1, "hosts": 1, "jobs": true}}',
            "with a whole number from 1",
        ),
        (
            '{"format": 1, "fleet": "' + FLEET + '", "stores": {"vault": 1, "settings": 1, "hosts": 1, "jobs": 1, '
            '"Extra-Store": 1}}',
            "by its name",
        ),
    ],
    ids=[
        "not-json",
        "a-list",
        "no-format",
        "format-0",
        "format-true",
        "format-a-string",
        "a-field-of-a-later-format",
        "fleet-in-capitals",
        "fleet-and-a-newline",
        "fleet-a-number",
        "stores-a-list",
        "a-store-missing",
        "a-store-at-0",
        "a-store-at-true",
        "a-store-misnamed",
    ],
)
def test_a_file_that_is_no_record_is_refused_by_its_path_and_never_by_what_it_holds(home, given, said) -> None:
    where = _home()
    found = record.path(where)
    found.parent.mkdir(parents=True)
    found.write_text(given.replace("{}", '{"note": "' + NOTE + '"}', 1) if "{}" in given else given)
    with pytest.raises(record.Unreadable) as refused:
        record.read(where)
    sentence = str(refused.value)
    assert sentence.startswith(f"error: {found} is not a fleet's record") and said in sentence, sentence
    assert NOTE not in sentence and "\n" not in sentence


def test_a_record_nested_past_what_json_reads_is_no_record(home, monkeypatch) -> None:
    # json raises RecursionError for nesting past the interpreter's limit, where that is below what 64 KiB can nest.
    def nested(text: str) -> object:
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")

    where = _home()
    record.path(where).parent.mkdir(parents=True)
    record.path(where).write_text("[[[]]]")
    monkeypatch.setattr(record.json, "loads", nested)
    with pytest.raises(record.Unreadable, match="it does not read as JSON"):
        record.read(where)


def test_a_folder_text_that_is_not_utf_8_and_a_file_too_large_are_no_record(home) -> None:
    where = _home()
    found = record.path(where)
    found.mkdir(parents=True)
    with pytest.raises(record.Unreadable, match="it is a folder"):
        record.read(where)
    found.rmdir()
    found.write_bytes(b'{"format": 1, "fleet": "\xff"}')
    with pytest.raises(record.Unreadable, match="it is not text"):
        record.read(where)
    found.write_text(" " * (record.MOST + 1))
    with pytest.raises(record.Unreadable, match="larger than one is"):
        record.read(where)


def test_a_record_in_a_newer_format_is_refused_by_name_and_never_read(home) -> None:
    where = _home()
    found = record.path(where)
    found.parent.mkdir(parents=True)
    # A field a later release adds comes with a raise of the format: this program reads nothing of it.
    found.write_text('{"format": 2, "fleet": "' + FLEET + '", "stores": {}, "controllers": ["' + NOTE + '"]}')
    with pytest.raises(record.Newer) as refused:
        record.read(where)
    assert str(refused.value) == (
        "error: this fleet keeps its record (fleet.json) in format 2, where this program reads 1 at most: run the"
        " release of agent-warehouse that wrote it, or a newer one"
    )
    assert isinstance(refused.value, record.Unreadable)


def test_a_store_in_a_newer_format_is_refused_by_its_name_to_a_caller_that_reads_it() -> None:
    assert record.refuse_newer(record.first(FLEET), record.KNOWN) is None
    jobs_newer = FleetRecord(1, FLEET, {**record.KNOWN, "jobs": 2})
    assert record.refuse_newer(jobs_newer, record.KNOWN) == (
        "error: this fleet keeps its jobs in format 2, where this program reads 1 at most: run the release of"
        " agent-warehouse that wrote it, or a newer one"
    )
    assert record.refuse_newer(jobs_newer, {"jobs": 1}) == record.refuse_newer(jobs_newer, record.KNOWN)
    # A caller that reads the vault alone is not refused for the jobs, nor one that reads the jobs' newer format.
    assert record.refuse_newer(jobs_newer, {"vault": 1}) is None
    assert record.refuse_newer(jobs_newer, {"jobs": 2}) is None
    both = FleetRecord(1, FLEET, {"vault": 3, "settings": 1, "hosts": 1, "jobs": 2})
    assert record.refuse_newer(both, record.KNOWN) == (
        "error: this fleet keeps its vault in format 3, where this program reads 1 at most and its jobs in format 2,"
        " where this program reads 1 at most: run the release of agent-warehouse that wrote them, or a newer one"
    )
    for known in ({"machines": 1}, {"jobs": 0}, {"jobs": True}):
        with pytest.raises(ValueError, match="no store of a fleet's record"):
            record.refuse_newer(both, known)


def test_a_later_release_s_store_is_read_and_kept(home) -> None:
    where = _home()
    later = FleetRecord(1, FLEET, {**record.KNOWN, "control": 3})
    record.write(where, later)
    assert record.read(where) == later
    assert record.refuse_newer(later, record.KNOWN) is None, "this program reads nothing of a store it does not know"


def test_a_write_never_changes_the_fleet_s_id(home) -> None:
    where = _home()
    record.write(where, record.first(FLEET))
    before = record.path(where).read_bytes()
    with pytest.raises(record.Refused) as refused:
        record.write(where, record.first(OTHER))
    assert str(refused.value) == (
        f"error: {record.path(where)} is the record of the fleet {FLEET}, and a fleet's ID never changes: the record"
        f" of {OTHER} is not written over it"
    )
    assert record.path(where).read_bytes() == before


@pytest.mark.parametrize(
    ("there", "said"),
    [
        ({**record.KNOWN, "jobs": 2}, "keeps its jobs in format 2, which the record to be written would lower or drop"),
        (
            {**record.KNOWN, "vault": 2, "control": 1},
            "keeps its vault in format 2 and its control in format 1, which the record to be written would lower",
        ),
    ],
)
def test_a_write_never_lowers_or_drops_a_store_s_number(home, there, said) -> None:
    where = _home()
    record.write(where, FleetRecord(1, FLEET, there))
    before = record.path(where).read_bytes()
    with pytest.raises(record.Refused) as refused:
        record.write(where, record.first(FLEET))
    assert said in str(refused.value) and "no program undoes the format a newer one wrote" in str(refused.value)
    assert record.path(where).read_bytes() == before


@pytest.mark.parametrize(
    "there",
    ["not json", '{"format": 2, "fleet": "' + FLEET + '", "stores": {}}'],
    ids=["unreadable", "newer"],
)
def test_nothing_is_written_over_a_record_this_program_cannot_read(home, there) -> None:
    where = _home()
    found = record.path(where)
    found.parent.mkdir(parents=True)
    found.write_text(there)
    with pytest.raises(record.Unreadable):
        record.write(where, record.first(FLEET))
    assert found.read_text() == there


def test_two_writers_of_fleet_json_under_the_lock_lose_neither_change(home) -> None:
    # Each raises a store of its own 60 times, from a record it read, at once: the write reads what is there under the
    # lock and refuses a record that would lower the other's, so a writer that read before the other wrote reads again.
    # Unlocked, or compared outside the lock, one would put back the number the other had raised.
    where = _home()
    record.write(where, record.first(FLEET))
    writer = (
        "import sys\n"
        f"sys.path.insert(0, {str(LIB)!r})\n"
        "from claude_vm.fleet import paths, record\n"
        f"where = paths.home({str(REPO)!r})\n"
        "store = sys.argv[1]\n"
        "sys.stdin.readline()\n"
        "for _ in range(60):\n"
        "    while True:\n"
        "        found = record.read(where)\n"
        "        stores = {**found.stores, store: found.stores[store] + 1}\n"
        "        raised = record.FleetRecord(found.format, found.fleet, stores)\n"
        "        try:\n"
        "            record.write(where, raised)\n"
        "        except record.Refused:\n"
        "            continue\n"
        "        break\n"
    )
    writers = [
        subprocess.Popen(
            [sys.executable, "-I", "-c", writer, store], stdin=subprocess.PIPE, text=True, env=environment(home)
        )
        for store in ("vault", "jobs")
    ]
    try:
        for each in writers:
            assert each.stdin is not None
            each.stdin.write("go\n")
            each.stdin.close()
        assert [each.wait(timeout=180) for each in writers] == [0, 0]
    finally:
        for each in writers:
            if each.poll() is None:
                each.kill()
                each.wait()
    assert record.read(where) == FleetRecord(1, FLEET, {"vault": 61, "settings": 1, "hosts": 1, "jobs": 61})
    assert sorted(path.name for path in record.path(where).parent.iterdir()) == ["fleet.json"]


# ---------------------------------------------------------------- the mark in a pool's comment ----


@pytest.mark.parametrize(
    "comment",
    [
        LINE,
        f"{LINE}\n",
        f"the lab's pool {LINE}",
        f"the lab's pool {LINE} kept by the owner",
        f"the lab's pool\n{LINE}\nkept by the owner",
        f"\t{LINE}\t",
        f"first line\n\n{LINE}",
    ],
    ids=["alone", "its-line-ended", "after-a-note", "inside-a-note", "on-a-line-of-its-own", "tabs", "last"],
)
def test_the_mark_is_found_wherever_it_stands_in_the_comment(comment: str) -> None:
    assert mark.parse(comment) == Mark(FLEET, "none")


def test_a_mark_that_names_a_control_vm_reads_as_one() -> None:
    found = mark.parse(f"agent-warehouse fleet={FLEET} controller={CONTROLLER}")
    assert found == Mark(FLEET, CONTROLLER) and found.line == f"agent-warehouse fleet={FLEET} controller={CONTROLLER}"


@pytest.mark.parametrize(
    "comment",
    [
        "",
        "the lab's pool",
        "agent-warehouse VMs live here",
        "agent-warehouse",
        "fleet=" + FLEET,
        f"Agent-Warehouse {LINE[16:]}",
    ],
)
def test_a_comment_with_no_mark_is_none(comment: str) -> None:
    assert mark.parse(comment) is None


@pytest.mark.parametrize(
    "comment",
    [
        f"{LINE} {LINE}",
        f"{LINE}\n{LINE}",
        f"{LINE}\nagent-warehouse fleet={OTHER} controller=none",
        f"{LINE} agent-warehouse fleet=",
    ],
    ids=["twice-on-a-line", "twice-on-two-lines", "two-fleets", "begun-again"],
)
def test_a_comment_that_holds_the_mark_twice_is_refused(comment: str) -> None:
    with pytest.raises(mark.Unreadable, match="more than once"):
        mark.parse(comment)


@pytest.mark.parametrize(
    "comment",
    [
        LINE.replace(FLEET, FLEET.upper()),
        LINE.replace(FLEET, FLEET[:8].upper() + FLEET[8:]),
        LINE.replace(FLEET, FLEET[:-1]),
        LINE.replace(FLEET, FLEET + "0"),
        LINE.replace(FLEET, "{" + FLEET + "}"),
        LINE.replace("none", "None"),
        LINE.replace("none", "nonesuch"),
        LINE.replace("none", ""),
        LINE.replace("none", CONTROLLER.upper()),
        LINE.replace("controller=none", ""),
        LINE.replace(" controller", "  controller"),
        LINE.replace("fleet=", "fleet= "),
        f"agent-warehouse controller=none fleet={FLEET}",
        LINE + ".",
        "a note" + LINE,
        # A newline after a value, inside the mark: what $ would let through.
        LINE.replace(" controller", "\n controller"),
        LINE.replace(" controller", "\ncontroller"),
        LINE.replace("agent-warehouse ", "agent-warehouse\n"),
        LINE.replace(FLEET, FLEET + "\n"),
    ],
)
def test_a_mark_that_does_not_read_as_one_is_refused_never_taken_for_none(comment: str) -> None:
    with pytest.raises(mark.Unreadable) as refused:
        mark.parse(f"{NOTE} {comment}")
    assert NOTE not in str(refused.value) and str(refused.value).startswith("error: the pool's comment holds a mark")


def test_render_keeps_a_person_s_note_byte_for_byte_and_writes_controller_none() -> None:
    ours = Mark(FLEET, "none")
    assert mark.render(ours, "") == LINE
    assert mark.render(ours, "the lab's pool") == f"the lab's pool {LINE}"
    assert mark.render(ours, "the lab's pool\n") == f"the lab's pool\n{LINE}"
    assert mark.render(ours, "the lab's pool  ") == f"the lab's pool  {LINE}"
    for comment in (LINE, f"the lab's pool {LINE} kept", f"a\n{LINE}\nb"):
        assert mark.render(ours, comment) == comment, "a comment that holds the mark already is kept as it is"
    for comment in ("", "x", "a note\nof two lines"):
        made = mark.render(ours, comment)
        assert made.startswith(comment) and mark.parse(made) == ours


def test_render_never_writes_a_control_vm_or_over_another_mark() -> None:
    with pytest.raises(ValueError, match="controller=none alone"):
        mark.render(Mark(FLEET, CONTROLLER), "")
    with pytest.raises(mark.Refused) as refused:
        mark.render(Mark(FLEET, "none"), f"a note agent-warehouse fleet={OTHER} controller=none")
    assert str(refused.value) == mark.may_change(Mark(OTHER, "none"), record.first(FLEET))
    with pytest.raises(mark.Refused) as refused:
        mark.render(Mark(FLEET, "none"), f"agent-warehouse fleet={FLEET} controller={CONTROLLER}")
    assert str(refused.value) == f"error: this fleet has a control VM ({CONTROLLER}): change it there, or retire it"
    with pytest.raises(mark.Unreadable):
        mark.render(Mark(FLEET, "none"), LINE.replace(FLEET, FLEET.upper()))


def test_a_run_may_change_its_own_fleet_with_no_control_vm_alone() -> None:
    ours = record.first(FLEET)
    assert mark.may_change(Mark(FLEET, "none"), ours) is None
    assert mark.may_change(mark.parse(f"the lab's pool {LINE}"), ours) is None


def test_a_mark_of_another_fleet_is_refused_naming_both() -> None:
    assert mark.may_change(Mark(OTHER, "none"), record.first(FLEET)) == (
        f"error: the pool's mark names another fleet, {OTHER}, where this computer's folder is the fleet {FLEET}'s:"
        " nothing of it is changed from here; drive it from that fleet's own folder, or give this fleet a pool of its"
        " own"
    )
    # Another fleet's mark that names a control VM is another fleet's first.
    assert mark.may_change(Mark(OTHER, CONTROLLER), record.first(FLEET)) == mark.may_change(
        Mark(OTHER, "none"), record.first(FLEET)
    )


@pytest.mark.parametrize("controller", [CONTROLLER, FLEET, OTHER, "00000000-0000-0000-0000-000000000000"])
def test_a_mark_that_names_any_control_vm_is_refused(controller: str) -> None:
    assert mark.may_change(Mark(FLEET, controller), record.first(FLEET)) == (
        f"error: this fleet has a control VM ({controller}): change it there, or retire it"
    )


def test_a_pool_with_no_mark_is_refused() -> None:
    assert mark.may_change(None, record.first(FLEET)) == (
        f"error: the pool's comment carries no mark of a fleet, where this computer's folder is the fleet {FLEET}'s:"
        " nothing is changed until an administrator of the host puts the mark back"
    )


@pytest.mark.parametrize("controller", ["", "None", "NONE", "none\n", CONTROLLER + "\n", "controller"])
def test_a_mark_holds_its_controller_to_none_or_an_id(controller: str) -> None:
    with pytest.raises(ValueError, match="lowercase canonical form"):
        Mark(FLEET, controller)


# ---------------------------------------------------------------- the fleet's folders ----


def test_a_folder_is_made_at_0700_from_the_top_down_whatever_the_umask(tmp_path) -> None:
    asked = tmp_path / "a" / "b" / "c"
    was = os.umask(0o222)
    try:
        assert files.folder(asked) == asked
    finally:
        os.umask(was)
    assert [_mode(each) for each in (tmp_path / "a", tmp_path / "a" / "b", asked)] == [0o700] * 3
    assert files.folder(asked) == asked, "a folder that is there is taken"


def test_a_folder_that_is_there_keeps_its_mode(tmp_path) -> None:
    # The data folder holds tools/env-run's checkouts/ at the umask's mode: a looser mode is never a refusal.
    data = tmp_path / "data"
    data.mkdir()
    data.chmod(0o755)
    files.folder(data)
    files.folder(data / "kept")
    assert _mode(data) == 0o755 and _mode(data / "kept") == 0o700


def test_a_link_where_a_folder_of_the_fleet_s_goes_is_refused_and_nothing_is_made_through_it(tmp_path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = tmp_path / "fleet"
    link.symlink_to(elsewhere)
    with pytest.raises(files.Unsafe) as refused:
        files.folder(link)
    assert str(refused.value) == (
        f"error: {link} is a link, where a folder of the fleet's goes: nothing is written through it; put the folder"
        " itself there in its place"
    )
    with pytest.raises(files.Unsafe):
        files.write(link / "fleet.json", "{}\n")
    assert list(elsewhere.iterdir()) == []
    plain = tmp_path / "plain"
    plain.write_text("")
    with pytest.raises(files.Unsafe, match="is not a folder, where a folder of the fleet's goes"):
        files.folder(plain)
    with pytest.raises(files.Unsafe, match="a file stands where one of the folders above it goes"):
        files.folder(plain / "below")


def test_a_link_above_the_folder_is_the_person_s_and_followed(tmp_path) -> None:
    # A ~/.config kept in a dotfiles folder, macOS's /var.
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    (tmp_path / "config").symlink_to(dotfiles)
    assert files.folder(tmp_path / "config" / "agent-warehouse" / "fleet").is_dir()
    assert (dotfiles / "agent-warehouse" / "fleet").is_dir() and _mode(dotfiles / "agent-warehouse") == 0o700


def test_a_file_is_written_whole_new_at_0600_and_one_that_is_there_keeps_its_mode(tmp_path) -> None:
    new = tmp_path / "fleet" / "settings.yml"
    files.write(new, "a: 1\n")
    assert new.read_text() == "a: 1\n" and _mode(new) == 0o600 and _mode(new.parent) == 0o700
    new.chmod(0o640)
    files.write(new, "a: 2\n")
    assert new.read_text() == "a: 2\n" and _mode(new) == 0o640
    # A file kept elsewhere on purpose stays a link: the file it names is the one replaced, as store.replace does.
    kept = tmp_path / "kept.yml"
    kept.write_text("b: 1\n")
    linked = tmp_path / "fleet" / "linked.yml"
    linked.symlink_to(kept)
    files.write(linked, "b: 2\n")
    assert linked.is_symlink() and kept.read_text() == "b: 2\n"


def test_the_record_the_jobs_and_the_locks_refuse_a_link_where_their_folder_goes(home, tmp_path) -> None:
    where = _home()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    paths.fleet_dir(where).symlink_to(elsewhere)
    with pytest.raises(files.Unsafe):
        record.write(where, record.first(FLEET))
    jobs_dir = paths.jobs_dir(where)
    jobs_dir.parent.mkdir()
    jobs_dir.symlink_to(elsewhere)
    with pytest.raises(files.Unsafe):
        jobs.create(jobs_dir, "deploy", "aw deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True)
    paths.locks_dir(where).symlink_to(elsewhere)
    with pytest.raises(files.Unsafe):
        locks._folder(paths.state(where))
    assert list(elsewhere.iterdir()) == []


# Where a folder may be made in fleet/ and runs/ but through files.folder, each with why: files.py itself; a job's own
# folder, made by create with one mkdir that fails where anything is there; and a run's short-lived directory, under
# the per-user temporary directory, no folder of the fleet's.
MAKES_A_FOLDER = {
    ("lib/claude_vm/fleet/files.py", None): "the one maker of the fleet's folders",
    ("lib/claude_vm/runs/job.py", "create"): "the job's own folder, inside the jobs folder files.folder made",
    ("lib/claude_vm/runs/rundir.py", "make"): "a run's short-lived directory, by mkdtemp in the temporary directory",
}
FOLDER_MAKERS = {"mkdir", "makedirs", "mkdtemp"}


def _folders_made(tree: ast.AST) -> list[tuple[int, str | None]]:
    """Each call that makes a folder, by its line and the function it is in (None outside one)."""
    found = []

    def visit(node: ast.AST, function: str | None) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Call):
            called = node.func
            name = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", None)
            if name in FOLDER_MAKERS:
                found.append((node.lineno, function))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, None)
    return found


def test_nothing_of_fleet_or_runs_makes_a_folder_but_through_files() -> None:
    made = []
    for package in ("fleet", "runs"):
        for path in sorted((LIB / "claude_vm" / package).rglob("*.py")):
            where = path.relative_to(REPO).as_posix()
            for line, function in _folders_made(ast.parse(path.read_text())):
                if (where, None) not in MAKES_A_FOLDER and (where, function) not in MAKES_A_FOLDER:
                    made.append((where, line))
    assert made == [], "make a folder of the fleet's with fleet/files.py's folder"


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("os.mkdir(x)", [(1, None)]),
        ("def f():\n    Path(x).mkdir(parents=True)", [(2, "f")]),
        ("def g():\n    os.makedirs(x, exist_ok=True)", [(2, "g")]),
        ("mkdir(x)", [(1, None)]),
        ("files.folder(x)", []),
    ],
)
def test_the_scan_sees_each_way_to_make_a_folder(source: str, found: list) -> None:
    assert _folders_made(ast.parse(source)) == found


# ---------------------------------------------------------------- what the modules import ----


@pytest.mark.parametrize("module", ["claude_vm.fleet.record", "claude_vm.fleet.mark", "claude_vm.fleet.files"])
def test_the_modules_import_nothing_outside_the_standard_library(home, module: str) -> None:
    added = imports_of(module=module, home=home) - imports_of(module="claude_vm.fleet", home=home)
    assert module in added, sorted(added)
    assert sorted({name.split(".")[0] for name in added} - set(sys.stdlib_module_names) - {"claude_vm"}) == []
    assert not added & {"asyncio", "subprocess", "uuid"}, "uuid is imported when an ID is made, never before"
