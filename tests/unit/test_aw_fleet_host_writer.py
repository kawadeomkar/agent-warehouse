"""host.yml's one writer, and its anchor's (lib/claude_vm/fleet/hosts.py's write; docs/architecture/engine.md §16.9).

A host's host.yml is written by write alone, whole, under the lock on its folder, only while its files are as the caller
read them (Layout.state's digest), every field held to what the reader takes before anything is written and read back
through the reader after; its anchor, proxmox-trust.pem, with the anchor's kind and fingerprint, in one write, in an
order a run stopped after any step leaves readable: never host.yml naming a kind with no anchor, nor an anchor with no
kind. Every fleet is in the test's own folder (AGENT_WAREHOUSE_HOME), with made-up addresses of the documentation's
range and made-up certificates: the writer holds an anchor to the shape of PEM certificates, and whether each is one is
the client's to say (pve/pin.py). The scan of fleet/'s writers (tests/unit/test_aw_fleet_overrides.py) names hosts.py;
here, its writes are _commit's alone, and only write calls it.
"""

from __future__ import annotations

import ast
import os
import re
import stat
from pathlib import Path

import pytest
import yaml
from harness import REPO
from test_aw_fleet_overrides import _writes

from claude_vm.fleet import hosts, paths
from claude_vm.fleet.hosts import MISSING, UNCHANGED, Anchor, Layout, reach
from claude_vm.hosts.model import Refused, Request

HOSTS = REPO / "lib" / "claude_vm" / "fleet" / "hosts.py"
FIELDS = {"address": "192.0.2.20", "token_user": "aw@pve", "token_id": "aw"}
SETTINGS = {
    "proxmox_node": "lab",
    "proxmox_storage": "local-lvm",
    "proxmox_bridge": "vmbr0",
    "proxmox_template_vmid": 9000,
}
# Made-up certificates of the shape PEM armours one in: no key of them is anywhere.
OLD = "-----BEGIN CERTIFICATE-----\n" + "QUJD" * 16 + "\n-----END CERTIFICATE-----\n"
NEW = "-----BEGIN CERTIFICATE-----\n" + "REVG" * 16 + "\n-----END CERTIFICATE-----\n"
OLD_FINGERPRINT = ":".join(["AB"] * 32)
NEW_FINGERPRINT = ":".join(["CD"] * 32)
# A key's PEM block, spelt in parts, since the secret scanners take the whole header for a key committed.
A_KEY = "-----BEGIN " + "PRIVATE KEY-----\n" + "QUJD" * 16 + "\n-----END " + "PRIVATE KEY-----\n"


def _layout() -> Layout:
    return Layout(paths.home(REPO))


def _folder(name: str = "lab") -> Path:
    return _layout().hosts_dir / name


def _set_up(change: dict | None = None, anchor: Anchor | None = None, name: str = "lab") -> str:
    """A host written by the one writer, as init would: its fields, its settings beside them, and an anchor or none."""
    layout = _layout()
    digest = hosts.write(
        layout, name, MISSING, change=lambda raw: raw.update({**FIELDS, **(change or {})}), anchor=anchor
    )
    (_folder(name) / "settings.yml").write_text(yaml.safe_dump(SETTINGS))
    return digest


def _files(name: str = "lab") -> dict[str, bytes]:
    """Every file of the host's folder, by name, as bytes."""
    return {path.name: path.read_bytes() for path in sorted(_folder(name).iterdir())}


def _read(name: str = "lab") -> tuple[hosts.HostFile, str | None]:
    layout = _layout()
    fields = layout.fields(name)
    return fields, layout.anchor(name, fields)


# ---------------------------------------------------------------- a host written whole ----


