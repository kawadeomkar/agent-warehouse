"""The hosts as a folder each, the first included, read by today's core (lib/claude_vm/fleet/hosts.py;
docs/architecture/engine.md §16.9).

The cases of tests/unit/test_hosts.py that are about the layout and the choice, on the new folders: one host and
nothing to choose; a second host, the current one, --host; a VM named on the host its alias records; names on two hosts
refused; a host without a setting each host sets for itself refused in its own place while a run against another goes
on; the other hosts' names and addresses; a host's SSH account and port read, and its key from client.yml's map for
that host alone. Every fleet is two or three hosts in the test's own folder (AGENT_WAREHOUSE_HOME, conftest.py's
aw_home), with made-up addresses of the documentation's range. The invariants: host.yml holds no field of one
computer's, by a literal list of its keys; and no module of the new engine reads a Host's today's files.
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

import pytest
import yaml
from harness import REPO

from claude_vm.fleet import hosts, overrides, paths, registry
from claude_vm.fleet.hosts import Layout, reach
from claude_vm.hosts import choose
from claude_vm.hosts.kinds import FLEET_KINDS, KINDS
from claude_vm.hosts.model import OtherHost, Refused, Request
from claude_vm.schema.printer import said
from claude_vm.schema.words import PLACEHOLDER, Line

# Every field a host's host.yml may hold, as the card names them, with the fingerprint recorded beside an anchor (B22's
# pin): none of them one computer's.
FIELDS = [
    "address",
    "api_port",
    "token_user",
    "token_id",
    "jump",
    "ssh_user",
    "ssh_port",
    "trust_kind",
    "fingerprint",
    "made",
]
# What names one computer's: a key, an identity, a known-hosts file, any file or path of this computer's.
ONE_COMPUTER_S = ("key", "identity", "known", "file", "path", "dir")
# Every setting each host sets for itself, as init writes them into its settings.yml.
ALWAYS = {"proxmox_storage": "local-lvm", "proxmox_bridge": "vmbr0", "proxmox_template_vmid": 9000}
# The new engine's subpackages, and its launcher: where no Host's today's files may be read.
NEW = ("schema", "cli", "engine", "pve", "ssh", "vendors", "runs", "fleet", "guest", "control")
TODAYS_FILES = ("local_file", "vault_file", "hosts_file", "sources")
# A value that is no field's, which no refusal says back.
ODD = "odd-value-0000"
PEM = "-----BEGIN CERTIFICATE-----\nMIIBmade-up\n-----END CERTIFICATE-----\n"


def _where() -> paths.Home:
    return paths.home(REPO)


def _layout() -> Layout:
    return Layout(_where())


def _host(
    name: str,
    address: str = "192.0.2.20",
    *,
    fields: dict | None = None,
    settings: dict | None = None,
    anchor: str | None = None,
) -> Path:
    """fleet/hosts/<name>/, set up as init leaves it, unless told otherwise."""
    folder = paths.fleet_dir(_where()) / "hosts" / name
    folder.mkdir(parents=True)
    given = {"address": address, "token_user": "aw@pve", "token_id": "aw"} if fields is None else fields
    (folder / "host.yml").write_text(yaml.safe_dump(given))
    own = {"proxmox_node": name, **ALWAYS} if settings is None else settings
    if own:
        (folder / "settings.yml").write_text(yaml.safe_dump(own))
    if anchor is not None:
        (folder / "proxmox-trust.pem").write_text(anchor)
    return folder


def _client(text: str) -> Path:
    path = paths.client_file(_where())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _aliases(**origins: str) -> None:
    """The managed SSH config under the test's own home, a block for each VM recording its host ("" for none)."""
    ssh = paths.user_home() / ".ssh"
    ssh.mkdir(parents=True, exist_ok=True)
    text = ""
    for name, origin in origins.items():
        record = f"# origin: {origin}\n" if origin else ""
        text += f"# BEGIN agent-warehouse: {name}\n{record}Host {name}\n    HostName 192.0.2.99\n"
        text += f"# END agent-warehouse: {name}\n"
    (ssh / "agent-warehouse.conf").write_text(text)


