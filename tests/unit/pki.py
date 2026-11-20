"""Certificates made the way Proxmox VE makes them, for the tests of just init's trust in the API's certificate: a CA
with no keyUsage, as every Proxmox VE 8 host's and every 9 host's made before pve-cluster 9.1.8 has, or with one, as
later ones do; and a node's certificate it signs, which names the node's address. Fake, made per test: no key of them
is in the repository."""

from __future__ import annotations

import datetime
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem(certificate) -> str:
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


def proxmox_pki(
    directory: Path, *, key_usage: bool = False, address: str = "127.0.0.1", name: str = "Proxmox Virtual Environment"
) -> dict:
    """A CA and a node certificate signed by it, naming ``address``: {"ca", "node"} as PEM text, and "cert" and "key",
    the files a TLS server serves the node's certificate alone from, as pveproxy does. ``name`` is the CA's common name,
    Proxmox's own unless a test wants a CA of another subject."""
    now = datetime.datetime.now(datetime.UTC)
    ca_key, node_key = _key(), _key()
    ca_name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PVE Cluster Manager CA"),
        ]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
    )
    if key_usage:
        ca = ca.add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    ca = ca.sign(ca_key, hashes.SHA256())
    node_name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "pve.lan"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PVE Cluster Node"),
        ]
    )
    node = (
        x509.CertificateBuilder()
        .subject_name(node_name)
        .issuer_name(ca_name)
        .public_key(node_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=700))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=False)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(address)), x509.DNSName("pve.lan")]),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(node_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    directory.mkdir(parents=True, exist_ok=True)
    cert, key = directory / "node.pem", directory / "node.key"
    cert.write_text(_pem(node))
    key.write_bytes(
        node_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return {"ca": _pem(ca), "node": _pem(node), "cert": cert, "key": key}
