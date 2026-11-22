"""The typed client of the Proxmox VE API: the reads every workflow makes, and its writes (docs/architecture/engine.md
§16.5).

One class, Proxmox, sends the engine's token to Proxmox, and it alone writes the token's header: the engine's one token
on the fleet's pool, a Token whose secret is a Secret, handed to the transport as parts of the header's value and
revealed there alone. It goes only on a connection pin.py made, whose certificate verified against what the host keeps
before anything was written; a certificate that did not is Unverified, its remedy told by OpenSSL's verify code, never
by its words. The first request a client sends is GET /version, which needs no privilege: a host below
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
applies again, EDITS times at most. A PUT or a POST to a configuration's path that carries no digest is refused before
it is sent, in the one function every request passes. A clone, a resize, a start, a shutdown, a stop, a conversion to a
template and a deletion each answer a Task, which follow() polls to its end within a deadline of its kind, or until the
run that follows it stops (Stopped, the task going on on Proxmox); a task that ended OK, or with warnings, which Proxmox
counts a success, is a success, its warnings' lines kept for the workflow to say; one that ended otherwise is
TaskFailed with the last lines of its log, one whose VMID another took is Exists, one that found the VM locked is
Locked. Proxmox answers every refusal of a write with a 500, so its words tell them apart: a lock (Locked, which a
workflow may try again), a configuration's file lock among them, a stale digest, a clone's VMID taken. A deletion
takes the VM and its disks alone: never purge, unless a caller asks for it, which would take the VM out of the host's
backup jobs, replication and HA as well. The VMID a workflow asks for is its own to choose, the record's text is its
own to write, and a sequence of calls is its own to make: none of them is this module's.

The guest agent's one call, agent_interfaces, asks what a started VM's agent reports of its network cards, on a
transport of its own whose read limit is AGENT_READ, shorter than every other read's, since Proxmox waits on the VM for
the answer: agent.py reads the VM's address from it, and waits for one. A VM that is not running is NotRunning, and one
with no guest agent configured NoAgent, each told by Proxmox's words on its 500, as the writes' refusals are; what the
agent reports is the VM's to choose, so of it only the plain values a network card and its addresses have are kept
(answers.Interfaces).

Each read and each write returns a frozen record of answers.py, built by a parsing function there, or nothing. Every
name and ID that goes into a path or a write is held to the form Proxmox gives it first, so that none can reach another
path or a query, and a configuration's write sends only the keys the engine's table covers for it (privileges.keys),
each under the row of the privilege Proxmox checks it with. Blocking, and safe to call from several threads, as the
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
from ..vendors.transport import (
    UNKNOWN_ISSUER,
    Answer,
    PeerRefused,
    TimedOut,
    Transport,
    TransportError,
    Unknown,
    Untrusted,
)
from . import answers, pin, privileges
from .answers import (
    UPID,
    Bridge,
    Current,
    Interfaces,
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
    "NoAgent",
    "NotRunning",
    "Proxmox",
    "ProxmoxError",
    "Refused",
    "Stopped",
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
# How long the guest agent's calls wait for each read, in seconds: N-14's 10 s, where every other read waits 30, since
# Proxmox asks the VM's agent before it answers, and an agent still starting may not answer at all.
AGENT_READ = 10.0
# A VM that is not running, which Proxmox says of a guest agent's call with a 500 before it asks the agent
# (qemu-server's PVE/QemuServer/Agent.pm, assert_agent_available): the VM's ID in its words. Read as the whole of
# Proxmox's words, never found within them: what follows "failed - " in an agent's other refusals is where a VM's words
# could be.
NOT_RUNNING = re.compile(r"VM ([0-9]+) is not running")
# A VM whose configuration turns no guest agent on, which Proxmox says before it asks whether the VM runs: no agent will
# answer, so waiting for one cannot help. Read as the whole of Proxmox's words too.
NO_AGENT = re.compile(r"No QEMU guest agent configured")
# OpenSSL's verify codes for a certificate that does not name the address it was reached at:
# X509_V_ERR_HOSTNAME_MISMATCH and X509_V_ERR_IP_ADDRESS_MISMATCH. What to change is the address, or what the host
# keeps: never the certificate's roots.
MISNAMED = frozenset({62, 64})
# OpenSSL's verify codes for a certificate out of its dates: X509_V_ERR_CERT_NOT_YET_VALID and
# X509_V_ERR_CERT_HAS_EXPIRED. What to change is the certificate on the host, or a clock: never what the host keeps.
NOT_YET_VALID, EXPIRED = 9, 10

# ------------------------------------------------------------------------------------------- the writes ----

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
# A VM's configuration file that another task held locked for longer than a write waits for it (lock_config's ten
# seconds, PVE::Tools::lock_file_full's words), with the VM's ID: Locked, its lock FILE. For a clone the same words with
# its new VMID say another run's clone holds that VMID: Exists, which is looked for first.
FILE_LOCK = re.compile(r"can't lock file '/var/lock/qemu-server/lock-([0-9]+)\.conf' - got timeout")
FILE = "file"
# A clone whose new VMID is taken, each with that VMID: the call's own check (and its worker's once the winner's
# clone has ended), the worker's timeout on the new VM's lock while the winner's clone still runs, and
# create_and_lock_config's where a container or another node's VM holds it.
TAKEN = (
    re.compile(r"unable to create VM ([0-9]+): config file already exists"),
    FILE_LOCK,
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
    followed: the token goes to the address it was given alone. A read of a VM that is not there is one too, Proxmox's
    500 naming its configuration's file: a workflow knows a VM is gone from resources("vm"), never from these words."""

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
    """Proxmox holds the VM locked - a clone, a deletion or a backup runs on it, or another task held its
    configuration's file longer than the write waits - and refused what was asked, which may be tried again once that
    has ended. lock is Proxmox's word for it, or FILE for its file."""

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