def test_a_new_host_is_written_whole_and_reads_back_as_meant(home) -> None:
    layout = _layout()
    assert layout.state("lab") == hosts.State(None, None, MISSING)
    digest = hosts.write(layout, "lab", MISSING, change=lambda raw: raw.update(FIELDS))
    folder = _folder()
    assert yaml.safe_load((folder / "host.yml").read_text()) == FIELDS
    assert stat.S_IMODE((folder / "host.yml").stat().st_mode) == paths.FILE_MODE
    assert stat.S_IMODE(folder.stat().st_mode) == paths.DIR_MODE
    state = layout.state("lab")
    assert state.digest == digest != MISSING
    assert state.fields == hosts.HostFile(**FIELDS) and state.anchor is None
    # The same again writes nothing, and says the same digest.
    assert hosts.write(layout, "lab", digest, change=lambda raw: raw.update(FIELDS)) == digest
    # What E7 and E12 add: an entry of made, the rest kept.
    made = {"what": "pool", "id": "agent-warehouse", "by": "engine"}
    after = hosts.write(layout, "lab", digest, change=lambda raw: raw.setdefault("made", []).append(made))
    assert after != digest
    assert layout.fields("lab").made == (hosts.Made(what="pool", id="agent-warehouse", by="engine"),)
    assert layout.fields("lab").address == FIELDS["address"]


def test_a_stale_digest_is_refused_and_nothing_is_written(home) -> None:
    digest = _set_up()
    layout = _layout()
    moved = hosts.write(layout, "lab", digest, change=lambda raw: raw.update(api_port=8007))
    before = _files()
    for expect in (digest, MISSING, "0" * 64):
        with pytest.raises(Refused, match="changed since it was read: nothing was written, so read it again"):
            hosts.write(layout, "lab", expect, change=lambda raw: raw.update(api_port=8443), anchor=Anchor(NEW, "ca"))
        assert _files() == before
    # A host not set up is written only from MISSING.
    with pytest.raises(Refused, match="changed since it was read"):
        hosts.write(layout, "nuc", moved, change=lambda raw: raw.update(FIELDS))
    assert not _folder("nuc").exists()


@pytest.mark.parametrize(
    ("change", "words"),
    [
        ({"address": "-oProxyCommand=x"}, "address takes a host's name or an IP address"),
        ({"api_port": 0}, "api_port takes a port"),
        ({"ssh_identity_file": "~/.ssh/id"}, "ssh_identity_file is a setting, not a field of the host's"),
        ({"token_secret": "swordfish-0000"}, "token_secret is no field of the host's"),
        ({"made": [{"what": "vm", "id": "100", "by": "engine"}]}, "made takes a list of what was made"),
    ],
)
def test_what_the_reader_would_refuse_is_never_written(home, change, words) -> None:
    digest = _set_up()
    before = _files()
    with pytest.raises(Refused, match=re.escape(words)) as refused:
        hosts.write(_layout(), "lab", digest, change=lambda raw: raw.update(change))
    assert "nothing written" in str(refused.value) and "swordfish" not in str(refused.value)
    assert _files() == before
    # A new host that lacks a field it must give is not made either.
    with pytest.raises(Refused, match="does not give token_user, token_id"):
        hosts.write(_layout(), "nuc", MISSING, change=lambda raw: raw.update(address="192.0.2.30"))
    assert not _folder("nuc").exists()


def test_the_anchor_s_kind_and_fingerprint_are_written_with_the_anchor_alone(home) -> None:
    digest = _set_up()
    before = _files()
    for change in ({"trust_kind": "ca"}, {"fingerprint": OLD_FINGERPRINT}):
        with pytest.raises(Refused, match="leaves trust_kind and fingerprint to the anchor's"):
            hosts.write(_layout(), "lab", digest, change=lambda raw, change=change: raw.update(change))
        assert _files() == before


