"""An OpenPGP detached signature, checked against one vendored key: what a VM downloads that its vendor signs - Claude
Code's release manifest, Ubuntu's SHA256SUMS - is verified here, on this machine, before a digest from it is handed to
the VM (docs/architecture/custom-code.md §7.15). Stdlib only, and only as much of OpenPGP (RFC 4880, the version 4
parts of RFC 9580) as those two files need; anything else is refused by name rather than half understood:

- one ASCII-armored signature packet, version 4, of a binary document (type 0x00), by an RSA key, over SHA-256 or
  SHA-512, made by the vendored key whose fingerprint is given, and by no other;
- the key is found in the vendored file by its version 4 fingerprint, computed from its own packet, never by the key
  ID the signature names;
- the RSA step builds the PKCS #1 v1.5 block the digest must give and compares it whole with what the signature
  raises to the key's exponent: building and comparing, never parsing what came out, which is the way past the
  forgeries a lenient parser of that block lets through.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time

# A hash's OpenPGP ID: (its name, the DER prefix PKCS #1 v1.5 puts before its digest).
_HASHES = {
    8: ("sha256", bytes.fromhex("3031300d060960864801650304020105000420")),
    10: ("sha512", bytes.fromhex("3051300d060960864801650304020305000440")),
}
_RSA = (1, 3)  # RSA, and RSA sign-only (deprecated, still RSA)
_SMALLEST_KEY = 2048
# The hashed subpackets understood: creation time, expiry, issuer, issuer fingerprint. Another one marked critical is
# a rule this module does not know, and the signature is refused.
_CREATED, _EXPIRES, _ISSUER, _ISSUER_FPR = 2, 3, 16, 33
# A key's own expiry, in its self-signatures; and the signature types that bind a key to itself, and revoke it.
_KEY_EXPIRES = 9
_SELF_CERTIFIED = (0x10, 0x11, 0x12, 0x13, 0x1F)
_REVOKED = 0x20
# A signature made this much after this machine's clock is one its clock disagrees with: an hour or two of skew is
# no forgery, a day ahead is.
_SKEW = 86400


class Refused(ValueError):
    """A signature that does not verify, or that is not one this module checks; the message says which."""


def _crc24(data: bytes) -> int:
    crc = 0xB704CE
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def dearmor(text: str | bytes, kind: str) -> bytes:
    """The bytes of the one ASCII-armored block of ``kind`` (``PUBLIC KEY BLOCK``, ``SIGNATURE``) that ``text`` is,
    its checksum held to them where it has one. Nothing but whitespace may come before or after it."""
    if isinstance(text, bytes):
        try:
            text = text.decode("ascii")
        except UnicodeDecodeError:
            raise Refused("the armor is not ASCII") from None
    lines = [line.rstrip("\r") for line in text.strip().split("\n")]
    if not lines or lines[0].strip() != f"-----BEGIN PGP {kind}-----":
        raise Refused(f"not one ASCII-armored PGP {kind.lower()}")
    if lines[-1].strip() != f"-----END PGP {kind}-----":
        raise Refused(f"the PGP {kind.lower()} does not end where its armor does, or something follows it")
    body = lines[1:-1]
    if any(line.startswith("-----") for line in body):
        raise Refused(f"more than one armored block where one PGP {kind.lower()} is expected")
    # Armor headers (Version:, Comment:) up to the blank line that ends them.
    blank = next((at for at, line in enumerate(body) if not line.strip()), None)
    if blank is not None:
        if any(": " not in line for line in body[:blank]):
            raise Refused("the armor's headers are not Key: value lines")
        body = body[blank + 1 :]
    checksum = None
    if body and body[-1].startswith("="):
        checksum, body = body[-1][1:].strip(), body[:-1]
    try:
        data = base64.b64decode("".join(line.strip() for line in body), validate=True)
    except (binascii.Error, ValueError):
        raise Refused("the armor's body is not base64") from None
    if checksum is not None:
        try:
            said = int.from_bytes(base64.b64decode(checksum, validate=True), "big")
        except (binascii.Error, ValueError):
            raise Refused("the armor's checksum is not base64") from None
        if said != _crc24(data):
            raise Refused("the armor's checksum does not match its body")
    return data


def packets(data: bytes) -> list[tuple[int, bytes]]:
    """Each packet of ``data``: (tag, body). Old and new headers; never a partial or indeterminate length, which
    neither a key nor a signature has."""
    found, at = [], 0
    while at < len(data):
        head = data[at]
        if not head & 0x80:
            raise Refused(f"no OpenPGP packet at byte {at}")
        if head & 0x40:
            tag, first = head & 0x3F, _byte(data, at + 1)
            if first < 192:
                length, at = first, at + 2
            elif first < 224:
                length, at = ((first - 192) << 8) + _byte(data, at + 2) + 192, at + 3
            elif first == 255:
                length, at = int.from_bytes(_bytes(data, at + 2, 4), "big"), at + 6
            else:
                raise Refused("a packet of partial lengths")
        else:
            kind = head & 3
            if kind == 3:
                raise Refused("a packet of indeterminate length")
            tag, size = (head >> 2) & 0x0F, 1 << kind
            length, at = int.from_bytes(_bytes(data, at + 1, size), "big"), at + 1 + size
        found.append((tag, _bytes(data, at, length)))
        at += length
    return found


def _byte(data: bytes, at: int) -> int:
    return _bytes(data, at, 1)[0]


def _bytes(data: bytes, at: int, count: int) -> bytes:
    if at + count > len(data):
        raise Refused("a packet runs past the end of its data")
    return data[at : at + count]


def _mpi(data: bytes, at: int) -> tuple[int, int]:
    bits = int.from_bytes(_bytes(data, at, 2), "big")
    count = (bits + 7) // 8
    return int.from_bytes(_bytes(data, at + 2, count), "big"), at + 2 + count


def fingerprint(key: bytes) -> str:
    """A version 4 key packet's fingerprint, 40 hex digits: SHA-1 of 0x99, its two-byte length and its body."""
    if not key or key[0] != 4:
        raise Refused(f"a key of version {key[0] if key else '?'}, not 4")
    return hashlib.sha1(b"\x99" + len(key).to_bytes(2, "big") + key).hexdigest().upper()


