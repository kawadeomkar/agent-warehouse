"""The fake Proxmox (tests/molecule/fake_pve_api.py), which the new engine's Proxmox client and init are written
against, as Proxmox VE 9 answers: its pools, users, roles, ACL entries and tokens, made through the routes Proxmox has,
a token made with or without privilege separation answered from the ACL entries as Proxmox answers it, and today's
tokens answered as before; a VM's configuration written only with the digest a read gave, its description kept and
bounded, its clones carrying a pool and a description and never landing on a VMID that is taken, each locked while
its clone or deletion runs; and every task a record that runs, ends and can fail. Where Proxmox takes two calls and
fails the loser in its task, the fake does too: a clone that lost a race for its VMID, or onto one a container or
another node's VM holds, and a POST whose task finds a write made since; a clone joins its pool when its task ends; a
description is read back without its trailing white space; and a running task ends though nobody polls it.

Two rules with a test of their own: a run of today's sends the fake what it sent before these routes, as recorded in
tests/unit/fake_pve_requests.json from the fake as it was (its methods, paths and parameter names, nothing else); and
with no fixture set, every answer today's requests get is what it was, as recorded in tests/unit/fake_pve_replies.json
from the fake before its tasks, digests and locks (_record_replies), but for the one answer card A5 changed.
"""

from __future__ import annotations

import ast
import hashlib
import http.client
import json
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from harness import (
    ENGINE_POOL,
    ENGINE_PRIVILEGES,
    FAKES,
    PVE_SECRET,
    _fake_pve,
    _pve,
    _pve_requests,
    engine_token,
    fake_fleet,
)

TODAYS = ("ansible@pve!ansible", PVE_SECRET)
RECORDED = Path(__file__).with_name("fake_pve_requests.json")
REPLIES = Path(__file__).with_name("fake_pve_replies.json")
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


# Four more rules of the answer, as PVE/RPCEnvironment.pm computes it, each held by a case of its own: the code that
# enforces a token's privileges is built on them, and a change to acl_roles, roles_at or acl_privileges keeps them.
def test_a_deeper_path_s_entries_replace_those_above_it_and_are_never_merged_with_them(fleet: str) -> None:
    _user(fleet, "engine@pve", "VM.Audit,VM.PowerMgmt", path="/")
    _ok(fleet, "POST", "access/roles", roleid="Above", privs="VM.PowerMgmt")
    _ok(fleet, "POST", "access/roles", roleid="Below", privs="VM.Audit")
    token = _token(fleet, "engine@pve", "aw")
    _ok(fleet, "PUT", "access/acl", path="/", roles="Above", tokens=token[0])
    _ok(fleet, "PUT", "access/acl", path="/vms", roles="Below", tokens=token[0])
    # Both propagate and the user holds both, so only the rule takes Above away: from /vms down, /vms's entry alone.
    assert _permissions(fleet, token, "/") == {"/": {"VM.PowerMgmt": 1}}
    assert _permissions(fleet, token, "/vms") == {"/vms": {"VM.Audit": 1}}
    assert _permissions(fleet, token, "/vms/9010") == {"/vms/9010": {"VM.Audit": 1}}
    # Where no deeper entry names the token, /'s still reaches.
    assert _permissions(fleet, token, "/nodes/pve") == {"/nodes/pve": {"VM.PowerMgmt": 1}}


def test_a_separated_token_propagates_a_privilege_only_where_its_user_does_too(fleet: str) -> None:
    _ok(fleet, "POST", "access/users", userid="engine@pve")
    _ok(fleet, "POST", "access/roles", roleid="Reader", privs="VM.Audit")
    _ok(fleet, "PUT", "access/acl", path="/vms", roles="Reader", users="engine@pve", propagate=0)
    token = _token(fleet, "engine@pve", "aw", privsep=1)
    _ok(fleet, "PUT", "access/acl", path="/vms", roles="Reader", tokens=token[0], propagate=1)
    # The token's own entry propagates and its user's does not: held at /vms without propagate, and nothing below.
    assert _permissions(fleet, token, "/vms") == {"/vms": {"VM.Audit": 0}}
    assert _permissions(fleet, token, "/vms/9010") == {"/vms/9010": {}}


def test_a_pool_s_role_never_replaces_the_same_role_on_a_member_s_own_path(fleet: str) -> None:
    _user(fleet, "engine@pve", "VM.Audit", path="/")
    _ok(fleet, "POST", "access/roles", roleid="Reader", privs="VM.Audit")
    token = _token(fleet, "engine@pve", "aw")
    _ok(fleet, "POST", "pools", poolid="fleet")
    _ok(fleet, "PUT", "pools", poolid="fleet", vms="9010,9011")
    _ok(fleet, "PUT", "access/acl", path="/vms/9010", roles="Reader", tokens=token[0], propagate=1)
    _ok(fleet, "PUT", "access/acl", path="/pool/fleet", roles="Reader", tokens=token[0])
    # The member's own entry propagates, and the pool's, which never propagates on a member, does not take its place.
    assert _permissions(fleet, token, "/vms/9010") == {"/vms/9010": {"VM.Audit": 1}}
    # A member with no entry of its own takes the pool's, without propagate.
    assert _permissions(fleet, token, "/vms/9011") == {"/vms/9011": {"VM.Audit": 0}}


