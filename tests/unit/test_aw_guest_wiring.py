"""The system play's variables for one machine (lib/claude_vm/guest/wiring.py; docs/architecture/engine.md §16.10).

The table holds every variable of the play's three roles' specs, every entry point's, once, but the internals, each
with its reason: no variable of a role is left out and none is made up. Each that a setting makes is the row today's
wiring reaches it from (inventory/group_vars/all/defaults.yml and the roles' defaults, read by lib/claude_vm/wiring.py),
and every variable today's wiring reaches from a row is that row's here; each the role's own option is one
fleet/passthrough.py passes through, and every option of the three roles passed through is one here; the machine's
name makes the hostname; and the internal password is empty, refused wherever a run could set it.

The function's output, for the registry's defaults and for four sets of overrides (the rows the play reads, twice, with
their switches set apart; every option of its roles set, the fact-made defaults among them; and values given for one run
with --set), equals, variable by variable, in type and value, what Ansible gives the same roles from today's example
inventory with the same values in local.yml: a child of the locked ansible-core asks each role's play for each variable
of its spec, with a machine's facts set, on today's side over the inventory made from the example files with host alpha
among the VMs, and on the new side over the one host's inventory layer 1 writes, named by an identity, no group_vars
beside it, and the variables file the play's own writer made from the function's output, loaded as extra variables as
layer 1 gives it. The comparison tells true from 1, and its cases set every two rows the play reads apart in one case
at least, so that a row read in another's place is a difference. The comparison asks Ansible in the test alone; the
module imports the standard library and the patterns.

The names the play's variables may hold are one list, made from the table: the play's own, each role variable and each
internal, every one of which the function makes when every option is set.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import MappingProxyType

import pytest
import yaml
from aw_kit import imports_of
from harness import REPO, ansible_config

from claude_vm import wiring as todays
from claude_vm.fleet import overrides, passthrough, paths, registry
from claude_vm.fleet.overrides import Value
from claude_vm.guest import layer1, system, wiring
from claude_vm.guest import variables as play_variables
from claude_vm.guest.wiring import INTERNALS, MACHINE, OPTION, PLAY, SETTING, TABLE
from claude_vm.ssh import runner

MODULE = "claude_vm.guest.wiring"
ROLES = system.PARTS
# The machine's name, as a person chose it, and a made-up key of this computer's: vm_ssh_public_keys' default reads
# ~/.ssh/id_ed25519.pub, and today's lookup reads the same file of the same home.
NAME = "alpha"
THIS_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF7WiringTestKeyDoNotUse000000000000000000 person@computer"
# What the machine's facts say: an arm64 virtual machine, so that the two defaults made from facts are not what a
# person sets over them in the options' case.
FACTS = {"architecture": "aarch64", "virtualization_type": "kvm"}


def _specs() -> dict[str, dict[str, dict]]:
    return {role: todays.specs()[role] for role in ROLES}


def _settings(where: paths.Home, run: tuple[str, ...] = ()) -> dict[str, Value]:
    return overrides.resolve(where, None, overrides.run_values(run, secrets=()))


@pytest.fixture
def where(home) -> paths.Home:
    """The fleet's home of this test, with this computer's key in the test's own HOME."""
    (home / ".ssh").mkdir(parents=True, exist_ok=True)
    (home / ".ssh" / "id_ed25519.pub").write_text(THIS_KEY + "\n")
    return paths.home(REPO)


# ---------------------------------------------------------------- the table ----


def test_the_table_s_roles_are_the_play_s_parts_in_its_order() -> None:
    assert tuple(TABLE) == wiring.PARTS == system.PARTS
    assert wiring.DEFAULT == overrides.DEFAULT


def test_every_variable_of_the_roles_specs_is_in_the_table_or_an_internal_once_and_none_else() -> None:
    specs = _specs()
    seen: list[str] = []
    for role in ROLES:
        internals = {name for name, internal in INTERNALS.items() if internal.role == role}
        assert not set(TABLE[role]) & internals, role
        assert set(TABLE[role]) | internals == set(specs[role]), role
        seen += [*TABLE[role], *internals]
        for name, source in TABLE[role].items():
            assert source.kind in wiring.KINDS, name
            assert (source.setting is not None) is (source.kind == SETTING), name
    assert len(seen) == len(set(seen)), "a variable is in the table twice"
    assert {internal.role for internal in INTERNALS.values()} <= set(ROLES)
    # Every role variable of the play's roles, every entry point's: the image's and its options' list the same names.
    for role in ROLES:
        listed = yaml.safe_load((REPO / "roles" / role / "meta" / "argument_specs.yml").read_text())["argument_specs"]
        every = {name for entry in listed.values() for name in ((entry or {}).get("options") or {})}
        assert every == set(specs[role]), role


