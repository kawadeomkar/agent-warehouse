"""The vault: `fleet/vault.age`, one age file of the fleet's named secrets, encrypted whole (docs/architecture/
engine.md §16.9).

The file's plaintext is one JSON document, `{"format": <n>, "secrets": {"<entry>": "<value>"}}`: the vault's format,
and each secret under its entry (fleet/secrets.py: its name, or `<name>@<host>` and `<name>@<machine>` for a host's and
an adopted machine's own) with its value. It is encrypted whole with age, armored, to the X25519 recipient that is the
public half of the key the caller hands in, so that no value can be read, or swapped for another, inside it without
that key. `read` decrypts the whole file and returns the map, each value a `Secret`; every write encrypts the whole map
again. The plaintext is never on disk: it is made and read in memory, and the file a write leaves, and the scratch file
beside it while it writes, hold the ciphertext alone.

The key is the caller's to find (fleet/vaultkey.py) and is handed in as a `Secret` that holds an age identity
(`AGE-SECRET-KEY-1...`): this module never looks for one, keeps one or says one.

**One number for the vault's format.** `fleet.json` numbers the vault (record.py's `stores["vault"]`), and that is the
number `record.refuse_newer` reads: a vault newer than this program reads is refused by name before anything is
decrypted. The vault's own `format` must equal it, and a vault whose number differs is refused by name. A write holds
the lock record.write holds for `fleet.json`, `store.locked` on the fleet's folder, across reading `fleet.json`, opening
the vault there and replacing it, so the two numbers are read and written under one lock; it writes the vault at
`fleet.json`'s number, which is this program's (`FORMAT`, the vault's in record.py's `KNOWN`) from the record's first
write, so at format 1 no write of the vault changes `fleet.json`. A vault is always beside its fleet's record: with no
`fleet.json` there is no vault to read, make or write.

**A write needs the key that opens what is there.** `create(where, key)` writes an empty map where there is no vault
yet; `write(where, key, secrets, expect=...)` replaces the map whole, only while the vault still holds `expect`, the map
the caller read, compared under the lock: a caller that read before another's put is refused (`Changed`) and the put
kept; `put` and `remove` change one entry of the map they read under the lock. Each opens the vault there with the key
under the lock before it writes, so a key that no longer opens it (a rotation came between, D13) reads nothing and
writes nothing. Every name is held to the closed list and every value to its kind before anything is written. The file
is replaced through `files.write`: written beside it, flushed and renamed over it, a new one at 0600 and one that is
there keeping its mode, so that a writer killed part way leaves the old vault whole.

Every refusal is a `Refused`, one sentence that names the file and never a value, nor anything of the plaintext but an
entry the list holds; the key is said by nothing. `pyrage` is imported only inside the functions that need it, and
`_plain` and `_identity` alone read a `Secret`'s value (tests/unit/test_aw_guards.py's REVEALS).
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator, Mapping
from pathlib import Path

from claude_vm import store
from claude_vm.fleet import files, paths, record, secrets
from claude_vm.secret import Secret
from claude_vm.text import plain_text

# The vault's file, in the fleet's folder, which a person copies to a second computer.
FILE = "vault.age"
# The vault's format that this program reads and writes: one number with fleet.json's for the vault.
FORMAT = record.KNOWN["vault"]
# The most of vault.age that is read, and that a write makes: a vault is a few kilobytes.
MOST = 1 << 20
# How an armored age file begins.
ARMOR = b"-----BEGIN AGE ENCRYPTED FILE-----"


# The vault cannot be read or written as asked: the sentence says why, never a value or the key. One class with the
# closed list's refusals (secrets.py), so that a caller catches every refusal of the vault's as one.
Refused = secrets.Refused


class Unreadable(Refused):
    """What is there is no vault this program reads, or does not open with the key: nothing is written over it."""


class Newer(Unreadable):
    """The vault, or its fleet's record, is in a format newer than this program reads."""


class Changed(Refused):
    """The vault no longer holds the map a write was given as read: another writer came between, and nothing is
    written. Read it again: its own class, so that a caller offers its change again on this refusal alone."""


def path(where: paths.Home) -> Path:
    """vault.age's place: the top of the fleet's folder, beside fleet.json."""
    return paths.fleet_dir(where) / FILE


def _said(found: Path) -> str:
    return plain_text(str(found))


def _identity(key: Secret):
    """key's age identity; Refused where it holds none. One of the two readers of a Secret's value here."""
    if not isinstance(key, Secret):
        raise TypeError(f"the vault's key is a Secret, not a {type(key).__name__}")
    import pyrage
    from pyrage import x25519

    try:
        return x25519.Identity.from_str(key.reveal())
    except pyrage.IdentityError:
        raise Refused("error: the key given for the vault is not an age key: nothing is read or written") from None