def test_a_pool_s_role_reaches_a_storage_the_pool_holds_without_propagate(fleet: str) -> None:
    _user(fleet, "engine@pve", "Datastore.AllocateSpace,Datastore.Audit", path="/")
    _ok(fleet, "POST", "access/roles", roleid="Space", privs="Datastore.AllocateSpace,Datastore.Audit")
    token = _token(fleet, "engine@pve", "aw")
    _ok(fleet, "POST", "pools", poolid="fleet")
    _ok(fleet, "PUT", "access/acl", path="/pool/fleet", roles="Space", tokens=token[0])
    assert _permissions(fleet, token, "/storage/local-lvm") == {"/storage/local-lvm": {}}
    # Once the pool holds the storage, the pool's role reaches it, propagating nothing, and is listed with no path too.
    _ok(fleet, "PUT", "pools", poolid="fleet", storage="local-lvm")
    held = {"Datastore.AllocateSpace": 0, "Datastore.Audit": 0}
    assert _permissions(fleet, token, "/storage/local-lvm") == {"/storage/local-lvm": held}
    assert _permissions(fleet, token)["/storage/local-lvm"] == held
    # Out of the pool again, and the storage holds nothing.
    _ok(fleet, "PUT", "pools", poolid="fleet", storage="local-lvm", delete=1)
    assert _permissions(fleet, token, "/storage/local-lvm") == {"/storage/local-lvm": {}}


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


# ------------------------------------------------------------- a VM's configuration, its clones, its tasks ---
# What the fake says where it refuses, as it says it: the constants at its top, which experiment 6 corrects (A21).
SAID = {
    target.id: node.value.value
    for node in ast.parse((FAKES / "fake_pve_api.py").read_text()).body
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
    for target in node.targets
    if isinstance(target, ast.Name)
}
CONFIG = "nodes/pve/qemu/9012/config"
CLONE = "nodes/pve/qemu/9000/clone"


def _answer(base: str, method: str, endpoint: str, **params) -> dict:
    """One request as today's token, its answer whole: the status, the reason, the Content-Type and the body."""
    return _sent(base, {"method": method, "path": f"/api2/json/{endpoint}", "params": params, "token": "ansible"})


def _task(base: str, upid: str, part: str = "status") -> object:
    return _ok(base, "GET", f"nodes/pve/tasks/{upid}/{part}")


def _tasks(directory: Path) -> dict:
    """Every task the fake in directory made, by its UPID, as its tasks.json records them."""
    return json.loads((directory / "pve" / "tasks.json").read_text())


def test_a_write_with_a_stale_digest_is_refused_and_changes_nothing(fleet: str) -> None:
    stale = _ok(fleet, "GET", CONFIG)["digest"]
    # A write that sends no digest is taken, as it always was.
    _ok(fleet, "PUT", CONFIG, cores=3)
    now = _ok(fleet, "GET", CONFIG)
    assert now["digest"] != stale
    for method in ("PUT", "POST"):
        refused = _call(fleet, method, CONFIG, digest=stale, memory=1024, delete="cores")
        assert refused == (SAID["DIGEST_REFUSED_STATUS"], SAID["DIGEST_REFUSED"])
    assert _ok(fleet, "GET", CONFIG) == now


def test_a_write_with_the_digest_a_read_gave_is_taken_and_the_digest_never_kept(fleet: str, tmp_path: Path) -> None:
    for method, cores in (("PUT", 5), ("POST", 7)):
        _ok(fleet, method, CONFIG, digest=_ok(fleet, "GET", CONFIG)["digest"], cores=cores)
    assert _ok(fleet, "GET", CONFIG)["cores"] == "7"
    kept = json.loads((tmp_path / "pve" / "state.json").read_text())["9012"]["config"]
    assert "digest" not in kept and kept["cores"] == "7"


def test_of_two_writers_that_read_the_same_configuration_the_second_is_refused(fleet: str) -> None:
    first, second = _ok(fleet, "GET", CONFIG)["digest"], _ok(fleet, "GET", CONFIG)["digest"]
    _ok(fleet, "PUT", CONFIG, digest=first, description="the first's")
    refused = _call(fleet, "PUT", CONFIG, digest=second, description="the second's")
    assert refused == (SAID["DIGEST_REFUSED_STATUS"], SAID["DIGEST_REFUSED"])
    assert _ok(fleet, "GET", CONFIG)["description"] == "the first's"


def test_a_description_is_kept_as_sent_and_refused_past_the_limit_a_fixture_sets(fleet: str) -> None:
    record = "agent-warehouse: vm\nstage: finished"
    _ok(fleet, "PUT", CONFIG, description=record)
    assert _ok(fleet, "GET", CONFIG)["description"] == record
    # Eight bytes: four two-byte letters fit, and one more byte is over.
    assert _pve(fleet, "PUT", "fake/notes-limit", bytes=8)["data"] == {"bytes": 8}
    _ok(fleet, "PUT", CONFIG, description="éééé")
    said = {"description": SAID["NOTES_REFUSED"].format(limit=8)}
    for method in ("PUT", "POST"):
        answer = _answer(fleet, method, CONFIG, description="éééé.")
        assert (answer["status"], answer["reason"]) == (SAID["NOTES_REFUSED_STATUS"], "Parameter verification failed.")
        assert json.loads(answer["body"]) == {"data": None, "errors": said}
    assert _ok(fleet, "GET", CONFIG)["description"] == "éééé"
    # A clone's description is held to no limit: clone_vm's parameter has none, unlike the configuration's.
    _ok(fleet, "POST", CLONE, newid=9020, name="noted", description="éééé.")
    assert _ok(fleet, "GET", "nodes/pve/qemu/9020/config")["description"] == "éééé."
    # No limit again: 8 KiB and more taken.
    _pve(fleet, "PUT", "fake/notes-limit", bytes=0)
    _ok(fleet, "PUT", CONFIG, description="x" * 9000)
    assert len(_ok(fleet, "GET", CONFIG)["description"]) == 9000