class Stopped(ProxmoxError):
    """The run stopped while a task was followed: follow() returned before the task's end, which Proxmox goes on with,
    so the workflow reads the VM to know where it was left."""

    def __init__(self, task: Task) -> None:
        self.task = task
        super().__init__(
            f"Proxmox's {task.kind} task for VM {task.vmid} still runs on Proxmox: the run stopped following it, so"
            " read the VM to know where it was left"
        )


class NotRunning(ProxmoxError):
    """The VM is not running, so its guest agent cannot answer: a stopped VM is left stopped, and not waited on."""

    def __init__(self, vmid: int) -> None:
        self.vmid = vmid
        super().__init__(f"VM {vmid} is not running, so its guest agent cannot answer: it is not waited for")


class NoAgent(ProxmoxError):
    """The VM's configuration turns no QEMU guest agent on, so none will answer and no address can be read from it:
    it is not waited for. Its agent setting, which a clone takes from its template, turns it on."""

    def __init__(self, vmid: int) -> None:
        self.vmid = vmid
        super().__init__(
            f"VM {vmid} has no QEMU guest agent configured, so none can report its address: it is not waited for. Its"
            " configuration's agent setting, which a clone takes from its template, must be enabled=1, and its guest"
            " must run qemu-guest-agent"
        )


class TooOld(ProxmoxError):
    """The host's Proxmox VE is older than 9, which the engine does not speak to. version is the host's as it may be
    said: one line, the token's secret struck from it, as from every word of a host's an error says."""

    def __init__(self, version: str) -> None:
        self.version = version
        super().__init__(
            f"this Proxmox host runs Proxmox VE {version}, and the engine speaks to Proxmox VE {LEAST_MAJOR} or later"
            " only: upgrade the host first"
        )


