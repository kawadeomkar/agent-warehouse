"""`just status`'s engine in parts: what it keeps (lib/claude_vm/status/settings.py), how it reaches a VM
(lib/claude_vm/status/reach.py), apt's answer kept (playbooks/module_utils/status_probe.py) and what it makes of a VM's
reads (found.py).

Every run keeps in a folder of the test's own, and every connection is to a stand-in: nothing here reaches a VM or
this machine's ssh, or reads the developer's ~/.ssh.
"""

from __future__ import annotations

import argparse
import getpass
import json
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from ansible.parsing.vault import VaultLib, VaultSecret
from harness import PVE_SECRET, REPO, _env, _executable, _inventory, fake_fleet, load_module
from test_status import STATUS, status_command, status_root

from claude_vm.status import engine, found, reach, settings
from claude_vm.status import model as S

PROBE = load_module(REPO / "playbooks" / "module_utils" / "status_probe.py", "status_probe_settings", register=False)


@pytest.fixture
def fake_pve(tmp_path):
    yield from fake_fleet(tmp_path)


# -------------------------------------------------------------------------------------------- what is kept ----


def _run_with_cache(
    root: Path, cache: Path, vault: Path, *python: str, env: dict | None = None
) -> subprocess.CompletedProcess[str]:
    """tools/status keeping in cache, as the recipe runs it; python's flags go to the interpreter, env's settings to its
    environment. The inventory is written once: it is one of the files what is kept is keyed by."""
    inventory = root / "hosts.yml"
    if not inventory.exists():
        _inventory(root, {}, False)
    argv = [STATUS, "-i", inventory, "--json", "--cache", cache, "--vault-file", vault]
    return subprocess.run(
        [sys.executable, *python, *map(str, argv)],
        cwd=root,
        env=_env(root, **(env or {})),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _imports_ansible(done: subprocess.CompletedProcess[str]) -> bool:
    return any("| ansible" in line or "| ansible." in line for line in done.stderr.splitlines())


def _plain_vault(root: Path, secret: str = PVE_SECRET, **more: str) -> Path:
    vault = root / "vault.yml"
    vault.write_text(json.dumps({"vault_proxmox_api_token_secret": secret, **more}))
    return vault


def test_a_second_run_starts_without_ansible_and_says_the_same(tmp_path, fake_pve) -> None:
    root = status_root(tmp_path, fake_pve)
    cache, vault = tmp_path / "cache", _plain_vault(root)
    first = _run_with_cache(root, cache, vault, "-X", "importtime")
    assert first.returncode in (0, 1, 2), first.stderr[-2000:]
    assert _imports_ansible(first)
    second = _run_with_cache(root, cache, vault, "-X", "importtime")
    assert second.returncode == first.returncode, second.stderr[-2000:]
    assert not _imports_ansible(second)
    keep = ("exit", "sections", "title")
    one, two = json.loads(first.stdout), json.loads(second.stdout)
    assert {k: one[k] for k in keep} == {k: two[k] for k in keep}
    assert [vm["name"] for vm in one["vms"]] == [vm["name"] for vm in two["vms"]]
    assert one["settings"] == two["settings"]


def test_a_change_to_a_file_the_settings_are_made_from_asks_ansible_again(tmp_path, fake_pve) -> None:
    root = status_root(tmp_path, fake_pve)
    cache, vault = tmp_path / "cache", _plain_vault(root)
    _run_with_cache(root, cache, vault)
    changed = root / "group_vars" / "all" / "more.yml"
    changed.write_text("vm_cores: 4\n")
    done = _run_with_cache(root, cache, vault, "-X", "importtime")
    assert _imports_ansible(done)
    assert any("the settings ask for 4 cores" in it["text"] for it in json.loads(done.stdout)["attention"])


def test_extra_vars_neither_read_nor_write_a_snapshot(tmp_path, fake_pve) -> None:
    root = status_root(tmp_path, fake_pve)
    cache, vault = tmp_path / "cache", _plain_vault(root)
    argv = [*status_command(root, {}, "--json", "--cache", str(cache), "--vault-file", str(vault)), "-e", "vm_cores=4"]
    done = subprocess.run(
        argv, cwd=root, env=_env(root), stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False
    )
    assert done.returncode in (0, 1, 2), done.stderr[-2000:]
    assert not (cache / "settings.json").exists()


def test_what_is_kept_holds_no_secret_and_only_this_user_may_read_it(tmp_path, fake_pve) -> None:
    root = status_root(tmp_path, fake_pve)
    cache, vault = tmp_path / "cache", _plain_vault(root, vault_vm_password="a-long-password")
    (root / "group_vars" / "all" / "pw.yml").write_text('vault_vm_password: "a-long-password"\n')
    done = _run_with_cache(root, cache, vault)
    assert done.returncode in (0, 1, 2), done.stderr[-2000:]
    kept = cache / "settings.json"
    assert kept.is_file() and kept.stat().st_mode & 0o077 == 0
    text = kept.read_text()
    assert PVE_SECRET not in text and "a-long-password" not in text
    assert settings.SECRET_MARK in text


def test_a_token_that_is_not_the_vaults_is_never_kept(tmp_path, fake_pve) -> None:
    root = status_root(tmp_path, fake_pve)
    cache, vault = tmp_path / "cache", _plain_vault(root, secret="another-secret-altogether")
    _run_with_cache(root, cache, vault)
    assert not (cache / "settings.json").exists()


@pytest.mark.parametrize(
    ("setting", "value"),
    [("vm_user", "the-vaults-own-value"), ("vm_projects_dir", '/srv/the "quoted" \\ projects')],
)
def test_a_snapshot_that_would_hold_a_vault_value_is_not_kept_and_the_log_says_which(
    tmp_path, fake_pve, setting: str, value: str
) -> None:
    # A quote or a backslash is escaped where JSON writes the value: it is looked for so.
    root = status_root(tmp_path, fake_pve, {setting: value})
    cache, vault = tmp_path / "cache", _plain_vault(root, vault_something=value)
    log = tmp_path / "status.log"
    done = _run_with_cache(root, cache, vault, env={"ANSIBLE_LOG_PATH": str(log)})
    assert done.returncode in (0, 1, 2), done.stderr[-2000:]
    assert not (cache / "settings.json").exists()
    said = log.read_text()
    assert "the settings are not kept for the next run, since what the vault's vault_something holds" in said
    assert value not in said


def test_a_vault_only_ansible_can_read_still_gives_a_report(tmp_path, fake_pve) -> None:
    # !unsafe, the way to keep a password holding braces: Ansible reads it, a plain YAML reader refuses it.
    root = status_root(tmp_path, fake_pve)
    cache, vault = tmp_path / "cache", root / "vault.yml"
    vault.write_text(f'vault_proxmox_api_token_secret: "{PVE_SECRET}"\nvault_vm_password: !unsafe "a{{{{b"\n')
    done = _run_with_cache(root, cache, vault)
    assert done.returncode in (0, 1, 2), done.stderr[-2000:]
    assert not (cache / "settings.json").exists()


PASSWORDS = """
import argparse, json, sys
sys.path.insert(0, {lib!r})
from claude_vm.status import settings
opts = argparse.Namespace(
    inventory=[sys.argv[1]], extra=[], names="", only="", cache="", vault_file="", vault_password_file=""
)
config = settings.load(opts)
print(json.dumps({{name: config["password"](name) for name in ("alpha", "beta")}}))
"""


def test_a_vm_with_a_sudo_password_of_its_own_is_given_that_one_and_nothing_is_kept(tmp_path, fake_pve) -> None:
    root = status_root(tmp_path, fake_pve, {"ansible_become_password": "the-fleets-own"})
    (root / "host_vars").mkdir()
    (root / "host_vars" / "beta.yml").write_text("ansible_become_password: betas-own\n")
    cache, vault, log = tmp_path / "cache", _plain_vault(root), tmp_path / "status.log"
    done = _run_with_cache(root, cache, vault, env={"ANSIBLE_LOG_PATH": str(log)})
    assert done.returncode in (0, 1, 2), done.stderr[-2000:]
    assert not (cache / "settings.json").exists() and "a VM has a sudo password of its own" in log.read_text()
    script = tmp_path / "passwords.py"
    script.write_text(PASSWORDS.format(lib=str(REPO / "lib")))
    said = subprocess.run(
        [sys.executable, str(script), str(root / "hosts.yml")],
        cwd=root,
        env=_env(root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert said.returncode == 0, said.stderr[-2000:]
    assert json.loads(said.stdout) == {"alpha": "the-fleets-own", "beta": "betas-own"}


def test_a_password_script_given_by_its_bare_name_is_run_from_here_not_found_on_path(tmp_path, monkeypatch) -> None:
    _executable(tmp_path / ".vault_pass", "#!/bin/sh\necho the-password\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "/nonexistent")
    assert settings._password(".vault_pass") == b"the-password"


def test_a_github_username_in_the_settings_still_lets_them_be_kept(tmp_path, fake_pve) -> None:
    # A vm_user named as the GitHub account: the username is no secret, and just settings never looks for it either.
    root = status_root(tmp_path, fake_pve, {"vm_user": "octocat-dev"})
    cache, vault = tmp_path / "cache", _plain_vault(root, vault_github_username="octocat-dev")
    done = _run_with_cache(root, cache, vault)
    assert done.returncode in (0, 1, 2), done.stderr[-2000:]
    assert (cache / "settings.json").is_file()


@pytest.mark.parametrize("kind", ["file", "script"])
def test_the_vault_opens_with_the_password_file_ansible_s_own_setting_names(tmp_path, fake_pve, kind: str) -> None:
    # No .vault_pass, as getting-started offers: ANSIBLE_VAULT_PASSWORD_FILE names the password, and an executable one
    # is run for it, as ansible-playbook does. The vault is the inventory's, so Ansible must open it too.
    root = status_root(tmp_path, fake_pve)
    secret = VaultSecret(b"the-vault-password")
    plain = json.dumps({"vault_proxmox_api_token_secret": PVE_SECRET}).encode()
    vault = root / "group_vars" / "all" / "vault.yml"
    vault.write_bytes(bytes(VaultLib(secrets=[("default", secret)]).encrypt(plain, secret)))
    if kind == "file":
        password = tmp_path / "vault-password"
        password.write_text("the-vault-password\n")
    else:
        password = _executable(tmp_path / "vault-password.sh", "#!/bin/sh\necho the-vault-password\n")
    cache, env = tmp_path / "cache", {"ANSIBLE_VAULT_PASSWORD_FILE": str(password)}
    first = _run_with_cache(root, cache, vault, "-X", "importtime", env=env)
    assert first.returncode in (0, 1, 2), first.stderr[-2000:]
    assert _imports_ansible(first) and json.loads(first.stdout)["vms"][0]["api"]["read"]
    second = _run_with_cache(root, cache, vault, "-X", "importtime", env=env)
    assert second.returncode == first.returncode, second.stderr[-2000:]
    assert not _imports_ansible(second) and json.loads(second.stdout)["vms"][0]["api"]["read"]


def test_collections_that_differ_from_their_pins_are_refused_when_ansible_is_asked(tmp_path, fake_pve) -> None:
    # Ansible resolves the roles' module_defaults group from the collections installed: the check every recipe that
    # runs Ansible makes, made by the engine where it asks Ansible, since in the recipe it would hold back the outline.
    root = status_root(tmp_path, fake_pve)
    cache, vault = tmp_path / "cache", _plain_vault(root)
    first = _run_with_cache(root, cache, vault)
    assert first.returncode in (0, 1, 2), first.stderr[-2000:]
    empty = tmp_path / "no-collections"
    empty.mkdir()
    # Another set of collections is another key: the snapshot made with the pinned ones does not hold.
    done = _run_with_cache(root, cache, vault, env={"ANSIBLE_COLLECTIONS_PATH": str(empty)})
    assert done.returncode == 3, done.stderr[-2000:]
    assert "the installed Galaxy collections are not the ones requirements.yml pins. Run: just install" in done.stderr


def test_the_snapshot_is_made_again_when_a_filter_ansible_core_or_a_collection_changes(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    manifest = tmp_path / "collections" / "ansible_collections" / "community" / "proxmox" / "MANIFEST.json"
    changed = ("filter_plugins/status.py", "lib/claude_vm/editors/registry.py", "requirements.yml")
    for path in [*(repo / name for name in changed), manifest]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("as it was\n")
    core = tmp_path / "site" / "ansible_core-2.21.5.dist-info" / "METADATA"
    core.parent.mkdir(parents=True)
    core.write_text("")
    monkeypatch.setattr(settings, "REPO", repo)
    monkeypatch.setattr(settings, "_ansible_core", lambda: [core])
    monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", str(tmp_path / "collections"))
    opts = argparse.Namespace(inventory=[str(repo / "inventory")], vault_password_file="")
    keys = [settings._digest(opts)]
    for path in [*(repo / name for name in changed), manifest]:
        path.write_text("changed by a pull, or a just install\n")
        keys.append(settings._digest(opts))
    upgraded = tmp_path / "site" / "ansible_core-2.21.6.dist-info" / "METADATA"
    core.parent.rename(upgraded.parent)
    monkeypatch.setattr(settings, "_ansible_core", lambda: [upgraded])
    keys.append(settings._digest(opts))
    assert len(set(keys)) == len(keys)


def test_the_installed_ansible_core_is_found_by_its_version_without_importing_it() -> None:
    import ansible.release

    found = [path.parent.name for path in settings._ansible_core()]
    assert found == [f"ansible_core-{ansible.release.__version__}.dist-info"]


def test_a_vault_is_opened_as_ansible_vault_seals_it() -> None:
    plain = b"vault_proxmox_api_token_secret: a-secret\n"
    secret = VaultSecret(b"the-password")
    sealed = VaultLib(secrets=[("default", secret)]).encrypt(plain, secret)
    assert settings._vault_open(bytes(sealed), b"the-password") == plain
    with pytest.raises(settings.Stale):
        settings._vault_open(bytes(sealed), b"not-the-password")


def test_each_vm_gets_its_own_name_in_what_is_said_of_it() -> None:
    config = {
        "per_vm": {"special": {"vm": {"marker": "kept as it was rendered"}, "reach": {"port": "2200"}}},
        "vm": {
            "claude": {"login_command": f"just login {settings.PLACEHOLDER} --only claude"},
            "tailscale": {"hostname": settings.dns_label(settings.PLACEHOLDER)},
        },
        "reach": {"port": ""},
    }
    alpha = settings.for_vm(config, "Alpha.lab")
    assert alpha["claude"]["login_command"] == "just login Alpha.lab --only claude"
    assert alpha["tailscale"]["hostname"] == "alpha-lab"
    assert settings.for_vm(config, "special") == {"marker": "kept as it was rendered"}
    # Reached as its own host_vars file says, too.
    assert settings.reach_for(config, "special") == {"port": "2200"}
    assert settings.reach_for(config, "alpha") == {"port": ""}


def test_with_discovery_off_no_token_is_asked_for(tmp_path) -> None:
    # A Vagrant box or the configure scenario's container: no Proxmox to ask, and no vault with its token.
    root = status_root(tmp_path, "https://127.0.0.1:9", {"claude_vm_discovery": False})
    fake_api = root / "group_vars" / "all" / "fake_api.yml"
    fake_api.write_text(
        "".join(line + "\n" for line in fake_api.read_text().splitlines() if "token_secret" not in line)
    )
    argv = [*status_command(root, {}, "--json"), "-e", json.dumps({"claude_vm_discovery": False})]
    done = subprocess.run(
        argv, cwd=root, env=_env(root), stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False
    )
    assert done.returncode in (0, 1, 2), done.stderr[-2000:]
    assert "claude_vm_discovery is off" in json.loads(done.stdout)["title"]


def test_a_vm_is_reached_with_the_system_s_python_and_sudo_by_its_path_and_the_host_key_held() -> None:
    # The VM user's PATH over SSH starts with directories the user owns: a python3 or a sudo there would run as root.
    assert settings.REACH["python"] == "{{ claude_vm_python }}"
    assert settings.REACH["become_exe"] == "{{ ansible_become_exe | default('sudo') }}"
    # status's own options first, since ssh keeps the first value it is given: the inventory's cannot loosen them.
    assert settings.REACH["ssh_args"].startswith("{{ status_ssh_args }} ")
    assert settings.REACH["connection"] == "ssh"


def test_whether_a_vm_had_a_tailscale_login_is_read_from_the_listing_never_from_the_vm() -> None:
    # What keeps the vault's key from a VM a role once logged in: Proxmox's record, which the VM cannot change.
    assert settings.FLEET["tailscale_tag_pattern"] == "{{ claude_vm_tailscale_tag_pattern }}"
    source = (REPO / "lib" / "claude_vm" / "status" / "engine.py").read_text()
    assert "before = bool(re.search(joined, tags))" in source and 'tags = str(entry.get("tags") or "")' in source


# ------------------------------------------------------------------------------------------- reaching a VM ----


def _how(**more) -> dict:
    return {
        "connection": "local",
        "become": True,
        "become_exe": "sudo",
        "python": sys.executable,
        "host": "x",
        "user": "",
        "ssh_args": "",
        "ssh_executable": "ssh",
        "control_dir": "~/.ansible/cp",
        **more,
    }


def _asked(tree: Path) -> dict:
    (tree / "proc" / "sys" / "kernel").mkdir(parents=True)
    (tree / "proc" / "sys" / "kernel" / "osrelease").write_text("6.8.0-85-generic\n")
    (tree / "proc" / "loadavg").write_text("0.42 0.31 0.20 1/123 4567\n")
    (tree / "proc" / "meminfo").write_text("MemTotal: 4 kB\nMemAvailable: 2 kB\n")
    (tree / "proc" / "uptime").write_text("100.0 1.0\n")
    return {"user": getpass.getuser(), "limit": 10, "system": {"root": str(tree), "upgrades": False, "editors": []}}


# A sudo that says no without the password it is given on stdin, and runs its command once it has it.
SUDO = """#!/bin/sh
echo "$*" >> "$(dirname "$0")/sudo.log"
case "$1" in
  -n) echo "sudo: a password is required" >&2; exit 1 ;;
  -S)
    IFS= read -r password
    [ "$password" = "$(cat "$(dirname "$0")/password")" ] || { echo "sudo: wrong password" >&2; exit 1; }
    shift 3
    [ "$1" != -H ] || shift
    exec "$@"
    ;;
esac
echo "stand-in sudo: unexpected: $*" >&2
exit 2
"""


def test_a_sudo_that_asks_is_given_the_password_on_stdin_alone(tmp_path) -> None:
    sudo = _executable(tmp_path / "sudo", SUDO)
    (tmp_path / "password").write_text("hun'ter\"2\n")
    read = reach.read(_how(become_exe=str(sudo)), _asked(tmp_path / "tree"), lambda _: None, lambda: "hun'ter\"2")
    assert read["user"]["system"]["os"] == "" and read["user"]["system"]["kernel"] == "6.8.0-85-generic"
    logged = (tmp_path / "sudo.log").read_text()
    assert "-n -H" in logged and "-S -p  -H" in logged and "hun" not in logged


def test_a_sudo_that_asks_for_no_password_never_hands_it_to_python(tmp_path) -> None:
    # A sudo that lets it through unasked leaves the password on stdin, ahead of the script: Python's syntax error would
    # say that line, so what starts the probe drops everything before its mark.
    sudo = _executable(tmp_path / "sudo", '#!/bin/sh\nshift 3\n[ "$1" != -H ] || shift\nexec "$@"\n')
    fails_once = tmp_path / "sudo-once"
    _executable(
        fails_once,
        f'#!/bin/sh\nif [ "$1" = -n ]; then echo no >&2; exit 1; fi\nexec "{sudo}" "$@"\n',
    )
    read = reach.read(
        _how(become_exe=str(fails_once)), _asked(tmp_path / "tree"), lambda _: None, lambda: "not python (("
    )
    assert read["user"]["system"]["kernel"] == "6.8.0-85-generic", read


def test_where_sudo_cannot_be_had_the_user_s_reads_are_made_and_root_s_said_not_to_be(tmp_path) -> None:
    sudo = _executable(tmp_path / "sudo", '#!/bin/sh\necho "sudo: not in the sudoers file" >&2\nexit 1\n')
    asked = {**_asked(tmp_path / "tree"), "tailscale": {"wanted": True}}
    read = reach.read(_how(become_exe=str(sudo)), asked, lambda _: None, lambda: "")
    assert read["user"]["system"]["kernel"] == "6.8.0-85-generic"
    assert read["root"] == {"failed": True, "msg": "sudo refused: sudo: not in the sudoers file"}


def test_no_program_asked_for_the_password_means_it_is_never_read(tmp_path) -> None:
    # Asked only for sudo's password: with sudo out of the way, nothing asks.
    asked_for: list[int] = []
    read = reach.read(_how(become=False), _asked(tmp_path / "tree"), lambda _: None, lambda: asked_for.append(1) or "")
    assert read["user"]["system"]["kernel"] == "6.8.0-85-generic" and asked_for == []


def test_ssh_that_cannot_connect_is_unreachable_with_its_own_words(tmp_path) -> None:
    ssh = _executable(
        tmp_path / "ssh", '#!/bin/sh\necho "ssh: connect to host x port 22: Connection refused" >&2\nexit 255\n'
    )
    how = _how(connection="ssh", ssh_executable=str(ssh), control_dir=str(tmp_path / "cp"))
    read = reach.read(how, {"user": "dev"}, lambda _: None, lambda: "")
    assert read == {"unreachable": True, "msg": "ssh: connect to host x port 22: Connection refused"}


def test_a_vm_that_takes_the_connection_and_says_nothing_is_said_once_and_soon(tmp_path, monkeypatch) -> None:
    # A live VM did this: every ssh to it hung past its ConnectTimeout, and the engine tried each of sudo's three ways.
    monkeypatch.setattr(reach, "START_SECONDS", 1)
    calls = tmp_path / "calls"
    ssh = _executable(tmp_path / "ssh", f'#!/bin/sh\necho x >> "{calls}"\nexec sleep 30\n')
    how = _how(connection="ssh", ssh_executable=str(ssh), control_dir=str(tmp_path / "cp"))
    read = reach.read(how, {"user": "dev"}, lambda _: None, lambda: "a-password")
    assert read["unreachable"] and "said nothing" in read["msg"], read
    assert S.status_refusal(read["msg"]) == "timeout"
    assert calls.read_text() == "x\n", "tried more than once"


@pytest.mark.parametrize(
    ("said", "wanted"),
    [
        ("not json\n[1,2,3]\n42\n", "exit 1"),
        ('{"started": true}\n', "exit 1"),
        ('{"started": true}\n{"user": {"n": 1e999}}\n', "exit 1"),
    ],
)
def test_what_a_vm_sends_is_read_with_care_for_its_shape(tmp_path, said: str, wanted: str) -> None:
    # A VM's root chooses every byte: anything that is not the probe's own document is no answer at all.
    interpreter = _executable(tmp_path / "python", f"#!/bin/sh\nprintf '{said}'\nexit 1\n")
    read = reach.read(_how(become=False, python=str(interpreter)), {"user": "dev"}, lambda _: None, lambda: "")
    assert read.get("failed") and wanted in read["msg"], read


@pytest.mark.parametrize("number", ["1e999", "-1e999", "NaN", "Infinity", "-Infinity"])
def test_a_number_python_cannot_count_is_no_answer(number: str) -> None:
    with pytest.raises(ValueError, match="is not a number"):
        reach.loads(f'{{"n": {number}}}')
    assert reach.loads('{"n": 1.5e3, "m": 7, "s": "1e999"}') == {"n": 1500.0, "m": 7, "s": "1e999"}


@pytest.mark.parametrize("told_early", [False, True])
def test_what_cannot_be_made_of_one_vm_s_answer_costs_that_vm_alone(monkeypatch, told_early: bool) -> None:
    # Anything its root's answer makes the reading raise: never the whole report, which every other VM's is part of.
    def read(how, asked, early, password):
        if told_early:
            early({"user": {}})
        return {"user": {}, "root": {}}

    monkeypatch.setattr(reach, "read", read)

    def ssh_found(read, *_):
        if not read.get("failed"):
            raise OverflowError("cannot convert float infinity to integer")
        return {"unread": read["msg"]}

    monkeypatch.setattr(found, "ssh_found", ssh_found)
    landed = []
    run = engine.Run.__new__(engine.Run)
    run.opts, run.sections, run.known, run.kept, run.lock = None, ["system"], {}, {}, threading.Lock()
    run.screen = type(
        "Screen", (), {"land": lambda self, name, part, merge=False: landed.append((name, part, merge))}
    )()
    run.password = lambda name: ""
    vm = {"asked": {"user": "dev", "limit": 25, "system": {}}}
    run.probe("alpha", {"host": "x"}, vm, False, "alpha/9010")
    assert landed == [("alpha", {"ssh": {"unread": "cannot convert float infinity to integer"}}, True)]


def test_each_vm_s_password_is_asked_for_once_and_by_its_own_name() -> None:
    asked = []
    run = engine.Run.__new__(engine.Run)
    run.password_lock, run.passwords = threading.Lock(), {}
    run.config = {"password": lambda name: asked.append(name) or f"{name}-password"}
    assert [run.password("alpha"), run.password("beta"), run.password("alpha")] == [
        "alpha-password",
        "beta-password",
        "alpha-password",
    ]
    assert asked == ["alpha", "beta"]


def test_no_connection_is_made_once_the_run_has_ended(tmp_path, monkeypatch) -> None:
    # A run that failed or was refused ends every probe, and a probe about to start finds the run over.
    monkeypatch.setattr(reach, "_over", threading.Event())
    ran = tmp_path / "ran"
    interpreter = _executable(tmp_path / "python", f"#!/bin/sh\ntouch {ran}\n")
    reach.end_all()
    read = reach.read(_how(become=False, python=str(interpreter)), {"user": "dev"}, lambda _: None, lambda: "")
    assert read == {"failed": True, "msg": "the run ended before it was reached"} and not ran.exists()


def test_a_vm_that_sends_too_much_is_cut_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(reach, "LINE_BYTES", 1024)
    script = "#!/bin/sh\necho '{\"started\": true}'\nhead -c 100000 /dev/zero | tr '\\000' x\n"
    interpreter = _executable(tmp_path / "python", script)
    read = reach.read(_how(become=False, python=str(interpreter)), {"user": "dev"}, lambda _: None, lambda: "")
    assert read == {"failed": True, "msg": "it sent more than it may"}


def test_a_hosts_way_in_is_the_inventory_s() -> None:
    assert reach.command(_how(become=False))[:1] == [sys.executable]
    docker = reach.command(_how(connection="community.docker.docker", become=False, host="box", user="dev"))
    assert docker[:5] == ["docker", "exec", "-i", "-u", "dev"] and docker[5] == "box"
    with pytest.raises(ValueError, match="cannot reach a host over the winrm connection"):
        reach.command(_how(connection="winrm"))


def test_ssh_is_told_the_host_key_options_first(tmp_path) -> None:
    how = _how(
        connection="ssh",
        ssh_args="-o StrictHostKeyChecking=yes -o UpdateHostKeys=no",
        host="192.0.2.60",
        user="dev",
        control_dir=str(tmp_path / "cp"),
        port=2222,
        key="/k",
    )
    argv = reach.command(how)
    assert argv[1:5] == ["-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no"]
    assert argv[argv.index("-p") + 1] == "2222" and argv[argv.index("-i") + 1] == "/k"
    assert argv[argv.index("-l") + 1] == "dev" and argv[-2] == "192.0.2.60"
    assert argv[-1].startswith("sudo -n -H ") and "/usr/bin/python3" not in argv[-1] or sys.executable in argv[-1]


# ------------------------------------------------------------------------------------- the engine's waves ----


def test_every_vm_s_address_is_asked_for_before_any_vm_s_history() -> None:
    # A later VM's probe starts on its own address, never behind the history of the VMs listed before it.
    vms = [{"vmid": 9000 + n, "node": "pve", "name": f"vm{n}", "status": "running"} for n in range(3)]
    kinds = [item["kind"] for item in engine._second_wave(vms, None, [], {"vm0", "vm1", "vm2"})]
    assert kinds[:6] == ["config", "network"] * 3 and set(kinds[6:]) == {"api"}


def test_a_vm_not_read_further_gets_none_of_its_own_reads() -> None:
    # On a node that is down, nameless, or one of two of a name: each read would wait out its timeout for nothing.
    vms = [{"vmid": 9000 + n, "node": "pve", "name": f"vm{n}", "status": "running"} for n in range(3)]
    items = engine._second_wave(vms, None, [], {"vm0"})
    assert [item["kind"] for item in items[:6]] == ["config", "network"] * 3
    assert {item["vmid"] for item in items if item["kind"] == "api"} == {9000}


def test_no_more_vms_are_read_at_once_than_ansible_s_forks() -> None:
    assert settings.FLEET["forks"] == "{{ lookup('ansible.builtin.config', 'DEFAULT_FORKS') }}"
    run = engine.Run.__new__(engine.Run)
    run.fleet = {"forks": "20"}
    sizes = []
    for count in (30, 3, 0):
        pool = run.probes(count)
        sizes.append(pool._max_workers)
        pool.shutdown()
    assert sizes == [20, 3, 1]


def test_a_vm_s_proxmox_and_ssh_parts_are_both_kept_whichever_lands_first() -> None:
    # Two threads land them: neither may replace the other.
    proxmox, ssh = {"api": {"read": True}, "drift": []}, {"ssh": {"system": {}}}
    for order in ((proxmox, ssh), (ssh, proxmox)):
        screen = engine.Screen(["vms"], True, "")
        for part in order:
            screen.land("alpha", part, merge=True)
        assert screen.found["alpha"] == {**proxmox, **ssh}
    source = (REPO / "lib" / "claude_vm" / "status" / "engine.py").read_text()
    assert source.count("self.screen.land(name,") == source.count("merge=True)") == 2


@pytest.mark.parametrize(
    "answer",
    [b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{"data"', b"NOT HTTP AT ALL\r\n\r\n"],
    ids=["cut-short", "not-http"],
)
def test_a_read_cut_short_or_not_http_is_that_read_failed(answer: bytes) -> None:
    # http.client's own errors (IncompleteRead, BadStatusLine) are no OSError: each is one read failed, not the run.
    one = _got(answer)
    assert one["status"] == -1 and one["msg"].startswith("Request failed: ") and one["json"] is None


def test_a_refusal_whose_body_never_arrives_is_that_read_s_status_alone() -> None:
    one = _got(b"HTTP/1.1 500 Oops\r\nContent-Length: 100\r\n\r\nshort")
    assert one["status"] == 500 and one["msg"] == "HTTP Error 500: Oops" and one["json"] is None


def _got(answer: bytes) -> dict:
    """What one GET makes of a server that answers it with these bytes and hangs up."""
    server = socket.create_server(("127.0.0.1", 0))

    def serve():
        connection, _ = server.accept()
        with connection:
            connection.recv(65536)
            connection.sendall(answer)

    threading.Thread(target=serve, daemon=True).start()
    fleet = {"url": f"http://127.0.0.1:{server.getsockname()[1]}", "headers": {}, "timeout": 5}
    api = engine.Proxmox({**fleet, "ca_path": "", "validate": True})
    try:
        one = api.get("version")
    finally:
        api.pool.shutdown()
        server.close()
    return one


# ---------------------------------------------------------------------------------------- apt's answer kept ----


def _system(upgrades_on=True, known=None):
    return {"root": "/nonexistent", "upgrades": upgrades_on, "timeout": 1, "editors": [], "known": known or {}}


def test_a_known_package_state_needs_no_simulation(monkeypatch) -> None:
    monkeypatch.setattr(PROBE, "apt_key", lambda: "k1")

    def never(limit):
        raise AssertionError("the simulation ran")

    monkeypatch.setattr(PROBE, "upgrades", never)
    told = []
    found_ = PROBE.user_reads(
        {"limit": 5, "system": _system(known={"key": "k1", "upgrades": 7, "security": 2})}, "/h", told.append
    )
    system = found_["system"]
    assert (system["upgrades"], system["security"], system["upgrades_key"]) == (7, 2, "k1")
    assert told == [] and "upgrades_pending" not in system


def test_an_unknown_one_is_simulated_and_everything_else_said_first(monkeypatch) -> None:
    monkeypatch.setattr(PROBE, "apt_key", lambda: "k2")
    monkeypatch.setattr(PROBE, "upgrades", lambda limit: {"upgrades": 3, "security": 1})
    told = []
    found_ = PROBE.user_reads({"limit": 5, "system": _system(known={"key": "stale", "upgrades": 7})}, "/h", told.append)
    assert found_["system"]["upgrades"] == 3 and found_["system"]["upgrades_key"] == "k2"
    assert len(told) == 1 and told[0]["system"]["upgrades_pending"] is True and told[0]["system"]["upgrades"] is None


def test_the_engine_keeps_each_vm_s_answer_by_its_name_and_id(tmp_path, monkeypatch) -> None:
    read = {"user": {"system": {"upgrades": 3, "security": 1, "upgrades_key": "k3", "unread": {}}}, "root": {}}
    monkeypatch.setattr(reach, "read", lambda how, asked, early, password: read)
    run = engine.Run.__new__(engine.Run)
    run.opts, run.sections, run.known, run.kept, run.lock = None, ["system"], {}, {}, __import__("threading").Lock()
    run.screen = type("Screen", (), {"land": lambda self, *_, **__: None})()
    run.password = lambda name: ""
    vm = {
        "asked": {"user": "dev", "limit": 25, "system": {}},
        "claude": {},
        "github": {},
        "tailscale": {},
        "vpn": "none",
        "projects_dir": "",
    }
    run.probe("alpha", {"host": "x"}, vm, False, "alpha/9010")
    assert run.kept == {"alpha/9010": {"key": "k3", "upgrades": 3, "security": 1}}


def test_only_what_the_sections_read_is_asked_for() -> None:
    run = engine.Run.__new__(engine.Run)
    run.sections = ["system"]
    run.known = {"a/1": {"key": "k"}}
    settings_ = {
        "asked": {"user": "dev", "limit": 25, "system": {}, "claude": {}, "github": {}, "work": {}, "tailscale": {}}
    }
    asked = run.asked(settings_, "a/1")
    assert set(asked) == {"user", "limit", "system"} and asked["system"]["known"] == {"key": "k"}
    assert found.parts_wanted(["vms", "storage"]) == set()
    assert found.parts_wanted(["access"]) == {"tailscale"}
    assert found.parts_wanted(["work"]) == {"system", "work"}
    assert found.parts_wanted(["sign-ins"]) == {"claude", "github", "tailscale"}


# --------------------------------------------------------------------------------- what a VM's reads mean ----

GITHUB = {
    "user": "dev",
    "auth": True,
    "auth_method": "auto",
    "token_wanted": False,
    "token_set": False,
    "configure_command": "just deploy x --tags github_projects",
    "login_command": "just login x --only github",
}
ALL_SETTINGS = {
    "claude": {},
    "github": GITHUB,
    "tailscale": {},
    "vpn": "none",
    "projects_dir": "",
}


def test_a_read_that_failed_is_said_by_name_and_the_others_kept() -> None:
    read = {
        "user": {"system": {"failed": True, "msg": "it took longer than 25 s"}, "github": {"found": False}},
        "root": {},
    }
    part = found.ssh_found(read, ALL_SETTINGS, "x", "192.0.2.1", ["system", "sign-ins"], False)
    assert part["unread"]["system"] == "it took longer than 25 s"
    assert part["github"]["line"].startswith("gh is not installed")
    assert part["reached"] and part["system"] is None


def test_a_connection_refused_says_nothing_was_read() -> None:
    part = found.ssh_found(
        {"unreachable": True, "msg": "Connection refused"}, ALL_SETTINGS, "x", "h", ["system"], False
    )
    assert part["reached"] is False and part["refusal"] == "refused" and part["unreachable"] == "Connection refused"


def test_the_whole_probe_failing_is_each_read_s_reason() -> None:
    part = found.ssh_found({"failed": True, "msg": "no python here"}, ALL_SETTINGS, "x", "h", ["system", "work"], False)
    assert (
        part["reached"] and part["unread"]["system"] == "no python here" and part["unread"]["work"] == "no python here"
    )


def test_an_early_read_shows_a_spinner_only_where_apt_is_working() -> None:
    assert S.status_system_read({"upgrades_pending": True, "unread": {}})["upgrades_pending"] is True


def test_garbage_from_a_vm_is_said_not_raised() -> None:
    settings_ = {**ALL_SETTINGS, "github": {**GITHUB, "auth": True}}
    read = {"user": {"github": {"found": True, "status": {"rc": 0, "stdout": "{" * 100000}}}, "root": {}}
    part = found.ssh_found(read, settings_, "x", "h", ["sign-ins"], False)
    assert part["github"]["signed_in"] is False or part["unread"]["github"]