def test_a_description_is_read_back_without_its_trailing_white_space(fleet: str) -> None:
    record = "agent-warehouse: vm\nstage: finished"
    for method, ending in (("PUT", "\n"), ("POST", " \n\t\n")):
        _ok(fleet, method, CONFIG, description=record + ending)
        assert _ok(fleet, "GET", CONFIG)["description"] == record
    # A clone's too, and white space inside it stays.
    _ok(fleet, "POST", CLONE, newid=9020, name="noted", description=" two\n\nlines\n")
    assert _ok(fleet, "GET", "nodes/pve/qemu/9020/config")["description"] == " two\n\nlines"


def test_a_clone_carries_a_pool_and_a_description_and_by_default_its_source_s_tags(fleet: str) -> None:
    _ok(fleet, "POST", "pools", poolid="fleet")
    record = "agent-warehouse: vm\nstage: cloning"
    _ok(fleet, "POST", "nodes/pve/qemu/9010/clone", newid=9020, name="recorded", pool="fleet", description=record)
    config = _ok(fleet, "GET", "nodes/pve/qemu/9020/config")
    assert (config["description"], config["tags"], "lock" in config) == (record, "agent-warehouse", False)
    assert [m["vmid"] for m in _ok(fleet, "GET", "pools", poolid="fleet")[0]["members"]] == [9020]
    # Given none, its source's description, as the fake always had it.
    _ok(fleet, "POST", CLONE, newid=9021, name="plain")
    template = _ok(fleet, "GET", "nodes/pve/qemu/9000/config")["description"]
    assert _ok(fleet, "GET", "nodes/pve/qemu/9021/config")["description"] == template
    # A fixture's clone takes neither its source's tags nor its description; one given is still kept.
    assert _pve(fleet, "PUT", "fake/clone-inherits", tags=0, description=0)["data"] == {"tags": 0, "description": 0}
    _ok(fleet, "POST", "nodes/pve/qemu/9010/clone", newid=9022, name="bare")
    _ok(fleet, "POST", "nodes/pve/qemu/9010/clone", newid=9023, name="noted", description=record)
    bare, noted = (_ok(fleet, "GET", f"nodes/pve/qemu/{vmid}/config") for vmid in (9022, 9023))
    assert not {"tags", "description"} & set(bare) and noted["description"] == record and "tags" not in noted


def test_a_clone_never_lands_on_a_vmid_that_is_taken_nor_one_a_running_clone_holds(fleet: str) -> None:
    _ok(fleet, "POST", CLONE, newid=9020, name="first")
    before = _ok(fleet, "GET", "nodes/pve/qemu/9020/config")
    assert _call(fleet, "POST", CLONE, newid=9020, name="second") == (500, SAID["CLONE_EXISTS"].format(newid=9020))
    assert _ok(fleet, "GET", "nodes/pve/qemu/9020/config") == before
    # A running clone's worker has made the VM's configuration file, which the call's check reads.
    _pve(fleet, "PUT", "fake/task-polls", polls=1)
    _ok(fleet, "POST", CLONE, newid=9021, name="running")
    assert _call(fleet, "POST", CLONE, newid=9021, name="behind") == (500, SAID["CLONE_EXISTS"].format(newid=9021))
    assert _ok(fleet, "GET", "nodes/pve/qemu/9021/config")["name"] == "running"


def _vms(base: str) -> dict:
    """Every guest the cluster lists, by its VMID: its type, its node and its name, where it is listed with one."""
    return {vm["vmid"]: (vm["type"], vm["node"], vm.get("name")) for vm in _ok(base, "GET", "cluster/resources")}


def test_of_two_clones_onto_one_vmid_in_a_race_both_get_a_task_and_only_the_loser_s_fails(fleet: str) -> None:
    # The loser's call was checked before the winner's worker made the VM: both answered a UPID.
    _pve(fleet, "PUT", "fake/task-polls", polls=1)
    assert _pve(fleet, "PUT", "fake/clone-race")["data"] == {"clone": 1, "config": 1}
    winner = _ok(fleet, "POST", CLONE, newid=9020, name="winner")
    loser = _ok(fleet, "POST", CLONE, newid=9020, name="loser")
    # While the winner's clone runs, the loser's worker waits for the new VM's lock, and gives up.
    timeout = SAID["CLONE_LOCK_TIMEOUT"].format(newid=9020)
    assert [_task(fleet, loser)["status"] for _ in range(2)] == ["running", "stopped"]
    assert _task(fleet, loser) == {"status": "stopped", "exitstatus": timeout, "upid": loser}
    assert _task(fleet, loser, "log") == [{"n": 1, "t": f"TASK ERROR: {timeout}"}]
    assert [_task(fleet, winner)["status"] for _ in range(2)] == ["running", "stopped"]
    assert _task(fleet, winner)["exitstatus"] == "OK"
    # Once the winner's clone has ended, the loser's worker finds its file: the call's words, in the task.
    _pve(fleet, "PUT", "fake/task-polls", polls=0)
    _pve(fleet, "PUT", "fake/clone-race")
    late = _ok(fleet, "POST", CLONE, newid=9020, name="late")
    assert _task(fleet, late)["exitstatus"] == SAID["CLONE_EXISTS"].format(newid=9020)
    # Exactly one VM at 9020, the winner's, as it was made; and the switch is spent.
    assert _vms(fleet)[9020] == ("qemu", "pve", "winner")
    config = _ok(fleet, "GET", "nodes/pve/qemu/9020/config")
    assert (config["name"], "lock" in config) == ("winner", False)
    assert _call(fleet, "POST", CLONE, newid=9020, name="refused") == (500, SAID["CLONE_EXISTS"].format(newid=9020))


