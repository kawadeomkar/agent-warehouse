"""aw host: the hosts listed, one made current, one removed, on the new layout (lib/claude_vm/fleet/host_verb.py;
docs/architecture/engine.md §16.9).

The cases of tests/unit/test_hosts.py's list and switch, on the folders fleet/hosts.py reads: one host and two, the
JSON's promised keys, --for a VM whose alias another host records, the switch, and the removal typed, mistyped and
with no terminal to type at, and refused for the current host and the one host. The lock: a switch takes the fleet's
lock where runs/lock_sets.py's locks_for says fleet, and only there; it is refused while the lock is held, and nothing
else takes the lock while a switch holds it. The line the removal prints is parsed back as aw reads it. Every fleet is
two hosts in the test's own folder (AGENT_WAREHOUSE_HOME), with made-up addresses of the documentation's range.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
from collections.abc import Iterator

import pytest
from fleet_fixture import pinned
from harness import REPO

from claude_vm.cli import parse
from claude_vm.fleet import host_verb, hosts, overrides, paths, record
from claude_vm.runs import job as jobs
from claude_vm.runs import lock_sets, locks
from claude_vm.schema import printer
from claude_vm.schema.words import EXITS, PLACEHOLDER

FLEET = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
HOSTS = {"lab": "192.0.2.20", "lab2": "192.0.2.21"}
TOKEN = {"token_user": "aw@pve", "token_id": "aw"}
ALWAYS = {"proxmox_storage": "local-lvm", "proxmox_bridge": "vmbr0"}
# The keys of just host --json that 1.x promises, the document's and each row's (tests/unit/test_hosts.py's).
PROMISED_KEYS = ["format_version", "kind", "current_host", "hosts", "problem"]
PROMISED_ROW_KEYS = ["name", "address", "folder", "settings", "host_kind", "first", "current", "problem"]
PROMISED_WHERE_KEYS = ["host", "routed", "unplaced", "notes", "refusal"]


def _where() -> paths.Home:
    return paths.home(REPO)


def _fleet(*names: str) -> paths.Home:
    """The hosts named (both by default), each as init leaves it, the first current."""
    where = _where()
    record.write(where, record.first(FLEET))
    layout = hosts.Layout(where)
    for name in names or tuple(HOSTS):
        fields = {"address": HOSTS[name], **TOKEN}
        hosts.write(layout, name, hosts.MISSING, change=lambda raw, fields=fields: raw.update(fields))
        digest = overrides.MISSING
        for setting, value in {"proxmox_node": name, **ALWAYS}.items():
            digest = overrides.set(where, setting, value, name, digest)
        pinned(name)
    hosts.make_current(layout, (names or tuple(HOSTS))[0], overrides.read(where, None).client.digest)
    return where


def _current(where: paths.Home) -> object:
    return overrides.read(where, None).client.own.get("current_host")


class _Terminal(io.StringIO):
    """Standard input at a terminal, where what a person types is given."""

    def isatty(self) -> bool:
        return True


def _host(capsys, **values) -> tuple[int, str, str]:
    status = host_verb.host(None, **values)
    out, err = capsys.readouterr()
    return status, out, err


def test_one_host_is_listed_as_the_current_one_and_its_json_has_the_promised_keys(capsys) -> None:
    _fleet("lab")
    status, out, _err = _host(capsys)
    assert status == EXITS["done"]
    assert "lab" in out and "the current host" in out and "fleet/hosts/lab/" in out
    document = json.loads(_host(capsys, json=True)[1])
    assert list(document)[:2] == ["format_version", "kind"] and document["kind"] == "host_list"
    assert set(PROMISED_KEYS) <= set(document) and document["current_host"] == "lab"
    assert all(set(PROMISED_ROW_KEYS) <= set(row) for row in document["hosts"])


def test_two_hosts_are_listed_each_with_its_address_and_the_current_one_marked(capsys) -> None:
    _fleet()
    _status, out, _err = _host(capsys)
    lines = {line.split()[1] if line.startswith("▸") else line.split()[0]: line for line in out.splitlines()[1:3]}
    assert set(lines) == set(HOSTS)
    assert lines["lab"].startswith("▸") and HOSTS["lab2"] in lines["lab2"]
    assert printer.line("host", {"name": PLACEHOLDER}, refused=True) in out
    document = json.loads(_host(capsys, json=True)[1])
    assert [row["name"] for row in document["hosts"]] == ["lab", "lab2"]
    assert [row["current"] for row in document["hosts"]] == [True, False]


def _aliases(**origins: str) -> None:
    """The managed SSH config under the test's own home, a block for each VM recording its host."""
    ssh = paths.user_home() / ".ssh"
    ssh.mkdir(parents=True, exist_ok=True)
    text = ""
    for name, origin in origins.items():
        text += f"# BEGIN agent-warehouse: {name}\n# origin: {origin}\nHost {name}\n    HostName 192.0.2.99\n"
        text += f"# END agent-warehouse: {name}\n"
    (ssh / "agent-warehouse.conf").write_text(text)


