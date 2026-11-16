"""lib/claude_vm/route.py: the route to the VMs, the hop through the Proxmox host, the hop's own ssh under its limits.

The hop's ssh runs against stand-ins written here, which play what OpenSSH does (docs/architecture/custom-code.md
§7.17); the one real ssh run is given no config of the developer's and connects nowhere.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from harness import REPO

from claude_vm import route

HOP = route.hop_settings(user="root", host="pve.example.ts.net", control_dir="/tmp/cp", name="pve")
ROUTE = {"route": "host", "hop": HOP, "policy": "accept-new"}


@pytest.mark.parametrize(
    ("setting", "addresses", "expected"),
    [
        ("auto", ["100.101.102.103"], "host"),
        ("auto", ["fd7a:115c:a1e0::1"], "host"),
        ("auto", ["100.64.0.1", "fd7a:115c:a1e0:ab12::7"], "host"),
        ("auto", ["192.0.2.10"], "direct"),
        ("auto", ["100.101.102.103", "192.0.2.10"], "direct"),  # a LAN address beside the tailnet's: not by the rule
        ("auto", ["100.63.255.255"], "direct"),  # just below Tailscale's range
        ("auto", ["100.128.0.0"], "direct"),  # just above it
        ("auto", [], "direct"),  # a name that does not resolve
        ("host", [], "host"),
        ("direct", ["100.101.102.103"], "direct"),
        (None, ["100.101.102.103"], "host"),  # auto by default
    ],
)
def test_the_rule_reads_every_address_ssh_dials(setting, addresses, expected):
    assert route.route_rule(setting, addresses) == expected


def test_a_route_that_is_none_of_the_three_is_refused():
    with pytest.raises(ValueError, match="vm_ssh_route is one of auto, direct, host"):
        route.route_rule("tailnet", [])


def _program(path: Path, text: str) -> str:
    path.write_text(text)
    path.chmod(0o755)
    return str(path)


def test_under_auto_a_hostname_of_the_user_s_decides(tmp_path):
    # ssh -G names where ssh would dial, a HostName of the user's ssh config included: here a tailnet address for a
    # LAN-looking name.
    ssh = _program(tmp_path / "ssh", '#!/bin/sh\necho "hostname 100.101.102.103"\n')
    hop = route.hop_settings(ssh=ssh, user="root", host="pve.lan", control_dir="/tmp/cp")
    assert route.route_settle("auto", hop) == {
        "route": "host",
        "addresses": ["100.101.102.103"],
        "dialled": "100.101.102.103",
    }


def test_under_auto_a_name_ssh_cannot_show_is_resolved_as_written(tmp_path):
    ssh = _program(tmp_path / "ssh", "#!/bin/sh\nexit 255\n")
    hop = route.hop_settings(ssh=ssh, host="192.0.2.10", control_dir="/tmp/cp")
    assert route.route_settle("auto", hop) == {"route": "direct", "addresses": ["192.0.2.10"], "dialled": "192.0.2.10"}


def test_under_auto_a_name_that_does_not_resolve_is_direct(tmp_path):
    ssh = _program(tmp_path / "ssh", "#!/bin/sh\necho 'hostname pve.invalid'\n")
    hop = route.hop_settings(ssh=ssh, host="pve", control_dir="/tmp/cp")
    assert route.route_settle("auto", hop)["route"] == "direct"


def test_a_route_named_asks_nothing(tmp_path):
    hop = route.hop_settings(ssh=str(tmp_path / "absent"), host="pve", control_dir="/tmp/cp")
    assert route.route_settle("host", hop) == {"route": "host", "addresses": [], "dialled": ""}
    assert route.route_settle("direct", hop)["route"] == "direct"


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({"user": "-oProxyCommand=id"}, "user"),
        ({"user": "root;id"}, "user"),
        ({"user": "ro ot"}, "user"),
        ({"host": "pve;id"}, "address"),
        ({"host": "-pve"}, "address"),
        ({"host": "pve example"}, "address"),
        ({"host": "fe80::1%en0"}, "address"),
        ({"host": "$(id)"}, "address"),
        ({"host": ""}, "address"),
        ({"port": "22;"}, "port"),
        ({"port": 0}, "port"),
        ({"port": 70000}, "port"),
        ({"key": "/keys/a b"}, "key file"),
        ({"key": "/keys/id_%h"}, "key file"),
        ({"key": "/keys/it's"}, "key file"),
        ({"key": "$HOME/.ssh/id"}, "key file"),
        ({"key": "-i"}, "key file"),
        ({"key": "=ssh/id_ed25519"}, "key file"),  # zsh, an account's shell, expands a leading =
        ({"ssh": "/usr/bin/ssh;id"}, "ssh program"),
        ({"control_dir": "/Users/Jo Smith/.ansible/cp"}, "control directory"),
        ({"control_dir": "/tmp/%C"}, "control directory"),
        ({"control_dir": "/" + "d" * 80}, "longer than 86 bytes"),
    ],
)
def test_every_setting_of_the_hop_is_one_plain_word_or_refused_by_name(given, said):
    settings = {"user": "root", "host": "pve", "control_dir": "/tmp/cp", **given}
    with pytest.raises(route.HopRefused, match=said):
        route.hop_settings(**settings)


@pytest.mark.parametrize("target", ["192.0.2.1\n", "192.0.2.1 -oProxyCommand=id", "vm.lan", "::1", "", "192.0.2.1;id"])
def test_the_hop_forwards_to_one_ipv4_address_and_nothing_else(target):
    with pytest.raises(route.HopRefused, match="not one IPv4 address"):
        route.hop_argv(HOP, target, "accept-new")


def test_the_hop_s_command_word_for_word():
    hop = route.hop_settings(
        ssh="/usr/bin/ssh", user="forwarder", host="100.64.0.7", port=2222, key="/k/id_ed25519", control_dir="/tmp/cp"
    )
    assert route.hop_argv(hop, "192.0.2.77", "accept-new") == [
        "/usr/bin/ssh",
        *("-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"),
        *("-o", "UpdateHostKeys=no", "-o", "ForwardAgent=no", "-o", "LogLevel=INFO"),
        *("-o", "ControlMaster=auto", "-o", f"ControlPath={hop['socket']}", "-o", "ControlPersist=120"),
        *("-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"),
        *("-p", "2222", "-i", "/k/id_ed25519"),
        *("-W", "192.0.2.77:%p", "forwarder@100.64.0.7"),
    ]
    # The open makes the shared connection and is never a client of one; it carries the keepalives that end it once its
    # far side is gone, and none of the user's forwards.
    assert route.hop_open_argv(hop, "accept-new") == [
        "/usr/bin/ssh",
        *("-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"),
        *("-o", "UpdateHostKeys=no", "-o", "ForwardAgent=no", "-o", "LogLevel=INFO"),
        *("-o", "ControlMaster=yes", "-o", f"ControlPath={hop['socket']}", "-o", "ControlPersist=120"),
        *("-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"),
        *("-p", "2222", "-i", "/k/id_ed25519"),
        *("-o", "ClearAllForwardings=yes", "-N", "forwarder@100.64.0.7"),
    ]
    unshared = route.hop_argv(hop, "192.0.2.77", "yes", port=22, shared=False)
    assert "ControlPath=none" in unshared and "ControlPersist=120" not in unshared
    assert unshared[-2:] == ["192.0.2.77:22", "forwarder@100.64.0.7"] and "StrictHostKeyChecking=yes" in unshared


def test_the_hop_s_socket_is_named_by_the_hop_and_fits_macos():
    socket = HOP["socket"]
    assert socket.startswith("/tmp/cp/awh-") and len(socket) == len("/tmp/cp/awh-") + 12
    other = route.hop_settings(user="forwarder", host="pve.example.ts.net", control_dir="/tmp/cp")
    assert other["socket"] != socket
    assert route.hop_settings(user="root", host="pve.example.ts.net", control_dir="/tmp/cp")["socket"] == socket
    # The longest directory that fits: the path and ssh's 17 while it binds stay within macOS's 103.
    longest = "/" + "d" * (route.SOCKET_MOST - len("/awh-") - 12 - 1)
    assert len(route.hop_settings(host="pve", control_dir=longest)["socket"]) + 17 == 103
    with pytest.raises(route.HopRefused):
        route.hop_settings(host="pve", control_dir=longest + "d")


def test_a_home_directory_is_expanded_before_it_is_held(monkeypatch):
    monkeypatch.setenv("HOME", "/home/dev")
    hop = route.hop_settings(host="pve", key="~/.ssh/id_ed25519", control_dir="~/.ansible/cp")
    assert hop["key"] == "/home/dev/.ssh/id_ed25519" and hop["socket"].startswith("/home/dev/.ansible/cp/awh-")


def test_the_hop_option_is_one_word_ansible_keeps_its_own_controlpath_beside():
    from ansible.plugins.connection.ssh import Connection

    words = shlex.split(route.hop_option(HOP, "192.0.2.77", "accept-new"))
    assert words[0] == "-o" and len(words) == 2 and words[1].startswith("ProxyCommand=")
    # Ansible adds the VM's own ControlPath only where a word holds ControlPersist; one holding ControlPath alone would
    # keep it from adding any.
    assert Connection._persistence_controls([word.encode() for word in words]) == (True, False)


def test_a_person_s_ssh_is_given_the_hop_as_one_jump():
    assert route.hop_via(HOP) == "-J root@pve.example.ts.net"
    assert route.hop_via(route.hop_settings(host="fd7a:115c:a1e0::5", port=2222, control_dir="/tmp/cp")) == (
        "-J [fd7a:115c:a1e0::5]:2222"
    )
    assert route.jump_spec(route.hop_settings(user="root", host="pve", port=22, control_dir="/tmp/cp")) == "root@pve"
    assert HOP["label"] == "pve (root@pve.example.ts.net)"


@pytest.mark.parametrize(
    ("args", "jump"),
    [
        ("-o ProxyJump=jump.example", "ProxyJump jump.example"),
        ("-oProxyJump=jump.example", "ProxyJump jump.example"),
        ("-o 'ProxyJump jump.example'", "ProxyJump jump.example"),
        ("-o proxyjump=jump.example", "ProxyJump jump.example"),
        ("-o 'ProxyCommand=nc -X 5 -x proxy:1080 %h %p'", "ProxyCommand nc -X 5 -x proxy:1080 %h %p"),
        ("-J jump.example", "-J jump.example"),
        ("-Jjump.example", "-J jump.example"),
        ("-AqJ jump.example", "-J jump.example"),
        ("-o ServerAliveInterval=5 -J a,b", "-J a,b"),
        (["-o", "ProxyJump=jump.example"], "ProxyJump jump.example"),
        ("-o '\"ProxyJump\" jump.example'", "ProxyJump jump.example"),  # a keyword in quotes
        ("-o=ProxyJump=jump.example", "ProxyJump jump.example"),  # a leading =
        ("-o ProxyCommand=none", ""),
        ("-o ProxyCommand=NONE", ""),  # ssh reads none in any case
        ("-o ProxyCommand=None", ""),
        ("-o ProxyJump=none", ""),
        ("-o ProxyJump=NONE", ""),
        ("-o ProxyJump=None", ""),
        ("-J none", ""),
        ("-J NONE", ""),
        ("-o '\"ProxyCommand\" none'", ""),
        ("-o=ProxyCommand=none", ""),
        ("-o '' ProxyCommand=none", ""),  # a blank word, which Ansible's split drops
        ("-o ServerAliveInterval=5", ""),
        ("-i /k/-J -o Port=22", ""),  # a value that looks like the option is a value
        ("", ""),
        (None, ""),
    ],
)
def test_a_jump_is_found_in_any_of_ssh_s_spellings_and_none_is_no_jump(args, jump):
    assert route.ssh_jump(args) == jump


def test_any_proxy_option_counts_where_it_would_come_before_the_hop():
    assert route.ssh_proxy_any("-o ControlMaster=auto -o ProxyCommand=none") == "ProxyCommand none"
    assert route.ssh_proxy_any("-o ControlMaster=auto -J jump") == "-J jump"
    assert route.ssh_proxy_any("-o ControlMaster=auto -o ControlPersist=60s") == ""


def test_a_vm_s_hop():
    hop = route.vm_hop(ROUTE, "192.0.2.77")
    assert hop["option"] == route.hop_option(HOP, "192.0.2.77", "accept-new")
    assert hop["name"] == os.path.basename(HOP["socket"]) and hop["via"] == "-J root@pve.example.ts.net"
    assert hop["jump"] == ""


@pytest.mark.parametrize(
    ("route_fact", "address", "given", "extra", "jump"),
    [
        ({"route": "direct"}, "192.0.2.77", "", "", ""),
        (None, "192.0.2.77", "", "", ""),  # no route settled: the golden build's own test
        (ROUTE, "", "", "", ""),  # no address: reached by nothing
        (ROUTE, "192.0.2.77", "-o ProxyJump=jump.example", "", "ProxyJump jump.example"),
        (ROUTE, "192.0.2.77", "", "-J jump.example", "-J jump.example"),
    ],
)
def test_no_hop_where_the_route_is_direct_the_vm_has_no_address_or_brings_its_own_jump(
    route_fact, address, given, extra, jump
):
    assert route.vm_hop(route_fact, address, given, extra) == {"option": "", "name": "", "via": "", "jump": jump}


def test_a_none_the_vm_is_given_comes_after_the_hop_and_is_left_alone():
    assert route.vm_hop(ROUTE, "192.0.2.77", "-o ProxyCommand=none")["option"]


def test_which_vms_go_through_the_hop_and_which_bring_a_jump():
    # By name, each with its address, a jump the VM is given in its common or extra args: as its groups give them for
    # a VM published already in its group, else as every host is given them; a name takes the address provision.yml's
    # play left on it, and a VM with no address is reached by nothing.
    hostvars = {
        "alpha": {"proxmox_vm_address": "192.0.2.70"},
        "beta": {"claude_vm_ssh_given": "-o ProxyJump=jump.example", "proxmox_vm_address": "192.0.2.71"},
        "gamma": {"ansible_ssh_extra_args": "-J other.example"},
        "delta": {"proxmox_vm_address": ""},
    }
    vms = ["beta", "alpha", "delta", {"name": "gamma", "address": "192.0.2.72"}, {"name": "zeta", "address": ""}]
    assert route.vm_route_vms(vms, hostvars) == [
        {"name": "alpha", "address": "192.0.2.70", "jump": ""},
        {"name": "beta", "address": "192.0.2.71", "jump": "ProxyJump jump.example"},
        {"name": "gamma", "address": "192.0.2.72", "jump": "-J other.example"},
    ]
    # Not yet in its group: what every host is given.
    assert route.vm_route_vms([{"name": "eps", "address": "192.0.2.73"}], {}, "-o ProxyJump=all.example") == [
        {"name": "eps", "address": "192.0.2.73", "jump": "ProxyJump all.example"}
    ]


@pytest.mark.parametrize("value", ["forwarder", "", None])
def test_vm_ssh_jump_is_one_account(value):
    assert route.account(value) == (value or "")


@pytest.mark.parametrize(
    ("value", "said"),
    [
        ("root@other.example", "the hop is the Proxmox host itself"),
        ("forwarder:2222", "the hop is the Proxmox host itself"),
        ("-oProxyCommand=id", "not an account name"),
        ("for warder", "not an account name"),
    ],
)
def test_vm_ssh_jump_that_is_more_than_an_account_is_refused_by_name(value, said):
    with pytest.raises(route.HopRefused, match=said):
        route.account(value)


# A stand-in for the hop's ssh, playing OpenSSH's control commands and its forward by a mode file: it logs each call,
# a master is a file at the socket's path - one that holds "stale" is a socket nothing listens at - an open makes one
# unless the mode says otherwise, and a forward ends as the file "forward" says, else as the mode does. An open beside
# a live master is a client with ControlMaster=auto, which asks the host for a session (logged in "sessions"), and
# with ControlMaster=yes says the socket exists and goes on as a connection of its own; at a stale one, auto takes the
# path over and yes says it exists.
STAND_IN = r"""#!/bin/sh
here=$(dirname "$0")
printf '%s\n' "$*" >> "$here/calls"
socket=$(printf '%s\n' "$@" | sed -n 's/^ControlPath=//p' | head -1)
master=$(printf '%s\n' "$@" | sed -n 's/^ControlMaster=//p' | head -1)
mode=$(cat "$here/mode" 2>/dev/null)
forward=$(cat "$here/forward" 2>/dev/null || printf '%s' "$mode")
case " $* " in
*" -O check "*) [ -e "$socket" ] && [ "$(cat "$socket")" = stale ] && {
        echo "Control socket connect($socket): Connection refused" >&2; exit 255; }
    [ -e "$socket" ] && { echo "Master running (pid=1)" >&2; exit 0; }
    # appears: another run's open lands right after this check.
    [ "$mode" = appears ] && : > "$socket"
    echo "Control socket connect($socket): No such file or directory" >&2; exit 255 ;;
