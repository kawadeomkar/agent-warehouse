"""The fleet as the engine reads it: a host's pool members, each a target with its identity, record, state and address,
and every machine the product must not touch refused before anything changes (docs/architecture/engine.md §16.4).

`connect(home, reached)` opens the host a run is against (fleet/hosts.py's `reach` chose it): `fleet.json`'s record,
the vault opened with its key as fleet/vaultkey.py finds it, the host's token's secret from it, and one client
(pve/client.py) pinned as the host keeps its anchor (pve/pin.py's `kept`), through the proxy the setting
`network_proxy` gives. It sends nothing; a `Host` closes its client.

**What is the fleet's.** Ownership is pool membership and a valid record (engine/record.py), never a label: nothing here
reads a VM's tags for a decision. `refusal(target)` is the one check, and `owned(target)` raises it (`NotOwned`): a
member with no record of this engine's, whatever tags it carries; a record whose UUID is not the VM's own (a copy, as a
clone sent without a description carries its source's notes), and one of kind template, build or golden on a VM that
is no template; a record still in its first form, which names no UUID yet; a member whose configuration or record the
token may not read, or that cannot be read; two members with one UUID, both (a restore without `unique` keeps its
source's); and a template, which is never a target. Every function that returns a target for a change returns it
through `owned`.

**Names.** `requested(host, names, create=..., change=...)` holds each name before Proxmox is asked anything: today's
pattern of a VM's name; no name of a host of the fleet's, nor `localhost` (a VM's SSH alias would take this computer's
connections to that host); with the aliases on, no name ssh would take for another host (`checks.ssh_alias_refusal`, a
dot allowed in a name given); and for a deploy that would create, no name whose alias records another host of the
fleet. Then, with the token, it reads the pool once: for a changing run, the pool's mark is held to D2's rule
(fleet/mark.py's `may_change`) before anything else is read. With names, each is the one member of its name: a name two
members carry is refused, a name no member carries is `Missing` (or, creating, a name to create), and one that is no
target the run may change is `NotOwned`. With none, a deploy that would create asks for the default VM
(`vm_default_name`) and every other run for every member, a member named after a host, one whose name another member
carries, and, for a changing run, one the run may not change each left out and said. A deploy that would create looks
each name to create up among the host's VMs outside the pool, wherever the token may read `/vms` (`VM.Audit` there,
which `init` grants by default), and refuses one it finds with today's sentence (a clone in flight there, under `lock:
clone`, is said as one); where the token may not, it says so, and the plan the run prints before it creates says it
too (`Requested.outside`). And it asks every other host of the fleet whether a VM of each name no alias places is its
own, refusing one found there with today's sentence, and saying and passing a host that does not answer.

**State and address.** Whether a VM runs is read from the VM's own endpoint (`current`), never the listing's status,
which lags what a run itself just changed by up to ten seconds. A VM's address is taken from its guest agent alone, on
its own network card (the MAC of `proxmox_nic`), through pve/agent.py's `address` (today's `checks.guest_ipv4`): one
IPv4 address `ipaddress` parses, never 0.0.0.0, multicast or reserved. Two targets of one read that report one address
are both left without one, and said. A token Proxmox refuses the agent's call (`Denied`, by its status alone) ends the
read at once; anything else one VM's agent says leaves that VM without an address.

`count_made_by_agent(host)` counts the members the fleet owns whose record says an agent's run made them (card I's cap).

Every refusal is `Refused`, one sentence with no "error: " before it, which never says the token or a secret. Blocking,
as the client is: a workflow calls these in a thread. Standard library, fleet/, pve/, vendors/, schema/ and today's
checks.py and hosts/ at import.
"""

from __future__ import annotations

import dataclasses
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from types import MappingProxyType

