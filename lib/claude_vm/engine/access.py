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
template alone, which the pool's role never reaches. Under `pool`, the stricter opt-in, the token's writes and its
reads of single VMs stay within the pool and that template; under both, `Sys.Audit` on the node, which `status` needs
for the node's capacity, also reads the node's report, and that report holds every guest's configuration. The user's
grants come before the token and the token's after it: an ACL entry can name only a token that exists, and the
token's secret, which Proxmox shows once, is held for the least time a later step could fail with it unsaid
(experiment 6).

The names are the engine's own (ruling B1, rule 1): the user `agent-warehouse@pve`, the token `aw`, the roles
`AgentWarehouse`, `AgentWarehouseAudit` and `AgentWarehouseClone`, and the pool `aw-fleet` where neither
`proxmox_pool` nor `--pool` names one. `Host` holds each value of one host to the form Proxmox gives it before any
object is made of it: a pool's name starts with a letter and holds no `/`, which on Proxmox VE 9.2 makes a nested pool.

`check(object, found)` says what is there of one object: missing, or the engine's own, and nothing else. `found` is
what one read of the host gives of the kinds the list makes (`Found`). A pool whose comment does not carry this
fleet's mark, and a token of the list's ID made without privilege separation, which holds every privilege of its user,
are `NotOwn`, each refused with its sentence, never a third answer; a role is told with what it lacks, which is
appended and never taken away, and with what it holds beyond its privileges. `plan(objects, found)` is what a run
makes of the list: each object that is missing made and each role given what it lacks. The engine's role holding more
is granted all the same, and said with the line that keeps it to its own; the audit role and the clone role, each
granted on guests outside the pool, are held to their privileges exactly: one that holds more is neither changed nor
granted, and said with the line that fixes it, or, where its grants are there already, said to give each privilege
now, since a role is one set of privileges granted on a path, and that role on `/vms` hands each privilege it holds
over every VM of the node.

`as_script(host, custom)` is the one rendering of the list: the script init runs on the host as root, over root SSH
on a `y` (E7), or prints for the person to run there (`aw init --print-commands`), rendered each time and never written
into the checkout. Its options change the values the list is made of, and a person's own template, which
`proxmox_template_custom` names (custom), is looked up on the host by its name, with the bridges its network cards are
on: so each object is rendered as one call of the script's fixed interpreter (pve/script.py), the values its shell
variables, made from the list of a host of placeholders, and held at every rendering to the list itself for each
scope, with and without the template, over one bridge and two. The checks of each value and the sentences the script
says are this module's and fleet/mark.py's, rendered: what the script decides is what `plan` decides of the same
host. This module runs nothing against a host and writes nothing. Standard library only, with fleet/'s mark and
settings table, pve/'s table of privileges, template judgement and the script's fixed text, and today's checks.py and
text.py.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from types import MappingProxyType
from typing import ClassVar

