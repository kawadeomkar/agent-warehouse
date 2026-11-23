"""The overrides (lib/claude_vm/fleet/overrides.py; docs/architecture/engine.md §16.9).

Each layer outranks the one before, a value for one run above all; a row is read from the file of its home alone: a
host's own from that host's settings.yml and no other host's, this computer's for a host from client.yml's map for that
host, a machine's from a map by <host>/<machine>, and one in a file that is not its home is refused by name, with its
line and where it belongs. A value is held to its row's type in a file, typed and for one run alike: an expression, a
control character, a value of the wrong type, an anchor, an alias, a merge and a key given twice are refused, and the
file is unchanged. A write is a compare-and-swap on the digest the caller read, keeps the file's other keys, reads
back as meant and makes a new file private. --set refuses a secret's row, an unknown name, a value the vault holds and
a value of the wrong type, never saying the value back; a size reads alike in a file, for one run and typed.
client.yml's fleet_home reads as fleet/paths.py reads it, by its own cases. Nothing in fleet/ writes a file but
through set and unset.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import stat
from pathlib import Path

import pytest
import yaml
from harness import REPO
from test_aw_fleet_paths import FLEET_HOME_LINES, FLEET_HOME_REFUSED

from claude_vm.fleet import overrides, paths, registry
from claude_vm.fleet.overrides import Refused, Value
from claude_vm.hosts.kinds import KINDS
from claude_vm.secret import Secret
from claude_vm.vendors import proxy, transport

FLEET = REPO / "lib" / "claude_vm" / "fleet"
# A secret the vault would hold, made up and of low entropy, which no sentence may say.
FAKE_SECRET = "not-a-real-token-0000"
# A value a person may have pasted where it does not belong: no refusal says it back.
TYPED = "swordfish-0000"
# Each module of fleet/ that replaces a file through store.replace, with what it writes. A card whose module writes a
# file of its own (the vault, fleet.json) adds its line here, with why, in the same commit.
REPLACES = {"lib/claude_vm/fleet/overrides.py": "the three files of overrides, by set and unset alone"}
# A proxy's user and password, made up, which no refusal may say; and the sentence network_proxy is refused in for them.
PROXY_USER = "proxyuser"
PROXY_PASSWORD = "made-up-made-up-0000"
CREDENTIALS = (
    "network_proxy takes no user or password: give a proxy that needs them as HTTPS_PROXY in the environment, which"
    " network_proxy: environment reads"
)
# Each way a URL names a user: with a password, alone, with no user before the password, and a %-escaped @ with a
# literal one after it and with none.
WITH_A_USER = [
    f"http://{PROXY_USER}:{PROXY_PASSWORD}@proxy.invalid:3128",
    f"http://{PROXY_USER}@proxy.invalid:3128",
    f"http://:{PROXY_PASSWORD}@proxy.invalid:3128",
    f"http://{PROXY_USER}%40corp:{PROXY_PASSWORD}@proxy.invalid:3128",
    f"http://{PROXY_USER}%40proxy.invalid:3128",
]
# The calls that write a file another way, each refused in fleet/ outright.
OTHER_WRITES = {"write_text", "write_bytes"}
OS_WRITES = {"replace", "rename"}


def _where() -> paths.Home:
    """The fleet's home for this test: its own, under AGENT_WAREHOUSE_HOME (tests/unit/conftest.py's aw_home)."""
    return paths.home(REPO)


def _put(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _fleet_file(where: paths.Home) -> Path:
    return paths.fleet_dir(where) / "settings.yml"


def _host_file(where: paths.Home, host: str) -> Path:
    return paths.fleet_dir(where) / "hosts" / host / "settings.yml"


def _client_file(where: paths.Home) -> Path:
    return paths.client_file(where)


def _shown(value: object) -> object:
    """A row's literal default as a reader is handed it: a tuple a list, a read-only mapping a dict."""
    if isinstance(value, tuple):
        return [_shown(item) for item in value]
    if hasattr(value, "keys"):
        return {key: _shown(item) for key, item in value.items()}  # type: ignore[attr-defined]
    return value


def _tree(top: Path) -> list[str]:
    return sorted(str(path.relative_to(top)) for path in top.rglob("*")) if top.exists() else []


# ---------------------------------------------------------------- the layers ----


def test_each_layer_outranks_the_one_before_and_a_value_for_one_run_all(home) -> None:
    where = _where()
    assert overrides.LAYERS == ("default", "fleet", "host", "client", "run")
    names = ("vm_cores", "proxmox_node", "editor")
    empty = overrides.resolve(where, "lab")
    assert [(empty[name].value, empty[name].layer) for name in names] == [
        (2, "default"),
        ("pve", "default"),
        ("vscode", "default"),
    ]
    _put(_fleet_file(where), "vm_cores: 4\n")
    _put(_host_file(where, "lab"), "proxmox_node: nuc\n")
    _put(_client_file(where), "editor: zed\n")
    files = overrides.resolve(where, "lab")
    assert [files[name] for name in names] == [
        Value("vm_cores", 4, "fleet"),
        Value("proxmox_node", "nuc", "host"),
        Value("editor", "zed", "client"),
    ]
    run = overrides.run_values(["vm_cores=8", "proxmox_node=other", "editor=vscode"], secrets=())
    top = overrides.resolve(where, "lab", run)
    assert [top[name] for name in names] == [
        Value("vm_cores", 8, "run"),
        Value("proxmox_node", "other", "run"),
        Value("editor", "vscode", "run"),
    ]
    # A value a file sets is not one a run without --set changes.
    assert overrides.resolve(where, "lab")["vm_cores"] == Value("vm_cores", 4, "fleet")


def test_a_host_s_own_file_never_supplies_another_host(home) -> None:
    where = _where()
    _put(_host_file(where, "lab"), "proxmox_node: lab-node\nvm_searchdomain: lab.lan\n")
    _put(_host_file(where, "nuc"), "proxmox_node: nuc-node\n")
    lab, nuc = overrides.resolve(where, "lab"), overrides.resolve(where, "nuc")
    assert (lab["proxmox_node"], nuc["proxmox_node"]) == (
        Value("proxmox_node", "lab-node", "host"),
        Value("proxmox_node", "nuc-node", "host"),
    )
    assert (lab["vm_searchdomain"].value, nuc["vm_searchdomain"]) == (
        "lab.lan",
        Value("vm_searchdomain", "", "default"),
    )
    assert overrides.resolve(where, "third")["proxmox_node"] == Value("proxmox_node", "pve", "default")
    assert overrides.resolve(where, None)["proxmox_node"] == Value("proxmox_node", "pve", "default")
    assert overrides.read(where, "lab").host.settings == {"proxmox_node": "lab-node", "vm_searchdomain": "lab.lan"}
    assert overrides.read(where, None).host is None


