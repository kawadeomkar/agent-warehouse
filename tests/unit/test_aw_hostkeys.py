"""Host keys by identity (lib/claude_vm/ssh/hostkeys.py; docs/architecture/engine.md §16.6).

Keys are made two ways: blobs written here for every type OpenSSH files, the security keys' among them, which no
ssh-keygen makes without a device; and real keys of the types ssh-keygen makes, by this machine's own ssh-keygen run by
its path, which reads and writes only the test's own directory and dials nothing: to check a fingerprint, a name as ssh
reads one (ssh-keygen -F) and a name as ssh hashes one (ssh-keygen -H). Every known-hosts file is in the test's own
directory, and HOME is the test's own (engine_guard), so the person's own files the module refuses are the test's. The
rules with a test of their own: no function takes an address, by its signatures; the person's own known-hosts files are
neither read nor written; nothing here removes or replaces a line, a deleted machine's above all.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import inspect
import os
import re
import shutil
import stat
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest

from claude_vm import checks, store
from claude_vm.ssh import hostkeys, runner

IDENTITY = "agent-warehouse-0f0e0d0c-0b0a-4908-8706-050403020100"
OTHER = "agent-warehouse-1f1e1d1c-1b1a-4918-9716-151413121110"
DELETED = "agent-warehouse-2f2e2d2c-2b2a-4928-a726-252423222120"
ADDRESS = "192.0.2.9"
CONTROL = Path("/aw-test/cp")


def _blob(kind: str, fill: bytes = b"\x01" * 32) -> str:
    """The base64 of a key blob of kind, as OpenSSH writes one: its type's name as an SSH string, then the key."""
    name = kind.encode()
    return base64.b64encode(struct.pack(">I", len(name)) + name + struct.pack(">I", len(fill)) + fill).decode()


def _key(kind: str = "ssh-ed25519", fill: bytes = b"\x01" * 32) -> hostkeys.Key:
    return hostkeys.Key(kind, _blob(kind, fill))


ONE = _key()
TWO = _key(fill=b"\x02" * 32)
RSA = _key("ssh-rsa", b"\x03" * 64)
P256 = _key("ecdsa-sha2-nistp256", b"\x04" * 65)
P384 = _key("ecdsa-sha2-nistp384", b"\x05" * 97)


def _keygen() -> str:
    found = shutil.which("ssh-keygen", path=os.defpath)
    if found is None:
        pytest.skip("this machine has no ssh-keygen")
    return found


