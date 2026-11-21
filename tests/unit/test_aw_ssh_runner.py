"""The SSH runner (lib/claude_vm/ssh/runner.py; docs/architecture/engine.md §16.6).

Every child here is a stand-in ssh, first on the test's own PATH by engine_guard.first_on_path, that records the words,
the environment and the standard input it was given in the test's own directory, and then does what the command it was
asked to run says: prints lines, prints one with no end or past the bound, says nothing, never ends, exits 255 as an
unreachable machine does, or holds its standard input open for a line. This machine's real ssh is run by its path for
ssh -G -F /dev/null alone, which reads no file and dials nothing, to show the values the built options hold. The rules
with a test of their own: in the new subpackages a child named ssh is started only in ssh/ (and route.py, today's,
outside them); no option is made of a value a machine supplied but its address; accept-new is written in ssh/ only for
a first use and the Proxmox host; and no ssh of the runner's carries a ControlMaster but no.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import harness
import pytest
from aw_kit import searched_for
from harness import REPO, repo_files
from test_aw_layout import NEW
from test_processes import _by_argv, _until

from claude_vm import checks, route
from claude_vm.fleet import paths
from claude_vm.runs import children
from claude_vm.runs import job as jobs
from claude_vm.secret import Secret
from claude_vm.ssh import runner

RUNNER = REPO / "lib" / "claude_vm" / "ssh" / "runner.py"
IDENTITY = "agent-warehouse-0f0e0d0c-0b0a-4908-8706-050403020100"
ADDRESS = "192.0.2.9"
# The shared control directory: short, as a socket's path must be, and never made - no stand-in opens a socket.
CONTROL = Path("/aw-test/cp")
CANARY = "unit-test-aw-ssh-runner-secret"
# Bounds a stand-in passes in a moment, each kind on its own: a line, the whole and what it says; the time to say
# anything; the time to end.
BYTES = runner.Limits(line=64, most=256, seconds=30, silent=30, said=64)
SILENCE = runner.Limits(seconds=30, silent=1)
DEADLINE = runner.Limits(seconds=3, silent=None)

# The stand-in ssh: what it was given, a call a line in calls.jsonl beside it; its standard input in stdin.bin where it
# reads one; then what the command (the word after the address) asks.
STAND_IN = r"""
import json, os, shlex, subprocess, sys, time

here = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]
with open(os.path.join(here, "calls.jsonl"), "a") as out:
    out.write(json.dumps({"argv": argv, "env": dict(os.environ), "pid": os.getpid(), "group": os.getpgrp()}) + "\n")
after = argv[argv.index("--") + 2:]
command = shlex.split(after[0]) if after else ["forward"]
what, rest = command[0], command[1:]


def say(*lines):
    for line in lines:
        sys.stdout.write(line + "\n")
    sys.stdout.flush()


def linger(marker):
    # A grandchild in the stand-in's own group, as ssh's ProxyCommand is: a kill of the group ends it too.
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)", marker])


def record(data):
    with open(os.path.join(here, "stdin.bin"), "ab") as out:
        out.write(data)


if what == "say":
    record(sys.stdin.buffer.read())
    say(*rest)
elif what == "exit":
    sys.stderr.write(rest[1] + "\n")
    sys.exit(int(rest[0]))
elif what == "unreachable":
    sys.stderr.write("ssh: connect to host 192.0.2.9 port 22: Connection refused\n")
    sys.exit(255)
elif what == "noend":
    sys.stdout.write("partial")
elif what == "flood":
    say("x" * int(rest[0]))
elif what == "much":
    say(*["y" * 40] * int(rest[0]))
elif what == "noisy":
    sys.stderr.write("e" * 100000)
    say("done")
elif what == "escape":
    sys.stderr.write("\x1b]52;c;ZXZpbA==\x07\x1b[31mwarning\x1b[0m: refused\n")
    say("\x1b]0;a title\x07plain line")
elif what == "quiet":
    linger(rest[0])
    time.sleep(300)
elif what == "hang":
    linger(rest[0])
    say("first")
    time.sleep(300)
elif what == "held":
    say("one", "two", "three")
    record(sys.stdin.buffer.readline())
    say("got it")
elif what == "wait":
    linger(rest[0])
    say("ready")
    time.sleep(300)
elif what == "forward":
    linger(os.environ.get("AW_FORWARD_MARK", "no-mark"))
    time.sleep(300)
"""


@pytest.fixture
def stand_in(engine_guard, tmp_path) -> Path:
    """The stand-in ssh's folder, first on this test's own PATH."""
    folder = tmp_path / "bin"
    folder.mkdir()
    (folder / "ssh.py").write_text(STAND_IN)
    harness._executable(folder / "ssh", f'#!/bin/sh\nexec "{sys.executable}" -I "{folder / "ssh.py"}" "$@"\n')
    engine_guard.first_on_path(folder)
    return folder


@pytest.fixture
def made(home: Path) -> Iterator[jobs.Job]:
    """A job of this process's own, as the run that starts the children has; dropped after the test."""
    job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True
    )
    yield job
    jobs.drop(job)


