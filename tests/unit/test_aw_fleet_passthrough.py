"""The role options passed through (lib/claude_vm/fleet/passthrough.py; docs/architecture/engine.md §16.9).

Every option of the roles is one of three, and none is left out: a row's (its name, or reached by a row's setting
through the wiring), passed through, or refused with why. An option passed through carries its role's spec - its
type, choices, list elements and description, and the entry point that first lists it - and is held to that spec when
typed and in a file: a value outside its type or its choices is refused, a list's elements are held, a path on the
machine is kept as typed. A secret is never passed through, whatever reaches it, nor a value the project's defaults, a
role or a play sets for itself, by each road the scan reads - a set_fact by any of its names, given its names directly
(a templated name as every name it can make), through action: or local_action: (text split at any whitespace, or a
mapping's module and its own args:), args: or a module_defaults of a role's or a play's, a register, a vars:, a play's
vars_files, vars_prompt and role entries' parameters, a file of a role's vars/ folder, in every folder under roles/ and
every play - unless another part of the engine holds it and it is left out by name; and no road whose names cannot be
read (an include_vars, a template in their place, a free-form name of no variable's shape), no include of a file or a
role the scan does not read, and no role's dependencies is in a role's or a play's files. A loop's variable is not read.
The overrides take its name where they take a row's, at the top of the fleet's settings.yml and with --set, and refuse
one of the roles' options that is no setting by its name and why, in a file and for one run, never saying the value. The
two whose value is several lines by what they are, an armored key and a whole CLAUDE.md, are each a text: taken from the
fleet's file as YAML's block holds them and read back whole, the vendored key itself among them, with no control
character but the line's end and no expression, and never typed, the refusal naming the file.

Until the cutover, today's tools/settings is run as a child, against an inventory made from the example files, with a
home and a collections path of its own: every name it lets a person set (its change starts with its own command) is
taken here, as a row or as an option passed through, but the nine of today's settings the table has no row for and the
options left out by name, which the test names; every option it writes through another setting is refused here
through that setting's row; and every name it refuses is no option here, refused for today's reason. After the cutover
the same test reads the roles' specs alone.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml
from aw_kit import imports_of
from harness import REPO

from claude_vm import wiring
from claude_vm.fleet import overrides, passthrough, paths, registry
from claude_vm.fleet.overrides import Refused, Value
from claude_vm.fleet.passthrough import (
    ANY_VALUE,
    BY_A_RUN,
    HOST_FLAG,
    LEFT_OUT,
    PER_MACHINE,
    SECRET,
    SET_BY_A_RUN,
    SSH_VIA,
    TEXT,
    THROUGH,
)
from claude_vm.guest.variables import NAME
from claude_vm.secret import Secret

MODULE = "claude_vm.fleet.passthrough"
TOOL = REPO / "tools" / "settings"
ROLES = wiring.roles()
# What a person may have typed where it does not belong: no refusal says it back.
TYPED = "swordfish-0000"
# The five older names today's files still read that the table gives no row (review/clean-slate/PROPOSAL.md, decision
# 10): the four 1.0 names of the editor's settings, no role's option and unknown names here, and tailscale_enabled, the
# old form of vpn, a typed option of the tailscale role, which the run sets from vpn and which is left out by name.
OLDER = ("vscode_claude_terminal", "vscode_workspaces_dir", "vscode_session_notes", "vscode_claude_sessions")
OLDER_VPN = "tailscale_enabled"
# Four of today's documented settings the table has no row for: the API's port, the token's user and its ID are fields
# of each host's host.yml, and the API's certificate is always checked.
LEFT = ("proxmox_api_user", "proxmox_api_token_id", "proxmox_api_port", "proxmox_validate_certs")
# The four options those four fed today, left out by name with that reason: today's tool writes the first three as the
# settings that feed them, and the fourth by its own name.
LEFT_WITH_THEM = ("proxmox_vm_api_port", "proxmox_vm_api_user", "proxmox_vm_api_token_id", "proxmox_vm_validate_certs")
# The options another part of the engine holds, which today's tool writes by their own names, left out by name with why:
# the host's address and its certificate's anchor (host.yml, and the anchor beside it), the client's bound on a call,
# the ownership tags (the pool and the record), the one naming vpn in the VPN's messages, the template a run chooses,
# the golden's build VM a role sets for itself, a folder of this computer's, each machine's own repositories and
# Tailscale login (its record), the three lines naming a machine's host and the two routes to it (the run's).
HELD_ELSEWHERE = (
    "proxmox_vm_api_host",
    "proxmox_vm_ca_path",
    "proxmox_vm_api_timeout",
    "proxmox_vm_tags",
    "proxmox_vm_tags_1x",
    "tailscale_off_setting",
    "proxmox_vm_template_name",
    "proxmox_template_build_vm",
    "proxmox_template_known_hosts_dir",
    "github_projects_vm_repos",
    "tailscale_joined_tag",
    "claude_code_host_flag",
    "github_projects_host_flag",
    "tailscale_host_flag",
    "claude_code_ssh_via",
    "github_projects_ssh_via",
)
# Today's settable names the engine does not take: the nine with no row, the one of the four options today's tool
# writes by its own name, and the options another part of the engine holds.
NOT_TAKEN = (*OLDER, OLDER_VPN, *LEFT, "proxmox_vm_validate_certs", *HELD_ELSEWHERE)
# An option today's tool writes by its own name that a row of another name takes, with today's behaviour (D4).
TAKEN_AS = {"proxmox_vm_allow_untagged_delete": "destroy_allow_unrecorded"}
# Today's refused names that are rows: one that may hold a secret, never given for one run and refused a value by the
# settings verb (D10); and the two whose default is the machine's name, refused today since one setting would give every
# VM the same, which the table makes each machine's own (scope machine, D4).
REFUSED_TODAY_ROWS = {
    "claude_code_settings": "a row that may hold a secret",
    "claude_code_remote_control_name": "a row of each machine's own",
    "tailscale_hostname": "a row of each machine's own",
}
# An option today's vault fed that a row of the table reaches now: whose repositories are listed, which today's vault
# held and the table's github_username, no secret, holds (D4).
FROM_THE_VAULT_TO_A_ROW = {"github_projects_username": "github_username"}
# How today's tool says why, after the name.
TODAY_PER_VM = "made from each VM's own name"
TODAY_BY_A_RUN = "set by a run itself"
TODAY_VAULT = "just vault"
TODAY_WRITES = "just settings "


def _where() -> paths.Home:
    """The fleet's home for this test: its own, under AGENT_WAREHOUSE_HOME (tests/unit/conftest.py's aw_home)."""
    return paths.home(REPO)


def _put(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _fleet_file(where: paths.Home) -> Path:
    return paths.fleet_dir(where) / "settings.yml"


def _passed() -> dict[str, passthrough.Option]:
    return {each.name: each for each in passthrough.options() if each.refused is None}


def _row_names() -> set[str]:
    return {each.name for each in registry.rows()}


def _spec_files(role: str) -> dict:
    return yaml.safe_load((REPO / "roles" / role / "meta" / "argument_specs.yml").read_text())["argument_specs"]


# ---------------------------------------------------------------- every option, and none left out ----


def test_every_option_of_the_roles_is_a_row_s_passed_through_or_refused_and_none_is_left_out() -> None:
    specs = wiring.specs()
    rows, passed = _row_names(), _passed()
    names = [name for role in ROLES for name in specs[role]]
    assert len(names) == len(set(names)), "an option's name is two roles'"
    for name in names:
        kinds = [name in rows, name in passed, passthrough.refusal(name) is not None]
        assert kinds.count(True) == 1, (name, kinds)
    # What options() lists is every option no row names, each passed or refused, and nothing else.
    listed = {each.name for each in passthrough.options()}
    assert set(passed) <= listed <= set(names) and not listed & rows
    # 203 options today: 71 passed through, the rest a row's or refused.
    assert (len(names), len(passed)) == (203, 71)


def test_an_option_passed_through_carries_its_role_s_spec() -> None:
    specs = wiring.specs()
    for each in passthrough.options():
        spec = specs[each.role][each.name]
        assert each.type == spec["type"], each.name
        assert each.choices == tuple(spec.get("choices") or ()), each.name
        assert each.elements == spec.get("elements"), each.name
        assert each.no_log is bool(spec.get("no_log")), each.name
        described = spec.get("description") or ""
        lines = described if isinstance(described, list) else [described]
        assert each.description == " ".join(" ".join(lines).split()), each.name
        # The entry point that first lists it, main first.
        entry_points = _spec_files(each.role)
        listing = [name for name, entry in entry_points.items() if each.name in ((entry or {}).get("options") or {})]
        assert each.entry_point == ("main" if "main" in listing else listing[0]), each.name
    assert passthrough.option("dev_tools_deb_arch") == passthrough.Option(
        name="dev_tools_deb_arch",
        role="dev_tools",
        entry_point="main",
        type="str",
        choices=("amd64", "arm64"),
        elements=None,
        no_log=False,
        description=passthrough.option("dev_tools_deb_arch").description,
        refused=None,
    )
    assert (passthrough.option("common_packages").type, passthrough.option("common_packages").elements) == (
        "list",
        "str",
    )
    assert passthrough.option("proxmox_template_build_vm").entry_point == "golden_template"
    assert passthrough.option("proxmox_template_golden_cores").entry_point == "golden_read"


def test_an_option_is_read_by_a_row_of_its_spec_s_type_the_fleet_s_for_the_whole_fleet() -> None:
    for each in _passed().values():
        row = passthrough.row(each.name)
        assert (row.name, row.choices, row.default, row.scope, row.home, row.group) == (
            each.name,
            each.choices,
            None,
            "global",
            "fleet",
            each.role,
        )
        assert row.type == ("text" if each.name in TEXT else passthrough.ROW_TYPES[each.type]), each.name
        assert not (row.secret or row.agent or row.values or row.form or row.least is not None)
    # The two of several lines by what they are, each passed through as a text, a string of its spec with no choices.
    assert sorted(TEXT) == ["claude_code_global_instructions", "proxmox_template_image_key"]
    for name in TEXT:
        assert _passed()[name].type == "str" and not _passed()[name].choices, name
        assert passthrough.row(name).type == "text" and "text" in registry.TYPES
    # A path is one on the machine the role runs on: a string, never this computer's path.
    assert passthrough.row("tailscale_marker_dir").type == "str"
    for name in ("tailscale_enabled", "proxmox_vm_cores", "vm_cores", "tailscale_auth_key", TYPED):
        with pytest.raises(KeyError):
            passthrough.row(name)


def test_a_new_kind_of_option_is_refused_before_it_is_passed_through() -> None:
    for spec, said in [
        ({"type": "list", "elements": "int"}, "a list of int"),
        ({"type": "list"}, "a list of None"),
        ({"type": "float"}, "of type float"),
        ({"type": "int", "choices": [1, 2]}, "choices of type int"),
    ]:
        with pytest.raises(ValueError, match=said):
            passthrough._row_type("made_up_option", spec)
    # A text is a string of its spec with no choices: a spec that changed its type or gained choices is refused.
    for spec in ({"type": "list", "elements": "str"}, {"type": "str", "choices": ["a"]}, {"type": "path"}):
        with pytest.raises(ValueError, match="only a string with no choices holds"):
            passthrough._row_type("claude_code_global_instructions", spec)
    assert passthrough._row_type("claude_code_global_instructions", {"type": "str"}) == "text"


# ---------------------------------------------------------------- what is no setting ----


def test_an_option_a_row_s_setting_reaches_is_set_through_that_row_alone() -> None:
    _defaults, feeds = wiring.wiring()
    specs = wiring.specs()
    rows = _row_names()
    reached = {}
    for each in registry.rows():
        for variable in wiring.set_by(each.name, feeds, specs):
            reached.setdefault(variable, each.name)
    through = {name: row for name, row in reached.items() if name not in rows and name not in LEFT_OUT}
    assert through, "no row reaches an option"
    for name, row in through.items():
        assert passthrough.refusal(name) == THROUGH.format(row=row), name
        assert name not in _passed()
    assert passthrough.refusal("proxmox_vm_cores") == THROUGH.format(row="vm_cores")
    assert passthrough.refusal("claude_code_login_redirect") == THROUGH.format(row="login_desktop")
    assert passthrough.refusal("github_projects_username") == THROUGH.format(row="github_username")


def test_a_secret_is_never_passed_through_whatever_reaches_it() -> None:
    specs = wiring.specs()
    rows = _row_names()
    marked = {name for role in ROLES for name, spec in specs[role].items() if spec.get("no_log")}
    assert len(marked) == 10
    for name in marked - rows:
        assert passthrough.refusal(name) == SECRET, name
    # The one row among them is the table's one secret, which a run is never given.
    assert {name for name in marked & rows} == {"claude_code_settings"} and registry.row("claude_code_settings").secret
    # A name the vault holds reaches it, though its spec marks it no secret.
    assert not specs["tailscale"]["tailscale_oauth_client_id"].get("no_log")
    assert passthrough.refusal("tailscale_oauth_client_id") == SECRET
    assert not [each.name for each in passthrough.options() if each.refused is None and each.no_log]


def test_the_options_left_out_by_name_are_each_a_role_s_option_refused_for_its_reason() -> None:
    specs = wiring.specs()
    every = {name for role in ROLES for name in specs[role]}
    assert set(LEFT_OUT) == {*LEFT_WITH_THEM, OLDER_VPN, "tailscale_wanted", *TAKEN_AS, *HELD_ELSEWHERE}
    assert len(HELD_ELSEWHERE) == len(set(HELD_ELSEWHERE)) == 16
    for name, why in LEFT_OUT.items():
        assert name in every and name not in _row_names(), name
        assert passthrough.refusal(name) == why == passthrough.option(name).refused
    for name, row in TAKEN_AS.items():
        assert LEFT_OUT[name] == THROUGH.format(row=row) and registry.row(row).type == "bool"
    assert "host.yml" in LEFT_OUT["proxmox_vm_api_port"] and "always checked" in LEFT_OUT["proxmox_vm_validate_certs"]
    assert "host.yml" in LEFT_OUT["proxmox_vm_api_host"] and "anchor" in LEFT_OUT["proxmox_vm_ca_path"]
    assert {name for name, why in LEFT_OUT.items() if why == HOST_FLAG} == {
        "claude_code_host_flag",
        "github_projects_host_flag",
        "tailscale_host_flag",
    }
    assert {name for name, why in LEFT_OUT.items() if why == SSH_VIA} == {
        "claude_code_ssh_via",
        "github_projects_ssh_via",
    }
    for name in ("proxmox_vm_tags", "proxmox_vm_tags_1x"):
        assert "its pool and its record" in LEFT_OUT[name], name
    for name in ("github_projects_vm_repos", "tailscale_joined_tag"):
        assert LEFT_OUT[name].startswith("is each machine's own: its record keeps"), name


def test_a_machine_s_own_a_run_s_own_and_one_of_any_value_are_refused() -> None:
    specs = wiring.specs()
    every = {name for role in ROLES for name in specs[role]}
    assert len(BY_A_RUN) == 22 and BY_A_RUN <= every
    for name in BY_A_RUN:
        assert passthrough.refusal(name) == SET_BY_A_RUN, name
    per_machine = sorted(name for name in every if passthrough.refusal(name) == PER_MACHINE)
    assert per_machine == ["common_hostname", "proxmox_vm_name"]
    assert sorted(name for name in every if passthrough.refusal(name) == ANY_VALUE) == ["proxmox_vm_id"]


# An action by any name ansible-core resolves to it: bare, ansible.builtin., ansible.legacy. or a collection's.
def _is(action: object, *names: str) -> bool:
    return isinstance(action, str) and any(action == name or action.endswith(f".{name}") for name in names)


# The names of free-form k=v text, as ansible-core's parse_kv reads a set_fact's: each word's part before its first =,
# each held to a role variable's shape (guest/variables.py's NAME), or a name the scan cannot read.
FREE_FORM = re.compile(r"(?:^|\s)([^\s=]+)=")
TEMPLATED = ("{{", "{%")
# A template's marks in a name set_fact templates, each of which can make any text.
MARKS = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)
# What Ansible loads a task, handler or variables file from: a file with one of these suffixes, never a hidden one.
LOADED = ("", ".yml", ".yaml", ".json")
# The actions that read a file of tasks or plays, and those that run a role, by any of their names.
INCLUDES = ("include_tasks", "import_tasks", "import_playbook")
ROLE_INCLUDES = ("include_role", "import_role")
# The folder of a role each of an include_role's *_from arguments reads a file from, and whether as tasks.
FROM = {"tasks_from": ("tasks", True), "handlers_from": ("handlers", True), "vars_from": ("vars", False)}


@dataclasses.dataclass
class _Scan:
    """What the scan found, each with the files it is in: every name set for the play; every name a set_fact templates,
    as a pattern of the names it can make; every road whose names it cannot read (an include_vars, a template given in
    their place, a name of no variable's shape, an include of a file or a role the walk does not read, a role's
    dependencies); and every action group a module_defaults gives arguments to."""

    names: dict[str, set[str]] = dataclasses.field(default_factory=dict)
    patterns: dict[str, set[str]] = dataclasses.field(default_factory=dict)
    unread: dict[str, set[str]] = dataclasses.field(default_factory=dict)
    groups: dict[str, set[str]] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class _At:
    """The file the walk reads, under the tree's root, beside every file it reads as tasks or plays (tasks) and as a
    role's variables (variables): an include of any other is a road it does not follow."""

    root: Path
    path: Path
    tasks: frozenset[Path]
    variables: frozenset[Path]

    def __str__(self) -> str:
        return str(self.path.relative_to(self.root))