from ..checks import PROXMOX_NAMES, proxmox_name
from ..fleet import mark, record, registry
from ..pve import privileges, script
from ..pve.template import AUDIT, GUESTS, option
from ..schema import patterns
from ..schema.printer import said
from ..schema.words import PLACEHOLDER, Line
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
    "as_script",
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
# What the token may read of single VMs beyond its pool: every VM of the node, or its pool's alone (the settings
# table's row).
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
# A person's own template, by the name proxmox_template_custom gives it: a name Proxmox gives a guest, a DNS name's
# characters, from and to a letter or a digit, at most 63.
TEMPLATE_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,61}[A-Za-z0-9])?")

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
    ("" for one with none), each role's privileges by its ID, the users' IDs, each token's privilege separation by its
    full ID (True for a token made with it), and the ACL's entries, each a Grant. An empty Found is a host with none of
    them."""

    pools: Mapping[str, str] = field(default_factory=dict)
    roles: Mapping[str, Iterable[str]] = field(default_factory=dict)
    users: Iterable[str] = ()
    tokens: Mapping[str, bool] = field(default_factory=dict)
    grants: Iterable[Grant] = ()

    def __post_init__(self) -> None:
        pools = dict(self.pools)
        if not all(isinstance(name, str) and isinstance(comment, str) for name, comment in pools.items()):
            raise TypeError("a pool's comment is a str, by the pool's name")
        roles = {name: frozenset(held) for name, held in dict(self.roles).items()}
        if not all(isinstance(name, str) and all(isinstance(one, str) for one in held) for name, held in roles.items()):
            raise TypeError("a role's privileges are each a str, by the role's ID")
        if not isinstance(self.tokens, Mapping):
            raise TypeError("a token's privilege separation is a bool, by the token's full ID")
        tokens = dict(self.tokens)
        if not all(isinstance(name, str) and isinstance(privsep, bool) for name, privsep in tokens.items()):
            raise TypeError("a token's privilege separation is a bool, by the token's full ID")
        users, grants = frozenset(self.users), frozenset(self.grants)
        if not all(isinstance(each, str) for each in users):
            raise TypeError("a user's ID is a str")
        if not all(isinstance(each, Grant) for each in grants):
            raise TypeError("an ACL entry is a Grant")
        object.__setattr__(self, "pools", MappingProxyType(pools))
        object.__setattr__(self, "roles", MappingProxyType(roles))
        object.__setattr__(self, "users", users)
        object.__setattr__(self, "tokens", MappingProxyType(tokens))
        object.__setattr__(self, "grants", grants)


@dataclass(frozen=True)
class Answer:
    """What check found of one object: MISSING or OWN; for a role, the privileges it lacks (lacking), which are appended
    to it, and those it holds beyond its own (beyond)."""

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
    beyond = tuple(sorted(frozenset(held) - wanted))
    return Answer(OWN, lacking, beyond)


def _token(token: Token, found: Found) -> Answer:
    privsep = found.tokens.get(token.id)
    if privsep is None:
        return Answer(MISSING)
    if privsep != token.privsep:
        beside = Line("init", {"token_id": PLACEHOLDER}, host_flag="")
        raise NotOwn(
            said(
                "The token {token} of {user} is there without privilege separation, so it holds every privilege its"
                " user holds, not only its own grants: it is not one init makes. As root on the Proxmox host, pveum"
                " user token delete {user} {token} deletes it, and init then makes it again; or {line}, with an ID of"
                " the fleet's own, makes one beside it.",
                {"token": token.name, "user": token.user, "line": beside},
            )
        )
    return Answer(OWN)


def check(obj: Object, found: Found) -> Answer:
    """What is there of one object of the list: MISSING, or OWN, the engine's own, and nothing else. A pool is OWN only
    where its comment carries this fleet's mark, controller=none; any other pool of its name is NotOwn, with
    fleet/mark.py's sentence. A token is OWN only where its full ID is there with privilege separation; one made
    without it is NotOwn. A role is told with what it lacks and what it holds beyond its privileges. A grant is OWN
    only where the entry is there as the list makes it, its propagation included, so that one made otherwise is made
    again as the list makes it."""
    if not isinstance(found, Found):
        raise TypeError(f"what exists is a Found, not a {type(found).__name__}")
    if isinstance(obj, Pool):
        return _pool(obj, found)
    if isinstance(obj, Role):
        return _role(obj, found)
    if isinstance(obj, User):
        return Answer(OWN if obj.name in found.users else MISSING)
    if isinstance(obj, Token):
        return _token(obj, found)
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


def _too_much(role: Role, beyond: tuple[str, ...], paths: Sequence[str], granted: Sequence[str]) -> str:
    """The sentence for a role that holds more than its privileges, with the line that keeps it to them: the engine's
    role, granted all the same (paths); a role held exactly, not granted, or granted already on some of its paths
    (granted)."""
    own = ", ".join(role.privileges)
    more = one_line(", ".join(beyond), SAID_MOST)
    fix = f"As root on the Proxmox host, pveum role modify {role.name} --privs {','.join(role.privileges)} keeps it to"
    if not role.exact:
        return (
            f"The role {role.name} holds {more} beyond what the engine needs, and is granted on {', '.join(paths)} all"
            f" the same, since init never takes a privilege away: there the token holds each of them. {fix} what the"
            " engine needs."
        )
    over = "that template" if role.name == CLONE_ROLE else "every guest there"
    if granted:
        return (
            f"The role {role.name} holds {more} beyond {own} and is granted on {', '.join(granted)} already: there it"
            f" gives each of them over {over} now. {fix} {own}."
        )
    return (
        f"The role {role.name} holds {more} beyond {own}, so it is not granted: on {', '.join(paths)} it would give the"
        f" token each of them over {over}. {fix} {own}, and init then grants it."
    )


def plan(listed: Sequence[Object], found: Found) -> Plan:
    """What a run makes of listed, objects(host)'s list, against what exists (found): each object that is missing made,
    and each role that lacks a privilege given it, none taken away. The engine's role holding more is granted all the
    same, and said; a role held exactly that holds more is neither changed nor granted, and said, as granted already
    where its grants are there. A pool or a token that is not this fleet's is NotOwn before any step: nothing is
    made."""
    answered = [(obj, check(obj, found)) for obj in listed]
    beyond = {obj.name: answer.beyond for obj, answer in answered if isinstance(obj, Role) and answer.beyond}
    withheld = {name for name, role in _roles_of(listed).items() if name in beyond and role.exact}
    said = []
    for name, role in _roles_of(listed).items():
        if name in beyond:
            grants = [each for each in listed if isinstance(each, Grant) and each.role == name]
            paths = list(dict.fromkeys(each.path for each in grants))
            granted = list(dict.fromkeys(each.path for each in grants if each in found.grants))
            said.append(_too_much(role, beyond[name], paths, granted))
    steps = []
    for obj, answer in answered:
        if (isinstance(obj, Role) and obj.name in withheld) or (isinstance(obj, Grant) and obj.role in withheld):
            continue
        if answer.state == MISSING:
            steps.append(Step(obj))
        elif answer.lacking:
            steps.append(Step(obj, answer.lacking))
    return Plan(tuple(steps), tuple(said))


def _roles_of(listed: Sequence[Object]) -> dict[str, Role]:
    return {each.name: each for each in listed if isinstance(each, Role)}


# ---------------------------------------------------------------- the script ----

# The values the script takes as options, each its shell variable in the rendered calls: the list is made for a host of
# placeholders, each a value its check holds and that nothing else of a list holds (an _ is in no node's name and no
# fleet's ID), and each placeholder is rendered as its variable. `each` is the bridge of the loop over the bridges.
_POOL, _STORAGE, _BRIDGE, _SECOND_BRIDGE = "Z_pool_1", "Z_storage_1", "Z_bridge_1", "Z_bridge_2"
_USER, _TOKEN = "Z_user_1@Z_realm_1", "Z_token_1"
_PLACES = {_POOL: "pool", _STORAGE: "storage", _BRIDGE: "each", _USER: "user", _TOKEN: "token"}
# The sentences' own: the privileges a role holds beyond its own, its grants' paths and those granted already (the
# arguments of the script's `sentence`), and the fleet or control VM a mark names.
_MORE, _PATHS, _GRANTED = "Z_more_1", "Z_paths_1", "Z_granted_1"
_SAID = {_MORE: "2", _PATHS: "3", _GRANTED: "4"}
_OTHERS = ("00000000-0000-4000-8000-000000000000", "00000000-0000-4000-8000-000000000001")
# A role's ID, which the script keeps values under.
_ROLE_ID = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True)
class _Entry:
    """One call of the rendered list: the object, of the placeholders' host; the scopes it is made under; whether only
    with a person's own template outside the pool; and whether once for each bridge."""

    obj: Object
    scopes: tuple[str, ...]
    custom: bool
    per_bridge: bool


