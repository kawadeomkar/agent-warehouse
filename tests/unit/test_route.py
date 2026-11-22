"""lib/claude_vm/route.py: the route to the VMs, the hop through the Proxmox host, the hop's own ssh under its limits.

The hop's ssh runs against stand-ins written here, which play what OpenSSH does (docs/architecture/custom-code.md
§7.17); the one real ssh run is given no config of the developer's and connects nowhere.
"""

from __future__ import annotations

import getpass
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
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


def test_the_new_engine_s_hop_pins_the_display_and_local_commands_off_first_on_every_line():
    # A person's Host * of ForwardX11 yes, or PermitLocalCommand yes with a LocalCommand, reaches the hop's own ssh,
    # which reads their config: the open's master, or a ProxyCommand's ssh that finds none live and becomes one, would
    # run that command here. Today's callers pass no pinned, and their lines are as above.
    settings = {"ssh": "/usr/bin/ssh", "user": "forwarder", "host": "100.64.0.7", "control_dir": "/tmp/cp"}
    today, hop = route.hop_settings(**settings), route.hop_settings(**settings, pinned=True)
    assert hop == {**today, "pinned": True} and "pinned" not in today
    pins = ["-o", "ForwardX11=no", "-o", "PermitLocalCommand=no"]
    for line in (
        lambda one: route.hop_argv(one, "192.0.2.77", "accept-new"),
        lambda one: route.hop_argv(one, "192.0.2.77", "yes", port=22, shared=False, level="VERBOSE"),
        lambda one: route.hop_open_argv(one, "accept-new"),
    ):
        assert line(hop) == [line(today)[0], *pins, *line(today)[1:]]
        assert "ForwardX11=no" not in line(today) and "PermitLocalCommand=no" not in line(today)
    # A machine's ProxyCommand is that line, and so is the line ssh must show for it (vm_route_refusal).
    settled = {"route": "host", "hop": hop, "policy": "accept-new"}
    proxy = " ".join(route.hop_argv(hop, "192.0.2.77", "accept-new"))
    assert route.vm_hop(settled, "192.0.2.77")["option"] == "-o " + shlex.quote(f"ProxyCommand={proxy}")
    shown = {"ok": True, "proxycommand": proxy, "proxyjump": "", "said": ""}
    assert route.vm_route_refusal(settled, "192.0.2.77", True, shown) == ""
    unpinned = " ".join(route.hop_argv(today, "192.0.2.77", "accept-new"))
    assert route.vm_route_refusal(settled, "192.0.2.77", True, {**shown, "proxycommand": unpinned}) != ""


def test_a_socket_past_the_length_is_left_to_the_new_engine_where_it_asks():
    # Today's callers are refused with today's sentence; the new engine says it in its own words (ssh/connections.py).
    long = "/" + "d" * 80
    with pytest.raises(route.HopRefused, match="set ansible_control_path_dir to a shorter directory"):
        route.hop_settings(host="pve", control_dir=long)
    hop = route.hop_settings(host="pve", control_dir=long, sized=False)
    assert hop["socket"].startswith(f"{long}/awh-") and not route.socket_fits(hop["socket"])
    assert route.socket_fits(route.hop_settings(host="pve", control_dir="/tmp/cp")["socket"])
    # sized holds the length alone: a setting that is not one plain word is refused whatever it says.
    with pytest.raises(route.HopRefused, match="control directory"):
        route.hop_settings(host="pve", control_dir="/tmp/%C", sized=False)


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
        ("root@other.example", "names more than an account: set vm_ssh_jump"),
        ("forwarder:2222", "names more than an account: set vm_ssh_jump"),
        ("-oProxyCommand=id", "is not an account name: set vm_ssh_jump"),
        ("for warder", "is not an account name: set vm_ssh_jump"),
    ],
)
def test_vm_ssh_jump_that_is_more_than_an_account_is_refused_by_name(value, said):
    with pytest.raises(route.HopRefused, match=said) as refused:
        route.account(value)
    # Short enough to be read whole where a line is cut, the way out beside it (just status's line and its hint).
    assert len(refused.value.reason) < 60
    assert refused.value.way_out == (
        "set vm_ssh_jump on the host's entry to one account on that host, or vm_ssh_route: direct"
    )


