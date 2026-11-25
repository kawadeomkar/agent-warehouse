#!/usr/bin/env python3
"""Minimal stateful stand-in for the Proxmox VE REST API.

Implements just enough of /api2/json for community.proxmox's proxmox_vm_info,
proxmox_kvm (clone / update / start / shutdown / delete) and proxmox_disk
(resize) so roles/proxmox_vm can be exercised without a hypervisor, plus
converting a VM to a template for the tests' fixtures. Every
request is appended as a JSON line to <state_dir>/calls.log and the VM table is
written to <state_dir>/state.json after each mutation, for verification.

One guest is a container, listed by /cluster/resources?type=vm as type "lxc"
the way a real cluster lists containers alongside VMs, and tagged as this
project's. It is not a VM: /nodes/<node>/qemu does not list it and any
/nodes/<node>/qemu/<its id>/... request fails as it would for a VMID that is
not a VM. It proves the consumers of the listing filter on type, and that a
fleet's VMIDs skip the ones containers hold.

The cluster has a second node that is down, holding one VM that is none of this
project's business and one that is, so every test also proves that one
unreachable node does not break commands about VMs on the others. Guests on it
are listed the way a real cluster lists them once the node's statistics have
expired: VMID, node, type, tags and status "unknown", with no name or template
key at all.

/cluster/resources reports each VM's status the moment it changes, unless a
fixture turns on the lag with PUT /api2/json/fake/listing-lag?listings=N: then,
after a start or a stop, the next N listings still report the status from
before it, the way a real cluster lists the status pvestatd last polled, up to
ten seconds old. A pool's listing of its members (GET /pools?poolid=), which
Proxmox fills from the same polled status, lags by the same map, and each
listing of either counts. /nodes/<node>/qemu/<id>/status/current is never stale.

PUT /api2/json/fake/unnamed?vmid=N has /cluster/resources, and a pool's listing,
give that guest with no name or template key, as the cluster lists a guest its
node has not reported yet - a VM made seconds ago, a clone still running - which
only its own configuration names; with &configuration=1 its configuration holds
no name either, as a clone's holds none until its worker writes the rest.

A started VM's guest agent fails its first two polls, so every wait for it is
exercised; a fixture that is not about that wait answers from the first with
PUT /api2/json/fake/agent-ready-after?polls=0. What a VM's agent reports of its
network cards is the VM's to choose: PUT /api2/json/fake/agent-answer?vmid=
&result=<JSON> plants it, in place of the usual lo, docker0 and eth0. A VM whose
configuration turns no guest agent on (no agent, or agent=0) has every agent
call refused first with NO_AGENT, as Proxmox refuses it before it asks whether
the VM runs; every VM the fake makes takes the template's.

GET /access/permissions answers each token's privileges by path, as Proxmox
does - the privilege's name as the key, 1 or 0 its propagate flag - from a
table a fixture fills with PUT /api2/json/fake/privileges?token=&path=&privs=
(a comma-separated list, empty for none; "VM.Clone:0" without Propagate); a
path the table does not name holds
the whole role tools/proxmox-access.sh makes, its guest-agent
privilege the one of the version /version reports, which PUT
/api2/json/fake/version?version= changes (8.2.4 until then).

Each node lists its storages for VM disks (GET /nodes/<node>/storage) and its
bridges (GET /nodes/<node>/network), each only where the token may see it -
Datastore.Audit or Datastore.AllocateSpace on /storage/<name>, SDN.Audit or
SDN.Use on /sdn/zones/localnetwork/<bridge> - as Proxmox filters them: on
pve, local-lvm (lvmthin) and vmbr0 until a fixture replaces them with PUT
/api2/json/fake/storages?node=&storages=<name>:<type>[:inactive],... or
/api2/json/fake/bridges?node=&bridges=<name>,... A fixture moves a VM to
another node with PUT /api2/json/fake/vm-node?vmid=&node=.

Pools: a clone given pool= is a member of that pool once its task ends, which /cluster/resources
lists as its "pool", as Proxmox does; PUT /api2/json/fake/pool-member?vmid=&pool=
makes a guest one, as root does on the host. PUT
/api2/json/fake/pool-scope?token=&pool= stands for a token granted the role on
/pool/<name> alone: its listing holds that pool's members only.

Pools are also made, read, changed and deleted through Proxmox's own routes, as
pve-manager's PVE/API2/Pool.pm answers them: POST /pools (poolid, comment);
GET /pools, the pools the token may audit, each with its comment, or with
poolid (and type: qemu, lxc or storage) that pool with its members; PUT /pools
(poolid, comment, vms, storage, delete, allow-move); DELETE /pools?poolid= of
an empty pool; and the deprecated GET, PUT and DELETE /pools/<poolid>, answered
the same way. A pool a guest is a member of exists too, made or not, as it did
before these routes. A refusal is a 500 with Proxmox's sentence, after the
label Proxmox gives it ("update pools failed: VM 9010 is already a pool
member"). One difference, on purpose: the fake keeps a guest's pool on the
guest, so it refuses a VMID it holds no guest for, which Proxmox would take.

Users, roles, ACL entries and tokens are made as pve-access-control's API2
modules make them: POST /access/users (userid, expire), POST /access/roles
(roleid, privs), PUT /access/acl (path, roles, users, tokens, propagate,
delete; the fake has no groups), and POST
/access/users/<userid>/token/<tokenid> (privsep, 1 unless given; expire, the
user's unless given; comment), which answers info, value - the token's
secret, a UUID - and full-tokenid; PUT on the same path changes privsep,
expire and comment, deletes the comment, and with regenerate=1 makes a new
secret, answering the token's info with value and full-tokenid. A token made
so is accepted with its own secret alone, until its expire if it has one.

GET /access/permissions answers today's tokens - any token ID with the secret
the fake was started with, the noagent one among them - as above, from the
table and the whole role. A token made through /access is answered from the
ACL entries, as PVE/RPCEnvironment.pm computes them: down from / to the path,
the deepest entries naming it win, an entry above the path only where it
propagates, and NoAccess among them stands alone; a guest or a storage that is
a pool's member (of each pool that holds it) also takes each role held on
/pool/<name> that its own path lacks, without propagate, whatever the pool's
entry says - but never over a NoAccess of its own path, and a pool's NoAccess
leaves it NoAccess alone; a token with privsep=0 holds its user's privileges,
one with privsep=1 its own entries' cut to what its user holds, propagating
where both do. The roles Proxmox has from the start are there: NoAccess, with
no privileges, and those of the stand-in pveum beside this file
(fake_pve_cli.py: PVEAuditor), none of which can be made again; a role takes
only the privileges Proxmox VE 9 defines, as that stand-in knows them. A clone
a made token sends into a pool that does not exist is refused, as
check_pool_exist refuses it.

Everything else a made token asks is answered as a today's token's is, nothing
refused for lack of a privilege, until a fixture turns enforcement on with PUT
/api2/json/fake/enforce?on=1 (on=0 turns it off): then each request of a made
token is held to the engine's one table of privileges,
lib/claude_vm/pve/privileges.py, read from the checkout's lib/ beside this file,
the fake keeping none of its own. A request is refused (403) where the token
lacks, on its path, the privilege a row of its call names - the path the
request's own path names, or its parameters (a clone's pool, its new VM and its
storage, a pool's poolid; a clone with no storage on its source's disk's), or
its VM's configuration (the storage of the disk a resize grows) - by the same
function that answers GET /access/permissions. Where a row names either of two
privileges, or either of two paths (a node's storages and a storage's disks,
Datastore.Audit or Datastore.AllocateSpace; a node's bridges, SDN.Use or
SDN.Audit; a clone's VM.Allocate, on the pool it names or on its new VM's path),
any of them held lets it through. A configuration's write is held key by key:
each key it sends or deletes to the privilege of the row that covers it, a key
sent with an empty value too, as Proxmox checks every key of the form, and a
key no row covers is refused. A task's owner needs nothing to read it; a listing
(/cluster/resources, a node's storages and bridges, the pools: LISTINGS) shows
each entry only where the token holds what its row names on it, and a node's
figures only so, and a guest's pool only where its pool's row is held; and a
call no row names, a key, or a GET row that neither the request's path nor its
parameters place and that its route lists no entry of, which nothing would
check, is refused the same way and recorded in <state_dir>/unmatched.log. Each
row a request was decided by - held on a path, read by a listing for its
entries, or needing nothing - is recorded in <state_dir>/decided.log, by its
index in the table: a row in no line of it was checked by nothing. Enforcement
answers before a call's own checks, so one difference from Proxmox stays: a
request whose parameter Proxmox's schema refuses (a 400) is answered 403 first
where the token lacks the privilege. A request for an object that does not
exist is answered as Proxmox answers it, the privilege checked first there too.
A request to a node that is down still gets its 595, as pveproxy answers it
before the node's own check. Today's tokens are answered as now, enforcement
or not.

Whatever enforcement says, a token made through /access is answered two
refusals of Proxmox's own that today's tokens are not: /cluster/resources asked
for a type its schema does not hold - pool, which an answer names but a request
may not ask for - is a 400 with the schema's words (ENUM_REFUSED); and a
configuration's PUT or POST that sends no key and deletes none, digest aside, is
update_vm_api's 500 (NO_OPTIONS).

A second token id, "noagent", stands for a token created without the
guest-agent privilege (VM.Monitor on PVE 8, VM.GuestAgent.Audit on 9): every
request is accepted except the agent's, which is refused with 403.

For `just status` (tools/status): /cluster/resources?type=storage lists
each storage the token may audit with its fill, and ?type=node the nodes, with
no statistics, as a token without Sys.Audit sees them - but a node a fixture
grants Sys.Audit on (PUT /api2/json/fake/privileges?token=&path=/nodes/<node>
&privs=Sys.Audit), listed with NODE_STATS. A running VM is listed
with its CPU, memory and uptime, and /nodes/<node>/qemu/<id>/status/current,
rrddata (a day of points), pending, and the agent's get-osinfo and get-fsinfo
answer as Proxmox VE does. memhost and the pressure figures are VE 9's: the fake
reports them only once /version says 9 or later, and a day of rrddata is 1440
points then, 70 on 8. /nodes/<node>/storage/<id>/content lists the disks on a
storage. Pending lists a VM's config with nothing waiting for a restart, and
every VM's root disk is 11 GiB used of 31. A clone's config carries the
template's meta ctime, as Proxmox VE 9.2's did when it was checked against a real
clone; the moment it was made is its disk's ctime, in the storage's content, which
PUT /api2/json/fake/disk-ctime?vmid=&ctime= sets for a fixture's VM made earlier.
The template's config has a SMBIOS UUID (smbios1), and each clone one of its own, so a VM made again at the same ID
has another, as Proxmox gives them: what the runs file a VM's SSH host key under.

A VM's configuration is written as qemu-server's PVE/API2/Qemu.pm writes it. A PUT or POST of it that sends digest is
refused, nothing written, unless that is the digest its GET gives now; one that sends none is taken, and digest is never
kept in the configuration. Before anything else, as Proxmox's schema does, a PUT or POST whose sshkeys is not still
percent-encoded whole once the form is decoded - unreserved characters and %XX alone, what today's proxmox_kvm sends
(quote(keys, safe="")) - is refused, nothing written. A POST's task checks the digest and the lock again when it ends,
as Proxmox's worker does under the VM's lock, and fails, writing nothing, where a write made since changed the one or
set the other. While a clone or a deletion runs, the VM's configuration holds lock ("clone", "destroyed"), and a write
to it is refused as locked; a deletion of a VM whose configuration holds one starts a task that fails so. A
description is kept as sent, but for its trailing white space, which Proxmox drops when it reads one back, and given
back by GET; a configuration write's is refused past a limit a fixture sets with PUT
/api2/json/fake/notes-limit?bytes=N, in bytes (none until then).
A clone takes description, over its source's and held to no limit, pool, which it joins when its task ends, target -
another node, where the source's disk is on a storage shared there - and storage, its disk's storage, one the node has;
it never lands on a VMID that is taken. One onto a VMID this node's VM holds is refused at the call; one onto a VMID a
container or another node's VM holds passes it, as Proxmox's check reads this node's files alone, and fails in its
task. Whether a clone takes its source's tags and description is PUT /api2/json/fake/clone-inherits?tags=&description=
(1, as the fake always had it, until then); a storage a fixture lists as <name>:<type>:shared is shared. A clone's
SMBIOS UUID is a new one, or the next PUT /api2/json/fake/clone-uuid?uuid= queued, each spent by one clone that passes
its call, so that a test knows the identity a VM it has not made yet will have. PUT /api2/json/fake/nextid-taken?vmid=
has the next GET of /cluster/nextid answer vmid, as Proxmox answers a VMID that another run's clone takes before this
one's lands, once. Each
refusal's status and words are the constants below the imports, for experiment 6 to correct (card A21).

Where Proxmox takes two calls and fails the loser in its worker, PUT /api2/json/fake/clone-race has the next clone onto
a VMID this node's VM holds, and the next configuration POST with a stale digest, each pass the call as the loser of a
race did, answer a UPID and fail in its task, changing nothing: the clone with the words its worker finds, the lock's
timeout while the winner's clone runs, else that the file exists; the POST with the digest's refusal. Each is armed
unless the fixture gives it 0 (clone=0, config=0, which also disarms one armed before), and each is spent once it has
changed an outcome.

Every call that answers a UPID makes a task of it, recorded by its UPID in <state_dir>/tasks.json - its type, the
token that made it, its VM - and what the call changes is changed when its task ends, but for a clone's VM, there
from the call and locked until then, and a deletion's, locked until it goes. A task ends at once, until a fixture has
those made after it run with PUT /api2/json/fake/task-polls?polls=N: then its status says running to its first N
polls and ends at the next, or when PUT /api2/json/fake/task-end?upid= ends it, or with no upid every running task,
as its worker ends whether or not anyone asks. It ends OK, its log "TASK OK"; or with the error PUT
/api2/json/fake/task-fail?error=&type= planted for the next task (of that type, where one is given), changing nothing
- a failed clone leaves no VM - its log "TASK ERROR: <error>"; or with the warnings PUT
/api2/json/fake/task-warn?warning=&type= planted for the next task (of that type, where one is given), one a line of
warning: a success, as Proxmox counts it, its change made, its exit status "WARNINGS: <n>" and its log each warning as
"WARN: <warning>", then "TASK WARNINGS: <n>", as Proxmox's worker logs them (TASK_WARNINGS). A UPID the fake never made
is answered as stopped and OK, with an empty log, as before.

A HEAD is answered 501, as pveproxy answers it, token or not. PUT /api2/json/fake/tokenless?answer=none has every
request with no token go unanswered from then on, the connection closed with nothing said: something answering in the
host's place, which can tell the probes just init sends with no token from the request that carries one. PUT
/api2/json/fake/redirect?location= has the next request that carries a token answered 302, to location, once: a
client that followed it would carry the token there.

GET /nodes/<node>/status answers a node's CPU threads, memory and uptime as Proxmox VE 9 does (NODE_STATS's figures,
the shape read from the API's schema and not yet from a real host), to a token with Sys.Audit on /nodes/<node>, which
PUT /api2/json/fake/privileges grants; to any other, 403, as Proxmox refuses it.

Usage: fake_pve_api.py <port> <certfile> <keyfile> <state_dir> <expected_token_secret>
"""

