"""The route to the VMs through the Proxmox host, as the playbooks take it: playbooks/tasks/vm_route.yml, its check of
each VM published (vm_route_held.yml), and every publisher's add_host (docs/architecture/lifecycle.md §4.15).

Runs discovery against the fake Proxmox with the route through a host whose ssh is a stand-in (test_route.py's), which
plays OpenSSH's control commands and forwards and logs each call: nothing here reaches a real host, and the hop's
socket is in a directory of the test's own.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml
from harness import REPO, _failures, _fake_pve, _pve_requests, _pve_vars, _pve_vm, _results, _run
from test_route import STAND_IN

from claude_vm import route

HOP_ADDRESS = "100.64.0.7"
DISCOVER = {"ansible.builtin.import_playbook": str(REPO / "playbooks" / "discover.yml")}
SAY = {
    "name": "Say what each VM is given",
    "hosts": "claude_vms",
    "gather_facts": False,
    "tasks": [
        {
            "name": "Given",
            "ansible.builtin.debug": {
                "msg": {
                    "common": "{{ ansible_ssh_common_args }}",
                    "control": "{{ ansible_control_path }}",
                    "via": "{{ claude_vm_ssh_via }}",
                }
            },
        }
    ],
}


@pytest.fixture
def short_dir():
    # The hop's socket's directory, short enough for macOS, where pytest's own are not.
    directory = tempfile.mkdtemp(prefix="aw-", dir="/tmp")
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def routed(tmp_path, short_dir):
    """A run against the fake with two VMs running, alpha and beta, and gamma stopped, the route through a host whose
    ssh is a stand-in: (root, the stand-in's directory). Each test sets the stand-in's mode, and files of its own
    under root."""
    for base in _fake_pve(tmp_path):
        _pve_vm(base, 9020, "alpha", running=True)
        _pve_vm(base, 9021, "beta", running=True)
        _pve_vm(base, 9022, "gamma")
        root = tmp_path / "run"
        _pve_vars(root, base, {"vm_ssh_route": "host"})
        hop = tmp_path / "hop"
        hop.mkdir()
        ssh = hop / "ssh"
        ssh.write_text(STAND_IN)
        ssh.chmod(0o755)
        (hop / "mode").write_text("open")
        (hop / "forward").write_text("answer")
        pve = {
            "ansible_host": HOP_ADDRESS,
            "ansible_user": "root",
            "ansible_ssh_executable": str(ssh),
            "ansible_control_path_dir": short_dir,
        }
        (root / "proxmox.yml").write_text(yaml.safe_dump({"proxmox": {"hosts": {"pve": pve}}}))
        yield root, hop


def _discover(root: Path, *plays: dict, extra: dict | None = None) -> dict:
    playbook = root / "discover.yml"
    playbook.write_text(yaml.safe_dump([DISCOVER, *plays]))
    args = ["-i", str(root / "proxmox.yml"), "-e", json.dumps({"vm_name": "alpha,beta", **(extra or {})})]
    return _run(root, playbook, {}, *args, timeout=180)


def _given(report: dict) -> dict:
    return {host: result["msg"] for name, host, result in _results(report) if name == "Given"}


def _said(report: dict, task: str) -> list[str]:
    """What each run of a debug task said, a loop's items each, its skipped runs left out."""
    said = []
    for name, _, result in _results(report):
        if name != task:
            continue
        for one in result.get("results") or [result]:
            if not one.get("skipped") and "msg" in one:
                said.append(one["msg"])
    return said


def _calls(hop: Path) -> list[str]:
    """Each ssh the run made through the hop, as its kind: check, stop, open, or forward to an address."""
    calls = hop / "calls"
    kinds = []
    for line in calls.read_text().splitlines() if calls.exists() else []:
        words = line.split()
        if "-O" in words:
            kinds.append(words[words.index("-O") + 1])
        elif "-N" in words:
            kinds.append("open")
        elif "-W" in words:
            kinds.append(
                f"forward {words[words.index('-W') + 1]}" + (" unshared" if "ControlPath=none" in words else "")
            )
        else:
            kinds.append(line)
    return kinds


def test_each_vm_goes_through_the_host_between_the_project_s_options_and_its_own(routed):
    root, hop = routed
    report = _discover(root, SAY)
    assert report["rc"] == 0, _failures(report)
    given = _given(report)
    assert sorted(given) == ["alpha", "beta"]
    settled = route.hop_settings(
        ssh=str(hop / "ssh"),
        user="root",
        host=HOP_ADDRESS,
        control_dir=yaml.safe_load((root / "proxmox.yml").read_text())["proxmox"]["hosts"]["pve"][
            "ansible_control_path_dir"
        ],
        name="pve",
    )
    for name, address in (("alpha", "192.0.2.70"), ("beta", "192.0.2.71")):
        words = shlex.split(given[name]["common"])
        proxy = route.ssh_proxies(words)
        assert proxy == [("ProxyCommand", " ".join(route.hop_argv(settled, address, "accept-new")))], words
        # After the project's own, which ssh keeps since they come first; the VM's socket named with the hop.
        known = next(at for at, word in enumerate(words) if word.startswith("UserKnownHostsFile="))
        assert known < words.index("ProxyCommand=" + proxy[0][1])
        assert given[name]["via"] == f"-J root@{HOP_ADDRESS}"
        assert given[name]["control"].startswith("%(directory)s/aw-") and given[name]["control"].endswith("-%%r-%%p")
    # The login at the run's start, one forward of its own to the first VM by name, the connection made sure of before
    # the VMs are reached; no forward or open more.
    assert _calls(hop) == ["check", "open", "check", "forward 192.0.2.70:22 unshared", "check"], _calls(hop)
    said = _said(report, "vm_route | Say how the VMs are reached")
    assert said == [
        f"VMs are reached through pve (root@{HOP_ADDRESS}). A VM that does not answer shows as a closed"
        " connection; `just status` says what the host reports for it."
    ]


def test_the_socket_s_name_has_the_hop_in_it(routed):
    root, _ = routed
    through = _given(_discover(root, SAY))
    (root / "group_vars" / "all" / "route.yml").write_text("vm_ssh_route: direct\n")
    direct = _given(_discover(root, SAY))
    assert through["alpha"]["control"] != direct["alpha"]["control"]
    assert len(through["alpha"]["control"]) == len(direct["alpha"]["control"])
    # Direct, every word is as before the route: no hop, no words for a person's ssh.
    assert route.ssh_proxies(direct["alpha"]["common"]) == [] and direct["alpha"]["via"] == ""


@pytest.mark.parametrize(("where", "logged_in"), [("all", False), ("claude_vms", True)])
def test_a_vm_given_a_jump_of_its_own_gets_no_hop(routed, where, logged_in):
    # Under all, what every host is given holds the jump: the login is put off at the start, and never made, since
    # every VM brings its own. On the claude_vms group alone, the start cannot see it: the login is made there, and no
    # forward, since no VM goes through the host.
    root, hop = routed
    jump = yaml.safe_dump({"ansible_ssh_common_args": "-o ProxyJump=jump.invalid"})
    (root / "group_vars" / ("all/jump.yml" if where == "all" else "claude_vms.yml")).write_text(jump)
    report = _discover(root, SAY)
    assert report["rc"] == 0, _failures(report)
    for name, given in _given(report).items():
        assert route.ssh_jump(given["common"]) == "ProxyJump jump.invalid" and given["via"] == "", name
        assert len(route.ssh_proxies(given["common"])) == 1, name
    assert _calls(hop) == (["check", "open", "check"] if logged_in else []), _calls(hop)
    said = _said(report, "vm_route | Say each VM that is reached by a jump of its own")
    assert any("alpha is reached by the jump it is given (ProxyJump jump.invalid)" in msg for msg in said), said


def test_a_jump_every_host_is_given_puts_the_login_off_until_a_vm_needs_the_hop(routed):
    # Under all, a jump; the claude_vms group sets common args of its own without one: its VMs go through the host.
    root, hop = routed
    (root / "group_vars" / "all" / "jump.yml").write_text("ansible_ssh_common_args: -o ProxyJump=jump.invalid\n")
    (root / "group_vars" / "claude_vms.yml").write_text("ansible_ssh_common_args: -o ServerAliveInterval=5\n")
    report = _discover(root, SAY)
    assert report["rc"] == 0, _failures(report)
    assert [
        name
        for name, _, result in _results(report)
        if name.startswith("vm_route | Log in") and not result.get("skipped")
    ] == ["vm_route | Log in to the Proxmox host as the VMs' hop"]
    assert all(given["via"] for given in _given(report).values())
    assert _calls(hop) == ["check", "open", "check", "forward 192.0.2.70:22 unshared", "check"], _calls(hop)


@pytest.mark.parametrize(
    ("mode", "said"),
    [
        ("refused", "ssh could not log in there: root@pve: Permission denied (publickey)"),
        ("prohibited", "AllowTcpForwarding, PermitOpen, DisableForwarding"),
        ("login", "ssh could not use pve (root@100.64.0.7) as a hop to alpha at 192.0.2.70"),
    ],
)
def test_a_host_that_cannot_be_the_hop_stops_the_run_before_any_vm_is_reached(routed, mode, said):
    # refused: the login; prohibited and login: the forward, after a login that holds.
    root, hop = routed
    (hop / "mode" if mode == "refused" else hop / "forward").write_text(mode)
    report = _discover(root, SAY)
    assert report["rc"] != 0
    assert said in json.dumps(_failures(report)), _failures(report)
    assert _given(report) == {}


def test_a_vm_that_says_nothing_through_the_host_is_said_and_stops_nothing(routed):
    root, hop = routed
    (hop / "forward").write_text("silent")
    report = _discover(root, SAY)
    assert report["rc"] == 0, _failures(report)
    said = _said(report, "vm_route | Say a VM that did not answer through the host")
    assert said and said[0].startswith("No answer from alpha at 192.0.2.70 through pve (root@100.64.0.7) within 15 s")


def test_a_proxy_option_ssh_would_read_before_the_hop_is_refused_by_name(routed):
    # On the claude_vms group alone, so the run's start, which reads what every host is given, cannot see it: the VM
    # is held to the route once it is added, before anything connects.
    root, _ = routed
    (root / "group_vars" / "claude_vms.yml").write_text(
        yaml.safe_dump({"ansible_ssh_args": "-o ControlMaster=auto -o ProxyCommand=none"})
    )
    report = _discover(root, SAY)
    assert report["rc"] != 0
    failure = json.dumps(_failures(report))
    assert "alpha: ssh_args holds ProxyCommand none, which ssh would take before the hop" in failure, failure


def test_a_proxy_option_every_host_is_given_before_the_hop_is_refused_at_the_start(routed):
    root, hop = routed
    report = _discover(root, SAY, extra={"ansible_ssh_args": "-o ProxyJump=jump.invalid"})
    assert report["rc"] != 0
    assert "ssh_args holds ProxyJump jump.invalid, which ssh would take before the hop" in json.dumps(_failures(report))
    assert _calls(hop) == []


def test_ansible_keeps_the_vm_s_own_controlpath_beside_the_hop(routed):
    # Ansible adds a VM's own ControlPath only where a word holds ControlPersist (ssh.py's _persistence_controls): the
    # real plugin, run to a recorder in the VM's ssh's place.
    root, _ = routed
    record = root / "argv"
    recorder = root / "vm-ssh"
    recorder.write_text(f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > {record}\nexit 255\n')
    recorder.chmod(0o755)
    reach = {
        "name": "Reach each VM",
        "hosts": "alpha",
        "gather_facts": False,
        "tasks": [
            {
                "name": "Reach",
                "ansible.builtin.ping": None,
                "ignore_unreachable": True,
                "vars": {"ansible_ssh_executable": str(recorder)},
            }
        ],
    }
    report = _discover(root, reach)
    assert report["rc"] == 0, _failures(report)
    argv = record.read_text().splitlines()
    proxy = [word for word in argv if word.startswith("ProxyCommand=")]
    own = [word for word in argv if word.startswith("ControlPath=")]
    assert len(proxy) == 1 and len(own) == 1 and "/aw-" in own[0] and own[0].endswith('-%r-%p"'), argv


def test_the_later_plays_make_sure_of_the_connection_and_fail_no_host(tmp_path):
    # The plays that reach the VMs at once after they are added, none serial: one local task first, where any host of
    # the play takes the hop, that fails no host - a hop that is gone shows as each VM not answering.
    plays = {
        "claude_login.yml": "Reach each VM before its sign-in",
        "github_login.yml": "Reach each VM before its sign-in",
        "tailscale_login.yml": "Reach each VM before its sign-in",
        "destroy.yml": "Release each VM from what it signed in to",
        "golden_build.yml": "Set up the golden template's build VM",
    }
    for path, name in plays.items():
        play = next(p for p in yaml.safe_load((REPO / "playbooks" / path).read_text()) if p.get("name") == name)
        first = play["tasks"][0]
        assert first["name"] == "Make sure of the connection through the Proxmox host", path
        assert first["vm_route"]["do"] == "held", path
        assert (first["delegate_to"], first["run_once"], first["become"], first["failed_when"]) == (
            "localhost",
            True,
            False,
            False,
        ), path
        assert "ansible_play_hosts_all" in first["when"] and "claude_vm_ssh_via" in first["when"], path
        assert "serial" not in play, path


def test_every_scenario_harness_and_the_vagrantfile_keep_the_route_direct():
    # auto would ask this machine's ssh, through the developer's own config, where it dials a test's Proxmox host.
    provision = yaml.safe_load((REPO / "molecule" / "provision" / "group_vars" / "all" / "fake_api.yml").read_text())
    assert provision["vm_ssh_route"] == "direct"
    for scenario in ("configure", "image"):
        local = yaml.safe_load((REPO / "molecule" / scenario / "host_vars" / "localhost.yml").read_text())
        assert local["vm_ssh_route"] == "direct", scenario
    assert 'vm_ssh_route: "direct"' in (REPO / "Vagrantfile").read_text()
    assert '"vm_ssh_route": "direct"' in (REPO / "tests" / "unit" / "harness.py").read_text()


def test_the_route_is_a_choice_of_three_never_inherited_and_never_an_agent_s_to_set():
    from claude_vm import catalogue
    from claude_vm.hosts import kinds
    from claude_vm.mcp import confirm

    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert defaults["vm_ssh_route"] == "auto" and set(defaults["claude_vm_ssh_routes"]) == set(route.ROUTES)
    assert catalogue.CHOICES["vm_ssh_route"] == ("claude_vm_ssh_routes", ())
    assert "vm_ssh_route" in kinds.KINDS["proxmox"].never_inherited
    assert "vm_ssh_route" in confirm.REFUSED
    # The hop's account is the host's own: never a setting of local.yml or the defaults.
    assert "vm_ssh_jump" not in defaults
    for example in (
        REPO / "inventory" / "hosts.yml.example",
        REPO / "tools" / "templates" / "host" / "hosts.yml.example",
    ):
        assert "# vm_ssh_jump: " in example.read_text(), example


def test_destroy_checks_the_login_before_it_starts_a_stopped_vm(routed, tmp_path):
    # A stopped VM is started for its release and save, which reach it over SSH: through a host that cannot be logged
    # in to, nothing is started, and nothing deleted.
    root, hop = routed
    (hop / "mode").write_text("refused")
    extra = {
        "vm_name": "gamma",
        "confirm_destroy": "yes",
        "destroy_sessions_dir": str(tmp_path / "saved"),
        "claude_vm_release_agent_delay": 0.2,
    }
    playbook = REPO / "playbooks" / "destroy.yml"
    report = _run(root, playbook, {}, "-i", str(root / "proxmox.yml"), "-e", json.dumps(extra), timeout=180)
    assert report["rc"] != 0
    assert "ssh could not log in there" in json.dumps(_failures(report))
    started = [r for r in _pve_requests(tmp_path) if str(r["path"]).endswith("/qemu/9022/status/start")]
    assert started == []


def test_every_hop_is_made_from_the_run_s_route_and_none_where_no_run_settled_one():
    # A run with no route settled - a play run on its own, the golden build's test - publishes as direct.
    made = {}
    for path in [REPO / "deploy.yml", *sorted((REPO / "playbooks").glob("*.yml"))]:
        for before in re.findall(r"\{\{(.{0,80}?)\|\s*vm_hop\(", path.read_text(), re.S):
            made.setdefault(path.name, []).append(" ".join(before.split()))
    assert sorted(made) == ["code.yml", "destroy.yml", "discover.yml", "golden_build.yml", "provision.yml"]
    assert all(each == ["claude_vm_route | default({})"] for each in made.values()), made
    assert route.vm_hop({}, "192.0.2.70") == {"option": "", "name": "", "via": "", "jump": ""}


def test_ssh_config_writes_no_alias_through_a_host_whose_login_fails_and_goes_on(routed):
    # It connects to no VM: an unusable hop stops nothing there, and no alias is written through it.
    root, hop = routed
    (hop / "mode").write_text("refused")
    playbook = REPO / "playbooks" / "ssh_config.yml"
    report = _run(root, playbook, {}, "-i", str(root / "proxmox.yml"), "-e", json.dumps({"vm_name": "alpha"}))
    assert report["rc"] == 0, _failures(report)
    assert any(
        "No alias was written through pve (root@100.64.0.7)" in msg
        for msg in _said(report, "ssh_config | Say that no alias went through the Proxmox host")
    )
    assert not (root / ".ssh" / "agent-warehouse.conf").exists()
    assert _calls(hop) == ["check", "open", "check"]
