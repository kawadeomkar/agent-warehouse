"""Pins that nothing moves on its own are held to ones that something does, and the signing keys the VMs trust to the
vendors' own.

Dependabot moves pyproject.toml's pins and reads no role default, so the uv every VM gets (dev_tools_uv_version) stayed
at 0.9.5 for months while uv published advisories against it - one of them a compromised root CA it bundled. The VM's
uv is held to be no older than the one the controller's lock pins.

The vendored apt keys are what apt trusts a repository's packages by, as root on every VM. Nothing else holds a key file
to its vendor's: a changed or added key would pass every other test. Each file's keys are held to the fingerprints the
vendors publish, computed here from the file's own packets, with no gpg.
"""

from __future__ import annotations

import base64
import hashlib
import re
import struct
from pathlib import Path

import tomllib
import yaml
from harness import _version

REPO = Path(__file__).resolve().parents[2]


def test_the_vm_s_uv_is_no_older_than_the_controller_s() -> None:
    defaults = yaml.safe_load((REPO / "roles" / "dev_tools" / "defaults" / "main.yml").read_text())
    dev = tomllib.loads((REPO / "pyproject.toml").read_text())["dependency-groups"]["dev"]
    controller = next(re.fullmatch(r"uv==([0-9.]+)", pin).group(1) for pin in dev if pin.startswith("uv=="))
    vm = defaults["dev_tools_uv_version"]
    assert _version(vm) >= _version(controller), (
        f"dev_tools_uv_version {vm} is older than pyproject.toml's uv {controller}"
    )
    assert set(defaults["dev_tools_uv_sha256"]) == set(defaults["dev_tools_uv_arch_map"])
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in defaults["dev_tools_uv_sha256"].values())


# Each vendored key file's primary keys, by v4 fingerprint, as their vendors publish them. GitHub's file holds its
# keyring as GitHub ships it: the key of 2022, expired 2026-09-05, which apt ignores, and the one after it (both named
# in cli/cli#13118 and in the Linux install page of its repository).
KEYS = {
    # Anthropic's Claude Code release key, which signs each release's manifest from 2.1.89
    # (https://code.claude.com/docs/en/setup, "Binary integrity and code signing").
    "roles/claude_code/files/claude-code.asc": {"31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"},
    "roles/dev_tools/files/docker.asc": {"9DC858229FC7DD38854AE2D88D81803C0EBFCD88"},
    "roles/dev_tools/files/github-cli.asc": {
        "2C6106201985B60E6C7AC87323F3D4EA75716059",
        "7F38BBB59D064DBCB3D84D725612B36462313325",
    },
    "roles/dev_tools/files/nodesource.asc": {"6F71F525282841EEDAF851B42F59B5F99B1BE0B4"},
    # Ubuntu's cloud image key, which signs SHA256SUMS: the copy with its 2025 SHA-512 self-signature, as
    # ubuntu-keyring and keyserver.ubuntu.com have it.
    "roles/proxmox_template/files/ubuntu-cloudimage.asc": {"D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81"},
    "roles/tailscale/files/tailscale.asc": {"2596A99EAAB33821893C0A79458CA832957F5868"},
}


def _dearmor(text: str) -> bytes:
    """Every ASCII-armored block of a key file, one after the other: the lines between each block's headers and its
    checksum. Nothing may stand outside a block, since apt and the verifier read the whole file - a key appended after
    the first block would be trusted as surely as the first."""
    lines = text.strip().splitlines()
    data, at = b"", 0
    while at < len(lines):
        if not lines[at].strip():
            at += 1
            continue
        assert lines[at].startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----"), f"outside an armored block: {lines[at]}"
        end = next(i for i in range(at, len(lines)) if lines[i].startswith("-----END PGP PUBLIC KEY BLOCK-----"))
        body = lines[at + 1 : end]
        if "" in [line.strip() for line in body]:
            body = body[[line.strip() for line in body].index("") + 1 :]
        data += base64.b64decode("".join(line.strip() for line in body if not line.startswith("=")))
        at = end + 1
    return data


def _primary_fingerprints(data: bytes) -> set[str]:
    """Every primary key's (tag 6) v4 fingerprint: SHA-1 of 0x99, the packet's two-byte length and its body."""
    found, i = set(), 0
    while i < len(data):
        head = data[i]
        assert head & 0x80, f"no packet at byte {i}"
        if head & 0x40:
            tag, first = head & 0x3F, data[i + 1]
            if first < 192:
                length, i = first, i + 2
            elif first < 224:
                length, i = ((first - 192) << 8) + data[i + 2] + 192, i + 3
            else:
                assert first == 255, "a partial length, which a key packet never has"
                length, i = struct.unpack(">I", data[i + 2 : i + 6])[0], i + 6
        else:
            assert head & 3 != 3, "an indeterminate length, which a key file never has"
            tag, size = (head >> 2) & 0x0F, 1 << (head & 3)
            length, i = int.from_bytes(data[i + 1 : i + 1 + size]), i + 1 + size
        body = data[i : i + length]
        if tag == 6:
            assert body[0] == 4, "a primary key that is not version 4"
            found.add(hashlib.sha1(b"\x99" + struct.pack(">H", len(body)) + body).hexdigest().upper())
        i += length
    return found


def test_the_release_keys_the_roles_verify_by_are_the_pinned_ones() -> None:
    # The fingerprint a role finds its key by, and the one held here, are one.
    claude = yaml.safe_load((REPO / "roles" / "claude_code" / "vars" / "main.yml").read_text())
    template = yaml.safe_load((REPO / "roles" / "proxmox_template" / "defaults" / "main.yml").read_text())
    assert {claude["claude_code_release_fingerprint"]} == KEYS["roles/claude_code/files/claude-code.asc"]
    assert {template["proxmox_template_image_fingerprint"]} == KEYS[
        "roles/proxmox_template/files/ubuntu-cloudimage.asc"
    ]


def test_every_vendored_signing_key_is_its_vendor_s() -> None:
    vendored = {path.relative_to(REPO).as_posix() for path in REPO.glob("roles/*/files/*.asc")}
    assert vendored == set(KEYS), "a key file was added or removed: pin its fingerprints here, from its vendor's page"
    for path, fingerprints in KEYS.items():
        assert _primary_fingerprints(_dearmor((REPO / path).read_text())) == fingerprints, path


def test_a_key_appended_to_a_vendored_file_is_found(tmp_path) -> None:
    # The roles hand apt, and the verifier, the whole file: a second block is a second trusted key.
    first = (REPO / "roles" / "tailscale" / "files" / "tailscale.asc").read_text()
    second = (REPO / "roles" / "dev_tools" / "files" / "docker.asc").read_text()
    both = _primary_fingerprints(_dearmor(first + "\n" + second))
    assert both == KEYS["roles/tailscale/files/tailscale.asc"] | KEYS["roles/dev_tools/files/docker.asc"]