def _normal(fpr: str) -> str:
    return "".join(str(fpr).split()).upper()


def _issuers(body: bytes) -> list[str]:
    """Who a signature packet says made it: each issuer key ID or fingerprint, hashed or not, in hex."""
    if len(body) < 6 or body[0] != 4:
        return []
    hashed_length = int.from_bytes(_bytes(body, 4, 2), "big")
    at = 6 + hashed_length
    unhashed_length = int.from_bytes(_bytes(body, at, 2), "big")
    named = []
    for part in (_bytes(body, 6, hashed_length), _bytes(body, at + 2, unhashed_length)):
        for subtype, _, sub in _subpackets(part):
            if subtype == _ISSUER and len(sub) == 8:
                named.append(sub.hex().upper())
            elif subtype == _ISSUER_FPR and len(sub) == 21 and sub[:1] == b"\x04":
                named.append(sub[1:].hex().upper())
    return named


def _key_state(found: list[tuple[int, bytes]], at: int, wanted: str, now: float) -> None:
    """Refused when the key at ``found[at]`` is revoked by itself, or its latest self-signature says it has expired,
    as of ``now``. Only signatures the key made count: a certification by another key says nothing of its life."""
    created = int.from_bytes(_bytes(found[at][1], 1, 4), "big")
    latest = None
    for tag, body in found[at + 1 :]:
        if tag in (6, 14):
            break
        if tag != 2 or len(body) < 6 or body[0] != 4:
            continue
        if not any(wanted.endswith(name) for name in _issuers(body)):
            continue
        if body[1] == _REVOKED:
            raise Refused(f"the key {wanted} is revoked")
        if body[1] in _SELF_CERTIFIED:
            subs = {subtype: sub for subtype, _, sub in _subpackets(_bytes(body, 6, int.from_bytes(body[4:6], "big")))}
            made = int.from_bytes(subs.get(_CREATED, b"\0"), "big")
            if latest is None or made >= latest[0]:
                latest = (made, subs.get(_KEY_EXPIRES))
    if latest and latest[1] and len(latest[1]) == 4:
        lasts = int.from_bytes(latest[1], "big")
        if lasts and created + lasts <= now:
            raise Refused(f"the key {wanted} has expired")


def rsa_key(keys: str | bytes, fpr: str, now: float | None = None) -> tuple[int, int]:
    """(n, e) of the primary key in the armored ``keys`` whose fingerprint is ``fpr``; Refused when there is none, it
    is no RSA key of 2048 bits or more, or it is revoked or expired."""
    wanted = _normal(fpr)
    found = packets(dearmor(keys, "PUBLIC KEY BLOCK"))
    for at, (tag, body) in enumerate(found):
        if tag == 6 and body[:1] == b"\x04" and fingerprint(body) == wanted:
            if _byte(body, 5) not in _RSA:
                raise Refused(f"the key {wanted} is not an RSA key")
            n, end = _mpi(body, 6)
            e, _ = _mpi(body, end)
            if n.bit_length() < _SMALLEST_KEY:
                raise Refused(f"the key {wanted} has {n.bit_length()} bits, fewer than {_SMALLEST_KEY}")
            _key_state(found, at, wanted, time.time() if now is None else now)
            return n, e
    raise Refused(f"no key {wanted} in the vendored keys")


