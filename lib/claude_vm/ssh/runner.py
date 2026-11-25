"""The SSH runner: one bounded command on a machine, or one held for a sign-in, over OpenSSH's own ssh as a child
(docs/architecture/engine.md §16.6).

A machine is a `Target`, a frozen record of everything its connection is made from: the machine's identity, the one
IPv4 address it is reached at, the account and the port, the known-hosts file its key is checked against, the one
shared control directory, an identity file or none, the hop through the Proxmox host or none, the person's own options
for it (the setting `vm_ssh_options`), whether this is a first use, and the route's mark (`routed_as`): how the route a
run settled reaches it, straight to its address, through the hop or by a jump of its own, which `connections.py`'s
`target_for` alone gives (engine/routes.py's `Route.targets` calls it), with what the route decided it from
(`routed_from`: its address, account, port, ssh program and options, and the hop it gave). A machine's `Target` is made
unmarked (`machine`), and the runner makes no command line of one: a `Target` made anywhere but through the route is
refused before anything starts, and so is one marked through the hop that no longer has it, or one whose address,
account, port, ssh program, options or hop are no longer what the route decided from (a `dataclasses.replace`, or any
other way of changing one), so that nothing dials a machine by a road the route did not give it: a first use's
known-hosts file is all that changes after the route. The Proxmox host itself is the second form of it
(`proxmox_host`), for root SSH and as the hop: no identity, its key in the person's own known-hosts file, taken at first
use as `ssh` reaches it today, and no mark.

`argv` builds the whole command line, each value one word and none through a shell: the product's options first, since
`ssh` keeps the first value it is given for an option, then the hop's, then the person's, then `--`, the address and the
command joined with `shlex.join`. No `-F`: the person's own `~/.ssh/config` is read, as today, where a key, an agent or
a jump of theirs is named. Every `ssh` here carries `BatchMode=yes`, so a key that wants a passphrase fails in one
sentence and never waits at a terminal it cannot reach, and `ControlMaster=no`: a child rides a master the engine opened
in the shared directory (`connections.py`'s `ensure`), or connects alone where none lives, and is never a master itself.
It authenticates by public key alone (`PreferredAuthentications=publickey`), the Proxmox host's connection too: GSSAPI
and host-based authentication are added only if someone asks. A machine is named by its identity alone, its key checked
strictly in the product's own file, which `ssh` can neither add to nor replace; a first use takes the key into a
scratch file instead, under the identity and never the address, for the caller to file, on a connection of its own
(`ControlPath=none`): a master of the identity and the address that still lives would carry it, and `ssh` would write no
key. A machine whose socket's path in the shared directory would pass `route.SOCKET_MOST` bytes (a long home) takes no
shared connection either (`Target.shared`): each of its connections logs in alone. A connection to the Proxmox host
rides the hop's shared connection where one lives, and from a home so long that the hop's socket would pass the length
it logs in alone too, as a machine's does: no master is opened there, since the hop's is opened only where the route
through the host is settled, and from such a home that route is refused (`connections.LONG_HOP`).

`run` starts the command line as a child of the run through `runs/children.py`'s `start`, in a process group of its
own, hands it bytes on its standard input, and reads its standard output a line at a time under a bound for one line,
one for the whole, a deadline, and a time to say anything at all; its standard error is kept to a fixed size; on a
breach its group is killed. `hold` starts the same command line and keeps it, its standard input open for a line
written later and its output read while it runs: the sign-ins' children. `forward` holds an `ssh` with the caller's
`-L` specs, a connection of its own whose far end is the command `sleep` for the caller's deadline, so that where the
run that holds it was killed and could end it no more it ends by itself, once its deadline has passed and the
connections through it have closed; nothing is recorded on the machine.
`ansible_vars` gives layer 1's Ansible child the same options, so that its `ssh`, `sftp` and `scp` ride the same master.
Every string a result holds is made plain (`text.plain_text`).

Ending the local `ssh` - a breach, a cancel, `end` - never ends the command on the machine: with no terminal on the
session, `sshd` sends it no signal, so a command that neither reads nor writes goes on. A caller whose command changes
something bounds it on the machine (`timeout`) or reads its state again. An exit status of 255 is `ssh`'s own failure,
or the command's own 255, or a signal that ended the command, which `ssh` cannot pass on: `Unreachable` keeps the lines
the command printed, and cannot say which it was. A command that ran and printed nothing reads as a machine never
reached, and one that printed may go on after a connection lost, so `Unreachable` never says that a command did not
run: a caller whose command changes something, above all one that must run once, reads the machine's state again before
it tries the machine again.

A secret reaches a child only on its standard input: a `Secret` is revealed in `_bytes` alone, never in a word or a
variable. Only the address is a value a machine supplied, held to one IPv4 address; nothing else of a machine's reaches
an option. Standard library only; asyncio is imported inside the functions that start or read a child.
"""

from __future__ import annotations

import contextlib
import dataclasses
import ipaddress
import math
import os
import re
import shlex
import signal
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from claude_vm import checks, route, text
from claude_vm.runs import children
from claude_vm.secret import Secret

if TYPE_CHECKING:
    from asyncio import StreamReader, StreamWriter
    from asyncio.subprocess import Process

    from claude_vm.runs.job import Job

# The product's own known-hosts file, where a machine's key is filed under its identity and every connection but a
# first use checks it: one for every fleet on the computer, outside every repository, beside the person's own, so that
# the editor's alias, which the person's own ssh reads, names the same file.
KNOWN_HOSTS = Path("~/.ssh/agent-warehouse.known_hosts")
# The person's own known-hosts files, which a first use never writes: the Proxmox host's key alone is kept there.
PERSONS_KNOWN_HOSTS = (Path("~/.ssh/known_hosts"), Path("~/.ssh/known_hosts2"))
# The ssh program where ssh_executable names none: the one found on PATH. aw status hands it to today's status engine.
SSH_PROGRAM = "ssh"
# The policy of the two forms that take a key at first use - a machine's first connection, into a scratch file, and the
# Proxmox host's, into the person's own file as ansible.cfg's ssh_args take it today - and of every other connection.
ACCEPT_NEW = "accept-new"
STRICT = "yes"

