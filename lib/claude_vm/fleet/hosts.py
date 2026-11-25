"""The hosts, a folder each, the first included: a second layout beside today's hosts/layout.py, read by today's core,
hosts/choose.py and hosts/model.py, as they stand (docs/architecture/engine.md §16.9).

A host is a folder of the fleet's files, `fleet/hosts/<name>/`, named as any host is (hosts/model.py's `name_refusal`).
It holds:

- `host.yml`, the host's own fields, which travel with the fleet's folder (`FIELDS`): `address`, the API's port
  (`api_port`), the token's user and its ID (`token_user`, `token_id`), the account and port root SSH and the hop use
  (`ssh_user`, `ssh_port`), the account the hop logs in as where it is another (`jump`), which kind of anchor
  `proxmox-trust.pem` is (`trust_kind`, `node` or `ca`) with the fingerprint recorded beside it (`fingerprint`), and
  what this fleet's `init` and template builds made on the host (`made`), each by its ID and with who made it, which a
  cleanup removes and nothing else. Nothing of one computer's is a field: a key's path is this computer's, the row
  `ssh_identity_file` in client.yml's map `hosts`, and a field `host.yml` does not have is refused by name.
- `settings.yml`, its overrides (fleet/overrides.py): its node, storage, bridge, pool and every other row of home
  `host`, which no other host reads.
- `proxmox-trust.pem`, where it keeps one: the anchor its API's certificate is verified against (card E3), there only
  beside a `trust_kind`, and a `trust_kind` only beside it; or, where host.yml gives a `trust_kind` and that file is
  not there, the anchor `write` staged beside it (`STAGED`), which a write stopped before its last step left.

`write(layout, name, expect, change=..., anchor=...)` is host.yml's one writer, and its anchor's: compare-and-swap on
the digest of what the caller read (`Layout.state`), under `store.locked` on the host's folder, held to what the reader
takes before anything is written and read back through `fields` and `anchor` after. Its anchor and the anchor's kind
and fingerprint are written by it alone, in an order a run stopped at any point leaves readable: a new anchor is
staged first and host.yml written before it is put in place, so that host.yml never names a kind with no anchor to
read, nor is an anchor ever there with no kind (`_commit`).

The current host is this computer's, `current_host` in client.yml, which never travels: `Layout.current_name` reads
it, and `make_current` writes it through fleet/overrides.py's one writer. With one host and none current, a run reaches
that host; with several and none current, which one is this computer's to choose, and a run that names none is refused.
A VM's host is the one its alias records in the managed SSH config, `# origin: <host>`, as today, read from that file,
which is this computer's and not moved.

`reach` decides which host a run is against with hosts/choose.py's rules - `--host`, the host the VMs named are on,
the current host, the fleet's one host - and keeps the refusals of today's hosts/resolve.py that still have a meaning
here: a host whose files cannot be used, and one that lacks a setting each host sets for itself (FLEET_KINDS' `always`,
in its own settings.yml). A setting the fleet's file may not supply for a host is refused by the overrides' reader, in
the fleet's file. The checks about Ansible's inventory and today's two vaults have nothing to act on here. What a host's
files say is that host's problem, said in its place, and a run against another goes on.

A Host record of this layout never names today's inventory files: its `local_file`, `vault_file`, `hosts_file` and
`sources` are today's and are read by nothing of the new engine (tests/unit/test_aw_fleet_hosts.py holds it). Its
`local` is the host's resolved settings, the rows of scope `host`; its `first` marks the fleet's one host.

Standard library, the table and today's hosts core at import; the YAML reader is the overrides', imported by the
functions that read or write a file.
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from claude_vm import store
from claude_vm.fleet import files as fleet_files
from claude_vm.fleet import overrides, paths, registry
from claude_vm.hosts import choose, model
from claude_vm.hosts.kinds import FLEET_KINDS
from claude_vm.schema import patterns
from claude_vm.schema.printer import said
from claude_vm.schema.words import PLACEHOLDER, Line

# The folder of the hosts in the fleet's files, as a run names it; and each host's files in its folder.
HOSTS_REL = "fleet/hosts"
HOST_FILE = "host.yml"
ANCHOR = "proxmox-trust.pem"
# A new anchor as write stages it beside the host's before it is put in place: read only where host.yml gives a kind
# and the anchor's own file is not there, and otherwise left for the next write to take out.
STAGED = f".{ANCHOR}.new"
# An anchor's file and host.yml as write makes them: an anchor holds certificates alone, which are no secret, and
# host.yml travels with the fleet's folder as its other files do.
ANCHOR_MODE = 0o644
# What an anchor's text is, whole: PEM certificates one after the other and nothing else - no key, no comment. Whether
# each is a certificate is pve/pin.py's context to say.
CERTIFICATES = re.compile(r"(?:-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\n]{64,16384}-----END CERTIFICATE-----\n)+")
# The one kind of host a folder of the fleet's is: a machine that is no Proxmox host is in machines.yml.
KIND = FLEET_KINDS["proxmox"]
# What host.yml holds, one and all, as a refusal says it.
HOST_FIELDS = ("field", "the host's fields")
# Every field of host.yml, and the three it must give: each the host's own, and none of one computer's.
FIELDS = (
    "address",
    "api_port",
    "token_user",
    "token_id",
    "jump",
    "ssh_user",
    "ssh_port",
    "trust_kind",
    "fingerprint",
    "made",
)
REQUIRED = ("address", "token_user", "token_id")
# The two kinds of anchor a host keeps (card E3): its node's own certificate, or the CA that signed it.
TRUST_KINDS = ("node", "ca")
# What this fleet makes on a host, each by its ID (card E1's list, and E12's build: the template, each image file it
# downloaded and each package it installed), and who made it: this engine through init's offer or a build, or the
# administrator by hand from the commands init printed.
MADE = ("pool", "role", "user", "token", "grant", "template", "file", "package")
MADE_BY = ("engine", "administrator")
MADE_KEYS = ("what", "id", "by")
# A host's address as a run reaches it: a host's name or an IP address, one word, starting with a letter or a digit,
# so that ssh never takes it for an option.
ADDRESS = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,252}")
# An account on the Proxmox host, as ssh logs in with one.
ACCOUNT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,63}")
# A thing made, by its ID: one line of text, no control character.
MADE_ID = re.compile(r"[^\x00-\x1f\x7f-\x9f\u2028\u2029]{1,256}")
# The form of a thing made by a build, by its kind, which a cleanup removes by: a template a guest's ID in Proxmox's
# range, given as a decimal; a file an absolute path of plain parts, none of them . or ..; a package a Debian name.
MADE_FORMS = {
    "template": re.compile(r"[1-9][0-9]{2,8}"),
    "file": re.compile(r"(?:/[A-Za-z0-9_+][A-Za-z0-9._+-]{0,254})+"),
    "package": re.compile(r"[a-z0-9][a-z0-9+.-]{1,99}"),
}
FORMS_SAID = (
    "a template's id is a guest's ID, a file's an absolute path of plain parts, none of them . or .., and a package's "
    "a Debian package's name"
)
# The managed SSH config, under the user's home, where each VM's alias records its host: this computer's own.
ALIASES = (".ssh", "agent-warehouse.conf")
# hosts/choose.py says where a name came from in today's words: the one for the current host, said as this layout
# keeps it.
WHERE_FROM = {f"{HOSTS_REL}/current names": "client.yml's current_host names"}

Refused = model.Refused


@dataclass(frozen=True)
class Made:
    """One thing this fleet made on a host: what it is (one of MADE), its ID there, and who made it (one of MADE_BY)."""

    what: str
    id: str
    by: str


@dataclass(frozen=True)
class HostFile:
    """A host's host.yml as it was read: every field of FIELDS, a default where it may be left out."""

    address: str
    token_user: str
    token_id: str
    api_port: int = 8006
    jump: str = ""
    ssh_user: str = "root"
    ssh_port: int = 22
    trust_kind: str | None = None
    fingerprint: str = ""
    made: tuple[Made, ...] = ()


