"""The one shared control directory and the route to a host's machines (lib/claude_vm/ssh/connections.py;
docs/architecture/engine.md §16.6).

Every ssh here is a stand-in, first on the test's own PATH by engine_guard.first_on_path, as tests/unit/test_route.py's
hop_at plays OpenSSH: it logs each call - its words, its environment, its session - and a master is a file at its
socket's path in a short directory of the test's own, one that holds "stale" a socket nothing listens at; -O check
answers from that file, an open writes it, and a file mode-<destination> beside the stand-in makes an open fail as a
refused login does. For ssh -G it runs this machine's own ssh by its path, given the test's own ~/.ssh/config with -F
(or /dev/null), so that ssh itself reads a configuration and nothing of the developer's is read: OpenSSH finds ~ in the
password database, never in HOME. A file hold-<kind> beside it holds the next call of that kind (G, check, open) until
a file go appears there, which the test writes: no clock decides what the test sees. No stand-in dials anything. The
rules with a test of their own: no -O but check in the new subpackages, never -O exit; nothing in ssh/ retries a
connection; only ensure (and route.py, for the hop) opens a master, and only with the kind that carries no job's marker;
route.py's ssh runs in a process group of its own, so a terminal's Ctrl-C settles nothing, and off the event loop, so
another task goes on while it waits; the control directory is its owner's alone; and a run remembers nothing of a
master's open, a machine's or the hop's.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import contextlib
import inspect
import ipaddress
import json
import os
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import types
from collections.abc import Iterator
from pathlib import Path

import harness
import pytest
from harness import REPO, repo_files
from test_aw_layout import NEW
from test_processes import _until

from claude_vm import checks, proc, route
from claude_vm.fleet import paths
from claude_vm.runs import attached, children, rundir
from claude_vm.runs import job as jobs
from claude_vm.ssh import connections, hostkeys, runner

IDENTITY = "agent-warehouse-0f0e0d0c-0b0a-4908-8706-050403020100"
ADDRESS = "192.0.2.9"
OTHER_ADDRESS = "192.0.2.10"
HOST = "pve-test"
TAILNET = "100.101.102.103"
LAN = "198.51.100.4"
CONNECTIONS = REPO / "lib" / "claude_vm" / "ssh" / "connections.py"

STAND_IN = r"""
import json, os, subprocess, sys, time

here = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]
with open(os.path.join(here, "calls.jsonl"), "a") as out:
    called = {"argv": argv, "env": dict(os.environ), "pid": os.getpid(), "session": os.getsid(0), "group": os.getpgrp()}
    out.write(json.dumps(called) + "\n")


def after(flag):
    return argv[argv.index(flag) + 1] if flag in argv else ""


def first(prefix):
    # ssh keeps the first value it is given for an option.
    return next((word[len(prefix):] for word in argv if word.startswith(prefix)), "")


def hold(kind):
    # hold-<kind> beside the stand-in: this call of that kind, the first, waits until go appears beside it, which the
    # test writes, and then says so in released-<kind>; 30 s at most, past every limit of route.py's, which ends it
    # first, unreleased.
    marker = os.path.join(here, "hold-" + kind)
    if not os.path.exists(marker):
        return
    os.remove(marker)
    for _ in range(3000):
        if os.path.exists(os.path.join(here, "go")):
            open(os.path.join(here, "released-" + kind), "w").close()
            return
        time.sleep(0.01)


if "-G" in argv:
    hold("G")
    # slow-G beside the stand-in: ssh -G takes that many seconds first, as a person's Match exec may; killed-G: it is
    # ended by a signal.
    if os.path.exists(os.path.join(here, "slow-G")):
        time.sleep(float(open(os.path.join(here, "slow-G")).read()))
    if os.path.exists(os.path.join(here, "killed-G")):
        os.kill(os.getpid(), 9)
    if "-F" not in argv:
        config = os.path.join(os.environ["HOME"], ".ssh", "config")
        argv = ["-F", config if os.path.exists(config) else "/dev/null", *argv]
    os.execv(REAL, [REAL, *argv])

socket = first("ControlPath=").replace("%r", after("-l")).replace("%p", after("-p") or "22")
destination = argv[-1]
moded = os.path.join(here, "mode-" + destination)
mode = open(moded).read() if os.path.exists(moded) else "open"


def held():
    return open(socket).read() if os.path.exists(socket) else ""


if "-O" in argv:
    if after("-O") == "check":
        hold("check")
        if held() == "hung":
            time.sleep(60)
        if held() == "stale":
            sys.stderr.write(f"Control socket connect({socket}): Connection refused\n")
            sys.exit(255)
        if held():
            sys.stderr.write("Master running (pid=1)\n")
            sys.exit(0)
        sys.stderr.write(f"Control socket connect({socket}): No such file or directory\n")
        sys.exit(255)
    if after("-O") == "stop":
        os.remove(socket)
        sys.exit(0)
    sys.exit(1)
if "-N" in argv and first("ControlMaster=") == "yes":
    if os.path.exists(os.path.join(here, "hold-open")):
        # The open's ProxyCommand, as ssh starts one, in the open's process group: the master the open leaves keeps it.
        proxy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL)
        # Written beside and renamed into place, so that the test, which reads it as soon as it is there, reads it
        # whole or not at all.
        kept = os.path.join(here, "proxy.pid")
        with open(kept + ".part", "w") as part:
            part.write(str(proxy.pid))
        os.rename(kept + ".part", kept)
    hold("open")
    if os.path.exists(socket):
        # A master never takes a path over: beside one, live or dead, ssh goes on as a connection of its own.
        sys.stderr.write(f"ControlSocket {socket} already exists, disabling multiplexing\n")
        sys.stderr.flush()
        time.sleep(60)
        sys.exit(0)
    if mode == "refused":
        sys.stderr.write(f"{destination}: Permission denied (publickey).\n")
        sys.exit(255)
    with open(socket, "w") as made:
        made.write("live")
    sys.exit(0)
sys.stderr.write("the stand-in dials nothing\n")
sys.exit(255)
"""


def _real_ssh() -> str:
    found = shutil.which("ssh", path=os.defpath)
    if found is None:
        pytest.skip("this machine has no ssh to ask ssh -G of")
    return found


@pytest.fixture
def stand_in(engine_guard, tmp_path) -> Path:
    """The stand-in ssh's folder, first on this test's own PATH."""
    folder = tmp_path / "bin"
    folder.mkdir()
    (folder / "ssh.py").write_text(f"REAL = {_real_ssh()!r}\n" + STAND_IN)
    harness._executable(folder / "ssh", f'#!/bin/sh\nexec "{sys.executable}" -I "{folder / "ssh.py"}" "$@"\n')
    engine_guard.first_on_path(folder)
    return folder


@pytest.fixture
def short_dir() -> Iterator[Path]:
    """The test's own control directory: short enough for a socket's path on macOS, where pytest's own are not."""
    directory = tempfile.mkdtemp(prefix="aw-", dir="/tmp")
    yield Path(directory)
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def made(home: Path) -> Iterator[jobs.Job]:
    """A job of this process's own, as the run that starts the children has; dropped after the test."""
    job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True
    )
    yield job
    jobs.drop(job)


