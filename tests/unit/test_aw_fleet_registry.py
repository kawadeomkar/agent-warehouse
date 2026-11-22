"""The settings table (lib/claude_vm/fleet/registry.py; docs/architecture/engine.md §16.9).

Every row is well formed: a type the reader knows, choices that hold its default, a scope and a home of the three that
agree with each other, a group and a description of one line that names no command as text; a computed default is one
of the module's named functions, reading only rows and what `GIVEN` names; no two rows share a name; today's documented
settings are 97 rows and the five older names have none; `CONNECTION` holds this computer's three SSH rows; the table
cannot be changed by a reader; and the module imports the standard library alone. What the rows must agree with in
today's files is tests/unit/test_aw_fleet_registry_today.py's, which goes with those files at the cutover.
"""

from __future__ import annotations

import dataclasses
import inspect
import re
import sys
from types import MappingProxyType

import pytest
from aw_kit import imports_of

from claude_vm.fleet import registry
from claude_vm.fleet.registry import CONNECTION, GIVEN, HOMES, ROWS, SCOPES, TYPES, Row

MODULE = "claude_vm.fleet.registry"
# The five names today's files still read that get no row: the four 1.0 names of the editor's settings, and
# tailscale_enabled, the old form of vpn (review/clean-slate/PROPOSAL.md, decision 10).
OLDER = ("vscode_claude_terminal", "vscode_workspaces_dir", "vscode_session_notes", "vscode_claude_sessions")
OLDER_VPN = "tailscale_enabled"
GROUP = re.compile(r"[a-z][a-z0-9_]*")
NAME = re.compile(r"[a-z][a-z0-9_]*")


def _of_type(value: object, kind: str) -> bool:
    """Whether a literal default is a value of a row's type, held as the table holds one: a list as a tuple, a mapping
    read-only."""
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind in ("str", "path"):
        return isinstance(value, str)
    if kind == "list[str]":
        return isinstance(value, tuple) and all(isinstance(item, str) for item in value)
    if kind == "dict":
        return isinstance(value, MappingProxyType) and all(isinstance(key, str) for key in value)
    return False


@pytest.mark.parametrize("each", registry.rows(), ids=lambda each: each.name)
def test_every_row_is_well_formed(each: Row) -> None:
    assert NAME.fullmatch(each.name)
    assert each.type in TYPES
    assert isinstance(each.choices, tuple) and all(isinstance(choice, str) for choice in each.choices)
    assert len(set(each.choices)) == len(each.choices)
    assert each.scope in SCOPES and each.home in HOMES
    assert GROUP.fullmatch(each.group)
    assert each.description and each.description == each.description.strip()
    if callable(each.default):
        assert each.default in registry.COMPUTED
    else:
        assert _of_type(each.default, each.type), (each.default, each.type)
        if each.choices:
            assert each.default in each.choices
    if each.choices:
        assert each.type == "str", "choices are a string's"


@pytest.mark.parametrize("each", registry.rows(), ids=lambda each: each.name)
def test_a_row_s_home_holds_its_scope(each: Row) -> None:
    # The fleet's file holds one value for the fleet, a host's file that host's own; client.yml holds the computer's
    # own at any scope, at its top level, in its map hosts or in its map machines.
    if each.home == "fleet":
        assert each.scope == "global"
    if each.home == "host":
        assert each.scope == "host"


@pytest.mark.parametrize("each", registry.rows(), ids=lambda each: each.name)
def test_a_description_is_one_line_of_plain_text_that_names_no_command_as_text(each: Row) -> None:
    # What a person reads of a setting is printed by the product, which names a command as aw's printer writes it, and
    # never says just (the owner's word 1): a verb is named alone, as the table of verbs' help names one.
    assert all(character.isprintable() for character in each.description), each.description
    assert re.search(r"\bjust\b", each.description) is None, each.description


def test_no_two_rows_share_a_name() -> None:
    names = [each.name for each in registry.rows()]
    assert sorted(name for name in set(names) if names.count(name) > 1) == []


def test_today_s_documented_settings_are_97_rows_and_the_five_older_names_have_none() -> None:
    assert len(ROWS) == 97
    for name in (*OLDER, OLDER_VPN):
        with pytest.raises(KeyError):
            registry.row(name)


def test_connection_holds_this_computer_s_three_ssh_rows_counted_apart() -> None:
    assert [(each.name, each.type, each.scope, each.home) for each in CONNECTION] == [
        ("ssh_identity_file", "path", "host", "client"),
        ("ssh_executable", "path", "global", "client"),
        ("vm_ssh_options", "dict", "machine", "client"),
    ]
    assert registry.row("ssh_executable").default == ""
    assert dict(registry.row("vm_ssh_options").default) == {}
    assert not {each.name for each in CONNECTION} & {each.name for each in ROWS}


