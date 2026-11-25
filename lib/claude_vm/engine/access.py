"""What init makes on a Proxmox host: one list of the objects, and a check of each against what exists there
(docs/architecture/engine.md §16.4).

`objects(host)` is the one list of what a first `init` makes on a host with its administrator's authority, in the
order it is made: the pool, its comment the fleet's mark (fleet/mark.py's `render`, `controller=none`); the engine's
role, whose privileges are what pve/privileges.py's one table needs on every kind of path (`needed`), so that one
table says both what the client calls and what init grants; under `proxmox_audit_scope: node`, its default (O15, item
2), the audit role, `VM.Audit` alone; where `proxmox_template_custom` names a template of the person's own outside the
pool, the clone role, `VM.Audit` and `VM.Clone`; the user; the user's grants; the token, with privilege separation, on
the user; and the token's grants, the same as the user's, since a token with privilege separation holds what both
hold. The grants: the engine's role on the pool, on the storage for VM disks, on each bridge a clone's source has a
network card on (a clone asks `SDN.Use` on each), and on the node; the audit role on every guest (`/vms`), read only,
so that `status` shows the node's other VMs and a capacity made from all of them; and the clone role on the person's
template alone, which the pool's role never reaches. Under `pool`, the stricter opt-in, the token holds nothing on a
guest outside the pool but that template. The user's grants come before the token and the token's after it: an ACL
entry can name only a token that exists, and the token's secret, which Proxmox shows once, is held for the least time
a later step could fail with it unsaid (experiment 6).

The names are the engine's own (ruling B1, rule 1): the user `agent-warehouse@pve`, the token `aw`, the roles
`AgentWarehouse`, `AgentWarehouseAudit` and `AgentWarehouseClone`, and the pool `aw-fleet` where neither
`proxmox_pool` nor `--pool` names one. `Host` holds each value of one host to the form Proxmox gives it before any
object is made of it: a pool's name starts with a letter and holds no `/`, which on Proxmox VE 9.2 makes a nested pool.

`check(object, found)` says what is there of one object: missing, or the engine's own, and nothing else. `found` is
what one read of the host gives of the kinds the list makes (`Found`). A pool whose comment does not carry this
fleet's mark is `NotOwn`, refused with fleet/mark.py's one rule's sentence, never a third answer; a role of the
engine's own is told with what it lacks, which is appended and never taken away; and the audit role and the clone
role, each granted on guests outside the pool, are held to their privileges exactly: what one holds beyond them is
told too. `plan(objects, found)` is what a run makes of the list: each object that is missing made, each role of the
engine's own given what it lacks, and a role held exactly that holds more neither changed nor granted, said with the
line that fixes it, since a role is one set of privileges granted on a path, and that role on `/vms` would hand each
privilege it holds over every VM of the node.

This module runs nothing against a host and renders nothing: card E2's script is the one rendering of the list, run
over root SSH on a `y` (E7) or printed for the person to run. Standard library only, with fleet/'s mark and settings
table, pve/'s table of privileges and template judgement, and today's checks.py and text.py.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar

from ..checks import proxmox_name
from ..fleet import mark, record, registry
from ..pve import privileges
from ..pve.template import AUDIT, GUESTS, option
from ..schema import patterns
from ..text import one_line

__all__ = [
    "ANSWERS",
    "AUDIT_ROLE",
    "CLONE_ROLE",
    "MISSING",
    "OWN",
    "POOL",
    "POOL_NAME",
    "POOL_RULE",
    "ROLE",
    "TOKEN",
    "USER",
    "Answer",
    "Found",
    "Grant",
    "Host",
    "NotOwn",
    "Plan",
    "Pool",
    "Refused",
    "Role",
    "Step",
    "Token",
    "User",
    "check",
    "objects",
    "plan",
]

# The engine's own names (ruling B1, rule 1): the token's user and its ID, the role on the pool's paths, the role of
# VM.Audit alone on every guest, the role a person's own template is cloned through, and the pool where none is named.
USER = "agent-warehouse@pve"
TOKEN = "aw"
ROLE = "AgentWarehouse"
AUDIT_ROLE = "AgentWarehouseAudit"
CLONE_ROLE = "AgentWarehouseClone"
POOL = "aw-fleet"
# What the user's and the token's comments say in Proxmox's page: whose they are, as today's do. The pool's is the
# fleet's mark.
COMMENT = mark.WORD
# The privilege a template is cloned with (pve/privileges.py's clone row on its source).
CLONE = "VM.Clone"
# What the token may read beyond its pool: every VM of the node, or its pool's alone (the settings table's row).
SCOPES = registry.row("proxmox_audit_scope").choices
NODE_SCOPE = "node"
# A pool's name as Proxmox makes one, and that rule in words: the settings table's (fleet/registry.py's POOL_NAME),
# which proxmox_pool's form holds a value to where it is written, as SCOPES is the table's.
POOL_NAME = re.compile(registry.POOL_NAME.pattern)
POOL_RULE = registry.POOL_NAME.said
# A user as Proxmox names one, name@realm (verify_username: three to 64 characters), in today's script's characters.
USER_NAME = re.compile(r"[A-Za-z0-9._-]+@[A-Za-z][A-Za-z0-9._-]+")
USER_MOST = 64
# A token's ID, the part after its user's !: Proxmox's pattern, two characters at least.
TOKEN_NAME = re.compile(r"[A-Za-z][A-Za-z0-9._-]+")
# A guest's ID as Proxmox gives one.
GUEST_ID = range(100, 1_000_000_000)
# The most characters of what the host holds that a sentence says.
SAID_MOST = 200

# What check answers.
MISSING, OWN = ANSWERS = ("missing", "own")


class Refused(ValueError):
    """A value of the host's is not one Proxmox makes an object by: the sentence says which, never the value."""


