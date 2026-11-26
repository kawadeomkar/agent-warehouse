"""aw status's settings, worked out from the fleet's files in the command's own process
(lib/claude_vm/cli/status_inputs.py; docs/architecture/engine.md §16.3).

The settings half of tests/unit/test_status_settings.py, which stays as it is. For E4's fleet folder as init makes it
(tests/unit/fleet_fixture.py) beside the example files filled in as init fills them, and for the same fleet with a
setting of each file's and each secret set, `load` gives what today's status/settings.py's `load` gives for the
inventory that says the same, path for path, but for the differences `_differences` names, each checked to be what it
says, with why. Today's is asked in a Python of its own, as today's status asks it. A value given for one run with --set
outranks every file; a secret's row, a name that is no setting's, a value of the wrong type and a value that is one of
the vault's secrets are each refused, the word given never said back, the first three before any file is read. The
person's own ssh options reach ssh each pair one word, held to the SSH runner's rule for every other verb. `load`
sends nothing, starts no ssh (a refusing one is first on PATH) and keeps nothing. What aw status imports is held by
tests/unit/test_aw_cli_imports.py alone. The lines a VM's report says are aw's, each the printer's whole line, where
today's are just's (each such difference below), and `load` gives under "pool" what aw status's reader of the fleet
reads by (lib/claude_vm/cli/status_pool.py, tests/unit/test_aw_status_pool.py), which today's has no key of.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from fleet_fixture import HOST, refusing_ssh, whole_fleet
from harness import ENGINE_POOL, ENGINE_TOKEN, ENGINE_USER, REPO, _env, fake_fleet
from pki import proxmox_pki

from claude_vm import route
from claude_vm.cli import status_inputs
from claude_vm.fleet import overrides, paths, registry, sealed, vaultkey
from claude_vm.secret import Secret
from claude_vm.status import settings as todays

# The project's ansible.cfg's settings that today's status reads: how many machines a run reaches at once, and ssh's
# own options, which status's are made of.
CFG = (
    "forks = 20\n"
    "[ssh_connection]\n"
    "ssh_args = -o ControlMaster=auto -o ControlPersist=60s -o StrictHostKeyChecking=accept-new\n"
)
# Today's settings.load, in a Python of its own as today's status runs it, printed as JSON: the password function asked
# for the placeholder's and for every VM's own.
TODAYS = r"""
import argparse, json, sys
sys.path.insert(0, sys.argv[1])
from claude_vm.status import settings
opts = argparse.Namespace(inventory=[sys.argv[2]], extra=[], names="", only="", cache="", vault_file=[],
    vault_password_file="", private_key="", host_extra="")
