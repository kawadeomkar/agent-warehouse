"""The fleet as the engine reads it: a host's pool members, each a target with its identity, record, state and address,
and every machine the product must not touch refused before anything changes (docs/architecture/engine.md §16.4).

A host is opened by fleet/opened.py's `connect(home, reached)`, which status opens one by too (card G16): `Host`,
`connect` and `Refused` are that module's, given here as this one's.

**What is the fleet's.** Ownership is pool membership and a valid record (engine/record.py), never a label: nothing here
reads a VM's tags for a decision. The one rule is pve/ownership.py's, which status judges by too: each target a read
makes carries the `Member` ownership.py's `Member.read` made of its configuration as the client read it (`member`, set
here alone and carried on by this module's own changes of a target, never by a caller's `dataclasses.replace`, which
leaves it none), and `refusal(target)` asks the rule of that member, and `owned(target)` raises it (`NotOwned`) or
returns the `Owned` that ownership.py alone makes, the identity and the digest of the configuration the decision was
made on. A target with no member - made by hand, or changed by `dataclasses.replace` after its read, a record given to
it among them - is a TypeError to both: what is judged is what was read, never a target's fields. A `Target` is what a
read gave, for reading: no write takes one. The client's writes of a VM and engine/record.py's writer take an Owned, so
a change is made on nothing but what passed `owned`, and the client holds the VM it reaches to that read again. It
refuses a member with no record of this engine's, whatever tags it carries; a record whose UUID is not the VM's own (a
copy, as a clone sent without a description carries its source's notes), and one of kind template, build or golden on
a VM that is no template; a record still in its first form, which names no UUID yet, but to the run that cloned it, by
its own job, which the record names, and its own clone's task (`job`, `clone`: never read from the record judged),
which writes the rest; a member whose configuration or record the token may not read, or that cannot be read; two
members with one UUID, both (a restore without `unique` keeps its source's); and a template, which is never a target. A
changing read gives each target it returns its Owned (`Target.owned`); a read that changes nothing gives none.

**Names.** `requested(host, names, create=..., change=...)` holds each name before Proxmox is asked anything: today's
pattern of a VM's name, and for a deploy that would create, the name Proxmox takes for a VM (pve/client.py's VM_NAME);
no name of a host of the fleet's, nor `localhost` (a VM's SSH alias would take this computer's connections to that
host); with the aliases on, no name ssh would take for another host (`checks.ssh_alias_refusal`, a dot allowed in a name
given); and for a deploy that would create, no name whose alias records another host of the fleet. A deploy that would
create is a changing run: create without change is refused. Then, with the token, it reads the pool once: for a
changing run, the pool's mark is held to D2's rule (fleet/mark.py's `may_change`) before anything else is read. With
names, each is the one member of its name: a name two members carry is refused, a name no member carries is `Missing`
(or, creating, a name to create), and one that is no target the run may change is `NotOwned`. With none, a deploy that
would create asks for the default VM (`vm_default_name`) and every other run for every member, a member named after a
host, one whose name another member carries, and, for a changing run, one the run may not change each left out and
said. A deploy that would create looks each name to create up among the host's VMs outside the pool, wherever the token
may read `/vms` (`VM.Audit` there, which `init` grants by default), and refuses one it finds with today's sentence (a
clone in flight there, under `lock: clone`, is said as one); an entry the listing gives with no name, as it lists a VM
made seconds ago whose node has not reported it yet, is read by its own configuration first, a clone in flight that
names no VM yet refused as one that may be of the name, and one that cannot be read said; where the token may not read
`/vms`, it says so, and the plan the run prints before it creates says it too (`Requested.outside`). And it asks every
other host of the fleet whether a VM of each name no alias places is its own, refusing one found there with today's
sentence, and saying and passing a host that does not answer.

**State and address.** Whether a VM runs is read from the VM's own endpoint (`current`), never a listing's status, the
cluster's or a pool's, which lags what a run itself just changed by up to ten seconds. A VM's address is taken from its
guest agent alone, on its own network card (the MAC of `proxmox_nic`), through pve/agent.py's `address` (today's
`checks.guest_ipv4`): one IPv4 address `ipaddress` parses, never 0.0.0.0, multicast or reserved. A VM whose address
another running member the fleet owns reports too is left without one, and said: in a read, among every such member of
the pool, the targets asked or not, so that a VM asked alone is never given its sibling's address; and in `address`, the
road for one VM a run just started, `Shared`. A token Proxmox refuses the agent's call (`Denied`, by its status alone)
ends the read at once; anything else one VM's agent says leaves that VM without an address.

`count_made_by_agent(host)` counts the members the fleet owns whose record says an agent's run made them (card I's cap).

Every refusal is `Refused`, one sentence with no "error: " before it, which never says the token or a secret. Blocking,
as the client is: a workflow calls these in a thread. Standard library, fleet/, pve/, schema/, vendors/transport.py,
engine/record.py and today's checks.py, hosts/ and text.py at import.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from collections.abc import Sequence

from .. import checks
from ..fleet import hosts, mark, overrides, paths
from ..fleet.opened import LOCALHOST, Host, Refused, client_for, connect, sentence
from ..hosts import model
from ..pve import agent, ownership
from ..pve.answers import Malformed, Pool, ProxmoxError, Resource, Task, VmConfig
from ..pve.client import VM_NAME, Denied, NoAgent, NotRunning, Proxmox
from ..pve.client import Refused as Answered
from ..pve.ownership import IDENTITY, KIND, Owned
from ..schema.patterns import PATTERNS, full
from ..schema.printer import said
from ..schema.words import Line
from ..text import one_line
from ..vendors.transport import TimedOut, TransportError, Unreachable
from . import record

__all__ = [
    "IDENTITY",
    "KIND",
    "LOCALHOST",
    "Host",
    "Missing",
    "NotOwned",
    "Owned",
    "Refused",
    "Requested",
    "Shared",
    "Target",
    "address",
    "connect",
    "count_made_by_agent",
    "members",
    "owned",
    "refusal",
    "requested",
]

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
# What a name to create is held to: the name Proxmox takes for a VM, said without the name given.
NOT_PROXMOX_S = (
    "a name to create is no name Proxmox gives a VM: letters, digits, dots and dashes, at most 63, starting and ending"
    " with a letter or a digit"
)


class NotOwned(Refused):
    """The target is not the fleet's to change: why, as refusal() says it."""