# Today's limits on a connection to a VM, as status/reach.py has them (tests/unit/test_aw_ssh_runner.py holds them
# equal, since the new engine imports nothing of status/, which makes TLS contexts): a line of 16 MiB, 45 s in all,
# 10 s to connect, 20 s to say a first word.
LINE_BYTES = 16 * 1024 * 1024
SECONDS = 45
CONNECT_SECONDS = 10
START_SECONDS = 20

# What every ssh here is given after the host key's options and its socket, in this order, with the host key's policy
# after them and the forwards' clearing after PreferredAuthentications (_pinned): a child, never a master; no prompt,
# ever; the computer's X display never handed to the machine, and no program run here for a connection (each known to
# every OpenSSH, so pinned, where a person's ForwardX11 yes or Ubuntu's own ForwardX11Trusted would otherwise reach
# it); today's limits on a connection and on its silence; a key and nothing else; no terminal; and ssh's own words kept
# to its errors (the hop's ssh keeps INFO, which route.py reads).
FIRST = ("ControlMaster=no", "BatchMode=yes", "ForwardX11=no", "PermitLocalCommand=no")
THEN = (
    f"ConnectTimeout={CONNECT_SECONDS}",
    "ServerAliveInterval=15",
    "ServerAliveCountMax=3",
    "PreferredAuthentications=publickey",
)
LAST = ("RequestTTY=no", "LogLevel=ERROR")

# The options a person may not give in vm_ssh_options, by ssh's name for each in lower case: what the product writes
# itself - the key's checking, the control socket, no prompt, no agent forwarded (claude_ssh_forward_agent's), the key
# (claude_ssh_identity_file's) - and where and as whom it connects, which -l, -p and the address after -- say; with the
# names ssh reads as one of them (IdentityFile2, OpenSSH's old name for IdentityFile), and what would hand the key's
# checking a key the product's file does not hold (GlobalKnownHostsFile, and KnownHostsCommand, a program run here for
# each connection, whose keys the strict check takes). StrictHostKeyChecking may be given only as yes.
REFUSED_OPTIONS = {
    "hostkeyalias": "HostKeyAlias",
    "userknownhostsfile": "UserKnownHostsFile",
    "globalknownhostsfile": "GlobalKnownHostsFile",
    "knownhostscommand": "KnownHostsCommand",
    "checkhostip": "CheckHostIP",
    "updatehostkeys": "UpdateHostKeys",
    "batchmode": "BatchMode",
    "controlpath": "ControlPath",
    "controlmaster": "ControlMaster",
    "forwardagent": "ForwardAgent",
    "identityfile": "IdentityFile",
    "identityfile2": "IdentityFile2",
    "hostname": "HostName",
    "user": "User",
    "port": "Port",
}
# The options a person may not give in vm_ssh_options because they undo what every connection holds, by ssh's name in
# lower case: a standard input never read (StdinNull: a secret would silently never arrive), an ssh that leaves for the
# background (ForkAfterAuthentication: out of its group, so a breach ends nothing), no command or another one
# (SessionType, RemoteCommand), the computer's X display handed to the machine (ForwardX11 and its two), a program
# run here for each connection (PermitLocalCommand, LocalCommand), and a library loaded here (PKCS11Provider, with
# SmartcardDevice, OpenSSH's other name for it, and SecurityKeyProvider). The three newer ones are refused rather than
# pinned, which would break an ssh older than 8.7.
UNDOING_OPTIONS = {
    "stdinnull": "StdinNull",
    "forkafterauthentication": "ForkAfterAuthentication",
    "sessiontype": "SessionType",
    "remotecommand": "RemoteCommand",
    "forwardx11": "ForwardX11",
    "forwardx11trusted": "ForwardX11Trusted",
    "forwardx11timeout": "ForwardX11Timeout",
    "permitlocalcommand": "PermitLocalCommand",
    "localcommand": "LocalCommand",
    "pkcs11provider": "PKCS11Provider",
    "smartcarddevice": "SmartcardDevice",
    "securitykeyprovider": "SecurityKeyProvider",
}
STRICT_OPTION = "stricthostkeychecking"

# A word of a command line or a path that holds no control character: nothing that ends a line of ssh's config.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# An account as ssh takes one, never starting with "-" (route.py's rule for the hop's).
_ACCOUNT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,63}")
# A program, or a directory whose socket path ssh expands: plain characters, never % (one of ssh's tokens), a space or
# a quote.
_PLAIN = re.compile(r"[A-Za-z0-9_./+@,=-]{1,4096}")
# An option's name as ssh reads one: a letter, then letters and digits (ForwardX11).
_OPTION = re.compile(r"[A-Za-z][A-Za-z0-9]{0,63}")
# The name of the hop's shared connection, as route.hop_settings names its socket.
_HOP_NAME = re.compile(r"awh-[0-9a-f]{12}")
# A forward's spec as -L takes it for a loopback port: [bind address:]port:host:hostport, an IPv6 address in brackets.
_FORWARD = re.compile(
    r"(?:(?:\[[0-9A-Fa-f:.]{2,45}\]|[A-Za-z0-9._-]{1,253}):)?[0-9]{1,5}:(?:\[[0-9A-Fa-f:.]{2,45}\]|[A-Za-z0-9._-]{1,253})"
    r":[0-9]{1,5}"
)

# How the route a run settled reaches a machine, the mark connections.target_for gives its Target, the one place that
# gives one: straight to its address (the route is direct, or the machine is no Proxmox VM), through the hop, or by a
# jump of its own. A Target marked through the hop has one, and one marked otherwise has none.
ROUTED_AS = ("direct", "hop", "own")
_ROUTED_SAID = {"direct": "straight at its address", "hop": "through the hop", "own": "by a jump of its own"}
# What the route decided a machine's mark from, and the hop it gave, kept on its Target beside the mark (routed_from,
# decided_from): the runner refuses a Target any of them changed on since, by whatever spelling, since its mark is then
# no longer the route's - an address moved to another VM's, a jump taken out of its options. Only a first use's
# known-hosts file changes after the route (connections.first_use), and the files and the directory a connection is
# checked and shared in, which decide no road.
ROUTED_FROM = ("address", "account", "port", "ssh", "given", "hop")
_ROUTED_FROM_SAID = {
    "address": "address",
    "account": "account",
    "port": "port",
    "ssh": "ssh program",
    "given": "vm_ssh_options",
    "hop": "hop",
}

