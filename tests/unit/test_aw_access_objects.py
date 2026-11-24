"""What init makes on a Proxmox host, and the check of each object against what exists there
(lib/claude_vm/engine/access.py; docs/architecture/engine.md §16.4).

The list is the pool with the fleet's mark, the engine's role, the audit role under `proxmox_audit_scope: node`, the
clone role where a person's own template is outside the pool, the user, the user's grants, the token with privilege
separation and the token's grants, each grant for the user and for the token alike, in the order a run makes them; and
nothing else. The engine's role holds what pve/privileges.py's one table needs on every kind of path, so a call added
to the table widens it, and one whose privilege no grant could reach fails here. Under `node` the token holds
`VM.Audit` on `/vms` through the audit role alone, never the engine's role; under `pool`, nothing on a guest outside the
pool but a person's own template. Every value is held to the form Proxmox gives it, a pool's name from a letter and with
no `/`, and no refusal says the value back.

The invariant with a test of its own: `check` answers missing or the engine's own and nothing else, a pool that is not
this fleet's being refused, never a third answer; and a role of the engine's own that lacks a privilege gets it
appended and loses none. A role held exactly that holds more (the audit role, the clone role) is neither changed nor
granted, and said with the line that fixes it. The module imports the standard library alone.
"""

from __future__ import annotations

import dataclasses
import sys

import pytest
from aw_kit import imports_of
from harness import ENGINE_POOL, ENGINE_ROLE, ENGINE_TOKEN, ENGINE_USER

from claude_vm.engine import access
from claude_vm.engine import init as engine_init
from claude_vm.fleet import hosts, mark, record, registry
from claude_vm.pve import privileges
from claude_vm.pve.privileges import Call

FLEET = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
OTHER = "0d6c9b1e-2f3a-4b5c-8d7e-6f5a4b3c2d1e"
CONTROLLER = "a1b2c3d4-e5f6-4a7b-9c8d-0e1f2a3b4c5d"
MARK = f"agent-warehouse fleet={FLEET} controller=none"
# A note of the person's own in a pool's comment, which no sentence ever says back.
NOTE = "unit-test-aw-access-note"
USER = "agent-warehouse@pve"
TOKEN = "agent-warehouse@pve!aw"
TEMPLATE = 9500
GUEST_PATHS = "/vms"


def _host(**changes: object) -> access.Host:
    values: dict[str, object] = {"fleet": FLEET, "node": "pve", "storage": "local-lvm", "bridges": ("vmbr0",)}
    return access.Host(**{**values, **changes})  # type: ignore[arg-type]


# The hosts the list is made for: the default, the pool alone, a person's own template beside each, and more bridges.
HOSTS = {
    "node": _host(),
    "pool": _host(audit_scope="pool"),
    "node, a template of the person's own": _host(custom_template=TEMPLATE, bridges=("vmbr0", "vmbr1")),
    "pool, a template of the person's own": _host(audit_scope="pool", custom_template=TEMPLATE),
    "another pool, user and token": _host(pool="dev-vms", user="automation@pve", token="automation"),
}


def _grants(listed: tuple[access.Object, ...]) -> list[access.Grant]:
    return [each for each in listed if isinstance(each, access.Grant)]


def _roles(listed: tuple[access.Object, ...]) -> dict[str, access.Role]:
    return {each.name: each for each in listed if isinstance(each, access.Role)}


def _everything(listed: tuple[access.Object, ...]) -> access.Found:
    """A host that holds every object of listed as the list makes it."""
    return access.Found(
        pools={each.name: each.comment for each in listed if isinstance(each, access.Pool)},
        roles={each.name: each.privileges for each in listed if isinstance(each, access.Role)},
        users=[each.name for each in listed if isinstance(each, access.User)],
        tokens=[each.id for each in listed if isinstance(each, access.Token)],
        grants=_grants(listed),
    )


def _without(found: access.Found, gone: access.Object) -> access.Found:
    """found with one object of the list not there."""
    return access.Found(
        pools={name: comment for name, comment in found.pools.items() if (gone.what, gone.id) != ("pool", name)},
        roles={name: held for name, held in found.roles.items() if (gone.what, gone.id) != ("role", name)},
        users=found.users - ({gone.id} if gone.what == "user" else set()),
        tokens=found.tokens - ({gone.id} if gone.what == "token" else set()),
        grants=found.grants - {gone},
    )


