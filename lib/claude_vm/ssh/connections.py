"""The one shared control directory, and the route to a host's machines: through the Proxmox host's sshd, or straight
to each machine's address (docs/architecture/engine.md §16.6).

Every shared connection - a machine's master and the hop's - lives in one control directory that every run on the
computer shares (`fleet/paths.py`'s `control_dir()`, `~/.ansible/cp`): short, as a socket's path must be, the account's
alone, outside every repository, and the one layer 1's Ansible child and the status probe use. It is given to
`Connections`, made once a run, as an argument, so that a test gives one of its own, and held to its owner there: one
`lstat` refuses a link, a directory of another account's, or one its group or others may write in, where anyone could
put a socket at a machine's path before its master and be handed what is sent to it (`Unsafe`, with the `chmod` to
run). A machine's socket is named by `checks.vm_control_name` from its identity, its address and its hop, so that a
machine that reports a sibling's address never rides the sibling's master, on which no host key is checked again, and
two hosts with one VM range never share one. Where its path would pass `route.SOCKET_MOST` bytes (a long home), the
machine takes no shared connection (`runner.Target.shared`): each connection logs in alone, `ensure` opens nothing, and
the run says so once (`notes`). So does a connection to the Proxmox host from such a home, whose socket is the hop's,
too long as well; and the route through the host, one shared connection, is refused there (`LONG_HOP`).

Three rules hold every master here. A machine's master is opened only by `ensure`, which starts it with
`children.MASTER`, the one kind of child that carries no job's marker, in a session of its own: other runs may ride it
after this one ends, and nothing that ends a run's children by its marker may end it. The hop's master is `route.py`'s:
`ensure_hop` opens it with the engine's own environment, and so may any connection through the hop whose
`ProxyCommand` finds none live - a bounded child, a first use, a forward - since that `ProxyCommand` carries
`ControlMaster=auto`; such a master carries that child's job's marker. No run ever ends a master, the hop's or a
machine's: it ends by itself, `PERSIST` after its last client, as today's do; two runs never ride one machine's master
at once, since each holds that machine's lock (`runs/locks.py`). And `-O exit` is never sent, which would cut another
run's sessions; `-O stop` only through `route.hop_stop`, for the hop.
`ensure` never opens a master for a first use: its key is not filed yet, and a master opened then would carry later
runs with no key checked; a first use is a connection of its own (`ControlPath=none`, the runner's). `first_use` makes
a machine's Target one where the product's file holds no key for its identity, its scratch file fresh in the run's own
directory: what a changing verb's first connection to a machine calls, and `status`, which takes no key, never.

The route is `route.py`'s, called as it stands: `route` settles it by today's rule, `target_for` gives a machine the
hop's `ProxyCommand` to its own address (`-W <address>:%p`) unless it brings a jump of its own, `held` asks the
machine's own `ssh` which proxy it would take and refuses any other before anything connects, and `ensure_hop` makes
sure of the hop's shared connection, the one place the hop's master is opened; `forward` makes the one forward of its
own through the hop to a machine that takes it, which says whether the host forwards there (`route.hop_forward`). Those
run `route.py`'s `ssh` children in a process group of their own (`process_group=0`), so that a terminal's first Ctrl-C,
which the run takes as "finish the step", never kills one and has its silence read as an answer, and off the event loop
(`asyncio.to_thread`), so that the run takes the Ctrl-C at once and its other tasks go on while one waits; `route`
settles nothing where its `ssh -G` gave no answer (ended by a signal, or out of its time), and refuses the
route through the host from a home so long that the hop's socket could not be bound; and every line the hop's own `ssh`
is run with pins `ForwardX11=no` and `PermitLocalCommand=no` first (`route.hop_settings`' `pinned`), as the runner pins
them for a machine. Nothing here ever dials a machine where the rule goes through the host. The engine settles its route
once a run, and gives every machine its Target, through engine/routes.py alone.

Standard library only; asyncio is imported inside the coroutines that start a child.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import shlex
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, TYPE_CHECKING

from claude_vm import route, text
from claude_vm.fleet import paths
from claude_vm.runs import children, rundir
from claude_vm.ssh import hostkeys, runner

if TYPE_CHECKING:
    from claude_vm.runs.job import Job

# How long a machine's master lives past its last client: today's, ansible.cfg's ControlPersist for a VM. The hop's is
# route.py's own (route.PERSIST).
PERSIST = "60s"
# What a master opened beside a live one says as it goes on as a connection of its own: it is ended then, as route.py
# ends the hop's.
ALREADY = "already exists, disabling multiplexing"
# What -O check says of a socket file that nothing listens at: removed before the open, never one whose check ran out
# its time.
DEAD = "Connection refused"
# How often a child that has not ended is looked at, in seconds, and the most of its output that is kept.
POLL = 0.05
MOST = 64 * 1024


class Unheld(runner.Refused):
    """A machine whose own ssh would not go where the route says: nothing connected to it."""


class Unusable(Exception):
    """The hop through the Proxmox host cannot be used: the sentence says why, and what to change."""


class Unsafe(runner.Refused):
    """The control directory is a link, another account's, or writable by its group or others: nothing was started."""