@pytest.mark.parametrize(
    ("anchor", "words"),
    [
        (Anchor(A_KEY, "ca"), "an anchor holds PEM certificates and nothing else"),
        (Anchor(OLD + A_KEY, "ca"), "an anchor holds PEM certificates and nothing else"),
        (Anchor("# my CA\n" + OLD, "ca"), "an anchor holds PEM certificates and nothing else"),
        (Anchor("not a certificate", "node"), "an anchor holds PEM certificates and nothing else"),
        (Anchor(OLD, "store"), "an anchor's kind or fingerprint takes node or ca"),
        (Anchor(OLD, "node", "AB:CD"), "an anchor's kind or fingerprint takes a SHA-256 fingerprint"),
    ],
)
def test_an_anchor_that_is_not_certificates_alone_is_never_kept(home, anchor, words) -> None:
    digest = _set_up()
    before = _files()
    with pytest.raises(Refused, match=re.escape(words)) as refused:
        hosts.write(_layout(), "lab", digest, anchor=anchor)
    assert "QUJD" not in str(refused.value)
    assert _files() == before


def test_no_write_goes_through_a_link(home, tmp_path) -> None:
    digest = _set_up()
    folder = _folder()
    elsewhere = tmp_path / "elsewhere.pem"
    elsewhere.write_text(OLD)
    (folder / "proxmox-trust.pem").symlink_to(elsewhere)
    with pytest.raises(Refused, match="is a link, which a write never goes through: nothing was written"):
        hosts.write(_layout(), "lab", digest, anchor=Anchor(NEW, "ca"))
    assert elsewhere.read_text() == OLD
    (folder / "proxmox-trust.pem").unlink()
    real = tmp_path / "host.yml"
    real.write_text((folder / "host.yml").read_text())
    (folder / "host.yml").unlink()
    (folder / "host.yml").symlink_to(real)
    with pytest.raises(Refused, match="is a link"):
        hosts.write(_layout(), "lab", digest, change=lambda raw: raw.update(api_port=8443))
    assert yaml.safe_load(real.read_text()) == FIELDS


def _below(folder: Path) -> list[tuple[str, bytes | None]]:
    """Everything below folder, by its path there, each file's bytes and None for a folder."""
    return sorted(
        (path.relative_to(folder).as_posix(), path.read_bytes() if path.is_file() else None)
        for path in folder.rglob("*")
    )


@pytest.mark.parametrize("there", [False, True], ids=["a new host", "a host that is there"])
@pytest.mark.parametrize("linked", ["fleet", "hosts", "host"])
def test_a_link_where_a_folder_of_the_host_s_goes_is_refused_as_files_py_refuses_one(home, tmp_path, linked, there):
    # The host's folders are made, and held where they are there, through fleet/files.py's folder, which refuses a link
    # where one goes (tests/unit/test_aw_fleet_mark.py): the fleet's folder, its hosts' and the host's own. A write
    # sends nothing through it, a new host's or one's that is there.
    layout = _layout()
    expect = _set_up() if there else MISSING
    where = {"fleet": paths.fleet_dir(layout.home), "hosts": layout.hosts_dir, "host": _folder()}[linked]
    elsewhere = tmp_path / "elsewhere"
    if there:
        where.rename(elsewhere)
    else:
        where.parent.mkdir(mode=paths.DIR_MODE, parents=True, exist_ok=True)
        elsewhere.mkdir()
    where.symlink_to(elsewhere, target_is_directory=True)
    before = _below(elsewhere)
    with pytest.raises(Refused) as refused:
        hosts.write(
            layout,
            "lab",
            expect,
            change=lambda raw: raw.update({**FIELDS, "api_port": 8443}),
            anchor=Anchor(OLD, "ca"),
        )
    assert str(refused.value) == (
        f"{where} is a link, where a folder of the fleet's goes: nothing is written through it; put the folder itself"
        " there in its place"
    )
    assert _below(elsewhere) == before


# ---------------------------------------------------------------- the anchor ----


