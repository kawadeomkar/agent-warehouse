"""aw settings's reads, from the settings table under the overrides, with no Ansible, and its writes of one typed value
(lib/claude_vm/fleet/settings_verb.py; docs/architecture/engine.md §16.9).

The scenario of tests/unit/test_settings.py on the new files: two hosts, lab current and lab2, each with the settings
it sets for itself and a value of its own; a value of the fleet's (vm_cores 4, tailscale_ssh on), one of this
computer's, and a row that may hold a secret holding a made-up one; and a vault, with its key, that holds each host's
token and a made-up GitHub token. Each form: the usage and its JSON; every role's group, holding every option its spec
lists, as today's settings <role> does; a name, its value, its file and the line that changes it; --all, every row,
each name of today's documented settings with a row and each of the vault's, set or not set, and every key today's
JSON promises; --changed; --host. No made-up secret is in anything any form prints. The vault is opened by --all alone,
and once, and a vault it cannot open is one note. A run of tools/aw for each form starts no child, with stand-ins that
refuse for ansible-playbook and ssh first on PATH.

The writes, each of the tests of test_settings.py that holds today's --value and --reset run against the new verb: a
value takes its place in the file of its home and the same again changes nothing; a write waits while another writer
holds the file; a secret, a name the vault holds and a value equal to a secret the vault holds are refused and the
value never said, the last looked for in the text and again in the value YAML's escapes spell; an expression (a
statement and a comment too, as tests/unit/test_init_local_setting.py holds for init's writes) and a control character
are refused; what is not one setting for every machine is refused with today's sentence; a value set and then reset
leaves the file as it was; --reset of a secret or of the host's address is refused; --host's file is the one changed.
edit, with a stand-in editor that rewrites its file: no change writes nothing, one changed line is one write, a line
taken out is a reset, and an expression or a secret in the document is refused by its line and the document offered
again. No refusal says the value given back.
"""

from __future__ import annotations

import functools
import io
import json
import shlex
import sys
import textwrap
import threading
from pathlib import Path

import pytest
import yaml
from aw_kit import run_aw
from fleet_fixture import pinned
from harness import REPO
from test_aw_fleet_registry_today import EXAMPLE, NO_ROW
from test_settings import (
    PROMISED_KEYS,
    PROMISED_RESET_KEYS,
    PROMISED_ROW_KEYS,
    PROMISED_SETTING_ROW_KEYS,
    PROMISED_WRITE_KEYS,
)

from claude_vm import store, wiring
from claude_vm.fleet import (
    hosts,
    overrides,
    passthrough,
    paths,
    record,
    registry,
    sealed,
    secrets,
    settings_verb,
    vaultkey,
)
from claude_vm.schema import printer
from claude_vm.schema.words import EXITS, PLACEHOLDER
from claude_vm.secret import Secret

FLEET = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
HOSTS = {"lab": "192.0.2.20", "lab2": "192.0.2.21"}
TOKEN = {"token_user": "aw@pve", "token_id": "aw"}
ALWAYS = {"proxmox_storage": "local-lvm", "proxmox_bridge": "vmbr0"}
# Made up, each where a person keeps it: a row that may hold a secret, a token of the vault's and each host's token.
IN_A_ROW = "sk-fake-settings-secret-0000"
GITHUB = "ghp_000000000000000000000000000000000000"
TOKENS = {"lab": "11111111-1111-4111-8111-111111111111", "lab2": "22222222-2222-4222-8222-222222222222"}
SECRETS = (IN_A_ROW, GITHUB, *TOKENS.values())
# The fleet's own values, as the scenario of test_settings.py sets them in local.yml; this computer's is the route to
# each host's VMs, which every fleet a test makes pins (fleet_fixture.pinned).
FLEET_VALUES = {"vm_cores": 4, "tailscale_ssh": True, "claude_code_settings": {"apiKeyHelper": IN_A_ROW}}
# Every read form, each as aw takes it, with what the parser hands the handler.
FORMS = {
    "alone": {},
    "usage as JSON": {"json": True},
    "a role": {"name": "tailscale"},
    "a role as JSON": {"name": "tailscale", "json": True},
    "common": {"name": "common"},
    "a name": {"name": "vm_cores"},
    "a secret's row": {"name": "claude_code_settings"},
    "a secret's row as JSON": {"name": "claude_code_settings", "json": True},
    "a name of the vault's": {"name": "github_token"},
    "an option passed through": {"name": "proxmox_vm_cores"},
    "--changed": {"changed": True},
    "--changed as JSON": {"changed": True, "json": True},
    "--changed of a group": {"name": "claude_code", "changed": True},
    "--host": {"name": "proxmox_vm", "host": "lab2"},
}
EVERY = {
    "--all": {"all": True},
    "--all as JSON": {"all": True, "json": True},
    "--all --changed": {"all": True, "changed": True},
}


def _where() -> paths.Home:
    return paths.home(REPO)


