"""The one table of what the engine asks of Proxmox (lib/claude_vm/pve/privileges.py; docs/architecture/engine.md
§16.5): each call, its privilege and the kind of ACL path Proxmox checks it on, which init grants from, the permission
check reads and the fake Proxmox enforces.

The rules with a test of their own: every privilege the table names is one Proxmox VE 9 defines (a literal list here,
from the design's notes, N-14); the module imports nothing outside the standard library, so the fake can read it with
nothing but lib/ on its path; and the fake (tests/molecule/fake_pve_api.py) and the harness hold no privilege name of
their own but today's, so that the table is the one place a call's privilege is said. How the fake enforces the table
is tests/unit/test_aw_fake_pve.py's.
"""

from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from pathlib import Path

import pytest
from harness import FAKES, REPO, load_module

from claude_vm.pve import privileges
from claude_vm.pve.privileges import CALLS, KINDS, for_request, needed

TABLE = REPO / "lib" / "claude_vm" / "pve" / "privileges.py"
# The privileges Proxmox VE 9 defines that the design's notes read for the engine's calls (N-14 section 2: its table of
# calls, the privileges today's role holds, and those the pool design adds), from pve-access-control and the API
# viewer. VE 8's VM.Monitor is not one: the engine speaks to no older host (O9).
VE9 = {
    "Datastore.AllocateSpace",
    "Datastore.AllocateTemplate",
    "Datastore.Audit",
    "Pool.Allocate",
    "Pool.Audit",
    "SDN.Use",
    "Sys.AccessNetwork",
    "Sys.Audit",
    "VM.Allocate",
    "VM.Audit",
    "VM.Clone",
    "VM.Config.CDROM",
    "VM.Config.CPU",
    "VM.Config.Cloudinit",
    "VM.Config.Disk",
    "VM.Config.Memory",
    "VM.Config.Network",
    "VM.Config.Options",
    "VM.GuestAgent.Audit",
    "VM.GuestAgent.FileRead",
    "VM.GuestAgent.Unrestricted",
    "VM.PowerMgmt",
}
# What a privilege's name looks like, of each group Proxmox defines privileges in.
GROUPS = "VM|Sys|Datastore|SDN|Pool|User|Group|Realm|Permissions|Mapping"
PRIVILEGE = re.compile(rf"\b(?:{GROUPS})\.[A-Z][A-Za-z]*(?:\.[A-Z][A-Za-z]*)*")
# The privilege names the fake's code says outside today's whole role (ROLE), each as often as it does: today's
# answers to today's tokens, as the fake gave them at main (2e9f42d6) - privileges() and the agent's 403 for the
# noagent token, the listings' filters, a storage's disks - with GET /pools' Pool.Audit (card A4) and a node's status's
# Sys.Audit with its 403 (card B2). What a call needs of a made token is the table's, never a name added here.
TODAYS = Counter(
    {
        "Datastore.AllocateSpace": 1,
        "Datastore.Audit": 4,
        "Pool.Audit": 4,
        "SDN.Audit": 1,
        "SDN.Use": 1,
        "Sys.Audit": 3,
        "VM.GuestAgent.Audit": 2,
        "VM.Monitor": 1,
    }
)
PLACEHOLDERS = {"node", "vmid", "upid", "storage", "bridge", "pool"}


def _names(path: Path, skipped: tuple[str, ...] = ()) -> Counter:
    """Each privilege name path's code says, outside docstrings, comments and the assignments to the names skipped,
    with how often."""
    tree = ast.parse(path.read_text())
    docs = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    left_out = {
        id(inner)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in skipped for t in node.targets)
        for inner in ast.walk(node)
    }
    found = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs | left_out:
            found.update(PRIVILEGE.findall(node.value))
    return found


# ------------------------------------------------------------------------------------------------- the rules ----


def test_every_privilege_the_table_names_is_one_proxmox_ve_9_defines() -> None:
    named = {call.privilege for call in CALLS if call.privilege is not None}
    assert named and named <= VE9, named - VE9
    # And one the fakes' list of VE 9's takes, which a role the fake makes is held to: the harness makes the engine's
    # role from the table.
    cli = load_module(FAKES / "fake_pve_cli.py", register=False)
    assert named <= {*cli.COMMON, *cli.BY_MAJOR[9]}, named - {*cli.COMMON, *cli.BY_MAJOR[9]}