def test_a_host_scoped_setting_of_this_computer_s_is_read_from_client_yml_s_map_for_that_host_alone(home) -> None:
    where = _where()
    _put(
        _client_file(where),
        "hosts:\n  lab:\n    ssh_identity_file: ~/.ssh/lab_key\n    claude_ssh_host_domain: lab.lan\n",
    )
    lab, nuc = overrides.resolve(where, "lab"), overrides.resolve(where, "nuc")
    assert lab["ssh_identity_file"] == Value("ssh_identity_file", str(home / ".ssh" / "lab_key"), "client")
    assert lab["claude_ssh_host_domain"] == Value("claude_ssh_host_domain", "lab.lan", "client")
    assert (nuc["ssh_identity_file"].layer, nuc["claude_ssh_host_domain"].layer) == ("default", "default")
    assert overrides.resolve(where, None)["claude_ssh_host_domain"].layer == "default"
    # Never at the top, which would give every host one: each host's is its own, never inherited.
    _put(_client_file(where), "claude_ssh_host_domain: lan\n")
    with pytest.raises(Refused, match=r"line 1: claude_ssh_host_domain is not kept here: .*in its map hosts"):
        overrides.resolve(where, "lab")


def test_a_machine_scoped_setting_of_this_computer_s_is_that_machine_s_over_every_machine_s(home) -> None:
    where = _where()
    _put(
        _client_file(where),
        "vm_ssh_options:\n  ServerAliveInterval: '10'\n"
        "machines:\n  lab/alpha:\n    vm_ssh_options:\n      ProxyJump: jump.lan\n",
    )
    every = {"ServerAliveInterval": "10"}
    assert overrides.resolve(where, "lab", machine="alpha")["vm_ssh_options"] == Value(
        "vm_ssh_options", {"ProxyJump": "jump.lan"}, "client"
    )
    assert overrides.resolve(where, "lab", machine="beta")["vm_ssh_options"].value == every
    assert overrides.resolve(where, "nuc", machine="alpha")["vm_ssh_options"].value == every
    assert overrides.resolve(where, "lab")["vm_ssh_options"].value == every


def test_a_fleet_setting_of_scope_machine_is_read_from_the_map_machines_alone(home) -> None:
    where = _where()
    _put(_fleet_file(where), "machines:\n  lab/alpha:\n    tailscale_hostname: dev-alpha\n")
    assert overrides.resolve(where, "lab", machine="alpha")["tailscale_hostname"] == Value(
        "tailscale_hostname", "dev-alpha", "fleet"
    )
    assert overrides.resolve(where, "lab", machine="beta")["tailscale_hostname"] == Value(
        "tailscale_hostname", "beta", "default"
    )
    assert overrides.resolve(where, "nuc", machine="alpha")["tailscale_hostname"].layer == "default"
    # At the top it would give every VM one name.
    _put(_fleet_file(where), "tailscale_hostname: one-name\n")
    with pytest.raises(Refused, match=r"tailscale_hostname is not kept here: it is each machine's own"):
        overrides.read(where, "lab")


@pytest.mark.parametrize("name", KINDS["proxmox"].never_inherited)
def test_a_setting_each_host_keeps_for_itself_is_refused_in_the_fleet_s_file(home, name: str) -> None:
    where = _where()
    row = registry.row(name)
    path = _put(_fleet_file(where), yaml.safe_dump({"vm_cores": 2, name: _shown(row.default) or "x"}, sort_keys=False))
    with pytest.raises(Refused) as refused:
        overrides.read(where, "lab")
    assert str(refused.value).startswith(f"{path} line 2: {name} is not kept here: it is ")
    assert "each host" in str(refused.value)


@pytest.mark.parametrize(
    ("kind", "name", "value", "belongs"),
    [
        ("fleet", "editor", "zed", "this computer's own: keep it at the top of client.yml"),
        ("fleet", "run_forks", 4, "this computer's own: keep it at the top of client.yml"),
        ("fleet", "vm_ssh_options", {}, "this computer's own: keep it at the top of client.yml, or in its map"),
        ("host", "editor", "zed", "this computer's own"),
        ("host", "vm_cores", 4, "the fleet's: keep it at the top of the fleet's settings.yml"),
        ("client", "vm_cores", 4, "the fleet's: keep it at the top of the fleet's settings.yml"),
        ("client", "proxmox_node", "nuc", "each host's own: keep it in its hosts/<host>/settings.yml"),
        ("fleet", "proxmox_vm_force_update", True, "given for one run alone, with --set, and kept in no file"),
        ("host", "configure_wait_for_cloud_init", False, "given for one run alone"),
        ("client", "proxmox_vm_force_update", True, "given for one run alone"),
    ],
)
def test_a_setting_in_a_file_that_is_not_its_home_is_refused_by_name(home, kind, name, value, belongs) -> None:
    where = _where()
    file = {"fleet": _fleet_file(where), "host": _host_file(where, "lab"), "client": _client_file(where)}[kind]
    _put(file, yaml.safe_dump({name: value}))
    with pytest.raises(Refused) as refused:
        overrides.read(where, "lab")
    assert str(refused.value).startswith(f"{file} line 1: {name} is not kept here: ")
    assert belongs in str(refused.value)


def test_a_key_that_is_no_setting_is_refused_with_its_line_and_said_only_where_it_is_a_name(home) -> None:
    where = _where()
    path = _put(_fleet_file(where), "vm_cores: 2\nvm_corse: 3\n")
    with pytest.raises(Refused, match=re.escape(f"{path} line 2: vm_corse is no setting")):
        overrides.read(where, None)
    _put(path, f"vm_cores: 2\n'{TYPED} pasted': 1\n")
    with pytest.raises(Refused) as refused:
        overrides.read(where, None)
    assert str(refused.value) == f"{path} line 2: a key that is no setting's name"
    _put(path, "fleet_home: /elsewhere\n")
    with pytest.raises(Refused, match="fleet_home is no setting"):
        overrides.read(where, None)


# ---------------------------------------------------------------- what a value may be ----