config = settings.load(opts)
config["password"] = {name: config["password"](name) for name in ["", *json.loads(sys.argv[3])]}
print(json.dumps(config, sort_keys=True, default=str))
"""
# The secrets the "set" fleet's vault holds, each by its row, with the name today's inventory gives it.
SECRETS = {
    "anthropic_api_key": ("vault_anthropic_api_key", "unit-test-anthropic-key"),
    "github_token": ("vault_github_token", "unit-test-github-token"),
    "tailscale_auth_key": ("vault_tailscale_auth_key", "tskey-client-unit-test"),
    "vm_password": ("vault_vm_password", "unit-test-vm-password"),
}
MACHINE = "alpha"
MACHINE_PASSWORD = "unit-test-alpha-password"
# A setting of each file's - the fleet's settings.yml, the host's own, client.yml - by its row's name.
SETTINGS = {
    "vm_cores": 4,
    "vm_memory_mb": 8192,
    "vm_disk_size": "64G",
    "vpn": "tailscale",
    "tailscale_ssh": True,
    "tailscale_advertise_tags": ["tag:dev"],
    "github_auth_method": "token",
    "github_login_scopes": ["workflow"],
    "vm_projects_dir": "/srv/work",
    "vm_searchdomain": "lab.internal",
    "proxmox_template_custom": "my-template",
    "editor": "zed",
    "run_forks": 7,
    "vm_ssh_options": {"ServerAliveInterval": "30"},
}
# The machine alpha's own, in the fleet's file and in client.yml.
MACHINE_SETTINGS = {"tailscale_hostname": "alpha-box", "vm_ssh_options": {"ServerAliveInterval": "20"}}
# Today's name for each that today names otherwise, or None for one no inventory gives (run_forks: ansible.cfg's forks).
TODAYS_NAMES = {"vm_ssh_options": None, "run_forks": None}
ABSENT = "<absent>"


@pytest.fixture
def pki(tmp_path):
    return proxmox_pki(tmp_path / "pki", key_usage=True)


@pytest.fixture
def made(home, tmp_path, pki, engine_guard):
    """E4's whole fleet against the fake, its host pve at the fake's own address, its route pinned direct; a refusing
    ssh first on PATH from then on."""
    directory = tmp_path / "fake"
    directory.mkdir()
    for base in fake_fleet(directory, engine=True, certificate=(pki["cert"], pki["key"])):
        fleet = whole_fleet(home, base, directory, pki)
        engine_guard.first_on_path(refusing_ssh(home))
        yield fleet


def _key(where: paths.Home) -> Secret:
    return vaultkey.open(where, key_file="", key_command=())


def _put(where: paths.Home, name: str, value: object, machine: str | None = None) -> None:
    """One setting written into the file its row's home names, for the host pve (and machine)."""
    layers = overrides.read(where, HOST)
    kept = {"fleet": layers.fleet, "host": layers.host, "client": layers.client}[registry.row(name).home]
    overrides.set(where, name, value, HOST, kept.digest, machine=machine)


def _set_each(where: paths.Home) -> None:
    """A setting of each file's set, the machine's own among them, and each secret put in the vault."""
    for name, value in SETTINGS.items():
        _put(where, name, value)
    for name, value in MACHINE_SETTINGS.items():
        _put(where, name, value, MACHINE)
    key = _key(where)
    for name, (_, value) in SECRETS.items():
        sealed.put(where, key, name, Secret(value))
    sealed.put(where, key, "become_password", Secret(MACHINE_PASSWORD), machine=MACHINE)


def _ssh_args(options: dict) -> str:
    return " ".join(f"-o {name}={value}" for name, value in options.items())


def _todays_inventory(root: Path, fleet, more: dict | None = None, machines: dict | None = None) -> Path:
    """The inventory today's status reads for the same fleet: localhost, and the Proxmox host as the example's hosts.yml
    names it at the fake's address; the example local.yml and vault.yml, and what init gives a host beside them (the
    token's user, ID and secret, the node, the pool, the route, the certificate kept beside hosts.yml); then more, and
    each machine's own in host_vars/. Returns the inventory's hosts.yml."""
    inventory = yaml.safe_load((REPO / "inventory" / "hosts.yml.example").read_text())
    inventory["all"]["hosts"] = {"localhost": {"ansible_connection": "local"}}
    inventory["all"]["children"]["proxmox"]["hosts"]["pve"].update(ansible_host="127.0.0.1", ansible_user="root")
    root.mkdir(parents=True)
    (root / "hosts.yml").write_text(yaml.safe_dump(inventory))
    group_vars = root / "group_vars" / "all"
    group_vars.mkdir(parents=True)
    examples = REPO / "inventory" / "group_vars" / "all"
    (group_vars / "defaults.yml").symlink_to(examples / "defaults.yml")
    shutil.copy2(examples / "local.yml.example", group_vars / "local.yml")
    vault = yaml.safe_load((examples / "vault.yml.example").read_text())
    shutil.copy2(fleet.home / "fleet" / "hosts" / HOST / "proxmox-trust.pem", root / "proxmox-trust.pem")
    init = {
        "proxmox_api_port": fleet.port,
        "proxmox_api_user": ENGINE_USER,
        "proxmox_api_token_id": ENGINE_TOKEN,
        "vault_proxmox_api_token_secret": fleet.secret,
        "proxmox_node": "pve",
        "proxmox_pool": ENGINE_POOL,
        "vm_ssh_route": "direct",
    }
    (group_vars / "fleet.yml").write_text(yaml.safe_dump({**vault, **init, **(more or {})}))
    if machines:
        host_vars = root / "host_vars"
        host_vars.mkdir()
        for name, values in machines.items():
            (host_vars / f"{name}.yml").write_text(yaml.safe_dump(values))
    return root / "hosts.yml"