def _noted(found: dict[str, set[str]], key: object, where: object) -> None:
    found.setdefault(str(key), set()).add(str(where))


def _templated(text: object) -> bool:
    return not isinstance(text, str) or any(mark in text for mark in TEMPLATED)


def _pattern(name: str) -> str:
    """The names a templated name can make, as a pattern: its text outside the template's marks as it is, each mark any
    text, and a name with a mark left open any name."""
    parts = MARKS.split(name)
    if any(mark in part for part in parts for mark in ("{{", "{%", "{#")):
        return ".*"
    return ".*".join(re.escape(part) for part in parts)


def _facts(given: object, where: object, scan: _Scan, road: str) -> None:
    """The names a set_fact is given by road: a mapping's keys, or free-form text's names, each of a role variable's
    shape, and a name that is a template (set_fact templates its keys) as every name it can make; a template in their
    place, or a name that is no string or, in free-form text, of another shape, is a road whose names the scan cannot
    read."""
    if given is None:
        return
    if isinstance(given, dict):
        names = [name for name in given if name != "cacheable"]
    elif isinstance(given, str):
        names = FREE_FORM.findall(given)
        if not names and _templated(given):
            _noted(scan.unread, f"{road} as a template", where)
    else:
        names = []
        _noted(scan.unread, f"{road} as no mapping", where)
    for name in names:
        if not isinstance(name, str):
            _noted(scan.unread, f"{road} with a name the scan cannot read", where)
        elif _templated(name):
            _noted(scan.patterns, _pattern(name), where)
        elif isinstance(given, str) and not NAME.fullmatch(name):
            _noted(scan.unread, f"{road} with a name the scan cannot read", where)
        else:
            _noted(scan.names, name, where)


