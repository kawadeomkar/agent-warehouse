"""The stand-in machines of the new engine's tests (tests/unit/aw_machine.py; docs/architecture/engine.md §16.10).

Every machine here is a directory of the test's own, and every connection to one goes through B3's runner to the
stand-in ssh, first on the test's own PATH by engine_guard.first_on_path. A command lands in the machine whose address
it names and in no other, and changes nothing outside that machine's directory; an address no machine has comes back as
the runner's Unreachable; the machine's own key filed under its identity passes, and a connection that names one
machine's identity at another's address is refused as ssh refuses it, as is one with no key filed, while a first use
files the key as ssh does, a machine's under its identity in its scratch file and the Proxmox host's under its address
in the test's own known-hosts file; words whose first StrictHostKeyChecking is neither yes nor accept-new, or whose
first BatchMode is not yes, another account, a known-hosts file of this computer's own ~/.ssh and a sudo named by its
path are refused, and nothing runs; standard input arrives whole, and a held child reads a line sent later; a
machine's command has no terminal, its own HOME, its bin/ first on PATH (its python3 the test's own Python) and the
test's own failing stand-ins next, and nothing else of the test's environment, and calls.log keeps the names of the
variables it was started with, never their values; a sudo that asks receives the password only after -n was refused
for want of one, takes each line after a wrong one as a further attempt, up to sudo's three, and keeps it nowhere but
as a digest; under sudo a command has sudo's HOME and secure_path whatever the caller's, and sudo's options; -O check
and -O stop answer as a live master would, and a forward is recorded and never opened; ssh -G is answered by this
computer's own ssh with the test's own configuration; a hop answers its control commands from its socket's file, opens
its master in its login's mode and forwards in its forward's; and a machine behind a ProxyCommand is reached only where
the proxy reaches its sshd. A machine's home and system
files are made as today's stand-in VMs make theirs (tests/unit/test_status_vms.py's `_vms`, case by case); planted
makes the Proxmox fake's VM report the machine's address and identity; and a play of one task over the local
connection touches only its machine's directory. The rules with a test of their own: no test_aw_*.py file writes an
ssh stand-in of its own, or hands the runner an ssh of its own, but the SSH layer's own tests and the guard's, each
named with why.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import dataclasses
import hmac
import json
import os
import pwd
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import harness
import pytest
from aw_kit import searched_for
from aw_machine import (
    CONTROL,
    Machine,
    filed,
    hop,
    hop_modes,
    known_hosts,
    layer1_host,
    machine,
    machine_path,
    planted,
    ssh_stand_in,
    target,
    unrouted,
)
from harness import REPO, _pve, ansible_config, fake_fleet, repo_files
from test_status_vms import CASES, _vms

from claude_vm import checks, proxmox
from claude_vm.fleet import paths
from claude_vm.pve import agent, answers
from claude_vm.runs import children
from claude_vm.runs import job as jobs
from claude_vm.secret import Secret
from claude_vm.ssh import hostkeys, runner

UNIT = REPO / "tests" / "unit"
# A sudo password no file, word or variable may hold: low in entropy, so that gitleaks takes it for no key.
PASSWORD = "unit-test-aw-machine-sudo-canary"
# What each machine's command is asked for, where it meets a program: the stand-ins a run must never reach past.
DESKTOP = ("open", "xdg-open", "pbcopy")
REFUSED = ("ssh", "security", "ansible-playbook")
OWN = ("gh", "claude", "tailscale", "systemctl", "dpkg-query", "getent", "sudo", "python3")
# A value of the runner's environment that no file under the machines may hold: calls.log keeps names alone.
CANARY = "unit-test-aw-machine-environment-canary"


@pytest.fixture
def made(home: Path) -> Iterator[jobs.Job]:
    """A job of this process's own, as the run that starts the children has; dropped after the test."""
    job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True
    )
    yield job
    jobs.drop(job)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "machines"


@pytest.fixture
def two(root: Path, engine_guard) -> tuple[Machine, Machine, Path]:
    """alpha, ready as status_vms' ready VM is, and beta, as a machine is made by default, each one's key filed under
    its identity in the file their targets check; the stand-in ssh's folder, first on this test's own PATH."""
    alpha = machine(root, "alpha", **CASES["ready"])
    beta = machine(root, "beta")
    filed(alpha)
    filed(beta)
    folder = ssh_stand_in(root, [alpha, beta])
    engine_guard.first_on_path(folder)
    return alpha, beta, folder


def _run(job: jobs.Job, at: runner.Target, command: list[str], stdin=None) -> runner.Result:
    return asyncio.run(runner.run(job, at, command, stdin, runner.LIMITS, dict(os.environ)))


def _direct(at: runner.Target) -> runner.Target:
    """at marked as a route settled straight at it marks one (connections.target_for), which a test of the stand-ins
    reaches without a route: a Target made for an address no target() gives."""
    return dataclasses.replace(at, routed_as="direct")


def _stand_in(folder: Path, words: list[str]) -> subprocess.CompletedProcess[str]:
    """The stand-in ssh in folder run by its path with words, as a run's ssh is given them."""
    return subprocess.run(
        [str(folder / "ssh"), *words], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30
    )


def _snapshot(top: Path) -> dict[str, object]:
    """Every entry under top by its path from top: a folder, a link and where it points, or a file's bytes and mode. A
    git checkout's own .git is said to be there and no more: its objects carry the time they were made."""
    seen: dict[str, object] = {}
    for where, folders, files in os.walk(top):
        here = Path(where)
        for name in [*folders, *files]:
            path = here / name
            relative = str(path.relative_to(top))
            if path.is_symlink():
                seen[relative] = ("link", os.readlink(path))
            elif path.is_dir():
                seen[relative] = "folder"
            else:
                seen[relative] = ("file", path.read_bytes(), path.stat().st_mode)
        # os.walk follows no link; a checkout's .git is said and not read.
        folders[:] = [name for name in folders if name != ".git"]
    return seen


def _changed(before: dict, after: dict) -> list[str]:
    return sorted(path for path in {*before, *after} if before.get(path) != after.get(path))


# ---------------------------------------------------------------- the runner reaches a machine ----


def test_a_command_run_through_the_runner_lands_in_its_machine_and_in_no_other(two, made) -> None:
    alpha, beta, folder = two
    command = ["sh", "-c", 'pwd; echo "$HOME"; touch landed']
    ran = _run(made, target(alpha), command)
    assert isinstance(ran, runner.Done) and (ran.status, ran.said) == (0, ""), ran
    assert [Path(line).resolve() for line in ran.lines] == [alpha.home.resolve()] * 2
    assert (alpha.home / "landed").is_file() and not (beta.home / "landed").exists()
    # The call is the machine's alone: the runner's words as they were given, and the job's marker among the names of
    # the variables it was started with.
    [call] = alpha.calls()
    assert call["argv"] == runner.argv(target(alpha), command)[1:] and call["outcome"] == "ran"
    assert children.MARKER in call["env"]
    assert beta.calls() == [] and unrouted(folder) == []


