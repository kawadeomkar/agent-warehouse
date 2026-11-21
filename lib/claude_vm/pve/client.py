"""The typed client of the Proxmox VE API: the reads every workflow makes, and its writes (docs/architecture/engine.md
§16.5).

One class, Proxmox, sends the engine's token to Proxmox, and it alone writes the token's header: the engine's one token
on the fleet's pool, a Token whose secret is a Secret, handed to the transport as parts of the header's value and
revealed there alone. It goes only on a connection pin.py made, whose certificate verified against what the host keeps
before anything was written. The first request a client sends is GET /version, which needs no privilege: a host below
Proxmox VE 9 is refused before any other request goes. A redirect is an answer, never followed (the transport follows
none); a 401 or a 403 is Denied, by its status alone, never by its words, since Proxmox names a VM in its reasons ("VM
4013 is not running"); any other answer but a 200 is Refused, with Proxmox's reason made plain. No method returns the
token, and no error says it: the transport's errors say no header's value, and this module's say what was asked and
what Proxmox said, with the token's secret struck from Proxmox's words first, so that a host, or a proxy the
computer's certificates trust, that echoes what it was sent has none of it said.

The writes are sent once each, never again: the transport sends no request but a GET twice, and one that left with no
answer is its Unknown, which a workflow settles by reading the VM again. Every write of a VM's configuration is one
PUT that sends the digest of the read it was made from, so that Proxmox writes it only while the configuration is
still that, in the call itself, under the VM's lock: a stale digest is DigestMismatch, and edit_config reads and
applies again, EDITS times at most. A clone, a resize, a start, a shutdown, a stop, a conversion to a template and a
deletion each answer a Task, which follow() polls to its end within a deadline of its kind; a task that ends with
anything but OK is TaskFailed with the last lines of its log, one whose VMID another took is Exists, one that found
the VM locked is Locked. Proxmox answers every refusal of a write with a 500, so its words tell them apart: a lock
(Locked, which a workflow may try again), a stale digest, a clone's VMID taken. The VMID a workflow asks for is its
own to choose, the record's text is its own to write, and a sequence of calls is its own to make: none of them is this
module's.

Each read and each write returns a frozen record of answers.py, built by a parsing function there, or nothing. Every
name and ID that goes into a path or a write is held to the form Proxmox gives it first, so that none can reach another
path or a query, and a configuration's write sends only the keys of CONFIG_KEYS, whose privileges are the engine's
table's rows for it. The guest agent's calls are card B6's. Blocking, and safe to call from several threads, as the
transport is.
"""

from __future__ import annotations

import dataclasses
import re
import threading
import time
import urllib.parse
from collections.abc import Callable, Collection, Mapping
from typing import Any

from ..checks import proxmox_name
from ..schema.patterns import full
from ..secret import Secret
from ..text import one_line
from ..vendors.transport import Answer, PeerRefused, TimedOut, Transport, Unknown, Untrusted
from . import answers, pin
from .answers import (
    UPID,
    Bridge,
    Current,
    Malformed,
    NodeStatus,
    Permissions,
    Pool,
    ProxmoxError,
    Read,
    Resource,
    Storage,
    Task,
    TaskLog,
    TaskStatus,
    Version,
    VmConfig,
)

__all__ = [
    "Denied",
    "DigestMismatch",
    "Exists",
    "Locked",
    "Malformed",
    "Proxmox",
    "ProxmoxError",
    "Refused",
    "Task",
    "TaskFailed",
    "TaskTimedOut",
    "Token",
    "TooOld",
    "Unknown",
    "Unverified",
]

# Where every path of the API is, under the host's address.
API = "/api2/json"
# The oldest Proxmox VE the engine speaks to: 9 (the owner's decision 6 of the nine; O9).
LEAST_MAJOR = 9
# What /cluster/resources lists, by its type.
KINDS = frozenset({"vm", "storage", "node", "pool", "sdn"})
# A network card of a VM's configuration.
NIC = re.compile(r"net[0-9]{1,2}")
# An ACL path, as /access/permissions takes one: / or its parts, each a name Proxmox gives.
ACL_PATH = re.compile(r"/|(?:/[A-Za-z0-9._@!:-]+)+")
# A path read() takes: its parts below the API, each a name, an ID or a UPID, none . or .. and none empty.
READ_PATH = re.compile(r"[A-Za-z0-9_@!:=-][A-Za-z0-9._@!:=-]*(?:/[A-Za-z0-9_@!:=-][A-Za-z0-9._@!:=-]*)*")