def _ssh_keygen(*words: str | Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    """This machine's own ssh-keygen, by its path: it reads and writes only the files it is named."""
    return subprocess.run(
        [_keygen(), *map(str, words)], capture_output=True, text=True, stdin=subprocess.DEVNULL, check=check
    )


def _hashed(name: str, salt: bytes = b"\x07" * 20) -> str:
    """name as ssh hashes one (HashKnownHosts), written out here: |1|, the salt, |, HMAC-SHA1 of name under it."""
    digest = hmac.new(salt, name.encode(), "sha1").digest()
    return f"|1|{base64.b64encode(salt).decode()}|{base64.b64encode(digest).decode()}"


def _product(tmp_path: Path) -> Path:
    return tmp_path / "ssh" / "agent-warehouse.known_hosts"


# ---------------------------------------------------------------- a key line ----


@pytest.mark.parametrize("kind", sorted(checks.KNOWN_KEY_TYPES))
def test_a_key_of_each_known_type_is_parsed_and_its_comment_left_off(kind) -> None:
    body = _blob(kind)
    for text in (f"{kind} {body}", f"{kind} {body} root@alpha", f"{kind}  {body}  a comment of words "):
        key = hostkeys.parse(text)
        assert (key.type, key.body, key.text) == (kind, body, f"{kind} {body}")
    assert hostkeys.line(IDENTITY, key) == f"{IDENTITY} {kind} {body}"


@pytest.mark.parametrize(
    ("kind", "bits"), [("ed25519", None), ("ecdsa", 256), ("ecdsa", 384), ("ecdsa", 521), ("rsa", 2048)]
)
def test_a_real_key_is_parsed_and_its_fingerprint_is_the_one_ssh_keygen_shows(tmp_path, kind, bits) -> None:
    made = tmp_path / "key"
    _ssh_keygen("-q", "-t", kind, *(["-b", str(bits)] if bits else []), "-N", "", "-C", "root@alpha", "-f", made)
    key = hostkeys.parse(made.with_suffix(".pub").read_text().removesuffix("\n"))
    shown = _ssh_keygen("-l", "-E", "sha256", "-f", made.with_suffix(".pub")).stdout.split()
    assert key.fingerprint == shown[1]
    assert key.text == " ".join(made.with_suffix(".pub").read_text().split()[:2])


ED = f"ssh-ed25519 {_blob('ssh-ed25519')}"


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (f"no-pty {ED}", "starts with its type"),
        (f'command="true" {ED}', "starts with its type"),
        (f"{IDENTITY} {ED}", "starts with its type"),
        (f"@cert-authority * {ED}", "starts with its type"),
        (f"{ED} me\nssh-rsa {_blob('ssh-rsa')}", "line break or a control character"),
        (f"{ED} me\r", "line break or a control character"),
        (f"{ED}\n", "line break or a control character"),
        (f"{ED} \x1b]0;a title\x07", "line break or a control character"),
        (f"ssh-dss {_blob('ssh-dss')}", "starts with its type"),
        (f"ssh-ed25519-cert-v01@openssh.com {_blob('ssh-ed25519-cert-v01@openssh.com')}", "starts with its type"),
        ("ssh-ed25519 AAAA!!!!", "base64 of a ssh-ed25519 key"),
        ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5A", "base64 of a ssh-ed25519 key"),
        (f"ssh-ed25519 ={_blob('ssh-ed25519')}", "base64 of a ssh-ed25519 key"),
        (f"ssh-ed25519 {_blob('ssh-ed25519')}==", "base64 of a ssh-ed25519 key"),
        (f"ssh-rsa {_blob('ssh-ed25519')}", "base64 of a ssh-rsa key"),
        (f"ecdsa-sha2-nistp384 {_blob('ecdsa-sha2-nistp256')}", "base64 of a ecdsa-sha2-nistp384 key"),
        ("ssh-ed25519 " + base64.b64encode(struct.pack(">I", 11) + b"ssh-ed25519").decode(), "base64 of a"),
        ("ssh-ed25519", "its type and its body"),
        ("", "its type and its body"),
    ],
)
def test_a_line_that_is_no_public_key_is_refused(text, why) -> None:
    with pytest.raises(hostkeys.Refused, match=re.escape(why)) as refused:
        hostkeys.parse(text)
    # Nothing of the line is said back: a machine may have supplied it.
    assert "AAAA" not in str(refused.value) and "title" not in str(refused.value)


def test_a_key_is_refused_unless_it_is_text() -> None:
    for text in (None, b"ssh-ed25519 AAAA", 1):
        with pytest.raises(hostkeys.Refused):
            hostkeys.parse(text)  # type: ignore[arg-type]
    with pytest.raises(hostkeys.Refused, match="made by parse"):
        hostkeys.line(IDENTITY, ED)  # type: ignore[arg-type]


# ---------------------------------------------------------------- filed under the identity ----


def test_a_key_is_filed_once_under_the_identity_and_found(tmp_path) -> None:
    product = _product(tmp_path)
    assert hostkeys.kept(product, IDENTITY) == ()
    assert hostkeys.file(product, IDENTITY, ONE) is True
    assert product.read_text() == f"{IDENTITY} {ONE.text}\n"
    assert stat.S_IMODE(product.stat().st_mode) == 0o600 and stat.S_IMODE(product.parent.stat().st_mode) == 0o700
    assert hostkeys.kept(product, IDENTITY) == (ONE,)
    assert hostkeys.kept(product, OTHER) == ()
    # Filed again, the same key changes nothing.
    before = product.read_bytes()
    assert hostkeys.file(product, IDENTITY, ONE) is False
    assert product.read_bytes() == before