class Missing(Refused):
    """No member of the pool carries a name given: the names, as given."""

    def __init__(self, sentence: str, names: tuple[str, ...]) -> None:
        self.names = names
        super().__init__(sentence)


class Shared(Refused):
    """A VM reports an address another running member the fleet owns reports too, so it is not reached: the
    address."""

    def __init__(self, sentence: str, address: str) -> None:
        self.address = address
        super().__init__(sentence)


@dataclasses.dataclass(frozen=True)
class Target:
    """A machine a run reads: a Proxmox VM (kind vm), its name, its identity (agent-warehouse-<its SMBIOS UUID>, ""
    where its configuration gives none), the host's name, its node and VMID, the account a run logs in as (vm_user), its
    address ("" for none: stopped, not answering, or one another member reports too), its record (None for none), and
    whether it runs, from its own endpoint. Beside them, what refusal() reads: whether it is a template; why its
    configuration or record could not be read ("" where they were); the VMIDs of the other members that carry its UUID;
    the MAC of its own network card, which its address is read on; the address another member reported too; and the
    digest of the configuration it was read from. It is for reading: a change is made on its Owned, which a changing
    read gives it (owned, None in any other read) and owned() alone makes, from the Member its read made (member, which
    no constructor takes and dataclasses.replace leaves None: see the module's docstring)."""

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
    digest: str = ""
    owned: Owned | None = None
    member: ownership.Member | None = dataclasses.field(default=None, init=False, repr=False, compare=False)

    @property
    def said(self) -> str:
        """The target as a sentence names it: its name and VMID."""
        return f"{self.name} (VMID {self.vmid})" if self.name else f"the VM with no name (VMID {self.vmid})"


@dataclasses.dataclass(frozen=True)
class Requested:
    """What requested found: the targets, in the pool's order, each with its Owned where the read was a changing one;
    the names a deploy would create, which no member carries; every line said and passed (a member left out and why, an
    address two report, a host that did not answer, the outside not looked at), in order; and whether a VM of each name
    to create was looked for outside the pool."""

    targets: tuple[Target, ...]
    new: tuple[str, ...] = ()
    said: tuple[str, ...] = ()
    outside: bool = True


# ------------------------------------------------------------------------------------------ the one check ----