def _refused(request: Request) -> Refused:
    with pytest.raises(Refused) as refused:
        reach(_layout(), request)
    return refused.value


def _line(verb: str, values: dict) -> str:
    return said("{line}", {"line": Line(verb, values)})


# ---------------------------------------------------------------- which host ----


def test_one_host_and_nothing_to_choose(home) -> None:
    _host("lab")
    layout = _layout()
    # Nothing to choose, so the choice names none and reads nothing; the run reaches the fleet's one host.
    choice = choose.choose(layout, Request())
    assert choice.host is None and not choice.many and not layout.many()
    assert choose.pick(layout).name == "lab" and layout.first().first
    reached = reach(layout, Request())
    assert reached.host.name == "lab" and reached.host.folder == "fleet/hosts/lab" and reached.host.first
    assert reached.others == () and reached.notes == () and reached.anchor is None
    assert reached.host.kind is FLEET_KINDS["proxmox"] and reached.host.address == "192.0.2.20"
    assert layout.others() == []


def test_a_fleet_with_no_host_has_none_to_reach(home) -> None:
    layout = _layout()
    assert layout.folders() == [] and layout.first() is None and choose.pick(layout) is None
    assert str(_refused(Request())) == "the fleet has no host yet: " + said(
        "{init} sets up the first", {"init": Line("init")}
    )
    assert choose.listing(layout).hosts == ()


def test_a_second_host_the_current_one_and_host_given(home) -> None:
    _host("lab", "192.0.2.21")
    _host("nuc", "192.0.2.22")
    layout = _layout()
    assert layout.many() and layout.folders() == ["lab", "nuc"]
    # Which of several is this computer's to choose: none current, and none named, is refused, saying how.
    refused = _refused(Request())
    assert str(refused) == (
        "the fleet has more than one host (lab, nuc), and none is this computer's current host: make one the current"
        " host with "
        f"{_line('host', {'name': PLACEHOLDER})}, or name one with --host"
    )
    _client("current_host: nuc\n")
    reached = reach(_layout(), Request())
    assert reached.host.name == "nuc" and not reached.host.first and reached.choice.many
    assert reached.others == (OtherHost(name="lab", address="192.0.2.21"),)
    # --host outranks it, and a nested run's host is its parent's.
    assert reach(_layout(), Request(host="lab")).host.name == "lab"
    assert reach(_layout(), Request(nested="lab")).host.name == "lab"
    assert "--host nuc in a run against lab: one host per run" in str(_refused(Request(host="nuc", nested="lab")))


def test_the_current_host_naming_no_host_is_refused_and_host_given_still_reaches_one(home) -> None:
    _host("lab")
    _host("nuc")
    _client("current_host: far\n")
    assert str(_refused(Request())) == (
        "client.yml's current_host names far, which is no host of the fleet's (lab, nuc): "
        + said("{init} sets one up", {"init": Line("init", {"host": "far", "address": PLACEHOLDER})})
    )
    assert reach(_layout(), Request(host="lab")).host.name == "lab"
    assert "--host names far, which is no host of the fleet's (lab, nuc)" in str(_refused(Request(host="far")))


@pytest.mark.parametrize("name", ["current", "machines", "localhost", "configure"])
def test_a_folder_of_a_reserved_name_is_no_host(home, name: str) -> None:
    _host("lab")
    (paths.fleet_dir(_where()) / "hosts" / name).mkdir()
    rows = {row.name: row for row in choose.listing(_layout()).hosts}
    assert "is no host's folder: a host's name is" in rows[name].problem and rows["lab"].problem == ""
    assert "--host names a name that is no host's" in str(_refused(Request(host=name)))
    # Never another host's other: a run against lab keeps lab's only other, none.
    assert reach(_layout(), Request(host="lab")).others == ()