def _fleet(*, vault: bool = True) -> paths.Home:
    """The scenario, each file written by its one writer."""
    where = _where()
    record.write(where, record.first(FLEET))
    layout = hosts.Layout(where)
    for name, address in HOSTS.items():
        fields = {"address": address, "api_port": 8006, **TOKEN}
        hosts.write(layout, name, hosts.MISSING, change=lambda raw, fields=fields: raw.update(fields))
        digest = overrides.MISSING
        for setting, value in {"proxmox_node": name, **ALWAYS}.items():
            digest = overrides.set(where, setting, value, name, digest)
    overrides.set(where, "proxmox_pool", "awlab2", "lab2", overrides.read(where, "lab2").host.digest)
    hosts.make_current(layout, "lab", overrides.MISSING)
    for name in HOSTS:
        pinned(name)
    for name, value in FLEET_VALUES.items():
        overrides.set(where, name, value, None, overrides.read(where, None).fleet.digest)
    if vault:
        key = vaultkey.make_key(paths.key_file(where))
        sealed.create(where, key)
        for name, token in TOKENS.items():
            sealed.put(where, key, "token", Secret(token), host=name)
        sealed.put(where, key, "github_token", Secret(GITHUB))
    return where


def _show(capsys, **values) -> tuple[int, str, str]:
    """The handler, as the dispatch calls it, its status and what it printed; no made-up secret in any of it."""
    status = settings_verb.show(None, **values)
    out, err = capsys.readouterr()
    assert not [secret for secret in SECRETS if secret in out + err], "a made-up secret was printed"
    return status, out, err


def _json(capsys, **values) -> dict:
    status, out, err = _show(capsys, json=True, **values)
    assert status == EXITS["done"], err
    return json.loads(out)


def _by_name(report: dict) -> dict[str, dict]:
    return {row["name"]: row for row in report["settings"]}


@functools.cache
def _documented() -> tuple[str, ...]:
    sections = wiring.parse_example(EXAMPLE, secret=False)
    return tuple(name for _title, groups in sections for _prose, found in groups for name, _example in found)


@pytest.mark.parametrize("values", [*FORMS.values(), *EVERY.values()], ids=[*FORMS, *EVERY])
def test_each_read_form_ends_done_and_never_prints_a_secret(capsys, values) -> None:
    _fleet()
    status, out, err = _show(capsys, **values)
    assert status == EXITS["done"], err
    assert out.strip()


def test_the_usage_lists_each_group_and_each_role_with_its_spec_s_description(capsys) -> None:
    _fleet()
    _status, out, _err = _show(capsys)
    for name in [*registry.GROUPS, *wiring.roles()]:
        assert f"  {name} " in out, name
    usage = _json(capsys)
    roles = {each["role"]: each for each in usage["roles"]}
    assert set(roles) >= set(wiring.roles()) | set(registry.GROUPS)
    assert roles["tailscale"]["description"] == passthrough.described()["tailscale"] != ""
    assert usage["options"] == list(settings_verb.OPTIONS)


@pytest.mark.parametrize("role", wiring.roles())
def test_a_role_s_group_lists_every_option_its_spec_lists_and_its_rows(capsys, role: str) -> None:
    _fleet()
    report = _json(capsys, name=role)
    names = [row["name"] for row in report["settings"]]
    assert len(names) == len(set(names)), "a name listed twice"
    assert set(wiring.specs()[role]) <= set(names), set(wiring.specs()[role]) - set(names)
    assert {each.name for each in registry.rows() if each.group == role} <= set(names)
    assert report["target"] == {"kind": "role", "name": role}


def test_every_setting_and_every_name_of_the_vault_s_is_one_row_of_all_with_today_s_keys(capsys) -> None:
    _fleet()
    report = _json(capsys, all=True)
    assert set(report) == set(PROMISED_KEYS)
    assert report["notes"] == []
    rows = report["settings"]
    names = [row["name"] for row in rows]
    assert len(names) == len(set(names))
    assert {each.name for each in registry.rows()} <= set(names)
    assert set(secrets.names()) <= set(names)
    assert set(_documented()) - set(NO_ROW) <= set(names)
    assert all(set(PROMISED_ROW_KEYS) | PROMISED_SETTING_ROW_KEYS <= set(row) for row in rows)
    by_name = _by_name(report)
    # The vault's names, read from the vault: one set, one not; the host's token is the current host's.
    assert by_name["github_token"]["secret"] == "set" and by_name["github_token"]["value"] is None
    assert by_name["anthropic_api_key"]["secret"] == "not set"
    assert by_name["token"]["secret"] == "set"
    assert by_name["token"]["change"] == printer.line("vault", {"name": "token"}, refused=False, host_flag="lab")


def test_a_row_that_may_hold_a_secret_is_set_or_not_set_never_its_value(capsys) -> None:
    _fleet()
    row = _by_name(_json(capsys, name="claude_code_settings"))["claude_code_settings"]
    assert (row["kind"], row["secret"], row["value"], row["change"]) == ("secret", "set", None, None)
    assert "default" not in row
    _status, out, _err = _show(capsys, name="claude_code_settings")
    assert "set (secret)" in out


def test_a_name_says_its_value_its_file_its_default_and_the_line_that_changes_it(capsys) -> None:
    _fleet()
    row = _by_name(_json(capsys, name="vm_cores"))["vm_cores"]
    assert (row["value"], row["set_in"], row["home"], row["changed"]) == (4, "fleet/settings.yml", "fleet", True)
    assert row["default"] == registry.plain(registry.row("vm_cores").default)
    assert row["change"] == printer.line("settings", {"name": "vm_cores", "value": PLACEHOLDER}, refused=False)
    _status, out, _err = _show(capsys, name="vm_cores")
    assert row["change"] in out
    # A host's own, with several hosts: the line names the host its value is read for.
    lab2 = _by_name(_json(capsys, name="proxmox_pool", host="lab2"))["proxmox_pool"]
    assert (lab2["value"], lab2["evaluated_as"]) == ("awlab2", "lab2")
    assert lab2["set_in"] == "fleet/hosts/lab2/settings.yml"
    assert "--host lab2" in " ".join(shlex.split(lab2["change"]))
    lab = _by_name(_json(capsys, name="proxmox_pool"))["proxmox_pool"]
    assert (lab["set_in"], lab["evaluated_as"]) == ("default", "lab")
    # This computer's own, named by its file.
    client = _by_name(_json(capsys, name="vm_ssh_route"))["vm_ssh_route"]
    assert (client["value"], client["set_in"], client["home"]) == ("direct", "client.yml", "client")