def test_after_a_run_only_the_machine_s_own_directory_has_changed(two, made, tmp_path) -> None:
    alpha, *_ = two
    before = _snapshot(tmp_path)
    _run(made, target(alpha), ["sh", "-c", 'echo kept > "$HOME/.claude/note"; mkdir made; touch made/one'])
    _run(made, target(alpha), ["rm", "-f", "dpkg-status"])
    changed = _changed(before, _snapshot(tmp_path))
    # The test's own home holds the run's job, which is the run's and no machine's.
    changed = [path for path in changed if not path.startswith("aw-home/")]
    assert {"machines/alpha/calls.log", "machines/alpha/home/.claude/note", "machines/alpha/home/made/one"} <= set(
        changed
    ), changed
    assert "machines/alpha/home/dpkg-status" in changed
    assert [path for path in changed if not path.startswith("machines/alpha/")] == []


def test_an_address_no_machine_has_comes_back_as_the_runner_s_unreachable(two, made, tmp_path) -> None:
    alpha, beta, folder = two
    stranger = _direct(
        runner.machine(alpha.identity, "203.0.113.250", alpha.account, CONTROL, known_hosts=tmp_path / "kh")
    )
    got = _run(made, stranger, ["touch", "landed"])
    assert isinstance(got, runner.Unreachable) and got.lines == (), got
    assert "203.0.113.250 port 22: No route to host" in got.said
    # ssh -G is no connection: this computer's own ssh answers it, given the test's own configuration, and nothing is
    # dialled.
    shown = _stand_in(folder, ["-G", "-F", "/dev/null", alpha.address])
    if _real_ssh() is None:
        assert shown.returncode == 255 and "no ssh to ask -G of" in shown.stderr
    else:
        assert shown.returncode == 0 and f"hostname {alpha.address}" in shown.stdout.splitlines(), shown
    assert [call["outcome"] for call in unrouted(folder)][0] == "refused: no machine has the address"
    assert alpha.calls() == beta.calls() == []
    assert not (alpha.home / "landed").exists() and not (beta.home / "landed").exists()


def _real_ssh() -> str | None:
    return shutil.which("ssh", path=os.defpath)


def test_ssh_g_reads_the_test_s_own_configuration_and_never_the_developer_s(two, home) -> None:
    # OpenSSH finds ~ in the account database, never in HOME: the stand-in gives this computer's ssh -F with the test's
    # own ~/.ssh/config, or /dev/null where there is none, unless the line gives its own.
    alpha, _, folder = two
    if _real_ssh() is None:
        pytest.skip("this machine has no ssh to ask ssh -G of")
    plain = _stand_in(folder, ["-G", alpha.address])
    assert plain.returncode == 0 and "proxyjump jump.invalid" not in plain.stdout.splitlines(), plain
    (home / ".ssh").mkdir()
    (home / ".ssh" / "config").write_text(f"Host {alpha.address}\n  ProxyJump jump.invalid\n")
    configured = _stand_in(folder, ["-G", alpha.address])
    assert "proxyjump jump.invalid" in configured.stdout.splitlines(), configured
    alone = _stand_in(folder, ["-G", "-F", "/dev/null", alpha.address])
    assert "proxyjump jump.invalid" not in alone.stdout.splitlines(), alone
    assert [call["outcome"] for call in unrouted(folder)] == ["-G"] * 3
    assert alpha.calls() == []


def _hop_words(at: runner.Target, socket: Path, *rest: str, level: str = "INFO") -> list[str]:
    """The words the hop's own ssh is given, as route.py gives them: its pins, its policy, its shared connection or
    none, then rest and root@ the host."""
    shared = ["-o", "ControlMaster=auto", "-o", f"ControlPath={socket}"] if socket else ["-o", "ControlPath=none"]
    return [
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"LogLevel={level}",
        *shared,
        *rest,
        f"root@{at.address}",
    ]


def test_a_hop_answers_from_its_socket_opens_in_its_login_s_mode_and_forwards_in_its_forward_s(
    root, home, engine_guard
) -> None:
    # The test's own home, made: the hop's key is taken at first use into its ~/.ssh/known_hosts.
    pve = hop(root)
    alpha = machine(root, "alpha")
    folder = ssh_stand_in(root, [pve, alpha])
    engine_guard.first_on_path(folder)
    at = runner.proxmox_host(pve.address, "root", CONTROL)
    socket = root / "awh-socket"
    check = _hop_words(at, socket, "-O", "check")
    assert _stand_in(folder, check).stderr.startswith(f"Control socket connect({socket}): No such file or directory")
    socket.write_text("stale")
    assert "Connection refused" in _stand_in(folder, check).stderr
    socket.unlink()
    hop_modes(pve, login="refused")
    opened = _stand_in(folder, _hop_words(at, socket, "-N"))
    assert opened.returncode == 255 and "Permission denied (publickey)" in opened.stderr
    hop_modes(pve, login="open")
    assert _stand_in(folder, _hop_words(at, socket, "-N")).returncode == 0
    assert socket.read_text() == "live" and _stand_in(folder, check).returncode == 0
    # A forward of its own at VERBOSE: the login said, then the machine's sshd's first words on its output.
    forward = _stand_in(folder, _hop_words(at, None, "-W", f"{alpha.address}:22", level="VERBOSE"))
    assert forward.returncode == 0 and forward.stdout.startswith("SSH-2.0-"), forward
    assert forward.stderr.startswith(f"Authenticated to {pve.address} ")
    # To an address no machine has: the host's connection there refused.
    nowhere = _stand_in(folder, _hop_words(at, socket, "-W", "203.0.113.250:22"))
    assert nowhere.returncode == 255 and "connect failed: No route to host" in nowhere.stderr
    for mode, said in (
        ("prohibited", "administratively prohibited"),
        ("failed", "stdio forwarding request failed"),
    ):
        hop_modes(pve, forward=mode)
        refused = _stand_in(folder, _hop_words(at, socket, "-W", f"{alpha.address}:22"))
        assert refused.returncode == 255 and said in refused.stderr, (mode, refused)
    assert _stand_in(folder, _hop_words(at, socket, "-O", "stop")).returncode == 0 and not socket.exists()
    assert [call["outcome"] for call in pve.calls()] == [
        "-O check",
        "-O check",
        "refused: the login",
        "a master, opened",
        "-O check",
        f"forward {alpha.address}:22 unshared",
        "forward 203.0.113.250:22",
        f"forward {alpha.address}:22",
        f"forward {alpha.address}:22",
        "-O stop",
    ]
    assert alpha.calls() == []