@pytest.fixture
def addresses(monkeypatch) -> None:
    """A name's addresses, as the resolver gives them for an address written as one, and none for any other: what
    route.py's rule reads under auto, with no lookup of the network's."""

    def literal(name, limit=None):
        try:
            return [str(ipaddress.ip_address(str(name)))]
        except ValueError:
            return []

    monkeypatch.setattr(route, "addresses_of", literal)


def _config(home: Path, text: str) -> None:
    """The test's own ~/.ssh/config, which the stand-in gives this machine's ssh -G to read."""
    (home / ".ssh").mkdir(exist_ok=True)
    (home / ".ssh" / "config").write_text(text)


def _calls(folder: Path) -> list[dict]:
    log = folder / "calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _kinds(folder: Path) -> list[str]:
    """Each call, by what it did: -G, -O <command>, open (a master), or the words after its options."""
    kinds = []
    for call in _calls(folder):
        argv = call["argv"]
        if "-G" in argv:
            kinds.append("-G")
        elif "-O" in argv:
            kinds.append(f"-O {argv[argv.index('-O') + 1]}")
        elif "-N" in argv:
            kinds.append("open")
        else:
            kinds.append(" ".join(argv[-2:]))
    return kinds


def _dialled(folder: Path, address: str) -> list[list[str]]:
    """Every call that would have reached address: anything but ssh -G and a control command."""
    return [
        call["argv"]
        for call in _calls(folder)
        if "-G" not in call["argv"] and "-O" not in call["argv"] and address in " ".join(call["argv"])
    ]


def _host(short_dir: Path, address: str = HOST) -> runner.Target:
    return runner.proxmox_host(address, "root", short_dir)


def _machine(tmp_path: Path, short_dir: Path, address: str = ADDRESS, **over) -> runner.Target:
    return runner.machine(IDENTITY, address, "dev", short_dir, **{"known_hosts": tmp_path / "known_hosts", **over})


def _settle(short_dir: Path, setting: str, address: str = HOST, **over) -> dict:
    return _route(connections.Connections(short_dir, dict(os.environ)), setting, _host(short_dir, address), **over)


def _route(conns, setting, host, **over) -> dict:
    return asyncio.run(conns.route(setting, host, **over))


def _held(conns, target, settled) -> None:
    return asyncio.run(conns.held(target, settled))


def _ensure_hop(conns, settled) -> connections.Master | None:
    return asyncio.run(conns.ensure_hop(settled))


def _target(conns, job, machine, settled, given=()) -> runner.Target:
    return asyncio.run(conns.target_for(job, machine, settled, given))


# ---------------------------------------------------------------- the one control directory ----


def test_the_one_control_directory_is_ansible_s_and_the_hop_s_default() -> None:
    # fleet/paths.py names it, and route.hop_settings' default is the same: short, the account's alone, and the one
    # layer 1's Ansible child and the status probe use.
    assert paths.control_dir({"HOME": "/home/person"}) == Path("/home/person/.ansible/cp")
    assert inspect.signature(route.hop_settings).parameters["control_dir"].default == "~/.ansible/cp"


def test_every_connection_lives_in_the_run_s_one_control_directory(tmp_path, short_dir, stand_in, made) -> None:
    conns = connections.Connections(short_dir, dict(os.environ))
    elsewhere = short_dir / "elsewhere"
    with pytest.raises(runner.Refused, match="one control directory"):
        _route(conns, "host", _host(elsewhere))
    machine = runner.machine(IDENTITY, ADDRESS, "dev", elsewhere, known_hosts=tmp_path / "known_hosts")
    for call in (
        lambda: asyncio.run(conns.ensure(made, machine)),
        lambda: _target(conns, made, machine, {"route": "direct"}),
        lambda: _held(conns, machine, {"route": "direct"}),
    ):
        with pytest.raises(runner.Refused, match="one control directory"):
            call()
    with pytest.raises(runner.Refused, match="control directory"):
        connections.Connections("relative/cp", {})
    assert _calls(stand_in) == []


def test_a_machine_s_socket_is_named_by_its_identity_its_address_and_its_hop(tmp_path, short_dir) -> None:
    hop = runner.Hop(" ".join(route.hop_argv(_settle(short_dir, "host")["hop"], ADDRESS, "yes")), "awh-0123456789ab")
    direct, through = _machine(tmp_path, short_dir), _machine(tmp_path, short_dir, hop=hop)
    assert connections.socket_path(direct) == short_dir / f"{checks.vm_control_name(IDENTITY, ADDRESS)}-dev-22"
    assert (
        connections.socket_path(through) == short_dir / f"{checks.vm_control_name(IDENTITY, ADDRESS, hop.name)}-dev-22"
    )
    # The socket the runner's children ride: ssh's -%r-%p made of the same account and port.
    for target in (direct, through):
        [word] = [word for word in runner.options(target) if word.startswith("ControlPath=")]
        assert word.replace("%r", "dev").replace("%p", "22") == f"ControlPath={connections.socket_path(target)}"


@pytest.mark.parametrize(
    ("make", "said"),
    [
        ("link", "is a symbolic link"),
        ("file", "is not a directory"),
        ("group", "may be written in by its group or others"),
        ("others", "may be written in by its group or others"),
    ],
)
def test_a_control_directory_that_is_not_its_owner_s_alone_is_refused_and_starts_nothing(
    short_dir, stand_in, make, said
) -> None:
    # Anyone who may write in it could put a socket at a machine's path before its master, and be handed what every
    # child then sends there, secrets included.
    control = short_dir / "cp"
    if make == "link":
        (short_dir / "elsewhere").mkdir(mode=0o700)
        control.symlink_to(short_dir / "elsewhere")
    elif make == "file":
        control.write_text("")
    else:
        control.mkdir(mode=0o700)
        os.chmod(control, 0o770 if make == "group" else 0o707)
    with pytest.raises(connections.Unsafe, match=said) as refused:
        connections.Connections(control, dict(os.environ))
    assert isinstance(refused.value, runner.Refused) and str(control) in str(refused.value)
    if make in ("group", "others"):
        assert f"run chmod 700 {control}" in str(refused.value)
    assert _calls(stand_in) == []


def test_a_control_directory_of_another_account_s_is_refused(short_dir, stand_in, monkeypatch) -> None:
    mine = os.geteuid()
    monkeypatch.setattr(connections.os, "geteuid", lambda: mine + 1)
    with pytest.raises(connections.Unsafe, match=f"belongs to another account \\(uid {mine}\\)"):
        connections.Connections(short_dir, dict(os.environ))
    assert _calls(stand_in) == []


def test_a_control_directory_of_its_owner_s_alone_or_not_made_yet_is_taken(short_dir) -> None:
    assert stat.S_IMODE(short_dir.stat().st_mode) == 0o700
    connections.Connections(short_dir, {})
    connections.Connections(short_dir / "not-made-yet", {})
    assert not (short_dir / "not-made-yet").exists()


def test_a_machine_whose_socket_s_path_would_be_too_long_logs_in_alone_said_once(tmp_path, stand_in, made) -> None:
    # A long home: past route.SOCKET_MOST a machine takes no shared connection, ensure opens nothing for it, and the
    # run's notes say so once, however many machines it meets.
    long_dir = Path(tempfile.mkdtemp(prefix="aw-" + "c" * 60, dir="/tmp"))
    try:
        conns = connections.Connections(long_dir, dict(os.environ))
        for address in (ADDRESS, OTHER_ADDRESS):
            target = _machine(tmp_path, long_dir, address)
            assert not target.shared and "ControlPath=none" in runner.options(target)
            # No master is used, so none is made sure of: no Master, whose ok would say one answers.
            assert asyncio.run(conns.ensure(made, target)) is None
        assert conns.notes == [connections.UNSHARED.format(directory=long_dir, most=route.SOCKET_MOST)]
        assert _calls(stand_in) == []
    finally:
        shutil.rmtree(long_dir, ignore_errors=True)