class Unverified(ProxmoxError):
    """The host's certificate is not what the host keeps: it does not verify against its anchor, or the computer's
    certificates where it keeps none, or it is not the node's certificate whose fingerprint was recorded. Nothing was
    sent; it is never tried again without the check. reason is what OpenSSL, or the pin, said; code is OpenSSL's verify
    code (0 for the pin's refusal), which alone tells the remedy, never the words:

    - a certificate that does not name the address it was reached at (MISNAMED): the address is what to change, to one
      the certificate holds, or the node's own certificate is what to keep, which is held to its fingerprint and not to
      a name, so that a host reached at a tailnet's address or through a forwarded port is reached;
    - one that has expired (EXPIRED), or one that issued it: it is renewed on the host, and where the host keeps an
      anchor, aw init then reads the new one and asks before it keeps it; one not valid yet (NOT_YET_VALID): a clock
      is behind, this computer's, whose time now the sentence says, or the host's when the certificate was made;
    - any other refusal, with an anchor or with none: aw init, which reads the host's own certificate and asks before
      it keeps one as the host's anchor; and where no anchor is kept and the issuer is unknown, a root of the person's
      own, added with tls_ca_file, comes second, never alone - the root of a Proxmox host's certificate is almost
      always the host's own CA, whose key is on the host, and a root added there is trusted for every vendor too."""

    def __init__(self, reason: str, code: int = 0, anchored: bool = True, address: str = "") -> None:
        self.reason = one_line(reason, 200)
        self.code = code
        self.anchored = anchored
        renewed = (
            ", then aw init reads the host's new one and asks before it keeps it in the old one's place"
            if anchored
            else ""
        )
        if code in MISNAMED:
            said = (
                f"the Proxmox host's certificate does not name the address it was reached at, {one_line(address, 100)},"
                f" the one the host's record gives ({self.reason}): give the host a name or address its certificate"
                " holds - on a tailnet, the node's own name, which MagicDNS resolves there - or have aw init keep the"
                " node's own certificate as the host's anchor, which is held to its fingerprint and not to a name"
            )
        elif code == EXPIRED:
            said = (
                f"the Proxmox host's certificate, or one that issued it, has expired ({self.reason}): renew it on the"
                " host" + renewed
            )
        elif code == NOT_YET_VALID:
            now = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            said = (
                f"the Proxmox host's certificate, or one that issued it, is not valid yet ({self.reason}), and this"
                f" computer's clock says {now}: if that is behind, set this computer's clock right; if not, the host's"
                " clock was ahead when the certificate was made: renew it there" + renewed
            )
        elif anchored:
            said = (
                f"the Proxmox host's certificate is not the one kept for it ({self.reason}): if Proxmox renewed it, or"
                " the host was reinstalled, aw init reads the host's own and asks before it trusts one in its place; if"
                " not, something else may answer at that address"
            )
        else:
            said = (
                f"the Proxmox host's certificate does not verify against the computer's certificates ({self.reason}):"
                " aw init reads the host's own and asks before it keeps it as the host's anchor"
            )
            if code in UNKNOWN_ISSUER:
                said += (
                    "; or, where your company's root signed it, add that root to the file of PEM certificates the"
                    " setting tls_ca_file names, which is added to certifi's list"
                )
        super().__init__(said)


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
    """key, where it is one the engine's table covers for a configuration's write (privileges.keys): each such key has
    the row of the privilege Proxmox checks it with, which init grants, so a key outside them is refused before anything
    is sent."""
    if not isinstance(key, str) or key not in privileges.keys("write_config"):
        raise ValueError(
            "a configuration's write sends only the keys the engine's table covers for it, each under the privilege"
            " Proxmox checks it with (privileges.keys); a key outside them needs its row first"
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
    """changes as write_config's form: each key one the table covers for the write (privileges.keys), its value as
    _value makes it; delete, the keys to remove, each one of them too, comma-joined. digest is the write's own, never
    one of them."""
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


def _digest_in(form: Mapping[str, Any] | None) -> bool:
    """Whether form carries a configuration's digest, as Proxmox gives one (DIGEST): an empty one, or any other value,
    Proxmox would read as none (update_vm_api compares only a digest it was given), writing the changes whatever the
    configuration had become."""
    digest = form.get("digest") if isinstance(form, Mapping) else None
    return isinstance(digest, str) and DIGEST.fullmatch(digest) is not None


def _taken(said: str, newid: int) -> bool:
    """Whether Proxmox's words said said that the VMID newid is taken."""
    return any(int(found.group(1)) == newid for pattern in TAKEN for found in pattern.finditer(said))


def _stopped(said: str, vmid: int) -> bool:
    """Whether Proxmox's words said, whole, are that the VM vmid is not running."""
    found = NOT_RUNNING.fullmatch(said.strip())
    return found is not None and int(found.group(1)) == vmid


def _file_locked(said: str, vmid: int) -> bool:
    """Whether Proxmox's words said said that the configuration's file of the VM vmid was held locked too long."""
    return any(int(found.group(1)) == vmid for found in FILE_LOCK.finditer(said))


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
        # The host's address as its record gives it, which a certificate that does not name it is said with.
        self._address = parts.hostname or ""
        self._token = token
        self._said = f"Proxmox(token={token.id!r})"
        self._transport: Transport = pin.transport(f"{url.rstrip('/')}{API}", pinned, proxy)
        # The guest agent's calls, on connections of their own that wait AGENT_READ for each read; agent_call_most, the
        # longest one of them usefully runs, a connection made and one read waited for, which agent.py holds a poll to.
        self._agent: Transport = pin.transport(f"{url.rstrip('/')}{API}", pinned, proxy, read=AGENT_READ)
        self.agent_call_most = AGENT_READ + pin.CONNECT
        self._version: Version | None = None
        self._lock = threading.Lock()

    def __enter__(self) -> Proxmox:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close every connection kept, and both transports: nothing more is sent. A request started after is the
        transport's Closed; one still under way on another thread, as a cancelled wait leaves its poll, ends at its
        first error, at once - its socket shut down under it and closed by that thread, never closed under its read -
        never tried again and never on a new connection."""
        self._transport.close()
        self._agent.close()

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
        agent: bool = False,
    ) -> Answer:
        """method path, carrying the token, with query and form, on the guest agent's transport where agent says so:
        Proxmox's answer, whatever its status. Every request passes here, so here a PUT or a POST to a configuration's
        path with no digest in its form is refused, before anything is sent: Proxmox would write it whatever the
        configuration had become since it was read. A certificate that is not what the host keeps is Unverified, told
        by OpenSSL's verify code. Every refusal is raised after the handler that caught its cause has ended, so it
        carries no cause."""
        if method in ("PUT", "POST") and path.rstrip("/").endswith("/config") and not _digest_in(form):
            raise ValueError(
                "a write of a VM's configuration sends the digest of the read it was made from, and this one had none:"
                " nothing was sent"
            )
        failure: ProxmoxError | None = None
        try:
            answer = (self._agent if agent else self._transport).request(
                method,
                path,
                headers={"Authorization": (f"PVEAPIToken={self._token.id}=", self._token.secret)},
                query=query,
                form=form,
                deadline=deadline,
            )
        except Untrusted as error:
            failure = Unverified(error.reason, error.code, self._anchored, self._address)
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

    def _ask(
        self, path: str, query: Mapping[str, str] | None = None, deadline: float | None = None, agent: bool = False
    ) -> Any:
        """GET path, carrying the token, on the guest agent's transport where agent says so: the answer's data, or the
        client's refusal."""
        asked = f"GET {path}"
        answer = self._request("GET", path, query, deadline=deadline, agent=agent)
        if answer.status in (401, 403):
            raise Denied(answer.status, asked)
        if answer.status != 200:
            raise Refused(answer.status, self._words(answer.reason, 200), asked)
        return answers.data(answer.body, asked)

    def _get(
        self, path: str, query: Mapping[str, str] | None = None, deadline: float | None = None, agent: bool = False
    ) -> Any:
        """GET path once the host's version is known to be one the engine speaks to, the version asked first where it
        is not known yet: both within deadline seconds together, where one is given, so that a first call is held to
        its caller's deadline too."""
        given, end = deadline, None if deadline is None else time.monotonic() + deadline
        self.version(deadline)
        if end is not None:
            deadline = end - time.monotonic()
            if deadline <= 0:
                raise TimedOut(f"no answer within the call's deadline of {given:g} s")
        return self._ask(path, query, deadline, agent)

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
        a clone's Exists (for newid), DigestMismatch, or Locked by vmid's lock or its configuration's file lock; else
        Refused. The transport's Unknown, a write that left with no answer, passes as it is: it is never sent
        again."""
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
            if _file_locked(said, vmid):
                raise Locked(vmid, FILE, asked)
        raise Refused(answer.status, said, asked)

    # ------------------------------------------------------------------------------------------------- reads ----

    def version(self, deadline: float | None = None) -> Version:
        """GET /version, asked until a client has it, within deadline seconds where given: the first request it sends.
        No lock is held across the request, so that a call made while another asks waits for none but its own, within
        its own deadline: two first calls may each ask it, and the first answer kept is the one every later call reads.
        Raises TooOld for a host below Proxmox VE 9, each time it is asked, with nothing more sent."""
        with self._lock:
            found = self._version
        if found is None:
            asked = answers.version(self._ask("version", deadline=deadline))
            with self._lock:
                if self._version is None:
                    self._version = asked
                found = self._version
        if found.major < LEAST_MAJOR:
            raise TooOld(self._words(found.version, 40))
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

    def task_log(self, node: str, upid: str, start: int = 0, limit: int = 50, deadline: float | None = None) -> TaskLog:
        """GET a task's log, limit lines from line start (0, the first), each made plain, within deadline seconds where
        given."""
        node, upid = _node(node), _upid(upid)
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise ValueError("a log is read from a line number of 0 or more")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= 10000:
            raise ValueError("a log is read 1 to 10000 lines at a time")
        if deadline is not None:
            deadline = _seconds(deadline)
        query = {"start": str(start), "limit": str(limit)}
        return answers.task_log(
            self._get(f"nodes/{node}/tasks/{upid}/log", query, deadline=deadline), node, upid, start
        )

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

    # ------------------------------------------------------------------------------------------- the guest agent ----

    def agent_interfaces(self, node: str, vmid: int, deadline: float | None = None) -> Interfaces:
        """GET what a VM's guest agent reports of its network cards (network-get-interfaces), on the agent's transport,
        whose reads wait AGENT_READ, within deadline seconds where given: the Interfaces, each card's plain values as
        the VM's agent reported them, which agent.address reads the VM's address from. Raises NotRunning for a VM that
        is not running, Denied for a token without the agent's privilege, and Refused while the agent does not answer
        yet (Proxmox's 500, "QEMU guest agent is not running"); NoAgent for a VM whose configuration turns no agent on,
        which Proxmox says before whether it runs."""
        node, vmid = _node(node), _vmid(vmid)
        if deadline is not None:
            deadline = _seconds(deadline)
        failure: ProxmoxError | None = None
        try:
            found = self._get(f"nodes/{node}/qemu/{vmid}/agent/network-get-interfaces", deadline=deadline, agent=True)
        except Refused as refused:
            if refused.status != REFUSED_STATUS:
                raise
            if NO_AGENT.fullmatch(refused.reason.strip()):
                failure = NoAgent(vmid)
            elif _stopped(refused.reason, vmid):
                failure = NotRunning(vmid)
            else:
                raise
        if failure is not None:
            raise failure
        return answers.interfaces(found, node, vmid)

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
        a call returned: a module once returned the clone's source's VMID for a clone that made nothing. Where the call
        left and no answer came, the transport's Unknown: the clone may be running at newid with its UPID unknown, the
        new VM's configuration holding only its lock (clone) until the worker ends. It is settled by reading newid
        again - resources("vm") for whether a VM is there, config() for its lock - until that lock is gone, and never
        by a clone under another VMID before then (card G3). Locked is the source's: a clone waits on no lock of its
        own VM but newid's, which is Exists."""
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
        return answers.task(upid, node, "clone", newid, source)

    def write_config(self, node: str, vmid: int, changes: Mapping[str, Any], digest: str) -> None:
        """PUT changes to a VM's configuration with digest, the one the read they were made from gave, in one call:
        Proxmox writes them only while the configuration is still that, under the VM's lock. changes' keys are those
        privileges.keys gives the write (delete, a list of them, removes those); tags are a list of tags, sent
        comma-joined; sshkeys is the keys' text, one a line, sent percent-encoded whole, and read back as Proxmox keeps
        it, encoded. Raises DigestMismatch where another writer changed it since, nothing written; Locked where a clone,
        a deletion or a backup holds the VM; and the transport's Unknown where the call left and no answer came: read
        the configuration to know, since it is never sent again."""
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

    def delete(self, node: str, vmid: int, purge: bool = False) -> Task:
        """DELETE a VM with its disks, those its configuration no longer lists but that carry its VMID too, as today's
        role deletes one (destroy-unreferenced-disks): the Task. Nothing else is changed on the host: without purge,
        which is sent only where a caller asks for it, Proxmox leaves its backup jobs, replication and HA as they are,
        and refuses to delete a VM a person put under HA or replication, as it refuses today's deletion; with it, the
        VMID is taken out of each of them too, a change outside the VM."""
        node, vmid = _node(node), _vmid(vmid)
        if not isinstance(purge, bool):
            raise TypeError("purge is True or False")
        query = {"destroy-unreferenced-disks": "1", **({"purge": "1"} if purge else {})}
        return answers.task(self._send("DELETE", f"nodes/{node}/qemu/{vmid}", vmid, query=query), node, "delete", vmid)

    # ----------------------------------------------------------------------------------------------- tasks ----

    def follow(self, task: Task, deadline: float | None = None, stop: threading.Event | None = None) -> TaskStatus:
        """Poll task's status until it ends, within deadline seconds (DEADLINES' for its kind where None), and never
        longer: each poll is held to what is left of it. stop, the run's Context.stopping where it is given, is read
        before each poll and waited on between them: once it is set, Stopped is raised and the task goes on on Proxmox,
        unfollowed; a poll already sent is never cut, and ends within its own limit; one that fails once it is set (its
        client closed under it) is Stopped too. A phase that must finish, a deletion inside a shield, passes none.
        Returns the status of a task that ended a success: OK, or WARNINGS: <n>, which Proxmox counts one (its worker
        logged a warning and went on), with the last lines of its log in lines then, for the workflow to say. Raises
        TaskTimedOut where it still runs at the deadline; for one that ended otherwise, Exists for a clone whose VMID
        was taken, Locked for one that found its VM locked (a clone's, its source), by its lock or its configuration's
        file lock, else TaskFailed with the last lines of its log. A poll that fails while the run goes on raises its
        own error - the transport's TimedOut, where one poll had no answer within its read's wait with time still left,
        its Unreachable, or the client's - and the task may then still run on Proxmox: the workflow follows it again by
        the Task it holds, or reads the VM. The log, read once the task has ended, is held to what is left of the same
        deadline and to stop, and never decides the outcome: where it cannot be read in that time, its read fails or
        the run stops, a success is returned with no lines, and a failure is TaskFailed with none - or Stopped where the
        run stopped, the TaskFailed its cause."""
        if not isinstance(task, Task):
            raise TypeError("follow() follows a Task a write answered")
        if stop is not None and not isinstance(stop, threading.Event):
            raise TypeError("stop is the run's threading.Event, Context.stopping, or None")
        seconds = DEADLINES[task.kind] if deadline is None else _seconds(deadline)
        end = time.monotonic() + seconds
        pauses = iter(POLLS)
        while True:
            if stop is not None and stop.is_set():
                raise Stopped(task)
            left = end - time.monotonic()
            if left <= 0:
                raise TaskTimedOut(task, seconds)
            late: TimedOut | None = None
            try:
                status = answers.task_status(
                    self._get(f"nodes/{task.node}/tasks/{task.upid}/status", deadline=left), task.node, task.upid
                )
            except Exception as error:
                # A poll that failed once the run stopped - its client closed under it, as a run's end may - is the
                # stop, never the poll's own error: the task goes on on Proxmox either way.
                if stop is not None and stop.is_set():
                    raise Stopped(task) from error
                if not isinstance(error, TimedOut):
                    raise
                late = error
            if late is not None:
                if end - time.monotonic() > 0:
                    raise late
                raise TaskTimedOut(task, seconds)
            if not status.running:
                break
            pause = max(0.0, min(next(pauses, POLLS[-1]), end - time.monotonic()))
            if stop is None:
                time.sleep(pause)
            elif stop.wait(pause):
                raise Stopped(task)
        if status.ok:
            if not status.warnings:
                return status
            # The task's success is Proxmox's answer, read; its lines are a courtesy, never a reason to fail it.
            return dataclasses.replace(status, lines=self._last_lines(task, end, stop) or ())
        error = status.entry.get("exitstatus")
        said = self._words(error if isinstance(error, str) else "", 400)
        if task.kind == "clone" and _taken(said, task.vmid):
            raise Exists(task.vmid)
        # A clone's task meets its source's lock: the new VM's own is Exists, above.
        held = task.source if task.source is not None else task.vmid
        if locked := LOCK.search(said):
            raise Locked(held, locked.group(1), f"its {task.kind} task")
        if _file_locked(said, held):
            raise Locked(held, FILE, f"its {task.kind} task")
        lines = self._last_lines(task, end, stop)
        if lines is None and stop is not None and stop.is_set():
            raise Stopped(task) from TaskFailed(task, said, ())
        raise TaskFailed(task, said, lines or ())

    def _last_lines(self, task: Task, end: float, stop: threading.Event | None) -> tuple[str, ...] | None:
        """The last LOG_LAST lines of an ended task's log, read LOG_PAGE lines a request and LOG_PAGES requests at
        most, each one line of plain text with the token's secret struck; each request held to what is left of
        follow()'s deadline, which ends at end (time.monotonic()'s), and stop read before it. None, and no line, where
        a request failed, none of the deadline was left, or the run stopped: lines read before then are not the last
        ones."""
        kept: list[str] = []
        for page in range(LOG_PAGES):
            left = end - time.monotonic()
            if left <= 0 or (stop is not None and stop.is_set()):
                return None
            try:
                read = self.task_log(task.node, task.upid, page * LOG_PAGE, LOG_PAGE, deadline=left)
            except (TransportError, ProxmoxError):
                return None
            kept = (kept + list(read.lines))[-LOG_LAST:]
            if len(read.lines) < LOG_PAGE:
                break
        return tuple(self._words(line, 200) for line in kept)
