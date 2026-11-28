"""Stand-in machines for the new engine's tests: directories of a test's own that B3's runner reaches as if over SSH
(docs/architecture/engine.md §16.10).

- `machine(root, name, **state)` makes one under root: an identity made for the test (agent-warehouse-<a UUID>), a
  made-up ed25519 host key of its own, an address from RFC 5737's documentation ranges, never one of a real network,
  and a directory that holds `home/`, its HOME, made for a state as today's stand-in VMs make theirs
  (tests/unit/test_status_vms.py's `_vms`, held equal by a test), `system/`, the files the probe reads, made by the
  same `_tree`, `root/`, the home a command run under sudo is given, and `bin/`, the stand-ins a command finds first:
  today's systemctl, dpkg-query and getent, tests/molecule's fake gh, tailscale and claude, a python3 that is the
  test's own Python, and a sudo of its own. That sudo runs the command as the same user, with root/ as its HOME and its
  secure_path as its PATH, and records each call's -n and -S in the machine's `sudo.log`; made with `sudo_password=`,
  it refuses -n for want of one and on -S reads a line an attempt, up to sudo's three, each kept as a digest only.
- `ssh_stand_in(root, machines)` gives the folder of an `ssh` for the first place on a run's PATH
  (`engine_guard.first_on_path`, or `first_on_path=` for a run of the kit's). It reads B3's words as ssh reads them;
  refuses with status 255 an address no machine has, and words whose first StrictHostKeyChecking is neither yes nor
  accept-new or whose first BatchMode is not yes; checks the key of the machine at the address, under the first
  HostKeyAlias (or the address), in the files of the first UserKnownHostsFile, as ssh checks it: one filed passes, none
  filed is refused under yes and filed under accept-new, another filed is refused, each in ssh's words; appends its
  words and the names of its environment's variables to that machine's `calls.log`; runs the command after the address
  with /bin/sh -c in the machine's home, with no terminal, that HOME, its bin/ first on PATH, the test's own failing
  stand-ins next and the system's folders last, and nothing else of the test's environment, the home's `.bashrc` read
  first where it has one, as bash run by sshd reads it; and passes its standard input through untouched. -O check and
  -O stop answer as a live master would; a forward (-L) is recorded and never opened. A ProxyCommand other than none,
  the first given, is run as ssh runs one before the key is checked, its %-tokens expanded, and the connection goes on
  only where it answers as an SSH server first does. ssh -G is answered by this computer's own ssh, by its path, given
  the test's own configuration (-F with HOME's .ssh/config, or /dev/null, where the line gives no -F), which dials
  nothing and reads nothing of the developer's - after the seconds a file slow-G beside it holds, or ended by a signal
  where a file killed-G is there, as a person's Match exec may make it; it never runs this computer's ssh otherwise,
  never reads or writes this computer's own ~/.ssh, and refuses a command that names a sudo by its path, which on a lane
  would be this computer's own and run as root on the runner.
- `hop(root)` makes a stand-in Proxmox host the hop to the machines (engine/routes.py), in the modes `hop_modes()` sets:
  its -O check and -O stop are answered from a file at the socket's path (live, or stale for a socket nothing listens
  at), its master's open writes that file or is refused as a login, and a forward through it (-W) meets the host
  refusing to forward, ssh failing to use it, a machine that says nothing, a login that never ends, or the machine's
  sshd's first words.
- `filed(machine, path)` files the machine's key under its identity in the known-hosts file at path, as B5 files one;
  `target(machine)` is the machine as B3's runner reaches it, its key checked strictly in the file `filed` writes by
  default, marked as a route marks one (runner.Target.routed_as, with what it was decided from): straight at it, or
  through the hop it is given.
- `planted(fake, vmid, machine)` makes the Proxmox fake's VM vmid that machine: its guest agent reports the machine's
  address on the VM's own network card, and its SMBIOS UUID is the identity's, so that a VM made against the fake is
  reached at the stand-in. An adopted machine is one made without it, reached at the address machine() gave it, under
  the account it was made with.
- `layer1_host(machine)` is the host variables of a play of one task over F3's test-only local connection, which
  touches only the machine's own directory. The real roles never run on a stand-in: they would run as root on the
  runner (F6's Molecule scenario converges them).

What this module's own code writes is under the test's own directory, and it reaches no network, this computer's
~/.ssh, ~/.claude, browser, clipboard or terminal. A machine is a directory, not a confinement: a command run on one
can still write the computer's /tmp, reach a file by its absolute path, read its ancestors' environment through /proc
and run the system's programs past its bin/ (on macOS, BSD tar and no timeout, sha256sum or flock), and a step's
absolute path written as root fails or lands on the computer itself. So each card points the absolute paths and URLs
of its steps at the machine's directory (as `_vms` points today's role at markers/) or at a fake, and a command that
needs GNU's tools brings a stand-in for each into the machine's bin/, or marks its macOS case with why. No test of the
new engine writes an ssh stand-in of its own, or hands the runner one, but the files tests/unit/test_aw_machine.py
names: the SSH layer's own tests, the guard's, and one that tests a child named ssh.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import ipaddress
import json
import os
import re
import shutil
import struct
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
# The type of every machine's made-up host key: the one key a machine shows, as a VM's sshd shows its ed25519 one first.
KEY_TYPE = "ssh-ed25519"
# The control directory of a stand-in's target: short, as a socket's path must be, and never made, since the stand-in
# opens no socket.
CONTROL = Path("/aw-machines/cp")
# What a machine's command finds after its bin/ and the test's own failing stand-ins: the system's folders.
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
# The stand-ins of a machine's bin/, by name: each a program of the machines' shared folder (PROGRAMS), linked, as
# today's harness keeps one copy of each, since macOS scans each program it has not run before.
TOOLS = ("systemctl", "dpkg-query", "getent", "gh", "tailscale", "claude", "sudo", "python3")
PROGRAMS = ".programs"
SSH_FOLDER = ".ssh-stand-in"

# The stand-in sudo: the same user, every call a JSON line of the machine's sudo.log - its -n and -S, the lines it
# read and what it did, never a line's text. Where the machine asks a password (its machine.json holds the digest of
# one), -n is refused for want of it, and -S reads a line an attempt, each as far as its newline or carriage return
# and no further, as sudo's tgetpass reads one: a right one runs the command, with the rest of standard input
# untouched; a wrong one is followed by "Sorry, try again." and the next line, up to sudo's three attempts
# (passwd_tries), and the third ends with sudo's words, as experiment M-2 saw sudo 1.9.9 to 1.9.15 do. A standard
# input that ends first is "no password was provided" (read from sudo's source, not run). Where it asks none, -S reads
# nothing, as sudo does. The command runs as sudo's env_reset and secure_path leave it: HOME the target's (root/, or
# the account's home for -u <account>), PATH the machine's (its bin/, the test's failing stand-ins, the system's
# folders) whatever the caller's was, USER and LOGNAME the target, SUDO_USER the account, and STAND_IN, the stand-ins'
# own, kept; -E keeps the caller's variables but PATH, and --preserve-env=<names> those named. -l lists, -v validates,
# -k and -K with no command end at once, and the long options are sudo's; -i and -s, a shell, are refused.
SUDO = r"""
import hashlib, json, os, shutil, sys