def _calls(folder: Path) -> list[dict]:
    log = folder / "calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _machine(tmp_path: Path, **over) -> runner.Target:
    return runner.machine(IDENTITY, ADDRESS, "dev", CONTROL, **{"known_hosts": tmp_path / "known_hosts", **over})


def _run(job, target, command, stdin=None, limits=runner.LIMITS, started=None) -> runner.Result:
    return asyncio.run(runner.run(job, target, command, stdin, limits, dict(os.environ), started))


def _marker() -> str:
    return f"aw-ssh-runner-{uuid.uuid4().hex}"


def _gone(marker: str) -> None:
    _until(lambda: _by_argv(marker) == [], f"every process marked {marker} ended")


def _hop(address: str = ADDRESS) -> runner.Hop:
    settled = route.hop_settings(user="root", host="198.51.100.4", control_dir=str(CONTROL))
    return runner.Hop(" ".join(route.hop_argv(settled, address, "accept-new")), os.path.basename(settled["socket"]))


def _real_ssh() -> str:
    found = shutil.which("ssh", path=os.defpath)
    if found is None:
        pytest.skip("this machine has no ssh to ask ssh -G of")
    return found


def _shown(words: list[str]) -> dict[str, list[str]]:
    """What this machine's own ssh says the options words hold for the address after --: ssh -G -F /dev/null, which
    reads no configuration file and dials nothing. Each key lower case, with its values as ssh prints them, a flag's
    true and false read as yes and no."""
    shown = subprocess.run(
        [_real_ssh(), "-G", "-F", "/dev/null", *words],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=dict(os.environ),
        check=True,
    ).stdout
    found: dict[str, list[str]] = {}
    for line in shown.splitlines():
        key, _, value = line.partition(" ")
        found.setdefault(key, []).append({"true": "yes", "false": "no"}.get(value, value))
    return found


# ---------------------------------------------------------------- the command line ----


def test_a_machine_s_command_line_is_the_product_s_options_then_the_place_then_the_command(tmp_path) -> None:
    target = _machine(tmp_path)
    assert runner.argv(target, ["printf", "%s\\n", "a b"]) == [
        "ssh",
        "-o",
        f'UserKnownHostsFile="{tmp_path / "known_hosts"}"',
        "-o",
        "CheckHostIP=no",
        "-o",
        "UpdateHostKeys=no",
        "-o",
        f"HostKeyAlias={IDENTITY}",
        "-o",
        "ForwardAgent=no",
        "-o",
        f"ControlPath={CONTROL}/{checks.vm_control_name(IDENTITY, ADDRESS)}-%r-%p",
        "-o",
        "ControlMaster=no",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "RequestTTY=no",
        "-o",
        "LogLevel=ERROR",
        "-l",
        "dev",
        "-p",
        "22",
        "--",
        ADDRESS,
        "printf '%s\\n' 'a b'",
    ]


def test_the_identity_file_then_the_hop_then_the_person_s_options_follow_the_product_s(tmp_path) -> None:
    hop = _hop()
    target = _machine(
        tmp_path,
        identity_file=tmp_path / "id ed25519",
        hop=hop,
        given={"Compression": "yes", "ServerAliveInterval": "60"},
    )
    words = runner.argv(target, ["true"])
    product = runner.argv(_machine(tmp_path), ["true"])
    socket = f"ControlPath={CONTROL}/{checks.vm_control_name(IDENTITY, ADDRESS, hop.name)}-%r-%p"
    assert words[: words.index("-l")] == [
        *[socket if word.startswith("ControlPath=") else word for word in product[: product.index("-l")]],
        "-o",
        f'IdentityFile="{tmp_path / "id ed25519"}"',
        "-o",
        f"ProxyCommand={hop.proxy_command}",
        "-o",
        "Compression=yes",
        "-o",
        "ServerAliveInterval=60",
    ]
    assert words[-7:] == ["-l", "dev", "-p", "22", "--", ADDRESS, "true"]


def test_the_default_limits_are_today_s() -> None:
    # status/reach.py's, which the runner may not import: status/ makes TLS contexts (test_aw_vendor_trust.py).
    from claude_vm.status import reach

    assert (runner.LINE_BYTES, runner.SECONDS, runner.CONNECT_SECONDS, runner.START_SECONDS) == (
        reach.LINE_BYTES,
        reach.SECONDS,
        reach.CONNECT_SECONDS,
        reach.START_SECONDS,
    )
    line, seconds, silent = reach.LINE_BYTES, reach.SECONDS, reach.START_SECONDS
    assert runner.LIMITS == runner.Limits(line=line, most=4 * line, seconds=seconds, silent=silent, said=1 << 16)
    options = runner.options(runner.machine(IDENTITY, ADDRESS, "dev", CONTROL))
    assert f"ConnectTimeout={reach.CONNECT_SECONDS}" in options


