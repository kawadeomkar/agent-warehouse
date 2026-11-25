"""How a run reaches its host's machines: the route settled once a run, the hop's login made once, and every machine's
SSH arguments made by one function (docs/architecture/engine.md §16.4).

`settle(run, host)` works out the route to the machines of the host a run is against (engine/fleet.py's `Host`) by
today's rule, route.py's as it stands, called through ssh/connections.py: the setting `vm_ssh_route` as this computer
keeps it for that host (client.yml's map `hosts`, never another host's, never a fleet's file), `direct` or `host` as
it says, and under `auto` `host` where every address `ssh` dials for the host is a tailnet one - the host as its own
`ssh -G` resolves it, a `HostName` of the person's among it, then that name's addresses here - worked out at each run
and never kept. The hop is the Proxmox host as `ssh` reaches it: host.yml's address, its `ssh_user` (or `jump`, another
account there) and `ssh_port`, and this computer's `ssh_identity_file` and `ssh_executable` for it; `host_target` is the
one function that makes the host's own `Target`, for the hop and for root SSH alike. Where the route goes through the
host and `vm_ssh_options` for every machine names no jump of its own, the hop's login is made there, before anything
else of the run; otherwise it is put off to the first machine that takes the hop, and never made where none does. A hop
that cannot be used stops the run (`Unusable`, a `Refused`), with the sentence that names `vm_ssh_route`, and nothing
dials a machine: there is no fallback either way.

`Route.targets(machines)` is the one function that makes a machine's SSH arguments, a `runner.Target` for each: its
identity, its one address, the account a run logs in as, this computer's `ssh_executable`, the person's own
`vm_ssh_options` for it (client.yml's for that machine, else for every machine, else the run's), and, where the route
goes through the host and the machine brings no jump of its own - a `ProxyJump`, a `-J` or a `ProxyCommand` other than
`none` among those options, or in what `ssh -G` resolves for its address from the person's own `~/.ssh/config` - the
hop's `ProxyCommand` to its own address (connections.py's `target_for`), which marks each `Target` with the route it was
given (`routed_as`: `direct`, `hop` or `own`): the runner connects to no machine's `Target` unmarked, nor to one marked
through the hop that has lost it. Only a Proxmox VM (fleet.KIND) takes the hop. Before anything connects to one, each
machine's own `ssh` is asked, with its whole line and no configuration file, which proxy it would take, and any other
than the route's is refused (connections.py's `held`): every machine first, then the hop's login, then the forward.
Where any takes the hop, the hop's login is made, once a run, and one forward of its own to the first of them by name
checks that the host forwards to the machines (route.hop_forward, a connection of its own under its hard limit): a
host that refuses to forward, or an `ssh` that cannot use it as a hop, stops the run, and every later `targets` of the
run raises it again, as does one that waited on that forward; a machine that says nothing to it, or a login for the
forward that does not finish, is said once and stops nothing, the machine failing alone when it is first reached.
Nothing here dials a machine itself, and no module of the new engine but this one and ssh/'s own makes a `Target` or the
connections that give one, or changes a `Target`'s route after it was made (tests/unit/test_aw_routes.py holds it, by a
scan).

`Route.ready()` makes sure of the hop's shared connection again before a step that reaches many machines at once, and
after a wait on a person, wherever the login was made: `-O check`, and an open where none answers; it fails no
machine, since a hop that is gone shows as each machine not answering. The run says once, as its report, how the
machines are reached through the host, each machine reached by a jump of its own, and each of the connections' notes (a
socket's path too long for a shared connection). Every `ssh` for the hop itself is route.py's, under its limits, in a
process group of its own and off the loop; never `-O exit`. `Route.proxy_jump` is the hop as an alias's `ProxyJump`
names it, only once its login was made: an alias gains it only through a hop that was checked.

Standard library, ssh/, fleet/, schema/ and today's route.py at import; asyncio inside the coroutines.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING

from .. import route
from ..fleet import overrides, paths
from ..schema.printer import said
from ..schema.words import Line
from ..ssh import connections, runner
from ..text import one_line
from . import fleet

if TYPE_CHECKING:
    import asyncio

    from ..runs.attached import Context

__all__ = ["Refused", "Route", "Unusable", "host_target", "settle"]

# The settings the route is made from: how this computer reaches the host's machines, and the person's own options for
# a machine's connections.
SETTING = "vm_ssh_route"
OPTIONS = "vm_ssh_options"
# What one forward through the hop says of it: the host refused to forward, or ssh could not use it as a hop, stops
# the run; a machine that said nothing, or a login for the forward that did not finish, is said and stops nothing.
STOPS = ("prohibited", "failed")
SAYS = ("silent", "stalled")
# The run's one line about its route, said once where it goes through the host.
THROUGH = (
    "VMs are reached through {label}. A VM that does not answer shows as a closed connection; {status} says what the"
    " host reports for it."
)
# A machine that brings a jump of its own, said once: where the jump is, the options or the person's configuration.
OWN_JUMP = "{name} is reached by the jump it is given ({jump}), not through {label}."
CONFIGURED = "from your ssh configuration"
# Why the host refused to forward, in this engine's words: route.forward_said's names the account to use as 2.x's
# inventory does (vm_ssh_jump), where this engine keeps it in host.yml (jump).
PROHIBITED = (
    "{label} refused to forward to {machine}: {said}. Its sshd forbids it - AllowTcpForwarding, PermitOpen,"
    " DisableForwarding, or restrict on the key - or Tailscale's own SSH server answers there and its policy does: each"
    " gives the same reply. Allow forwarding to the VMs there, or name an account that may (jump, in the host's"
    " host.yml).{tail}"
)
TAIL = (
    " Where a VPN routes the VMs' network, or a jump of your own reaches them, set vm_ssh_route: direct: under auto, a"
    " host reached on a tailnet is a hop."
)


class Refused(Exception):
    """The run reaches none of its host's machines: one sentence that says why, and what to change. Nothing was
    dialled."""

    def __init__(self, sentence: str) -> None:
        self.sentence = sentence
        super().__init__(sentence)


class Unusable(Refused):
    """The route to the host's machines cannot be used - not settled, a hop whose login fails, a host that will not
    forward - said with the setting to change: the run stops, and nothing dials a machine."""


def host_target(
    address: str, account: str, port: int, values: Mapping[str, overrides.Value], environ: Mapping[str, str]
) -> runner.Target:
    """The Proxmox host's own Target, for the hop and for root SSH: its address, account and port (host.yml's address,
    ssh_user and ssh_port), this computer's key for it and its ssh program (ssh_identity_file and ssh_executable, the
    runner's own default where none is named), its socket in the one control directory (paths.control_dir).
    runner.Refused, as the runner says it, for a value it does not take."""
    program = values["ssh_executable"].value
    return runner.proxmox_host(
        address,
        account,
        paths.control_dir(environ),
        port=port,
        identity_file=values["ssh_identity_file"].value or None,
        **(dict(ssh=program) if program else {}),
    )


def _words(options: object) -> list[str]:
    """The person's own options as ssh's words, -o Name=value each, in their order: what a jump among them is read
    from."""
    pairs = options.items() if isinstance(options, Mapping) else ()
    return [word for name, value in pairs for word in ("-o", f"{name}={value}")]


async def settle(run: Context, host: fleet.Host) -> Route:
    """The run's route to host's machines, settled once (see the module's docstring): Unusable, with the sentence that
    names vm_ssh_route, where it cannot be settled or the hop's login made at its start fails; Refused where the host's
    settings or the control directory are not what a connection is made from. Says the route's line once, where it goes
    through the host, and the connections' notes."""
    import asyncio

    setting = host.values[SETTING].value
    try:
        fields = host.fields
        reached = host_target(fields.address, fields.ssh_user, fields.ssh_port, host.values, host.environ)
        made = connections.Connections(paths.control_dir(host.environ), host.environ)
        settled = await made.route(str(setting or "auto"), reached, jump=host.fields.jump, name=host.name)
    except route.HopRefused as refused:
        raise Unusable(one_line(str(refused), None)) from None
    except connections.Unsafe as unsafe:
        raise Refused(one_line(str(unsafe), None)) from None
    except (runner.Refused, paths.Refused) as refused:
        raise Refused(f"{host.name} cannot be reached as its files say: {one_line(str(refused), None)}") from None
    found = Route(
        run=run,
        host=host.name,
        host_flag=host.host_flag,
        settled=MappingProxyType(dict(settled)),
        connections=made,
        home=host.home,
        values=host.values,
        environ=host.environ,
        lock=asyncio.Lock(),
    )
    found.say_notes()
    if found.through:
        found.say(said(THROUGH, {"label": found.label, "status": Line("status", {}, host_flag=host.host_flag)}))
        # The login at the run's start, where what every machine is given holds no jump: a hop that cannot be used then
        # stops the run before anything else of it. Otherwise the first machine that takes the hop makes it.
        if not route.ssh_jump(_words(host.values[OPTIONS].value)):
            await found.login()
    return found


@dataclasses.dataclass(eq=False, kw_only=True)
class Route:
    """A run's route to one host's machines, settled once (settle): the run, the host's name and flag, route.py's
    settled route (`{route, setting, hop, policy, addresses}`), the run's connections, and what each machine's own
    options are read from. Made by settle alone."""

    run: Context
    host: str
    host_flag: str
    settled: Mapping[str, object]
    connections: connections.Connections
    home: paths.Home
    values: Mapping[str, overrides.Value]
    environ: Mapping[str, str]
    lock: asyncio.Lock
    logged_in: bool = False
    forwarded: bool = False
    # The sentence of a forward's verdict that stopped the run: every later targets raises it again.
    stopped: str = ""
    said: list[str] = dataclasses.field(default_factory=list)

    @property
    def through(self) -> bool:
        """Whether the machines are reached through the Proxmox host."""
        return self.settled["route"] == "host"

    @property
    def label(self) -> str:
        """The hop as a sentence names it, the host's name and how ssh reaches it; "" where the route is direct."""
        hop = self.settled.get("hop")
        return str(hop.get("label", "")) if isinstance(hop, Mapping) else ""

    @property
    def proxy_jump(self) -> str:
        """The hop as an alias's ProxyJump line names it, user@host[:port], only once its login was made: an alias gains
        it only through a hop that was checked; "" otherwise (card G8 reads it)."""
        hop = self.settled.get("hop")
        return route.jump_spec(dict(hop)) if self.through and self.logged_in and isinstance(hop, Mapping) else ""

    # ------------------------------------------------------------ what the run says ----

    def say(self, line: str) -> None:
        """line, said once a run as its report."""
        if line and line not in self.said:
            self.said.append(line)
            self.run.emit("report", None, line)

    def say_notes(self) -> None:
        """Each of the connections' notes not said yet (a socket's path too long), once a run."""
        for note in self.connections.notes:
            self.say(note)

    # ------------------------------------------------------------ the hop ----

    async def login(self) -> None:
        """The hop's login, made once a run where the route goes through the host: its shared connection made sure of
        (connections.ensure_hop, route.hop_held as it stands). Unusable, with route.login_said's sentence in this
        engine's names, where it cannot be made."""
        if not self.through:
            return
        async with self.lock:
            if self.logged_in:
                return
            try:
                await self.connections.ensure_hop(self.settled)
            except connections.Unusable as unusable:
                raise Unusable(one_line(str(unusable), None)) from None
            self.logged_in = True

    async def _forward(self, name: str, target: runner.Target) -> None:
        """One forward of its own through the hop to target, the first machine by name that takes it, once a run: the
        run stops where the host refuses to forward there or ssh cannot use it as a hop, and a machine that said
        nothing, or a login for the forward that did not finish, is said. Made once a verdict came back: one a
        cancellation or a refusal cut is made again by the next targets. A verdict that stops the run is kept
        (`stopped`), and every later targets raises it again: Unusable is the run's stop, even where a caller caught
        it, and a targets that waited on the lock while another's forward was refused raises it too."""
        why = None
        async with self.lock:
            if not self.forwarded:
                verdict = await self.connections.forward(target, self.settled)
                self.forwarded = True
                machine = f"{name} at {target.address}"
                why = verdict.get("why")
                if why == "prohibited":
                    self.stopped = PROHIBITED.format(
                        label=self.label,
                        machine=machine,
                        said=route.ssh_said(verdict.get("said")).rstrip(".") or "no reason given",
                        tail=TAIL if self.settled.get("setting") == "auto" else "",
                    )
                elif why in STOPS:
                    self.stopped = one_line(route.forward_said(dict(self.settled), name, target.address, verdict), None)
        if self.stopped:
            raise Unusable(self.stopped)
        if why in SAYS:
            self.say(one_line(route.forward_said(dict(self.settled), name, target.address, verdict), None))

    async def ready(self) -> connections.Master | None:
        """The hop's shared connection made sure of again, before a step that reaches many machines at once and after a
        wait on a person: None where no login was made (the route is direct, or no machine took the hop); a Master
        otherwise, its `ok` false and `said` why where none answers, which fails no machine: each then fails alone,
        not answering."""
        if not (self.through and self.logged_in):
            return None
        try:
            return await self.connections.ensure_hop(self.settled)
        except connections.Unusable as unusable:
            return connections.Master(ok=False, opened=False, said=one_line(str(unusable), None))

    # ------------------------------------------------------------ a machine's SSH arguments ----

    def _options(self, machine: fleet.Target) -> object:
        """The person's own options for machine's connections: the run's, given for every machine; else client.yml's
        for that machine, else for every machine."""
        if self.values[OPTIONS].layer == overrides.RUN:
            return self.values[OPTIONS].value
        try:
            return overrides.resolve(self.home, self.host, machine=machine.name, environ=self.environ)[OPTIONS].value
        except overrides.Refused as refused:
            raise Refused(f"{machine.said}'s {OPTIONS} cannot be read: {one_line(str(refused), None)}") from None

    def _settled_for(self, machine: fleet.Target) -> Mapping[str, object]:
        """The route machine is reached by: the run's, for a Proxmox VM; straight to its address for any other kind,
        which never takes the hop."""
        if machine.kind == fleet.KIND:
            return self.settled
        return MappingProxyType({**self.settled, "route": "direct", "hop": {}})

    async def targets(self, machines: Sequence[fleet.Target]) -> tuple[runner.Target, ...]:
        """Each of machines as the runner reaches it, in their order: the one function that makes a machine's SSH
        arguments (see the module's docstring). Refused where a machine has no address, its options are not what a
        connection takes, or its own ssh would not go where the route says (nothing was dialled); Unusable where the
        hop, needed by a machine that takes it, cannot be used, and again at every later call once a forward's verdict
        stopped the run. Each machine is asked of its own ssh (held) before anything connects: the hop's login and the
        forward come after every machine is held, so a machine refused there leaves nothing reached."""
        if self.stopped:
            raise Unusable(self.stopped)
        program = self.values["ssh_executable"].value
        made: list[runner.Target] = []
        for machine in machines:
            if not machine.address:
                raise Refused(f"{machine.said} has no address to be reached at: it does not run, or does not answer")
            try:
                base = runner.machine(
                    machine.identity,
                    machine.address,
                    machine.user,
                    self.connections.control_dir,
                    **(dict(ssh=program) if program else {}),
                )
                made.append(
                    await self.connections.target_for(
                        self.run.job, base, self._settled_for(machine), self._options(machine)
                    )
                )
            except runner.Refused as refused:
                raise Refused(f"{machine.said} cannot be reached: {one_line(str(refused), None)}") from None
        unheld = []
        for machine, target in zip(machines, made, strict=True):
            try:
                await self.connections.held(target, self._settled_for(machine))
            except connections.Unheld as refused:
                unheld.append(one_line(str(refused), None))
        if unheld:
            raise Refused(" ".join(unheld))
        hopped = sorted(
            ((machine.name, target) for machine, target in zip(machines, made, strict=True) if target.hop is not None),
            key=lambda pair: pair[0],
        )
        if hopped:
            await self.login()
            await self._forward(*hopped[0])
        for machine, target in zip(machines, made, strict=True):
            if self.through and machine.kind == fleet.KIND and target.hop is None:
                jump = route.ssh_jump(runner.given_words(target)) or CONFIGURED
                self.say(OWN_JUMP.format(name=machine.name, jump=one_line(jump, 200), label=self.label))
        self.say_notes()
        return tuple(made)