def test_an_option_set_through_a_row_shows_that_row_s_value_and_one_passed_through_its_role_s_default(capsys) -> None:
    _fleet()
    through = _by_name(_json(capsys, name="proxmox_vm_cores"))["proxmox_vm_cores"]
    assert through["value"] == 4 and through["via"] == [{"name": "vm_cores", "file": "fleet/settings.yml"}]
    assert through["why"] == passthrough.refusal("proxmox_vm_cores")
    passed = next(each for each in passthrough.options() if each.refused is None and each.role == "tailscale")
    row = _by_name(_json(capsys, name=passed.name))[passed.name]
    has, default = passthrough.default(passed)
    assert row["entry_point"] == passed.entry_point and row["set_in"] == "default"
    assert row["value"] == (default if has and row["kind"] == "value" else None)


def test_changed_lists_only_what_the_files_change(capsys) -> None:
    _fleet()
    rows = _by_name(_json(capsys, changed=True))
    assert {"vm_cores", "tailscale_ssh", "proxmox_node"} <= set(rows)
    assert all(row["changed"] for row in rows.values())
    assert "vm_memory_mb" not in rows
    grouped = _by_name(_json(capsys, name="claude_code", changed=True))
    assert set(grouped) == {"claude_code_settings"}


def test_only_all_opens_the_vault_and_once(capsys, monkeypatch) -> None:
    _fleet()
    opened = []
    real = vaultkey.open

    def counted(*args, **kwargs):
        opened.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(vaultkey, "open", counted)
    for values in FORMS.values():
        _show(capsys, **values)
    assert opened == []
    for values in EVERY.values():
        _show(capsys, **values)
    assert opened == [1] * len(EVERY)


def test_a_vault_all_cannot_open_is_said_in_one_note(capsys) -> None:
    where = _fleet()
    paths.key_file(where).unlink()
    report = _json(capsys, all=True)
    assert len(report["notes"]) == 1 and report["notes"][0].startswith(settings_verb.VAULT_UNREAD)
    assert all(row["secret"] is None for row in report["settings"] if row["home"] == "vault")


def test_a_fleet_with_no_host_yet_is_read_for_none(capsys) -> None:
    record.write(_where(), record.first(FLEET))
    row = _by_name(_json(capsys, name="vm_cores"))["vm_cores"]
    assert (row["value"], row["evaluated_as"]) == (registry.row("vm_cores").default, None)


def test_an_unknown_name_is_refused_never_said_back(capsys) -> None:
    _fleet()
    status, out, err = _show(capsys, name=GITHUB.replace("_", "x"))
    assert status == EXITS["refused"] and out == "" and err.startswith("error: ")
    assert GITHUB.replace("_", "x") not in err
    _status, _out, close = _show(capsys, name="vm_core")
    assert "vm_cores" in close


def test_a_host_that_is_not_there_is_refused(capsys) -> None:
    _fleet()
    status, _out, err = _show(capsys, name="vm_cores", host="nowhere")
    assert status == EXITS["refused"] and "nowhere" in err


def _refusing(folder: Path, ran: Path) -> Path:
    """A folder of stand-ins for ansible-playbook and ansible, each of which records that it ran and fails; ssh is the
    kit's stand-in, which fails the run that reaches it (tests/unit/aw_machine.py holds that no test writes its own)."""
    folder.mkdir()
    for name in ("ansible-playbook", "ansible"):
        program = folder / name
        program.write_text(f"#!/bin/sh\necho {name} >> {shlex.quote(str(ran))}\nexit 1\n")
        program.chmod(0o755)
    return folder


@pytest.mark.parametrize("words", [("settings",), ("settings", "tailscale", "--json"), ("settings", "--all")])
def test_a_run_of_aw_settings_starts_no_child(home, tmp_path, words) -> None:
    _fleet()
    ran = tmp_path / "ran"
    folder = _refusing(tmp_path / "refusing", ran)
    result = run_aw(*words, home=home, first_on_path=[folder])
    assert result.returncode == EXITS["done"], result.stderr
    assert not ran.exists(), ran.read_text()
    assert not [secret for secret in SECRETS if secret in result.stdout + result.stderr]
    assert "\x1b[" not in result.stdout


# ---------------------------------------------------------------------------------------------- the writes ----

# What a person typed after --value, in a refusal's place: never said back.
TYPED = "typed-on-the-command-line-0000"
# Words given as a name and as a value, which no refusal says back.
GIVEN_NAME = "zq_given_words_7731"
GIVEN = "zq-given-words-7731"


def _settings(capsys, **values) -> tuple[int, str, str]:
    """The handler, as the dispatch calls it with what the parser hands it; no made-up secret in what it printed."""
    status = settings_verb.settings(None, **values)
    out, err = capsys.readouterr()
    assert not [secret for secret in SECRETS if secret in out + err], "a made-up secret was printed"
    return status, out, err