def _defaults(given: object, where: object, scan: _Scan) -> None:
    """A module_defaults, which ansible-core merges into the arguments of every action under it: a set_fact's entry read
    as the names it gives every set_fact there, each action group's noted, and one that is not a mapping unread."""
    for each in given if isinstance(given, list) else [given]:
        if not isinstance(each, dict):
            _noted(scan.unread, "a module_defaults as a template", where)
            continue
        for action, arguments in each.items():
            if _is(action, "set_fact"):
                _facts(arguments, where, scan, "a set_fact's module_defaults")
            elif isinstance(action, str) and action.startswith("group/"):
                _noted(scan.groups, action, where)


def _split(text: str) -> tuple[str, str]:
    """An action's text as ansible-core's ModuleArgsParser splits it: the module, then its free-form arguments, at the
    first whitespace of any kind, a line's end among them."""
    words = text.split(None, 1)
    return (words[0] if words else "", words[1] if len(words) > 1 else "")


def _actions(node: dict) -> list[tuple[object, object]]:
    """Each action a mapping names, with its arguments: every key, as a task names its module by one; and action: and
    local_action:, read as ansible-core reads them - text split into its module and free-form arguments, and a
    mapping's module split the same way, beside the rest of the mapping and its own args:."""
    found = [(key, value) for key, value in node.items() if key not in ("action", "local_action")]
    for key in ("action", "local_action"):
        given = node.get(key)
        if isinstance(given, str):
            found.append(_split(given))
        elif isinstance(given, dict):
            module = given.get("module")
            module, rest = _split(module) if isinstance(module, str) else (module, "")
            found += [(module, rest), (module, {k: v for k, v in given.items() if k not in ("module", "args")})]
            if "args" in given:
                found.append((module, given["args"]))
    return found


def _follows(targets: list[object], bases: list[Path], read: frozenset[Path]) -> bool:
    """Whether the walk reads the file an include names, by any of targets, wherever Ansible looks for it (bases):
    never a template, and every one of each base/target that is there, at least one, a file the walk reads - so not one
    outside a role's tasks/, handlers/ or vars/ or the plays, a hidden file or one of another suffix."""
    if not all(isinstance(target, str) and target.strip() and not _templated(target) for target in targets):
        return False
    there = {Path(os.path.normpath(base / str(target).strip())) for base in bases for target in targets}
    there = {path for path in there if path.exists()}
    return bool(there) and there <= read


def _bases(at: _At) -> list[Path]:
    """Where Ansible may look for a file an include in at names: at's folder and each above it to the root, the tasks/
    folder of each, and the plays' folder, where a role's include falls back to the playbook's."""
    folders = [at.path.parent, *(p for p in at.path.parent.parents if p == at.root or at.root in p.parents)]
    return [*folders, *(folder / "tasks" for folder in folders), at.root / "playbooks"]


def _role(name: object, given: dict, at: _At, scan: _Scan) -> None:
    """A role an include_role, import_role or a play's role entry runs: one under the tree's roles/, which the walk
    reads whole, and each file its *_from names one the walk reads; any other is a road it does not follow."""
    folder = at.root / "roles" / name if isinstance(name, str) else None
    if folder is None or _templated(name) or "/" in name or name.startswith(".") or not folder.is_dir():
        _noted(scan.unread, "a role the scan does not read", at)
        return
    for key, (kind, as_tasks) in FROM.items():
        target = given.get(key)
        # Ansible tries the name as given, then with each suffix.
        targets = [f"{target}{suffix}" for suffix in LOADED] if isinstance(target, str) else [target]
        if key in given and not _follows(targets, [folder / kind], at.tasks if as_tasks else at.variables):
            _noted(scan.unread, "an include the scan does not follow", at)


@functools.cache
def _keywords() -> frozenset[str]:
    """What a play's role entry gives that is no parameter of the role: ansible-core's own attributes of one, as its
    RoleDefinition splits an entry, in the suite's own ansible-core."""
    from ansible.playbook.role.include import RoleInclude

    return frozenset(RoleInclude.fattributes)


def _play(node: dict, at: _At, scan: _Scan) -> None:
    """What a play sets beside its tasks: each name its vars_prompt asks, each of its vars_files' names (a file the
    scan cannot read is a road it does not follow), and each role entry's role and parameters."""
    for each in node.get("vars_prompt") or []:
        name = each.get("name") if isinstance(each, dict) else None
        if _templated(name):
            _noted(scan.unread, "a vars_prompt with a templated name", at)
        else:
            _noted(scan.names, name, at)
    listed = node.get("vars_files") or []
    for each in listed if isinstance(listed, list) else [listed]:
        path = at.path.parent / each if isinstance(each, str) and not _templated(each) else None
        document = yaml.safe_load(path.read_text()) if path is not None and path.is_file() else None
        if not isinstance(document, dict):
            _noted(scan.unread, "a vars_files the scan does not read", at)
            continue
        for name in document:
            _noted(scan.names, name, path.relative_to(at.root))
    for entry in node.get("roles") or []:
        if isinstance(entry, dict):
            _role(entry.get("role", entry.get("name")), {}, at, scan)
            for name in entry:
                if name not in _keywords():
                    _noted(scan.names, name, at)
        else:
            _role(entry, {}, at, scan)


def _task(node: dict, at: _At, scan: _Scan) -> None:
    """What one mapping of a role's file or a play's sets, read as a task or a play: its set_fact by any name, through
    action: or local_action: too, and its args: when its action is a set_fact; its register; its vars:; an include_vars,
    whose names the scan cannot read; an include of a file or a role, which the scan follows only where it reads it;
    and, of a play, its vars_prompt, its vars_files and its role entries."""
    facts = False
    given = node.get("args") if isinstance(node.get("args"), dict) else {}
    for action, arguments in _actions(node):
        options = {**given, **arguments} if isinstance(arguments, dict) else given
        if _is(action, "set_fact"):
            facts = True
            _facts(arguments, at, scan, "a set_fact")
        elif _is(action, "include_vars"):
            _noted(scan.unread, "an include_vars", at)
        elif _is(action, *INCLUDES):
            target = arguments if isinstance(arguments, str) else options.get("file")
            if not _follows([target], _bases(at), at.tasks):
                _noted(scan.unread, "an include the scan does not follow", at)
        elif _is(action, *ROLE_INCLUDES):
            if isinstance(arguments, str):
                _noted(scan.unread, "a role the scan does not read", at)
            else:
                _role(options.get("name"), options, at, scan)
    if facts and "args" in node:
        _facts(node["args"], at, scan, "a set_fact's args:")
    if isinstance(node.get("register"), str):
        _noted(scan.names, node["register"], at)
    if isinstance(node.get("vars"), dict):
        for name in node["vars"]:
            _noted(scan.names, name, at)
    if "hosts" in node:
        _play(node, at, scan)