def _with_role(found: access.Found, role: str, held: set[str]) -> access.Found:
    return dataclasses.replace(found, roles={**found.roles, role: held})


def _applied(found: access.Found, steps: tuple[access.Step, ...]) -> access.Found:
    """found once a run has made each step: an object made as the list makes it, or privileges appended to a role, as
    Proxmox's role modify --append does, which takes none away."""
    pools, roles = dict(found.pools), {name: set(held) for name, held in found.roles.items()}
    users, tokens, grants = set(found.users), set(found.tokens), set(found.grants)
    for step in steps:
        obj = step.object
        if step.append:
            assert isinstance(obj, access.Role) and obj.name in roles, "only a role there is appended to"
            roles[obj.name] |= set(step.append)
        elif isinstance(obj, access.Pool):
            pools[obj.name] = obj.comment
        elif isinstance(obj, access.Role):
            roles[obj.name] = set(obj.privileges)
        elif isinstance(obj, access.User):
            users.add(obj.name)
        elif isinstance(obj, access.Token):
            tokens.add(obj.id)
        else:
            grants = {each for each in grants if each.id != obj.id} | {obj}
    return access.Found(pools=pools, roles=roles, users=users, tokens=tokens, grants=grants)


def _kind_of(path: str) -> str | None:
    """The kind of pve/privileges.py's table an ACL path is of; None for /vms, every guest at once."""
    for kind, (_, template) in privileges.KINDS.items():
        start = template.split("{", 1)[0]
        if kind != "new" and path.startswith(start) and "/" not in path[len(start) :]:
            return kind
    assert path == GUEST_PATHS, path
    return None


def _ungranted(listed: tuple[access.Object, ...], subject: str) -> dict[str, set[str]]:
    """What the table needs on each kind of path that no role the list grants subject holds there: a VM's through its
    pool, whose roles a member takes; a new VM's through nothing, since no grant of init's reaches a VM a clone has not
    made yet."""
    roles = _roles(listed)
    held: dict[str | None, set[str]] = {}
    for grant in _grants(listed):
        if grant.subject == subject:
            held.setdefault(_kind_of(grant.path), set()).update(roles[grant.role].privileges)
    reached_through = {"vm": "pool", "new": "nothing"}
    lacking = {}
    for kind in privileges.KINDS:
        missing = set(privileges.needed(kind)) - held.get(reached_through.get(kind, kind), set())
        if missing:
            lacking[kind] = missing
    return lacking


# ---------------------------------------------------------------- the list ----


def test_the_list_is_the_pool_the_roles_the_user_its_grants_the_token_and_its_grants() -> None:
    listed = access.objects(_host())
    grants = [
        "/pool/aw-fleet AgentWarehouse",
        "/storage/local-lvm AgentWarehouse",
        "/sdn/zones/localnetwork/vmbr0 AgentWarehouse",
        "/nodes/pve AgentWarehouse",
        "/vms AgentWarehouseAudit",
    ]
    assert [(each.what, each.id) for each in listed] == [
        ("pool", "aw-fleet"),
        ("role", "AgentWarehouse"),
        ("role", "AgentWarehouseAudit"),
        ("user", USER),
        *(("grant", f"{grant} {USER}") for grant in grants),
        ("token", TOKEN),
        *(("grant", f"{grant} {TOKEN}") for grant in grants),
    ]
    # Experiment 6's count with the defaults: two roles, the pool, the user, the token and ten grants.
    assert len(listed) == 15
    token = next(each for each in listed if isinstance(each, access.Token))
    assert (token.user, token.name, token.privsep) == (USER, "aw", True)
    assert all(grant.propagate for grant in _grants(listed))


@pytest.mark.parametrize("host", HOSTS.values(), ids=HOSTS)
def test_each_object_comes_after_what_it_names_and_the_token_between_the_user_s_grants_and_its_own(host) -> None:
    listed = access.objects(host)
    made: set[tuple[str, str]] = set()
    for obj in listed:
        if isinstance(obj, access.Grant):
            assert ("role", obj.role) in made
            assert ("token" if "!" in obj.subject else "user", obj.subject) in made
            if _kind_of(obj.path) == "pool":
                assert ("pool", host.pool) in made
        if isinstance(obj, access.Token):
            assert ("user", obj.user) in made
        made.add((obj.what, obj.id))
    at = next(index for index, obj in enumerate(listed) if isinstance(obj, access.Token))
    users = [index for index, obj in enumerate(listed) if isinstance(obj, access.Grant) and "!" not in obj.subject]
    tokens = [index for index, obj in enumerate(listed) if isinstance(obj, access.Grant) and "!" in obj.subject]
    assert max(users) < at < min(tokens)
    # The token's grants are the user's, path by path and role by role: privilege separation gives what both hold.
    by_user = [(grant.path, grant.role) for grant in _grants(listed) if "!" not in grant.subject]
    assert by_user == [(grant.path, grant.role) for grant in _grants(listed) if "!" in grant.subject]