def _files(where: paths.Home) -> dict[str, bytes | None]:
    """Every file of overrides of the scenario's, byte for byte, None where it is not there."""
    found: dict[str, bytes | None] = {}
    for host in (*HOSTS, None):
        for path in overrides.files(where, host):
            if path is not None:
                found[str(path)] = path.read_bytes() if path.exists() else None
    return found


def _refused(capsys, where: paths.Home, says: str, **values) -> str:
    """A write that refuses, saying says: nothing on standard output, every file as it was. Returns its error."""
    before = _files(where)
    status, out, err = _settings(capsys, **values)
    assert status == EXITS["refused"] and out == "" and err.startswith("error: "), err
    assert says in err, err
    assert _files(where) == before
    return err


def _fleet_file(where: paths.Home) -> Path:
    return overrides.files(where, None)[0]


def _host_file(where: paths.Home, host: str) -> Path:
    own = overrides.files(where, host)[1]
    assert own is not None
    return own


def _literals(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_a_value_takes_its_place_in_its_home_and_the_same_again_changes_nothing(capsys) -> None:
    where = _fleet()
    status, out, err = _settings(capsys, name="vm_cores", value="6", json=True)
    assert status == EXITS["done"], err
    report = json.loads(out)
    assert PROMISED_WRITE_KEYS <= set(report), sorted(PROMISED_WRITE_KEYS - set(report))
    assert report["wrote"]["wrote"] == "vm_cores: 6" and report["now"]["value"] == 6
    assert any("a VM gets it when it is created" in note for note in report["notes"]), report["notes"]
    assert _literals(_fleet_file(where))["vm_cores"] == 6
    before = _files(where)
    status, out, _err = _settings(capsys, name="vm_cores", value="6")
    assert status == EXITS["done"] and "sets it already: nothing was written" in out
    assert _files(where) == before


def test_a_write_says_what_applies_it(capsys) -> None:
    _fleet()
    _status, out, _err = _settings(capsys, name="tailscale_ssh", value="false", json=True)
    notes = json.loads(out)["notes"]
    assert any("configure" in note and "tailscale" in note and "applies it to every VM" in note for note in notes)
    _status, out, _err = _settings(capsys, name="run_lock", value="fleet", json=True)
    assert json.loads(out)["notes"] == [settings_verb.NEXT_RUN]


def test_a_fleet_with_no_vault_yet_is_written(capsys) -> None:
    where = _fleet(vault=False)
    status, _out, err = _settings(capsys, name="vm_cores", value="6")
    assert status == EXITS["done"], err
    assert _literals(_fleet_file(where))["vm_cores"] == 6


def test_a_write_waits_while_another_writer_holds_the_file_and_then_writes() -> None:
    # Two writes at once each read the file and replace it: without the lock between them, one would be lost.
    where = _fleet()
    path = _fleet_file(where)
    before = path.read_bytes()
    done: list[int] = []
    with store.locked(path):
        writer = threading.Thread(target=lambda: done.append(settings_verb.settings(None, name="vm_cores", value="8")))
        writer.start()
        writer.join(timeout=3)
        assert writer.is_alive() and path.read_bytes() == before, "it wrote while the lock was held"
    writer.join(timeout=60)
    assert done == [EXITS["done"]]
    assert _literals(path)["vm_cores"] == 8


@pytest.mark.parametrize("name", ["github_token", "vault_github_token", "token", "claude_code_settings"])
def test_a_secret_is_refused_and_the_value_never_said(capsys, name) -> None:
    err = _refused(capsys, _fleet(), "your shell's history keeps it", name=name, value=TYPED)
    assert TYPED not in err


@pytest.mark.parametrize(
    "name",
    [
        "tailscale_auth_key",
        "vault_tailscale_auth_key",
        "github_projects_token",
        "proxmox_vm_password",
        "vault_vm_password",
        "github_projects_username",
        "claude_code_anthropic_api_key",
        "vault_anything_at_all",
    ],
)
def test_today_s_secrets_are_refused_and_the_value_never_said(capsys, name) -> None:
    err = _refused(capsys, _fleet(), "", name=name, value=TYPED)
    assert TYPED not in err
    if name == "vault_anything_at_all":
        assert name not in err and settings_verb.VAULT_NAMED in err


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("vm_default_name", GITHUB),
        ("vm_default_name", f"x{TOKENS['lab2']}x"),
        ("vm_cores", GITHUB),
        ("tailscale_advertise_tags", '["\\x' + format(ord(GITHUB[0]), "02x") + GITHUB[1:] + '"]'),
    ],
    ids=["equal", "within", "before its type", "spelt with YAML's escapes"],
)
def test_a_value_that_holds_a_secret_the_vault_holds_is_refused_never_said(capsys, name, value) -> None:
    err = _refused(capsys, _fleet(), "a secret the vault holds", name=name, value=value)
    assert GITHUB[1:] not in err and TOKENS["lab2"] not in err


@pytest.mark.parametrize(
    "value",
    [
        "{{ lookup('pipe', 'id') }}",
        "{% if true %}x{% endif %}",
        "{# x #}",
        "[\"\\x7b{ lookup('pipe', 'id') }}\"]",
    ],
    ids=["expression", "statement", "comment", "spelt with YAML's escapes"],
)
def test_a_value_that_opens_a_template_is_refused_whatever_its_form(capsys, value) -> None:
    name = "tailscale_advertise_tags" if value.startswith("[") else "vm_default_name"
    _refused(capsys, _fleet(), "never an expression", name=name, value=value)