def _walked(node: object, at: _At, scan: _Scan) -> None:
    """Every road a role's file or a play's sets a name by, at any depth of its YAML, a module_defaults among them, on
    the play, a role entry, a block or a task, which reaches every set_fact under it."""
    if isinstance(node, list):
        for item in node:
            _walked(item, at, scan)
        return
    if not isinstance(node, dict):
        return
    if "module_defaults" in node:
        _defaults(node["module_defaults"], at, scan)
    _task(node, at, scan)
    for key, value in node.items():
        if key != "module_defaults":
            _walked(value, at, scan)


def _loaded(folder: Path, suffixes: tuple[str, ...] = LOADED) -> list[Path]:
    """Every file under folder Ansible would load as tasks, handlers or variables (or of suffixes), at any depth."""
    if not folder.is_dir():
        return []
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix in suffixes and not path.name.startswith(".")
    )


def _set_in_the_tree(root: Path = REPO) -> _Scan:
    """Every name the project's defaults.yml gives a value, and every name a role or a play sets for the play by each
    road the scan reads - a set_fact by any of its names, given its names directly (a templated one as a pattern of the
    names it can make), through action: or local_action: (text or a mapping, split as ansible-core splits it), args: or
    a module_defaults, a register, a vars: at any depth of a role's task files and handlers or of a play's file, a
    play's vars_prompt, vars_files and role entries' parameters, a file of a role's vars/ folder at any depth - with
    the files that set it: what outranks an option's own default where today's product runs the role, so a value the
    run or the role works out. Every folder under roles/ is read, whether or not it has an argument_specs.yml; a road
    whose names the scan cannot read is noted as unread, and so is an include of a file or a role it does not read and a
    role's dependencies."""
    scan = _Scan()
    defaults = root / "inventory" / "group_vars" / "all" / "defaults.yml"
    if defaults.is_file():
        for name in yaml.safe_load(defaults.read_text()) or {}:
            _noted(scan.names, name, defaults.relative_to(root))
    roles = sorted(path for path in (root / "roles").glob("*") if path.is_dir())
    tasks = [path for folder in roles for kind in ("tasks", "handlers") for path in _loaded(folder / kind)]
    variables = [path for folder in roles for path in _loaded(folder / "vars")]
    plays = sorted({*root.glob("*.yml"), *_loaded(root / "playbooks", (".yml", ".yaml"))})
    read = (frozenset([*tasks, *plays]), frozenset(variables))
    for path in variables:
        for name in yaml.safe_load(path.read_text()) or {}:
            _noted(scan.names, name, path.relative_to(root))
    for folder in roles:
        for path in _loaded(folder / "meta"):
            document = yaml.safe_load(path.read_text())
            if isinstance(document, dict) and document.get("dependencies"):
                _noted(scan.unread, "a role's dependencies", path.relative_to(root))
    for path in [*tasks, *plays]:
        _walked(yaml.safe_load(path.read_text()), _At(root, path, *read), scan)
    return scan


def _set_here(scan: _Scan) -> dict[str, list[str]]:
    """Each option passed through that the scan found set, by its name or by a pattern it matches, with its files."""
    found = {}
    for name in _passed():
        files = set(scan.names.get(name, ()))
        files |= {each for pattern, where in scan.patterns.items() if re.fullmatch(pattern, name) for each in where}
        if files:
            found[name] = sorted(files)
    return found


def _group(group: str) -> list[object]:
    """The actions of a module_defaults' group/<namespace>.<collection>.<group>, from its collection's meta/runtime.yml
    in the collections' path every sanctioned run exports (tools/env-run)."""
    found = os.environ.get("ANSIBLE_COLLECTIONS_PATH")
    if not found:
        pytest.fail("ANSIBLE_COLLECTIONS_PATH is not set: run the suite through tools/env-run, as just test does")
    namespace, collection, name = group.removeprefix("group/").split(".")
    for folder in found.split(os.pathsep):
        runtime = Path(folder) / "ansible_collections" / namespace / collection / "meta" / "runtime.yml"
        if runtime.is_file():
            return list((yaml.safe_load(runtime.read_text()).get("action_groups") or {})[name])
    pytest.fail(f"no folder of ANSIBLE_COLLECTIONS_PATH holds {namespace}.{collection}")


def test_no_option_passed_through_is_one_defaults_yml_a_role_or_a_play_sets() -> None:
    # A value the project's defaults, a role's own task or a play sets is the run's or the role's, never a person's
    # setting: a person's value for it, handed to the play as an extra variable, would outrank the role's own set_fact
    # or the play's own vars for the whole run. Each such option is a row's, refused, or left out by name with why.
    found = _set_in_the_tree()
    assert _set_here(found) == {}, f"passed through, yet set by the project, a role or a play: {_set_here(found)}"
    # No road whose names the scan cannot read, no include it does not follow, no role's dependencies, and a
    # module_defaults gives arguments to the Proxmox modules' group alone, whose actions are each a module of its own,
    # no set_fact among them and no other group's.
    assert found.unread == {}, found.unread
    assert set(found.groups) == {"group/community.proxmox.proxmox"}, found.groups
    actions = _group("group/community.proxmox.proxmox")
    assert actions and all(type(action) is str and not _is(action, "set_fact") for action in actions), actions
    # The scan reads what it is meant to: a line of defaults.yml, a mapping there, a role's set_fact, a task's vars, a
    # register, a role's vars/main.yml, the system play's own vars and register, a play's vars_files and vars_prompt.
    assert "inventory/group_vars/all/defaults.yml" in found.names["proxmox_vm_api_host"]
    assert "inventory/group_vars/all/defaults.yml" in found.names["tailscale_off_setting"]
    assert "roles/proxmox_template/tasks/golden_build.yml" in found.names["proxmox_template_build_vm"]
    assert "roles/claude_code/tasks/remote_control.yml" in found.names["claude_code_unit_retired"]
    assert "roles/common/tasks/sudo.yml" in found.names["common_user_password_salt"]
    assert "roles/common/vars/main.yml" in found.names["common_ssh_path"]
    assert "playbooks/system.yml" in found.names["system_connection_variables"]
    assert "playbooks/system.yml" in found.names["configure_base"]
    assert "playbooks/destroy.yml" in found.names["confirm_destroy"]
    sign_in = yaml.safe_load((REPO / "playbooks" / "vars" / "sign_in.yml").read_text())
    assert any("playbooks/vars/sign_in.yml" in found.names.get(name, ()) for name in sign_in)
    # And a set_fact's templated name as the names it can make: today's sign-in plays name a fact for each sign-in.
    assert "playbooks/tasks/sign_in_failed.yml" in found.patterns["claude_vm_.*_login_failure"]


