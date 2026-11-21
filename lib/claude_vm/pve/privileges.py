"""The one table of what the engine asks of Proxmox: each API call, the privilege it needs and the kind of ACL path
Proxmox checks it on (docs/architecture/engine.md §16.5).

init grants from it (needed: E1 makes the engine's role of it), the permission check names what the token lacks of it
(E6), and the fake Proxmox (tests/molecule/fake_pve_api.py) enforces it when a test turns that on (for_request), so
that a test proves what the engine's token can and cannot do from the same table init grants. Experiment 6 corrects
it from a real host (E14). Proxmox VE 9's names only: the engine speaks to no older host (O9).

Each row is one privilege of one call: a call that needs several has a row for each, and needs every one. A row's kind
says where Proxmox checks it:

- vm, /vms/<vmid>, which a VM that is a member of a pool also takes from the roles held on /pool/<pool>, without
  propagate and never over a role its own path sets (PVE/RPCEnvironment.pm, compile_acl_path);
- pool, /pool/<pool>;
- storage, /storage/<storage>;
- bridge, /sdn/zones/localnetwork/<bridge>, a bridge of the node's own network;
- node, /nodes/<node>.

A row with no privilege needs none (the version, the next free VMID, the token's own permissions). Where the call's
path names the object a row is checked on, for_request gives the concrete ACL path; where it does not, the call names
it in its parameters (a clone's pool and storage, a pool read by its poolid) or its VM's configuration does (the
storage of the disk a resize grows), or the call lists such objects, each listed only where the row's privilege is held
on it (GET /cluster/resources, a node's storages and bridges): Proxmox's own code says which, and so does the fake's.

No call writes a pool, so of Pool.* the table names Pool.Audit alone: a new VM joins the fleet's pool through its
clone's pool, which needs VM.Allocate on the pool and nothing of Pool.*, and the VMs a fleet already had join it over
root SSH (card K5). A token whose role is on its pool alone could add no guest it did not already hold, and
Pool.Allocate would let it rewrite its pool's comment, the fleet's mark, and delete the pool once empty.

Standard library only: the fake imports this module with nothing but the checkout's lib/ on its path.
"""

from __future__ import annotations

import dataclasses

__all__ = ["CALLS", "KINDS", "Call", "for_request", "needed"]

# Where a row of each kind is checked, by the name its object has in a call's route.
KINDS = {
    "vm": ("vmid", "/vms/{vmid}"),
    "pool": ("pool", "/pool/{pool}"),
    "storage": ("storage", "/storage/{storage}"),
    "bridge": ("bridge", "/sdn/zones/localnetwork/{bridge}"),
    "node": ("node", "/nodes/{node}"),
}
METHODS = frozenset({"GET", "POST", "PUT", "DELETE"})


@dataclasses.dataclass(frozen=True)
class Call:
    """One privilege of one call: the client's name for the call, its method, its route below /api2/json with each
    name in braces (/nodes/{node}/qemu/{vmid}/config), the privilege, and the kind of ACL path it is checked on; or
    no privilege and no kind, for a call that needs none."""

    name: str
    method: str
    route: str
    privilege: str | None
    on: str | None