def test_a_control_character_is_refused(capsys) -> None:
    _refused(capsys, _fleet(), "control character", name="vm_default_name", value="a\tb")


@pytest.mark.parametrize(
    ("name", "says"),
    [
        ("tailscale_hostname", "made from each machine's own name"),
        ("proxmox_vm_force_update", "given for one run alone"),
        ("proxmox_vm_cores", "set through vm_cores"),
        ("tailscale_sign_in_follows", "set by a run"),
        ("ansible_host", "the Proxmox host's address is a field of the host's own host.yml"),
        ("vm_ssh_jump", "the hop's account"),
        ("address", "is a field of the host's own host.yml"),
        ("proxmox_api_module_defaults", "internal to the project"),
    ],
)
def test_what_is_not_one_setting_for_every_machine_is_refused(capsys, name, says) -> None:
    err = _refused(capsys, _fleet(), says, name=name, value="anything")
    if says.endswith("host.yml"):
        assert "hosts/lab/host.yml" in err


def test_an_inline_vault_value_typed_is_refused(capsys) -> None:
    _refused(capsys, _fleet(), "not YAML", name="tailscale_advertise_tags", value="[!vault x]")


def test_a_file_the_reader_refuses_is_never_written_over(capsys) -> None:
    where = _fleet()
    path = _fleet_file(where)
    path.write_text(path.read_text(encoding="utf-8") + "vm_user: !vault |\n  $ANSIBLE_VAULT;1.1;AES256\n", "utf-8")
    _refused(capsys, where, "", name="vm_cores", value="8")


def test_a_vault_that_cannot_be_opened_refuses_the_write(capsys) -> None:
    where = _fleet()
    paths.key_file(where).unlink()
    _refused(capsys, where, settings_verb.VAULT_SHUT, name="vm_cores", value="8")


def _option() -> str:
    return next(each.name for each in passthrough.options() if each.refused is None and each.type == "bool")


@pytest.mark.parametrize(
    ("name", "value", "host"),
    [
        ("vm_memory_mb", "8192", None),
        ("proxmox_template_vmid", "9100", None),
        ("proxmox_template_vmid", "9200", "lab2"),
        ("run_lock", "fleet", None),
        (None, "true", None),
    ],
    ids=["the fleet's", "the host's", "another host's", "this computer's", "an option passed through"],
)
def test_a_value_set_and_then_reset_leaves_the_file_as_it_was(capsys, name, value, host) -> None:
    where = _fleet()
    name = name or _option()
    before = _files(where)
    status, _out, err = _settings(capsys, name=name, value=value, host=host)
    assert status == EXITS["done"], err
    assert _files(where) != before
    status, out, err = _settings(capsys, name=name, reset=True, host=host, json=True)
    assert status == EXITS["done"], err
    report = json.loads(out)
    assert PROMISED_RESET_KEYS <= set(report) and report["reset"]["removed"] == [name]
    assert _files(where) == before
    _status, out, _err = _settings(capsys, name=name, reset=True, host=host)
    assert "so it is at its default already: nothing was written" in " ".join(out.split())


@pytest.mark.parametrize(
    ("name", "says"),
    [
        ("ansible_host", "hosts/lab/host.yml"),
        ("github_token", "vault github_token"),
        ("vault_github_token", "vault github_token"),
        ("claude_code_settings", "fleet/settings.yml"),
        ("nonsense_name", "that is neither a group"),
    ],
)
def test_reset_of_a_secret_or_the_host_s_address_is_refused(capsys, name, says) -> None:
    _refused(capsys, _fleet(), says, name=name, reset=True)


def test_host_s_own_file_is_the_one_changed_and_a_fleet_row_says_it_is_the_fleet_s(capsys) -> None:
    where = _fleet()
    lab, fleet = _host_file(where, "lab").read_bytes(), _fleet_file(where).read_bytes()
    status, out, err = _settings(capsys, name="proxmox_storage", value="tank", host="lab2", json=True)
    assert status == EXITS["done"], err
    assert _literals(_host_file(where, "lab2"))["proxmox_storage"] == "tank"
    assert _host_file(where, "lab").read_bytes() == lab and _fleet_file(where).read_bytes() == fleet
    assert json.loads(out)["wrote"]["file"].endswith("hosts/lab2/settings.yml")
    status, out, err = _settings(capsys, name="vm_cores", value="3", host="lab2")
    assert status == EXITS["done"], err
    assert "the fleet's, one value for every host" in out and _literals(_fleet_file(where))["vm_cores"] == 3