def test_this_machine_s_ssh_shows_the_values_the_options_hold(tmp_path) -> None:
    target = _machine(tmp_path, port=2222, identity_file=tmp_path / "key")
    shown = _shown(runner.argv(target, ["true"])[1:-1])
    name = checks.vm_control_name(IDENTITY, ADDRESS)
    assert {key: shown.get(key) for key in EXPECTED} == {
        **EXPECTED,
        "userknownhostsfile": [str(tmp_path / "known_hosts")],
        "identityfile": [str(tmp_path / "key")],
        "controlpath": [f"{CONTROL}/{name}-dev-2222"],
        "port": ["2222"],
    }


EXPECTED = {
    "user": ["dev"],
    "hostname": [ADDRESS],
    "port": ["22"],
    "hostkeyalias": [IDENTITY],
    "userknownhostsfile": None,
    "checkhostip": ["no"],
    "updatehostkeys": ["no"],
    "forwardagent": ["no"],
    "controlpath": None,
    "controlmaster": ["no"],
    "batchmode": ["yes"],
    "stricthostkeychecking": ["yes"],
    "connecttimeout": ["10"],
    "serveraliveinterval": ["15"],
    "serveralivecountmax": ["3"],
    "preferredauthentications": ["publickey"],
    "clearallforwardings": ["yes"],
    "requesttty": ["no"],
    "loglevel": ["ERROR"],
    "identityfile": None,
}


def test_the_person_s_options_change_what_the_product_leaves_and_nothing_it_pins(tmp_path) -> None:
    given = {
        "Compression": "yes",
        "ProxyJump": "jumper@jump.example.test",
        "ServerAliveInterval": "60",
        "ConnectTimeout": "3",
        "LogLevel": "DEBUG3",
        "RequestTTY": "force",
        "StrictHostKeyChecking": "yes",
        "IdentitiesOnly": "yes",
    }
    shown = _shown(runner.argv(_machine(tmp_path, given=given), ["true"])[1:-1])
    # The person's, where the product sets nothing.
    assert (shown["compression"], shown["proxyjump"], shown["identitiesonly"]) == (
        ["yes"],
        ["jumper@jump.example.test"],
        ["yes"],
    )
    # The product's, where it pins one: ssh keeps the first value it is given.
    assert (shown["serveraliveinterval"], shown["connecttimeout"], shown["loglevel"], shown["requesttty"]) == (
        ["15"],
        ["10"],
        ["ERROR"],
        ["no"],
    )
    assert (shown["batchmode"], shown["stricthostkeychecking"], shown["hostkeyalias"]) == (["yes"], ["yes"], [IDENTITY])


@pytest.mark.parametrize(
    "given",
    [
        {"HostKeyAlias": "other"},
        {"userknownhostsfile": "/dev/null"},
        {"CheckHostIP": "yes"},
        {"UpdateHostKeys": "yes"},
        {"BatchMode": "no"},
        {"ControlPath": "/tmp/mine"},
        {"CONTROLMASTER": "auto"},
        {"ForwardAgent": "yes"},
        {"IdentityFile": "~/.ssh/other"},
        {"HostName": "elsewhere.example.test"},
        {"User": "root"},
        {"Port": "2200"},
    ],
)
def test_an_option_the_product_writes_itself_is_refused_by_its_name(tmp_path, given) -> None:
    [(name, _)] = given.items()
    with pytest.raises(runner.Refused, match=rf"vm_ssh_options sets {runner.REFUSED_OPTIONS[name.lower()]}, which"):
        _machine(tmp_path, given=given)


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({"StrictHostKeyChecking": "no"}, "StrictHostKeyChecking to other than yes"),
        ({"StrictHostKeyChecking": "accept-new"}, "StrictHostKeyChecking to other than yes"),
        ({"Compression": "yes\nProxyCommand evil"}, "value for Compression is one line"),
        ({"Compression": ""}, "value for Compression is one line"),
        ({"Proxy Command": "x"}, "letters alone"),
        ({"-oProxyCommand": "x"}, "letters alone"),
        ((("Compression", "yes"), ("compression", "no")), "gives compression twice"),
        ({"Compression": 1}, "value for Compression is one line"),
    ],
)
def test_an_option_given_wrongly_is_refused_and_its_value_never_said(tmp_path, given, said) -> None:
    with pytest.raises(runner.Refused, match=re.escape(said)) as refused:
        _machine(tmp_path, given=given)
    assert "evil" not in str(refused.value)


def test_a_machine_given_a_jump_of_its_own_takes_no_hop(tmp_path) -> None:
    for jump in ({"ProxyJump": "jump.example.test"}, {"proxycommand": "nc %h %p"}):
        with pytest.raises(runner.Refused, match="a jump of its own"):
            _machine(tmp_path, hop=_hop(), given=jump)
    # ProxyCommand none is no jump.
    assert _machine(tmp_path, hop=_hop(), given={"ProxyCommand": "none"}).hop is not None


