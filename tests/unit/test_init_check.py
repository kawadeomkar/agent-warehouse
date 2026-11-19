"""playbooks/init_check.yml, just init's read-only checks: run against the fake Proxmox, never a real one.

Each run's inventory is the test's own: group_vars/all/defaults.yml is the
project's, beside a file of the test's settings, and the Proxmox host is either
a stand-in host on the local connection, whose stand-in `hostname` prints its
node name, or a host on ssh with a stand-in `ssh` first on PATH that records
its call and exits 255. No run reaches a real ssh: ansible.cfg's accept-new
would add a host key to the developer's ~/.ssh/known_hosts, and
test_no_run_reaches_a_real_ssh holds every run to one of the two. The vault
file and the directory searched for a public key are the test's too, and so
are the host's certificates read over root SSH, and the trust file beside the
test's inventory. The certificate's step runs only in the tests that ask for it,
against a fake Proxmox serving one made as Proxmox makes them (pki.py).
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml
from harness import (
    LOGINS,
    PVE_SECRET,
    REPO,
    _env,
    _every_task,
    _executable,
    _failures,
    _fake_pve,
    _free_port,
    _init_next,
    _left_to_do,
    _messages,
    _pve,
    _pve_requests,
    _refusing_ssh,
    _results,
    _run,
    _run_at_a_terminal,
    _ssh_calls,
    _stand_in_hosts,
    _text,
)
from harness import REFUSING_SSH as SSH_STAND_IN
from pki import proxmox_pki

from claude_vm import trust

PLAYBOOK = REPO / "playbooks" / "init_check.yml"
CLUSTER_VMS = REPO / "playbooks" / "tasks" / "cluster_vms.yml"
VAULT_HEADER = "$ANSIBLE_VAULT;1.1;AES256\n"
ROLE_8 = [
    "VM.Allocate",
    "VM.Clone",
    "VM.Config.CPU",
    "VM.Config.Cloudinit",
    "VM.Config.Disk",
    "VM.Config.Memory",
    "VM.Config.Network",
    "VM.Config.Options",
    "VM.PowerMgmt",
    "VM.Audit",
    "Datastore.AllocateSpace",
    "Datastore.Audit",
    "SDN.Use",
]
# The node it says it is: pve, or what hostname-is beside it holds.
HOSTNAME_STAND_IN = """#!/bin/sh
printf '%s\\n' "$*" >> "$(dirname "$0")/hostname-calls.log"
cat "$(dirname "$0")/hostname-is" 2>/dev/null || echo pve
"""
LOCAL_EXAMPLE = REPO / "inventory" / "group_vars" / "all" / "local.yml.example"
DEPLOY_NEXT = "Next: just deploy alpha, which creates a VM named alpha (any name will do)."
# Python 3.13 and later verify strictly: a CA without keyUsage is refused, and the node's own certificate is an anchor.
STRICT = sys.version_info >= (3, 13)


@pytest.fixture
def fake_pve(tmp_path):
    """The fake Proxmox on a loopback port: its base URL."""
    yield from _fake_pve(tmp_path)


def _port(base: str) -> int:
    return int(base.rsplit(":", 1)[1])


def _inventory_of(
    root: Path,
    base: str,
    pve: str,
    settings: dict | None,
    vault: str,
    host: str,
    proxmox_host: bool = True,
    trust: dict | None = None,
    hosts_file: Path | None = None,
) -> tuple[dict, dict]:
    """The test's inventory under root - the project's defaults.yml, and local.yml: the example, then the
    settings - and the Proxmox host, in hosts_file where given, as another host's hosts/<name>/hosts.yml is laid
    over the shared inventory; returns the extra vars pointing the playbook at root's files, and the environment of
    the run."""
    assert pve in ("local", "ssh"), pve
    group_vars = root / "group_vars" / "all"
    group_vars.mkdir(parents=True, exist_ok=True)
    if not (group_vars / "defaults.yml").exists():
        (group_vars / "defaults.yml").symlink_to(REPO / "inventory" / "group_vars" / "all" / "defaults.yml")
    local = group_vars / "local.yml"
    if not local.exists():
        test_settings = {
            # Straight to each VM: auto would ask this machine's ssh where it dials the host. A test of the route
            # through it sets its own.
            "vm_ssh_route": "direct",
            "proxmox_api_port": _port(base),
            "vault_proxmox_api_token_secret": PVE_SECRET,
            "vault_github_username": "octocat",
            "vm_ssh_public_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyDoNotUse test@example"],
        }
        local.write_text(LOCAL_EXAMPLE.read_text() + yaml.safe_dump(test_settings | (settings or {})))
    vault_file = root / "vault.yml"
    vault_file.write_text(vault)
    (root / "ssh").mkdir(exist_ok=True)
    if pve == "local":
        if not (root / "bin").exists():
            hostvars = _stand_in_hosts(root, {"pve": LOGINS["logged-out"]})["pve"]
            _executable(root / "bin" / "hostname", HOSTNAME_STAND_IN)
        else:
            hostvars = {"ansible_connection": "local", "ansible_python_interpreter": str(root / "pve" / "python")}
        hostvars |= {"ansible_host": host, "ansible_user": "root"}
        env = {}
    else:
        hostvars = {"ansible_connection": "ssh", "ansible_host": host, "ansible_user": "root"}
        # The refusing ssh first on PATH (SSH_STAND_IN).
        env = _refusing_ssh(root)
    hosts = {"pve": hostvars} if proxmox_host else {}
    if hosts_file:
        hosts_file.parent.mkdir(parents=True, exist_ok=True)
        hosts_file.write_text(yaml.safe_dump({"proxmox": {"hosts": hosts}}))
        hosts = {}
    (root / "proxmox.yml").write_text(yaml.safe_dump({"proxmox": {"hosts": hosts}}))
    paths = {"init_vault_file": str(vault_file), "init_ssh_dir": str(root / "ssh"), "init_local_file": str(local)}
    # The host's certificates, as root SSH reads them: the test's own files, and the step that reads them only where
    # the test gives them (trust, by kind: ca, node, custom).
    etc = root / "pve-etc"
    paths["init_trust_paths"] = {
        "ca": str(etc / "pve-root-ca.pem"),
        "custom": str(etc / "local" / "pveproxy-ssl.pem"),
        "node": str(etc / "local" / "pve-ssl.pem"),
    }
    # The copy a run is checked against where it keeps nothing: never the checkout's .cache.
    paths["init_trust_run_file"] = str(root / "cache" / "init" / "proxmox-trust.pem")
    for kind, text in (trust or {}).items():
        Path(paths["init_trust_paths"][kind]).parent.mkdir(parents=True, exist_ok=True)
        Path(paths["init_trust_paths"][kind]).write_text(text)
    if trust is None:
        paths["init_trust_wanted"] = False
    return paths, env


def _check(
    root: Path,
    base: str,
    pve: str,
    settings: dict | None = None,
    *args: str,
    vault: str = VAULT_HEADER,
    host: str = "127.0.0.1",
    extra: dict | None = None,
    env: dict | None = None,
    proxmox_host: bool = True,
    trust: dict | None = None,
    hosts_file: Path | None = None,
) -> dict:
    """init_check.yml with the fake Proxmox at base, with no terminal; pve is "local" (a stand-in host) or "ssh". With
    trust (the host's certificates, by kind, {} for none), the certificate's step runs; with hosts_file, the Proxmox
    host is defined there (_inventory_of)."""
    paths, run_env = _inventory_of(root, base, pve, settings, vault, host, proxmox_host, trust, hosts_file)
    extra_vars = json.dumps(paths | (extra or {}))
    sources = ["-i", str(root / "proxmox.yml"), *(["-i", str(hosts_file)] if hosts_file else [])]
    return _run(root, PLAYBOOK, {}, *sources, "-e", extra_vars, *args, env=(env or {}) | run_env)


def _check_at_a_terminal(
    root: Path,
    base: str,
    pve: str,
    settings: dict | None,
    steps,
    extra: dict | None = None,
    trust: dict | None = None,
):
    """init_check.yml at a pseudo-terminal, init_ask on, typing steps; rc, output, log. With trust, the certificate's
    step runs, as _check's."""
    paths, run_env = _inventory_of(root, base, pve, settings, VAULT_HEADER, "127.0.0.1", trust=trust)
    return _run_at_a_terminal(
        root,
        PLAYBOOK,
        {},
        paths | {"init_ask": True} | (extra or {}),
        steps=steps,
        args=["-i", str(root / "proxmox.yml")],
        env=run_env,
    )


def _said(report: dict) -> str:
    return "\n".join(str(message) for message in _messages(report, "Say what each check found").values())


def _hostname_calls(root: Path) -> str:
    return _text(root / "bin" / "hostname-calls.log")


# -------------------------------------------------------------- another host ---
# just init --host nuc: once every check passed, each setting the host sets for itself goes into its own local.yml
# (here the test's), as the checks settled it or as this run reads it, and its default VM's name - never before.
HOST = {
    "name": "nuc",
    "secret": "vault_proxmox_api_token_secret",
    "always": ["proxmox_node", "proxmox_storage", "proxmox_bridge", "proxmox_api_port", "proxmox_template_vmid"],
    "default_vm": "nuc-default",
}


def test_another_host_is_given_what_it_sets_for_itself_once_every_check_passed(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", extra={"init_host": HOST})
    assert report["rc"] == 0, _failures(report)
    local = yaml.safe_load((root / "group_vars" / "all" / "local.yml").read_text())
    assert {name: local[name] for name in [*HOST["always"], "vm_default_name"]} == {
        "proxmox_node": "pve",
        "proxmox_storage": "local-lvm",
        "proxmox_bridge": "vmbr0",
        "proxmox_api_port": _port(fake_pve),
        "proxmox_template_vmid": 9000,
        "vm_default_name": "nuc-default",
    }
    said = _said(report)
    assert "Next: just deploy alpha --host nuc, which creates a VM named alpha on nuc" in said
    # Each written once: a second run finds them there, and writes nothing.
    again = _check(root, fake_pve, "ssh", extra={"init_host": HOST})
    assert again["rc"] == 0 and (root / "group_vars" / "all" / "local.yml").read_text().count("proxmox_node:") == 1


def test_another_host_is_given_nothing_while_a_check_fails(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    wrong = {"vault_proxmox_api_token_secret": "11111111-2222-3333-4444-555555555555"}
    report = _check(root, fake_pve, "ssh", wrong, extra={"init_host": HOST})
    assert report["rc"] != 0
    local = yaml.safe_load((root / "group_vars" / "all" / "local.yml").read_text())
    assert "vm_default_name" not in local and "proxmox_node" not in local


# ------------------------------------------------------------------- the checks ---
def test_nothing_is_checked_while_a_value_is_the_examples(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", host="192.0.2.10")
    assert report["rc"] == 0, _failures(report)
    said = "\n".join(str(m) for m in _messages(report, "Say what is not set yet").values())
    assert "Proxmox is not checked yet: the Proxmox host address (inventory/hosts.yml) is still" in said
    assert _pve_requests(tmp_path) == [] and _ssh_calls(root) == ""
    # Nothing new for NEXT STEPS: init_settings.yml's, printed before, names what is missing.
    assert _left_to_do(report) == {}


def test_every_check_passes_on_a_host_with_its_template_and_every_default_fits(tmp_path: Path, fake_pve: str) -> None:
    # As just init runs it at a terminal: every default fits, so nothing is
    # asked or written.
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", extra={"init_ask": True})
    assert report["rc"] == 0, _failures(report)
    said = _said(report)
    assert f"ok   Proxmox VE 8.2.4 at 127.0.0.1:{_port(fake_pve)} accepted ansible@pve!ansible" in said
    assert "ok   the token holds every privilege the project uses on /vms" in said
    assert "ok   the template ubuntu-24.04-cloudinit is on pve, so just deploy needs no root SSH" in said
    assert "ok   the VMs get 1 SSH key" in said and "!!" not in said
    # The template exists: no SSH at all.
    assert _ssh_calls(root) == ""
    assert "ok   node pve: it holds the template, and just deploy creates the VMs there" in said
    assert "ok   storage local-lvm: the token can use it" in said
    assert "ok   bridge vmbr0, the template's: the token has SDN.Use on it" in said
    assert "written" not in said
    asked = [
        name for name, _, result in _results(report) if name.startswith("init_ask | Ask") and not result.get("skipped")
    ]
    assert asked == []
    # The one step left, said last and kept for NEXT STEPS.
    assert said.rstrip().endswith("\n" + DEPLOY_NEXT)
    assert _init_next(report) == {"init_check": DEPLOY_NEXT}


@pytest.mark.parametrize("asking", [True, False])
@pytest.mark.parametrize("login", ["works", "refused"])
def test_where_the_vms_are_reached_through_the_host_its_login_is_checked(tmp_path, fake_pve, login, asking) -> None:
    # As a run's start makes it, through the vm_route action: an SSH connection that runs nothing, and no forward. A
    # login that fails is a problem, since just deploy would stop at its first step.
    from test_route import STAND_IN

    sockets = tempfile.mkdtemp(prefix="aw-", dir="/tmp")
    try:
        hop = tmp_path / "hop"
        hop.mkdir()
        _executable(hop / "ssh", STAND_IN)
        (hop / "mode").write_text("open" if login == "works" else "refused")
        settings = {
            "vm_ssh_route": "host",
            "ansible_ssh_executable": str(hop / "ssh"),
            "ansible_control_path_dir": sockets,
        }
        root = tmp_path / "run"
        report = _check(root, fake_pve, "ssh", settings, extra={"init_ask": asking})
    finally:
        shutil.rmtree(sockets, ignore_errors=True)
    said = _said(report)
    assert said, _failures(report)
    calls = (hop / "calls").read_text()
    assert " -W " not in calls and " -O check " in calls
    assert (
        "ok   the template ubuntu-24.04-cloudinit is on pve, so just deploy needs no root SSH but the hop to the VMs"
        in said
    )
    if login == "works":
        assert report["rc"] == 0, _failures(report)
        assert "ok   the VMs are reached through pve (root@127.0.0.1) (vm_ssh_route: host), and its login works" in said
    else:
        assert report["rc"] != 0
        assert (
            "!!   VMs are reached through pve (root@127.0.0.1) (vm_ssh_route: host), and ssh could not log in" in said
        )


def _hop_stand_in(tmp_path: Path, sockets: str, mode: str = "open") -> dict:
    from test_route import STAND_IN

    hop = tmp_path / "hop"
    hop.mkdir()
    _executable(hop / "ssh", STAND_IN)
    (hop / "mode").write_text(mode)
    return {"ansible_ssh_executable": str(hop / "ssh"), "ansible_control_path_dir": sockets}


@pytest.mark.parametrize("aliases", ["their own jump", "through the hop"])
def test_where_every_host_brings_a_jump_the_hop_s_login_is_made_only_for_the_aliases(tmp_path, fake_pve, aliases):
    # A run's start puts the login off where every host is given a jump: only the aliases go through the hop, unless
    # claude_ssh_options names a jump too, when nothing does and init asks nothing of it.
    sockets = tempfile.mkdtemp(prefix="aw-", dir="/tmp")
    try:
        settings = {
            "vm_ssh_route": "host",
            "ansible_ssh_common_args": "-o ProxyJump=jump.invalid",
            **_hop_stand_in(tmp_path, sockets),
            **({"claude_ssh_options": {"ProxyJump": "jump.invalid"}} if aliases == "their own jump" else {}),
        }
        report = _check(tmp_path / "run", fake_pve, "ssh", settings)
    finally:
        shutil.rmtree(sockets, ignore_errors=True)
    said = _said(report)
    calls = tmp_path / "hop" / "calls"
    jumped = "ok   the VMs are reached by the jump every host is given (ProxyJump jump.invalid), and"
    if aliases == "their own jump":
        assert not calls.exists()
        assert f"{jumped} the aliases by the one claude_ssh_options names, so just deploy needs no hop" in said, said
    else:
        assert " -O check " in calls.read_text()
        assert (
            f"{jumped} only the aliases go through pve (root@127.0.0.1) (vm_ssh_route: host), whose login works" in said
        ), said


def test_another_host_s_route_is_its_own_never_the_shared_files(tmp_path, fake_pve):
    # A file read after nuc's own local.yml says host, as the shared one is read beside it; nuc's own says direct (the
    # harness's pin): its route is its own, so no login is made through the host - whose shared value no run against
    # nuc may read.
    root = tmp_path / "run"
    (root / "group_vars" / "all").mkdir(parents=True)
    (root / "group_vars" / "all" / "shared.yml").write_text("vm_ssh_route: host\n")
    report = _check(root, fake_pve, "ssh", extra={"init_host": HOST})
    said = _said(report)
    assert said, _failures(report)
    assert "vm_ssh_route" not in said and "hop" not in said, said


def test_a_host_named_on_a_tailnet_goes_through_it_under_auto_and_its_login_is_checked(tmp_path, fake_pve):
    # ssh -G cannot show it here (the refusing stand-in): the host as written, 100.64.0.7, is a tailnet address.
    settings = {"vm_ssh_route": "auto", "proxmox_api_host": "127.0.0.1"}
    report = _check(tmp_path / "run", fake_pve, "ssh", settings, host="100.64.0.7")
    said = _said(report)
    assert (
        "!!   VMs are reached through pve (root@100.64.0.7) (vm_ssh_route: auto), and ssh could not log in there:"
        in said
    ), said
    assert "set vm_ssh_route: direct: under auto, a host reached on a tailnet is a hop" in said


def test_a_hop_setting_that_is_not_one_account_is_a_problem_of_its_own(tmp_path, fake_pve):
    report = _check(tmp_path / "run", fake_pve, "ssh", {"vm_ssh_route": "host", "vm_ssh_jump": "forwarder@other"})
    said = _said(report)
    assert report["rc"] != 0
    assert (
        "!!   vm_ssh_jump 'forwarder@other' names more than an account: set vm_ssh_jump on the host's entry to one"
        " account on that host, or vm_ssh_route: direct" in said
    ), said


def test_a_wrong_secret_names_the_token(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", {"vault_proxmox_api_token_secret": "11111111-2222-3333-4444-555555555555"})
    assert report["rc"] != 0
    assert "!!   Proxmox at 127.0.0.1:" in _said(report) and "refused the token ansible@pve!ansible (401)" in _said(
        report
    )
    assert "just init found" in "\n".join(_failures(report).values())


# An IPv6 address is said as a URL says it, in brackets.
@pytest.mark.parametrize(("host", "url"), [("127.0.0.1", "127.0.0.1"), ("::1", "[::1]")], ids=["ipv4", "ipv6"])
def test_nothing_listening_names_the_address(tmp_path: Path, fake_pve: str, host: str, url: str) -> None:
    root = tmp_path / "run"
    closed = _free_port()
    report = _check(root, fake_pve, "ssh", {"proxmox_api_port": closed}, host=host)
    assert report["rc"] != 0
    assert f"nothing answered at https://{url}:{closed}" in _said(report)


@pytest.mark.parametrize(("version", "agent"), [("8.2.4", "VM.Monitor"), ("9.2.13", "VM.GuestAgent.Audit")])
def test_a_missing_privilege_is_named_with_its_path(tmp_path: Path, fake_pve: str, version: str, agent: str) -> None:
    _pve(fake_pve, "PUT", "fake/version", version=version)
    _pve(
        fake_pve,
        "PUT",
        "fake/privileges",
        token="ansible",
        path="/vms",
        privs=",".join(p for p in ROLE_8 if p != "VM.Clone"),
    )
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh")
    assert report["rc"] != 0
    said = _said(report)
    assert f"!!   the token lacks VM.Clone, {agent} on /vms" in said
    assert "pveum role modify AnsibleVM -append 1 -privs 'VM.Clone " + agent + "'" in said
    assert "just init at a terminal, with root SSH to the host, offers to" in " ".join(said.split())


def test_a_missing_privilege_on_another_host_names_that_host_s_just_init(tmp_path: Path, fake_pve: str) -> None:
    # The current host's just init would set up another (RP-1): the line ends its just with the host checked.
    _pve(
        fake_pve,
        "PUT",
        "fake/privileges",
        token="ansible",
        path="/vms",
        privs=",".join(p for p in ROLE_8 if p != "VM.Clone"),
    )
    report = _check(tmp_path / "run", fake_pve, "ssh", extra={"init_host": HOST})
    assert report["rc"] != 0
    assert "just init --host nuc at a terminal, with root SSH to the host, offers to" in " ".join(_said(report).split())


NO_TEMPLATE = {"proxmox_template_name": "not-built-yet", "proxmox_template_vmid": 9999}


def test_without_the_template_root_ssh_is_checked_and_reads_the_node(tmp_path: Path, fake_pve: str) -> None:
    # With the log at -vvv and every module's arguments injected, as the most a
    # run can print or log: the token's secret appears nowhere.
    root = tmp_path / "run"
    env = {"ANSIBLE_LOG_PATH": str(tmp_path / "run.log"), "ANSIBLE_LOG_VERBOSITY": "3"}
    report = _check(root, fake_pve, "local", NO_TEMPLATE, "-e", '{"ansible_inject_invocation": true}', env=env)
    assert report["rc"] == 0, _failures(report)
    assert PVE_SECRET not in json.dumps(report) + (tmp_path / "run.log").read_text()
    said = _said(report)
    assert "ok   there is no template not-built-yet yet: just deploy builds it at VMID 9999, over root SSH" in said
    assert "ok   root SSH to 127.0.0.1 works, and it is node pve (the template is not built yet)" in said
    assert _hostname_calls(root).splitlines() == ["-s"]
    facts = [r.get("ansible_facts", {}) for name, _, r in _results(report) if name == "Record root SSH's result"]
    assert facts[0]["init_ssh_node"] == "pve"


# The fake's template is one this version builds; another CPU type makes it one the settings would build otherwise.
DIFFERS = {"proxmox_template_cpu_type": "x86-64-v2-AES"}


def test_a_template_that_differs_from_its_settings_is_said_and_root_ssh_checked(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "local", DIFFERS)
    assert report["rc"] == 0, _failures(report)
    said = _said(report)
    assert (
        "ok   the template ubuntu-24.04-cloudinit on pve differs from its settings (cpu host → x86-64-v2-AES):"
        " just deploy rebuilds it, over root SSH" in said
    ), said
    assert "ok   root SSH to 127.0.0.1 works, and it is node pve (just deploy rebuilds the template)" in said
    assert _hostname_calls(root).splitlines() == ["-s"]


def test_a_template_with_no_record_is_a_problem_since_deploy_refuses_it(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "nodes/pve/qemu/9000/config", description="made by hand")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "local", {})
    said = _said(report)
    assert report["rc"] != 0, said
    assert "!!   the template ubuntu-24.04-cloudinit on pve keeps no record of its build, so just deploy" in said
    assert "rebuilds" not in said, said


def test_a_template_kept_as_it_is_needs_no_root_ssh(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", {**DIFFERS, "proxmox_template_rebuild": False})
    assert report["rc"] == 0, _failures(report)
    said = _said(report)
    assert (
        "ok   the template ubuntu-24.04-cloudinit on pve differs from its settings (cpu host → x86-64-v2-AES), and is"
        " kept as it is since proxmox_template_rebuild is false, so just deploy needs no root SSH" in said
    ), said
    assert not _ssh_calls(root)


def test_root_ssh_that_fails_says_how_to_install_the_key(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", NO_TEMPLATE)
    assert report["rc"] != 0
    said = _said(report)
    assert "!!   root SSH to 127.0.0.1 failed" in said and "ssh-copy-id root@127.0.0.1" in said
    assert _ssh_calls(root)


def test_a_vmid_taken_where_the_template_would_be_built_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "POST", "nodes/pve/qemu/9000/clone", newid=9050, name="just-a-vm")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "local", {"proxmox_template_name": "not-built-yet", "proxmox_template_vmid": 9050})
    assert report["rc"] != 0
    said = _said(report)
    assert "!!   there is no template not-built-yet yet, and VMID 9050, where just deploy would build it" in said
    assert "is taken by just-a-vm on pve:" in said


def test_a_template_of_another_name_at_the_template_vmid_is_no_template(tmp_path: Path, fake_pve: str) -> None:
    # just deploy finds the template by its name: one of another name at
    # proxmox_template_vmid is no template of this project's, and in the way.
    root = tmp_path / "run"
    report = _check(root, fake_pve, "local", {"proxmox_template_name": "the-one-deploy-looks-for"})
    assert report["rc"] != 0
    assert "is taken by ubuntu-24.04-cloudinit on pve" in _said(report)


@pytest.mark.parametrize(("vmid", "holder"), [(8000, "a qemu on pve2, which is down"), (9003, "ours-container on pve")])
def test_a_vmid_another_guest_holds_is_named_with_its_node(
    tmp_path: Path, fake_pve: str, vmid: int, holder: str
) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "local", {"proxmox_template_name": "not-built-yet", "proxmox_template_vmid": vmid})
    assert report["rc"] != 0
    assert f"is taken by {holder}:" in _said(report)


def test_a_template_on_another_node_than_a_proxmox_node_set_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    before = (LOCAL_EXAMPLE.read_text(), None)
    report = _check(root, fake_pve, "ssh", {"proxmox_node": "nuc"})
    assert report["rc"] != 0
    said = _said(report)
    assert "!!   the template is on pve, but proxmox_node is nuc, and VMs are cloned on the node that holds" in said
    assert "or build a template on nuc with a proxmox_template_name of its own" in said
    assert before[0] in (root / "group_vars" / "all" / "local.yml").read_text()


def test_a_host_that_is_another_node_than_a_proxmox_node_set_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "local", NO_TEMPLATE | {"proxmox_node": "nuc"})
    assert report["rc"] != 0
    # pve2 is down: pve is the only node there is.
    assert "!!   pve is the only node, but proxmox_node is nuc" in _said(report)


def test_no_key_suggests_one_from_the_ssh_directory(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    (root / "ssh").mkdir(parents=True)
    (root / "ssh" / "id_test.pub").write_text("ssh-ed25519 AAAA test\n")
    report = _check(root, fake_pve, "ssh", {"vm_ssh_public_keys": [], "vault_vm_password": ""})
    assert report["rc"] != 0
    assert f'vm_ssh_public_keys: ["<the line in {root / "ssh" / "id_test.pub"}>"]' in _said(report)


# A console password under its old name, vm_password, is a problem and never said: as a string in
# test_next_steps_keep_each_problem_with_its_fix_and_no_deploy, as a number here, and empty, which sets nothing, in
# test_every_check_passes_on_proxmox_ve_9_and_just_deploy_waits_for_the_username. The check reads nothing of Proxmox,
# and adds its line to whatever else a run finds.
def test_a_password_alone_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    # The console takes it; SSH, which just deploy configures the VMs over, does not.
    root = tmp_path / "run"
    settings = {"vm_ssh_public_keys": [], "vault_vm_password": "a-console-password", "vm_password": 12345678}
    report = _check(root, fake_pve, "ssh", settings)
    assert report["rc"] != 0
    said = _said(report)
    assert "!!   the VMs get only vault_vm_password, which is for the Proxmox console" in said
    assert "a-console-password" not in json.dumps(report)
    # A password YAML reads as a number is a password all the same, and names no Python error.
    assert "!!   vm_password is set, which is vault_vm_password from 1.0" in said and "has no len" not in said
    assert len([line for line in said.splitlines() if line.startswith("  !!   ")]) == 2, said


def test_a_privilege_without_propagate_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    privs = [p if p != "VM.Clone" else "VM.Clone:0" for p in ROLE_8] + ["VM.Monitor"]
    _pve(fake_pve, "PUT", "fake/privileges", token="ansible", path="/vms", privs=",".join(privs))
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh")
    assert report["rc"] != 0
    assert "!!   the token holds VM.Clone on /vms but not on the VMs under it" in _said(report)
    assert "-propagate 1" in _said(report)


def test_with_a_pool_the_privileges_are_read_on_it_and_a_template_outside_it_is_a_problem(
    tmp_path: Path, fake_pve: str
) -> None:
    report = _check(tmp_path / "run", fake_pve, "ssh", {"proxmox_pool": "agent-warehouse"})
    said = _said(report)
    assert "ok   the token holds every privilege the project uses on /pool/agent-warehouse" in said, said
    assert "!!   the template ubuntu-24.04-cloudinit on pve is no member of pool agent-warehouse" in said
    assert "pveum pool modify agent-warehouse --vms 9000" in " ".join(said.split())
    # Made a member, as root does on the host: nothing left to say about it.
    _pve(fake_pve, "PUT", "fake/pool-member", vmid=9000, pool="agent-warehouse")
    again = _said(_check(tmp_path / "again", fake_pve, "ssh", {"proxmox_pool": "agent-warehouse"}))
    assert "no member of pool" not in again, again


def test_with_a_pool_a_token_holding_nothing_on_it_is_told_the_grant_to_make(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/privileges", token="ansible", path="/pool/agent-warehouse", privs="")
    report = _check(tmp_path / "run", fake_pve, "ssh", {"proxmox_pool": "agent-warehouse"})
    assert report["rc"] != 0
    said = " ".join(_said(report).split())
    assert "the token holds no privilege on /pool/agent-warehouse at all" in said
    assert "tools/proxmox-access.sh --pool agent-warehouse" in said
    assert "pveum aclmod /pool/agent-warehouse -user ansible@pve -role AnsibleVM" in said


def test_a_pool_that_is_no_pool_s_name_is_said_and_the_token_not_asked_about_it(tmp_path: Path, fake_pve: str) -> None:
    report = _check(tmp_path / "run", fake_pve, "ssh", {"proxmox_pool": "a pool"})
    assert report["rc"] != 0
    said = " ".join(_said(report).split())
    assert "proxmox_pool is the name of a pool on the Proxmox host" in said and 'after the first, not "a pool"' in said
    assert "the token was not asked about it" in said


def test_a_token_with_no_privileges_is_told_about_privilege_separation(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/privileges", token="ansible", path="/vms", privs="")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh")
    assert report["rc"] != 0
    assert "has none of its user's: make it with --privsep 0" in _said(report)
    assert "pveum aclmod /vms -token 'ansible@pve!ansible'" in _said(report)


def test_every_check_passes_on_proxmox_ve_9_and_just_deploy_waits_for_the_username(
    tmp_path: Path, fake_pve: str
) -> None:
    # Every check passes, but the username is the example's: init_settings.yml's
    # NEXT STEPS names it, and just deploy is not next yet. And vm_password
    # empty, as vault.yml.example had it, sets nothing and is no problem.
    _pve(fake_pve, "PUT", "fake/version", version="9.2.13")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", {"vault_github_username": "your-github-username", "vm_password": ""})
    assert report["rc"] == 0, _failures(report)
    said = _said(report)
    assert "ok   Proxmox VE 9.2.13" in said and "!!" not in said
    assert "vm_password is set" not in said
    assert "just deploy alpha" not in said
    assert _init_next(report) == {"init_check": ""}


def test_a_plaintext_vault_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", vault="vault_proxmox_api_token_secret: x\n")
    assert report["rc"] != 0
    assert "!!   vault.yml is not encrypted, and every recipe refuses it: just vault encrypts it" in _said(report)
    assert _init_next(report) == {
        "init_check": "just init found 1 problem for just deploy: fix it, then run just init again.\n"
        "  !!   vault.yml is not encrypted, and every recipe refuses it: just vault encrypts it"
    }


# The token's secret nowhere in the output or the log: test_without_the_template_root_ssh_is_checked_and_reads_the_node.


# ------------------------------------------------- the node, storage and bridge ---
def _local(root: Path) -> str:
    return (root / "group_vars" / "all" / "local.yml").read_text()


# Every default fitting, nothing written: test_every_check_passes_on_a_host_with_its_template_and_every_default_fits.


@pytest.mark.parametrize("terminal", [True, False], ids=["terminal", "no-terminal"])
def test_the_templates_node_is_written_in_place_of_the_default(tmp_path: Path, fake_pve: str, terminal: bool) -> None:
    _pve(fake_pve, "PUT", "fake/nodes/pve2", status="online")
    _pve(fake_pve, "PUT", "fake/vm-node", vmid="9000", node="pve2")
    _pve(fake_pve, "PUT", "fake/storages", node="pve2", storages="local-lvm:lvmthin")
    _pve(fake_pve, "PUT", "fake/bridges", node="pve2", bridges="vmbr0")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", extra={"init_ask": True} if terminal else None)
    said = _said(report)
    if terminal:
        assert report["rc"] == 0, _failures(report)
        assert "ok   node pve2: written into local.yml as proxmox_node, since the template is on pve2" in said
        assert '\nproxmox_node: "pve2"\n' in _local(root) and "# proxmox_node: pve\n" not in _local(root)
        assert "ok   storage local-lvm: the token can use it" in said
    else:
        assert report["rc"] != 0
        assert (
            "!!   the template is on pve2, but proxmox_node is pve" in said
            and "(just init at a terminal writes it)" in said
        )
        assert "# proxmox_node: pve\n" in _local(root)


def test_without_a_template_two_nodes_take_the_node_ssh_reaches(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/nodes/pve2", status="online")
    _pve(fake_pve, "PUT", "fake/storages", node="pve2", storages="local-lvm:lvmthin")
    _pve(fake_pve, "PUT", "fake/bridges", node="pve2", bridges="vmbr0")
    root = tmp_path / "run"
    _inventory_of(root, fake_pve, "local", NO_TEMPLATE, VAULT_HEADER, "127.0.0.1")
    (root / "bin" / "hostname-is").write_text("pve2\n")
    report = _check(root, fake_pve, "local", extra={"init_ask": True})
    assert report["rc"] == 0, _failures(report)
    assert "ok   node pve2: written into local.yml as proxmox_node, since 127.0.0.1 is node pve2" in _said(report)
    assert '\nproxmox_node: "pve2"\n' in _local(root)


def test_two_nodes_and_no_ssh_cannot_tell_the_node_and_nothing_is_asked_for_storage(
    tmp_path: Path, fake_pve: str
) -> None:
    # And while the node is unknown, a storage the default is not is neither
    # asked about nor written.
    _pve(fake_pve, "PUT", "fake/nodes/pve2", status="online")
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-zfs:zfspool")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", NO_TEMPLATE, extra={"init_ask": True})
    assert report["rc"] != 0
    said = _said(report)
    assert "!!   which node 127.0.0.1 is cannot be told until root SSH to it works" in said
    assert "proxmox_node:" not in _local(root).replace("# proxmox_node:", "")
    assert "--   the storage is not checked: the node is not known yet" in said
    assert "--   the bridge is not checked: the node is not known yet" in said
    assert "\nproxmox_storage:" not in _local(root)
    asked = [
        name for name, _, result in _results(report) if name.startswith("init_ask | Ask") and not result.get("skipped")
    ]
    assert asked == []


def test_a_node_named_otherwise_than_proxmox_names_one_is_never_written(tmp_path: Path, fake_pve: str) -> None:
    # local.yml is read as a template by every later run: a name the API's and root SSH's answers chose, {{ }} in
    # it, would run here, and with the certificate not checked and the host key taken on first contact, those
    # answers are whoever's sits in the path. The node root SSH names, when the API lists it, is written unasked.
    _pve(fake_pve, "PUT", "fake/nodes/%7B%7B%207%20*%207%20%7D%7D", status="online")
    root = tmp_path / "run"
    _inventory_of(root, fake_pve, "local", NO_TEMPLATE, VAULT_HEADER, "127.0.0.1")
    (root / "bin" / "hostname-is").write_text("{{ 7 * 7 }}\n")
    report = _check(root, fake_pve, "local", extra={"init_ask": True})
    assert report["rc"] != 0
    said = _said(report)
    assert '!!   the host reported a node named "{{ 7 * 7 }}", which is not a name Proxmox gives a node' in said
    assert "7 * 7" not in _local(root) and "proxmox_node:" not in _local(root).replace("# proxmox_node:", "")


def test_a_storage_named_otherwise_than_proxmox_names_one_is_shown_and_never_offered(
    tmp_path: Path, fake_pve: str
) -> None:
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-zfs:zfspool,{{7*7}}:dir")
    root = tmp_path / "run"
    rc, output, _ = _check_at_a_terminal(root, fake_pve, "ssh", None, [(b"Use local-zfs? y or n", b"y\n")])
    assert rc == 0, output
    assert '-  "{{7*7}}"' in output and "not a name Proxmox gives one, so never used" in output
    assert '\nproxmox_storage: "local-zfs"\n' in _local(root) and "7*7" not in _local(root)


def test_a_storage_is_chosen_among_those_the_token_can_use(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-zfs:zfspool,tank-vms:nfs,local:dir")
    _pve(fake_pve, "PUT", "fake/privileges", token="ansible", path="/storage/local", privs="Datastore.Audit")
    root = tmp_path / "run"
    rc, output, _ = _check_at_a_terminal(root, fake_pve, "ssh", None, [(b"Number, Enter to decide later:", b"2\n")])
    assert rc == 0, output
    assert "1) local-zfs" in output and "2) tank-vms" in output
    assert "-  local       dir       the token lacks Datastore.AllocateSpace on /storage/local" in output
    assert '\nproxmox_storage: "tank-vms"\n' in _local(root) and "# proxmox_storage: local-lvm\n" not in _local(root)
    assert "storage tank-vms: written into local.yml as proxmox_storage" in output


def test_one_usable_storage_is_offered_yes_or_no(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-zfs:zfspool")
    root = tmp_path / "run"
    rc, output, _ = _check_at_a_terminal(root, fake_pve, "ssh", None, [(b"Use local-zfs? y or n", b"y\n")])
    assert rc == 0, output
    assert '\nproxmox_storage: "local-zfs"\n' in _local(root)


def test_enter_at_the_storage_leaves_it_and_says_so(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-zfs:zfspool")
    root = tmp_path / "run"
    rc, output, _ = _check_at_a_terminal(root, fake_pve, "ssh", None, [(b"Use local-zfs? y or n", b"\n")])
    assert rc != 0
    assert "storage local-lvm is not on pve: set proxmox_storage in local.yml" in output
    assert "\nproxmox_storage:" not in _local(root)


def test_without_a_terminal_a_storage_that_does_not_fit_is_reported(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-zfs:zfspool,tank-vms:nfs")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh")
    assert report["rc"] != 0
    said = _said(report)
    assert "!!   storage local-lvm is not on pve: set proxmox_storage in local.yml to one the token can use" in said
    assert "- local-zfs, tank-vms - or run just init at a terminal" in said
    assert "\nproxmox_storage:" not in _local(root)


def test_a_storage_set_that_is_not_listed_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", {"proxmox_storage": "gone"}, extra={"init_ask": True})
    assert report["rc"] != 0
    assert "!!   proxmox_storage is gone in local.yml, which the token cannot see on pve: local-lvm instead" in _said(
        report
    )


def test_the_templates_bridge_without_sdn_use_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/privileges", token="ansible", path="/sdn/zones/localnetwork/vmbr0", privs="SDN.Audit")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", extra={"init_ask": True})
    assert report["rc"] != 0
    assert "!!   bridge vmbr0, the template's, which every clone inherits: the token lacks SDN.Use" in _said(report)


def test_without_a_template_a_bridge_is_offered_and_one_it_cannot_use_shown(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/bridges", node="pve", bridges="vmbr1,vmbr2")
    _pve(fake_pve, "PUT", "fake/privileges", token="ansible", path="/sdn/zones/localnetwork/vmbr2", privs="SDN.Audit")
    root = tmp_path / "run"
    rc, output, _ = _check_at_a_terminal(root, fake_pve, "local", NO_TEMPLATE, [(b"Use vmbr1? y or n", b"y\n")])
    assert rc == 0, output
    assert "the token lacks SDN.Use on /sdn/zones/localnetwork/vmbr2" in output
    assert '\nproxmox_bridge: "vmbr1"\n' in _local(root)


def test_a_storage_given_is_written_even_where_the_default_fits(tmp_path: Path, fake_pve: str) -> None:
    # With a bridge given equal to the template's, which is nothing to do: a scripted just init that gave --bridge
    # runs again once deploy built the template. The bridge is checked before the storage is written.
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", extra={"init_given": {"storage": "local-lvm", "bridge": "vmbr0"}})
    assert report["rc"] == 0, _failures(report)
    assert '\nproxmox_storage: "local-lvm"\n' in _local(root)
    assert "\nproxmox_bridge:" not in _local(root)


def test_a_storage_given_that_will_not_do_fails_and_writes_nothing(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-lvm:lvmthin,local:dir")
    _pve(fake_pve, "PUT", "fake/privileges", token="ansible", path="/storage/local", privs="Datastore.Audit")
    root = tmp_path / "run"
    for storage, said in (("local", "--storage local: the token lacks Datastore.AllocateSpace on /storage/local"),
                          ("nfs", "--storage nfs: the token cannot see it on pve (local-lvm, local)")):  # fmt: skip
        report = _check(root, fake_pve, "ssh", extra={"init_given": {"storage": storage}})
        assert report["rc"] != 0
        assert said in "\n".join(_failures(report).values())
        assert "\nproxmox_storage:" not in _local(root)


def test_a_storage_given_that_differs_from_the_one_set_fails(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-lvm:lvmthin,local-zfs:zfspool")
    root = tmp_path / "run"
    report = _check(
        root, fake_pve, "ssh", {"proxmox_storage": "local-lvm"}, extra={"init_given": {"storage": "local-zfs"}}
    )
    assert report["rc"] != 0
    assert "already sets proxmox_storage: local-lvm" in "\n".join(_failures(report).values())


def test_a_bridge_given_while_the_template_exists_is_refused(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", extra={"init_given": {"bridge": "vmbr1", "storage": "local-lvm"}})
    assert report["rc"] != 0
    assert "--bridge vmbr1: the template ubuntu-24.04-cloudinit is built" in "\n".join(_failures(report).values())
    # Refused before anything was written: the storage given too.
    assert "\nproxmox_storage:" not in _local(root)


# A bridge given equal to the template's is nothing to do: test_a_storage_given_is_written_even_where_the_default_fits.


# Nothing asked or written for storage while the node is unknown:
# test_two_nodes_and_no_ssh_cannot_tell_the_node_and_nothing_is_asked_for_storage.


def test_a_storage_given_while_the_node_is_unknown_is_refused(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/nodes/pve2", status="online")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", NO_TEMPLATE, extra={"init_given": {"storage": "local-lvm"}})
    assert report["rc"] != 0
    assert "--storage local-lvm cannot be checked yet (the node is not known yet)" in "\n".join(
        _failures(report).values()
    )
    assert "\nproxmox_storage:" not in _local(root)


def test_a_token_that_sees_no_storage_is_told_what_lists_one(tmp_path: Path, fake_pve: str) -> None:
    # The common ZFS case: the role granted on /storage/local-lvm, which the host does not have.
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-zfs:zfspool")
    _pve(fake_pve, "PUT", "fake/privileges", token="ansible", path="/storage/local-zfs", privs="")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh")
    assert report["rc"] != 0
    said = _said(report)
    assert "Proxmox lists one only to a token that holds Datastore.Audit or Datastore.AllocateSpace on it" in said
    assert "pveum aclmod /storage/<yours> -user ansible@pve -role AnsibleVM" in said


def test_an_inactive_storage_is_not_used(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="nas:nfs:inactive,local-zfs:zfspool")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", {"proxmox_storage": "nas"})
    assert report["rc"] != 0
    assert "!!   storage nas is inactive on pve" in _said(report)


def test_a_template_with_no_bridge_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "nodes/pve/qemu/9000/config", delete="net0")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh")
    assert report["rc"] != 0
    assert "!!   the template's net0 has no bridge" in _said(report)


def test_no_host_in_the_proxmox_group_is_said_not_a_crash(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", NO_TEMPLATE | {"proxmox_api_host": "127.0.0.1"}, proxmox_host=False)
    assert report["rc"] != 0
    assert "which has none" in _said(report)


# ------------------------------------------------------------------ NEXT STEPS ---
def test_next_steps_keep_each_problem_with_its_fix_and_no_deploy(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    keyless = {"vm_ssh_public_keys": [], "vault_vm_password": "", "vm_password": "an-old-console-password"}
    report = _check(root, fake_pve, "ssh", keyless, vault="vault_proxmox_api_token_secret: x\n")
    assert report["rc"] != 0
    line = _init_next(report)["init_check"]
    said = _said(report)
    problems = [text for text in said.splitlines() if text.startswith("  !!   ")]
    assert len(problems) == 3, problems
    assert line == "\n".join(
        ["just init found 3 problems for just deploy: fix them, then run just init again.", *problems]
    )
    assert "ssh-keygen -t ed25519" in line and "just deploy alpha" not in line + said
    # The password under its old name is named, and never said.
    assert "!!   vm_password is set, which is vault_vm_password from 1.0" in said
    assert "an-old-console-password" not in json.dumps(report)


# Nothing while the GitHub username is the example's:
# test_every_check_passes_on_proxmox_ve_9_and_just_deploy_waits_for_the_username.


def test_next_steps_say_what_could_not_be_checked_when_nothing_is_wrong(tmp_path: Path, fake_pve: str) -> None:
    # The template on a node just gone down, still listed by name: its storage
    # and bridge cannot be read, nothing is wrong yet, and just deploy is not next.
    _pve(fake_pve, "PUT", "fake/vm-node", vmid="9000", node="pve2")
    _pve(fake_pve, "PUT", "fake/nodes/pve2", status="offline", named="1")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", {"proxmox_node": "pve2"})
    assert report["rc"] == 0, _failures(report)
    assert "ok   node pve2: it holds the template" in _said(report) and "just deploy alpha" not in _said(report)
    assert _init_next(report) == {
        "init_check": "just init could not check all that just deploy needs: run it again once it can.\n"
        "  --   the storage is not checked: node pve2 is not up\n"
        "  --   the template's bridge is not checked: node pve2 is not up"
    }


# -------------------------------------------------------------- the certificate ---
# Before the token goes anywhere: what makes the API's certificate verify, kept beside the inventory (here the test's
# own folder, where proxmox.yml is) in proxmox-trust.pem with proxmox_validate_certs on; a certificate that changed put
# in its place only on a yes or the fingerprint given; and nothing that carries the token where what answers is not
# the host.
KEPT = "inventory/proxmox-trust.pem"


def _pki_pve(tmp_path: Path, **made):
    """The fake Proxmox serving a node's certificate under a CA made as Proxmox makes one (pki.py's ``made``): its
    base URL, and the certificates as the host keeps them."""
    pki = proxmox_pki(tmp_path / "pki", **made)
    for base in _fake_pve(tmp_path, (pki["cert"], pki["key"])):
        yield base, pki


@pytest.fixture
def pve_8(tmp_path):
    """Its CA has no keyUsage, as every Proxmox VE 8 host's."""
    yield from _pki_pve(tmp_path)


@pytest.fixture
def pve_9(tmp_path):
    """Its CA has keyUsage, as a Proxmox VE 9 host's from pve-cluster 9.1.2."""
    yield from _pki_pve(tmp_path, key_usage=True)


def _host_files(pki: dict) -> dict:
    return {"ca": pki["ca"], "node": pki["node"]}


TRUST_READS = (
    "Look for the host's certificates over root SSH",
    "Read Proxmox's CA over root SSH",
    "Read the certificate the node serves over root SSH",
)


def _trust_read(report: dict) -> list[str]:
    """The reads of the host's certificates a run made, by task name, once each."""
    return list(
        dict.fromkeys(name for name, _, result in _results(report) if name in TRUST_READS and not result.get("skipped"))
    )


def _local_settings(root: Path) -> dict:
    return yaml.safe_load((root / "group_vars" / "all" / "local.yml").read_text()) or {}


def _token_sent(directory: Path) -> bool:
    """Whether the fake Proxmox in directory was sent any request that carried a token."""
    return any(call.get("token") for call in _pve_requests(directory))


def test_the_certificate_that_verifies_is_kept_and_checked_from_then_on(tmp_path: Path, pve_8) -> None:
    base, pki = pve_8
    root = tmp_path / "run"
    report = _check(root, base, "local", extra={"init_ask": True}, trust=_host_files(pki))
    assert report["rc"] == 0, _failures(report)
    # Where Python is strict, Proxmox VE 8's CA is refused and the node's own certificate is the one that verifies.
    expected, kind = (pki["node"], "the node's own certificate") if STRICT else (pki["ca"], "its CA")
    assert (root / "proxmox-trust.pem").read_text() == expected
    # And the setting on, so that a copy of the checkout without the file refuses rather than sends the token.
    assert _local_settings(root)["proxmox_validate_certs"] is True
    said = _said(report)
    assert (
        f"ok   Proxmox's certificate verifies against {kind} (SHA-256 {trust.pem_fingerprint(expected)}), read over"
        f" root SSH and kept in {KEPT} with proxmox_validate_certs on: every run checks it"
    ) in said
    assert f"ok   Proxmox VE 8.2.4 at 127.0.0.1:{_port(base)} accepted ansible@pve!ansible" in said
    assert "note" not in said and _init_next(report) == {"init_check": DEPLOY_NEXT}
    # Checked from then on: the next run finds it verifies, and reads nothing over root SSH.
    again = _check(root, base, "local", extra={"init_ask": True}, trust=_host_files(pki))
    assert again["rc"] == 0, _failures(again)
    assert f"ok   Proxmox's certificate verifies against {KEPT}: every run checks it" in _said(again)
    assert _trust_read(again) == []


def test_another_host_s_certificate_is_kept_in_that_host_s_own_folder(tmp_path: Path, pve_9) -> None:
    # just init --host nuc: the host is hosts/nuc/hosts.yml laid over the shared inventory, and its file goes beside
    # it, said there - never into the folder of the first host's, whose runs would then check nuc's certificate.
    base, pki = pve_9
    root = tmp_path / "run"
    hosts_file = root / "hosts" / "nuc" / "hosts.yml"
    extra = {"init_ask": True, "init_host": HOST | {"hosts_said": "hosts/nuc/hosts.yml"}}
    report = _check(root, base, "local", extra=extra, trust=_host_files(pki), hosts_file=hosts_file)
    assert report["rc"] == 0, _failures(report)
    assert (hosts_file.parent / "proxmox-trust.pem").read_text() == pki["ca"]
    assert not (root / "proxmox-trust.pem").exists()
    assert "read over root SSH and kept in hosts/nuc/proxmox-trust.pem with proxmox_validate_certs on" in _said(report)


def test_a_ca_with_key_usage_is_the_one_kept(tmp_path: Path, pve_9) -> None:
    base, pki = pve_9
    root = tmp_path / "run"
    report = _check(root, base, "local", extra={"init_ask": True}, trust=_host_files(pki))
    assert report["rc"] == 0, _failures(report)
    assert (root / "proxmox-trust.pem").read_text() == pki["ca"]
    assert "ok   Proxmox's certificate verifies against its CA (SHA-256 " in _said(report)


def test_a_custom_certificate_is_read_in_place_of_the_one_proxmox_made_and_kept_without_its_chain(
    tmp_path: Path, pve_8
) -> None:
    # pveproxy serves pveproxy-ssl.pem where there is one, the one Proxmox made then no anchor of it; and a chain served
    # with it is left out of what is kept, since each certificate of a trust file is an anchor of its own.
    base, pki = pve_8
    other = proxmox_pki(tmp_path / "other")
    root = tmp_path / "run"
    files = {"ca": other["ca"], "node": other["node"], "custom": pki["node"] + other["ca"]}
    report = _check(root, base, "local", extra={"init_ask": True}, trust=files)
    assert report["rc"] == 0, _failures(report)
    if STRICT:
        assert (root / "proxmox-trust.pem").read_text() == pki["node"]
    else:
        # Python 3.12 takes no certificate but a CA's as an anchor, and the custom certificate's CA is not the host's:
        # what it presents is still the host's own, so it is said as a name or a chain to fix, and nothing is held.
        assert not (root / "proxmox-trust.pem").exists()
        assert "note Proxmox's certificate is not checked: what https://" in _said(report)


def test_without_a_terminal_the_certificate_is_said_and_nothing_written(tmp_path: Path, pve_9) -> None:
    base, pki = pve_9
    root = tmp_path / "run"
    report = _check(root, base, "local", trust=_host_files(pki))
    assert report["rc"] == 0, _failures(report)
    assert not (root / "proxmox-trust.pem").exists() and "proxmox_validate_certs" not in _local_settings(root)
    fingerprint = trust.pem_fingerprint(pki["ca"])
    note = (
        f"Proxmox's certificate verifies against its CA (SHA-256 {fingerprint}), read over root SSH, and this run"
        f" checked it, but nothing keeps it for the next: just init at a terminal keeps it in {KEPT}, and so does"
        f" just init --certificate {fingerprint}"
    )
    assert f"note {note}" in _said(report)
    # Nothing kept, but the token never goes out unchecked with a certificate that verifies in hand: the rest of the
    # run is checked against a copy of its own, removed as it ends.
    run_copy = root / "cache" / "init" / "proxmox-trust.pem"
    facts = {name: result.get("ansible_facts") for name, _, result in _results(report)}
    assert facts["Check it against the run's copy"] == {"proxmox_api_ca_path": str(run_copy)}
    assert facts["Check the API's certificate for the rest of the run"] == {"proxmox_validate_certs": True}
    assert f"ok   Proxmox VE 8.2.4 at 127.0.0.1:{_port(base)} accepted ansible@pve!ansible" in _said(report)
    assert not run_copy.exists()
    # A note fails nothing, and is kept for NEXT STEPS whatever else is.
    assert _init_next(report) == {"init_check": f"{DEPLOY_NEXT}\n  note {note}"}


def test_the_fingerprint_given_keeps_the_certificate_with_no_terminal_and_another_keeps_nothing(
    tmp_path: Path, pve_9
) -> None:
    base, pki = pve_9
    fingerprint = trust.pem_fingerprint(pki["ca"])
    wrong = ":".join(["AB"] * 32)
    root = tmp_path / "wrong"
    report = _check(root, base, "local", extra={"init_given": {"certificate": wrong}}, trust=_host_files(pki))
    assert report["rc"] == 0, _failures(report)
    assert not (root / "proxmox-trust.pem").exists()
    assert f"(--certificate {wrong} is not it, so nothing was kept)" in _said(report)
    root = tmp_path / "given"
    # As typed, in lowercase: a fingerprint is compared as openssl prints it.
    report = _check(
        root, base, "local", extra={"init_given": {"certificate": fingerprint.lower()}}, trust=_host_files(pki)
    )
    assert report["rc"] == 0, _failures(report)
    assert (root / "proxmox-trust.pem").read_text() == pki["ca"]
    assert _local_settings(root)["proxmox_validate_certs"] is True


def test_a_certificate_that_is_the_host_s_under_another_name_is_said_and_checking_left_off(tmp_path: Path) -> None:
    pki = proxmox_pki(tmp_path / "pki", key_usage=True, address="127.0.0.2")
    root = tmp_path / "run"
    for base in _fake_pve(tmp_path, (pki["cert"], pki["key"])):
        report = _check(root, base, "local", extra={"init_ask": True}, trust=_host_files(pki))
    assert report["rc"] == 0, _failures(report)
    assert not (root / "proxmox-trust.pem").exists()
    said = _said(report)
    assert (
        f"note Proxmox's certificate is not checked: what https://127.0.0.1:{_port(base)} presents is the host's own"
        f" (SHA-256 {trust.pem_fingerprint(pki['node'])}), but nothing makes it verify there (this machine's trust"
        " store: "
    ) in said
    assert "; its CA: " in said and "Usually the certificate does not name the address in inventory/hosts.yml" in said
    # The host itself, as root SSH says: the token goes to it, unchecked as before.
    assert f"ok   Proxmox VE 8.2.4 at 127.0.0.1:{_port(base)} accepted ansible@pve!ansible" in said


def test_what_answers_with_none_of_the_host_s_certificates_is_never_sent_the_token(tmp_path: Path) -> None:
    # D1's own case: root SSH reaches the host and reads its certificates, while something else answers on the API's
    # port, with a certificate of its own.
    host, impostor = proxmox_pki(tmp_path / "host", key_usage=True), proxmox_pki(tmp_path / "impostor", key_usage=True)
    root = tmp_path / "run"
    for base in _fake_pve(tmp_path, (impostor["cert"], impostor["key"])):
        report = _check(root, base, "local", extra={"init_ask": True}, trust=_host_files(host))
    assert report["rc"] != 0
    said = _said(report)
    presented = trust.pem_fingerprint(impostor["node"])
    assert (
        f"!!   https://127.0.0.1:{_port(base)} presents a certificate (SHA-256 {presented})"
        " that is none of the host's, as root SSH to 127.0.0.1 reads them, so something other than the Proxmox host"
        " answers there, and the token was not sent"
    ) in said
    assert "--   the API is not asked, and nothing that needs it is checked" in said
    assert not _token_sent(tmp_path) and not (root / "proxmox-trust.pem").exists()


def test_what_says_nothing_to_a_request_without_a_token_is_never_sent_the_token(tmp_path: Path) -> None:
    # Something in the host's place sees which request carries no token: it says nothing to every probe, waiting for
    # the request that carries one. Its certificate alone decides.
    host, impostor = proxmox_pki(tmp_path / "host", key_usage=True), proxmox_pki(tmp_path / "impostor", key_usage=True)
    root = tmp_path / "run"
    for base in _fake_pve(tmp_path, (impostor["cert"], impostor["key"])):
        _pve(base, "PUT", "fake/tokenless", answer="none")
        before = len(_pve_requests(tmp_path))
        report = _check(root, base, "local", extra={"init_ask": True}, trust=_host_files(host))
    assert report["rc"] != 0
    said = _said(report)
    presented = trust.pem_fingerprint(impostor["node"])
    assert (
        f"!!   https://127.0.0.1:{_port(base)} presents a certificate (SHA-256 {presented})"
        " that is none of the host's, as root SSH to 127.0.0.1 reads them"
    ) in said
    assert "--   the API is not asked, and nothing that needs it is checked" in said
    assert not [call for call in _pve_requests(tmp_path)[before:] if call.get("token")]
    assert not (root / "proxmox-trust.pem").exists()


def test_no_certificate_read_where_root_ssh_read_the_host_s_is_never_sent_the_token(
    tmp_path: Path, fake_pve: str
) -> None:
    # Nothing to read at the address, while root SSH reads the host's: nothing shows that the host answers there,
    # whatever the reason - nothing listening, or something that finishes no handshake but the token's.
    pki = proxmox_pki(tmp_path / "pki", key_usage=True)
    root = tmp_path / "run"
    closed = _free_port()
    report = _check(
        root, fake_pve, "local", {"proxmox_api_port": closed}, extra={"init_ask": True}, trust=_host_files(pki)
    )
    assert report["rc"] != 0
    said = _said(report)
    assert f"!!   https://127.0.0.1:{closed} presented no certificate to read (" in said
    assert "so nothing shows that the Proxmox host answers there, and the token was not sent" in said
    assert "--   the API is not asked, and nothing that needs it is checked" in said
    assert not _token_sent(tmp_path)


def test_without_root_ssh_the_way_to_copy_it_by_hand_is_said(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", extra={"init_ask": True}, trust={})
    assert report["rc"] == 0, _failures(report)
    said = _said(report)
    assert "note Proxmox's certificate is not checked" in said and "just init could not read it over root SSH (" in said
    assert ": install your key with ssh-copy-id root@127.0.0.1 and run just init again, or copy it by hand" in said
    # Root SSH tried, and nothing read once the host is unreachable.
    assert _ssh_calls(root)
    assert _trust_read(report) == ["Look for the host's certificates over root SSH"]


def test_without_a_proxmox_host_the_way_to_copy_it_by_hand_is_said(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", {"proxmox_api_host": "127.0.0.1"}, proxmox_host=False, trust={})
    assert report["rc"] == 0, _failures(report)
    said = _said(report)
    assert "note Proxmox's certificate is not checked" in said
    assert "inventory/hosts.yml names no Proxmox host to read it from over root SSH: copy it by hand" in said
    assert _trust_read(report) == [] and _ssh_calls(root) == ""


def test_a_certificate_the_run_s_clients_trust_turns_the_setting_on_and_writes_no_file(tmp_path: Path, pve_9) -> None:
    # A certificate from a public CA, as ACME's: the bundle every client of the run is handed - here the CA, as
    # REQUESTS_CA_BUNDLE, which uri is given as its ca_path and requests reads itself - verifies it, and
    # proxmox_validate_certs is turned on in local.yml instead.
    base, pki = pve_9
    store = tmp_path / "store.pem"
    store.write_text(pki["ca"])
    root = tmp_path / "run"
    env = {"REQUESTS_CA_BUNDLE": str(store)}
    report = _check(root, base, "local", extra={"init_ask": True}, trust={}, env=env)
    assert report["rc"] == 0, _failures(report)
    assert not (root / "proxmox-trust.pem").exists()
    assert _local_settings(root)["proxmox_validate_certs"] is True
    said = _said(report)
    assert (
        "ok   Proxmox's certificate verifies against this machine's trust store, and proxmox_validate_certs is on"
        in said
    )
    # Checked for the rest of the run too, which read local.yml before the write.
    assert f"ok   Proxmox VE 8.2.4 at 127.0.0.1:{_port(base)} accepted ansible@pve!ansible" in said
    again = _check(root, base, "local", extra={"init_ask": True}, trust={}, env=env)
    assert again["rc"] == 0, _failures(again)
    assert "ok   Proxmox's certificate verifies against this machine's trust store: every run checks it" in _said(again)
    assert _local_settings(root)["proxmox_validate_certs"] is True
    assert (root / "group_vars" / "all" / "local.yml").read_text().count("proxmox_validate_certs:") == 1


def test_a_store_only_the_standard_library_trusts_is_not_taken_for_one_the_modules_trust(tmp_path: Path, pve_9) -> None:
    # SSL_CERT_FILE reaches the standard library's store, which uri uses, and not certifi's, which every
    # community.proxmox module verifies against: taken for this machine's store, the first deploy would fail.
    base, pki = pve_9
    store = tmp_path / "store.pem"
    store.write_text(pki["ca"])
    root = tmp_path / "run"
    report = _check(root, base, "local", extra={"init_ask": True}, trust={}, env={"SSL_CERT_FILE": str(store)})
    assert report["rc"] == 0, _failures(report)
    assert "proxmox_validate_certs" not in _local_settings(root)
    assert "this machine's trust store, and proxmox_validate_certs is on" not in _said(report)


def test_a_certificate_that_changed_is_never_trusted_in_its_place_unasked_and_the_token_held(tmp_path: Path) -> None:
    first, second = proxmox_pki(tmp_path / "first", key_usage=True), proxmox_pki(tmp_path / "second", key_usage=True)
    root = tmp_path / "run"
    root.mkdir()
    (root / "proxmox-trust.pem").write_text(first["ca"])
    for base in _fake_pve(tmp_path, (second["cert"], second["key"])):
        report = _check(root, base, "local", extra={"init_ask": True}, trust=_host_files(second))
    assert report["rc"] != 0
    said = _said(report)
    new = trust.pem_fingerprint(second["ca"])
    assert (
        f"!!   Proxmox's certificate no longer verifies against {KEPT} (SHA-256 {trust.pem_fingerprint(first['ca'])})"
        in said
        and f"and its CA, read over root SSH, does (SHA-256 {new}), so the token was not sent: just init at a terminal"
        f" asks whether to trust it in its place, and just init --certificate {new} puts it there"
        in said
    ), said
    # Never turned off, never replaced unasked, and nothing sent with the token to what answers.
    assert "validate_certs: false" not in said and "set it back to false" not in said
    assert (root / "proxmox-trust.pem").read_text() == first["ca"] and not _token_sent(tmp_path)


def test_a_certificate_that_changed_is_put_in_place_for_its_fingerprint_given(tmp_path: Path) -> None:
    first, second = proxmox_pki(tmp_path / "first", key_usage=True), proxmox_pki(tmp_path / "second", key_usage=True)
    root = tmp_path / "run"
    root.mkdir()
    (root / "proxmox-trust.pem").write_text(first["ca"])
    given = {"init_given": {"certificate": trust.pem_fingerprint(second["ca"])}}
    for base in _fake_pve(tmp_path, (second["cert"], second["key"])):
        report = _check(root, base, "local", extra=given, trust=_host_files(second))
    assert report["rc"] == 0, _failures(report)
    assert (root / "proxmox-trust.pem").read_text() == second["ca"]
    said = _said(report)
    assert f"ok   Proxmox's certificate no longer verified against {KEPT} (SHA-256 " in said
    assert f"ok   Proxmox VE 8.2.4 at 127.0.0.1:{_port(base)} accepted ansible@pve!ansible" in said


def test_a_certificate_that_changed_is_put_in_place_on_a_yes_that_showed_both_fingerprints(tmp_path: Path) -> None:
    first, second = proxmox_pki(tmp_path / "first", key_usage=True), proxmox_pki(tmp_path / "second", key_usage=True)
    root = tmp_path / "run"
    root.mkdir()
    (root / "proxmox-trust.pem").write_text(first["ca"])
    for base in _fake_pve(tmp_path, (second["cert"], second["key"])):
        rc, output, _ = _check_at_a_terminal(
            root, base, "local", None, [(b"Trust it in its place? y or n", b"y\n")], trust=_host_files(second)
        )
    assert rc == 0, output
    assert trust.pem_fingerprint(first["ca"]) in output and trust.pem_fingerprint(second["ca"]) in output
    assert (root / "proxmox-trust.pem").read_text() == second["ca"]


# ------------------------------------------------------------------- static ---
READ_ONLY = {
    "vm_route",
    "ansible.builtin.uri",
    "ansible.builtin.command",
    "ansible.builtin.find",
    "ansible.builtin.slurp",
    "ansible.builtin.stat",
    "ansible.builtin.set_fact",
    "ansible.builtin.set_stats",
    "ansible.builtin.debug",
    "ansible.builtin.assert",
    "ansible.builtin.fail",
    "ansible.builtin.import_tasks",
    "ansible.builtin.include_tasks",
    "ansible.builtin.include_role",
    "ansible.builtin.meta",
}
# The one role entry point included: the template's comparison, which only records what differs.
ROLE_ENTRY_POINTS = {("proxmox_template", "drift")}
TASK_KEYS = {
    "name", "when", "register", "vars", "no_log", "failed_when", "changed_when", "check_mode", "delegate_to",
    "ignore_unreachable", "loop", "loop_control", "until", "retries", "delay", "tags",
}  # fmt: skip
CHOOSE = REPO / "playbooks" / "tasks" / "init_choose.yml"
INCLUDED = {"tasks/cluster_vms.yml", "tasks/init_choose.yml", "tasks/init_local_setting.yml", "init_ask.yml",
            "init_local_setting.yml", "tasks/init_ask.yml"}  # fmt: skip


RUN_COPY = {
    "Make the folder the run's copy of the certificate goes in": {
        "path": "{{ init_trust_run_file | dirname }}",
        "state": "directory",
        "mode": "0700",
    },
    "Copy the certificate that verifies for this run alone": {
        "content": "{{ init_trust.pem }}",
        "dest": "{{ init_trust_run_file }}",
        "mode": "0600",
    },
    "Remove the run's copy of the certificate": {"path": "{{ init_trust_run_file }}", "state": "absent"},
}


def test_every_task_is_a_read_but_the_settings_written():
    # A task reads, records for NEXT STEPS, or includes the cluster listing, a
    # choice, a question or local.yml's writer - the one thing that writes, a
    # setting at a time.
    files = (PLAYBOOK, CLUSTER_VMS, CHOOSE)
    tasks = [task for path in files for task in _every_task(yaml.safe_load(path.read_text()))]
    for task in tasks:
        if "block" in task:
            assert set(task) <= TASK_KEYS | {"block", "rescue", "always"}, task["name"]
            continue
        modules = set(task) - TASK_KEYS
        if (
            len(modules) == 1
            and modules <= {"ansible.builtin.copy", "ansible.builtin.file"}
            and task["name"] in RUN_COPY
        ):
            # The copy of a certificate that verifies but is not kept, which the rest of the run is checked against,
            # its folder and its removal: in a file of the run's own, never the trust file.
            assert task[next(iter(modules))] == RUN_COPY[task["name"]], task["name"]
            assert task["when"] == "init_trust_run_copy | default(false) | bool", task["name"]
            continue
        if modules == {"ansible.builtin.copy"}:
            # The trust file, from the certificate kept: over one there only where it replaces one that no longer
            # verifies, which the run settled on a yes or the fingerprint given (init_trust_keep_file).
            assert task["name"] == "Keep the certificate the API is checked against from now on", task["name"]
            assert task["ansible.builtin.copy"] == {
                "content": "{{ init_trust.pem }}",
                "dest": "{{ proxmox_trust_file }}",
                "mode": "0644",
                "force": "{{ init_trust_replacing | bool }}",
            }
            assert "init_trust_keep_file | bool" in task["when"]
            continue
        assert len(modules) == 1 and modules <= READ_ONLY, (task["name"], modules)
        module = modules.pop()
        if module == "ansible.builtin.uri":
            # A GET, and nothing saved: uri's dest writes a file.
            assert task[module].get("method", "GET") == "GET" and "dest" not in task[module], task["name"]
        if module == "ansible.builtin.command":
            assert task[module] == {"argv": ["hostname", "-s"]}, task["name"]
        if module == "vm_route":
            # The route settled, and the hop's login: an SSH connection that runs nothing. Never a forward.
            assert task[module]["do"] in ("settle", "login"), task["name"]
        if module in ("ansible.builtin.import_tasks", "ansible.builtin.include_tasks"):
            assert task[module] in INCLUDED, task["name"]
        if module == "ansible.builtin.include_role":
            entry = (task[module]["name"], task[module]["tasks_from"])
            assert set(task[module]) == {"name", "tasks_from"} and entry in ROLE_ENTRY_POINTS, task["name"]
            path = REPO / "roles" / entry[0] / "tasks" / f"{entry[1]}.yml"
            assert {next(iter(set(t) - TASK_KEYS)) for t in _every_task(yaml.safe_load(path.read_text()))} == {
                "ansible.builtin.set_fact"
            }, path


def test_a_setting_is_written_only_at_a_terminal_or_as_given():
    # Each writer's condition, as it opens: a terminal (init_ask), or a terminal
    # or a value given; whatever follows is and-ed to it.
    writers = {
        task["name"]: " ".join(str(task["when"]).split())
        for path in (PLAYBOOK, CHOOSE)
        for task in _every_task(yaml.safe_load(path.read_text()))
        if str(task.get("ansible.builtin.include_tasks", "")).endswith("init_local_setting.yml")
        or ("ansible.builtin.copy" in task and task["name"] not in RUN_COPY)
    }
    assert sorted(writers) == [
        "Keep the certificate the API is checked against from now on",
        "Turn checking on in local.yml",
        "Write proxmox_node into local.yml",
        "Write what the host sets for itself into its local.yml",
        "init_choose | Write into local.yml {{ init_choose_setting }}",
    ]
    assert writers["Write proxmox_node into local.yml"].startswith("(init_ask | bool) and ")
    # The certificate's: a terminal, or the fingerprint given (--certificate) being the certificate's that verifies.
    for name in ("Keep the certificate the API is checked against from now on", "Turn checking on in local.yml"):
        assert writers[name].startswith("(init_ask | bool or init_trust_given_matches | default(false) | bool) and "), (
            name
        )
    # Another host's own settings: its name given on the command line, just init --host.
    assert writers["Write what the host sets for itself into its local.yml"].startswith(
        "(init_host_name | length > 0) and "
    )
    assert writers["init_choose | Write into local.yml {{ init_choose_setting }}"].startswith(
        "(init_ask | bool or init_choose_given | length > 0) and "
    )


def test_the_playbook_has_no_duplicate_key(tmp_path):
    # A YAML merge that redefines a key loads with a warning on every just init. In the harness's environment, as
    # every run here: the shell's ANSIBLE_* - just test unit's ANSIBLE_LOG_PATH among them - would log this check
    # into the developer's run log.
    result = subprocess.run(
        [str(Path(sys.executable).with_name("ansible-playbook")), "--syntax-check", str(PLAYBOOK)],
        env=_env(tmp_path, ANSIBLE_DUPLICATE_YAML_DICT_KEY="error"),
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_uri_call_passes_the_ca_and_hides_the_token():
    for task in _every_task(yaml.safe_load(PLAYBOOK.read_text())):
        if "ansible.builtin.uri" in task:
            uri = task["ansible.builtin.uri"]
            assert uri["ca_path"] == "{{ proxmox_api_ca_path | default(omit, true) }}", task["name"]
            assert uri["headers"] == "{{ proxmox_api_headers }}" and task["no_log"] is True, task["name"]


def test_no_run_reaches_a_real_ssh():
    # Every run goes through _check or _check_at_a_terminal, which give the Proxmox host the local connection or
    # put the refusing ssh first on PATH, and point the vault and the key directory into the test's own.
    tree = ast.parse(Path(__file__).read_text())
    callers = set()
    for function in ast.walk(tree):
        if isinstance(function, ast.FunctionDef):
            for call in ast.walk(function):
                if isinstance(call, ast.Call) and getattr(call.func, "id", None) in {"_run", "_run_at_a_terminal"}:
                    callers.add((call.func.id, function.name))
    assert callers == {("_run", "_check"), ("_run_at_a_terminal", "_check_at_a_terminal")}, callers
    assert SSH_STAND_IN.rstrip().endswith("exit 255") and "ssh-calls.log" in SSH_STAND_IN