# The longest a forward is held, in seconds: a sign-in's wait on a person, a few minutes, well within it.
FORWARD_MOST = 24 * 60 * 60

# The way out of a refused setting of the Proxmox host's: where the new engine keeps each one.
HOST_WAY_OUT = (
    "set it in the host's file (ssh_user, ssh_port, address), or this computer's own (ssh_identity_file,"
    " ssh_executable)"
)


class Refused(ValueError):
    """A target, a command or an option the runner does not take, said by name: nothing was started."""


class Ended(OSError):
    """A held child that has ended, or whose standard input is not held: nothing was sent."""


# ---------------------------------------------------------------- the target ----


@dataclasses.dataclass(frozen=True)
class Hop:
    """The hop through the Proxmox host that the route settles for one machine: the ProxyCommand ssh runs to reach the
    machine's own address (route.py's, as it stands), and the name of the hop's shared connection, which the machine's
    socket is named with (checks.vm_control_name's third argument)."""

    proxy_command: str
    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.proxy_command, str) or not self.proxy_command or _CONTROL.search(self.proxy_command):
            raise Refused("the hop's command is one line of words")
        if not isinstance(self.name, str) or not _HOP_NAME.fullmatch(self.name):
            raise Refused("the hop's connection is named as route.py names one: awh- and twelve hex digits")


@dataclasses.dataclass(frozen=True, kw_only=True)
class Target:
    """What a connection to a machine is made from, or to the Proxmox host itself (`identity` None): each value held
    before any child starts. `given` is the person's own options, as (name, value) pairs in their order. `routed_as` is
    the route's mark, one of ROUTED_AS, which connections.target_for alone gives and dataclasses.replace carries: "" for
    a machine's Target no route has marked, which the runner makes no command line of (options), and for the host.
    `routed_from` is what the route decided the mark from (decided_from, as target_for read it), given with the mark and
    carried with it: a Target whose fields of ROUTED_FROM are no longer those is refused as it is made, and again where
    a command line is made of one changed by other means; () unmarked."""

    identity: str | None
    address: str
    account: str
    port: int = 22
    known_hosts: Path | None = None
    control_dir: Path
    identity_file: Path | None = None
    hop: Hop | None = None
    given: tuple[tuple[str, str], ...] = ()
    first_use: bool = False
    ssh: str = SSH_PROGRAM
    routed_as: str = ""
    routed_from: tuple[tuple[str, object], ...] = dataclasses.field(default=(), repr=False)

    def __post_init__(self) -> None:
        _port(self.port)
        _account(self.account)
        _program(self.ssh)
        if not isinstance(self.first_use, bool):
            raise Refused("whether a connection is a first use is True or False")
        if self.identity is None:
            self._as_the_host()
        else:
            self._as_a_machine()

    @property
    def is_host(self) -> bool:
        """Whether this is the Proxmox host itself, for root SSH and as the hop."""
        return self.identity is None

    def _as_a_machine(self) -> None:
        if not isinstance(self.identity, str) or not checks.HOST_KEY_ALIAS_RE.fullmatch(self.identity):
            raise Refused(f"{self.identity!r} is not a machine's identity, agent-warehouse-<its UUID>")
        object.__setattr__(self, "address", _ipv4(self.address))
        if self.known_hosts is None:
            raise Refused("a machine's key is checked against a known-hosts file: the product's own, or a scratch one")
        object.__setattr__(self, "known_hosts", _file(self.known_hosts, "the known-hosts file"))
        object.__setattr__(self, "control_dir", _directory(self.control_dir))
        if self.identity_file is not None:
            object.__setattr__(self, "identity_file", _file(self.identity_file, "the identity file"))
        if self.hop is not None and not isinstance(self.hop, Hop):
            raise Refused("a machine's hop is a Hop, or none")
        if self.hop is not None and self.hop.proxy_command.split(" ")[-3:-1] != ["-W", f"{self.address}:%p"]:
            raise Refused("the hop forwards to another address than the machine's own")
        object.__setattr__(self, "given", _given(self.given))
        if self.hop is not None and route.ssh_jump(given_words(self)):
            raise Refused("a machine given a jump of its own in vm_ssh_options takes no hop through the Proxmox host")
        if self.routed_as not in ("", *ROUTED_AS):
            raise Refused(f"a machine's Target is marked with the route it was given: {', '.join(ROUTED_AS)}")
        if self.routed_as == "hop" and self.hop is None:
            raise Refused(
                "a machine the route reaches through the Proxmox host has lost its hop, and would be dialled from here:"
                " nothing was started"
            )
        if self.routed_as not in ("", "hop") and self.hop is not None:
            raise Refused(f"a machine the route reaches {_ROUTED_SAID[self.routed_as]} takes no hop through the host")
        if not self.routed_as and self.routed_from:
            raise Refused("a machine's Target no route marked carries nothing a route decided from")
        if self.routed_as and (changed := _changed(self)):
            raise Refused(_moved(self, changed))
        if self.first_use:
            scratch = self.known_hosts.resolve()
            kept = [KNOWN_HOSTS, *PERSONS_KNOWN_HOSTS]
            if any(scratch == path.expanduser().resolve() for path in kept):
                raise Refused(
                    "a first use takes a key into a scratch file of its own, never the product's known-hosts file or"
                    " the person's"
                )

    @property
    def shared(self) -> bool:
        """Whether a machine's connections ride a shared one: its socket's path in the control directory is at most
        route.SOCKET_MOST bytes, 103 (the most macOS binds a socket's path to) less the 17 ssh adds while it binds one.
        Past it (a long home), each connection logs in alone (ControlPath=none), and no master is opened for it. The
        Proxmox host's is whether the hop's socket fits: its connections ride the hop's where it does, and past it each
        logs in alone, as a machine's does."""
        if self.is_host:
            return route.socket_fits(_host_socket(self))
        socket = f"{self.control_dir}/{_control_name(self)}-{self.account}-{self.port}"
        return route.socket_fits(socket)

    def _as_the_host(self) -> None:
        _host_refusal(self)
        try:
            # A socket past the length is no refusal of a setting: shared and options say it (route.socket_fits).
            settled = route.hop_settings(
                ssh=self.ssh,
                user=self.account,
                host=self.address,
                port=self.port,
                key=str(self.identity_file or ""),
                control_dir=str(_directory(self.control_dir)),
                sized=False,
            )
        except route.HopRefused as refused:
            raise Refused(f"{refused.reason}: {HOST_WAY_OUT}") from None
        object.__setattr__(self, "address", settled["host"])
        object.__setattr__(self, "control_dir", _directory(self.control_dir))
        if self.identity_file is not None:
            object.__setattr__(self, "identity_file", Path(settled["key"]))


