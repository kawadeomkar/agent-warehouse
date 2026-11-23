"""The role options passed through (lib/claude_vm/fleet/passthrough.py; docs/architecture/engine.md §16.9).

Every option of the roles is one of three, and none is left out: a row's (its name, or reached by a row's setting
through the wiring), passed through, or refused with why. An option passed through carries its role's spec - its
type, choices, list elements and description, and the entry point that first lists it - and is held to that spec when
typed and in a file: a value outside its type or its choices is refused, a list's elements are held, a path on the
machine is kept as typed. A secret is never passed through, whatever reaches it. The overrides take its name where they
take a row's, at the top of the fleet's settings.yml and with --set, and refuse one of the roles' options that is no
setting by its name and why, in a file and for one run, never saying the value.

Until the cutover, today's tools/settings is run as a child, against an inventory made from the example files, with a
home and a collections path of its own: every name it lets a person set (its change starts with its own command) is
taken here, as a row or as an option passed through, but the nine of today's settings the table has no row for and the
three options left out by name, which the test names; every option it writes through another setting is refused here
through that setting's row; and every name it refuses is no option here, refused for today's reason. After the cutover
the same test reads the roles' specs alone.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
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
    LEFT_OUT,
    PER_MACHINE,
    SECRET,
    SET_BY_A_RUN,
    THROUGH,
)
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
# Today's settable names the engine does not take: the nine with no row, and the one of the four options today's tool
# writes by its own name.
NOT_TAKEN = (*OLDER, OLDER_VPN, *LEFT, "proxmox_vm_validate_certs")
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
    # 203 options today: 87 passed through, the rest a row's or refused.
    assert (len(names), len(passed)) == (203, 87)


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
        assert row.type == passthrough.ROW_TYPES[each.type]
        assert not (row.secret or row.agent or row.values or row.form or row.least is not None)
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
    assert set(LEFT_OUT) == {*LEFT_WITH_THEM, OLDER_VPN, "tailscale_wanted", *TAKEN_AS}
    for name, why in LEFT_OUT.items():
        assert name in every and name not in _row_names(), name
        assert passthrough.refusal(name) == why == passthrough.option(name).refused
    for name, row in TAKEN_AS.items():
        assert LEFT_OUT[name] == THROUGH.format(row=row) and registry.row(row).type == "bool"
    assert "host.yml" in LEFT_OUT["proxmox_vm_api_port"] and "always checked" in LEFT_OUT["proxmox_vm_validate_certs"]


def test_a_machine_s_own_a_run_s_own_and_one_of_any_value_are_refused() -> None:
    specs = wiring.specs()
    every = {name for role in ROLES for name in specs[role]}
    assert len(BY_A_RUN) == 22 and BY_A_RUN <= every
    for name in BY_A_RUN:
        assert passthrough.refusal(name) == SET_BY_A_RUN, name
    per_machine = sorted(name for name in every if passthrough.refusal(name) == PER_MACHINE)
    assert per_machine == ["common_hostname", "proxmox_vm_name"]
    assert sorted(name for name in every if passthrough.refusal(name) == ANY_VALUE) == ["proxmox_vm_id"]


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