def test_a_machine_reached_through_a_proxy_is_reached_only_where_the_proxy_reaches_its_sshd(
    root, home, engine_guard
) -> None:
    pve, alpha = hop(root), machine(root, "alpha")
    filed(alpha)
    folder = ssh_stand_in(root, [pve, alpha])
    engine_guard.first_on_path(folder)
    socket = root / "awh-socket"
    proxy = " ".join(["ssh", *_hop_words(runner.proxmox_host(pve.address, "root", CONTROL), socket, "-W", "%h:%p")])
    words = [*runner.argv(target(alpha), ["touch", "landed"])[1:]]
    at = words.index("--")
    through = [*words[:at], "-o", f"ProxyCommand={proxy}", *words[at:]]
    ran = _stand_in(folder, through)
    assert ran.returncode == 0, ran
    assert (alpha.home / "landed").is_file()
    # The ProxyCommand's ssh, with no master live, became the master on the hop's socket, as ssh's does.
    assert socket.read_text() == "live"
    hop_modes(pve, forward="prohibited")
    refused = _stand_in(folder, through)
    assert refused.returncode == 255 and "administratively prohibited" in refused.stderr
    assert "Connection closed by UNKNOWN port 65535" in refused.stderr
    assert [call["outcome"] for call in alpha.calls()] == ["ran", "refused: its proxy reached no SSH server"]
    forwards = [f"forward {alpha.address}:22 unshared", f"forward {alpha.address}:22"]
    assert [call["outcome"] for call in pve.calls()] == forwards


def _without(words: list[str], option: str) -> list[str]:
    at = words.index(option)
    assert words[at - 1] == "-o"
    return words[: at - 1] + words[at + 1 :]


LOOSE = "refused: not StrictHostKeyChecking=yes or accept-new, and BatchMode=yes"


def test_words_that_loosen_the_key_s_check_ask_or_name_another_account_are_refused_and_nothing_runs(two, made) -> None:
    alpha, _, folder = two
    words = runner.argv(target(alpha), ["touch", "landed"])[1:]
    # The runner always gives BatchMode=yes: these words are made here without it.
    no_batch = _stand_in(folder, _without(words, "BatchMode=yes"))
    # ssh keeps the first value given: a yes after a no, or after an ask, makes up for nothing.
    earlier = _stand_in(folder, ["-o", "StrictHostKeyChecking=no", *words])
    asking = _stand_in(folder, ["-o", "StrictHostKeyChecking=ask", *words])
    # The account the machine was made with is the one it takes: given by -l, or by -o User=, as Ansible's ssh
    # connection gives it, quoted, before the runner's own -l.
    other = _stand_in(folder, ["-l", "root", *words])
    named = _stand_in(folder, ["-o", 'User="root"', *words])
    for refused in (no_batch, earlier, asking):
        assert refused.returncode == 255 and "(StrictHostKeyChecking=yes or accept-new, and BatchMode=yes" in (
            refused.stderr
        )
    for refused in (other, named):
        assert refused.returncode == 255 and f"root@{alpha.address}: Permission denied (publickey)." in refused.stderr
    assert not (alpha.home / "landed").exists()
    assert [call["outcome"] for call in alpha.calls()] == [
        LOOSE,
        LOOSE,
        LOOSE,
        "refused: another account",
        "refused: another account",
    ]


# ---------------------------------------------------------------- the host key ----


def test_a_connection_naming_one_machine_s_identity_at_another_s_address_is_refused_as_ssh_refuses_it(
    two, made, root, tmp_path
) -> None:
    alpha, beta, _ = two
    # alpha's identity at beta's address: beta answers there, with its own key, where the file holds alpha's under
    # that name.
    sibling = _direct(
        runner.machine(alpha.identity, beta.address, beta.account, CONTROL, known_hosts=known_hosts(alpha))
    )
    got = _run(made, sibling, ["touch", "landed"])
    assert isinstance(got, runner.Unreachable), got
    assert "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!" in got.said
    assert f"Offending ED25519 key in {known_hosts(alpha)}:1" in got.said
    assert got.said.endswith(
        f"ED25519 host key for {alpha.identity} has changed and you have requested strict checking.\n"
        "Host key verification failed."
    )
    # Nothing filed under the name at all: refused under strict checking, never taken.
    empty = tmp_path / "empty_known_hosts"
    unfiled = _run(made, target(alpha, known_hosts=empty), ["touch", "landed"])
    assert isinstance(unfiled, runner.Unreachable), unfiled
    assert unfiled.said == (
        f"No ED25519 host key is known for {alpha.identity} and you have requested strict checking.\n"
        "Host key verification failed."
    )
    assert not empty.exists()
    # Neither ran, and the file was left as it was: the two machines' lines, each under its own identity.
    assert not (alpha.home / "landed").exists() and not (beta.home / "landed").exists()
    assert [call["outcome"] for call in beta.calls()] == ["refused: another host key is filed under the name"]
    assert [call["outcome"] for call in alpha.calls()] == ["refused: no host key is filed under the name"]
    assert known_hosts(alpha).read_text() == f"{alpha.line}\n{beta.line}\n"
    # A line a person's ssh hashed (HashKnownHosts) is read as ssh reads it, and so is a pattern.
    hashed = tmp_path / "hashed_known_hosts"
    salt = b"\x07" * 20
    digest = hmac.new(salt, alpha.identity.encode(), "sha1").digest()
    name = f"|1|{base64.b64encode(salt).decode()}|{base64.b64encode(digest).decode()}"
    hashed.write_text(f"# a comment\n{name} {alpha.key}\nagent-warehouse-* {beta.key}\n")
    assert _run(made, target(alpha, known_hosts=hashed), ["true"]) == runner.Done(0, (), "")
    assert _run(made, target(beta, known_hosts=hashed), ["true"]) == runner.Done(0, (), "")


def test_a_first_use_files_the_machine_s_key_under_its_identity_as_ssh_does(two, made, tmp_path) -> None:
    alpha, beta, _ = two
    scratch = hostkeys.fresh(tmp_path, alpha.identity)
    first = target(alpha, first_use=True, known_hosts=scratch)
    assert _run(made, first, ["touch", "landed"]) == runner.Done(0, (), "")
    assert (alpha.home / "landed").is_file()
    # The one line ssh writes, under the identity and never the address, which B5 reads back as the machine's key.
    assert scratch.read_text() == f"{alpha.line}\n"
    assert hostkeys.taken(scratch, alpha.identity) == hostkeys.parse(alpha.key)
    # Known now: a second use finds it there and writes nothing more.
    assert _run(made, first, ["true"]) == runner.Done(0, (), "")
    assert scratch.read_text() == f"{alpha.line}\n"
    # A first use takes whatever key answers at the address: alpha's identity at beta's takes beta's, which is why a
    # key is read where something vouches for it before a first use, never from the first use alone.
    (tmp_path / "elsewhere").mkdir()
    elsewhere = hostkeys.fresh(tmp_path / "elsewhere", alpha.identity)
    wrong = _direct(
        runner.machine(alpha.identity, beta.address, beta.account, CONTROL, known_hosts=elsewhere, first_use=True)
    )
    assert _run(made, wrong, ["true"]) == runner.Done(0, (), "")
    assert hostkeys.taken(elsewhere, alpha.identity) == hostkeys.parse(beta.key)
    # Another key under the identity in the scratch file is refused at a first use too, as ssh refuses a changed key.
    changed = tmp_path / "changed"
    changed.write_text(f"{alpha.identity} {beta.key}")
    refused = _run(made, target(alpha, first_use=True, known_hosts=changed), ["touch", "again"])
    assert isinstance(refused, runner.Unreachable) and "Host key verification failed." in refused.said, refused
    assert changed.read_text() == f"{alpha.identity} {beta.key}" and not (alpha.home / "again").exists()