def test_an_anchor_is_kept_with_its_kind_and_fingerprint_and_taken_out_with_them(home) -> None:
    digest = _set_up()
    layout = _layout()
    folder = _folder()
    digest = hosts.write(layout, "lab", digest, anchor=Anchor(OLD, "node", OLD_FINGERPRINT.lower()))
    fields, anchor = _read()
    assert anchor == OLD and (fields.trust_kind, fields.fingerprint) == ("node", OLD_FINGERPRINT)
    assert stat.S_IMODE((folder / "proxmox-trust.pem").stat().st_mode) == hosts.ANCHOR_MODE
    assert reach(layout, Request()).anchor == OLD
    # Another in its place, a CA with no fingerprint: the fingerprint goes with the old anchor.
    digest = hosts.write(layout, "lab", digest, anchor=Anchor(NEW, "ca"))
    fields, anchor = _read()
    assert anchor == NEW and (fields.trust_kind, fields.fingerprint) == ("ca", "")
    assert "fingerprint" not in yaml.safe_load((folder / "host.yml").read_text())
    # UNCHANGED keeps it while host.yml changes.
    digest = hosts.write(layout, "lab", digest, change=lambda raw: raw.update(api_port=8443), anchor=UNCHANGED)
    fields, anchor = _read()
    assert anchor == NEW and fields.api_port == 8443 and fields.trust_kind == "ca"
    # None takes the anchor out, with its kind.
    digest = hosts.write(layout, "lab", digest, anchor=None)
    fields, anchor = _read()
    assert anchor is None and fields.trust_kind is None
    assert sorted(_files()) == ["host.yml", "settings.yml"]
    assert layout.state("lab").digest == digest


def test_a_host_with_a_kept_anchor_is_written_new_with_both(home) -> None:
    # init with nobody there (E4): host.yml and the anchor E3 worked out, in one write of a host not set up.
    _set_up(anchor=Anchor(OLD, "node", OLD_FINGERPRINT))
    fields, anchor = _read()
    assert anchor == OLD and fields.trust_kind == "node" and fields.address == FIELDS["address"]
    assert not (_folder() / hosts.STAGED).exists()


# ---------------------------------------------------------------- a run stopped between ----


class _Stopped(BaseException):
    """A run stopped where it stood: a kill, which nothing in the writer catches."""


def _stopping_after(monkeypatch, steps: int) -> None:
    """The writer stops once steps of its writes are on disk: each is followed by its folder's fsync (_synced)."""
    done = []
    synced = hosts._synced

    def counted(folder: Path) -> None:
        synced(folder)
        done.append(folder)
        if len(done) == steps:
            raise _Stopped

    monkeypatch.setattr(hosts, "_synced", counted)


def _pair(name: str = "lab") -> tuple[str | None, str | None, str]:
    """The anchor, its kind and its fingerprint, as the reader reads them: never one without the other."""
    fields, anchor = _read(name)
    assert (anchor is None) == (fields.trust_kind is None)
    return anchor, fields.trust_kind, fields.fingerprint


# Each change of the anchor: from what the host keeps to what it is to keep. Between them, a stop leaves the old or the
# new where one has an anchor and the other none; and, where both have one, the old or the new anchor beside the old or
# the new kind, each holding the host to no more than one of the two.
CHANGES = {
    "first anchor": (None, Anchor(NEW, "node", NEW_FINGERPRINT)),
    "no anchor any more": (Anchor(OLD, "node", OLD_FINGERPRINT), None),
    "a CA in place of a node": (Anchor(OLD, "node", OLD_FINGERPRINT), Anchor(NEW, "ca")),
    "a node in place of a CA": (Anchor(OLD, "ca"), Anchor(NEW, "node", NEW_FINGERPRINT)),
    "a renewed CA": (Anchor(OLD, "ca"), Anchor(NEW, "ca")),
    "a renewed CA, the old held to a fingerprint by hand": (Anchor(OLD, "ca", OLD_FINGERPRINT), Anchor(NEW, "ca")),
}


def _triple(anchor: Anchor | None) -> tuple[str | None, str | None, str]:
    return (None, None, "") if anchor is None else (anchor.pem, anchor.kind, anchor.fingerprint)