# Each road a role or a play sets an option passed through by, planted alone in a tree of its own: where (under the
# tree's root) the name is set and its YAML, with any file beside it (BESIDE). Review 53's low 3 (a register,
# ansible.legacy.set_fact, a vars/main/ folder), review 61's 1 (args:, module_defaults on the play, a role entry, a
# block and a task) and review 65's 1 (action: text split at any whitespace, a mapping's module and its own args:, a
# role with no argument_specs.yml, a play's tasks, vars:, vars_files, vars_prompt and a role entry's vars: and
# parameters) and low 4 (a free-form name after the first), with the other spellings of the same action.
SET = "common_timezone"
TASKS = "roles/common/tasks/main.yml"
PLAY = "playbooks/system.yml"
PLANTED = [
    ("a register", TASKS, [{"ansible.builtin.command": "date", "register": SET}]),
    ("ansible.legacy.set_fact", TASKS, [{"ansible.legacy.set_fact": {SET: "UTC"}}]),
    ("a free-form set_fact", TASKS, [{"set_fact": f"{SET}=UTC"}]),
    ("a free-form set_fact's second name", TASKS, [{"set_fact": f"common_other=1 {SET}=UTC"}]),
    ("a set_fact's templated name", TASKS, [{"ansible.builtin.set_fact": {"common_{{ 'time' }}zone": "UTC"}}]),
    ("a set_fact through action:", TASKS, [{"action": f"ansible.builtin.set_fact {SET}=UTC"}]),
    ("a set_fact through action: on two lines", TASKS, [{"action": f"ansible.builtin.set_fact\n{SET}=UTC"}]),
    ("a set_fact through local_action: after a tab", TASKS, [{"local_action": f"set_fact\t{SET}=UTC"}]),
    ("a set_fact through local_action:", TASKS, [{"local_action": {"module": "set_fact", SET: "UTC"}}]),
    ("a mapping's module on two lines", TASKS, [{"action": {"module": f"set_fact\n{SET}=UTC"}}]),
    ("a mapping's own args:", TASKS, [{"local_action": {"module": "set_fact", "args": {SET: "UTC"}}}]),
    (
        "a set_fact in a block's rescue",
        TASKS,
        [{"block": [{"ansible.builtin.ping": None}], "rescue": [{"ansible.builtin.set_fact": {SET: "UTC"}}]}],
    ),
    ("a .yaml task file", "roles/common/tasks/more.yaml", [{"ansible.builtin.set_fact": {SET: "UTC"}}]),
    ("a handler", "roles/common/handlers/main.yml", [{"name": "x", "ansible.builtin.set_fact": {SET: "UTC"}}]),
    ("a task's vars:", TASKS, [{"ansible.builtin.ping": None, "vars": {SET: "UTC"}}]),
    ("a vars/main/ folder", "roles/common/vars/main/extra.yml", {SET: "UTC"}),
    ("args:", TASKS, [{"ansible.builtin.set_fact": None, "args": {SET: "UTC"}}]),
    (
        "module_defaults on a task",
        TASKS,
        [{"ansible.builtin.set_fact": {"common_x": 1}, "module_defaults": {"ansible.builtin.set_fact": {SET: "UTC"}}}],
    ),
    (
        "module_defaults on a block",
        TASKS,
        [{"block": [{"set_fact": {"common_x": 1}}], "module_defaults": {"set_fact": {SET: "UTC"}}}],
    ),
    (
        "module_defaults on the play",
        PLAY,
        [{"hosts": "all", "module_defaults": [{"ansible.legacy.set_fact": {SET: "UTC"}}], "roles": ["common"]}],
    ),
    (
        "module_defaults on a role entry",
        PLAY,
        [{"hosts": "all", "roles": [{"role": "common", "module_defaults": {"set_fact": {SET: "UTC"}}}]}],
    ),
    ("a role with no argument_specs.yml", "roles/other/tasks/main.yml", [{"ansible.builtin.set_fact": {SET: "UTC"}}]),
    (
        "a play's pre_tasks",
        PLAY,
        [{"hosts": "all", "pre_tasks": [{"ansible.builtin.set_fact": {SET: "UTC"}}], "roles": ["common"]}],
    ),
    ("a play's vars:", PLAY, [{"hosts": "all", "vars": {SET: "UTC"}, "roles": ["common"]}]),
    ("a play's vars_files", "playbooks/vars/more.yml", {SET: "UTC"}),
    ("a play's vars_prompt", PLAY, [{"hosts": "all", "vars_prompt": [{"name": SET, "prompt": "?"}], "roles": []}]),
    ("a role entry's vars:", PLAY, [{"hosts": "all", "roles": [{"role": "common", "vars": {SET: "UTC"}}]}]),
    ("a role entry's parameter", PLAY, [{"hosts": "all", "roles": [{"role": "common", SET: "UTC"}]}]),
    ("a role entry by name", PLAY, [{"hosts": "all", "roles": [{"name": "common", "tags": ["x"], SET: "UTC"}]}]),
]
# The files planted beside a road of PLANTED: the include of a role with no argument_specs.yml, which the walk reads
# whole whether or not the include is followed, and the play that names a vars_files.
BESIDE = {
    "a role with no argument_specs.yml": {TASKS: [{"ansible.builtin.include_role": {"name": "other"}}]},
    "a play's vars_files": {PLAY: [{"hosts": "all", "vars_files": ["vars/more.yml"], "roles": ["common"]}]},
}
# And each road whose names the scan cannot read, or whose file or role it does not read, planted alone: where, its
# YAML, any file beside it, and what the scan says of it.
INCLUDED = [{"ansible.builtin.set_fact": {SET: "UTC"}}]
UNREAD = [
    (
        "an include_vars",
        TASKS,
        [{"ansible.builtin.include_vars": {"file": "/elsewhere/settings.yml"}}],
        {},
        "an include_vars",
    ),
    (
        "an include_vars through action:",
        TASKS,
        [{"action": "include_vars /elsewhere/settings.yml"}],
        {},
        "an include_vars",
    ),
    (
        "args: as one template string",
        TASKS,
        [{"ansible.builtin.set_fact": None, "args": "{{ common_given }}"}],
        {},
        "a set_fact's args: as a template",
    ),
    (
        "a set_fact's module_defaults as a template",
        PLAY,
        [{"hosts": "all", "module_defaults": {"set_fact": "{{ given }}"}, "roles": ["common"]}],
        {},
        "a set_fact's module_defaults as a template",
    ),
    (
        "a module_defaults as a template",
        TASKS,
        [{"block": [{"set_fact": {"common_x": 1}}], "module_defaults": "{{ given }}"}],
        {},
        "a module_defaults as a template",
    ),
    (
        "a free-form name of no variable's shape",
        TASKS,
        [{"set_fact": f"'{SET}'=UTC"}],
        {},
        "a set_fact with a name the scan cannot read",
    ),
    (
        "an include of a file outside tasks/",
        TASKS,
        [{"ansible.builtin.include_tasks": "../files/more.yml"}],
        {"roles/common/files/more.yml": INCLUDED},
        "an include the scan does not follow",
    ),
    (
        "an include of another suffix",
        TASKS,
        [{"include_tasks": {"file": "more.txt"}}],
        {"roles/common/tasks/more.txt": INCLUDED},
        "an include the scan does not follow",
    ),
    (
        "an import of a hidden file",
        TASKS,
        [{"ansible.builtin.import_tasks": ".more.yml"}],
        {"roles/common/tasks/.more.yml": INCLUDED},
        "an include the scan does not follow",
    ),
    (
        "an include through action:",
        TASKS,
        [{"action": "include_tasks\n../files/more.yml"}],
        {"roles/common/files/more.yml": INCLUDED},
        "an include the scan does not follow",
    ),
    (
        "an include of a template",
        TASKS,
        [{"ansible.builtin.include_tasks": "{{ common_which }}.yml"}],
        {},
        "an include the scan does not follow",
    ),
    (
        "an include of a file that is not there",
        PLAY,
        [{"hosts": "all", "tasks": [{"ansible.builtin.include_tasks": "tasks/absent.yml"}]}],
        {},
        "an include the scan does not follow",
    ),
    (
        "an include_role's tasks_from outside tasks/",
        TASKS,
        [{"ansible.builtin.include_role": {"name": "common", "tasks_from": "../files/more"}}],
        {"roles/common/files/more.yml": INCLUDED},
        "an include the scan does not follow",
    ),
    (
        "an import_role's vars_from outside vars/",
        TASKS,
        [{"ansible.builtin.import_role": {"name": "common", "vars_from": "../files/more"}}],
        {"roles/common/files/more.yml": {SET: "UTC"}},
        "an include the scan does not follow",
    ),
    (
        "a role outside roles/",
        TASKS,
        [{"ansible.builtin.include_role": {"name": "some.collection.role"}}],
        {},
        "a role the scan does not read",
    ),
    (
        "a play's role outside roles/",
        PLAY,
        [{"hosts": "all", "roles": ["elsewhere"]}],
        {},
        "a role the scan does not read",
    ),
    (
        "a role's dependencies",
        "roles/common/meta/main.yml",
        {"dependencies": [{"role": "common", SET: "UTC"}]},
        {},
        "a role's dependencies",
    ),
    (
        "a vars_files of a template",
        PLAY,
        [{"hosts": "all", "vars_files": ["{{ given }}.yml"], "roles": ["common"]}],
        {},
        "a vars_files the scan does not read",
    ),
]


def _planted(root: Path, where: str, document: object) -> Path:
    path = root / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document))
    return path


def _tree(root: Path) -> Path:
    """A tree of its own with one role, common, whose folder is there and holds nothing yet."""
    (root / "roles" / "common" / "tasks").mkdir(parents=True)
    return root


def test_a_tree_with_nothing_planted_sets_nothing(tmp_path) -> None:
    assert _set_in_the_tree(_tree(tmp_path)) == _Scan()


@pytest.mark.parametrize(("case", "where", "document"), PLANTED, ids=[case for case, _, _ in PLANTED])
def test_each_road_a_role_or_a_play_sets_an_option_by_fails_the_scan_alone(tmp_path, case, where, document) -> None:
    assert SET in _passed()
    _planted(_tree(tmp_path), where, document)
    for path, beside in BESIDE.get(case, {}).items():
        _planted(tmp_path, path, beside)
    scan = _set_in_the_tree(tmp_path)
    assert _set_here(scan) == {SET: [where]}
    assert scan.unread == {} and scan.groups == {}


@pytest.mark.parametrize(("case", "where", "document", "beside", "said"), UNREAD, ids=[case for case, *_ in UNREAD])
def test_each_road_the_scan_cannot_read_or_does_not_follow_fails_it_alone(
    tmp_path, case, where, document, beside, said
) -> None:
    _planted(_tree(tmp_path), where, document)
    for path, planted in beside.items():
        _planted(tmp_path, path, planted)
    scan = _set_in_the_tree(tmp_path)
    assert scan.unread == {said: {where}}
    assert _set_here(scan) == {}