@pytest.mark.parametrize("host", HOSTS.values(), ids=HOSTS)
def test_no_object_is_outside_the_pool_the_roles_the_user_the_token_the_grants_and_the_mark(host) -> None:
    listed = access.objects(host)
    assert {obj.what for obj in listed} == {"pool", "role", "user", "token", "grant"}
    assert {obj.what for obj in listed} <= set(hosts.MADE)
    assert [type(obj) for obj in listed if not isinstance(obj, (access.Role, access.Grant))] == [
        access.Pool,
        access.User,
        access.Token,
    ]
    (pool,) = [obj for obj in listed if isinstance(obj, access.Pool)]
    user, token = (obj for obj in listed if isinstance(obj, (access.User, access.Token)))
    assert pool.name == host.pool
    # The pool's comment is the fleet's mark and nothing beside it, controller=none.
    assert pool.comment == f"agent-warehouse fleet={host.fleet} controller=none"
    assert mark.parse(pool.comment) == mark.Mark(host.fleet, mark.NONE)
    assert (user.comment, token.comment) == ("agent-warehouse", "agent-warehouse")
    assert {grant.subject for grant in _grants(listed)} == {user.id, token.id}
    assert {grant.role for grant in _grants(listed)} == set(_roles(listed))
    # Every role holds privileges of the one table alone: none of the list's own.
    named = {call.privileges[0] for call in privileges.CALLS if call.privileges}
    assert all(set(role.privileges) <= named for role in _roles(listed).values())
    # Each is recorded in host.yml's made as it is named, when this engine makes it.
    for obj in listed:
        assert hosts.MADE_ID.fullmatch(obj.id)
        assert hosts.Made(obj.what, obj.id, "engine").id == obj.id
    assert len({(obj.what, obj.id) for obj in listed}) == len(listed)


def test_the_names_are_the_engine_s_own_and_one_set() -> None:
    assert (access.USER, access.TOKEN, access.POOL) == ("agent-warehouse@pve", "aw", "aw-fleet")
    assert (access.ROLE, access.AUDIT_ROLE, access.CLONE_ROLE) == (
        "AgentWarehouse",
        "AgentWarehouseAudit",
        "AgentWarehouseClone",
    )
    # init writes the same names into a fleet folder where none is given (card E4), and the harness makes the same
    # objects in the fake: held equal here until init reads them from this module.
    assert (engine_init.TOKEN_USER, engine_init.TOKEN_ID, engine_init.POOL) == (access.USER, access.TOKEN, access.POOL)
    harness = (ENGINE_USER, ENGINE_TOKEN, ENGINE_POOL, ENGINE_ROLE)
    assert harness == (access.USER, access.TOKEN, access.POOL, access.ROLE)
    host = _host()
    assert (host.user, host.token, host.pool, host.audit_scope) == (access.USER, access.TOKEN, access.POOL, "node")


# ---------------------------------------------------------------- the roles: A6's one table ----


@pytest.mark.parametrize("host", HOSTS.values(), ids=HOSTS)
def test_the_engine_s_role_holds_what_the_table_needs_and_every_need_is_granted_where_it_is_checked(host) -> None:
    listed = access.objects(host)
    role = _roles(listed)[access.ROLE]
    union = set().union(*(privileges.needed(kind) for kind in privileges.KINDS))
    assert set(role.privileges) == union and list(role.privileges) == sorted(union)
    assert not role.exact
    for subject in (host.user, f"{host.user}!{host.token}"):
        assert _ungranted(listed, subject) == {}
    on = {_kind_of(grant.path) for grant in _grants(listed) if grant.role == access.ROLE}
    assert on == {"pool", "storage", "bridge", "node"}
    assert {grant.path for grant in _grants(listed) if _kind_of(grant.path) == "bridge"} == {
        f"/sdn/zones/localnetwork/{bridge}" for bridge in host.bridges
    }