@dataclass(frozen=True)
class Reached:
    """The host a run is against, once every rule here passed: its record as hosts/choose.py reads it, with why it was
    chosen; its fields and its anchor's text (None where it keeps none); the other hosts, whose names and addresses a
    run keeps apart from its own machines (claude_hosts_other), with more than one host; and the notes said on
    choosing it, in their order, the routing note last."""

    choice: model.Choice
    host: model.Host
    fields: HostFile
    anchor: str | None
    others: tuple[model.OtherHost, ...]
    notes: tuple[model.Note, ...]


@dataclass(frozen=True)
class Anchor:
    """An anchor a host keeps: its PEM certificates, which kind it is (one of TRUST_KINDS), and the SHA-256 fingerprint
    recorded beside it, "" for none (card E3's to work out, pve/pin.py's to hold)."""

    pem: str
    kind: str
    fingerprint: str = ""


@dataclass(frozen=True)
class State:
    """A host's host.yml and anchor as one read found them, under the lock a write takes: its fields (None where it has
    no host.yml yet), its anchor's text (None where it keeps none), and the digest a write compares (MISSING for
    neither)."""

    fields: HostFile | None
    anchor: str | None
    digest: str


class _Unchanged:
    """The anchor write leaves as it is."""

    def __repr__(self) -> str:
        return "UNCHANGED"


UNCHANGED = _Unchanged()
# The digest of a host with neither host.yml nor an anchor: one not set up yet, which a write may make.
MISSING = overrides.MISSING


def _init(name: str) -> str:
    """The command that sets up the host called name, as the printer writes it."""
    return said("{init}", {"init": Line("init", {"host": name})})