def test_each_setting_is_the_row_today_s_wiring_reaches_the_variable_from_and_none_is_missed() -> None:
    specs = _specs()
    _defaults, feeds = todays.wiring()
    every = todays.specs()
    variables = {name for role in ROLES for name in specs[role]}
    today: set[tuple[str, str]] = set()
    for row in registry.rows():
        if row.name in variables:
            today.add((row.name, row.name))
        for variable in todays.set_by(row.name, feeds, every):
            if variable in variables:
                today.add((variable, row.name))
    ours = {
        (name, str(source.setting)) for role in ROLES for name, source in TABLE[role].items() if source.kind == SETTING
    }
    assert ours == today
    for source in PLAY.values():
        assert source.kind == SETTING and registry.row(str(source.setting)).home == "run"
    assert set(PLAY) == {"configure_wait_for_cloud_init"}
    # Each setting's row is read for the whole fleet: one value for every machine of the run.
    for _name, row in ours:
        assert registry.row(row).scope == "global", row


def test_each_option_is_one_passed_through_and_every_option_of_the_play_s_roles_passed_through_is_one() -> None:
    passed = {each.name for each in passthrough.options() if each.refused is None and each.role in ROLES}
    ours = {name for role in ROLES for name, source in TABLE[role].items() if source.kind == OPTION}
    assert ours == passed
    named = {name for role in ROLES for name, source in TABLE[role].items() if source.kind == MACHINE}
    assert named == {"common_hostname"}
    assert passthrough.refusal("common_hostname") == passthrough.PER_MACHINE


def test_the_internal_password_is_empty_with_its_reason_and_refused_wherever_a_run_could_set_it(where) -> None:
    internal = INTERNALS["common_user_password"]
    assert set(INTERNALS) == {"common_user_password"}
    assert (internal.role, internal.value) == ("common", "")
    assert "F24" in internal.why and "pipe" in internal.why
    assert wiring.role_variables(_settings(where), NAME, ROLES)["common_user_password"] == ""
    # Never a setting: --set and the fleet's file refuse it by name, as the vault's.
    said = f"common_user_password {passthrough.SECRET}"
    with pytest.raises(overrides.Refused) as refused:
        overrides.run_values(["common_user_password=not-the-password"], secrets=())
    assert str(refused.value) == said
    path = paths.fleet_dir(where) / "settings.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("common_user_password: not-the-password\n")
    with pytest.raises(overrides.Refused) as refused:
        overrides.read(where, None)
    assert str(refused.value) == f"{path} line 1: {said}"
    path.unlink()
    # Nor the play's, whatever a caller hands as the settings.
    settings = {**_settings(where), "common_user_password": Value("common_user_password", "not-the-password", "run")}
    with pytest.raises(wiring.Refused) as refused:
        wiring.role_variables(settings, NAME, ROLES)
    assert str(refused.value).startswith("common_user_password is the engine's own, never set: ")
    assert "not-the-password" not in str(refused.value)


@pytest.mark.parametrize(
    ("name", "said"),
    [
        ("common_user", "is made from vm_user, a setting of its own: set vm_user instead"),
        ("vscode_server_user", "is made from vm_user, a setting of its own: set vm_user instead"),
        ("dev_tools_user", "is made from vm_user, a setting of its own: set vm_user instead"),
        ("common_user_ssh_public_keys", "is made from vm_ssh_public_keys, a setting of its own"),
        ("common_extra_packages", "is made from vm_extra_packages, a setting of its own"),
        ("vscode_server_enabled", "is made from vscode_remote, a setting of its own: set vscode_remote instead"),
        ("common_hostname", "is the machine's own name"),
    ],
)
def test_a_variable_made_here_is_refused_in_the_settings_by_its_name(where, name, said) -> None:
    settings = {**_settings(where), name: Value(name, "kept", "run")}
    with pytest.raises(wiring.Refused) as refused:
        wiring.role_variables(settings, NAME, ROLES)
    assert str(refused.value).startswith(f"{name} {said}"), str(refused.value)
    assert wiring.made_here(name) is not None
    # A row of its own name, an option, the play's own and a name no role of the play reads are the settings' to hold.
    for held in ("dev_tools_install_docker", "common_timezone", "configure_wait_for_cloud_init", "vm_cores"):
        assert wiring.made_here(held) is None, held