def _read(target: Target) -> ownership.Member:
    """The Member target's read made of it, which the one rule judges: TypeError for a target no read made, or one
    changed by dataclasses.replace after its read."""
    if not isinstance(target, Target) or target.member is None:
        raise TypeError(
            "a target is judged only as a read made it (members, requested): never one made by hand, nor one"
            " dataclasses.replace or copy.replace changed after its read"
        )
    return target.member


def _kept(target: Target, **changes: object) -> Target:
    """target with changes made by this module, after its read - its Owned, its address - still carrying the Member
    its read made: a caller's dataclasses.replace leaves it none."""
    changed = dataclasses.replace(target, **changes)
    object.__setattr__(changed, "member", target.member)
    return changed


def refusal(target: Target, job: str = "", clone: Task | None = None) -> str:
    """Why target is not the fleet's to change, "" where it is: pve/ownership.py's one check, of what its read read.
    job and clone: the run's own job and its own clone's task, which own the first form of the clone it made."""
    return ownership.refusal(_read(target), job, clone)


def owned(target: Target, *, job: str = "", clone: Task | None = None) -> Owned:
    """The Owned a change on target is made through, made by pve/ownership.py's owned alone, of what its read read:
    NotOwned, saying why, where refusal() says the fleet may not change it; TypeError for a target no read made. job and
    clone: the run's own job and its own clone's task (the clone call's answer, or its job record's), which own the
    first form of the clone it made, so that it writes the rest: never read from the record judged. The one gate
    every change passes."""
    member = _read(target)
    try:
        return ownership.owned(member, job=job, clone=clone)
    except ownership.NotOwned as refused:
        raise NotOwned(refused.sentence) from None


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
        raise Refused(f"the pool {host.pool} on {host.name} could not be read: {sentence(error)}") from None


def _running(host: Host, node: str, vmid: int) -> bool:
    """Whether the VM runs, from its own endpoint, never a listing's status, which lags what a run just changed."""
    try:
        return host.client.current(node, vmid).status == RUNNING
    except (Denied, Answered, Malformed):
        return False


def _member(host: Host, entry: Resource, config: VmConfig | None, unread: str, twins: tuple[int, ...]) -> Target:
    """The target a pool's member is, from its configuration where it was read (config), else from the listing alone,
    unread saying why; twins, the VMIDs of the other members that carry its UUID, which _members read beside it. It
    carries the Member the one rule judges: pve/ownership.py's Member.read of the configuration with those twins, the
    typed record's refusal passed to it as its own (the one call of Member.read in lib/, which a scan of
    tests/unit/test_aw_ownership.py holds), and for a member whose configuration was not read, one that says why, which
    the rule refuses."""
    if config is None:
        target = Target(
            kind=KIND,
            name=entry.name,
            identity="",
            host=host.name,
            node=entry.node,
            vmid=entry.vmid,
            user=host.user,
            template=entry.template,
            unread=unread,
            twins=twins,
        )
        unknown = ownership.Member(entry.name, entry.node, entry.vmid, template=entry.template, unread=unread)
        object.__setattr__(target, "member", unknown)
        return target
    # Its SMBIOS UUID as Proxmox gives it (proxmox.smbios_uuid), the name its SSH host key is filed under.
    identity = f"{IDENTITY}{config.identity}" if config.identity else ""
    found: record.Record | None = None
    if not config.template:
        try:
            found = record.read(config)
        except record.Unreadable as error:
            unread = f"{sentence(error)}: it is never changed or deleted"
    target = Target(
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
        twins=twins,
        mac=config.mac,
        digest=config.digest,
    )
    object.__setattr__(target, "member", ownership.Member.read(config, unread=unread, twins=twins))
    return target


def _members(host: Host, pool: Pool) -> tuple[Target, ...]:
    """Every VM of the pool, as a target: its configuration read from its own endpoint, a member the token may not read
    (NoAccess on it, which the pool's listing still names) or that cannot be read said as such and never changed; and
    the VMIDs that carry one UUID marked on each of them."""
    read: list[tuple[Resource, VmConfig | None, str]] = []
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
                f" ({one_line(sentence(error), SAID_MOST)}): it is never changed or deleted"
            )
        read.append((entry, config, unread))
    configs = [config for _, config, _ in read if config is not None and config.identity]
    uuids = Counter(config.identity for config in configs)
    return tuple(
        _member(
            host,
            entry,
            config,
            unread,
            tuple(other.vmid for other in configs if other.identity == config.identity and other.vmid != config.vmid)
            if config is not None and uuids[config.identity] > 1
            else (),
        )
        for entry, config, unread in read
    )