machine, args = sys.argv[1], sys.argv[2:]
with open(os.path.join(machine, "machine.json")) as made:
    record = json.load(made)
asks, account = record["sudo"], record["account"]
# sudo's options that take a value, and those that take none, as sudo(8) gives them, and its long options by the
# short one each stands for.
VALUED = set("CDghpRrTtUu")
FLAGS = set("ABbEHiKklnPSsv")
LONG = {
    "askpass": "A", "bell": "B", "background": "b", "close-from": "C", "chdir": "D", "preserve-env": "E",
    "group": "g", "set-home": "H", "host": "h", "login": "i", "remove-timestamp": "K", "reset-timestamp": "k",
    "list": "l", "non-interactive": "n", "preserve-groups": "P", "prompt": "p", "chroot": "R", "role": "r",
    "stdin": "S", "shell": "s", "type": "t", "command-timeout": "T", "other-user": "U", "user": "u", "validate": "v",
}
TRIES = 3
USAGE = "usage: sudo [-nS] [-p prompt] [-u user] command"


def log(entry):
    with open(os.path.join(machine, "sudo.log"), "a") as out:
        out.write(json.dumps(entry) + "\n")


def said(text):
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def ended(entry, outcome, *words):
    log({**entry, "outcome": outcome})
    for text in words:
        said(text)
    sys.exit(1)


flags, valued, preserved = set(), {}, None
while args and args[0].startswith("-") and args[0] != "-":
    word = args.pop(0)
    if word == "--":
        break
    if word.startswith("--"):
        name, equals, value = word[2:].partition("=")
        letter = LONG.get(name)
        if letter is None:
            said(f"sudo: unrecognized option '{word}'")
            said(USAGE)
            sys.exit(1)
        if letter == "E":
            flags.add("E")
            preserved = value.split(",") if equals else None
        elif letter in VALUED:
            if not equals:
                if not args:
                    said(f"sudo: option '--{name}' requires an argument")
                    sys.exit(1)
                value = args.pop(0)
            valued.setdefault(letter, []).append(value)
        elif equals:
            said(f"sudo: option '--{name}' doesn't allow an argument")
            sys.exit(1)
        else:
            flags.add(letter)
        continue
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
if flags & {"i", "s"}:
    ended(entry, "refused: a shell", "stand-in sudo: refused: it runs a command as given, never a shell or a login"
          " shell (-i, -s)")