def _plain(entry: str, value: Secret) -> str:
    """value's plain text, held to the kind of entry's row: Refused, never saying it, where it is not one. The other
    reader of a Secret's value here, for the plaintext alone."""
    each, _owner = secrets.parsed(entry)
    if not isinstance(value, Secret):
        raise TypeError(f"the vault's values are each a Secret, not a {type(value).__name__}")
    text = value.reveal()
    if not secrets.held(each, text):
        raise Refused(f"error: that value of {entry} is not {secrets.KINDS[each.kind].said}: nothing is written")
    return text


def _checked(given: Mapping[str, Secret]) -> dict[str, Secret]:
    """given held to the closed list and each value to its kind, as a map of its own; Refused says the first entry or
    value that is not."""
    if not isinstance(given, Mapping):
        raise TypeError(f"the vault's secrets are a mapping, not a {type(given).__name__}")
    checked = {}
    for entry, value in given.items():
        _plain(entry, value)
        checked[entry] = value
    return checked


def _number(where: paths.Home) -> int:
    """fleet.json's number for the vault, held to what this program reads. Refused where there is no fleet.json;
    Newer where it, or the vault it numbers, is newer than this program reads; Unreadable where it is no record."""
    try:
        found = record.read(where)
    except record.Newer as error:
        raise Newer(str(error)) from None
    except record.Refused as error:
        raise Unreadable(str(error)) from None
    if found is None:
        raise Refused(
            f"error: {_said(record.path(where))} is not there: a vault is kept beside its fleet's record, so there is"
            " no vault to read or write"
        )
    refusal = record.refuse_newer(found, {"vault": FORMAT})
    if refusal is not None:
        raise Newer(refusal)
    return found.stores["vault"]