def machine(
    identity: str,
    address: str,
    account: str,
    control_dir: Path | str,
    *,
    port: int = 22,
    known_hosts: Path | str = KNOWN_HOSTS,
    identity_file: Path | str | None = None,
    hop: Hop | None = None,
    given: Mapping[str, str] | Sequence[tuple[str, str]] = (),
    first_use: bool = False,
    ssh: str = SSH_PROGRAM,
) -> Target:
    """A machine, named by its identity and reached at one IPv4 address: its key checked against the product's own
    known-hosts file, or with first_use taken into the scratch file known_hosts names."""
    return Target(
        identity=identity,
        address=address,
        account=account,
        port=port,
        known_hosts=Path(known_hosts),
        control_dir=Path(control_dir),
        identity_file=None if identity_file is None else Path(identity_file),
        hop=hop,
        given=tuple(given.items()) if isinstance(given, Mapping) else tuple(given),
        first_use=first_use,
        ssh=ssh,
    )


def proxmox_host(
    address: str,
    account: str,
    control_dir: Path | str,
    *,
    port: int = 22,
    identity_file: Path | str | None = None,
    ssh: str = SSH_PROGRAM,
) -> Target:
    """The Proxmox host, for root SSH and as the hop: its account, port and address from the host's file (ssh_user,
    ssh_port, address), its key from this computer's own (ssh_identity_file), and the ssh program this computer names
    (ssh_executable). Its key is the person's own known-hosts file's, taken at first use."""
    return Target(
        identity=None,
        address=address,
        account=account,
        port=port,
        control_dir=Path(control_dir),
        identity_file=None if identity_file is None else Path(identity_file),
        ssh=ssh,
    )


def _host_refusal(target: Target) -> None:
    """Refused where target, the Proxmox host's (no identity), carries what only a machine's takes: a known-hosts file
    of the product's, a hop, vm_ssh_options, a first use or a route's mark. The one check, which the Target's making
    and every connection's options both call."""
    if (
        target.known_hosts is not None
        or target.hop is not None
        or target.given
        or target.first_use
        or target.routed_as
        or target.routed_from
    ):
        raise Refused(
            "the Proxmox host's key is the person's own known-hosts file's, taken at first use, and it takes no"
            " hop, no vm_ssh_options and no route's mark"
        )


def _port(port: object) -> int:
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        raise Refused("a port is a number from 1 to 65535")
    return port


def _account(account: object) -> str:
    if not isinstance(account, str) or not _ACCOUNT.fullmatch(account):
        raise Refused(f"{account!r} is not an account name")
    return account


def _program(ssh: object) -> str:
    if not isinstance(ssh, str) or not _PLAIN.fullmatch(ssh) or ssh.startswith(("-", "=")):
        raise Refused("the ssh program is one plain word: a name on PATH or a path, of letters, digits and _./+@,=-")
    return ssh


def _ipv4(address: object) -> str:
    """The one IPv4 address a machine is reached at: what ipaddress parses as one, and never one that reaches no
    machine (0.0.0.0, loopback, multicast, the reserved range and broadcast). The one value a machine supplies."""
    try:
        ip = ipaddress.IPv4Address(address) if isinstance(address, str) else None
    except ValueError:
        ip = None
    if ip is None or ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip.is_reserved:
        raise Refused(f"{address!r} is not one IPv4 address a machine is reached at")
    return str(ip)


def _file(path: object, what: str) -> Path:
    """A file ssh is given: absolute once ~ is expanded, with no control character, no double quote and no % (which ssh
    reads as one of its tokens)."""
    if not isinstance(path, (str, Path)):
        raise Refused(f"{what} is a path")
    path = Path(path).expanduser()
    if not path.is_absolute() or _CONTROL.search(str(path)) or any(ch in str(path) for ch in '"%'):
        raise Refused(f"{what} is an absolute path with no control character, double quote or % in it")
    return path


def _directory(path: object) -> Path:
    """The shared control directory: absolute once ~ is expanded, of plain characters, since ssh expands its tokens in
    a socket's path."""
    if not isinstance(path, (str, Path)):
        raise Refused("the control directory is a path")
    path = Path(path).expanduser()
    if not path.is_absolute() or not _PLAIN.fullmatch(str(path)):
        raise Refused("the control directory is an absolute path of letters, digits and _./+@,=- alone")
    return path