if flags & {"K", "k"} and not args:
    log({**entry, "outcome": "timestamp"})
    sys.exit(0)
if not args and not flags & {"l", "v"}:
    ended(entry, "refused: no command", USAGE)
user = (valued.get("u") or ["root"])[0]
if user not in ("root", account):
    ended(entry, "refused: another user", f"sudo: unknown user {user}")


def line():
    # One line of standard input, as tgetpass reads one: to its newline or carriage return; None where it ends first.
    got, any_byte = bytearray(), False
    while (byte := os.read(0, 1)) not in (b"", b"\n", b"\r"):
        got += byte
        any_byte = True
    if byte == b"" and not any_byte:
        return None
    return bytes(got)


if asks is not None:
    if "n" in flags:
        ended(entry, "refused: a password is required", "sudo: a password is required")
    if "S" not in flags:
        ended(entry, "refused: no terminal to ask at", "sudo: a terminal is required to read the password; either use"
              " the -S option to read from standard input or configure an askpass helper",
              "sudo: a password is required")
    prompt = valued.get("p", ["[sudo] password for %p: "])[0].replace("%p", account)
    for attempt in range(1, TRIES + 1):
        if attempt > 1:
            said("Sorry, try again.")
        if prompt:
            sys.stderr.write(prompt)
            sys.stderr.flush()
        given = line()
        if given is None:
            failed = attempt - 1
            counted = [f"sudo: {failed} incorrect password attempt{'s' if failed != 1 else ''}"] if failed else []
            ended(entry, "refused: no password", "sudo: no password was provided", *counted)
        entry["read"] = attempt
        if hashlib.sha256(given).hexdigest() == asks:
            break
    else:
        ended(entry, "refused: a wrong password", f"sudo: {TRIES} incorrect password attempts")
home = os.path.join(machine, "home" if user == account else "root")
if "E" in flags and preserved is None:
    env = dict(os.environ)
    if "H" in flags:
        env["HOME"] = home
else:
    env = {name: os.environ[name] for name in (preserved or []) if name in os.environ}
    env["HOME"] = home
env.update({"PATH": record["path"], "USER": user, "LOGNAME": user, "SUDO_USER": account})
if "STAND_IN" in os.environ:
    env["STAND_IN"] = os.environ["STAND_IN"]
if "v" in flags and not args:
    log({**entry, "outcome": "validated"})
    sys.exit(0)
if "l" in flags:
    if not args:
        log({**entry, "outcome": "listed"})
        print(f"User {account} may run the following commands on {record['name']}:")
        print("    (ALL : ALL) " + ("ALL" if asks is not None else "NOPASSWD: ALL"))
        sys.exit(0)
    found = shutil.which(args[0], path=env["PATH"])
    if found is None:
        ended(entry, "refused: not found", f"sudo: {args[0]}: command not found")
    log({**entry, "outcome": "listed"})
    print(" ".join([found, *args[1:]]))
    sys.exit(0)
if shutil.which(args[0], path=env["PATH"]) is None:
    ended(entry, "refused: not found", f"sudo: {args[0]}: command not found")
log({**entry, "outcome": "ran"})
os.execvpe(args[0], args, env)
"""

# The stand-in ssh: its route table, machines.json, beside it. Every call to a machine is a JSON line of that
# machine's calls.log - its words, the names of its environment's variables and what it did - and a call to an address
# no machine has, or one that asks ssh -G, a line of unrouted.log beside it.
SSH = r'''
import base64, binascii, fcntl, hashlib, hmac, json, os, pwd, re, shlex, subprocess, sys, termios, time

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "machines.json")) as table:
    MACHINES = json.load(table)
# ssh's own options that take a value, and those that take none, as ssh(1) gives them.
VALUED = set("BbcDEeFIiJLlmOopQRSWw")
FLAGS = set("46AaCfGgKkMNnqsTtVvXxYy")
# The policies of StrictHostKeyChecking a connection to a machine may have: strict, or a first use's.
POLICIES = ("yes", "accept-new")
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
    the first word after it that is no option. A destination's user@, and a -o User= (as Ansible's ssh connection names
    the account), count as a -l where they come, a value's double quotes taken off as ssh takes them off."""
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
                        if name.strip().lower() == "user":
                            valued.setdefault("l", []).append(rest.strip().strip('"'))
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