# The command that makes a host the current one, as a sentence names it.
_HOST = Line("host", {"name": PLACEHOLDER})


def _them(names: list[str]) -> str:
    return "it" if len(names) == 1 else "them"


def _belongs(name: str) -> str:
    """Where a setting given in host.yml belongs, as its refusal there says it."""
    row = registry.row(name)
    if row.home == "host":
        return "keep it in the host's settings.yml"
    if row.home == "client" and row.scope == "host":
        return "it is this computer's own: keep it in client.yml, in its map hosts by the host's name"
    if row.home == "client":
        return "it is this computer's own: keep it in client.yml"
    if row.home == "run":
        return "it is given for one run alone, with --set"
    return "it is the fleet's: keep it in the fleet's settings.yml"


def _port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 65536:
        raise ValueError("takes a port: a whole number from 1 to 65535")
    return value


def _address(value: object) -> str:
    if not isinstance(value, str) or ADDRESS.fullmatch(value) is None:
        raise ValueError("takes a host's name or an IP address, one word that starts with a letter or a digit")
    return value


def _account(value: object) -> str:
    if not isinstance(value, str) or ACCOUNT.fullmatch(value) is None:
        raise ValueError("takes an account on the host, alone: no @ and no host")
    return value


def _jump(value: object) -> str:
    return "" if value == "" else _account(value)


def _token_user(value: object) -> str:
    if not isinstance(value, str) or not patterns.full("token_user", value) or value.count("@") != 1:
        raise ValueError(f"takes {patterns.PATTERNS['token_user'].words}")
    if not all(value.partition("@")[::2]):
        raise ValueError(f"takes {patterns.PATTERNS['token_user'].words}")
    return value


def _token_id(value: object) -> str:
    if not isinstance(value, str) or not value or not patterns.full("proxmox_id", value):
        raise ValueError(f"takes {patterns.PATTERNS['proxmox_id'].words}")
    return value


def _trust_kind(value: object) -> str:
    if not isinstance(value, str) or value not in TRUST_KINDS:
        raise ValueError(f"takes {' or '.join(TRUST_KINDS)}")
    return value


def _fingerprint(value: object) -> str:
    if not isinstance(value, str) or not value or not patterns.full("fingerprint", value):
        raise ValueError(f"takes {patterns.PATTERNS['fingerprint'].words}")
    return value


def _made(value: object) -> tuple[Made, ...]:
    takes = (
        f"takes a list of what was made, each with its what (one of {', '.join(MADE)}), its id (one line) and its by"
        f" ({' or '.join(MADE_BY)}), and nothing else"
    )
    if not isinstance(value, list):
        raise ValueError(takes)
    found = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != set(MADE_KEYS):
            raise ValueError(takes)
        what, made_id, by = entry["what"], entry["id"], entry["by"]
        # A template's ID is a number, which YAML reads as one.
        if isinstance(made_id, int) and not isinstance(made_id, bool) and made_id > 0:
            made_id = str(made_id)
        if what not in MADE or by not in MADE_BY or not isinstance(made_id, str) or not MADE_ID.fullmatch(made_id):
            raise ValueError(takes)
        if what in MADE_FORMS and not MADE_FORMS[what].fullmatch(made_id):
            raise ValueError(f"{takes}; {FORMS_SAID}")
        found.append(Made(what=what, id=made_id, by=by))
    return tuple(found)


def _file_bytes(path: Path, what: str = "the host's anchor") -> bytes | None:
    """A file of the host's folder as bytes, at most one more than overrides.MOST of them; None where it is not there.
    Refused where it cannot be read, saying that what it holds is not known."""
    try:
        with open(path, "rb") as stream:
            return stream.read(overrides.MOST + 1)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise Refused(f"{path} cannot be read ({error.strerror or error}): {what} is not known") from None


def _digest(host: bytes | None, anchor: bytes | None) -> str:
    """What a write compares: host.yml's bytes and the anchor's, each with whether it is there; MISSING for neither."""
    if host is None and anchor is None:
        return MISSING
    made = hashlib.sha256()
    for part in (host, anchor):
        made.update(b"-" if part is None else b"+%d:" % len(part))
        made.update(part or b"")
    return made.hexdigest()