def _todays(root: Path, inventory: Path, machines: list[str]) -> dict:
    """Today's settings.load for inventory, asked as today's status asks it, the password function asked for
    machines'."""
    done = subprocess.run(
        [sys.executable, "-I", "-c", TODAYS, str(REPO / "lib"), str(inventory), json.dumps(machines)],
        cwd=root,
        env=_env(root, CFG),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout)


def _new(where: paths.Home, machines: list[str]) -> dict:
    config = status_inputs.load(where, None, status_inputs.Opts(), environ=os.environ)
    config["password"] = {name: config["password"](name) for name in ["", *machines]}
    # What aw status's reader of the fleet reads by, which today's has no key of (tests/unit/test_aw_status_pool.py).
    pool = config.pop("pool")
    assert (pool["pool"], pool["host"], pool["host_flag"]) == (ENGINE_POOL, HOST, "")
    # Every command the model and its drawing name, printed whole, which today's makes in the model: aw's, with no
    # host's flag where the fleet has one host (tests/unit/test_aw_status_pool.py holds each in a report).
    printed = config["fleet"]["settings"].pop("lines")
    assert printed == status_inputs.lines("") and all(line.startswith("aw ") for line in printed.values())
    return json.loads(json.dumps(config, sort_keys=True, default=str))


def _flat(value: object, prefix: str = "") -> dict[str, object]:
    """value by dotted path to each leaf, a list's items by their index; an empty mapping or list is a leaf."""
    if isinstance(value, (dict, list)) and value:
        items = value.items() if isinstance(value, dict) else enumerate(value)
        out: dict[str, object] = {}
        for key, inner in items:
            out.update(_flat(inner, f"{prefix}{key}."))
        return out
    return {prefix.removesuffix("."): value}


def _differences(fleet, root: Path) -> dict[str, tuple[object, object, str]]:
    """Each value that is not today's, by its dotted path: today's, the new one's, and why."""
    user_home = paths.user_home(os.environ)
    control = str(paths.control_dir(os.environ))
    owned_1x = r"(?i)(^|[;,])(agent\-warehouse|claude\-on\-proxmox){}([;,]|\Z)"
    owned = r"(?i)(^|[;,])(agent\-warehouse){}([;,]|\Z)"
    anchor = str(fleet.home / "fleet" / "hosts" / HOST / "proxmox-trust.pem")
    clean = "the clean slate: no name of 1.x's is read"
    there = "the host's anchor is in its folder of the fleet"
    one = "the one control directory, its ~ expanded (paths.control_dir)"
    return {
        "fleet.ssh_config_legacy": (str(user_home / ".ssh" / "claude-on-proxmox.conf"), "", clean),
        "fleet.settings.tags_owned.1": ("claude-on-proxmox", ABSENT, clean),
        "fleet.tag_pattern": (owned_1x.format(""), owned.format(""), clean),
        "fleet.tailscale_tag_pattern": (owned_1x.format("-tailscale"), owned.format("-tailscale"), clean),
        "vm.asked.github.marker_1x": ("./.config/claude-on-proxmox/gh-login", "", clean),
        "vm.asked.tailscale.marker_1x": ("/var/lib/claude-on-proxmox/tailscale-installed", "", clean),
        "vm.asked.tailscale.joined_1x": ("/var/lib/claude-on-proxmox/tailscale-joined", "", clean),
        "fleet.ca_path": (str(root / "proxmox-trust.pem"), anchor, there),
        "fleet.trust_file": (str(root / "proxmox-trust.pem"), anchor, there),
        "reach.control_dir": ("~/.ansible/cp", control, one),
        "hop.control_dir": ("~/.ansible/cp", control, one),
        "fleet.template_wanted.build": (
            1,
            "1",
            "pve/template.py's wanted gives each field as a string, as proxmox_template_drift compares them",
        ),
        "hop.port": ("", 22, "host.yml's ssh_port is 22 unless set; today's port was ssh's own where none was set"),
        **_aw_lines(),
    }