from .. import checks
from ..fleet import hosts, mark, overrides, paths, sealed, secrets, vaultkey
from ..fleet import record as fleet_record
from ..hosts import model
from ..pve import agent, pin
from ..pve.answers import Malformed, Pool, ProxmoxError, Resource, VmConfig
from ..pve.client import Denied, NoAgent, NotRunning, Proxmox, Token
from ..pve.client import Refused as Answered
from ..schema.patterns import PATTERNS, full
from ..schema.printer import said
from ..schema.words import Line
from ..secret import Secret
from ..text import one_line
from ..vendors import proxy
from ..vendors.transport import TimedOut, TransportError, Unreachable
from . import record

__all__ = [
    "Host",
    "Missing",
    "NotOwned",
    "Refused",
    "Requested",
    "Target",
    "address",
    "connect",
    "count_made_by_agent",
    "members",
    "owned",
    "refusal",
    "requested",
]

# The kind of target a Proxmox VM is (card J3 adds the machine that is none).
KIND = "vm"
# The name every computer reaches itself by: no VM takes it, as no host of the fleet's.
LOCALHOST = "localhost"
# A VM's identity, its SSH host key's name: this prefix and its SMBIOS UUID (proxmox.ssh_host_key_alias).
IDENTITY = "agent-warehouse-"
# A pool's member that is a VM, as Proxmox lists it.
QEMU = "qemu"
# What the VM's own endpoint says of one that runs.
RUNNING = "running"
# The lock a VM's configuration holds while its clone runs, before it joins its pool.
CLONING = "clone"
# Where the token reads every VM of the host, and what it needs there: init grants it beside the role on the pool.
EVERY_VM = "/vms"
AUDIT = "VM.Audit"
# The privilege a VM's address is read with, which the role on the pool gives (pve/privileges.py's agent row).
AGENT_PRIVILEGE = "VM.GuestAgent.Audit"
# The most of a VM's word an error of one VM's quotes.
SAID_MOST = 200


class Refused(Exception):
    """One sentence that says why a run reads or changes nothing here: never a secret, with no "error: " before it."""

    def __init__(self, sentence: str) -> None:
        self.sentence = sentence
        super().__init__(sentence)


class NotOwned(Refused):
    """The target is not the fleet's to change: why, as refusal() says it."""


class Missing(Refused):
    """No member of the pool carries a name given: the names, as given."""

    def __init__(self, sentence: str, names: tuple[str, ...]) -> None:
        self.names = names
        super().__init__(sentence)


@dataclasses.dataclass(frozen=True)
class Target:
    """A machine a run is against: a Proxmox VM (kind vm), its name, its identity (agent-warehouse-<its SMBIOS UUID>, ""
    where its configuration gives none), the host's name, its node and VMID, the account a run logs in as (vm_user), its
    address ("" for none: stopped, not answering, or one another target reports too), its record (None for none), and
    whether it runs, from its own endpoint. Beside them, what refusal() reads: whether it is a template; why its
    configuration or record could not be read ("" where they were); the VMIDs of the other members that carry its UUID;
    the MAC of its own network card, which its address is read on; and the address another target reported too."""

    kind: str
    name: str
    identity: str
    host: str
    node: str
    vmid: int
    user: str
    address: str = ""
    record: record.Record | None = None
    running: bool = False
    template: bool = False
    unread: str = ""
    twins: tuple[int, ...] = ()
    mac: str = ""
    shared: str = ""

    @property
    def said(self) -> str:
        """The target as a sentence names it: its name and VMID."""
        return f"{self.name} (VMID {self.vmid})" if self.name else f"the VM with no name (VMID {self.vmid})"


