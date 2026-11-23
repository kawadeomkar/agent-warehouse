"""The vault: one age file of named secrets, encrypted whole (lib/claude_vm/fleet/sealed.py and secrets.py;
docs/architecture/engine.md §16.9).

The closed list of names, each with its scope and kind, and the entry a host's or a machine's own is kept under. A
put, a read and a removal with keys the test makes, the file armored age at 0600 beside `fleet.json`; a file sealed to
another key refused with one sentence, and a file changed by one byte, at any byte, refused and never opened; an unknown
name refused, by its name only where it has a name's shape, the value never in the message; a value outside its kind
refused; two writers under the lock lose neither change; a writer killed before its rename leaves the old file; a newer
format refused by the vault's name, and a vault whose own number differs from `fleet.json`'s refused by name. A key
that no longer opens the file reads nothing and writes nothing.

The invariant with its own test: the plaintext is never on disk. After each case every file under the test's own
directory, its home among them, is read for every fake secret and every key the case used, and none is in anything the
case printed, its children's output included. Every refusal is read for them too.
"""

from __future__ import annotations

import ast
import base64
import json
import os
import signal
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pyrage
import pytest
from aw_kit import environment, imports_of
from harness import REPO
from pyrage import x25519

from claude_vm import init
from claude_vm.fleet import overrides, paths, record, registry, sealed, secrets
from claude_vm.fleet.record import FleetRecord
from claude_vm.schema import patterns
from claude_vm.secret import Secret

LIB = REPO / "lib"
SEALED = REPO / "lib" / "claude_vm" / "fleet" / "sealed.py"
FLEET = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
# The closed list, as the card names it.
NAMES = (
    "token",
    "vm_password",
    "become_password",
    "anthropic_api_key",
    "github_token",
    "tailscale_auth_key",
    "claude_setup_token",
    "tailscale_api_token",
    "tailscale_oauth_client_id",
    "tailscale_oauth_client_secret",
)
# Made-up secrets of low entropy, one of each kind, which nothing may write in clear or say.
TOKEN = "00000000-0000-4000-8000-00000000d7d7"
PASSWORD = "made up password 0000"
WORD = "made-up-token-0000"
AUTH_KEY = "made-up-auth-key-0000-0000"
OTHER_WORD = "made-up-token-1111"
# Made-up values each kind refuses; never said either.
NOT_A_UUID = "00000000-0000-4000-8000-00000000D7D7"
NOT_A_WORD = "made-up token 0000"
API_TOKEN = "tskey-api-made-up-0000-0000"
FAKES = (TOKEN, PASSWORD, WORD, AUTH_KEY, OTHER_WORD, NOT_A_UUID, NOT_A_WORD, API_TOKEN)
# The two writers' tokens, one for each of thirty hosts on each side.
WRITERS = 30
WRITTEN = {f"token@{side}{n}": f"00000000-0000-4000-8000-{side}{n:011d}" for side in "ab" for n in range(WRITERS)}


def _where() -> paths.Home:
    """The test's own fleet home (AGENT_WAREHOUSE_HOME, set by conftest.py's aw_home)."""
    return paths.home(REPO)


def _nowhere(root: Path, values: list[str]) -> None:
    """No file under root holds any of values in clear."""
    for folder, _dirs, names in os.walk(root):
        for name in names:
            found = Path(folder) / name
            if found.is_symlink() or not found.is_file():
                continue
            data = found.read_bytes()
            held = [value for value in values if value.encode() in data]
            assert not held, f"{found} holds a secret in clear"


@pytest.fixture(autouse=True)
def seen(tmp_path: Path, capfd) -> Iterator[list[str]]:
    """Every secret a case uses, the fakes and the keys it makes: none in a file under its own directory, its home among
    them, nor in anything it or its children printed, once it ends."""
    used = list(FAKES) + list(WRITTEN.values())
    yield used
    _nowhere(tmp_path, used)
    out, err = capfd.readouterr()
    assert not [value for value in used if value in out or value in err], "a secret was printed"


def _key(seen: list[str]) -> Secret:
    made = str(x25519.Identity.generate())
    seen.append(made)
    return Secret(made)


@pytest.fixture
def key(seen: list[str]) -> Secret:
    return _key(seen)


@pytest.fixture
def fleet(home: Path) -> paths.Home:
    """A fleet's record, which a vault is kept beside."""
    where = _where()
    record.write(where, record.first(FLEET))
    return where


