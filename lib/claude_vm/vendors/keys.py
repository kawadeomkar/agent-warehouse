"""The vendors' signing keys the new engine verifies a download by: each vendored key file and the fingerprint its key
is found by (docs/architecture/engine.md §16.7).

Read from the files where today's roles keep them, roles/claude_code/files/claude-code.asc and
roles/proxmox_template/files/ubuntu-cloudimage.asc: today's roles read them there until the cutover moves them (card
K28), and then this table is the one place that changes. tests/unit/test_pins.py holds each row to the same
fingerprints it holds the files to, which are the vendors' published ones, and to the fingerprints today's roles find
the keys by. A file is read when a key is asked for, never when this module is imported.

Standard library only; downloads.py alone verifies with these keys.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

# The checkout this module is part of, whose roles keep the key files until the cutover.
REPO = Path(__file__).resolve().parents[3]


@dataclasses.dataclass(frozen=True)
class Key:
    """A vendor's signing key: the armored key file, and the version 4 fingerprint openpgp.verify finds the key in it
    by, 40 upper-case hex digits."""

    file: Path
    fingerprint: str

    def armored(self) -> str:
        """The key file's text, read now. Raises OSError where it cannot be read."""
        return self.file.read_text(encoding="ascii")


# Anthropic's Claude Code release key, "Anthropic Claude Code Release Signing <security@anthropic.com>", which signs
# each release's manifest from 2.1.89 (today's claude_code_release_fingerprint).
CLAUDE_CODE = Key(
    REPO / "roles" / "claude_code" / "files" / "claude-code.asc", "31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"
)
# Ubuntu's cloud image key, "UEC Image Automatic Signing Key <cdimage@ubuntu.com>", which signs each release's
# SHA256SUMS (today's proxmox_template_image_fingerprint, by default).
UBUNTU_CLOUD_IMAGE = Key(
    REPO / "roles" / "proxmox_template" / "files" / "ubuntu-cloudimage.asc", "D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81"
)

# Every vendored key the new engine verifies by, by what it signs.
KEYS: dict[str, Key] = {"claude-code": CLAUDE_CODE, "ubuntu-cloud-image": UBUNTU_CLOUD_IMAGE}