def _placed(host: Host, scope: str, custom_id: int | None, bridges: tuple[str, ...]) -> Host:
    return Host(
        fleet=host.fleet,
        node=host.node,
        storage=_STORAGE,
        bridges=bridges,
        pool=_POOL,
        user=_USER,
        token=_TOKEN,
        audit_scope=scope,
        custom_template=custom_id,
    )


def _texts(obj: Object) -> list[str]:
    return [value for each in fields(obj) if isinstance(value := getattr(obj, each.name), str)]


def _bridged(obj: Object, bridge: str) -> Object:
    """obj with the bridge's placeholder made bridge: a grant's path is the one text of the list that names a bridge,
    which the shape's check holds."""
    return replace(obj, path=obj.path.replace(_BRIDGE, bridge)) if isinstance(obj, Grant) else obj


def _shape(host: Host, custom_id: int) -> tuple[_Entry, ...]:
    """The list as the script makes it: each object of the placeholders' host, with when it is made. Held to the list
    itself under every scope, with and without the template, over one bridge and two: a list of another shape is
    refused, never rendered otherwise than it is."""
    one, two = (_BRIDGE,), (_BRIDGE, _SECOND_BRIDGE)
    listed = {
        (scope, made): objects(_placed(host, scope, custom_id if made else None, one))
        for scope in SCOPES
        for made in (True, False)
    }
    entries = tuple(
        _Entry(
            obj,
            tuple(scope for scope in SCOPES if obj in listed[scope, True]),
            any(obj in listed[scope, True] and obj not in listed[scope, False] for scope in SCOPES),
            any(_BRIDGE in text for text in _texts(obj)),
        )
        for obj in max(listed.values(), key=len)
    )
    for scope in SCOPES:
        for made in (True, False):
            for bridges in (one, two):
                rendered = tuple(
                    _bridged(entry.obj, bridge)
                    for entry in entries
                    if scope in entry.scopes and (made or not entry.custom)
                    for bridge in (bridges if entry.per_bridge else one)
                )
                if rendered != objects(_placed(host, scope, custom_id if made else None, bridges)):
                    raise RuntimeError(f"the list under {scope} is not of a shape the script renders")
    return entries


