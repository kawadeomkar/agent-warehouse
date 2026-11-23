"""Stand-in machines for the new engine's tests: directories of a test's own that B3's runner reaches as if over SSH
(docs/architecture/engine.md §16.10).

- `machine(root, name, **state)` makes one under root: an identity made for the test (agent-warehouse-<a UUID>), an
  address from RFC 5737's documentation ranges, never one of a real network, and a directory that holds `home/`, its
  HOME, made for a state as today's stand-in VMs make theirs (tests/unit/test_status_vms.py's `_vms`, held equal by a
  test), `system/`, the files the probe reads, made by the same `_tree`, and `bin/`, the stand-ins a command finds
  first: today's systemctl, dpkg-query and getent, tests/molecule's fake gh, tailscale and claude, and a sudo of its
  own. That sudo runs the command as the same user and records each call's -n and -S in the machine's `sudo.log`;
  made with `sudo_password=`, it refuses -n for want of one and reads exactly one line on -S, kept as a digest only.
- `ssh_stand_in(root, machines)` gives the folder of an `ssh` for the first place on a run's PATH
  (`engine_guard.first_on_path`, or `first_on_path=` for a run of the kit's). It reads B3's words as ssh reads them;
  refuses with status 255 an address no machine has, and words whose first StrictHostKeyChecking or BatchMode is not
  yes; appends its words and the names of its environment's variables to that machine's `calls.log`; runs the command
  after the address with /bin/sh -c in the machine's home, with that HOME, its bin/ first on PATH, the test's own
  failing stand-ins next and the system's folders last, and nothing else of the test's environment; and passes its
  standard input through untouched. -O check and -O stop answer as a live master would; a forward (-L) is recorded
  and never opened. It never runs this computer's ssh, and refuses a command that names this computer's own sudo by
  its path, which would run as root on the runner.
- `target(machine)` is the machine as B3's runner reaches it, its key checked strictly.
- `planted(fake, vmid, machine)` makes the Proxmox fake's VM vmid that machine: its guest agent reports the machine's
  address on the VM's own network card, and its SMBIOS UUID is the identity's, so that a VM made against the fake is
  reached at the stand-in. An adopted machine is one made without it, reached at the address machine() gave it, under
  the account it was made with.
- `layer1_host(machine)` is the host variables of a play of one task over F3's test-only local connection, which
  touches only the machine's own directory. The real roles never run on a stand-in: they would run as root on the
  runner (F6's Molecule scenario converges them).

Nothing here reaches a network, this computer's ~/.ssh, ~/.claude, browser, clipboard or terminal: every file is under
the test's own directory, and a machine's command meets the test's own failing stand-ins ahead of the system's
programs, whose reach fails the test as engine_guard's do. No test of the new engine writes an ssh stand-in of its own
but the files tests/unit/test_aw_machine.py names, which test the ssh layer itself.
"""

from __future__ import annotations

import dataclasses
import hashlib
import ipaddress
import json
import os
import re
import sys
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path

import harness
import pytest
from aw_kit import stand_ins
from harness import FAKES, GETENT, STAND_INS
from test_status_vms import SYSTEMCTL, _tree
from test_status_work import _commit, _git
from test_tailscale import DPKG_QUERY

from claude_vm import proxmox
from claude_vm.fleet import paths
from claude_vm.ssh import runner

# RFC 5737's documentation ranges, the only addresses a stand-in machine is given: none is a real network's. The
# machines take 203.0.113.0/24's in order, apart from the fake's own guests, which report 192.0.2.0/24's.
DOCUMENTATION = (
    ipaddress.IPv4Network("192.0.2.0/24"),
    ipaddress.IPv4Network("198.51.100.0/24"),
    ipaddress.IPv4Network("203.0.113.0/24"),
)
FIRST = ipaddress.IPv4Address("203.0.113.10")
# A machine's name: a word a VM's name is, which becomes the name of its folder.
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
# The account a machine is reached under, unless it is made with another: the VM user's.
ACCOUNT = "dev"
# The control directory of a stand-in's target: short, as a socket's path must be, and never made, since the stand-in
# opens no socket.
CONTROL = Path("/aw-machines/cp")
# What a machine's command finds after its bin/ and the test's own failing stand-ins: the system's folders.
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
# The stand-ins of a machine's bin/, by name: each a program of the machines' shared folder (PROGRAMS), linked, as
# today's harness keeps one copy of each, since macOS scans each program it has not run before.
TOOLS = ("systemctl", "dpkg-query", "getent", "gh", "tailscale", "claude", "sudo")
PROGRAMS = ".programs"
SSH_FOLDER = ".ssh-stand-in"