def _given(given: object) -> tuple[tuple[str, str], ...]:
    """The person's own options for a machine, held: each an option ssh names, given once, never one the product
    writes itself or one that would loosen the host key's check (REFUSED_OPTIONS), or one that undoes what every
    connection holds (UNDOING_OPTIONS), each said by name - by the name it was given, ssh's other names among them -
    StrictHostKeyChecking only as yes, each value one line."""
    if isinstance(given, Mapping):
        given = tuple(given.items())
    if not isinstance(given, tuple) or not all(isinstance(pair, tuple) and len(pair) == 2 for pair in given):
        raise Refused("vm_ssh_options is a mapping of ssh's option names to values")
    seen: set[str] = set()
    for name, value in given:
        if not isinstance(name, str) or not _OPTION.fullmatch(name):
            raise Refused("vm_ssh_options names each option as ssh does: a letter, then letters and digits")
        lower = name.lower()
        if lower in REFUSED_OPTIONS:
            raise Refused(
                f"vm_ssh_options sets {REFUSED_OPTIONS[lower]}, which only the product sets - the host key's check, the"
                " shared connection, no prompt, the agent, the key, and where and as whom it connects: take it out of"
                " vm_ssh_options (agent forwarding is claude_ssh_forward_agent, the key claude_ssh_identity_file)"
            )
        if lower in UNDOING_OPTIONS:
            raise Refused(
                f"vm_ssh_options sets {UNDOING_OPTIONS[lower]}, which undoes what every connection holds - a command's"
                " standard input, the ssh a run ends, the command run, this computer's display kept from the machine,"
                " and no program or library of its own run here for a connection: take it out of vm_ssh_options"
            )
        if lower in seen:
            raise Refused(f"vm_ssh_options gives {name} twice")
        seen.add(lower)
        if not isinstance(value, str) or not value.strip() or _CONTROL.search(value):
            raise Refused(f"vm_ssh_options' value for {name} is one line of text")
        if lower == STRICT_OPTION and value.strip().lower() != STRICT:
            raise Refused(
                "vm_ssh_options sets StrictHostKeyChecking to other than yes: the host key's checking is only ever made"
                " as strict as the product makes it"
            )
    return given


def _control_name(target: Target) -> str:
    """What a machine's socket is named by: its identity, its address and its hop (checks.vm_control_name)."""
    return checks.vm_control_name(target.identity, target.address, target.hop.name if target.hop else "")


def _host_socket(target: Target) -> str:
    """The Proxmox host's socket: the hop's, as route.hop_settings names it, whatever its length."""
    return route.hop_settings(
        ssh=target.ssh,
        user=target.account,
        host=target.address,
        port=target.port,
        key=str(target.identity_file or ""),
        control_dir=str(target.control_dir),
        sized=False,
    )["socket"]


def _policy(target: Target) -> str:
    """How the host key is checked: taken at first use for a machine's first connection, into its scratch file, and
    for the Proxmox host, into the person's own file; strictly against the product's own file for every other."""
    return ACCEPT_NEW if target.first_use or target.is_host else STRICT


# ---------------------------------------------------------------- the command line ----


def decided_from(target: Target) -> tuple[tuple[str, object], ...]:
    """What a route decides target's mark from, and the hop it gives, as (name, value) pairs of ROUTED_FROM in its
    order: what connections.target_for keeps beside the mark it gives (Target.routed_from)."""
    return tuple((name, getattr(target, name)) for name in ROUTED_FROM)


def _changed(target: Target) -> list[str]:
    """The fields of ROUTED_FROM that target no longer holds as its route decided from: none where it is as the route
    marked it; every one where what it keeps is not a record of them."""
    now = decided_from(target)
    if target.routed_from == now:
        return []
    try:
        then = dict(target.routed_from)
    except (TypeError, ValueError):
        then = {}
    changed = [name for name, value in now if name not in then or then[name] != value]
    return changed if changed and set(then) == set(ROUTED_FROM) else list(ROUTED_FROM)


def _moved(target: Target, changed: Sequence[str]) -> str:
    """Why a Target changed since its route marked it is refused, its changed fields named."""
    said = ", ".join(_ROUTED_FROM_SAID[name] for name in changed)
    return (
        f"a machine the route reaches {_ROUTED_SAID[target.routed_as]} was marked so for the address, account, port,"
        f" ssh program, vm_ssh_options and hop it had then, and its {said} changed since, so the mark is no longer the"
        " route's: nothing was started; take it again from the route (engine/routes.py's Route.targets)"
    )


def _routed(target: Target) -> None:
    """A machine's Target held to the route's mark before any command line is made of it: Refused where no route
    marked it (made anywhere but through engine/routes.py's Route.targets), where one marked through the hop has none,
    or where any of what the route decided its mark from changed since (routed_from) - as Target holds it when it is
    made, and again here, for one changed since by other means."""
    if target.routed_as not in ROUTED_AS:
        raise Refused(
            "a machine's Target is made by the route the run settled (engine/routes.py's Route.targets), which marks"
            " how it is reached: this one is unmarked, so nothing was started"
        )
    if (target.routed_as == "hop") != (target.hop is not None):
        raise Refused(
            f"a machine the route reaches {_ROUTED_SAID[target.routed_as]} "
            + ("has lost its hop" if target.hop is None else "has been given a hop")
            + ": nothing was started"
        )
    if changed := _changed(target):
        raise Refused(_moved(target, changed))


def given_words(target: Target) -> list[str]:
    """The person's own options for target, as words: -o Name=value each, in their order."""
    return [word for name, value in target.given for word in ("-o", f"{name}={value}")]


def _pinned(target: Target, forward: bool) -> list[str]:
    """The options every ssh here carries after its socket. A forward keeps its -L whatever a person's
    ClearAllForwardings says, fails where a port cannot be bound, and binds each loopback family it is given, as
    today's forward does whatever a person's AddressFamily says."""
    pinned = [*FIRST, f"StrictHostKeyChecking={_policy(target)}", *THEN]
    pinned += ["ClearAllForwardings=no"] if forward else ["ClearAllForwardings=yes"]
    pinned += LAST
    if forward:
        pinned += ["ExitOnForwardFailure=yes", "AddressFamily=any"]
    return [word for option in pinned for word in ("-o", option)]