# ---------------------------------------------------------------- the route ----


def test_direct_settles_with_no_hop_and_asks_nothing(short_dir, stand_in) -> None:
    settled = _settle(short_dir, "direct")
    assert settled == {"route": "direct", "setting": "direct", "hop": {}, "policy": "accept-new", "addresses": []}
    assert _calls(stand_in) == []


def test_host_settles_the_hop_in_the_shared_directory_and_asks_nothing(short_dir, stand_in) -> None:
    settled = _settle(short_dir, "host", name="pve")
    # The host's own Target's: its ssh, account, address and port, as root SSH to it rides the same master; pinned, so
    # that every line of its own ssh pins ForwardX11=no, PermitLocalCommand=no and ClearAllForwardings=yes first.
    hop = route.hop_settings(
        ssh="ssh", user="root", host=HOST, port=22, control_dir=str(short_dir), name="pve", pinned=True
    )
    assert settled == {"route": "host", "setting": "host", "hop": hop, "policy": "accept-new", "addresses": []}
    assert Path(hop["socket"]).parent == short_dir and hop["label"] == f"pve (root@{HOST})"
    assert _calls(stand_in) == []


@pytest.mark.parametrize(("dialled", "expected"), [(TAILNET, "host"), (LAN, "direct")])
def test_auto_settles_by_where_ssh_dials_for_the_host(home, short_dir, stand_in, addresses, dialled, expected) -> None:
    _config(home, f"Host {HOST}\n  HostName {dialled}\n")
    settled = _settle(short_dir, "auto")
    assert (settled["route"], settled["setting"], settled["addresses"]) == (expected, "auto", [dialled])
    assert bool(settled["hop"]) is (expected == "host")
    # The one call: ssh -G for the host, as the person's own ssh reads it, in a process group of its own.
    assert _kinds(stand_in) == ["-G"]
    [call] = _calls(stand_in)
    assert call["group"] == call["pid"] != os.getpgrp()


def test_under_auto_an_ssh_g_ended_by_a_signal_settles_nothing(home, short_dir, stand_in, addresses) -> None:
    # Read as no answer, it would settle from the address as written: direct, where the person's configuration sends
    # the host to a tailnet. The new engine's run refuses; today's callers of route.py settle as they did.
    _config(home, f"Host {HOST}\n  HostName {TAILNET}\n")
    (stand_in / "killed-G").write_text("")
    with pytest.raises(route.HopRefused) as refused:
        _settle(short_dir, "auto")
    said = str(refused.value)
    assert "ended by a signal (9)" in said and "vm_ssh_route: auto is not settled" in said
    assert said.endswith("set vm_ssh_route: direct or host")
    hop = route.hop_settings(user="root", host=HOST, control_dir=str(short_dir))
    assert route.route_settle("auto", hop)["route"] == "direct"


def test_under_auto_an_ssh_g_past_its_limit_settles_nothing(home, short_dir, stand_in, addresses, monkeypatch) -> None:
    # Its kill at the limit is a signal too: read as no answer, it would settle from the address as written, direct,
    # where the person's configuration sends the host to a tailnet, and the run would dial the VMs straight.
    _config(home, f"Host {HOST}\n  HostName {TAILNET}\n")
    (stand_in / "slow-G").write_text("30")
    monkeypatch.setitem(route.LIMITS, "config", 0.5)
    started = time.monotonic()
    with pytest.raises(route.HopRefused) as refused:
        _settle(short_dir, "auto")
    assert time.monotonic() - started < 10
    said = str(refused.value)
    assert "said nothing of where ssh dials for it within 0.5 s" in said and "vm_ssh_route: auto is not settled" in said
    assert said.endswith("set vm_ssh_route: direct or host")
    # Ended at its limit and waited for: no stand-in is left sleeping (one that had not yet said it started neither).
    for call in _calls(stand_in):
        with pytest.raises(ProcessLookupError):
            os.kill(call["pid"], 0)


def test_every_line_of_the_hop_s_own_ssh_pins_the_display_and_local_commands_off_first(
    home, tmp_path, short_dir, stand_in, made
) -> None:
    # The hop's own ssh reads the person's own config: a Host * of ForwardX11 yes, or PermitLocalCommand yes with a
    # LocalCommand, would reach the open's master, and a ProxyCommand's ssh that finds none live and becomes one; and a
    # ClearAllForwardings no there would have the -W lines open the person's forwards and tunnel.
    conns = connections.Connections(short_dir, dict(os.environ))
    settled = _route(conns, "host", _host(short_dir))
    assert settled["hop"]["pinned"] is True
    pins = ["-o", "ForwardX11=no", "-o", "PermitLocalCommand=no", "-o", "ClearAllForwardings=yes"]
    assert _ensure_hop(conns, settled) == connections.Master(ok=True, opened=True, said="")
    assert _kinds(stand_in) == ["-O check", "open", "-O check"]
    assert all(call["argv"][: len(pins)] == pins for call in _calls(stand_in))
    # A machine's ProxyCommand is the same line, and its own ssh shows that line, which held takes.
    target = _target(conns, made, _machine(tmp_path, short_dir), settled)
    assert target.hop is not None and target.hop.proxy_command.split(" ")[1 : 1 + len(pins)] == pins
    _held(conns, target, settled)


def test_a_long_home_settles_direct_and_refuses_the_route_through_the_host_in_this_engine_s_words(
    home, stand_in, addresses, made
) -> None:
    # Past route.SOCKET_MOST the hop's socket cannot be bound, and the hop is one shared connection: the route through
    # the host is refused, with the one way out it has, and direct settles. route.hop_settings' own sentence, which
    # today's callers keep, names 2.x's ansible_control_path_dir.
    long_dir = Path(tempfile.mkdtemp(prefix="aw-" + "c" * 60, dir="/tmp"))
    try:
        host = _host(long_dir)
        conns = connections.Connections(long_dir, dict(os.environ))
        assert _route(conns, "direct", host)["route"] == "direct"
        _config(home, f"Host {HOST}\n  HostName {LAN}\n")
        assert _route(conns, "auto", host)["route"] == "direct"
        _config(home, f"Host {HOST}\n  HostName {TAILNET}\n")
        for setting in ("host", "auto"):
            with pytest.raises(route.HopRefused) as refused:
                _route(conns, setting, host)
            said = str(refused.value)
            assert said.startswith(
                f"VMs would be reached through root@{HOST}, on one shared connection whose socket, {long_dir}/awh-"
            ), setting
            assert f"longer than {route.SOCKET_MOST} bytes" in said and "ansible_" not in said
            assert said.endswith(
                "set vm_ssh_route: direct where this computer reaches the VMs' network, or run from an account whose"
                " home is shorter"
            )
        # Nothing was opened, and the host's own Target, whose connections log in alone from here, made the note that
        # says so, once, however many times it was met; a machine's that logs in alone adds none.
        assert _kinds(stand_in) == ["-G", "-G"]
        note = connections.UNSHARED.format(directory=long_dir, most=route.SOCKET_MOST)
        assert conns.notes == [note]
        assert "and to the Proxmox host" in note and "103" in note
        assert asyncio.run(conns.ensure(made, _machine(home, long_dir))) is None
        assert conns.notes == [note]
    finally:
        shutil.rmtree(long_dir, ignore_errors=True)