def _refused(error: pytest.ExceptionInfo, seen: list[str]) -> str:
    """The refusal's sentence, held to say no secret."""
    said = str(error.value)
    assert not [value for value in seen if value in said], "a refusal said a secret"
    return said


def _plaintext(where: paths.Home, key: Secret) -> dict:
    """The vault's plaintext, opened here in memory with pyrage alone."""
    identity = x25519.Identity.from_str(key.reveal())
    return json.loads(pyrage.decrypt(sealed.path(where).read_bytes(), [identity]))


def _seal_by_hand(where: paths.Home, key: Secret, document: bytes) -> None:
    """A vault.age made of document as it stands, sealed to key: what sealed.py would never write."""
    recipient = x25519.Identity.from_str(key.reveal()).to_public()
    sealed.path(where).write_bytes(pyrage.encrypt(document, [recipient], armored=True))


def _revealed(found: dict[str, Secret]) -> dict[str, str]:
    assert all(isinstance(value, Secret) for value in found.values())
    return {name: value.reveal() for name, value in found.items()}


# ---------------------------------------------------------------- the closed list ----


def test_the_list_is_closed_and_each_name_has_its_scope_kind_and_description() -> None:
    assert secrets.names() == NAMES
    scopes = {each.name: each.scope for each in secrets.ROWS}
    assert scopes == {
        **dict.fromkeys(NAMES, "global"),
        "token": "host",
        "become_password": "machine",
    }
    kinds = {each.name: each.kind for each in secrets.ROWS}
    assert kinds["token"] == "uuid" and kinds["tailscale_auth_key"] == "auth_key"
    assert kinds["vm_password"] == kinds["become_password"] == "password"
    settings = {each.name for each in registry.rows()}
    for each in secrets.ROWS:
        assert patterns.full("secret_name", each.name), each.name
        assert each.description.strip() and each.scope in registry.SCOPES and each.kind in secrets.KINDS
        # A secret is never a setting, so settings and the vault never name one thing two ways.
        assert each.name not in settings, each.name
        assert secrets.row(each.name) is each
    assert secrets.row("vault_github_token") is None and secrets.row("github_username") is None


@pytest.mark.parametrize(
    ("given", "why"),
    [
        (("Token", "host", "uuid", "x"), "a secret's name is"),
        (("token\n", "host", "uuid", "x"), "a secret's name is"),
        (("token", "fleet", "uuid", "x"), "scope is one of"),
        (("token", "host", "text", "x"), "kind is one of"),
        (("token", "host", "uuid", " "), "has a description"),
    ],
)
def test_a_row_is_held_as_it_is_made(given, why) -> None:
    with pytest.raises(ValueError, match=why):
        secrets.Row(*given)


def test_the_kinds_and_a_machine_s_name_are_held_equal_to_where_they_come_from() -> None:
    assert secrets.KINDS["uuid"].pattern == init._UUID.pattern
    assert secrets.KINDS["auth_key"].pattern == init._AUTH_KEY.pattern
    assert secrets.MACHINE == overrides.MACHINE.pattern


@pytest.mark.parametrize(
    ("kind", "taken", "refused"),
    [
        ("uuid", [TOKEN], [NOT_A_UUID, TOKEN + "\n", TOKEN[:-1], "", "{" + TOKEN + "}", " " + TOKEN]),
        (
            "password",
            [PASSWORD, "x", "p" * 1024, "pässwörd-0000"],
            ["", PASSWORD + "\n", "made\tup", "made\x85up", "made up", "\ud800", "p" * 1025],
        ),
        ("word", [WORD, "x", "w" * 4096, "a!b~c"], ["", NOT_A_WORD, WORD + "\n", "w" * 4097, "wörd-0000", "a\x7fb"]),
        ("auth_key", [AUTH_KEY, "tskey-auth-made-up-0000"], [API_TOKEN, "short-0000", AUTH_KEY + "\n", "a b" * 10]),
    ],
)
def test_each_kind_takes_its_values_whole_and_refuses_the_rest(kind, taken, refused) -> None:
    each = next(row for row in secrets.ROWS if row.kind == kind)
    assert all(secrets.held(each, value) for value in taken)
    assert not [value for value in refused if secrets.held(each, value)]


def test_an_entry_is_a_name_or_its_owner_s_and_reads_back() -> None:
    assert secrets.entry("github_token") == "github_token"
    assert secrets.entry("token", host="lab2") == "token@lab2"
    assert secrets.entry("become_password", machine="pi-4.lan") == "become_password@pi-4.lan"
    for made in ("github_token", "token@lab2", "become_password@pi-4.lan"):
        each, owner = secrets.parsed(made)
        assert secrets.entry(each.name, **({} if owner is None else {each.scope: owner})) == made


