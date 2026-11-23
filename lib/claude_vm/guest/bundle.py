"""The helper as one script for a machine's standard input: its sources packed after a mark, each defined in memory
under the name it is imported by, then its dispatcher handed a verb (docs/architecture/engine.md §16.10).

The helper is one program for the machine's own `python3`, held to Python 3.9's syntax and to the standard library,
and never installed: each use sends it whole on standard input, where `BOOTSTRAP`, the one word on the command line,
reads it and executes it from `MARK`, which starts a line. What comes before the mark is dropped unread: a line that
`sudo -S` was handed and left there, a password's, is never Python's to say back. The verb, its words and what it is
asked travel inside the text as JSON, never on a command line.

Its sources are read by `sources()` alone, the one function of the new engine that names where the probe's are: they
stay in `playbooks/module_utils/`, which today's Ansible modules import as `ansible.module_utils`, until the cutover
moves them into `guest/helper/` (card K28), and `sources()` finds them from this package's own place, never the working
directory. A release is a checkout of a signed tag, so it holds the same files. The script defines `ansible` and
`ansible.module_utils` as empty packages, so the probe's own imports resolve on a machine with no Ansible.

`reach.probe_script`, today's bundler of the probe for `just status`, is not this module's: today's product never
imports the new engine, so the two live side by side until the cutover, held to the same document by
`tests/unit/test_aw_guest_helper.py`.

Standard library only; nothing is read when this module is imported.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

# The checkout this module is part of: lib/claude_vm/ is two folders below its top.
CHECKOUT = Path(__file__).resolve().parents[3]
# The helper's own files, in this package.
HELPER = Path(__file__).resolve().parent / "helper"
# The line the script starts with. The bootstrap looks for it at the start of a line, after the newline the script
# begins with: a line in front of it has its own newline last, so the mark is never found inside one.
MARK = "#!agent-warehouse-helper"
START = f"\n{MARK}\n"
# What the machine's python3 is given on its command line, -I and -B before it (command): standard input read whole,
# and executed from the mark.
BOOTSTRAP = f"import sys; d = sys.stdin.read(); exec(d[d.index({START!r}):])"
# The machine's own interpreter, by its path.
PYTHON = "/usr/bin/python3"
# The packages the probe's sources are imported from, defined empty: no Ansible is on a machine for them.
PACKAGES = ("ansible", "ansible.module_utils")
# The probe's sources, as today's modules name them, in the order the script defines them: each imports only those
# before it.
PROBE = ("agent_warehouse", "git_checkouts", "status_probe")
# The name the script defines the dispatcher (helper/main.py) under, and calls last.
DISPATCHER = "helper"


def sources() -> dict[str, str]:
    """Every source the script packs, by the module name the script defines it under, in that order: the probe's three,
    read where they are in this checkout, then the dispatcher. Read now; OSError where one cannot be read."""
    utils = CHECKOUT / "playbooks" / "module_utils"
    packed = {f"ansible.module_utils.{name}": (utils / f"{name}.py").read_text(encoding="utf-8") for name in PROBE}
    packed[DISPATCHER] = (HELPER / "main.py").read_text(encoding="utf-8")
    return packed


def _literal(value: object) -> str:
    """value as JSON inside a Python string literal: JSON's text is ASCII, and a JSON string is a Python one."""
    return json.dumps(json.dumps(value, allow_nan=False))


def script(entry: Sequence[str], asked: object) -> str:
    """The helper as one text for a machine's standard input, run by BOOTSTRAP: the mark on a line of its own, each of
    sources() defined in memory under its name, then the dispatcher handed entry, the verb and its words, and asked,
    what the verb is given, both as JSON. ValueError where entry is not a verb and words, or asked is not JSON."""
    if isinstance(entry, str) or not isinstance(entry, Sequence) or not entry:
        raise ValueError("the helper's entry is a list of words, the verb first")
    if not all(isinstance(word, str) for word in entry):
        raise ValueError("each word of the helper's entry is text")
    try:
        words, given = _literal(list(entry)), _literal(asked)
    except (TypeError, ValueError) as error:
        raise ValueError(f"what the helper is asked is JSON: {error}") from None
    packed = _literal(list(sources().items()))
    return (
        f"{START}"
        "import json, sys, types\n"
        "def _module(name, source):\n"
        "    module = types.ModuleType(name); sys.modules[name] = module\n"
        "    exec(compile(source, name, 'exec'), module.__dict__); return module\n"
        f"for _name in {PACKAGES!r}:\n"
        "    _module(_name, '')\n"
        f"for _name, _source in json.loads({packed}):\n"
        "    _module(_name, _source)\n"
        f"sys.exit(sys.modules[{DISPATCHER!r}].main(json.loads({words}), json.loads({given})))\n"
    )


def command(python: str = PYTHON) -> list[str]:
    """What runs the script on a machine, its text on standard input: python, isolated and writing no bytecode, given
    BOOTSTRAP."""
    return [python, "-I", "-B", "-c", BOOTSTRAP]