# A stand-in for the hop's ssh, playing OpenSSH's control commands and its forward by a mode file: it logs each call,
# a master is a file at the socket's path - one that holds "stale" is a socket nothing listens at - an open makes one
# unless the mode says otherwise, and a forward ends as the file "forward-<address>" says, else the file "forward",
# else the mode. An open beside a live master is a client with ControlMaster=auto, which asks the host for a session
# (logged in "sessions"), and with ControlMaster=yes says the socket exists and goes on as a connection of its own; at
# a stale one, auto takes the path over and yes says it exists. "lost" plays an open whose master another caller's
# unlink took the path from, "lost-once" that the first time only. A master whose socket holds "quiet" says it runs
# and carries nothing, as one whose far side froze: a forward over it fails as its client does. At LogLevel=VERBOSE a
# forward whose login is done says so first, as OpenSSH does: every forward but "stalled", which waits before any
# login, and "login", refused it.
STAND_IN = r"""#!/bin/sh
here=$(dirname "$0")
printf '%s\n' "$*" >> "$here/calls"
socket=$(printf '%s\n' "$@" | sed -n 's/^ControlPath=//p' | head -1)
master=$(printf '%s\n' "$@" | sed -n 's/^ControlMaster=//p' | head -1)
mode=$(cat "$here/mode" 2>/dev/null)
target=$(printf '%s\n' "$@" | sed -n '/^-W$/{n;p;}' | head -1 | cut -d: -f1)
forward=$(cat "$here/forward-$target" 2>/dev/null || cat "$here/forward" 2>/dev/null || printf '%s' "$mode")
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
    lost) exit 0 ;;
    lost-once) [ -e "$here/lost" ] && { : > "$socket"; exit 0; }; : > "$here/lost"; exit 0 ;;
    *) : > "$socket"; exit 0 ;;
    esac ;;
*" -W "*) case " $* " in *" ControlPath=none "*) ;; *) [ -e "$socket" ] && [ "$(cat "$socket")" = quiet ] && {
        echo "mux_client_request_session: read from master failed: Broken pipe" >&2; exit 255; } ;; esac
    case $forward in stalled|login) ;; *) case " $* " in *" LogLevel=VERBOSE "*)
        for last in "$@"; do :; done
        echo "Authenticated to ${last#*@} ([${last#*@}]:22) using \"publickey\"." >&2 ;; esac ;; esac
    case $forward in
    answer) echo "SSH-2.0-OpenSSH_9.6"; exit 0 ;;
    wordless) exit 0 ;;
    forged) echo "channel 0: open failed: administratively prohibited: open failed"; exit 0 ;;
    closed) echo "channel 0: open failed: connect failed: Connection refused" >&2; echo "stdio forwarding failed" >&2
        exit 255 ;;
    silent|stalled) exec sleep 60 ;;
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
    ("mode", "usable", "why", "banner"),
    [
        ("answer", True, "answered", True),
        ("closed", True, "refused", False),
        # Ended at its limit: after ssh said its login was done, the far end's silence; before, the login's own.
        ("silent", True, "silent", False),
        ("stalled", True, "stalled", False),
        ("prohibited", False, "prohibited", False),
        ("login", False, "failed", False),
        # What arrives on the forward is the far end's to write: words of a refusal there are not the host's, and only
        # whether they begin as an SSH server's do is kept.
        ("forged", True, "answered", False),
        ("wordless", True, "answered", False),
    ],
)
def test_the_forward_check_is_decided_from_exit_status_and_standard_error(hop_at, mode, usable, why, banner):
    started = time.monotonic()
    verdict = route.hop_forward(hop_at(mode), "192.0.2.77", "accept-new", limit=1.5)
    assert (verdict["usable"], verdict["why"], verdict["banner"]) == (usable, why, banner)
    assert time.monotonic() - started < 10
    if why == "failed":
        assert "Permission denied" in verdict["said"]
    # What VERBOSE adds is never in the words a person reads.
    assert "Authenticated" not in verdict["said"]


@pytest.mark.parametrize(
    "said",
    [
        'Authenticated to pve ([100.64.0.7]:22) using "publickey".',
        'Authenticated to pve (via proxy) using "publickey".',
        "Authenticated to pve ([100.64.0.7]:22).",
    ],
)
def test_a_login_ssh_says_is_done_in_any_of_its_spellings_makes_a_forward_ended_at_its_limit_silent(said):
    assert route.forward_verdict(None, said, True)["why"] == "silent"
    assert route.forward_verdict(None, "", True)["why"] == "stalled"
    # Only the line's start: the words anywhere else are not ssh's.
    assert route.forward_verdict(None, f"debug: {said}", True)["why"] == "stalled"
    sent = "Transferred: sent 2416, received 2552 bytes, in 0.1 seconds"
    totals = "\n".join([said, sent, "Bytes per second: sent 1.0, received 1.0"])
    assert route.forward_verdict(0, totals, False) == {"usable": True, "why": "answered", "said": "", "banner": False}


def test_only_the_forward_check_runs_ssh_at_verbose(hop_at, tmp_path):
    hop = hop_at("open")
    route.hop_held(hop, "accept-new")
    route.hop_forward(hop, "192.0.2.77", "accept-new", limit=1.5)
    route.hop_stop(hop, "accept-new")
    lines = (tmp_path / "calls").read_text().splitlines()
    assert [" -W " in line for line in lines] == [False, False, False, True, False]
    for line in lines:
        assert ("LogLevel=VERBOSE" in line) == (" -W " in line) and ("LogLevel=INFO" in line) != (" -W " in line), line
    # A VM's ProxyCommand, whose words are in every VM's failure, stays at INFO.
    assert "LogLevel=INFO" in route.hop_option(hop, "192.0.2.77", "accept-new")
    assert "VERBOSE" not in route.hop_option(hop, "192.0.2.77", "accept-new")


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


def test_every_ssh_of_the_hop_still_running_is_ended_for_a_caller_that_ends_at_once(tmp_path, monkeypatch):
    # just status's Ctrl-C ends its process at once (os._exit), while a thread of its waits on the hop's login: the
    # login's ssh is ended first, never left to run on under init - one whose start is still under way too. That start
    # is held open here, its ssh running and Popen not yet returned, and let go only once end_all has run or must wait
    # for it: the moment that, met by chance on a loaded machine, once left the ssh running.
    monkeypatch.setattr(route, "_OVER", threading.Event())
    pid_file = tmp_path / "pid"
    program = _program(tmp_path / "slow", f"#!/bin/sh\necho $$ > {pid_file}\nexec sleep 60\n")
    started, go = threading.Event(), threading.Event()

    class Held(subprocess.Popen):
        def __init__(self, argv, *args, **kwargs):
            super().__init__(argv, *args, **kwargs)
            if argv == [program]:
                started.set()
                go.wait(10)

    monkeypatch.setattr(subprocess, "Popen", Held)
    ended: list = []
    waiting = threading.Thread(target=lambda: ended.append(route._run([program], 60)), daemon=True)
    waiting.start()
    try:
        deadline = time.monotonic() + 10
        while not (started.is_set() and pid_file.exists() and pid_file.read_text().strip()):
            assert time.monotonic() < deadline, "the stand-in ssh never started"
            time.sleep(0.05)
        ender = threading.Thread(target=route.end_all, daemon=True)
        ender.start()
        while ender.is_alive() and not route._LIVE_LOCK.locked():
            assert time.monotonic() < deadline, "end_all neither ran nor waited"
            time.sleep(0.01)
        go.set()
        let_go = time.monotonic()
        waiting.join(10)
        ender.join(10)
        assert ended and ended[0]["rc"] is not None and time.monotonic() - let_go < 5
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
        assert not route._LIVE
    finally:
        go.set()
        route.end_all()  # a run of this test that failed leaves no ssh behind it
        waiting.join(10)


def test_no_ssh_of_the_hop_s_is_started_once_the_run_has_ended(tmp_path, monkeypatch):
    # A thread of just status's that came to start one after its Ctrl-C ended the others would start it just before
    # os._exit, to run on by itself.
    monkeypatch.setattr(route, "_OVER", threading.Event())
    ran = tmp_path / "ran"
    program = _program(tmp_path / "ssh", f"#!/bin/sh\ntouch {ran}\n")
    route.end_all()
    said = f"{program}: not started, the run ended"
    assert route._run([program], 5) == {"rc": 255, "said": said, "killed": False, "banner": False}
    assert not ran.exists() and not route._LIVE


@pytest.mark.parametrize("asked", ["route_settle", "route_settle, the new engine's", "ssh_shown"])
def test_an_ssh_g_being_started_is_ended_by_end_all_with_the_rest(tmp_path, monkeypatch, asked):
    # route_settle's ssh -G reads the person's own ssh config, whose Match exec may run anything: one that just status's
    # Ctrl-C meets while it starts is ended with the hop's other ssh, never left to run on under init. Its start is held
    # open here, the ssh -G running and Popen not yet returned, as the hop's login's is above.
    monkeypatch.setattr(route, "_OVER", threading.Event())
    pid_file = tmp_path / "pid"
    ssh = _program(tmp_path / "ssh", f"#!/bin/sh\necho $$ > {pid_file}\nexec sleep 60\n")
    started, go = threading.Event(), threading.Event()

    class Held(subprocess.Popen):
        def __init__(self, argv, *args, **kwargs):
            super().__init__(argv, *args, **kwargs)
            if argv[:1] == [ssh]:
                started.set()
                go.wait(10)

    monkeypatch.setattr(subprocess, "Popen", Held)
    hop = route.hop_settings(ssh=ssh, host="192.0.2.10", control_dir="/tmp/cp")
    calls = {
        "route_settle": lambda: route.route_settle("auto", hop),
        "route_settle, the new engine's": lambda: route.route_settle("auto", hop, signalled_settles=False),
        "ssh_shown": lambda: route.ssh_shown(ssh, [], "192.0.2.10"),
    }
    answered: list = []

    def ask():
        try:
            answered.append(calls[asked]())
        except route.HopRefused as refused:
            answered.append(refused)

    waiting = threading.Thread(target=ask, daemon=True)
    waiting.start()
    try:
        deadline = time.monotonic() + 10
        while not (started.is_set() and pid_file.exists() and pid_file.read_text().strip()):
            assert time.monotonic() < deadline, "the stand-in ssh -G never started"
            time.sleep(0.05)
        ender = threading.Thread(target=route.end_all, daemon=True)
        ender.start()
        go.set()
        let_go = time.monotonic()
        waiting.join(10)
        ender.join(10)
        assert answered and time.monotonic() - let_go < 5
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
        assert not route._LIVE
    finally:
        go.set()
        route.end_all()  # a run of this test that failed leaves no ssh behind it
        waiting.join(10)
    [answer] = answered
    if asked == "route_settle":
        # Today's callers: as an ssh -G that could not answer, the address as written.
        assert answer == {"route": "direct", "addresses": ["192.0.2.10"], "dialled": "192.0.2.10"}
    elif asked == "ssh_shown":
        assert answer == {"ok": False, "proxycommand": "", "proxyjump": "", "said": f"{ssh} -G exited -9"}
    else:
        assert isinstance(answer, route.HopRefused) and "ended by a signal (9)" in str(answer)


def test_no_ssh_g_is_started_once_the_run_has_ended(tmp_path, monkeypatch):
    monkeypatch.setattr(route, "_OVER", threading.Event())
    ran = tmp_path / "ran"
    ssh = _program(tmp_path / "ssh", f"#!/bin/sh\ntouch {ran}\necho 'hostname 100.101.102.103'\n")
    hop = route.hop_settings(ssh=ssh, host="192.0.2.10", control_dir="/tmp/cp")
    route.end_all()
    # Today's callers: as an ssh that cannot be run, the address as written; the new engine's settle nothing.
    assert route.route_settle("auto", hop) == {"route": "direct", "addresses": ["192.0.2.10"], "dialled": "192.0.2.10"}
    with pytest.raises(route.HopRefused, match="was not started, since the run is ending, so the route under"):
        route.route_settle("auto", hop, signalled_settles=False)
    said = f"{ssh}: not started, the run ended"
    assert route.ssh_shown(ssh, [], "192.0.2.10") == {"ok": False, "proxycommand": "", "proxyjump": "", "said": said}
    assert not ran.exists() and not route._LIVE


def test_under_auto_an_ssh_g_past_its_limit_settles_nothing_for_the_new_engine(tmp_path):
    # Its kill at the limit is a signal too: read as no answer, it would settle from the address as written, direct,
    # where the person's own configuration may send the host to a tailnet. Today's callers settle as they did.
    ssh = _program(tmp_path / "ssh", "#!/bin/sh\nexec sleep 30\n")
    hop = route.hop_settings(ssh=ssh, host="192.0.2.10", control_dir="/tmp/cp")
    started = time.monotonic()
    with pytest.raises(route.HopRefused) as refused:
        route.route_settle("auto", hop, limit=0.5, signalled_settles=False)
    assert str(refused.value) == (
        "ssh -G for the Proxmox host said nothing of where ssh dials for it within 0.5 s, so the route under"
        " vm_ssh_route: auto is not settled: run it again, or set vm_ssh_route: direct or host"
    )
    today = route.route_settle("auto", hop, limit=0.5)
    assert today == {"route": "direct", "addresses": ["192.0.2.10"], "dialled": "192.0.2.10"}
    assert time.monotonic() - started < 10 and not route._LIVE


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


# An ssh -G whose person's Match exec runs a program of its own, in the ssh's own process group, that would outlive it:
# it writes that program's pid and its own beside itself, then waits for the program, as ssh -G waits for its Match
# exec. Ended by its pid alone, it leaves the program running.
MATCH_EXEC = (
    "#!/bin/sh\n"
    'sleep 60 > /dev/null 2>&1 &\necho $! > "$0.child.part" && mv "$0.child.part" "$0.child"\n'
    'echo $$ > "$0.pid.part" && mv "$0.pid.part" "$0.pid"\nwait\n'
)


def _match_exec(tmp_path: Path) -> str:
    return _program(tmp_path / "ssh", MATCH_EXEC)


def _its_pids(ssh: str) -> tuple[int, int]:
    """The stand-in's pid and its Match exec's program's, once both are written."""
    deadline = time.monotonic() + 10
    while not (Path(f"{ssh}.pid").is_file() and Path(f"{ssh}.child").is_file()):
        assert time.monotonic() < deadline, "the stand-in ssh -G never started its Match exec's program"
        time.sleep(0.02)
    return int(Path(f"{ssh}.pid").read_text()), int(Path(f"{ssh}.child").read_text())