def test_the_table_imports_nothing_outside_the_standard_library() -> None:
    tree = ast.parse(TABLE.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"line {node.lineno}: a relative import"
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
    assert imported and imported <= set(sys.stdlib_module_names) | {"__future__"}, imported


def test_the_fake_and_the_harness_hold_no_privilege_name_of_their_own() -> None:
    """The fake reads the table and keeps none: outside today's whole role, its code names only what it named for
    today's tokens; and the harness names none, the engine's role being made from the table."""
    assert _names(FAKES / "fake_pve_api.py", ("ROLE",)) == TODAYS
    assert _names(REPO / "tests" / "unit" / "harness.py") == Counter()
    assert "from claude_vm.pve import privileges" in (FAKES / "fake_pve_api.py").read_text()


def test_the_scan_sees_a_privilege_name_in_code_and_none_in_a_docstring_or_a_comment(tmp_path: Path) -> None:
    source = tmp_path / "planted.py"
    source.write_text(
        '"""VM.Audit, said."""\n'
        "# VM.Clone, a comment\n"
        'ROLE = ["VM.Allocate"]\n'
        "def f(path):\n"
        '    """Sys.Audit, said."""\n'
        '    return f"Permission check failed ({path}, VM.Config.Options)" + "Pool.Audit"\n'
    )
    assert _names(source, ("ROLE",)) == Counter({"VM.Config.Options": 1, "Pool.Audit": 1})


# ------------------------------------------------------------------------------------------------- its shape ----


def test_every_row_is_one_call_s_privilege_on_a_kind_or_none_at_all() -> None:
    assert isinstance(CALLS, tuple) and all(isinstance(call, privileges.Call) for call in CALLS)
    names = {}
    for call in CALLS:
        assert call.method in privileges.METHODS, call
        assert call.route.startswith("/") and not call.route.startswith("/api2") and "?" not in call.route, call
        held = {part[1:-1] for part in call.route.strip("/").split("/") if part.startswith("{")}
        assert held <= PLACEHOLDERS, call
        assert (call.privilege is None) == (call.on is None), call
        assert call.on is None or call.on in KINDS, call
        # One call, one name: the rows of a route say what the client calls it.
        assert names.setdefault((call.method, call.route), call.name) == call.name, call
    assert len(set(CALLS)) == len(CALLS)


def test_no_two_routes_of_a_method_can_be_one_request() -> None:
    routes = sorted({(call.method, call.route) for call in CALLS})
    for i, (method, route) in enumerate(routes):
        for other_method, other in routes[i + 1 :]:
            mine, theirs = route.strip("/").split("/"), other.strip("/").split("/")
            if method != other_method or len(mine) != len(theirs):
                continue
            assert any(
                a != b and not a.startswith("{") and not b.startswith("{") for a, b in zip(mine, theirs, strict=True)
            ), (method, route, other)


def test_a_request_is_its_call_s_rows_each_with_the_acl_path_its_own_path_names() -> None:
    (config,) = for_request("GET", "/nodes/pve/qemu/9010/config")
    assert (config[0].name, config[0].privilege, config[1]) == ("config", "VM.Audit", "/vms/9010")
    clone = for_request("POST", "/nodes/pve/qemu/9000/clone")
    assert [(call.privilege, at) for call, at in clone] == [
        ("VM.Clone", "/vms/9000"),
        ("VM.Allocate", None),
        ("Datastore.AllocateSpace", None),
    ]
    (status,) = for_request("GET", "/nodes/pve/status")
    assert status[1] == "/nodes/pve"
    (disks,) = for_request("GET", "/nodes/pve/storage/local-lvm/content")
    assert disks[1] == "/storage/local-lvm"
    # A listing's rows name no path: each entry is held to them.
    listed = for_request("GET", "/cluster/resources")
    assert {call.on for call, _ in listed} == {"vm", "storage", "pool", "node"} and {at for _, at in listed} == {None}
    # A call that needs nothing is a row all the same.
    assert [(call.privilege, at) for call, at in for_request("GET", "/version")] == [(None, None)]
    # The writes: a configuration's, with each group of the keys the client sends; a resize, on the VM and the storage
    # its configuration names.
    write = for_request("PUT", "/nodes/pve/qemu/9010/config")
    assert {call.privilege for call, _ in write} == {
        "VM.Config.Options",
        "VM.Config.Cloudinit",
        "VM.Config.CPU",
        "VM.Config.Memory",
    }
    assert {at for _, at in write} == {"/vms/9010"}
    resize = for_request("PUT", "/nodes/pve/qemu/9010/resize")
    assert [(call.privilege, at) for call, at in resize] == [
        ("VM.Config.Disk", "/vms/9010"),
        ("Datastore.AllocateSpace", None),
    ]
    for method, path, privilege in [
        ("POST", "/nodes/pve/qemu/9010/status/start", "VM.PowerMgmt"),
        ("POST", "/nodes/pve/qemu/9010/status/shutdown", "VM.PowerMgmt"),
        ("POST", "/nodes/pve/qemu/9010/status/stop", "VM.PowerMgmt"),
        ("POST", "/nodes/pve/qemu/9010/template", "VM.Allocate"),
        ("DELETE", "/nodes/pve/qemu/9010", "VM.Allocate"),
        # What a VM's guest agent reports of its network cards, for its address.
        ("GET", "/nodes/pve/qemu/9010/agent/network-get-interfaces", "VM.GuestAgent.Audit"),
    ]:
        assert [(call.privilege, at) for call, at in for_request(method, path)] == [(privilege, "/vms/9010")], path


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/nodes/pve/qemu/9010/agent/get-osinfo"),
        ("POST", "/nodes/pve/qemu/9010/agent/ping"),
        ("POST", "/nodes/pve/qemu/9010/status/reboot"),
        ("DELETE", "/nodes/pve/qemu/9010/config"),
        ("POST", "/nodes/pve/qemu/9010/config"),
        ("PUT", "/pools"),
        ("PUT", "/pools/aw-fleet"),
        ("POST", "/pools"),
        ("DELETE", "/pools"),
        ("GET", "/nodes/pve/qemu/9010/config/extra"),
        ("GET", "/nodes/../qemu/9010/config"),
        ("GET", "/nodes//qemu/9010/config"),
        ("GET", "/api2/json/version"),
        ("PUT", "/version"),
    ],
)
def test_a_call_no_row_names_has_none(method: str, path: str) -> None:
    assert for_request(method, path) == ()