@pytest.mark.parametrize(
    ("name", "owner", "said"),
    [
        ("nonesuch", {}, "error: nonesuch is no secret the vault keeps: it keeps token, vm_password,"),
        ("vault_github_token", {}, "error: vault_github_token is no secret the vault keeps"),
        (WORD, {}, "error: that is no secret's name: a name the vault keeps is a secret's name in the vault"),
        (API_TOKEN, {}, "error: that is no secret's name"),
        ("n" * 65, {}, "error: that is no secret's name"),
        ("token", {}, "error: token is each host's own: name the host it is for"),
        ("token", {"machine": "pi"}, "error: token is each host's own, never a machine's"),
        ("token", {"host": "Lab"}, "error: the host token is for is not a host's name"),
        ("token", {"host": "lab\n"}, "error: the host token is for is not a host's name"),
        ("token", {"host": ""}, "error: the host token is for is not a host's name"),
        ("token", {"host": WORD + "@x"}, "error: the host token is for is not a host's name"),
        ("become_password", {}, "error: become_password is each adopted machine's own: name the machine"),
        ("become_password", {"host": "lab"}, "error: become_password is each adopted machine's own, never a host's"),
        ("become_password", {"machine": "pi/4"}, "error: the machine become_password is for is no machine's name"),
        ("become_password", {"machine": "pi\n"}, "error: the machine become_password is for is no machine's name"),
        ("github_token", {"host": "lab"}, "error: github_token is one for the whole fleet"),
        ("github_token", {"machine": "pi"}, "error: github_token is one for the whole fleet"),
    ],
)
def test_an_entry_is_refused_by_name_only_where_it_has_a_name_s_shape(name, owner, said, seen) -> None:
    with pytest.raises(secrets.Refused) as refused:
        secrets.entry(name, **owner)
    assert _refused(refused, seen).startswith(said)


@pytest.mark.parametrize(
    "key",
    ["token", "token@", "github_token@lab", "become_password@", f"{WORD}@lab", "token@lab@lab", "token@La b", 42],
)
def test_an_entry_read_back_is_held_as_one_made(key, seen) -> None:
    with pytest.raises(secrets.Refused) as refused:
        secrets.parsed(key)
    _refused(refused, seen)


# ---------------------------------------------------------------- a put, a read and a removal ----


def test_a_put_a_read_and_a_removal(fleet, key: Secret) -> None:
    assert sealed.read(fleet, key) is None
    sealed.create(fleet, key)
    assert sealed.read(fleet, key) == {}
    sealed.put(fleet, key, "github_token", Secret(WORD))
    sealed.put(fleet, key, "token", Secret(TOKEN), host="lab2")
    sealed.put(fleet, key, "become_password", Secret(PASSWORD), machine="pi")
    sealed.put(fleet, key, "tailscale_auth_key", Secret(AUTH_KEY))
    assert _revealed(sealed.read(fleet, key)) == {
        "github_token": WORD,
        "token@lab2": TOKEN,
        "become_password@pi": PASSWORD,
        "tailscale_auth_key": AUTH_KEY,
    }
    sealed.put(fleet, key, "github_token", Secret(OTHER_WORD))
    assert sealed.remove(fleet, key, "become_password", machine="pi") is True
    assert _revealed(sealed.read(fleet, key)) == {
        "github_token": OTHER_WORD,
        "token@lab2": TOKEN,
        "tailscale_auth_key": AUTH_KEY,
    }
    before = sealed.path(fleet).read_bytes()
    assert sealed.remove(fleet, key, "become_password", machine="pi") is False
    assert sealed.path(fleet).read_bytes() == before, "a removal of what is not there writes nothing"
    sealed.write(fleet, key, {"vm_password": Secret(PASSWORD)})
    assert _revealed(sealed.read(fleet, key)) == {"vm_password": PASSWORD}