class NotOwn(ValueError):
    """What is there under one of the list's names is not this fleet's: the sentence says why. Nothing is made."""


@dataclass(frozen=True)
class Host:
    """The values of one host the list is made for: the fleet's ID, which the pool's mark names; the node; the storage
    for VM disks; each bridge a clone's source has a network card on, the base template's and a person's template's;
    the pool, the token's user and its ID; what the token may read beyond its pool (one of SCOPES); and the ID of a
    template of the person's own outside the pool, which proxmox_template_custom names, or None. Each is held as it is
    made: Refused says which is not one Proxmox makes an object by."""

    fleet: str
    node: str
    storage: str
    bridges: tuple[str, ...]
    pool: str = POOL
    user: str = USER
    token: str = TOKEN
    audit_scope: str = NODE_SCOPE
    custom_template: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.fleet, str) or not patterns.full("fleet_id", self.fleet):
            raise Refused("the fleet's ID is a UUID in its lowercase canonical form")
        if not isinstance(self.pool, str) or not POOL_NAME.fullmatch(self.pool):
            raise Refused(f"the pool's name is not one Proxmox makes a pool by: {POOL_RULE}")
        if not isinstance(self.user, str) or not USER_NAME.fullmatch(self.user) or len(self.user) > USER_MOST:
            raise Refused(
                "the token's user is not one Proxmox names a user by: a name and its realm joined by @, letters,"
                " digits, _, . and -, the realm from a letter, at most 64 characters"
            )
        if self.user == "root@pam":
            raise Refused("the token's user is a user of the engine's own, never root@pam, which holds every privilege")
        if not isinstance(self.token, str) or not TOKEN_NAME.fullmatch(self.token):
            raise Refused(
                "the token's ID is not one Proxmox makes a token by: letters, digits, _, . and -, from a letter, two"
                " characters at least"
            )
        for value, kind in ((self.node, "node"), (self.storage, "storage")):
            if not proxmox_name(value, kind):
                raise Refused(f"the {kind}'s name is not one Proxmox gives a {kind}")
        if not isinstance(self.bridges, tuple) or not self.bridges:
            raise Refused("a host's bridges are a tuple of one bridge's name at least")
        if not all(proxmox_name(bridge, "bridge") for bridge in self.bridges):
            raise Refused("a bridge's name is not one Proxmox gives a bridge")
        if len(set(self.bridges)) != len(self.bridges):
            raise Refused("a host's bridges name each bridge once")
        if self.audit_scope not in SCOPES:
            raise Refused(f"proxmox_audit_scope is one of {', '.join(SCOPES)}")
        custom = self.custom_template
        if custom is not None and (isinstance(custom, bool) or not isinstance(custom, int) or custom not in GUEST_ID):
            raise Refused("a template's ID is a whole number from 100 to 999999999, as Proxmox gives a guest's")

    @classmethod
    def of(
        cls,
        settings: Mapping[str, object],
        *,
        fleet: str,
        user: str = USER,
        token: str = TOKEN,
        custom_template: int | None = None,
        custom_bridges: Sequence[str] = (),
    ) -> Host:
        """The host settings describe, every row's value for it: its node, its storage, the base template's bridge
        (proxmox_template_bridge, else proxmox_bridge), its pool (POOL where proxmox_pool is empty) and its audit
        scope; with the fleet's ID, the token's user and ID, and, where proxmox_template_custom names a template of
        the person's own outside the pool, its ID and the bridges of its network cards, read from its configuration."""
        bridges = (str(option(settings, "proxmox_template_bridge")), *custom_bridges)
        return cls(
            fleet=fleet,
            node=str(settings["proxmox_node"]),
            storage=str(settings["proxmox_storage"]),
            bridges=tuple(dict.fromkeys(bridges)),
            pool=str(settings["proxmox_pool"] or POOL),
            user=user,
            token=token,
            audit_scope=str(settings["proxmox_audit_scope"]),
            custom_template=custom_template,
        )