def test_a_clone_onto_a_vmid_a_container_or_another_node_s_vm_holds_passes_the_call_and_fails_in_its_task(
    fleet: str, tmp_path: Path
) -> None:
    assert SAID["CLONE_TAKEN_IN_TASK"] is True
    before = _vms(fleet)
    for newid, kind, node in [(9003, "CT", "pve"), (8000, "VM", "pve2")]:
        upid = _ok(fleet, "POST", CLONE, newid=newid, name="elsewhere")
        taken = SAID["CLONE_TAKEN"].format(kind=kind, newid=newid, node=node)
        assert _task(fleet, upid) == {"status": "stopped", "exitstatus": taken, "upid": upid}
        assert _tasks(tmp_path)[upid]["type"] == "qmclone"
    assert _vms(fleet) == before
    # The clone-race switch is no part of it, and stays for the clone it is for.
    _pve(fleet, "PUT", "fake/clone-race")
    upid = _ok(fleet, "POST", CLONE, newid=9003, name="elsewhere")
    assert _task(fleet, upid)["exitstatus"] == SAID["CLONE_TAKEN"].format(kind="CT", newid=9003, node="pve")
    raced = _ok(fleet, "POST", CLONE, newid=9010, name="raced")
    assert _task(fleet, raced)["exitstatus"] == SAID["CLONE_EXISTS"].format(newid=9010)


def test_a_post_that_lost_a_race_is_answered_a_task_that_fails_and_writes_nothing(fleet: str) -> None:
    stale = _ok(fleet, "GET", CONFIG)["digest"]
    _ok(fleet, "PUT", CONFIG, cores=3)
    _pve(fleet, "PUT", "fake/clone-race")
    upid = _ok(fleet, "POST", CONFIG, digest=stale, cores=5)
    assert _task(fleet, upid) == {"status": "stopped", "exitstatus": SAID["DIGEST_REFUSED"], "upid": upid}
    assert _ok(fleet, "GET", CONFIG)["cores"] == "3"
    # The switch is spent, and a PUT is never taken so: it is written in the call.
    refused = (SAID["DIGEST_REFUSED_STATUS"], SAID["DIGEST_REFUSED"])
    assert _call(fleet, "POST", CONFIG, digest=stale, cores=5) == refused
    _pve(fleet, "PUT", "fake/clone-race")
    assert _call(fleet, "PUT", CONFIG, digest=stale, cores=5) == refused


def test_a_post_s_task_checks_the_digest_and_the_lock_again_when_it_ends_so_a_write_made_since_stands(
    fleet: str,
) -> None:
    _pve(fleet, "PUT", "fake/task-polls", polls=1)
    digest = _ok(fleet, "GET", CONFIG)["digest"]
    upid = _ok(fleet, "POST", CONFIG, digest=digest, description="the POST's")
    # While its task runs, a read gives the configuration as it was, and a PUT with that digest is taken.
    assert _ok(fleet, "GET", CONFIG)["digest"] == digest
    _ok(fleet, "PUT", CONFIG, digest=digest, description="the PUT's")
    assert _task(fleet, upid)["status"] == "running"
    assert _task(fleet, upid) == {"status": "stopped", "exitstatus": SAID["DIGEST_REFUSED"], "upid": upid}
    assert _ok(fleet, "GET", CONFIG)["description"] == "the PUT's"
    # One that sends no digest meets a lock a deletion took since.
    cores = _ok(fleet, "GET", CONFIG)["cores"]
    unchecked = _ok(fleet, "POST", CONFIG, cores=4)
    _ok(fleet, "DELETE", "nodes/pve/qemu/9012")
    _task(fleet, unchecked)
    locked = SAID["LOCKED"].format(lock=SAID["DELETION_LOCK"])
    assert _task(fleet, unchecked) == {"status": "stopped", "exitstatus": locked, "upid": unchecked}
    assert _ok(fleet, "GET", CONFIG)["cores"] == cores
    # With nothing in between, it writes when it ends, its digest still the configuration's.
    beta = "nodes/pve/qemu/9011/config"
    upid = _ok(fleet, "POST", beta, digest=_ok(fleet, "GET", beta)["digest"], cores=6)
    assert _ok(fleet, "GET", beta)["cores"] == cores
    assert [_task(fleet, upid)["status"] for _ in range(2)] == ["running", "stopped"]
    assert (_task(fleet, upid)["exitstatus"], _ok(fleet, "GET", beta)["cores"]) == ("OK", "6")


def test_a_clone_joins_its_pool_when_its_task_ends_and_a_failed_one_never_does(fleet: str) -> None:
    _ok(fleet, "POST", "pools", poolid="fleet")
    _pve(fleet, "PUT", "fake/task-polls", polls=1)
    upid = _ok(fleet, "POST", CLONE, newid=9020, name="pooled", pool="fleet")

    def members() -> list[int]:
        return [m["vmid"] for m in _ok(fleet, "GET", "pools", poolid="fleet")[0]["members"]]

    def listed() -> dict:
        return {vm["vmid"]: vm for vm in _ok(fleet, "GET", "cluster/resources")}[9020]

    assert members() == [] and "pool" not in listed()
    assert [_task(fleet, upid)["status"] for _ in range(2)] == ["running", "stopped"]
    assert members() == [9020] and listed()["pool"] == "fleet"
    _pve(fleet, "PUT", "fake/task-fail", error="clone failed: no space left on device", type="qmclone")
    failed = _ok(fleet, "POST", CLONE, newid=9021, name="failing", pool="fleet")
    assert _pve(fleet, "PUT", "fake/task-end", upid=failed)["data"] == [failed]
    assert members() == [9020]