def test_a_host_not_set_up_is_refused_naming_init(home) -> None:
    _host("lab")
    (paths.fleet_dir(_where()) / "hosts" / "nuc").mkdir()
    assert str(_refused(Request(host="nuc"))) == (
        f"--host names nuc, which cannot be used: not set up: {_line('init', {'host': 'nuc'})}"
    )
    # Its address is unknown, and it is still another host of a run against lab's.
    assert reach(_layout(), Request(host="lab")).others == (OtherHost(name="nuc", address=""),)


# ---------------------------------------------------------------- a VM named, and its host ----


def test_a_vm_named_runs_against_the_host_its_alias_records(home) -> None:
    _host("lab")
    _host("nuc")
    _aliases(alpha="nuc", beta="lab", gamma="", delta="far")
    _client("current_host: nuc\n")
    alpha = reach(_layout(), Request(names=("alpha",)))
    assert alpha.host.name == "nuc" and alpha.choice.routed and alpha.notes == ()
    # On another host than the current one: said, as a note, the routing note last.
    beta = reach(_layout(), Request(names=("beta",)))
    assert beta.host.name == "lab" and [(note.level, note.text) for note in beta.notes] == [
        ("note", "beta is lab's, as its SSH alias records: this run is against lab, not the current host, nuc.")
    ]
    # No record, or one of a host the fleet does not have: the current host, as for a VM to be made.
    assert reach(_layout(), Request(names=("gamma",))).host.name == "nuc"
    assert reach(_layout(), Request(names=("delta", "newvm"))).host.name == "nuc"
    mixed = reach(_layout(), Request(names=("beta", "new_vm")))
    assert mixed.host.name == "lab" and mixed.choice.unplaced == ("new-vm",)
    where = choose.where(_layout(), Request(names=("beta", "new_vm")))
    assert (where.host, where.routed, where.unplaced, where.refusal) == ("lab", True, ("new-vm",), "")


def test_names_on_two_hosts_are_one_run_too_many(home) -> None:
    _host("lab")
    _host("nuc")
    _aliases(alpha="nuc", beta="lab")
    _client("current_host: nuc\n")
    assert "the VMs named are on 2 hosts (beta on lab; alpha on nuc)" in str(_refused(Request(names=("alpha", "beta"))))


def test_host_given_beside_a_name_another_host_has_reaches_its_own_vm_and_warns(home) -> None:
    _host("lab")
    _host("nuc")
    _aliases(alpha="nuc")
    reached = reach(_layout(), Request(host="lab", names=("alpha",)))
    assert reached.host.name == "lab" and not reached.choice.routed
    assert reached.notes[0].level == "warning"
    assert reached.notes[0].text.startswith("alpha is nuc's, as its SSH alias records, and this run is against lab")
    assert reach(_layout(), Request(host="nuc", names=("alpha",))).notes == ()


def test_with_the_aliases_off_a_name_records_nothing(home) -> None:
    _host("lab")
    _host("nuc")
    _aliases(alpha="nuc")
    _client("current_host: lab\nclaude_ssh_config: false\n")
    assert _layout().alias_file() is None and _layout().origins(["alpha"]) == {}
    assert reach(_layout(), Request(names=("alpha",))).host.name == "lab"


# ---------------------------------------------------------------- what each host sets for itself ----


