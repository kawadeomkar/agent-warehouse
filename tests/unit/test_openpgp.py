"""lib/claude_vm/openpgp.py: a vendor's detached signature, checked with a vendored key, against the real files the VMs
download - Ubuntu's SHA256SUMS and a Claude Code release manifest, each with its signature, kept in tests/unit/openpgp/
as the vendors published them - and against signatures a key generated here makes wrong one way at a time."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from signing import Signer, armor, mpi, packet, subpacket

from claude_vm import openpgp

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "openpgp"
UBUNTU = (
    (FIXTURES / "ubuntu-SHA256SUMS").read_bytes(),
    (FIXTURES / "ubuntu-SHA256SUMS.gpg").read_text(),
    (REPO / "roles" / "proxmox_template" / "files" / "ubuntu-cloudimage.asc").read_text(),
    "D2EB 4462 6FDD C30B 513D 5BB7 1A5D 6C4C 7DB8 7C81",
)
CLAUDE = (
    (FIXTURES / "claude-code-2.1.311-manifest.json").read_bytes(),
    (FIXTURES / "claude-code-2.1.311-manifest.json.sig").read_text(),
    (REPO / "roles" / "claude_code" / "files" / "claude-code.asc").read_text(),
    "31DD DE24 DDFA B679 F42D 7BD2 BAA9 29FF 1A7E CACE",
)
DATA = b'{"version": "2.1.400"}\n'


@pytest.fixture(scope="module")
def signer() -> Signer:
    return Signer()


@pytest.mark.parametrize("vendor", [UBUNTU, CLAUDE], ids=["ubuntu", "claude-code"])
def test_a_vendor_s_own_signature_verifies(vendor):
    openpgp.verify(*vendor)


@pytest.mark.parametrize("vendor", [UBUNTU, CLAUDE], ids=["ubuntu", "claude-code"])
def test_one_byte_of_the_data_changed_is_refused(vendor):
    data, signature, keys, fpr = vendor
    changed = data[:100] + bytes([data[100] ^ 1]) + data[101:]
    with pytest.raises(openpgp.Refused, match="^the data is not what was signed$"):
        openpgp.verify(changed, signature, keys, fpr)


@pytest.mark.parametrize("vendor", [UBUNTU, CLAUDE], ids=["ubuntu", "claude-code"])
def test_one_byte_of_the_signature_changed_is_refused(vendor):
    data, signature, keys, fpr = vendor
    ((tag, body),) = openpgp.packets(openpgp.dearmor(signature, "SIGNATURE"))
    # A byte of the signature's value, near its end: the armor's checksum is made again, so the value is what fails.
    changed = body[:-5] + bytes([body[-5] ^ 1]) + body[-4:]
    with pytest.raises(openpgp.Refused, match="^the data is not what was signed$"):
        openpgp.verify(data, armor("SIGNATURE", packet(tag, changed)), keys, fpr)


def test_the_right_signature_with_another_key_vendored_is_refused():
    data, signature, _, fpr = CLAUDE
    with pytest.raises(openpgp.Refused, match="^no key 31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE in the vendored keys$"):
        openpgp.verify(data, signature, UBUNTU[2], fpr)


def test_a_signature_by_another_key_is_refused_whichever_key_signed_it():
    data, signature, _, _ = CLAUDE
    with pytest.raises(
        openpgp.Refused, match="^a signature by another key than D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81$"
    ):
        openpgp.verify(data, signature, UBUNTU[2], UBUNTU[3])


def test_a_key_of_our_own_signs_as_a_vendor_s_does(signer):
    openpgp.verify(DATA, signer.sign(DATA), signer.public(), signer.fingerprint)
    openpgp.verify(DATA, signer.sign(DATA, hashed_with=8), signer.public(), signer.fingerprint)


@pytest.mark.parametrize(
    ("make", "why"),
    [
        (lambda s: s.packet(DATA, head=s.head(version=3)), "^a version 3 signature, not 4$"),
        (lambda s: s.packet(DATA, head=s.head(kind=1)), "^a signature of type 0x01, not of a binary document$"),
        (lambda s: s.packet(DATA, head=s.head(kind=0x13)), "^a signature of type 0x13, not of a binary document$"),
        (
            lambda s: s.packet(DATA, head=s.head(hashed_with=2), hashed_with=2, value=1),
            "^a signature over hash algorithm 2, not SHA-256 or SHA-512$",
        ),
        (
            lambda s: s.packet(DATA, head=s.head(extra=subpacket(99, b"x", critical=True))),
            "^a critical signature subpacket of type 99",
        ),
        (lambda s: s.packet(DATA, head=s.head(created=1, extra=subpacket(3, (60).to_bytes(4, "big")))), "expired"),
        (lambda s: s.packet(DATA) + s.packet(DATA), "^not one signature packet$"),
        (lambda s: packet(6, s.body), "^not one signature packet$"),
    ],
    ids=["v3", "text", "certification", "sha1", "critical", "expired", "two", "a-key"],
)
def test_a_signature_this_module_does_not_check_is_refused_by_name(signer, make, why):
    with pytest.raises(openpgp.Refused, match=why):
        openpgp.verify(DATA, armor("SIGNATURE", make(signer)), signer.public(), signer.fingerprint)


def test_a_signature_naming_no_issuer_is_refused(signer):
    hashed = subpacket(2, (1_700_000_000).to_bytes(4, "big"))
    body = bytes([4, 0, 1, 10]) + len(hashed).to_bytes(2, "big") + hashed + b"\x00\x00" + b"\x00\x00" + mpi(1)
    with pytest.raises(openpgp.Refused, match="^a signature by another key than"):
        openpgp.verify(DATA, armor("SIGNATURE", packet(2, body)), signer.public(), signer.fingerprint)


@pytest.mark.parametrize(
    ("issuer", "why"),
    [(subpacket(16, b""), "an issuer key ID that names no key"), (subpacket(33, b"\x04"), "names no version 4 key")],
)
def test_an_issuer_that_names_no_key_is_refused(signer, issuer, why):
    # An empty issuer ends every fingerprint: taken for a name, it would name every key.
    hashed = subpacket(2, (1_700_000_000).to_bytes(4, "big")) + issuer
    body = bytes([4, 0, 1, 10]) + len(hashed).to_bytes(2, "big") + hashed + b"\x00\x00" + b"\x00\x00" + mpi(1)
    with pytest.raises(openpgp.Refused, match=why):
        openpgp.verify(DATA, armor("SIGNATURE", packet(2, body)), signer.public(), signer.fingerprint)


def test_a_signature_made_after_now_is_refused(signer):
    import time

    tomorrow = signer.sign(DATA, head=signer.head(created=int(time.time()) + 2 * 86400))
    with pytest.raises(openpgp.Refused, match="made after now"):
        openpgp.verify(DATA, tomorrow, signer.public(), signer.fingerprint)
    # An hour or two of clock skew is no forgery.
    openpgp.verify(
        DATA, signer.sign(DATA, head=signer.head(created=int(time.time()) + 3600)), signer.public(), signer.fingerprint
    )


def _key_with(signer: Signer, by: Signer, kind: int, extra: bytes = b"") -> str:
    """signer's public key with one signature over it, of kind, made by ``by``: its value is never checked, only who
    made it and what it says of the key's life."""
    body = by.head(kind=kind, created=1_700_000_100, extra=extra) + b"\x00\x00" + b"\x00\x00" + mpi(1)
    return armor(
        "PUBLIC KEY BLOCK", packet(6, signer.body) + packet(13, b"A test key <t@example.invalid>") + packet(2, body)
    )


