"""The helper as one script for a machine's standard input: its sources packed after a mark, each defined in memory
under the name it is imported by, then its dispatcher handed a verb (docs/architecture/engine.md §16.10).

The helper is one program for the machine's own `python3`, held to Python 3.10 and to the standard library, and never
installed: each use sends it whole on standard input, where `BOOTSTRAP`, the one word on the command line, reads it.
The script has a marked end (`frame`): a line of its own, `MARK` and the script's length in bytes, after a newline,
then that many bytes. The bootstrap first prints `STARTED`, a line of its own, so that its caller knows the machine's
Python ran (and so that `sudo` let it); then it reads standard input a line at a time up to the mark's line, reads
exactly the script's length, and executes it. What comes before the mark's line - a line that `sudo -S` was handed and
left there, a password's - is read and dropped, never executed or said back: the mark's line is the whole of a line,
and a line in front of it holds a newline only at its end. What comes after the script is the verb's own standard
input, read on from where the bootstrap stopped (`sys.stdin.buffer`), never compiled with the script. The verb, its
words and what it is asked travel inside the script as JSON, never on a command line.

Its sources are read by `sources()` alone, the one function of the new engine that names where the probe's are: they
stay in `playbooks/module_utils/`, which today's Ansible modules import as `ansible.module_utils`, until the cutover
moves them into `guest/helper/` (card K28), and `sources()` finds them from this package's own place, never the working
directory. A release is a checkout of a signed tag, so it holds the same files. A script packs its own verb's sources
alone: the probe's three for `probe`, and for a verb of the helper's own files (`OWN`: `clones`, the checkouts' writer),
that file, so that the probe's script, held to reads, carries no writer. The script defines `ansible` and
`ansible.module_utils` as empty modules, each source put in `sys.modules` before the one that imports it, so the
probe's own imports resolve on a machine with no Ansible.

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
# What the script's own line starts with, the script's length in bytes after it: `#!agent-warehouse-helper 49152`.
MARK = "#!agent-warehouse-helper"
# The mark's line as the bootstrap reads it, the whole of a line: a length of one to nine digits.
LINE = rb"#!agent-warehouse-helper ([0-9]{1,9})\n"
# The most bytes a script's length says.
MOST = 999_999_999
# The line the bootstrap prints first, before it reads anything: the machine's Python ran. A JSON document, as every
# line a verb prints is.
STARTED = '{"helper": "started"}'
# What the machine's python3 is given on its command line, -I and -B before it (command): STARTED printed; standard
# input read a line at a time up to the mark's line, then exactly the length it says, executed; anything else, one line
# on standard error and exit status 2, nothing executed. The rest of standard input is the verb's.
BOOTSTRAP = (
    "import re, sys; "
    f"sys.stdout.write({STARTED!r} + chr(10)); sys.stdout.flush(); "
    "b = sys.stdin.buffer; "
    f"m = next(filter(None, map(re.compile({LINE!r}).fullmatch, iter(b.readline, b''))), None); "
    "s = b.read(int(m[1])) if m else b''; "
    "m and len(s) == int(m[1]) and s or (sys.stderr.write('helper: no whole script on standard input' + chr(10)), "
    "sys.exit(2)); "
    "exec(compile(s, '<helper>', 'exec'))"
)
# The machine's own interpreter, by its path.
PYTHON = "/usr/bin/python3"
# The modules the probe's sources are imported from, defined empty: no Ansible is on a machine for them.
PACKAGES = ("ansible", "ansible.module_utils")
# The probe's sources, as today's modules name them, in the order the script defines them: each imports only those
# before it.
PROBE = ("agent_warehouse", "git_checkouts", "status_probe")
# The name the script defines the dispatcher (helper/main.py) under, and calls last.
DISPATCHER = "helper"
# The verbs whose sources are the helper's own files, each by its verb: the file of this package and the name the
# script defines it under, the one the dispatcher's VERBS gives. Each script packs its own verb's sources alone, so that
# the probe's script, which `just status`'s rule holds to reads, carries no writer.
OWN = {"clones": ("clones.py", "clones")}


def sources(verb: str = "probe") -> dict[str, str]:
    """Every source the script for verb packs, by the module name the script defines it under, in that order: a verb
    of OWN's, its file; any other, the probe's three, read where they are in this checkout; then the dispatcher. Read
    now; OSError where one cannot be read."""
    if verb in OWN:
        file, name = OWN[verb]
        packed = {name: (HELPER / file).read_text(encoding="utf-8")}
    else:
        utils = CHECKOUT / "playbooks" / "module_utils"
        packed = {f"ansible.module_utils.{name}": (utils / f"{name}.py").read_text(encoding="utf-8") for name in PROBE}
    packed[DISPATCHER] = (HELPER / "main.py").read_text(encoding="utf-8")
    return packed


def _literal(value: object) -> str:
    """value as JSON inside a Python string literal: JSON's text is ASCII, and a JSON string is a Python one."""
    return json.dumps(json.dumps(value, allow_nan=False))


def frame(body: str) -> str:
    """body behind its mark's line, as BOOTSTRAP reads it: a newline, MARK and body's length in bytes (UTF-8), a
    newline, then body; ValueError where body is not text, or longer than MOST bytes."""
    if not isinstance(body, str):
        raise ValueError("the helper's script is text")
    length = len(body.encode("utf-8"))
    if length > MOST:
        raise ValueError(f"the helper's script is at most {MOST} bytes")
    return f"\n{MARK} {length}\n{body}"


def script(entry: Sequence[str], asked: object) -> str:
    """The helper as one text for a machine's standard input, run by BOOTSTRAP: its mark's line (frame), each of the
    verb's sources() defined in memory under its name, then the dispatcher handed entry, the verb and its words, and
    asked, what the verb is given, both as JSON. ValueError where entry is not a verb and words, or asked is not
    JSON."""
    if isinstance(entry, str) or not isinstance(entry, Sequence) or not entry:
        raise ValueError("the helper's entry is a list of words, the verb first")
    if not all(isinstance(word, str) for word in entry):
        raise ValueError("each word of the helper's entry is text")
    try:
        words, given = _literal(list(entry)), _literal(asked)
    except (TypeError, ValueError) as error:
        raise ValueError(f"what the helper is asked is JSON: {error}") from None
    packed = _literal(list(sources(entry[0]).items()))
    return frame(
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