@pytest.mark.parametrize("change", list(CHANGES))
def test_a_run_stopped_after_any_step_leaves_the_anchor_and_its_kind_together(home, monkeypatch, change) -> None:
    old, new = CHANGES[change]
    steps = 1
    while True:
        for name in ("lab",):
            if _folder(name).exists():
                for path in sorted(_folder(name).iterdir()):
                    path.unlink()
                _folder(name).rmdir()
        digest = _set_up(anchor=old)
        with monkeypatch.context() as patch:
            _stopping_after(patch, steps)
            try:
                hosts.write(_layout(), "lab", digest, anchor=new)
                finished = True
            except _Stopped:
                finished = False
        pem, kind, fingerprint = _pair()
        olds, news = _triple(old), _triple(new)
        if old is None or new is None:
            assert (pem, kind, fingerprint) in (olds, news), (change, steps)
        else:
            assert pem in (olds[0], news[0]) and (kind, fingerprint) in (olds[1:], news[1:]), (change, steps)
            # Neither anchor is ever without the fingerprint its own end records: where the new records one, it is
            # there from host.yml's write on; where the old does, it goes only once the new anchor is in place.
            if news[2] and pem == news[0]:
                assert fingerprint == news[2], (change, steps)
            if olds[2] and pem == olds[0]:
                assert fingerprint, (change, steps)
        assert reach(_layout(), Request()).host.problem == ""
        # The next write, from what it reads now, ends where the stopped one meant to, and leaves nothing staged.
        state = _layout().state("lab")
        hosts.write(_layout(), "lab", state.digest, anchor=new)
        assert _pair() == _triple(new), (change, steps)
        assert not (_folder() / hosts.STAGED).exists()
        if finished:
            break
        steps += 1
    assert steps > 1


def test_the_staged_anchor_is_read_only_beside_a_kind_and_no_anchor_in_its_place(home) -> None:
    _set_up()
    folder = _folder()
    (folder / hosts.STAGED).write_text(NEW)
    # Staged, and host.yml not yet written: no anchor.
    assert _pair() == (None, None, "")
    # host.yml written, and the anchor not yet in its place: the staged one is it.
    raw = yaml.safe_load((folder / "host.yml").read_text())
    (folder / "host.yml").write_text(yaml.safe_dump({**raw, "trust_kind": "ca"}))
    assert _pair() == (NEW, "ca", "")
    # Beside an anchor in its place, it is no one's.
    (folder / "proxmox-trust.pem").write_text(OLD)
    assert _pair() == (OLD, "ca", "")
    # A kind with neither is still the reader's refusal, as is an anchor in its place with no host.yml.
    (folder / "proxmox-trust.pem").unlink()
    (folder / hosts.STAGED).unlink()
    with pytest.raises(Refused, match="gives a trust_kind, and .* is not there"):
        _read()
    (folder / "host.yml").unlink()
    (folder / "proxmox-trust.pem").write_text(OLD)
    with pytest.raises(Refused, match="proxmox-trust.pem is there, and .*host.yml is not"):
        _layout().state("lab")


# ---------------------------------------------------------------- the one writer, held ----


def test_hosts_py_writes_a_file_in_commit_alone_and_only_write_calls_it() -> None:
    source = HOSTS.read_text()
    assert {function for function, _ in _writes(source)} == {"_commit"}
    tree = ast.parse(source)
    callers = sorted(
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and node.id == "_commit" and function.name != "_commit"
    )
    assert callers == ["write"]
    # And nothing else in hosts.py takes a file out or makes one: unlink is _commit's, which write alone calls, and the
    # folders are made through fleet/files.py's folder, by _folders (test_aw_fleet_mark.py's MAKES_A_FOLDER holds it).
    unlinks = sorted(
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and node.attr in ("unlink", "mkdir", "rmdir", "remove")
    )
    assert set(unlinks) == {"_commit"}
    folders = sorted(
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and node.attr == "folder"
    )
    assert folders == ["_folders"]
    assert os.path.basename(HOSTS) == "hosts.py"