def test_task_end_ends_running_tasks_nobody_polls_as_their_workers_would(fleet: str, tmp_path: Path) -> None:
    _pve(fleet, "PUT", "fake/task-polls", polls=5)
    clone = _ok(fleet, "POST", CLONE, newid=9020, name="orphaned")
    assert _ok(fleet, "GET", "nodes/pve/qemu/9020/config")["lock"] == SAID["CLONE_LOCK"]
    # The run that made it is gone; its clone ends on the host all the same, and its lock goes.
    assert _pve(fleet, "PUT", "fake/task-end", upid=clone)["data"] == [clone]
    assert "lock" not in _ok(fleet, "GET", "nodes/pve/qemu/9020/config")
    assert _task(fleet, clone) == {"status": "stopped", "exitstatus": "OK", "upid": clone}
    assert _tasks(tmp_path)[clone]["polls"] == 0
    # With no upid, every running task: a start and a deletion, each with what it does when it ends.
    start = _ok(fleet, "POST", "nodes/pve/qemu/9012/status/start")
    deletion = _ok(fleet, "DELETE", "nodes/pve/qemu/9020")
    assert sorted(_pve(fleet, "PUT", "fake/task-end")["data"]) == sorted([start, deletion])
    assert _ok(fleet, "GET", "nodes/pve/qemu/9012/status/current")["status"] == "running"
    assert 9020 not in _vms(fleet)
    assert _pve(fleet, "PUT", "fake/task-end")["data"] == []


def test_a_deletion_that_fails_lets_its_lock_go(fleet: str) -> None:
    _pve(fleet, "PUT", "fake/task-fail", error="disk busy", type="qmdestroy")
    upid = _ok(fleet, "DELETE", "nodes/pve/qemu/9012")
    assert _task(fleet, upid) == {"status": "stopped", "exitstatus": "disk busy", "upid": upid}
    assert "lock" not in _ok(fleet, "GET", CONFIG)
    _ok(fleet, "PUT", CONFIG, cores=4)
    assert _ok(fleet, "GET", CONFIG)["cores"] == "4"


def _at_once(base: str, method: str, endpoint: str, sent: list[dict]) -> list[tuple[int, object]]:
    """Each of sent, the parameters of one request, sent to the fake at base from a thread of its own, every thread
    released at one moment; their answers, in sent's order."""
    start, answers = threading.Barrier(len(sent)), [(0, None)] * len(sent)

    def send(i: int) -> None:
        start.wait()
        answers[i] = _call(base, method, endpoint, **sent[i])

    racers = [threading.Thread(target=send, args=(i,)) for i in range(len(sent))]
    for racer in racers:
        racer.start()
    for racer in racers:
        racer.join(timeout=60)
    return answers


def test_the_fake_answers_one_request_at_a_time(fleet: str) -> None:
    """While one request is still being read, another is not answered: the fake's server is single-threaded."""
    address = urllib.parse.urlsplit(fleet)
    unverified = ssl.create_default_context()
    unverified.check_hostname = False
    unverified.verify_mode = ssl.CERT_NONE
    raw = socket.create_connection((address.hostname, address.port), timeout=30)
    with unverified.wrap_socket(raw, server_hostname=address.hostname) as first:
        # Its request line sent, its headers not yet ended.
        first.sendall(b"GET /api2/json/version HTTP/1.0\r\n")
        answered = threading.Event()
        second = threading.Thread(target=lambda: _ok(fleet, "GET", "version") and answered.set())
        second.start()
        assert not answered.wait(2), "a second request was answered while the first was still being read"
        first.sendall(f"Authorization: PVEAPIToken={TODAYS[0]}={TODAYS[1]}\r\n\r\n".encode())
        reply = b""
        while chunk := first.recv(65536):
            reply += chunk
    assert reply.startswith(b"HTTP/1.0 200 "), reply[:80]
    second.join(timeout=30)
    assert answered.is_set()


def test_requests_sent_at_once_are_answered_one_at_a_time_so_each_race_has_one_winner(fleet: str) -> None:
    """The fake serves one request at a time, which is what makes each check and the write after it atomic: four
    clones onto one VMID, and four writes that read one digest, released together, have one winner each round."""
    for newid in range(9020, 9025):
        clones = _at_once(fleet, "POST", CLONE, [{"newid": newid, "name": f"racer-{i}"} for i in range(4)])
        refused = (500, SAID["CLONE_EXISTS"].format(newid=newid))
        won = [i for i, (status, _) in enumerate(clones) if status == 200]
        assert len(won) == 1 and all(clones[i] == refused for i in range(4) if i not in won), clones
        assert _ok(fleet, "GET", f"nodes/pve/qemu/{newid}/config")["name"] == f"racer-{won[0]}"

        # Each round's own words: a write that changes nothing leaves the digest as it was.
        digest = _ok(fleet, "GET", CONFIG)["digest"]
        sent = [{"digest": digest, "description": f"racer-{newid}-{i}"} for i in range(4)]
        writes = _at_once(fleet, "PUT", CONFIG, sent)
        won = [i for i, (status, _) in enumerate(writes) if status == 200]
        refused = (SAID["DIGEST_REFUSED_STATUS"], SAID["DIGEST_REFUSED"])
        assert len(won) == 1 and all(writes[i] == refused for i in range(4) if i not in won), writes
        assert _ok(fleet, "GET", CONFIG)["description"] == f"racer-{newid}-{won[0]}"