# ------------------------------------------------------------------------------------------- the writes ----

# The keys write_config sends, each with the privilege Proxmox checks it with on the VM (qemu-server's
# check_vm_modify_config_perm), which are the engine's table's rows for a configuration's write, no more
# (tests/unit/test_aw_pve_writes.py holds them equal): a key outside them is refused before anything is sent, so that
# the role init grants from the table holds what the writes need. ipconfig0 to ipconfig31 are cloud-init's too. tags
# are checked by a rule of their own (assert_tag_permissions, not read from its source here), which under the
# datacenter's default, free tags, asks VM.Config.Options; nameserver and searchdomain take VM.Config.Network instead.
CONFIG_KEYS = {
    "description": "VM.Config.Options",
    "tags": "VM.Config.Options",
    "name": "VM.Config.Options",
    "onboot": "VM.Config.Options",
    "agent": "VM.Config.Options",
    "ciuser": "VM.Config.Cloudinit",
    "cipassword": "VM.Config.Cloudinit",
    "sshkeys": "VM.Config.Cloudinit",
    "ciupgrade": "VM.Config.Cloudinit",
    "nameserver": "VM.Config.Cloudinit",
    "searchdomain": "VM.Config.Cloudinit",
    "cores": "VM.Config.CPU",
    "memory": "VM.Config.Memory",
    "balloon": "VM.Config.Memory",
}
IPCONFIG = re.compile(r"ipconfig(?:[0-9]|[12][0-9]|3[01])")
# A configuration's digest, as Proxmox gives one: SHA-1, hex.
DIGEST = re.compile(r"[0-9a-f]{40,64}")
# A tag, as Proxmox takes one (pve-common's pve-tag format, letters of either case).
TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_+.-]{0,99}")
# A VM's name, as Proxmox takes one (its dns-name format, one label or several).
VM_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,61}[A-Za-z0-9])?")
# A disk a resize grows, and its new size: an absolute size or one added, in Proxmox's units (resize_vm's pattern).
DISK = re.compile(r"(?:scsi|virtio|sata|ide)[0-9]{1,2}")
SIZE = re.compile(r"\+?[0-9]{1,12}(?:\.[0-9]{1,6})?[KMGT]?")
# How often edit_config reads, applies and writes before a stale digest is raised.
EDITS = 3
# What Proxmox answers a write it refuses for its VM's state: a 500, whatever the reason (qemu-server's PVE/API2/Qemu.pm
# and PVE/AbstractConfig.pm, read from their source). Its words tell the reasons apart; each is held equal to the
# fake's constant, which experiment 6 corrects from a real host (card A21).
REFUSED_STATUS = 500
# A digest that is not the configuration's now: update_vm_api's words.
STALE = re.compile(r"checksum mismatch")
# A VM that Proxmox holds locked, its lock in brackets (clone, destroyed, backup, ...): check_lock's words.
LOCK = re.compile(r"VM is locked \(([A-Za-z0-9_-]{1,40})\)")
# A clone whose new VMID is taken, each with that VMID: the call's own check (and its worker's once the winner's
# clone has ended), the worker's timeout on the new VM's lock while the winner's clone still runs, and
# create_and_lock_config's where a container or another node's VM holds it.
TAKEN = (
    re.compile(r"unable to create VM ([0-9]+): config file already exists"),
    re.compile(r"can't lock file '/var/lock/qemu-server/lock-([0-9]+)\.conf' - got timeout"),
    re.compile(r"(?:VM|CT) ([0-9]+) already exists on node "),
)
# How long follow() waits for a task of each kind, in seconds, unless told: today's waits, roles/proxmox_vm's
# proxmox_vm_timeout of 600 for what it follows and roles/proxmox_template's 300 for a conversion, and two minutes for a
# disk grown.
DEADLINES = {
    "clone": 600.0,
    "resize": 120.0,
    "start": 600.0,
    "shutdown": 600.0,
    "stop": 600.0,
    "template": 300.0,
    "delete": 600.0,
}
# The pauses between follow()'s polls, growing to the last, which it keeps.
POLLS = (0.1, 0.2, 0.5, 1.0)
# A failed task's log is read this many lines a request, at most so many requests, and its last lines kept.
LOG_PAGE, LOG_PAGES, LOG_LAST = 500, 20, 10


class Denied(ProxmoxError):
    """Proxmox refused the token: a 401 or a 403, by its status alone. It holds for every VM, so a workflow stops at
    once rather than wait or skip."""

    def __init__(self, status: int, asked: str) -> None:
        self.status = status
        what = "did not take the token" if status == 401 else "refused the token the privilege it needs"
        super().__init__(f"Proxmox {what} ({status}) to {asked}")


