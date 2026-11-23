"""What Proxmox answers to the client's reads, and to its writes that start a task, as small frozen records, each built
by a plain parsing function (docs/architecture/engine.md §16.5).

Proxmox's JSON is loose: 0 and 1 for booleans, numbers sent as strings, keys that come and go by version, and some
values that a person typed into Proxmox's page. So each function here takes what it knows and keeps the rest as it was
sent, frozen (a mapping read-only, a list a tuple), never refusing an answer for a key it does not know; it refuses,
as Malformed, only an answer without what the record cannot be without, or nested deeper than MOST_DEPTH, which no
answer of Proxmox's is, so that what a host sends is never a RecursionError. A guest agent's answer is the one
exception: the VM chose all of it, so of it only the plain values a network card and its addresses have are kept, and
nothing the VM nests deeper. A VM's MAC, its SMBIOS UUID and its tags are read by today's parsers in
lib/claude_vm/proxmox.py, as they stand. No pydantic: an answer is the API's, the product's own records are another
workstream's.

Standard library only, and nothing read or written.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
import types
from collections.abc import Mapping
from typing import Any

from .. import proxmox
from ..text import one_line

# A Proxmox task's ID, as PVE::Tools::upid_decode reads one: its node, process, its start and its kind, the guest and
# the user; never a character that would end a path's part, or start its query.
UPID = re.compile(
    r"UPID:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?:[0-9A-Fa-f]{8}:[0-9A-Fa-f]{8,9}:[0-9A-Fa-f]{8}"
    r":[^:\s/?#%]+:[^:\s/?#%]*:[^:\s/?#%]+:"
)


class ProxmoxError(Exception):
    """One of the client's refusals, in its own words."""


class Malformed(ProxmoxError):
    """Proxmox answered 200 with something that is not the answer asked for."""


# How deep the values of an answer kept as it was sent may nest: far deeper than any answer of Proxmox's, and far short
# of Python's recursion limit, which json.loads, in C, lets an answer reach before _frozen would meet it.
MOST_DEPTH = 100


def _frozen(value: Any, depth: int = 0) -> Any:
    """value with every mapping in it read-only and every list a tuple; Malformed where it nests deeper than
    MOST_DEPTH, never a RecursionError."""
    if depth > MOST_DEPTH:
        raise Malformed(f"Proxmox answered with values nested more than {MOST_DEPTH} deep")
    if isinstance(value, Mapping):
        return types.MappingProxyType({str(key): _frozen(one, depth + 1) for key, one in value.items()})
    if isinstance(value, list | tuple):
        return tuple([_frozen(one, depth + 1) for one in value])
    return value


def _no_number(text: str) -> float:
    raise ValueError(f"{text} is no number")


def _finite(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):
        raise ValueError("a number too large")
    return number


def data(body: bytes, asked: str) -> Any:
    """The "data" of an answer's JSON body, as Proxmox wraps every answer; Malformed for a body that is no such JSON.
    asked names the request in the error."""
    try:
        whole = json.loads(body, parse_float=_finite, parse_constant=_no_number)
    except (RecursionError, UnicodeDecodeError, ValueError):
        whole = None
    if not isinstance(whole, dict) or "data" not in whole:
        raise Malformed(f"Proxmox answered {asked} with something that is not its JSON")
    return whole["data"]


def _text(value: Any) -> str:
    """A value Proxmox gives as a string, or a number it may give for one; "" for anything else."""
    if isinstance(value, str):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return ""