# What the run says once where a machine, or the Proxmox host, takes no shared connection, its socket's path too long.
UNSHARED = (
    "the control directory {directory} is too long for a shared connection's socket there ({most} bytes at most: 103,"
    " the most macOS binds a socket's path to, less the 17 ssh adds while it binds one): each connection to a machine,"
    " and to the Proxmox host, logs in alone, which is slower"
)
# Why the route through the Proxmox host is refused from a long home, in the new engine's words: route.hop_settings'
# own names a setting of 2.x's inventory.
LONG_HOP = (
    "VMs would be reached through {label}, on one shared connection whose socket, {socket}, would be longer than"
    " {most} bytes, the most a shared connection's socket may be"
)
# How route.py's children are started: in a process group of their own (Popen's process_group).
OWN_GROUP = 0


def _owned(directory: Path) -> None:
    """The control directory held to its owner, by one lstat: Unsafe where it is a link, not a directory, another
    account's, or writable by its group or others, with what to run. One that is not there yet is made 0700 by the first
    master's open."""
    try:
        found = os.lstat(directory)
    except FileNotFoundError:
        return
    except OSError as error:
        raise Unsafe(f"the control directory {directory} cannot be looked at: {error.strerror or error}") from None
    if stat.S_ISLNK(found.st_mode):
        raise Unsafe(
            f"the control directory {directory} is a symbolic link, so every shared connection's socket would be"
            f" wherever it points: remove the link and make it a directory of your own, mkdir -m 700 {directory}"
        )
    if not stat.S_ISDIR(found.st_mode):
        raise Unsafe(f"the control directory {directory} is not a directory: remove it, and mkdir -m 700 {directory}")
    if found.st_uid != os.geteuid():
        raise Unsafe(
            f"the control directory {directory} belongs to another account (uid {found.st_uid}), which could answer at"
            " a machine's socket and be handed what is sent to it: remove it, or make it yours with chown as root"
        )
    if found.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise Unsafe(
            f"the control directory {directory} may be written in by its group or others, who could put a socket at a"
            f" machine's path before its master and be handed what is sent to it: run chmod 700 {directory}"
        )


def _login_said(settled: Mapping[str, object], said: str) -> str:
    """Why the hop's login failed, and the ways out, in the new engine's names: route.login_said's sentence, with this
    computer's ssh_identity_file where route.py names an inventory's variable."""
    hop = settled["hop"]
    assert isinstance(hop, Mapping)
    setting = settled.get("setting", "auto")
    tail = (
        " Where a VPN routes the VMs' network, or a jump of your own reaches them, set vm_ssh_route: direct: under"
        " auto, a host reached on a tailnet is a hop."
        if setting == "auto"
        else ""
    )
    return (
        f"VMs are reached through {hop['label']} (vm_ssh_route: {setting}), and ssh could not log in there:"
        f" {route.ssh_said(said).rstrip('.') or 'no reason given'}. Its key may be missing from the agent and from"
        " this computer's ssh_identity_file, the host's key may have changed, or the login may be refused:"
        f" `{route.hop_login_line(dict(hop))}` here shows which.{tail}"
    )


@dataclasses.dataclass(frozen=True)
class Master:
    """A master made sure of: `ok` where one answers at its socket, `opened` where this call opened it, and `said`,
    why not, plain, where none answers. Where no master is used - a machine that takes no shared connection
    (runner.Target.shared false: each of its connections logs in alone, as `notes` says), or the hop where the route is
    direct - there is none to make sure of, and no Master: ensure and ensure_hop answer None."""

    ok: bool
    opened: bool
    said: str


@dataclasses.dataclass(frozen=True)
class _Ended:
    status: int | None
    said: str
    out: str
    killed: bool


