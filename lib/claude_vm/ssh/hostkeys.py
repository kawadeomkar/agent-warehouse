"""A machine's SSH host key: a key line parsed, filed under the machine's identity in the product's own known-hosts
file, and taken at a first connection from a scratch file of its own (docs/architecture/engine.md §16.6).

A machine is named by its identity, `agent-warehouse-<its SMBIOS UUID>` (`checks.HOST_KEY_ALIAS_RE`), which Proxmox
holds and the machine cannot change, never by the address it reports: no function here takes an address. `parse` holds
one line of a public key, `<type> <base64> [comment]`, to a type OpenSSH files (`checks.KNOWN_KEY_TYPES`) and to a body
that is base64 (`checks.KNOWN_KEY_RE`) of a key of that very type; `line` is the known-hosts line under an identity.
`kept` reads the product's file (`runner.KNOWN_HOSTS`) as `ssh` reads it - a plain name, a list of patterns, or a name
`ssh` hashed (`HashKnownHosts`), as a person's own `ssh` writes there for the editor's alias - and returns the keys
filed under the identity. `file` adds the line under the folder's lock (`store.locked`), only where no key of that
type is filed for the identity, and raises `Changed` where another is: a changed key is a person's decision, and nothing
here ever replaces or removes a line, so a deleted machine's key stays under an identity no machine made later has.

A first connection (`runner.Target`'s `first_use`) takes the key into a scratch file that `fresh` makes empty, 0600, in
a directory of the run's; `taken` reads back exactly the one line `ssh` wrote there under the identity, and the caller
files it with `file`, after showing it where it asks a person (an adopted machine's question). Every other connection
checks the product's file strictly (`StrictHostKeyChecking=yes`, `UpdateHostKeys=no`), so `ssh` can neither add to it
nor replace a line in it. The person's own known-hosts files are never read or written here.

Standard library only. Nothing is started, and nothing of a key's line is quoted in a message: a machine supplied it.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import hashlib
import hmac
import os
import re
import shlex
import struct
from pathlib import Path

from claude_vm import checks, store
from claude_vm.fleet import paths
from claude_vm.ssh import runner

# The start of a name ssh hashed (HashKnownHosts): |1|<base64 of a 20-byte salt>|<base64 of HMAC-SHA1(salt, name)>.
HASHED = "|1|"
# The most of a scratch file that is read: one line ssh wrote, a key of 16384 bits among them, fits many times over.
SCRATCH_MOST = 64 * 1024
# The scratch file's name in the run's directory, after the machine's identity, so two first uses never share one.
SCRATCH_PREFIX = "known_hosts-"
# A key's body as base64: KNOWN_KEY_RE's characters, in groups of four as base64 has them.
_BASE64 = re.compile(r"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?")


class Refused(ValueError):
    """A key line, an identity, a scratch file or a path this module does not take, said by what is wrong: nothing was
    filed."""


class Changed(Exception):
    """A key offered for an identity under which another key of its type is filed: nothing was filed or replaced, since
    a changed key is a person's decision."""

    def __init__(self, path: Path | str, identity: str, filed: tuple[Key, ...], offered: Key) -> None:
        self.path, self.identity, self.filed, self.offered = Path(path), identity, filed, offered
        # The file as a person types it in a shell: ~/ left as it is, so that the shell expands it.
        where = str(path)
        where = "~/" + shlex.quote(where[2:]) if where.startswith("~/") else shlex.quote(where)
        super().__init__(
            f"the {offered.type} host key of {identity} is {offered.fingerprint}, where {path} holds"
            f" {', '.join(key.fingerprint for key in filed)} for it: nothing was filed. A machine's key changes when it"
            " is made again or renamed, or when something else answers at its address; if you know why it changed,"
            f" take the old key out with ssh-keygen -R {identity} -f {where}"
        )


@dataclasses.dataclass(frozen=True)
class Key:
    """A public host key: its type, as OpenSSH names it, and its body, the base64 of a key of that type."""

    type: str
    body: str

    def __post_init__(self) -> None:
        if not isinstance(self.type, str) or self.type not in checks.KNOWN_KEY_TYPES:
            raise Refused(f"a host key's type is one OpenSSH files: {', '.join(sorted(checks.KNOWN_KEY_TYPES))}")
        if not isinstance(self.body, str) or not _named_in(self.body, self.type):
            raise Refused(f"a host key's body is the base64 of a {self.type} key")

    @property
    def text(self) -> str:
        """The key as a known-hosts line carries it after the name: `<type> <base64>`."""
        return f"{self.type} {self.body}"

    @property
    def fingerprint(self) -> str:
        """The key's fingerprint as ssh shows one: SHA256: and the digest of its body, in base64 with no padding."""
        digest = hashlib.sha256(base64.b64decode(self.body)).digest()
        return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _named_in(body: str, kind: str) -> bool:
    """Whether body is base64 (KNOWN_KEY_RE's characters, padded as base64 is) of a key blob whose first field, as
    OpenSSH writes every public key, is the name of the key's type, kind."""
    if not checks.KNOWN_KEY_RE.fullmatch(body) or not _BASE64.fullmatch(body):
        return False
    try:
        blob = base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(blob) < 4:
        return False
    (size,) = struct.unpack(">I", blob[:4])
    return blob[4 : 4 + size] == kind.encode() and len(blob) > 4 + size