def test_a_host_without_a_setting_each_host_sets_for_itself_is_refused_in_its_own_place(home) -> None:
    _host("lab", "192.0.2.21")
    nuc = _host("nuc", "192.0.2.22", settings={"proxmox_storage": "local-lvm", "proxmox_template_vmid": 9000})
    _client("current_host: lab\n")
    said_of_nuc = (
        f"{nuc / 'settings.yml'} does not set proxmox_node, proxmox_bridge, which each host sets for itself: "
        f"{_line('init', {'host': 'nuc'})} writes them"
    )
    assert str(_refused(Request(host="nuc"))) == f"--host names nuc, which cannot be used: {said_of_nuc}"
    # A run against another goes on, nuc among its others; the list says it in nuc's row alone.
    lab = reach(_layout(), Request())
    assert lab.host.name == "lab" and lab.others == (OtherHost(name="nuc", address="192.0.2.22"),)
    rows = {row.name: row for row in choose.listing(_layout()).hosts}
    assert rows["nuc"].problem == said_of_nuc and rows["lab"].problem == "" and rows["lab"].current
    # settings and vault reach it, to put that right.
    lenient = reach(_layout(), Request(host="nuc", lenient=True))
    assert lenient.host.incomplete and lenient.host.problem == said_of_nuc
    # An empty value is no value of its own.
    (nuc / "settings.yml").write_text(yaml.safe_dump({"proxmox_node": "", "proxmox_bridge": "vmbr0", **ALWAYS}))
    assert "does not set proxmox_node, which" in str(_refused(Request(host="nuc")))


@pytest.mark.parametrize(
    ("file", "text", "words"),
    [
        # One network's setting in the fleet's file would be every host's: the overrides' reader refuses it there.
        ("fleet", "vm_ipconfig: ip=192.0.2.60/24\n", "line 1: vm_ipconfig is not kept here: it is each host's own"),
        ("fleet", "vm_nameservers: [192.0.2.53]\n", "line 1: vm_nameservers is not kept here: it is each host's own"),
        ("fleet", "proxmox_node: pve\n", "line 1: proxmox_node is not kept here: it is each host's own"),
        ("client", "claude_ssh_host_domain: lan\n", "line 1: claude_ssh_host_domain is not kept here"),
        ("client", "vm_ssh_route: host\n", "line 1: vm_ssh_route is not kept here"),
    ],
)
def test_a_setting_the_fleet_s_files_may_not_supply_for_a_host_is_refused_there(
    home, file: str, text: str, words: str
) -> None:
    _host("lab")
    _host("nuc")
    path = paths.fleet_dir(_where()) / "settings.yml" if file == "fleet" else paths.client_file(_where())
    path.write_text(text)
    refused = _refused(Request(host="nuc"))
    assert str(refused).startswith(str(path)) and words in str(refused)
    # Said once, for the fleet: the list holds it as its problem.
    assert words in choose.listing(_layout()).problem


def test_the_new_layout_s_kinds_keep_today_s_rules_less_what_host_yml_and_the_client_hold() -> None:
    new, today = FLEET_KINDS["proxmox"], KINDS["proxmox"]
    # The API's user, token ID and port are host.yml's fields, and the certificate is always checked.
    gone = ("proxmox_api_user", "proxmox_api_token_id", "proxmox_api_port", "proxmox_validate_certs")
    assert new.always == tuple(name for name in today.always if name not in gone)
    assert new.never_inherited == today.never_inherited and new.shown == today.shown
    assert new.from_host == () and new.secret_shared == new.secret_host == "token"
    # Each is a row of scope host of the table, so no file but the host's own, or this computer's map for it, sets one.
    for name in new.always + new.never_inherited:
        assert registry.row(name).scope == "host", name
    assert {registry.row(name).home for name in new.always} == {"host"}
    for name in gone:
        with pytest.raises(KeyError):
            registry.row(name)


# ---------------------------------------------------------------- what a host's files hold ----