@pytest.mark.parametrize(
    "values",
    [
        {"name": "github_token", "value": GIVEN},
        {"name": "claude_code_settings", "value": GIVEN},
        {"name": f"vault_{GIVEN_NAME}", "value": "x"},
        {"name": GIVEN_NAME, "value": "x"},
        {"name": GIVEN_NAME, "reset": True},
        {"name": "vm_cores", "value": GIVEN},
        {"name": "run_lock", "value": GIVEN},
        {"name": "vm_default_name", "value": "{{ " + GIVEN + " }}"},
        {"name": "vm_default_name", "value": GIVEN + "\t"},
        {"name": "tailscale_advertise_tags", "value": "[" + GIVEN},
        {"name": "tailscale_hostname", "value": GIVEN},
        {"name": "ansible_host", "value": GIVEN},
        {"name": "vm_default_name", "value": GITHUB + GIVEN},
    ],
    ids=[
        "a secret",
        "a row that may hold one",
        "a vault_ name",
        "an unknown name",
        "an unknown name reset",
        "a value its row does not take",
        "a choice it is not",
        "an expression",
        "a control character",
        "a list that is not YAML",
        "not one setting",
        "the host's address",
        "a value of the vault's",
    ],
)
def test_no_refusal_of_a_write_says_the_words_given_back(capsys, values) -> None:
    err = _refused(capsys, _fleet(), "", **values)
    assert GIVEN not in err and GIVEN_NAME not in err


def test_no_setting_is_named_edit() -> None:
    names = {
        *(each.name for each in registry.rows()),
        *(each.name for each in passthrough.options()),
        *secrets.names(),
        *registry.GROUPS,
        *passthrough.described(),
    }
    assert settings_verb.EDIT not in names


def test_the_roles_deploy_configures_are_configure_yml_s() -> None:
    found: list[str] = []

    def walk(tasks) -> None:
        for task in tasks or []:
            if isinstance(task, dict):
                for key in ("ansible.builtin.import_role", "ansible.builtin.include_role"):
                    if key in task:
                        found.append(task[key]["name"])
                for key in ("block", "rescue", "always"):
                    walk(task.get(key))

    for play in yaml.safe_load((REPO / "playbooks" / "configure.yml").read_text(encoding="utf-8")):
        found.extend(each["role"] if isinstance(each, dict) else each for each in play.get("roles") or [])
        for section in ("pre_tasks", "tasks", "post_tasks", "handlers"):
            walk(play.get(section))
    assert set(found) == set(settings_verb.CONFIGURED)


# ------------------------------------------------------------------------------------------------- edit ----


class _Terminal(io.StringIO):
    """Standard input at a terminal, with what the person types."""

    def isatty(self) -> bool:
        return True


def _editor(tmp_path: Path, monkeypatch, *runs: list[tuple[str, str]]) -> Path:
    """A stand-in editor, as VISUAL names it: its nth run keeps what it was given in seen-<n>, then makes each change of
    runs[n], (old, new), in the file, where old "" puts new first. Returns the file that counts its runs."""
    plan = tmp_path / "editor.json"
    plan.write_text(json.dumps([[list(change) for change in each] for each in runs]))
    count = tmp_path / "editor-runs"
    script = tmp_path / "editor.txt"
    script.write_text(
        textwrap.dedent(
            f"""\
            import json, sys
            from pathlib import Path
            count = Path({str(count)!r})
            run = int(count.read_text()) if count.exists() else 0
            count.write_text(str(run + 1))
            path = Path(sys.argv[1])
            text = path.read_text(encoding="utf-8")
            (count.parent / f"seen-{{run}}").write_text(text, encoding="utf-8")
            for old, new in json.loads(Path({str(plan)!r}).read_text())[run]:
                assert not old or old in text, old
                text = new + text if not old else text.replace(old, new, 1)
            path.write_text(text, encoding="utf-8")
            """
        )
    )
    monkeypatch.setenv("VISUAL", f"{shlex.quote(sys.executable)} -I {shlex.quote(str(script))}")
    return count


def _written(monkeypatch) -> list[tuple[str, object]]:
    """Each write of the overrides' a run makes, by name and value (None for a reset)."""
    made: list[tuple[str, object]] = []
    real_set, real_unset, real_many = overrides.set, overrides.unset, overrides.set_many

    def counted_set(home, name, value, *args, **kwargs):
        made.append((name, value))
        return real_set(home, name, value, *args, **kwargs)

    def counted_unset(home, name, *args, **kwargs):
        made.append((name, None))
        return real_unset(home, name, *args, **kwargs)

    def counted_many(home, host, expect, *, values=None, removed=(), **kwargs):
        made.extend([*(values or {}).items(), *((name, None) for name in removed)])
        return real_many(home, host, expect, values=values or {}, removed=removed, **kwargs)

    monkeypatch.setattr(overrides, "set", counted_set)
    monkeypatch.setattr(overrides, "unset", counted_unset)
    monkeypatch.setattr(overrides, "set_many", counted_many)
    return made


def _visual(monkeypatch, tmp_path: Path, code: str) -> None:
    """VISUAL as a stand-in editor that runs code, with the document's path as path."""
    script = tmp_path / "visual.txt"
    script.write_text("import sys\nfrom pathlib import Path\npath = Path(sys.argv[1])\n" + textwrap.dedent(code))
    monkeypatch.setenv("VISUAL", f"{shlex.quote(sys.executable)} -I {shlex.quote(str(script))}")