def test_while_a_clone_runs_its_vm_is_locked_and_a_write_to_it_refused(fleet: str) -> None:
    _pve(fleet, "PUT", "fake/task-polls", polls=2)
    upid = _ok(fleet, "POST", CLONE, newid=9020, name="cloning")
    assert _ok(fleet, "GET", "nodes/pve/qemu/9020/config")["lock"] == SAID["CLONE_LOCK"] == "clone"
    locked = (SAID["LOCKED_STATUS"], SAID["LOCKED"].format(lock="clone"))
    assert _call(fleet, "PUT", "nodes/pve/qemu/9020/config", cores=4) == locked
    assert [_task(fleet, upid)["status"] for _ in range(3)] == ["running", "running", "stopped"]
    assert "lock" not in _ok(fleet, "GET", "nodes/pve/qemu/9020/config")
    _ok(fleet, "PUT", "nodes/pve/qemu/9020/config", cores=4)


def test_while_a_deletion_runs_its_vm_is_locked_and_a_deletion_of_a_locked_vm_fails_in_its_task(fleet: str) -> None:
    _pve(fleet, "PUT", "fake/task-polls", polls=1)
    upid = _ok(fleet, "DELETE", "nodes/pve/qemu/9012")
    assert _ok(fleet, "GET", CONFIG)["lock"] == SAID["DELETION_LOCK"] == "destroyed"
    locked = SAID["LOCKED"].format(lock="destroyed")
    assert _call(fleet, "PUT", CONFIG, cores=4) == (SAID["LOCKED_STATUS"], locked)
    again = _ok(fleet, "DELETE", "nodes/pve/qemu/9012")
    assert _task(fleet, upid) == {"status": "running", "upid": upid}
    assert _task(fleet, upid) == {"status": "stopped", "exitstatus": "OK", "upid": upid}
    assert _call(fleet, "GET", CONFIG)[0] == 500
    _task(fleet, again)
    assert _task(fleet, again) == {"status": "stopped", "exitstatus": locked, "upid": again}
    assert _task(fleet, again, "log") == [{"n": 1, "t": f"TASK ERROR: {locked}"}]


def test_a_task_runs_for_the_polls_a_fixture_sets_and_its_change_is_made_when_it_ends(
    fleet: str, tmp_path: Path
) -> None:
    _pve(fleet, "PUT", "fake/task-polls", polls=3)
    upid = _ok(fleet, "POST", "nodes/pve/qemu/9012/status/start")
    for _ in range(3):
        assert _task(fleet, upid) == {"status": "running", "upid": upid}
        assert _ok(fleet, "GET", "nodes/pve/qemu/9012/status/current")["status"] == "stopped"
        assert _task(fleet, upid, "log") == []
    assert _task(fleet, upid) == {"status": "stopped", "exitstatus": "OK", "upid": upid}
    assert _ok(fleet, "GET", "nodes/pve/qemu/9012/status/current")["status"] == "running"
    assert _task(fleet, upid, "log") == [{"n": 1, "t": "TASK OK"}]
    assert _ok(fleet, "GET", f"nodes/pve/tasks/{upid}/log", start=1) == []
    recorded = _tasks(tmp_path)[upid]
    assert recorded == {
        "type": "qmstart",
        "owner": "ansible@pve!ansible",
        "vmid": 9012,
        "status": "stopped",
        "polls": 0,
        "error": "",
    }
    # A UPID the fake never made: stopped and OK, with an empty log, as it always was.
    never = "UPID:pve:0000FFFF:00000000:00000000:qmtask:65535:root@pam!ansible:"
    assert _task(fleet, never) == {"status": "stopped", "exitstatus": "OK", "upid": never}
    assert _task(fleet, never, "log") == []


def test_a_task_ends_with_the_error_a_fixture_planted_and_changes_nothing(fleet: str) -> None:
    failed = "clone failed: no space left on device"
    _pve(fleet, "PUT", "fake/task-fail", error=failed, type="qmclone")
    # A task of another type is not the one planted for.
    started = _ok(fleet, "POST", "nodes/pve/qemu/9012/status/start")
    assert _task(fleet, started)["exitstatus"] == "OK"
    upid = _ok(fleet, "POST", CLONE, newid=9020, name="failing")
    assert _task(fleet, upid) == {"status": "stopped", "exitstatus": failed, "upid": upid}
    assert _task(fleet, upid, "log") == [{"n": 1, "t": f"TASK ERROR: {failed}"}]
    # A clone that failed leaves no VM, and the error is the next task's alone.
    assert _call(fleet, "GET", "nodes/pve/qemu/9020/config")[0] == 500
    assert _task(fleet, _ok(fleet, "POST", CLONE, newid=9020, name="made"))["exitstatus"] == "OK"
    # Planted for any type, it ends the next task, whose change is not made.
    _pve(fleet, "PUT", "fake/task-fail", error="stop failed")
    stopped = _ok(fleet, "POST", "nodes/pve/qemu/9012/status/stop")
    assert _task(fleet, stopped)["exitstatus"] == "stop failed"
    assert _ok(fleet, "GET", "nodes/pve/qemu/9012/status/current")["status"] == "running"


