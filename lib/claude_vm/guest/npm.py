"""Claude Code as npm installs it: the package, the read of npm's global list, and the version that list gives
(docs/architecture/engine.md §16.10).

Layer 2's install (layer2/claude_install.py) reads the list as root, as today's install_npm.yml does, and states.py
reads it as the machine's account for status, so that a machine whose `claude_code_install_method` is npm says the
Claude Code npm installed, where the probe reads only the native launcher. Standard library only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

# npm's package of Claude Code.
PACKAGE = "@anthropic-ai/claude-code"
# The read of npm's global packages, their top level alone, as JSON. npm exits 1 for a problem of the tree it lists,
# and still lists it (LISTED).
LIST = ("npm", "ls", "-g", "--depth=0", "--json")
LISTED = (0, 1)
# A release's version, as npm lists one: vendors/downloads.py's RELEASE, which this module does not import.
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?")


def version(lines: Sequence[str]) -> str | None:
    """The version of PACKAGE in npm's list, lines its standard output: None where the list has no PACKAGE, "" where
    it has one with no release's version. ValueError where the lines are no JSON object."""
    try:
        tree = json.loads("\n".join(lines))
    except ValueError:
        tree = None
    if not isinstance(tree, dict):
        raise ValueError("npm listed its global packages in no JSON object")
    dependencies = tree.get("dependencies") or {}
    package = dependencies.get(PACKAGE) if isinstance(dependencies, dict) else None
    if package is None:
        return None
    found = package.get("version") if isinstance(package, dict) else None
    return found if isinstance(found, str) and VERSION.fullmatch(found) else ""
