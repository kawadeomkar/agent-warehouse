"""The system play: `playbooks/system.yml`, layer 1's one play, which converges a machine's system layer with today's
three system roles as they stand, and Tailscale's package where it is the VPN, by its role's entry `system`
(docs/architecture/engine.md §16.10).

`PLAY` is the play, `PARTS` its parts, each a role it runs under a tag of the role's own name, in their order (a part
asked for is a `--tags` of layer1.run's), and `NAMES` the names its variables may hold: guest/wiring.py's one list, the
play's own, each variable of its roles' table and each internal, which layer1.run holds a variables file beside the play
to (its `PLAY_NAMES`).
`variables(path, document, secrets=...)` writes the play's variables, the one road they take: through
guest/variables.py's `write` alone, held to `NAMES`, after a setting the engine cannot serve yet (`UNSERVED`) is refused
by its name, before anything is written and so before any child starts. Today that is one:
`common_user_passwordless_sudo` other than true, which needs the VM user's password to become root with, and no secret
reaches a play until the card that hands it over on a pipe (F24) is built.

Standard library only, and nothing at import but what guest/variables.py and guest/wiring.py load.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from claude_vm.guest import variables as play_variables
from claude_vm.guest import wiring
from claude_vm.secret import Secret

# The play, in this checkout or release: playbooks/ beside lib/, which holds this module.
PLAY = Path(__file__).resolve().parents[3] / "playbooks" / "system.yml"
# The roles the play runs, in its order, each under a tag of its own name: tailscale's, its entry system, the package.
PARTS = ("common", "vscode_server", "dev_tools", "tailscale")
# The names the play's variables may hold, and no other: guest/wiring.py's one list.
NAMES = wiring.NAMES
# The settings the engine cannot serve yet, each with the one value it serves and why another is refused.
UNSERVED: Mapping[str, tuple[object, str]] = {
    "common_user_passwordless_sudo": (
        True,
        "turned off, sudo asks the VM user's password, which a play is not handed before F24",
    ),
}


class Refused(play_variables.Refused):
    """The play's variables hold a setting the engine cannot serve yet, said by its name: nothing was written."""


def unserved(document: Mapping[str, object]) -> list[str]:
    """Each setting of document the engine cannot serve yet, with why: a sentence each, by the setting's name."""
    found = []
    for name, (served, why) in UNSERVED.items():
        if name in document and (type(document[name]) is not type(served) or document[name] != served):
            found.append(f"{name} is served as {str(served).lower()} alone for now: {why}")
    return found


def variables(path: str | os.PathLike, document: Mapping[str, object], *, secrets: Iterable[Secret]) -> Path:
    """document written at path as the play's variables, by guest/variables.py's `write` and its rules, its names held
    to NAMES; refused, before anything is written, where it holds a setting the engine cannot serve yet."""
    if not isinstance(document, Mapping):
        raise Refused("a play's variables are a mapping of names to values")
    found = unserved(document)
    if found:
        raise Refused("; ".join(found))
    return play_variables.write(path, document, secrets=secrets, names=NAMES)