# ---------------------------------------------------------------- the objects ----


@dataclass(frozen=True)
class Pool:
    """The fleet's pool, by its name, with its comment: the fleet's mark."""

    what: ClassVar[str] = "pool"
    name: str
    comment: str

    @property
    def id(self) -> str:
        return self.name


@dataclass(frozen=True)
class Role:
    """A role of the engine's own, by its ID, with its privileges, sorted. exact: granted on guests outside the pool,
    so held to these privileges exactly, where the engine's role is held to having them and may hold more."""

    what: ClassVar[str] = "role"
    name: str
    privileges: tuple[str, ...]
    exact: bool = False

    @property
    def id(self) -> str:
        return self.name


@dataclass(frozen=True)
class User:
    """The token's user, by its ID (name@realm), with its comment; it has no password and signs in by its token
    alone."""

    what: ClassVar[str] = "user"
    name: str
    comment: str

    @property
    def id(self) -> str:
        return self.name


@dataclass(frozen=True)
class Token:
    """The one token, by its user and its ID, with its comment, made with privilege separation: it holds what its own
    grants and its user's both give."""

    what: ClassVar[str] = "token"
    user: str
    name: str
    comment: str
    privsep: bool = True

    @property
    def id(self) -> str:
        """Its full ID, as an ACL entry and the token's own requests name it."""
        return f"{self.user}!{self.name}"


@dataclass(frozen=True)
class Grant:
    """A role granted one user or token (subject: a user's ID, or a token's full ID) on one ACL path, propagating to
    the paths under it, as Proxmox grants by default."""

    what: ClassVar[str] = "grant"
    path: str
    role: str
    subject: str
    propagate: bool = True

    @property
    def id(self) -> str:
        """The grant as host.yml's made records it: its path, its role and whom it is for."""
        return f"{self.path} {self.role} {self.subject}"


Object = Pool | Role | User | Token | Grant
KINDS: tuple[type, ...] = (Pool, Role, User, Token, Grant)


def _path(kind: str, value: str) -> str:
    """The ACL path of one object of a kind of pve/privileges.py's table, named by its value."""
    key, template = privileges.KINDS[kind]
    return template.format(**{key: value})


