"""The one table of what the engine asks of Proxmox: each API call, the privilege it needs and the kind of ACL path
Proxmox checks it on, as Proxmox checks it (docs/architecture/engine.md §16.5).

init grants from it (needed: E1 makes the engine's role of it), the permission check names what the token lacks of it
(E6), and the fake Proxmox (tests/molecule/fake_pve_api.py) enforces it when a test turns that on (for_request), so
that a test proves what the engine's token can and cannot do from the same table init grants. Experiment 6 corrects
it from a real host (E14). Proxmox VE 9's names only: the engine speaks to no older host (O9).

Each row is one privilege of one call: a call that needs several has a row for each, and needs every one. A row's kind
says where Proxmox checks it:

- vm, /vms/<vmid>, which a VM that is a member of a pool also takes from the roles held on /pool/<pool>, without
  propagate and never over a role its own path sets (PVE/RPCEnvironment.pm, compile_acl_path);
- new, /vms/<newid>, the VM a clone makes, named by the clone's newid: no member of any pool until its clone's task
  ends, so no role init grants reaches it, and a row checked there names another kind first;
- pool, /pool/<pool>;
- storage, /storage/<storage>;
- bridge, /sdn/zones/localnetwork/<bridge>, a bridge of the node's own network;
- node, /nodes/<node>.

Where Proxmox takes either of two privileges, or either of two paths, the row says so: its privilege, or its kind, is
then a tuple of the alternatives, any of which suffices (a node's storages and a storage's disks take Datastore.Audit
or Datastore.AllocateSpace, a node's bridges SDN.Use or SDN.Audit, a clone's VM.Allocate the pool's path or the new
VM's). The first of them is the one init grants and the permission check asks for: needed() takes it alone, so a
second way Proxmox lets a token through widens no role.

A configuration's write is checked key by key, each key with the privilege of its group (qemu-server's
check_vm_modify_config_perm): its rows each carry the keys they cover, and a row with keys is checked only for a
request that sends one of them, or deletes one; a key no row of its call covers is one the engine does not send
(keys(), which the client holds every write to), and the fake refuses it.

A row with no privilege needs none (the version, the next free VMID, the token's own permissions). Where the call's
path names the object a row is checked on, for_request gives the concrete ACL path; where it does not, the call names
it in its parameters (a clone's pool, its new VM and its storage, a pool read by its poolid) or its VM's configuration
does (the storage of the disk a resize grows), or the call lists such objects, each listed only where the row's
privilege is held on it (GET /cluster/resources, a node's storages and bridges): Proxmox's own code says which, and so
does the fake's.

No call writes a pool, so of Pool.* the table names Pool.Audit alone: a new VM joins the fleet's pool through its
clone's pool, which needs VM.Allocate on the pool and nothing of Pool.*. A token whose role is on its pool alone could
add no guest it did not already hold, and Pool.Allocate would let it rewrite its pool's comment, the fleet's mark, and
delete the pool once empty.

Standard library only: the fake imports this module with nothing but the checkout's lib/ on its path.
"""

from __future__ import annotations

import dataclasses

__all__ = ["CALLS", "KINDS", "Call", "for_request", "keys", "needed"]

# Where a row of each kind is checked, by the name its object has in a call's route or its parameters.
KINDS = {
    "vm": ("vmid", "/vms/{vmid}"),
    "new": ("newid", "/vms/{newid}"),
    "pool": ("pool", "/pool/{pool}"),
    "storage": ("storage", "/storage/{storage}"),
    "bridge": ("bridge", "/sdn/zones/localnetwork/{bridge}"),
    "node": ("node", "/nodes/{node}"),
}
METHODS = frozenset({"GET", "POST", "PUT", "DELETE"})
# A VM's cloud-init network settings, one for each of its network cards, net0 to net31: ipconfig0 to ipconfig31.
IPCONFIGS = tuple(f"ipconfig{card}" for card in range(32))


@dataclasses.dataclass(frozen=True)
class Call:
    """One privilege of one call: the client's name for the call, its method, its route below /api2/json with each
    name in braces (/nodes/{node}/qemu/{vmid}/config), the privilege, and the kind of ACL path it is checked on; or
    no privilege and no kind, for a call that needs none. The privilege, or the kind, may be a tuple of alternatives,
    any of which suffices, the first the one init grants. keys, for a configuration's write, are the keys the row
    covers: it is checked only where a request sends one of them."""

    name: str
    method: str
    route: str
    privilege: str | tuple[str, ...] | None
    on: str | tuple[str, ...] | None
    keys: tuple[str, ...] = ()

    @property
    def privileges(self) -> tuple[str, ...]:
        """The privileges any of which the row takes, the first the one init grants; () for none."""
        return (
            () if self.privilege is None else (self.privilege,) if isinstance(self.privilege, str) else self.privilege
        )

    @property
    def kinds(self) -> tuple[str, ...]:
        """The kinds of ACL path the privilege may be held on, any of which suffices, the first the one init grants it
        on; () for none."""
        return () if self.on is None else (self.on,) if isinstance(self.on, str) else self.on