@pytest.mark.parametrize(
    ("name", "text", "value"),
    [
        ("github_auth", "yes", True),
        ("github_auth", "No", False),
        ("github_auth", "TRUE", True),
        ("github_auth", "off", False),
        ("vm_cores", "8", 8),
        ("agents_vm_cap", "0", 0),
        ("vm_extra_packages", "jq, htop", ["jq", "htop"]),
        ("vm_extra_packages", "[jq, 'a,b']", ["jq", "a,b"]),
        ("vm_extra_packages", "", []),
        (
            "claude_ssh_options",
            "ServerAliveInterval=10,Compression=yes",
            {"ServerAliveInterval": "10", "Compression": "yes"},
        ),
        ("claude_ssh_options", "{ServerAliveInterval: 10}", {"ServerAliveInterval": 10}),
        ("claude_ssh_options", "", {}),
        ("tls_ca_file", "", ""),
        ("tls_ca_file", "/etc/ssl/company.pem", "/etc/ssl/company.pem"),
        ("editor", "zed", "zed"),
        ("vm_searchdomain", "lab.lan", "lab.lan"),
        ("vm_disk_size", "128g", "128G"),
        ("vm_disk_size", "1t", "1T"),
        ("vm_disk_size", "128G", "128G"),
    ],
)
def test_typed_text_is_its_row_s_type(name: str, text: str, value: object) -> None:
    assert overrides.typed(registry.row(name), text) == value


def test_a_path_from_home_is_this_computer_s_home(home) -> None:
    assert overrides.typed(registry.row("tls_ca_file"), "~/ca.pem") == str(home / "ca.pem")
    assert overrides.typed(registry.row("vault_key_file"), "~") == str(home)


@pytest.mark.parametrize(
    ("name", "text", "said"),
    [
        ("github_auth", "maybe", "takes true or false"),
        ("vm_cores", "four", "takes a whole number"),
        ("vm_cores", "2.5", "takes a whole number"),
        ("editor", "emacs", "takes one of vscode, zed"),
        ("tls_ca_file", "ca.pem", "a relative path is not taken"),
        ("tls_ca_file", "~other/ca.pem", "a relative path is not taken"),
        ("vm_extra_packages", "jq,,htop", "takes a list with no empty item"),
        ("vm_extra_packages", "[1, 2]", "takes a list of strings"),
        ("vm_extra_packages", "[jq", "is not YAML"),
        ("claude_ssh_options", "Compression", "takes a mapping"),
        ("claude_ssh_options", "[a]", "takes a mapping"),
        ("vm_searchdomain", "{{ lookup('pipe', 'id') }}", "never an expression"),
        ("vm_searchdomain", "a {% raw %} b", "never an expression"),
        ("vm_searchdomain", "{# a note #}", "never an expression"),
        ("vm_extra_packages", '["\\x7b{ x }}"]', "never an expression"),
        ("claude_ssh_options", '{a: "\\x07"}', "without a control character"),
        ("vm_searchdomain", "lab\x85lan", "without a control character"),
        ("vm_searchdomain", "lab\tlan", "without a control character"),
    ],
)
def test_typed_text_its_row_does_not_take_is_refused_with_no_value_said(name: str, text: str, said: str) -> None:
    with pytest.raises(Refused) as refused:
        overrides.typed(registry.row(name), text)
    assert said in str(refused.value)
    assert str(refused.value).startswith(f"{name} ") and str(refused.value).endswith(": the value given is not one")
    for piece in ("lookup", "raw", "note", "emacs", "four", "maybe", "Compression"):
        assert piece not in str(refused.value) or piece in said


@pytest.mark.parametrize("text", ["128gb", "1.5g", "0g", "128", "16 g", "16k", "-1g", "1234567G", "G", ""])
def test_a_size_is_a_whole_number_with_g_or_t(text: str) -> None:
    with pytest.raises(Refused, match="vm_disk_size takes a whole number with G or T after it"):
        overrides.typed(registry.row("vm_disk_size"), text)
    with pytest.raises(Refused, match="vm_disk_size takes one line of text"):
        overrides.typed(registry.row("vm_disk_size"), "128G\n")


def test_a_size_reads_alike_in_a_file_for_one_run_and_typed(home) -> None:
    # --disk 128g, --set vm_disk_size=128g and vm_disk_size: 128g are one size: kept with its unit capital.
    where = _where()
    row = registry.row("vm_disk_size")
    _put(_fleet_file(where), "vm_disk_size: 128g\n")
    in_a_file = overrides.resolve(where, None)["vm_disk_size"]
    for_one_run = overrides.run_values(["vm_disk_size=128g"], secrets=())["vm_disk_size"]
    assert in_a_file == Value("vm_disk_size", "128G", "fleet")
    assert for_one_run == overrides.typed(row, "128G") == overrides.typed(row, "128g") == in_a_file.value
    digest = overrides.read(where, None).fleet.digest
    overrides.set(where, "vm_disk_size", "1t", None, digest)
    assert yaml.safe_load(_fleet_file(where).read_text()) == {"vm_disk_size": "1T"}
    for text in ("128gb", "1.5g"):
        _put(_fleet_file(where), f"vm_disk_size: {text}\n")
        with pytest.raises(Refused, match="line 1: vm_disk_size takes a whole number with G or T"):
            overrides.read(where, None)
        with pytest.raises(Refused):
            overrides.run_values([f"vm_disk_size={text}"], secrets=())


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("vm_cores: '4'\n", "line 1: vm_cores takes a whole number: the value there is not one"),
        ("vm_cores: true\n", "line 1: vm_cores takes a whole number"),
        ("git_user_name: 1.5\n", "line 1: git_user_name takes one line of text"),
        ("git_user_name: 2026-11-22\n", "line 1: git_user_name takes one line of text"),
        ("github_auth: 'yes'\n", "line 1: github_auth takes true or false"),
        ("vm_extra_packages: [jq, 2]\n", "line 1: vm_extra_packages takes a list of strings"),
        ("git_user_name:\n", "line 1: git_user_name is empty there"),
        ('vm_cores: 2\ngit_user_name: "lab\\nlan"\n', "line 2: git_user_name takes one line of text"),
        ("vm_extra_packages: [\"\\x7b{ lookup('pipe', 'id') }}\"]\n", "line 1: vm_extra_packages takes a value, never"),
        ("vm_cores: 2\n# {{ a note }}\n", "line 2: a setting takes a value, never an expression"),
        ("github_projects_clone_protocol: git\n", "takes one of https, ssh"),
        ("claude_code_settings: {env: {A: .nan}}\n", "takes a finite number"),
    ],
)
def test_a_value_in_a_file_its_row_does_not_take_is_refused_by_its_line(home, text: str, said: str) -> None:
    where = _where()
    path = _put(_fleet_file(where), text)
    with pytest.raises(Refused) as refused:
        overrides.read(where, None)
    assert str(refused.value).startswith(f"{path} line ")
    assert said in str(refused.value)
    assert "lookup" not in str(refused.value)


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("# the fleet's\n- vm_cores\n", "line 2: this is not a mapping of settings to their values"),
        ("vm_cores: [\n", "this is not a file of settings"),
        ("vm_cores: 2\n  vm_user: dev\n", "line 2: this is not a file of settings"),
        ("machines: [lab/alpha]\n", "line 1: machines is not a mapping"),
        ("machines:\n  lab/alpha: [x]\n", "line 2: lab/alpha is not a mapping of settings to their values"),
        ("machines:\n  alpha:\n    tailscale_hostname: a\n", "line 2: a key of machines is not <host>/<machine>"),
        ("machines:\n  Lab/alpha:\n    tailscale_hostname: a\n", "line 2: a key of machines is not <host>/<machine>"),
        (
            "base: &shared 2\nvm_cores: *shared\n",
            "line 1: this is not a file of settings (found an anchor or an alias)",
        ),
        ("vm_cores: 2\nvm_cores: 3\n", "line 2: this is not a file of settings (found a key given twice)"),
        ("<<: {vm_cores: 2}\n", "line 1: this is not a file of settings (found a merge)"),
        ("vm_cores: 2\n---\nvm_cores: 3\n", "this is not a file of settings"),
        ("vm_cores: !custom 2\n", "this is not a file of settings"),
    ],
)
def test_a_file_that_is_no_mapping_of_literals_is_said_by_its_path_and_line(home, text: str, said: str) -> None:
    where = _where()
    path = _put(_fleet_file(where), text)
    with pytest.raises(Refused) as refused:
        overrides.read(where, None)
    assert str(refused.value).startswith(str(path))
    assert said in str(refused.value)


