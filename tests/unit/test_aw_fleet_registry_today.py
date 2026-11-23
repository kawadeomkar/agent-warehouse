"""The settings table against today's files: the cutover's check, which goes with those files (lib/claude_vm/fleet/
registry.py; docs/architecture/engine.md §16.9).

Every setting today's example documents (inventory/group_vars/all/local.yml.example, read by its own rule,
lib/claude_vm/wiring.py's parse_example) has exactly one row, but the five older names and the four the engine keeps
elsewhere or has no use for, which have none, each named with where it went: a setting added to the example without a
row fails here by its name. Each row's type and choices are the argument spec's of the role variable it feeds
(lib/claude_vm/wiring.py's set_by), or, for a setting that feeds none, its literal default's type and the choices
today's checks hold it to (lib/claude_vm/catalogue.py's CHOICES); every literal default equals today's, and each
default that is an expression today is the literal or the function this file names for it; each row's group is the
role today's just settings gives it; and a setting each host sets for itself today (lib/claude_vm/hosts/kinds.py) is
each host's own. A row names a secret where the role variable it feeds is no_log today, and is made from the machine's
name where today's default is; the rows an agent may change are today's server's ALLOWED, with VALUES' rule, and every
name of ALLOWED and REFUSED is a row but the nine (lib/claude_vm/mcp/confirm.py); and the settings the engine adds
that stand for today's variables keep their types, defaults and groups, run_forks ansible.cfg's forks.
"""

from __future__ import annotations

import configparser
import functools
from pathlib import Path

import pytest

from claude_vm import catalogue, wiring
from claude_vm.fleet import registry
from claude_vm.fleet.registry import ADDED, ROWS
from claude_vm.hosts.kinds import KINDS
from claude_vm.mcp import confirm
from claude_vm.vpn.tailscale import tailscale_label

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "inventory" / "group_vars" / "all" / "local.yml.example"
ANSIBLE_CFG = REPO / "ansible.cfg"
# The five older names today's files still read, with no row: the four 1.0 names of the editor's settings and
# tailscale_enabled, the old form of vpn (review/clean-slate/PROPOSAL.md, decision 10). tailscale_enabled stays the
# tailscale role's own variable, set from vpn, and is no setting a person writes.
OLDER = ("vscode_claude_terminal", "vscode_workspaces_dir", "vscode_session_notes", "vscode_claude_sessions")
OLDER_VPN = "tailscale_enabled"
# Four documented settings with no row, and where each went: the API's port, the token's user and its ID are fields of
# each host's host.yml (D6), and the new client always checks the API's certificate, so there is no switch to turn it
# off.
LEFT = {
    "proxmox_api_user": "a field of each host's host.yml",
    "proxmox_api_token_id": "a field of each host's host.yml",
    "proxmox_api_port": "a field of each host's host.yml",
    "proxmox_validate_certs": "no setting: the new client always checks the API's certificate",
}
NO_ROW = (*OLDER, OLDER_VPN, *LEFT)
# The settings the engine adds that stand for a variable of today's, by that variable's name: one that today's vault
# held and is no secret, one today's role takes, and two the repository prints for one run.
STANDS_FOR = {
    "github_username": "github_username",
    "destroy_allow_unrecorded": "proxmox_vm_allow_untagged_delete",
    "proxmox_vm_force_update": "proxmox_vm_force_update",
    "configure_wait_for_cloud_init": "configure_wait_for_cloud_init",
}
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
# A row's type as today's spec states it, where the table's is narrower: a size with its unit is a string today.
TODAY_S_TYPE = {"size": "str"}
# The paths on this computer whose literal default today is "", which reads as a string: typed path, so that the path
# rule reaches them (review 12, D3.md 4).
PATHS_OF_THIS_COMPUTER = {
    "claude_ssh_identity_file": "the key each alias names, a file of this computer's, as ssh_identity_file is",
    "vscode_user_settings_file": "the editor's settings file, read on this computer",
    "destroy_sessions_dir": "the folder destroy saves a VM's sessions into, on this computer",
}


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


def test_every_documented_setting_has_exactly_one_row_but_nine_each_named() -> None:
    documented = _documented()
    assert set(NO_ROW) <= set(documented), "the nine are still documented today"
    assert all(where.strip() for where in LEFT.values())
    rows = [each.name for each in ROWS]
    wanted = [name for name in documented if name not in NO_ROW]
    assert sorted(name for name in wanted if name not in rows) == [], (
        "give each a row in lib/claude_vm/fleet/registry.py"
    )
    assert sorted(name for name in rows if name not in wanted) == [], "a row of no setting the example documents"
    assert len(rows) == len(wanted) == 93


@pytest.mark.parametrize("name", NO_ROW)
def test_a_name_with_no_row_has_none_and_no_row_reads_it(name: str) -> None:
    with pytest.raises(KeyError):
        registry.row(name)
    assert not [each.name for each in registry.rows() if name in each.description], name


def test_no_setting_the_engine_adds_is_one_today_s_example_documents() -> None:
    assert sorted({each.name for each in ADDED} & set(_documented())) == []