import hashlib
import importlib.util
import json
import re
import ssl
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

# What Proxmox VE 9 answers where it refuses what the fake refuses as it does, read from its source - qemu-server's
# PVE/API2/Qemu.pm, pve-guest-common's PVE/AbstractConfig.pm, pve-storage's PVE/Storage.pm - and not yet from a real
# host: each its own line, for experiment 6 to correct (card A21).
# A configuration write whose digest is not the configuration's: refused before anything is written.
DIGEST_REFUSED_STATUS = 500
DIGEST_REFUSED = "checksum mismatch (file change by other user?)"
# A configuration write to a VM whose configuration holds a lock, and the error of a deletion's task that finds one.
LOCKED_STATUS = 500
LOCKED = "VM is locked ({lock})"
# The lock a running clone leaves in its VM's configuration, and the one a running deletion does.
CLONE_LOCK = "clone"
DELETION_LOCK = "destroyed"
# A clone onto a VMID that is taken: a 500, as the fake always answered it. The call's check reads this node's
# qemu-server/ files alone, and its task reads them again under the new VM's lock, which says the same.
CLONE_EXISTS = "unable to create VM {newid}: config file already exists"
# Whether a clone onto a VMID that a container or another node's VM holds passes the call, which reads this node's
# qemu-server/ files alone, and fails in its task, where create_and_lock_config makes the VM's configuration; off, the
# call refuses it as one onto this node's VM.
CLONE_TAKEN_IN_TASK = True
# The task of a clone that lost a race for its VMID to one whose task still runs: it waits a second for the new VM's
# lock, which the winner's task holds until it ends (PVE::Tools::lock_file_full).
CLONE_LOCK_TIMEOUT = "can't lock file '/var/lock/qemu-server/lock-{newid}.conf' - got timeout"
# The task of a clone onto a VMID that a container (CT) or another node's VM holds: create_and_lock_config's words, from
# PVE::Cluster::check_vmid_unused. A draft: read from the source alone.
CLONE_TAKEN = "{kind} {newid} already exists on node '{node}'"
# What Proxmox takes off the end of a description when it reads one back (qemu-server's parse_vm_config,
# $descr =~ s/\s+$//), "" for nothing.
NOTES_TRAILING = r"\s+\Z"
# A description longer than the notes limit (PUT /api2/json/fake/notes-limit): a parameter the schema refuses, the
# words in the answer's errors under the parameter's name.
NOTES_REFUSED_STATUS = 400
NOTES_REFUSED = "value may only be {limit} characters long"
# A configuration write whose sshkeys, once the form is decoded, is not percent-encoded whole: a parameter the schema
# refuses (its format urlencoded, pve-common's pve_verify_urlencoded), its words in the answer's errors under the
# parameter's name, the value said. The fake takes what today's proxmox_kvm sends (quote(keys, safe="")), unreserved
# characters and %XX alone: stricter than Proxmox's format, which also takes !*'() and a bare %.
SSHKEYS_REFUSED_STATUS = 400
SSHKEYS_REFUSED = "invalid format - invalid urlencoded string: {value}"
URLENCODED = r"(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2})*"
# A clone's target or storage that will not do: each a 500.
NO_SUCH_NODE = "no such cluster node '{node}'"
NO_SUCH_STORAGE = "storage '{storage}' does not exist"
STORAGE_NOT_ON_NODE = "storage '{storage}' is not available on node '{node}'"
STORAGE_NOT_SHARED = "can't clone to non-shared storage '{storage}'"
DISK_NOT_SHARED = "can't clone VM to node '{node}' (VM uses local storage)"
# Under enforcement (PUT /api2/json/fake/enforce?on=1), a request of a token made through /access that lacks the
# privilege a row of the engine's table names on its path: raise_perm_exc's words (PVE/RPCEnvironment.pm), the path and
# the privilege.
PERMISSION_REFUSED_STATUS = 403
PERMISSION_REFUSED = "Permission check failed ({path}, {privilege})"
# Where either of two privileges would do, the privilege is each of them, "|" between them (check_any's words); where
# either of two paths would, as a clone's VM.Allocate on its pool's path or its new VM's, the words are these alone, as
# the or of two declared checks raises them.
PRIVILEGES_JOINED = "|"
PERMISSION_REFUSED_ANY = "Permission check failed"
# A call no row of the engine's table names, refused the same way: the fake's own words, since Proxmox keeps no table;
# and a configuration's key that no row of its call covers.
NO_ROW = "Permission check failed (no row of the engine's table names {method} {path})"
NO_KEY = "Permission check failed (no row of the engine's table covers the key {key} of {method} {path})"
# A GET row that neither its request's path nor its parameters place, on a route that lists no entry of its kind
# (LISTINGS): checked by nothing, so refused the same way, in the fake's own words.
NO_PLACE = "Permission check failed (nothing {method} {path} names or lists places the row of {privilege} on a {kind})"
# A clone a token made through /access sends into a pool that does not exist: check_pool_exist's words, a 403.
NO_SUCH_POOL = "Permission check failed (pool '{pool}' does not exist)"
# A value a parameter's enumeration does not hold, refused by Proxmox's schema before anything else (pve-common's
# PVE/JSONSchema.pm, check_prop): a 400, the words in the answer's errors under the parameter's name. The cluster's
# listing takes these types alone (pve-manager's PVE/API2/Cluster.pm): pool is a type the answer names, never one a
# request may ask for. Answered so to a token made through /access; today's tokens keep today's answer.
ENUM_REFUSED_STATUS = 400
ENUM_REFUSED = "value '{value}' does not have a value in the enumeration '{values}'"
RESOURCE_TYPES = ("vm", "storage", "node", "sdn")
# A configuration's write that sends no key and deletes none, digest aside: update_vm_api's words, a 500, before the
# digest is read. Answered so to a token made through /access; today's tokens keep today's answer.
NO_OPTIONS_STATUS = 500
NO_OPTIONS = "no options specified"
# A role made with a privilege Proxmox VE 9 does not define: add_role_privs' words, after create_role's label.
INVALID_PRIVILEGE = "invalid privilege '{privilege}'"
# A guest agent's call, refused with a 500 before the agent is asked, as qemu-server's assert_agent_available refuses
# it: the VM is not running; its agent does not answer yet.
AGENT_REFUSED_STATUS = 500
VM_NOT_RUNNING = "VM {vmid} is not running"
AGENT_NOT_RUNNING = "QEMU guest agent is not running"
# A guest agent's call to a VM whose configuration turns no agent on, refused before the VM's state is read.
NO_AGENT = "No QEMU guest agent configured"
# The exit status of a task whose worker logged warnings and went on, which Proxmox counts a success, and the last line
# of its log (PVE::RESTEnvironment's worker; PVE::Tools::upid_read_status reads the one from the other); each warning
# is a line of its own before it. Read from the source, for experiment 6 to confirm (card A21).
TASK_WARNINGS_STATUS = "WARNINGS: {count}"
TASK_WARNINGS = "TASK WARNINGS: {count}"
TASK_WARN = "WARN: {warning}"

PORT = int(sys.argv[1])
CERT, KEY = sys.argv[2], sys.argv[3]
STATE_DIR = Path(sys.argv[4])
TOKEN_SECRET = sys.argv[5]
TOKEN_RE = re.compile(r"^PVEAPIToken=([^!]+)!([^=]+)=(.*)$")
# Accepted everywhere but at the guest agent, like a token whose role lacks
# the guest-agent privilege.
NOAGENT_TOKEN_ID = "noagent"
NODE = "pve"
# Listed by /nodes and /cluster/resources, but every request to it fails the
# way pveproxy fails one it cannot forward to a node that is powered off.
DOWN_NODE = "pve2"
# Whether that node currently reports. Off, as above, until a fixture turns
# it on with PUT /api2/json/fake/nodes/pve2?status=online - the one thing
# the playbooks' "a node is down" paths cannot otherwise be shown to recover
# from. While it is on, its guests are listed with their names and as
# stopped, the way Proxmox lists guests on a node that has just come back.
NODES_ONLINE = {DOWN_NODE: False}
# What /cluster/resources?type=node says of an online node to a token with Sys.Audit there: 16 CPU threads, 64 GiB of
# memory of which 24 GiB in use, 12% busy.
NODE_STATS = {"maxcpu": 16, "maxmem": 64 * 1024**3, "mem": 24 * 1024**3, "cpu": 0.12, "uptime": 86400}
# Whether its guests are still listed with their names while it is down, as
# in the five minutes before the cluster drops its statistics: on with
# PUT /api2/json/fake/nodes/pve2?status=offline&named=1.
NODES_NAMED = {DOWN_NODE: False}