def socket_path(target: runner.Target) -> Path:
    """Where a machine's master listens: the shared directory, its control name, and ssh's -%r-%p made of its account
    and port, as the runner names it."""
    if target.is_host:
        raise runner.Refused("the Proxmox host's socket is the hop's, which route.hop_settings names")
    return target.control_dir / f"{runner._control_name(target)}-{target.account}-{target.port}"


def first_use(job: Job, target: runner.Target) -> runner.Target:
    """target as its run's first use where the known-hosts file it names, the product's, holds no key under its
    identity (hostkeys.kept): its key then taken on a connection of its own into a fresh scratch file in the run's own
    directory (rundir.made), never by its address; target as it is where a key is filed. What a changing verb's first
    connection to a machine calls, and status, which takes no key, never. The caller files the key it took
    (hostkeys.taken, then hostkeys.file), and only then makes sure of the machine's master (ensure)."""
    if not isinstance(target, runner.Target) or target.is_host or target.first_use:
        raise runner.Refused("a first use is decided for a machine's Target that checks the product's known-hosts file")
    if hostkeys.kept(target.known_hosts, str(target.identity)):
        return target
    directory = rundir.made(job)
    if directory is None:
        raise runner.Refused(f"job {job.id} has no run directory, where a first use's scratch file goes: rundir.make")
    scratch = hostkeys.fresh(directory, str(target.identity))
    return dataclasses.replace(target, known_hosts=scratch, first_use=True)


def _read(stream: IO[bytes]) -> str:
    # pread, which moves no offset: ssh writes through the same open file.
    return os.pread(stream.fileno(), MOST, 0).decode("utf-8", "replace")


def _shown(lines: str) -> str:
    """The jump ssh -G shows, as `Option value`: its ProxyJump, else a ProxyCommand that is not none; "" for none."""
    shown = {}
    for line in lines.splitlines():
        word, _, value = line.strip().partition(" ")
        if word.lower() in ("proxycommand", "proxyjump"):
            shown[word.lower()] = value.strip()
    if shown.get("proxyjump", "none").lower() != "none":
        return f"ProxyJump {shown['proxyjump']}"
    if shown.get("proxycommand", "none").lower() != "none":
        return f"ProxyCommand {shown['proxycommand']}"
    return ""


