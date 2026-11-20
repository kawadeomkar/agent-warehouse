"""aw's entry, which tools/aw runs (docs/architecture/engine.md §16.3). Until the parser and the first verb are built it
answers --version, and refuses anything else in one sentence. Standard library only.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Said for anything but --version, whatever was typed, which it never repeats: a word typed may be a secret.
NOT_BUILT = "The new engine behind aw is not built yet: until it is, the commands are just's (just --list names them)."


def checkout(program: str) -> Path:
    """The checkout aw runs from, worked out from tools/aw's own path and nothing else: not the working directory, the
    environment, or where this module was imported from. A checkout has no version number of its own."""
    return Path(program).resolve().parent.parent


def main(argv: list[str]) -> int:
    """argv as tools/aw hands it on: its own path, then the words typed. Returns the exit status."""
    if argv[1:] == ["--version"]:
        print(f"aw from the checkout {checkout(argv[0])}")
        return 0
    print(NOT_BUILT, file=sys.stderr)
    return 1