def engine_privileges() -> tuple[str, ...]:
    """The engine's role: every privilege the one table needs on any kind of path, sorted. A pool's include every one
    checked on a VM, which the pool's members take from it; and no row needs one only on a VM a clone has not made
    yet, which no grant of init's could reach."""
    return tuple(sorted(frozenset().union(*(privileges.needed(kind) for kind in privileges.KINDS))))


def objects(host: Host) -> tuple[Object, ...]:
    """Every object init makes on the host, in the order it is made (the module's docstring)."""
    if not isinstance(host, Host):
        raise TypeError(f"the list is made for a Host, not a {type(host).__name__}")
    pool = Pool(host.pool, mark.render(mark.Mark(host.fleet, mark.NONE), ""))
    roles = [Role(ROLE, engine_privileges())]
    granted = [
        (_path("pool", host.pool), ROLE),
        (_path("storage", host.storage), ROLE),
        *((_path("bridge", bridge), ROLE) for bridge in host.bridges),
        (_path("node", host.node), ROLE),
    ]
    if host.audit_scope == NODE_SCOPE:
        roles.append(Role(AUDIT_ROLE, (AUDIT,), exact=True))
        granted.append((GUESTS, AUDIT_ROLE))
    if host.custom_template is not None:
        roles.append(Role(CLONE_ROLE, tuple(sorted((AUDIT, CLONE))), exact=True))
        granted.append((_path("vm", str(host.custom_template)), CLONE_ROLE))
    user = User(host.user, COMMENT)
    token = Token(host.user, host.token, COMMENT)
    return (
        pool,
        *roles,
        user,
        *(Grant(path, role, user.id) for path, role in granted),
        token,
        *(Grant(path, role, token.id) for path, role in granted),
    )


# ---------------------------------------------------------------- what exists, and the check ----


@dataclass(frozen=True, eq=False)
class Found:
    """What exists on a host of the kinds the list makes, as one read of it gives them: each pool's comment by its name
    ("" for one with none), each role's privileges by its ID, the users' IDs, the tokens' full IDs, and the ACL's
    entries, each a Grant. An empty Found is a host with none of them."""

    pools: Mapping[str, str] = field(default_factory=dict)
    roles: Mapping[str, Iterable[str]] = field(default_factory=dict)
    users: Iterable[str] = ()
    tokens: Iterable[str] = ()
    grants: Iterable[Grant] = ()

    def __post_init__(self) -> None:
        pools = dict(self.pools)
        if not all(isinstance(name, str) and isinstance(comment, str) for name, comment in pools.items()):
            raise TypeError("a pool's comment is a str, by the pool's name")
        roles = {name: frozenset(held) for name, held in dict(self.roles).items()}
        if not all(isinstance(name, str) and all(isinstance(one, str) for one in held) for name, held in roles.items()):
            raise TypeError("a role's privileges are each a str, by the role's ID")
        users, tokens, grants = frozenset(self.users), frozenset(self.tokens), frozenset(self.grants)
        if not all(isinstance(each, str) for each in users | tokens):
            raise TypeError("a user's and a token's IDs are each a str")
        if not all(isinstance(each, Grant) for each in grants):
            raise TypeError("an ACL entry is a Grant")
        object.__setattr__(self, "pools", MappingProxyType(pools))
        object.__setattr__(self, "roles", MappingProxyType(roles))
        object.__setattr__(self, "users", users)
        object.__setattr__(self, "tokens", tokens)
        object.__setattr__(self, "grants", grants)


@dataclass(frozen=True)
class Answer:
    """What check found of one object: MISSING or OWN; for a role of the engine's own, the privileges it lacks
    (lacking), which are appended to it, and for a role held exactly, those it holds beyond its own (beyond)."""

    state: str
    lacking: tuple[str, ...] = ()
    beyond: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in ANSWERS:
            raise ValueError(f"check answers one of {', '.join(ANSWERS)}")


