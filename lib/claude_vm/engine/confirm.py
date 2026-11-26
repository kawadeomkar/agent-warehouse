"""The names that confirm a deletion, held to the VMs it deletes (card G5; docs/architecture/engine.md §16.4).

`names(answer, given)` is the one validator a confirmation by names passes, in the command's own process: destroy's
`--confirm=<names>`, which schema/rules.py's destroy.2 has refused already where it differs, held to the VMs given
again here, where the run is made; the names typed at destroy's question under `destroy_confirm: names`; and, once card
J13 builds it, release's. Both sides are taken as today's recipe takes them: a set, each name with - for _, split at
the commas (and, typed at a question, at the spaces too), an empty part dropped. It gives "" where the answer names
exactly the VMs given, and otherwise the words that say which name differs: those the answer names that are not among
the VMs given, and those it leaves out. A name it says back is made one line of plain text and cut, since a typed one is
the person's and may hold anything; a VM's name given is held to its pattern before any handler.

host --remove's typed confirmation of one host's name (card D12) is its own, and is not this.

Standard library and text.py alone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..text import one_line

# What the parts of an answer are split at: commas, and the spaces a typed answer may carry beside them.
SPLIT = re.compile(r"[,\s]+")
# The most of one name a sentence says back.
SAID_MOST = 64


def _set_of(names: Iterable[str]) -> set[str]:
    """names as a set, each split at SPLIT and with - for _, an empty part dropped."""
    found: set[str] = set()
    for name in names:
        found.update(part.replace("_", "-") for part in SPLIT.split(name) if part)
    return found


def _listed(names: Iterable[str]) -> str:
    return ", ".join(one_line(name, SAID_MOST) for name in sorted(names, key=str.lower))


def names(answer: str, given: Iterable[str]) -> str:
    """The empty string where answer names exactly the VMs given, as a set (see the module's docstring); else what
    differs: each name answer gives that is not among them, and each of them it leaves out."""
    if not isinstance(answer, str):
        raise TypeError("a confirmation's answer is the text given or typed")
    wanted = _set_of(given)
    typed = _set_of([answer])
    extra, missing = typed - wanted, wanted - typed
    parts = []
    if extra:
        parts.append(f"{_listed(extra)} {'is' if len(extra) == 1 else 'are'} not among the VMs given")
    if missing:
        parts.append(f"{_listed(missing)} {'is' if len(missing) == 1 else 'are'} left out")
    return "; ".join(parts)