def test_a_file_that_is_not_text_or_too_large_is_refused(home) -> None:
    where = _where()
    path = _fleet_file(where)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"vm_user: \xff\xfe\n")
    with pytest.raises(Refused, match="is not UTF-8 text"):
        overrides.read(where, None)
    path.write_text("vm_user: dev\n" + "#" * overrides.MOST)
    with pytest.raises(Refused, match="holds more than a file of settings does"):
        overrides.read(where, None)


# ---------------------------------------------------------------- client.yml's fleet_home ----


@pytest.mark.parametrize(("text", "followed"), FLEET_HOME_LINES)
def test_client_yml_s_reader_reads_fleet_home_as_paths_does(home, text: str, followed: str | None) -> None:
    where = _where()
    client = _put(_client_file(where), f"editor: vscode\n{text}\n")
    said = paths.pointer_in(client.read_text(), client, os.environ)
    try:
        read = overrides.read(where, None).client.own["fleet_home"]
    except Refused as refused:
        # Where the file is YAML at all (a key needs a space after its colon), as fleet/paths.py's test says.
        assert text == "fleet_home:/not/a/key", refused
        assert "this is not a file of settings" in str(refused)
        return
    assert read == said == (Path(followed.replace("~", str(home), 1)) if followed else None)


@pytest.mark.parametrize(("text", "said"), FLEET_HOME_REFUSED)
def test_client_yml_s_reader_refuses_the_fleet_home_paths_refuses_in_its_words(home, text: str, said: str) -> None:
    where = _where()
    client = _put(_client_file(where), text)
    with pytest.raises(paths.Refused) as by_paths:
        paths.pointer_in(text, client, os.environ)
    with pytest.raises(Refused) as by_the_reader:
        overrides.read(where, None)
    assert said in str(by_paths.value)
    assert str(by_the_reader.value) == str(by_paths.value)


@pytest.mark.parametrize(
    "text",
    [
        '"fleet_home": /elsewhere/fleet\n',
        "'fleet_home': /elsewhere/fleet\n",
        "fleet_home : /elsewhere/fleet\n",
        "? fleet_home\n: /elsewhere/fleet\n",
        "editor: zed\nfleet_home  : /elsewhere/fleet\n",
    ],
)
def test_a_fleet_home_yaml_reads_that_its_line_does_not_say_is_refused(home, text: str) -> None:
    # fleet/paths.py reads no pointer there, and YAML reads one: the two would drive different fleets.
    where = _where()
    client = _put(_client_file(where), text)
    assert paths.pointer_in(text, client, os.environ) is None
    assert yaml.safe_load(text)["fleet_home"] == "/elsewhere/fleet"
    with pytest.raises(Refused, match=r"fleet_home is not read as one line fleet_home: /the/folder"):
        overrides.read(where, None)


def test_client_yml_keeps_its_current_host_as_a_host_s_name(home) -> None:
    where = _where()
    client = _put(_client_file(where), "current_host: lab\n")
    assert overrides.read(where, None).client.own == {"fleet_home": None, "current_host": "lab"}
    _put(client, "current_host: ../elsewhere\n")
    with pytest.raises(Refused, match="line 1: current_host is no host's name"):
        overrides.read(where, None)


# ---------------------------------------------------------------- every row's value ----


def test_resolve_over_an_empty_folder_gives_every_row_its_default(home) -> None:
    where = _where()
    found = overrides.resolve(where, "lab")
    assert list(found) == [each.name for each in registry.rows()]
    assert {each.layer for each in found.values()} == {"default"}
    for each in registry.rows():
        if not callable(each.default):
            assert found[each.name].value == _shown(each.default), each.name
    # The one-run rows too: never in a file, at their defaults unless given.
    assert found["proxmox_vm_force_update"] == Value("proxmox_vm_force_update", False, "default")
    assert found["configure_wait_for_cloud_init"] == Value("configure_wait_for_cloud_init", True, "default")
    # The defaults worked out from something else: the home's key file, this computer's key (none in this test's home),
    # another row, and the machine's name, which a run with no machine named has none of.
    assert found["vault_key_file"].value == str(paths.key_file(where))
    assert found["vm_ssh_public_keys"].value == []
    assert found["vm_memory_min_mb"].value == found["vm_memory_mb"].value == 4096
    assert found["claude_code_remote_control_dir"].value == found["vm_projects_dir"].value == ""
    assert (found["tailscale_hostname"].value, found["claude_code_remote_control_name"].value) == (None, None)
    one = overrides.resolve(where, "lab", machine="Alpha_Lab")
    assert (one["tailscale_hostname"].value, one["claude_code_remote_control_name"].value) == ("alpha-lab", "Alpha_Lab")
    # A copy each time: a reader that changes a value changes no other reader's, nor the table.
    found["claude_ssh_options"].value["ServerAliveInterval"] = "5"  # type: ignore[index]
    found["github_projects"].value.append("mine")  # type: ignore[attr-defined]
    again = overrides.resolve(where, "lab")
    assert again["claude_ssh_options"].value == {"ServerAliveInterval": "30"}
    assert again["github_projects"].value == []
    # A read makes nothing.
    assert _tree(home) == []