def members(host: Host) -> tuple[Target, ...]:
    """Every VM of the host's pool, as a target for reading, with no address read and no Owned: Refused where the pool
    cannot be read. A change is made on owned(target)."""
    try:
        return _members(host, _pool(host))
    except (ProxmoxError, TransportError) as error:
        raise Refused(f"{host.name} could not be read: {sentence(error)}") from None


def count_made_by_agent(host: Host) -> int:
    """How many VMs of the host's pool that the fleet owns an agent's run made, as their records say."""
    return sum(1 for target in members(host) if not refusal(target) and target.record and target.record.made_by_agent)


# ------------------------------------------------------------------------------------------ the addresses ----


def _asked(host: Host, target: Target) -> str:
    """What target's guest agent reports as its address on its own network card, asked once whatever is known of
    whether it runs: "" where it reports none, does not run, has no agent or does not answer. Refused at once where
    Proxmox refuses the token the agent's call, told by its status alone (Denied)."""
    if not target.mac:
        return ""
    try:
        interfaces = host.client.agent_interfaces(target.node, target.vmid, host.client.agent_call_most)
    except Denied as error:
        raise Refused(
            f"{error}: reading {target.said}'s address needs {AGENT_PRIVILEGE} on it, which the role on the pool"
            " gives, so no VM's address can be read and nothing was reached"
        ) from None
    except (NotRunning, NoAgent, Answered, Malformed, TimedOut, Unreachable):
        return ""
    except (ProxmoxError, TransportError) as error:
        raise Refused(f"{target.said}'s address could not be asked: {sentence(error)}") from None
    return agent.address(interfaces, target.mac) or ""


def _reported(host: Host, everyone: Sequence[Target]) -> dict[str, str]:
    """Each running member of everyone that the fleet owns, by its identity (one member's alone: two that carry one UUID
    are owned by none), with the address its guest agent reports ("" for none): the members a VM's address is held
    against."""
    return {target.identity: _asked(host, target) for target in everyone if target.running and not refusal(target)}


def _one_address(shared: Sequence[Target], addresses: Sequence[str]) -> str:
    """The line that says the members shared report one address each, and that none of them is reached."""
    names = ", ".join(target.said for target in shared)
    return (
        f"{names} report one address ({', '.join(sorted(set(addresses)))}), so none of them is reached: nothing here"
        " can tell which is telling the truth. Look at each one's address in Proxmox's console."
    )


def address(host: Host, target: Target) -> str | None:
    """target's address, from its guest agent on its own network card, asked once whatever is known of whether it
    runs (a VM the run just started is asked too), and held against the address every other running member of the pool
    that the fleet owns reports: None where it reports none, does not run, has no agent or does not answer; Shared
    where another member reports it too, since nothing tells which is telling the truth, and whatever answers there
    would be taken for this VM. Refused at once where Proxmox refuses the token the agent's call, told by its status
    alone (Denied)."""
    found = _asked(host, target)
    if not found:
        return None
    siblings = [member for member in members(host) if member.identity != target.identity]
    reported = _reported(host, siblings)
    same = [member for member in siblings if reported.get(member.identity) == found]
    if same:
        raise Shared(_one_address([target, *same], [found]), found)
    return found


def _addressed(
    host: Host, targets: Sequence[Target], everyone: Sequence[Target]
) -> tuple[tuple[Target, ...], list[str]]:
    """targets, each owned one that runs given its address; one whose address another running member the fleet owns
    reports too, a target of this read or not, left with none, and said."""
    reported = _reported(host, everyone)
    counts = Counter(found for found in reported.values() if found)
    shared = {identity for identity, found in reported.items() if found and counts[found] > 1}
    found = [
        _kept(target, address="", shared=reported[target.identity])
        if target.identity in shared
        else _kept(target, address=reported[target.identity])
        if target.identity in reported
        else target
        for target in targets
    ]
    asked = {reported[target.identity] for target in targets if target.identity in shared}
    if not asked:
        return tuple(found), []
    sharing = [member for member in everyone if member.identity in shared and reported[member.identity] in asked]
    return tuple(found), [_one_address(sharing, [reported[member.identity] for member in sharing])]