def named_by(field, name):
    """Whether a known-hosts line's first field names name, as ssh reads one: a name ssh hashed (|1|salt|hash, an
    HMAC-SHA1 of the name), or names and patterns split by commas, * and ? wildcards, in any case, ! excluding."""
    if field.startswith("|1|"):
        salt_text, _, digest_text = field[3:].partition("|")
        try:
            salt = base64.b64decode(salt_text, validate=True)
            digest = base64.b64decode(digest_text, validate=True)
        except (binascii.Error, ValueError):
            return False
        return hmac.compare_digest(hmac.new(salt, name.encode(), "sha1").digest(), digest)
    found = False
    for pattern in field.split(","):
        excluded = pattern.startswith("!")
        shape = "".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in pattern[excluded:].lower())
        if re.fullmatch(shape, name, re.S):
            if excluded:
                return False
            found = True
    return found


def looked_up(files, name, kind, body):
    """What ssh finds for the machine's key under name in files: ("known", None) where a line holds that very key;
    ("changed", (file, line)) where a line of the key's type holds another, the first such named; else ("new", None).
    A comment, a marker's line and a key of another type are passed over, as ssh passes them."""
    offending = None
    for path in files:
        try:
            with open(path, "rb") as kept:
                text = kept.read().decode("utf-8", "replace")
        except OSError:
            continue
        for number, each in enumerate(text.splitlines(), 1):
            fields = each.split()
            if len(fields) < 3 or fields[0].startswith(("#", "@")) or fields[1] != kind:
                continue
            if not named_by(fields[0], name):
                continue
            if fields[2] == body:
                return "known", None
            offending = offending or (path, number)
    return ("changed", offending) if offending else ("new", None)


def take(path, name, kind, body):
    """The key added at the end of path under name, as ssh adds one at a first use: the line ended where the file's
    last is not, ~/.ssh made 0700 where the file is in it and it is not there. Where it cannot be written, ssh says so
    only at INFO, which LogLevel=ERROR keeps back, and goes on."""
    folder = os.path.dirname(path)
    if folder == os.path.join(os.path.expanduser("~"), ".ssh") and not os.path.isdir(folder):
        os.mkdir(folder, 0o700)
    try:
        with open(path, "a+b") as kept:
            kept.seek(0, os.SEEK_END)
            if kept.tell():
                kept.seek(-1, os.SEEK_END)
                if kept.read(1) != b"\n":
                    kept.write(b"\n")
            kept.write(f"{name} {kind} {body}\n".encode())
    except OSError:
        pass


def no_terminal():
    """The command's controlling terminal dropped before it starts, as sshd starts one with no terminal: /dev/tty then
    fails at once. Its process group is kept, so that the runner's kill of the ssh's group ends it too. A Python whose
    termios has no TIOCNOTTY leaves it, which the test of the terminal then says."""
    request = getattr(termios, "TIOCNOTTY", None)
    try:
        handle = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return
    try:
        if request is not None:
            fcntl.ioctl(handle, request)
    except OSError:
        pass
    finally:
        os.close(handle)


flags, valued, options, destination, command = words()
unrouted = os.path.join(HERE, "unrouted.log")
if "G" in flags:
    # Answered by this computer's own ssh, by its path, given the test's own configuration: HOME's .ssh/config, or
    # /dev/null, where the line gives no -F. OpenSSH finds ~ in the account database, never in HOME, so without the -F
    # it would read the developer's. -G dials nothing.
    if REAL is None:
        log(unrouted, "refused: -G, no ssh on this computer")
        said("stand-in ssh: this computer has no ssh to ask -G of", 255)
    log(unrouted, "-G")
    # slow-G beside the stand-in: ssh -G takes that many seconds first, as a person's Match exec may; killed-G: it is
    # ended by a signal before it answers.
    if os.path.exists(os.path.join(HERE, "slow-G")):
        with open(os.path.join(HERE, "slow-G")) as seconds:
            time.sleep(float(seconds.read()))
    if os.path.exists(os.path.join(HERE, "killed-G")):
        os.kill(os.getpid(), 9)
    if "F" not in valued:
        config = os.path.join(os.environ.get("HOME", "/nonexistent"), ".ssh", "config")
        argv = ["-F", config if os.path.isfile(config) else "/dev/null", *argv]
    os.execv(REAL, [REAL, *argv])
if destination is None:
    log(unrouted, "refused: no destination")
    said("usage: ssh [options] destination [command]", 255)