def test_a_hop_to_another_address_is_refused(tmp_path) -> None:
    with pytest.raises(runner.Refused, match="another address"):
        _machine(tmp_path, hop=_hop("192.0.2.10"))


def test_a_first_use_takes_the_key_into_its_scratch_file_under_the_identity_and_never_by_address(tmp_path) -> None:
    scratch = tmp_path / "scratch" / "known_hosts"
    words = runner.argv(_machine(tmp_path, known_hosts=scratch, first_use=True), ["true"])
    options = words[: words.index("--")]
    assert "StrictHostKeyChecking=accept-new" in options and "StrictHostKeyChecking=yes" not in options
    assert f'UserKnownHostsFile="{scratch}"' in options and f"HostKeyAlias={IDENTITY}" in options
    assert not [word for word in options if ADDRESS in word]
    shown = _shown(words[1:-1])
    assert (shown["stricthostkeychecking"], shown["userknownhostsfile"], shown["hostkeyalias"]) == (
        ["accept-new"],
        [str(scratch)],
        [IDENTITY],
    )
    # A connection of its own: a master of the identity and the address that still lives would carry it, and ssh would
    # write no key. ControlPath=none is no socket at all, which ssh -G leaves unsaid.
    assert [word for word in options if word.startswith("ControlPath=")] == ["ControlPath=none"]
    assert "controlpath" not in shown


@pytest.mark.parametrize("kept", ["~/.ssh/agent-warehouse.known_hosts", "~/.ssh/known_hosts", "~/.ssh/known_hosts2"])
def test_a_first_use_never_writes_the_product_s_file_or_the_person_s(tmp_path, kept) -> None:
    with pytest.raises(runner.Refused, match="scratch file of its own"):
        _machine(tmp_path, known_hosts=Path(kept), first_use=True)


def _forms(tmp_path: Path) -> list[tuple[str, runner.Target]]:
    """Every form of target the runner takes, each kind of option on it: name, target."""
    known = runner.KNOWN_HOSTS
    return [
        ("a machine", runner.machine(IDENTITY, ADDRESS, "dev", CONTROL)),
        (
            "with a key",
            runner.machine(IDENTITY, ADDRESS, "dev", CONTROL, identity_file=tmp_path / "k", known_hosts=known),
        ),
        ("through the hop", runner.machine(IDENTITY, ADDRESS, "dev", CONTROL, hop=_hop())),
        ("with options", runner.machine(IDENTITY, ADDRESS, "dev", CONTROL, given={"ProxyJump": "j.example.test"})),
        ("on a port", runner.machine(IDENTITY, ADDRESS, "dev", CONTROL, port=2200, ssh="/usr/bin/ssh")),
        ("a first use", runner.machine(IDENTITY, ADDRESS, "dev", CONTROL, known_hosts=tmp_path / "s", first_use=True)),
        ("the proxmox host", runner.proxmox_host("pve.example.test", "root", CONTROL, identity_file=tmp_path / "k")),
    ]


def test_every_form_carries_batch_mode_a_client_s_control_master_and_its_one_policy(tmp_path) -> None:
    product = f'UserKnownHostsFile="{runner.KNOWN_HOSTS.expanduser()}"'
    for name, target in _forms(tmp_path):
        for words in (runner.argv(target, ["true"]), runner.forward_argv(target, ["127.0.0.1:5000:127.0.0.1:5000"])):
            options = words[: words.index("--")]
            assert options.count("BatchMode=yes") == 1, name
            # The runner's own: the hop's ssh inside a ProxyCommand is the route's, a master of the hop's own.
            assert [word for word in options if word.startswith("ControlMaster")] == ["ControlMaster=no"], name
            policies = [word for word in options if word.startswith("StrictHostKeyChecking=")]
            known = [word for word in options if word.startswith("UserKnownHostsFile=")]
            if name == "a first use":
                assert (policies, known) == (
                    ["StrictHostKeyChecking=accept-new"],
                    [f'UserKnownHostsFile="{tmp_path / "s"}"'],
                )
            elif name == "the proxmox host":
                # The person's own known_hosts, as ansible.cfg's ssh_args reach the host today; no identity.
                assert (policies, known) == (["StrictHostKeyChecking=accept-new"], [])
                assert not [word for word in options if word.startswith("HostKeyAlias=")]
            else:
                assert (policies, known) == (["StrictHostKeyChecking=yes"], [product]), name
                assert f"HostKeyAlias={IDENTITY}" in options, name


def test_a_forward_is_ssh_n_with_each_l_its_own_connection_and_its_failure_said(tmp_path) -> None:
    specs = ["127.0.0.1:5000:127.0.0.1:5000", "[::1]:5000:127.0.0.1:5000"]
    words = runner.forward_argv(_machine(tmp_path), specs)
    options = words[: words.index("--")]
    assert words[:2] == ["ssh", "-N"] and words[words.index("--") :] == ["--", ADDRESS]
    assert options[options.index("-L") :] == ["-L", specs[0], "-L", specs[1], "-l", "dev", "-p", "22"]
    for option in ("ExitOnForwardFailure=yes", "ControlPath=none", "ControlMaster=no", "ClearAllForwardings=no"):
        assert option in options, option
    shown = _shown(words[1:])
    assert (shown["exitonforwardfailure"], shown["clearallforwardings"], shown["addressfamily"]) == (
        ["yes"],
        ["no"],
        ["any"],
    )
    # ControlPath=none is no socket at all, which ssh -G leaves unsaid.
    assert "controlpath" not in shown and len(shown["localforward"]) == 2