def test_a_default_worked_out_from_another_row_follows_that_row_s_value(home) -> None:
    where = _where()
    _put(_fleet_file(where), "vm_memory_mb: 8192\nvm_projects_dir: /srv/code\n")
    found = overrides.resolve(where, None)
    assert found["vm_memory_min_mb"] == Value("vm_memory_min_mb", 8192, "default")
    assert found["claude_code_remote_control_dir"] == Value("claude_code_remote_control_dir", "/srv/code", "default")
    run = overrides.run_values(["vm_memory_mb=2048"], secrets=())
    assert overrides.resolve(where, None, run)["vm_memory_min_mb"].value == 2048
    _put(home / ".ssh" / "id_ed25519.pub", "ssh-ed25519 AAAAC3Nza you@here\n")
    assert overrides.resolve(where, None)["vm_ssh_public_keys"].value == ["ssh-ed25519 AAAAC3Nza you@here"]


def test_resolve_refuses_a_value_for_one_run_no_row_takes() -> None:
    where = _where()
    with pytest.raises(Refused, match="no setting's name"):
        overrides.resolve(where, None, {TYPED.replace("-", "_"): 1})
    with pytest.raises(Refused, match="claude_code_settings may hold a secret"):
        overrides.resolve(where, None, {"claude_code_settings": {}})
    with pytest.raises(Refused, match="vm_cores takes a whole number"):
        overrides.resolve(where, None, {"vm_cores": "8"})


# ---------------------------------------------------------------- --set ----


def test_set_run_forks_30_is_the_integer_30(home) -> None:
    run = overrides.run_values(["run_forks=30"], secrets=())
    assert run == {"run_forks": 30} and type(run["run_forks"]) is int
    assert overrides.resolve(_where(), None, run)["run_forks"] == Value("run_forks", 30, "run")
    assert overrides.run_values([], secrets=()) == {}
    assert overrides.run_values(["proxmox_vm_force_update=true", "vm_searchdomain="], secrets=()) == {
        "proxmox_vm_force_update": True,
        "vm_searchdomain": "",
    }


@pytest.mark.parametrize(
    ("text", "said"),
    [
        (f"claude_code_settings={{env: {{ANTHROPIC_API_KEY: {TYPED}}}}}", "claude_code_settings may hold a secret"),
        (f"vm_corse={TYPED}", "that is no setting's name (did you mean vm_cores"),
        (f"{TYPED.replace('-', '_')}=1", "that is no setting's name"),
        (f"proxmox_api_host={TYPED}", "that is no setting's name"),
        (f"vm_cores={TYPED}", "vm_cores takes a whole number"),
        (f"editor={TYPED}", "editor takes one of vscode, zed"),
        (f"vm_searchdomain={{{{ {TYPED} }}}}", "never an expression"),
        (f"Vm_cores={TYPED}", "--set takes a setting's name, then ="),
        (TYPED, "--set takes a setting's name, then ="),
        (f"={TYPED}", "--set takes a setting's name, then ="),
    ],
)
def test_set_refuses_a_secret_an_unknown_name_and_a_wrong_type_with_no_value_said(text: str, said: str) -> None:
    with pytest.raises(Refused) as refused:
        overrides.run_values([text], secrets=())
    assert said in str(refused.value)
    assert "swordfish" not in str(refused.value)


@pytest.mark.parametrize(
    "text",
    [
        f"vm_searchdomain={FAKE_SECRET}",
        f"git_user_name=by {FAKE_SECRET} here",
        f"vm_extra_packages=jq,{FAKE_SECRET}",
        # A comment YAML drops from the value: the text held the secret all the same.
        f"github_projects=[mine]  # {FAKE_SECRET}",
        'vm_extra_packages=["\\x6e' + FAKE_SECRET[1:] + '"]',
        'claude_ssh_options={"\\x6e' + FAKE_SECRET[1:] + '": "1"}',
    ],
)
def test_set_refuses_a_value_that_holds_a_secret_the_vault_holds_and_never_says_it(text: str) -> None:
    vault = [Secret("made-up-made-up-0000"), Secret(FAKE_SECRET)]
    with pytest.raises(Refused) as refused:
        overrides.run_values([text], secrets=vault)
    assert "is a secret the vault holds, which the run's record would keep: not used" in str(refused.value)
    assert FAKE_SECRET not in str(refused.value)
    # Taken where the vault holds no such secret, and the empty secret strikes nothing.
    assert overrides.run_values([text], secrets=[Secret(""), Secret("made-up-made-up-0000")])


def test_every_caller_of_run_values_names_the_vault_s_secrets() -> None:
    with pytest.raises(TypeError):
        overrides.run_values(["vm_cores=2"])  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="each a Secret"):
        overrides.run_values(["vm_cores=2"], secrets=[FAKE_SECRET])  # type: ignore[list-item]


def test_set_given_twice_for_one_setting_is_refused() -> None:
    with pytest.raises(Refused, match="--set gives vm_cores more than once"):
        overrides.run_values(["vm_cores=2", "vm_cores=4"], secrets=())


# ---------------------------------------------------------------- a write ----


def test_two_writers_the_second_with_a_stale_digest_is_refused_and_nothing_is_lost(home) -> None:
    where = _where()
    path = _put(_fleet_file(where), "vm_cores: 2\n")
    read = overrides.read(where, None).fleet.digest
    assert read == hashlib.sha256(path.read_bytes()).hexdigest()
    first = overrides.set(where, "vm_cores", 4, None, read)
    assert first == hashlib.sha256(path.read_bytes()).hexdigest() != read
    before = path.read_bytes()
    with pytest.raises(Refused, match=re.escape(f"{path} changed since it was read: nothing was written")):
        overrides.set(where, "vm_user", "me", None, read)
    assert path.read_bytes() == before
    overrides.set(where, "vm_user", "me", None, first)
    assert yaml.safe_load(path.read_text()) == {"vm_cores": 4, "vm_user": "me"}
    # A file read as missing is a stale read once another made it.
    with pytest.raises(Refused, match="changed since it was read"):
        overrides.set(where, "vm_user", "you", None, overrides.MISSING)