def _host_file(raw: dict, lines: Mapping[tuple[str, ...], int], path: Path, name: str) -> HostFile:
    """host.yml's mapping, read from path, each field held to what it takes: the HostFile it is. Refused, by its path
    and line and the field's name, never its value, where it holds what it may not or lacks what it must give."""
    settings = {row.name for row in registry.rows()}
    kept: dict[str, object] = {}
    for key, value in raw.items():
        at = f"{path} line {lines.get((key,), 1) if isinstance(key, str) else 1}"
        if key not in FIELDS:
            named = isinstance(key, str) and patterns.full("setting_name", key)
            if named and key in settings:
                raise Refused(f"{at}: {key} is a setting, not a field of the host's: {_belongs(key)}")
            raise Refused(f"{at}: {key if named else 'a key'} is no field of the host's: {', '.join(FIELDS)} are")
        if value is None:
            raise Refused(f"{at}: {key} is empty there: give it a value, or take its line out")
        try:
            kept[key] = HELD[key](value)
        except ValueError as error:
            raise Refused(f"{at}: {key} {error}") from None
    missing = [field for field in REQUIRED if field not in kept]
    if missing:
        raise Refused(f"{path} does not give {', '.join(missing)}: {_init(name)} writes {_them(missing)}")
    if kept.get("fingerprint") and "trust_kind" not in kept:
        raise Refused(
            f"{path} line {lines.get(('fingerprint',), 1)}: fingerprint is the anchor's, and it gives no"
            " trust_kind: say which kind of anchor the host keeps, or take the line out"
        )
    return HostFile(**kept)


# How each field of host.yml is held: a function that returns it as kept, or raises ValueError with what it takes.
HELD: dict[str, Callable[[object], object]] = {
    "address": _address,
    "api_port": _port,
    "token_user": _token_user,
    "token_id": _token_id,
    "jump": _jump,
    "ssh_user": _account,
    "ssh_port": _port,
    "trust_kind": _trust_kind,
    "fingerprint": _fingerprint,
    "made": _made,
}