class Connections:
    """A run's connections: the one shared control directory, and the environment its children are started with.
    Made once a run."""

    def __init__(self, control_dir: Path | str, env: Mapping[str, str]) -> None:
        self.control_dir = runner._directory(control_dir)
        _owned(self.control_dir)
        if not isinstance(env, Mapping):
            raise runner.Refused("a run's environment is a mapping of names to values")
        self.env = dict(env)
        # What each machine's own ssh configuration gives it, read once a run: its jump, or "".
        self._configured: dict[tuple[object, ...], str] = {}
        # What the run says once of its connections, each sentence at most once, for its workflow to say: a machine
        # that takes no shared connection, its socket's path too long (UNSHARED).
        self.notes: list[str] = []

    def _own(self, target: runner.Target) -> None:
        if not isinstance(target, runner.Target):
            raise runner.Refused("a connection is made from a runner.Target")
        if target.control_dir != self.control_dir:
            raise runner.Refused(
                f"every shared connection lives in the run's one control directory, {self.control_dir}, and this"
                f" target names {target.control_dir}"
            )
        if not target.shared:
            note = UNSHARED.format(directory=self.control_dir, most=route.SOCKET_MOST)
            if note not in self.notes:
                self.notes.append(note)

    # ------------------------------------------------------------ the route ----

    async def route(self, setting: str, host: runner.Target, *, jump: str = "", name: str = "") -> dict:
        """The run's route to host's machines, `{route, setting, hop, policy, addresses}` as route.py's callers keep
        it: route.hop_settings of the Proxmox host's own Target - its account, or the account `jump` (vm_ssh_jump)
        names, its port, key and ssh, its socket in the shared directory, labelled with the host's `name` - and
        route.route_settle's answer for `setting`, its ssh -G in a process group of its own and off the loop. The hop
        is pinned (route.hop_settings' `pinned`): its own ssh never hands the host this computer's display, nor runs a
        program here. route.HopRefused, as it is, for a setting of the hop that is not one plain word, which names
        vm_ssh_route: direct, under auto only where the rule would go through the host; under auto where the ssh -G
        gave no answer (ended by a signal, or out of its time), which settles nothing; and where the route goes through
        the host and the hop's socket would pass route.SOCKET_MOST (a long home), in this engine's words (LONG_HOP),
        naming vm_ssh_route: direct or a shorter home."""
        import asyncio

        self._own(host)
        if not host.is_host:
            raise runner.Refused("the route is settled for the Proxmox host's own Target (runner.proxmox_host)")
        setting = "auto" if setting in (None, "") else setting
        if setting not in route.ROUTES:
            raise runner.Refused(f"vm_ssh_route is one of {', '.join(route.ROUTES)}, not {setting!r}")
        return await asyncio.to_thread(self._settle, setting, host, jump, name)

    def _settle(self, setting: str, host: runner.Target, jump: str, name: str) -> dict:
        # The hop's host key is the Proxmox host's own, taken at first use into the person's own known_hosts.
        none = {"route": "direct", "setting": setting, "hop": {}, "policy": runner._policy(host), "addresses": []}
        if setting == "direct":
            return none
        try:
            account = route.account(jump)
            hop = route.hop_settings(
                ssh=host.ssh,
                user=account or host.account,
                host=host.address,
                port=host.port,
                key=str(host.identity_file or ""),
                control_dir=str(self.control_dir),
                name=name,
                sized=False,
                pinned=True,
            )
        except route.HopRefused:
            # Under auto, a hop that could not be made matters only where the rule would go through it.
            if setting == "auto" and route.route_rule("auto", route.addresses_of(host.address)) == "direct":
                return none
            raise
        settled = route.route_settle(setting, hop, process_group=OWN_GROUP, signalled_settles=False)
        if settled["route"] != "host":
            return {**none, "addresses": settled["addresses"]}
        if not route.socket_fits(hop["socket"]):
            # The hop is one shared connection, its master and every machine's ProxyCommand on one socket: none can be
            # bound past the length, so the route through the host is refused, never taken without one.
            raise route.HopRefused(
                LONG_HOP.format(label=hop["label"], socket=hop["socket"], most=route.SOCKET_MOST),
                "set vm_ssh_route: direct where this computer reaches the VMs' network, or run from an account whose"
                " home is shorter",
            )
        return {**none, "route": "host", "hop": hop, "addresses": settled["addresses"]}

    async def ensure_hop(self, settled: Mapping[str, object]) -> Master | None:
        """The hop's shared connection made sure of, route.hop_held as it stands, its ssh in a process group of its own
        and off the loop: `-O check` first, a dead socket file removed, the open of its master or nothing. Unusable,
        with route.login_said's sentence in the new engine's names, where it cannot be made: the run stops, and nothing
        dials a machine. None where the route is direct: no master is used, and none is made sure of. The one place
        the hop's master is opened, so only where the route goes through the host. route.py
        starts the hop's ssh with this process's own environment, which carries no job's marker: a run marks only its
        children (children.start); while its open runs a run remembers nothing of its tree (children.opening)."""
        import asyncio

        if not isinstance(settled, Mapping) or settled.get("route") not in route.ROUTES:
            raise runner.Refused("a settled route is what Connections.route returns")
        if settled["route"] != "host":
            return None
        hop = settled["hop"]
        if not isinstance(hop, Mapping) or os.path.dirname(str(hop.get("socket"))) != str(self.control_dir):
            raise runner.Refused(f"the hop's socket lives in the run's one control directory, {self.control_dir}")
        held = await asyncio.to_thread(route.hop_held, dict(hop), str(settled["policy"]), process_group=OWN_GROUP)
        if not held["ok"]:
            raise Unusable(_login_said(settled, held["said"]))
        return Master(ok=True, opened=bool(held["opened"]), said="")

    async def forward(self, target: runner.Target, settled: Mapping[str, object]) -> dict:
        """One forward of its own through the hop to target's address at its port, as route.hop_forward makes it - a
        connection of its own (ControlPath=none), its standard input closed, at LogLevel=VERBOSE, under its hard limit -
        its ssh in a process group of its own and off the loop: what it says of the hop (route.forward_verdict's
        `{usable, why, said, banner}`), and of the far end's words only whether they begin as an SSH server's do. Only
        for a machine that takes the hop, where the route goes through the host: what a run asks once, of the first
        such machine by name, before any connection to one."""
        import asyncio

        self._own(target)
        if target.is_host or target.hop is None:
            raise runner.Refused("a forward through the hop is made for a machine that takes it (target_for)")
        if not isinstance(settled, Mapping) or settled.get("route") != "host":
            raise runner.Refused("a forward through the hop is made where the settled route goes through the host")
        hop = settled["hop"]
        if not isinstance(hop, Mapping) or os.path.dirname(str(hop.get("socket"))) != str(self.control_dir):
            raise runner.Refused(f"the hop's socket lives in the run's one control directory, {self.control_dir}")
        return await asyncio.to_thread(
            route.hop_forward,
            dict(hop),
            target.address,
            str(settled["policy"]),
            port=target.port,
            process_group=OWN_GROUP,
        )

    # ------------------------------------------------------------ a machine's target ----

    async def target_for(
        self,
        job: Job,
        machine: runner.Target,
        settled: Mapping[str, object],
        given: Mapping[str, str] | Sequence[tuple[str, str]] = (),
    ) -> runner.Target:
        """machine's Target for the settled route, with the person's own options for it (`given`, vm_ssh_options): the
        hop's ProxyCommand to its own address where the route goes through the host (route.vm_hop), and none where it
        is direct or where the machine brings a jump of its own - a ProxyJump, a -J or a ProxyCommand other than none
        in `given`, or in what ssh -G resolves for its address from the person's own ~/.ssh/config, read once a run for
        each machine. `machine` is a machine's Target with neither a hop nor options of its own: they are given here."""
        self._own(machine)
        if machine.is_host or machine.hop is not None or machine.given:
            raise runner.Refused("target_for takes a machine's Target with no hop and no vm_ssh_options: it gives both")
        if not isinstance(settled, Mapping) or settled.get("route") not in route.ROUTES:
            raise runner.Refused("a settled route is what Connections.route returns")
        pairs = tuple(given.items()) if isinstance(given, Mapping) else tuple(given)
        candidate = dataclasses.replace(machine, given=pairs)
        if settled["route"] != "host":
            return candidate
        found = route.vm_hop(dict(settled), candidate.address, runner.given_words(candidate))
        if found["jump"] or await self._configured_jump(job, candidate):
            return candidate
        [_, option] = shlex.split(found["option"])
        return dataclasses.replace(candidate, hop=runner.Hop(option.removeprefix("ProxyCommand="), found["name"]))

    def _key(self, target: runner.Target) -> tuple[object, ...]:
        return (target.ssh, target.address, target.account, target.port, target.given)

    async def _configured_jump(self, job: Job, target: runner.Target) -> str:
        """The jump the person's own ssh configuration gives target's address, as ssh -G resolves it with the person's
        own options for it on the line, -l and -p: read once a run for each machine. runner.Refused, with ssh's words,
        where ssh could not read it: the connection would read the same configuration."""
        key = self._key(target)
        if key not in self._configured:
            words = [target.ssh, "-G", *runner.given_words(target), "-l", target.account, "-p", str(target.port)]
            ended = await self._child(job, [*words, "--", target.address], route.LIMITS["config"])
            if ended.status != 0:
                why = ended.said or (f"no answer within {route.LIMITS['config']} s" if ended.killed else "")
                raise runner.Refused(
                    f"ssh -G could not read your ssh configuration for {target.address}: "
                    f"{why or f'it exited {ended.status}'}"
                )
            self._configured[key] = _shown(ended.out)
        return self._configured[key]

    async def held(self, target: runner.Target, settled: Mapping[str, object]) -> None:
        """Unheld where target's own ssh, asked with its whole line and no configuration file (route.ssh_shown, -G -F
        /dev/null, in a process group of its own and off the loop), would not go where the route says: through the
        host, the hop's command word for word to its own address and nothing else; with a jump of its own, that jump -
        on the line, or where only the person's own configuration gives it, as target_for read it this run. Under
        direct, a target that takes the hop. Asked before anything connects to it; nothing is dialled here."""
        import asyncio

        self._own(target)
        if target.is_host:
            raise runner.Refused("the Proxmox host is the hop: held asks a machine's ssh")
        if not isinstance(settled, Mapping) or settled.get("route") not in route.ROUTES:
            raise runner.Refused("a settled route is what Connections.route returns")
        name = f"{target.identity} at {target.address}"
        if settled["route"] != "host":
            if target.hop is not None:
                raise Unheld(f"{name} takes the hop through the Proxmox host, where the route is direct")
            return
        takes_hop = target.hop is not None
        words = [*runner.options(target), "-l", target.account, "-p", str(target.port), "--"]
        shown = await asyncio.to_thread(route.ssh_shown, target.ssh, words, target.address, process_group=OWN_GROUP)
        why = route.vm_route_refusal(dict(settled), target.address, takes_hop, shown)
        if why and shown["ok"] and not takes_hop and self._configured.get(self._key(target)):
            # The jump is the person's own configuration's, which the line does not name and so cannot outrank.
            why = ""
        if why:
            hop = settled["hop"]
            label = hop.get("label") if isinstance(hop, Mapping) else "the Proxmox host"
            raise Unheld(
                f"VMs are reached through {label}, and {name}: {why}. Nothing was dialled: give a jump of your own in"
                " vm_ssh_options, or set vm_ssh_route: direct."
            )

    # ------------------------------------------------------------ a machine's master ----

    async def ensure(self, job: Job, target: runner.Target) -> Master | None:
        """target's master made sure of before anything rides it: `-O check` on its socket; where nothing answers, a
        socket file that nothing listens at removed, and the master opened - `ControlMaster=yes`, `ControlPersist`,
        `-N` and the rest of the runner's options - in a session of its own and without the job's marker
        (`children.MASTER`), once, and checked once more. Never for a first use, whose key is not filed yet: a master
        opened then would carry later runs with no key checked. A machine through the hop rides the hop's master, made
        sure of first (ensure_hop). Nothing here ends a master. A machine that takes no shared connection
        (runner.Target.shared, its socket's path too long) has none to make sure of: None, nothing started, its
        connections each its own, as notes says once. The Proxmox host is
        refused: its master is the hop's, opened by ensure_hop alone, where the route goes through it."""
        self._own(target)
        if target.is_host:
            raise runner.Refused("the Proxmox host's master is the hop's: ensure_hop")
        if target.first_use:
            raise runner.Refused(
                "a first use is a connection of its own: no master is opened for a machine until its key is filed"
            )
        if not target.shared:
            return None
        socket = socket_path(target)
        place = ["-l", target.account, "-p", str(target.port), "--", target.address]
        check = [target.ssh, *runner.options(target), "-O", "check", *place]
        try:
            # Ansible's control directory, as Ansible and route.py make it: 0700, its owner's alone.
            os.makedirs(self.control_dir, mode=paths.DIR_MODE, exist_ok=True)
        except OSError as error:
            return Master(False, False, f"the control directory {self.control_dir}: {error.strerror or error}")
        checked = await self._child(job, check, route.LIMITS["check"])
        if checked.status == 0:
            return Master(True, False, "")
        if not checked.killed and DEAD in checked.said:
            try:
                socket.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                return Master(False, False, f"{socket}: {error.strerror or error}")
        options = runner.options(target)
        at = options.index("ControlMaster=no")
        options[at : at + 1] = ["ControlMaster=yes", "-o", f"ControlPersist={PERSIST}"]
        opened = await self._child(
            job, [target.ssh, *options, "-N", *place], route.LIMITS["open"], master=True, until=ALREADY
        )
        if (await self._child(job, check, route.LIMITS["check"])).status == 0:
            return Master(True, opened.status == 0, "")
        said = (
            opened.said
            or (f"no answer within {route.LIMITS['open']} s" if opened.killed else "")
            or f"no connection answered at {socket} after ssh made one there (it exited {opened.status})"
        )
        return Master(False, False, said)

    async def _child(self, job: Job, argv: list[str], limit: float, *, master: bool = False, until: str = "") -> _Ended:
        """argv run as a child of job's run under a hard limit: an ssh of the runner's kind in a group of its own, or,
        for a master, the one kind with no marker, in a session of its own. Its standard input /dev/null, its output and
        standard error kept in files, so that a master or a ProxyCommand that keeps them open never holds a pipe; its
        group killed at `limit` seconds, or once its standard error holds `until`. A master leaves that group as it goes
        to the background, so nothing that is killed here is a master."""
        import asyncio
        import tempfile

        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as errors:
            own, kind = (children.SESSION, children.MASTER) if master else (children.GROUP, children.SSH)
            process = await children.start(job, argv, own, self.env, asyncio.subprocess.DEVNULL, out, errors, kind)
            killed = False
            try:
                loop = asyncio.get_running_loop()
                end = loop.time() + limit
                while process.returncode is None:
                    left = end - loop.time()
                    if left <= 0 or (until and until in _read(errors)):
                        killed = True
                        break
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(process.wait(), min(POLL, left))
            finally:
                runner._kill(process)
                await process.wait()
            said = text.plain_text(_read(errors)).replace("\r", "").strip()
            return _Ended(None if killed else process.returncode, said, _read(out), killed)