CONFIG = "/nodes/{node}/qemu/{vmid}/config"

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
    Call("config", "GET", CONFIG, "VM.Audit", "vm"),
    Call("current", "GET", "/nodes/{node}/qemu/{vmid}/status/current", "VM.Audit", "vm"),
    Call("next_id", "GET", "/cluster/nextid", None, None),
    Call("node_status", "GET", "/nodes/{node}/status", "Sys.Audit", "node"),
    # A task's: Proxmox asks Sys.Audit on its node only of a token that is not the task's owner (PVE/API2/Tasks.pm),
    # and the engine reads the tasks it made.
    Call("task_status", "GET", "/nodes/{node}/tasks/{upid}/status", "Sys.Audit", "node"),
    Call("task_log", "GET", "/nodes/{node}/tasks/{upid}/log", "Sys.Audit", "node"),
    # A node's storages for VM disks, each listed where Datastore.Audit or Datastore.AllocateSpace is held on it
    # (pve-storage's PVE/API2/Storage/Status.pm, check_any); its bridges, each where SDN.Use or SDN.Audit is held on it
    # (pve-manager's PVE/API2/Network.pm, check_sdn_bridge): SDN.Use first, which a clone's bridge needs too.
    Call("storages", "GET", "/nodes/{node}/storage", ("Datastore.Audit", "Datastore.AllocateSpace"), "storage"),
    Call("bridges", "GET", "/nodes/{node}/network", ("SDN.Use", "SDN.Audit"), "bridge"),
    # The token's own, which needs nothing (PVE/API2/AccessControl.pm).
    Call("permissions", "GET", "/access/permissions", None, None),
    # What status adds through read(): a VM's day of history, its pending changes, and a storage's disks, which take
    # Datastore.Audit or Datastore.AllocateSpace on the storage (PVE/API2/Storage/Content.pm, any).
    Call("read", "GET", "/nodes/{node}/qemu/{vmid}/rrddata", "VM.Audit", "vm"),
    Call("read", "GET", "/nodes/{node}/qemu/{vmid}/pending", "VM.Audit", "vm"),
    Call(
        "read",
        "GET",
        "/nodes/{node}/storage/{storage}/content",
        ("Datastore.Audit", "Datastore.AllocateSpace"),
        "storage",
    ),
    # A clone (N-14 row 6): VM.Clone on its source; VM.Allocate on the pool it names or on the new VM's own path, either
    # of which suffices, and with no pool named the new VM's alone (the call's declared check, an or of the two);
    # Datastore.AllocateSpace on the storage its disk goes to, the one named or its source's (qemu-server's clone_vm and
    # check_storage_access_clone).
    Call("clone", "POST", "/nodes/{node}/qemu/{vmid}/clone", "VM.Clone", "vm"),
    Call("clone", "POST", "/nodes/{node}/qemu/{vmid}/clone", "VM.Allocate", ("pool", "new")),
    Call("clone", "POST", "/nodes/{node}/qemu/{vmid}/clone", "Datastore.AllocateSpace", "storage"),
    # A VM's configuration, written synchronously (N-14 row 9): Proxmox checks each key it is sent, or deletes, with the
    # privilege of its group (qemu-server's check_vm_modify_config_perm), and the client sends the keys of these rows
    # alone: the record's notes, its tags, its name and the general options; cloud-init's, of which nameserver and
    # searchdomain take VM.Config.Network as well; the CPU's; the memory's. tags are checked by a rule of their own
    # (assert_tag_permissions, not read from its source here), which under the datacenter's default, free tags, asks
    # VM.Config.Options.
    Call("write_config", "PUT", CONFIG, "VM.Config.Options", "vm", ("description", "tags", "name", "onboot", "agent")),
    Call(
        "write_config",
        "PUT",
        CONFIG,
        "VM.Config.Cloudinit",
        "vm",
        ("ciuser", "cipassword", "sshkeys", "ciupgrade", *IPCONFIGS),
    ),
    Call(
        "write_config",
        "PUT",
        CONFIG,
        ("VM.Config.Cloudinit", "VM.Config.Network"),
        "vm",
        ("nameserver", "searchdomain"),
    ),
    Call("write_config", "PUT", CONFIG, "VM.Config.CPU", "vm", ("cores",)),
    Call("write_config", "PUT", CONFIG, "VM.Config.Memory", "vm", ("memory", "balloon")),
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


def for_request(method: str, path: str) -> tuple[tuple[Call, tuple[str | None, ...]], ...]:
    """The rows of the call a request is, each with, for each kind of ACL path it may be checked on (its kinds, in
    order), the concrete path the request's own path names, else None (the call names it in its parameters, or lists
    such objects); () where no row names the call. path is below /api2/json, with no query."""
    found = []
    for call in CALLS:
        if call.method != method:
            continue
        named = _matches(call.route, path)
        if named is None:
            continue
        paths = []
        for kind in call.kinds:
            key, template = KINDS[kind]
            paths.append(template.format(**{key: named[key]}) if key in named else None)
        found.append((call, tuple(paths)))
    return tuple(found)


def needed(on: str) -> frozenset[str]:
    """The privileges the table needs on one kind of path: what init grants there (E1) and the permission check reads
    the token's permissions there against (E6), each row's first privilege on its first kind, never an alternative. On
    a pool, also every privilege checked on a VM: a VM of the pool takes the pool's roles, so the role on the pool is
    what gives them to its members."""
    if on not in KINDS:
        raise ValueError(f"a kind of ACL path is one of {', '.join(KINDS)}")
    kinds = {on, "vm"} if on == "pool" else {on}
    return frozenset(call.privileges[0] for call in CALLS if call.kinds and call.kinds[0] in kinds)


def keys(name: str) -> frozenset[str]:
    """The configuration keys the rows of the call name cover, which are the keys its requests may send: a key outside
    them has no row, so init would grant nothing for it, and the client refuses it before anything is sent."""
    return frozenset(key for call in CALLS if call.name == name for key in call.keys)