def test_the_file_is_one_armored_age_file_of_the_whole_map_at_0600_beside_fleet_json(fleet, key: Secret) -> None:
    sealed.create(fleet, key)
    found = sealed.path(fleet)
    assert found == paths.fleet_dir(fleet) / "vault.age" == record.path(fleet).parent / "vault.age"
    assert stat.S_IMODE(found.stat().st_mode) == 0o600
    assert stat.S_IMODE(found.parent.stat().st_mode) == 0o700
    sealed.put(fleet, key, "token", Secret(TOKEN), host="lab2")
    sealed.put(fleet, key, "anthropic_api_key", Secret(WORD))
    text = found.read_text(encoding="ascii")
    assert text.startswith("-----BEGIN AGE ENCRYPTED FILE-----\n")
    assert text.endswith("-----END AGE ENCRYPTED FILE-----\n")
    assert _plaintext(fleet, key) == {"format": 1, "secrets": {"anthropic_api_key": WORD, "token@lab2": TOKEN}}
    # The whole map in one ciphertext to one recipient: the age header under the armor holds one X25519 stanza, beside
    # the stanza of random grease age adds to every header.
    binary = base64.b64decode("".join(text.splitlines()[1:-1]))
    header = binary.split(b"\n---", 1)[0].split(b"\n")
    assert header[0] == b"age-encryption.org/v1"
    stanzas = [line.split(b" ")[1] for line in header if line.startswith(b"-> ")]
    assert [each for each in stanzas if not each.endswith(b"-grease")] == [b"X25519"]
    assert sorted(each.name for each in found.parent.iterdir()) == ["fleet.json", "vault.age"], "no scratch file left"
    assert stat.S_IMODE(found.stat().st_mode) == 0o600


def test_a_vault_is_made_once_and_beside_its_fleet_s_record(home, key: Secret, seen) -> None:
    where = _where()
    # No fleet's record: nothing to read, and nothing is made, no folder either.
    assert sealed.read(where, key) is None
    for attempt in (
        lambda: sealed.create(where, key),
        lambda: sealed.put(where, key, "github_token", Secret(WORD)),
        lambda: sealed.write(where, key, {}),
        lambda: sealed.remove(where, key, "github_token"),
    ):
        with pytest.raises(sealed.Refused) as refused:
            attempt()
        assert _refused(refused, seen) == (
            f"error: {record.path(where)} is not there: a vault is kept beside its fleet's record, so there is no"
            " vault to read or write"
        )
    assert not paths.fleet_dir(where).exists()
    record.write(where, record.first(FLEET))
    # A record and no vault: a write is refused, and the vault is made by create alone, once.
    for attempt in (
        lambda: sealed.put(where, key, "github_token", Secret(WORD)),
        lambda: sealed.write(where, key, {}),
        lambda: sealed.remove(where, key, "github_token"),
    ):
        with pytest.raises(sealed.Refused) as refused:
            attempt()
        assert "is not there: the fleet has no vault yet" in _refused(refused, seen)
    assert not sealed.path(where).exists()
    sealed.create(where, key)
    before = sealed.path(where).read_bytes()
    with pytest.raises(sealed.Refused) as refused:
        sealed.create(where, key)
    assert "is there already: a vault is made once" in _refused(refused, seen)
    assert sealed.path(where).read_bytes() == before
    # A vault with no record beside it is no vault to read.
    record.path(where).unlink()
    with pytest.raises(sealed.Refused) as refused:
        sealed.read(where, key)
    assert "a vault is kept beside its fleet's record" in _refused(refused, seen)


def test_a_link_where_the_fleet_s_folder_goes_is_refused_and_nothing_is_written_through_it(
    home, tmp_path: Path, key: Secret, seen
) -> None:
    where = _where()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (elsewhere / "fleet.json").write_text(json.dumps({"format": 1, "fleet": FLEET, "stores": dict(record.KNOWN)}))
    where.root.mkdir(exist_ok=True)
    paths.fleet_dir(where).symlink_to(elsewhere)
    with pytest.raises(sealed.Refused) as refused:
        sealed.create(where, key)
    assert "is a link, where a folder of the fleet's goes" in _refused(refused, seen)
    assert sorted(each.name for each in elsewhere.iterdir()) == ["fleet.json"]


# ---------------------------------------------------------------- what does not open ----


def test_a_file_sealed_to_another_key_is_refused_with_one_sentence_and_nothing_is_written(fleet, seen) -> None:
    mine, theirs = _key(seen), _key(seen)
    sealed.create(fleet, mine)
    sealed.put(fleet, mine, "github_token", Secret(WORD))
    before = sealed.path(fleet).read_bytes()
    sentence = (
        f"error: {sealed.path(fleet)} does not open with this key: it is sealed to another key, or it was changed"
        " after it was written, so nothing is read or written"
    )
    for attempt in (
        lambda: sealed.read(fleet, theirs),
        lambda: sealed.put(fleet, theirs, "github_token", Secret(OTHER_WORD)),
        lambda: sealed.write(fleet, theirs, {"github_token": Secret(OTHER_WORD)}),
        lambda: sealed.remove(fleet, theirs, "github_token"),
    ):
        with pytest.raises(sealed.Unreadable) as refused:
            attempt()
        assert _refused(refused, seen) == sentence
    assert sealed.path(fleet).read_bytes() == before


