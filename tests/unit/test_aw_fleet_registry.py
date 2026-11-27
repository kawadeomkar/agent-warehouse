"""The settings table (lib/claude_vm/fleet/registry.py; docs/architecture/engine.md §16.9).

Every row is well formed: a type the reader knows, never a text (a role's option of several lines alone), choices that
hold its default, a scope and a home that agree with each other, a group and a description of one line that names no
command as text, and its marks for a secret and an agent; a computed default is one of the module's named functions,
reading only rows and what `GIVEN` names; no two rows share a name; today's documented settings the engine keeps are 93
rows, and the five older names and the four that left have none; `ADDED` holds the settings the engine adds, each where
the card placed it, and `CONNECTION` this computer's three SSH rows; a row that names a secret is never one an agent may
change, and one that would be cannot be built; an agent may give claude_code_version a channel or a release alone; a row
made from the machine's name is each machine's own; the table cannot be changed by a reader; and the module imports the
standard library alone. What the rows must agree with in today's files is tests/unit/test_aw_fleet_registry_today.py's,
which goes with those files at the cutover.
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
from claude_vm.fleet.registry import ADDED, CONNECTION, GIVEN, HOMES, ROWS, SCOPES, TYPES, Row, ValueRule
from claude_vm.pve import client as pve_client
from claude_vm.runs import locks
from claude_vm.schema import patterns
from claude_vm.vendors import proxy

MODULE = "claude_vm.fleet.registry"
# The five names today's files still read that get no row: the four 1.0 names of the editor's settings, and
# tailscale_enabled, the old form of vpn (review/clean-slate/PROPOSAL.md, decision 10).
OLDER = ("vscode_claude_terminal", "vscode_workspaces_dir", "vscode_session_notes", "vscode_claude_sessions")
OLDER_VPN = "tailscale_enabled"
# Four of today's documented settings the engine keeps elsewhere or has no use for: the API's port, the token's user and
# its ID are fields of each host's host.yml, and the certificate is always checked.
LEFT = ("proxmox_api_user", "proxmox_api_token_id", "proxmox_api_port", "proxmox_validate_certs")
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
    if kind == "size":
        return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,5}[GT]", value) is not None
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
    assert isinstance(each.secret, bool) and isinstance(each.agent, bool)
    assert each.values is None or (isinstance(each.values, ValueRule) and each.type == "str")
    assert each.form is None or (isinstance(each.form, ValueRule) and each.type == "str")
    assert each.least is None or (type(each.least) is int and each.type == "int")
    if callable(each.default):
        assert each.default in registry.COMPUTED
    else:
        assert _of_type(each.default, each.type), (each.default, each.type)
        if each.choices:
            assert each.default in each.choices
        if each.form is not None:
            assert each.form.allows(each.default), each.name
        if each.least is not None:
            assert each.default >= each.least, each.name
    if each.choices:
        assert each.type == "str", "choices are a string's"


def test_a_text_is_the_type_of_a_role_s_option_of_several_lines_alone_and_of_no_row() -> None:
    # A text, a string that may hold line breaks, is fleet/passthrough.py's TEXT's: a role's option whose value is
    # several lines by what it is. No setting of the table is one: each is one line, typed or in a file.
    assert TYPES == ("bool", "int", "str", "path", "list[str]", "dict", "size", "text")
    assert [each.name for each in registry.rows() if each.type == "text"] == []


@pytest.mark.parametrize("each", registry.rows(), ids=lambda each: each.name)
def test_a_row_s_home_holds_its_scope(each: Row) -> None:
    # The fleet's file holds one value for the fleet, or one machine's in its map machines; a host's file that host's
    # own; client.yml the computer's own at any scope, at its top level, in its map hosts or in its map machines; and a
    # value for one run alone is the whole run's.
    if each.home == "fleet":
        assert each.scope in ("global", "machine")
    if each.home == "host":
        assert each.scope == "host"
    if each.home == "run":
        assert each.scope == "global"


@pytest.mark.parametrize("each", registry.rows(), ids=lambda each: each.name)
def test_a_description_is_one_line_of_plain_text_that_names_no_command_as_text(each: Row) -> None:
    # What a person reads of a setting is printed by the product, which names a command as aw's printer writes it, and
    # never says just (the owner's word 1): a verb is named alone, as the table of verbs' help names one.
    assert all(character.isprintable() for character in each.description), each.description
    assert re.search(r"\bjust\b", each.description) is None, each.description
    # Nor the program the engine left behind, nor today's files: a person of the new engine has no playbook, local.yml
    # or hosts.yml, and a name with vault_ before it is a setting's row, never a vault's variable (review/code/D3.md 5).
    assert re.search(r"ansible|playbook|local\.yml|hosts\.yml", each.description, re.IGNORECASE) is None
    named = set(re.findall(r"\bvault_[a-z_]+", each.description))
    assert named <= {row.name for row in registry.rows()}, named


@pytest.mark.parametrize("each", registry.rows(), ids=lambda each: each.name)
def test_a_row_hashes_and_its_default_is_handed_on_as_plain_values(each: Row) -> None:
    # A row is one object, compared by identity, so that one whose default is a mapping can be hashed and kept in a set;
    # plain is the copy a reader hands on, no tuple and no read-only mapping at any depth (review/code/D3.md 6).
    assert {each: 1}[each] == 1 and each == each and each != dataclasses.replace(each)
    plain = registry.plain(each.default)

    def held(value: object) -> bool:
        if isinstance(value, list):
            return all(held(item) for item in value)
        if isinstance(value, dict):
            return all(held(item) for item in value.values())
        return not isinstance(value, (tuple, MappingProxyType))

    assert held(plain)
    if isinstance(each.default, tuple):
        assert isinstance(plain, list) and len(plain) == len(each.default)
    if isinstance(each.default, MappingProxyType):
        assert isinstance(plain, dict) and plain.keys() == each.default.keys()


def test_no_two_rows_share_a_name() -> None:
    names = [each.name for each in registry.rows()]
    assert sorted(name for name in set(names) if names.count(name) > 1) == []


def test_today_s_documented_settings_the_engine_keeps_are_93_rows_and_the_other_nine_have_none() -> None:
    assert len(ROWS) == 93
    for name in (*OLDER, OLDER_VPN, *LEFT):
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
    assert not {each.name for each in CONNECTION} & {each.name for each in ROWS + ADDED}


def test_the_rows_the_card_placed_are_where_it_placed_them() -> None:
    # One computer's, as the report gives it, and each host's own, never inherited, as today (hosts/kinds.py); and how
    # this computer reaches a host's VMs, which a second computer that copies the fleet's folder decides for itself
    # (review 12, D3.md 3).
    for name in ("claude_ssh_host_domain", "vm_ssh_route"):
        assert (registry.row(name).scope, registry.row(name).home) == ("host", "client"), name
    for name in ("proxmox_pool", "vm_searchdomain"):
        assert (registry.row(name).scope, registry.row(name).home) == ("host", "host"), name


# Where each of today's documented settings is kept, with why (review 12, D3.md 2): a row moved by mistake changes
# where a person may write it, which nothing else would see. This computer's own, in client.yml: the report's
# seventeen, the carry's eight, and vm_ssh_route, how this computer reaches a host's VMs; by scope, global at
# client.yml's top, host in its map hosts.
OF_THIS_COMPUTER = {
    "editor": "global",
    "claude_ssh_config": "global",
    "claude_ssh_config_include": "global",
    "claude_ssh_host_domain": "host",
    "claude_ssh_identity_file": "global",
    "claude_ssh_forward_agent": "global",
    "claude_ssh_options": "global",
    "vm_ssh_route": "host",
    "editor_claude_terminal": "global",
    "editor_files_dir": "global",
    "editor_session_notes": "global",
    "claude_sessions_sync": "global",
    "claude_sessions_sync_max_size": "global",
    "claude_sessions_sync_subagents": "global",
    "claude_memory_sync": "global",
    "claude_instructions_sync": "global",
    "claude_settings_sync": "global",
    "claude_settings_sync_keys": "global",
    "claude_plugins_sync": "global",
    "claude_history_sync": "global",
    "claude_extensions_sync": "global",
    "vscode_user_settings_file": "global",
    "deploy_sign_in": "global",
    "deploy_code": "global",
    "destroy_sessions_dir": "global",
    "login_desktop": "global",
}
# Each host's own, by home: what kinds.py has each host set for itself or never inherit, the pool, and the search
# domain, one network's as the nameservers are; the two of this computer's in client.yml's map hosts.
EACH_HOST_S = {
    "proxmox_node": "host",
    "proxmox_storage": "host",
    "proxmox_bridge": "host",
    "proxmox_pool": "host",
    "proxmox_template_vmid": "host",
    "proxmox_template_custom": "host",
    "vm_nameservers": "host",
    "vm_searchdomain": "host",
    "vm_ipconfig": "host",
    "claude_ssh_host_domain": "client",
    "vm_ssh_route": "client",
}
# Each machine's own, in the fleet's map machines: the two whose default is the machine's name.
EACH_MACHINE_S = ("claude_code_remote_control_name", "tailscale_hostname")


def test_each_of_today_s_settings_is_kept_where_its_reason_puts_it() -> None:
    assert {each.name: each.scope for each in ROWS if each.home == "client"} == OF_THIS_COMPUTER
    assert {each.name: each.home for each in ROWS if each.scope == "host"} == EACH_HOST_S
    assert sorted(each.name for each in ROWS if each.scope == "machine") == sorted(EACH_MACHINE_S)
    assert [registry.row(name).home for name in EACH_MACHINE_S] == ["fleet", "fleet"]
    # Every other one is the fleet's, one value for every host and machine.
    placed = {*OF_THIS_COMPUTER, *EACH_HOST_S, *EACH_MACHINE_S}
    assert [(each.name, each.scope, each.home) for each in ROWS if each.name not in placed] == [
        (each.name, "global", "fleet") for each in ROWS if each.name not in placed
    ]


def test_the_reader_finds_a_row_and_a_group_and_refuses_what_the_table_lacks() -> None:
    assert registry.rows() == ROWS + ADDED + CONNECTION
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


# ---------------------------------------------------------------- the settings the engine adds ----

# Each, as the card places it: its type, scope, home and default, and its choices.
PLACED = {
    "vault_key_file": ("path", "global", "client", registry.the_homes_key_file, ()),
    "vault_key_command": ("list[str]", "global", "client", (), ()),
    "tls_ca_file": ("path", "global", "client", "", ()),
    "network_proxy": ("str", "global", "client", "environment", ()),
    "run_lock": ("str", "global", "client", "machine", ("machine", "fleet")),
    "run_forks": ("int", "global", "client", 20, ()),
    "run_detached_limit": ("int", "global", "fleet", 120, ()),
    "machine_claims": ("str", "global", "fleet", "inform", ("inform", "refuse")),
    "agents_vm_cap": ("int", "global", "fleet", 5, ()),
    "proxmox_audit_scope": ("str", "host", "host", "node", ("node", "pool")),
    "vm_label": ("str", "global", "fleet", "agent-warehouse", ()),
    "vm_first_boot_upgrade": ("bool", "global", "fleet", True, ()),
    "destroy_confirm": ("str", "global", "fleet", "yes", ("yes", "names")),
    "destroy_allow_unrecorded": ("bool", "global", "fleet", False, ()),
    "github_username": ("str", "global", "fleet", "", ()),
    "proxmox_vm_force_update": ("bool", "global", "run", False, ()),
    "configure_wait_for_cloud_init": ("bool", "global", "run", True, ()),
}
# The owner's answers to O15: today's behaviour is each one's default, and the stricter form an opt-in.
TODAY_S_BEHAVIOUR = {
    "network_proxy": "environment",  # the proxy the environment names, as requests takes it today
    "proxmox_audit_scope": "node",  # the token reads the node's other VMs, as status shows them today
    "destroy_confirm": "yes",  # destroy asks for yes, as today
    "destroy_allow_unrecorded": False,  # today's proxmox_vm_allow_untagged_delete, off unless set
    "vm_first_boot_upgrade": True,  # Proxmox's own first-boot upgrade, left alone as today
}


def test_the_settings_the_engine_adds_are_where_the_card_placed_them() -> None:
    assert {each.name: (each.type, each.scope, each.home, each.default, each.choices) for each in ADDED} == PLACED
    assert not {each.name for each in ADDED} & {each.name for each in ROWS}


def test_o15_s_five_rows_keep_today_s_behaviour_by_default() -> None:
    assert {name: registry.row(name).default for name in TODAY_S_BEHAVIOUR} == TODAY_S_BEHAVIOUR
    # network_proxy's two words are the proxy's own; any other value is an http:// URL, so it has no choices.
    assert registry.row("network_proxy").default == proxy.ENVIRONMENT
    assert re.search(rf"\b{proxy.NONE}, no proxy\b", registry.row("network_proxy").description)
    for name in ("proxmox_audit_scope", "destroy_confirm"):
        assert registry.row(name).choices[0] == TODAY_S_BEHAVIOUR[name], "today's behaviour is listed first"


def test_a_vm_s_label_is_the_product_s_name_by_default() -> None:
    label = registry.row("vm_label")
    assert (label.default, label.scope, label.home, label.type) == ("agent-warehouse", "global", "fleet", "str")


def test_the_vault_s_key_file_is_the_home_s_unless_set(tmp_path) -> None:
    assert registry.row("vault_key_file").default is registry.the_homes_key_file
    assert registry.the_homes_key_file(key_file=tmp_path / "the-key") == str(tmp_path / "the-key")
    command = registry.row("vault_key_command")
    assert (command.type, command.default) == ("list[str]", ())


def test_a_path_of_this_computer_s_says_how_it_is_read() -> None:
    # tls_ca_file was taken against the working directory: ~ is the user's home, a relative path is refused by name,
    # and "" is unset (the coordinator's ruling of 2026-11-21).
    paths = [each for each in registry.rows() if each.type == "path"]
    assert sorted(each.name for each in paths) == [
        "claude_ssh_identity_file",
        "destroy_sessions_dir",
        "ssh_executable",
        "ssh_identity_file",
        "tls_ca_file",
        "vault_key_file",
        "vscode_user_settings_file",
    ]
    for each in paths:
        assert "~ at its start is your home; a relative path is refused by name." in each.description, each.name
        # "" is unset, and each row says what that means (review 12, D4.md 3).
        assert re.search(r"\bEmpty\b", each.description), each.name
    assert "Empty: none." in registry.row("tls_ca_file").description
    # The vault's key file: empty is the default, and a file named where the key is never kept is refused
    # (fleet/vaultkey.py, tests/unit/test_aw_fleet_vaultkey.py).
    key_file = registry.row("vault_key_file").description
    assert "Empty: the key file in a folder of this computer's own" in key_file
    assert "a file you name in one of them is refused by name" in key_file
    # A key file open to another account is refused, as ssh refuses such a key (fleet/vaultkey.py).
    assert (
        "Only you may read or change it: one that other accounts may read, write or run is refused, as ssh refuses"
        " such a key." in key_file
    )


def test_five_rows_are_held_to_more_than_their_type_by_one_rule_each() -> None:
    # Their readers need more than a type (review 12, D4.md 5): one Proxmox tag, which a , or a ; would make two; a
    # GitHub account's name, as init --github-user takes it; a pool's name as Proxmox makes one, or none (card E5's
    # landing); at least one machine at once; and the two policies of the run's locks. network_proxy is held by
    # vendors/proxy.py's one check (tests/unit/test_aw_fleet_overrides.py).
    assert {each.name: each.form for each in registry.rows() if each.form is not None} == {
        "vm_label": registry.PROXMOX_TAG,
        "github_username": registry.GITHUB_USER,
        "proxmox_pool": registry.PROXMOX_POOL,
    }
    assert registry.PROXMOX_TAG.pattern == pve_client.TAG.pattern
    assert registry.GITHUB_USER.pattern == patterns.PATTERNS["github_user"].text
    for tag in ("a,b", "a;b", "a b", "-a", "", "a" * 101):
        assert not registry.PROXMOX_TAG.allows(tag), tag
    # A pool: from a letter, no /, which makes a nested pool on Proxmox VE 9.2, no space or colon, at most 64
    # characters; or none, the fleet's own. engine/access.py's POOL_NAME and POOL_RULE are this one rule, never a copy.
    for pool in ("", "aw-fleet", "dev.vms_1", "P" + "p" * 63):
        assert registry.PROXMOX_POOL.allows(pool), pool
    for pool in ("1fleet", "_pool", "-pool", "fleet/aw", "aw-fleet ", " aw-fleet", "a:b", "a b", "p" * 65):
        assert not registry.PROXMOX_POOL.allows(pool) and not registry.POOL_NAME.allows(pool), pool
    assert not registry.POOL_NAME.allows("")
    assert registry.PROXMOX_POOL.said == f"a pool's name: {registry.POOL_NAME.said}"
    # And the least a whole number may be: one run at once at the least, and no negative bound (D5's hand-back).
    assert {each.name: each.least for each in registry.rows() if each.least is not None} == {
        "run_forks": 1,
        "run_detached_limit": 0,
        "agents_vm_cap": 0,
    }
    assert registry.row("run_lock").choices == locks.POLICIES


@pytest.mark.parametrize(
    ("type_", "marks", "refused"),
    [
        ("int", {"form": registry.GITHUB_USER}, "has a form, which only a string's value is held to"),
        ("str", {"least": 1}, "has a least value, which only an integer's is held to"),
    ],
)
def test_a_form_is_a_string_s_and_a_least_value_an_integer_s(type_: str, marks: dict, refused: str) -> None:
    with pytest.raises(ValueError, match=re.escape(refused)):
        Row("made_up", type_, (), "", "global", "fleet", "common", "A setting of no one's.", **marks)


def test_the_one_size_with_a_unit_is_the_disk_s_which_deploy_s_disk_stands_for() -> None:
    # vm_disk_size is typed as --disk is read, the unit in either case and kept capital (the owner's answer of
    # 2026-11-22); the sessions' two bounds are sizes of another form (M, a number alone, 0), kept as text.
    assert [each.name for each in registry.rows() if each.type == "size"] == ["vm_disk_size"]
    assert registry.row("vm_disk_size").default == "32G"
    for name in ("claude_sessions_sync_max_size", "destroy_sessions_max_size"):
        assert registry.row(name).type == "str", name


def test_a_row_for_one_run_alone_is_kept_in_no_file() -> None:
    assert sorted(each.name for each in registry.rows() if each.home == "run") == [
        "configure_wait_for_cloud_init",
        "proxmox_vm_force_update",
    ]
    for each in registry.rows():
        if each.home == "run":
            assert each.description.endswith("Kept in no file."), each.name
            assert not each.agent and not each.secret


def test_a_row_made_from_the_machine_s_name_is_each_machine_s_own() -> None:
    # Set for the fleet, it would give every VM one name: today's settings refuses to write one for that reason.
    made = [
        each.name
        for each in registry.rows()
        if callable(each.default) and "machine" in inspect.signature(each.default).parameters
    ]
    assert sorted(made) == ["claude_code_remote_control_name", "tailscale_hostname"]
    for name in made:
        assert registry.row(name).scope == "machine", name
    hostname = registry.row("tailscale_hostname")
    assert (hostname.default, hostname.home) == (registry.tailnet_label, "fleet")


def test_whose_repositories_are_cloned_is_a_setting_of_the_fleet_s_and_no_secret() -> None:
    each = registry.row("github_username")
    assert (each.type, each.scope, each.home, each.group, each.default) == (
        "str",
        "global",
        "fleet",
        "github_projects",
        "",
    )
    assert not each.secret and not each.agent


# ---------------------------------------------------------------- a secret, and what an agent may change ----


def test_a_row_that_names_a_secret_is_never_one_an_agent_may_change() -> None:
    assert [each.name for each in registry.rows() if each.secret and each.agent] == []
    # claude_code_settings may carry an API key in its env, as today's role marks it.
    assert [each.name for each in registry.rows() if each.secret] == ["claude_code_settings"]
    assert len([each for each in registry.rows() if each.agent]) == 36


@pytest.mark.parametrize(
    ("marks", "refused"),
    [
        ({"secret": True, "agent": True}, "names a secret, so no agent may change it"),
        ({"values": registry.VERSION_FROM_AN_AGENT}, "has a rule for an agent's values, but no agent may change it"),
        ({"secret": True, "values": registry.VERSION_FROM_AN_AGENT}, "has a rule for an agent's values"),
    ],
)
def test_a_row_an_agent_may_change_that_names_a_secret_cannot_be_built(marks: dict, refused: str) -> None:
    with pytest.raises(ValueError, match=re.escape(refused)):
        Row("made_up", "str", (), "", "global", "fleet", "common", "A setting of no one's.", **marks)
    for each in registry.rows():
        with pytest.raises(ValueError):
            dataclasses.replace(each, secret=True, agent=True)
    assert Row("made_up", "str", (), "", "global", "fleet", "common", "A setting.", agent=True).agent


def test_an_agent_may_give_claude_code_version_a_channel_or_a_release_alone() -> None:
    assert [each.name for each in registry.rows() if each.values is not None] == ["claude_code_version"]
    rule = registry.row("claude_code_version").values
    assert rule is registry.VERSION_FROM_AN_AGENT and registry.row("claude_code_version").agent
    for value in ("stable", "latest", "2.1.319", "10.0.0"):
        assert rule.allows(value), value
    for value in ("npm:other-package", "github:someone/claude-code", "2.1", "2.1.319\n", " latest", "latest ", ""):
        assert not rule.allows(value), value
    assert rule.said == "stable, latest or a version as 2.1.319"


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