# A run's own process, as a terminal's foreground group: it settles the route under auto while the test sends its group
# SIGINT, as a terminal's Ctrl-C does, which the run only takes note of, as the ladder does a first Ctrl-C.
CTRL_C_RUN = r"""
import asyncio, ipaddress, json, os, signal, sys

lib, control, host = sys.argv[1:]
sys.path.insert(0, lib)
from claude_vm import route
from claude_vm.ssh import connections, runner

noted = []
signal.signal(signal.SIGINT, lambda *_: noted.append("SIGINT"))


def literal(name, limit=None):
    try:
        return [str(ipaddress.ip_address(str(name)))]
    except ValueError:
        return []


route.addresses_of = literal
conns = connections.Connections(control, dict(os.environ))
try:
    settled = asyncio.run(conns.route("auto", runner.proxmox_host(host, "root", control)))
    said = {"route": settled["route"], "addresses": settled["addresses"]}
except route.HopRefused as refused:
    said = {"refused": str(refused)}
print(json.dumps({**said, "noted": noted}), flush=True)
"""


def test_a_first_ctrl_c_while_the_route_is_settled_kills_no_ssh_and_settles_it_as_it_is(home, short_dir, stand_in):
    # The route's ssh -G waits for a file the test writes once the Ctrl-C is sent, never for a time: the Ctrl-C lands
    # while it runs, however loaded the machine.
    _config(home, f"Host {HOST}\n  HostName {TAILNET}\n")
    (stand_in / "hold-G").write_text("")
    argv = [sys.executable, "-I", "-c", CTRL_C_RUN, str(REPO / "lib"), str(short_dir), HOST]
    run = subprocess.Popen(
        argv,
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        text=True,
        process_group=0,
        preexec_fn=harness._default_signals,
    )
    try:
        _until(lambda: "-G" in _kinds(stand_in), "the route's ssh -G started")
        os.killpg(run.pid, signal.SIGINT)
        (stand_in / "go").write_text("")
        out, _ = run.communicate(timeout=60)
    finally:
        run.kill()
        run.wait(timeout=10)
    assert json.loads(out) == {"route": "host", "addresses": [TAILNET], "noted": ["SIGINT"]}


@pytest.mark.parametrize("waiting", ["route", "ensure_hop", "held"])
def test_route_ensure_hop_and_held_wait_off_the_event_loop_while_another_task_goes_on(
    home, tmp_path, short_dir, stand_in, made, addresses, waiting
) -> None:
    # Each runs route.py's ssh in a thread of its own (asyncio.to_thread): while that ssh waits - here a stand-in held
    # until a file appears - another task on the same loop goes on, and that task is the one that writes the file. Run
    # on the loop, the call would hold it until route.py's limit ended the ssh, and the task would not have run
    # meanwhile.
    _config(home, f"Host {HOST}\n  HostName {TAILNET}\n")
    conns = connections.Connections(short_dir, dict(os.environ))
    host = _host(short_dir)
    settled = _route(conns, "host", host)
    target = _target(conns, made, _machine(tmp_path, short_dir), settled)
    calls = {
        "route": (lambda: conns.route("auto", host), "G"),
        "ensure_hop": (lambda: conns.ensure_hop(settled), "check"),
        "held": (lambda: conns.held(target, settled), "G"),
    }
    call, kind = calls[waiting]
    (stand_in / f"hold-{kind}").write_text("")
    before = len(_calls(stand_in))

    async def both() -> tuple[bool, asyncio.Future]:
        waited = asyncio.ensure_future(call())
        let_go = False
        while not waited.done():
            await asyncio.sleep(0.01)
            if not let_go and not waited.done() and len(_calls(stand_in)) > before:
                # The held ssh has started, the call has not returned, and this task runs: it lets the ssh go.
                (stand_in / "go").write_text("")
                let_go = True
        return let_go, waited

    let_go, waited = asyncio.run(both())
    assert let_go, f"{waiting} held the event loop while its ssh waited"
    # The held ssh went on because this task let it go, not because a limit ended it.
    assert (stand_in / f"released-{kind}").exists()
    answer = waited.result()
    if waiting == "route":
        assert (answer["route"], answer["addresses"]) == ("host", [TAILNET])
    elif waiting == "ensure_hop":
        assert answer == connections.Master(ok=True, opened=True, said="")
    else:
        assert answer is None
    assert not (stand_in / f"hold-{kind}").exists()


def test_the_hop_s_account_is_vm_ssh_jump_where_it_names_one(short_dir, stand_in) -> None:
    assert _settle(short_dir, "host", jump="hop")["hop"]["user"] == "hop"


@pytest.mark.parametrize("jump", ["hop@elsewhere", "hop:2222", "-oProxyCommand=x"])
def test_a_hop_that_is_not_one_plain_word_stops_the_run_naming_direct(short_dir, stand_in, jump) -> None:
    with pytest.raises(route.HopRefused) as refused:
        _settle(short_dir, "host", jump=jump)
    assert "vm_ssh_route: direct" in str(refused.value)
    assert _calls(stand_in) == []


def test_under_auto_a_hop_that_is_not_one_plain_word_matters_only_where_the_rule_goes_through_it(
    short_dir, stand_in, addresses
) -> None:
    assert _settle(short_dir, "auto", LAN, jump="hop@elsewhere")["route"] == "direct"
    with pytest.raises(route.HopRefused, match="vm_ssh_route: direct"):
        _settle(short_dir, "auto", TAILNET, jump="hop@elsewhere")


def test_a_setting_that_is_no_route_and_a_machine_for_the_host_are_refused(tmp_path, short_dir) -> None:
    conns = connections.Connections(short_dir, {})
    with pytest.raises(runner.Refused, match="vm_ssh_route is one of auto, direct, host"):
        _route(conns, "tailnet", _host(short_dir))
    with pytest.raises(runner.Refused, match="Proxmox host's own Target"):
        _route(conns, "host", _machine(tmp_path, short_dir))


# ---------------------------------------------------------------- a machine's target ----


def test_under_host_a_machine_s_line_holds_the_hop_s_command_word_for_word(tmp_path, short_dir, stand_in, made) -> None:
    conns = connections.Connections(short_dir, dict(os.environ))
    settled = _route(conns, "host", _host(short_dir))
    target = _target(conns, made, _machine(tmp_path, short_dir), settled, {"Compression": "yes"})
    proxy = " ".join(route.hop_argv(settled["hop"], ADDRESS, settled["policy"]))
    assert target.hop == runner.Hop(proxy, os.path.basename(settled["hop"]["socket"]))
    assert f" -W {ADDRESS}:%p " in f" {proxy} " and "%h" not in proxy
    words = runner.argv(target, ["true"])
    assert f"ProxyCommand={proxy}" in words and target.given == (("Compression", "yes"),)
    assert f"ControlPath={short_dir}/{checks.vm_control_name(IDENTITY, ADDRESS, target.hop.name)}-%r-%p" in words
    # The person's own configuration was read once, for the machine's own address, and nothing was dialled.
    assert _kinds(stand_in) == ["-G"]
    [call] = _calls(stand_in)
    assert call["argv"][-2:] == ["--", ADDRESS] and "Compression=yes" in call["argv"]
    assert call["env"][children.MARKER] == made.id