*" -O stop "*) rm -f "$socket"; echo "Stop listening request sent." >&2; exit 0 ;;
*" -N "*) if [ -e "$socket" ] && [ "$master" = yes ]; then
        echo "ControlSocket $socket already exists, disabling multiplexing" >&2; exec sleep 60
    elif [ -e "$socket" ] && [ "$(cat "$socket")" != stale ]; then
        echo "a session, by a client" >> "$here/sessions"; exit 0
    fi
    case $mode in
    race) : > "$socket"; echo "ControlSocket $socket already exists, disabling multiplexing" >&2; exec sleep 60 ;;
    hang-then-opened) : > "$socket"; exec sleep 60 ;;
    refused) echo "root@pve: Permission denied (publickey)." >&2; exit 255 ;;
    *) : > "$socket"; exit 0 ;;
    esac ;;
*" -W "*) case $forward in
    answer) echo "SSH-2.0-OpenSSH_9.6"; exit 0 ;;
    forged) echo "channel 0: open failed: administratively prohibited: open failed"; exit 0 ;;
    closed) echo "channel 0: open failed: connect failed: Connection refused" >&2; echo "stdio forwarding failed" >&2
        exit 255 ;;
    silent) exec sleep 60 ;;
    prohibited) echo "channel 0: open failed: administratively prohibited: open failed" >&2
        echo "stdio forwarding failed" >&2; exit 255 ;;
    login) echo "root@pve: Permission denied (publickey)." >&2; exit 255 ;;
    esac ;;