class Layout:
    """The hosts of the fleet at home, each a folder of fleet/hosts/: every method and attribute of today's
    hosts/layout.Layout that hosts/choose.py uses, over the new folders. environ is where ~ and client.yml's paths are
    read from, this process's where it is None."""

    def __init__(self, home: paths.Home, environ: Mapping[str, str] | None = None):
        self.home = home
        self.environ = os.environ if environ is None else environ
        self.hosts_dir = paths.fleet_dir(home) / overrides.HOSTS
        self.hosts_rel = HOSTS_REL

    # ------------------------------------------------------------------------------------------- reading ----

    def _shared(self) -> overrides.Layers:
        """The fleet's settings.yml and client.yml, which every host's run reads: Refused, for the whole fleet, where
        either holds what it may not."""
        try:
            return overrides.read(self.home, None, environ=self.environ)
        except (overrides.Refused, paths.Refused) as error:
            raise Refused(str(error)) from None

    def folders(self) -> list[str]:
        """The names of the folders under fleet/hosts/, sorted: every entry but a file and a dot-file."""
        try:
            entries = sorted(os.scandir(self.hosts_dir), key=lambda entry: entry.name)
        except FileNotFoundError:
            return []
        except OSError as error:
            raise Refused(
                f"{self.hosts_dir} cannot be read ({error.strerror or error}): the hosts are not known"
            ) from None
        return [entry.name for entry in entries if entry.is_dir() and not entry.name.startswith(".")]

    def many(self) -> bool:
        """Whether the fleet has more than one host."""
        return len(self.folders()) > 1

    def fields(self, name: str) -> HostFile:
        """The host's host.yml, each field held to what it takes; Refused, by its path and line and the field's name,
        never its value, where it is not there, cannot be read or holds what it may not."""
        refusal = model.name_refusal(name)
        if refusal:
            raise Refused(f"that is no host's folder: {refusal}")
        path = self.hosts_dir / name / HOST_FILE
        if not path.exists():
            raise Refused(f"not set up: {_init(name)}")
        try:
            raw, lines = overrides.literals(path, HOST_FIELDS)
        except overrides.Refused as error:
            raise Refused(str(error)) from None
        return _host_file(raw, lines, path, name)

    def anchor(self, name: str, fields: HostFile) -> str | None:
        """The host's anchor, proxmox-trust.pem's text, where its host.yml gives a trust_kind; None where it keeps
        none. Where host.yml gives one and that file is not there, the anchor write staged beside it is the host's: a
        write that put host.yml in place and was stopped before the anchor. Refused where the anchor is there and the
        kind is not, or the kind is and no anchor is, or the file cannot be read as text."""
        path = self.hosts_dir / name / ANCHOR
        data = _file_bytes(path)
        if data is None and fields.trust_kind is not None:
            data = _file_bytes(self.hosts_dir / name / STAGED)
        if data is None:
            if fields.trust_kind is not None:
                raise Refused(
                    f"{self.hosts_dir / name / HOST_FILE} gives a trust_kind, and {path} is not there: {_init(name)}"
                    " keeps the host's anchor again"
                )
            return None
        if fields.trust_kind is None:
            raise Refused(
                f"{path} is there, and {self.hosts_dir / name / HOST_FILE} gives no trust_kind: say which kind of"
                f" anchor it is ({' or '.join(TRUST_KINDS)}), or take the file out"
            )
        if len(data) > overrides.MOST:
            raise Refused(f"{path} holds more than an anchor does ({overrides.MOST} bytes at most)")
        try:
            return data.decode("ascii")
        except UnicodeDecodeError:
            raise Refused(f"{path} is not the text of PEM certificates") from None

    def state(self, name: str) -> State:
        """The host's host.yml and anchor as one read finds them, read under the lock a write takes, so that a write is
        seen whole or not at all: the State whose digest write compares. A host with no folder yet has neither. A
        host.yml that gives a trust_kind beside no anchor and none staged, whose anchor a hand took out, is given as a
        host that keeps none, so that a write that keeps an anchor, or none, puts it right (init's, the one way back
        the reader's refusal names); every reader of the host still refuses it. Refused as fields and anchor refuse,
        but for a host.yml that is not there."""
        refusal = model.name_refusal(name)
        if refusal:
            raise Refused(f"that is no host's folder: {refusal}")
        folder = self.hosts_dir / name
        if not folder.is_dir():
            return State(None, None, MISSING)
        with store.locked(folder / HOST_FILE):
            return self._state(name)

    def _state(self, name: str) -> State:
        """state, its caller holding the lock."""
        folder = self.hosts_dir / name
        host = _file_bytes(folder / HOST_FILE, "the host's fields")
        if host is None:
            # Not set up yet: a staged anchor is a first write's that was stopped before host.yml, and no anchor; one in
            # its place is no host's, and nothing says which kind it is.
            if (folder / ANCHOR).exists():
                raise Refused(
                    f"{folder / ANCHOR} is there, and {folder / HOST_FILE} is not: take the file out, and "
                    f"{_init(name)} keeps the host's anchor again"
                )
            return State(None, None, MISSING)
        fields = self.fields(name)
        if fields.trust_kind is not None and not any(os.path.lexists(folder / each) for each in (ANCHOR, STAGED)):
            # A kind with nothing to read beside it: given as no anchor, which a write that keeps one, or none, mends.
            return State(fields, None, _digest(host, None))
        anchor = self.anchor(name, fields)
        return State(fields, anchor, _digest(host, None if anchor is None else anchor.encode("ascii")))

    def other(self, name: str) -> model.Host:
        """The host in fleet/hosts/<name>, with what keeps it from being used, if anything: what is known of it, each
        field worked out first, then the record built once. Refused, for the whole fleet, where the fleet's
        settings.yml or client.yml holds what it may not."""
        folder = f"{HOSTS_REL}/{name}"
        first = self.folders() == [name]

        def unusable(problem: str, **known) -> model.Host:
            return model.Host(name=name, folder=folder, first=first, problem=problem, **known)

        refusal = model.name_refusal(name)
        if refusal:
            return unusable(f"{self.hosts_dir / name} is no host's folder: {refusal}")
        self._shared()
        # A file of its that cannot be used costs this host alone: said in its place.
        try:
            fields = self.fields(name)
        except Refused as refused:
            return unusable(str(refused))
        try:
            self.anchor(name, fields)
        except Refused as refused:
            return unusable(str(refused), kind=KIND, address=fields.address)
        try:
            own = overrides.read(self.home, name, environ=self.environ).host
            values = overrides.resolve(self.home, name, environ=self.environ)
        except (overrides.Refused, paths.Refused) as error:
            return unusable(str(error), kind=KIND, address=fields.address)
        local = {row.name: values[row.name].value for row in registry.rows() if row.scope == "host"}
        known = {"kind": KIND, "address": fields.address, "local": local}
        settings = {} if own is None else own.settings
        missing = [setting for setting in KIND.always if settings.get(setting) in (None, "")]
        if missing:
            return unusable(
                f"{self.hosts_dir / name / overrides.SETTINGS} does not set {', '.join(missing)}, which each host sets"
                " for itself:"
                f" {_init(name)} writes {_them(missing)}",
                incomplete=True,
                **known,
            )
        return model.Host(name=name, folder=folder, first=first, **known)

    def every(self) -> list[model.Host]:
        """Every host, by its folder's name."""
        return [self.other(name) for name in self.folders()]

    def others(self) -> list[model.Host]:
        """Every host but the fleet's one host: none with one host, every one with several."""
        return [host for host in self.every() if not host.first]

    def first(self) -> model.Host | None:
        """The host a run reaches with none named and none current: the fleet's one host, as its files say it is; None
        with no host. With several, which one is this computer's to choose: Refused, saying how."""
        names = self.folders()
        if len(names) == 1:
            return self.other(names[0])
        if not names:
            return None
        shown = ", ".join(name for name in names if not model.name_refusal(name))
        raise Refused(
            f"the fleet has more than one host ({shown}), and none is this computer's current host: "
            + said("make one the current host with {host}, or name one with --host", {"host": _HOST})
        )

    def current_name(self) -> str:
        """The name client.yml's current_host holds, "" with none."""
        current = self._shared().client.own.get("current_host")
        return current if isinstance(current, str) else ""

    def named(self, name: str, why: str, lenient: bool = False) -> model.Host:
        """The host called name, set up; Refused otherwise, why saying where the name came from. lenient takes one
        that lacks a setting it sets for itself, for settings and vault to put that right."""
        why = WHERE_FROM.get(why, why)
        refusal = model.name_refusal(name)
        if refusal:
            raise Refused(f"{why} a name that is no host's: {refusal}")
        if name in self.folders():
            host = self.other(name)
            if host.problem and not (lenient and host.incomplete):
                raise Refused(f"{why} {name}, which cannot be used: {host.problem}")
            return host
        known = [folder for folder in self.folders() if not model.name_refusal(folder)]
        way_out = said("{init} sets one up", {"init": Line("init", {"host": name, "address": PLACEHOLDER})})
        raise Refused(f"{why} {name}, which is no host of the fleet's ({', '.join(known) or 'none'}): {way_out}")

    def others_of(self, host: model.Host | None) -> tuple[model.OtherHost, ...]:
        """Every other host of the fleet, by its name and its address: a run against host keeps their names and
        addresses apart from its own machines'. A host not set up has the address its files give, or none."""
        return tuple(
            model.OtherHost(name=other.name, address=other.address)
            for other in self.every()
            if (host is None or other.name != host.name) and not model.name_refusal(other.name)
        )

    def defaults(self) -> dict:
        """The table's defaults of the rows of scope host, each a copy: what a host's setting is where nothing sets
        it."""
        return {
            row.name: list(row.default) if isinstance(row.default, tuple) else row.default
            for row in registry.rows()
            if row.scope == "host" and not callable(row.default)
        }

    # ------------------------------------------------------------------------------------------- the names ----

    def alias_file(self) -> Path | None:
        """The managed SSH config, where each VM's alias records its host, under this computer's user home; None with
        the aliases off (client.yml's claude_ssh_config), when nothing records where a VM is."""
        if self._shared().client.settings.get("claude_ssh_config", registry.row("claude_ssh_config").default) is False:
            return None
        try:
            return paths.user_home(self.environ).joinpath(*ALIASES)
        except paths.Refused as error:
            raise Refused(str(error)) from None

    def origins(self, names: list[str]) -> dict[str, str]:
        """Of names, those whose alias records one of the fleet's hosts, and that host: where each VM is. A name with
        no alias, or one recording a host the fleet does not have, is none of them."""
        path = self.alias_file()
        try:
            text = path.read_text(encoding="utf-8") if path else ""
        except (OSError, UnicodeDecodeError):
            text = ""
        from claude_vm.proxmox import ssh_config_blocks

        known = {name for name in self.folders() if not model.name_refusal(name)}
        recorded = {block["name"]: block["origin"] for block in ssh_config_blocks(text)}
        return {name: recorded[name] for name in names if recorded.get(name) in known}