def test_a_key_revoked_or_expired_by_its_own_self_signature_is_refused(signer):
    signature = signer.sign(DATA)
    with pytest.raises(openpgp.Refused, match="is revoked"):
        openpgp.verify(DATA, signature, _key_with(signer, signer, 0x20), signer.fingerprint)
    # Its latest self-certification says it lived one second.
    expired = _key_with(signer, signer, 0x13, subpacket(9, (1).to_bytes(4, "big")))
    with pytest.raises(openpgp.Refused, match="has expired"):
        openpgp.verify(DATA, signature, expired, signer.fingerprint)
    # A key that lives on verifies.
    lives = _key_with(signer, signer, 0x13, subpacket(9, (10**9).to_bytes(4, "big")))
    openpgp.verify(DATA, signature, lives, signer.fingerprint)


def test_another_key_s_word_on_a_key_s_life_counts_for_nothing(signer):
    stranger = Signer()
    signature = signer.sign(DATA)
    for kind, extra in ((0x20, b""), (0x10, subpacket(9, (1).to_bytes(4, "big")))):
        openpgp.verify(DATA, signature, _key_with(signer, stranger, kind, extra), signer.fingerprint)


def test_a_key_smaller_than_2048_bits_is_refused():
    small = Signer(bits=1024)
    with pytest.raises(openpgp.Refused, match="has 1024 bits, fewer than 2048"):
        openpgp.verify(DATA, small.sign(DATA), small.public(), small.fingerprint)