def _word(text: str, places: Mapping[str, str]) -> str:
    """text as one shell word: quoted as shlex quotes it where it holds no placeholder, else in double quotes with each
    placeholder its variable."""
    if not any(place in text for place in places):
        return shlex.quote(text)
    pattern = "|".join(re.escape(place) for place in sorted(places, key=len, reverse=True))
    parts = re.split(f"({pattern})", text)
    quoted = "".join(
        f"${{{places[part]}}}" if index % 2 else re.sub(r'([\\"$`])', r"\\\1", part) for index, part in enumerate(parts)
    )
    return f'"{quoted}"'


def _call(obj: Object, tokens: frozenset[str], places: Mapping[str, str]) -> str:
    """One object as one call of the script's interpreter."""
    if isinstance(obj, Pool):
        words = ("Pool", _word(obj.name, places), _word(obj.comment, places))
    elif isinstance(obj, Role):
        if not _ROLE_ID.fullmatch(obj.name):
            raise RuntimeError("a role's ID is letters alone, which the script keeps values under")
        words = ("Role", _word(obj.name, places), _word(",".join(obj.privileges), places), str(int(obj.exact)))
    elif isinstance(obj, User):
        words = ("User", _word(obj.name, places), _word(obj.comment, places))
    elif isinstance(obj, Token):
        words = ("Token", _word(obj.user, places), _word(obj.name, places), _word(obj.comment, places))
        words += (str(int(obj.privsep)),)
    else:
        if not obj.propagate or not _ROLE_ID.fullmatch(obj.role):
            raise RuntimeError("the script makes a grant of a role of its own, propagating, as pveum makes one")
        kind = "tokens" if obj.subject in tokens else "users"
        words = ("Grant", _word(obj.path, places), _word(obj.role, places), kind, _word(obj.subject, places))
    return " ".join(words)


def _objects_function(entries: Sequence[_Entry], places: Mapping[str, str]) -> str:
    """The script's `objects`: each call, under the scopes it is made under, the template's lookup, and the loop over
    the bridges."""
    tokens = frozenset(entry.obj.id for entry in entries if isinstance(entry.obj, Token))
    lines = ["objects() {"]
    for entry in entries:
        conditions = []
        if entry.scopes != SCOPES:
            conditions.append(" || ".join(f'[ "$audit_scope" = {shlex.quote(scope)} ]' for scope in entry.scopes))
        if entry.custom:
            conditions.append('[ -n "$custom_id" ]')
        body = [_call(entry.obj, tokens, places)]
        if entry.per_bridge:
            body = ["for each in $bridges; do", *(f"  {line}" for line in body), "done"]
        if conditions:
            body = [f"if {' && '.join(conditions)}; then", *(f"  {line}" for line in body), "fi"]
        lines += [f"  {line}" for line in body]
    return "\n".join([*lines, "}", ""])