def test_a_name_that_is_a_template_alone_is_every_option_and_one_left_open_too(tmp_path) -> None:
    for name in ("{{ common_which }}", "common_{{ which"):
        _planted(_tree(tmp_path / name), TASKS, [{"ansible.builtin.set_fact": {name: "UTC"}}])
        scan = _set_in_the_tree(tmp_path / name)
        assert scan.patterns == {".*": {TASKS}} and set(_set_here(scan)) == set(_passed())


def test_an_include_the_walk_reads_is_followed_and_what_it_sets_is_found(tmp_path) -> None:
    # The roads today's roles and plays take, each to a file the walk reads: a task file beside, a role's tasks_from
    # and vars_from by name with no suffix, a play's task file and another play.
    _planted(_tree(tmp_path), "roles/common/tasks/more.yml", INCLUDED)
    _planted(tmp_path, "roles/common/tasks/release.yml", [{"ansible.builtin.ping": None}])
    _planted(tmp_path, "roles/common/vars/other.yml", {"common_other": 1})
    _planted(tmp_path, "playbooks/tasks/shared.yml", [{"ansible.builtin.ping": None}])
    _planted(
        tmp_path,
        TASKS,
        [
            {"ansible.builtin.include_tasks": "more.yml"},
            {"ansible.builtin.include_role": {"name": "common", "tasks_from": "release", "vars_from": "other"}},
        ],
    )
    _planted(tmp_path, PLAY, [{"hosts": "all", "tasks": [{"ansible.builtin.import_tasks": "tasks/shared.yml"}]}])
    _planted(tmp_path, "deploy.yml", [{"ansible.builtin.import_playbook": PLAY}])
    scan = _set_in_the_tree(tmp_path)
    assert scan.unread == {}
    assert _set_here(scan) == {SET: ["roles/common/tasks/more.yml"]}


def test_a_role_entry_s_keywords_are_ansible_core_s_own_and_no_parameter(tmp_path) -> None:
    # What a role entry gives beside its parameters is what ansible-core's RoleDefinition splits off as its own.
    assert {"role", "name", "vars", "tags", "when", "become", "module_defaults", "delegate_to"} <= _keywords()
    assert SET not in _keywords()
    entry = {"role": "common", "tags": ["x"], "when": "true", "become": True, "vars": {}, "common_other": 1}
    _planted(_tree(tmp_path), PLAY, [{"hosts": "all", "roles": [entry]}])
    assert set(_set_in_the_tree(tmp_path).names) == {"common_other"}


def test_a_module_defaults_group_is_noted_and_another_module_s_entry_sets_nothing(tmp_path) -> None:
    group = "group/community.proxmox.proxmox"
    play = [{"hosts": "all", "module_defaults": {group: "{{ given }}", "ansible.builtin.copy": {SET: 1}}}]
    _planted(_tree(tmp_path), PLAY, play)
    scan = _set_in_the_tree(tmp_path)
    assert scan == _Scan(groups={group: {PLAY}})


def test_the_module_imports_the_standard_library_the_table_and_today_s_wiring_alone(home) -> None:
    added = imports_of(module=MODULE, home=home) - imports_of(module="claude_vm.fleet", home=home)
    assert MODULE in added, sorted(added)
    assert sorted({name.split(".")[0] for name in added} - set(sys.stdlib_module_names) - {"claude_vm"}) == []
    assert sorted(name for name in added if name.startswith("claude_vm.")) == [
        "claude_vm.fleet.passthrough",
        "claude_vm.fleet.registry",
        "claude_vm.wiring",
    ]


# ---------------------------------------------------------------- a value held to its spec ----


@pytest.mark.parametrize(
    ("name", "text", "value"),
    [
        ("common_apt_upgrade", "yes", True),
        ("common_apt_upgrade", "Off", False),
        ("common_apt_cache_valid_time", "3600", 3600),
        ("common_packages", "jq, htop", ["jq", "htop"]),
        ("common_packages", "[jq, 'a,b']", ["jq", "a,b"]),
        ("common_packages", "", []),
        ("dev_tools_uv_arch_map", "x86_64=x86_64-unknown-linux-gnu", {"x86_64": "x86_64-unknown-linux-gnu"}),
        ("dev_tools_uv_arch_map", "{aarch64: aarch64-unknown-linux-gnu}", {"aarch64": "aarch64-unknown-linux-gnu"}),
        ("dev_tools_deb_arch", "arm64", "arm64"),
        ("common_timezone", "Europe/Paris", "Europe/Paris"),
        # A path on the machine, kept as typed: its ~ is the machine's account's, never this computer's home.
        ("tailscale_marker_dir", "~/marks", "~/marks"),
        ("tailscale_marker_dir", "marks", "marks"),
    ],
)
def test_typed_text_is_the_option_s_spec_s_type(name: str, text: str, value: object) -> None:
    assert passthrough.typed(passthrough.option(name), text) == value


@pytest.mark.parametrize(
    ("name", "text", "said"),
    [
        ("dev_tools_deb_arch", "sparc", "dev_tools_deb_arch takes one of amd64, arm64"),
        ("common_apt_cache_valid_time", "soon", "common_apt_cache_valid_time takes a whole number"),
        ("common_apt_upgrade", "maybe", "common_apt_upgrade takes true or false"),
        ("common_packages", "[jq, 1]", "common_packages takes a list of strings"),
        ("common_packages", "[jq, [a]]", "common_packages takes a list of strings"),
        ("dev_tools_uv_arch_map", "[a, b]", "dev_tools_uv_arch_map takes a mapping"),
        ("common_timezone", "{{ lookup('pipe', 'id') }}", "common_timezone takes a value, never an expression"),
        ("common_packages", '["\\x7b{ x }}"]', "common_packages takes a value, never an expression"),
        ("common_timezone", "Europe\x1b]0;x\x07", "common_timezone takes one line of text"),
    ],
)
def test_typed_text_its_spec_does_not_take_is_refused_with_no_value_said(name: str, text: str, said: str) -> None:
    with pytest.raises(Refused) as refused:
        passthrough.typed(passthrough.option(name), text)
    assert str(refused.value).startswith(said), str(refused.value)
    assert text not in str(refused.value)


# The vendored key a cloud image's list of digests is signed by, and a CLAUDE.md of two lines: text of several lines.
KEY_FILE = REPO / "roles" / "proxmox_template" / "files" / "ubuntu-cloudimage.asc"
INSTRUCTIONS = "# Every project\nWrite British English, and say what a change is for.\n"


def _block(name: str, text: str) -> str:
    """A setting whose value is text, written as a person writes one in the fleet's settings.yml: YAML's literal block,
    its lines indented two spaces, an empty line left empty."""
    return f"{name}: |\n" + "".join(f"  {line}\n" if line else "\n" for line in text.splitlines())


def test_a_text_of_several_lines_is_taken_from_the_fleet_s_file_and_read_back_whole(home) -> None:
    key = KEY_FILE.read_text()
    assert key.count("\n") > 10 and key.endswith("-----\n"), "the vendored key is several lines, ending with one"
    where = _where()
    text = "vm_cores: 4\n" + _block("proxmox_template_image_key", key)
    path = _put(_fleet_file(where), text + _block("claude_code_global_instructions", INSTRUCTIONS))
    read = overrides.read(where, None).fleet.settings
    assert read["proxmox_template_image_key"] == key
    assert read["claude_code_global_instructions"] == INSTRUCTIONS
    found = overrides.resolve(where, "lab")
    assert found["proxmox_template_image_key"] == Value("proxmox_template_image_key", key, "fleet")
    assert found["claude_code_global_instructions"] == Value("claude_code_global_instructions", INSTRUCTIONS, "fleet")
    # Written by the one writer, and read back whole again; the file's other settings as they were.
    digest = overrides.read(where, None).fleet.digest
    again = "# Two\n\nlines, and one empty between them\n"
    overrides.set(where, "claude_code_global_instructions", again, None, digest)
    read = overrides.read(where, None).fleet.settings
    assert (read["claude_code_global_instructions"], read["proxmox_template_image_key"], read["vm_cores"]) == (
        again,
        key,
        4,
    )
    assert yaml.safe_load(path.read_text())["claude_code_global_instructions"] == again