@pytest.mark.parametrize("each", ROWS, ids=lambda each: each.name)
def test_a_row_s_type_and_choices_are_its_role_variable_s_or_its_literal_default_s(each) -> None:
    defaults, _feeds, _specs = _today()
    fed = _fed(each.name)
    if fed:
        stated_types = {_spec_type(spec) for _var, spec in fed}
        today = "str" if each.name in PATHS_OF_THIS_COMPUTER else TODAY_S_TYPE.get(each.type, each.type)
        assert today in stated_types, [(var, _spec_type(spec)) for var, spec in fed]
        stated = [tuple(spec["choices"]) for _var, spec in fed if spec.get("choices")]
        assert all(set(choices) == set(each.choices) for choices in stated), stated
        assert stated or each.choices == ()
        return
    default = defaults[each.name]
    if each.name in READS_A_1_0_NAME:
        default = defaults[READS_A_1_0_NAME[each.name]]
    literal = registry.row(each.name).default if _expression(default) else default
    if each.name in PATHS_OF_THIS_COMPUTER:
        assert (each.type, literal) == ("path", ""), each.name
        return
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
    own = [name for name in (*kind.always, *kind.never_inherited) if name not in LEFT]
    for name in own:
        assert rows[name].scope == "host", name
    # The computer's own of them is kept in client.yml's map of hosts: a name this computer's resolver knows, and how
    # this computer reaches the host's VMs (review 12, D3.md 3); the rest in the host's settings.yml.
    assert sorted(name for name in own if rows[name].home == "client") == ["claude_ssh_host_domain", "vm_ssh_route"]
    # The four with no row are each a setting a host sets for itself today.
    assert sorted(name for name in kind.always if name in LEFT) == sorted(LEFT)


# ---------------------------------------------------------------- a secret, a machine's own, an agent's ----


def test_a_row_names_a_secret_where_the_role_variable_it_feeds_is_no_log_today() -> None:
    no_log = sorted(each.name for each in ROWS if any(spec.get("no_log") for _var, spec in _fed(each.name)))
    assert no_log == ["claude_code_settings"]
    assert sorted(each.name for each in ROWS if each.secret) == no_log
    assert not [each.name for each in ADDED if each.secret]


def test_a_row_is_each_machine_s_own_where_today_s_default_is_made_from_its_name() -> None:
    # Today's settings refuses to write such a setting: one value would give every VM the same.
    defaults, _feeds, _specs = _today()
    made = sorted(each.name for each in ROWS if "inventory_hostname" in str(defaults[each.name]))
    assert made == ["claude_code_remote_control_name", "tailscale_hostname"]
    assert sorted(each.name for each in ROWS if each.scope == "machine") == made


def test_the_rows_an_agent_may_change_are_today_s_allowed_with_today_s_rule() -> None:
    assert sorted(each.name for each in registry.rows() if each.agent) == sorted(confirm.ALLOWED)
    assert sorted(each.name for each in registry.rows() if each.values is not None) == sorted(confirm.VALUES)
    for name, (pattern, said) in confirm.VALUES.items():
        rule = registry.row(name).values
        assert (rule.pattern, rule.said) == (pattern.pattern, said)
        for value in ("stable", "latest", "2.1.319", "npm:other-package", "2.1", "2.1.319\n", ""):
            assert rule.allows(value) == (pattern.fullmatch(value) is not None), value


def test_the_column_covers_both_of_today_s_lists() -> None:
    # Every name today's server allows or refuses is a row, but the nine with none, which it refuses.
    listed = set(confirm.ALLOWED) | confirm.REFUSED
    assert set(NO_ROW) <= confirm.REFUSED
    assert sorted(listed - {each.name for each in ROWS} - set(NO_ROW)) == []
    assert sorted({each.name for each in ROWS} - listed) == []


# ---------------------------------------------------------------- what the engine adds, against today ----


def test_run_forks_is_as_many_as_today_s_forks() -> None:
    parser = configparser.ConfigParser()
    parser.read(ANSIBLE_CFG, encoding="utf-8")
    assert registry.row("run_forks").default == parser.getint("defaults", "forks") == 20


@pytest.mark.parametrize(("name", "today"), sorted(STANDS_FOR.items()))
def test_an_added_row_that_stands_for_today_s_variable_keeps_its_type_default_and_group(name: str, today: str) -> None:
    defaults, feeds, specs = _today()
    each = registry.row(name)
    assert each in ADDED
    fed = _fed(today)
    if fed:
        assert each.type in {_spec_type(spec) for _var, spec in fed}
    else:
        assert each.type == _literal_type(defaults[today])
    if _expression(defaults[today]):
        # Today's vault's value, '' where the vault holds none.
        assert defaults[today] == "{{ vault_github_username | default('', true) }}" and each.default == ""
    else:
        assert each.default == defaults[today]
    roles = [wiring.role_of(var, specs) for var in wiring.set_by(today, feeds, specs)]
    assert each.group == next((role for role in roles if role), wiring.role_of(today, specs) or "common")