def test_under_direct_a_machine_takes_no_hop_and_nothing_is_read(tmp_path, short_dir, stand_in, made) -> None:
    conns = connections.Connections(short_dir, dict(os.environ))
    target = _target(conns, made, _machine(tmp_path, short_dir), _route(conns, "direct", _host(short_dir)))
    assert target.hop is None and _calls(stand_in) == []


@pytest.mark.parametrize(
    "given", [{"ProxyJump": "jumper@jump.example.test"}, {"proxycommand": "nc %h %p"}, {"ProxyJump": "a,b"}]
)
def test_a_machine_given_a_jump_by_vm_ssh_options_takes_no_hop(tmp_path, short_dir, stand_in, made, given) -> None:
    conns = connections.Connections(short_dir, dict(os.environ))
    target = _target(conns, made, _machine(tmp_path, short_dir), _route(conns, "host", _host(short_dir)), given)
    assert target.hop is None and target.given == tuple(given.items())
    # Decided from the options alone: the person's configuration is not asked.
    assert _calls(stand_in) == []


def test_proxycommand_none_is_no_jump(tmp_path, short_dir, stand_in, made) -> None:
    conns = connections.Connections(short_dir, dict(os.environ))
    settled = _route(conns, "host", _host(short_dir))
    assert _target(conns, made, _machine(tmp_path, short_dir), settled, {"ProxyCommand": "none"}).hop is not None


def test_a_machine_whose_own_ssh_config_shows_a_jump_takes_no_hop(home, tmp_path, short_dir, stand_in, made) -> None:
    _config(home, f"Host {ADDRESS}\n  ProxyJump jumper@jump.example.test\n")
    conns = connections.Connections(short_dir, dict(os.environ))
    settled = _route(conns, "host", _host(short_dir))
    machine = _machine(tmp_path, short_dir)
    assert _target(conns, made, machine, settled).hop is None
    # Read once a run for each machine: asked again, the run's reading is used.
    assert _target(conns, made, machine, settled).hop is None
    assert _kinds(stand_in) == ["-G"]
    # A machine the configuration does not name takes the hop; a new run reads the configuration again.
    assert _target(conns, made, _machine(tmp_path, short_dir, OTHER_ADDRESS), settled).hop is not None
    fresh = connections.Connections(short_dir, dict(os.environ))
    assert _target(fresh, made, machine, settled).hop is None
    assert _kinds(stand_in) == ["-G", "-G", "-G"]


def test_a_configuration_ssh_cannot_read_is_said_and_nothing_is_built(home, tmp_path, short_dir, stand_in, made):
    _config(home, "Host *\n  NoSuchOption yes\n")
    conns = connections.Connections(short_dir, dict(os.environ))
    with pytest.raises(
        runner.Refused, match=r"ssh -G could not read your ssh configuration for 192\.0\.2\.9: .*Bad config"
    ):
        _target(conns, made, _machine(tmp_path, short_dir), _route(conns, "host", _host(short_dir)))


def test_target_for_takes_a_machine_with_no_hop_and_no_options_of_its_own(tmp_path, short_dir, stand_in, made):
    conns = connections.Connections(short_dir, dict(os.environ))
    settled = _route(conns, "host", _host(short_dir))
    for machine in (
        _machine(tmp_path, short_dir, given={"Compression": "yes"}),
        _target(conns, made, _machine(tmp_path, short_dir), settled),
        _host(short_dir),
    ):
        with pytest.raises(runner.Refused, match="no hop and no vm_ssh_options"):
            _target(conns, made, machine, settled)
    # The person's options are held as the runner holds them.
    with pytest.raises(runner.Refused, match="vm_ssh_options sets ControlPath"):
        _target(conns, made, _machine(tmp_path, short_dir), settled, {"ControlPath": "/tmp/mine"})


# ---------------------------------------------------------------- the held check ----


def test_held_takes_the_hop_s_command_and_a_jump_of_the_machine_s_own(home, tmp_path, short_dir, stand_in, made):
    conns = connections.Connections(short_dir, dict(os.environ))
    settled = _route(conns, "host", _host(short_dir))
    _held(conns, _target(conns, made, _machine(tmp_path, short_dir), settled), settled)
    jump = {"ProxyJump": "jumper@jump.example.test"}
    _held(conns, _target(conns, made, _machine(tmp_path, short_dir), settled, jump), settled)
    _config(home, f"Host {OTHER_ADDRESS}\n  ProxyJump jumper@jump.example.test\n")
    _held(conns, _target(conns, made, _machine(tmp_path, short_dir, OTHER_ADDRESS), settled), settled)
    direct = _route(conns, "direct", _host(short_dir))
    _held(conns, _target(conns, made, _machine(tmp_path, short_dir), direct), direct)
    assert not _dialled(stand_in, ADDRESS) and not _dialled(stand_in, OTHER_ADDRESS)
    # Every ssh -G, held's (route.ssh_shown) and target_for's, in a process group of its own.
    assert [call["group"] == call["pid"] for call in _calls(stand_in)] == [True] * len(_calls(stand_in))


def test_held_refuses_any_other_proxy_before_anything_connects(tmp_path, short_dir, stand_in, made) -> None:
    conns = connections.Connections(short_dir, dict(os.environ))
    settled = _route(conns, "host", _host(short_dir))
    other = route.hop_settings(user="root", host="pve-other", control_dir=str(short_dir))
    elsewhere = runner.Hop(" ".join(route.hop_argv(other, ADDRESS, settled["policy"])), "awh-0123456789ab")
    refusals = {
        # No hop and no jump of its own: ssh would dial the VM from here.
        "dialling it from here": _machine(tmp_path, short_dir),
        # Another host's hop: not this route's command.
        "not the hop": _machine(tmp_path, short_dir, hop=elsewhere),
    }
    for said, target in refusals.items():
        with pytest.raises(connections.Unheld, match=said) as refused:
            _held(conns, target, settled)
        assert "vm_ssh_route: direct" in str(refused.value)
    # Under direct, a target that takes the hop.
    hopped = _target(conns, made, _machine(tmp_path, short_dir), settled)
    with pytest.raises(connections.Unheld, match="where the route is direct"):
        _held(conns, hopped, _route(conns, "direct", _host(short_dir)))
    assert not _dialled(stand_in, ADDRESS)


# ---------------------------------------------------------------- the hop's master ----


def test_ensure_hop_opens_the_hop_s_master_once_and_finds_it_the_second_time(short_dir, stand_in) -> None:
    conns = connections.Connections(short_dir, dict(os.environ))
    settled = _route(conns, "host", _host(short_dir))
    assert _ensure_hop(conns, settled) == connections.Master(ok=True, opened=True, said="")
    assert _ensure_hop(conns, settled) == connections.Master(ok=True, opened=False, said="")
    assert _kinds(stand_in) == ["-O check", "open", "-O check", "-O check"]
    assert Path(settled["hop"]["socket"]).read_text() == "live"
    # route.py's open, with this process's own environment, which a run never marks; each of its ssh in a process
    # group of its own.
    assert children.MARKER not in _calls(stand_in)[1]["env"] and children.MARKER not in os.environ
    assert [call["group"] == call["pid"] for call in _calls(stand_in)] == [True] * 4
    # Under direct no hop's master is used: none is made sure of, and no Master says one answers.
    assert _ensure_hop(conns, _route(conns, "direct", _host(short_dir))) is None and len(_calls(stand_in)) == 4