def _gone(pid: int) -> bool:
    """Whether pid has ended: no such process, or one dead that nothing has reaped yet."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return not state or state.startswith("Z")


def _until_gone(*pids: int) -> bool:
    deadline = time.monotonic() + 5
    while not all(_gone(pid) for pid in pids):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def _end(*pids: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


# Each road an ssh of the new engine's, started in a process group of its own, is ended on: end_all, for a caller about
# to end at once, met by route_settle's ssh -G, ssh_shown's and _run's ssh; and the three time-outs of _asked and _run.
LEADERS = {
    "end_all, route_settle's ssh -G": ("end_all", "route_settle"),
    "end_all, ssh_shown's ssh -G": ("end_all", "ssh_shown"),
    "end_all, _run's ssh": ("end_all", "_run"),
    "_asked past its limit, route_settle's ssh -G": ("limit", "route_settle"),
    "_asked past its limit, ssh_shown's ssh -G": ("limit", "ssh_shown"),
    "_run past its limit": ("limit", "_run"),
    "_run with its caller ended meanwhile": ("caller", "_run"),
}


@pytest.mark.parametrize("road", LEADERS)
def test_an_ssh_of_the_new_engine_s_is_ended_with_its_group_and_the_match_exec_s_program_with_it(
    tmp_path, monkeypatch, road
):
    # Its ssh -G reads the person's own ssh config, whose Match exec may run anything: an ssh ended by its pid alone
    # leaves that program running under init (B24's hand-back; the owner's word of 2026-11-22). One started with
    # process_group=0, the new engine's, leads its group, and each of its ends ends that group.
    monkeypatch.setattr(route, "_OVER", threading.Event())
    how, caller = LEADERS[road]
    ssh = _match_exec(tmp_path)
    hop = route.hop_settings(ssh=ssh, host="192.0.2.10", control_dir="/tmp/cp")
    limit = 0.5 if how == "limit" else 30
    calls = {
        "route_settle": lambda: route.route_settle("auto", hop, limit=limit, process_group=0, signalled_settles=False),
        "ssh_shown": lambda: route.ssh_shown(ssh, [], "192.0.2.10", limit=limit, process_group=0),
        "_run": lambda: route._run([ssh], limit, process_group=0),
    }
    if how == "caller":
        real_sleep = time.sleep

        def interrupted(seconds):
            # The caller's thread alone ended, once the stand-in has started its program: this test's waits sleep on.
            if threading.current_thread().name == "asking" and Path(f"{ssh}.pid").is_file():
                raise KeyboardInterrupt
            real_sleep(seconds)

        monkeypatch.setattr(route.time, "sleep", interrupted)
    answered: list = []

    def ask():
        try:
            answered.append(calls[caller]())
        except (route.HopRefused, KeyboardInterrupt) as ended:
            answered.append(ended)

    waiting = threading.Thread(target=ask, name="asking", daemon=True)
    waiting.start()
    pids: tuple[int, ...] = ()
    try:
        pids = _its_pids(ssh)
        if how == "end_all":
            route.end_all()
        waiting.join(15)
        assert answered and not waiting.is_alive()
        assert _until_gone(*pids), f"left running: {[pid for pid in pids if not _gone(pid)]}"
        assert not route._LIVE and not route._LEADING
    finally:
        _end(*pids)
        route.end_all()  # a run of this test that failed leaves no ssh behind it
        waiting.join(10)


# Today's caller, just status, in a process group of its own as a shell starts one, with a program of its own in its
# group: it runs the stand-in ssh as it does (no process_group), ends it at its limit or by end_all, and then says what
# it saw, which it can only where its own group was never signalled.
TODAYS_CALLER = r"""
import json, os, subprocess, sys, threading, time
lib, ssh, how = sys.argv[1:4]
sys.path.insert(0, lib)
from claude_vm import route
bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
if how == "limit":
    ended = route._run([ssh], 0.5)