# Each line a VM's report says to run, by its path under a VM's block, and its words after the program and the VM.
LINES = {
    "claude.login_command": "login {vm} --only claude",
    "github.login_command": "login {vm} --only github",
    "github.configure_command": "deploy {vm} --tags github_projects",
    "tailscale.login_command": "login {vm} --only tailscale",
    "tailscale.configure_command": "deploy {vm} --tags tailscale",
}


def _aw_lines(at: str = "vm", vm: str = status_inputs.PLACEHOLDER) -> dict[str, tuple[object, object, str]]:
    """Each line the block at at of VM vm says (the placeholder's, which each VM's name replaces, unless given): aw's,
    the printer's whole line, where today's is just's; and the program Tailscale's line names, which today's leaves to
    tailscale_report's default (just)."""
    why = "every line aw status says names aw, the printer's whole line"
    found = {
        f"{at}.{path}": (f"just {words}", f"aw {words}", why)
        for path, words in ((path, words.format(vm=vm)) for path, words in LINES.items())
    }
    found[f"{at}.tailscale.program"] = (ABSENT, "aw", "the program tailscale_report's line names, aw status's")
    found[f"{at}.tailscale.reconfigure_command"] = (
        ABSENT,
        f"aw deploy {vm} --only configure",
        "the line tailscale_report names to configure it again, printed whole, where today's writes its own",
    )
    return found


def _own_differences(new: dict, machines: list[str]) -> dict[str, tuple[object, object, str]]:
    """Status's own ssh options, in each reach of new, as today's were: its shared connection first."""
    shared = "-o ControlMaster=auto -o ControlPersist=60s "
    why = "only the SSH runner's ensure opens a master (ssh/connections.py): status's own options open none"
    found = {}
    reaches = [("reach", new["reach"]), *((f"per_vm.{name}.reach", new["per_vm"][name]["reach"]) for name in machines)]
    for prefix, reach in reaches:
        for key in ("own", "ssh_args"):
            found[f"{prefix}.{key}"] = (shared + reach[key], reach[key], why)
    return found


def _compare(today: dict, new: dict, differences: dict, machines: list[str]) -> None:
    """today and new equal path for path, once each difference is checked to be what it says."""
    flat_today, flat_new = _flat(today), _flat(new)
    differing = {
        path: (flat_today.get(path, ABSENT), flat_new.get(path, ABSENT))
        for path in sorted(flat_today.keys() | flat_new.keys())
        if flat_today.get(path, ABSENT) != flat_new.get(path, ABSENT)
    }
    expected = dict(differences)
    for path, difference in differences.items():
        if path.startswith(("vm.", "reach.")):
            for name in machines:
                expected.setdefault(f"per_vm.{name}.{path}", difference)
    unmet = {
        path: (differing.get(path), why)
        for path, (was, now, why) in expected.items()
        if differing.pop(path, None) != (was, now)
    }
    assert (differing, unmet) == ({}, {})


def test_e4_s_fleet_is_today_s_settings_for_the_example_files_path_for_path(made, tmp_path) -> None:
    root = tmp_path / "today"
    today = _todays(root, _todays_inventory(root, made), [])
    new = _new(paths.home(REPO), [])
    _compare(today, new, {**_differences(made, root), **_own_differences(new, [])}, [])