def reach(layout: Layout, request: model.Request) -> Reached:
    """The host a run is against, by hosts/choose.py's rules - --host, the host the VMs named are on as their aliases
    record, client.yml's current_host, the fleet's one host - and the refusals of today's resolver that still have a
    meaning: a host whose files cannot be used, and one that lacks a setting each host sets for itself, but for a
    lenient request (settings and vault), which may put that right. Another host's files refuse nothing. Refused, with
    the notes said before it."""
    notes: tuple[model.Note, ...] = ()
    try:
        choice = choose.choose(layout, request)
        notes = choice.notes
        host = choice.host or choose.pick(layout, lenient=request.lenient)
        if host is None:
            raise Refused("the fleet has no host yet: " + said("{init} sets up the first", {"init": Line("init")}))
        if host.problem and not (request.lenient and host.incomplete):
            raise Refused(f"{host.name} cannot be used: {host.problem}")
        fields = layout.fields(host.name)
        anchor = layout.anchor(host.name, fields)
        others = layout.others_of(host) if choice.many else ()
    except Refused as refused:
        raise Refused(str(refused), notes=refused.notes or notes) from refused
    if choice.routed and choice.current and choice.current != host.name:
        placed = sorted(name for name, _ in choice.origins)
        notes += (
            model.Note(
                level="note",
                text=f"{', '.join(placed)} {'is' if len(placed) == 1 else 'are'} {host.name}'s, as "
                f"{'its' if len(placed) == 1 else 'their'} SSH alias records: this run is against {host.name}, not "
                f"the current host, {choice.current}.",
            ),
        )
    return Reached(choice=choice, host=host, fields=fields, anchor=anchor, others=others, notes=notes)