else:
    got = []
    waiting = threading.Thread(target=lambda: got.append(route._run([ssh], 30)), daemon=True)
    waiting.start()
    deadline = time.monotonic() + 10
    while not (os.path.isfile(ssh + ".pid") and os.path.isfile(ssh + ".child")):
        if time.monotonic() > deadline:
            sys.exit("the stand-in ssh never started")
        time.sleep(0.02)
    route.end_all()
    waiting.join(10)
    ended = got[0]
time.sleep(0.5)
print(json.dumps({"ended": ended, "bystander": bystander.poll() is None}), flush=True)
bystander.kill()
"""


@pytest.mark.parametrize("how", ["limit", "end_all"])
def test_today_s_callers_ssh_is_ended_by_its_pid_alone_and_their_own_group_is_never_signalled(tmp_path, how):
    ssh = _match_exec(tmp_path)
    pids: tuple[int, ...] = ()
    try:
        ran = subprocess.run(
            [sys.executable, "-I", "-c", TODAYS_CALLER, str(REPO / "lib"), ssh, how],
            capture_output=True,
            text=True,
            timeout=60,
            process_group=0,
        )
        pids = _its_pids(ssh)
        assert ran.returncode == 0, ran.stderr
        said = json.loads(ran.stdout)
        assert said["bystander"] is True
        # Killed at its limit, rc None; or by end_all, its status -9.
        assert said["ended"]["rc"] == (None if how == "limit" else -signal.SIGKILL), said
        stand_in, program = pids
        assert _until_gone(stand_in)
        # The Match exec's program, in the caller's group, lives on: a kill of the group would have reached the caller.
        assert not _gone(program)
    finally:
        _end(*pids)


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


def test_an_open_whose_path_another_caller_removed_is_made_sure_of_once_more(hop_at, tmp_path):
    # Another caller's check found a socket nothing listened at a moment before this open made its master there, and
    # removed it: this open ended by itself, and no connection answers at the path. Once more, and only once.
    assert route.hop_held(hop_at("lost-once"), "accept-new") == {"ok": True, "opened": True, "said": ""}
    assert [call if call in ("check", "stop") else "-N" for call in _calls(tmp_path)] == [
        "check",
        "-N",
        "check",
        "check",
        "-N",
        "check",
    ]


def test_a_failure_to_make_sure_of_the_connection_always_says_why(hop_at, tmp_path):
    held = route.hop_held(hop_at("lost"), "accept-new")
    assert not held["ok"] and held["said"].startswith("no connection answered at /tmp/aw-")
    assert held["said"].endswith("after ssh made one there (it exited 0)")
    assert len([call for call in _calls(tmp_path) if call not in ("check", "stop")]) == 2


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


@pytest.mark.parametrize(
    ("said", "unanswered"),
    [
        ("Connection closed by UNKNOWN port 65535", True),
        ("Connection timed out during banner exchange", True),
        ("Connection to UNKNOWN port 65535 timed out", True),
        ("Stdio forwarding request failed: Session open refused by peer", True),
        ("kex_exchange_identification: Connection closed by remote host", True),
        ("Received disconnect from UNKNOWN port 65535:2: Too many authentication failures", False),
        ("Received disconnect from UNKNOWN port 65535:11: Bye\nConnection closed by UNKNOWN port 65535", False),
        # just status's own line for a VM that took the connection and said nothing: its sshd is up.
        ("it took the connection and said nothing for 20 s: timed out", False),
        ("Permission denied (publickey).", False),
        ("Host key verification failed.", False),
    ],
)
def test_only_ssh_s_words_for_a_connection_that_ended_before_the_vm_spoke_are_the_hop_s(said, unanswered):
    # One list, read by just status and just destroy alike.
    assert route.hop_unanswered(said) is unanswered


def test_a_forward_whose_login_never_finished_is_said_as_the_login_s(hop_at):
    stalled = route.forward_said(ROUTE, "alpha", "192.0.2.77", {"why": "stalled", "said": ""})
    assert stalled.startswith(
        "The login to pve (root@pve.example.ts.net) for a forward of its own to alpha at 192.0.2.77 did not finish"
        " within 15 s, so forwarding there was not checked: `ssh root@pve.example.ts.net` here shows why"
    )
    assert "No answer from" not in stalled


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


def test_a_vm_whose_ansible_host_is_not_the_address_its_hop_goes_to_is_refused_by_that():
    # A host var or -e outranked its ansible_host, not the common args, which are the project's: the hop's own command
    # to the address it was published with.
    common = route.hop_option(HOP, "192.0.2.77", "accept-new")
    shown = route.ssh_shown(_real_ssh(), shlex.split(common), "192.0.2.99")
    assert route.vm_route_refusal(ROUTE, "192.0.2.99", True, shown) == (
        "its ansible_host, 192.0.2.99, is not the address it was published with, 192.0.2.77, which the hop forwards to"
    )
    assert route.unheld_detail(ROUTE, "192.0.2.99", True, {"ssh_common_args": common}) == ""
    # Another command than the hop's, there, is still said for what it is.
    other = route.hop_option({**HOP, "ssh": "/opt/other-ssh"}, "192.0.2.77", "accept-new")
    detail = route.unheld_detail(ROUTE, "192.0.2.99", True, {"ssh_common_args": other})
    assert detail == "ansible_ssh_common_args is set where it outranks the project's"


def test_a_vm_whose_ansible_host_is_a_name_is_refused_by_name():
    shown = {"ok": True, "proxycommand": "", "proxyjump": "", "said": ""}
    assert route.vm_route_refusal(ROUTE, "vm01.lan", True, shown) == (
        "its ansible_host, vm01.lan, is not the address it was published with"
    )


def test_an_ssh_that_cannot_be_asked_is_a_refusal(tmp_path):
    shown = route.ssh_shown(str(tmp_path / "absent"), [], "192.0.2.77")
    assert not shown["ok"] and "absent" in shown["said"]
    assert route.vm_route_refusal(ROUTE, "192.0.2.77", False, shown).startswith("ssh refused its options: ")


@pytest.mark.parametrize("via", ["", "-J root@pve.example.ts.net"])
def test_the_gh_line_names_the_hop_s_words_after_ssh_and_nothing_else(via):
    # The role's report and just status's are one function's (lib/claude_vm/status/lines.py).
    from claude_vm.status import lines

    facts = {
        "gh_found": True,
        "status_rc": 0,
        "signed_in": True,
        "login_done": False,
        "account": "octocat",
        "kind": "oauth",
        "is_ours": True,
        "lacks_workflow": True,
        "user": "dev",
        "host": "192.0.2.10",
        "via": via,
        "auth": True,
        "token_wanted": False,
        "token_set": False,
    }
    words = f" {via}" if via else ""
    said = lines.github_login_report(facts)
    said = said if isinstance(said, str) else json.dumps(said)
    assert f"ssh{words} -t dev@192.0.2.10 gh auth refresh -h github.com -s workflow" in said


def test_remote_control_s_printed_line_names_the_hop_s_words_after_ssh():
    text = (REPO / "roles" / "claude_code" / "tasks" / "remote_control.yml").read_text()
    assert "ssh{{ claude_code_ssh_words }} {{ claude_code_user }}@" in text
    words = (REPO / "roles" / "claude_code" / "vars" / "main.yml").read_text()
    assert (
        "claude_code_ssh_words: \"{{ (' ' ~ claude_code_ssh_via) if claude_code_ssh_via | default('') | length > 0"
        " else '' }}\"" in words
    )


def test_the_words_of_a_vm_destroy_keeps_and_of_one_it_deletes():
    # Kept only where the road is still what fails, named; deleted: the VM's own fault, what the host's forward
    # showed, never just status.
    answered = {"why": "answered"}
    made = {"ok": True, "said": ""}
    ended = "Failed to connect to the host via ssh: Connection closed by UNKNOWN port 65535"
    closed = {"unreachable": True, "msg": ended}
    assert route.vm_kept_said(ROUTE, "alpha", "192.0.2.77", answered, made, closed) == (
        "it answered a forward of its own through pve (root@pve.example.ts.net), but not this run's connection, made"
        " again"
    )
    # Its sshd spoke - a login refused, a host key that does not match - or its module failed: its own.
    for again in (
        {"unreachable": True, "msg": "Failed to connect to the host via ssh: dev@192.0.2.77: Permission denied."},
        {"unreachable": True, "msg": "Failed to connect to the host via ssh: Host key verification failed."},
        {"failed": True, "msg": "The module interpreter '/usr/bin/python3' was not found."},
        {"ping": "pong"},
    ):
        assert route.vm_kept_said(ROUTE, "alpha", "192.0.2.77", answered, made, again) == "", again
    # The connection could not be made again: the hop's, whatever the reach would have said.
    unmade = {"ok": False, "said": "root@pve: Permission denied (publickey)."}
    assert route.vm_kept_said(ROUTE, "alpha", "192.0.2.77", answered, unmade, {}) == (
        "it answered a forward of its own through pve (root@pve.example.ts.net), and the connection through the host"
        " could not be made again: root@pve: Permission denied (publickey)"
    )
    # A making sure that never ran says nothing of the connection.
    skipped = {"skipped": True, "changed": False}
    assert route.vm_kept_said(ROUTE, "alpha", "192.0.2.77", answered, skipped, closed).startswith("it answered")
    forbidden = route.vm_kept_said(ROUTE, "alpha", "192.0.2.77", {"why": "prohibited", "said": "administratively"})
    assert forbidden.startswith("pve (root@pve.example.ts.net) refused to forward to alpha at 192.0.2.77")
    stalled = route.vm_kept_said(ROUTE, "alpha", "192.0.2.77", {"why": "stalled", "said": ""})
    assert stalled.startswith("the login to pve (root@pve.example.ts.net) for a forward of its own to alpha")
    for why in ("refused", "silent", None):
        assert route.vm_kept_said(ROUTE, "alpha", "192.0.2.77", {"why": why}, made, closed) == "", why
    assert route.vm_kept_said({"route": "direct"}, "alpha", "192.0.2.77", answered, made, closed) == ""
    via = "-J root@pve.example.ts.net"
    refused = {"why": "refused", "said": "channel 0: open failed: connect failed: No route to host"}
    assert route.vm_gone_said(ROUTE, via, refused) == (
        "It did not answer through pve (root@pve.example.ts.net): the host's connection to it was refused (No route to"
        " host)."
    )
    assert route.vm_gone_said(ROUTE, via, {"why": "silent"}).endswith("it said nothing to the host within 15 s.")
    assert route.vm_gone_said(ROUTE, via) == "It did not answer through pve (root@pve.example.ts.net)."
    assert route.vm_gone_said(ROUTE, via, {"why": "answered"}).startswith(
        "It answered a forward of its own through pve (root@pve.example.ts.net), so the fault is the VM's own"
    )
    # Not asked: its connection worked, and nothing of the hop is said.
    assert route.vm_gone_said(ROUTE, via, {"skipped": True}) == ""
    assert route.vm_gone_said(ROUTE, "") == "" and "just status" not in route.vm_gone_said(ROUTE, via, refused)
    assert "vm_ssh_route: host" in route.vm_gone_said({"route": "direct"}, "")


# The forward check against the real OpenSSH: a throwaway sshd of this machine's own on its loopback, run as this user,
# with keys and files of the test's own, every ssh given -F /dev/null and this folder's files, so nothing of ~/.ssh is
# read or written. What ssh says at VERBOSE, where it says its login is done, is what it says at INFO once the
# forward's words are read: no line a person reads changes.
SSHD = """Port {port}
ListenAddress 127.0.0.1
HostKey {here}/host
PidFile {here}/sshd.pid
AuthorizedKeysFile {here}/authorized_keys
UsePAM no
StrictModes no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AllowTcpForwarding yes
"""


def _port() -> int:
    with socket.socket() as one:
        one.bind(("127.0.0.1", 0))
        return one.getsockname()[1]


def _listening(port: int, kind: str) -> socket.socket:
    """A listener on the loopback that takes each connection and, by ``kind``, says nothing (mute), closes it at once
    without a word (wordless), or sends an SSH server's banner and then nothing (banner): a hop whose login stalls."""
    import threading

    server = socket.socket()
    server.bind(("127.0.0.1", port))
    server.listen(8)
    held = []

    def serve():
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            if kind == "wordless":
                connection.close()
                continue
            if kind == "banner":
                connection.sendall(b"SSH-2.0-OpenSSH_9.6\r\n")
            held.append(connection)

    threading.Thread(target=serve, daemon=True).start()
    return server