# ssh keeps the first account given, by -l, -o User= or as the destination's user@, and this computer's own where none
# is.
address = destination.rpartition("@")[2]
account = (valued.get("l") or [os.environ.get("USER", "")])[0]
port = (valued.get("p") or ["22"])[0]
machine = MACHINES.get(address)
if machine is None:
    log(unrouted, "refused: no machine has the address")
    said(f"ssh: connect to host {address} port {port}: No route to host", 255)
calls = os.path.join(machine["directory"], "calls.log")
# A Proxmox host made a hop (hop()): its modes, and its shared connection a file at the socket's path that holds live,
# or stale for a socket nothing listens at.
hop = None
if os.path.exists(os.path.join(machine["directory"], "hop.json")):
    with open(os.path.join(machine["directory"], "hop.json")) as modes:
        hop = json.load(modes)
socket = first(options, "controlpath")
socket = None if socket is None or socket.lower() == "none" else socket


def master():
    """What the hop's shared connection holds: live, stale, or "" for none."""
    if socket is None or not os.path.exists(socket):
        return ""
    with open(socket) as held:
        return held.read()


policy = (first(options, "stricthostkeychecking") or "").lower()
if policy not in POLICIES or (first(options, "batchmode") or "").lower() != "yes":
    log(calls, "refused: not StrictHostKeyChecking=yes or accept-new, and BatchMode=yes")
    said("stand-in ssh: refused: a connection to a machine checks its host key strictly, or takes it at a first use,"
         " and never asks (StrictHostKeyChecking=yes or accept-new, and BatchMode=yes, the first value of each), and"
         " this one does not", 255)
control = (valued.get("O") or [None])[0]
if control is not None:
    if account != machine["account"]:
        log(calls, "refused: another account")
        said(f"{account}@{address}: Permission denied (publickey).", 255)
    if hop is not None and control in ("check", "stop"):
        # The hop's, as ssh answers from its socket: a master running, a socket nothing listens at, or none.
        log(calls, f"-O {control}")
        held = master()
        if held == "live":
            if control == "stop":
                os.remove(socket)
                said("Stop listening request sent.\r", 0)
            said(f"Master running (pid={os.getpid()})\r", 0)
        if held == "stale":
            said(f"Control socket connect({socket}): Connection refused\r", 255)
        said(f"Control socket connect({socket}): No such file or directory\r", 255)
    if control == "check":
        log(calls, "-O check")
        said(f"Master running (pid={os.getpid()})\r", 0)
    if control == "stop":
        log(calls, "-O stop")
        said("Stop listening request sent.\r", 0)
    log(calls, f"refused: -O {control}")
    said(f"stand-in ssh: -O {control} is not answered: a stand-in master answers check and stop alone", 255)
# A ProxyCommand other than none, the first given: run as ssh runs one, its %-tokens expanded (%h the address, %p the
# port, %r the account, %% a %), by /bin/sh with this ssh's environment, its standard error ssh's own; ssh goes on only
# where its standard output begins as an SSH server's does, and otherwise says what ssh says of a proxy that closed
# before the server spoke.
proxy = first(options, "proxycommand")
if proxy is not None and proxy.lower() != "none":
    tokens = {"h": address, "p": port, "r": account, "%": "%"}
    expanded = re.sub(r"%(.)", lambda found: tokens.get(found.group(1), found.group(0)), proxy)
    try:
        through = subprocess.run(["/bin/sh", "-c", expanded], stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        log(calls, "refused: its proxy said nothing")
        said("Connection timed out during banner exchange\r\nConnection to UNKNOWN port 65535 timed out", 255)
    sys.stderr.write(through.stderr.decode("utf-8", "replace"))
    sys.stderr.flush()
    if not through.stdout.startswith(b"SSH-"):
        log(calls, "refused: its proxy reached no SSH server")
        said("kex_exchange_identification: Connection closed by remote host\r\n"
             "Connection closed by UNKNOWN port 65535", 255)
# The host key, as ssh checks it at every connection before any account is tried: under the first HostKeyAlias, else
# the address ([address]:port off port 22), in the files of the first UserKnownHostsFile, else ~/.ssh's two, with ~ the
# test's own HOME (ssh's is the account's, from the account database). The system's own known-hosts files are never
# read: a made-up key is in none.
alias = first(options, "hostkeyalias")
name = shlex.split(alias)[0] if alias else (address if port == "22" else f"[{address}]:{port}")
listed = first(options, "userknownhostsfile")
listed = shlex.split(listed) if listed is not None else ["~/.ssh/known_hosts", "~/.ssh/known_hosts2"]
files = [] if [each.lower() for each in listed] == ["none"] else [os.path.expanduser(each) for each in listed]
try:
    own_ssh = os.path.join(os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir), ".ssh")