def test_a_key_of_another_type_is_filed_beside_the_first(tmp_path) -> None:
    product = _product(tmp_path)
    for key in (ONE, RSA, P256):
        assert hostkeys.file(product, IDENTITY, key) is True
    assert hostkeys.kept(product, IDENTITY) == (ONE, RSA, P256)
    assert product.read_text().splitlines() == [hostkeys.line(IDENTITY, key) for key in (ONE, RSA, P256)]


@pytest.mark.parametrize(("first", "second"), [(ONE, TWO), (P256, P384)], ids=["ed25519", "ecdsa-another-curve"])
def test_a_second_key_of_a_type_raises_and_the_file_is_unchanged_byte_for_byte(tmp_path, first, second) -> None:
    product = _product(tmp_path)
    hostkeys.file(product, IDENTITY, first)
    hostkeys.file(product, OTHER, second)
    before, written = product.read_bytes(), product.stat().st_mtime_ns
    with pytest.raises(hostkeys.Changed) as changed:
        hostkeys.file(product, IDENTITY, second)
    assert product.read_bytes() == before and product.stat().st_mtime_ns == written
    assert (changed.value.identity, changed.value.filed, changed.value.offered) == (IDENTITY, (first,), second)
    said = str(changed.value)
    assert first.fingerprint in said and second.fingerprint in said
    assert f"ssh-keygen -R {IDENTITY} -f {product}" in said
    assert first.body not in said and second.body not in said
    assert hostkeys.kept(product, IDENTITY) == (first,)


def test_the_way_out_of_a_changed_key_names_the_product_s_file_as_a_person_types_it(tmp_path, home) -> None:
    hostkeys.file(runner.KNOWN_HOSTS, IDENTITY, ONE)
    with pytest.raises(hostkeys.Changed, match=re.escape(f"ssh-keygen -R {IDENTITY} -f ~/.ssh/agent-warehouse")):
        hostkeys.file(runner.KNOWN_HOSTS, IDENTITY, TWO)
    spaced = tmp_path / "a folder" / "known hosts"
    hostkeys.file(spaced, IDENTITY, ONE)
    with pytest.raises(hostkeys.Changed, match=re.escape(f"-f '{spaced}'")):
        hostkeys.file(spaced, IDENTITY, TWO)


def test_a_line_is_added_after_a_last_line_with_no_end(tmp_path) -> None:
    product = _product(tmp_path)
    product.parent.mkdir()
    product.write_text(f"{OTHER} {RSA.text}")
    hostkeys.file(product, IDENTITY, ONE)
    assert product.read_text() == f"{OTHER} {RSA.text}\n{IDENTITY} {ONE.text}\n"


def test_a_file_that_is_not_there_files_nothing(tmp_path) -> None:
    assert hostkeys.kept(tmp_path / "none", IDENTITY) == ()
    assert not (tmp_path / "none").exists()


def test_a_key_ssh_filed_under_a_hashed_name_is_found_and_never_filed_twice(tmp_path) -> None:
    product = _product(tmp_path)
    product.parent.mkdir()
    product.write_text(f"{IDENTITY} {ONE.text}\n{OTHER} {RSA.text}\n")
    # ssh-keygen hashes every name in the file as ssh does under HashKnownHosts, keeping the original beside it.
    _ssh_keygen("-H", "-f", product)
    assert IDENTITY not in product.read_text() and product.read_text().startswith("|1|")
    assert hostkeys.kept(product, IDENTITY) == (ONE,) and hostkeys.kept(product, OTHER) == (RSA,)
    before = product.read_bytes()
    assert hostkeys.file(product, IDENTITY, ONE) is False
    with pytest.raises(hostkeys.Changed):
        hostkeys.file(product, IDENTITY, TWO)
    assert product.read_bytes() == before