def test_a_setting_of_each_file_and_each_secret_set_is_today_s_settings_path_for_path(made, tmp_path) -> None:
    where = paths.home(REPO)
    _set_each(where)
    more = {
        **{name: value for name, value in SETTINGS.items() if TODAYS_NAMES.get(name, name)},
        "ansible_ssh_common_args": _ssh_args(SETTINGS["vm_ssh_options"]),
        **dict(SECRETS.values()),
    }
    machine = {
        "tailscale_hostname": MACHINE_SETTINGS["tailscale_hostname"],
        "ansible_ssh_common_args": _ssh_args(MACHINE_SETTINGS["vm_ssh_options"]),
        "ansible_become_password": MACHINE_PASSWORD,
    }
    root = tmp_path / "today"
    today = _todays(root, _todays_inventory(root, made, more, {MACHINE: machine}), [MACHINE])
    new = _new(where, [MACHINE])
    assert new["per_vm"][MACHINE]["vm"]["tailscale"]["hostname"] == "alpha-box"
    assert new["password"] == {"": SECRETS["vm_password"][1], MACHINE: MACHINE_PASSWORD}
    assert new["vm"]["tailscale"]["key_wanted"] and not new["vm"]["tailscale"]["key_refused_locally"]
    differences = _differences(made, root)
    forks = "run_forks is client.yml's; today's is the forks of Ansible's configuration"
    differences["fleet.forks"] = (20, SETTINGS["run_forks"], forks)
    differences.update(_own_differences(new, [MACHINE]))
    differences.update(_aw_lines(f"per_vm.{MACHINE}.vm", MACHINE))
    _compare(today, new, differences, [MACHINE])


def test_what_is_shared_with_today_s_status_is_today_s() -> None:
    # The placeholder each VM's name replaces, and the editors' rows.
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert status_inputs.PLACEHOLDER == todays.PLACEHOLDER
    assert defaults["claude_editors"] == status_inputs.EDITORS


# ---------------------------------------------------------------- --set for one run ----


def test_a_value_given_for_one_run_outranks_every_file(made) -> None:
    where = paths.home(REPO)
    _set_each(where)
    given = status_inputs.Opts(set=("vm_cores=6", "editor=vscode", "proxmox_template_custom=", "run_forks=3"))
    config = status_inputs.load(where, None, given, environ=os.environ)
    assert config["vm"]["drift"]["cores"] == 6
    assert config["fleet"]["settings"]["editor"] == "vscode"
    assert config["fleet"]["settings"]["template_custom"] is False
    assert config["fleet"]["forks"] == 3
    # And for a machine of its own, whose own files set none of them.
    assert config["per_vm"][MACHINE]["vm"]["drift"]["cores"] == 6
    assert status_inputs.load(where, None, status_inputs.Opts(), environ=os.environ)["vm"]["drift"]["cores"] == 4


@pytest.mark.parametrize(
    ("word", "said"),
    [
        ("claude_code_settings=unit-test-given-value", "claude_code_settings may hold a secret"),
        ("unit-test-given-name=4", "--set takes a setting's name"),
        ("unit_test_given_name=4", "that is no setting's name"),
        ("vm_cores=unit-test-given-value", "vm_cores"),
    ],
)
def test_a_word_refused_is_said_before_any_file_is_read_and_never_said_back(home, word, said) -> None:
    # No fleet at all: the word is refused first, and nothing of it but a setting's name is said.
    with pytest.raises(status_inputs.Refused) as refused:
        status_inputs.load(paths.home(REPO), None, status_inputs.Opts(set=(word,)), environ=os.environ)
    assert said in refused.value.sentence
    assert "unit-test-given" not in refused.value.sentence
    assert "unit_test_given" not in refused.value.sentence
    assert not (home / "fleet").exists()