STATE_DIR.mkdir(parents=True, exist_ok=True)
VMS = {
    9000: {
        "vmid": 9000,
        "name": "ubuntu-24.04-cloudinit",
        "node": NODE,
        "status": "stopped",
        "template": 1,
        # As this version of roles/proxmox_template builds it with its defaults, as Proxmox VE 9.2 gives its config
        # back (read from a real one, 2026-10-30): deploy.yml rebuilds a template that differs, so one that did here
        # would send every deploy the scenarios run to the template play. test_filters.py holds it to the role.
        "config": {
            "name": "ubuntu-24.04-cloudinit",
            "cores": 2,
            "memory": 2048,
            "cpu": "host",
            "ostype": "l26",
            "scsihw": "virtio-scsi-pci",
            "agent": "enabled=1",
            "serial0": "socket",
            "vga": "serial0",
            "boot": "order=scsi0",
            "ipconfig0": "ip=dhcp",
            "scsi0": "local-lvm:base-9000-disk-0,discard=on,size=3584M",
            "ide2": "local-lvm:vm-9000-cloudinit,media=cdrom",
            "net0": "virtio=BC:24:11:00:23:28,bridge=vmbr0",
            # Every VM's own, as Proxmox gives one to each VM it creates: what a VM's SSH host key is filed under.
            "smbios1": "uuid=9a0e1c2d-3b4f-4a5b-8c6d-7e8f90a1b2c3",
            # As a 1.x build recorded it, on purpose: every scenario's deploy then holds that 2.0 rebuilds no template
            # for its record's 1.x name alone.
            "description": "Built by claude-on-proxmox: build=1;"
            " image=https://cloud-images.ubuntu.com/minimal/releases/noble/release/ubuntu-24.04-minimal-cloudimg-amd64.img;"
            " packages=qemu-guest-agent; sha256=0000000000000000000000000000000000000000000000000000000000000000",
            "template": 1,
            "meta": "creation-qemu=8.1.5,ctime=1760000000",
        },
    },
    # Two guests on the node that is down: a stranger, and one this project
    # created there. Below NEXTID_LOWER, so they never affect the numbering.
    8000: {
        "vmid": 8000,
        "name": "on-a-node-that-is-down",
        "node": DOWN_NODE,
        "status": "unknown",
        "template": 0,
        "config": {"name": "on-a-node-that-is-down"},
    },
    8001: {
        "vmid": 8001,
        "name": "ours-on-a-node-that-is-down",
        "node": DOWN_NODE,
        "status": "unknown",
        "template": 0,
        "config": {"name": "ours-on-a-node-that-is-down", "tags": "agent-warehouse"},
    },
    # A container carrying this project's tag, inside the fleet's numbering:
    # the provision scenario numbers alpha and beta from NEXTID_LOWER and must
    # step over it, and discovery, the listing and the destroy pre-check must
    # all leave it alone for being an lxc, tag or no tag.
    9003: {
        "vmid": 9003,
        "name": "ours-container",
        "node": NODE,
        "type": "lxc",
        "status": "running",
        "template": 0,
        "config": {"hostname": "ours-container", "tags": "agent-warehouse"},
    },
}
# /cluster/nextid answers with the lowest free VMID, as Proxmox does - not the
# highest in use plus one, which never collided with anything and so never
# exercised the fleet's "skip the VMIDs in use" step. Proxmox lets the
# datacenter raise the floor (Datacenter -> Options -> Next free VMID range);
# the fake's floor keeps the fixtures below it out of the numbering.
NEXTID_LOWER = 9001
TASKS = 0
# Each task a call made, by its UPID, as tasks.json in the state directory shows them: its type, the token that made
# it, its VM - the one its call names, a clone's source as Proxmox's UPID names it - whether it is running or stopped,
# how many more polls of its status it answers running to, and the error it ends with, "" for none. With each running
# task, what it does when it ends, given whether it ended OK, and for some what its worker finds when it ends: the error
# that fails it, "" for none.
TASK_RECORDS = {}
TASK_ENDS = {}
TASK_CHECKS = {}
# How many polls of its status a task made from now on answers running to: PUT /api2/json/fake/task-polls?polls=N.
TASK_POLLS = {"polls": 0}
# Errors planted for tasks to come, each ending the next task made - of its type, where it names one - once:
# PUT /api2/json/fake/task-fail?error=&type=
TASK_FAIL = []
# Warnings planted for tasks to come, each list the next task's - of its type, where it names one - once:
# PUT /api2/json/fake/task-warn?warning=&type=, one warning a line
TASK_WARN_NEXT = []
# Whether a clone takes its source's tags and its description: PUT /api2/json/fake/clone-inherits?tags=&description=
CLONE_INHERITS = {"tags": 1, "description": 1}
# The SMBIOS UUIDs the next clones take, in order, each spent once: PUT /api2/json/fake/clone-uuid?uuid=
CLONE_UUIDS = []
# The VMIDs the next GETs of /cluster/nextid answer, taken or not, each once: PUT /api2/json/fake/nextid-taken?vmid=
NEXTID_TAKEN = []
# Whether the next clone onto a VMID this node's VM holds, and the next configuration POST with a stale digest, each
# lost a race: its call's checks passed before the winner's change landed, and its task fails.
# PUT /api2/json/fake/clone-race?clone=&config=, each 1 unless given 0
RACE = {"clone": 0, "config": 0}
# The most bytes a VM's description may hold, 0 for no limit: PUT /api2/json/fake/notes-limit?bytes=N.
NOTES_LIMIT = {"bytes": 0}
# The guest agent is not up the moment a VM starts. Fail this many polls first
# so the role's wait loop is actually exercised. Counted per VM: with a single
# global counter the first VM absorbed every refusal and the second one's very
# first poll succeeded, so a fleet never exercised the loop more than once.
AGENT_CALLS = {}
AGENT_READY_AFTER = 2
# How many it fails now: AGENT_READY_AFTER until a fixture sets another.
AGENT_READY = {"after": AGENT_READY_AFTER}
# What a VM's agent reports of its network cards in place of agent_interfaces(), by VMID: the agent runs inside the VM,
# so its root chooses what it says. PUT /api2/json/fake/agent-answer?vmid=&result=<JSON>, and with no result, as before.
AGENT_ANSWER = {}
# The listing's lag (see the docstring): how many listings after a status
# change still report the old status, and per VM, that status and how many
# listings it has left.
LISTING_LAG = {"listings": 0}
# Requests to fail, once each and in the order given: the next write, or request of methods (GET,PUT), to a path ending
# with suffix. PUT /api2/json/fake/refuse?suffix=&methods=
REFUSE_NEXT = []
# VMs another run deletes once this one has listed them: gone from the next listing on, so that a delete finds nothing.
# PUT /api2/json/fake/vanish?vmid=
VANISH = {}
STALE = {}
# Guests the listings give with no name or template key (see the docstring), by VMID. PUT /api2/json/fake/unnamed?vmid=
UNNAMED = set()
# What /version reports, and each token's privileges by path where a fixture
# set them: {(token id, path): [privilege, ...]}.
VERSION = {"version": "8.2.4"}
TOKENLESS = {"answer": "401"}
# Where the next requests that carry a token are redirected, one each: PUT /api2/json/fake/redirect?location=
REDIRECT_NEXT = []
PRIVILEGES = {}
# Tokens granted the role on one pool alone, by token id: /cluster/resources lists that pool's members only.
POOL_SCOPE = {}
# The pools POST /pools made, by pool ID: each one's info - its comment, where it was given one - and its storages. A
# guest's pool is its own "pool", as before, so a pool a guest is a member of exists made or not: pool_names() is every
# pool.
POOLS = {}
# What /access made, as Proxmox keeps it in user.cfg: each user's expire; each ACL entry, (path, "user" or "token", its
# ID, the role) to its propagate flag; each token by its full ID, user@realm!tokenid, with its secret (value), privsep,
# expire and comment. Each role's privileges are ROLES, below.
USERS = {}
ACL = {}
TOKENS = {}