def test_a_host_s_ssh_account_and_port_are_read_and_its_key_from_client_yml_for_that_host_alone(home) -> None:
    _host("lab", "192.0.2.21")
    _host("nuc", "192.0.2.22", fields={**_fields("192.0.2.22"), "ssh_user": "admin", "ssh_port": 2222, "jump": "hop"})
    _client("current_host: lab\nhosts:\n  nuc:\n    ssh_identity_file: ~/.ssh/nuc_key\n")
    nuc, lab = reach(_layout(), Request(host="nuc")), reach(_layout(), Request(host="lab"))
    assert (nuc.fields.ssh_user, nuc.fields.ssh_port, nuc.fields.jump) == ("admin", 2222, "hop")
    assert (lab.fields.ssh_user, lab.fields.ssh_port, lab.fields.jump) == ("root", 22, "")
    assert nuc.host.local["ssh_identity_file"] == str(paths.user_home() / ".ssh" / "nuc_key")
    assert lab.host.local["ssh_identity_file"] == ""
    assert (nuc.fields.api_port, nuc.fields.token_user, nuc.fields.token_id) == (8006, "aw@pve", "aw")


def test_a_host_s_local_is_its_resolved_settings_of_its_own_scope(home) -> None:
    _host("lab", settings={"proxmox_node": "lab1", "proxmox_pool": "aw-lab", **ALWAYS})
    _client("current_host: lab\nhosts:\n  lab:\n    vm_ssh_route: direct\n")
    local = reach(_layout(), Request()).host.local
    assert sorted(local) == sorted(row.name for row in registry.rows() if row.scope == "host")
    assert (local["proxmox_node"], local["proxmox_pool"], local["vm_ssh_route"]) == ("lab1", "aw-lab", "direct")
    # Where nothing sets it, the table's default, which the layout's defaults say too.
    assert local["vm_ipconfig"] == _layout().defaults()["vm_ipconfig"] == registry.row("vm_ipconfig").default
    assert _layout().defaults()["vm_nameservers"] == []


def _fields(address: str = "192.0.2.20") -> dict:
    return {"address": address, "token_user": "aw@pve", "token_id": "aw"}


def test_host_yml_holds_no_field_of_one_computer_s(home) -> None:
    assert list(hosts.FIELDS) == FIELDS
    assert [field for field in FIELDS if any(word in field for word in ONE_COMPUTER_S)] == []
    _host("lab")
    folder = _host("nuc")
    # A key's path given there is refused, saying where it belongs: this computer's own, for that host.
    for key, where in (
        ("ssh_identity_file", "it is this computer's own: keep it in client.yml, in its map hosts by the host's name"),
        ("claude_ssh_identity_file", "it is this computer's own: keep it in client.yml"),
        ("proxmox_node", "keep it in the host's settings.yml"),
        ("vm_cores", "it is the fleet's: keep it in the fleet's settings.yml"),
    ):
        (folder / "host.yml").write_text(yaml.safe_dump({**_fields(), key: "/x"}))
        refused = str(_refused(Request(host="nuc")))
        assert f"{folder / 'host.yml'} line" in refused, refused
        assert f"{key} is a setting, not a field of the host's: {where}" in refused, refused
    for key in ("identity_file", "hop_key"):
        (folder / "host.yml").write_text(yaml.safe_dump({**_fields(), key: "/x"}))
        assert f"{key} is no field of the host's: {', '.join(FIELDS)} are" in str(_refused(Request(host="nuc")))
    # The other host's run goes on.
    assert reach(_layout(), Request(host="lab")).host.name == "lab"