def test_a_value_that_is_one_of_the_vault_s_secrets_is_refused_never_said(made) -> None:
    where = paths.home(REPO)
    _set_each(where)
    for word in (f"github_username={SECRETS['github_token'][1]}", f"vm_projects_dir=/{SECRETS['vm_password'][1]}"):
        with pytest.raises(status_inputs.Refused) as refused:
            status_inputs.load(where, None, status_inputs.Opts(set=(word,)), environ=os.environ)
        assert "is a secret the vault holds" in refused.value.sentence
        assert all(value not in refused.value.sentence for _, value in SECRETS.values())


def test_a_vm_ssh_option_s_value_with_a_space_reaches_ssh_as_one_word(made) -> None:
    # Today's engine splits reach's ssh_args as a shell does: each pair is one word there, so a ProxyCommand with spaces
    # is the VM's own jump, as the engine reads one, never ssh's own -W and another host. The machine with options of
    # its own has a reach of its own, which the run's outrank.
    where = paths.home(REPO)
    _put(where, "vm_ssh_options", MACHINE_SETTINGS["vm_ssh_options"], MACHINE)
    jump = "ssh -W %h:%p unit-test-jumpbox"
    word = f"vm_ssh_options={{ProxyCommand: '{jump}', LocalForward: '8080 localhost:80'}}"
    config = status_inputs.load(where, None, status_inputs.Opts(set=(word,)), environ=os.environ)
    assert sorted(config["per_vm"]) == [MACHINE]
    for reach in (config["reach"], *(one["reach"] for one in config["per_vm"].values())):
        given = shlex.split(reach["given"])
        assert given == ["-o", f"ProxyCommand={jump}", "-o", "LocalForward=8080 localhost:80"]
        assert shlex.split(reach["ssh_args"])[-4:] == given
        assert route.ssh_jump(reach["given"]) == f"ProxyCommand {jump}"


@pytest.mark.parametrize(
    ("given", "machine", "said"),
    [
        ({"HostKeyAlias": "unit-test-other-vm"}, None, "vm_ssh_options sets HostKeyAlias, which only the product sets"),
        ({"LocalCommand": "touch /tmp/unit-test-given"}, MACHINE, "vm_ssh_options sets LocalCommand, which undoes"),
    ],
    ids=["for-every-machine", "a-machine-s-own"],
)
def test_a_vm_ssh_option_the_runner_refuses_is_refused_as_for_every_other_verb(made, given, machine, said) -> None:
    # The SSH runner's rule for every other verb (ssh/runner.py's _given): a HostKeyAlias before the product's own would
    # check a VM's key as another's, and a LocalCommand runs a program here for each connection. Said by the option's
    # name, never its value.
    where = paths.home(REPO)
    _put(where, "vm_ssh_options", given, machine)
    with pytest.raises(status_inputs.Refused) as refused:
        status_inputs.load(where, None, status_inputs.Opts(), environ=os.environ)
    assert said in refused.value.sentence
    assert "unit-test" not in refused.value.sentence


def test_a_host_the_vault_holds_no_token_for_is_refused_with_how_to_put_it(made) -> None:
    where = paths.home(REPO)
    sealed.remove(where, _key(where), "token", host=HOST)
    with pytest.raises(status_inputs.Refused) as refused:
        status_inputs.load(where, None, status_inputs.Opts(), environ=os.environ)
    assert "holds no token's secret for pve" in refused.value.sentence
    assert made.secret not in refused.value.sentence


def test_the_token_goes_into_the_api_s_header_alone_and_nothing_is_kept(made) -> None:
    where = paths.home(REPO)

    def files() -> list[tuple[str, int]]:
        return sorted((str(path), path.stat().st_mtime_ns) for path in made.home.rglob("*") if path.is_file())

    before = files()
    config = status_inputs.load(where, None, status_inputs.Opts(), environ=os.environ)
    header = f"PVEAPIToken={ENGINE_USER}!{ENGINE_TOKEN}={made.secret}"
    assert config["fleet"]["headers"] == {"Authorization": header}
    rest = {key: value for key, value in config.items() if key != "fleet"}
    assert made.secret not in json.dumps({**rest, "fleet": {**config["fleet"], "headers": None}}, default=str)
    assert files() == before