@pytest.mark.parametrize(
    ("name", "names_it"),
    [
        (IDENTITY, True),
        (IDENTITY.upper(), True),
        (f"{ADDRESS},{IDENTITY}", True),
        (f"{IDENTITY},{ADDRESS}", True),
        ("agent-warehouse-*", True),
        ("agent-warehouse-0f0e0d0?-*", True),
        ("*", True),
        (_hashed(IDENTITY), True),
        (f"!{IDENTITY},agent-warehouse-*", False),
        (f"agent-warehouse-*,!{IDENTITY}", False),
        (f"[{IDENTITY}]:22", False),
        (f"{IDENTITY}x", False),
        (f"x{IDENTITY}", False),
        ("agent-warehouse-0f0e0d0?", False),
        (OTHER, False),
        (ADDRESS, False),
        (_hashed(OTHER), False),
        (_hashed(IDENTITY) + "x", False),
        (_hashed(IDENTITY, b"\x07" * 19), False),
        ("|1|!!!!|AAAA", False),
    ],
)
def test_a_name_is_read_as_ssh_reads_it(tmp_path, name, names_it) -> None:
    product = _product(tmp_path)
    product.parent.mkdir()
    product.write_text(f"{name} {ONE.text}\n")
    assert hostkeys.kept(product, IDENTITY) == ((ONE,) if names_it else ())
    # And as this machine's own ssh-keygen finds a host's key: the same function of OpenSSH's as ssh's own check.
    assert (_ssh_keygen("-F", IDENTITY, "-f", product, check=False).returncode == 0) is names_it


def test_comments_marks_markers_and_lines_of_no_key_are_passed_over(tmp_path) -> None:
    product = _product(tmp_path)
    product.parent.mkdir()
    product.write_text(
        f"# {IDENTITY} {ONE.text}\n"
        f"# agent-warehouse carried {IDENTITY}\n"
        f"@revoked {IDENTITY} {TWO.text}\n"
        f"@cert-authority {IDENTITY} {RSA.text}\n"
        f"{IDENTITY} ssh-dss {_blob('ssh-dss')}\n"
        f"{IDENTITY} ssh-ed25519 AAAA!!!!\n"
        f"{IDENTITY}\n"
        "\n"
        f"{IDENTITY} {P256.text} a comment\n"
        f"{IDENTITY}\t{RSA.text}\n"
    )
    assert hostkeys.kept(product, IDENTITY) == (P256, RSA)
    # A key of a type passed over is not filed, so one of that type is filed beside it.
    assert hostkeys.file(product, IDENTITY, ONE) is True


# ---------------------------------------------------------------- two writers ----


def test_a_writer_reads_the_file_only_once_it_holds_the_lock_on_its_folder(tmp_path) -> None:
    product = _product(tmp_path)
    product.parent.mkdir()
    outcome: list[object] = []

    def writer() -> None:
        try:
            outcome.append(hostkeys.file(product, IDENTITY, ONE))
        except hostkeys.Changed as changed:
            outcome.append(changed)

    with store.locked(product):
        thread = threading.Thread(target=writer)
        thread.start()
        time.sleep(0.5)
        assert outcome == [] and not product.exists()
        # What another writer filed while it held the lock: the waiting writer reads it once the lock is its own.
        product.write_text(hostkeys.line(IDENTITY, TWO) + "\n")
    thread.join(10)
    assert not thread.is_alive() and len(outcome) == 1 and isinstance(outcome[0], hostkeys.Changed)
    assert product.read_text() == hostkeys.line(IDENTITY, TWO) + "\n"