def test_the_names_the_play_s_variables_may_hold_are_its_own_each_role_variable_and_each_internal_once() -> None:
    every = [*PLAY, *INTERNALS, *(name for role in ROLES for name in TABLE[role])]
    assert len(every) == len(set(every))
    assert type(wiring.NAMES) is frozenset and wiring.NAMES == set(every)
    # The one list, which the play's writer and layer 1's hold read: guest/system.py names it beside the play.
    assert system.NAMES is wiring.NAMES
    # Each of a role variable's shape, none the connection's and none Ansible gives the play itself: the writer's own
    # rules, which hold whatever the list says, never refuse one of them.
    for name in wiring.NAMES:
        assert play_variables.NAME.fullmatch(name) and not name.startswith(play_variables.CONNECTION), name
    assert not wiring.NAMES & play_variables.FIXED


# ---------------------------------------------------------------- the function ----


def test_the_variables_are_the_play_s_then_each_role_s_made_by_the_table(where) -> None:
    fleet = paths.fleet_dir(where) / "settings.yml"
    fleet.parent.mkdir(parents=True, exist_ok=True)
    fleet.write_text("vm_user: pat\nvm_extra_packages: [jq]\ncommon_timezone: Europe/Paris\n")
    settings = _settings(where, ("dev_tools_install_rust=true", "configure_wait_for_cloud_init=false"))
    made = wiring.role_variables(settings, "alpha.lab", ROLES)
    assert list(made)[0] == "configure_wait_for_cloud_init" and made["configure_wait_for_cloud_init"] is False
    assert (made["common_user"], made["vscode_server_user"], made["dev_tools_user"]) == ("pat", "pat", "pat")
    assert made["common_user_ssh_public_keys"] == [THIS_KEY]
    assert made["common_extra_packages"] == ["jq"]
    assert made["common_hostname"] == "alpha.lab"
    assert made["dev_tools_install_rust"] is True and made["dev_tools_install_docker"] is True
    assert made["vscode_server_enabled"] is True and made["dev_tools_node_major"] == 22
    # An option is handed only where it is set; set nowhere, its role's own default applies where the role runs.
    assert made["common_timezone"] == "Europe/Paris"
    options = {name for role in ROLES for name, source in TABLE[role].items() if source.kind == OPTION}
    assert {name for name in made if name in options} == {"common_timezone"}
    # The play's own first, then each role's variables in the table's order, the internal after its role's.
    order = [*PLAY, *(name for role in ROLES for name in (*TABLE[role], *_internals(role)) if name in made)]
    assert list(made) == order
    # Every value is a copy: what the caller changes of it changes nothing of the settings.
    made["common_extra_packages"].append("htop")
    made["common_user_ssh_public_keys"].clear()
    assert settings["vm_extra_packages"].value == ["jq"] and settings["vm_ssh_public_keys"].value == [THIS_KEY]


def _internals(role: str) -> list[str]:
    return [name for name, internal in INTERNALS.items() if internal.role == role]


def test_the_parts_asked_for_give_their_roles_variables_alone_in_the_play_s_order(where) -> None:
    settings = _settings(where)
    made = wiring.role_variables(settings, NAME, ["dev_tools", "common"])
    assert [name for name in made if name not in PLAY] == [
        *(name for name in (*TABLE["common"], *_internals("common")) if name in made),
        *(name for name in TABLE["dev_tools"] if name in made),
    ]
    assert not [name for name in made if name.startswith("vscode_server_")]
    assert list(wiring.role_variables(settings, NAME, [])) == list(PLAY)
    for parts in (["docker"], ["common", "common"], "common", [None], {"common": 1}):
        with pytest.raises(wiring.Refused, match="part"):
            wiring.role_variables(settings, NAME, parts)