@pytest.mark.parametrize("spec", ["5000", "127.0.0.1:5000:127.0.0.1", "-oProxyCommand=x", "a b:1:c:2", "1:h:2\n"])
def test_a_forward_s_spec_is_one_plain_l_spec(tmp_path, spec) -> None:
    with pytest.raises(runner.Refused, match="a forward's spec"):
        runner.forward_argv(_machine(tmp_path), [spec])


def test_the_ansible_child_is_given_the_same_options_in_the_same_order(tmp_path) -> None:
    for name, target in _forms(tmp_path):
        given = runner.ansible_vars(target)
        common = checks.ssh_args_split(given["ansible_ssh_common_args"])
        words = runner.argv(target, ["true"])
        assert common == runner.options(target) == words[1 : words.index("-l")], name
        assert (
            given["ansible_host"],
            given["ansible_port"],
            given["ansible_user"],
            given["ansible_ssh_executable"],
        ) == (
            target.address,
            target.port,
            target.account,
            target.ssh,
        ), name
        for option in ("StrictHostKeyChecking=", "BatchMode=yes", "ControlPath=", "ControlMaster=no"):
            assert any(word.startswith(option) for word in common), (name, option)


def test_the_proxmox_host_takes_its_account_port_key_and_ssh_from_its_fields(tmp_path, stand_in, made) -> None:
    key = tmp_path / "pve_key"
    target = runner.proxmox_host(
        "pve.example.test", "admin", CONTROL, port=2222, identity_file=key, ssh=str(stand_in / "ssh")
    )
    socket = route.hop_settings(
        ssh=str(stand_in / "ssh"),
        user="admin",
        host="pve.example.test",
        port=2222,
        key=str(key),
        control_dir=str(CONTROL),
    )["socket"]
    words = runner.argv(target, ["hostname", "-s"])
    assert words[0] == str(stand_in / "ssh") and words[-7:] == [
        "-l",
        "admin",
        "-p",
        "2222",
        "--",
        "pve.example.test",
        "hostname -s",
    ]
    assert f'IdentityFile="{key}"' in words and f"ControlPath={socket}" in words
    assert not [word for word in words if word.startswith(("UserKnownHostsFile=", "HostKeyAlias="))]
    # Run: the program the field names is the one that ran.
    result = _run(made, target, ["say", "on the host"])
    assert result == runner.Done(0, ("on the host",), "")
    [call] = _calls(stand_in)
    assert call["argv"] == words[1:-1] + ["say 'on the host'"]


@pytest.mark.parametrize(
    ("over", "said"),
    [
        ({"address": "pve example"}, "not one host name or address"),
        ({"address": "-oProxyCommand=x"}, "not one host name or address"),
        ({"account": "root@pve"}, "not an account name"),
        ({"port": 0}, "a port is a number"),
        ({"identity_file": "/k ey"}, "has a character other than"),
        ({"ssh": "/usr/bin/ssh;id"}, "plain word"),
    ],
)
def test_a_setting_of_the_proxmox_host_s_that_is_not_one_plain_word_is_refused(over, said) -> None:
    given = {"address": "pve.example.test", "account": "root", "port": 22, "identity_file": None, "ssh": "ssh"} | over
    with pytest.raises(runner.Refused, match=said):
        runner.proxmox_host(given.pop("address"), given.pop("account"), CONTROL, **given)


def test_the_proxmox_host_takes_no_hop_no_options_and_no_scratch_file(tmp_path) -> None:
    for over in ({"hop": _hop()}, {"given": (("Compression", "yes"),)}, {"first_use": True}, {"known_hosts": tmp_path}):
        with pytest.raises(runner.Refused, match="person's own known-hosts file"):
            runner.Target(identity=None, address="pve.example.test", account="root", control_dir=CONTROL, **over)


@pytest.mark.parametrize(
    "address",
    [
        "192.0.2.9\n",
        "192.0.2.9 -oProxyCommand=x",
        "-oProxyCommand=x",
        "2001:db8::9",
        "vm.example.test",
        "192.0.2.256",
        "192.0.2.09",
        "0.0.0.0",
        "127.0.0.1",
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "",
        None,
    ],
)
def test_an_address_that_is_not_one_ipv4_address_is_refused_before_any_child_starts(tmp_path, stand_in, address):
    with pytest.raises(runner.Refused, match="not one IPv4 address a machine is reached at"):
        runner.machine(IDENTITY, address, "dev", CONTROL, known_hosts=tmp_path / "known_hosts")
    assert _calls(stand_in) == []