def _int(value: Any) -> int | None:
    """A whole number, given as one or as a string of digits; None for anything else."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]{1,30}", value.strip()):
        return int(value.strip())
    return None


def _float(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _flag(value: Any) -> bool:
    """A boolean as Proxmox gives one: 1, "1", true or "true"; anything else is false."""
    if isinstance(value, bool):
        return value
    return _text(value).strip().lower() in ("1", "true", "yes", "on")


def _mapping(value: Any, asked: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Malformed(f"Proxmox answered {asked} with something that is not one record")
    return value


def _list(value: Any, asked: str) -> list[Any]:
    if not isinstance(value, list):
        raise Malformed(f"Proxmox answered {asked} with something that is not a list")
    return value


# ------------------------------------------------------------------------------------------------- /version ----


@dataclasses.dataclass(frozen=True)
class Version:
    """GET /version: the version as Proxmox gives it ("9.2.21"), its release ("9.2"), and its major."""

    version: str
    release: str
    major: int


def version(value: Any) -> Version:
    entry = _mapping(value, "version")
    said = _text(entry.get("version")).strip()
    major = re.match(r"([0-9]{1,4})(?:\.|\Z)", said)
    if major is None:
        # Nothing of what it said: the words a host sends here are no refusal's, and are never struck of the token.
        raise Malformed("Proxmox answered version with no version it reads as one")
    return Version(said, _text(entry.get("release")), int(major.group(1)))


# ------------------------------------------------------------------------------------ /cluster/resources ----


@dataclasses.dataclass(frozen=True)
class Resource:
    """One entry of /cluster/resources, or a pool's member: its id and type (qemu, lxc, storage, node, pool, sdn), its
    node, its status as the cluster last polled it, a guest's name, VMID, template flag, tags and pool, a storage's
    name, its figures where the token may see them, and the entry as it was sent. The status lags what a run changed by
    up to ten seconds: a run reads a VM it changed with the client's current, never from here."""

    id: str
    type: str
    node: str
    status: str
    name: str
    vmid: int | None
    template: bool
    tags: tuple[str, ...]
    pool: str
    storage: str
    maxcpu: int | None
    maxmem: int | None
    mem: int | None
    maxdisk: int | None
    disk: int | None
    uptime: int | None
    entry: Mapping[str, Any]


def resource(value: Any) -> Resource:
    entry = _mapping(value, "a listing")
    return Resource(
        id=_text(entry.get("id")),
        type=_text(entry.get("type")),
        node=_text(entry.get("node")),
        status=_text(entry.get("status")),
        name=_text(entry.get("name")),
        vmid=_int(entry.get("vmid")),
        template=_flag(entry.get("template")),
        tags=tuple(proxmox.proxmox_tags(_text(entry.get("tags")))),
        pool=_text(entry.get("pool")),
        storage=_text(entry.get("storage")),
        maxcpu=_int(entry.get("maxcpu")),
        maxmem=_int(entry.get("maxmem")),
        mem=_int(entry.get("mem")),
        maxdisk=_int(entry.get("maxdisk")),
        disk=_int(entry.get("disk")),
        uptime=_int(entry.get("uptime")),
        entry=_frozen(entry),
    )


def resources(value: Any) -> tuple[Resource, ...]:
    return tuple(resource(entry) for entry in _list(value, "the cluster's listing"))


# ---------------------------------------------------------------------------------------------- /pools ----


@dataclasses.dataclass(frozen=True)
class Pool:
    """GET /pools?poolid=: a pool, its comment and its members, each as the cluster's listing has it."""

    poolid: str
    comment: str
    members: tuple[Resource, ...]


def pool(value: Any, poolid: str) -> Pool:
    listed = value if isinstance(value, Mapping) else _list(value, "a pool")
    found = [listed] if isinstance(listed, Mapping) else [one for one in listed if isinstance(one, Mapping)]
    entry = next((one for one in found if _text(one.get("poolid")) == poolid), None)
    if entry is None:
        raise Malformed(f"Proxmox answered the pool {poolid} with no pool of that name")
    members = tuple(resource(one) for one in _list(entry.get("members", []), "a pool's members"))
    return Pool(poolid, _text(entry.get("comment")), members)


# ------------------------------------------------------------------------------------------ a VM's config ----


@dataclasses.dataclass(frozen=True)
class VmConfig:
    """GET .../config: a VM's configuration with its digest, which a write sends back; its name, notes, tags, template
    flag and lock; its identity, the SMBIOS UUID Proxmox gives every VM it creates or clones ("" for none); the MAC of
    the network card asked for ("" for none); and every key as it was sent."""

    node: str
    vmid: int
    digest: str
    name: str
    description: str
    tags: tuple[str, ...]
    template: bool
    lock: str
    identity: str
    mac: str
    values: Mapping[str, Any]