@pytest.mark.parametrize(("setting", "named"), [("auto", "vm_ssh_route: direct"), ("host", "vm_ssh_route: host")])
def test_a_hop_that_cannot_be_used_stops_the_run_and_nothing_dials_the_vm(
    home, tmp_path, short_dir, stand_in, made, addresses, setting, named
) -> None:
    _config(home, f"Host {HOST}\n  HostName {TAILNET}\n")
    (stand_in / f"mode-root@{HOST}").write_text("refused")
    conns = connections.Connections(short_dir, dict(os.environ))
    settled = _route(conns, setting, _host(short_dir))
    target = _target(conns, made, _machine(tmp_path, short_dir), settled)
    with pytest.raises(connections.Unusable, match="ssh could not log in there") as unusable:
        _ensure_hop(conns, settled)
    # route.login_said's sentence in the new engine's names: under auto it names vm_ssh_route: direct, under host the
    # setting given; the key is this computer's ssh_identity_file, never an inventory's variable.
    said = str(unusable.value)
    assert named in said and "Permission denied" in said
    assert "ssh_identity_file" in said and "ansible_" not in said and f"`ssh -p 22 root@{HOST}`" in said
    assert target.hop is not None and not _dialled(stand_in, ADDRESS)


# ---------------------------------------------------------------- a machine's master ----


@pytest.mark.parametrize("through", [False, True])
def test_ensure_opens_a_master_once_unmarked_and_finds_it_the_second_time(
    tmp_path, short_dir, stand_in, made, through
) -> None:
    conns = connections.Connections(short_dir, dict(os.environ))
    settled = _route(conns, "host" if through else "direct", _host(short_dir))
    target = _target(conns, made, _machine(tmp_path, short_dir), settled)
    assert (target.hop is not None) is through
    before = len(_calls(stand_in))
    assert asyncio.run(conns.ensure(made, target)) == connections.Master(ok=True, opened=True, said="")
    assert asyncio.run(conns.ensure(made, target)) == connections.Master(ok=True, opened=False, said="")
    calls = _calls(stand_in)[before:]
    assert _kinds(stand_in)[before:] == ["-O check", "open", "-O check", "-O check"]
    assert connections.socket_path(target).read_text() == "live"
    opened = calls[1]
    # The master: the runner's options, ControlMaster=yes and ControlPersist in place of ControlMaster=no, then -N.
    options = runner.options(target)
    at = options.index("ControlMaster=no")
    assert opened["argv"] == [
        *options[:at],
        "ControlMaster=yes",
        "-o",
        f"ControlPersist={connections.PERSIST}",
        *options[at + 1 :],
        "-N",
        "-l",
        "dev",
        "-p",
        "22",
        "--",
        ADDRESS,
    ]
    # Unmarked, in a session of its own, and entered in no registry: no run ends it.
    assert children.MARKER not in opened["env"] and opened["session"] == opened["pid"]
    for call in (calls[0], calls[2], calls[3]):
        assert call["env"][children.MARKER] == made.id and call["session"] != call["pid"]
        assert call["argv"][: len(options)] == options
    assert children.live(made) == []


def test_ensure_removes_a_dead_socket_file_first(tmp_path, short_dir, stand_in, made) -> None:
    conns = connections.Connections(short_dir, dict(os.environ))
    target = _machine(tmp_path, short_dir)
    connections.socket_path(target).write_text("stale")
    assert asyncio.run(conns.ensure(made, target)) == connections.Master(ok=True, opened=True, said="")
    assert _kinds(stand_in) == ["-O check", "open", "-O check"]
    assert connections.socket_path(target).read_text() == "live"


def test_an_open_that_fails_says_why_and_is_not_tried_again(tmp_path, short_dir, stand_in, made) -> None:
    (stand_in / f"mode-{ADDRESS}").write_text("refused")
    conns = connections.Connections(short_dir, dict(os.environ))
    master = asyncio.run(conns.ensure(made, _machine(tmp_path, short_dir)))
    assert master == connections.Master(ok=False, opened=False, said=f"{ADDRESS}: Permission denied (publickey).")
    assert _kinds(stand_in) == ["-O check", "open", "-O check"]


def test_a_socket_whose_check_ran_out_its_time_is_kept_and_the_open_beside_it_ended_at_what_ssh_says(
    tmp_path, short_dir, stand_in, made, monkeypatch
) -> None:
    # Never removed: a master that does not answer within the check's limit may be another run's, busy. The open beside
    # it says the socket exists and would go on as a connection of its own, so it is ended then, well within its limit.
    conns = connections.Connections(short_dir, dict(os.environ))
    target = _machine(tmp_path, short_dir)
    connections.socket_path(target).write_text("hung")
    monkeypatch.setitem(route.LIMITS, "check", 0.5)
    started = time.monotonic()
    master = asyncio.run(conns.ensure(made, target))
    assert time.monotonic() - started < route.LIMITS["open"]
    assert master == connections.Master(
        ok=False, opened=False, said=f"ControlSocket {connections.socket_path(target)} {connections.ALREADY}"
    )
    assert _kinds(stand_in) == ["-O check", "open", "-O check"]
    assert connections.socket_path(target).read_text() == "hung"


def test_ensure_never_opens_a_master_for_a_first_use_or_the_host(tmp_path, short_dir, stand_in, made) -> None:
    conns = connections.Connections(short_dir, dict(os.environ))
    first = _machine(tmp_path, short_dir, known_hosts=tmp_path / "scratch", first_use=True)
    with pytest.raises(runner.Refused, match="no master is opened for a machine until its key is filed"):
        asyncio.run(conns.ensure(made, first))
    with pytest.raises(runner.Refused, match="ensure_hop"):
        asyncio.run(conns.ensure(made, _host(short_dir)))
    # A first use rides no master at all: a connection of its own.
    assert "ControlPath=none" in runner.options(first)
    assert _calls(stand_in) == []


def test_a_run_remembers_nothing_of_a_master_s_open(made) -> None:
    # The master a finished open leaves behind keeps its ProxyCommand in the open's group, so a run's end that had
    # remembered the open's tree at a Ctrl-C during it would end that master, which no run does (review/code/B8.md 3).
    holds = (
        "import subprocess, sys, time;"
        " subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)"
    )

    async def opening() -> None:
        devnull = asyncio.subprocess.DEVNULL
        process = await children.start(
            made,
            [sys.executable, "-c", holds],
            children.SESSION,
            dict(os.environ),
            devnull,
            devnull,
            devnull,
            children.MASTER,
        )
        # Another child of the run's, which is remembered.
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL)
        try:
            await asyncio.to_thread(_until, lambda: len(proc.descendants(process.pid)) == 1, "the open's ProxyCommand")
            assert children.opening() == [process.pid] and children.live(made) == []
            # The ladder's remember on a stand-in of its own: what it leaves out, its hold of the interrupts aside.
            ladder = types.SimpleNamespace(remembered={}, blind=[], _held=contextlib.nullcontext)
            attached._Ladder.remember(ladder)
            tree = {process.pid, *proc.descendants(process.pid)}
            assert ladder.blind == [] and other.pid in ladder.remembered and not tree & set(ladder.remembered)
        finally:
            other.kill()
            other.wait()
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
        assert children.opening() == []

    asyncio.run(opening())