# The stand-in sudo: the same user, every call a JSON line of the machine's sudo.log - its -n and -S, the lines it
# read and what it did, never a line's text. Where the machine asks a password (its machine.json holds the digest of
# one), -n is refused for want of it, and -S reads exactly one line, as far as its newline and no further, so that the
# rest of standard input reaches the command untouched; where it asks none, -S reads nothing, as sudo does.
SUDO = r"""
import hashlib, json, os, sys

machine, args = sys.argv[1], sys.argv[2:]
with open(os.path.join(machine, "machine.json")) as made:
    asks = json.load(made)["sudo"]
VALUED = set("pugCDhRrtTU")
FLAGS = set("ABbEHKknPSs")


def log(entry):
    with open(os.path.join(machine, "sudo.log"), "a") as out:
        out.write(json.dumps(entry) + "\n")


def said(text):
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


flags, valued = set(), {}
while args and args[0].startswith("-") and args[0] != "-":
    word = args.pop(0)
    if word == "--":
        break
    for at, letter in enumerate(word[1:], 1):
        if letter in VALUED:
            value = word[at + 1:] or (args.pop(0) if args else None)
            if value is None:
                said(f"sudo: option requires an argument -- '{letter}'")
                sys.exit(1)
            valued.setdefault(letter, []).append(value)
            break
        if letter not in FLAGS:
            said(f"sudo: invalid option -- '{letter}'")
            sys.exit(1)
        flags.add(letter)
entry = {"n": "n" in flags, "S": "S" in flags, "read": 0}
if not args:
    log({**entry, "outcome": "refused: no command"})
    said("usage: sudo [-nS] [-p prompt] [-u user] command")
    sys.exit(1)
if asks is not None:
    if "n" in flags:
        log({**entry, "outcome": "refused: a password is required"})
        said("sudo: a password is required")
        sys.exit(1)
    if "S" not in flags:
        log({**entry, "outcome": "refused: no terminal to ask at"})
        said("sudo: a terminal is required to read the password; either use the -S option to read from standard input"
             " or configure an askpass helper")
        sys.exit(1)
    prompt = valued.get("p", ["[sudo] password for %p: "])[0].replace("%p", os.environ.get("USER", "dev"))
    if prompt:
        sys.stderr.write(prompt)
        sys.stderr.flush()
    line = bytearray()
    while (byte := os.read(0, 1)) not in (b"", b"\n"):
        line += byte
    entry["read"] = 1
    if hashlib.sha256(bytes(line)).hexdigest() != asks:
        log({**entry, "outcome": "refused: a wrong password"})
        said("Sorry, try again.\nsudo: 1 incorrect password attempt")
        sys.exit(1)
log({**entry, "outcome": "ran"})
os.execvp(args[0], args)
"""