def make_current(layout: Layout, name: str, expect: str) -> str:
    """Make name this computer's current host: a host of the fleet that can be used (Refused otherwise, saying why),
    written as client.yml's current_host by fleet/overrides.py's one writer, compare-and-swap on expect, the digest of
    client.yml as the caller read it (overrides.read's client.digest). The caller holds the fleet's lock for it (card
    D12's host <name>). Returns the digest of client.yml after it."""
    host = layout.named(name, "the host to make current is")
    try:
        return overrides.set_current(layout.home, host.name, expect, environ=layout.environ)
    except overrides.Refused as error:
        raise Refused(str(error)) from None


# ---------------------------------------------------------------- host.yml's one writer ----


def _anchor_held(anchor: Anchor) -> Anchor:
    """anchor held to what the reader takes beside a kind: PEM certificates alone, as ASCII and within the bound, a kind
    of TRUST_KINDS, and a fingerprint as openssl prints one or none; Refused otherwise, with nothing said of its text.
    The fingerprint is kept in upper case, as pve/pin.py compares it."""
    if not isinstance(anchor, Anchor):
        raise Refused("an anchor to keep is an Anchor, or None for none: nothing was written")
    pem = anchor.pem
    if not isinstance(pem, str) or not pem.isascii() or len(pem) > overrides.MOST or not CERTIFICATES.fullmatch(pem):
        raise Refused("an anchor holds PEM certificates and nothing else, a key least of all: nothing was written")
    try:
        kind = _trust_kind(anchor.kind)
        fingerprint = _fingerprint(anchor.fingerprint).upper() if anchor.fingerprint else ""
    except ValueError as error:
        raise Refused(f"an anchor's kind or fingerprint {error}: nothing was written") from None
    return Anchor(pem=pem, kind=kind, fingerprint=fingerprint)


def _unlinked(folder: Path) -> None:
    """No file of the host's folder that a write touches is a link: a write never goes through one, to a file that
    does not travel with the fleet's folder or to another host's. Refused otherwise. The folder itself, and those above
    it, are _folders'."""
    for path in (folder / HOST_FILE, folder / ANCHOR, folder / STAGED):
        if path.is_symlink():
            raise Refused(f"{path} is a link, which a write never goes through: nothing was written")


def _folders(layout: Layout, folder: Path) -> None:
    """The folders a host's files are written in, the home's, the fleet's, its hosts' and the host's own, through
    fleet/files.py's folder: each made at paths.DIR_MODE where it is not there, and each that is there held to be a
    folder itself, so that a link where one goes is refused as files.py refuses one, and nothing is written through
    it."""
    for each in dict.fromkeys((layout.home.root, paths.fleet_dir(layout.home), layout.hosts_dir, folder)):
        try:
            fleet_files.folder(each)
        except fleet_files.Unsafe as unsafe:
            raise Refused(str(unsafe).removeprefix("error: ")) from None
        except OSError as error:
            raise Refused(f"{each} cannot be made ({error.strerror or error}): nothing was written") from None


def _synced(folder: Path) -> None:
    """The folder's entries on disk, so that a rename lands before the next step does, a crash included."""
    descriptor = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _commit(folder: Path, kind_now: bool, text: str | None, pem: str | None | _Unchanged, fingerprinted: bool) -> None:
    """Every write of a file hosts.py makes, in the one order a run stopped after any step leaves readable, its caller
    holding the folder's lock: host.yml's new text (None: as it is), and the anchor (UNCHANGED, None for none, or its
    new text). kind_now is whether host.yml gives a trust_kind now; fingerprinted whether the new one records a
    fingerprint. Each step is on disk, its folder synced, before the next.

    First a write a stopped run left is put right, as the reader already reads it: a staged anchor beside a kind and no
    anchor is put in place, and one beside an anchor or no kind is taken out. Then:

    - a first anchor: staged, then host.yml, then put in place. Stopped between, host.yml gives no kind and the staged
      file is no anchor; or it gives one and the staged file is the anchor.
    - no anchor any more: the anchor moved to the staged name, then host.yml, then the staged file taken out. Stopped
      between, the staged file is the anchor while host.yml gives its kind, and none once it does not.
    - another anchor in place of one: both are always there, so a stop leaves the old anchor beside the new kind or the
      new beside the old, either of which holds the host to no more than one of the two: where the new records a
      fingerprint, host.yml first, so that no state between lacks it beside the new anchor; where it records none, the
      anchor first, so that the old fingerprint goes last.
    """
    host, live, staged = folder / HOST_FILE, folder / ANCHOR, folder / STAGED
    # Each step: put (a file written whole, with the mode a new one takes), move (a rename) or drop, and its paths.
    steps: list[tuple[str, Path, Path | str | None, int]] = []
    had = live.exists() or (kind_now and staged.exists())
    if staged.exists():
        steps.append(("move", live, staged, 0) if kind_now and not live.exists() else ("drop", staged, None, 0))
    put_host = [] if text is None else [("put", host, text, paths.FILE_MODE)]
    if isinstance(pem, _Unchanged):
        steps += put_host
    elif pem is None:
        steps += [("move", staged, live, 0), *put_host, ("drop", staged, None, 0)] if had else put_host
    else:
        steps.append(("put", staged, pem, ANCHOR_MODE))
        put_in_place = [("move", live, staged, 0)]
        steps += [*put_host, *put_in_place] if not had or fingerprinted else [*put_in_place, *put_host]
    for step, path, source, mode in steps:
        if step == "put":
            store.replace(path, str(source), mode_if_new=mode)
        elif step == "move":
            os.replace(str(source), path)
        else:
            path.unlink(missing_ok=True)
        _synced(folder)