def test_the_proxmox_host_s_first_use_takes_its_key_into_the_test_s_own_known_hosts_under_its_address(
    root, made, home, engine_guard
) -> None:
    host = machine(root, "pve", account="root")
    engine_guard.first_on_path(ssh_stand_in(root, [host]))
    at = runner.proxmox_host(host.address, "root", CONTROL)
    assert _run(made, at, ["touch", "landed"]) == runner.Done(0, (), "")
    # Into the person's own file, as ssh takes the host's at first use today: the test's home's, made as ssh makes it.
    person = home / ".ssh" / "known_hosts"
    assert person.read_text() == f"{host.address} {host.key}\n"
    assert (person.parent.stat().st_mode & 0o777) == 0o700
    assert _run(made, at, ["true"]) == runner.Done(0, (), "")
    assert person.read_text() == f"{host.address} {host.key}\n"
    # Another key there under the address: refused, as a host whose key changed is.
    person.write_text(f"{host.address} {machine(root, 'other').key}\n")
    refused = _run(made, at, ["touch", "again"])
    assert isinstance(refused, runner.Unreachable) and "Host key verification failed." in refused.said, refused
    assert not (host.home / "again").exists()


def test_a_known_hosts_file_of_this_computer_s_own_ssh_folder_is_refused_and_never_opened(two) -> None:
    alpha, _, folder = two
    own = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".ssh" / "known_hosts"
    words = runner.argv(target(alpha), ["touch", "landed"])[1:]
    refused = _stand_in(folder, ["-o", f"UserKnownHostsFile={own}", *words])
    assert refused.returncode == 255 and "this computer's own ~/.ssh" in refused.stderr
    assert [call["outcome"] for call in alpha.calls()] == ["refused: this computer's own ~/.ssh"]
    assert not (alpha.home / "landed").exists()


# A sudo by its path, or command -p, which takes the system's default PATH: each would be this computer's own on a lane.
BY_PATH = (
    ["/usr/bin/sudo", "-n", "true"],
    ["sh", "-c", "exec /bin/sudo -n true"],
    ["/usr/lib/cargo/bin/sudo", "-n", "true"],
    ["/usr/bin/sudo-rs", "-n", "true"],
    ["sh", "-c", "/usr/bin/sudo.ws -n true"],
    ["sh", "-c", "command -p sudo -n true"],
    ["sh", "-c", "command -pv sudo"],
)


def test_a_sudo_named_by_its_path_is_refused_as_this_computer_s_own(two, made) -> None:
    alpha, *_ = two
    for command in BY_PATH:
        got = _run(made, target(alpha), command)
        assert isinstance(got, runner.Unreachable) and "names a sudo by its path" in got.said, (command, got)
    assert alpha.sudo_calls() == []
    assert [call["outcome"] for call in alpha.calls()] == ["refused: this computer's own sudo"] * len(BY_PATH)
    # The machine's own, by its path or by name, runs; a word that only holds sudo inside it is no sudo.
    for command in ([str(alpha.bin / "sudo"), "-n", "true"], ["sudo", "-n", "true"], ["echo", "/opt/pseudo"]):
        assert _run(made, target(alpha), command).status == 0, command


def test_standard_input_arrives_whole_and_a_held_child_reads_a_line_sent_later(two, made) -> None:
    alpha, *_ = two
    # Every byte, NUL and newline among them, and an end with no newline.
    data = bytes(range(256)) * 4096 + b"the end, with no newline"
    got = _run(made, target(alpha), ["sh", "-c", 'cat > "$HOME/received"'], data)
    assert got == runner.Done(0, (), "")
    assert (alpha.home / "received").read_bytes() == data

    async def held() -> tuple[str, runner.Result]:
        command = ["sh", "-c", 'echo ready; read line; echo "got $line"']
        child = await runner.hold(made, target(alpha), command, dict(os.environ), deadline=30)
        async for line in child.lines():
            first = line
            break
        await child.send("a line sent once the child spoke")
        return first, await child.result()

    assert asyncio.run(held()) == ("ready", runner.Done(0, ("ready", "got a line sent once the child spoke"), ""))


def test_a_machine_s_command_has_its_own_environment_and_meets_the_stand_ins_first(two, made, engine_guard) -> None:
    alpha, *_ = two
    tools = " ".join([*DESKTOP, *REFUSED, *OWN])
    command = ["sh", "-c", f'env; echo "-- found"; for tool in {tools}; do command -v "$tool"; done']
    got = _run(made, target(alpha), command)
    assert isinstance(got, runner.Done) and got.status == 0, got
    at = got.lines.index("-- found")
    variables = dict(line.split("=", 1) for line in got.lines[:at] if "=" in line)
    # What a shell, or macOS for each process, sets for itself aside: the machine's HOME, its PATH and today's
    # stand-ins' STAND_IN alone.
    itself = {"PWD", "OLDPWD", "SHLVL", "_", "__CF_USER_TEXT_ENCODING"}
    assert set(variables) - itself == {"HOME", "PATH", "STAND_IN"}, sorted(variables)
    assert variables["HOME"] == variables["STAND_IN"] == str(alpha.home)
    assert variables["PATH"].split(os.pathsep)[0] == str(alpha.bin)
    found = dict(zip([*DESKTOP, *REFUSED, *OWN], got.lines[at + 1 :], strict=True))
    standing = engine_guard.stand_ins
    assert {tool: found[tool] for tool in DESKTOP} == {tool: str(standing / ".desktop" / tool) for tool in DESKTOP}
    assert {tool: found[tool] for tool in REFUSED} == {tool: str(standing / ".refused" / tool) for tool in REFUSED}
    assert {tool: found[tool] for tool in OWN} == {tool: str(alpha.bin / tool) for tool in OWN}


