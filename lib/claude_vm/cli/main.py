"""aw's entry, which tools/aw runs (docs/architecture/engine.md §16.3): the words typed read against the one table, held
by the one check, today's habits applied, and the verb handed to its handler or refused in one sentence.

    aw [--attended=1] <verb> [<word>...] [<option>...]

A refusal is one line on standard error, and exits 1 (words.py's EXITS), before any of the words reach a handler: it
imports no Ansible and no module of the engine. Its sentence says no value typed but where today's does (the rules
between options, lib/claude_vm/schema/rules.py). A line the printer will not write (schema/printer.py's Unwritten, a
form whose one placeholder is a verb's names beside words the check takes among them), which only the code asks it for,
is said in one line too, as the code's own fault (the printer's own_fault), never as a traceback, and exits 1: here
where it reaches main, and by the run where a handler's run ends the process at once with a call still in a thread
(runs/attached.py's _end_now), which it then never reaches. A Ctrl-C at deploy's question exits 130, as a cancelled run
does.
`--version` alone answers with the checkout aw runs from. Every command it prints, and every sentence that names one, is
the program as it was started: the launcher at the checkout's top says which in STARTED_AS (aw where aw on PATH is that
launcher, else what the person typed to start it), which main takes out of the environment at once, so nothing the run
starts inherits it, and hands to the printer of a command (schema/printer.py's started_as); with none, as for tools/aw
run directly, the program is aw. It takes a job's marker, AGENT_WAREHOUSE_JOB, out of its environment as soon: a run is
started by a marked process now and then (an agent's job, a terminal of an editor a run opened), and its own children
are marked by the one way a run starts them (runs/children.py), so a child it starts any other way - route.py's ssh,
proc.py's ps - would carry another job's ID. Standard library only, and the modules of cli/, schema/ and fleet/paths.py,
and of tui/ to ask deploy's question.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from claude_vm.cli import before, dispatch, habits, parse
from claude_vm.schema import printer
from claude_vm.schema.words import EXITS, VERBS

# What the launcher at the checkout's top says the program is, as a person can run it again: aw, or what they typed.
STARTED_AS = "AGENT_WAREHOUSE_STARTED_AS"
# A job's marker, which a run never inherits: runs/children.py's MARKER, held equal by tests/unit/test_aw_children.py.
MARKER = "AGENT_WAREHOUSE_JOB"


def checkout(program: str) -> Path:
    """The checkout aw runs from, worked out from tools/aw's own path and nothing else: not the working directory, the
    environment, or where this module was imported from. A checkout has no version number of its own."""
    return Path(program).resolve().parent.parent


def main(argv: list[str]) -> int:
    """argv as tools/aw hands it on: its own path, then the words typed. Returns the exit status."""
    program, words = argv[0], argv[1:]
    os.environ.pop(MARKER, None)
    printer.started_as(os.environ.pop(STARTED_AS, None))
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
    except printer.Unwritten as unwritten:
        # A line the printer will not write, which only the code asks it for: said as the code's own fault, in the
        # printer's sentence, which never says a value, and never as a traceback. A run that ends the process at once
        # with a call still in a thread says it the same way (runs/attached.py's _end_now).
        print(printer.own_fault(unwritten), file=sys.stderr)
        return EXITS["refused"]