@pytest.mark.parametrize(
    ("field", "value", "words"),
    [
        ("address", f"-o{ODD}", "address takes a host's name or an IP address"),
        ("address", "", "address takes a host's name or an IP address"),
        ("address", f"{ODD} x", "address takes a host's name or an IP address"),
        ("api_port", 0, "api_port takes a port"),
        ("api_port", 65536, "api_port takes a port"),
        ("api_port", "8006", "api_port takes a port"),
        ("ssh_port", True, "ssh_port takes a port"),
        ("token_user", ODD, "token_user takes a Proxmox user and its realm"),
        ("token_user", f"{ODD}@pve@pam", "token_user takes a Proxmox user and its realm"),
        ("token_user", "@pve", "token_user takes a Proxmox user and its realm"),
        ("token_id", "", "token_id takes a name as Proxmox gives one"),
        ("token_id", f"{ODD}!x", "token_id takes a name as Proxmox gives one"),
        ("jump", f"{ODD}@host", "jump takes an account on the host, alone"),
        ("ssh_user", f"root@{ODD}", "ssh_user takes an account on the host, alone"),
        ("trust_kind", ODD, "trust_kind takes node or ca"),
        ("fingerprint", ODD, "fingerprint takes a SHA-256 fingerprint"),
        ("made", [{"what": ODD, "id": "x", "by": "engine"}], "made takes a list of what was made"),
        ("made", [{"what": "pool", "id": "x", "by": ODD}], "made takes a list of what was made"),
        ("made", [{"what": "pool", "id": "x", "by": "engine", ODD: 1}], "made takes a list of what was made"),
        ("made", [{"what": "pool", "id": "", "by": "engine"}], "made takes a list of what was made"),
        ("made", {"what": "pool"}, "made takes a list of what was made"),
        ("ssh_user", None, "ssh_user is empty there: give it a value, or take its line out"),
    ],
)
def test_each_field_of_host_yml_is_held_and_its_value_never_said(home, field: str, value: object, words: str) -> None:
    folder = _host("nuc")
    (folder / "host.yml").write_text(yaml.safe_dump({**_fields(), field: value}))
    refused = str(_refused(Request(host="nuc")))
    assert f"{folder / 'host.yml'} line" in refused and words in refused and ODD not in refused, refused


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("address: 192.0.2.20\n", "does not give token_user, token_id: {init} writes them"),
        ("address: 192.0.2.20\ntoken_user: aw@pve\n", "does not give token_id: {init} writes it"),
        ("a: &x 192.0.2.20\naddress: *x\n", "line 1: this is not a file of the host's fields (found an anchor or an"),
        ("address: 192.0.2.20\naddress: 192.0.2.21\n", "line 2: this is not a file of the host's fields (found a key"),
        ("address: '{{ lookup }}'\n", "line 1: a field takes a value, never an expression"),
        ("- address\n", "line 1: this is not a mapping of the host's fields to their values"),
    ],
)
def test_a_host_yml_that_is_not_whole_is_that_host_s_problem(home, text: str, words: str) -> None:
    _host("lab")
    folder = _host("nuc")
    (folder / "host.yml").write_text(text)
    assert words.format(init=_line("init", {"host": "nuc"})) in str(_refused(Request(host="nuc")))
    assert reach(_layout(), Request(host="lab")).host.name == "lab"


def test_what_was_made_on_a_host_is_read_by_its_id_and_who_made_it(home) -> None:
    made = [
        {"what": "pool", "id": "aw-lab", "by": "engine"},
        {"what": "token", "id": "aw@pve!aw", "by": "administrator"},
        {"what": "grant", "id": "/pool/aw-lab AWRole aw@pve!aw", "by": "engine"},
        {"what": "template", "id": 9100, "by": "engine"},
    ]
    _host("lab", fields={**_fields(), "made": made})
    found = reach(_layout(), Request()).fields.made
    assert [(one.what, one.id, one.by) for one in found] == [
        ("pool", "aw-lab", "engine"),
        ("token", "aw@pve!aw", "administrator"),
        ("grant", "/pool/aw-lab AWRole aw@pve!aw", "engine"),
        ("template", "9100", "engine"),
    ]