class Refused(ProxmoxError):
    """Any other answer but a 200: status, and Proxmox's reason, made one line of plain text. A 3xx is one, never
    followed: the token goes to the address it was given alone."""

    def __init__(self, status: int, reason: str, asked: str) -> None:
        self.status = status
        self.reason = one_line(reason, 200)
        if 300 <= status < 400:
            said = f"Proxmox answered {asked} with a redirect ({status}), which is never followed"
        else:
            said = f"Proxmox answered {asked} with {status}" + (f": {self.reason}" if self.reason else "")
        super().__init__(said)


class DigestMismatch(ProxmoxError):
    """Proxmox refused a write of a VM's configuration: another writer changed it after the read whose digest the write
    sent. Nothing was written; the writer reads it again, as edit_config does."""

    def __init__(self, vmid: int, asked: str) -> None:
        self.vmid = vmid
        super().__init__(
            f"Proxmox refused {asked}: VM {vmid}'s configuration changed since it was read, and nothing was written"
        )


class Locked(ProxmoxError):
    """Proxmox holds the VM locked - a clone, a deletion or a backup runs on it - and refused what was asked, which may
    be tried again once that has ended. lock is Proxmox's word for it."""

    def __init__(self, vmid: int, lock: str, asked: str) -> None:
        self.vmid = vmid
        self.lock = lock
        super().__init__(f"VM {vmid} is locked ({lock}) on Proxmox, which refused {asked}: try again once it has ended")


class Exists(ProxmoxError):
    """A clone's new VMID is taken - by a VM or a container, here or on another node, or by another run's clone that
    came first - at the call or in its task. Nothing was made at that VMID by this clone, and nothing there is deleted:
    on a host it would be another's VM. The workflow asks for the next free VMID again."""

    def __init__(self, newid: int) -> None:
        self.newid = newid
        super().__init__(
            f"VMID {newid} is taken: a VM or a container holds it, or another run's clone came first; ask for the"
            " next free VMID again"
        )


class TaskFailed(ProxmoxError):
    """A task ended with anything but OK: Proxmox's error, and the last lines of its log, each one line of plain text
    with the token's secret struck."""

    def __init__(self, task: Task, error: str, lines: tuple[str, ...]) -> None:
        self.task = task
        self.error = error
        self.lines = lines
        said = f"Proxmox's {task.kind} task for VM {task.vmid} failed: {error or 'no error given'}"
        super().__init__(said + (f" (its log ends: {' | '.join(lines)})" if lines else ""))


class TaskTimedOut(ProxmoxError):
    """A task still ran when its deadline came, and was not waited for longer: it may yet end either way, so the
    workflow reads the VM to know."""

    def __init__(self, task: Task, seconds: float) -> None:
        self.task = task
        self.seconds = seconds
        super().__init__(
            f"Proxmox's {task.kind} task for VM {task.vmid} still ran after {seconds:g} s, and was not waited for"
            " longer: it may yet end, so read the VM to know"
        )


class TooOld(ProxmoxError):
    """The host's Proxmox VE is older than 9, which the engine does not speak to."""

    def __init__(self, found: Version) -> None:
        self.version = found.version
        super().__init__(
            f"this Proxmox host runs Proxmox VE {one_line(found.version, 40)}, and the engine speaks to Proxmox VE"
            f" {LEAST_MAJOR} or later only: upgrade the host first"
        )


class Unverified(ProxmoxError):
    """The host's certificate is not what the host keeps: it does not verify against its anchor, or it is not the
    node's certificate whose fingerprint was recorded. Nothing was sent; it is never tried again without the check."""

    def __init__(self, reason: str) -> None:
        self.reason = one_line(reason, 200)
        super().__init__(
            f"the Proxmox host's certificate is not the one kept for it ({self.reason}): if Proxmox renewed it, or the"
            " host was reinstalled, aw init reads the host's own and asks before it trusts one in its place; if not,"
            " something else may answer at that address"
        )


@dataclasses.dataclass(frozen=True)
class Token:
    """The engine's one token on the fleet's pool: its ID, user@realm!name, and its secret, a Secret."""

    id: str
    secret: Secret

    def __post_init__(self) -> None:
        user, _, name = self.id.partition("!") if isinstance(self.id, str) else ("", "", "")
        realm = user.partition("@")[2]
        if not (realm and name and full("token_user", user) and full("proxmox_id", name)):
            raise ValueError("a token's ID is user@realm!name, of the names Proxmox gives")
        if not isinstance(self.secret, Secret):
            raise TypeError("a token's secret is a Secret")


