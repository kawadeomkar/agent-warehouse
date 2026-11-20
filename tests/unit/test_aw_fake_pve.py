"""The fake Proxmox's pools, users, roles, ACL entries and tokens (tests/molecule/fake_pve_api.py), which the new
engine's Proxmox client and init are written against: made and read through the routes Proxmox VE 9 has, a token made
with or without privilege separation answered from the ACL entries as Proxmox answers it, and today's tokens answered as
before.

The rule with a test of its own: a run of today's sends the fake what it sent before these routes, as recorded in
tests/unit/fake_pve_requests.json from the fake as it was (its methods, paths and parameter names, nothing else).
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from harness import (
    ENGINE_POOL,
    ENGINE_PRIVILEGES,
    PVE_SECRET,
    _pve,
    _pve_requests,
    engine_token,
    fake_fleet,
)

TODAYS = ("ansible@pve!ansible", PVE_SECRET)
RECORDED = Path(__file__).with_name("fake_pve_requests.json")
ROLE = ["VM.Allocate", "VM.Clone", "VM.Config.CPU", "VM.Config.Cloudinit", "VM.Config.Disk", "VM.Config.Memory"]
ROLE += ["VM.Config.Network", "VM.Config.Options", "VM.PowerMgmt", "VM.Audit", "Datastore.AllocateSpace"]
ROLE += ["Datastore.Audit", "SDN.Use"]


@pytest.fixture
def fleet(tmp_path):
    yield from fake_fleet(tmp_path)


def _call(base: str, method: str, endpoint: str, auth: tuple[str, str] = TODAYS, **params) -> tuple[int, object]:
    """One request to the fake at base as the token auth gives, its ID and its secret: the status, and the answer's
    data, or the reason the fake gave for refusing it."""
    request = urllib.request.Request(
        f"{base}/api2/json/{endpoint}",
        data=urllib.parse.urlencode(params).encode() if params and method != "GET" else None,
        method=method,
        headers={"Authorization": f"PVEAPIToken={auth[0]}={auth[1]}"},
    )
    if method == "GET" and params:
        request.full_url += "?" + urllib.parse.urlencode(params)
    unverified = ssl.create_default_context()
    unverified.check_hostname = False
    unverified.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, timeout=10, context=unverified) as answer:
            return answer.status, json.loads(answer.read() or b"{}").get("data")
    except urllib.error.HTTPError as refused:
        return refused.code, refused.reason


def _ok(base: str, method: str, endpoint: str, auth: tuple[str, str] = TODAYS, **params) -> object:
    status, data = _call(base, method, endpoint, auth, **params)
    assert status == 200, (method, endpoint, status, data)
    return data


def _token(base: str, user: str, name: str, **params) -> tuple[str, str]:
    """A token made through the API: its full ID and its secret."""
    made = _ok(base, "POST", f"access/users/{user}/token/{name}", **params)
    assert made["full-tokenid"] == f"{user}!{name}" and set(made) == {"full-tokenid", "info", "value"}, made
    return made["full-tokenid"], made["value"]


def _permissions(base: str, auth: tuple[str, str], path: str | None = None) -> dict:
    return _ok(base, "GET", "access/permissions", auth, **({"path": path} if path else {}))


# --------------------------------------------------------------------------------------------- pools ---
def test_a_pool_is_made_read_given_members_and_a_comment_and_deleted_once_empty(fleet: str) -> None:
    assert _ok(fleet, "POST", "pools", poolid="fleet", comment="made here") is None
    assert {"poolid": "fleet", "comment": "made here"} in _ok(fleet, "GET", "pools")
    assert _ok(fleet, "GET", "pools", poolid="fleet") == [{"poolid": "fleet", "comment": "made here", "members": []}]
    # An empty pool is listed by the cluster too, as a guest's pool always was.
    assert {"id": "/pool/fleet", "type": "pool", "pool": "fleet"} in _ok(fleet, "GET", "cluster/resources", type="pool")

    _ok(fleet, "PUT", "pools", poolid="fleet", vms="9010,9011", storage="local-lvm", comment="changed")
    (pool,) = _ok(fleet, "GET", "pools", poolid="fleet")
    assert pool["comment"] == "changed"
    assert [(m["type"], m.get("vmid"), m.get("storage")) for m in pool["members"]] == [
        ("qemu", 9010, None),
        ("qemu", 9011, None),
        ("storage", None, "local-lvm"),
    ]
    assert [m["vmid"] for m in _ok(fleet, "GET", "pools", poolid="fleet", type="qemu")[0]["members"]] == [9010, 9011]
    assert [m["storage"] for m in _ok(fleet, "GET", "pools", poolid="fleet", type="storage")[0]["members"]] == [
        "local-lvm"
    ]
    listed = {vm["vmid"]: vm.get("pool") for vm in _ok(fleet, "GET", "cluster/resources")}
    assert (listed[9010], listed[9011], listed[9012]) == ("fleet", "fleet", None)

    # The deprecated per-pool forms, answered the same way: the one pool, not a list of it.
    assert _ok(fleet, "GET", "pools/fleet")["members"] == pool["members"]
    _ok(fleet, "PUT", "pools/fleet", vms="9011", delete=1)
    assert [m.get("vmid") for m in _ok(fleet, "GET", "pools/fleet")["members"]] == [9010, None]

    assert _call(fleet, "DELETE", "pools", poolid="fleet") == (
        500,
        "delete pool failed: pool 'fleet' is not empty (contains VM 9010)",
    )
    _ok(fleet, "PUT", "pools", poolid="fleet", vms="9010", storage="local-lvm", delete=1)
    assert _ok(fleet, "DELETE", "pools/fleet") is None
    assert _call(fleet, "GET", "pools", poolid="fleet") == (500, "pool 'fleet' does not exist")
    assert all(pool["poolid"] != "fleet" for pool in _ok(fleet, "GET", "pools"))


def test_a_clone_given_a_pool_is_listed_as_its_member(fleet: str) -> None:
    _ok(fleet, "POST", "pools", poolid="fleet")
    _ok(fleet, "POST", "nodes/pve/qemu/9000/clone", newid=9020, name="pooled", pool="fleet")
    (pool,) = _ok(fleet, "GET", "pools", poolid="fleet")
    assert [(m["vmid"], m["name"]) for m in pool["members"]] == [(9020, "pooled")]
    assert "comment" not in pool
    # As before: a clone into a pool nobody made puts it in that pool, which then exists.
    _ok(fleet, "POST", "nodes/pve/qemu/9000/clone", newid=9021, name="elsewhere", pool="unmade")
    assert [m["vmid"] for m in _ok(fleet, "GET", "pools/unmade")["members"]] == [9021]


def test_a_guest_of_another_pool_moves_only_with_allow_move_and_a_refusal_writes_nothing(fleet: str) -> None:
    for name in ("one", "two"):
        _ok(fleet, "POST", "pools", poolid=name)
    _ok(fleet, "PUT", "pools", poolid="one", vms="9011")
    assert _call(fleet, "PUT", "pools", poolid="two", vms="9010,9011") == (
        500,
        "update pools failed: VM 9011 belongs already to pool 'one' and 'allow-move' is not set",
    )
    assert _ok(fleet, "GET", "pools/two")["members"] == []
    _ok(fleet, "PUT", "pools", poolid="two", vms="9010 9011", **{"allow-move": 1})
    assert [m["vmid"] for m in _ok(fleet, "GET", "pools/two")["members"]] == [9010, 9011]
    assert _ok(fleet, "GET", "pools/one")["members"] == []


@pytest.mark.parametrize(
    ("method", "endpoint", "params", "said"),
    [
        ("POST", "pools", {"poolid": "fleet"}, "create pool failed: pool 'fleet' already exists"),
        ("POST", "pools", {"poolid": "9pool"}, "create pool failed: pool name must start with a letter"),
        ("PUT", "pools", {"poolid": "absent", "vms": "9010"}, "update pools failed: pool 'absent' does not exist"),
        ("PUT", "pools", {"poolid": "fleet", "vms": "9011"}, "update pools failed: VM 9011 is already a pool member"),
        (
            "PUT",
            "pools",
            {"poolid": "fleet", "vms": "9010", "delete": 1},
            "update pools failed: VM 9010 is not a pool member",
        ),
        ("PUT", "pools", {"poolid": "fleet", "vms": "4242"}, "update pools failed: VM 4242 does not exist"),
        (
            "PUT",
            "pools",
            {"poolid": "fleet", "storage": "x", "delete": 1},
            "update pools failed: Storage 'x' is not a pool member",
        ),
        ("DELETE", "pools", {"poolid": "absent"}, "delete pool failed: pool 'absent' does not exist"),
        ("POST", "access/users", {"userid": "engine@pve"}, "create user failed: user 'engine@pve' already exists"),
        ("POST", "access/users", {"userid": "x@pve", "groups": "admins"}, "create user failed: no such group 'admins'"),
        ("POST", "access/roles", {"roleid": "Engine"}, "create role failed: role 'Engine' already exists"),
        (
            "PUT",
            "access/acl",
            {"path": "/vms", "roles": "Absent", "users": "engine@pve"},
            "ACL update failed: role 'Absent' does not exist",
        ),
        (
            "PUT",
            "access/acl",
            {"path": "/vms", "roles": "Engine", "users": "x@pve"},
            "ACL update failed: user 'x@pve' does not exist",
        ),
        (
            "PUT",
            "access/acl",
            {"path": "/vms", "roles": "Engine", "tokens": "engine@pve!absent"},
            "ACL update failed: no such token 'absent' for user 'engine@pve'",
        ),
        ("POST", "access/users/x@pve/token/t", {}, "generating token failed: no such user ('x@pve')"),
        (
            "PUT",
            "access/users/engine@pve/token/absent",
            {},
            "updating token info failed: no such token 'absent' for user 'engine@pve'",
        ),
        ("POST", "access/users/engine@pve/token/made", {}, "Parameter verification failed."),
        ("PUT", "access/acl", {"path": "/vms", "roles": "Engine"}, "Parameter verification failed."),
    ],
)
def test_a_refusal_says_what_proxmox_says(fleet: str, method: str, endpoint: str, params: dict, said: str) -> None:
    _ok(fleet, "POST", "pools", poolid="fleet")
    _ok(fleet, "PUT", "pools", poolid="fleet", vms="9011")
    _ok(fleet, "POST", "access/users", userid="engine@pve")
    _ok(fleet, "POST", "access/roles", roleid="Engine", privs="VM.Audit")
    _token(fleet, "engine@pve", "made")
    status, reason = _call(fleet, method, endpoint, **params)
    assert (status, reason) == (400 if said.startswith("Parameter") else 500, said)


# ---------------------------------------------------------------------------------- users and tokens ---
def _user(base: str, user: str, held: str, path: str = "/vms") -> None:
    """A user holding the role held, made of its privileges, on path."""
    _ok(base, "POST", "access/users", userid=user)
    _ok(base, "POST", "access/roles", roleid=f"{user.split('@')[0]}-role", privs=held)
    _ok(base, "PUT", "access/acl", path=path, roles=f"{user.split('@')[0]}-role", users=user)


def test_two_tokens_of_one_user_with_privilege_separation_read_different_permissions(fleet: str) -> None:
    _user(fleet, "engine@pve", "VM.Audit,VM.PowerMgmt,VM.Config.Options")
    _ok(fleet, "POST", "access/roles", roleid="Reader", privs="VM.Audit")
    _ok(fleet, "POST", "access/roles", roleid="Changer", privs="VM.PowerMgmt VM.Allocate")
    reader, changer = _token(fleet, "engine@pve", "reader"), _token(fleet, "engine@pve", "changer", privsep=1)
    whole = _token(fleet, "engine@pve", "whole", privsep=0)
    _ok(fleet, "PUT", "access/acl", path="/vms", roles="Reader", tokens=reader[0])
    _ok(fleet, "PUT", "access/acl", path="/vms", roles="Changer", tokens=changer[0])

    assert _permissions(fleet, reader, "/vms") == {"/vms": {"VM.Audit": 1}}
    # VM.Allocate is the token's own entry's, but its user holds none: cut away.
    assert _permissions(fleet, changer, "/vms") == {"/vms": {"VM.PowerMgmt": 1}}
    # Without separation, the user's own.
    assert _permissions(fleet, whole, "/vms") == {"/vms": {"VM.Audit": 1, "VM.PowerMgmt": 1, "VM.Config.Options": 1}}
    # Below /vms, what propagates; with no path, every path that holds something.
    assert _permissions(fleet, reader, "/vms/9010") == {"/vms/9010": {"VM.Audit": 1}}
    assert _permissions(fleet, reader) == {"/vms": {"VM.Audit": 1}}
    # A path where nothing is held is still a key, as Proxmox answers.
    assert _permissions(fleet, reader, "/nodes/pve") == {"/nodes/pve": {}}


def test_a_grant_reaches_below_its_path_only_where_it_propagates_and_a_pool_s_reaches_its_members(fleet: str) -> None:
    _user(fleet, "engine@pve", "VM.Audit,VM.PowerMgmt,Pool.Audit", path="/")
    _ok(fleet, "POST", "access/roles", roleid="Here", privs="VM.Audit")
    _ok(fleet, "POST", "access/roles", roleid="Pooled", privs="VM.PowerMgmt,Pool.Audit")
    token = _token(fleet, "engine@pve", "aw")
    _ok(fleet, "PUT", "access/acl", path="/vms", roles="Here", tokens=token[0], propagate=0)
    assert _permissions(fleet, token, "/vms") == {"/vms": {"VM.Audit": 0}}
    assert _permissions(fleet, token, "/vms/9010") == {"/vms/9010": {}}

    _ok(fleet, "POST", "pools", poolid="fleet")
    _ok(fleet, "PUT", "pools", poolid="fleet", vms="9010")
    # Granted on the pool without propagate, the pool's role still reaches its member, itself propagating nothing.
    _ok(fleet, "PUT", "access/acl", path="/pool/fleet", roles="Pooled", tokens=token[0], propagate=0)
    assert _permissions(fleet, token, "/pool/fleet") == {"/pool/fleet": {"VM.PowerMgmt": 0, "Pool.Audit": 0}}
    assert _permissions(fleet, token, "/vms/9010") == {"/vms/9010": {"VM.PowerMgmt": 0, "Pool.Audit": 0}}
    assert _permissions(fleet, token, "/vms/9011") == {"/vms/9011": {}}
    assert set(_permissions(fleet, token)) == {"/vms", "/pool/fleet", "/vms/9010"}

    # Taken away again, and the member holds nothing.
    _ok(fleet, "PUT", "access/acl", path="/pool/fleet", roles="Pooled", tokens=token[0], delete=1)
    assert _permissions(fleet, token, "/vms/9010") == {"/vms/9010": {}}


def test_a_regenerated_token_s_old_secret_is_refused(fleet: str) -> None:
    _user(fleet, "engine@pve", "VM.Audit")
    full_id, old = _token(fleet, "engine@pve", "aw", comment="the engine's")
    assert _ok(fleet, "PUT", "access/users/engine@pve/token/aw", comment="kept") == {
        "privsep": 1,
        "expire": 0,
        "comment": "kept",
    }
    changed = _ok(fleet, "PUT", "access/users/engine@pve/token/aw", regenerate=1, delete="comment")
    assert changed["full-tokenid"] == full_id and changed["value"] != old and "comment" not in changed, changed
    assert _call(fleet, "GET", "version", (full_id, old)) == (401, "authentication failure")
    assert _call(fleet, "GET", "version", (full_id, changed["value"]))[0] == 200
    # Today's secret is no secret of a token made through the API.
    assert _call(fleet, "GET", "version", (full_id, PVE_SECRET))[0] == 401


def test_a_token_past_its_expiry_is_refused(fleet: str) -> None:
    _user(fleet, "engine@pve", "VM.Audit")
    lasting = _token(fleet, "engine@pve", "lasting", expire=int(time.time()) + 3600)
    ended = _token(fleet, "engine@pve", "ended", expire=int(time.time()) - 1)
    assert _call(fleet, "GET", "version", lasting)[0] == 200
    assert _call(fleet, "GET", "version", ended) == (401, "authentication failure")


def test_today_s_tokens_are_answered_by_the_fixture_table_and_the_whole_role_as_before(fleet: str) -> None:
    # Whatever the ACL entries hold, which answer only the tokens made through the API.
    _user(fleet, "ansible@pve", "VM.Audit")
    agent = {"VM.GuestAgent.Audit": 1}
    assert _permissions(fleet, TODAYS, "/vms") == {"/vms": {**dict.fromkeys(ROLE, 1), **agent}}
    noagent = ("ansible@pve!noagent", PVE_SECRET)
    assert _permissions(fleet, noagent, "/vms") == {"/vms": dict.fromkeys(ROLE, 1)}
    assert _call(fleet, "GET", "nodes/pve/qemu/9010/agent/network-get-interfaces", noagent)[0] == 403
    _pve(fleet, "PUT", "fake/privileges", token="ansible", path="/vms", privs="VM.Audit,VM.Clone:0")
    assert _permissions(fleet, TODAYS, "/vms") == {"/vms": {"VM.Audit": 1, "VM.Clone": 0}}
    assert _permissions(fleet, TODAYS) == {"/vms": {"VM.Audit": 1, "VM.Clone": 0}}
    _pve(fleet, "PUT", "fake/privileges", token="ansible", path="/nodes/pve", privs="")
    assert _permissions(fleet, TODAYS, "/nodes/pve") == {}


def test_the_engine_s_pool_user_and_one_token_are_seeded_on_request(tmp_path: Path) -> None:
    for base in fake_fleet(tmp_path, engine=True):
        token = engine_token(tmp_path)
        assert token[0] == "agent-warehouse@pve!aw"
        held = dict.fromkeys(ENGINE_PRIVILEGES.split(), 1)
        assert _permissions(base, token, f"/pool/{ENGINE_POOL}") == {f"/pool/{ENGINE_POOL}": held}
        assert _permissions(base, token, "/vms") == {"/vms": {}}
        (pool,) = _ok(base, "GET", "pools", token, poolid=ENGINE_POOL)
        assert pool["members"] == [] and pool["comment"]
    # Off, the default, nothing of it is made.
    (tmp_path / "plain").mkdir()
    for base in fake_fleet(tmp_path / "plain"):
        assert _ok(base, "GET", "pools") == []


# ---------------------------------------------------------------------------------- today's requests ---
def _requests_of_a_run_of_today_s(tmp_path: Path) -> list[list]:
    """test_vm_size.py's test of a VM created with a size, run against the fake: every request it sent, as its method,
    its path and its parameters' names."""
    import test_vm_size

    for base in fake_fleet(tmp_path):
        test_vm_size.test_a_vm_is_created_with_the_size_given_and_tagged_as_sized(tmp_path, base)
    return [[sent["method"], sent["path"], sorted(sent["params"])] for sent in _pve_requests(tmp_path)]


def test_a_run_of_today_s_sends_the_fake_what_it_sent_before_its_new_routes(tmp_path: Path) -> None:
    assert _requests_of_a_run_of_today_s(tmp_path) == json.loads(RECORDED.read_text())