def test_the_anchor_is_read_beside_its_kind_and_nowhere_else(home) -> None:
    fingerprint = ":".join(["AB"] * 32)
    _host("lab", fields={**_fields(), "trust_kind": "node", "fingerprint": fingerprint}, anchor=PEM)
    nuc = _host("nuc")
    _client("current_host: lab\n")
    lab = reach(_layout(), Request())
    assert lab.anchor == PEM and (lab.fields.trust_kind, lab.fields.fingerprint) == ("node", fingerprint)
    assert reach(_layout(), Request(host="nuc")).anchor is None
    for fields, anchor, words in (
        (_fields(), PEM, "proxmox-trust.pem is there, and"),
        ({**_fields(), "trust_kind": "ca"}, None, "gives a trust_kind, and"),
        ({**_fields(), "fingerprint": fingerprint}, None, "line 2: fingerprint is the anchor's, and it gives no"),
        ({**_fields(), "trust_kind": "ca"}, "café", "proxmox-trust.pem is not the text of PEM certificates"),
    ):
        (nuc / "host.yml").write_text(yaml.safe_dump(fields))
        (nuc / "proxmox-trust.pem").unlink(missing_ok=True)
        if anchor is not None:
            (nuc / "proxmox-trust.pem").write_text(anchor)
        assert words in str(_refused(Request(host="nuc"))), words
        assert reach(_layout(), Request()).host.name == "lab"


def test_a_host_whose_settings_are_not_yaml_costs_that_host_alone(home) -> None:
    _host("lab")
    nuc = _host("nuc")
    _client("current_host: lab\n")
    (nuc / "settings.yml").write_text("proxmox_node: [\n")
    refused = str(_refused(Request(host="nuc")))
    assert f"{nuc / 'settings.yml'} line" in refused and "this is not a file of settings" in refused
    assert reach(_layout(), Request()).host.name == "lab"
    rows = {row.name: row for row in choose.listing(_layout()).hosts}
    assert "this is not a file of settings" in rows["nuc"].problem and rows["lab"].problem == ""


# ---------------------------------------------------------------- the list, and the current host ----


def test_the_list_says_each_host_from_its_files_alone(home) -> None:
    _host("lab", "192.0.2.21", settings={"proxmox_node": "pve1", **ALWAYS})
    _host("nuc", "192.0.2.22")
    (paths.fleet_dir(_where()) / "hosts" / "bare").mkdir()
    (paths.fleet_dir(_where()) / "hosts" / ".hidden").mkdir()
    (paths.fleet_dir(_where()) / "hosts" / "notes.txt").write_text("not a host\n")
    _client("current_host: nuc\n")
    listed = choose.listing(_layout())
    assert listed.current_host == "nuc" and listed.problem == ""
    rows = {row.name: row for row in listed.hosts}
    assert list(rows) == ["bare", "lab", "nuc"]
    assert rows["lab"].settings == {"proxmox_node": "pve1"} and rows["lab"].folder == "fleet/hosts/lab"
    assert rows["nuc"].current and rows["nuc"].address == "192.0.2.22" and rows["nuc"].host_kind == "proxmox"
    assert rows["bare"].problem == f"not set up: {_line('init', {'host': 'bare'})}" and not rows["bare"].first
    # With several and none current, the list says so in its problem, and still lists every host.
    _client("")
    listed = choose.listing(_layout())
    assert listed.current_host is None and "none is this computer's current host" in listed.problem
    assert len(listed.hosts) == 3


def test_making_a_host_current_writes_client_yml_through_the_one_writer(home) -> None:
    _host("lab")
    _host("nuc")
    (paths.fleet_dir(_where()) / "hosts" / "bare").mkdir()
    client = _client("editor: zed\n")
    layout = _layout()
    read = overrides.read(_where(), None).client.digest
    after = hosts.make_current(layout, "nuc", read)
    assert yaml.safe_load(client.read_text()) == {"editor": "zed", "current_host": "nuc"}
    assert after == hashlib.sha256(client.read_bytes()).hexdigest() == overrides.read(_where(), None).client.digest
    assert reach(_layout(), Request()).host.name == "nuc"
    # The same again writes nothing; a host not set up, no host, and a stale digest are each refused, nothing written.
    assert hosts.make_current(layout, "nuc", after) == after
    before = client.read_bytes()
    for name, expect, words in (
        ("bare", after, "the host to make current is bare, which cannot be used: not set up"),
        ("far", after, "the host to make current is far, which is no host of the fleet's (bare, lab, nuc)"),
        ("Lab", after, "the host to make current is a name that is no host's"),
        ("lab", read, "changed since it was read: nothing was written"),
    ):
        with pytest.raises(Refused, match=re.escape(words)):
            hosts.make_current(layout, name, expect)
        assert client.read_bytes() == before, name
    assert hosts.make_current(layout, "lab", after) != after
    assert yaml.safe_load(client.read_text())["current_host"] == "lab"