def test_a_machine_s_python3_is_the_test_s_own_python_on_every_system(two, made) -> None:
    alpha, *_ = two
    asked = "import sys; print(sys.executable); print('{}.{}'.format(*sys.version_info[:2]))"
    got = _run(made, target(alpha), ["python3", "-c", asked])
    assert isinstance(got, runner.Done) and got.status == 0, got
    # Never the system's (macOS's own is 3.9, below the floor): the interpreter this test runs on.
    assert got.lines == (sys.executable, "{}.{}".format(*sys.version_info[:2]))


def test_calls_log_keeps_the_names_of_the_runner_s_variables_and_never_their_values(two, made, root) -> None:
    alpha, *_ = two
    env = {**os.environ, "AW_MACHINE_CANARY": CANARY}
    got = asyncio.run(runner.run(made, target(alpha), ["true"], None, runner.LIMITS, env))
    assert got == runner.Done(0, (), "")
    [call] = alpha.calls()
    assert "AW_MACHINE_CANARY" in call["env"]
    assert searched_for([CANARY], root) == []


def test_a_machine_s_command_has_no_terminal_so_dev_tty_fails_at_once(two, tmp_path) -> None:
    alpha, _, folder = two
    # Run as a session of its own whose controlling terminal is a pseudo-terminal, as a person's own terminal would be
    # pytest's: the command on the machine must not reach it, as no command sshd starts without a terminal can.
    opens = "if ( : </dev/tty ) 2>/dev/null; then echo tty-opened; else echo no-tty; fi"
    words = runner.argv(target(alpha), ["sh", "-c", opens])[1:]
    argv = [str(folder / "ssh"), *words]
    status, output = harness._at_a_pseudo_terminal(argv, tmp_path, dict(os.environ), timeout=60)
    assert status == 0, output
    assert b"no-tty" in output and b"tty-opened" not in output, output


# ---------------------------------------------------------------- sudo ----


def _attempts(made_machine: Machine) -> list[tuple]:
    return [(call["n"], call["S"], call["read"], call["outcome"]) for call in made_machine.sudo_calls()]


def test_a_sudo_that_asks_gets_the_password_only_after_n_was_refused_and_once(root, made, engine_guard) -> None:
    asking = machine(root, "asking", sudo_password=PASSWORD)
    filed(asking)
    engine_guard.first_on_path(ssh_stand_in(root, [asking]))
    at = target(asking)
    refused = _run(made, at, ["sudo", "-n", "-H", "true"])
    assert refused == runner.Done(1, (), "sudo: a password is required")
    # The password's line, then what the command reads: sudo takes the one line and leaves the rest.
    sent = [Secret(PASSWORD), "\nrest"]
    took = _run(made, at, ["sudo", "-S", "-p", "", "-H", "sh", "-c", 'cat > "$HOME/rest"'], sent)
    assert took == runner.Done(0, (), "")
    assert (asking.root_home / "rest").read_text() == "rest"
    # A line ends at a carriage return too, as tgetpass reads one: the rest begins with what followed it.
    ended = _run(made, at, ["sudo", "-S", "-p", "", "sh", "-c", 'cat > "$HOME/ended"'], [Secret(PASSWORD), "\r\nrest"])
    assert ended == runner.Done(0, (), "") and (asking.root_home / "ended").read_text() == "\nrest"
    assert _attempts(asking) == [
        (True, False, 0, "refused: a password is required"),
        (False, True, 1, "ran"),
        (False, True, 1, "ran"),
    ]
    # Nowhere on disk: no word, no variable, no record holds it, and the machine keeps its digest alone.
    assert searched_for([PASSWORD], root) == []


def test_a_wrong_password_makes_sudo_take_each_line_after_it_as_a_further_attempt(root, made, engine_guard) -> None:
    asking = machine(root, "asking", sudo_password=PASSWORD)
    filed(asking)
    engine_guard.first_on_path(ssh_stand_in(root, [asking]))
    at = target(asking)
    command = ["sudo", "-S", "-p", "", "sh", "-c", 'cat > "$HOME/after"']
    # The line after a wrong password is the second attempt, as sudo takes it: here the password, so the command runs.
    second = _run(made, at, command, ["wrong\n", Secret(PASSWORD), "\nrest"])
    assert second == runner.Done(0, (), "Sorry, try again.")
    assert (asking.root_home / "after").read_text() == "rest"
    (asking.root_home / "after").unlink()
    # Three lines that are not it: sudo's three attempts, with its words, and nothing run.
    three = _run(made, at, command, "one\ntwo\nthree\nrest")
    assert three == runner.Done(1, (), "Sorry, try again.\nSorry, try again.\nsudo: 3 incorrect password attempts")
    # A caller that puts nothing after the password's line loses no further attempt: sudo meets the input's end.
    alone = _run(made, at, command, "wrong\n")
    assert alone == runner.Done(
        1, (), "Sorry, try again.\nsudo: no password was provided\nsudo: 1 incorrect password attempt"
    )
    nothing = _run(made, at, command, None)
    assert nothing == runner.Done(1, (), "sudo: no password was provided")
    assert not (asking.root_home / "after").exists()
    assert _attempts(asking) == [
        (False, True, 2, "ran"),
        (False, True, 3, "refused: a wrong password"),
        (False, True, 1, "refused: no password"),
        (False, True, 0, "refused: no password"),
    ]
    assert searched_for([PASSWORD], root) == []


def test_a_sudo_that_asks_nothing_reads_nothing_of_standard_input(root, made, engine_guard) -> None:
    free = machine(root, "free")
    filed(free)
    engine_guard.first_on_path(ssh_stand_in(root, [free]))
    assert _run(made, target(free), ["sudo", "-n", "true"]) == runner.Done(0, (), "")
    # As sudo with no password to ask: -S reads no line, and the command is given the whole of standard input.
    took = _run(made, target(free), ["sudo", "-S", "-p", "", "sh", "-c", 'cat > "$HOME/all"'], "line\nrest")
    assert took == runner.Done(0, (), "") and (free.root_home / "all").read_text() == "line\nrest"
    assert _attempts(free) == [(True, False, 0, "ran"), (False, True, 0, "ran")]


def _variables(lines: tuple[str, ...]) -> dict[str, str]:
    """env's lines as names and values, less what a shell, or macOS for each process, sets for itself."""
    itself = {"PWD", "OLDPWD", "SHLVL", "_", "__CF_USER_TEXT_ENCODING"}
    return {name: value for name, _, value in (line.partition("=") for line in lines) if name not in itself}


