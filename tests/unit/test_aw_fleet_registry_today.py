"""The settings table against today's files: the cutover's check, which goes with those files (lib/claude_vm/fleet/
registry.py; docs/architecture/engine.md §16.9).

Every setting today's example documents (inventory/group_vars/all/local.yml.example, read by its own rule,
lib/claude_vm/wiring.py's parse_example) has exactly one row, but the five older names, which have none: a setting
added to the example without a row fails here by its name. Each row's type and choices are the argument spec's of the
role variable it feeds (lib/claude_vm/wiring.py's set_by), or, for a setting that feeds none, its literal default's
type and the choices today's checks hold it to (lib/claude_vm/catalogue.py's CHOICES); every literal default equals
today's, and each default that is an expression today is the literal or the function this file names for it; each
row's group is the role today's just settings gives it; and a setting each host sets for itself today
(lib/claude_vm/hosts/kinds.py) is each host's own.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

from claude_vm import catalogue, wiring
from claude_vm.fleet import registry
from claude_vm.fleet.registry import ROWS
from claude_vm.hosts.kinds import KINDS
from claude_vm.vpn.tailscale import tailscale_label

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "inventory" / "group_vars" / "all" / "local.yml.example"
# The five older names today's files still read, with no row: the four 1.0 names of the editor's settings and
# tailscale_enabled, the old form of vpn (review/clean-slate/PROPOSAL.md, decision 10). tailscale_enabled stays the
# tailscale role's own variable, set from vpn, and is no setting a person writes.
OLDER = ("vscode_claude_terminal", "vscode_workspaces_dir", "vscode_session_notes", "vscode_claude_sessions")
OLDER_VPN = "tailscale_enabled"
# Each setting whose default today is an expression, with what its row's default is: the literal it stands for, or the
# function that works it out. The four that read a 1.0 name take that name's literal, since the name has no row; vpn's
# reads tailscale_enabled, which is false unless set, so none.
FROM_AN_EXPRESSION = {
    "vm_ssh_public_keys": registry.this_computers_key,
    "vm_memory_min_mb": registry.same_as_vm_memory_mb,
    "editor_claude_terminal": True,
    "editor_files_dir": "",
    "editor_session_notes": True,
    "claude_sessions_sync": True,
    "claude_code_remote_control_name": registry.machine_name,
    "claude_code_remote_control_dir": registry.same_as_vm_projects_dir,
    "vpn": "none",
    "tailscale_hostname": registry.tailnet_label,
}
# The 1.0 name each of the four reads today.
READS_A_1_0_NAME = {
    "editor_claude_terminal": "vscode_claude_terminal",
    "editor_files_dir": "vscode_workspaces_dir",
    "editor_session_notes": "vscode_session_notes",
    "claude_sessions_sync": "vscode_claude_sessions",
}
# What each computed default reads today, as its expression says it.
TODAY_READS = {
    "vm_ssh_public_keys": "/.ssh/id_ed25519.pub",
    "vm_memory_min_mb": "{{ proxmox_vm_memory_mb }}",
    "claude_code_remote_control_name": "{{ inventory_hostname }}",
    "claude_code_remote_control_dir": "{{ vm_projects_dir }}",
    "tailscale_hostname": "{{ inventory_hostname | tailscale_label }}",
    "vpn": "{{ 'tailscale' if tailscale_enabled | bool else 'none' }}",
}
# A spec's type, and a list's elements, as the table writes it.
SPEC_TYPES = {"bool": "bool", "int": "int", "str": "str", "path": "path", "dict": "dict"}


@functools.cache
def _today() -> tuple[dict, dict, dict]:
    defaults, feeds = wiring.wiring()
    return defaults, feeds, wiring.specs()


@functools.cache
def _documented() -> tuple[str, ...]:
    sections = wiring.parse_example(EXAMPLE, secret=False)
    return tuple(name for _title, groups in sections for _prose, found in groups for name, _example in found)


def _fed(name: str) -> list[tuple[str, dict]]:
    """The role variables a setting is, or ends up in, with each one's argument spec."""
    _defaults, feeds, specs = _today()
    own = wiring.role_of(name, specs)
    variables = [name] if own else wiring.set_by(name, feeds, specs)
    return [(var, specs[wiring.role_of(var, specs)][var]) for var in variables]


def _spec_type(spec: dict) -> str:
    kind = spec.get("type", "str")
    if kind == "list":
        return f"list[{spec.get('elements', 'raw')}]"
    return SPEC_TYPES.get(kind, kind)


def _literal_type(value: object) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "list[str]"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _plain(value: object) -> object:
    """A row's literal default as today's files hold it: a tuple as a list, a read-only mapping as a dict."""
    if isinstance(value, tuple):
        return list(value)
    if hasattr(value, "keys") and not isinstance(value, dict):
        return dict(value)  # type: ignore[call-overload]
    return value


def _expression(value: object) -> bool:
    return isinstance(value, str) and "{{" in value