def test_a_forgery_a_lenient_parser_would_take_is_refused():
    # Bleichenbacher's forgery against a key of exponent 3: a cube that starts as the right PKCS #1 block and ends in
    # garbage. A verifier that parsed the block it got back, stopping after the digest, would take it; this one builds
    # the whole block the digest must give and compares it.
    weak = Signer(exponent=3)
    head = weak.head(hashed_with=8)
    trailer = b"\x04\xff" + len(head).to_bytes(4, "big")
    digest = hashlib.sha256(DATA + head + trailer).digest()
    wanted = b"\x00\x01\xff\x00" + bytes.fromhex("3031300d060960864801650304020105000420") + digest
    size = (weak.n.bit_length() + 7) // 8
    root = _cube_root(int.from_bytes(wanted + b"\xff" * (size - len(wanted)), "big"))
    assert (root**3).to_bytes(size, "big").startswith(wanted)
    forged = weak.packet(DATA, head=head, hashed_with=8, value=root)
    with pytest.raises(openpgp.Refused, match="^the data is not what was signed$"):
        openpgp.verify(DATA, armor("SIGNATURE", forged), weak.public(), weak.fingerprint)


def _cube_root(value: int) -> int:
    """The integer cube root of value, rounded down."""
    low, high = 0, 1 << (value.bit_length() // 3 + 1)
    while low < high:
        mid = (low + high + 1) // 2
        if mid**3 <= value:
            low = mid
        else:
            high = mid - 1
    return low


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (lambda text: text + "trailing\n", "something follows it"),
        (lambda text: "leading\n" + text, "not one ASCII-armored PGP signature"),
        (lambda text: text + text, "something follows it|more than one armored block"),
        (lambda text: text.replace("-----BEGIN PGP SIGNATURE-----", "-----BEGIN PGP MESSAGE-----"), "not one"),
        (lambda text: text.replace("\n\n", "\nnot a header\n\n", 1), "headers are not Key: value lines"),
    ],
    ids=["trailing", "leading", "twice", "a-message", "bad-header"],
)
def test_an_armor_that_is_not_one_signature_is_refused(signer, change, why):
    with pytest.raises(openpgp.Refused, match=why):
        openpgp.verify(DATA, change(signer.sign(DATA)), signer.public(), signer.fingerprint)


def test_an_armor_whose_checksum_does_not_match_is_refused(signer):
    signature = armor("SIGNATURE", signer.packet(DATA), crc=0x123456)
    with pytest.raises(openpgp.Refused, match="checksum does not match"):
        openpgp.verify(DATA, signature, signer.public(), signer.fingerprint)


def test_the_filter_hands_back_the_text_it_verified_and_refuses_what_is_not_text(signer):
    text = DATA.decode()
    assert openpgp.openpgp_verified(text, signer.sign(DATA), signer.public(), signer.fingerprint) == text
    with pytest.raises(openpgp.Refused, match="each a text"):
        openpgp.openpgp_verified(None, signer.sign(DATA), signer.public(), signer.fingerprint)
    with pytest.raises(ValueError):
        openpgp.openpgp_verified(text + " ", signer.sign(DATA), signer.public(), signer.fingerprint)


def test_a_fingerprint_is_the_one_gpg_prints():
    # gpg prints Ubuntu's as D2EB 4462 6FDD C30B 513D  5BB7 1A5D 6C4C 7DB8 7C81, which is what ubuntu.com publishes.
    keys = openpgp.packets(openpgp.dearmor(UBUNTU[2], "PUBLIC KEY BLOCK"))
    assert [openpgp.fingerprint(body) for tag, body in keys if tag == 6] == ["D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81"]