def options(target: Target, forward: bool = False) -> list[str]:
    """Every option of target's connection, in the order ssh is given them: the product's, then the hop's, then the
    person's. A forward's is a connection of its own (ControlPath=none) that fails where a port cannot be bound and
    keeps its forwards whatever a person's ClearAllForwardings says; so is a first use's, which a master that still
    lives for the identity and the address would otherwise carry, so that ssh wrote no key into its scratch file; and
    so is every connection of a machine whose socket's path would be too long (Target.shared). The Proxmox host's
    connections ride the hop's, and past its socket's length each logs in alone, as a machine's does. A machine's
    Target is one the route marked (_routed)."""
    if target.is_host:
        # What made the Target a host's is held again: one whose identity was set to None in place after it was made
        # a machine's would otherwise skip the route's mark and dial by its first use.
        _host_refusal(target)
        socket = _host_socket(target)
        words = ["-o", "UpdateHostKeys=no", "-o", "ForwardAgent=no"]
    else:
        _routed(target)
        socket = f"{target.control_dir}/{_control_name(target)}-%r-%p"
        words = shlex.split(checks.vm_ssh_args(target.identity, target.known_hosts))
    alone = forward or target.first_use or not target.shared
    words += ["-o", "ControlPath=none" if alone else f"ControlPath={socket}"]
    words += _pinned(target, forward)
    if target.identity_file is not None:
        words += ["-o", f"IdentityFile={checks.vm_ssh_word(target.identity_file)}"]
    if target.hop is not None:
        words += ["-o", f"ProxyCommand={target.hop.proxy_command}"]
    return words + given_words(target)


def _command(command: object) -> str:
    if isinstance(command, (str, bytes)) or not isinstance(command, Sequence) or not command:
        raise Refused("a command is a list of words, the program first")
    if not all(isinstance(word, str) and "\0" not in word for word in command):
        raise Refused("each word of a command is text with no NUL in it")
    return shlex.join(command)


def _place(target: Target) -> list[str]:
    return ["-l", target.account, "-p", str(target.port), "--", target.address]


def argv(target: Target, command: Sequence[str]) -> list[str]:
    """The whole command line that runs command on target: ssh, its options (options), the account and the port, then
    --, the address and the command as one word."""
    return [target.ssh, *options(target), *_place(target), _command(command)]


def _far_end(seconds: object) -> list[str]:
    """What a forward runs at its far end: sleep for seconds, rounded up, so that the local end, ended at seconds, ends
    it first, and the far end ends it where nothing here can."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 0 < seconds <= FORWARD_MOST:
        raise Refused(f"a forward's deadline is a number of seconds, more than 0 and at most {FORWARD_MOST}")
    return ["sleep", str(math.ceil(seconds))]


def forward_argv(target: Target, specs: Sequence[str], seconds: float) -> list[str]:
    """The command line of a forward to target for seconds: ssh, a forward's options, each of specs as one -L, and the
    far end's command, sleep for seconds, which ends the forward by itself where the run that held it was killed, once
    the connections through it have closed."""
    if isinstance(specs, (str, bytes)) or not isinstance(specs, Sequence) or not specs:
        raise Refused("a forward is given one -L spec or more")
    for spec in specs:
        if not isinstance(spec, str) or not _FORWARD.fullmatch(spec):
            raise Refused("a forward's spec is [address:]port:host:port, an IPv6 address in brackets")
    forwards = [word for spec in specs for word in ("-L", spec)]
    return [target.ssh, *options(target, forward=True), *forwards, *_place(target), _command(_far_end(seconds))]


def ansible_vars(target: Target) -> dict[str, object]:
    """How layer 1's Ansible child reaches target as one inventory host: its address, port, account and ssh program,
    and ansible_ssh_common_args holding exactly the options argv gives, in the same order, so that its ssh, sftp and
    scp ride the same master and a jump of the person's reaches the file transfers too."""
    return {
        "ansible_host": target.address,
        "ansible_port": target.port,
        "ansible_user": target.account,
        "ansible_ssh_executable": target.ssh,
        "ansible_ssh_common_args": shlex.join(options(target)),
    }


# ---------------------------------------------------------------- the bounds and the results ----


@dataclasses.dataclass(frozen=True)
class Limits:
    """What a child may take: `line` bytes in one line of its standard output, `most` in all of it, `seconds` from its
    start to its end, `silent` seconds to say anything at all (None: no such limit), and `said` bytes of its standard
    error kept."""

    line: int = LINE_BYTES
    most: int = 4 * LINE_BYTES
    seconds: float = SECONDS
    silent: float | None = START_SECONDS
    said: int = 64 * 1024

    def __post_init__(self) -> None:
        for name in ("line", "most", "said"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise Refused(f"the limit {name} is a number of bytes, 1 or more")
        for name in ("seconds", "silent"):
            value = getattr(self, name)
            if value is None and name == "silent":
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not value > 0:
                raise Refused(f"the limit {name} is a number of seconds, more than 0")


# Today's: a line of 16 MiB, 45 s in all, 20 s to say a first word, 64 KiB of standard error (status/reach.py's), and
# four of the longest lines in all.
LIMITS = Limits()


@dataclasses.dataclass(frozen=True)
class Done:
    """The child ended by itself: its exit status (negative, the signal that ended it), the lines it printed and what
    it said on its standard error, all plain."""

    status: int
    lines: tuple[str, ...]
    said: str


@dataclasses.dataclass(frozen=True)
class Cut:
    """The child sent more than it may - a line past its bound, more than the whole, a line with no end - and was
    ended: why, the lines read before, and what it said. Its local ssh was ended, never the command on the machine,
    which goes on where it neither reads nor writes."""

    why: str
    lines: tuple[str, ...]
    said: str


@dataclasses.dataclass(frozen=True)
class Silent:
    """The child said nothing within its time to say anything, and was ended: its local ssh, never the command on the
    machine, which goes on where it neither reads nor writes."""

    said: str


@dataclasses.dataclass(frozen=True)
class TimedOut:
    """The child outlived its deadline, and was ended: the lines read before, and what it said. Its local ssh was
    ended, never the command on the machine, which goes on where it neither reads nor writes."""

    lines: tuple[str, ...]
    said: str


@dataclasses.dataclass(frozen=True)
class Unreachable:
    """ssh exited 255: its own failure - it could not reach the machine, or lost it - or the command's own 255, or a
    signal that ended the command, which ssh has no status to pass on for. What it said, and the lines the command
    printed. Which of them it was cannot be told: a command that ran and printed nothing reads as a machine never
    reached, and one that printed may go on after a connection lost. So it never says that a command did not run: a
    caller whose command changes something reads the machine's state again before it tries the machine again."""

    said: str
    lines: tuple[str, ...] = ()


Result = Done | Cut | Silent | TimedOut | Unreachable

# How much of a child's output is read at a time, and how long a child ended with SIGTERM has before SIGKILL.
CHUNK = 64 * 1024
END_GRACE = 2.0
# How long what a child said on its standard error is waited for once it has ended.
SAID_GRACE = 1.0


Input = bytes | str | Secret | Sequence[bytes | str | Secret] | None


def _input(given: object) -> None:
    """given held to what a child's standard input takes: bytes, text, a Secret, a list of them, or nothing."""
    parts = given if isinstance(given, (list, tuple)) else [given]
    if not all(isinstance(part, (bytes, str, Secret)) for part in parts) and given is not None:
        raise Refused("standard input is bytes, text, a Secret or a list of them")


def _bytes(given: Input) -> bytes:
    """What a child's standard input is sent: the one place a Secret's value is read, written to the child and never
    kept."""
    if given is None:
        return b""
    parts = given if isinstance(given, (list, tuple)) else [given]
    out = bytearray()
    for part in parts:
        if isinstance(part, Secret):
            out += part.reveal().encode()
        elif isinstance(part, str):
            out += part.encode()
        else:
            out += part
    return bytes(out)


def _kill(process: Process, signum: int = signal.SIGKILL) -> None:
    """process's group signalled, while process has not been reaped: the group is its own (children.GROUP), and its pid
    is no other process's until it is."""
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signum)