def test_the_one_writer_of_the_current_host_holds_it_to_a_host_s_name_and_keeps_fleet_home(home) -> None:
    where = _where()
    client = _client(f"fleet_home: {where.root}\nvm_ssh_options:\n  ServerAliveInterval: '30'\n")
    digest = overrides.read(where, None).client.digest
    for name in ("../elsewhere", "", "Lab", "a b"):
        with pytest.raises(overrides.Refused, match="that is no host's name"):
            overrides.set_current(where, name, digest)
    overrides.set_current(where, "lab", digest)
    assert overrides.read(where, None).client.own == {"fleet_home": where.root, "current_host": "lab"}
    assert yaml.safe_load(client.read_text())["vm_ssh_options"] == {"ServerAliveInterval": "30"}
    # A home with no client.yml yet gets one, private.
    client.unlink()
    overrides.set_current(where, "nuc", overrides.MISSING)
    assert client.read_text() == "current_host: nuc\n" and client.stat().st_mode & 0o777 == paths.FILE_MODE


# ---------------------------------------------------------------- the layout as the core reads it ----


def _layout_uses(source: str) -> set[str]:
    """Each attribute hosts/choose.py reads off a layout."""
    return {
        node.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "layout"
    }


def test_the_layout_has_every_method_and_attribute_today_s_core_uses_of_one(home) -> None:
    used = _layout_uses((REPO / "lib" / "claude_vm" / "hosts" / "choose.py").read_text())
    assert used >= {"named", "current_name", "first", "many", "folders", "origins", "every", "defaults", "hosts_rel"}
    layout = _layout()
    for name in used | {"others", "others_of"}:
        assert hasattr(layout, name), name
    assert layout.hosts_rel == "fleet/hosts" and layout.hosts_dir == paths.fleet_dir(_where()) / "hosts"


def _reads_todays_files(source: str) -> list[str]:
    """Each of a Host's today's files source reads: as an attribute, or by getattr with its name."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in TODAYS_FILES:
            found.append(node.attr)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in TODAYS_FILES
        ):
            found.append(node.args[1].value)
    return found


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("x = host.local_file", ["local_file"]),
        ("x = choice.host.vault_file", ["vault_file"]),
        ("x = getattr(host, 'hosts_file')", ["hosts_file"]),
        ("for s in host.sources: pass", ["sources"]),
        ("x = host.local", []),
        ("x = 'sources'", []),
    ],
)
def test_the_scan_for_today_s_files_sees_each_way_to_read_one(source: str, found: list) -> None:
    assert _reads_todays_files(source) == found


def test_no_module_of_the_new_engine_reads_a_host_s_today_s_files() -> None:
    modules = sorted(path for sub in NEW for path in (REPO / "lib" / "claude_vm" / sub).rglob("*.py"))
    modules += sorted((REPO / "tools").glob("aw*"))
    assert REPO / "lib" / "claude_vm" / "fleet" / "hosts.py" in modules
    found = {
        str(path.relative_to(REPO)): reads
        for path in modules
        if path.is_file() and (reads := _reads_todays_files(path.read_text()))
    }
    assert found == {}, "today's inventory files of a host are today's product's: the new road reads none"