@pytest.mark.parametrize(
    ("name", "value", "said"),
    [
        ("vm_cores", "four", "vm_cores takes a whole number: nothing was written"),
        ("vm_cores", True, "vm_cores takes a whole number"),
        ("git_user_name", 7, "git_user_name takes one line of text"),
        ("git_user_name", "{{ lookup('pipe', 'id') }}", "never an expression"),
        ("git_user_name", "lab\nlan", "without a control character"),
        ("vm_extra_packages", ["jq", "{% raw %}"], "never an expression"),
        ("claude_code_settings", {"env": {"A": "{# a note #}"}}, "never an expression"),
        ("claude_code_settings", {"env": {"A": "a\x07b"}}, "without a control character"),
        ("vm_disk_size", "128gb", "vm_disk_size takes a whole number with G or T"),
        ("github_projects_clone_protocol", "git", "takes one of https, ssh"),
        ("tls_ca_file", "ca.pem", "a relative path is not taken"),
    ],
)
def test_a_value_its_row_does_not_take_is_refused_and_the_file_is_unchanged(home, name, value, said) -> None:
    where = _where()
    fleet = _put(_fleet_file(where), "vm_cores: 2\n")
    client = _put(_client_file(where), "editor: zed\n")
    before = (fleet.read_bytes(), client.read_bytes())
    layers = overrides.read(where, None)
    expect = layers.client.digest if registry.row(name).home == "client" else layers.fleet.digest
    with pytest.raises(Refused) as refused:
        overrides.set(where, name, value, None, expect)
    assert said in str(refused.value) and "lookup" not in str(refused.value)
    assert (fleet.read_bytes(), client.read_bytes()) == before


def test_a_write_keeps_the_file_s_other_keys_and_fleet_home_reads_the_same(home) -> None:
    where = _where()
    client = _put(_client_file(where), f"fleet_home: {where.root}\ncurrent_host: lab\neditor: zed\n")
    digest = overrides.read(where, "lab").client.digest
    after = overrides.set(where, "deploy_code", False, "lab", digest)
    text = client.read_text()
    assert yaml.safe_load(text) == {
        "fleet_home": str(where.root),
        "current_host": "lab",
        "editor": "zed",
        "deploy_code": False,
    }
    assert paths.pointer_in(text, client, os.environ) == where.root
    assert after == hashlib.sha256(client.read_bytes()).hexdigest() == overrides.read(where, "lab").client.digest
    # Into a host's map and a machine's: each made where it is not there, and the rest kept.
    after = overrides.set(where, "ssh_identity_file", "~/.ssh/lab", "lab", after)
    after = overrides.set(where, "vm_ssh_options", {"ProxyJump": "jump.lan"}, "lab", after, machine="alpha")
    assert yaml.safe_load(client.read_text()) == {
        "fleet_home": str(where.root),
        "current_host": "lab",
        "editor": "zed",
        "deploy_code": False,
        "hosts": {"lab": {"ssh_identity_file": str(home / ".ssh" / "lab")}},
        "machines": {"lab/alpha": {"vm_ssh_options": {"ProxyJump": "jump.lan"}}},
    }
    # The same value again writes nothing.
    stamp = client.stat().st_mtime_ns
    assert overrides.set(where, "editor", "zed", "lab", after) == after
    assert client.stat().st_mtime_ns == stamp


def test_a_new_file_is_made_private_in_private_folders(aw_home) -> None:
    where = _where()
    assert not aw_home.exists()
    overrides.set(where, "vm_cores", 4, None, overrides.MISSING)
    overrides.set(where, "editor", "zed", None, overrides.MISSING)
    for folder in (aw_home, paths.fleet_dir(where)):
        assert stat.S_IMODE(folder.stat().st_mode) == paths.DIR_MODE, folder
    for file in (_fleet_file(where), _client_file(where)):
        assert stat.S_IMODE(file.stat().st_mode) == paths.FILE_MODE, file
    assert yaml.safe_load(_fleet_file(where).read_text()) == {"vm_cores": 4}
    # A value outside ASCII is written as YAML's escape, whatever the locale.
    digest = overrides.read(where, None).fleet.digest
    overrides.set(where, "git_user_name", "Zoë", None, digest)
    assert _fleet_file(where).read_bytes().isascii()
    assert overrides.resolve(where, None)["git_user_name"].value == "Zoë"


def test_each_row_is_written_into_the_file_of_its_home(home) -> None:
    where = _where()
    _host_file(where, "lab").parent.mkdir(parents=True)
    layers = overrides.read(where, "lab")
    overrides.set(where, "proxmox_node", "nuc", "lab", layers.host.digest)
    overrides.set(where, "tailscale_hostname", "dev-alpha", "lab", layers.fleet.digest, machine="alpha")
    overrides.set(where, "claude_ssh_host_domain", "lab.lan", "lab", layers.client.digest)
    assert yaml.safe_load(_host_file(where, "lab").read_text()) == {"proxmox_node": "nuc"}
    assert yaml.safe_load(_fleet_file(where).read_text()) == {
        "machines": {"lab/alpha": {"tailscale_hostname": "dev-alpha"}}
    }
    assert yaml.safe_load(_client_file(where).read_text()) == {"hosts": {"lab": {"claude_ssh_host_domain": "lab.lan"}}}
    found = overrides.resolve(where, "lab", machine="alpha")
    assert [found[name].layer for name in ("proxmox_node", "tailscale_hostname", "claude_ssh_host_domain")] == [
        "host",
        "fleet",
        "client",
    ]


def test_a_write_is_refused_where_its_place_is_not_named_or_not_there(home) -> None:
    where = _where()
    with pytest.raises(Refused, match="proxmox_vm_force_update is given for one run alone, with --set"):
        overrides.set(where, "proxmox_vm_force_update", True, None, overrides.MISSING)
    with pytest.raises(Refused, match="proxmox_node is kept for each host or each machine: name the host"):
        overrides.set(where, "proxmox_node", "nuc", None, overrides.MISSING)
    with pytest.raises(Refused, match="tailscale_hostname is each machine's own: name the machine"):
        overrides.set(where, "tailscale_hostname", "a", "lab", overrides.MISSING)
    with pytest.raises(Refused, match="the fleet has no host of that name"):
        overrides.set(where, "proxmox_node", "nuc", "lab", overrides.MISSING)
    with pytest.raises(Refused, match="no host's name"):
        overrides.set(where, "proxmox_node", "nuc", "../lab", overrides.MISSING)
    with pytest.raises(Refused, match="no setting's name"):
        overrides.set(where, "fleet_home", "/elsewhere", None, overrides.MISSING)
    assert _tree(home) == []