def test_a_call_added_to_the_table_widens_the_role_and_one_no_grant_reaches_is_found(monkeypatch) -> None:
    # The role is the table's, read when the list is made: a call added with its privilege is granted with no list of
    # this module's to change.
    added = Call("unit_test_call", "GET", "/nodes/{node}/unit-test", "Sys.Modify", "node")
    monkeypatch.setattr(privileges, "CALLS", (*privileges.CALLS, added))
    listed = access.objects(_host())
    assert "Sys.Modify" in _roles(listed)[access.ROLE].privileges
    assert _ungranted(listed, USER) == {}
    # One checked only on a VM a clone has not made yet, which no grant of init's reaches, is named.
    unreachable = Call("unit_test_call", "POST", "/nodes/{node}/unit-test", "VM.Allocate", ("new",))
    monkeypatch.setattr(privileges, "CALLS", (*privileges.CALLS, unreachable))
    assert _ungranted(access.objects(_host()), USER) == {"new": {"VM.Allocate"}}


def test_under_node_the_token_reads_every_guest_through_the_audit_role_alone() -> None:
    listed = access.objects(_host())
    audit = _roles(listed)[access.AUDIT_ROLE]
    assert (audit.privileges, audit.exact) == (("VM.Audit",), True)
    on_guests = [grant for grant in _grants(listed) if grant.path.startswith(GUEST_PATHS)]
    assert [(grant.path, grant.role, grant.subject) for grant in on_guests] == [
        (GUEST_PATHS, access.AUDIT_ROLE, USER),
        (GUEST_PATHS, access.AUDIT_ROLE, TOKEN),
    ]
    # The engine's role, which writes, is granted on no guest outside the pool.
    assert not [grant for grant in _grants(listed) if grant.role == access.ROLE and grant.path.startswith(GUEST_PATHS)]


def test_under_pool_the_token_holds_nothing_on_a_guest_outside_the_pool() -> None:
    listed = access.objects(_host(audit_scope="pool"))
    assert set(_roles(listed)) == {access.ROLE}
    assert not [grant for grant in _grants(listed) if grant.path.startswith(GUEST_PATHS)]
    assert {(grant.path, grant.role) for grant in _grants(listed)} == {
        ("/pool/aw-fleet", access.ROLE),
        ("/storage/local-lvm", access.ROLE),
        ("/sdn/zones/localnetwork/vmbr0", access.ROLE),
        ("/nodes/pve", access.ROLE),
    }
    assert len(listed) == 12


@pytest.mark.parametrize("scope", ["node", "pool"])
def test_a_template_of_the_person_s_own_is_read_and_cloned_through_a_role_of_its_own_on_its_path_alone(scope) -> None:
    listed = access.objects(_host(audit_scope=scope, custom_template=TEMPLATE, bridges=("vmbr0", "vmbr1")))
    clone = _roles(listed)[access.CLONE_ROLE]
    assert (clone.privileges, clone.exact) == (("VM.Audit", "VM.Clone"), True)
    path = f"{GUEST_PATHS}/{TEMPLATE}"
    assert [(grant.role, grant.subject) for grant in _grants(listed) if grant.path == path] == [
        (access.CLONE_ROLE, USER),
        (access.CLONE_ROLE, TOKEN),
    ]
    guests = {grant.path for grant in _grants(listed) if grant.path.startswith(GUEST_PATHS)}
    assert guests == ({GUEST_PATHS, path} if scope == "node" else {path})
    # Its bridges are granted beside the base template's, since a clone asks SDN.Use on each.
    assert {grant.path for grant in _grants(listed) if _kind_of(grant.path) == "bridge"} == {
        "/sdn/zones/localnetwork/vmbr0",
        "/sdn/zones/localnetwork/vmbr1",
    }


# ---------------------------------------------------------------- the host's values ----


def _settings(**changes: object) -> dict[str, object]:
    return {
        "proxmox_node": "pve",
        "proxmox_storage": "local-lvm",
        "proxmox_bridge": "vmbr0",
        "proxmox_pool": "",
        "proxmox_audit_scope": registry.row("proxmox_audit_scope").default,
        **changes,
    }