def test_under_sudo_a_command_has_sudo_s_home_and_secure_path_whatever_the_caller_s(two, made) -> None:
    alpha, *_ = two
    at = target(alpha)
    # The caller's own HOME, PATH and a variable of its own, none of which sudo keeps (env_reset, secure_path).
    reset = _run(made, at, ["sh", "-c", "HOME=/nowhere PATH=/elsewhere:$PATH KEPT=1 sudo -n env"])
    assert isinstance(reset, runner.Done) and reset.status == 0, reset
    assert _variables(reset.lines) == {
        "HOME": str(alpha.root_home),
        "PATH": machine_path(alpha),
        "USER": "root",
        "LOGNAME": "root",
        "SUDO_USER": alpha.account,
        "STAND_IN": str(alpha.home),
    }
    # As the machine's own account, its home; -E keeps the caller's variables but PATH; --preserve-env those named.
    own = _run(made, at, ["sh", "-c", f"sudo -n -u {alpha.account} env"])
    assert _variables(own.lines)["HOME"] == str(alpha.home)
    kept = _variables(_run(made, at, ["sh", "-c", "PATH=/elsewhere:$PATH KEPT=1 sudo -n -E env"]).lines)
    assert (kept["KEPT"], kept["HOME"], kept["PATH"]) == ("1", str(alpha.home), machine_path(alpha))
    command = "KEPT=1 OTHER=2 sudo --non-interactive --preserve-env=KEPT env"
    named = _variables(_run(made, at, ["sh", "-c", command]).lines)
    assert (named.get("KEPT"), named.get("OTHER"), named["HOME"]) == ("1", None, str(alpha.root_home))


def test_sudo_takes_its_own_options_and_refuses_a_shell(two, made, root) -> None:
    alpha, *_ = two
    at = target(alpha)
    listed = _run(made, at, ["sudo", "-n", "-l"])
    rule = "    (ALL : ALL) NOPASSWD: ALL"
    assert listed == runner.Done(0, (f"User {alpha.account} may run the following commands on {alpha.name}:", rule), "")
    assert _run(made, at, ["sudo", "-n", "-l", "gh", "auth"]) == runner.Done(0, (f"{alpha.bin / 'gh'} auth",), "")
    for command in (["sudo", "-n", "-v"], ["sudo", "-K"], ["sudo", "-k"], ["sudo", "--non-interactive", "--", "true"]):
        assert _run(made, at, command) == runner.Done(0, (), ""), command
    for command, said in (
        (["sudo", "-n", "-i", "true"], "never a shell or a login shell (-i, -s)"),
        (["sudo", "-n", "-s"], "never a shell or a login shell (-i, -s)"),
        (["sudo", "--bogus", "true"], "sudo: unrecognized option '--bogus'"),
        (["sudo", "-n", "-u", "nobody", "true"], "sudo: unknown user nobody"),
        (["sudo", "-n", "no-such-program"], "sudo: no-such-program: command not found"),
    ):
        got = _run(made, at, command)
        assert isinstance(got, runner.Done) and got.status == 1 and said in got.said, (command, got)
    # Where sudo asks a password, -n -l and -n -v are refused for want of it, as sudo refuses them.
    asking = machine(root, "asking", sudo_password=PASSWORD)
    filed(asking)
    # The stand-in's table made again with both, in the folder already first on this test's PATH.
    ssh_stand_in(root, [alpha, asking])
    for command in (["sudo", "-n", "-l"], ["sudo", "-n", "-v"]):
        assert _run(made, target(asking), command) == runner.Done(1, (), "sudo: a password is required"), command


# ---------------------------------------------------------------- a master and a forward ----


def test_a_master_answers_check_and_stop_and_a_forward_is_recorded_and_never_opened(two, made) -> None:
    alpha, _, folder = two
    at = target(alpha)
    options, place = runner.options(at), ["-l", at.account, "-p", str(at.port), "--", at.address]
    check = _stand_in(folder, [*options, "-O", "check", *place])
    stop = _stand_in(folder, [*options, "-O", "stop", *place])
    gone = _stand_in(folder, [*options, "-O", "exit", *place])
    assert check.returncode == 0 and check.stderr.startswith("Master running (pid=")
    assert stop.returncode == 0 and stop.stderr.startswith("Stop listening request sent.")
    assert gone.returncode == 255 and "-O exit is not answered" in gone.stderr
    port = harness._free_port()
    spec = f"127.0.0.1:{port}:127.0.0.1:{port}"
    # In the background at once (-f), as ssh goes once its forwards are bound: none ever is.
    background = _stand_in(folder, [*runner.options(at, forward=True), "-f", "-L", spec, *place, "sleep", "1"])
    assert background.returncode == 0

    async def forwarded() -> list[str]:
        held = await runner.forward(made, at, [spec], dict(os.environ), deadline=30)
        try:
            for _ in range(200):
                if len(alpha.calls()) == 5:
                    break
                await asyncio.sleep(0.05)
            with pytest.raises(ConnectionRefusedError):
                socket.create_connection(("127.0.0.1", port), timeout=5).close()
            return [call["outcome"] for call in alpha.calls()]
        finally:
            await held.end()

    assert asyncio.run(forwarded()) == [
        "-O check",
        "-O stop",
        "refused: -O exit",
        "forward, never opened",
        "forward, never opened",
    ]


# ---------------------------------------------------------------- what a machine holds ----


def _link(target_of: str, top: Path) -> object:
    """A link as a machine's own: where it points within top, or which program of the stand-ins' it is."""
    pointed = Path(target_of)
    return ("in", str(pointed.relative_to(top))) if pointed.is_relative_to(top) else ("program", pointed.name)


def _held_as_a_machine(top: Path, system: Path | None = None) -> dict[str, object]:
    """top's entries as _snapshot gives them, each link read by _link, with system's under system/ where it is a folder
    of its own."""
    seen = {}
    for path, entry in _snapshot(top).items():
        seen[path] = _link(entry[1], top) if isinstance(entry, tuple) and entry[0] == "link" else entry
    if system is not None:
        seen.update({f"system/{path}": entry for path, entry in _snapshot(system).items()})
        seen["system"] = "folder"
    return seen


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_machine_s_home_and_system_files_are_made_as_today_s_stand_in_vms_make_them(tmp_path, case) -> None:
    _vms(tmp_path / "today", {case: CASES[case]})
    made = machine(tmp_path / "machines", case, **CASES[case])
    assert _held_as_a_machine(made.home, made.system) == _held_as_a_machine(tmp_path / "today" / case)


def test_each_machine_is_given_an_address_of_rfc_5737_s_ranges_and_an_identity_of_its_own(root) -> None:
    made = [machine(root, f"m{index}") for index in range(3)]
    assert [one.address for one in made] == ["203.0.113.10", "203.0.113.11", "203.0.113.12"]
    assert len({one.identity for one in made}) == 3
    assert all(checks.HOST_KEY_ALIAS_RE.fullmatch(one.identity) for one in made)
    assert machine(root, "given", address="198.51.100.7").address == "198.51.100.7"
    for name, address in (("private", "10.0.0.5"), ("public", "8.8.8.8"), ("taken", "203.0.113.10"), ("odd", "x")):
        with pytest.raises(pytest.fail.Exception):
            machine(root, name, address=address)
        assert not (root / name).exists()


# ---------------------------------------------------------------- the fake reports a machine ----