def _contents(found: Path, text: bytes, number: int) -> dict[str, Secret]:
    """The vault's plaintext as its map, held to format number and the closed list; Unreadable where it is not."""
    said = _said(found)
    try:
        data = json.loads(text.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Unreadable(f"error: {said} opens, and what it holds is no vault's: it does not read as JSON") from None
    if not isinstance(data, dict):
        raise Unreadable(f"error: {said} opens, and what it holds is no vault's: it is not a mapping")
    given = data.get("format")
    if not isinstance(given, int) or isinstance(given, bool) or given < 1:
        raise Unreadable(f"error: {said} opens, and what it holds is no vault's: it has no format number")
    if given != number:
        raise Unreadable(
            f"error: {said} holds the vault in format {given}, where {record.FILE} numbers it {number}: the two are"
            " one number, so nothing is read or written"
        )
    if set(data) != {"format", "secrets"} or not isinstance(data["secrets"], dict):
        raise Unreadable(
            f"error: {said} opens, and what it holds is no vault of format {number}: it is the format number and the"
            " map of secrets, and nothing else"
        )
    found_map = {}
    for entry, value in data["secrets"].items():
        try:
            each, _owner = secrets.parsed(entry)
        except secrets.Refused as error:
            said_why = str(error).removeprefix("error: ")
            raise Unreadable(f"error: {said} holds what the vault never keeps: {said_why}") from None
        if not isinstance(value, str) or not secrets.held(each, value):
            raise Unreadable(f"error: {said} holds {entry}, whose value is not {secrets.KINDS[each.kind].said}")
        found_map[entry] = Secret(value)
    return found_map


def _opened(where: paths.Home, identity, number: int | None = None) -> dict[str, Secret] | None:
    """The map in the vault at where, opened with identity; None where there is no vault. Where number is not given,
    the file is looked for before fleet.json's number is read: with no vault there is nothing to refuse."""
    found = path(where)
    try:
        with open(found, "rb") as stream:
            data = stream.read(MOST + 1)
    except FileNotFoundError:
        return None
    except IsADirectoryError:
        raise Unreadable(f"error: {_said(found)} is not a vault: it is a folder") from None
    if number is None:
        number = _number(where)
    if len(data) > MOST:
        raise Unreadable(f"error: {_said(found)} is not a vault: it is larger than one is")
    if not data.startswith(ARMOR):
        raise Unreadable(f"error: {_said(found)} is not a vault: it is not an armored age file")
    import pyrage

    try:
        text = pyrage.decrypt(data, [identity])
    except pyrage.DecryptError:
        raise Unreadable(
            f"error: {_said(found)} does not open with this key: it is sealed to another key, or it was changed after"
            " it was written, so nothing is read or written"
        ) from None
    return _contents(found, text, number)


def _plaintext(map_: Mapping[str, Secret], number: int) -> bytes:
    """The vault's plaintext: its format and its map, in memory alone."""
    document = {"format": number, "secrets": {entry: _plain(entry, map_[entry]) for entry in sorted(map_)}}
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("ascii")


def _seal(where: paths.Home, identity, map_: Mapping[str, Secret], number: int) -> None:
    """map_ encrypted whole to identity's recipient, armored, and put in place of the vault."""
    import pyrage

    text = pyrage.encrypt(_plaintext(map_, number), [identity.to_public()], armored=True)
    if len(text) > MOST:
        raise Refused(
            f"error: {_said(path(where))} would be larger than a vault is read at: nothing is written, and the vault"
            " is as it was"
        )
    files.write(path(where), text.decode("ascii"))


@contextlib.contextmanager
def _locked(where: paths.Home) -> Iterator[int]:
    """The lock on the fleet's folder, which fleet.json's writer holds too, for the block: yields fleet.json's number
    for the vault, read under it. Refused where there is no fleet.json, before any folder is made, or where a link
    stands where the fleet's folder goes."""
    _number(where)
    found = path(where)
    try:
        files.folder(found.parent)
    except files.Unsafe as error:
        raise Refused(str(error)) from None
    with store.locked(found):
        yield _number(where)


def _there(where: paths.Home, identity, number: int) -> dict[str, Secret]:
    """The map in the vault there, under the lock; Refused where there is none."""
    there = _opened(where, identity, number)
    if there is None:
        raise Refused(
            f"error: {_said(path(where))} is not there: the fleet has no vault yet, which is made with the fleet, so"
            " nothing is written"
        )
    return there


def create(where: paths.Home, key: Secret) -> None:
    """An empty vault at where, sealed to key: only where there is none, and beside the fleet's record."""
    identity = _identity(key)
    with _locked(where) as number:
        if os.path.lexists(path(where)):
            raise Refused(
                f"error: {_said(path(where))} is there already: a vault is made once, and nothing is written over it"
            )
        _seal(where, identity, {}, number)


def read(where: paths.Home, key: Secret) -> dict[str, Secret] | None:
    """The vault's map, each entry's value a Secret, opened with key; None where there is no vault. Takes no lock: a
    write replaces the file whole, so a reader finds the old vault or the new one."""
    return _opened(where, _identity(key))


def _same(found: Mapping[str, Secret], expect: Mapping[str, Secret]) -> bool:
    """Whether found and expect hold the same entries with the same values, compared by their plain text."""
    if found.keys() != expect.keys():
        return False
    return all(_plain(entry, found[entry]) == _plain(entry, expect[entry]) for entry in found)


def write(where: paths.Home, key: Secret, secrets_map: Mapping[str, Secret], *, expect: Mapping[str, Secret]) -> None:
    """The vault's map replaced whole by secrets_map, sealed to key, only while the vault still holds expect, the map
    the caller read (compare-and-swap, as the overrides' writer has): each entry of both held to the closed list and
    each value to its kind first, and the vault there opened with key and compared with expect under the lock before
    it is replaced. Changed, with nothing written, where another writer came between the caller's read and this."""
    identity = _identity(key)
    checked = _checked(secrets_map)
    expected = _checked(expect)
    with _locked(where) as number:
        if not _same(_there(where, identity, number), expected):
            raise Changed(
                f"error: {_said(path(where))} changed since it was read: nothing is written, so read it again"
            )
        _seal(where, identity, checked, number)


def put(
    where: paths.Home, key: Secret, name: str, value: Secret, *, host: str | None = None, machine: str | None = None
) -> None:
    """One secret put in the vault, under its entry (secrets.entry: its name, with the host or the machine its scope
    names), the rest kept: a read and a write under one short lock."""
    identity = _identity(key)
    entry = secrets.entry(name, host=host, machine=machine)
    _plain(entry, value)
    with _locked(where) as number:
        there = _there(where, identity, number)
        there[entry] = value
        _seal(where, identity, there, number)


def remove(where: paths.Home, key: Secret, name: str, *, host: str | None = None, machine: str | None = None) -> bool:
    """One secret taken out of the vault, the rest kept: a read and a write under one short lock. Whether it was
    there; where it was not, nothing is written."""
    identity = _identity(key)
    entry = secrets.entry(name, host=host, machine=machine)
    with _locked(where) as number:
        there = _there(where, identity, number)
        if there.pop(entry, None) is None:
            return False
        _seal(where, identity, there, number)
        return True