def test_a_put_whose_key_no_longer_opens_the_file_reads_nothing_and_writes_nothing(fleet, seen) -> None:
    old, new = _key(seen), _key(seen)
    sealed.create(fleet, old)
    sealed.put(fleet, old, "github_token", Secret(WORD))
    # A rotation came between (D13's): the same map, sealed to a new key.
    _seal_by_hand(fleet, new, json.dumps({"format": 1, "secrets": {"github_token": WORD}}).encode())
    rotated = sealed.path(fleet).read_bytes()
    with pytest.raises(sealed.Unreadable) as refused:
        sealed.put(fleet, old, "anthropic_api_key", Secret(OTHER_WORD))
    assert "does not open with this key" in _refused(refused, seen)
    assert sealed.path(fleet).read_bytes() == rotated
    assert _revealed(sealed.read(fleet, new)) == {"github_token": WORD}


def test_a_file_changed_by_one_byte_anywhere_is_refused_and_never_opened(fleet, key: Secret, seen) -> None:
    sealed.create(fleet, key)
    sealed.put(fleet, key, "github_token", Secret(WORD))
    whole = sealed.path(fleet).read_bytes()
    said = set()
    for at in range(len(whole)):
        changed = bytearray(whole)
        changed[at] ^= 0x01
        sealed.path(fleet).write_bytes(bytes(changed))
        with pytest.raises(sealed.Unreadable) as refused:
            sealed.read(fleet, key)
        said.add(_refused(refused, seen).split(": ", 2)[-1])
    assert said <= {
        "it is not an armored age file",
        "it is sealed to another key, or it was changed after it was written, so nothing is read or written",
    }, said


@pytest.mark.parametrize(
    ("made", "why"),
    [
        (lambda whole: b"", "it is not an armored age file"),
        (lambda whole: whole[: len(whole) // 2], "does not open with this key"),
        (lambda whole: whole[:-2], "does not open with this key"),
        (lambda whole: whole + b"more\n", "does not open with this key"),
        (lambda whole: b" " + whole, "it is not an armored age file"),
        (lambda whole: b"not a vault\n", "it is not an armored age file"),
        (lambda whole: b"\xff\xfe" * 8, "it is not an armored age file"),
        (lambda whole: b"-" * (sealed.MOST + 1), "it is larger than one is"),
    ],
    ids=["empty", "half", "end-marker-cut", "more", "space-first", "text", "binary", "too-large"],
)
def test_a_file_cut_short_or_that_is_no_vault_is_refused_by_its_path(fleet, key: Secret, made, why, seen) -> None:
    sealed.create(fleet, key)
    sealed.put(fleet, key, "github_token", Secret(WORD))
    found = sealed.path(fleet)
    found.write_bytes(made(found.read_bytes()))
    with pytest.raises(sealed.Unreadable) as refused:
        sealed.read(fleet, key)
    said = _refused(refused, seen)
    assert said.startswith(f"error: {found} ") and why in said


def test_an_age_file_that_is_not_armored_is_refused(fleet, key: Secret, seen) -> None:
    recipient = x25519.Identity.from_str(key.reveal()).to_public()
    sealed.path(fleet).write_bytes(pyrage.encrypt(b'{"format": 1, "secrets": {}}', [recipient]))
    with pytest.raises(sealed.Unreadable) as refused:
        sealed.read(fleet, key)
    assert "it is not an armored age file" in _refused(refused, seen)


def test_a_folder_where_the_vault_goes_is_refused(fleet, key: Secret, seen) -> None:
    sealed.path(fleet).mkdir()
    with pytest.raises(sealed.Unreadable) as refused:
        sealed.read(fleet, key)
    assert _refused(refused, seen) == f"error: {sealed.path(fleet)} is not a vault: it is a folder"
    with pytest.raises(sealed.Refused):
        sealed.create(fleet, key)


@pytest.mark.parametrize(
    "given",
    [lambda key: "AGE-SECRET-KEY-1NOTAKEY", lambda key: key.reveal() + "\n", lambda key: "", lambda key: WORD],
    ids=["not-bech32", "newline", "empty", "a-token"],
)
def test_a_key_that_is_no_age_key_is_refused_without_being_said(fleet, key: Secret, given, seen) -> None:
    sealed.create(fleet, key)
    text = given(key)
    if text:
        seen.append(text)
    with pytest.raises(sealed.Refused) as refused:
        sealed.read(fleet, Secret(text))
    assert _refused(refused, seen) == "error: the key given for the vault is not an age key: nothing is read or written"


def test_a_key_and_a_value_are_each_a_secret(fleet, key: Secret) -> None:
    sealed.create(fleet, key)
    with pytest.raises(TypeError):
        sealed.read(fleet, key.reveal())
    with pytest.raises(TypeError):
        sealed.put(fleet, key, "github_token", WORD)
    with pytest.raises(TypeError):
        sealed.write(fleet, key, {"github_token": WORD})
    with pytest.raises(TypeError):
        sealed.write(fleet, key, [("github_token", Secret(WORD))])


# ---------------------------------------------------------------- names and values ----


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({"nonesuch": Secret(WORD)}, "error: nonesuch is no secret the vault keeps"),
        ({WORD: Secret(WORD)}, "error: that is no secret's name"),
        ({"token": Secret(TOKEN)}, "error: token is each host's own: name the host it is for"),
        ({"github_token@lab": Secret(WORD)}, "error: github_token is one for the whole fleet"),
        ({"github_token": Secret(NOT_A_WORD)}, "error: that value of github_token is not one word of printable ASCII"),
        ({"token@lab": Secret(NOT_A_UUID)}, "error: that value of token@lab is not a UUID in lower case"),
        (
            {"tailscale_auth_key": Secret(API_TOKEN)},
            "error: that value of tailscale_auth_key is not a key as Tailscale",
        ),
        ({"vm_password": Secret(PASSWORD + "\n")}, "error: that value of vm_password is not one line of text"),
        ({"vm_password": Secret("")}, "error: that value of vm_password is not one line of text"),
    ],
)
def test_a_write_holds_each_name_to_the_list_and_each_value_to_its_kind(fleet, key: Secret, given, said, seen) -> None:
    sealed.create(fleet, key)
    sealed.put(fleet, key, "github_token", Secret(WORD))
    before = sealed.path(fleet).read_bytes()
    with pytest.raises(sealed.Refused) as refused:
        sealed.write(fleet, key, {"anthropic_api_key": Secret(OTHER_WORD), **given})
    assert _refused(refused, seen).startswith(said)
    assert sealed.path(fleet).read_bytes() == before


