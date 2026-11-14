"""OpenPGP keys and detached signatures made for a test, by an RSA key generated for it: what lib/claude_vm/openpgp.py
verifies, without a private key in the repository. Each signature can be made wrong in one way at a time, to see the
verifier refuse it."""

from __future__ import annotations

import base64
import hashlib
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

HASHES = {8: (hashes.SHA256, "sha256"), 10: (hashes.SHA512, "sha512"), 2: (hashes.SHA1, "sha1")}


def mpi(value: int) -> bytes:
    bits = value.bit_length()
    return bits.to_bytes(2, "big") + value.to_bytes((bits + 7) // 8, "big")


def packet(tag: int, body: bytes) -> bytes:
    """A new-format packet with a five-byte length."""
    return bytes([0xC0 | tag, 0xFF]) + len(body).to_bytes(4, "big") + body


def subpacket(kind: int, body: bytes, critical: bool = False) -> bytes:
    return bytes([len(body) + 1, kind | (0x80 if critical else 0)]) + body


def crc24(data: bytes) -> int:
    crc = 0xB704CE
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def armor(kind: str, data: bytes, crc: int | None = None) -> str:
    text = base64.b64encode(data).decode()
    lines = [text[at : at + 64] for at in range(0, len(text), 64)]
    check = base64.b64encode((crc24(data) if crc is None else crc).to_bytes(3, "big")).decode()
    return "\n".join([f"-----BEGIN PGP {kind}-----", "", *lines, f"={check}", f"-----END PGP {kind}-----"]) + "\n"


class Signer:
    """An RSA key of its own, as a vendor's would be."""

    def __init__(self, bits: int = 2048, exponent: int = 65537, created: int = 1_700_000_000) -> None:
        self.key = rsa.generate_private_key(public_exponent=exponent, key_size=bits)
        numbers = self.key.public_key().public_numbers()
        self.n, self.e = numbers.n, numbers.e
        self.body = b"\x04" + created.to_bytes(4, "big") + b"\x01" + mpi(self.n) + mpi(self.e)
        self.fingerprint = hashlib.sha1(b"\x99" + len(self.body).to_bytes(2, "big") + self.body).hexdigest().upper()

    def public(self) -> str:
        """The armored public key, with a user ID, as a vendor publishes it."""
        return armor("PUBLIC KEY BLOCK", packet(6, self.body) + packet(13, b"A test key <test@example.invalid>"))

    def head(
        self, *, version: int = 4, kind: int = 0, hashed_with: int = 10, created: int | None = None, extra: bytes = b""
    ) -> bytes:
        """The hashed part of a signature: version, type, RSA, the hash, then its subpackets."""
        made = int(time.time()) if created is None else created
        hashed = (
            subpacket(2, made.to_bytes(4, "big")) + subpacket(33, b"\x04" + bytes.fromhex(self.fingerprint)) + extra
        )
        return bytes([version, kind, 1, hashed_with]) + len(hashed).to_bytes(2, "big") + hashed

    def packet(
        self, data: bytes, *, head: bytes | None = None, hashed_with: int = 10, value: int | None = None
    ) -> bytes:
        """A signature packet of data: made as OpenPGP makes one, or carrying value in place of the signature."""
        head = self.head(hashed_with=hashed_with) if head is None else head
        trailer = b"\x04\xff" + len(head).to_bytes(4, "big")
        algorithm, name = HASHES[hashed_with]
        digest = hashlib.new(name, data + head + trailer).digest()
        if value is None:
            value = int.from_bytes(self.key.sign(data + head + trailer, padding.PKCS1v15(), algorithm()), "big")
        unhashed = subpacket(16, bytes.fromhex(self.fingerprint)[-8:])
        return packet(2, head + len(unhashed).to_bytes(2, "big") + unhashed + digest[:2] + mpi(value))

    def sign(self, data: bytes, **how) -> str:
        """data's armored detached signature."""
        return armor("SIGNATURE", self.packet(data, **how))