def _node(node: str) -> str:
    if not proxmox_name(node, "node"):
        raise ValueError("a node is named as Proxmox names one")
    return node


def _vmid(vmid: int) -> int:
    if isinstance(vmid, bool) or not isinstance(vmid, int) or not answers.LEAST_VMID <= vmid <= answers.MOST_VMID:
        raise ValueError(f"a VMID is a number from {answers.LEAST_VMID} to {answers.MOST_VMID}")
    return vmid


def _upid(upid: str) -> str:
    if not isinstance(upid, str) or not UPID.fullmatch(upid):
        raise ValueError("a task's ID is a UPID as Proxmox gives one")
    return upid


def _pool(poolid: str) -> str:
    if not proxmox_name(poolid, "pool"):
        raise ValueError("a pool is named as Proxmox names one")
    return poolid


def _seconds(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not value > 0:
        raise ValueError("a deadline is a number of seconds above 0")
    return float(value)


def _key(key: str) -> str:
    if not isinstance(key, str) or not (key in CONFIG_KEYS or IPCONFIG.fullmatch(key)):
        raise ValueError(
            "a configuration's write sends the keys whose privileges the engine's table holds:"
            f" {', '.join(CONFIG_KEYS)} and ipconfig0 to ipconfig31; a key outside them needs a row of its own first"
        )
    return key


def _value(key: str, value: Any) -> str | int | Secret:
    """One key's value as the form sends it: tags a list of tags, comma-joined, which is how Proxmox takes them and
    reads back either way; sshkeys the keys' text, one a line, percent-encoded whole, as Proxmox's format wants it;
    any other a str, a whole number or a Secret."""
    if key == "tags":
        if (
            isinstance(value, str)
            or not isinstance(value, Collection)
            or not all(isinstance(tag, str) and TAG.fullmatch(tag) for tag in value)
        ):
            raise ValueError("tags are a list of tags, each as Proxmox takes one")
        return ",".join(value)
    if key == "sshkeys":
        if not isinstance(value, str):
            raise TypeError("sshkeys is the keys' text, one a line")
        return urllib.parse.quote(value, safe="")
    if isinstance(value, bool) or not isinstance(value, str | int | Secret):
        raise TypeError(f"{key} is a str, a whole number or a Secret")
    return value


def _changes(changes: Mapping[str, Any]) -> dict[str, str | int | Secret]:
    """changes as write_config's form: each key one of CONFIG_KEYS, its value as _value makes it; delete, the keys to
    remove, each one of CONFIG_KEYS too, comma-joined. digest is the write's own, never one of them."""
    if not isinstance(changes, Mapping) or not changes:
        raise ValueError("a configuration's write is a mapping of the keys it changes, at least one")
    form: dict[str, str | int | Secret] = {}
    for key, value in changes.items():
        if key == "delete":
            if isinstance(value, str) or not isinstance(value, Collection) or not value:
                raise ValueError("delete is a list of the keys to remove")
            form["delete"] = ",".join(_key(one) for one in value)
        else:
            form[_key(key)] = _value(key, value)
    return form


def _taken(said: str, newid: int) -> bool:
    """Whether Proxmox's words said said that the VMID newid is taken."""
    return any(int(found.group(1)) == newid for pattern in TAKEN for found in pattern.finditer(said))


class Proxmox:
    """The client of the API at url (https://host:port), its connections verified as pinned says (pin.kept), sending
    token, through proxy where there is one: an http:// URL, which proxy.choose works out for the API's address."""

    def __init__(self, url: str, pinned: pin.Pin, token: Token, proxy: str | None = None) -> None:
        if not isinstance(pinned, pin.Pin):
            raise TypeError("the client is given the Pin its connections are verified with")
        if not isinstance(token, Token):
            raise TypeError("the client is given a Token")
        parts = urllib.parse.urlsplit(url)
        if parts.path not in ("", "/"):
            raise ValueError("the API's address is https://host:port, with no path: the client adds /api2/json")
        self._anchored = pinned.anchored
        self._token = token
        self._said = f"Proxmox(token={token.id!r})"
        self._transport: Transport = pin.transport(f"{url.rstrip('/')}{API}", pinned, proxy)
        self._version: Version | None = None
        self._lock = threading.Lock()

    def __enter__(self) -> Proxmox:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close every connection kept: for when no request is in flight."""
        self._transport.close()

    def __repr__(self) -> str:
        return self._said

    # ------------------------------------------------------------------------------------------------ asking ----

    def _request(
        self,
        method: str,
        path: str,
        query: Mapping[str, Any] | None = None,
        form: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Answer:
        """method path, carrying the token, with query and form: Proxmox's answer, whatever its status. Every refusal
        is raised after the handler that caught its cause has ended, so it carries no cause."""
        failure: ProxmoxError | None = None
        try:
            answer = self._transport.request(
                method,
                path,
                headers={"Authorization": (f"PVEAPIToken={self._token.id}=", self._token.secret)},
                query=query,
                form=form,
                deadline=deadline,
            )
        except Untrusted as error:
            if not self._anchored:
                raise  # the computer's certificate file: the transport's sentence names tls_ca_file
            failure = Unverified(f"it does not verify against the host's anchor: {error.reason}")
        except PeerRefused:
            failure = Unverified("its SHA-256 fingerprint is not the one recorded for the host")
        if failure is not None:
            raise failure
        return answer

    def _words(self, text: str, most: int | None) -> str:
        """Proxmox's words as they may be said: one line of plain text, the token's secret struck from them before
        they are cut, as they came and again once made plain, so that no part of a secret a peer echoed is said."""
        secret = self._token.secret
        return one_line(secret.struck(one_line(secret.struck(text), None)), most)

    def _ask(self, path: str, query: Mapping[str, str] | None = None, deadline: float | None = None) -> Any:
        """GET path, carrying the token: the answer's data, or the client's refusal."""
        asked = f"GET {path}"
        answer = self._request("GET", path, query, deadline=deadline)
        if answer.status in (401, 403):
            raise Denied(answer.status, asked)
        if answer.status != 200:
            raise Refused(answer.status, self._words(answer.reason, 200), asked)
        return answers.data(answer.body, asked)

    def _get(self, path: str, query: Mapping[str, str] | None = None, deadline: float | None = None) -> Any:
        """GET path once the host's version is known to be one the engine speaks to."""
        self.version()
        return self._ask(path, query, deadline)

    def _send(
        self,
        method: str,
        path: str,
        vmid: int,
        form: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        newid: int | None = None,
    ) -> Any:
        """A write, method path for VM vmid, sent once, once the host's version is one the engine speaks to: the
        answer's data, or its refusal - Denied by its status alone; where Proxmox answers REFUSED_STATUS, by its words,
        a clone's Exists (for newid), DigestMismatch or Locked; else Refused. The transport's Unknown, a write that
        left with no answer, passes as it is: it is never sent again."""
        self.version()
        asked = f"{method} {path}"
        answer = self._request(method, path, query, form)
        if answer.status == 200:
            return answers.data(answer.body, asked)
        if answer.status in (401, 403):
            raise Denied(answer.status, asked)
        said = self._words(answer.reason, 200)
        if answer.status == REFUSED_STATUS:
            if newid is not None and _taken(said, newid):
                raise Exists(newid)
            if STALE.search(said):
                raise DigestMismatch(vmid, asked)
            if locked := LOCK.search(said):
                raise Locked(vmid, locked.group(1), asked)
        raise Refused(answer.status, said, asked)

    # ------------------------------------------------------------------------------------------------- reads ----

    def version(self) -> Version:
        """GET /version, asked once a client: the first request it sends. Raises TooOld for a host below Proxmox VE 9,
        each time it is asked, with nothing more sent."""
        with self._lock:
            if self._version is None:
                self._version = answers.version(self._ask("version"))
            found = self._version
        if found.major < LEAST_MAJOR:
            raise TooOld(found)
        return found

    def resources(self, kind: str) -> tuple[Resource, ...]:
        """GET /cluster/resources?type=kind: what the cluster lists of that kind (vm, storage, node, pool, sdn), as far
        as the token may see it. A guest's status here is the one the cluster last polled, up to ten seconds old: it is
        never read for what the same run just changed, which current() reads from the VM itself."""
        if kind not in KINDS:
            raise ValueError(f"the cluster lists {', '.join(sorted(KINDS))}")
        return answers.resources(self._get("cluster/resources", {"type": kind}))

    def pool(self, poolid: str) -> Pool:
        """GET /pools?poolid=: the pool, its comment and its members."""
        if not proxmox_name(poolid, "pool"):
            raise ValueError("a pool is named as Proxmox names one")
        return answers.pool(self._get("pools", {"poolid": poolid}), poolid)

    def config(self, node: str, vmid: int, nic: str = "net0") -> VmConfig:
        """GET a VM's configuration, with its digest, its identity (its SMBIOS UUID) and the MAC of its network card
        nic (proxmox_nic's, net0 by default)."""
        if not isinstance(nic, str) or not NIC.fullmatch(nic):
            raise ValueError("a network card is net0 to net99")
        node, vmid = _node(node), _vmid(vmid)
        return answers.config(self._get(f"nodes/{node}/qemu/{vmid}/config"), node, vmid, nic)

    def current(self, node: str, vmid: int) -> Current:
        """GET a VM's own state now, from the VM, never stale as the cluster's listing is."""
        node, vmid = _node(node), _vmid(vmid)
        return answers.current(self._get(f"nodes/{node}/qemu/{vmid}/status/current"), node, vmid)

    def next_id(self) -> int:
        """GET /cluster/nextid: the lowest free VMID when asked, which another run may take first: a clone onto it
        that finds it taken asks again (card B4)."""
        return answers.next_id(self._get("cluster/nextid"))

    def node_status(self, node: str) -> NodeStatus:
        """GET a node's CPU threads and memory, which a VM's size is held to: Sys.Audit on the node, or Denied."""
        node = _node(node)
        return answers.node_status(self._get(f"nodes/{node}/status"), node)

    def task_status(self, node: str, upid: str) -> TaskStatus:
        """GET whether a task runs, and how it ended."""
        node, upid = _node(node), _upid(upid)
        return answers.task_status(self._get(f"nodes/{node}/tasks/{upid}/status"), node, upid)

    def task_log(self, node: str, upid: str, start: int = 0, limit: int = 50) -> TaskLog:
        """GET a task's log, limit lines from line start (0, the first), each made plain."""
        node, upid = _node(node), _upid(upid)
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise ValueError("a log is read from a line number of 0 or more")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= 10000:
            raise ValueError("a log is read 1 to 10000 lines at a time")
        query = {"start": str(start), "limit": str(limit)}
        return answers.task_log(self._get(f"nodes/{node}/tasks/{upid}/log", query), node, upid, start)

    def storages(self, node: str, content: str = "images") -> tuple[Storage, ...]:
        """GET the node's enabled storages that hold content (images, a VM's disks, unless told), those the token may
        see."""
        node = _node(node)
        if not re.fullmatch(r"[a-z]{1,20}", content):
            raise ValueError("a storage's content is one word of Proxmox's: images, iso, import, ...")
        return answers.storages(self._get(f"nodes/{node}/storage", {"content": content, "enabled": "1"}), node)

    def bridges(self, node: str) -> tuple[Bridge, ...]:
        """GET the node's bridges, those the token may see."""
        node = _node(node)
        return answers.bridges(self._get(f"nodes/{node}/network", {"type": "any_local_bridge"}), node)

    def permissions(self, path: str) -> Permissions:
        """GET the privileges the token holds at an ACL path (/vms/<id>, /pool/<name>, /storage/<name>, ...)."""
        if not isinstance(path, str) or not ACL_PATH.fullmatch(path):
            raise ValueError("an ACL path is / or its parts, each a name Proxmox gives")
        return answers.permissions(self._get("access/permissions", {"path": path}), path)

    def read(self, path: str, query: Mapping[str, str] | None = None) -> Read:
        """GET any other path below the API, with query, for the reads status adds (a VM's history, its pending
        changes, a storage's disks): its data as sent, frozen."""
        if not isinstance(path, str) or not READ_PATH.fullmatch(path):
            raise ValueError("a path below the API is its parts, each a name, an ID or a UPID")
        if query is not None and not all(isinstance(k, str) and isinstance(v, str) for k, v in query.items()):
            raise TypeError("a read's query is names and values, each a str")
        return answers.read(self._get(path, query), path)

    # ------------------------------------------------------------------------------------------------ writes ----

    def clone(
        self,
        node: str,
        source: int,
        newid: int,
        name: str,
        pool: str,
        full: bool = True,
        storage: str | None = None,
        description: str | None = None,
    ) -> Task:
        """POST a clone of the VM or template source on node onto newid, named name, a member of pool once its task
        has ended - a full clone unless full is False, its disk on storage where one is named (a linked clone shares
        its source's, and names none), its notes description where one is given, over its source's: the Task, which
        follow() follows to its end. Raises Exists where newid is taken at the call; follow() raises it where the task
        finds it taken, in any of Proxmox's words for it. Either way the workflow asks for the next free VMID again, and
        nothing is ever deleted at newid, which on a host would be the VM of the run that came first. The new VM is
        proved by reading its own config once the task has ended OK - its identity, its MAC, its name - never by an ID
        a call returned: a module once returned the clone's source's VMID for a clone that made nothing."""
        node, source, newid, pool = _node(node), _vmid(source), _vmid(newid), _pool(pool)
        if not isinstance(name, str) or not VM_NAME.fullmatch(name):
            raise ValueError("a VM's name is one Proxmox takes: letters, digits, dots and dashes")
        if not isinstance(full, bool):
            raise TypeError("full is True or False")
        form: dict[str, Any] = {"newid": newid, "name": name, "pool": pool, "full": int(full)}
        if storage is not None:
            if not full:
                raise ValueError("a linked clone shares its source's storage, and names none")
            if not proxmox_name(storage, "storage"):
                raise ValueError("a storage is named as Proxmox names one")
            form["storage"] = storage
        if description is not None:
            if not isinstance(description, str):
                raise TypeError("a description is a str")
            form["description"] = description
        upid = self._send("POST", f"nodes/{node}/qemu/{source}/clone", source, form=form, newid=newid)
        return answers.task(upid, node, "clone", newid)

    def write_config(self, node: str, vmid: int, changes: Mapping[str, Any], digest: str) -> None:
        """PUT changes to a VM's configuration with digest, the one the read they were made from gave, in one call:
        Proxmox writes them only while the configuration is still that, under the VM's lock. changes' keys are
        CONFIG_KEYS' (delete, a list of them, removes those); tags are a list of tags, sent comma-joined; sshkeys is the
        keys' text, one a line, sent percent-encoded whole, and read back as Proxmox keeps it, encoded. Raises
        DigestMismatch where another writer changed it since, nothing written; Locked where a clone, a deletion or a
        backup holds the VM; and the transport's Unknown where the call left and no answer came: read the configuration
        to know, since it is never sent again."""
        node, vmid = _node(node), _vmid(vmid)
        form = _changes(changes)
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ValueError("a configuration's write sends the digest its read gave")
        form["digest"] = digest
        self._send("PUT", f"nodes/{node}/qemu/{vmid}/config", vmid, form=form)

    def edit_config(
        self, node: str, vmid: int, edit: Callable[[VmConfig], Mapping[str, Any] | None]
    ) -> Mapping[str, Any]:
        """Read a VM's configuration, apply edit to it - a function of the VmConfig read that returns write_config's
        changes, or nothing for none - and write them with the digest that read gave. Where another writer changed it in
        between, read it again and apply edit again, EDITS times in all, then raise the last DigestMismatch. Returns the
        changes written, {} where edit asked for none."""
        if not callable(edit):
            raise TypeError("edit is a function of the configuration read")
        stale: DigestMismatch | None = None
        for _ in range(EDITS):
            config = self.config(node, vmid)
            changes = edit(config)
            if not changes:
                return {}
            try:
                self.write_config(node, vmid, changes, config.digest)
            except DigestMismatch as refused:
                stale = refused
                continue
            return dict(changes)
        assert stale is not None
        raise stale

    def resize(self, node: str, vmid: int, disk: str, size: str) -> Task:
        """PUT a VM's disk grown to size (40G), or by it (+8G): the Task. Proxmox never shrinks a disk, and leaves one
        already that size as it is."""
        node, vmid = _node(node), _vmid(vmid)
        if not isinstance(disk, str) or not DISK.fullmatch(disk):
            raise ValueError("a disk is scsi0, virtio0, sata0 or ide0, ...")
        if not isinstance(size, str) or not SIZE.fullmatch(size):
            raise ValueError("a size is a number of bytes or of K, M, G or T, + before one added")
        upid = self._send("PUT", f"nodes/{node}/qemu/{vmid}/resize", vmid, form={"disk": disk, "size": size})
        return answers.task(upid, node, "resize", vmid)

    def start(self, node: str, vmid: int) -> Task:
        """POST a VM started: the Task."""
        node, vmid = _node(node), _vmid(vmid)
        return answers.task(self._send("POST", f"nodes/{node}/qemu/{vmid}/status/start", vmid), node, "start", vmid)

    def shutdown(self, node: str, vmid: int, timeout: int | None = None, force_stop: bool = False) -> Task:
        """POST a VM shut down through its guest, waiting timeout seconds for it where given, and stopped then where
        force_stop says so: the Task."""
        node, vmid = _node(node), _vmid(vmid)
        form: dict[str, Any] = {}
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 0:
                raise ValueError("a shutdown's timeout is a whole number of seconds")
            form["timeout"] = timeout
        if not isinstance(force_stop, bool):
            raise TypeError("force_stop is True or False")
        if force_stop:
            form["forceStop"] = 1
        upid = self._send("POST", f"nodes/{node}/qemu/{vmid}/status/shutdown", vmid, form=form or None)
        return answers.task(upid, node, "shutdown", vmid)

    def stop(self, node: str, vmid: int) -> Task:
        """POST a VM stopped at once, as pulling its power would: the Task."""
        node, vmid = _node(node), _vmid(vmid)
        return answers.task(self._send("POST", f"nodes/{node}/qemu/{vmid}/status/stop", vmid), node, "stop", vmid)

    def to_template(self, node: str, vmid: int) -> Task:
        """POST a stopped VM made a template: the Task."""
        node, vmid = _node(node), _vmid(vmid)
        return answers.task(self._send("POST", f"nodes/{node}/qemu/{vmid}/template", vmid), node, "template", vmid)

    def delete(self, node: str, vmid: int, purge: bool = True) -> Task:
        """DELETE a VM with its disks, those its configuration no longer lists but that carry its VMID too, as today's
        role deletes one (destroy-unreferenced-disks), and with purge, its VMID taken out of the host's backup jobs,
        replication and HA as well: the Task."""
        node, vmid = _node(node), _vmid(vmid)
        if not isinstance(purge, bool):
            raise TypeError("purge is True or False")
        query = {"destroy-unreferenced-disks": "1", **({"purge": "1"} if purge else {})}
        return answers.task(self._send("DELETE", f"nodes/{node}/qemu/{vmid}", vmid, query=query), node, "delete", vmid)

    def pool_add(self, poolid: str, vmids: Collection[int]) -> None:
        """PUT guests into a pool: Proxmox answers nothing and has made the change in the call, so there is no task.
        Each guest is one the token holds VM.Allocate on, or Proxmox refuses the whole call, and one already in another
        pool is refused, never moved."""
        poolid = _pool(poolid)
        if isinstance(vmids, str) or not isinstance(vmids, Collection) or not vmids:
            raise ValueError("a pool is given one VMID or more")
        listed = [_vmid(vmid) for vmid in vmids]
        self._send("PUT", "pools", listed[0], form={"poolid": poolid, "vms": ",".join(map(str, listed))})

    # ----------------------------------------------------------------------------------------------- tasks ----

    def follow(self, task: Task, deadline: float | None = None) -> TaskStatus:
        """Poll task's status until it ends, within deadline seconds (DEADLINES' for its kind where None), and never
        longer: each poll is held to what is left of it. Returns the status of a task that ended OK. Raises
        TaskTimedOut where it still runs at the deadline; for one that ended otherwise, Exists for a clone whose VMID
        was taken, Locked for one that found its VM locked, else TaskFailed with the last lines of its log."""
        if not isinstance(task, Task):
            raise TypeError("follow() follows a Task a write answered")
        seconds = DEADLINES[task.kind] if deadline is None else _seconds(deadline)
        end = time.monotonic() + seconds
        pauses = iter(POLLS)
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise TaskTimedOut(task, seconds)
            late: TimedOut | None = None
            try:
                status = answers.task_status(
                    self._get(f"nodes/{task.node}/tasks/{task.upid}/status", deadline=left), task.node, task.upid
                )
            except TimedOut as error:
                late = error
            if late is not None:
                if end - time.monotonic() > 0:
                    raise late
                raise TaskTimedOut(task, seconds)
            if not status.running:
                break
            time.sleep(max(0.0, min(next(pauses, POLLS[-1]), end - time.monotonic())))
        if status.ok:
            return status
        error = status.entry.get("exitstatus")
        said = self._words(error if isinstance(error, str) else "", 400)
        if task.kind == "clone" and _taken(said, task.vmid):
            raise Exists(task.vmid)
        if locked := LOCK.search(said):
            raise Locked(task.vmid, locked.group(1), f"its {task.kind} task")
        raise TaskFailed(task, said, self._last_lines(task))

    def _last_lines(self, task: Task) -> tuple[str, ...]:
        """The last LOG_LAST lines of task's log, read LOG_PAGE lines a request and LOG_PAGES requests at most, each
        one line of plain text with the token's secret struck."""
        kept: list[str] = []
        for page in range(LOG_PAGES):
            read = self.task_log(task.node, task.upid, page * LOG_PAGE, LOG_PAGE)
            kept = (kept + list(read.lines))[-LOG_LAST:]
            if len(read.lines) < LOG_PAGE:
                break
        return tuple(self._words(line, 200) for line in kept)