def _pool(pool: Pool, found: Found) -> Answer:
    there = found.pools.get(pool.name)
    if there is None:
        return Answer(MISSING)
    ours = mark.parse(pool.comment)
    assert ours is not None, "the list's pool carries the fleet's mark"
    try:
        theirs = mark.parse(there)
    except mark.Refused as unreadable:
        raise NotOwn(str(unreadable)) from None
    refusal = mark.may_change(theirs, record.first(ours.fleet))
    if refusal is not None:
        raise NotOwn(refusal)
    return Answer(OWN)


def _role(role: Role, found: Found) -> Answer:
    held = found.roles.get(role.name)
    if held is None:
        return Answer(MISSING)
    wanted = frozenset(role.privileges)
    lacking = tuple(sorted(wanted - frozenset(held)))
    beyond = tuple(sorted(frozenset(held) - wanted)) if role.exact else ()
    return Answer(OWN, lacking, beyond)


def check(obj: Object, found: Found) -> Answer:
    """What is there of one object of the list: MISSING, or OWN, the engine's own, and nothing else. A pool is OWN only
    where its comment carries this fleet's mark, controller=none; any other pool of its name is NotOwn, with
    fleet/mark.py's sentence. A role is told with what it lacks, and a role held exactly with what it holds beyond its
    privileges too. A grant is OWN only where the entry is there as the list makes it, its propagation included, so
    that one made otherwise is made again as the list makes it."""
    if not isinstance(found, Found):
        raise TypeError(f"what exists is a Found, not a {type(found).__name__}")
    if isinstance(obj, Pool):
        return _pool(obj, found)
    if isinstance(obj, Role):
        return _role(obj, found)
    if isinstance(obj, User):
        return Answer(OWN if obj.name in found.users else MISSING)
    if isinstance(obj, Token):
        return Answer(OWN if obj.id in found.tokens else MISSING)
    if isinstance(obj, Grant):
        return Answer(OWN if obj in found.grants else MISSING)
    raise TypeError(f"an object of the list is one of {', '.join(kind.__name__ for kind in KINDS)}")


# ---------------------------------------------------------------- what a run makes of it ----


@dataclass(frozen=True)
class Step:
    """One change a run makes: the object made, or, with append, those privileges appended to a role of the engine's
    own, none taken away."""

    object: Object
    append: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    """What a run makes of the list on a host: its steps, in the list's order, and what it says of what it leaves as
    it is."""

    steps: tuple[Step, ...]
    said: tuple[str, ...]


def _too_much(role: Role, beyond: tuple[str, ...], paths: Sequence[str]) -> str:
    """The sentence for a role held exactly that holds more than its privileges, with the line that fixes it."""
    own = ", ".join(role.privileges)
    more = one_line(", ".join(beyond), SAID_MOST)
    return (
        f"The role {role.name} holds {more} beyond {own}, so it is not granted: on {', '.join(paths)} it would give the"
        f" token each of them over every guest there. As root on the Proxmox host, pveum role modify {role.name}"
        f" --privs {','.join(role.privileges)} keeps it to {own}, and init then grants it."
    )


def plan(listed: Sequence[Object], found: Found) -> Plan:
    """What a run makes of listed, objects(host)'s list, against what exists (found): each object that is missing made,
    and each role of the engine's own that lacks a privilege given it, none taken away. A role held exactly that holds
    more is neither changed nor granted, and said. A pool that is not this fleet's is NotOwn before any step: nothing
    is made."""
    answered = [(obj, check(obj, found)) for obj in listed]
    withheld = {obj.name: answer.beyond for obj, answer in answered if isinstance(obj, Role) and answer.beyond}
    said = []
    for obj, _ in answered:
        if isinstance(obj, Role) and obj.name in withheld:
            paths = dict.fromkeys(each.path for each in listed if isinstance(each, Grant) and each.role == obj.name)
            said.append(_too_much(obj, withheld[obj.name], list(paths)))
    steps = []
    for obj, answer in answered:
        if (isinstance(obj, Role) and obj.name in withheld) or (isinstance(obj, Grant) and obj.role in withheld):
            continue
        if answer.state == MISSING:
            steps.append(Step(obj))
        elif answer.lacking:
            steps.append(Step(obj, answer.lacking))
    return Plan(tuple(steps), tuple(said))