def _family(kind: str) -> str:
    """What a key's type is when a changed key is looked for: the three ECDSA curves count as one, since a machine
    whose ECDSA key now has another curve has changed its key."""
    return "ecdsa" if kind.startswith("ecdsa-sha2-") else kind


def parse(text: str) -> Key:
    """One line of a public key, `<type> <base64>` and a comment or none, as a `.pub` file or ssh-keyscan has it:
    refused where it is more than one line or holds a character that is not printable, where its first word is no type
    OpenSSH files (an option of an authorized_keys line, a known-hosts line's name or marker), or where its body is not
    the base64 of a key of that type. The comment is left off."""
    if not isinstance(text, str):
        raise Refused("a host key is one line of text")
    if not text.isprintable():
        raise Refused("a host key is one line of printable text: this one has a line break or a control character")
    fields = text.split()
    if len(fields) < 2:
        raise Refused("a host key is its type and its body, and a comment or none")
    if fields[0] not in checks.KNOWN_KEY_TYPES:
        raise Refused(
            "a host key's line starts with its type, one OpenSSH files"
            f" ({', '.join(sorted(checks.KNOWN_KEY_TYPES))}): no option, name or marker before it"
        )
    return Key(fields[0], fields[1])


def _identity(identity: object) -> str:
    """A machine's identity, agent-warehouse-<its SMBIOS UUID>: never an address, which the machine reports."""
    if not isinstance(identity, str) or not checks.HOST_KEY_ALIAS_RE.fullmatch(identity):
        raise Refused(f"{identity!r} is not a machine's identity, agent-warehouse-<its UUID>")
    return identity


def _key(key: object) -> Key:
    if not isinstance(key, Key):
        raise Refused("a host key is filed as a Key, made by parse")
    return key


def line(identity: str, key: Key) -> str:
    """The known-hosts line that files key under identity, with no line end: `<identity> <type> <base64>`."""
    return f"{_identity(identity)} {_key(key).text}"


def _real(path: Path | str) -> Path:
    """path with ~ expanded, absolute, by its real path where it is a link or lies under one."""
    found = Path(path).expanduser()
    if not found.is_absolute():
        raise Refused(f"{path} is no absolute path")
    return found.resolve()


def _persons(path: Path | str) -> bool:
    """Whether path is one of the person's own known-hosts files, by their real paths."""
    return _real(path) in {_real(persons) for persons in runner.PERSONS_KNOWN_HOSTS}


def _product(path: Path | str) -> Path:
    """The product's known-hosts file at path, with ~ expanded: never one of the person's own."""
    if _persons(path):
        raise Refused(f"{path} is your own known-hosts file, which the product never reads or writes for a machine")
    return Path(path).expanduser()


def _names(field: str, identity: str) -> bool:
    """Whether a known-hosts line's first field names identity, as ssh reads one: a name ssh hashed (HASHED), or names
    and patterns split by commas, with * and ? as wildcards, in any case, and one that starts with ! excluding."""
    if field.startswith(HASHED):
        # As ssh compares one: the name hashed again with the field's salt, the whole field written out again.
        salt_text = field[len(HASHED) :].partition("|")[0]
        try:
            salt = base64.b64decode(salt_text, validate=True)
        except (binascii.Error, ValueError):
            return False
        if len(salt) != hashlib.sha1().digest_size:
            return False
        digest = hmac.new(salt, identity.encode(), "sha1").digest()
        again = f"{HASHED}{base64.b64encode(salt).decode()}|{base64.b64encode(digest).decode()}"
        return hmac.compare_digest(field.encode(), again.encode())
    named = False
    for pattern in field.split(","):
        excluded = pattern.startswith("!")
        shape = "".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in pattern[excluded:].lower())
        if re.fullmatch(shape, identity, re.S):
            if excluded:
                return False
            named = True
    return named


def _keys_in(text: str, identity: str) -> tuple[Key, ...]:
    """The keys of text's lines that file a key under identity, in their order and each once: a comment, a marker's
    line (@cert-authority, @revoked), a type OpenSSH does not file here and a body that is not one are passed over, as
    ssh passes them."""
    found: list[Key] = []
    for each in text.splitlines():
        fields = each.split()
        if len(fields) < 3 or fields[0].startswith(("#", "@")) or not _names(fields[0], identity):
            continue
        try:
            key = Key(fields[1], fields[2])
        except Refused:
            continue
        if key not in found:
            found.append(key)
    return tuple(found)