@pytest.mark.parametrize(
    "identity", [IDENTITY + "\n", IDENTITY.upper(), "agent-warehouse-xyz", "-oProxyCommand=x", "", "pve", 42]
)
def test_an_identity_that_is_not_one_is_refused_before_any_child_starts(tmp_path, stand_in, identity) -> None:
    with pytest.raises(runner.Refused, match="not a machine's identity"):
        runner.machine(identity, ADDRESS, "dev", CONTROL, known_hosts=tmp_path / "known_hosts")
    assert _calls(stand_in) == []


@pytest.mark.parametrize(
    ("over", "said"),
    [
        ({"account": "-l"}, "not an account name"),
        ({"port": 70000}, "a port is a number"),
        ({"port": True}, "a port is a number"),
        ({"known_hosts": "relative/known_hosts"}, "absolute path"),
        ({"known_hosts": "/a%h/known_hosts"}, "absolute path"),
        ({"identity_file": '/a"b'}, "absolute path"),
        ({"ssh": "-oProxyCommand=x"}, "plain word"),
        ({"ssh": "ssh tool"}, "plain word"),
    ],
)
def test_a_machine_s_other_values_are_held_before_any_child_starts(tmp_path, over, said) -> None:
    given = {"known_hosts": tmp_path / "known_hosts"} | over
    with pytest.raises(runner.Refused, match=said):
        runner.machine(IDENTITY, ADDRESS, given.pop("account", "dev"), CONTROL, **given)


@pytest.mark.parametrize("control", ["relative/cp", "/a b/cp", "/a%C/cp", "/" + "c" * 70])
def test_a_control_directory_that_ssh_could_not_bind_a_socket_in_is_refused(tmp_path, control) -> None:
    with pytest.raises(runner.Refused, match="control directory"):
        runner.machine(IDENTITY, ADDRESS, "dev", control, known_hosts=tmp_path / "known_hosts")


def test_a_command_that_is_not_a_list_of_words_starts_nothing(tmp_path, stand_in, made) -> None:
    for command in ("echo hi", [], ["echo", "a\0b"], [b"echo"]):
        with pytest.raises(runner.Refused):
            _run(made, _machine(tmp_path), command)
    with pytest.raises(runner.Refused, match="standard input"):
        _run(made, _machine(tmp_path), ["say"], stdin=42)
    assert _calls(stand_in) == []


# ---------------------------------------------------------------- a bounded call ----


def test_a_call_hands_its_input_and_gets_its_lines_with_the_secret_on_standard_input_alone(
    tmp_path, stand_in, made
) -> None:
    seen = []

    def started(process) -> None:
        # Handed asyncio's own process, already entered in the run's registry as an ssh child for the run's end.
        seen.append((process, [child.process for child in children.live(made, children.SSH)]))

    result = _run(
        made,
        _machine(tmp_path),
        ["say", "first line", "second line"],
        stdin=[b"#!helper\n", Secret(CANARY), "\n"],
        started=started,
    )
    assert result == runner.Done(0, ("first line", "second line"), "")
    [call] = _calls(stand_in)
    # The job's marker reached the child, in a process group of its own.
    assert call["env"][children.MARKER] == made.id and call["group"] == call["pid"]
    [(process, registered)] = seen
    assert registered == [process] and children.live(made) == []
    assert isinstance(process, asyncio.subprocess.Process) and process.pid == call["pid"]
    assert (stand_in / "stdin.bin").read_bytes() == f"#!helper\n{CANARY}\n".encode()
    assert searched_for([CANARY], stand_in / "stdin.bin") == [(stand_in / "stdin.bin", CANARY)]
    # In no word and no variable: what ssh was given, and what the call said back.
    assert searched_for([CANARY], stand_in / "calls.jsonl") == []
    assert CANARY not in repr(result)
    assert call["argv"][call["argv"].index("BatchMode=yes") - 1] == "-o"


def test_a_call_says_how_the_child_ended(tmp_path, stand_in, made) -> None:
    target = _machine(tmp_path)
    assert _run(made, target, ["exit", "3", "it failed"]) == runner.Done(3, (), "it failed")
    assert _run(made, target, ["unreachable"]) == runner.Unreachable(
        "ssh: connect to host 192.0.2.9 port 22: Connection refused"
    )
    noisy = _run(made, target, ["noisy"], limits=BYTES)
    assert noisy == runner.Done(0, ("done",), "e" * BYTES.said)


def test_escape_sequences_in_what_the_machine_said_are_gone(tmp_path, stand_in, made) -> None:
    result = _run(made, _machine(tmp_path), ["escape"])
    assert result == runner.Done(0, ("plain line",), "warning: refused")