# The stand-in ssh: its route table, machines.json, beside it. Every call to a machine is a JSON line of that
# machine's calls.log - its words, the names of its environment's variables and what it did - and a call to an address
# no machine has, or one that asks ssh -G, a line of unrouted.log beside it.
SSH = r'''
import json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "machines.json")) as table:
    MACHINES = json.load(table)
# ssh's own options that take a value, and those that take none, as ssh(1) gives them.
VALUED = set("BbcDEeFIiJLlmOopQRSWw")
FLAGS = set("46AaCfGgKkMNnqsTtVvXxYy")
argv = sys.argv[1:]


def log(path, outcome):
    with open(path, "a") as out:
        out.write(json.dumps({"argv": argv, "env": sorted(os.environ), "outcome": outcome}) + "\n")


def said(text, status):
    sys.stderr.write(text + "\n")
    sys.stderr.flush()
    sys.exit(status)


def words():
    """argv read as ssh reads it: its flags, each other option's values in their order, -o's as (name in lower case,
    value), the destination, and the command's words: everything after the destination once -- was given, else from
    the first word after it that is no option. A destination's user@ counts as a -l where it comes."""
    flags, valued, options, destination, i, ended = set(), {}, [], None, 0, False
    while i < len(argv):
        word = argv[i]
        if not ended and word == "--":
            ended, i = True, i + 1
            continue
        if not ended and word.startswith("-") and len(word) > 1:
            for at, letter in enumerate(word[1:], 1):
                if letter in VALUED:
                    value = word[at + 1:]
                    if not value:
                        i += 1
                        if i >= len(argv):
                            said(f"ssh: option requires an argument -- {letter}", 255)
                        value = argv[i]
                    if letter == "o":
                        name, _, rest = value.replace("=", " ", 1).partition(" ")
                        options.append((name.strip().lower(), rest.strip()))
                    else:
                        valued.setdefault(letter, []).append(value)
                    break
                if letter not in FLAGS:
                    said(f"ssh: unknown option -- {letter}", 255)
                flags.add(letter)
            i += 1
            continue
        if destination is not None:
            return flags, valued, options, destination, argv[i:]
        destination, i = word, i + 1
        if "@" in destination:
            valued.setdefault("l", []).append(destination.rpartition("@")[0])
        if ended:
            return flags, valued, options, destination, argv[i:]
    return flags, valued, options, destination, []


def first(options, name):
    """The value ssh keeps for an option: the first given."""
    return next((value for key, value in options if key == name), None)


flags, valued, options, destination, command = words()
unrouted = os.path.join(HERE, "unrouted.log")
if "G" in flags:
    log(unrouted, "refused: -G")
    said("stand-in ssh: it reads no configuration, so it answers no -G: ask this computer's own ssh by its path, with"
         " -F /dev/null", 255)
if destination is None:
    log(unrouted, "refused: no destination")
    said("usage: ssh [options] destination [command]", 255)
# ssh keeps the first account given, by -l or as the destination's user@, and this computer's own where none is.
address = destination.rpartition("@")[2]
account = (valued.get("l") or [os.environ.get("USER", "")])[0]
port = (valued.get("p") or ["22"])[0]
machine = MACHINES.get(address)
if machine is None:
    log(unrouted, "refused: no machine has the address")
    said(f"ssh: connect to host {address} port {port}: No route to host", 255)
calls = os.path.join(machine["directory"], "calls.log")
held = [(first(options, name) or "").lower() == "yes" for name in ("stricthostkeychecking", "batchmode")]
if not all(held):
    log(calls, "refused: not StrictHostKeyChecking=yes and BatchMode=yes")
    said("stand-in ssh: refused: a connection to a machine checks its host key strictly and never asks"
         " (StrictHostKeyChecking=yes and BatchMode=yes, the first value of each), and this one does not", 255)
if account != machine["account"]:
    log(calls, "refused: another account")
    said(f"{account}@{address}: Permission denied (publickey).", 255)
control = (valued.get("O") or [None])[0]
if control is not None:
    if control == "check":
        log(calls, "-O check")
        said(f"Master running (pid={os.getpid()})\r", 0)
    if control == "stop":
        log(calls, "-O stop")
        said("Stop listening request sent.\r", 0)
    log(calls, f"refused: -O {control}")
    said(f"stand-in ssh: -O {control} is not answered: a stand-in master answers check and stop alone", 255)
if valued.get("L") and "f" in flags:
    # Gone to the background with its forwards, which are never opened.
    log(calls, "forward, never opened")
    sys.exit(0)
if "N" in flags and not valued.get("L"):
    # A master's open: the master goes to the background, and none is ever opened.
    log(calls, "a master, never opened")
    sys.exit(0)
if "N" in flags:
    log(calls, "forward, never opened")
    while True:
        time.sleep(3600)
if not command:
    log(calls, "refused: no command")
    said("stand-in ssh: no command given: a stand-in machine runs a command, never a shell", 255)
line = " ".join(command)
# A sudo named by its path, however the command quotes it: never the machine's own, which its PATH finds.
own = os.path.join(machine["directory"], "bin", "sudo")
named = re.findall(r"[^\s'\"`;|&()<>]+", line)
if any(word.startswith("/") and word.endswith("/sudo") and word != own for word in named):
    log(calls, "refused: this computer's own sudo")
    said("stand-in ssh: refused: the command names a sudo by its path, which would be this computer's own and run as"
         " root on it: a stand-in machine's sudo is the one its PATH finds", 255)
log(calls, "forward, never opened" if valued.get("L") else "ran")
env = {"HOME": machine["home"], "PATH": machine["path"], "STAND_IN": machine["home"]}
stdin = subprocess.DEVNULL if "n" in flags else None
status = subprocess.Popen(["/bin/sh", "-c", line], cwd=machine["home"], env=env, stdin=stdin).wait()
# A command ended by a signal has no status ssh can pass on: 255, as ssh exits.
sys.exit(255 if status < 0 else status)
'''