def test_the_host_is_read_from_its_settings() -> None:
    assert access.SCOPES == registry.row("proxmox_audit_scope").choices == ("node", "pool")
    assert access.Host.of(_settings(), fleet=FLEET) == _host()
    assert access.Host.of(_settings(proxmox_pool="dev-vms"), fleet=FLEET).pool == "dev-vms"
    assert access.Host.of(_settings(proxmox_audit_scope="pool"), fleet=FLEET).audit_scope == "pool"
    given = access.Host.of(_settings(), fleet=FLEET, user="automation@pve", token="automation")
    assert (given.user, given.token) == ("automation@pve", "automation")
    # The base template's bridge, which falls back to proxmox_bridge, then a person's template's, each once.
    assert access.Host.of(_settings(proxmox_template_bridge="vmbr1"), fleet=FLEET).bridges == ("vmbr1",)
    custom = access.Host.of(_settings(), fleet=FLEET, custom_template=TEMPLATE, custom_bridges=("vmbr2", "vmbr0"))
    assert (custom.bridges, custom.custom_template) == (("vmbr0", "vmbr2"), TEMPLATE)


REFUSED = [
    *(("pool", value, "the pool's name") for value in ("a/b", "1pool", "_pool", "my pool", "a:b", "unitpool\n", "")),
    ("pool", "p" * 65, "the pool's name"),
    *(("user", value, "the token's user") for value in ("agent-warehouse", "a@b", "x@pve\n", "a b@pve", "@pve")),
    ("user", "u" * 61 + "@pve", "at most 64 characters"),
    ("user", "root@pam", "never root@pam"),
    *(("token", value, "the token's ID") for value in ("a", "1aw", "aw\n", "a w", "aw!x", "")),
    ("node", "pve\n", "the node's name"),
    ("node", "-pve", "the node's name"),
    ("storage", "local lvm", "the storage's name"),
    ("bridges", (), "one bridge's name at least"),
    ("bridges", ["vmbr0"], "one bridge's name at least"),
    ("bridges", ("vm br",), "a bridge's name"),
    ("bridges", ("vmbr0\n",), "a bridge's name"),
    ("bridges", ("vmbr0", "vmbr0"), "each bridge once"),
    ("audit_scope", "all", "proxmox_audit_scope is one of node, pool"),
    *(("custom_template", value, "a template's ID") for value in (99, 0, True, "9500", 10**9)),
    *(("fleet", value, "the fleet's ID") for value in (FLEET.upper(), "", FLEET + "\n")),
]


@pytest.mark.parametrize(("name", "value", "words"), REFUSED)
def test_a_value_proxmox_makes_no_object_by_is_refused_before_any_and_never_said_back(name, value, words) -> None:
    with pytest.raises(access.Refused, match=words) as refused:
        _host(**{name: value})
    if isinstance(value, str) and len(value) > 3 and value != "root@pam":
        assert value.strip() not in str(refused.value) and "\n" not in str(refused.value)


# ---------------------------------------------------------------- the check: missing or the engine's own ----


def _found_states(listed: tuple[access.Object, ...]) -> list[access.Found]:
    """What a host may hold of the list: nothing, everything, everything but one object, each role lacking a
    privilege or holding one more, each grant there but not propagating, and a user, a token or a grant of another
    name beside them."""
    everything = _everything(listed)
    states = [access.Found(), everything, *(_without(everything, obj) for obj in listed)]
    for role in _roles(listed).values():
        states.append(_with_role(everything, role.name, set(role.privileges[1:])))
        states.append(_with_role(everything, role.name, {*role.privileges, "Sys.Modify"}))
        states.append(_with_role(everything, role.name, set()))
    for grant in _grants(listed):
        moved = dataclasses.replace(grant, propagate=False)
        states.append(dataclasses.replace(everything, grants={*everything.grants - {grant}, moved}))
    states.append(
        access.Found(
            users=["someone@pve"],
            tokens=["someone@pve!aw"],
            grants=[access.Grant("/pool/aw-fleet", access.ROLE, "someone@pve")],
            roles={"PVEAuditor": {"VM.Audit"}},
        )
    )
    return states