def test_edit_refuses_with_no_terminal(capsys, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setenv("VISUAL", "false")
    _refused(capsys, _fleet(), "at a terminal", name="edit")


@pytest.mark.parametrize("values", [{"json": True}, {"changed": True}], ids=["--json", "--changed"])
def test_edit_takes_host_alone(capsys, values) -> None:
    _refused(capsys, _fleet(), "it takes --host alone", name="edit", **values)


def test_edit_with_no_change_writes_nothing_and_shows_no_secret(capsys, monkeypatch, tmp_path) -> None:
    where = _fleet()
    runs = _editor(tmp_path, monkeypatch, [])
    monkeypatch.setattr("sys.stdin", _Terminal(""))
    made = _written(monkeypatch)
    before = _files(where)
    status, out, err = _settings(capsys, name="edit")
    assert status == EXITS["done"], err
    assert settings_verb.UNCHANGED in out and made == [] and _files(where) == before and runs.read_text() == "1"
    seen = (tmp_path / "seen-0").read_text(encoding="utf-8")
    assert "\nvm_cores: 4\n" in seen and "\n# vm_memory_mb: 4096\n" in seen
    assert not [secret for secret in SECRETS if secret in seen] and "\nclaude_code_settings:" not in seen


def test_edit_writes_one_changed_line_as_one_write_and_a_line_taken_out_as_a_reset(
    capsys, monkeypatch, tmp_path
) -> None:
    where = _fleet()
    _editor(tmp_path, monkeypatch, [("\nvm_cores: 4\n", "\nvm_cores: 6\n"), ("\ntailscale_ssh: true\n", "\n")])
    monkeypatch.setattr("sys.stdin", _Terminal(""))
    made = _written(monkeypatch)
    status, out, err = _settings(capsys, name="edit")
    assert status == EXITS["done"], err
    assert made == [("vm_cores", 6), ("tailscale_ssh", None)]
    fleet = _literals(_fleet_file(where))
    assert fleet["vm_cores"] == 6 and "tailscale_ssh" not in fleet
    assert "wrote vm_cores: 6" in out and "reset tailscale_ssh" in out


def test_an_expression_in_the_document_is_refused_by_its_line_and_the_document_offered_again(
    capsys, monkeypatch, tmp_path
) -> None:
    _fleet()
    expression = "{{ lookup('pipe', 'id') }}"
    _editor(tmp_path, monkeypatch, [("", f"vm_default_name: {expression}\n")], [(expression, "dev-box")])
    monkeypatch.setattr("sys.stdin", _Terminal("\n"))
    made = _written(monkeypatch)
    status, _out, err = _settings(capsys, name="edit")
    assert status == EXITS["done"], err
    assert "error: line 1: vm_default_name takes a value, never an expression" in err
    assert made == [("vm_default_name", "dev-box")]


def test_a_secret_in_the_document_is_refused_by_its_line_and_no_writes_nothing(capsys, monkeypatch, tmp_path) -> None:
    where = _fleet()
    _editor(tmp_path, monkeypatch, [("", f"vm_default_name: {GITHUB}\n"), ("\nvm_cores: 4\n", "\nvm_cores: 6\n")])
    monkeypatch.setattr("sys.stdin", _Terminal("n\n"))
    made = _written(monkeypatch)
    err = _refused(capsys, where, "nothing was written", name="edit")
    assert "error: line 1: that value is a secret the vault holds" in err
    assert made == []


CHANGED = 'path.write_text(path.read_text(encoding="utf-8").replace("\\nvm_cores: 4\\n", "\\nvm_cores: 6\\n"))\n'
FAILING = {
    "an editor that ends with exit 3": (f"{CHANGED}raise SystemExit(3)\n", "the editor ended with exit 3"),
    "bytes that are not UTF-8": ('path.write_bytes(b"vm_cores: \\xff\\n")\n', "is not UTF-8 text"),
    "a document taken away": ("path.unlink()\n", "the editor left no document to read"),
    "a line with no colon": (
        'path.write_text(path.read_text(encoding="utf-8") + "vm_cores 6\\n")\n',
        f"{settings_verb.NOT_A_LINE}",
    ),
    "a name given twice": (
        f'{CHANGED}path.write_text(path.read_text(encoding="utf-8") + "vm_cores: 8\\n")\n',
        "is given on line",
    ),
}


@pytest.mark.parametrize("code, says", list(FAILING.values()), ids=list(FAILING))
def test_an_editor_that_fails_or_a_document_refused_writes_nothing(capsys, monkeypatch, tmp_path, code, says) -> None:
    where = _fleet()
    _visual(monkeypatch, tmp_path, code)
    monkeypatch.setattr("sys.stdin", _Terminal("n\n"))
    made = _written(monkeypatch)
    _refused(capsys, where, says, name="edit")
    assert made == []


@pytest.mark.parametrize(
    "visual, says",
    [("aw-no-such-editor-9f3", "the editor cannot be run"), ("vi 'x", "VISUAL cannot be read as a command")],
    ids=["not on PATH", "no shell would split it"],
)
def test_an_editor_that_cannot_be_run_writes_nothing(capsys, monkeypatch, visual, says) -> None:
    where = _fleet()
    monkeypatch.setenv("VISUAL", visual)
    monkeypatch.setattr("sys.stdin", _Terminal("n\n"))
    made = _written(monkeypatch)
    _refused(capsys, where, says, name="edit")
    assert made == []


def test_a_hosts_row_in_a_fleet_with_no_host_is_refused_by_its_line(capsys, monkeypatch, tmp_path) -> None:
    where = _where()
    record.write(where, record.first(FLEET))
    _editor(tmp_path, monkeypatch, [("", "vm_cores: 6\nproxmox_bridge: vmbr1\n")])
    monkeypatch.setattr("sys.stdin", _Terminal("n\n"))
    made = _written(monkeypatch)
    err = _refused(capsys, where, "nothing was written", name="edit")
    assert "error: line 2: proxmox_bridge is each host's own, and the fleet has no host yet" in err
    assert "a fleet with no host yet" in (tmp_path / "seen-0").read_text(encoding="utf-8")
    assert made == [] and not _fleet_file(where).exists()


@pytest.mark.parametrize("again", [False, True], ids=["no", "yes"])
def test_a_file_another_write_changed_while_the_editor_was_open_is_offered_again(
    capsys, monkeypatch, tmp_path, again
) -> None:
    where = _fleet()
    fleet = _fleet_file(where)
    # The other write is the editor's own here, in the file it never opened: tailscale_ssh, in the fleet's file.
    other = (
        f"Path({str(fleet)!r}).write_text(Path({str(fleet)!r}).read_text()"
        ".replace('tailscale_ssh: true', 'tailscale_ssh: false'))\n"
    )
    _visual(monkeypatch, tmp_path, f"if 'vm_cores: 4' in path.read_text():\n    {other}{CHANGED}")
    monkeypatch.setattr("sys.stdin", _Terminal("\n" if again else "n\n"))
    made = _written(monkeypatch)
    status, out, err = _settings(capsys, name="edit")
    assert "was changed by another write while the editor was open: nothing was written" in " ".join(err.split())
    if not again:
        assert status == EXITS["refused"] and out == "" and made == []
        assert _literals(fleet)["vm_cores"] == 4 and _literals(fleet)["tailscale_ssh"] is False
        return
    # What the person left as it was (tailscale_ssh: true) stays as the other write left it.
    assert status == EXITS["done"], err
    assert made == [("vm_cores", 6)]
    assert _literals(fleet)["vm_cores"] == 6 and _literals(fleet)["tailscale_ssh"] is False


def test_each_file_is_written_once_and_a_write_refused_between_files_names_those_written(
    capsys, monkeypatch, tmp_path
) -> None:
    where = _fleet()
    fleet, own = _fleet_file(where), _host_file(where, "lab")
    _editor(
        tmp_path,
        monkeypatch,
        [
            ("\nvm_cores: 4\n", "\nvm_cores: 6\n"),
            ("\ntailscale_ssh: true\n", "\n"),
            ("\nproxmox_bridge: vmbr0\n", "\nproxmox_bridge: vmbr1\n"),
        ],
    )
    monkeypatch.setattr("sys.stdin", _Terminal(""))
    calls: list[Path] = []
    real = overrides.set_many

    def racing(home, host, expect, **kwargs):
        names = [*(kwargs.get("values") or {}), *(kwargs.get("removed") or ())]
        calls.append(overrides.placed(home, names[0], host))
        after = real(home, host, expect, **kwargs)
        if len(calls) == 1:
            # Another writer of the other file, between the two files' writes.
            other = own if calls[0] == fleet else fleet
            other.write_text(other.read_text(encoding="utf-8") + "# another write\n", encoding="utf-8")
        return after

    monkeypatch.setattr(overrides, "set_many", racing)
    status, out, err = _settings(capsys, name="edit")
    assert status == EXITS["refused"], err
    assert sorted(calls) == sorted([fleet, own])
    first, second = calls
    label = functools.partial(settings_verb._relative, where)
    flat = " ".join(err.split())
    assert "changed since it was read" in flat
    assert f"{label(first)} was written as said above, and nothing of {label(second)}" in flat
    if first == fleet:
        assert "wrote vm_cores: 6" in out and "reset tailscale_ssh" in out and "proxmox_bridge" not in out
        assert _literals(fleet)["vm_cores"] == 6 and _literals(own)["proxmox_bridge"] == "vmbr0"
    else:
        assert "wrote proxmox_bridge" in out and "vm_cores" not in out
        assert _literals(fleet)["vm_cores"] == 4 and _literals(own)["proxmox_bridge"] == "vmbr1"


def test_edit_for_one_host_of_several_names_the_host_in_what_applies_it(capsys, monkeypatch, tmp_path) -> None:
    _fleet()
    _editor(tmp_path, monkeypatch, [("\ntailscale_ssh: true\n", "\ntailscale_ssh: false\n")])
    monkeypatch.setattr("sys.stdin", _Terminal(""))
    status, out, err = _settings(capsys, name="edit", host="lab2")
    assert status == EXITS["done"], err
    assert "--host lab2" in " ".join(out.split())


@pytest.mark.parametrize(
    "values, removed, says",
    [
        ({"vm_cores": 6, "proxmox_bridge": "vmbr1"}, (), "kept in more than one file"),
        ({"vm_cores": 6}, ("proxmox_bridge",), "kept in more than one file"),
        ({}, (), "no setting was given"),
    ],
    ids=["two files", "a set and a reset in two files", "none"],
)
def test_set_many_writes_one_file_or_nothing(values, removed, says) -> None:
    where = _fleet()
    before = _files(where)
    digest = overrides.read(where, "lab").fleet.digest
    with pytest.raises(overrides.Refused, match=says):
        overrides.set_many(where, "lab", digest, values=values, removed=removed)
    assert _files(where) == before


def test_a_run_of_aw_settings_writes_and_edit_with_no_terminal_refuses(home) -> None:
    where = _fleet()
    result = run_aw("settings", "vm_cores", "--value=6", home=home)
    assert result.returncode == EXITS["done"], result.stderr
    assert _literals(_fleet_file(where))["vm_cores"] == 6
    result = run_aw("settings", "edit", home=home)
    assert result.returncode == EXITS["refused"] and "at a terminal" in result.stderr
