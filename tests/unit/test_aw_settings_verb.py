"""aw settings's reads, from the settings table under the overrides, with no Ansible
(lib/claude_vm/fleet/settings_verb.py; docs/architecture/engine.md §16.9).

The scenario of tests/unit/test_settings.py on the new files: two hosts, lab current and lab2, each with the settings
it sets for itself and a value of its own; a value of the fleet's (vm_cores 4, tailscale_ssh on), one of this
computer's, and a row that may hold a secret holding a made-up one; and a vault, with its key, that holds each host's
token and a made-up GitHub token. Each form: the usage and its JSON; every role's group, holding every option its spec
lists, as today's settings <role> does; a name, its value, its file and the line that changes it; --all, every row,
each name of today's documented settings with a row and each of the vault's, set or not set, and every key today's
JSON promises; --changed; --host. No made-up secret is in anything any form prints. The vault is opened by --all alone,
and once, and a vault it cannot open is one note. A run of tools/aw for each form starts no child, with stand-ins that
refuse for ansible-playbook and ssh first on PATH. The writes are refused, naming the card that builds them.
"""

from __future__ import annotations

import functools
import json
import shlex
from pathlib import Path

import pytest
from aw_kit import run_aw
from fleet_fixture import pinned
from harness import REPO
from test_aw_fleet_registry_today import EXAMPLE, NO_ROW
from test_settings import PROMISED_KEYS, PROMISED_ROW_KEYS, PROMISED_SETTING_ROW_KEYS

from claude_vm import wiring
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


@pytest.mark.parametrize(
    "values",
    [{"name": "vm_cores", "value": "8"}, {"name": "vm_cores", "reset": True}, {"name": "edit"}],
    ids=["--value", "--reset", "edit"],
)
def test_a_write_is_refused_naming_its_card(capsys, values) -> None:
    where = _fleet()
    before = overrides.read(where, "lab")
    status, out, err = _show(capsys, **values)
    assert status == EXITS["refused"] and out == ""
    assert err.startswith("error: ") and "D10" in err
    assert overrides.read(where, "lab") == before


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
