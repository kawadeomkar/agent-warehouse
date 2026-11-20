"""aw's entry, which tools/aw runs (docs/architecture/engine.md §16.3): the words typed read against the one table, held
by the one check, today's habits applied, and the verb handed to its handler or refused in one sentence.

    aw [--no-lock] [--attended=1] <verb> [<word>...] [<option>...]

A refusal is one line on standard error that says no value typed, and exits 1 (words.py's EXITS), before any of the
words reach a handler: it imports no Ansible and no module of the engine. `--version` alone answers with the
checkout aw runs from. Standard library only, and the modules of cli/, schema/ and fleet/paths.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from claude_vm.cli import before, dispatch, habits, parse
from claude_vm.schema.words import EXITS, VERBS


def checkout(program: str) -> Path:
    """The checkout aw runs from, worked out from tools/aw's own path and nothing else: not the working directory, the
    environment, or where this module was imported from. A checkout has no version number of its own."""
    return Path(program).resolve().parent.parent


def main(argv: list[str]) -> int:
    """argv as tools/aw hands it on: its own path, then the words typed. Returns the exit status."""
    program, words = argv[0], argv[1:]
    if words == ["--version"]:
        print(f"aw from the checkout {checkout(program)}")
        return EXITS["done"]
    try:
        reading = parse.read(words)
        verb = VERBS[reading.verb]
        values, said = habits.names(verb, reading.values)
        for line in said:
            print(line, file=sys.stderr)
        run, values = before.run_words(reading.before, values, os.environ)
        return dispatch.dispatch(checkout(program), verb, run, values)
    except parse.Refused as refused:
        print(refused.sentence, file=sys.stderr)
        return EXITS["refused"]