@pytest.fixture
def fake(tmp_path: Path) -> Iterator[str]:
    yield from fake_fleet(tmp_path)


def _reported(fake: str, vmid: int) -> tuple[answers.VmConfig, str | None]:
    """vmid's configuration as the engine reads it, and the address its agent reports on the VM's own card."""
    config = answers.config(_pve(fake, "GET", f"nodes/pve/qemu/{vmid}/config")["data"], "pve", vmid, "net0")
    cards = _pve(fake, "GET", f"nodes/pve/qemu/{vmid}/agent/network-get-interfaces")["data"]["result"]
    return config, agent.address(cards, config.mac)


def test_planted_makes_the_fake_s_vm_report_the_machine_s_address_and_identity(fake, root) -> None:
    alpha = machine(root, "alpha")
    before, _ = _reported(fake, 9010)
    planted(fake, 9010, alpha)
    config, address = _reported(fake, 9010)
    assert (config.identity, address, config.mac) == (alpha.uuid, alpha.address, before.mac)
    assert proxmox.ssh_host_key_alias(dict(config.values)) == alpha.identity
    # Another VM keeps its own identity and the address the fake's agent gives it.
    other, theirs = _reported(fake, 9011)
    assert other.identity not in ("", alpha.uuid) and theirs == "192.0.2.61"


# ---------------------------------------------------------------- layer 1 on a machine ----