def test_every_documented_setting_has_exactly_one_row_but_the_five_older_names() -> None:
    documented = _documented()
    assert {*OLDER, OLDER_VPN} <= set(documented), "the five older names are still documented today"
    rows = [each.name for each in ROWS]
    wanted = [name for name in documented if name not in {*OLDER, OLDER_VPN}]
    assert sorted(name for name in wanted if name not in rows) == [], (
        "give each a row in lib/claude_vm/fleet/registry.py"
    )
    assert sorted(name for name in rows if name not in wanted) == [], "a row of no setting the example documents"
    assert len(rows) == len(wanted) == 97


@pytest.mark.parametrize("name", (*OLDER, OLDER_VPN))
def test_an_older_name_has_no_row_and_no_row_reads_it(name: str) -> None:
    with pytest.raises(KeyError):
        registry.row(name)
    assert not [each.name for each in registry.rows() if name in each.description], name


@pytest.mark.parametrize("each", ROWS, ids=lambda each: each.name)
def test_a_row_s_type_and_choices_are_its_role_variable_s_or_its_literal_default_s(each) -> None:
    defaults, _feeds, _specs = _today()
    fed = _fed(each.name)
    if fed:
        assert each.type in {_spec_type(spec) for _var, spec in fed}, [(var, _spec_type(spec)) for var, spec in fed]
        stated = [tuple(spec["choices"]) for _var, spec in fed if spec.get("choices")]
        assert all(set(choices) == set(each.choices) for choices in stated), stated
        assert stated or each.choices == ()
        return
    default = defaults[each.name]
    if each.name in READS_A_1_0_NAME:
        default = defaults[READS_A_1_0_NAME[each.name]]
    literal = registry.row(each.name).default if _expression(default) else default
    assert each.type == _literal_type(_plain(literal))
    if each.name in catalogue.CHOICES:
        listed, also = catalogue.CHOICES[each.name]
        assert set(each.choices) == set(defaults[listed]) | set(also)
    else:
        assert each.choices == ()


@pytest.mark.parametrize("each", ROWS, ids=lambda each: each.name)
def test_a_literal_default_is_today_s(each) -> None:
    defaults, _feeds, _specs = _today()
    today = defaults[each.name]
    if _expression(today):
        assert each.name in FROM_AN_EXPRESSION, f"{each.name}'s default is an expression today: name what it becomes"
        assert each.default == FROM_AN_EXPRESSION[each.name] or each.default is FROM_AN_EXPRESSION[each.name]
    else:
        assert each.name not in FROM_AN_EXPRESSION
        assert not callable(each.default)
        assert _plain(each.default) == today


def test_each_default_from_an_expression_stands_for_what_today_s_expression_reads() -> None:
    defaults, feeds, specs = _today()
    rows = {each.name for each in ROWS}
    assert sorted(FROM_AN_EXPRESSION) == sorted(name for name in rows if _expression(defaults[name]))
    for name, old in READS_A_1_0_NAME.items():
        assert defaults[name] == "{{ " + old + " }}"
        assert registry.row(name).default == defaults[old]
    for name, reads in TODAY_READS.items():
        assert reads in defaults[name], name
    assert defaults[OLDER_VPN] is False
    # vm_memory_min_mb's proxmox_vm_memory_mb is vm_memory_mb's.
    assert "proxmox_vm_memory_mb" in feeds["vm_memory_mb"]
    assert wiring.role_of("proxmox_vm_memory_mb", specs) == "proxmox_vm"


@pytest.mark.parametrize(
    "machine", ["alpha", "Alpha_Lab", "dev.web--2", "--edge--", "x" * 62 + "-y", "a" * 70, "café", "___", "a b"]
)
def test_the_tailnet_name_is_made_as_today_s_role_makes_it(machine: str) -> None:
    assert registry.tailnet_label(machine=machine) == tailscale_label(machine)


@pytest.mark.parametrize("each", ROWS, ids=lambda each: each.name)
def test_a_row_s_group_is_the_role_today_s_settings_gives_it(each) -> None:
    # tools/settings' Settings.setting: the role of the first role variable the setting feeds, else the role whose
    # variable it is, else common, the role whose play it is read in.
    _defaults, feeds, specs = _today()
    fed = [wiring.role_of(var, specs) for var in wiring.set_by(each.name, feeds, specs)]
    assert each.group == next((role for role in fed if role), wiring.role_of(each.name, specs) or "common")


def test_a_setting_each_host_sets_for_itself_today_is_each_host_s_own() -> None:
    kind = KINDS["proxmox"]
    rows = {each.name: each for each in ROWS}
    for name in (*kind.always, *kind.never_inherited):
        assert rows[name].scope == "host", name
    # The computer's own of them is kept in client.yml's map of hosts; the rest in the host's settings.yml.
    assert sorted(name for name in (*kind.always, *kind.never_inherited) if rows[name].home == "client") == [
        "claude_ssh_host_domain"
    ]