except KeyError:
    # An account the account database does not know has no home of its own to keep out.
    own_ssh = None
if own_ssh and any((os.path.realpath(each) + os.sep).startswith(own_ssh + os.sep) for each in files):
    log(calls, "refused: this computer's own ~/.ssh")
    said("stand-in ssh: refused: a known-hosts file in this computer's own ~/.ssh, which no test reads or writes", 255)
kind, body = machine["key"].split()
found, offending = looked_up(files, name, kind, body)
label = kind.removeprefix("ssh-").upper()
if found == "changed":
    path, number = offending
    fingerprint = base64.b64encode(hashlib.sha256(base64.b64decode(body)).digest()).decode().rstrip("=")
    log(calls, "refused: another host key is filed under the name")
    said("\n".join([
        "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@",
        "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @",
        "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@",
        "IT IS POSSIBLE THAT SOMEONE IS DOING SOMETHING NASTY!",
        "Someone could be eavesdropping on you right now (man-in-the-middle attack)!",
        "It is also possible that a host key has just been changed.",
        f"The fingerprint for the {label} key sent by the remote host is",
        f"SHA256:{fingerprint}.",
        "Please contact your system administrator.",
        f"Add correct host key in {files[0]} to get rid of this message.",
        f"Offending {label} key in {path}:{number}",
        f"{label} host key for {name} has changed and you have requested strict checking.",
        "Host key verification failed.",
    ]), 255)
if found == "new" and policy == "yes":
    log(calls, "refused: no host key is filed under the name")
    said(f"No {label} host key is known for {name} and you have requested strict checking.\n"
         "Host key verification failed.", 255)
if found == "new" and files:
    take(files[0], name, kind, body)
if account != machine["account"]:
    log(calls, "refused: another account")
    said(f"{account}@{address}: Permission denied (publickey).", 255)
if hop is not None and valued.get("W"):
    # A forward through the hop (-W): on its shared connection where one is live, else a login of its own, refused in
    # its mode; a ProxyCommand's (ControlMaster=auto) with none live becomes the master, as ssh's does.
    forward_to = valued["W"][0]
    shared = socket is not None and master() == "live"
    alone = "" if shared else " unshared"
    if not shared and hop["login"] == "refused":
        log(calls, f"refused: the login, for a forward to {forward_to}{alone}")
        said(f"{account}@{address}: Permission denied (publickey).", 255)
    if not shared and socket is not None and (first(options, "controlmaster") or "").lower() == "auto":
        with open(socket, "w") as made:
            made.write("live")
    log(calls, f"forward {forward_to}{alone}")
    mode = hop["forward"]
    if mode == "stalled":
        # The login never done: no word of it, and nothing until the caller's limit.
        time.sleep(3600)
    if (first(options, "loglevel") or "").upper() in ("VERBOSE", "DEBUG", "DEBUG1", "DEBUG2", "DEBUG3"):
        sys.stderr.write(f'Authenticated to {address} ([{address}]:{port}) using "publickey".\n')
        sys.stderr.flush()
    if mode == "prohibited":
        said("channel 0: open failed: administratively prohibited: open failed\r\nstdio forwarding failed", 255)
    if mode == "failed":
        said("stdio forwarding request failed: Session open refused by peer", 255)
    if mode == "silent":
        time.sleep(3600)
    reached = MACHINES.get(forward_to.rpartition(":")[0])
    if reached is None or reached is machine:
        said("channel 0: open failed: connect failed: No route to host\r\nstdio forwarding failed", 255)
    # What the machine's sshd says first, then the end of the forward.
    sys.stdout.buffer.write(b"SSH-2.0-OpenSSH_9.6 stand-in\r\n")
    sys.stdout.flush()
    sys.exit(0)