@pytest.mark.parametrize("host", HOSTS.values(), ids=HOSTS)
def test_check_answers_missing_or_the_engine_s_own_and_nothing_else(host) -> None:
    listed = access.objects(host)
    answered = set()
    for found in _found_states(listed):
        for obj in listed:
            answer = access.check(obj, found)
            assert answer.state in (access.MISSING, access.OWN)
            if answer.state == access.MISSING:
                assert (answer.lacking, answer.beyond) == ((), ())
            answered.add((obj.what, answer.state))
    kinds = ("pool", "role", "user", "token", "grant")
    assert answered == {(what, state) for what in kinds for state in access.ANSWERS}
    with pytest.raises(ValueError, match="check answers one of missing, own"):
        access.Answer("today's")


@pytest.mark.parametrize("host", HOSTS.values(), ids=HOSTS)
def test_what_the_list_makes_is_the_engine_s_own_and_what_is_not_there_missing(host) -> None:
    listed = access.objects(host)
    everything = _everything(listed)
    assert [access.check(obj, everything) for obj in listed] == [access.Answer(access.OWN)] * len(listed)
    assert [access.check(obj, access.Found()) for obj in listed] == [access.Answer(access.MISSING)] * len(listed)
    for obj in listed:
        assert access.check(obj, _without(everything, obj)).state == access.MISSING
    # A grant that does not propagate is not the list's grant: made again, as the list makes it.
    for grant in _grants(listed):
        moved = dataclasses.replace(grant, propagate=False)
        assert access.check(grant, dataclasses.replace(everything, grants={moved})).state == access.MISSING


@pytest.mark.parametrize(
    "comment",
    [MARK, f"{NOTE}\n{MARK}", f"{MARK} {NOTE}", f"x6 note\n{MARK}"],
    ids=["the mark", "a note above it", "a note beside it", "experiment 6's comment"],
)
def test_a_pool_whose_comment_carries_this_fleet_s_mark_is_the_engine_s_own(comment) -> None:
    (pool, *_) = access.objects(_host())
    assert access.check(pool, access.Found(pools={"aw-fleet": comment})) == access.Answer(access.OWN)


@pytest.mark.parametrize(
    ("comment", "words"),
    [
        ("", "carries no mark of a fleet"),
        (NOTE, "carries no mark of a fleet"),
        (f"agent-warehouse fleet={OTHER} controller=none {NOTE}", f"names another fleet, {OTHER}"),
        (f"{NOTE} agent-warehouse fleet={FLEET} controller={CONTROLLER}", "this fleet has a control VM"),
        (f"agent-warehouse fleet={FLEET} controller=nobody {NOTE}", "does not read as one"),
        (f"{MARK}\n{MARK}", "more than once"),
    ],
)
def test_a_pool_that_is_not_this_fleet_s_is_refused_with_the_mark_s_sentence_and_nothing_planned(comment, words):
    listed = access.objects(_host())
    found = access.Found(pools={"aw-fleet": comment})
    with pytest.raises(access.NotOwn, match=words) as refused:
        access.check(listed[0], found)
    assert NOTE not in str(refused.value)
    if words.startswith(("carries", "names", "this fleet")):
        theirs = mark.parse(comment)
        assert str(refused.value) == mark.may_change(theirs, record.first(FLEET))
    with pytest.raises(access.NotOwn, match=words):
        access.plan(listed, found)


def test_check_and_plan_take_only_the_list_s_objects_and_a_found() -> None:
    (pool, *_) = access.objects(_host())
    with pytest.raises(TypeError, match="a Found"):
        access.check(pool, {"aw-fleet": MARK})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="one of Pool, Role, User, Token, Grant"):
        access.check("aw-fleet", access.Found())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="a Host"):
        access.objects({"pool": "aw-fleet"})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="an ACL entry is a Grant"):
        access.Found(grants=[("/vms", access.AUDIT_ROLE, USER)])  # type: ignore[list-item]


# ---------------------------------------------------------------- what a run makes of it ----


@pytest.mark.parametrize("host", HOSTS.values(), ids=HOSTS)
def test_a_fresh_host_gets_every_object_in_order_and_a_second_run_changes_nothing(host) -> None:
    listed = access.objects(host)
    first = access.plan(listed, access.Found())
    assert first == access.Plan(tuple(access.Step(obj) for obj in listed), ())
    after = _applied(access.Found(), first.steps)
    assert all(access.check(obj, after).state == access.OWN for obj in listed)
    assert access.plan(listed, after) == access.Plan((), ())