@dataclasses.dataclass(frozen=True)
class Machine:
    """A stand-in machine: its name, its identity, the address it is reached at, the account it takes, its directory,
    and whether its sudo asks a password."""

    name: str
    identity: str
    address: str
    account: str
    directory: Path
    asks_password: bool

    @property
    def uuid(self) -> str:
        """Its SMBIOS UUID, as Proxmox gives the identity's."""
        return self.identity.removeprefix("agent-warehouse-")

    @property
    def home(self) -> Path:
        return self.directory / "home"

    @property
    def system(self) -> Path:
        """The root of the files the probe reads (status_system_root)."""
        return self.directory / "system"

    @property
    def bin(self) -> Path:
        return self.directory / "bin"

    def calls(self) -> list[dict]:
        """Every call the stand-in ssh took for this machine, in order: its words, its variables' names, its outcome."""
        return harness._json_lines(self.directory / "calls.log")

    def sudo_calls(self) -> list[dict]:
        """Every call of its sudo, in order: its -n and -S, the lines it read, and its outcome."""
        return harness._json_lines(self.directory / "sudo.log")


def _programs(root: Path) -> Path:
    """The machines' shared stand-ins under root, made on first use: one copy of each, which each machine's bin/
    links."""
    folder = root / PROGRAMS
    if folder.is_dir():
        return folder
    folder.mkdir(parents=True)
    texts = {
        "systemctl": SYSTEMCTL,
        "dpkg-query": DPKG_QUERY,
        "getent": GETENT,
        "gh": (FAKES / "fake_gh.sh").read_text(),
        "tailscale": (FAKES / "fake_tailscale.sh").read_text(),
        "claude": (FAKES / "fake_claude.sh").read_text(),
        "python": STAND_INS["python"],
        # The machine's directory, from the path the link in its bin/ was run by, which the kernel hands the shell.
        "sudo": f'#!/bin/sh\nexec "{sys.executable}" -I "{folder / "sudo.py"}" "$(dirname "$(dirname "$0")")" "$@"\n',
    }
    (folder / "sudo.py").write_text(SUDO)
    for name, text in texts.items():
        harness._executable(folder / name, text)
    return folder


def _taken(root: Path) -> set[str]:
    return {json.loads(made.read_text())["address"] for made in root.glob("*/machine.json")}


def _address(root: Path, given: str | None) -> str:
    """given, held to RFC 5737's ranges and to no other machine's of root; or the next of 203.0.113.0/24's free."""
    taken = _taken(root)
    if given is not None:
        try:
            address = ipaddress.IPv4Address(given)
        except ValueError:
            pytest.fail(f"a stand-in machine's address is one IPv4 address: {given!r}")
        if not any(address in network for network in DOCUMENTATION):
            pytest.fail(f"a stand-in machine's address is one of RFC 5737's documentation ranges, never {given}")
        if str(address) in taken:
            pytest.fail(f"another stand-in machine under {root} has {given}")
        return str(address)
    for offset in range(245):
        address = str(FIRST + offset)
        if address not in taken:
            return address
    pytest.fail(f"every address of 203.0.113.0/24 a stand-in takes is taken under {root}")