def _refusal(host: Host, changed: Mapping[str, object]) -> str:
    """The sentence Host refuses host with, changed so."""
    values = {each.name: getattr(host, each.name) for each in fields(host)}
    try:
        Host(**{**values, **changed})
    except Refused as refused:
        return str(refused)
    raise RuntimeError(f"a host of {', '.join(changed)} so is not refused")


def _unreadable(comment: str) -> str:
    try:
        mark.parse(comment)
    except mark.Unreadable as unreadable:
        return str(unreadable)
    raise RuntimeError("a comment that does not read as a mark is read as one")


def _printed(text: str, places: Mapping[str, str] | None = None) -> str:
    return f"printf '%s' {_word(text, places or {})}"


def _sentence_function(host: Host, entries: Sequence[_Entry]) -> str:
    """The script's `sentence <key> [more paths granted]`: each sentence it says that this module or fleet/mark.py
    words, with the values it names its variables."""
    listed = [entry.obj for entry in entries]
    cases = [
        (f"refused-{key}", _printed(_refusal(host, changed)))
        for key, changed in (
            ("pool", {"pool": "1"}),
            ("user", {"user": "x"}),
            ("root", {"user": "root@pam"}),
            ("token", {"token": "x"}),
            ("storage", {"storage": "-"}),
            ("bridge", {"bridges": ("-",)}),
            ("scope", {"audit_scope": "-"}),
        )
    ]
    ours = record.first(host.fleet)
    other = next(each for each in _OTHERS if each != host.fleet)
    found = {other: "found"}
    cases += [
        ("pool-unmarked", _printed(str(mark.may_change(None, ours)))),
        ("pool-other", _printed(str(mark.may_change(mark.Mark(other, mark.NONE), ours)), found)),
        ("pool-controlled", _printed(str(mark.may_change(mark.Mark(host.fleet, other), ours)), found)),
        ("pool-doubled", _printed(_unreadable(f"{mark.WORD} fleet= {mark.WORD} fleet="))),
        ("pool-unreadable", _printed(_unreadable(f"{mark.WORD} fleet="))),
    ]
    token = next(each for each in listed if isinstance(each, Token))
    try:
        check(token, Found(tokens={token.id: not token.privsep}))
    except NotOwn as not_own:
        cases.append(("token-not-own", _printed(str(not_own), {_USER: "user", _TOKEN: "token"})))
    else:
        raise RuntimeError("a token of the list's ID made otherwise is taken for the engine's own")
    for role in _roles_of(listed).values():
        apart = _printed(_too_much(role, (_MORE,), [_PATHS], []), _SAID)
        if role.exact:
            granted = _printed(_too_much(role, (_MORE,), [_PATHS], [_GRANTED]), _SAID)
            apart = f'if [ -n "${{4}}" ]; then {granted}; else {apart}; fi'
        cases.append((f"too-much-{role.name}", apart))
    lines = ["sentence() {", '  case "$1" in', *(f"  {key}) {body} ;;" for key, body in cases), "  esac", "}"]
    return "\n".join([*lines, ""])


# The access script's first lines: what it is, how it is run, and its options with their values for this host. Said
# through the printer, which writes init's two lines, the one that rendered it and the one that prints it, as Lines;
# every other value is one the host's checks held to its plain form.
_HEAD = """#!/bin/sh
# Rendered by {init} for node {node} of fleet {fleet}: the API access the engine uses on this Proxmox VE host, and
# nothing else. Run it on the host as root, as init does over root SSH on a y. Each command it would run, one an
# object, and then the same, run:
#
#   {print_commands} | ssh root@<host> sh -s -- --dry-run
#   {print_commands} | ssh root@<host> sh -s
#
# It makes whichever of the objects init makes is missing, in their order, and changes nothing that is there but a
# role of the engine's own, to which it adds a privilege it lacks, never taking one away: the pool, its comment the
# fleet's mark; the roles; the user; the user's grants; the token, with privilege separation; and the token's grants.
# A pool another fleet marked, or with no mark, and a token made without privilege separation are refused, and
# nothing is changed.
#
# Options:
#   --storage <id>           the storage for VM disks: {storage}
#   --bridge <name>          the bridge the base template's network card is on: {bridge}
#   --user <user@realm>      the token's user: {user}
#   --token <id>             the token's ID: {token}
#   --pool <name>            the fleet's pool: {pool}
#   --audit-scope {scopes}  what the token may read of single VMs beyond its pool: {scope}
#   --take-unmarked          with --pool: take that pool, whose comment carries no mark, the mark added at its end
#   --no-token               make no token: the token must be there, or nothing changes
#   --dry-run                print the commands it would run, one an object, and run none
#   --list                   print this node's storages for VM disks and its bridges, and change nothing
#
# A person's own template, which proxmox_template_custom names, is looked up here by its name: {custom}.
#
# stdout carries only data: the token's secret, one line, where it makes the token; the commands with --dry-run; the
# lines "storage <id>" and "bridge <name>" with --list. Everything said to a person goes to stderr.
"""


