"""The choices a checkout makes once - the editor, the VPN, and later the host's OS - each a setting naming one row of a
list in the inventory defaults: CHOICES, the rows parsed the one way (rows_from) and a value held to them (pick).
Stdlib only (docs/architecture/custom-code.md §7.11)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

from .records import plain

# Each choice's setting: the inventory defaults' list of its members, and the values it takes that are no member.
# tools/settings (CHOICES_FROM, CHOICES_ALSO) and tools/check settings (CHOICES) say the same, held by a test.
CHOICES: Final[Mapping[str, tuple[str, tuple[str, ...]]]] = {
    "editor": ("claude_editors", ()),
    "vpn": ("claude_vpns", ("none",)),
}


def rows_from[Row](raw: object, where: str, build: Callable[[str, dict[str, Any]], Row]) -> dict[str, Row]:
    """A list of the inventory defaults, as the core's filters are handed it, each row built into its record: parsed on
    every call, in the list's order."""
    rows = plain(raw, where)
    if not isinstance(rows, dict) or not all(isinstance(row, dict) for row in rows.values()):
        raise ValueError(f"{where} is not a mapping of rows")
    return {key: build(key, row) for key, row in rows.items()}


def pick[Row](key: object, rows: Mapping[str, Row], what: str) -> Row:
    """The row a setting's value names, or the one refusal of a value that is none, naming the members."""
    row = rows.get(str(key))
    if row is None:
        raise ValueError(f"{key} is not {what}: {', '.join(sorted(rows))}")
    return row