@pytest.mark.parametrize(
    ("command", "why"),
    [
        (["noend"], "it sent a line with no end"),
        (["flood", "100"], "it sent a line longer than 64 bytes"),
        (["much", "10"], "it sent more than 256 bytes"),
    ],
)
def test_a_child_that_sends_more_than_it_may_is_cut(tmp_path, stand_in, made, command, why) -> None:
    result = _run(made, _machine(tmp_path), command, limits=BYTES)
    assert isinstance(result, runner.Cut) and (result.why, result.said) == (why, ""), result
    # What came before the line that broke a bound, each line whole.
    assert set(result.lines) <= {"y" * 40} and len(result.lines) <= 6
    assert children.live(made) == []


def test_a_child_that_says_nothing_is_silent_and_leaves_nothing(tmp_path, stand_in, made) -> None:
    marker = _marker()
    assert _run(made, _machine(tmp_path), ["quiet", marker], limits=SILENCE) == runner.Silent("")
    _gone(marker)
    assert children.live(made) == []


def test_a_child_that_never_ends_times_out_and_leaves_nothing(tmp_path, stand_in, made) -> None:
    marker = _marker()
    assert _run(made, _machine(tmp_path), ["hang", marker], limits=DEADLINE) == runner.TimedOut(("first",), "")
    _gone(marker)


def test_a_call_cancelled_by_its_run_leaves_nothing(tmp_path, stand_in, made) -> None:
    marker = _marker()

    async def cancelled() -> None:
        call = asyncio.ensure_future(
            runner.run(made, _machine(tmp_path), ["wait", marker], None, runner.LIMITS, dict(os.environ))
        )
        await asyncio.to_thread(_until, lambda: _by_argv(marker) != [], "the stand-in's grandchild started")
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

    asyncio.run(cancelled())
    _gone(marker)


# ---------------------------------------------------------------- a held child ----


def test_a_held_child_s_lines_arrive_while_it_runs_and_a_line_sent_later_reaches_it(tmp_path, stand_in, made) -> None:
    async def held() -> tuple[list[str], bool, list, runner.Result]:
        child = await runner.hold(made, _machine(tmp_path), ["held"], dict(os.environ), deadline=60)
        read = []
        async for line in child.lines():
            read.append(line)
            if len(read) == 3:
                break
        running = child.process.returncode is None
        registered = [entry.process for entry in children.live(made, children.SSH)]
        await child.send(Secret(CANARY))
        return read, running, registered == [child.process], await child.result()

    read, running, registered, result = asyncio.run(held())
    assert (read, running, registered) == (["one", "two", "three"], True, True)
    assert result == runner.Done(0, ("one", "two", "three", "got it"), "")
    assert (stand_in / "stdin.bin").read_bytes() == f"{CANARY}\n".encode()
    assert searched_for([CANARY], stand_in / "calls.jsonl") == []


def test_a_held_child_takes_one_line_at_a_time_and_none_once_it_has_ended(tmp_path, stand_in, made) -> None:
    async def held() -> None:
        child = await runner.hold(made, _machine(tmp_path), ["held"], dict(os.environ), deadline=60)
        with pytest.raises(runner.Refused, match="one line is sent at a time") as refused:
            await child.send(Secret(f"{CANARY}\nmore"))
        assert CANARY not in str(refused.value)
        await child.close()
        assert isinstance(await child.result(), runner.Done)
        with pytest.raises(runner.Ended):
            await child.send("late")

    asyncio.run(held())


def test_end_leaves_nothing_of_a_held_child(tmp_path, stand_in, made) -> None:
    marker = _marker()

    async def held() -> runner.Result:
        child = await runner.hold(made, _machine(tmp_path), ["wait", marker], dict(os.environ))
        assert await child.lines().__anext__() == "ready"
        await child.end()
        return await child.result()

    result = asyncio.run(held())
    assert isinstance(result, runner.Done) and result.status < 0 and result.lines == ("ready",)
    _gone(marker)
    assert children.live(made) == []


def test_a_held_child_past_its_deadline_is_ended(tmp_path, stand_in, made) -> None:
    marker = _marker()

    async def held() -> runner.Result:
        child = await runner.hold(made, _machine(tmp_path), ["wait", marker], dict(os.environ), deadline=1)
        return await child.result()

    assert asyncio.run(held()) == runner.TimedOut(("ready",), "")
    _gone(marker)


def test_a_forward_is_held_until_its_end_and_takes_no_input(tmp_path, stand_in, made) -> None:
    marker = _marker()
    specs = ["127.0.0.1:5000:127.0.0.1:5000"]

    async def held() -> runner.Result:
        env = {**os.environ, "AW_FORWARD_MARK": marker}
        child = await runner.forward(made, _machine(tmp_path), specs, env, deadline=60)
        await asyncio.to_thread(_until, lambda: _by_argv(marker) != [], "the forward's stand-in up")
        with pytest.raises(runner.Ended):
            await child.send("anything")
        await child.end()
        return await child.result()

    assert asyncio.run(held()).status < 0
    _gone(marker)
    [call] = _calls(stand_in)
    assert call["argv"] == runner.forward_argv(_machine(tmp_path), specs)[1:]


# ---------------------------------------------------------------- the rules ----