# ---------------------------------------------------------------------------------------------- the names ----


def _them(names: Sequence[str]) -> str:
    return "it" if len(names) == 1 else "them"


def _hold(host: Host, names: Sequence[str], create: bool) -> None:
    """Each name given held before Proxmox is asked anything: see the module's docstring."""
    if not all(isinstance(name, str) and full("vm_name", name) and "," not in name for name in names):
        raise Refused(f"a name given is no VM's name: {PATTERNS['vm_name'].words}")
    if create and not all(VM_NAME.fullmatch(name) for name in names):
        raise Refused(NOT_PROXMOX_S)
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
            raise Refused(sentence(error)) from None
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
    """Whether each name to create was looked for among the host's VMs outside the pool, and the lines that say it was
    not, or that an entry listed with no name could not be read; Refused for a VM of such a name found there, as today's
    role refuses an existing VM it does not own, and for a clone in flight there that names no VM yet."""
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
    lines: list[str] = []
    for entry in listed:
        if entry.type != QEMU or entry.vmid is None or entry.vmid in ours or (entry.name and entry.name not in new):
            continue
        # One of a name to create, or one listed with no name, as the listing gives a guest its node has not reported
        # to the cluster yet: a VM made seconds ago, a clone in flight above all, may be one of a name to create.
        config: VmConfig | None = None
        try:
            config = host.client.config(entry.node, entry.vmid)
        except (Denied, Answered, Malformed) as error:
            if not entry.name:
                lines.append(
                    f"VMID {entry.vmid} on {entry.node} is listed with no name, and its configuration could not be read"
                    f" ({one_line(sentence(error), SAID_MOST)}), so whether it is a VM named {', '.join(new)} is not"
                    " known."
                )
                continue
        name = entry.name or (config.name if config is not None else "")
        lock = config.lock if config is not None else ""
        if not name:
            if lock == CLONING:
                raise Refused(
                    f"VMID {entry.vmid} is being cloned, its configuration under lock: clone and naming no VM yet, and"
                    f" is no member of the pool {host.pool}: it may be one named {', '.join(new)}, made by a run under"
                    " way, or left by one that stopped. Run this again once its clone has ended."
                )
            continue
        if name not in new:
            continue
        if lock == CLONING:
            raise Refused(
                f'A VM named "{name}" (VMID {entry.vmid}) is being cloned, its configuration under lock: clone,'
                f" and is no member of the pool {host.pool} yet: a run making it is under way, or one stopped and left"
                " it. Run this again once its clone has ended."
            )
        template = config.template if config is not None else entry.template
        if template:
            raise Refused(
                f'"{name}" is the name of a template (VMID {entry.vmid}) outside the pool {host.pool}. Refusing'
                " to configure it. Pick a different name."
            )
        raise Refused(
            f'A VM named "{name}" already exists (VMID {entry.vmid}) and is no member of the pool {host.pool}, so'
            " this fleet did not create it. Refusing to change it, or to make a second VM of its name. Pick a different"
            " name."
        )
    return True, lines


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
        raise Refused(sentence(error)) from None
    pool = values["proxmox_pool"].value
    if not pool:
        raise Refused(f"{name} names no pool (proxmox_pool)")
    return client_for(name, fields, anchor, values, host.tokens, host.environ), pool


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
    create), which is a changing run (ValueError without change). change: a run that changes something, which reads
    the pool's mark by D2's rule before anything else and gives each target its Owned through owned(); a read that
    changes nothing gives targets for reading alone. Refused, Missing or NotOwned, in one sentence, before anything is
    changed."""
    if create and not change:
        raise ValueError("a deploy that would create changes something: create is asked with change")
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
                raise Refused(sentence(rule))
        everyone = _members(host, pool)
        if chosen:
            targets, new = _by_name(host, everyone, chosen, create)
        else:
            (targets, lines), new = _every(host, everyone, change), []
        if change:
            targets = [_kept(target, owned=owned(target)) for target in targets]
        outside = True
        if new:
            outside, more = _outside(host, everyone, new)
            lines += more
        if create:
            lines += _elsewhere(host, chosen)
        addressed, more = _addressed(host, targets, everyone)
        lines += more
    except (ProxmoxError, TransportError) as error:
        raise Refused(f"{host.name} could not be read: {sentence(error)}") from None
    return Requested(addressed, tuple(new), tuple(lines), outside)