@pytest.mark.parametrize(
    ("name", "value", "owner", "said"),
    [
        ("nonesuch", WORD, {}, "error: nonesuch is no secret the vault keeps"),
        (WORD, WORD, {}, "error: that is no secret's name"),
        ("token", TOKEN, {}, "error: token is each host's own"),
        ("token", NOT_A_UUID, {"host": "lab"}, "error: that value of token@lab is not a UUID"),
        ("become_password", PASSWORD + "\n", {"machine": "pi"}, "error: that value of become_password@pi is not"),
        ("tailscale_auth_key", API_TOKEN, {}, "error: that value of tailscale_auth_key is not a key as Tailscale"),
        ("claude_setup_token", NOT_A_WORD, {}, "error: that value of claude_setup_token is not one word"),
    ],
)
def test_a_put_holds_its_name_and_its_value_and_writes_nothing_it_refuses(
    fleet, key: Secret, name, value, owner, said, seen
) -> None:
    sealed.create(fleet, key)
    before = sealed.path(fleet).read_bytes()
    with pytest.raises(sealed.Refused) as refused:
        sealed.put(fleet, key, name, Secret(value), **owner)
    assert _refused(refused, seen).startswith(said)
    assert sealed.path(fleet).read_bytes() == before


# ---------------------------------------------------------------- the format's one number ----


def test_a_newer_vault_is_refused_by_name_before_anything_is_decrypted(fleet, key: Secret, seen) -> None:
    sealed.create(fleet, key)
    before = sealed.path(fleet).read_bytes()
    record.write(fleet, FleetRecord(1, FLEET, {**record.KNOWN, "vault": 2}))
    sentence = (
        "error: this fleet keeps its vault in format 2, where this program reads 1 at most: run the release of"
        " agent-warehouse that wrote it, or a newer one"
    )
    for attempt in (
        lambda: sealed.read(fleet, key),
        lambda: sealed.put(fleet, key, "github_token", Secret(WORD)),
        lambda: sealed.write(fleet, key, {}),
        lambda: sealed.remove(fleet, key, "github_token"),
        # Never decrypted: a key that would not open it is refused for the format all the same.
        lambda: sealed.read(fleet, _key(seen)),
    ):
        with pytest.raises(sealed.Newer) as refused:
            attempt()
        assert _refused(refused, seen) == sentence
    assert sealed.path(fleet).read_bytes() == before