@pytest.mark.parametrize("target", ["alpha,beta", "", "a b", "-alpha", "alpha\n", None, 7])
def test_the_machine_is_one_vm_name(where, target) -> None:
    with pytest.raises(wiring.Refused, match="a machine's name is one VM name"):
        wiring.role_variables(_settings(where), target, ROLES)


def test_a_profile_is_laid_where_the_person_set_nothing_and_holds_no_other_name(where) -> None:
    profile = MappingProxyType(
        {
            "vm_user": "pi",
            "dev_tools_install_docker": False,
            "configure_wait_for_cloud_init": False,
            "common_timezone": "Europe/London",
            "dev_tools_deb_arch": "arm64",
        }
    )
    laid = wiring.role_variables(_settings(where), NAME, ROLES, profile)
    assert (laid["common_user"], laid["dev_tools_user"], laid["dev_tools_install_docker"]) == ("pi", "pi", False)
    assert (laid["common_timezone"], laid["dev_tools_deb_arch"]) == ("Europe/London", "arm64")
    assert laid["configure_wait_for_cloud_init"] is False
    # What the person set, in a file or for the run, stays theirs.
    fleet = paths.fleet_dir(where) / "settings.yml"
    fleet.parent.mkdir(parents=True, exist_ok=True)
    fleet.write_text("vm_user: pat\ncommon_timezone: UTC\n")
    settings = _settings(where, ("dev_tools_install_docker=true",))
    kept = wiring.role_variables(settings, NAME, ROLES, profile)
    assert (kept["common_user"], kept["common_timezone"], kept["dev_tools_install_docker"]) == ("pat", "UTC", True)
    assert kept["dev_tools_deb_arch"] == "arm64"
    # A value laid is a copy too.
    listed = {"vm_extra_packages": ["jq"]}
    made = wiring.role_variables(_settings(where), NAME, ROLES, listed)
    made["common_extra_packages"].append("htop")
    assert listed == {"vm_extra_packages": ["jq"]}
    for name in ("common_user", "common_hostname", "common_user_password", "vm_cores", "made_up", 7):
        with pytest.raises(wiring.Refused, match="a profile lays a setting"):
            wiring.role_variables(_settings(where), NAME, ROLES, {name: "x"})
    with pytest.raises(wiring.Refused, match="a profile is a mapping"):
        wiring.role_variables(_settings(where), NAME, ROLES, [("vm_user", "pi")])


def test_every_name_the_function_makes_is_one_the_play_takes_and_every_one_is_made(tmp_path, where) -> None:
    digest = overrides.MISSING
    for name, value in _options_set().items():
        digest = overrides.set(where, name, value, None, digest)
    made = wiring.role_variables(_settings(where), NAME, ROLES)
    assert set(made) == wiring.NAMES
    # Written by the play's own writer, and read back by the hold layer 1 asks of the file, with layer 1's own list of
    # the play's names: a name the play takes is one of guest/wiring.py's, never one a caller adds.
    written = system.variables(tmp_path / "vars.json", made, secrets=())
    assert play_variables.held(written, layer1.PLAY_NAMES[system.PLAY]) == written


def test_settings_that_are_not_resolve_s_are_refused(where) -> None:
    for settings in ([("vm_user", "dev")], {"vm_user": "dev"}):
        with pytest.raises(wiring.Refused, match="resolve"):
            wiring.role_variables(settings, NAME, ROLES)
    lacking = {name: value for name, value in _settings(where).items() if name != "vm_user"}
    with pytest.raises(wiring.Refused, match="the settings hold no value of vm_user, which common_user is made from"):
        wiring.role_variables(lacking, NAME, ROLES)


def test_the_module_imports_the_standard_library_and_the_patterns_alone(home) -> None:
    added = imports_of(module=MODULE, home=home) - imports_of(module="claude_vm.guest", home=home)
    assert MODULE in added, sorted(added)
    assert sorted({name.split(".")[0] for name in added} - set(sys.stdlib_module_names) - {"claude_vm"}) == []
    assert {name for name in added if name.startswith("claude_vm.")} <= {
        "claude_vm.guest.wiring",
        "claude_vm.schema",
        "claude_vm.schema.patterns",
    }


# ---------------------------------------------------------------- against what Ansible gives today ----