@pytest.mark.parametrize(
    ("value", "line", "said"),
    [
        ('"one\\r\\ntwo\\n"', 1, "takes text whose one control character is the line's end"),
        ('"one\\ttwo\\n"', 1, "takes text whose one control character is the line's end"),
        ('"one\\x1b]0;x\\x07two\\n"', 1, "takes text whose one control character is the line's end"),
        ('"one\\u2028two"', 1, "takes text whose one control character is the line's end"),
        ("|\n  one\n  {{ lookup('pipe', 'id') }}\n", 3, "a setting takes a value, never an expression"),
        ("|\n  one\n  {# two #}\n", 3, "a setting takes a value, never an expression"),
    ],
)
def test_a_text_holds_no_control_character_but_the_line_s_end_and_no_expression(home, value, line, said) -> None:
    where = _where()
    path = _put(_fleet_file(where), f"claude_code_global_instructions: {value}\n")
    with pytest.raises(Refused) as refused:
        overrides.read(where, None)
    named = "" if said.startswith("a setting") else "claude_code_global_instructions "
    assert str(refused.value).startswith(f"{path} line {line}: {named}{said}"), str(refused.value)
    path.unlink()
    held = yaml.safe_load(f"v: {value}")["v"]
    with pytest.raises(Refused, match="nothing was written"):
        overrides.set(where, "claude_code_global_instructions", held, None, overrides.MISSING)
    assert not path.exists()


def test_a_text_is_never_typed_and_its_refusal_names_the_file_it_is_written_in(home) -> None:
    said = f"claude_code_global_instructions {overrides.TEXT_TYPED}"
    assert "settings.yml" in said and "name: |" in said
    option = passthrough.option("claude_code_global_instructions")
    for refusing in (
        lambda: overrides.run_values(["claude_code_global_instructions=one line"], secrets=()),
        lambda: passthrough.typed(option, "one line"),
        lambda: overrides.typed(passthrough.row(option.name), "one line"),
        lambda: overrides.resolve(_where(), None, {option.name: "one line"}),
    ):
        with pytest.raises(Refused) as refused:
            refusing()
        assert str(refused.value) == said
    with pytest.raises(Refused) as refused:
        overrides.run_values(["proxmox_template_image_key=x"], secrets=())
    assert str(refused.value) == f"proxmox_template_image_key {overrides.TEXT_TYPED}"


def test_an_option_refused_is_refused_typed_for_its_reason() -> None:
    for name in ("tailscale_enabled", "proxmox_vm_state", "common_hostname", "tailscale_auth_key"):
        option = passthrough.option(name)
        with pytest.raises(Refused) as refused:
            passthrough.typed(option, TYPED)
        assert str(refused.value) == f"{name} {option.refused}"


# ---------------------------------------------------------------- the overrides take its name ----


def test_an_option_is_read_from_the_fleet_s_file_and_given_for_one_run_above_it(home) -> None:
    where = _where()
    empty = overrides.resolve(where, None)
    assert not set(empty) & set(_passed()), "an option set nowhere has no value here"
    _put(_fleet_file(where), "common_timezone: Europe/Paris\ncommon_packages: [jq]\nvm_cores: 4\n")
    found = overrides.resolve(where, "lab")
    assert found["common_timezone"] == Value("common_timezone", "Europe/Paris", "fleet")
    assert found["common_packages"] == Value("common_packages", ["jq"], "fleet")
    assert found["vm_cores"] == Value("vm_cores", 4, "fleet")
    # The rows first, in the table's order, then the options set, in passthrough's.
    assert list(found)[: len(registry.rows())] == [each.name for each in registry.rows()]
    assert list(found)[len(registry.rows()) :] == ["common_timezone", "common_packages"]
    run = overrides.run_values(["common_timezone=UTC", "dev_tools_deb_arch=arm64"], secrets=())
    assert run == {"common_timezone": "UTC", "dev_tools_deb_arch": "arm64"}
    top = overrides.resolve(where, "lab", run)
    assert top["common_timezone"] == Value("common_timezone", "UTC", "run")
    assert top["dev_tools_deb_arch"] == Value("dev_tools_deb_arch", "arm64", "run")
    assert top["common_packages"] == Value("common_packages", ["jq"], "fleet")


def test_an_option_is_the_fleet_s_and_refused_in_any_other_place(home) -> None:
    where = _where()
    belongs = "common_timezone is not kept here: it is the fleet's: keep it at the top of the fleet's settings.yml"
    host = _put(paths.fleet_dir(where) / "hosts" / "lab" / "settings.yml", "common_timezone: UTC\n")
    with pytest.raises(Refused, match=re.escape(f"{host} line 1: {belongs}")):
        overrides.read(where, "lab")
    host.unlink()
    client = _put(paths.client_file(where), "common_timezone: UTC\n")
    with pytest.raises(Refused, match=re.escape(f"{client} line 1: {belongs}")):
        overrides.read(where, None)
    client.unlink()
    fleet = _put(_fleet_file(where), "machines:\n  lab/alpha:\n    common_timezone: UTC\n")
    with pytest.raises(Refused, match=re.escape(f"{fleet} line 3: {belongs}")):
        overrides.read(where, None)


def test_set_and_unset_write_an_option_at_the_top_of_the_fleet_s_file(home) -> None:
    where = _where()
    digest = overrides.set(where, "common_timezone", "UTC", None, overrides.MISSING)
    assert yaml.safe_load(_fleet_file(where).read_text()) == {"common_timezone": "UTC"}
    digest = overrides.set(where, "common_packages", ["jq"], "lab", digest)
    assert yaml.safe_load(_fleet_file(where).read_text()) == {"common_timezone": "UTC", "common_packages": ["jq"]}
    with pytest.raises(Refused, match="dev_tools_deb_arch takes one of amd64, arm64: nothing was written"):
        overrides.set(where, "dev_tools_deb_arch", "sparc", None, digest)
    overrides.unset(where, "common_timezone", None, digest)
    assert yaml.safe_load(_fleet_file(where).read_text()) == {"common_packages": ["jq"]}


@pytest.mark.parametrize(
    "name",
    [
        "tailscale_auth_key",
        "tailscale_oauth_client_id",
        "proxmox_vm_cores",
        "github_projects_username",
        "common_hostname",
        "proxmox_vm_state",
        "proxmox_vm_id",
        "proxmox_vm_validate_certs",
        "proxmox_vm_api_port",
        "tailscale_enabled",
        "tailscale_wanted",
        "proxmox_vm_allow_untagged_delete",
        "proxmox_vm_template_name",
        "proxmox_template_build_vm",
        "github_projects_host_flag",
        "github_projects_vm_repos",
    ],
)
def test_a_role_s_option_that_is_no_setting_is_refused_by_name_and_why_never_saying_the_value(home, name) -> None:
    why = passthrough.refusal(name)
    assert why is not None
    where = _where()
    path = _put(_fleet_file(where), f"vm_cores: 2\n{name}: {TYPED}\n")
    with pytest.raises(Refused) as refused:
        overrides.read(where, None)
    assert str(refused.value) == f"{path} line 2: {name} {why}"
    with pytest.raises(Refused) as refused:
        overrides.run_values([f"{name}={TYPED}"], secrets=())
    assert str(refused.value) == f"{name} {why}"
    path.unlink()
    with pytest.raises(Refused) as refused:
        overrides.resolve(where, None, {name: TYPED})
    assert str(refused.value) == f"{name} {why}"


def test_a_name_no_role_has_is_still_never_said_back_and_an_option_is_offered_beside_the_rows() -> None:
    with pytest.raises(Refused) as refused:
        overrides.run_values(["common_timezon=UTC"], secrets=())
    said = str(refused.value)
    assert said.startswith("that is no setting's name (did you mean ") and "common_timezone" in said, said
    assert "common_timezon " not in said and "common_timezon?" not in said
    for name in OLDER:
        with pytest.raises(Refused, match=re.escape("that is no setting's name")):
            overrides.run_values([f"{name}=true"], secrets=())


def test_an_option_s_value_in_a_file_is_held_to_its_spec_by_its_line(home) -> None:
    where = _where()
    for text, said in [
        ("dev_tools_deb_arch: sparc\n", "dev_tools_deb_arch takes one of amd64, arm64: the value there is not one"),
        ("common_packages: [jq, 1]\n", "common_packages takes a list of strings"),
        ("common_apt_upgrade: 'yes'\n", "common_apt_upgrade takes true or false"),
    ]:
        path = _put(_fleet_file(where), text)
        with pytest.raises(Refused) as refused:
            overrides.read(where, None)
        assert str(refused.value).startswith(f"{path} line 1: {said}"), str(refused.value)


def test_an_option_s_value_that_holds_a_secret_the_vault_holds_is_refused_for_one_run() -> None:
    secret = Secret("not-a-real-token-0000")
    with pytest.raises(Refused) as refused:
        overrides.run_values(["common_timezone=not-a-real-token-0000"], secrets=(secret,))
    assert "not-a-real-token-0000" not in str(refused.value) and "common_timezone" in str(refused.value)


# ---------------------------------------------------------------- against today's tools/settings ----