def test_every_task_is_recorded_with_its_type_the_token_that_made_it_and_its_vm(tmp_path: Path) -> None:
    for base in fake_fleet(tmp_path, engine=True):
        token, vm = engine_token(tmp_path), "nodes/pve/qemu/9020"
        made = [
            _ok(base, "POST", CLONE, token, newid=9020, name="tasked", pool=ENGINE_POOL),
            _ok(base, "POST", f"{vm}/config", token, cores=2),
            _ok(base, "PUT", f"{vm}/resize", token, disk="scsi0", size="+1G"),
            _ok(base, "POST", f"{vm}/status/start", token),
            _ok(base, "POST", f"{vm}/status/stop", token),
            _ok(base, "POST", f"{vm}/template", token),
            _ok(base, "DELETE", vm, token),
        ]
        recorded = _tasks(tmp_path)
        assert [(recorded[upid]["type"], recorded[upid]["vmid"]) for upid in made] == [
            ("qmclone", 9000),
            ("qmconfig", 9020),
            ("resize", 9020),
            ("qmstart", 9020),
            ("qmstop", 9020),
            ("qmtemplate", 9020),
            ("qmdestroy", 9020),
        ]
        assert {recorded[upid]["owner"] for upid in made} == {token[0]}
        # fake_fleet's own, made as today's token.
        assert {task["owner"] for upid, task in recorded.items() if upid not in made} == {"ansible@pve!ansible"}


def test_a_clone_takes_a_storage_its_node_has_and_another_node_where_its_storage_is_shared(fleet: str) -> None:
    _pve(fleet, "PUT", "fake/nodes/pve2", status="online")
    _pve(fleet, "PUT", "fake/storages", node="pve", storages="local-lvm:lvmthin,nas:nfs:shared")
    _pve(fleet, "PUT", "fake/storages", node="pve2", storages="nas:nfs:shared,fast:zfspool")
    _ok(fleet, "POST", CLONE, newid=9020, name="on-nas", full=1, storage="nas")
    assert _ok(fleet, "GET", "nodes/pve/qemu/9020/config")["scsi0"].startswith("nas:vm-9020-disk-0,")
    _ok(fleet, "POST", CLONE, newid=9021, name="moved", full=1, storage="nas", target="pve2")
    assert {vm["vmid"]: vm["node"] for vm in _ok(fleet, "GET", "cluster/resources")}[9021] == "pve2"
    assert _ok(fleet, "GET", "nodes/pve2/qemu/9021/config")["scsi0"].startswith("nas:vm-9021-disk-0,")
    assert {s["storage"]: s["shared"] for s in _ok(fleet, "GET", "nodes/pve/storage")} == {"local-lvm": 0, "nas": 1}
    for given, said in [
        ({"target": "pve9"}, SAID["NO_SUCH_NODE"].format(node="pve9")),
        ({"storage": "tank"}, SAID["NO_SUCH_STORAGE"].format(storage="tank")),
        (
            {"storage": "local-lvm", "target": "pve2"},
            SAID["STORAGE_NOT_ON_NODE"].format(storage="local-lvm", node="pve2"),
        ),
        ({"storage": "fast", "target": "pve2"}, SAID["STORAGE_NOT_SHARED"].format(storage="fast")),
        # The template's disk is on local-lvm, which pve2 does not share.
        ({"target": "pve2"}, SAID["DISK_NOT_SHARED"].format(node="pve2")),
    ]:
        assert _call(fleet, "POST", CLONE, newid=9022, name="refused", full=1, **given) == (500, said), given
    assert _call(fleet, "GET", "nodes/pve/qemu/9022/config")[0] == 500


# ---------------------------------------------------------------------------------- today's requests ---
def _run_of_today_s(tmp_path: Path) -> list[dict]:
    """test_vm_size.py's test of a VM created with a size, run against the fake: every request it sent, fake_fleet's
    own first, as the fake logged it - its method, path, parameters and token ID."""
    import test_vm_size

    for base in fake_fleet(tmp_path):
        test_vm_size.test_a_vm_is_created_with_the_size_given_and_tagged_as_sized(tmp_path, base)
    return _pve_requests(tmp_path)


def _requests_of_a_run_of_today_s(tmp_path: Path) -> list[list]:
    """_run_of_today_s's requests as their method, their path and their parameters' names."""
    return [[sent["method"], sent["path"], sorted(sent["params"])] for sent in _run_of_today_s(tmp_path)]


def test_a_run_of_today_s_sends_the_fake_what_it_sent_before_its_new_routes(tmp_path: Path) -> None:
    assert _requests_of_a_run_of_today_s(tmp_path) == json.loads(RECORDED.read_text())


# A UUID, which the fake makes afresh for each clone's SMBIOS and each token's secret: the one part of an answer that is
# never the same twice, said as <uuid> in a recorded answer.
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _sent(base: str, request: dict) -> dict:
    """One request as the fake logged it - method, path, params, token ID, the secret today's - sent again to the fake
    at base; its answer as the fake gave it: the status, the reason, the Content-Type and the body, its UUIDs said as
    <uuid> and a configuration's digest, where it is the SHA-1 of the rest of that configuration, said as such."""
    address = urllib.parse.urlsplit(base)
    unverified = ssl.create_default_context()
    unverified.check_hostname = False
    unverified.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection(address.hostname, address.port, timeout=30, context=unverified)
    form = urllib.parse.urlencode(request["params"])
    headers = {"Authorization": f"PVEAPIToken=ansible@pve!{request['token']}={PVE_SECRET}"} if request["token"] else {}
    try:
        if request["method"] in ("GET", "HEAD", "DELETE"):
            connection.request(request["method"], request["path"] + (f"?{form}" if form else ""), headers=headers)
        else:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            connection.request(request["method"], request["path"], body=form, headers=headers)
        answer = connection.getresponse()
        body = answer.read().decode()
    finally:
        connection.close()
    try:
        data = json.loads(body)["data"]
    except (ValueError, KeyError, TypeError):
        data = None
    if isinstance(data, dict) and isinstance(data.get("digest"), str):
        rest = json.dumps({key: value for key, value in data.items() if key != "digest"}, sort_keys=True)
        if data["digest"] == hashlib.sha1(rest.encode()).hexdigest():
            body = body.replace(data["digest"], "<the SHA-1 of the rest of the configuration>")
    kind = answer.getheader("Content-Type")
    return {"status": answer.status, "reason": answer.reason, "type": kind, "body": UUID.sub("<uuid>", body)}