def test_a_play_of_one_task_over_the_local_connection_touches_only_its_machine_s_directory(root, tmp_path) -> None:
    alpha, beta = machine(root, "alpha"), machine(root, "beta")
    host = layer1_host(alpha)
    assert (host["ansible_connection"], host["ansible_become"], host["ansible_become_exe"]) == (
        "local",
        False,
        str(alpha.bin / "sudo"),
    )
    # Its interpreter is today's wrapper, run from the machine's home: that HOME, and the machine's bin/ first.
    asked = "import os, shutil; print(os.environ['HOME'], shutil.which('gh'))"
    shown = subprocess.run(
        [str(host["ansible_python_interpreter"]), "-c", asked],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert shown.stdout.split() == [str(alpha.home), str(alpha.bin / "gh")]
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(json.dumps({"all": {"hosts": {alpha.identity: host}}}))
    play = tmp_path / "play.yml"
    task = {"name": "made", "ansible.builtin.file": {"path": "~/made-by-a-play", "state": "touch"}}
    play.write_text(json.dumps([{"hosts": "all", "gather_facts": False, "tasks": [task]}]))
    before = _snapshot(root)
    ran = subprocess.run(
        [str(harness.ANSIBLE_PLAYBOOK), "-i", str(inventory), str(play)],
        cwd=tmp_path,
        env={**os.environ, "ANSIBLE_CONFIG": ansible_config(tmp_path)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        process_group=0,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert (alpha.home / "made-by-a-play").is_file() and not (beta.home / "made-by-a-play").exists()
    changed = _changed(before, _snapshot(root))
    assert "alpha/home/made-by-a-play" in changed and [path for path in changed if not path.startswith("alpha/")] == []
    # The module's own files went to the play's remote folder, in the machine's directory, and were taken away.
    assert "alpha/ansible-tmp" in changed and list((alpha.directory / "ansible-tmp").iterdir()) == []


# ---------------------------------------------------------------- one stand-in ssh ----

# The engine's test files that write an ssh stand-in of their own, or hand the runner one, each with why: what they
# test is the ssh layer, a child named ssh, or the guard, itself. Every other reaches a machine through aw_machine's.
# The scan reads a file's own code, not a script it holds as text for a child to run.
OWN_SSH: dict[str, str] = {
    "test_aw_ssh_runner.py": "the runner's own tests: a stand-in that records every word, variable and byte the runner"
    " gives it, and plays each way a child ends, is what they hold the command line and its bounds to",
    "test_aw_ssh_connections.py": "the shared connections' own tests: the masters, -O check's answers and the socket"
    " files they leave are what they hold",
    "test_aw_children.py": "a stand-in that says the marker it was handed, for route.ssh_shown's: what a child"
    " carries, not a machine",
    "test_aw_guards.py": "the guard's own tests of first_on_path: a program of the test's own ahead of the stand-ins",
    "test_aw_engine_init.py": "a recording ssh that keeps the whole environment root SSH's child was started with,"
    " which ssh_stand_in keeps the names of alone, so that the token's secret init takes out of its own is looked for"
    " under every name: what a child carries, not a machine",
}
# What writes a file: a method of the path written, or a call given the path.
WRITTEN_BY = {"write_text", "write_bytes", "symlink_to", "hardlink_to", "touch"}
WRITTEN_TO = {"_executable", "copy", "copy2", "copyfile", "symlink", "link"}
# What makes a target of the runner's, which an ssh of the test's own would be handed as ssh=.
MAKES_A_TARGET = {"machine", "proxmox_host", "Target", "target", "replace"}


def _callee(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _holds_ssh(node: ast.AST, words: set[str], dicts: set[str]) -> bool:
    """Whether node is a literal a loop over it binds the word ssh from: a tuple, list or set holding it (or a pair that
    starts with it), a dict with it as a key, or a name bound to such a dict."""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(
            _ssh_word(item, words) or (isinstance(item, ast.Tuple) and item.elts and _ssh_word(item.elts[0], words))
            for item in node.elts
        )
    if isinstance(node, ast.Dict):
        return any(key is not None and _ssh_word(key, words) for key in node.keys)
    return isinstance(node, ast.Name) and node.id in dicts


def _ssh_bound(tree: ast.AST) -> tuple[set[str], set[str]]:
    """The names bound to the word ssh - by an assignment, plain or annotated, or as a loop's over a literal that holds
    it, a dict's keys and items among them - and those bound to a path that ends in it."""
    words: set[str] = set()
    paths: set[str] = set()
    dicts: set[str] = set()
    for _ in range(3):
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.comprehension)):
                loop, target_of = node.iter, node.target
                if isinstance(loop, ast.Call) and isinstance(loop.func, ast.Attribute):
                    loop = loop.func.value if loop.func.attr in ("items", "keys") else loop
                if isinstance(target_of, ast.Tuple) and target_of.elts:
                    target_of = target_of.elts[0]
                if isinstance(target_of, ast.Name) and _holds_ssh(loop, words, dicts):
                    words.add(target_of.id)
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {target.id for target in targets if isinstance(target, ast.Name)}
                if _ssh_word(node.value, words):
                    words |= names
                elif _ssh_path(node.value, words, paths):
                    paths |= names
                elif isinstance(node.value, ast.Dict) and _holds_ssh(node.value, words, dicts):
                    dicts |= names
    return words, paths


def _ssh_word(node: ast.AST, words: set[str]) -> bool:
    return (isinstance(node, ast.Constant) and node.value == "ssh") or (isinstance(node, ast.Name) and node.id in words)


def _ends_in_ssh(node: ast.AST) -> bool:
    """Whether node is text spelled to end in /ssh: a string, or an f-string whose last part does."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.endswith("/ssh")
    if isinstance(node, ast.JoinedStr) and node.values:
        last = node.values[-1]
        return isinstance(last, ast.Constant) and str(last.value).endswith("/ssh")
    return False


def _ssh_path(node: ast.AST, words: set[str], paths: set[str]) -> bool:
    """Whether node is a path whose last part is ssh: joined by /, by os.path.join or joinpath, spelled whole, added to
    as text (str(folder) + "/ssh"), made text by str(), or a name bound to one."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _ssh_word(node.right, words)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _ends_in_ssh(node.right)
    if isinstance(node, ast.Call) and _callee(node) in ("join", "joinpath", "Path", "str", "fspath") and node.args:
        return _ssh_word(node.args[-1], words) or _ssh_path(node.args[-1], words, paths)
    if _ends_in_ssh(node):
        return True
    return isinstance(node, ast.Name) and node.id in paths


def _writing(mode: ast.AST | None) -> bool:
    return isinstance(mode, ast.Constant) and bool(set(str(mode.value)) & set("wax+"))


def _writes_ssh(source: str) -> list[int]:
    """The lines of source that write a program named ssh: a stand-in of the file's own."""
    tree = ast.parse(source)
    words, paths = _ssh_bound(tree)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node)
        given = [keyword.value for keyword in node.keywords if keyword.arg is not None]
        mode = next((keyword.value for keyword in node.keywords if keyword.arg == "mode"), None)
        if name in WRITTEN_BY and isinstance(node.func, ast.Attribute):
            if _ssh_path(node.func.value, words, paths):
                found.add(node.lineno)
        elif name in WRITTEN_TO and any(_ssh_path(arg, words, paths) for arg in [*node.args, *given]):
            found.add(node.lineno)
        elif name == "open" and isinstance(node.func, ast.Attribute) and _ssh_path(node.func.value, words, paths):
            # A path's own open: its mode first.
            if _writing(node.args[0] if node.args else mode):
                found.add(node.lineno)
        elif name == "open" and node.args and _ssh_path(node.args[0], words, paths):
            if _writing(node.args[1] if len(node.args) > 1 else mode):
                found.add(node.lineno)
    return sorted(found)


def _hands_ssh(source: str) -> list[int]:
    """The lines of source that hand the runner an ssh of the file's own: ssh= given where a target is made."""
    return sorted(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and _callee(node) in MAKES_A_TARGET
        and any(keyword.arg == "ssh" for keyword in node.keywords)
    )


def test_no_engine_test_writes_an_ssh_stand_in_of_its_own_or_hands_the_runner_one() -> None:
    files = [path for path in repo_files("tests/unit", untracked=True) if path.name.startswith("test_aw_")]
    assert UNIT / "test_aw_machine.py" in files and UNIT / "test_aw_ssh_runner.py" in files, len(files)
    sources = {path.name: path.read_text() for path in files if path.suffix == ".py"}
    found = {name: lines for name, source in sources.items() if (lines := _writes_ssh(source))}
    assert sorted(found) == sorted(OWN_SSH), (
        "reach a machine through tests/unit/aw_machine.py's ssh_stand_in, or name a file that tests the ssh layer"
        f" itself in OWN_SSH with why: {found}"
    )
    handed = {name: lines for name, source in sources.items() if name not in OWN_SSH and (lines := _hands_ssh(source))}
    assert handed == {}, f"a target of the runner's is given no ssh of a test's own but in OWN_SSH's files: {handed}"
    assert all(reason.strip() for reason in OWN_SSH.values())


@pytest.mark.parametrize(
    ("source", "writes"),
    [
        ('(folder / "ssh").write_text("#!/bin/sh\\n")', True),
        ('harness._executable(folder / "ssh", STAND_IN)', True),
        ('_executable(tmp_path / "bin" / "ssh", "x")', True),
        ('ssh = folder / "ssh"\nssh.write_text("x")', True),
        ('ssh: Path = folder / "ssh"\nssh.write_text("x")', True),
        ('for name in ("ps", "ssh"):\n    (folder / name).write_text("x")', True),
        ('for name, text in {"ssh": S}.items():\n    _executable(folder / name, text)', True),
        ('texts = {"ps": P, "ssh": S}\nfor name, text in texts.items():\n    _executable(folder / name, text)', True),
        ('for name in {"ssh": S}:\n    (folder / name).touch()', True),
        ('for name, text in [("ssh", S)]:\n    (folder / name).write_text(text)', True),
        ('os.symlink(real, os.path.join(folder, "ssh"))', True),
        ('os.symlink(real, dst=folder / "ssh")', True),
        ('shutil.copy(stand_in, folder / "ssh")', True),
        ('shutil.copyfile(stand_in, dst=folder / "ssh")', True),
        ('open(folder / "ssh", "w").write("x")', True),
        ('(folder / "ssh").open("w").write("x")', True),
        ('(folder / "ssh").open(mode="a")', True),
        ('Path(f"{folder}/ssh").write_text("x")', True),
        ('Path(str(folder) + "/ssh").write_text("x")', True),
        ('program = str(folder / "ssh")\nos.symlink(real, program)', True),
        ('(folder / "ssh").symlink_to(real)', True),
        ('(tmp_path / "ssh" / "known_hosts").write_text("x")', False),
        ('assert not (tmp_path / "ssh").exists()', False),
        ('shutil.which("ssh")', False),
        ('open(folder / "ssh").read()', False),
        ('(folder / "ssh").open().read()', False),
        ('(folder / "ssh.py").write_text(STAND_IN)', False),
        ('for name, text in {"ps": P}.items():\n    _executable(folder / name, text)', False),
        ("engine_guard.first_on_path(aw_machine.ssh_stand_in(root, [made]))", False),
    ],
)
def test_the_scan_sees_each_way_an_ssh_stand_in_is_written(source, writes) -> None:
    assert bool(_writes_ssh(source)) == writes


@pytest.mark.parametrize(
    ("source", "hands"),
    [
        ('runner.machine(identity, address, "dev", control, ssh=str(tmp_path / "fake-ssh"))', True),
        ('runner.proxmox_host(address, "root", control, ssh=program)', True),
        ("target(alpha, ssh=program)", True),
        ("dataclasses.replace(at, ssh=program)", True),
        ("runner.Target(identity=None, address=address, account='root', control_dir=control, ssh=program)", True),
        ("target(alpha, first_use=True)", False),
        ('runner.machine(identity, address, "dev", control)', False),
        ('route.hop_settings(ssh="ssh", user="root", host=host)', False),
    ],
)
def test_the_scan_sees_an_ssh_of_a_test_s_own_handed_to_the_runner(source, hands) -> None:
    assert bool(_hands_ssh(source)) == hands
