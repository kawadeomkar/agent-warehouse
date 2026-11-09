"""What more than one VPN family, or the registry, knows: the marks a family may keep, and the NEXT STEPS matters its
records go under."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from .model import FamilyName

# The mark a Tailscale that has held a login leaves, <tag>-tailscale: the vault's key is never sent to its VM again.
# Written on VMs, so it stays (tests/unit/test_persisted_names.py).
TAILSCALE: Final = "tailscale"
# Each family's NEXT STEPS matters (lib/claude_vm/run/next_steps.py's MATTERS), the only ones its plans record under.
MATTERS: Final[Mapping[FamilyName, frozenset[str]]] = {
    "tailscale": frozenset({"tailscale_record"}),
}


def marks_of(family: FamilyName, mark: str) -> bool:
    """Whether a mark is one the family keeps."""
    match family:
        case "tailscale":
            return mark == TAILSCALE