def _inventory(root: Path) -> Path:
    """An inventory made from the example files, as tests/unit/test_settings.py's _scenario makes its plain one, but
    with none of its attacks, which test what the tool prints and change nothing of what it lets a person set: the
    project's defaults and the controller's group, the hosts' example as hosts.yml, and the examples of local.yml and
    vault.yml as they are, every setting at its default and the vault holding its placeholders, in the clear."""
    inventory = root / "inventory"
    (inventory / "group_vars" / "all").mkdir(parents=True)
    for each in ("controller.yml", "group_vars/all/defaults.yml"):
        (inventory / each).write_text((REPO / "inventory" / each).read_text())
    (inventory / "hosts.yml").write_text((REPO / "inventory" / "hosts.yml.example").read_text())
    for each in ("local.yml", "vault.yml"):
        example = REPO / "inventory" / "group_vars" / "all" / f"{each}.example"
        (inventory / "group_vars" / "all" / each).write_text(example.read_text())
    return inventory


def _today(root: Path, *args: str) -> list[dict]:
    """Today's tools/settings' report, as JSON, of the inventory made from the examples: run as a child with a home of
    its own and the collections' path pinned (the recipes export it; no role loads without them), nothing on stdin."""
    home = root / "home"
    (home / ".ssh").mkdir(parents=True)
    env = {
        "PATH": os.environ["PATH"],
        "LANG": "en_US.UTF-8",
        "HOME": str(home),
        "ANSIBLE_VERBOSITY": "0",
        "ANSIBLE_DEBUG": "0",
        "ANSIBLE_COLLECTIONS_PATH": os.environ.get("ANSIBLE_COLLECTIONS_PATH")
        or str(Path.home() / ".ansible" / "collections"),
    }
    result = subprocess.run(
        [sys.executable, str(TOOL), "--inventory", str(_inventory(root)), "--json=true", *args],
        cwd=REPO,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout)["settings"]


def _refused_here(name: str) -> bool:
    """Whether the engine refuses a name for one run: what it takes in a file it takes with --set too."""
    try:
        overrides.run_values([f"{name}=x"], secrets=())
    except Refused as refused:
        return not str(refused).startswith(f"{name} takes")
    return False


@pytest.mark.parametrize("args", [("--", role) for role in ROLES] + [("--all=true",)], ids=[*ROLES, "all"])
def test_every_name_today_s_tool_lets_a_person_set_is_taken_and_every_name_it_refuses_is_no_option(
    tmp_path, args
) -> None:
    report = _today(tmp_path, *args)
    assert report, args
    rows, passed = _row_names(), _passed()
    writes: dict[str, str] = {}
    refused: dict[str, tuple[str, str]] = {}
    for each in report:
        change = each.get("change") or ""
        if change.startswith(TODAY_WRITES):
            writes[each["name"]] = change.split()[2]
        else:
            refused[each["name"]] = (change, each.get("why") or "")
    # Every name it writes is taken here, as a row or as an option passed through, but those the test names.
    left = sorted({written for written in writes.values()} - rows - set(passed) - set(NOT_TAKEN) - set(TAKEN_AS))
    assert left == [], f"settable today and taken by no row or option here: {left}"
    for name in {written for written in writes.values()} & {*NOT_TAKEN, *TAKEN_AS}:
        assert _refused_here(name), name
    for name, row in TAKEN_AS.items():
        assert writes.get(name, name) == name and row in rows
    # Every option it writes through another setting is refused here through that setting's row, or, where that
    # setting is one the table has no row for, left out by name; but a row of its own here, as the table made it
    # (claude_code_remote_control_dir, which today's tool writes as vm_projects_dir, its default here).
    for name, written in writes.items():
        if name == written or name in rows:
            continue
        if written in rows:
            assert passthrough.refusal(name) == THROUGH.format(row=written), name
        else:
            assert written in (*LEFT, OLDER_VPN) and name in LEFT_OUT, name
    # Every option of a role it writes by its own name, and that no row takes, is passed through, and no other.
    if args[0] == "--":
        role = args[1]
        own = {name for name, written in writes.items() if name == written and name not in rows}
        assert own - set(NOT_TAKEN) - set(TAKEN_AS) == {name for name, each in passed.items() if each.role == role}
    # Every name it refuses is no option here, and is refused for today's reason, but the rows the test names.
    for name, (change, why) in refused.items():
        assert name not in passed, name
        if name in rows:
            assert name in REFUSED_TODAY_ROWS, name
            row = registry.row(name)
            assert row.secret if REFUSED_TODAY_ROWS[name].endswith("secret") else row.scope == "machine"
            continue
        ours = passthrough.refusal(name)
        if TODAY_PER_VM in why:
            assert ours == PER_MACHINE, name
        elif TODAY_BY_A_RUN in why:
            assert ours in (SET_BY_A_RUN, ANY_VALUE), name
        elif change == TODAY_VAULT and name in FROM_THE_VAULT_TO_A_ROW:
            assert ours == THROUGH.format(row=FROM_THE_VAULT_TO_A_ROW[name]), name
        elif change == TODAY_VAULT and ours is not None:
            assert ours == SECRET, name
        else:
            # A name of today's that is no role's option: the host's address and the vault's own names, unknown here.
            assert ours is None and _refused_here(name), name
    # The refusals by kind are today's, role by role.
    if args[0] == "--":
        role = args[1]
        mine = {name for name in wiring.specs()[role]}
        for kind, today in [(PER_MACHINE, TODAY_PER_VM), (SET_BY_A_RUN, TODAY_BY_A_RUN)]:
            assert {name for name in mine if passthrough.refusal(name) == kind} == {
                name for name, (_change, why) in refused.items() if today in why and name not in rows
            } - {name for name in mine if passthrough.refusal(name) == ANY_VALUE}, kind
        assert {name for name in mine if passthrough.refusal(name) == SECRET} == {
            name for name, (change, _why) in refused.items() if change == TODAY_VAULT
        } - rows - set(FROM_THE_VAULT_TO_A_ROW)


def _table_read(monkeypatch: pytest.MonkeyPatch, *, pure: bool) -> tuple[tuple, tuple, set]:
    """The table built anew, and today's wiring read, by the pure-Python loader or by libyaml's: each in a plain form,
    with every loader the reads used."""
    used: set = set()
    real = yaml.load

    def load(stream, Loader):
        used.add(Loader)
        return real(stream, Loader=Loader)

    with monkeypatch.context() as patch:
        if pure:
            patch.delattr(yaml, "CSafeLoader")
        patch.setattr(yaml, "load", load)
        table = passthrough._table.__wrapped__()
        wired = (wiring.specs(), wiring.wiring())
    plain = (table.options, dict(table.by_name), dict(table.refusals), dict(table.rows))
    return plain, wired, used


def test_the_table_libyaml_reads_is_the_table_the_pure_python_loader_reads(monkeypatch) -> None:
    # Where PyYAML has libyaml, the roles' specs and defaults are read by its loader, several times faster, and the
    # table that comes out is the one PyYAML's own loader makes, to the last option, refusal and row.
    if not yaml.__with_libyaml__:
        pytest.skip("this PyYAML was built without libyaml, so its own loader is the one the table is read with")
    fast, fast_wired, fast_used = _table_read(monkeypatch, pure=False)
    slow, slow_wired, slow_used = _table_read(monkeypatch, pure=True)
    assert fast_used == {yaml.CSafeLoader} and slow_used == {yaml.SafeLoader}
    assert fast[0] and fast == slow
    assert fast_wired == slow_wired
    table = passthrough._table()
    assert fast == (table.options, dict(table.by_name), dict(table.refusals), dict(table.rows))


def test_the_table_reads_and_parses_each_of_the_roles_files_once(monkeypatch) -> None:
    # Every role's spec and defaults, and the project's defaults: each read from disk once and parsed once, for
    # wiring.specs and the entry points alike.
    files = [wiring.ROOT / "roles" / role / "meta" / "argument_specs.yml" for role in wiring.roles()]
    files += sorted((wiring.ROOT / "roles").glob("*/defaults/main.yml")) + [wiring.GROUP_VARS / "defaults.yml"]
    texts = {path: path.read_text() for path in files}
    read: Counter = Counter()
    parsed: Counter = Counter()
    real_read, real_load = Path.read_text, wiring._load

    def read_text(self, *args, **kwargs):
        read[self] += 1
        return real_read(self, *args, **kwargs)

    def load(text):
        parsed[text] += 1
        return real_load(text)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(wiring, "_load", load)
    passthrough._table.__wrapped__()
    alike = Counter(texts.values())
    assert len(files) > len(wiring.roles()) + 1
    assert {path.relative_to(wiring.ROOT): read[path] for path in files if read[path] != 1} == {}
    assert {
        path.relative_to(wiring.ROOT): parsed[text] for path, text in texts.items() if parsed[text] != alike[text]
    } == {}