def _target(
    raw: dict,
    change: Callable[[dict], None] | None,
    held: Anchor | None | _Unchanged,
    anchor_now: str | None,
    path: Path,
    name: str,
) -> tuple[dict, str | None, HostFile]:
    """What host.yml and the anchor become: host.yml's mapping once change and the anchor's kind and fingerprint are in
    it, the anchor's text, and the HostFile the reader will take it for. Refused, with nothing written, where change
    touches the anchor's kind or fingerprint, or the reader would refuse what would be written."""
    after = copy.deepcopy(raw)
    if change is not None:
        change(after)
    if any(after.get(key) != raw.get(key) for key in ("trust_kind", "fingerprint")):
        raise Refused("a change of host.yml leaves trust_kind and fingerprint to the anchor's: nothing was written")
    pem = anchor_now
    if isinstance(held, Anchor):
        after.update(trust_kind=held.kind, fingerprint=held.fingerprint)
        if not held.fingerprint:
            del after["fingerprint"]
        pem = held.pem
    elif held is None:
        after.pop("trust_kind", None)
        after.pop("fingerprint", None)
        pem = None
    try:
        meant = _host_file(after, {}, path, name)
    except Refused as error:
        raise Refused(f"what would be written is not what the reader takes ({error}): nothing written") from None
    if (meant.trust_kind is None) != (pem is None):
        raise Refused(f"{path} would give a trust_kind with no anchor, or an anchor with none: nothing was written")
    return after, pem, meant


def write(
    layout: Layout,
    name: str,
    expect: str,
    *,
    change: Callable[[dict], None] | None = None,
    anchor: Anchor | None | _Unchanged = UNCHANGED,
) -> str:
    """Write the host called name's host.yml, and its anchor: the one writer of both. change edits host.yml's mapping
    in place (any field but trust_kind and fingerprint, which are the anchor's); anchor is the Anchor to keep, None to
    keep none, or UNCHANGED. Compare-and-swap on expect, the digest of the host's files as the caller read them
    (Layout.state's, MISSING for a host not set up yet, whose folders it makes through fleet/files.py's folder, which
    refuses a link where one goes, as it does for a host that is there); under store.locked on the host's folder;
    host.yml written whole, with every field held to what the reader takes before anything is written, and the anchor
    with its kind and fingerprint in an order no stop after any step leaves the one without the other (_commit); then
    read back through fields and anchor. Never through a link. Refused, with nothing written, where the files changed
    since they were read or what would be written is not what the reader takes. Returns the digest after it."""
    refusal = model.name_refusal(name)
    if refusal:
        raise Refused(f"that is no host's folder: {refusal}: nothing was written")
    held = anchor if isinstance(anchor, _Unchanged) or anchor is None else _anchor_held(anchor)
    folder = layout.hosts_dir / name
    path = folder / HOST_FILE
    changed = f"{path} changed since it was read: nothing was written, so read it again"
    if not folder.is_dir():
        if expect != MISSING:
            raise Refused(changed)
        # A host not set up: what it would hold is held first, so that a refusal makes no folder.
        _target({}, change, held, None, path, name)
    _folders(layout, folder)
    _unlinked(folder)
    with store.locked(path):
        before = layout._state(name)
        if before.digest != expect:
            raise Refused(changed)
        try:
            raw, _lines = overrides.literals(path, HOST_FIELDS)
        except overrides.Refused as error:
            raise Refused(str(error)) from None
        after, pem, meant = _target(raw, change, held, before.anchor, path, name)
        text = None if after == raw and before.fields is not None else overrides._dumped(after)
        if text is None and pem == before.anchor and not (folder / STAGED).exists():
            return before.digest
        try:
            _commit(
                folder,
                raw.get("trust_kind") is not None,
                text,
                UNCHANGED if pem == before.anchor else pem,
                bool(meant.fingerprint),
            )
        except OSError as error:
            raise Refused(f"{folder} could not be written ({error.strerror or error}): read it again") from None
        again = layout.fields(name)
        if again != meant or layout.anchor(name, again) != pem:
            raise Refused(f"{path} does not read back as it was written: read it again")
        return layout._state(name).digest