def config(value: Any, node: str, vmid: int, nic: str) -> VmConfig:
    entry = _mapping(value, "a VM's configuration")
    digest = _text(entry.get("digest"))
    if not re.fullmatch(r"[0-9a-f]{40,64}", digest):
        raise Malformed(f"Proxmox answered the configuration of VM {vmid} with no digest")
    values = {key: one for key, one in entry.items() if key != "digest"}
    return VmConfig(
        node=node,
        vmid=vmid,
        digest=digest,
        name=_text(values.get("name")),
        description=_text(values.get("description")),
        tags=tuple(proxmox.proxmox_tags(_text(values.get("tags")))),
        template=_flag(values.get("template")),
        lock=_text(values.get("lock")),
        identity=proxmox.smbios_uuid(values),
        mac=proxmox.net_mac(_text(values.get(nic)), ""),
        values=_frozen(values),
    )


# ----------------------------------------------------------------------------------- a VM's own state now ----


@dataclasses.dataclass(frozen=True)
class Current:
    """GET .../status/current: a VM's own state now, never stale as the listing's is - running or stopped, QEMU's own
    word for it (running, paused, prelaunch), its lock, name and uptime - and the entry as it was sent."""

    node: str
    vmid: int
    status: str
    qmpstatus: str
    lock: str
    name: str
    uptime: int | None
    entry: Mapping[str, Any]


def current(value: Any, node: str, vmid: int) -> Current:
    entry = _mapping(value, "a VM's state")
    status = _text(entry.get("status"))
    if not status:
        raise Malformed(f"Proxmox answered the state of VM {vmid} with no status")
    return Current(
        node=node,
        vmid=vmid,
        status=status,
        qmpstatus=_text(entry.get("qmpstatus")),
        lock=_text(entry.get("lock")),
        name=_text(entry.get("name")),
        uptime=_int(entry.get("uptime")),
        entry=_frozen(entry),
    )


# --------------------------------------------------------------------------------------- /cluster/nextid ----

# The VMIDs Proxmox gives: 100 to 999999999.
LEAST_VMID, MOST_VMID = 100, 999_999_999


def next_id(value: Any) -> int:
    """GET /cluster/nextid: the lowest VMID free when it was asked, which another run may take before this one."""
    found = _int(value)
    if found is None or not LEAST_VMID <= found <= MOST_VMID:
        raise Malformed("Proxmox answered the next free VMID with no VMID")
    return found


# -------------------------------------------------------------------------------------------- a node now ----


@dataclasses.dataclass(frozen=True)
class NodeStatus:
    """GET /nodes/<node>/status: a node's CPU threads, its memory in bytes, in all and in use, how long it has run,
    and the entry as it was sent; None for a figure it did not give."""

    node: str
    cpus: int | None
    memory_total: int | None
    memory_used: int | None
    uptime: int | None
    entry: Mapping[str, Any]


def node_status(value: Any, node: str) -> NodeStatus:
    entry = _mapping(value, "a node's status")
    cpuinfo = entry.get("cpuinfo") if isinstance(entry.get("cpuinfo"), Mapping) else {}
    memory = entry.get("memory") if isinstance(entry.get("memory"), Mapping) else {}
    return NodeStatus(
        node=node,
        cpus=_int(cpuinfo.get("cpus")),
        memory_total=_int(memory.get("total")),
        memory_used=_int(memory.get("used")),
        uptime=_int(entry.get("uptime")),
        entry=_frozen(entry),
    )


# ------------------------------------------------------------------------------------------------- tasks ----


# The writes that answer a task, by the client's name for each.
TASK_KINDS = frozenset({"clone", "resize", "start", "shutdown", "stop", "template", "delete"})


@dataclasses.dataclass(frozen=True)
class Task:
    """A task a write started: the node it runs on, its UPID, the kind of write (TASK_KINDS) and the VM it is for - a
    clone's, the new VM's VMID - and a clone's source, the VM or template it copies, whose lock its task may meet (None
    for any other). The client's follow() polls it to its end."""

    node: str
    upid: str
    kind: str
    vmid: int
    source: int | None = None