# What the locked ansible-core gives each role's play: each variable of the role's spec, and the play's own, templated
# as a task would read them, with the machine's facts set, for one host of one inventory and the extra variables a run
# gives. Printed as one line of JSON, in a Python of its own: one child for each side, since ansible-core reads the
# extra variables once a process.
ASKED = r"""
import json, sys
from pathlib import Path

from ansible import context
from ansible.inventory.manager import InventoryManager
from ansible.module_utils.common.collections import ImmutableDict
from ansible.parsing.dataloader import DataLoader
from ansible.playbook.play import Play
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar
from ansible.vars.manager import VariableManager

asked = json.loads(Path(sys.argv[1]).read_text())
context.CLIARGS = ImmutableDict(extra_vars=tuple(asked["extra_vars"]))
init_plugin_loader([])
loader = DataLoader()
inventory = InventoryManager(loader=loader, sources=asked["sources"])
manager = VariableManager(loader=loader, inventory=inventory)
host = inventory.get_host(asked["host"])
manager.set_host_facts(host.name, asked["facts"])
given = {}
for role, names in asked["roles"].items():
    play = Play.load({"hosts": "all", "gather_facts": False, "roles": [role]}, variable_manager=manager, loader=loader)
    values = manager.get_vars(play=play, host=host)
    templar = Templar(loader=loader, variables=values)
    given[role] = {name: templar.template(values[name]) for name in names if name in values}
print(json.dumps(given, sort_keys=True))
"""


def _collections() -> str:
    """The collections' path every sanctioned run exports (tools/env-run, the recipes): the roles' tasks name modules
    of the Galaxy collections, which ansible-core resolves as a role loads. Never the developer's own folder."""
    found = os.environ.get("ANSIBLE_COLLECTIONS_PATH")
    if not found:
        pytest.fail("ANSIBLE_COLLECTIONS_PATH is not set: run the suite through tools/env-run, as just test does")
    return found


def _typed(value: object) -> str:
    """A value as its JSON text, which says its type at every level: true is not 1, nor 1.0 1."""
    return json.dumps(value, sort_keys=True)


def _differ(today: dict, new: dict, names: list[str]) -> dict[str, tuple[object, object]]:
    """Each of names whose value differs between the two sides, in its type or its value, with both values."""
    return {name: (today[name], new[name]) for name in names if _typed(today[name]) != _typed(new[name])}


def test_the_comparison_tells_a_boolean_from_an_integer_at_every_level() -> None:
    today = {"a": True, "b": False, "c": [True], "d": {"e": 1}, "f": 1.0, "g": "x", "h": None, "i": [1, 2]}
    new = {"a": 1, "b": 0, "c": [1], "d": {"e": True}, "f": 1, "g": "x", "h": None, "i": [1, 2]}
    # Python's own == takes each pair for equal.
    assert all(today[name] == new[name] for name in today)
    assert sorted(_differ(today, new, list(today))) == ["a", "b", "c", "d", "f"]