@dataclasses.dataclass(frozen=True, eq=False)
class Host:
    """The host a run is against, opened: its name, its client, its pool, the network card a VM's address is read on
    (proxmox_nic), the account a run logs in as (vm_user), the default VM's name (vm_default_name), whether the aliases
    are on (claude_ssh_config), fleet.json's record, the fleet's hosts (layout), the environment its settings were read
    from, the names a VM may not take (localhost and the fleet's hosts) and those this computer reaches a host by (with
    each host's address), and every host's token's secret the vault holds, by the host's name, which asking another host
    needs. Beside them, what the route to its machines is settled from (engine/routes.py): the fleet's home, its
    host.yml as it was read (fields: its address, ssh_user, ssh_port and jump), and every setting's value for the run
    against it (values, overrides.resolve's, read-only), vm_ssh_route, ssh_identity_file, ssh_executable and
    vm_ssh_options among them. A context manager that closes the client."""

    name: str
    client: Proxmox
    pool: str
    nic: str
    user: str
    default_name: str
    aliases: bool
    fleet: fleet_record.FleetRecord
    layout: hosts.Layout
    environ: Mapping[str, str]
    hosts_named: tuple[str, ...]
    reached_by: tuple[str, ...]
    home: paths.Home
    fields: hosts.HostFile
    values: Mapping[str, overrides.Value]
    tokens: Mapping[str, Secret] = dataclasses.field(repr=False)

    def __enter__(self) -> Host:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    @property
    def host_flag(self) -> str:
        """The host's flag a line that names a command ends with: the host's name with more than one, "" with one."""
        return self.name if self.layout.many() else ""


@dataclasses.dataclass(frozen=True)
class Requested:
    """What requested found: the targets, in the pool's order; the names a deploy would create, which no member carries;
    every line said and passed (a member left out and why, a host that did not answer, the outside not looked at), in
    order; and whether a VM of each name to create was looked for outside the pool."""

    targets: tuple[Target, ...]
    new: tuple[str, ...] = ()
    said: tuple[str, ...] = ()
    outside: bool = True


# ---------------------------------------------------------------------------------------- opening a host ----


def _words(error: BaseException | str) -> str:
    """A refusal of fleet/'s, or another error, as one sentence of this module's: one line, no "error: " before it."""
    return one_line(str(error), None).removeprefix("error: ")


def _tokens(opened: Mapping[str, Secret]) -> dict[str, Secret]:
    """Of the vault's map, each host's token's secret, by the host's name."""
    found = {}
    for entry, value in opened.items():
        try:
            row, owner = secrets.parsed(entry)
        except secrets.Refused:
            continue
        if row.name == "token" and owner is not None:
            found[owner] = value
    return found


def _client(
    name: str,
    fields: hosts.HostFile,
    anchor: str | None,
    values: Mapping[str, overrides.Value],
    tokens: Mapping[str, Secret],
    environ: Mapping[str, str],
) -> Proxmox:
    """The client of the host called name, as its folder keeps it: its address and port, its anchor (with its
    fingerprint beside it, where it keeps one) or the computer's certificates, its token from the vault, and the proxy
    the setting network_proxy gives for its address. It sends nothing."""
    secret = tokens.get(name)
    if secret is None:
        raise Refused(
            said(
                "the vault holds no token's secret for {name}, so nothing of it can be read: {vault} puts it",
                {"name": name, "vault": Line("vault", {"name": "token", "host": name})},
            )
        )
    # The address and port as a URL has them: an IPv6 address in brackets.
    at = f"[{fields.address}]" if ":" in fields.address else fields.address
    url = f"https://{at}:{fields.api_port}"
    try:
        pinned = pin.kept(anchor, fields.fingerprint if anchor is not None else "", values["tls_ca_file"].value or None)
        through = proxy.choose(values["network_proxy"].value, environ, url)
        token = Token(f"{fields.token_user}!{fields.token_id}", secret)
    except (ValueError, OSError) as error:
        raise Refused(f"{name}'s API cannot be reached as its files say: {_words(error)}") from None
    return Proxmox(url, pinned, token, through)