if hop is not None and "N" in flags and not valued.get("L"):
    # The hop's master opened, in its login's mode: never over a socket file there, as a master never takes a path
    # over: it says so and goes on as a connection of its own.
    if hop["login"] == "refused":
        log(calls, "refused: the login")
        said(f"{account}@{address}: Permission denied (publickey).", 255)
    if socket is not None and master():
        log(calls, "a master, beside one there")
        sys.stderr.write(f"ControlSocket {socket} already exists, disabling multiplexing\n")
        sys.stderr.flush()
        time.sleep(3600)
    if socket is not None:
        with open(socket, "w") as made:
            made.write("live")
    log(calls, "a master, opened")
    sys.exit(0)
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
# A check that the line names no sudo by its path as a word - one whose last part starts with sudo (sudo-rs, sudo.ws),
# but the machine's own, which its PATH finds - and no command -p, which takes the system's default PATH: it catches a
# caller naming /usr/bin/sudo, as today's plays do, and not a shell that builds the path.
own = os.path.join(machine["directory"], "bin", "sudo")
named = re.findall(r"[^\s'\"`;|&()<>]+", line)
by_path = [word for word in named if word.startswith("/") and os.path.basename(word).startswith("sudo") and word != own]
default_path = [
    word for word, after in zip(named, named[1:]) if word == "command" and re.fullmatch(r"-[a-zA-Z]*p[a-zA-Z]*", after)
]
if by_path or default_path:
    log(calls, "refused: this computer's own sudo")
    said("stand-in ssh: refused: the command names a sudo by its path, or command -p, which would be this computer's"
         " own and run as root on it: a stand-in machine's sudo is the one its PATH finds", 255)
log(calls, "forward, never opened" if valued.get("L") else "ran")
env = {"HOME": machine["home"], "PATH": machine["path"], "STAND_IN": machine["home"]}
stdin = subprocess.DEVNULL if "n" in flags else None
# The home's .bashrc read first, where it has one, as Debian's and Ubuntu's bash reads it for a command sshd runs: what
# it prints comes before anything of the command's.
if os.path.isfile(os.path.join(machine["home"], ".bashrc")):
    line = '. "$HOME/.bashrc"\n' + line