def test_the_rows_the_card_placed_are_where_it_placed_them() -> None:
    # One computer's, as the report gives it, and each host's own, never inherited, as today (hosts/kinds.py).
    domain = registry.row("claude_ssh_host_domain")
    assert (domain.scope, domain.home) == ("host", "client")
    pool = registry.row("proxmox_pool")
    assert (pool.scope, pool.home) == ("host", "host")


def test_the_reader_finds_a_row_and_a_group_and_refuses_what_the_table_lacks() -> None:
    assert registry.rows() == ROWS + CONNECTION
    assert registry.row("vm_cores") is next(each for each in ROWS if each.name == "vm_cores")
    with pytest.raises(KeyError):
        registry.row("no_such_setting")
    tailscale = registry.group("tailscale")
    assert tailscale and all(each.group == "tailscale" for each in tailscale)
    assert [each.name for each in tailscale] == [each.name for each in registry.rows() if each.group == "tailscale"]
    assert registry.group("connection") == CONNECTION
    with pytest.raises(KeyError):
        registry.group("no_such_group")
    assert set(registry.GROUPS) == {each.group for each in registry.rows()}
    assert sum(len(registry.group(name)) for name in registry.GROUPS) == len(registry.rows())


def test_no_reader_can_change_the_table() -> None:
    each = registry.row("vm_cores")
    with pytest.raises(dataclasses.FrozenInstanceError):
        each.default = 4  # type: ignore[misc]
    for each in registry.rows():
        assert not isinstance(each.default, (list, dict, set)), each.name
    with pytest.raises(TypeError):
        registry.row("claude_ssh_options").default["ServerAliveInterval"] = "5"  # type: ignore[index]


# ---------------------------------------------------------------- the computed defaults ----


@pytest.mark.parametrize("function", registry.COMPUTED, ids=lambda function: function.__name__)
def test_a_computed_default_reads_only_rows_and_what_given_names(function) -> None:
    names = {each.name for each in registry.rows()}
    parameters = inspect.signature(function).parameters.values()
    assert parameters, function.__name__
    for parameter in parameters:
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, parameter
        assert parameter.name in names or parameter.name in GIVEN, parameter.name
    assert [each.name for each in registry.rows() if each.default is function], "a computed default no row uses"


def test_the_key_is_this_computer_s_own_public_key_or_none(tmp_path) -> None:
    ssh = tmp_path / ".ssh"
    assert registry.this_computers_key(user_home=tmp_path) == []
    ssh.mkdir()
    (ssh / "id_ed25519.pub").write_text("ssh-ed25519 AAAAC3Nza you@here\n\n")
    assert registry.this_computers_key(user_home=tmp_path) == ["ssh-ed25519 AAAAC3Nza you@here"]
    (ssh / "id_ed25519.pub").write_text(" \n")
    assert registry.this_computers_key(user_home=tmp_path) == []
    (ssh / "id_ed25519.pub").unlink()
    (ssh / "id_ed25519.pub").mkdir()
    assert registry.this_computers_key(user_home=tmp_path) == []
    (ssh / "id_ed25519.pub").rmdir()
    (ssh / "id_ed25519.pub").write_bytes(b"\xff\xfe not text")
    assert registry.this_computers_key(user_home=tmp_path) == []


def test_two_defaults_follow_another_row_and_two_the_machine_s_name() -> None:
    assert registry.row("vm_memory_min_mb").default is registry.same_as_vm_memory_mb
    assert registry.same_as_vm_memory_mb(vm_memory_mb=8192) == 8192
    assert registry.row("claude_code_remote_control_dir").default is registry.same_as_vm_projects_dir
    assert registry.same_as_vm_projects_dir(vm_projects_dir="/srv/code") == "/srv/code"
    assert registry.row("claude_code_remote_control_name").default is registry.machine_name
    assert registry.machine_name(machine="alpha") == "alpha"
    assert registry.row("tailscale_hostname").default is registry.tailnet_label


@pytest.mark.parametrize(
    ("machine", "label"),
    [
        ("alpha", "alpha"),
        ("Alpha_Lab", "alpha-lab"),
        ("dev.web--2", "dev-web--2"),
        ("--edge--", "edge"),
        ("x" * 62 + "-y", "x" * 62),
        ("a" * 70, "a" * 63),
        ("café", "caf"),
        ("___", ""),
    ],
)
def test_the_tailnet_name_is_one_dns_label(machine: str, label: str) -> None:
    assert registry.tailnet_label(machine=machine) == label


# ---------------------------------------------------------------- what the module imports ----


def test_the_table_imports_nothing_outside_the_standard_library(home) -> None:
    # What importing the package alone loads is taken away: what is left is what the module itself brings.
    added = imports_of(module=MODULE, home=home) - imports_of(module="claude_vm.fleet", home=home)
    assert MODULE in added, sorted(added)
    assert sorted({name.split(".")[0] for name in added} - set(sys.stdlib_module_names) - {"claude_vm"}) == []
    assert sorted(name for name in added if name.startswith("claude_vm.") and name != MODULE) == []