def test_a_newer_fleet_record_is_refused_as_newer(fleet, key: Secret, seen) -> None:
    sealed.create(fleet, key)
    record.path(fleet).write_text(json.dumps({"format": 2, "fleet": FLEET, "stores": dict(record.KNOWN)}))
    with pytest.raises(sealed.Newer) as refused:
        sealed.read(fleet, key)
    assert "its record (fleet.json) in format 2" in _refused(refused, seen)
    record.path(fleet).write_text("not a record")
    with pytest.raises(sealed.Unreadable) as refused:
        sealed.put(fleet, key, "github_token", Secret(WORD))
    assert "is not a fleet's record" in _refused(refused, seen)


def test_a_vault_whose_own_number_differs_from_fleet_json_s_is_refused_by_name(fleet, key: Secret, seen) -> None:
    _seal_by_hand(fleet, key, json.dumps({"format": 2, "secrets": {"github_token": WORD}}).encode())
    before = sealed.path(fleet).read_bytes()
    sentence = (
        f"error: {sealed.path(fleet)} holds the vault in format 2, where fleet.json numbers it 1: the two are one"
        " number, so nothing is read or written"
    )
    for attempt in (lambda: sealed.read(fleet, key), lambda: sealed.put(fleet, key, "github_token", Secret(WORD))):
        with pytest.raises(sealed.Unreadable) as refused:
            attempt()
        assert _refused(refused, seen) == sentence
    assert sealed.path(fleet).read_bytes() == before


def test_every_write_is_at_fleet_json_s_number_and_leaves_fleet_json_as_it_is(fleet, key: Secret) -> None:
    written = record.path(fleet).read_bytes()
    sealed.create(fleet, key)
    sealed.put(fleet, key, "github_token", Secret(WORD))
    sealed.remove(fleet, key, "github_token")
    assert sealed.FORMAT == record.KNOWN["vault"] == record.read(fleet).stores["vault"] == 1
    assert _plaintext(fleet, key)["format"] == 1
    assert record.path(fleet).read_bytes() == written


@pytest.mark.parametrize(
    ("document", "why"),
    [
        (b"not json", "it does not read as JSON"),
        (b"\xff\xfe", "it does not read as JSON"),
        (b"[" * 100000, "it does not read as JSON"),
        (b"[]", "it is not a mapping"),
        (b'{"secrets": {}}', "it has no format number"),
        (b'{"format": true, "secrets": {}}', "it has no format number"),
        (b'{"format": 1}', "it is the format number and the map of secrets, and nothing else"),
        (b'{"format": 1, "secrets": [], "more": 1}', "it is the format number and the map of secrets"),
        (b'{"format": 1, "secrets": {}, "more": 1}', "it is the format number and the map of secrets"),
        (b'{"format": 1, "secrets": {"nonesuch": "x"}}', "holds what the vault never keeps: nonesuch is no secret"),
        (
            json.dumps({"format": 1, "secrets": {WORD: WORD}}).encode(),
            "holds what the vault never keeps: that is no secret's name",
        ),
        (b'{"format": 1, "secrets": {"token": "x"}}', "holds what the vault never keeps: token is each host's own"),
        (
            json.dumps({"format": 1, "secrets": {"github_token": NOT_A_WORD}}).encode(),
            "holds github_token, whose value is not one word",
        ),
        (b'{"format": 1, "secrets": {"github_token": 42}}', "holds github_token, whose value is not one word"),
    ],
)
def test_a_vault_that_opens_and_holds_what_is_no_vault_s_is_refused_without_saying_it(
    fleet, key: Secret, document, why, seen
) -> None:
    _seal_by_hand(fleet, key, document)
    with pytest.raises(sealed.Unreadable) as refused:
        sealed.read(fleet, key)
    said = _refused(refused, seen)
    assert said.startswith(f"error: {sealed.path(fleet)} ") and why in said, said


# ---------------------------------------------------------------- the lock and a kill ----


WRITER = """\
import sys
sys.path.insert(0, {lib!r})
from claude_vm.fleet import paths, sealed
from claude_vm.secret import Secret
where = paths.home({repo!r})
side = sys.argv[1]
key = Secret(sys.stdin.readline().strip())
for n in range({count}):
    sealed.put(where, key, "token", Secret(f"00000000-0000-4000-8000-{{side}}{{n:011d}}"), host=f"{{side}}{{n}}")
"""