def _subpackets(data: bytes) -> list[tuple[int, bool, bytes]]:
    """(type, critical, body) of each signature subpacket."""
    found, at = [], 0
    while at < len(data):
        first = _byte(data, at)
        if first < 192:
            length, at = first, at + 1
        elif first < 255:
            length, at = ((first - 192) << 8) + _byte(data, at + 1) + 192, at + 2
        else:
            length, at = int.from_bytes(_bytes(data, at + 1, 4), "big"), at + 5
        if length == 0:
            raise Refused("an empty signature subpacket")
        body = _bytes(data, at, length)
        found.append((body[0] & 0x7F, bool(body[0] & 0x80), body[1:]))
        at += length
    return found


def verify(data: bytes, signature: str | bytes, keys: str | bytes, fpr: str, now: float | None = None) -> None:
    """Return when ``signature`` is a good signature of ``data`` by the key ``fpr`` of ``keys``; Refused, saying why,
    otherwise."""
    found = packets(dearmor(signature, "SIGNATURE"))
    if len(found) != 1 or found[0][0] != 2:
        raise Refused("not one signature packet")
    body = found[0][1]
    version, kind, algorithm, hashed_with = _bytes(body, 0, 4)
    if version != 4:
        raise Refused(f"a version {version} signature, not 4")
    if kind != 0x00:
        raise Refused(f"a signature of type {kind:#04x}, not of a binary document")
    if algorithm not in _RSA:
        raise Refused(f"a signature by public-key algorithm {algorithm}, not RSA")
    if hashed_with not in _HASHES:
        raise Refused(f"a signature over hash algorithm {hashed_with}, not SHA-256 or SHA-512")
    hashed_length = int.from_bytes(_bytes(body, 4, 2), "big")
    hashed = _bytes(body, 6, hashed_length)
    at = 6 + hashed_length
    unhashed_length = int.from_bytes(_bytes(body, at, 2), "big")
    unhashed = _bytes(body, at + 2, unhashed_length)
    at += 2 + unhashed_length
    left16 = _bytes(body, at, 2)
    value, end = _mpi(body, at + 2)
    if end != len(body):
        raise Refused("the signature packet holds more than one signature value")

    wanted = _normal(fpr)
    created = expires = None
    named = []
    for subtype, critical, sub in _subpackets(hashed):
        if subtype == _CREATED and len(sub) == 4:
            created = int.from_bytes(sub, "big")
        elif subtype == _EXPIRES and len(sub) == 4:
            expires = int.from_bytes(sub, "big")
        elif subtype == _ISSUER_FPR:
            if len(sub) != 21 or sub[:1] != b"\x04":
                raise Refused("an issuer fingerprint that names no version 4 key")
            named.append(sub[1:].hex().upper())
        elif subtype == _ISSUER:
            if len(sub) != 8:
                raise Refused("an issuer key ID that names no key")
            named.append(sub.hex().upper())
        elif critical:
            raise Refused(f"a critical signature subpacket of type {subtype}, which is not checked here")
    # The key ID may also be among the unhashed ones, where it is only a hint: it must still name the key given.
    for subtype, _, sub in _subpackets(unhashed):
        if subtype == _ISSUER:
            if len(sub) != 8:
                raise Refused("an issuer key ID that names no key")
            named.append(sub.hex().upper())
    if created is None:
        raise Refused("a signature with no creation time")
    if not named or any(not wanted.endswith(name) for name in named):
        raise Refused(f"a signature by another key than {wanted}")
    moment = time.time() if now is None else now
    if created > moment + _SKEW:
        raise Refused("a signature made after now, by this machine's clock")
    if expires and created + expires <= moment:
        raise Refused("a signature that has expired")

    name, prefix = _HASHES[hashed_with]
    digest = hashlib.new(name, data + body[: 6 + hashed_length] + b"\x04\xff" + (6 + hashed_length).to_bytes(4, "big"))
    digest = digest.digest()
    if not hmac.compare_digest(left16, digest[:2]):
        raise Refused("the data is not what was signed")
    n, e = rsa_key(keys, wanted, moment)
    size = (n.bit_length() + 7) // 8
    if value >= n:
        raise Refused("a signature value out of the key's range")
    padding = size - 3 - len(prefix) - len(digest)
    if padding < 8:
        raise Refused("a key too small for this hash")
    expected = b"\x00\x01" + b"\xff" * padding + b"\x00" + prefix + digest
    if not hmac.compare_digest(pow(value, e, n).to_bytes(size, "big"), expected):
        raise Refused("the data is not what was signed")


def openpgp_verified(data: str, signature: str, keys: str, fpr: str) -> str:
    """``data`` once ``signature`` verifies it as the key ``fpr`` of ``keys`` signed it: the filter. ``data`` and
    ``signature`` are the text the target fetched (uri's content), ``keys`` the vendored armored key file. A signature
    that does not verify is a ValueError naming why, which fails the task."""
    if not isinstance(data, str) or not isinstance(signature, str):
        raise Refused("the data and the signature are each a text, as uri returns one")
    verify(data.encode("utf-8"), signature, keys, fpr)
    return data