def _today_s_other_requests(base: str) -> list[dict]:
    """After _run_of_today_s's requests, sent to the fake at base, the requests of today's that its run does not send,
    each made of the answers before it, as the fake logs them, with the answer it was given: a clone onto a VMID that
    is taken (the provision scenario's converge, run again); a clone as harness._pve_vm makes one, a full one onto a
    storage and one into a pool (the role, with proxmox_vm_pool), each waited for as proxmox_kvm waits; the notes
    written (the role's settings call, test_golden_play.py); a template made of a VM (test_vm_repos.py); a VM started
    and stopped again (destroy.yml, tasks/vm_stop.yml); a deletion refused for protection, then made (proxmox_kvm,
    test_golden_play.py); the listings after; a node's storages and the cluster's, before and after a fixture lists one
    Proxmox cannot reach (init_check.yml, just status); and a VM started, then shut down as proxmox_kvm stops one
    (the role's absent.yml)."""
    sent: list[dict] = []

    def send(method: str, path: str, **params) -> object:
        request = {"method": method, "path": f"/api2/json/{path}", "params": params, "token": "ansible"}
        sent.append({**request, "answer": _sent(base, request)})
        return json.loads(sent[-1]["answer"]["body"] or "{}").get("data")

    def waited(method: str, path: str, **params) -> None:
        send("GET", f"nodes/pve/tasks/{send(method, path, **params)}/status")

    send("POST", "nodes/pve/qemu/9000/clone", newid="9010", name="in-the-way")
    waited("POST", "nodes/pve/qemu/9000/clone", newid="9020", name="delta")
    waited("POST", "nodes/pve/qemu/9000/clone", newid="9021", name="epsilon", full="1", storage="local-lvm")
    waited("POST", "nodes/pve/qemu/9000/clone", newid="9022", name="pooled", pool="agent-warehouse")
    send("PUT", "nodes/pve/qemu/9020/config", tags="agent-warehouse", description="the notes of delta")
    send("GET", "nodes/pve/qemu/9020/config")
    waited("POST", "nodes/pve/qemu/9020/template")
    waited("POST", "nodes/pve/qemu/9021/status/start")
    send("GET", "nodes/pve/qemu/9021/status/current")
    waited("POST", "nodes/pve/qemu/9021/status/stop")
    send("PUT", "nodes/pve/qemu/9021/config", protection="1")
    send("DELETE", "nodes/pve/qemu/9021")
    send("PUT", "nodes/pve/qemu/9021/config", delete="protection")
    waited("DELETE", "nodes/pve/qemu/9021", purge="1")
    send("GET", "nodes/pve/qemu/9021/config")
    waited("DELETE", "nodes/pve/qemu/9020")
    send("GET", "cluster/nextid")
    send("GET", "cluster/resources")
    send("GET", "nodes/pve/qemu")
    send("GET", "nodes/pve/storage")
    send("GET", "cluster/resources", type="storage")
    send("PUT", "fake/storages", node="pve", storages="local-lvm:lvmthin,slow:dir:inactive")
    send("GET", "nodes/pve/storage")
    send("GET", "cluster/resources", type="storage")
    waited("POST", "nodes/pve/qemu/9022/status/start")
    waited("POST", "nodes/pve/qemu/9022/status/shutdown", forceStop="1", timeout="30")
    send("GET", "nodes/pve/qemu/9022/status/current")
    return sent


def _record_replies(tmp_path: Path) -> list[dict]:
    """Every request of _run_of_today_s, then _today_s_other_requests, sent to a fake of its own, each with the answer
    it was given: what REPLIES records. Run once, on a lane, from the fake as it was before a change to its answers."""
    run = [{key: sent[key] for key in ("method", "path", "params", "token")} for sent in _run_of_today_s(tmp_path)]
    (tmp_path / "again").mkdir()
    for base in _fake_pve(tmp_path / "again"):
        recorded = [{**request, "answer": _sent(base, request)} for request in run]
        recorded += _today_s_other_requests(base)
    return recorded


def test_with_no_fixture_set_every_answer_today_s_requests_get_is_what_it_was(tmp_path: Path) -> None:
    """Each request REPLIES records, sent in its order to a fake of its own, is answered as the fake answered it before
    card A5, byte for byte but for its UUIDs and its digests, which are never the same twice. The one answer A5 changed
    on purpose: a clone onto a VMID that is taken keeps its 500 and gains Proxmox's words."""
    recorded = json.loads(REPLIES.read_text())
    changed = 0
    for base in _fake_pve(tmp_path):
        for request in recorded:
            wanted = request["answer"]
            if request["path"].endswith("/clone") and wanted["status"] == 500:
                wanted = {**wanted, "reason": SAID["CLONE_EXISTS"].format(newid=request["params"]["newid"])}
                changed += 1
            assert _sent(base, request) == wanted, request
    assert changed == 1