def test_a_run_remembers_nothing_of_the_hop_s_open_while_it_runs(short_dir, stand_in) -> None:
    # route.py opens the hop's master in a process group of its own, and the master it leaves keeps its ProxyCommand in
    # the open's group: a run's end that had remembered the open's tree at a Ctrl-C during it would end the hop's
    # master, which no run does, as for a machine's (review/code/B23.md 6).
    conns = connections.Connections(short_dir, dict(os.environ))
    settled = _route(conns, "host", _host(short_dir))
    (stand_in / "hold-open").write_text("")

    async def opening() -> connections.Master:
        waited = asyncio.ensure_future(conns.ensure_hop(settled))
        # Another child of the run's, which is remembered.
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL)
        try:
            # The stand-in renames proxy.pid into place, so that here and in the finally below it is read whole.
            await asyncio.to_thread(_until, lambda: (stand_in / "proxy.pid").exists(), "the open's ProxyCommand")
            [opened] = [call["pid"] for call in _calls(stand_in) if "-N" in call["argv"]]
            tree = {opened, int((stand_in / "proxy.pid").read_text())}
            assert set(proc.descendants(opened)) | {opened} == tree
            # The ladder's remember on a stand-in of its own: what it leaves out, its hold of the interrupts aside.
            ladder = types.SimpleNamespace(remembered={}, blind=[], _held=contextlib.nullcontext)
            attached._Ladder.remember(ladder)
            assert ladder.blind == [] and other.pid in ladder.remembered and not tree & set(ladder.remembered)
            assert children.opening() == [opened]
        finally:
            other.kill()
            other.wait()
            (stand_in / "go").write_text("")
            master = await waited
        assert children.opening() == []
        return master

    try:
        assert asyncio.run(opening()) == connections.Master(ok=True, opened=True, said="")
    finally:
        if (stand_in / "proxy.pid").exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int((stand_in / "proxy.pid").read_text()), signal.SIGKILL)


# ---------------------------------------------------------------- a first use ----


def _key() -> hostkeys.Key:
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(range(32))
    return hostkeys.Key("ssh-ed25519", base64.b64encode(blob).decode())


def test_a_first_use_is_decided_by_the_key_filed_and_takes_its_key_in_the_run_s_directory(
    tmp_path, short_dir, stand_in, made
) -> None:
    conns = connections.Connections(short_dir, dict(os.environ))
    product = tmp_path / "known_hosts"
    target = _machine(tmp_path, short_dir)
    with pytest.raises(runner.Refused, match="no run directory"):
        connections.first_use(made, target)
    directory = rundir.make(made)
    try:
        first = connections.first_use(made, target)
        scratch = directory.resolve() / f"{hostkeys.SCRATCH_PREFIX}{IDENTITY}"
        assert (first.first_use, first.known_hosts) == (True, scratch)
        assert scratch.read_bytes() == b"" and stat.S_IMODE(scratch.stat().st_mode) == 0o600
        # On a connection of its own, and no master is opened for it: its key is not filed yet.
        assert "ControlPath=none" in runner.options(first)
        with pytest.raises(runner.Refused, match="until its key is filed"):
            asyncio.run(conns.ensure(made, first))
        with pytest.raises(runner.Refused, match="a first use is decided"):
            connections.first_use(made, first)
        # Filed: no first use, and the master is opened for the strict connection.
        hostkeys.file(product, IDENTITY, _key())
        assert connections.first_use(made, target) is target
        assert asyncio.run(conns.ensure(made, target)).opened
        assert _kinds(stand_in) == ["-O check", "open", "-O check"]
    finally:
        rundir.remove(made)


# ---------------------------------------------------------------- the forward through the hop ----


def test_a_forward_is_route_py_s_of_its_own_in_a_group_of_its_own_and_only_for_a_machine_that_takes_the_hop(
    tmp_path, short_dir, stand_in, made
) -> None:
    # B8's refusal of every route but direct is gone (card G7): host settles a hop, and the one forward through it a run
    # makes is route.hop_forward's, a connection of its own to the machine's own port, its ssh leading a group of its
    # own; the stand-in forwards nothing, so ssh fails to use the host, which is what the verdict says.
    conns = connections.Connections(short_dir, dict(os.environ))
    direct = _route(conns, "direct", _host(short_dir))
    settled = _route(conns, "host", _host(short_dir))
    alone = _target(conns, made, _machine(tmp_path, short_dir), direct)
    through = _target(conns, made, _machine(tmp_path, short_dir, port=2222), settled)
    for target, route_ in ((alone, settled), (through, direct), (_host(short_dir), settled)):
        with pytest.raises(runner.Refused, match="a forward through the hop is made"):
            asyncio.run(conns.forward(target, route_))
    assert _kinds(stand_in) == ["-G"]
    verdict = asyncio.run(conns.forward(through, settled))
    assert (verdict["why"], verdict["usable"]) == ("failed", False)
    [*_, call] = _calls(stand_in)
    assert call["argv"][-3:] == ["-W", f"{ADDRESS}:2222", f"root@{HOST}"] and "ControlPath=none" in call["argv"]
    assert "LogLevel=VERBOSE" in call["argv"] and call["group"] == call["pid"]


# ---------------------------------------------------------------- the rules ----


def _code() -> list[Path]:
    code = [
        path for path in repo_files(*(f"lib/claude_vm/{name}" for name in NEW), untracked=True) if path.suffix == ".py"
    ]
    code.append(REPO / "tools" / "aw")
    assert CONNECTIONS in code, code
    return code


def _said(tree: ast.AST) -> set[int]:
    """Every docstring and bare string of tree: what code says of itself, never what it sends."""
    return {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr)}


def _control_commands(source: str) -> list[str]:
    """Each place source sends a control command other than check: a word -O not followed in its list by check, and a
    string that runs -O and a command together."""
    tree = ast.parse(source)
    said = _said(tree)
    allowed: set[int] = set()
    for node in ast.walk(tree):
        elements = (
            node.elts if isinstance(node, (ast.List, ast.Tuple)) else node.args if isinstance(node, ast.Call) else []
        )
        for word, after in zip(elements, elements[1:], strict=False):
            if isinstance(word, ast.Constant) and word.value == "-O":
                if isinstance(after, ast.Constant) and after.value == "check":
                    allowed.add(id(word))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in said:
            if node.value == "-O" and id(node) not in allowed:
                found.append(f"line {node.lineno}: -O, not followed by check")
            elif re.search(r"(?<!\S)-O\s*(?!check\b)[A-Za-z]", node.value):
                found.append(f"line {node.lineno}: {node.value!r}")
    return found


def test_no_control_command_but_check_and_never_o_exit_in_the_new_subpackages() -> None:
    found = {str(path.relative_to(REPO)): where for path in _code() if (where := _control_commands(path.read_text()))}
    assert found == {}, "the engine sends -O check alone: never -O exit, which cuts another run's sessions, nor -O stop"


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ('argv = ["ssh", "-O", "exit", host]', True),
        ('argv = ["ssh", "-O", "stop", host]', True),
        ('word = "-O"', True),
        ('line = "ssh -O exit host"', True),
        ('line = "-Oexit"', True),
        ("run(ssh, *options, '-O', command)", True),
        ('argv = [ssh, *options, "-O", "check", *place]', False),
        ('status = data.get("exit")', False),
        ('"""Never -O exit."""', False),
    ],
)
def test_the_control_command_scan_sees_each_way(source, found) -> None:
    assert bool(_control_commands(source)) is found