async def _within(awaitable, seconds: float):
    """awaitable's result, or TimeoutError once seconds have passed."""
    import asyncio

    if seconds <= 0:
        task = asyncio.ensure_future(awaitable)
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            raise TimeoutError
        return task.result()
    return await asyncio.wait_for(awaitable, seconds)


async def _send(stream: StreamWriter | None, given: Input) -> None:
    """given written to a child's standard input, drained, and the input closed; a child that ended first is no
    error."""
    if stream is None:
        return
    try:
        data = _bytes(given)
        if data:
            stream.write(data)
            await stream.drain()
        del data
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        stream.close()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await stream.wait_closed()


async def _said(stream: StreamReader | None, most: int) -> bytes:
    """A child's standard error read to its end, the first most bytes kept: it never blocks on a full pipe."""
    if stream is None:
        return b""
    kept = bytearray()
    while chunk := await stream.read(CHUNK):
        kept += chunk[: max(most - len(kept), 0)]
    return bytes(kept)


async def _collected(saying) -> str:
    """What a child said on its standard error, plain, once it has ended: waited for SAID_GRACE at most."""
    import asyncio

    try:
        said = await asyncio.wait_for(asyncio.shield(saying), SAID_GRACE)
    except TimeoutError:
        saying.cancel()
        said = b""
    return text.plain_text(said.decode("utf-8", "replace")).strip()


class _Output:
    """A child's standard output read a line at a time under its limits: each line plain, kept in `lines`; `cut` says
    why the reading stopped where the child sent more than it may; `spoke` whether it said anything at all, which ends
    its time to say anything (Limits.silent)."""

    def __init__(self, stream: StreamReader | None, limits: Limits) -> None:
        self._stream = stream
        self._limits = limits
        self._buffer = bytearray()
        self._total = 0
        self._ended = stream is None
        self.spoke = False
        self.cut: str | None = None
        self.lines: list[str] = []

    async def next(self, wait: float | None) -> str | None:
        """The next line, or None at the end of the output or where it was cut; TimeoutError where wait seconds
        pass first."""
        limits = self._limits
        while self.cut is None:
            at = self._buffer.find(b"\n")
            if at >= 0:
                raw = bytes(self._buffer[:at])
                del self._buffer[: at + 1]
                if len(raw) > limits.line:
                    self.cut = f"it sent a line longer than {limits.line} bytes"
                    break
                line = text.plain_text(raw.decode("utf-8", "replace"))
                self.lines.append(line)
                return line
            if len(self._buffer) > limits.line:
                self.cut = f"it sent a line longer than {limits.line} bytes"
                break
            if self._ended:
                if self._buffer:
                    self.cut = "it sent a line with no end"
                return None
            assert self._stream is not None
            chunk = await (self._stream.read(CHUNK) if wait is None else _within(self._stream.read(CHUNK), wait))
            if not chunk:
                self._ended = True
                continue
            self.spoke = True
            self._total += len(chunk)
            if self._total > limits.most:
                self.cut = f"it sent more than {limits.most} bytes"
                break
            self._buffer += chunk
        return None


def _result(process: Process, output: _Output, said: str, timed_out: bool = False, silent: bool = False) -> Result:
    lines = tuple(output.lines)
    if silent:
        return Silent(said)
    if timed_out:
        return TimedOut(lines, said)
    if output.cut is not None:
        return Cut(output.cut, lines, said)
    if process.returncode == 255:
        return Unreachable(said, lines)
    assert process.returncode is not None
    return Done(process.returncode, lines, said)


# ---------------------------------------------------------------- a bounded call ----