@pytest.fixture
def real_sshd(tmp_path, short_dir):
    """(the hop for a client key, the sshd's port): keys ok, restricted (no forwarding: restrict) and other (not let
    in)."""
    sshd = "/usr/sbin/sshd"
    keygen = shutil.which("ssh-keygen")
    if not os.access(sshd, os.X_OK) or not keygen or not shutil.which("ssh"):
        pytest.skip("no sshd, ssh-keygen and ssh here")
    here = tmp_path / "sshd"
    here.mkdir(mode=0o700)
    for key in ("host", "ok", "restricted", "other"):
        subprocess.run([keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(here / key)], check=True, timeout=30)
    ok, restricted = ((here / f"{key}.pub").read_text().strip() for key in ("ok", "restricted"))
    (here / "authorized_keys").write_text(f"{ok}\nrestrict {restricted}\n")
    port = _port()
    (here / "known_hosts").write_text(f"[127.0.0.1]:{port} {(here / 'host.pub').read_text().strip()}\n")
    (here / "sshd_config").write_text(SSHD.format(port=port, here=here))
    log = here / "sshd.log"
    with log.open("w") as written:
        argv = [sshd, "-D", "-e", "-f", str(here / "sshd_config")]
        daemon = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=written, stderr=written)
    try:
        deadline = time.monotonic() + 10
        while True:
            if daemon.poll() is not None:
                pytest.skip(f"sshd would not start here as this user: {log.read_text()[-300:]}")
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    pytest.skip("sshd did not listen within 10 s")
                time.sleep(0.1)

        def hop(key: str, at: int) -> dict:
            ssh = _program(
                here / f"ssh-{key}",
                f"#!/bin/sh\nexec ssh -F /dev/null -o UserKnownHostsFile={here}/known_hosts"
                f" -o GlobalKnownHostsFile=/dev/null -o IdentityFile={here}/{key} -o IdentitiesOnly=yes"
                ' -o IdentityAgent=none "$@"\n',
            )
            return route.hop_settings(ssh=ssh, user=getpass.getuser(), host="127.0.0.1", port=at, control_dir=short_dir)

        yield hop, port
    finally:
        daemon.kill()
        daemon.wait()