def task(value: Any, node: str, kind: str, vmid: int, source: int | None = None) -> Task:
    """A write's answer, a UPID, as the Task of kind for VM vmid on node, a clone's with its source; Malformed for
    anything else."""
    if kind not in TASK_KINDS:
        raise ValueError(f"a task's kind is one of {', '.join(sorted(TASK_KINDS))}")
    if source is not None and kind != "clone":
        raise ValueError("a clone's task alone names a source")
    if not isinstance(value, str) or not UPID.fullmatch(value):
        raise Malformed(f"Proxmox answered the {kind} of VM {vmid} with no task's ID")
    return Task(node, value, kind, vmid, source)


# How a task Proxmox counts a success ended with warnings: its worker logged them (WARN: ...), and its exit status says
# how many, as PVE::Tools::upid_read_status reads the log's last line, TASK WARNINGS: <n>. Today's community.proxmox
# takes it as a success (api_task_ok). A21 confirms the words on a host.
WARNINGS = re.compile(r"WARNINGS: ([1-9][0-9]{0,8})")


@dataclasses.dataclass(frozen=True)
class TaskStatus:
    """GET /nodes/<node>/tasks/<upid>/status: whether a task runs, and once stopped, how it ended - "OK", "WARNINGS:
    <n>" for one that succeeded with warnings, or the error Proxmox gives, made plain - how many warnings it logged, its
    kind, and the entry as it was sent; and lines, the last lines of its log, which the client's follow() keeps for a
    task that ended with warnings, so that a workflow can say them (none otherwise)."""

    node: str
    upid: str
    status: str
    exitstatus: str
    type: str
    entry: Mapping[str, Any]
    warnings: int = 0
    lines: tuple[str, ...] = ()

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def ok(self) -> bool:
        """Whether it ended a success: OK, or with warnings, which Proxmox counts one."""
        return self.status == "stopped" and (self.exitstatus == "OK" or self.warnings > 0)


def task_status(value: Any, node: str, upid: str) -> TaskStatus:
    entry = _mapping(value, "a task's status")
    status = _text(entry.get("status"))
    if status not in ("running", "stopped"):
        raise Malformed("Proxmox answered a task's status with neither running nor stopped")
    exitstatus = one_line(_text(entry.get("exitstatus")), 400)
    warned = WARNINGS.fullmatch(exitstatus) if status == "stopped" else None
    return TaskStatus(
        node=node,
        upid=upid,
        status=status,
        exitstatus=exitstatus,
        type=_text(entry.get("type")),
        entry=_frozen(entry),
        warnings=int(warned.group(1)) if warned else 0,
    )


@dataclasses.dataclass(frozen=True)
class TaskLog:
    """GET /nodes/<node>/tasks/<upid>/log: the lines asked for, from the line start (0, the first), each made one line
    of plain text."""

    node: str
    upid: str
    start: int
    lines: tuple[str, ...]


def task_log(value: Any, node: str, upid: str, start: int) -> TaskLog:
    lines = []
    for entry in _list(value, "a task's log"):
        if isinstance(entry, Mapping):
            lines.append(one_line(_text(entry.get("t")), None))
    return TaskLog(node, upid, start, tuple(lines))


# ------------------------------------------------------------------------------- storages and bridges ----


@dataclasses.dataclass(frozen=True)
class Storage:
    """One of a node's storages, as GET /nodes/<node>/storage lists those the token may see: its name and type, what
    it holds, whether it is active, enabled and shared, its bytes free, in all and in use, and the entry as sent."""

    node: str
    storage: str
    type: str
    content: tuple[str, ...]
    active: bool
    enabled: bool
    shared: bool
    avail: int | None
    total: int | None
    used: int | None
    entry: Mapping[str, Any]