def _home(home: Path, *, claude, login, units, gh, ts, marker, projects) -> None:
    """home made for a state as tests/unit/test_status_vms.py's _vms makes a stand-in VM's: Claude Code's link and
    login, Remote Control's units, gh's files, Tailscale installed by the role and its stand-in's files, and a checkout
    of the VM user's with a commit on no remote."""
    if claude:
        versions = home / ".local" / "share" / "claude" / "versions"
        versions.mkdir(parents=True)
        (versions / claude).write_text("")
        (home / ".local" / "bin").mkdir(parents=True)
        (home / ".local" / "bin" / "claude").symlink_to(versions / claude)
    (home / ".claude").mkdir()
    if login:
        (home / ".claude" / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "never-read"}}')
    (home / "units").mkdir()
    for unit, text in units.items():
        (home / "units" / f"claude-remote-control.{unit}").write_text(text)
    (home / ".config" / "gh").mkdir(parents=True)
    for file_name, content in gh.items():
        (home / ".config" / "gh" / file_name).write_text(content)
    (home / "markers").mkdir()
    (home / "dpkg-status").write_text("installed")
    (home / "markers" / "tailscale-installed").write_text("installed by the role\n")
    if marker:
        (home / "markers" / "tailscale-joined").write_text(f"nodekey:{marker}\n")
    (home / ".tailscale-fake").mkdir()
    for file_name, content in ts.items():
        (home / ".tailscale-fake" / file_name).write_text(content)
    if projects:
        checkout = home / "projects" / "proj"
        checkout.mkdir(parents=True)
        _git(checkout, "init", "-q", "-b", "main")
        _commit(checkout, "README")


def machine(
    root: Path,
    name: str,
    *,
    claude: str | None = None,
    login: bool = False,
    units: Mapping[str, str] | None = None,
    gh: Mapping[str, str] | None = None,
    ts: Mapping[str, str] | None = None,
    marker: str | None = None,
    tree: Mapping[str, object] | None = None,
    projects: bool = False,
    account: str = ACCOUNT,
    address: str | None = None,
    sudo_password: str | None = None,
) -> Machine:
    """A stand-in machine named name under root, a folder of the test's own: its state as one of
    tests/unit/test_status_vms.py's CASES gives one - claude (the version its link names, or none), login, units, gh,
    ts, marker, tree (_tree's), projects - reached under account at address (the next free one of RFC 5737's), its sudo
    asking sudo_password where one is given. Its identity is made for the test."""
    if not NAME.fullmatch(name):
        pytest.fail(f"a stand-in machine is named as a VM is, a word of a-z, 0-9 and -: {name!r}")
    if sudo_password is not None and (not isinstance(sudo_password, str) or "\n" in sudo_password):
        pytest.fail("a stand-in's sudo password is one line of text")
    root.mkdir(parents=True, exist_ok=True)
    reached_at = _address(root, address)
    programs = _programs(root)
    directory = root / name
    directory.mkdir()
    made = Machine(
        name=name,
        identity=f"agent-warehouse-{uuid.uuid4()}",
        address=reached_at,
        account=account,
        directory=directory,
        asks_password=sudo_password is not None,
    )
    made.bin.mkdir()
    for tool in TOOLS:
        (made.bin / tool).symlink_to(programs / tool)
    made.home.mkdir()
    _home(
        made.home,
        claude=claude,
        login=login,
        units=dict(units or {}),
        gh=dict(gh or {}),
        ts=dict(ts or {}),
        marker=marker,
        projects=projects,
    )
    # The interpreter of a play over the local connection (layer1_host), and of fake_claude.sh's listener: today's
    # wrapper, whose HOME and STAND_IN are the folder of the link, the home, and whose PATH starts with the machine's
    # bin/, the folder beside it.
    (made.home / "python").symlink_to(programs / "python")
    _tree(made.system, **dict(tree or {}))
    digest = None if sudo_password is None else hashlib.sha256(sudo_password.encode()).hexdigest()
    record = {"name": name, "identity": made.identity, "address": made.address, "account": account, "sudo": digest}
    (directory / "machine.json").write_text(json.dumps(record))
    return made


def _test_stand_ins() -> Path:
    """The folder of the failing stand-ins engine_guard checks once the test ends: the test's own home's."""
    home = os.environ.get(paths.VARIABLE)
    if not home:
        pytest.fail(f"a stand-in machine is reached in a test, whose home conftest.py's aw_home sets: {paths.VARIABLE}")
    return stand_ins(Path(home))