@pytest.mark.parametrize(
    ("case", "key", "hop_is", "target", "why", "banner"),
    [
        ("the far end answers", "ok", "sshd", "sshd", "answered", True),
        ("nothing listens at the port", "ok", "sshd", "closed", "refused", False),
        ("the host forbids forwarding", "restricted", "sshd", "sshd", "prohibited", False),
        ("the host refuses the login", "other", "sshd", "sshd", "failed", False),
        ("the far end says nothing", "ok", "sshd", "mute", "silent", False),
        ("the far end closes it without a word", "ok", "sshd", "wordless", "answered", False),
        ("the host stops after its banner", "ok", "banner", "sshd", "stalled", False),
    ],
)
def test_each_verdict_of_the_real_ssh_says_at_verbose_what_it_says_at_info(
    real_sshd, case, key, hop_is, target, why, banner
):
    hop, port = real_sshd
    others = {kind: _port() for kind in ("closed", "mute", "wordless", "banner")}
    servers = [_listening(others[kind], kind) for kind in ("mute", "wordless", "banner")]
    try:
        settings = hop(key, port if hop_is == "sshd" else others[hop_is])
        at = port if target == "sshd" else others[target]
        verdict = route.hop_forward(settings, "127.0.0.1", "yes", port=at, limit=2)
        assert (verdict["why"], verdict["banner"]) == (why, banner), (case, verdict)
        info = route._run(route.hop_argv(settings, "127.0.0.1", "yes", port=at, shared=False), 2)
        assert route.forward_verdict(info["rc"], info["said"], info["killed"])["said"] == verdict["said"], case
        assert "Authenticated" not in verdict["said"]
    finally:
        for server in servers:
            server.close()


def test_a_vm_refused_before_it_connects_is_told_the_way_out_that_works_for_it():
    asked = route.unheld_said(ROUTE, [{"name": "alpha", "why": "ssh would take no proxy for it", "asked": True}])
    assert asked.endswith(
        "Set a jump of your own as ansible_ssh_common_args, which ssh, sftp and scp all get, where the VM is given it"
        " - with an empty value on the Proxmox host's own entry where it is set for every host, or set vm_ssh_route:"
        " direct."
    )
    assert "ansible_ssh_extra_args" not in asked
    unasked = route.unheld_said(
        ROUTE, [{"name": "alpha", "why": "its ssh, /opt/wrapper, could not be asked", "asked": False}]
    )
    assert "Set ansible_ssh_executable to an OpenSSH ssh, which is asked with -G" in unasked
    assert "a jump of your own" not in unasked and unasked.endswith(", or set vm_ssh_route: direct.")