def storages(value: Any, node: str) -> tuple[Storage, ...]:
    found = []
    for entry in _list(value, "a node's storages"):
        entry = _mapping(entry, "a node's storages")
        found.append(
            Storage(
                node=node,
                storage=_text(entry.get("storage")),
                type=_text(entry.get("type")),
                content=tuple(part for part in _text(entry.get("content")).split(",") if part),
                active=_flag(entry.get("active")),
                enabled=_flag(entry.get("enabled", 1)),
                shared=_flag(entry.get("shared")),
                avail=_int(entry.get("avail")),
                total=_int(entry.get("total")),
                used=_int(entry.get("used")),
                entry=_frozen(entry),
            )
        )
    return tuple(found)


@dataclasses.dataclass(frozen=True)
class Bridge:
    """One of a node's bridges, as GET /nodes/<node>/network lists those the token may see: its name, its type, whether
    it is active and starts with the node, and the entry as sent."""

    node: str
    iface: str
    type: str
    active: bool
    autostart: bool
    entry: Mapping[str, Any]


def bridges(value: Any, node: str) -> tuple[Bridge, ...]:
    found = []
    for entry in _list(value, "a node's bridges"):
        entry = _mapping(entry, "a node's bridges")
        found.append(
            Bridge(
                node=node,
                iface=_text(entry.get("iface")),
                type=_text(entry.get("type")),
                active=_flag(entry.get("active")),
                autostart=_flag(entry.get("autostart")),
                entry=_frozen(entry),
            )
        )
    return tuple(found)


# ------------------------------------------------------------------------------------- /access/permissions ----


@dataclasses.dataclass(frozen=True)
class Permissions:
    """GET /access/permissions?path=: the privileges the token holds at one ACL path, each with whether it reaches
    the paths below (Proxmox's propagate flag)."""

    path: str
    privileges: Mapping[str, bool]


def permissions(value: Any, path: str) -> Permissions:
    entry = _mapping(value, "the token's permissions")
    held = entry.get(path, {})
    held = held if isinstance(held, Mapping) else {}
    return Permissions(path, types.MappingProxyType({str(name): _flag(flag) for name, flag in held.items()}))


# ------------------------------------------------------------------------------------ the guest agent's cards ----


@dataclasses.dataclass(frozen=True)
class Interfaces:
    """GET .../agent/network-get-interfaces: what a VM's guest agent reported of its network cards. The agent runs
    inside the VM, so all of it is the VM's to choose: Proxmox's wrapper is held to its shape, and of the agent's answer
    each card that is a mapping is kept with what a card has (CARD), its addresses each with what an address has
    (ADDRESS), every value as it came where it is a plain one, and nothing else, none where the agent sent no list: what
    a VM nests deeper never reaches a record. agent.address reads the VM's address from them."""

    node: str
    vmid: int
    interfaces: tuple[Mapping[str, Any], ...]


# What a network card of the agent's answer has (QEMU's GuestNetworkInterface), and each of its addresses
# (GuestIpAddress): what Interfaces keeps of them.
CARD = ("name", "hardware-address")
ADDRESS = ("ip-address", "ip-address-type", "prefix")


def _plain(entry: Mapping[Any, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Of entry, keys whose values are plain - a str, a number, a boolean or null - as they came."""
    return {key: entry[key] for key in keys if key in entry and isinstance(entry[key], str | int | float | type(None))}


def _card(card: Mapping[Any, Any]) -> Mapping[str, Any]:
    kept = _plain(card, CARD)
    addresses = card.get("ip-addresses")
    if isinstance(addresses, list):
        kept["ip-addresses"] = tuple(
            types.MappingProxyType(_plain(one, ADDRESS)) for one in addresses if isinstance(one, Mapping)
        )
    return types.MappingProxyType(kept)


def interfaces(value: Any, node: str, vmid: int) -> Interfaces:
    entry = _mapping(value, "a guest agent's network cards")
    found = entry.get("result")
    cards = tuple(_card(card) for card in found if isinstance(card, Mapping)) if isinstance(found, list) else ()
    return Interfaces(node=node, vmid=vmid, interfaces=cards)


# ------------------------------------------------------------------------------------------- any other read ----


@dataclasses.dataclass(frozen=True)
class Read:
    """Any other GET's answer, by its path: its data as it was sent, frozen. For the reads `status` adds."""

    path: str
    data: Any


def read(value: Any, path: str) -> Read:
    return Read(path, _frozen(value))