def test_two_writers_under_the_lock_lose_neither_change(home, fleet, key: Secret) -> None:
    # Each puts thirty hosts' tokens of its own, at once: a put reads and writes under one lock, so each reads what the
    # other last wrote. Unlocked, a writer would put back the map it read before the other's last put, and lose it.
    sealed.create(fleet, key)
    script = WRITER.format(lib=str(LIB), repo=str(REPO), count=WRITERS)
    writers = [
        subprocess.Popen(
            [sys.executable, "-I", "-c", script, side], stdin=subprocess.PIPE, text=True, env=environment(home)
        )
        for side in "ab"
    ]
    try:
        for each in writers:
            assert each.stdin is not None
            each.stdin.write(key.reveal() + "\n")
            each.stdin.close()
        assert [each.wait(timeout=180) for each in writers] == [0, 0]
    finally:
        for each in writers:
            if each.poll() is None:
                each.kill()
                each.wait()
    assert _revealed(sealed.read(fleet, key)) == WRITTEN
    assert sorted(each.name for each in paths.fleet_dir(fleet).iterdir()) == ["fleet.json", "vault.age"]


KILLED = """\
import os, sys, time
sys.path.insert(0, {lib!r})
from claude_vm.fleet import paths, sealed
from claude_vm.secret import Secret
where = paths.home({repo!r})
key = Secret(sys.stdin.readline().strip())
value = Secret(sys.stdin.readline().strip())
def stopped(*args):
    print("renaming", flush=True)
    time.sleep(120)
os.replace = stopped
sealed.put(where, key, "github_token", value)
"""


def test_a_writer_killed_before_its_rename_leaves_the_old_file(home, fleet, key: Secret) -> None:
    sealed.create(fleet, key)
    sealed.put(fleet, key, "github_token", Secret(WORD))
    before = sealed.path(fleet).read_bytes()
    script = KILLED.format(lib=str(LIB), repo=str(REPO))
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=environment(home),
    )
    try:
        assert child.stdin is not None and child.stdout is not None
        child.stdin.write(f"{key.reveal()}\n{OTHER_WORD}\n")
        child.stdin.close()
        assert child.stdout.readline() == "renaming\n"
        os.kill(child.pid, signal.SIGKILL)
        assert child.wait(timeout=60) == -signal.SIGKILL
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    assert sealed.path(fleet).read_bytes() == before
    assert _revealed(sealed.read(fleet, key)) == {"github_token": WORD}
    # Its scratch file is left beside it, the ciphertext alone (the case's own scan reads it); the lock went with it.
    left = [each.name for each in paths.fleet_dir(fleet).iterdir() if each.name.endswith(".tmp")]
    assert len(left) == 1 and left[0].startswith(".vault.age.")
    sealed.put(fleet, key, "github_token", Secret(OTHER_WORD))
    assert _revealed(sealed.read(fleet, key)) == {"github_token": OTHER_WORD}


# ---------------------------------------------------------------- what the modules hold to ----


@pytest.mark.parametrize("module", ["claude_vm.fleet.sealed", "claude_vm.fleet.secrets"])
def test_the_modules_import_pyrage_only_when_they_need_it_and_the_standard_library_else(home, module: str) -> None:
    added = imports_of(module=module, home=home) - imports_of(module="claude_vm.fleet", home=home)
    assert module in added, sorted(added)
    assert sorted({name.split(".")[0] for name in added} - set(sys.stdlib_module_names) - {"claude_vm"}) == []
    assert not added & {"asyncio", "subprocess"}


def test_sealed_py_reads_a_secret_s_value_in_two_functions_alone() -> None:
    tree = ast.parse(SEALED.read_text())
    reading = sorted(
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and node.attr == "reveal"
    )
    assert reading == ["_identity", "_plain"]
    outside = [
        node.lineno
        for node in tree.body
        if not isinstance(node, ast.FunctionDef)
        for each in ast.walk(node)
        if isinstance(each, ast.Attribute) and each.attr == "reveal"
    ]
    assert outside == []


def test_sealed_py_writes_through_files_write_alone() -> None:
    # The one writer of a fleet's file (fleet/files.py), so a new file is at 0600 and its folder made, never a link.
    tree = ast.parse(SEALED.read_text())
    calls = sorted(
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("write", "replace", "write_text", "write_bytes", "rename", "mkdir")
    )
    assert calls == ["files.write"]
