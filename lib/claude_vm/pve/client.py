"""The typed client of the Proxmox VE API: the reads every workflow makes (docs/architecture/engine.md §16.5).

One class, Proxmox, sends the engine's token to Proxmox, and it alone writes the token's header: the engine's one token
on the fleet's pool, a Token whose secret is a Secret, handed to the transport as parts of the header's value and
revealed there alone. It goes only on a connection pin.py made, whose certificate verified against what the host keeps
before anything was written. The first request a client sends is GET /version, which needs no privilege: a host below
Proxmox VE 9 is refused before any other request goes. A redirect is an answer, never followed (the transport follows
none); a 401 or a 403 is Denied, by its status alone, never by its words, since Proxmox names a VM in its reasons ("VM
4013 is not running"); any other answer but a 200 is Refused, with Proxmox's reason made plain. No method returns the
token, and no error says it: the transport's errors say no header's value, and this module's say what was asked and
what Proxmox said.

Reads only: the writes (clone, a configuration's write, a task followed to its end) are card B4's, the guest agent's
calls card B6's. Each read returns a frozen record of answers.py, built by a parsing function there. Every name and ID
that goes into a path is held to the form Proxmox gives it first, so that none can reach another path or a query.
Blocking, and safe to call from several threads, as the transport is.
"""

from __future__ import annotations

import dataclasses
import re
import threading
import urllib.parse
from collections.abc import Mapping
from typing import Any

from ..checks import proxmox_name
from ..schema.patterns import full
from ..secret import Secret
from ..text import one_line
from ..vendors.transport import PeerRefused, Transport, Untrusted
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
    TaskLog,
    TaskStatus,
    Version,
    VmConfig,
)

__all__ = ["Denied", "Malformed", "Proxmox", "ProxmoxError", "Refused", "Token", "TooOld", "Unverified"]

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

    def _ask(self, path: str, query: Mapping[str, str] | None = None) -> Any:
        """GET path, carrying the token: the answer's data, or the client's refusal. Every refusal is raised after the
        handler that caught its cause has ended, so it carries no cause."""
        asked = f"GET {path}"
        failure: ProxmoxError | None = None
        try:
            answer = self._transport.request(
                "GET",
                path,
                headers={"Authorization": (f"PVEAPIToken={self._token.id}=", self._token.secret)},
                query=query,
            )
        except Untrusted as error:
            if not self._anchored:
                raise  # the computer's certificate file: the transport's sentence names tls_ca_file
            failure = Unverified(f"it does not verify against the host's anchor: {error.reason}")
        except PeerRefused:
            failure = Unverified("its SHA-256 fingerprint is not the one recorded for the host")
        if failure is not None:
            raise failure
        if answer.status in (401, 403):
            raise Denied(answer.status, asked)
        if answer.status != 200:
            raise Refused(answer.status, answer.reason, asked)
        return answers.data(answer.body, asked)

    def _get(self, path: str, query: Mapping[str, str] | None = None) -> Any:
        """GET path once the host's version is known to be one the engine speaks to."""
        self.version()
        return self._ask(path, query)

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