def as_script(host: Host, custom: str = "", *, host_flag: str = "") -> str:
    """The script that makes host's list on the host, run there as root as `sh -s`: rendered for host, whose values
    are its options' defaults, its one bridge the base template's (`--bridge`); custom is the name of a person's own
    template, which proxmox_template_custom names, looked up on the host with the bridges of its network cards, or ""
    for none. Its header says it was rendered by aw init for host's node and fleet, and the lines that print and run
    it, init's as the printer writes them, with host_flag as the printer takes it: a further host's name, "" with one
    host. Refused where custom is not a name Proxmox gives a guest; a host given a template's ID, or the bridges of
    one, is the caller's mistake: the script looks them up. ValueError where the printer refuses host_flag."""
    if not isinstance(host, Host):
        raise TypeError(f"the script is rendered for a Host, not a {type(host).__name__}")
    if host.custom_template is not None or len(host.bridges) != 1:
        raise ValueError(
            "the script looks a person's own template and its bridges up on the host, by the name given as custom:"
            " it is rendered for a host of the base template's bridge alone, and no template's ID"
        )
    if not isinstance(custom, str) or (custom and not TEMPLATE_NAME.fullmatch(custom)):
        raise Refused(
            "proxmox_template_custom is not a name Proxmox gives a guest: letters, digits, . and -, from and to a"
            " letter or a digit, at most 63 characters"
        )
    custom_id = next(each for each in range(999_999_001, 999_999_999) if str(each) not in host.fleet + host.node)
    entries = _shape(host, custom_id)
    places = {**_PLACES, str(custom_id): "custom_id"}
    head = said(
        _HEAD,
        {
            "init": Line("init", host_flag=host_flag),
            "print_commands": Line("init", {"print_commands": True}, host_flag=host_flag),
            "node": host.node,
            "fleet": host.fleet,
            "storage": host.storage,
            "bridge": host.bridges[0],
            "user": host.user,
            "token": host.token,
            "pool": host.pool,
            "scopes": "|".join(SCOPES),
            "scope": host.audit_scope,
            "custom": custom or "(none)",
        },
    )
    values = [
        ("node", host.node),
        ("fleet", host.fleet),
        ("storage", host.storage),
        ("bridge", host.bridges[0]),
        ("pool", host.pool),
        ("user", host.user),
        ("token", host.token),
        ("audit_scope", host.audit_scope),
        ("custom", custom),
        ("mark_line", mark.Mark(host.fleet, mark.NONE).line),
        ("said_most", str(SAID_MOST)),
        ("user_most", str(USER_MOST)),
        ("re_pool", POOL_NAME.pattern),
        ("re_user", USER_NAME.pattern),
        ("re_token", TOKEN_NAME.pattern),
        ("re_storage", PROXMOX_NAMES["storage"].pattern),
        ("re_bridge", PROXMOX_NAMES["bridge"].pattern),
        ("re_scope", "|".join(re.escape(scope) for scope in SCOPES)),
    ]
    functions = _sentence_function(host, entries) + _objects_function(entries, places)
    text = script.render(head, [(name, shlex.quote(value)) for name, value in values], functions)
    left = [place for place in (*places, *_SAID, *_OTHERS) if place in text and place not in host.fleet]
    if left:
        raise RuntimeError("a placeholder of the list is left in the script")
    return text