def _asked(work: Path, side: str, sources: list[str], host: str, extra_vars: list[str]) -> dict[str, dict]:
    """What a child of the locked ansible-core gives each role's play of the system play, on one side."""
    names = {role: [*PLAY, *_specs()[role]] for role in ROLES}
    question = work / f"{side}.json"
    question.write_text(
        json.dumps(
            {"sources": sources, "host": host, "facts": FACTS, "roles": names, "extra_vars": extra_vars},
        )
    )
    env = {
        "PATH": os.environ["PATH"],
        "LANG": "en_US.UTF-8",
        "HOME": os.environ["HOME"],
        "ANSIBLE_CONFIG": ansible_config(work),
        "ANSIBLE_COLLECTIONS_PATH": _collections(),
        "ANSIBLE_ROLES_PATH": str(REPO / "roles"),
        "ANSIBLE_FILTER_PLUGINS": str(REPO / "filter_plugins"),
        "ANSIBLE_INVENTORY_ENABLED": "yaml",
        "ANSIBLE_VERBOSITY": "0",
        "ANSIBLE_DEBUG": "0",
        layer1.UNTRUSTED_VARIABLE: layer1.UNTRUSTED,
    }
    shown = subprocess.run(
        [sys.executable, "-I", "-c", ASKED, str(question)],
        cwd=work,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert shown.returncode == 0, shown.stderr[-3000:]
    return json.loads(shown.stdout.splitlines()[-1])


def _today(work: Path, local: dict[str, object]) -> dict[str, dict]:
    """Today's side: an inventory made from the example files, as the passthrough test makes one, with the values in
    local.yml and host alpha among the VMs, as a run's add_host puts it there."""
    inventory = work / "inventory"
    (inventory / "group_vars" / "all").mkdir(parents=True)
    for each in ("controller.yml", "group_vars/all/defaults.yml"):
        (inventory / each).write_text((REPO / "inventory" / each).read_text())
    hosts = yaml.safe_load((REPO / "inventory" / "hosts.yml.example").read_text())
    hosts["all"]["children"]["claude_vms"]["hosts"] = {NAME: None}
    (inventory / "hosts.yml").write_text(yaml.safe_dump(hosts))
    example = REPO / "inventory" / "group_vars" / "all"
    (inventory / "group_vars" / "all" / "vault.yml").write_text((example / "vault.yml.example").read_text())
    (inventory / "group_vars" / "all" / "local.yml").write_text(
        (example / "local.yml.example").read_text() + "\n" + (yaml.safe_dump(local) if local else "")
    )
    return _asked(work, "today", [str(inventory)], NAME, [])


def _new(work: Path, where: paths.Home, fleet: dict[str, object], run: tuple[str, ...]) -> dict[str, dict]:
    """The new side: the values in the fleet's settings.yml through the overrides' writer, those for one run through
    --set, the function's output written by the play's own writer, and the one host's inventory layer 1 writes."""
    digest = overrides.MISSING
    for name, value in fleet.items():
        digest = overrides.set(where, name, value, None, digest)
    document = wiring.role_variables(_settings(where, run), NAME, ROLES)
    written = system.variables(work / "vars.json", document, secrets=())
    identity = f"{layer1.PREFIX}{uuid.uuid5(uuid.NAMESPACE_DNS, NAME)}"
    made = runner.machine(identity, "192.0.2.10", "dev", work / "cp", known_hosts=work / "known_hosts")
    # Marked as a route settled straight at it marks one (connections.target_for), which the runner's lines take alone.
    target = dataclasses.replace(made, routed_as="direct", routed_from=runner.decided_from(made))
    inventory = work / f"{layer1.INVENTORY_PREFIX}{identity}{layer1.INVENTORY_SUFFIX}"
    inventory.write_text(json.dumps(layer1.inventory_document(target, NAME), indent=2) + "\n")
    assert sorted(path.name for path in work.iterdir()) == sorted(["vars.json", inventory.name]), "nothing beside it"
    return _asked(work, "new", [str(inventory)], identity, [f"@{written}"])


def _another(option: passthrough.Option) -> object:
    """A value of an option's type that is not its role's default, as a person sets one."""
    default = (yaml.safe_load((REPO / "roles" / option.role / "defaults" / "main.yml").read_text()) or {}).get(
        option.name
    )
    if option.name == "common_user_passwordless_sudo":
        # Served as true alone until F24 (guest/system.py), and set to it: an option handed is still the person's.
        return True
    if option.type == "bool":
        return not default if isinstance(default, bool) else False
    if option.type == "int":
        return default + 7
    if option.choices:
        return next(choice for choice in option.choices if choice != default)
    if option.type == "path":
        return f"/made/up/{option.name}"
    if option.type == "list":
        return [f"made-{option.name}", "another"]
    if option.type == "dict":
        return {"made": option.name}
    return f"made-{option.name}"


# The rows the play reads, set in the fleet's file: some of the switches turned from their default and some left at it,
# and in the next case the other way about, so that with the defaults and the run's own no two switches carry one value
# in every case, and a row read in another's place is a difference (test_the_cases_set_every_two_rows_..._apart).
ROWS_SET = {
    "vm_user": "pat",
    "vm_ssh_public_keys": [
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF7WiringTestKeyOne0000000000000000000000000000 pat@one",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF7WiringTestKeyTwo0000000000000000000000000000 pat@two",
    ],
    "vm_extra_packages": ["jq", "htop"],
    "vscode_remote": False,
    "dev_tools_install_docker": False,
    "dev_tools_install_uv": False,
    "dev_tools_install_rust": True,
    "dev_tools_node_major": 20,
}
ROWS_APART = {"dev_tools_install_node": False, "dev_tools_install_docker": False, "dev_tools_install_gh": False}
# The values for one run, over the fleet's file: a row of the fleet's and the play's own, an option, a fact-made one,
# and two switches.
RUN = (
    "vm_user=sam",
    "configure_wait_for_cloud_init=false",
    "common_apt_upgrade=no",
    "dev_tools_deb_arch=amd64",
    "dev_tools_install_gh=false",
    "dev_tools_install_uv=false",
)
CASES = ("defaults", "rows", "rows apart", "options", "for one run")


def _options_set() -> dict[str, object]:
    return {each.name: _another(each) for each in passthrough.options() if each.refused is None and each.role in ROLES}


def _case(case: str) -> tuple[dict[str, object], tuple[str, ...]]:
    """A case's values: those in the fleet's settings.yml, and those given for its run."""
    fleet: dict[str, object] = {
        "defaults": {},
        "rows": ROWS_SET,
        "rows apart": ROWS_APART,
        "options": _options_set(),
        "for one run": {"vm_user": "pat", "common_timezone": "Europe/Paris"},
    }[case]
    return fleet, RUN if case == "for one run" else ()


def test_the_cases_set_every_two_rows_the_play_reads_apart(where) -> None:
    sources = [*PLAY.values(), *(source for role in ROLES for source in TABLE[role].values())]
    rows = sorted({str(source.setting) for source in sources if source.kind == SETTING})
    seen: dict[str, list[str]] = {row: [] for row in rows}
    for case in CASES:
        fleet, run = _case(case)
        digest = overrides.MISSING
        for name, value in fleet.items():
            digest = overrides.set(where, name, value, None, digest)
        settings = _settings(where, run)
        for row in rows:
            seen[row].append(_typed(settings[row].value))
        (paths.fleet_dir(where) / "settings.yml").unlink(missing_ok=True)
    # No two rows carry one value in every case, so the comparison sees one read in another's place; and each row is
    # set from its default in one case at least, so it sees one left at its role's default.
    assert [(one, other) for one, other in itertools.combinations(rows, 2) if seen[one] == seen[other]] == []
    assert [row for row in rows if len(set(seen[row])) == 1] == []


@pytest.mark.parametrize("case", CASES)
def test_the_variables_are_what_ansible_gives_the_roles_from_today_s_inventory(tmp_path, where, case) -> None:
    fleet, run = _case(case)
    local = {**fleet, **overrides.run_values(run, secrets=())}
    (tmp_path / "today").mkdir()
    (tmp_path / "new").mkdir()
    today = _today(tmp_path / "today", local)
    new = _new(tmp_path / "new", where, fleet, run)
    specs = _specs()
    for role in ROLES:
        names = [*PLAY, *specs[role]]
        assert sorted(today[role]) == sorted(new[role]) == sorted(names), role
        assert _differ(today[role], new[role], names) == {}, role
    # What was set reached the roles, not their defaults: every value set, and the machine's name.
    given = {name: value for role in ROLES for name, value in new[role].items()}
    assert given["common_hostname"] == NAME
    for name, value in local.items():
        if name in given:
            assert _typed(given[name]) == _typed(value), name
    if case == "defaults":
        assert given["common_user_ssh_public_keys"] == [THIS_KEY]
        assert (given["dev_tools_deb_arch"], given["dev_tools_docker_manage_service"]) == ("arm64", True)
    if case == "rows":
        assert (given["common_user"], given["vscode_server_user"], given["dev_tools_user"]) == ("pat", "pat", "pat")
        assert given["common_user_ssh_public_keys"] == ROWS_SET["vm_ssh_public_keys"]
        assert given["vscode_server_enabled"] is False and given["common_extra_packages"] == ["jq", "htop"]
    if case == "rows apart":
        assert given["vscode_server_enabled"] is True and given["dev_tools_install_node"] is False
    if case == "options":
        assert (given["dev_tools_deb_arch"], given["dev_tools_docker_manage_service"]) == ("amd64", False)
        # Every option of the three roles: seventeen of common's, four of the editor's server's, twelve of dev_tools'.
        options = {name for role in ROLES for name, source in TABLE[role].items() if source.kind == OPTION}
        assert set(fleet) == options and len(options) == 33
    if case == "for one run":
        assert (given["common_user"], given["configure_wait_for_cloud_init"]) == ("sam", False)
        assert (given["common_apt_upgrade"], given["dev_tools_deb_arch"]) == (False, "amd64")
        assert (given["dev_tools_install_gh"], given["dev_tools_install_uv"]) == (False, False)