def _at_once(*calls) -> list[object]:
    """Each call run in a thread of its own, all let go at once: what each returned or raised."""
    start = threading.Barrier(len(calls))
    results: list[object] = [None] * len(calls)

    def run(at: int) -> None:
        start.wait()
        try:
            results[at] = calls[at]()
        except Exception as error:
            results[at] = error

    threads = [threading.Thread(target=run, args=(at,)) for at in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return results


def test_two_writers_at_once_lose_neither_line(tmp_path) -> None:
    identities = [f"agent-warehouse-{n:08x}-0b0a-4908-8706-050403020100" for n in range(16)]
    keys = [_key(fill=bytes([n + 1]) * 32) for n in range(16)]
    for attempt in range(5):
        product = tmp_path / str(attempt) / "agent-warehouse.known_hosts"
        product.parent.mkdir()
        calls = [lambda p=product, i=i, k=k: hostkeys.file(p, i, k) for i, k in zip(identities, keys, strict=True)]
        assert _at_once(*calls) == [True] * 16
        assert sorted(product.read_text().splitlines()) == sorted(
            hostkeys.line(i, k) for i, k in zip(identities, keys, strict=True)
        )


def test_two_writers_of_one_identity_at_once_file_one_key_and_the_other_is_told(tmp_path) -> None:
    for attempt in range(10):
        product = tmp_path / str(attempt) / "agent-warehouse.known_hosts"
        product.parent.mkdir()
        results = _at_once(*(lambda p=product, k=key: hostkeys.file(p, IDENTITY, k) for key in (ONE, TWO)))
        assert sorted(type(result).__name__ for result in results) == ["Changed", "bool"]
        [filed] = [key for key, result in zip((ONE, TWO), results, strict=True) if result is True]
        assert product.read_text() == hostkeys.line(IDENTITY, filed) + "\n"


# ---------------------------------------------------------------- a deleted machine's line ----


def test_a_deleted_machine_s_line_is_still_there_after_every_call(tmp_path) -> None:
    product = _product(tmp_path)
    product.parent.mkdir()
    kept = (
        f"# filed by hand\n{DELETED} {ONE.text}\n{_hashed(DELETED)} {RSA.text}\n# agent-warehouse carried {DELETED}\n"
        f"{OTHER} {P256.text}"
    )
    product.write_text(kept)

    def still_there() -> None:
        assert product.read_text().startswith(kept)
        assert hostkeys.kept(product, DELETED) == (ONE, RSA)

    still_there()
    assert hostkeys.file(product, DELETED, ONE) is False
    still_there()
    with pytest.raises(hostkeys.Changed):
        hostkeys.file(product, DELETED, TWO)
    still_there()
    with pytest.raises(hostkeys.Changed):
        hostkeys.file(product, OTHER, P384)
    still_there()
    scratch = hostkeys.fresh(tmp_path, IDENTITY)
    scratch.write_text(f"{IDENTITY} {TWO.text}\n")
    assert hostkeys.file(product, IDENTITY, hostkeys.taken(scratch, IDENTITY)) is True
    still_there()
    assert product.read_text() == f"{kept}\n{IDENTITY} {TWO.text}\n"


# ---------------------------------------------------------------- a first use ----


@pytest.mark.parametrize("hashed", [False, True], ids=["by-name", "hashed"])
def test_a_first_use_takes_the_one_line_ssh_wrote_and_it_is_filed_by_name(tmp_path, hashed) -> None:
    run = tmp_path / "run"
    run.mkdir()
    old = os.umask(0o277)
    try:
        scratch = hostkeys.fresh(run, IDENTITY)
    finally:
        os.umask(old)
    assert scratch.parent == run.resolve() and scratch.read_bytes() == b""
    assert stat.S_IMODE(scratch.stat().st_mode) == 0o600
    # The runner takes it as a first use's file, and names it as the one ssh may add a line to: a Target marked as a
    # route settled straight at the machine marks one (connections.target_for), which the runner connects to alone.
    target = runner.machine(IDENTITY, ADDRESS, "dev", CONTROL, known_hosts=scratch, first_use=True)
    target = dataclasses.replace(target, routed_as="direct")
    assert f'UserKnownHostsFile="{scratch}"' in runner.argv(target, ["true"])
    # As ssh writes one under HostKeyAlias: appended, the name or its hash, the key, a line end.
    with scratch.open("a") as stream:
        stream.write(f"{IDENTITY} {ONE.text}\n")
    if hashed:
        _ssh_keygen("-H", "-f", scratch)
        assert scratch.read_text().startswith("|1|") and len(scratch.read_text().splitlines()) == 1
    key = hostkeys.taken(scratch, IDENTITY)
    assert key == ONE
    product = _product(tmp_path)
    assert hostkeys.file(product, IDENTITY, key) is True
    # Filed under the identity by name, never the hash ssh wrote and never an address.
    assert product.read_text() == f"{IDENTITY} {ONE.text}\n"


def test_a_scratch_file_is_made_fresh_or_refused(tmp_path) -> None:
    hostkeys.fresh(tmp_path, IDENTITY)
    with pytest.raises(hostkeys.Refused, match="there already"):
        hostkeys.fresh(tmp_path, IDENTITY)
    # A link planted at its name is never followed.
    target = tmp_path / "elsewhere"
    (tmp_path / "linked").mkdir()
    (tmp_path / "linked" / f"known_hosts-{IDENTITY}").symlink_to(target)
    with pytest.raises(hostkeys.Refused, match="there already"):
        hostkeys.fresh(tmp_path / "linked", IDENTITY)
    assert not target.exists()
    assert hostkeys.fresh(tmp_path, OTHER).name == f"known_hosts-{OTHER}"
    with pytest.raises(hostkeys.Refused, match="no absolute path"):
        hostkeys.fresh("relative", IDENTITY)


@pytest.mark.parametrize(
    ("written", "why"),
    [
        ("", "wrote no host key"),
        (f"{IDENTITY} {ONE.text}\n{IDENTITY} {RSA.text}\n", "holds 2 lines"),
        (f"{IDENTITY} {ONE.text}\n\n", "holds 2 lines"),
        (f"\n{IDENTITY} {ONE.text}\n", "holds 2 lines"),
        (f"{OTHER} {ONE.text}\n", "under another name"),
        (f"{ADDRESS} {ONE.text}\n", "under another name"),
        (f"{IDENTITY},{ADDRESS} {ONE.text}\n", "under another name"),
        (f"agent-warehouse-* {ONE.text}\n", "under another name"),
        (f"{IDENTITY.upper()} {ONE.text}\n", "under another name"),
        (f"{_hashed(OTHER)} {ONE.text}\n", "under another name"),
        (f"@cert-authority {IDENTITY} {ONE.text}\n", "not a known-hosts line"),
        (f"{IDENTITY} {ONE.text} a comment\n", "not a known-hosts line"),
        (f"{IDENTITY}  {ONE.text}\n", "not a known-hosts line"),
        (f"{IDENTITY}\t{ONE.text}\n", "not a known-hosts line"),
        (f"{IDENTITY} ssh-ed25519 AAAA!!!!\n", "holds no key OpenSSH files"),
        (f"{IDENTITY} ssh-dss {_blob('ssh-dss')}\n", "holds no key OpenSSH files"),
        (f"{IDENTITY} {ONE.text}é\n", "never writes"),
        (f"{IDENTITY} {ONE.text}\n" + "#" * hostkeys.SCRATCH_MOST, "more than a first connection writes"),
    ],
)
def test_a_scratch_file_that_is_not_one_line_under_the_identity_is_refused_and_nothing_filed(
    tmp_path, written, why
) -> None:
    scratch = hostkeys.fresh(tmp_path, IDENTITY)
    scratch.write_text(written)
    with pytest.raises(hostkeys.Refused, match=re.escape(why)) as refused:
        hostkeys.taken(scratch, IDENTITY)
    assert scratch.read_text() == written
    assert not (tmp_path / "ssh").exists() and not runner.KNOWN_HOSTS.expanduser().exists()
    assert "AAAA" not in str(refused.value)


def test_a_scratch_file_that_is_not_there_or_is_a_link_is_refused(tmp_path) -> None:
    with pytest.raises(hostkeys.Refused, match="is not there"):
        hostkeys.taken(tmp_path / f"known_hosts-{IDENTITY}", IDENTITY)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text(f"{IDENTITY} {ONE.text}\n")
    (tmp_path / f"known_hosts-{IDENTITY}").symlink_to(elsewhere)
    with pytest.raises(hostkeys.Refused, match="cannot be read as a scratch file"):
        hostkeys.taken(tmp_path / f"known_hosts-{IDENTITY}", IDENTITY)


def test_a_kept_file_is_never_a_first_use_s_scratch_file(tmp_path, home) -> None:
    ssh = home / ".ssh"
    ssh.mkdir()
    for kept in (runner.KNOWN_HOSTS, *runner.PERSONS_KNOWN_HOSTS):
        kept.expanduser().write_text(f"{IDENTITY} {ONE.text}\n")
        with pytest.raises(hostkeys.Refused, match="never a first use's scratch file"):
            hostkeys.taken(kept, IDENTITY)
    (tmp_path / "linked").symlink_to(ssh / "agent-warehouse.known_hosts")
    with pytest.raises(hostkeys.Refused, match="never a first use's scratch file"):
        hostkeys.taken(tmp_path / "linked", IDENTITY)


# ---------------------------------------------------------------- the rules ----


def test_the_person_s_own_known_hosts_files_are_never_read_or_written(tmp_path, home) -> None:
    ssh = home / ".ssh"
    ssh.mkdir()
    for persons in runner.PERSONS_KNOWN_HOSTS:
        own = persons.expanduser()
        own.write_text(f"{IDENTITY} {ONE.text}\n")
        before = own.read_bytes()
        for path in (persons, own):
            with pytest.raises(hostkeys.Refused, match="your own known-hosts file"):
                hostkeys.kept(path, IDENTITY)
            with pytest.raises(hostkeys.Refused, match="your own known-hosts file"):
                hostkeys.file(path, OTHER, RSA)
        assert own.read_bytes() == before
    # Nor through a link to one.
    (tmp_path / "linked").symlink_to(ssh / "known_hosts")
    with pytest.raises(hostkeys.Refused, match="your own known-hosts file"):
        hostkeys.file(tmp_path / "linked", OTHER, RSA)
    assert (ssh / "known_hosts").read_text() == f"{IDENTITY} {ONE.text}\n"


# What the functions here take: none is an address, a host or a name that could be one.
PARAMETERS = {"text", "identity", "key", "path", "directory"}


def test_no_function_here_takes_an_address_by_its_signatures(tmp_path) -> None:
    public = {
        name: value
        for name, value in vars(hostkeys).items()
        if inspect.isfunction(value) and value.__module__ == hostkeys.__name__ and not name.startswith("_")
    }
    assert set(public) == {"parse", "line", "kept", "file", "fresh", "taken"}
    signatures = {name: inspect.signature(function) for name, function in public.items()}
    signatures["Key"] = inspect.signature(hostkeys.Key)
    signatures["Changed"] = inspect.signature(hostkeys.Changed)
    for name, signature in signatures.items():
        for parameter in signature.parameters.values():
            assert parameter.name in PARAMETERS | {"type", "body", "filed", "offered"}, (name, parameter.name)
            assert not re.search(r"(?i)address|ipv4|host", str(parameter.annotation)), (name, parameter)
    # And an address where an identity goes is refused by each that takes one, before anything is read or written.
    where = tmp_path / "keys"
    where.mkdir()
    product = _product(where)
    for call in (
        lambda: hostkeys.line(ADDRESS, ONE),
        lambda: hostkeys.kept(product, ADDRESS),
        lambda: hostkeys.file(product, ADDRESS, ONE),
        lambda: hostkeys.fresh(where, ADDRESS),
        lambda: hostkeys.taken(where / "scratch", ADDRESS),
    ):
        with pytest.raises(hostkeys.Refused, match="not a machine's identity"):
            call()
    assert list(where.iterdir()) == []


def test_a_fingerprint_is_sha256_of_the_key_s_blob_as_ssh_shows_one() -> None:
    blob = base64.b64decode(ONE.body)
    assert ONE.fingerprint == "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