def test_only_what_is_missing_is_made() -> None:
    listed = access.objects(_host())
    everything = _everything(listed)
    for obj in listed:
        assert access.plan(listed, _without(everything, obj)).steps == (access.Step(obj),)
    grant = _grants(listed)[0]
    moved = {*everything.grants - {grant}, dataclasses.replace(grant, propagate=False)}
    assert access.plan(listed, dataclasses.replace(everything, grants=moved)).steps == (access.Step(grant),)


@pytest.mark.parametrize("held", [{"VM.Audit"}, {"VM.Audit", "Sys.Modify"}, set()], ids=["one", "and more", "none"])
def test_a_role_of_the_engine_s_own_that_lacks_a_privilege_gets_it_appended_and_loses_none(held) -> None:
    listed = access.objects(_host())
    wanted = set(_roles(listed)[access.ROLE].privileges)
    found = _with_role(_everything(listed), access.ROLE, held)
    answer = access.check(_roles(listed)[access.ROLE], found)
    lacking = tuple(sorted(wanted - held))
    assert answer == access.Answer(access.OWN, lacking)
    planned = access.plan(listed, found)
    assert planned == access.Plan((access.Step(_roles(listed)[access.ROLE], lacking),), ())
    after = _applied(found, planned.steps)
    assert set(after.roles[access.ROLE]) == wanted | held
    assert access.plan(listed, after) == access.Plan((), ())


@pytest.mark.parametrize(
    ("host", "role", "own", "path"),
    [
        (_host(), access.AUDIT_ROLE, "VM.Audit", GUEST_PATHS),
        (_host(custom_template=TEMPLATE), access.CLONE_ROLE, "VM.Audit,VM.Clone", f"{GUEST_PATHS}/{TEMPLATE}"),
    ],
    ids=["the audit role", "the clone role"],
)
def test_a_role_held_exactly_that_holds_more_is_said_with_its_fix_and_not_granted(host, role, own, path) -> None:
    listed = access.objects(host)
    held = {*own.split(","), "VM.Allocate"}
    fresh = _with_role(access.Found(), role, held)
    assert access.check(_roles(listed)[role], fresh) == access.Answer(access.OWN, (), ("VM.Allocate",))
    planned = access.plan(listed, fresh)
    made = [step.object for step in planned.steps]
    # Neither the role nor any grant of it, on its path, for the user or the token; everything else as on a fresh host.
    assert made == [obj for obj in listed if obj != _roles(listed)[role] and getattr(obj, "role", None) != role]
    assert not [obj for obj in made if isinstance(obj, access.Grant) and obj.path == path]
    assert all(not step.append for step in planned.steps)
    (said,) = planned.said
    assert said == (
        f"The role {role} holds VM.Allocate beyond {own.replace(',', ', ')}, so it is not granted: on {path} it would"
        f" give the token each of them over every guest there. As root on the Proxmox host, pveum role modify {role}"
        f" --privs {own} keeps it to {own.replace(',', ', ')}, and init then grants it."
    )
    # Once it holds exactly its privileges it is granted; and one of its name that lacks one is given it, and granted.
    exact = _with_role(_applied(fresh, planned.steps), role, set(own.split(",")))
    regranted = access.plan(listed, exact)
    assert regranted.said == () and {step.object.path for step in regranted.steps} == {path}
    lacking = access.plan(listed, _with_role(access.Found(), role, set()))
    assert lacking.said == () and access.Step(_roles(listed)[role], tuple(own.split(","))) in lacking.steps
    assert {step.object.path for step in lacking.steps if isinstance(step.object, access.Grant)} >= {path}


def test_what_a_host_holds_is_said_as_one_line_of_plain_text() -> None:
    listed = access.objects(_host())
    found = _with_role(access.Found(), access.AUDIT_ROLE, {"VM.Audit", "Sys.Modify\n\x1b[2Jrm -rf /"})
    (said,) = access.plan(listed, found).said
    assert "\n" not in said and "\x1b" not in said


# ---------------------------------------------------------------- what the module imports ----


def test_the_module_imports_nothing_outside_the_standard_library(home) -> None:
    added = imports_of(module="claude_vm.engine.access", home=home) - imports_of(module="claude_vm.engine", home=home)
    assert "claude_vm.engine.access" in added, sorted(added)
    assert sorted({name.split(".")[0] for name in added} - set(sys.stdlib_module_names) - {"claude_vm"}) == []
    assert not added & {"asyncio", "subprocess"}