def _named_hosts(layout: hosts.Layout) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The names no VM may take (localhost, and each host of the fleet's), and every name this computer reaches one of
    them by: those, and each host's address."""
    named = [LOCALHOST, *(name for name in layout.folders() if not model.name_refusal(name))]
    addresses = [one.address for one in layout.every() if one.address]
    return tuple(dict.fromkeys(named)), tuple(dict.fromkeys([*named, *addresses]))


def connect(
    home: paths.Home,
    reached: hosts.Reached,
    values: Mapping[str, overrides.Value] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Host:
    """The host reached is, opened: see the module's docstring. values is every setting's value for the run against it
    (overrides.resolve's), read here where it is not given. Refused, in one sentence, where the fleet has no record, the
    vault cannot be opened or holds no token for the host, the host names no pool, or its files say no API that can be
    reached. Sends nothing."""
    env = os.environ if environ is None else environ
    name = reached.host.name
    layout = hosts.Layout(home, env)
    try:
        if values is None:
            values = overrides.resolve(home, name, environ=env)
        fleet = fleet_record.read(home)
        if fleet is None:
            raise Refused(
                said("the fleet has no record of its own (fleet.json) yet: {init} writes it", {"init": Line("init")})
            )
        key = vaultkey.open(
            home,
            key_file=values["vault_key_file"].value,
            key_command=values["vault_key_command"].value,
            environ=env,
        )
        tokens = _tokens(sealed.read(home, key) or {})
        named, reached_by = _named_hosts(layout)
    except (sealed.Refused, fleet_record.Refused, overrides.Refused, paths.Refused, hosts.Refused) as error:
        raise Refused(_words(error)) from None
    pool = values["proxmox_pool"].value
    if not pool:
        raise Refused(
            said(
                "{name} names no pool (proxmox_pool), and the fleet's VMs are its members: {init} writes it",
                {"name": name, "init": Line("init", {"host": name})},
            )
        )
    return Host(
        name=name,
        client=_client(name, reached.fields, reached.anchor, values, tokens, env),
        pool=pool,
        nic=values["proxmox_nic"].value,
        user=values["vm_user"].value,
        default_name=values["vm_default_name"].value,
        aliases=values["claude_ssh_config"].value is not False,
        fleet=fleet,
        layout=layout,
        environ=env,
        hosts_named=named,
        reached_by=reached_by,
        home=home,
        fields=reached.fields,
        values=MappingProxyType(dict(values)),
        tokens=MappingProxyType(tokens),
    )


# ------------------------------------------------------------------------------------------ the one check ----


def refusal(target: Target) -> str:
    """Why target is not the fleet's to change, "" where it is: the one check, decided from the target alone, never
    from a label. See the module's docstring."""
    if target.kind != KIND:
        return f"{target.said} is no Proxmox VM, which is all this host's runs change"
    if target.template:
        return f"{target.said} is a template, which is never a target"
    if target.unread:
        return target.unread
    found = target.record
    if found is None:
        return (
            f"{target.said} is a member of the pool with no record of this engine's in its notes, so this fleet did"
            " not make it, whatever tags it carries: it is never changed or deleted"
        )
    if found.kind != KIND:
        return (
            f"{target.said} holds a {found.kind}'s record and is no template, so it is a copy of another's notes (a"
            " clone sent without a description carries its source's record): it is never changed or deleted"
        )
    if not found.uuid:
        return (
            f"{target.said}'s record is still the first form its clone carries, which names no UUID: the run that"
            " clones a VM writes the rest, and until then nothing tells it from a copy"
        )
    if found.uuid != target.identity.removeprefix(IDENTITY) or not target.identity:
        return (
            f"{target.said}'s record names another VM's UUID ({found.uuid}), so it is a copy of that VM's notes, as a"
            " clone carries its source's: it is never changed or deleted"
        )
    if target.twins:
        others = ", ".join(str(vmid) for vmid in target.twins)
        return (
            f"{target.said} carries one UUID with VMID {others}, as a restore without unique leaves them, so nothing"
            " tells them apart: none of them is changed"
        )
    return ""


def owned(target: Target) -> Target:
    """target, where the fleet may change it: NotOwned, saying why, where refusal() says it may not. The one gate every
    target a change is made on passes."""
    why = refusal(target)
    if why:
        raise NotOwned(why)
    return target


# ----------------------------------------------------------------------------------------- the members ----


def _pool(host: Host) -> Pool:
    """The host's pool, with its comment and its members: Refused where Proxmox refuses the token for it, it is not
    there, or the host does not answer."""
    try:
        return host.client.pool(host.pool)
    except Denied as error:
        raise Refused(
            f"{error}: the token cannot read the pool {host.pool} on {host.name}, where the fleet's VMs are, so"
            " nothing was read. Check the token's grant on the pool"
        ) from None
    except (ProxmoxError, TransportError) as error:
        raise Refused(f"the pool {host.pool} on {host.name} could not be read: {_words(error)}") from None


def _running(host: Host, node: str, vmid: int) -> bool:
    """Whether the VM runs, from its own endpoint, never the listing's status, which lags what a run just changed."""
    try:
        return host.client.current(node, vmid).status == RUNNING
    except (Denied, Answered, Malformed):
        return False


def _member(host: Host, entry: Resource, config: VmConfig | None, unread: str) -> Target:
    """The target a pool's member is, from its configuration where it was read (config), else from the listing alone,
    unread saying why."""
    if config is None:
        return Target(
            kind=KIND,
            name=entry.name,
            identity="",
            host=host.name,
            node=entry.node,
            vmid=entry.vmid,
            user=host.user,
            template=entry.template,
            unread=unread,
        )
    # Its SMBIOS UUID as Proxmox gives it (proxmox.smbios_uuid), the name its SSH host key is filed under.
    identity = f"{IDENTITY}{config.identity}" if config.identity else ""
    found: record.Record | None = None
    if not config.template:
        try:
            found = record.read(config)
        except record.Unreadable as error:
            unread = f"{_words(error)}: it is never changed or deleted"
    return Target(
        kind=KIND,
        name=config.name or entry.name,
        identity=identity,
        host=host.name,
        node=config.node,
        vmid=config.vmid,
        user=host.user,
        record=found,
        running=False if config.template else _running(host, config.node, config.vmid),
        template=config.template,
        unread=unread,
        mac=config.mac,
    )


def _members(host: Host, pool: Pool) -> tuple[Target, ...]:
    """Every VM of the pool, as a target: its configuration read from its own endpoint, a member the token may not read
    (NoAccess on it, which the pool's listing still names) or that cannot be read said as such and never changed; and
    the VMIDs that carry one UUID marked on each of them."""
    found: list[Target] = []
    for entry in pool.members:
        if entry.type != QEMU or entry.vmid is None:
            continue
        config, unread = None, ""
        try:
            config = host.client.config(entry.node, entry.vmid, host.nic)
        except Denied:
            unread = (
                f"the token may not read the configuration of {entry.name or 'the VM'} (VMID {entry.vmid}), a member"
                " of the pool: it is never changed or deleted"
            )
        except (Answered, Malformed) as error:
            unread = (
                f"the configuration of {entry.name or 'the VM'} (VMID {entry.vmid}) could not be read"
                f" ({one_line(_words(error), SAID_MOST)}): it is never changed or deleted"
            )
        found.append(_member(host, entry, config, unread))
    uuids = Counter(target.identity for target in found if target.identity)
    return tuple(
        dataclasses.replace(
            target,
            twins=tuple(
                other.vmid for other in found if other.identity == target.identity and other.vmid != target.vmid
            ),
        )
        if uuids[target.identity] > 1
        else target
        for target in found
    )


def members(host: Host) -> tuple[Target, ...]:
    """Every VM of the host's pool, as a target, with no address read: Refused where the pool cannot be read."""
    try:
        return _members(host, _pool(host))
    except (ProxmoxError, TransportError) as error:
        raise Refused(f"{host.name} could not be read: {_words(error)}") from None


def count_made_by_agent(host: Host) -> int:
    """How many VMs of the host's pool that the fleet owns an agent's run made, as their records say."""
    return sum(1 for target in members(host) if not refusal(target) and target.record and target.record.made_by_agent)


# ------------------------------------------------------------------------------------------ the addresses ----


def address(host: Host, target: Target) -> str | None:
    """target's address, from its guest agent on its own network card, asked once whatever is known of whether it
    runs (a VM the run just started is asked too): None where it reports none, does not run, has no agent or does not
    answer. Refused at once where Proxmox refuses the token the agent's call, told by its status alone (Denied)."""
    if not target.mac:
        return None
    try:
        interfaces = host.client.agent_interfaces(target.node, target.vmid, host.client.agent_call_most)
    except Denied as error:
        raise Refused(
            f"{error}: reading {target.said}'s address needs {AGENT_PRIVILEGE} on it, which the role on the pool"
            " gives, so no VM's address can be read and nothing was reached"
        ) from None
    except (NotRunning, NoAgent, Answered, Malformed, TimedOut, Unreachable):
        return None
    except (ProxmoxError, TransportError) as error:
        raise Refused(f"{target.said}'s address could not be asked: {_words(error)}") from None
    return agent.address(interfaces, target.mac)


def _addressed(host: Host, targets: Sequence[Target]) -> tuple[tuple[Target, ...], list[str]]:
    """targets, each owned one that runs given its address; two that report one address each left with none, said."""
    found = [
        dataclasses.replace(target, address=address(host, target) or "")
        if target.running and not refusal(target)
        else target
        for target in targets
    ]
    reported = Counter(target.address for target in found if target.address)
    shared = [target for target in found if target.address and reported[target.address] > 1]
    lines = []
    if shared:
        vmids = {target.vmid for target in shared}
        found = [
            dataclasses.replace(target, address="", shared=target.address) if target.vmid in vmids else target
            for target in found
        ]
        names = ", ".join(target.said for target in shared)
        lines.append(
            f"{names} report one address ({', '.join(sorted({target.address for target in shared}))}), so none of them"
            " is reached: nothing here can tell which is telling the truth. Look at each one's address in Proxmox's"
            " console."
        )
    return tuple(found), lines


# ---------------------------------------------------------------------------------------------- the names ----


def _them(names: Sequence[str]) -> str:
    return "it" if len(names) == 1 else "them"


def _hold(host: Host, names: Sequence[str], create: bool) -> None:
    """Each name given held before Proxmox is asked anything: see the module's docstring."""
    if not all(isinstance(name, str) and full("vm_name", name) and "," not in name for name in names):
        raise Refused(f"a name given is no VM's name: {PATTERNS['vm_name'].words}")
    taken = [name for name in names if name in host.hosts_named]
    if taken:
        which = "is the name" if len(taken) == 1 else "are names"
        raise Refused(
            f"{', '.join(taken)} {which} of a host of the fleet's ({', '.join(host.hosts_named)}), which no VM may"
            " take: its SSH alias would take this computer's connections to that host to the VM. Give the VM another"
            " name."
        )
    if not host.aliases:
        return
    refused = [(name, why) for name in names if (why := checks.ssh_alias_refusal(name, host.reached_by, list(names)))]
    if refused:
        first, why = refused[0]
        raise Refused(
            f"{', '.join(name for name, _ in refused)} cannot be a VM's name while this project keeps an SSH alias for"
            f" each VM, by its name: for {first}, {why}. Choose another name, or set claude_ssh_config: false."
        )
    if create:
        try:
            origins = host.layout.origins(list(names))
        except hosts.Refused as error:
            raise Refused(_words(error)) from None
        theirs = {name: origin for name, origin in origins.items() if origin != host.name}
        if theirs:
            where = host.layout.alias_file()
            listed = "; ".join(f"{name} is a VM of {origin}" for name, origin in theirs.items())
            raise Refused(
                f"{listed}, as the SSH alias of the name records in {where}, and a VM name belongs to one host. Give"
                " this host's VM another name."
            )


def _named_after_a_host(host: Host, targets: Sequence[Target]) -> tuple[list[Target], list[str]]:
    """targets less those named after a host, and the line that says which were left out."""
    misnamed = [target for target in targets if target.name in host.hosts_named]
    if not misnamed:
        return list(targets), []
    names = [target.name for target in misnamed]
    return [target for target in targets if target not in misnamed], [
        f"Left out: {', '.join(names)}, member{'s' if len(names) > 1 else ''} of the pool {host.pool} but named like a"
        f" host of the fleet, which a VM must never become. Rename {_them(names)} in Proxmox, or ask whoever made"
        f" {_them(names)}."
    ]


def _every(host: Host, everyone: Sequence[Target], change: bool) -> tuple[list[Target], list[str]]:
    """Every VM of the pool but the templates, with no names given: one named after a host, one whose name another
    member carries, and, for a changing run, one the run may not change, each left out and said."""
    candidates, lines = _named_after_a_host(host, [target for target in everyone if not target.template])
    counts = Counter(target.name for target in candidates if target.name)
    twice = sorted({target.name for target in candidates if counts[target.name] > 1}, key=str.lower)
    if twice:
        candidates = [target for target in candidates if target.name not in twice]
        lines.append(
            f"Left out: {', '.join(twice)}, each the name of more than one VM of the pool {host.pool}, and a VM is"
            " found by its name. Rename one of each in Proxmox."
        )
    if change:
        for target in [target for target in candidates if refusal(target)]:
            candidates.remove(target)
            lines.append(f"Left out: {refusal(target)}.")
    return candidates, lines


def _by_name(
    host: Host, everyone: Sequence[Target], names: Sequence[str], create: bool
) -> tuple[list[Target], list[str]]:
    """The one VM of the pool each name names, in the order given; and the names no member carries, which a deploy that
    would create makes (Missing for any other run). Refused for a name two members carry."""
    targets: list[Target] = []
    new: list[str] = []
    for name in dict.fromkeys(names):
        carry = [target for target in everyone if target.name == name]
        if len(carry) > 1:
            vmids = ", ".join(str(target.vmid) for target in carry)
            raise Refused(
                f"More than one VM of the pool {host.pool} on {host.name} is named {name} (VMID {vmids}), and a VM is"
                " found by its name: rename one of them in Proxmox."
            )
        if carry:
            targets.append(carry[0])
        else:
            new.append(name)
    if new and not create:
        listed = ", ".join(new)
        raise Missing(
            said(
                "{names} {be} no VM of the pool {pool} on {host}: {status} lists the fleet's VMs",
                {
                    "names": listed,
                    "be": "is" if len(new) == 1 else "are",
                    "pool": host.pool,
                    "host": host.name,
                    "status": Line("status", {}, host_flag=host.host_flag),
                },
            ),
            tuple(new),
        )
    return targets, new


# ----------------------------------------------------------------------------------- a deploy that creates ----


def _outside(host: Host, everyone: Sequence[Target], new: Sequence[str]) -> tuple[bool, list[str]]:
    """Whether each name to create was looked for among the host's VMs outside the pool, and the line that says it was
    not; Refused for a VM of such a name found there, as today's role refuses an existing VM it does not own."""
    try:
        held = host.client.permissions(EVERY_VM).privileges
    except (Denied, Answered, Malformed):
        held = {}
    if not held.get(AUDIT):
        return False, [
            f"The token may not read every VM of {host.name} ({AUDIT} on {EVERY_VM}, proxmox_audit_scope: pool), so"
            f" no VM named {', '.join(new)} outside the pool {host.pool} was looked for: one there would carry the"
            " name a second time."
        ]
    ours = {target.vmid for target in everyone}
    try:
        listed = host.client.resources("vm")
    except Denied as error:
        raise Refused(f"{error}: the host's VMs could not be listed, so nothing was made") from None
    for entry in listed:
        if entry.type != QEMU or entry.vmid in ours or entry.name not in new:
            continue
        try:
            lock = host.client.config(entry.node, entry.vmid).lock
        except (Denied, Answered, Malformed):
            lock = ""
        if lock == CLONING:
            raise Refused(
                f'A VM named "{entry.name}" (VMID {entry.vmid}) is being cloned, its configuration under lock: clone,'
                f" and is no member of the pool {host.pool} yet: a run making it is under way, or one stopped and left"
                " it. Run this again once its clone has ended."
            )
        if entry.template:
            raise Refused(
                f'"{entry.name}" is the name of a template (VMID {entry.vmid}) outside the pool {host.pool}. Refusing'
                " to configure it. Pick a different name."
            )
        raise Refused(
            f'A VM named "{entry.name}" already exists (VMID {entry.vmid}) and is no member of the pool {host.pool}, so'
            " this fleet did not create it. Refusing to change it, or to make a second VM of its name. Pick a different"
            " name."
        )
    return True, []


def _other(host: Host, name: str) -> tuple[Proxmox, str]:
    """Another host of the fleet, opened as connect opens the run's: its client and its pool's name. Refused where its
    files cannot be used, or the vault holds no token for it."""
    try:
        each = host.layout.other(name)
        if each.problem:
            raise Refused(f"{name} cannot be used: {each.problem}")
        fields = host.layout.fields(name)
        anchor = host.layout.anchor(name, fields)
        values = overrides.resolve(host.layout.home, name, environ=host.environ)
    except (hosts.Refused, overrides.Refused, paths.Refused) as error:
        raise Refused(_words(error)) from None
    pool = values["proxmox_pool"].value
    if not pool:
        raise Refused(f"{name} names no pool (proxmox_pool)")
    return _client(name, fields, anchor, values, host.tokens, host.environ), pool


def _elsewhere(host: Host, names: Sequence[str]) -> list[str]:
    """Each other host of the fleet asked whether a VM of a name no alias places on a host is a member of its pool:
    Refused for one found there, with today's sentence; each host that does not answer said and passed."""
    others = [name for name in host.layout.folders() if name != host.name and not model.name_refusal(name)]
    if not others:
        return []
    try:
        placed = host.layout.origins(list(names))
    except hosts.Refused:
        placed = {}
    unplaced = [name for name in names if name not in placed]
    lines: list[str] = []
    unanswered: list[str] = []
    for other in others if unplaced else []:
        try:
            client, pool = _other(host, other)
        except Refused:
            unanswered.append(other)
            continue
        try:
            found = client.pool(pool)
        except (ProxmoxError, TransportError):
            unanswered.append(other)
            continue
        finally:
            client.close()
        theirs = {entry.name for entry in found.members if entry.type == QEMU}
        for name in unplaced:
            if name in theirs:
                raise Refused(
                    said(
                        "{name} is a VM of {other} already, and a VM name belongs to one host: {line} runs it there, or"
                        " give this one another name",
                        {"name": name, "other": other, "line": Line("deploy", {"names": [name]}, host_flag=other)},
                    )
                )
    for other in unanswered:
        lines.append(
            f"{other} did not answer, so whether it has a VM of these names is not known: its alias, once written,"
            " records it"
        )
    return lines


# ------------------------------------------------------------------------------------------- the request ----


def requested(host: Host, names: Sequence[str] = (), *, create: bool = False, change: bool = True) -> Requested:
    """The targets a run against host asks for by names, each with its address where it runs: see the module's
    docstring. create: a deploy that may create (names default to the default VM's; a name no member carries is one to
    create). change: a run that changes something, which reads the pool's mark by D2's rule before anything else and
    returns each target through owned(). Refused, Missing or NotOwned, in one sentence, before anything is changed."""
    chosen = tuple(names) or ((host.default_name,) if create else ())
    _hold(host, chosen, create)
    lines: list[str] = []
    try:
        pool = _pool(host)
        if change:
            try:
                rule = mark.may_change(mark.parse(pool.comment), host.fleet)
            except mark.Refused as error:
                rule = str(error)
            if rule is not None:
                raise Refused(_words(rule))
        everyone = _members(host, pool)
        if chosen:
            targets, new = _by_name(host, everyone, chosen, create)
        else:
            (targets, lines), new = _every(host, everyone, change), []
        if change:
            targets = [owned(target) for target in targets]
        outside = True
        if new:
            outside, more = _outside(host, everyone, new)
            lines += more
        if create:
            lines += _elsewhere(host, chosen)
        addressed, more = _addressed(host, targets)
        lines += more
    except (ProxmoxError, TransportError) as error:
        raise Refused(f"{host.name} could not be read: {_words(error)}") from None
    return Requested(addressed, tuple(new), tuple(lines), outside)
