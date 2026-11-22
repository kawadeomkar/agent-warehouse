"""A verb handed to its handler, or refused before any of its words reach one (docs/architecture/engine.md §16.3).

`dispatch` calls the handler the verb's row names (`module:function` under claude_vm, imported only when that verb
runs, so a refusal or another verb never loads it) with the run's own words first and then the verb's values as
keyword arguments, and returns the exit status it returns. Before that, in this order:

- A Python that is no virtualenv's is refused, naming PYTHON, for every verb: the project runs only from a virtualenv,
  never a system-wide install, as tools/env-run says, and the launcher runs whatever PYTHON names. Told by sys.prefix,
  in this process, at no second start of a Python.
- An environment that lacks the packages the engine needs is refused, naming aw install, for every verb but install,
  which makes it, and help, which reads the table alone. The launcher at the checkout's top makes the checkout's own
  environment by itself on first use, and never installs into a virtualenv of the person's own unasked.
- A verb whose row has no handler yet says it is not built, the card that builds it, and, where today's justfile has a
  recipe of that name, the just command that does its work until the cutover (K14 takes that clause out at the flip).
- A verb whose run is a job (the row's `changes`) is refused where the fleet's files do not exist, naming aw init, but
  for the two that come before any fleet, init and install; and says on standard error which fleet it drives where
  that is not the checkout's own or the installed program's, as fleet/paths.py's variable or a fleet_home line chose.

Each command a sentence here names is the printer's (inverse.py), the program as it was started; but today's just
<verb>, which is no command of aw's, and goes at the flip.

Standard library only, the printer of a command, and fleet/paths.py, imported only to look for a fleet, which reads at
most two client.yml files.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TextIO

from claude_vm.cli import inverse
from claude_vm.cli.before import RunWords
from claude_vm.cli.parse import Refused
from claude_vm.schema import check as checks
from claude_vm.schema.words import EXITS, Verb

# Today's justfile's recipes a person runs, as H1's contract lists them (tests/unit/test_aw_cli_parse.py holds the two
# equal): a verb not built yet names just <verb> only where one of these is its name. K14 takes it out at the flip.
JUST_RECIPES = frozenset(
    ("help", "init", "install", "vault", "deploy", "code", "login", "status", "settings", "destroy", "host", "logs")
    + ("mcp",)
)
# The changing verbs that run before there is a fleet, each with why.
BEFORE_A_FLEET = {
    "init": "it makes the fleet's files",
    "install": "it makes the checkout's Python environment, which comes before any fleet",
}
# A Python that is no virtualenv's, said with its path: the PYTHON a person set is the one way the launcher runs one.
NO_VIRTUALENV = (
    "error: {python} is no virtualenv's Python, and aw runs only from a virtualenv, never a system-wide install: set"
    " PYTHON to a virtualenv's python, or leave it unset and aw makes this checkout's own"
)
# The top-level modules of the packages the engine needs, which an environment lacks until aw install has run there.
NEEDS = ("ansible",)
# The verbs that run in an environment that lacks them, each with why.
WITHOUT_PACKAGES = {
    "install": "it puts them there",
    "help": "it prints the table, which is the standard library's",
}


def in_a_virtualenv() -> bool:
    """Whether this interpreter is a virtualenv's, as tools/env-run tells one: its prefix is not its base's."""
    return sys.prefix != sys.base_prefix


def lacking(needs: tuple[str, ...] = NEEDS) -> list[str]:
    """The modules of needs this interpreter's environment cannot import: found by their spec, never imported."""
    return [name for name in needs if importlib.util.find_spec(name) is None]


def _load(handler: str) -> Callable[..., int | None]:
    """The function a row names as module:function under claude_vm, imported now."""
    module, function = handler.split(":")
    return getattr(importlib.import_module(f"claude_vm.{module}"), function)


def not_built(verb: Verb) -> str:
    """The sentence of a verb no card has built yet, which says none of the words given."""
    until = f": until then, use today's just {verb.name}" if verb.name in JUST_RECIPES else ""
    return f"error: {checks.command(verb.name)} is not built yet (card {verb.card} builds it){until}"


def _fleet(program_root: Path, verb: Verb, environ: Mapping[str, str], err: TextIO) -> None:
    """Refuse a changing verb with no fleet to drive; say which fleet it drives where that is not the default."""
    from claude_vm.fleet import paths

    try:
        home = paths.home(program_root, environ)
    except paths.Refused as refused:
        raise Refused(f"error: {refused}") from None
    if verb.name not in BEFORE_A_FLEET and not paths.fleet_dir(home).is_dir():
        raise Refused(f"error: {paths.fleet_dir(home)} missing. Run: {inverse.line('init', {}, refused=False)}")
    if home.how == "variable":
        print(f"aw drives the fleet in {home.root}, which {paths.VARIABLE} names", file=err)
    elif home.pointer is not None:
        print(f"aw drives the fleet in {home.root}, which fleet_home in {home.pointer} names", file=err)


def dispatch(
    program_root: Path,
    verb: Verb,
    run: RunWords,
    values: Mapping[str, object],
    *,
    environ: Mapping[str, str] | None = None,
    err: TextIO | None = None,
) -> int:
    """Run verb's handler with run and values, and return its exit status; raises Refused before it where the verb
    cannot run here."""
    env = os.environ if environ is None else environ
    if not in_a_virtualenv():
        raise Refused(NO_VIRTUALENV.format(python=sys.executable))
    if verb.name not in WITHOUT_PACKAGES and (missing := lacking()):
        install = inverse.line("install", {}, refused=False)
        lacks = ", ".join(missing)
        raise Refused(f"error: the Python environment {sys.prefix} lacks {lacks}, which uv.lock pins: run {install}")
    if verb.handler is None:
        raise Refused(not_built(verb))
    if verb.changes:
        _fleet(program_root, verb, env, err or sys.stderr)
    status = _load(verb.handler)(run, **values)
    return EXITS["done"] if status is None else status