def test_unset_takes_a_value_out_and_a_map_it_leaves_empty_with_it(home) -> None:
    where = _where()
    path = _put(
        _fleet_file(where),
        "vm_cores: 4\nmachines:\n  lab/alpha:\n    tailscale_hostname: a\n  lab/beta:\n    tailscale_hostname: b\n",
    )
    digest = overrides.unset(
        where, "tailscale_hostname", "lab", overrides.read(where, None).fleet.digest, machine="beta"
    )
    assert yaml.safe_load(path.read_text()) == {"vm_cores": 4, "machines": {"lab/alpha": {"tailscale_hostname": "a"}}}
    digest = overrides.unset(where, "tailscale_hostname", "lab", digest, machine="alpha")
    assert yaml.safe_load(path.read_text()) == {"vm_cores": 4}
    digest = overrides.unset(where, "vm_cores", None, digest)
    assert path.read_text() == "" and overrides.read(where, None).fleet.settings == {}
    # Nothing there to take out writes nothing.
    stamp = path.stat().st_mtime_ns
    assert overrides.unset(where, "vm_cores", None, digest) == digest
    assert path.stat().st_mtime_ns == stamp


def test_unset_in_a_fleet_with_no_folder_makes_none(aw_home) -> None:
    where = _where()
    assert overrides.unset(where, "vm_cores", None, overrides.MISSING) == overrides.MISSING
    assert overrides.unset(where, "editor", None, overrides.MISSING) == overrides.MISSING
    assert not aw_home.exists()


# ---------------------------------------------------------------- the one writer ----


def _writes(source: str) -> list[tuple[str, str]]:
    """Each write of a file in source, with the function it is in ('' outside one): store.replace by any name it is
    bound to, os.replace and os.rename, a path's write_text and write_bytes, and any of them taken by getattr."""
    tree = ast.parse(source)
    stores: set[str] = set()
    replaces: set[str] = set()
    oses = {"os"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "claude_vm":
            stores |= {alias.asname or alias.name for alias in node.names if alias.name == "store"}
        if isinstance(node, ast.ImportFrom) and node.module == "claude_vm.store":
            replaces |= {alias.asname or alias.name for alias in node.names if alias.name in ("replace", "*")}
        if isinstance(node, ast.Import):
            stores |= {alias.asname for alias in node.names if alias.name == "claude_vm.store" and alias.asname}
            oses |= {alias.asname or alias.name for alias in node.names if alias.name == "os"}
    found: list[tuple[str, str]] = []

    def named(node: ast.AST) -> str | None:
        if isinstance(node, ast.Attribute):
            receiver = ast.unparse(node.value)
            if node.attr == "replace" and receiver in (*stores, "claude_vm.store"):
                return f"{receiver}.replace"
            if node.attr in OS_WRITES and receiver in oses:
                return f"{receiver}.{node.attr}"
            return node.attr if node.attr in OTHER_WRITES else None
        if isinstance(node, ast.Name):
            return node.id if node.id in replaces else None
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in ("replace", *OS_WRITES, *OTHER_WRITES)
        ):
            return f"getattr(..., {node.args[1].value!r})"
        return None

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if (write := named(node)) is not None:
            found.append((function, write))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, "")
    return found


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("from claude_vm import store\ndef f(p):\n    store.replace(p, 'x')\n", [("f", "store.replace")]),
        ("from claude_vm import store as s\ns.replace('p', 'x')\n", [("", "s.replace")]),
        ("import claude_vm.store as kept\nkept.replace('p', 'x')\n", [("", "kept.replace")]),
        ("import claude_vm.store\nclaude_vm.store.replace('p', 'x')\n", [("", "claude_vm.store.replace")]),
        ("from claude_vm.store import replace as put\nput('p', 'x')\n", [("", "put")]),
        ("import os\nos.replace('a', 'b')\n", [("", "os.replace")]),
        ("import os as system\nsystem.rename('a', 'b')\n", [("", "system.rename")]),
        ("def f(p):\n    p.write_text('x')\n", [("f", "write_text")]),
        ("from claude_vm import store\ngetattr(store, 'replace')('p', 'x')\n", [("", "getattr(..., 'replace')")]),
        ("text = 'a'.replace('a', 'b')\n", []),
    ],
)
def test_the_scan_for_a_write_sees_each_way_to_name_one(source: str, found: list) -> None:
    assert _writes(source) == found


def test_nothing_in_fleet_writes_a_file_but_through_set_and_unset() -> None:
    written = {}
    for path in sorted(FLEET.rglob("*.py")):
        writes = _writes(path.read_text())
        if writes:
            written[str(path.relative_to(REPO))] = writes
    assert sorted(written) == sorted(REPLACES), "a module of fleet/ that writes a file: name it in REPLACES, with why"
    assert all(REPLACES.values())
    # In overrides.py, only _write replaces a file, and only set and unset call _write.
    assert written["lib/claude_vm/fleet/overrides.py"] == [("_write", "store.replace")]
    tree = ast.parse((FLEET / "overrides.py").read_text())
    callers = sorted(
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and node.id == "_write" and function.name != "_write"
    )
    assert callers == ["set", "unset"]


# ---------------------------------------------------------------- review 12 ----


def test_how_this_computer_reaches_a_host_s_vms_is_client_yml_s_for_that_host_alone(home) -> None:
    where = _where()
    _put(_client_file(where), "hosts:\n  lab:\n    vm_ssh_route: direct\n")
    assert overrides.resolve(where, "lab")["vm_ssh_route"] == Value("vm_ssh_route", "direct", "client")
    assert overrides.resolve(where, "nuc")["vm_ssh_route"] == Value("vm_ssh_route", "auto", "default")
    # Never in a host's settings.yml, which travels with the fleet's folder, nor at client.yml's top.
    path = _put(_host_file(where, "lab"), "vm_ssh_route: direct\n")
    with pytest.raises(Refused) as refused:
        overrides.read(where, "lab")
    assert str(refused.value).startswith(f"{path} line 1: vm_ssh_route is not kept here: it is this computer's own")
    path.unlink()
    _put(_client_file(where), "vm_ssh_route: direct\n")
    with pytest.raises(Refused, match="vm_ssh_route is not kept here: .*in its map hosts"):
        overrides.read(where, "lab")


PATH_ROWS = [each.name for each in registry.rows() if each.type == "path"]