def stand_in():
    """The stand-in pveum beside this file (fake_pve_cli.py), whose lists of what Proxmox VE defines this fake reads
    too; None for a copy of this file alone, as the Molecule scenarios install it, where nothing asks /access for
    them."""
    path = Path(__file__).with_name("fake_pve_cli.py")
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("fake_pve_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLI = stand_in()
# The privileges Proxmox VE 9 defines, as the stand-in knows them: a role takes these alone.
DEFINED = frozenset({*CLI.COMMON, *CLI.BY_MAJOR[9]}) if CLI else frozenset()
# The roles Proxmox has from the start, which no POST /access/roles makes again: NoAccess, with no privileges, and the
# stand-in's own (PVEAuditor). With them, each role /access made.
ROLES = {"NoAccess": set(), **{name: set(privs) for name, privs in (CLI.DEFAULT["roles"] if CLI else {}).items()}}
# Whether a made token's requests are held to the engine's table (PUT /api2/json/fake/enforce?on=1), and the table:
# lib/claude_vm/pve/privileges.py, read from the checkout's lib/ beside this file when enforcement is first turned on.
ENFORCE = {"on": 0}
LIB = Path(__file__).resolve().parents[2] / "lib"
TABLE = []
# Under enforcement, each listing of the engine's table by its route, and the kinds of entry it shows only where the
# token holds a row's privilege on them (Handler._sees): a GET row whose kind its request's path and parameters do not
# name is checked so, and only where its route lists that kind, or it is checked by nothing. The cluster's listing names
# a guest's pool only where its pool's row is held (PVE/API2/Cluster.pm); a node's storages and bridges (pve-storage's
# PVE/API2/Storage/Status.pm, pve-manager's PVE/API2/Network.pm); the pools, read with no poolid (PVE/API2/Pool.pm).
LISTINGS = {
    "/cluster/resources": ("vm", "storage", "node", "pool"),
    "/nodes/{node}/storage": ("storage",),
    "/nodes/{node}/network": ("bridge",),
    "/pools": ("pool",),
}


def table():
    """The engine's table of privileges, the one place this fake learns what a call needs."""
    if not TABLE:
        sys.path.insert(0, str(LIB))
        from claude_vm.pve import privileges

        TABLE.append(privileges)
    return TABLE[0]


# GET /access/permissions without a path, for a token made through /access: these paths, every path of an ACL entry and
# its parents, and every pool member's, each where the token holds something (PVE/RPCEnvironment.pm).
TOP_PATHS = ("/", "/access", "/access/groups", "/nodes", "/pool", "/sdn", "/storage", "/vms")
# Each node's storages for VM disks, with their type and free bytes, and its bridges.
STORAGES = {NODE: [{"storage": "local-lvm", "type": "lvmthin", "avail": 412 * 1024**3}]}
BRIDGES = {NODE: ["vmbr0"]}
# Each storage's size, for its fill in /cluster/resources?type=storage: used is
# the size less what STORAGES says is free.
STORAGE_SIZE = 465 * 1024**3
# Every VM's root disk use, as the agent's get-fsinfo reports it.
ROOT_USED = 11 * 1024**3
ROOT_TOTAL = 31 * 1024**3
ROLE = [
    "VM.Allocate",
    "VM.Clone",
    "VM.Config.CPU",
    "VM.Config.Cloudinit",
    "VM.Config.Disk",
    "VM.Config.Memory",
    "VM.Config.Network",
    "VM.Config.Options",
    "VM.PowerMgmt",
    "VM.Audit",
    "Datastore.AllocateSpace",
    "Datastore.Audit",
    "SDN.Use",
]


def privileges(token_id, path):
    """The token's privileges at path, each with its propagate flag: the fixture's, else the whole role.

    The role's guest-agent privilege is this version's, and the noagent token's
    role lacks it. A fixture's "VM.Clone:0" is granted without Propagate.
    """
    if (token_id, path) in PRIVILEGES:
        listed = PRIVILEGES[(token_id, path)]
    else:
        agent = "VM.GuestAgent.Audit" if int(VERSION["version"].split(".")[0]) >= 9 else "VM.Monitor"
        listed = ROLE + ([] if token_id == NOAGENT_TOKEN_ID else [agent])
        # On a pool, the role as tools/proxmox-access.sh --pool grants it there: Pool.Audit too.
        if path.startswith("/pool/"):
            listed = [*listed, "Pool.Audit"]
    granted = {}
    for entry in listed:
        name, _, flag = entry.partition(":")
        granted[name] = int(flag or "1")
    return granted


def split_list(raw):
    """A list parameter as Proxmox splits one (PVE::Tools::split_list): on commas, semicolons and white space."""
    return [item for item in re.split(r"[,;\s]+", raw or "") if item]


def truthy(raw):
    """A boolean parameter as Proxmox reads one."""
    return str(raw).lower() in ("1", "true", "yes", "on")


def acl_path(raw):
    """An ACL path as Proxmox keeps it: one / between its parts, none at the end."""
    return "/" + "/".join(part for part in raw.split("/") if part)


def pool_names():
    """Every pool: those POST /pools made, and those a guest is a member of."""
    return set(POOLS) | {vm["pool"] for vm in VMS.values() if vm.get("pool")}


def acl_roles(kind, who, path):
    """The roles who holds at path from the ACL entries, each with its propagate flag, as Proxmox reads them
    (PVE::AccessControl::roles): from / down to path, the entries naming who at the deepest level that has any win, one
    above path only where it propagates; where NoAccess is among them, NoAccess alone. A token's are its "token"
    entries, a user's its "user" entries."""
    names = [part for part in path.split("/") if part]
    held = {}
    for depth in range(len(names) + 1):
        here, final = "/" + "/".join(names[:depth]), depth == len(names)
        found = {
            role: flag for (at, k, w, role), flag in ACL.items() if (at, k, w) == (here, kind, who) and (final or flag)
        }
        if found:
            held = found
    return {"NoAccess": held["NoAccess"]} if "NoAccess" in held else held


def roles_at(kind, who, path):
    """acl_roles, and where path is a pool member's - a guest's /vms/<id>, a storage's /storage/<id> in each pool that
    holds it - each role held on its pools that path's own lack, without propagate and whatever the pool's entry says;
    but a NoAccess of the path's own is never trumped by a pool's, and a pool's NoAccess leaves NoAccess alone
    (PVE::RPCEnvironment, compile_acl_path)."""
    held = dict(acl_roles(kind, who, path))
    guest = re.fullmatch(r"/vms/(\d+)", path)
    storage = re.fullmatch(r"/storage/([^/]+)", path)
    pools = []
    if guest and VMS.get(int(guest[1]), {}).get("pool"):
        pools = [VMS[int(guest[1])]["pool"]]
    elif storage:
        pools = [name for name, pool in POOLS.items() if storage[1] in pool["storage"]]
    pooled = {}
    for pool in pools:
        pooled.update(acl_roles(kind, who, f"/pool/{pool}"))
    if not pooled or "NoAccess" in held:
        return held
    if "NoAccess" in pooled:
        return {"NoAccess": 0}
    for role in pooled:
        held.setdefault(role, 0)
    return held


def role_privileges(roles):
    """The privileges of roles, each propagating where a role that grants it does."""
    granted = {}
    for role, flag in roles.items():
        for name in ROLES.get(role, ()):
            granted[name] = granted.get(name, 0) or flag
    return granted


def acl_privileges(full_id, path):
    """What a token made through /access holds at path, each privilege with its propagate flag, as Proxmox computes it
    (PVE::RPCEnvironment::permissions): with privsep=0 its user's; with privsep=1 its own entries', cut to what its
    user holds there, propagating only where both do."""
    user = role_privileges(roles_at("user", full_id.split("!", 1)[0], path))
    if not TOKENS[full_id]["privsep"]:
        return user
    own = role_privileges(roles_at("token", full_id, path))
    return {name: int(bool(flag and user[name])) for name, flag in own.items() if name in user}


def acl_permissions(full_id, path=None):
    """GET /access/permissions for a token made through /access: the path asked for, a key even where nothing is held
    there, as Proxmox answers; or each path of TOP_PATHS, of the ACL entries and their parents and of the pools' members
    where the token holds something."""
    if path is not None:
        return {path: acl_privileges(full_id, acl_path(path))}
    paths = set(TOP_PATHS)
    for at, *_ in ACL:
        names = [part for part in at.split("/") if part]
        paths |= {"/" + "/".join(names[:depth]) for depth in range(len(names) + 1)}
    paths |= {f"/vms/{vm['vmid']}" for vm in VMS.values() if vm.get("pool")}
    paths |= {f"/storage/{storage}" for pool in POOLS.values() for storage in pool["storage"]}
    held = {path: acl_privileges(full_id, path) for path in sorted(paths)}
    return {path: privs for path, privs in held.items() if privs}


def token_info(token):
    """A token's info, as Proxmox answers it."""
    return {"privsep": token["privsep"], "expire": token["expire"], **{k: token[k] for k in ("comment",) if k in token}}


def normalise_tags(raw):
    """Store tags the way Proxmox does.

    proxmox_kvm sends them comma-joined, but PVE stores and returns them
    ";"-joined, lowercased and deduplicated, in alphabetical order. Echoing
    the request back verbatim hid that from every test.
    """
    parts = [t.strip().lower() for t in raw.replace(",", ";").split(";")]
    return ";".join(sorted({t for t in parts if t}))


def mac_for(vmid):
    return f"BC:24:11:{vmid // 65536 % 256:02X}:{vmid // 256 % 256:02X}:{vmid % 256:02X}"


def agent_interfaces(vm):
    """What network-get-interfaces reports: loopback, a docker bridge the role
    must ignore, and the VM's own NIC with its DHCP address."""
    vmid = vm["vmid"]
    return [
        {
            "name": "lo",
            "hardware-address": "00:00:00:00:00:00",
            "ip-addresses": [{"ip-address": "127.0.0.1", "ip-address-type": "ipv4", "prefix": 8}],
        },
        {
            "name": "docker0",
            "hardware-address": "02:42:9A:11:22:33",
            "ip-addresses": [{"ip-address": "172.17.0.1", "ip-address-type": "ipv4", "prefix": 16}],
        },
        {
            "name": "eth0",
            "hardware-address": mac_for(vmid).lower(),
            "ip-addresses": [
                {"ip-address": f"192.0.2.{vmid % 200 + 50}", "ip-address-type": "ipv4", "prefix": 24},
                {"ip-address": "fe80::1", "ip-address-type": "ipv6", "prefix": 64},
            ],
        },
    ]


def guest_type(vm):
    return vm.get("type", "qemu")


UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def disk_bytes(config):
    """scsi0's size=, in bytes, as Proxmox reports maxdisk; 0 when there is none."""
    for option in str(config.get("scsi0", "")).split(","):
        if option.startswith("size=") and option[-1:] in UNITS:
            return int(float(option[5:-1]) * UNITS[option[-1]])
    return 0


def version_9():
    return int(VERSION["version"].split(".")[0]) >= 9


def live(vm):
    """A running VM's CPU, memory and uptime, the same each time it is asked; nothing for one that is not."""
    vmid, maxmem = vm["vmid"], int(vm["config"].get("memory", 512)) * 1024**2
    if vm["status"] != "running":
        return {"cpu": 0, "mem": 0, "uptime": 0}
    stats = {"cpu": round((vmid % 7 + 1) / 50, 4), "mem": maxmem * 3 // 8, "uptime": 3600 * (vmid % 50 + 1)}
    if version_9():
        stats["memhost"] = maxmem // 2
    return stats


def resource(vm):
    entry = {
        "vmid": vm["vmid"],
        "node": vm["node"],
        "type": guest_type(vm),
        "status": vm["status"],
        "id": f"{guest_type(vm)}/{vm['vmid']}",
        # What a guest is given, as Proxmox lists it: cores, and bytes.
        "maxcpu": int(vm["config"].get("cores", 1)),
        "maxmem": int(vm["config"].get("memory", 512)) * 1024**2,
        "maxdisk": disk_bytes(vm["config"]),
        "disk": 0,
        **live(vm),
    }
    # Proxmox fills name, template and the real status from the node's
    # statistics, which the cluster drops five minutes after a node stops
    # reporting. A guest on a node that is down is therefore listed with
    # neither, and consumers that index `name` without a default fall over.
    if (vm["node"] != DOWN_NODE or NODES_ONLINE[DOWN_NODE] or NODES_NAMED[DOWN_NODE]) and vm["vmid"] not in UNNAMED:
        entry["name"] = vm["name"]
        entry["template"] = vm["template"]
    if vm["node"] == DOWN_NODE and NODES_ONLINE[DOWN_NODE]:
        entry["status"] = "stopped"
    # PVE leaves the key out for a guest with no tags, rather than sending "".
    # Always sending it hid that anything filtering on tags has to allow for
    # a VM without the attribute at all.
    if vm["config"].get("tags"):
        entry["tags"] = vm["config"]["tags"]
    return entry


def lagged(entries):
    """entries, a listing's, each guest's status as the listing gives it while the lag is on (see the docstring): the
    status from before its last start or stop, for as many listings, of the cluster or of a pool, as the lag said."""
    for entry in entries:
        stale = STALE.get(entry.get("vmid"))
        if stale:
            entry["status"] = stale[0]
            stale[1] -= 1
            if stale[1] <= 0:
                del STALE[entry["vmid"]]
    return entries


def resource_for(vm, pooled):
    """resource(vm) as the token sees it: as Proxmox (PVE/API2/Cluster.pm), the pool a guest is a member of only where
    the token may audit that pool (pooled), and no key for one that is in none."""
    entry = resource(vm)
    if vm.get("pool") and pooled:
        entry["pool"] = vm["pool"]
    return entry


def storage_resource(node, entry):
    """A storage of node's, as /cluster/resources?type=storage lists it, with its fill."""
    return {
        "id": f"storage/{node}/{entry['storage']}",
        "type": "storage",
        "storage": entry["storage"],
        "node": node,
        "plugintype": entry["type"],
        "maxdisk": STORAGE_SIZE,
        "disk": STORAGE_SIZE - entry["avail"],
        "status": "available" if entry.get("active", 1) else "unknown",
        "shared": entry.get("shared", 0),
        "content": "images,rootdir",
    }


def node_status():
    """GET /nodes/<node>/status, as Proxmox VE 9 answers it (the shape read from the API's schema, a draft until a real
    host corrects it): NODE_STATS's threads, memory and uptime, the rest of its keys made up."""
    total, used = NODE_STATS["maxmem"], NODE_STATS["mem"]
    return {
        "uptime": NODE_STATS["uptime"],
        "cpu": NODE_STATS["cpu"],
        "wait": 0,
        "loadavg": ["0.50", "0.40", "0.30"],
        "cpuinfo": {"cpus": NODE_STATS["maxcpu"], "cores": NODE_STATS["maxcpu"] // 2, "sockets": 1, "hvm": "1"},
        "memory": {"total": total, "used": used, "free": total - used, "available": total - used},
        "pveversion": f"pve-manager/{VERSION['version']}/fake",
    }


def pool_members(pool, kind=None):
    """A pool's members, as GET /pools?poolid= lists them: its guests by VMID, as /cluster/resources lists each, then
    its storages the cluster has, on this node where it has one; kind, qemu, lxc or storage, keeps that kind alone."""
    guests = sorted((vm for vm in VMS.values() if vm.get("pool") == pool), key=lambda vm: vm["vmid"])
    members = [resource(vm) for vm in guests if kind in (None, guest_type(vm))]
    storages = sorted(POOLS.get(pool, {}).get("storage", ())) if kind in (None, "storage") else []
    for storage in storages:
        held = [(node, e) for node, entries in STORAGES.items() for e in entries if e["storage"] == storage]
        held.sort(key=lambda found: found[0] != NODE)
        members += [storage_resource(*held[0])] if held else []
    return members


def save_state():
    STATE_DIR.joinpath("state.json").write_text(json.dumps(VMS, indent=2, sort_keys=True))


def save_tasks():
    STATE_DIR.joinpath("tasks.json").write_text(json.dumps(TASK_RECORDS, indent=2, sort_keys=True))


def new_task(kind, vmid, owner, end=None, error=None, check=None):
    """A task of kind for VM vmid, made by the token owner, its UPID as the fake always made them: it runs for the polls
    TASK_POLLS says, then ends with error, or the one planted for it, or what check() finds when it ends, or OK; end(ok)
    is what it does when it ends."""
    global TASKS
    TASKS += 1
    upid = f"UPID:{NODE}:0000{TASKS:04X}:00000000:00000000:qmtask:{TASKS}:root@pam!ansible:"
    if error is None:
        planted = next((i for i, (only, _) in enumerate(TASK_FAIL) if only in ("", kind)), None)
        error = "" if planted is None else TASK_FAIL.pop(planted)[1]
    warned = next((i for i, (only, _) in enumerate(TASK_WARN_NEXT) if only in ("", kind)), None)
    warnings = [] if warned is None else TASK_WARN_NEXT.pop(warned)[1]
    TASK_RECORDS[upid] = {
        "type": kind,
        "owner": owner,
        "vmid": vmid,
        "status": "running",
        "polls": TASK_POLLS["polls"],
        "error": error,
        # Only where some were planted, so that a record is what it always was otherwise.
        **({"warnings": warnings} if warnings else {}),
    }
    TASK_ENDS[upid] = end
    TASK_CHECKS[upid] = check
    if not TASK_POLLS["polls"]:
        end_task(upid)
    save_tasks()
    return upid


def end_task(upid):
    """The task stops, failing with what its worker finds where nothing failed it before, and does what it does when it
    ends."""
    record = TASK_RECORDS[upid]
    record["status"] = "stopped"
    check = TASK_CHECKS.pop(upid, None)
    if check and not record["error"]:
        record["error"] = check()
    end = TASK_ENDS.pop(upid, None)
    if end:
        end(not record["error"])
    save_tasks()


def task_lines(record):
    """A task's log, as Proxmox's ends one: nothing yet while it runs, then each warning its worker logged, and TASK
    OK, or TASK ERROR and its error, or TASK WARNINGS and how many where there were some."""
    if record["status"] == "running":
        return []
    warnings = record.get("warnings", [])
    said = [TASK_WARN.format(warning=warning) for warning in warnings]
    if record["error"]:
        return [*said, f"TASK ERROR: {record['error']}"]
    return [*said, TASK_WARNINGS.format(count=len(warnings)) if warnings else "TASK OK"]


def task_exitstatus(record):
    """How a stopped task ended, as its status says: its error, WARNINGS and how many, or OK."""
    if record["error"]:
        return record["error"]
    warnings = record.get("warnings", [])
    return TASK_WARNINGS_STATUS.format(count=len(warnings)) if warnings else "OK"


def agent_on(config):
    """Whether a VM's configuration turns its guest agent on: its agent option enabled, written as Proxmox takes it
    ([enabled=]1, then its other keys); off where it has none, as Proxmox's default is."""
    for part in str(config.get("agent", "")).split(","):
        key, sep, value = part.partition("=")
        if (value if sep else key).strip().lower() in ("1", "on", "yes", "true") and (not sep or key == "enabled"):
            return True
    return False


def config_digest(config):
    """The digest of a VM's configuration, which changes with any change to it."""
    return hashlib.sha1(json.dumps(config, sort_keys=True).encode()).hexdigest()


def notes_refusal(params):
    """NOTES_REFUSED, where a description sent to a VM's configuration is longer than the limit a fixture set; else "".
    A clone's description is not held to it: clone_vm's own parameter has no limit."""
    limit = NOTES_LIMIT["bytes"]
    if limit and len(params.get("description", "").encode()) > limit:
        return NOTES_REFUSED.format(limit=limit)
    return ""


def notes_kept(description):
    """A description as Proxmox reads it back, NOTES_TRAILING taken off its end."""
    return re.sub(NOTES_TRAILING, "", description) if NOTES_TRAILING else description


def shared(node, storage):
    """Whether node has storage, and it is shared."""
    return any(entry["storage"] == storage and entry.get("shared") for entry in STORAGES.get(node, []))


def clone_node(node, params):
    """The node a clone made on node lands on: its target, where it names another."""
    target = params.get("target") or node
    return node if target == "localhost" else target


def clone_refusal(vm, node, params):
    """Why Proxmox refuses a clone of vm, on node, its target or its storage (PVE/API2/Qemu.pm's clone_vm, and
    PVE/Storage.pm's storage_check_enabled); "" when it takes them."""
    target, storage = clone_node(node, params), params.get("storage")
    if target not in {NODE, DOWN_NODE, *NODES_ONLINE}:
        return NO_SUCH_NODE.format(node=target)
    if storage:
        holding = {held for held, entries in STORAGES.items() for entry in entries if entry["storage"] == storage}
        if not holding:
            return NO_SUCH_STORAGE.format(storage=storage)
        if target != node and target not in holding:
            return STORAGE_NOT_ON_NODE.format(storage=storage, node=target)
        if target != node and not shared(target, storage):
            return STORAGE_NOT_SHARED.format(storage=storage)
    elif target != node and not shared(node, str(vm["config"].get("scsi0", "")).split(":", 1)[0]):
        return DISK_NOT_SHARED.format(node=target)
    return ""


def on_storage(volume, storage):
    """A disk's volume, moved to storage where one is named."""
    return f"{storage}:{volume.split(':', 1)[1]}" if storage and ":" in volume else volume


class Handler(BaseHTTPRequestHandler):
    def _params(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        query = urlparse(self.path).query
        params = {k: v[0] for k, v in parse_qs(body).items()}
        params.update({k: v[0] for k, v in parse_qs(query).items()})
        # The form as Proxmox reads it, a key sent with an empty value kept: what a made token's write is held by
        # (_keys, NO_OPTIONS). Every other answer reads params, whose blank values are dropped, as it always did.
        self.form = {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}
        self.form.update({k: v[0] for k, v in parse_qs(query, keep_blank_values=True).items()})
        return params

    def _reply_empty(self, status, reason):
        self.send_response(status, reason)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _reply(self, status, data=None, reason=None, errors=None):
        # PVE puts the reason for a failure in the HTTP reason phrase, and
        # proxmoxer copies it into the error message ("500 Internal Server
        # Error: VM 4013 is not running"). Callers pattern-match that message,
        # so the fake has to say what Proxmox says. A parameter it refuses is
        # named in the answer's errors, with why.
        payload = json.dumps({"data": data, **({"errors": errors} if errors else {})}).encode()
        self.send_response(status, reason)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _refused(self, label, why):
        """Proxmox's refusal of a write: a 500, its sentence after the label the API gives it."""
        return self._reply(500, None, f"{label}: {why}")

    def _notes_refused(self, why):
        """A description past the notes limit, refused as Proxmox's schema refuses a parameter."""
        return self._reply(NOTES_REFUSED_STATUS, None, "Parameter verification failed.", {"description": why})

    def _sshkeys_refused(self, value):
        """An sshkeys that is not percent-encoded whole, refused as Proxmox's schema refuses a parameter."""
        why = SSHKEYS_REFUSED.format(value=value)
        return self._reply(SSHKEYS_REFUSED_STATUS, None, "Parameter verification failed.", {"sshkeys": why})

    def _task(self, kind, vmid, change, check=None):
        """A task of kind for vmid, made by the token of this request, that makes change when it ends OK: when check()
        finds nothing to fail it, where there is one."""
        return new_task(kind, vmid, self.owner, lambda ok: ok and change(), check=check)

    def _refusal(self, method, parts, params):
        """Under enforcement, why the engine's table refuses this request of a made token; "" where it may go on. Each
        row of its call is held on the ACL paths its path names, or its parameters, by acl_privileges, which answers GET
        /access/permissions too: any of its privileges held on any of its paths lets it through. A GET row of neither is
        a listing's, where its route lists that kind (LISTINGS), whose entries are each shown only where it is held
        (self.entries, _sees); one whose route lists no such kind is checked by nothing, and refused. A row with keys,
        of a configuration's write, is held only where the request sends or deletes one of them, a key sent with an
        empty value among them, and a key no row of the call covers is refused. A task's owner needs nothing to read it.
        A call no row names, a key, or a row nothing places, is refused, and recorded in unmatched.log for
        harness.fake_fleet(enforce=True) to fail on. Each row the request is decided by - held on a path, read by a
        listing (_sees), or needing nothing - is recorded in decided.log (_decided)."""
        path = "/" + "/".join(parts)
        rows = table().for_request(method, path)
        if not rows:
            self._unmatched({"method": method, "path": path})
            return NO_ROW.format(method=method, path=path)
        sent = self._keys(self.form) if any(call.keys for call, _ in rows) else set()
        uncovered = sorted(sent - {key for call, _ in rows for key in call.keys})
        if uncovered:
            self._unmatched({"method": method, "path": path, "key": uncovered[0]})
            return NO_KEY.format(key=uncovered[0], method=method, path=path)
        task = TASK_RECORDS.get(parts[3], {}) if parts[2:3] == ["tasks"] and len(parts) > 3 else {}
        for call, at in rows:
            row = table().CALLS.index(call)
            if not call.privileges:
                self._decided(row, "nothing")
                continue
            if task.get("owner") == self.owner:
                continue
            if call.keys and not sent & set(call.keys):
                continue
            named = [one or self._named(kind, parts, params) for kind, one in zip(call.kinds, at, strict=True)]
            named = [one for one in named if one]
            said = PRIVILEGES_JOINED.join(call.privileges)
            if not named and method == "GET":
                if call.kinds[0] in LISTINGS.get(call.route, ()):
                    self.entries[call.kinds[0]] = (row, call.privileges)
                    continue
                self._unmatched({"method": method, "path": path, "row": row})
                return NO_PLACE.format(method=method, path=path, privilege=said, kind=call.kinds[0])
            if not named:
                return PERMISSION_REFUSED.format(path=call.kinds[0], privilege=said)
            self._decided(row, "path")
            if not any(set(call.privileges) & set(acl_privileges(self.owner, one)) for one in named):
                if len(call.kinds) > 1:
                    return PERMISSION_REFUSED_ANY
                return PERMISSION_REFUSED.format(path=named[0], privilege=said)
        return ""

    @staticmethod
    def _unmatched(entry):
        """A request of a made token the engine's table has no row for, or a key, or a row nothing places, recorded for
        harness.fake_fleet."""
        with STATE_DIR.joinpath("unmatched.log").open("a") as log:
            log.write(json.dumps(entry) + "\n")

    def _decided(self, row, by):
        """That this request of a made token was decided by the table's row at index row of CALLS, by: held on a path
        ("path"), read by a listing for its entries ("listing"), or needing nothing ("nothing"). Once a request each,
        in decided.log, for the test that every row is one the fake decides a request the client sends by."""
        if (row, by) in self.decided:
            return
        self.decided.add((row, by))
        entry = {"row": row, "by": by, "method": self.command, "path": self.routed}
        with STATE_DIR.joinpath("decided.log").open("a") as log:
            log.write(json.dumps(entry) + "\n")

    @staticmethod
    def _keys(form):
        """The configuration's keys a write sends or deletes, as Proxmox checks them: every key of the form but digest
        and delete, one sent with an empty value too, and each key delete names (qemu-server's update_vm_api, which
        checks every key of %$param)."""
        return {key for key in form if key not in ("digest", "delete")} | set(split_list(form.get("delete")))

    def _no_options(self):
        """Whether a configuration's write of a made token sends no key and deletes none, digest aside, which
        update_vm_api refuses (NO_OPTIONS) whatever the token holds: an empty delete is none."""
        return self.made and not set(self.form) - {"digest", "delete"} and not self.form.get("delete")

    @staticmethod
    def _named(kind, parts, params):
        """The ACL path a call names in its parameters, or its VM's configuration does, for a row of kind that its path
        does not name: a pool by pool or poolid, a clone's new VM by newid, a storage by storage, and with no storage a
        clone's on its source's disk's (qemu-server's clone_vm, check_storage_access_clone) and a resize's on the
        storage of the disk it grows (resize_vm). None for a listing, and for a pool a clone does not name: the new VM's
        path is then the clone's one way through the or of its declared check."""
        vmid = int(parts[3]) if len(parts) > 3 and parts[2] == "qemu" and parts[3].isdigit() else None
        clone = vmid is not None and parts[4:] == ["clone"]
        resize = vmid is not None and parts[4:] == ["resize"]
        if kind == "pool" and (params.get("pool") or params.get("poolid")):
            return f"/pool/{params.get('pool') or params.get('poolid')}"
        if kind == "new" and clone and params.get("newid"):
            return f"/vms/{params['newid']}"
        if kind == "storage" and params.get("storage"):
            return f"/storage/{params['storage']}"
        if kind == "storage" and (clone or resize) and vmid in VMS:
            disk = "scsi0" if clone else params.get("disk", "")
            return "/storage/" + str(VMS[vmid]["config"].get(disk, "")).split(":", 1)[0]
        return None

    def _sees(self, kind, path, today):
        """Whether this request's token sees the entry of kind at path in a listing: under enforcement, a made token
        where it holds any of the privileges its call's row names on that kind, the row recorded as one the request was
        decided by, and nowhere where no row of its call names that kind; any other, today's answer, today."""
        if not self.enforced:
            return today
        if kind not in self.entries:
            return False
        row, held = self.entries[kind]
        self._decided(row, "listing")
        return bool(set(held) & set(acl_privileges(self.owner, path)))

    def _pooled(self, vm, token_id):
        """Whether the cluster's listing names vm's pool to this request's token: where it may audit that pool."""
        if not vm.get("pool"):
            return False
        at = f"/pool/{vm['pool']}"
        return self._sees("pool", at, "Pool.Audit" in privileges(token_id, at))

    def _pools(self, method, poolid, params, token, deprecated):
        """/pools, and the deprecated /pools/<poolid>, as pve-manager's PVE/API2/Pool.pm answers them."""
        if method == "GET" and not poolid:
            # The pools the token may read, each with its comment.
            audited = [
                name
                for name in sorted(pool_names())
                if self._sees("pool", f"/pool/{name}", "Pool.Audit" in privileges(token.group(2), f"/pool/{name}"))
            ]
            return self._reply(200, [{"poolid": name, **POOLS.get(name, {}).get("info", {})} for name in audited])
        if not poolid:
            return self._reply(400, None, "Parameter verification failed.")
        if method == "GET":
            if poolid not in pool_names():
                return self._reply(500, None, f"pool '{poolid}' does not exist")
            info = POOLS.get(poolid, {}).get("info", {})
            listed = {"poolid": poolid, **info, "members": lagged(pool_members(poolid, params.get("type")))}
            # The deprecated form answers the one pool, not a list of it.
            return self._reply(200, listed if deprecated else [listed])
        if method == "POST":
            if poolid in pool_names():
                return self._refused("create pool failed", f"pool '{poolid}' already exists")
            if not re.match(r"[A-Za-z]", poolid):
                return self._refused("create pool failed", "pool name must start with a letter")
            POOLS[poolid] = {"info": {"comment": params["comment"]} if params.get("comment") else {}, "storage": set()}
            return self._reply(200, None)
        if method == "PUT":
            return self._pool_update(poolid, params)
        if method == "DELETE":
            if poolid not in pool_names():
                return self._refused("delete pool failed", f"pool '{poolid}' does not exist")
            guests = sorted(vm["vmid"] for vm in VMS.values() if vm.get("pool") == poolid)
            if guests:
                return self._refused("delete pool failed", f"pool '{poolid}' is not empty (contains VM {guests[0]})")
            held = {e["storage"] for entries in STORAGES.values() for e in entries}
            stored = sorted(POOLS[poolid]["storage"] & held)
            if stored:
                return self._refused(
                    "delete pool failed", f"pool '{poolid}' is not empty (contains storage '{stored[0]}')"
                )
            del POOLS[poolid]
            for entry in [entry for entry in ACL if entry[0] == f"/pool/{poolid}"]:
                del ACL[entry]
            return self._reply(200, None)
        return self._reply(501, None)

    def _pool_update(self, poolid, params):
        """PUT /pools: a comment, guests and storages added or taken away, every one checked before any is written."""
        label = "update pools failed"
        if poolid not in pool_names():
            return self._refused(label, f"pool '{poolid}' does not exist")
        pool = POOLS.setdefault(poolid, {"info": {}, "storage": set()})
        delete, move = truthy(params.get("delete")), truthy(params.get("allow-move"))
        moved, storages = {}, set(pool["storage"])
        for word in split_list(params.get("vms")):
            if not word.isdigit():
                return self._reply(400, None, "Parameter verification failed.")
            vmid = int(word)
            if vmid not in VMS:
                # The fake's own refusal: it keeps a guest's pool on the guest, so a VMID with no guest has nowhere.
                return self._refused(label, f"VM {vmid} does not exist")
            member = moved.get(vmid, VMS[vmid].get("pool"))
            if delete and member != poolid:
                return self._refused(label, f"VM {vmid} is not a pool member")
            if not delete and member == poolid:
                return self._refused(label, f"VM {vmid} is already a pool member")
            if not delete and member and not move:
                return self._refused(label, f"VM {vmid} belongs already to pool '{member}' and 'allow-move' is not set")
            moved[vmid] = None if delete else poolid
        for storage in split_list(params.get("storage")):
            if delete and storage not in storages:
                return self._refused(label, f"Storage '{storage}' is not a pool member")
            if not delete and storage in storages:
                return self._refused(label, f"Storage '{storage}' is already a pool member")
            (storages.discard if delete else storages.add)(storage)
        if "comment" in params:
            pool["info"]["comment"] = params["comment"]
        pool["storage"] = storages
        for vmid, member in moved.items():
            if member:
                VMS[vmid]["pool"] = member
            else:
                VMS[vmid].pop("pool", None)
        save_state()
        return self._reply(200, None)

    def _access(self, method, rest, params):
        """Users, roles, ACL entries and a user's tokens, as pve-access-control's API2 modules answer them. Every other
        path under /access is unknown here, as before."""
        if rest == ["users"] and method == "POST":
            userid = params.get("userid", "")
            if not userid:
                return self._reply(400, None, "Parameter verification failed.")
            if userid in USERS:
                return self._refused("create user failed", f"user '{userid}' already exists")
            groups = split_list(params.get("groups"))
            if groups:
                # The fake has no groups.
                return self._refused("create user failed", f"no such group '{groups[0]}'")
            USERS[userid] = {"expire": int(params.get("expire", "0"))}
            return self._reply(200, None)
        if rest == ["roles"] and method == "POST":
            roleid = params.get("roleid", "")
            if not roleid:
                return self._reply(400, None, "Parameter verification failed.")
            if roleid in ROLES:
                return self._refused("create role failed", f"role '{roleid}' already exists")
            if not DEFINED:
                return self._reply(501, None, "no list of Proxmox VE's privileges beside this copy of the fake")
            privs = set(split_list(params.get("privs")))
            unknown = sorted(privs - DEFINED)
            if unknown:
                return self._refused("create role failed", INVALID_PRIVILEGE.format(privilege=unknown[0]))
            ROLES[roleid] = privs
            return self._reply(200, None)
        if rest == ["acl"] and method == "PUT":
            return self._acl(params)
        if len(rest) == 4 and rest[0] == "users" and rest[2] == "token" and method in ("POST", "PUT"):
            return self._token(method, rest[1], rest[3], params)
        return self._reply(404, None)

    def _acl(self, params):
        """PUT /access/acl: each role given to, or with delete=1 taken from, each user and token named, at the path."""
        label = "ACL update failed"
        users, tokens, groups = (split_list(params.get(kind)) for kind in ("users", "tokens", "groups"))
        if "path" not in params or "roles" not in params or not (users or tokens or groups):
            return self._reply(400, None, "Parameter verification failed.")
        roles = split_list(params["roles"])
        for role in roles:
            if role not in ROLES:
                return self._refused(label, f"role '{role}' does not exist")
        if groups:
            return self._refused(label, f"group '{groups[0]}' does not exist")
        for user in users:
            if user not in USERS:
                return self._refused(label, f"user '{user}' does not exist")
        for full_id in tokens:
            if full_id not in TOKENS:
                user, _, tokenid = full_id.partition("!")
                return self._refused(label, f"no such token '{tokenid}' for user '{user}'")
        path, flag = acl_path(params["path"]), int(truthy(params.get("propagate", "1")))
        for role in roles:
            for kind, who in [("user", user) for user in users] + [("token", full_id) for full_id in tokens]:
                if truthy(params.get("delete")):
                    ACL.pop((path, kind, who, role), None)
                else:
                    ACL[(path, kind, who, role)] = flag
        return self._reply(200, None)

    def _token(self, method, userid, tokenid, params):
        """A user's token: made (POST), or changed and its secret made again (PUT)."""
        full_id = f"{userid}!{tokenid}"
        if method == "POST":
            if full_id in TOKENS:
                return self._reply(400, None, "Parameter verification failed.")
            if userid not in USERS:
                return self._refused("generating token failed", f"no such user ('{userid}')")
            TOKENS[full_id] = {
                "value": str(uuid.uuid4()),
                "privsep": int(truthy(params.get("privsep", "1"))),
                "expire": int(params.get("expire", USERS[userid]["expire"])),
                **({"comment": params["comment"]} if "comment" in params else {}),
            }
            made = TOKENS[full_id]
            return self._reply(200, {"full-tokenid": full_id, "info": token_info(made), "value": made["value"]})
        made = TOKENS.get(full_id)
        if made is None:
            return self._refused("updating token info failed", f"no such token '{tokenid}' for user '{userid}'")
        deleted = split_list(params.get("delete"))
        if any(key != "comment" for key in deleted):
            return self._reply(400, None, "Parameter verification failed.")
        if deleted:
            made.pop("comment", None)
        if "comment" in params:
            made["comment"] = params["comment"]
        for key in ("privsep", "expire"):
            if key in params:
                made[key] = int(truthy(params[key])) if key == "privsep" else int(params[key])
        answer = token_info(made)
        if truthy(params.get("regenerate")):
            made["value"] = str(uuid.uuid4())
            answer |= {"value": made["value"], "full-tokenid": full_id}
        return self._reply(200, answer)

    def _handle(self, method):
        path = urlparse(self.path).path
        params = self._params()
        token = TOKEN_RE.match(self.headers.get("Authorization", ""))
        with STATE_DIR.joinpath("calls.log").open("a") as log:
            # The agent tells a request the playbooks made themselves through
            # `uri` ("ansible-httpget") from one a community.proxmox module
            # made through proxmoxer ("python-requests/...").
            entry = {
                "method": method,
                "path": path,
                "params": params,
                "token": token and token.group(2),
                "agent": self.headers.get("User-Agent", ""),
            }
            log.write(json.dumps(entry) + "\n")

        if not token and TOKENLESS["answer"] == "none":
            self.close_connection = True
            return None
        if method == "HEAD":
            return self._reply_empty(501, "method 'HEAD' not available")
        # A token made through /access is held to its own secret, until its expire; any other, to the fake's.
        made = token and TOKENS.get(f"{token.group(1)}!{token.group(2)}")
        if made and (token.group(3) != made["value"] or 0 < made["expire"] <= time.time()):
            return self._reply_empty(401, "authentication failure")
        if not token or (not made and token.group(3) != TOKEN_SECRET):
            # Real pveproxy sends 401 with an *empty* body, deliberately
            # withholding the reason; the detail survives only in the HTTP
            # reason phrase. A well-formed {"data": null} here would let the
            # role look better-informed than it can be in production.
            return self._reply_empty(401, "authentication failure")
        # The token a task this request makes is recorded as made by.
        self.owner = f"{token.group(1)}!{token.group(2)}"
        # Under enforcement a made token is held to the engine's table; what a listing shows it of each kind of entry is
        # the privilege its call's row names (_refusal fills it).
        self.made = bool(made)
        self.enforced = self.made and bool(ENFORCE["on"])
        self.entries = {}
        self.decided = set()

        parts = [unquote(p) for p in path.removeprefix("/api2/json").strip("/").split("/")]
        # The path below the API as the fake routes it, its %-escapes decoded.
        self.routed = "/" + "/".join(parts)
        if token.group(2) == NOAGENT_TOKEN_ID and "agent" in parts:
            vmid = parts[parts.index("agent") - 1]
            return self._reply(403, None, f"Permission check failed (/vms/{vmid}, VM.GuestAgent.Audit)")
        if parts[:2] == ["fake", "tokenless"] and method == "PUT":
            # Not a PVE endpoint: a fixture's answer to a request with no token.
            TOKENLESS["answer"] = params["answer"]
            return self._reply(200, dict(TOKENLESS))
        if parts[:2] == ["fake", "redirect"] and method == "PUT":
            # Not a PVE endpoint: the next request that carries a token is sent elsewhere.
            REDIRECT_NEXT.append(params["location"])
            return self._reply(200, len(REDIRECT_NEXT))
        if REDIRECT_NEXT:
            self.send_response(302, "Found")
            self.send_header("Location", REDIRECT_NEXT.pop(0))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if parts[:2] == ["fake", "enforce"] and method == "PUT":
            # Not a PVE endpoint: a made token's requests held to the engine's table, or no longer.
            if truthy(params.get("on", "1")):
                try:
                    table()
                except ImportError:
                    return self._reply(501, None, f"no table of the engine's privileges under {LIB}")
            ENFORCE["on"] = int(truthy(params.get("on", "1")))
            return self._reply(200, dict(ENFORCE))
        # A node that is down answers nothing: pveproxy says 595 before that node could check a privilege.
        down = parts[:2] == ["nodes", DOWN_NODE] and not NODES_ONLINE[DOWN_NODE]
        if self.enforced and parts[:1] != ["fake"] and not down:
            refused = self._refusal(method, parts, params)
            if refused:
                return self._reply(PERMISSION_REFUSED_STATUS, None, refused)
        if parts[:2] == ["fake", "version"] and method == "PUT":
            # Not a PVE endpoint: a fixture's Proxmox version.
            VERSION["version"] = params["version"]
            return self._reply(200, dict(VERSION))
        if parts == ["version"]:
            release = ".".join(VERSION["version"].split(".")[:2])
            return self._reply(200, {"version": VERSION["version"], "release": release, "repoid": "fake"})
        if parts[:2] == ["fake", "privileges"] and method == "PUT":
            # Not a PVE endpoint: a fixture's privileges for one token at one path.
            PRIVILEGES[(params["token"], params["path"])] = [p for p in params.get("privs", "").split(",") if p]
            return self._reply(200, PRIVILEGES[(params["token"], params["path"])])
        if parts == ["access", "permissions"] and made:
            return self._reply(200, acl_permissions(f"{token.group(1)}!{token.group(2)}", params.get("path")))
        if parts == ["access", "permissions"]:
            fixed = [path for token_id, path in PRIVILEGES if token_id == token.group(2)]
            paths = [params["path"]] if "path" in params else ["/vms", *fixed]
            granted = {path: privileges(token.group(2), path) for path in paths}
            return self._reply(200, {path: privs for path, privs in granted.items() if privs})
        if parts[:2] == ["fake", "nodes"] and method == "PUT" and len(parts) == 3:
            # Not a PVE endpoint: a fixture's switch for the node that is down.
            NODES_ONLINE[parts[2]] = params.get("status") == "online"
            NODES_NAMED[parts[2]] = params.get("named") == "1"
            return self._reply(200, {"node": parts[2], "status": params.get("status")})
        if parts == ["nodes"]:
            # A node of any other name a fixture switched on is listed too, with nothing on it: what init reads
            # of a name the API chose.
            others = [{"node": n, "status": "online"} for n, on in NODES_ONLINE.items() if n != DOWN_NODE and on]
            return self._reply(
                200,
                [
                    {"node": NODE, "status": "online"},
                    {"node": DOWN_NODE, "status": "online" if NODES_ONLINE[DOWN_NODE] else "offline"},
                    *others,
                ],
            )
        if parts[:2] == ["fake", "refuse"] and method == "PUT":
            # Not a PVE endpoint: a fixture's one write that fails.
            REFUSE_NEXT.append((params["suffix"], params.get("methods", "PUT,POST,DELETE").split(",")))
            return self._reply(200, len(REFUSE_NEXT))
        if parts[:2] == ["fake", "vanish"] and method == "PUT":
            # Not a PVE endpoint: another run deleted this VM just after it was read.
            VANISH[int(params["vmid"])] = False
            return self._reply(200, sorted(VANISH))
        if REFUSE_NEXT and method in REFUSE_NEXT[0][1] and "/".join(parts).endswith(REFUSE_NEXT[0][0]):
            suffix, _ = REFUSE_NEXT.pop(0)
            return self._reply(500, None, f"refused by the fixture: {suffix}")
        if parts[:2] == ["fake", "listing-lag"] and method == "PUT":
            # Not a PVE endpoint: a fixture's switch for the listing's lag.
            LISTING_LAG["listings"] = int(params.get("listings", "0"))
            return self._reply(200, dict(LISTING_LAG))
        if parts[:2] == ["fake", "unnamed"] and method == "PUT":
            # Not a PVE endpoint: a guest the listings give with no name, as one its node has not reported yet; with
            # configuration=1, its configuration holds none either, as a clone's holds none until its worker writes it.
            UNNAMED.add(int(params["vmid"]))
            if params.get("configuration") == "1":
                VMS[int(params["vmid"])]["config"].pop("name", None)
                save_state()
            return self._reply(200, sorted(UNNAMED))
        if parts[:2] == ["fake", "agent-ready-after"] and method == "PUT":
            # Not a PVE endpoint: how many polls of a started VM's agent fail, for a fixture not about the wait.
            AGENT_READY["after"] = int(params.get("polls", str(AGENT_READY_AFTER)))
            return self._reply(200, dict(AGENT_READY))
        if parts[:2] == ["fake", "agent-answer"] and method == "PUT":
            # Not a PVE endpoint: what a VM's agent reports of its network cards, as its root makes it say.
            vmid = int(params["vmid"])
            if "result" in params:
                AGENT_ANSWER[vmid] = json.loads(params["result"])
            else:
                AGENT_ANSWER.pop(vmid, None)
            return self._reply(200, sorted(AGENT_ANSWER))
        if parts[:2] == ["fake", "task-polls"] and method == "PUT":
            # Not a PVE endpoint: how many polls of its status each task made from now on answers running to.
            TASK_POLLS["polls"] = int(params.get("polls", "0"))
            return self._reply(200, dict(TASK_POLLS))
        if parts[:2] == ["fake", "task-fail"] and method == "PUT":
            # Not a PVE endpoint: the error the next task, of the type given or any, ends with.
            TASK_FAIL.append((params.get("type", ""), params["error"]))
            return self._reply(200, len(TASK_FAIL))
        if parts[:2] == ["fake", "task-warn"] and method == "PUT":
            # Not a PVE endpoint: the warnings the next task, of the type given or any, logs and ends with, a success.
            TASK_WARN_NEXT.append((params.get("type", ""), params["warning"].split("\n")))
            return self._reply(200, len(TASK_WARN_NEXT))
        if parts[:2] == ["fake", "task-end"] and method == "PUT":
            # Not a PVE endpoint: running tasks end as their workers would, polled or not - the one upid names, or
            # every one; the UPIDs ended.
            ended = [
                upid
                for upid, record in TASK_RECORDS.items()
                if record["status"] == "running" and params.get("upid") in (None, upid)
            ]
            for upid in ended:
                TASK_RECORDS[upid]["polls"] = 0
                end_task(upid)
            return self._reply(200, ended)
        if parts[:2] == ["fake", "clone-race"] and method == "PUT":
            # Not a PVE endpoint: the next clone onto a VMID this node's VM holds, and the next configuration POST with
            # a stale digest, each lose a race in their tasks; each armed unless given 0, which disarms it.
            RACE.update({key: int(truthy(params.get(key, "1"))) for key in RACE})
            return self._reply(200, dict(RACE))
        if parts[:2] == ["fake", "clone-inherits"] and method == "PUT":
            # Not a PVE endpoint: whether a clone takes its source's tags and description.
            for key in CLONE_INHERITS:
                CLONE_INHERITS[key] = int(truthy(params.get(key, CLONE_INHERITS[key])))
            return self._reply(200, dict(CLONE_INHERITS))
        if parts[:2] == ["fake", "nextid-taken"] and method == "PUT":
            # Not a PVE endpoint: the VMID the next GET of /cluster/nextid answers, once, whatever holds it.
            NEXTID_TAKEN.append(int(params["vmid"]))
            return self._reply(200, list(NEXTID_TAKEN))
        if parts[:2] == ["fake", "clone-uuid"] and method == "PUT":
            # Not a PVE endpoint: the SMBIOS UUID the next clone that passes its call takes, queued after any before.
            CLONE_UUIDS.append(str(uuid.UUID(params["uuid"])))
            return self._reply(200, list(CLONE_UUIDS))
        if parts[:2] == ["fake", "notes-limit"] and method == "PUT":
            # Not a PVE endpoint: the most bytes a description may hold, 0 for no limit.
            NOTES_LIMIT["bytes"] = int(params.get("bytes", "0"))
            return self._reply(200, dict(NOTES_LIMIT))
        if parts == ["cluster", "resources"] and self.made and params.get("type") not in (None, *RESOURCE_TYPES):
            # As Proxmox's schema, to a token made through /access: a type its enumeration does not hold, pool among
            # them, is a 400. Today's tokens keep today's answer, which lists the pools.
            why = ENUM_REFUSED.format(value=params["type"], values=", ".join(RESOURCE_TYPES))
            return self._reply(ENUM_REFUSED_STATUS, None, "Parameter verification failed.", {"type": why})
        if parts == ["cluster", "resources"] and params.get("type") == "storage":
            # Only the storages the token may audit, as Proxmox lists them; none on the node that is down.
            listed = []
            for node, entries in STORAGES.items():
                if node == DOWN_NODE and not NODES_ONLINE[DOWN_NODE]:
                    continue
                for entry in entries:
                    at = "/storage/" + entry["storage"]
                    if not self._sees("storage", at, "Datastore.Audit" in privileges(token.group(2), at)):
                        continue
                    listed.append(storage_resource(node, entry))
            return self._reply(200, listed)
        if parts == ["cluster", "resources"] and params.get("type") == "pool":
            # Today's tokens alone, a made token's being the 400 above: the pools the token may read the members of
            # (Pool.Audit), as the fake always listed them, which today's just status reads.
            listed = [
                {"id": f"/pool/{name}", "type": "pool", "pool": name}
                for name in sorted(pool_names())
                if self._sees("pool", f"/pool/{name}", "Pool.Audit" in privileges(token.group(2), f"/pool/{name}"))
            ]
            return self._reply(200, listed)
        if parts == ["cluster", "resources"] and params.get("type") == "node":
            # A token without Sys.Audit sees the nodes, and no statistics of theirs; with it, an online node's too.
            nodes = [(NODE, "online"), (DOWN_NODE, "online" if NODES_ONLINE[DOWN_NODE] else "offline")]
            listed = []
            for n, s in nodes:
                entry = {"id": f"node/{n}", "type": "node", "node": n, "status": s, "level": ""}
                audited = (token.group(2), f"/nodes/{n}") in PRIVILEGES
                today = audited and "Sys.Audit" in privileges(token.group(2), f"/nodes/{n}")
                if s == "online" and self._sees("node", f"/nodes/{n}", today):
                    entry |= NODE_STATS
                listed.append(entry)
            return self._reply(200, listed)
        if parts[:2] == ["fake", "disk-ctime"] and method == "PUT":
            # Not a PVE endpoint: when a fixture's VM was made, as its disk's ctime says.
            VMS[int(params["vmid"])]["disk_ctime"] = int(params["ctime"])
            save_state()
            return self._reply(200, {"vmid": int(params["vmid"]), "ctime": int(params["ctime"])})
        if parts[:2] == ["fake", "pool-scope"] and method == "PUT":
            # Not a PVE endpoint: a token granted the role on /pool/<name> alone, which sees that pool's members only.
            POOL_SCOPE[params["token"]] = params["pool"]
            return self._reply(200, dict(POOL_SCOPE))
        if parts[:2] == ["fake", "pool-member"] and method == "PUT":
            # Not a PVE endpoint: a guest made a member of a pool on the host, as root does with pveum pool modify.
            VMS[int(params["vmid"])]["pool"] = params["pool"]
            save_state()
            return self._reply(200, {"vmid": int(params["vmid"]), "pool": params["pool"]})
        if parts[:1] == ["pools"] and len(parts) <= 2:
            deprecated = len(parts) == 2
            return self._pools(method, parts[1] if deprecated else params.get("poolid"), params, token, deprecated)
        if parts[:1] == ["access"]:
            return self._access(method, parts[1:], params)
        if parts == ["cluster", "resources"]:
            for vmid, listed in list(VANISH.items()):
                VANISH[vmid] = True
                if listed:
                    VMS.pop(vmid, None)
                    del VANISH[vmid]
                    save_state()
            scope = POOL_SCOPE.get(token.group(2))
            entries = [
                resource_for(vm, self._pooled(vm, token.group(2)))
                for vm in VMS.values()
                if (scope is None or vm.get("pool") == scope) and self._sees("vm", f"/vms/{vm['vmid']}", True)
            ]
            return self._reply(200, lagged(entries))
        if parts == ["cluster", "nextid"] and NEXTID_TAKEN:
            # As Proxmox's answer to a run whose VMID another run took before its clone landed (fake/nextid-taken).
            return self._reply(200, str(NEXTID_TAKEN.pop(0)))
        if parts == ["cluster", "nextid"]:
            return self._reply(200, str(next(i for i in range(NEXTID_LOWER, NEXTID_LOWER + 10000) if i not in VMS)))
        if parts[:2] == ["fake", "storages"] and method == "PUT":
            # Not a PVE endpoint: a fixture's storages for VM disks on a node.
            # <name>:<type>, then :inactive for one Proxmox cannot reach, :shared for one every node shares.
            listed = [entry.split(":") for entry in params.get("storages", "").split(",") if entry]
            entries = [(words + [""])[:2] + words[2:] for words in listed]
            STORAGES[params["node"]] = [
                {
                    "storage": n,
                    "type": t,
                    "avail": 100 * 1024**3,
                    "active": 0 if "inactive" in flags else 1,
                    **({"shared": 1} if "shared" in flags else {}),
                }
                for n, t, *flags in entries
            ]
            return self._reply(200, STORAGES[params["node"]])
        if parts[:2] == ["fake", "bridges"] and method == "PUT":
            # Not a PVE endpoint: a fixture's bridges on a node.
            BRIDGES[params["node"]] = [b for b in params.get("bridges", "").split(",") if b]
            return self._reply(200, BRIDGES[params["node"]])
        if parts[:2] == ["fake", "vm-node"] and method == "PUT":
            # Not a PVE endpoint: a fixture's VM, moved to another node.
            VMS[int(params["vmid"])]["node"] = params["node"]
            save_state()
            return self._reply(200, {"vmid": int(params["vmid"]), "node": params["node"]})
        if parts[:2] == ["nodes", DOWN_NODE] and not NODES_ONLINE[DOWN_NODE]:
            return self._reply_empty(595, "No route to host")
        if parts[:1] == ["nodes"] and parts[2:] == ["storage"]:
            # Only the storages the token may see, as Proxmox lists them.
            sees = {"Datastore.Audit", "Datastore.AllocateSpace"}
            seen = [
                {"active": 1, **entry, "content": "images,rootdir", "enabled": 1, "shared": entry.get("shared", 0)}
                for entry in STORAGES.get(parts[1], [])
                if self._sees(
                    "storage",
                    "/storage/" + entry["storage"],
                    bool(sees & set(privileges(token.group(2), "/storage/" + entry["storage"]))),
                )
            ]
            return self._reply(200, seen)
        if parts[:1] == ["nodes"] and parts[2:3] == ["storage"] and parts[4:] == ["content"]:
            # The disks on one storage, each VM's scsi0 that is on it; only with Datastore.Audit there.
            storage = parts[3]
            # Under enforcement, the engine's table has held a made token to its row already.
            if not self.enforced and "Datastore.Audit" not in privileges(token.group(2), "/storage/" + storage):
                return self._reply(403, None, f"Permission check failed (/storage/{storage}, Datastore.Audit)")
            volumes = []
            for vm in VMS.values():
                volid = str(vm["config"].get("scsi0", "")).split(",", 1)[0]
                if vm["node"] == parts[1] and volid.startswith(storage + ":"):
                    volumes.append(
                        {
                            "volid": volid,
                            "vmid": vm["vmid"],
                            "size": disk_bytes(vm["config"]),
                            "format": "raw",
                            "content": "images",
                            "ctime": vm.get("disk_ctime", 1760000000),
                        }
                    )
            return self._reply(200, volumes)
        if parts[:1] == ["nodes"] and parts[2:] == ["network"]:
            seen = [
                {"iface": bridge, "type": "bridge", "active": 1, "autostart": 1}
                for bridge in BRIDGES.get(parts[1], [])
                if self._sees(
                    "bridge",
                    "/sdn/zones/localnetwork/" + bridge,
                    bool(
                        {"SDN.Audit", "SDN.Use"} & set(privileges(token.group(2), "/sdn/zones/localnetwork/" + bridge))
                    ),
                )
            ]
            return self._reply(200, seen)
        # Either node, once up: a VM is served on the node it is on.
        if parts[:1] != ["nodes"] or len(parts) < 2 or parts[1] not in (NODE, DOWN_NODE):
            return self._reply(404, None)
        node, rest = parts[1], parts[2:]
        if rest == ["status"] and method == "GET":
            if not self.enforced and "Sys.Audit" not in privileges(token.group(2), f"/nodes/{node}"):
                return self._reply(403, None, f"Permission check failed (/nodes/{node}, Sys.Audit)")
            return self._reply(200, node_status())
        if rest == ["qemu"]:
            return self._reply(
                200, [resource(vm) for vm in VMS.values() if vm["node"] == node and guest_type(vm) == "qemu"]
            )
        if rest[:1] == ["tasks"] and rest[2:] == ["status"]:
            # Running to as many polls as it was made to run for, then stopped: it ends at the first poll after them.
            record = TASK_RECORDS.get(rest[1])
            if record and record["status"] == "running":
                if record["polls"] > 0:
                    record["polls"] -= 1
                    save_tasks()
                    return self._reply(200, {"status": "running", "upid": rest[1]})
                end_task(rest[1])
            ended = task_exitstatus(record) if record else "OK"
            return self._reply(200, {"status": "stopped", "exitstatus": ended, "upid": rest[1]})
        if rest[:1] == ["tasks"] and rest[2:] == ["log"]:
            # Its lines from start, at most limit of them, each with its number, as Proxmox gives them.
            lines = task_lines(TASK_RECORDS[rest[1]]) if rest[1] in TASK_RECORDS else []
            start = int(params.get("start", "0"))
            shown = lines[start : start + int(params.get("limit", "50"))]
            return self._reply(200, [{"n": start + i + 1, "t": line} for i, line in enumerate(shown)])
        if rest[:1] != ["qemu"] or len(rest) < 2 or not rest[1].isdigit():
            return self._reply(404, None)
        vmid, sub = int(rest[1]), rest[2:]
        if vmid not in VMS or VMS[vmid]["node"] != node or guest_type(VMS[vmid]) != "qemu":
            return self._reply(500, None, f"Configuration file 'nodes/{node}/qemu-server/{vmid}.conf' does not exist")
        vm = VMS[vmid]

        if method == "DELETE" and not sub:
            # Proxmox refuses to remove a VM with protection on, which is the
            # simplest way for one deletion of a fleet to fail.
            if str(vm["config"].get("protection", "0")) == "1":
                return self._reply(500, None, f"can't remove VM {vmid} - protection mode enabled")
            held = vm["config"].get("lock")
            if held:
                # As Proxmox: the deletion's task finds the lock, and fails with it, the VM as it was.
                return self._reply(200, new_task("qmdestroy", vmid, self.owner, error=LOCKED.format(lock=held)))
            # Locked while its task runs; gone when it ends, as it was when it fails.
            vm["config"]["lock"] = DELETION_LOCK

            def deleted(ok):
                if not ok:
                    vm["config"].pop("lock", None)
                elif VMS.get(vmid) is vm:
                    del VMS[vmid]
                save_state()

            save_state()
            return self._reply(200, new_task("qmdestroy", vmid, self.owner, deleted))
        if sub == ["config"] and method == "GET":
            # As Proxmox: the config's digest, which changes with any change to it.
            return self._reply(200, {**vm["config"], "digest": config_digest(vm["config"])})
        if sub == ["config"] and method in ("PUT", "POST"):
            # As Proxmox's schema, before anything else: sshkeys still percent-encoded once the form is decoded.
            if "sshkeys" in params and not re.fullmatch(URLENCODED, params["sshkeys"]):
                return self._sshkeys_refused(params["sshkeys"])
            # As update_vm_api, before the digest is read, to a token made through /access: a write of no key.
            if self._no_options():
                return self._reply(NO_OPTIONS_STATUS, None, NO_OPTIONS)
            # As Proxmox: a digest sent is the one a GET gives now, or nothing is written; it is never kept. A POST
            # that lost a race (RACE) passed this check before the winner's write, and its task fails instead.
            digest = params.pop("digest", None)
            stale = digest is not None and digest != config_digest(vm["config"])
            raced = stale and method == "POST" and RACE["config"] > 0
            if stale and not raced:
                return self._reply(DIGEST_REFUSED_STATUS, None, DIGEST_REFUSED)
            if vm["config"].get("lock"):
                return self._reply(LOCKED_STATUS, None, LOCKED.format(lock=vm["config"]["lock"]))
            if refused := notes_refusal(params):
                return self._notes_refused(refused)

            def written():
                # PVE removes a setting through `delete`, a comma-separated list of
                # keys - not by sending it blank. parse_qs drops blank values, so a
                # fixture that sent `net0=` used to leave the NIC in place and pass
                # as if it had removed it.
                for key in filter(None, (k.strip() for k in params.pop("delete", "").split(","))):
                    vm["config"].pop(key, None)
                # Not a PVE parameter: a fixture sends it to keep the tags exactly
                # as given - comma-joined, the way proxmox_kvm sends them and some
                # Proxmox versions echo them back - rather than normalised.
                verbatim = params.pop("fake-verbatim-tags", None)
                vm["config"].update(params)
                if "tags" in params and not verbatim:
                    vm["config"]["tags"] = normalise_tags(params["tags"])
                if "description" in params:
                    vm["config"]["description"] = notes_kept(params["description"])
                if "name" in params:
                    vm["name"] = params["name"]
                save_state()

            def found():
                # As Proxmox: the task's worker loads the configuration again under its lock and checks the digest
                # and the lock again (update_vm_api's $load_and_check_config), so a write made since stands.
                if VMS.get(vmid) is not vm:
                    return f"Configuration file 'nodes/{node}/qemu-server/{vmid}.conf' does not exist"
                if digest is not None and digest != config_digest(vm["config"]):
                    return DIGEST_REFUSED
                return LOCKED.format(lock=vm["config"]["lock"]) if vm["config"].get("lock") else ""

            if method == "POST":
                RACE["config"] -= int(raced)
                return self._reply(200, self._task("qmconfig", vmid, written, found))
            written()
            return self._reply(200, None)
        if sub == ["clone"] and method == "POST":
            if self.made and params.get("pool") and params["pool"] not in pool_names():
                # As Proxmox's check_pool_exist, for the tokens made through /access: today's keep today's answer.
                return self._reply(PERMISSION_REFUSED_STATUS, None, NO_SUCH_POOL.format(pool=params["pool"]))
            newid = int(params["newid"])
            # As Proxmox: the call refuses a VMID whose configuration file this node holds; one a container or
            # another node's VM holds, and one a race's winner took after the loser's call was checked (RACE), pass it,
            # and fail in the task.
            held = VMS.get(newid)
            here = held is not None and guest_type(held) == "qemu" and held["node"] == node
            at_the_call = held is not None and (here or not CLONE_TAKEN_IN_TASK)
            if at_the_call and not RACE["clone"]:
                return self._reply(500, None, CLONE_EXISTS.format(newid=newid))
            # As Proxmox: a linked clone shares its template's storage, and names none.
            if str(params.get("full", "")) == "0" and "storage" in params:
                return self._reply(400, None, "Parameter verification failed")
            if refused := clone_refusal(vm, node, params):
                return self._reply(500, None, refused)
            if held is not None:
                # As Proxmox's worker: it waits on the new VM's lock while the winner's task holds it, then reads this
                # node's files again; create_and_lock_config finds a VMID held anywhere else. Nothing is made.
                RACE["clone"] -= int(at_the_call)
                if not here:
                    kind = "VM" if guest_type(held) == "qemu" else "CT"
                    error = CLONE_TAKEN.format(kind=kind, newid=newid, node=held["node"])
                elif held["config"].get("lock") == CLONE_LOCK:
                    error = CLONE_LOCK_TIMEOUT.format(newid=newid)
                else:
                    error = CLONE_EXISTS.format(newid=newid)
                return self._reply(200, new_task("qmclone", vmid, self.owner, error=error))
            # Its source's whole config, as Proxmox copies it, but the tags and description where a fixture says not.
            taken = {k: v for k, v in vm["config"].items() if k != "template" and CLONE_INHERITS.get(k, 1)}
            disk = on_storage(vm["config"]["scsi0"].replace(f"base-{vmid}", f"vm-{newid}"), params.get("storage"))
            made = VMS[newid] = {
                "vmid": newid,
                "name": params.get("name", f"Copy-of-VM-{vmid}"),
                "node": clone_node(node, params),
                "status": "stopped",
                "template": 0,
                "config": {
                    **taken,
                    "name": params.get("name"),
                    "scsi0": disk,
                    "net0": f"virtio={mac_for(newid)},bridge=vmbr0",
                    # As Proxmox: every clone a SMBIOS UUID of its own, a VM made again at the same ID another; or the
                    # one a fixture queued (fake/clone-uuid).
                    "smbios1": f"uuid={CLONE_UUIDS.pop(0) if CLONE_UUIDS else uuid.uuid4()}",
                    # As Proxmox: the description given, in place of its source's.
                    **({"description": notes_kept(params["description"])} if "description" in params else {}),
                    # Locked until its task ends.
                    "lock": CLONE_LOCK,
                },
                # Its config keeps the template's meta; its disk is made now.
                "disk_ctime": int(time.time()),
            }

            def cloned(ok):
                # As Proxmox: its lock taken off when it ends, then pool= makes it a member of that pool, a pool the
                # token may allocate in; a clone that fails leaves no VM.
                if ok:
                    made["config"].pop("lock", None)
                    if params.get("pool"):
                        made["pool"] = params["pool"]
                elif VMS.get(newid) is made:
                    del VMS[newid]
                save_state()

            save_state()
            return self._reply(200, new_task("qmclone", vmid, self.owner, cloned))
        if sub == ["template"] and method == "POST":
            # Only the tests' fixtures call this, to build a template that
            # carries this project's tag. PVE refuses to convert a running VM.
            if vm["status"] == "running":
                return self._reply(500, None)

            def made_a_template():
                vm["template"] = 1
                vm["config"]["template"] = 1
                save_state()

            return self._reply(200, self._task("qmtemplate", vmid, made_a_template))
        if sub == ["resize"] and method == "PUT":
            disk, size = params["disk"], params["size"]
            # As Proxmox: a disk is grown, never shrunk.
            if not size.startswith("+") and disk_bytes({"scsi0": f"size={size}"}) < disk_bytes(
                {"scsi0": vm["config"][disk]}
            ):
                return self._reply(500, None, "shrinking disks is not supported")

            def resized():
                opts = vm["config"][disk].split(",")
                vm["config"][disk] = ",".join(o if not o.startswith("size=") else f"size={size}" for o in opts)
                save_state()

            return self._reply(200, self._task("resize", vmid, resized))
        if sub == ["agent", "network-get-interfaces"] and method == "GET":
            if not agent_on(vm["config"]):
                return self._reply(AGENT_REFUSED_STATUS, None, NO_AGENT)
            if vm["status"] != "running":
                return self._reply(AGENT_REFUSED_STATUS, None, VM_NOT_RUNNING.format(vmid=vmid))
            AGENT_CALLS[vmid] = AGENT_CALLS.get(vmid, 0) + 1
            if AGENT_CALLS[vmid] <= AGENT_READY["after"]:
                return self._reply(AGENT_REFUSED_STATUS, None, AGENT_NOT_RUNNING)
            return self._reply(200, {"result": AGENT_ANSWER[vmid] if vmid in AGENT_ANSWER else agent_interfaces(vm)})
        if sub == ["status", "current"]:
            maxmem = int(vm["config"].get("memory", 512)) * 1024**2
            current = {
                "status": vm["status"],
                "vmid": vmid,
                "name": vm["name"],
                "cpus": int(vm["config"].get("cores", 1)),
                "maxmem": maxmem,
                "qmpstatus": vm["status"],
                "agent": 1,
                "balloon": int(vm["config"].get("balloon", vm["config"].get("memory", 512))) * 1024**2,
                **live(vm),
            }
            if version_9():
                current.update(
                    {k: 0 for k in ("pressurecpusome", "pressurecpufull", "pressureiosome", "pressureiofull")}
                )
            return self._reply(200, current)
        if sub == ["rrddata"] and method == "GET":
            # A day: one point a minute on VE 9, one every twenty minutes on 8, the VM's CPU rising and falling.
            step = 60 if version_9() else 1200
            now = int(time.time()) // step * step
            points = []
            for i in range(86400 // step):
                t = now - 86400 + (i + 1) * step
                point = {"time": t}
                if vm["status"] == "running":
                    point.update(
                        {"cpu": round(0.05 + 0.04 * ((t // 7200) % 5), 4), "maxcpu": int(vm["config"].get("cores", 1))}
                    )
                    if version_9():
                        point.update({"pressurecpusome": 0.5, "memhost": 1})
                points.append(point)
            return self._reply(200, points)
        if sub == ["pending"] and method == "GET":
            return self._reply(200, [{"key": k, "value": v} for k, v in sorted(vm["config"].items())])
        if sub[:1] == ["agent"] and sub[1:] in (["get-osinfo"], ["get-fsinfo"]) and method == "GET":
            if not agent_on(vm["config"]):
                return self._reply(AGENT_REFUSED_STATUS, None, NO_AGENT)
            if vm["status"] != "running":
                return self._reply(AGENT_REFUSED_STATUS, None, VM_NOT_RUNNING.format(vmid=vmid))
            if AGENT_CALLS.get(vmid, 0) <= AGENT_READY["after"]:
                return self._reply(AGENT_REFUSED_STATUS, None, AGENT_NOT_RUNNING)
            if sub[1] == "get-osinfo":
                return self._reply(
                    200,
                    {
                        "result": {
                            "id": "ubuntu",
                            "name": "Ubuntu",
                            "pretty-name": "Ubuntu 24.04.3 LTS",
                            "version-id": "24.04",
                            "kernel-release": "6.8.0-85-generic",
                            "machine": "x86_64",
                        }
                    },
                )
            return self._reply(
                200,
                {
                    "result": [
                        {
                            "name": "sda1",
                            "mountpoint": "/",
                            "type": "ext4",
                            "used-bytes": ROOT_USED,
                            "total-bytes": ROOT_TOTAL,
                            "disk": [],
                        },
                        {
                            "name": "sda15",
                            "mountpoint": "/boot/efi",
                            "type": "vfat",
                            "used-bytes": 6 * 1024**2,
                            "total-bytes": 105 * 1024**2,
                            "disk": [],
                        },
                    ]
                },
            )
        if sub[:1] == ["status"] and method == "POST":
            action = sub[1]

            def done():
                if LISTING_LAG["listings"] > 0 and vmid not in STALE:
                    STALE[vmid] = [vm["status"], LISTING_LAG["listings"]]
                vm["status"] = "running" if action in ("start", "reboot", "reset", "resume") else "stopped"
                save_state()

            return self._reply(200, self._task(f"qm{action}", vmid, done))
        return self._reply(501, None)

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def log_message(self, *args):
        pass


save_state()
# One request at a time, on purpose: each check and the write after it - a digest, a VMID, a lock - is atomic only so,
# and a threaded server would make every one racy. tests/unit/test_aw_fake_pve.py holds it with a request that stalls
# half sent while another waits (test_the_fake_answers_one_request_at_a_time).
server = HTTPServer(("0.0.0.0", PORT), Handler)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(CERT, KEY)
server.socket = ctx.wrap_socket(server.socket, server_side=True)
# Given port 0 the system picks a free one, said on stdout: tests/unit/harness.py starts fakes so,
# never on a port it probed and freed, which another xdist worker's fake could take first.
if PORT == 0:
    print(server.server_address[1], flush=True)
server.serve_forever()