# A string that names the ssh program: ssh, a path to it, or a command line that starts with it.
NAMES_SSH = re.compile(r"(?:\S*/)?ssh(?:\s.*)?", re.S)


def _callee(node: ast.Call) -> str:
    return getattr(node.func, "attr", getattr(node.func, "id", ""))


def _names_ssh(source: str, kind_named: bool = False) -> list[str]:
    """Each place source names the ssh program, or starts a child of the ssh kind: a string that is ssh, a path to it
    or a command line that starts with it, but a docstring; and a call of start given children.SSH. kind_named: the
    one assignment of the kind's own name, SSH = "ssh", is children.py's."""
    tree = ast.parse(source)
    skipped = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr)}
    if kind_named:
        skipped |= {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and [getattr(target, "id", "") for target in node.targets] == ["SSH"]
        }
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skipped:
            if NAMES_SSH.fullmatch(node.value):
                found.append(f"line {node.lineno}: {node.value!r}")
        if isinstance(node, ast.Call) and _callee(node) == "start":
            given = [*node.args, *(keyword.value for keyword in node.keywords)]
            if any(getattr(arg, "attr", getattr(arg, "id", "")) == "SSH" for arg in given):
                found.append(f"line {node.lineno}: a child of the ssh kind")
    return found


def test_a_child_named_ssh_is_started_only_in_ssh() -> None:
    code = [
        path for path in repo_files(*(f"lib/claude_vm/{name}" for name in NEW), untracked=True) if path.suffix == ".py"
    ]
    code.append(REPO / "tools" / "aw")
    assert RUNNER in code and REPO / "lib" / "claude_vm" / "runs" / "children.py" in code, code
    found = {
        str(path.relative_to(REPO)): where
        for path in code
        if not path.is_relative_to(RUNNER.parent)
        and (where := _names_ssh(path.read_text(), kind_named=path.name == "children.py"))
    }
    assert found == {}, "an ssh child is the SSH runner's: start it through lib/claude_vm/ssh/"


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ('argv = ["ssh", "-G", host]', True),
        ('program = "/usr/bin/ssh"', True),
        ('line = "ssh -o BatchMode=yes host"', True),
        ("await children.start(job, argv, GROUP, env, a, b, c, children.SSH)", True),
        ("await start(job, argv, GROUP, env, a, b, c, kind=SSH)", True),
        ('SSH = "ssh"', True),
        ('"""ssh as a child, every option on its line."""', False),
        ('stage = "ssh-config"', False),
        ('daemon = "sshd"', False),
        ("children.live(job, children.SSH)", False),
    ],
)
def test_the_scan_sees_each_way_ssh_is_named(source, found) -> None:
    assert bool(_names_ssh(source)) is found
    assert _names_ssh('SSH = "ssh"', kind_named=True) == []


def test_no_option_is_made_of_a_value_a_machine_supplied_but_its_address(tmp_path) -> None:
    # The address is the one value a machine supplies (its guest agent's, or what it says it is): two machines that
    # differ in it alone differ in no option but their socket's name, a digest that holds it.
    one, two = (runner.machine(IDENTITY, address, "dev", CONTROL) for address in (ADDRESS, "198.51.100.77"))
    first, second = runner.options(one), runner.options(two)
    differ = [at for at, (a, b) in enumerate(zip(first, second, strict=True)) if a != b]
    assert [first[at].split("=", 1)[0] for at in differ] == ["ControlPath"]
    assert not [word for word in first if ADDRESS in word]
    words = runner.argv(one, ["true"])
    assert [at for at, word in enumerate(words) if ADDRESS in word] == [words.index("--") + 1]


def _strings(path: Path) -> list[ast.Constant]:
    """Every string path's code holds, but a docstring: what it writes, never what it says of itself."""
    tree = ast.parse(path.read_text())
    said = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr)}
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in said
    ]


def test_accept_new_is_written_in_ssh_only_for_a_first_use_and_the_proxmox_host() -> None:
    files = [path for path in repo_files("lib/claude_vm/ssh", untracked=True) if path.suffix == ".py"]
    assert RUNNER in files, files
    written = {
        str(path.relative_to(REPO)): [node.lineno for node in _strings(path) if "accept-new" in node.value]
        for path in files
    }
    # Once, as the constant's value, which _policy alone reads: a first use's, and the Proxmox host's.
    tree = ast.parse(RUNNER.read_text())
    [constant] = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign) and [getattr(target, "id", "") for target in node.targets] == ["ACCEPT_NEW"]
    ]
    assert {name: lines for name, lines in written.items() if lines} == {
        str(RUNNER.relative_to(REPO)): [constant.lineno]
    }
    readers = {
        function.name
        for function in ast.walk(tree)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and node.id == "ACCEPT_NEW"
    }
    assert readers == {"_policy"}


def test_no_ssh_of_the_runner_s_carries_a_control_master_but_no() -> None:
    written = [node.value for node in _strings(RUNNER) if re.search(r"(?i)controlmaster\s*=", node.value)]
    assert written == ["ControlMaster=no"]