process = subprocess.Popen(["/bin/sh", "-c", line], cwd=machine["home"], env=env, stdin=stdin, preexec_fn=no_terminal)
status = process.wait()
# A command ended by a signal has no status ssh can pass on: 255, as ssh exits.
sys.exit(255 if status < 0 else status)
'''


@dataclasses.dataclass(frozen=True)
class Machine:
    """A stand-in machine: its name, its identity, its host key, the address it is reached at, the account it takes,
    its directory, and whether its sudo asks a password."""

    name: str
    identity: str
    key: str
    address: str
    account: str
    directory: Path
    asks_password: bool

    @property
    def uuid(self) -> str:
        """Its SMBIOS UUID, as Proxmox gives the identity's."""
        return self.identity.removeprefix("agent-warehouse-")

    @property
    def line(self) -> str:
        """The known-hosts line that files its key under its identity, as B5 files one: `<identity> <type> <base64>`."""
        return f"{self.identity} {self.key}"

    @property
    def home(self) -> Path:
        return self.directory / "home"

    @property
    def root_home(self) -> Path:
        """The HOME a command run under its sudo is given, as sudo gives root's."""
        return self.directory / "root"

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
        # The test's own Python, as a machine's python3: one Python on every system, never macOS's own 3.9. Plain, as
        # a machine's is: today's python wrapper would take its HOME from its own folder, bin/.
        "python3": f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
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


def _made_up_key() -> str:
    """A made-up ed25519 public key, as OpenSSH writes one: its type, then the base64 of the type's name and 32 bytes
    of the test's own, which no machine anywhere holds the private half of."""
    blob = struct.pack(">I", len(KEY_TYPE)) + KEY_TYPE.encode() + struct.pack(">I", 32) + os.urandom(32)
    return f"{KEY_TYPE} {base64.b64encode(blob).decode()}"


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
    asking sudo_password where one is given. Its identity and its host key are made for the test."""
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
        key=_made_up_key(),
        address=reached_at,
        account=account,
        directory=directory,
        asks_password=sudo_password is not None,
    )
    made.bin.mkdir()
    for tool in TOOLS:
        (made.bin / tool).symlink_to(programs / tool)
    made.home.mkdir()
    made.root_home.mkdir()
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
    record = {
        "name": name,
        "identity": made.identity,
        "key": made.key,
        "address": made.address,
        "account": account,
        "sudo": digest,
        # sudo's secure_path on the machine: the PATH a command run under its sudo has, whatever the caller's.
        "path": machine_path(made),
    }
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
            "identity": made.identity,
            "key": made.key,
            "directory": str(made.directory),
            "home": str(made.home),
            "account": made.account,
            "path": machine_path(made),
        }
    (folder / "machines.json").write_text(json.dumps(table))
    # This computer's own ssh, by its path, for -G alone, which reads the test's own configuration and dials nothing.
    (folder / "ssh.py").write_text(f"REAL = {shutil.which('ssh', path=os.defpath)!r}\n" + SSH)
    harness._executable(folder / "ssh", f'#!/bin/sh\nexec "{sys.executable}" -I "{folder / "ssh.py"}" "$@"\n')
    return folder


# The modes a hop is made in: its login (open, or refused as a key the host does not take is), and what a forward
# through it meets (answer: the machine's sshd speaks; prohibited: the host's sshd refuses to forward; failed: ssh
# cannot use the host as a hop; silent: the machine says nothing; stalled: the login for a forward of its own never
# ends).
HOP_LOGINS = ("open", "refused")
HOP_FORWARDS = ("answer", "prohibited", "failed", "silent", "stalled")
# Where the fake Proxmox listens, and so where host.yml points a host made against it (fleet_fixture.with_port): the one
# address but RFC 5737's a stand-in is reached at, and only a hop, which the stand-in ssh alone answers.
LOOPBACK = "127.0.0.1"


def hop(
    root: Path, name: str = "pve", *, address: str | None = None, login: str = "open", forward: str = "answer"
) -> Machine:
    """A stand-in Proxmox host under root that is the hop to the machines (engine/routes.py): a machine reached as root
    (machine()), at address - one of RFC 5737's, or LOOPBACK, the fake Proxmox's own, where host.yml points the host -
    in the modes hop_modes() sets. The stand-in ssh answers its -O check and -O stop from a file at the socket's path
    (live, or stale for a socket nothing listens at), opens its master in its login's mode by writing that file, and
    answers a forward (-W) in its forward's mode: to a machine of its table, the machine's sshd's first words."""
    at = address
    if address == LOOPBACK:
        at = None
    made = machine(root, name, account="root", address=at)
    if address == LOOPBACK:
        record = json.loads((made.directory / "machine.json").read_text())
        record["address"] = LOOPBACK
        (made.directory / "machine.json").write_text(json.dumps(record))
        made = dataclasses.replace(made, address=LOOPBACK)
    hop_modes(made, login=login, forward=forward)
    return made


def hop_modes(made: Machine, *, login: str | None = None, forward: str | None = None) -> None:
    """made's modes as a hop, each kept where not given: its login, one of HOP_LOGINS, and a forward's, one of
    HOP_FORWARDS."""
    path = made.directory / "hop.json"
    modes = json.loads(path.read_text()) if path.exists() else {"login": "open", "forward": "answer"}
    if login is not None:
        if login not in HOP_LOGINS:
            pytest.fail(f"a hop's login is one of {', '.join(HOP_LOGINS)}: {login!r}")
        modes["login"] = login
    if forward is not None:
        if forward not in HOP_FORWARDS:
            pytest.fail(f"a forward through a hop is one of {', '.join(HOP_FORWARDS)}: {forward!r}")
        modes["forward"] = forward
    path.write_text(json.dumps(modes))


def unrouted(folder: Path) -> list[dict]:
    """The calls the stand-in ssh in folder took for no machine: an address none has, or ssh -G."""
    return harness._json_lines(folder / "unrouted.log")


def known_hosts(made: Machine) -> Path:
    """The known-hosts file a target of made's checks its key in unless it is given another: one beside the machines,
    which filed writes by default."""
    return made.directory.parent / "known_hosts"


def filed(made: Machine, path: Path | None = None) -> Path:
    """made's key filed under its identity in the known-hosts file at path (known_hosts(made) unless given), as B5
    files one and a first use leaves it: the line `<identity> <type> <base64>` (Machine.line), added once at the file's
    end, the file made 0600 where it is not there. path."""
    where = known_hosts(made) if path is None else Path(path)
    where.parent.mkdir(parents=True, exist_ok=True)
    text = where.read_text() if where.exists() else ""
    if made.line not in text.splitlines():
        handle = os.open(where, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(handle, (("\n" if text and not text.endswith("\n") else "") + made.line + "\n").encode())
        finally:
            os.close(handle)
    return where


def target(made: Machine, *, routed_as: str | None = None, **over) -> runner.Target:
    """made as B3's runner reaches it: its identity, address and account, its key checked strictly against
    known_hosts(made), where a test files it with filed(made), and a control directory that is never made. over goes
    to runner.machine (first_use, known_hosts, given, ...). Marked as a route marks one (connections.target_for, which
    a test that settles no route does not reach): through the hop where over gives one, else straight at its address;
    routed_as gives another mark, "" none, which the runner refuses. A mark is given with what it was decided from
    (runner.decided_from), as target_for gives it."""
    given = {"known_hosts": known_hosts(made), **over}
    made_target = runner.machine(made.identity, made.address, made.account, CONTROL, **given)
    mark = ("hop" if made_target.hop is not None else "direct") if routed_as is None else routed_as
    if not mark:
        return made_target
    return dataclasses.replace(made_target, routed_as=mark, routed_from=runner.decided_from(made_target))


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