@pytest.mark.parametrize("name", PATH_ROWS)
def test_a_path_from_home_is_made_absolute_a_relative_one_refused_by_name_and_empty_unset(home, name: str) -> None:
    row = registry.row(name)
    assert overrides.typed(row, "~/x") == str(home / "x")
    assert overrides.typed(row, "/srv/x") == "/srv/x"
    assert overrides.typed(row, "") == ""
    for text in ("x", "./x", "~user/x", "../x"):
        with pytest.raises(Refused) as refused:
            overrides.typed(row, text)
        assert str(refused.value) == (
            f"{name} takes a path that starts with / or ~: a relative path is not taken: the value given is not one"
        )
    # A file's value the same way, kept where the row's home keeps it.
    where = _where()
    held = {name: "~/x"} if row.scope == "global" else {"hosts": {"lab": {name: "~/x"}}}
    _put(_client_file(where), yaml.safe_dump(held))
    assert overrides.resolve(where, "lab")[name] == Value(name, str(home / "x"), "client")
    _put(_client_file(where), yaml.safe_dump({name: ""} if row.scope == "global" else {"hosts": {"lab": {name: ""}}}))
    assert overrides.resolve(where, "lab")[name] == Value(name, "", "client")
    _put(_client_file(where), yaml.safe_dump({name: "x"} if row.scope == "global" else {"hosts": {"lab": {name: "x"}}}))
    with pytest.raises(Refused, match=f"{name} takes a path that starts with / or ~"):
        overrides.read(where, "lab")


@pytest.mark.parametrize("url", WITH_A_USER)
def test_network_proxy_takes_no_user_or_password_at_any_way_in(home, url: str) -> None:
    where = _where()
    row = registry.row("network_proxy")
    said = []
    with pytest.raises(Refused) as typed:
        overrides.typed(row, url)
    said.append(str(typed.value))
    assert str(typed.value) == CREDENTIALS
    with pytest.raises(Refused) as for_one_run:
        overrides.run_values([f"network_proxy={url}"], secrets=())
    said.append(str(for_one_run.value))
    path = _put(_client_file(where), yaml.safe_dump({"network_proxy": url}))
    with pytest.raises(Refused) as in_a_file:
        overrides.read(where, None)
    said.append(str(in_a_file.value))
    assert str(in_a_file.value) == f"{path} line 1: {CREDENTIALS}"
    path.unlink()
    with pytest.raises(Refused) as written:
        overrides.set(where, "network_proxy", url, None, overrides.MISSING)
    said.append(str(written.value))
    assert not path.exists()
    with pytest.raises(ValueError) as chosen:
        proxy.choose(url, {}, "https://api.github.com/")
    said.append(str(chosen.value))
    assert str(chosen.value) == CREDENTIALS
    for sentence in said:
        assert PROXY_USER not in sentence and PROXY_PASSWORD not in sentence, sentence


@pytest.mark.parametrize(
    "text", ["", "https://proxy.invalid:3128", "socks5://proxy.invalid:1080", "proxy:3128", "http://"]
)
def test_network_proxy_is_its_two_words_or_an_http_url_with_a_host_when_it_is_typed(text: str) -> None:
    with pytest.raises(Refused) as refused:
        overrides.typed(registry.row("network_proxy"), text)
    assert str(refused.value) == (
        "network_proxy is environment, none or an http:// URL with a host: the value given is not one"
    )
    for taken in ("environment", "none", "http://proxy.invalid:3128", "http://192.0.2.1"):
        assert overrides.typed(registry.row("network_proxy"), taken) == taken


def test_network_proxy_is_held_by_one_check_which_typed_and_choose_both_make(monkeypatch) -> None:
    asked = []

    def stand_in(setting: str) -> str | None:
        asked.append(setting)
        return "is held by the stand-in"

    monkeypatch.setattr(proxy, "setting_refusal", stand_in)
    with pytest.raises(Refused, match="network_proxy is held by the stand-in"):
        overrides.typed(registry.row("network_proxy"), "http://proxy.invalid:3128")
    with pytest.raises(ValueError, match="network_proxy is held by the stand-in"):
        proxy.choose("http://proxy.invalid:3128", {}, "https://api.github.com/")
    assert asked == ["http://proxy.invalid:3128", "http://proxy.invalid:3128"]
    # And its sentence is written in proxy.py alone: no module keeps a copy of the rule.
    rule = proxy.CREDENTIALS.split(":")[0]
    written = sorted(
        str(path.relative_to(REPO)) for path in (REPO / "lib" / "claude_vm").rglob("*.py") if rule in path.read_text()
    )
    assert written == ["lib/claude_vm/vendors/proxy.py"]


def test_a_proxy_s_user_and_password_still_reach_the_transport_as_a_secret_from_the_environment() -> None:
    named = f"http://{PROXY_USER}:{PROXY_PASSWORD}@proxy.invalid:3128"
    chosen = proxy.choose(proxy.ENVIRONMENT, {"HTTPS_PROXY": named}, "https://api.github.com/")
    assert chosen == named
    held = transport._proxy(chosen)
    assert held is not None and isinstance(held.credentials, Secret)
    assert PROXY_PASSWORD not in repr(held)


@pytest.mark.parametrize(
    ("name", "text", "said"),
    [
        ("vm_label", "a,b", "vm_label takes one Proxmox tag"),
        ("vm_label", "a;b", "vm_label takes one Proxmox tag"),
        ("vm_label", "a b", "vm_label takes one Proxmox tag"),
        ("run_forks", "0", "run_forks takes a whole number of at least 1"),
        ("run_forks", "-3", "run_forks takes a whole number of at least 1"),
        ("github_username", "some one", "github_username takes a GitHub account's name"),
        ("github_username", "some_one", "github_username takes a GitHub account's name"),
    ],
)
def test_a_row_held_to_more_than_its_type_refuses_what_its_reader_could_not_use(home, name, text, said) -> None:
    with pytest.raises(Refused, match=re.escape(said)):
        overrides.typed(registry.row(name), text)
    with pytest.raises(Refused, match=re.escape(said)):
        overrides.run_values([f"{name}={text}"], secrets=())
    where = _where()
    file = _client_file(where) if registry.row(name).home == "client" else _fleet_file(where)
    # In a file, as YAML reads the same text: a string, or the integer.
    _put(file, yaml.safe_dump({name: yaml.safe_load(text)}))
    with pytest.raises(Refused, match=f"line 1: {re.escape(said)}"):
        overrides.read(where, None)


def test_the_rows_held_to_more_take_what_their_readers_use() -> None:
    assert overrides.typed(registry.row("vm_label"), "agent-warehouse") == "agent-warehouse"
    assert overrides.typed(registry.row("vm_label"), "aw_lab.2") == "aw_lab.2"
    assert overrides.typed(registry.row("run_forks"), "1") == 1
    assert overrides.typed(registry.row("github_username"), "octo-cat") == "octo-cat"
    assert overrides.typed(registry.row("github_username"), "") == ""