async def run(
    job: Job,
    target: Target,
    command: Sequence[str],
    stdin: Input,
    limits: Limits,
    env: Mapping[str, str],
    started: Callable[[Process], None] | None = None,
) -> Result:
    """command run on target as a child of job's run, under limits: started through children.start in a process group
    of its own with env and the job's marker, entered in the run's registry as an ssh child; started(process) called;
    stdin written, drained and closed; its standard output read a line at a time; on a breach its group killed. Every
    value is held before anything starts."""
    import asyncio

    words = argv(target, command)
    _input(stdin)
    if not isinstance(limits, Limits):
        raise Refused("limits is a Limits")
    pipe = asyncio.subprocess.PIPE
    process = await children.start(job, words, children.GROUP, env, pipe, pipe, pipe, children.SSH)
    sending = saying = None
    try:
        if started is not None:
            started(process)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limits.seconds
        quiet = None if limits.silent is None else loop.time() + limits.silent
        sending = asyncio.ensure_future(_send(process.stdin, stdin))
        saying = asyncio.ensure_future(_said(process.stderr, limits.said))
        output = _Output(process.stdout, limits)
        timed_out = silent = False
        while True:
            until = deadline if quiet is None or output.spoke else min(deadline, quiet)
            try:
                line = await output.next(until - loop.time())
            except TimeoutError:
                silent = not output.spoke and quiet is not None and quiet < deadline
                timed_out = not silent
                break
            if line is None:
                break
        if not (timed_out or silent or output.cut):
            try:
                await _within(process.wait(), deadline - loop.time())
            except TimeoutError:
                timed_out = True
        _kill(process)
        await process.wait()
        said = await _collected(saying)
        return _result(process, output, said, timed_out=timed_out, silent=silent)
    finally:
        # However the call ends - a breach, its caller cancelled, started raising - nothing of it is left running.
        _kill(process)
        await process.wait()
        pending = [task for task in (sending, saying) if task is not None and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending)


# ---------------------------------------------------------------- a held child ----


class Held:
    """A child the run holds while a sign-in waits on it: a line sent to its standard input when the caller has one, its
    output read while it runs, its end the caller's, its deadline's or the run's (children.py's registry)."""

    def __init__(self, process: Process, limits: Limits, deadline: float | None) -> None:
        import asyncio

        loop = asyncio.get_running_loop()
        self.process = process
        self._output = _Output(process.stdout, limits)
        self._saying = asyncio.ensure_future(_said(process.stderr, limits.said))
        self._timed_out = False
        self._timer = None if deadline is None else loop.call_later(deadline, self._expire)

    def _expire(self) -> None:
        if self.process.returncode is None:
            self._timed_out = True
            _kill(self.process)

    async def send(self, line: str | Secret) -> None:
        """One line written to the child's standard input, a Secret's value revealed only there; Ended where the child
        has ended or its input is closed or not held."""
        stream = self.process.stdin
        if stream is None or stream.is_closing() or self.process.returncode is not None:
            raise Ended("the held child has ended, or its standard input is closed: nothing was sent")
        if not isinstance(line, (str, Secret)):
            raise Refused("a line sent is text or a Secret")
        data = _bytes(line)
        if b"\n" in data or b"\r" in data:
            del data
            raise Refused("one line is sent at a time: what was given holds a line break, and nothing was sent")
        try:
            stream.write(data + b"\n")
            await stream.drain()
        except (BrokenPipeError, ConnectionResetError):
            raise Ended("the held child has ended: nothing more was sent") from None
        finally:
            del data

    async def lines(self) -> AsyncIterator[str]:
        """What the child prints, a line at a time while it runs, plain, under its limits; to its end, its cut or its
        deadline, which ends it."""
        while (line := await self._output.next(None)) is not None:
            yield line
        if self._output.cut is not None:
            _kill(self.process)

    async def close(self) -> None:
        """The child's standard input closed: it reads its end."""
        stream = self.process.stdin
        if stream is not None and not stream.is_closing():
            stream.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await stream.wait_closed()

    async def end(self) -> None:
        """The child's group ended: SIGTERM, then SIGKILL where it has not ended within END_GRACE seconds. That is its
        local ssh, never the command on the machine, which goes on where it neither reads nor writes."""
        import asyncio

        if self._timer is not None:
            self._timer.cancel()
        if self.process.returncode is None:
            _kill(self.process, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.process.wait(), END_GRACE)
            except TimeoutError:
                _kill(self.process)
                await self.process.wait()

    async def result(self) -> Result:
        """How the child ended, once it has: what is left of its output read under the same limits, then its status."""
        async for _ in self.lines():
            pass
        await self.process.wait()
        if self._timer is not None:
            self._timer.cancel()
        said = await _collected(self._saying)
        return _result(self.process, self._output, said, timed_out=self._timed_out)


async def _held(
    job: Job,
    words: list[str],
    stdin: object,
    limits: Limits,
    env: Mapping[str, str],
    started: Callable[[Process], None] | None,
    deadline: float | None,
) -> Held:
    import asyncio

    if not isinstance(limits, Limits):
        raise Refused("limits is a Limits")
    if deadline is not None and (not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or deadline <= 0):
        raise Refused("a held child's deadline is a number of seconds, more than 0, or none")
    pipe = asyncio.subprocess.PIPE
    process = await children.start(job, words, children.GROUP, env, stdin, pipe, pipe, children.SSH)
    try:
        if started is not None:
            started(process)
        return Held(process, limits, deadline)
    except BaseException:
        _kill(process)
        await process.wait()
        raise


async def hold(
    job: Job,
    target: Target,
    command: Sequence[str],
    env: Mapping[str, str],
    started: Callable[[Process], None] | None = None,
    deadline: float | None = None,
    limits: Limits = LIMITS,
) -> Held:
    """command started on target as run starts it, and held: its standard input open for a line written later, its
    output read while it runs under limits' bounds for a line and the whole, ended at deadline seconds (the caller's),
    or by the caller, or at the run's end."""
    import asyncio

    return await _held(job, argv(target, command), asyncio.subprocess.PIPE, limits, env, started, deadline)


async def forward(
    job: Job,
    target: Target,
    specs: Sequence[str],
    env: Mapping[str, str],
    started: Callable[[Process], None] | None = None,
    *,
    deadline: float,
) -> Held:
    """A held ssh to target with each of specs as a -L, ExitOnForwardFailure=yes, made as a connection of its own
    (ControlPath=none), since a forward asked of a shared master would outlive the run on it. It is ended at deadline
    seconds, which a forward requires, by its caller or at the run's end; and its far end is sleep for deadline, so that
    where the run was killed and nothing here can end it, it ends by itself once its deadline has passed and the
    connections through it have closed: ssh ends once its command has and no forwarded connection is open. Its standard
    input is /dev/null: nothing is sent to it."""
    import asyncio

    words = forward_argv(target, specs, deadline)
    return await _held(job, words, asyncio.subprocess.DEVNULL, LIMITS, env, started, deadline)