# What starts a child, beside a function of the module's own that does: children.start, and route.py's functions that
# run the hop's ssh or ssh -G.
ROUTE_STARTS = {"hop_held", "hop_check", "hop_stop", "hop_forward", "route_settle", "ssh_shown", "_run"}


def _callee(node: ast.Call) -> tuple[str, str]:
    """A call's owner and name: ("children", "start"), ("route", "hop_held"), ("self", "_child"), ("", "run")."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return getattr(func.value, "id", ""), func.attr
    return "", getattr(func, "id", "")


def _retries(sources: list[str]) -> list[str]:
    """Each place the sources start a connection again: a call that starts a child, or calls a function that does,
    inside a loop or a comprehension; a function that starts a child calling itself; or ssh's ConnectionAttempts."""
    trees = [ast.parse(source) for source in sources]
    functions = {
        node.name: node
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def starts(owner: str, name: str, starting: set[str]) -> bool:
        return (owner, name) == ("children", "start") or (owner == "route" and name in ROUTE_STARTS) or name in starting

    starting: set[str] = set()
    while True:
        more = {
            name
            for name, function in functions.items()
            if name not in starting
            and any(isinstance(call, ast.Call) and starts(*_callee(call), starting) for call in ast.walk(function))
        }
        if not more:
            break
        starting |= more
    found = []
    loops = (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    for tree in trees:
        for loop in ast.walk(tree):
            if isinstance(loop, loops):
                for call in ast.walk(loop):
                    if isinstance(call, ast.Call) and starts(*_callee(call), starting):
                        found.append(f"line {call.lineno}: {'.'.join(filter(None, _callee(call)))} in a loop")
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "connectionattempts" in node.value.lower()
            ):
                found.append(f"line {node.lineno}: {node.value!r}")
    for name in starting:
        if any(isinstance(call, ast.Call) and _callee(call)[1] == name for call in ast.walk(functions[name])):
            found.append(f"{name} calls itself")
    return sorted(set(found))


def test_nothing_in_ssh_retries_a_connection() -> None:
    files = [path for path in repo_files("lib/claude_vm/ssh", untracked=True) if path.suffix == ".py"]
    assert CONNECTIONS in files, files
    assert _retries([path.read_text() for path in files]) == []


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("async def a(job):\n    for _ in range(3):\n        await children.start(job)", True),
        (
            "async def a(job):\n    await children.start(job)\n"
            "async def b(job):\n    while True:\n        await a(job)",
            True,
        ),
        ("def a():\n    return route.hop_held(hop, policy)\ndef b(hops):\n    return [a() for _ in hops]", True),
        ("async def a(job):\n    await children.start(job)\n    await a(job)", True),
        ('words = ["-o", "ConnectionAttempts=3"]', True),
        (
            "async def a(job):\n    await children.start(job)\nasync def b(job):\n    await a(job)\n    await a(job)",
            False,
        ),
        (
            "async def a(job):\n    p = await children.start(job)\n"
            "    while p.returncode is None:\n        await p.wait()",
            False,
        ),
    ],
)
def test_the_retry_scan_sees_each_way(source, found) -> None:
    assert bool(_retries([source])) is found


# ssh's flags that take no value (its getopt string's): a word of them run together, -NM, holds -M where it holds M.
_FLAGS = re.compile(r"-[1246afgknqstvxACGKMNTVXYy]*M[1246afgknqstvxACGKMNTVXYy]*")


def _masters(path: Path, source: str) -> list[str]:
    """Each place source opens a master or names the kind of child that carries no marker: a ControlMaster other than
    no - in a string, or in an f-string whose formatted value is its value - ssh's -M alone or run together with other
    flags, children.MASTER, or master=True; each with the function it is in."""
    tree = ast.parse(source)
    said = _said(tree)
    inside: dict[int, str] = {}
    # ast.walk gives a function before the functions inside it, so the innermost is written last.
    for function in [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for node in ast.walk(function):
            inside[id(node)] = function.name
    # An f-string is read whole, a formatted value as {}, and the strings inside it not again.
    joined = {id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) for part in node.values}
    found = []
    for node in ast.walk(tree):
        where = f"{path.name}:{inside.get(id(node), '<module>')}"
        if isinstance(node, ast.JoinedStr) and id(node) not in said:
            text = "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in node.values)
            master = re.search(r"(?i)controlmaster\s*=\s*(\w+|\{\})", text)
            if master and master.group(1).lower() != "no":
                found.append(f"{where}: {text}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in said | joined:
            master = re.search(r"(?i)controlmaster\s*=\s*(\w+)", node.value)
            if (master and master.group(1).lower() != "no") or _FLAGS.fullmatch(node.value):
                found.append(f"{where}: {node.value}")
        elif isinstance(node, ast.Attribute) and node.attr == "MASTER" and getattr(node.value, "id", "") == "children":
            found.append(f"{where}: children.MASTER")
        elif isinstance(node, ast.keyword) and node.arg == "master":
            if isinstance(node.value, ast.Constant) and node.value.value is True:
                found.append(f"{where}: master=True")
    return found


def test_only_ensure_opens_a_master_and_only_with_the_kind_that_carries_no_marker() -> None:
    found = [
        each
        for path in _code()
        if path.name != "children.py" or path.parent.name != "runs"
        for each in _masters(path, path.read_text())
    ]
    # ensure writes ControlMaster=yes and asks its child for a master; _child, which ensure alone asks so, starts it as
    # children.MASTER, in a session of its own. route.py opens the hop's, outside the new subpackages.
    assert sorted(found) == [
        "connections.py:_child: children.MASTER",
        "connections.py:ensure: ControlMaster=yes",
        "connections.py:ensure: master=True",
    ]


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ('def f():\n    return ["-o", "ControlMaster=auto"]', ["x.py:f: ControlMaster=auto"]),
        ('def f():\n    return ["-o", "ControlMaster=yes"]', ["x.py:f: ControlMaster=yes"]),
        ('def f():\n    return ["-M", host]', ["x.py:f: -M"]),
        ('def f():\n    return ["-NM", host]', ["x.py:f: -NM"]),
        ('def f():\n    return ["-fMN", host]', ["x.py:f: -fMN"]),
        ('def f(mode):\n    return ["-o", f"ControlMaster={mode}"]', ["x.py:f: ControlMaster={}"]),
        ('def f(mode):\n    return [f"-oControlMaster=yes{mode}"]', ["x.py:f: -oControlMaster=yes{}"]),
        ('def f(x):\n    return [f"ControlMaster=no {x}"]', []),
        ('def f():\n    return ["-N", "-Mode"]', []),
        ("def f():\n    return children.MASTER", ["x.py:f: children.MASTER"]),
        ("def f():\n    return g(master=True)", ["x.py:f: master=True"]),
        ('def f():\n    return ["-o", "ControlMaster=no"]', []),
        ('def f():\n    """ControlMaster=yes is ensure\'s."""', []),
    ],
)
def test_the_master_scan_sees_each_way(source, found) -> None:
    assert _masters(Path("x.py"), source) == found