esac
exit 1
"""


@pytest.fixture
def short_dir():
    # A socket's directory short enough for macOS, where pytest's own are not.
    directory = tempfile.mkdtemp(prefix="aw-", dir="/tmp")
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def hop_at(tmp_path, short_dir):
    ssh = _program(tmp_path / "ssh", STAND_IN)

    def at(mode: str) -> dict:
        (tmp_path / "mode").write_text(mode)
        return route.hop_settings(ssh=ssh, user="root", host="pve", control_dir=short_dir)

    return at


def _calls(tmp_path: Path) -> list[str]:
    calls = tmp_path / "calls"
    return [line.split(" -O ")[-1].split()[0] if " -O " in line else line for line in calls.read_text().splitlines()]


@pytest.mark.parametrize(
    ("mode", "usable", "why"),
    [
        ("answer", True, "answered"),
        ("closed", True, "refused"),
        ("silent", True, "silent"),
        ("prohibited", False, "prohibited"),
        ("login", False, "failed"),
        # What arrives on the forward is the far end's to write: words of a refusal there are not the host's.
        ("forged", True, "answered"),
    ],
)
def test_the_forward_check_is_decided_from_exit_status_and_standard_error(hop_at, mode, usable, why):
    started = time.monotonic()
    verdict = route.hop_forward(hop_at(mode), "192.0.2.77", "accept-new", limit=1.5)
    assert (verdict["usable"], verdict["why"]) == (usable, why)
    assert time.monotonic() - started < 10
    if why == "failed":
        assert "Permission denied" in verdict["said"]


def test_making_sure_never_opens_beside_a_live_connection(hop_at, tmp_path):
    hop = hop_at("open")
    assert route.hop_held(hop, "accept-new") == {"ok": True, "opened": True, "said": ""}
    assert route.hop_held(hop, "accept-new") == {"ok": True, "opened": False, "said": ""}
    assert [call if call in ("check", "stop") else "-N" for call in _calls(tmp_path)] == [
        "check",
        "-N",
        "check",
        "check",
    ]
    assert "ClearAllForwardings=yes" in (tmp_path / "calls").read_text().splitlines()[1]


def test_an_open_beside_a_connection_that_came_up_after_its_check_starts_no_session(hop_at, tmp_path):
    # Another run's open lands between this one's check and its open: the open is that connection's master or nothing,
    # never its client, which with -N would ask the host for a session (a shell on a full account).
    started = time.monotonic()
    assert route.hop_held(hop_at("appears"), "accept-new")["ok"]
    assert time.monotonic() - started < 10
    assert not (tmp_path / "sessions").exists()
    assert "ControlMaster=yes" in (tmp_path / "calls").read_text().splitlines()[1]


def test_a_socket_nothing_listens_at_is_removed_and_opened_again(hop_at, tmp_path, short_dir):
    # A connection that ended without removing its socket: a master never takes a path over, so the file goes first.
    hop = hop_at("open")
    Path(hop["socket"]).write_text("stale")
    assert route.hop_held(hop, "accept-new") == {"ok": True, "opened": True, "said": ""}
    assert Path(hop["socket"]).read_text() == "" and not (tmp_path / "sessions").exists()


def test_a_socket_whose_check_was_ended_at_its_limit_is_left(tmp_path, short_dir):
    slow = _program(tmp_path / "ssh", "#!/bin/sh\necho 'Connection refused' >&2\nexec sleep 30\n")
    hop = route.hop_settings(ssh=slow, user="root", host="pve", control_dir=short_dir)
    Path(hop["socket"]).write_text("held by a check that never answered")
    held = route.hop_held(hop, "accept-new", {"check": 0.5, "open": 0.5})
    assert not held["ok"] and Path(hop["socket"]).exists()


def test_a_control_directory_that_cannot_be_made_is_said_never_raised(tmp_path, short_dir):
    (Path(short_dir) / "file").write_text("")
    hop = route.hop_settings(ssh="/bin/false", user="root", host="pve", control_dir=f"{short_dir}/file/cp")
    held = route.hop_held(hop, "accept-new")
    assert not held["ok"] and held["said"].startswith(f"the control directory {short_dir}/file/cp: ")


def test_a_caller_ended_during_an_ssh_ends_it_and_waits_for_it(tmp_path, monkeypatch):
    pid_file = tmp_path / "pid"
    program = _program(tmp_path / "slow", f"#!/bin/sh\necho $$ > {pid_file}\nexec sleep 60\n")
    real_sleep = time.sleep

    def interrupted(seconds):
        if pid_file.exists() and pid_file.read_text().strip():
            raise KeyboardInterrupt
        real_sleep(seconds)

    monkeypatch.setattr(route.time, "sleep", interrupted)
    with pytest.raises(KeyboardInterrupt):
        route._run([program], 30)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def test_a_name_that_gets_no_answer_in_time_does_not_resolve(monkeypatch):
    monkeypatch.setattr(route.socket, "getaddrinfo", lambda *args, **kwargs: time.sleep(5) or [])
    started = time.monotonic()
    assert route.addresses_of("pve.example", limit=0.3) == []
    assert time.monotonic() - started < 2


def test_a_refused_setting_of_the_hop_names_where_to_change_it():
    with pytest.raises(route.HopRefused) as refused:
        route.hop_settings(host="pve", key="/keys/a b", control_dir="/tmp/cp")
    assert "set ansible_ssh_private_key_file on the Proxmox host's entry" in str(refused.value)
    assert str(refused.value).endswith("or set vm_ssh_route: direct")


def test_an_open_that_lost_the_race_is_ended_at_once_and_the_winner_s_connection_found(hop_at):
    started = time.monotonic()
    assert route.hop_held(hop_at("race"), "accept-new", {"open": 30})["ok"]
    assert time.monotonic() - started < 10


def test_an_open_that_never_returns_is_ended_at_its_limit_and_the_check_decides(hop_at):
    started = time.monotonic()
    held = route.hop_held(hop_at("hang-then-opened"), "accept-new", {"open": 1})
    assert held["ok"] and 1 <= time.monotonic() - started < 10


def test_making_sure_makes_the_control_directory_its_owner_s_alone(tmp_path, short_dir):
    ssh = _program(tmp_path / "ssh", STAND_IN)
    (tmp_path / "mode").write_text("open")
    hop = route.hop_settings(ssh=ssh, user="root", host="pve", control_dir=f"{short_dir}/cp")
    assert route.hop_held(hop, "accept-new")["ok"]
    assert (Path(short_dir) / "cp").stat().st_mode & 0o777 == 0o700


def test_an_open_that_fails_with_no_connection_found_says_what_ssh_said(hop_at):
    held = route.hop_held(hop_at("refused"), "accept-new")
    assert held == {"ok": False, "opened": False, "said": "root@pve: Permission denied (publickey)."}


def test_a_stop_takes_no_new_sessions_and_is_never_an_exit(hop_at, tmp_path):
    hop = hop_at("open")
    route.hop_held(hop, "accept-new")
    assert route.hop_stop(hop, "yes") and not route.hop_check(hop, "yes")
    assert "-O exit" not in (tmp_path / "calls").read_text()


def test_a_program_that_is_not_there_is_ssh_s_failure_said(tmp_path):
    hop = route.hop_settings(ssh=str(tmp_path / "absent"), host="pve", control_dir="/tmp/cp")
    verdict = route.hop_forward(hop, "192.0.2.77", "yes")
    assert verdict["why"] == "failed" and "absent" in verdict["said"]


@pytest.mark.parametrize("shell", ["/bin/sh", "/bin/bash", "/bin/dash", "/bin/zsh", "/usr/bin/zsh"])
def test_the_real_ssh_hands_the_hop_s_command_its_port_and_nothing_else(tmp_path, shell):
    # ssh -G cannot hold this: it passes a ProxyCommand with a bad token. The real ssh runs it, through the shell,
    # after its own expansion; the hop's ssh is a stand-in that records what it was given.
    ssh = shutil.which("ssh")
    if not ssh:
        pytest.skip("no ssh client here")
    if not os.access(shell, os.X_OK):
        pytest.skip(f"no {shell} here")
    record = tmp_path / "argv"
    recorder = f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > {record}\nexit 1\n'
    inner = _program(tmp_path / "inner-ssh", recorder)
    hop = route.hop_settings(ssh=inner, user="root", host="pve.example.ts.net", control_dir="/tmp/cp")
    (tmp_path / "ssh_config").write_text("")
    words = shlex.split(route.hop_option(hop, "192.0.2.77", "accept-new"))
    argv = [ssh, "-F", str(tmp_path / "ssh_config"), "-o", "BatchMode=yes", "-o", "UserKnownHostsFile=/dev/null"]
    argv += ["-o", "GlobalKnownHostsFile=/dev/null", *words, "-p", "2222", "dev@192.0.2.77", "true"]
    # ssh runs a ProxyCommand through the account's shell: each that a person may have, the words the same.
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "SHELL": shell}
    subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=False)
    assert record.read_text().splitlines() == [*route.hop_argv(hop, "192.0.2.77", "accept-new", port=2222)[1:]]


def test_the_core_names_no_host_key_policy_of_its_own():
    # The project's two places that take a new host key are counted by tests/unit/test_controller_writes.py; the core
    # is handed its policy by each caller.
    assert "StrictHostKeyChecking=accept-new" not in (REPO / "lib" / "claude_vm" / "route.py").read_text()


def test_a_first_contact_that_fails_says_where_to_look_by_the_route():
    # Through the host, ssh says only that the connection closed: the host's view is just status's. Under direct, the
    # setting that goes through it. Nothing for a VM that brings its own jump, nor for a run that settled no route.
    through = route.vm_unanswered(ROUTE, "-J root@pve.example.ts.net", "alpha", " --host nuc")
    assert through == (
        "It did not answer through pve (root@pve.example.ts.net): `just status alpha --host nuc` says what the host"
        " reports for it."
    )
    assert "vm_ssh_route: host" in route.vm_unanswered({"route": "direct"}, "", "alpha")
    assert route.vm_unanswered(ROUTE, "", "alpha") == "" and route.vm_unanswered({}, "", "alpha") == ""


def test_the_words_of_a_hop_that_cannot_be_used_name_every_reply_and_the_way_out():
    login = route.login_said({**ROUTE, "setting": "auto"}, "root@pve: Permission denied (publickey).\r\n")
    assert "ssh could not log in there: root@pve: Permission denied (publickey). Its key" in login
    assert "`ssh root@pve.example.ts.net` here shows which" in login and "vm_ssh_route: direct" in login
    forbidden = route.forward_said(ROUTE, "alpha", "192.0.2.77", {"why": "prohibited", "said": "administratively"})
    for setting in ("AllowTcpForwarding", "PermitOpen", "DisableForwarding", "restrict", "Tailscale's own SSH"):
        assert setting in forbidden, setting
    # Under host, no word of auto's.
    assert "vm_ssh_route: direct" not in forbidden
    assert route.forward_said(ROUTE, "alpha", "192.0.2.77", {"why": "answered"}) == ""


def test_the_login_s_words_name_the_hop_s_own_port_and_key():
    hop = route.hop_settings(user="forwarder", host="100.64.0.7", port=2222, key="/k/id_ed25519", control_dir="/tmp/cp")
    login = route.login_said({"route": "host", "hop": hop, "policy": "accept-new"}, "Permission denied (publickey).")
    assert "`ssh -p 2222 -i /k/id_ed25519 forwarder@100.64.0.7` here shows which" in login


def test_a_changed_host_key_is_said_by_ssh_s_last_lines_without_its_banner():
    said = "\n".join(
        [
            "@" * 59,
            "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @",
            "@" * 59,
            "IT IS POSSIBLE THAT SOMEONE IS DOING SOMETHING NASTY!",
            "Someone could be eavesdropping on you right now (man-in-the-middle attack)!",
            "It is also possible that a host key has just been changed.",
            "The fingerprint for the ED25519 key sent by the remote host is",
            "SHA256:" + "A" * 43 + ".",
            "Please contact your system administrator.",
            "Add correct host key in /home/dev/.ssh/known_hosts to get rid of this message.",
            "Offending ED25519 key in /home/dev/.ssh/known_hosts:3",
            "  remove with:",
            "  ssh-keygen -f '/home/dev/.ssh/known_hosts' -R '100.64.0.7'",
            "Host key for 100.64.0.7 has changed and you have requested strict checking.",
            "Host key verification failed.",
        ]
    )
    login = route.login_said(ROUTE, said)
    assert "@" * 3 not in login and "WARNING" not in login
    assert "Host key for 100.64.0.7 has changed and you have requested strict checking. Host key verification" in login
    assert "ssh-keygen -f '/home/dev/.ssh/known_hosts' -R '100.64.0.7'" in login


def _real_ssh() -> str:
    ssh = shutil.which("ssh")
    if not ssh:
        pytest.skip("no ssh client here")
    return ssh


def test_ssh_itself_shows_the_hop_s_command_word_for_word():
    shown = route.ssh_shown(_real_ssh(), shlex.split(route.hop_option(HOP, "192.0.2.77", "accept-new")), "192.0.2.77")
    assert shown["ok"] and shown["proxyjump"] == ""
    assert shown["proxycommand"] == " ".join(route.hop_argv(HOP, "192.0.2.77", "accept-new"))
    assert route.vm_route_refusal(ROUTE, "192.0.2.77", True, shown) == ""


# What ssh takes, for a VM given the hop after these words: each before the hop wins, or none leaves no proxy at all.
@pytest.mark.parametrize(
    ("before", "why"),
    [
        (["-o", "ProxyCommand=none"], "ssh would take no proxy for it, dialling it from here, not through the hop"),
        (["-o", "ProxyCommand=NONE"], "ssh would take no proxy for it, dialling it from here, not through the hop"),
        (["-o", '"ProxyCommand" none'], "ssh would take no proxy for it, dialling it from here, not through the hop"),
        (["-o=ProxyCommand=none"], "ssh would take no proxy for it, dialling it from here, not through the hop"),
        (["-o", "ProxyJump=bastion"], "ssh would take ProxyJump bastion for it, not the hop"),
        (["-o", "ProxyCommand=nc %h %p"], "ssh would take ProxyCommand nc %h %p for it, not the hop"),
    ],
)
def test_a_proxy_before_the_hop_is_what_ssh_itself_shows_and_refused(before, why):
    words = [*before, *shlex.split(route.hop_option(HOP, "192.0.2.77", "accept-new"))]
    shown = route.ssh_shown(_real_ssh(), words, "192.0.2.77")
    assert route.vm_route_refusal(ROUTE, "192.0.2.77", True, shown) == why


def test_a_jump_after_the_hop_is_ssh_s_refusal_of_the_line():
    words = [*shlex.split(route.hop_option(HOP, "192.0.2.77", "accept-new")), "-J", "none"]
    shown = route.ssh_shown(_real_ssh(), words, "192.0.2.77")
    assert not shown["ok"] and "Cannot specify -J with ProxyCommand" in shown["said"]
    assert route.vm_route_refusal(ROUTE, "192.0.2.77", True, shown).startswith("ssh refused its options: ")


def test_a_vm_given_a_jump_must_show_one_on_its_whole_line_and_on_its_own_args_alone():
    ssh = _real_ssh()
    jump = route.ssh_shown(ssh, ["-o", "ProxyJump=bastion"], "192.0.2.77")
    assert jump["proxyjump"] == "bastion"
    assert route.vm_route_refusal(ROUTE, "192.0.2.77", False, jump, alone=jump) == ""
    # ssh_args holds a none, which ssh reads before the jump the VM is given.
    beaten = route.ssh_shown(ssh, ["-o", "ProxyCommand=None", "-o", "ProxyJump=bastion"], "192.0.2.77")
    assert route.vm_route_refusal(ROUTE, "192.0.2.77", False, beaten, alone=jump) == (
        "it is given a jump of its own, and ssh would take none, dialling it from here"
    )
    plain = route.ssh_shown(ssh, [], "192.0.2.77")
    assert "common and extra args alone" in route.vm_route_refusal(ROUTE, "192.0.2.77", False, jump, alone=plain)


def test_a_vm_whose_ansible_host_is_a_name_is_refused_by_name():
    shown = {"ok": True, "proxycommand": "", "proxyjump": "", "said": ""}
    assert route.vm_route_refusal(ROUTE, "vm01.lan", True, shown) == (
        "its ansible_host, vm01.lan, is not the address it was published with"
    )


def test_an_ssh_that_cannot_be_asked_is_a_refusal(tmp_path):
    shown = route.ssh_shown(str(tmp_path / "absent"), [], "192.0.2.77")
    assert not shown["ok"] and "absent" in shown["said"]
    assert route.vm_route_refusal(ROUTE, "192.0.2.77", False, shown).startswith("ssh refused its options: ")
