"""aw's entry, which tools/aw runs (docs/architecture/engine.md §16.3): the words typed read against the one table, held
by the one check, today's habits applied, and the verb handed to its handler or refused in one sentence.

    aw [--attended=1] <verb> [<word>...] [<option>...]

A refusal is one line on standard error, and exits 1 (words.py's EXITS), before any of the words reach a handler: it
imports no Ansible and no module of the engine. Its sentence says no value typed but where today's does (the rules
between options, lib/claude_vm/schema/rules.py). A Ctrl-C at deploy's question exits 130, as a cancelled run does.
`--version` alone answers with the checkout aw runs from. Every command it prints, and every sentence that names one,
is the program as it was started: the launcher at the checkout's top says which in STARTED_AS (aw where aw on PATH is
that launcher, else what the person typed to start it), which main takes out of the environment at once, so nothing the
run starts inherits it, and hands to the printer of a command (inverse.py's started_as); with none, as for tools/aw run
directly, the program is aw. Standard library only, and the modules of cli/, schema/ and fleet/paths.py, and of tui/ to
ask deploy's question.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from claude_vm.cli import before, dispatch, habits, inverse, parse
from claude_vm.schema.words import EXITS, VERBS

# What the launcher at the checkout's top says the program is, as a person can run it again: aw, or what they typed.
STARTED_AS = "AGENT_WAREHOUSE_STARTED_AS"


def checkout(program: str) -> Path:
    """The checkout aw runs from, worked out from tools/aw's own path and nothing else: not the working directory, the
    environment, or where this module was imported from. A checkout has no version number of its own."""
    return Path(program).resolve().parent.parent


def main(argv: list[str]) -> int:
    """argv as tools/aw hands it on: its own path, then the words typed. Returns the exit status."""
    program, words = argv[0], argv[1:]
    inverse.started_as(os.environ.pop(STARTED_AS, None))
    if words == ["--version"]:
        print(f"aw from the checkout {checkout(program)}")
        return EXITS["done"]
    try:
        reading = parse.read(words)
        verb = VERBS[reading.verb]
        values, said = habits.names(verb, reading.values)
        for line in said:
            print(line, file=sys.stderr)
        try:
            values = habits.stage(verb, values, sys.stdin, sys.stderr)
        except KeyboardInterrupt:
            # Ctrl-C at deploy's question, before anything ran: ended as today's recipe is, with nothing more said.
            return EXITS["cancelled"]
        run, values = before.run_words(reading.before, values, os.environ)
        return dispatch.dispatch(checkout(program), verb, run, values)
    except parse.Refused as refused:
        print(refused.sentence, file=sys.stderr)
        return EXITS["refused"]