def test_needed_is_what_the_rows_check_on_a_kind_and_on_a_pool_what_its_members_take_from_it() -> None:
    on = {kind: {call.privilege for call in CALLS if call.on == kind} for kind in KINDS}
    for kind in KINDS:
        assert needed(kind) == (on[kind] | on["vm"] if kind == "pool" else on[kind]), kind
    assert set().union(*(needed(kind) for kind in KINDS)) == {call.privilege for call in CALLS} - {None}
    assert {"VM.Audit", "VM.Clone"} <= needed("vm") and {"Pool.Audit", "VM.Allocate"} <= needed("pool")
    with pytest.raises(ValueError):
        needed("access")


def test_no_call_writes_a_pool_and_the_role_on_the_pool_holds_no_pool_privilege_but_pool_audit() -> None:
    """A new VM joins the pool through its clone's pool, and the fleet's earlier VMs over root SSH (K5): the engine's
    token adds no guest to a pool, and holds no Pool.Allocate, which would let it rewrite its pool's mark and delete the
    pool once empty."""
    writing = [call for call in CALLS if call.method != "GET" and call.route.strip("/").split("/")[0] == "pools"]
    assert writing == []
    named = {call.privilege for call in CALLS if call.privilege and call.privilege.startswith("Pool.")}
    assert named == {"Pool.Audit"}
    assert {privilege for privilege in needed("pool") if privilege.startswith("Pool.")} == {"Pool.Audit"}