def _read(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def kept(path: Path | str, identity: str) -> tuple[Key, ...]:
    """The keys the product's known-hosts file at path files under identity, as ssh would find them: none where the
    file is not there. Never one of the person's own files."""
    identity = _identity(identity)
    return _keys_in(_read(_product(path)), identity)


def file(path: Path | str, identity: str, key: Key) -> bool:
    """File key under identity in the product's known-hosts file at path, under the lock on its folder that every
    writer through store takes: True where its line was added, False where that key was filed already. Where another
    key of its type is filed for the identity, `Changed` is raised and the file is left as it was, byte for byte.
    Nothing is ever replaced or removed: the line is added at the file's end, which is made 0600 where it is not
    there, its folder 0700 where that is not there either."""
    identity, key, where = _identity(identity), _key(key), _product(path)
    where.parent.mkdir(mode=paths.DIR_MODE, exist_ok=True)
    with store.locked(where):
        text = _read(where)
        filed = _keys_in(text, identity)
        if key in filed:
            return False
        same = tuple(other for other in filed if _family(other.type) == _family(key.type))
        if same:
            raise Changed(path, identity, same, key)
        added = ("\n" if text and not text.endswith("\n") else "") + line(identity, key) + "\n"
        _append(where, added.encode())
    return True


def _append(where: Path, data: bytes) -> None:
    """data written at the end of where, made 0600 where it is not there, and flushed to disk."""
    handle = os.open(where, os.O_WRONLY | os.O_APPEND | os.O_CREAT, paths.FILE_MODE)
    try:
        while data:
            data = data[os.write(handle, data) :]
        os.fsync(handle)
    finally:
        os.close(handle)


def _scratch(path: Path | str) -> Path:
    """A scratch file's path, with ~ expanded: never the product's known-hosts file or one of the person's own."""
    if _real(path) == _real(runner.KNOWN_HOSTS) or _persons(path):
        raise Refused(f"{path} is a known-hosts file that is kept, never a first use's scratch file")
    return Path(path).expanduser()


def fresh(directory: Path | str, identity: str) -> Path:
    """An empty scratch file, mode 0600, in directory (the run's own), named for identity: the one a first connection's
    ssh may add one line to, and the path a first use's Target names. Refused where one is there already."""
    identity = _identity(identity)
    scratch = _scratch(_real(directory) / f"{SCRATCH_PREFIX}{identity}")
    try:
        handle = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, paths.FILE_MODE)
    except FileExistsError:
        raise Refused(f"{scratch} is there already, where a first use's scratch file is made fresh") from None
    try:
        os.fchmod(handle, paths.FILE_MODE)
    finally:
        os.close(handle)
    return scratch


def taken(path: Path | str, identity: str) -> Key:
    """The key a first connection's ssh wrote into the scratch file at path: exactly one line, under identity - by its
    name or as ssh hashes it - and the key's type and body alone, as ssh writes one. A file with no line, more than one,
    a line under another name or more than one, or a line that is no key OpenSSH files is refused, and nothing is filed:
    the caller files the key this returns with `file`."""
    identity = _identity(identity)
    scratch = _scratch(path)
    try:
        handle = os.open(scratch, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise Refused(f"{path} is not there: no first connection wrote a host key, and nothing was filed") from None
    except OSError as error:
        # A link where the scratch file goes (ELOOP under O_NOFOLLOW), or a file this account may not read.
        raise Refused(f"{path} cannot be read as a scratch file ({error.strerror}): nothing was filed") from None
    with os.fdopen(handle, "rb") as stream:
        data = stream.read(SCRATCH_MOST + 1)
    if len(data) > SCRATCH_MOST:
        raise Refused(f"{path} holds more than a first connection writes: nothing was filed")
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        raise Refused(f"{path} holds what a first connection never writes: nothing was filed") from None
    lines = text.removesuffix("\n").split("\n") if text else []
    if not lines:
        raise Refused(f"the first connection to {identity} wrote no host key into {path}: nothing was filed")
    if len(lines) > 1:
        raise Refused(f"{path} holds {len(lines)} lines, where a first connection writes one: nothing was filed")
    fields = lines[0].split(" ")
    if len(fields) != 3 or fields[0].startswith("@"):
        raise Refused(
            f"the line in {path} is not a known-hosts line as a first connection writes one: nothing was filed"
        )
    name = fields[0]
    if name != identity and not (name.startswith(HASHED) and _names(name, identity)):
        raise Refused(f"the line in {path} files a key under another name than {identity}: nothing was filed")
    try:
        return Key(fields[1], fields[2])
    except Refused:
        raise Refused(f"the line in {path} holds no key OpenSSH files: nothing was filed") from None