def test_for_a_vm_another_host_s_alias_records_says_that_host_and_host_says_the_one_given(capsys) -> None:
    _fleet()
    _aliases(alpha="lab2")
    where = json.loads(_host(capsys, json=True, for_="alpha")[1])["where"]
    assert list(where) == PROMISED_WHERE_KEYS
    assert (where["host"], where["routed"], where["refusal"]) == ("lab2", True, "")
    given = json.loads(_host(capsys, json=True, host="lab")[1])["where"]
    assert (given["host"], given["routed"]) == ("lab", False)


def test_a_host_named_is_made_current_and_one_that_is_none_is_refused(capsys) -> None:
    where = _fleet()
    status, out, _err = _host(capsys, name="lab2")
    assert status == EXITS["done"] and out.startswith("lab2 is the current host")
    assert _current(where) == "lab2"
    status, _out, err = _host(capsys, name="nowhere")
    assert status == EXITS["refused"] and err.startswith("error: ") and _current(where) == "lab2"


def test_a_switch_takes_the_fleet_s_lock_exactly_where_locks_for_says_fleet(capsys, monkeypatch) -> None:
    _fleet()
    taken = []
    real = locks.exclusive

    def counted(state, command):
        taken.append(command)
        return real(state, command)

    monkeypatch.setattr(locks, "exclusive", counted)
    for values in ({}, {"json": True}, {"json": True, "for_": "alpha"}, {"name": "lab2"}):
        before = len(taken)
        _host(capsys, **values)
        given = {"name": values["name"]} if "name" in values else {}
        assert (len(taken) > before) == (lock_sets.locks_for("host", given) == lock_sets.FLEET), values
    assert taken == [printer.line("host", {"name": "lab2"}, refused=False)]


def test_a_switch_is_refused_while_the_fleet_s_lock_is_held_and_holds_it_against_another(capsys, monkeypatch) -> None:
    where = _fleet()
    with locks.exclusive(paths.state(where), "aw vault --rotate"), pytest.raises(locks.Held) as held:
        _host(capsys, name="lab2")
    assert held.value.sentence.startswith("error: the fleet is held") and _current(where) == "lab"
    seen = []
    real = hosts.make_current

    def inside(layout, name, expect):
        # While the switch holds the lock, nothing else takes it.
        with pytest.raises(locks.Held), locks.exclusive(paths.state(where), "aw host lab"):
            pass
        seen.append(name)
        return real(layout, name, expect)

    monkeypatch.setattr(hosts, "make_current", inside)
    assert _host(capsys, name="lab2")[0] == EXITS["done"] and seen == ["lab2"]


@contextlib.contextmanager
def _a_run(where: paths.Home) -> Iterator[jobs.Job]:
    """A run of the fleet's, made in the test's own process, holding its locks: the fleet's shared and its VM's."""
    state = paths.state(where)
    made = jobs.create(state / paths.JOBS, "deploy", "aw deploy alpha", "lab", ["alpha"], {"kind": "terminal"}, True)
    try:
        locks.take(made, "machines", [("lab", "alpha")], "machine")
        yield made
    finally:
        for _, held, _ in locks._TAKEN.pop(jobs._key(made.folder), []):
            os.close(held)
        if made._held_here() is not None:
            jobs.drop(made)


@pytest.mark.parametrize("remove", [False, True], ids=["a switch", "a removal"])
def test_a_switch_and_a_removal_are_refused_naming_the_run_that_holds_the_fleet(capsys, monkeypatch, remove) -> None:
    where = _fleet()
    monkeypatch.setattr("sys.stdin", _Terminal("lab2\n"))
    with _a_run(where), pytest.raises(locks.Held) as held:
        _host(capsys, name="lab2", remove=remove)
    typed = printer.line("host", {"name": "lab2", "remove": remove}, refused=False)
    assert f"`{typed}`" in held.value.sentence and printer.line("logs", {}, refused=False) in held.value.sentence
    assert _current(where) == "lab" and (paths.fleet_dir(where) / "hosts" / "lab2").is_dir()