def machine_path(made: Machine) -> str:
    """The PATH a machine's command runs with: its bin/, the test's own failing stand-ins - for the desktop's openers
    and clipboards, this computer's ssh, ansible-playbook and login keychain - and the system's folders."""
    standing = _test_stand_ins()
    return os.pathsep.join([str(made.bin), str(standing / ".refused"), str(standing / ".desktop"), *SYSTEM_PATH])


def ssh_stand_in(root: Path, machines: Iterable[Machine]) -> Path:
    """The folder of the stand-in ssh for machines, under root, for the first place on a run's PATH: made again, with
    its table, at each call."""
    folder = root / SSH_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    table: dict[str, dict] = {}
    for made in machines:
        if made.address in table:
            pytest.fail(f"two stand-in machines have {made.address}")
        table[made.address] = {
            "name": made.name,
            "directory": str(made.directory),
            "home": str(made.home),
            "account": made.account,
            "path": machine_path(made),
        }
    (folder / "machines.json").write_text(json.dumps(table))
    (folder / "ssh.py").write_text(SSH)
    harness._executable(folder / "ssh", f'#!/bin/sh\nexec "{sys.executable}" -I "{folder / "ssh.py"}" "$@"\n')
    return folder


def unrouted(folder: Path) -> list[dict]:
    """The calls the stand-in ssh in folder took for no machine: an address none has, or ssh -G."""
    return harness._json_lines(folder / "unrouted.log")


def target(made: Machine, **over) -> runner.Target:
    """made as B3's runner reaches it: its identity, address and account, its key checked strictly against a
    known-hosts file beside the machines (the stand-in checks no key), and a control directory that is never made.
    over goes to runner.machine (first_use, given, ...)."""
    given = {"known_hosts": made.directory.parent / "known_hosts", **over}
    return runner.machine(made.identity, made.address, made.account, CONTROL, **given)


def interfaces(mac: str, address: str) -> list[dict]:
    """What a VM's guest agent reports for a machine at address on the card whose MAC is mac: loopback, and that
    card."""
    return [
        {
            "name": "lo",
            "hardware-address": "00:00:00:00:00:00",
            "ip-addresses": [{"ip-address": "127.0.0.1", "ip-address-type": "ipv4", "prefix": 8}],
        },
        {
            "name": "eth0",
            "hardware-address": mac.lower(),
            "ip-addresses": [{"ip-address": address, "ip-address-type": "ipv4", "prefix": 24}],
        },
    ]


def planted(fake: str, vmid: int, made: Machine, node: str = "pve") -> None:
    """The VM vmid on node of the Proxmox fake at fake (fake_fleet's URL) made the stand-in machine made: its guest
    agent reports made's address on the VM's own network card (its configuration's net0), and its SMBIOS UUID is
    made's identity's, the rest of its smbios1 kept. Written as a fixture writes, with the fake's own token."""
    config = harness._pve(fake, "GET", f"nodes/{node}/qemu/{vmid}/config")["data"]
    mac = proxmox.net_mac(str(config.get("net0", "")))
    harness._pve(fake, "PUT", "fake/agent-answer", vmid=vmid, result=json.dumps(interfaces(mac, made.address)))
    kept = [part for part in str(config.get("smbios1", "")).split(",") if part and not part.startswith("uuid=")]
    harness._pve(fake, "PUT", f"nodes/{node}/qemu/{vmid}/config", smbios1=",".join([f"uuid={made.uuid}", *kept]))


def layer1_host(made: Machine) -> dict[str, object]:
    """The host variables of a play over F3's test-only local connection on made, for a play of one task that touches
    only made's directory: its modules run by today's interpreter wrapper from made's home, so their HOME is made's home
    and made's bin/ is first on their PATH; the module's own files in a folder of made's directory, the play's remote
    one; become off, and made's own sudo where a play turns it on, never this computer's. A task's ~ is made's home
    where the module expands it (a path option); an action plugin that expands it here (copy's, template's) expands it
    to the test's own home, as the local connection's shell is the run's."""
    return {
        "ansible_connection": "local",
        "ansible_host": made.address,
        "ansible_python_interpreter": str(made.home / "python"),
        "ansible_remote_tmp": str(made.directory / "ansible-tmp"),
        "ansible_become": False,
        "ansible_become_exe": str(made.bin / "sudo"),
    }