# Filled from N-14's table of calls for Proxmox VE 9, with the calls the base makes once they are built: the client's
# reads (card B2), its writes (card B4) and the guest agent's (card B6), each privilege read from Proxmox's source.
CALLS = (
    # The reads every workflow makes (client.py).
    Call("version", "GET", "/version", None, None),
    # The cluster's listing: a guest listed only with VM.Audit on it, a storage with Datastore.Audit, a pool with
    # Pool.Audit; a node always, its figures only with Sys.Audit (PVE/API2/Cluster.pm).
    Call("resources", "GET", "/cluster/resources", "VM.Audit", "vm"),
    Call("resources", "GET", "/cluster/resources", "Datastore.Audit", "storage"),
    Call("resources", "GET", "/cluster/resources", "Pool.Audit", "pool"),
    Call("resources", "GET", "/cluster/resources", "Sys.Audit", "node"),
    # The pool named by poolid, with its members; with none, the pools listed (pve-manager's PVE/API2/Pool.pm).
    Call("pool", "GET", "/pools", "Pool.Audit", "pool"),
    Call("config", "GET", "/nodes/{node}/qemu/{vmid}/config", "VM.Audit", "vm"),
    Call("current", "GET", "/nodes/{node}/qemu/{vmid}/status/current", "VM.Audit", "vm"),
    Call("next_id", "GET", "/cluster/nextid", None, None),
    Call("node_status", "GET", "/nodes/{node}/status", "Sys.Audit", "node"),
    # A task's: Proxmox asks Sys.Audit on its node only of a token that is not the task's owner (PVE/API2/Tasks.pm),
    # and the engine reads the tasks it made.
    Call("task_status", "GET", "/nodes/{node}/tasks/{upid}/status", "Sys.Audit", "node"),
    Call("task_log", "GET", "/nodes/{node}/tasks/{upid}/log", "Sys.Audit", "node"),
    # A node's storages for VM disks and its bridges, each listed only where the row's privilege is held on it.
    Call("storages", "GET", "/nodes/{node}/storage", "Datastore.Audit", "storage"),
    Call("bridges", "GET", "/nodes/{node}/network", "SDN.Use", "bridge"),
    # The token's own, which needs nothing (PVE/API2/AccessControl.pm).
    Call("permissions", "GET", "/access/permissions", None, None),
    # What status adds through read(): a VM's day of history, its pending changes, and a storage's disks.
    Call("read", "GET", "/nodes/{node}/qemu/{vmid}/rrddata", "VM.Audit", "vm"),
    Call("read", "GET", "/nodes/{node}/qemu/{vmid}/pending", "VM.Audit", "vm"),
    Call("read", "GET", "/nodes/{node}/storage/{storage}/content", "Datastore.Audit", "storage"),
    # A clone (N-14 row 6): VM.Clone on its source; VM.Allocate on the pool it names, or with none on the new VM's
    # own path; Datastore.AllocateSpace on the storage its disk goes to, the one named or its source's (qemu-server's
    # clone_vm and check_storage_access_clone).
    Call("clone", "POST", "/nodes/{node}/qemu/{vmid}/clone", "VM.Clone", "vm"),
    Call("clone", "POST", "/nodes/{node}/qemu/{vmid}/clone", "VM.Allocate", "pool"),
    Call("clone", "POST", "/nodes/{node}/qemu/{vmid}/clone", "Datastore.AllocateSpace", "storage"),
    # A VM's configuration, written synchronously (N-14 row 9): Proxmox checks each key it is sent with the privilege of
    # its group (qemu-server's check_vm_modify_config_perm), and the client sends the keys of four groups alone
    # (client.CONFIG_KEYS): the record's notes, its tags and the general options; cloud-init's; the CPU's; the memory's.
    Call("write_config", "PUT", "/nodes/{node}/qemu/{vmid}/config", "VM.Config.Options", "vm"),
    Call("write_config", "PUT", "/nodes/{node}/qemu/{vmid}/config", "VM.Config.Cloudinit", "vm"),
    Call("write_config", "PUT", "/nodes/{node}/qemu/{vmid}/config", "VM.Config.CPU", "vm"),
    Call("write_config", "PUT", "/nodes/{node}/qemu/{vmid}/config", "VM.Config.Memory", "vm"),
    # A disk grown (N-14 row 10): VM.Config.Disk on the VM, and in its task Datastore.AllocateSpace on the storage the
    # disk is on (qemu-server's resize_vm).
    Call("resize", "PUT", "/nodes/{node}/qemu/{vmid}/resize", "VM.Config.Disk", "vm"),
    Call("resize", "PUT", "/nodes/{node}/qemu/{vmid}/resize", "Datastore.AllocateSpace", "storage"),
    # A VM started, shut down or stopped (N-14 rows 11 and 20): VM.PowerMgmt.
    Call("start", "POST", "/nodes/{node}/qemu/{vmid}/status/start", "VM.PowerMgmt", "vm"),
    Call("shutdown", "POST", "/nodes/{node}/qemu/{vmid}/status/shutdown", "VM.PowerMgmt", "vm"),
    Call("stop", "POST", "/nodes/{node}/qemu/{vmid}/status/stop", "VM.PowerMgmt", "vm"),
    # A VM made a template, and a VM deleted with its disks (N-14 rows 21 and 22): VM.Allocate.
    Call("to_template", "POST", "/nodes/{node}/qemu/{vmid}/template", "VM.Allocate", "vm"),
    Call("delete", "DELETE", "/nodes/{node}/qemu/{vmid}", "VM.Allocate", "vm"),
    # What a started VM's guest agent reports of its network cards, for its address (N-14 row 13): VM.GuestAgent.Audit,
    # or VM.GuestAgent.Unrestricted, which the engine is never granted (qemu-server's PVE/API2/Qemu/Agent.pm).
    Call(
        "agent_interfaces", "GET", "/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces", "VM.GuestAgent.Audit", "vm"
    ),
)


def _parts(route: str) -> list[str]:
    return route.strip("/").split("/")


def _placeholder(part: str) -> str:
    """The name in a route's part, {node}, or "" for a part that is literal."""
    return part[1:-1] if part.startswith("{") and part.endswith("}") and len(part) > 2 else ""


def _matches(route: str, path: str) -> dict[str, str] | None:
    """The names path gives each of route's placeholders, or None where it is not route's: each placeholder one part,
    never empty, never . or .., and every other part the route's own."""
    want, got = _parts(route), _parts(path)
    if len(want) != len(got):
        return None
    named = {}
    for part, given in zip(want, got, strict=True):
        name = _placeholder(part)
        if name and given not in ("", ".", ".."):
            named[name] = given
        elif name or part != given:
            return None
    return named


def for_request(method: str, path: str) -> tuple[tuple[Call, str | None], ...]:
    """The rows of the call a request is, each with the concrete ACL path its privilege is checked on where the
    request's path names it, else None (the call names it in its parameters, or lists such objects); () where no row
    names the call. path is below /api2/json, with no query."""
    found = []
    for call in CALLS:
        if call.method != method:
            continue
        named = _matches(call.route, path)
        if named is None:
            continue
        at = None
        if call.on is not None:
            key, template = KINDS[call.on]
            at = template.format(**{key: named[key]}) if key in named else None
        found.append((call, at))
    return tuple(found)


def needed(on: str) -> frozenset[str]:
    """The privileges the table needs on one kind of path: what init grants there (E1) and the permission check reads
    the token's permissions there against (E6). On a pool, also every privilege checked on a VM: a VM of the pool
    takes the pool's roles, so the role on the pool is what gives them to its members."""
    if on not in KINDS:
        raise ValueError(f"a kind of ACL path is one of {', '.join(KINDS)}")
    kinds = {on, "vm"} if on == "pool" else {on}
    return frozenset(call.privilege for call in CALLS if call.on in kinds and call.privilege is not None)