def test_a_run_is_refused_while_a_switch_holds_the_fleet(capsys, monkeypatch) -> None:
    where = _fleet()
    refused = []
    real = hosts.make_current

    def inside(layout, name, expect):
        with pytest.raises(locks.Held) as held, _a_run(where):
            pass
        refused.append(held.value.sentence)
        return real(layout, name, expect)

    monkeypatch.setattr(hosts, "make_current", inside)
    assert _host(capsys, name="lab2")[0] == EXITS["done"] and len(refused) == 1
    # Let go once the switch is done: a run takes the fleet again.
    with _a_run(where):
        pass


def test_a_host_typed_is_removed_and_the_line_it_prints_reads_back(capsys, monkeypatch) -> None:
    where = _fleet()
    folder = paths.fleet_dir(where) / "hosts" / "lab2"
    monkeypatch.setattr("sys.stdin", _Terminal("lab2\n"))
    status, out, err = _host(capsys, name="lab2", remove=True)
    assert status == EXITS["done"], err
    assert "Type lab2 to delete it: " in err and not folder.exists() and _current(where) == "lab"
    vault = out.split("listed by ", 1)[1].rstrip().removesuffix(".")
    assert vault == printer.line("vault", {}, refused=True, host_flag="lab2")
    reading = parse.read(shlex.split(vault)[1:])
    assert (reading.verb, reading.values) == ("vault", {"host": "lab2"})


class _Asked(_Terminal):
    """A terminal whose person, while the question waits, starts a run of the fleet, and then makes the host to be
    removed the current one where told to, before typing its name."""

    def __init__(self, where: paths.Home, typed: str, current: bool) -> None:
        super().__init__(typed)
        self.where, self.current, self.ran = where, current, False

    def readline(self, *args) -> str:
        with _a_run(self.where):
            self.ran = True
        if self.current:
            hosts.make_current(hosts.Layout(self.where), "lab2", overrides.read(self.where, None).client.digest)
        return super().readline(*args)


@pytest.mark.parametrize("current", [False, True], ids=["as it was", "made current meanwhile"])
def test_a_removal_asks_holding_no_lock_and_checks_again_under_it(capsys, monkeypatch, current) -> None:
    where = _fleet()
    folder = paths.fleet_dir(where) / "hosts" / "lab2"
    terminal = _Asked(where, "lab2\n", current)
    monkeypatch.setattr("sys.stdin", terminal)
    status, out, err = _host(capsys, name="lab2", remove=True)
    assert terminal.ran, "a run was refused while the question waited"
    if current:
        assert status == EXITS["refused"] and out == "" and "the current host" in err and folder.is_dir()
    else:
        assert status == EXITS["done"], err
        assert not folder.exists()


def test_a_host_mistyped_is_not_removed(capsys, monkeypatch) -> None:
    where = _fleet()
    monkeypatch.setattr("sys.stdin", _Terminal("lab\n"))
    status, out, err = _host(capsys, name="lab2", remove=True)
    assert status == EXITS["refused"] and out == "" and err.endswith("Nothing was deleted.\n")
    assert (paths.fleet_dir(where) / "hosts" / "lab2" / "host.yml").exists()


def test_a_removal_with_no_terminal_to_type_at_is_refused_before_anything_is_read(capsys, monkeypatch) -> None:
    where = _fleet()
    monkeypatch.setattr("sys.stdin", io.StringIO("lab2\n"))
    status, out, err = _host(capsys, name="lab2", remove=True)
    assert status == EXITS["refused"] and out == ""
    assert err == (
        f"error: {printer.line('host', {'remove': True}, refused=True)} asks you to"
        " type the host's name, and there is no terminal to ask at\n"
    )
    assert (paths.fleet_dir(where) / "hosts" / "lab2").is_dir()


@pytest.mark.parametrize(("hosts_made", "name"), [(("lab",), "lab"), (("lab", "lab2"), "lab"), ((), "nowhere")])
def test_the_one_host_the_current_host_and_no_host_are_never_removed(capsys, monkeypatch, hosts_made, name) -> None:
    where = _fleet(*hosts_made) if hosts_made else _fleet("lab")
    monkeypatch.setattr("sys.stdin", _Terminal(f"{name}\n"))
    status, out, err = _host(capsys, name=name, remove=True)
    assert status == EXITS["refused"] and out == "" and err.startswith("error: ")
    assert "Type " not in err
    assert ("the fleet's one host" in err) == (hosts_made == ("lab",))
    assert (paths.fleet_dir(where) / "hosts" / "lab").is_dir()


def test_the_listing_reads_no_vault_and_a_fleet_with_no_host_says_how_to_make_one(capsys) -> None:
    record.write(_where(), record.first(FLEET))
    status, out, _err = _host(capsys)
    assert status == EXITS["done"] and printer.line("init", {}, refused=True) in out
    assert not paths.key_file(_where()).exists()
