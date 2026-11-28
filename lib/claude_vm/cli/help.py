"""aw help: today's two levels of just help, printed from the one table (docs/architecture/engine.md §16.3).

`aw help`, and `aw` alone, lists every verb of the table in its order, each with the words it takes, [options] where
it has any, and its doc, as `just` alone lists the recipes: help first, then each group under its name. Then the
options given before any verb, but the hidden one, which the one check's refusal of such an option sends a person here
for; then today's closing paragraph, with aw where it said just, command where it said recipe, and -v and --set where
it named ANSIBLE_ARGS, each named on the verbs the table gives it. `aw help <command>` prints one verb: its usage line
as today's command reference writes a recipe's (`aw deploy [<name>...]`) and its doc, each option with its value's
placeholder (`--cores <cores>`, words.py's `placeholder`) and its help, `--detach` last on a verb whose run is a job,
then the words it takes with theirs. After the help of each option and of the words that the table holds to a pattern
comes what a value may be, in words and with an example (the check's `form_of`: `[a whole number from 1 to 999 with no
leading 0, e.g. 8]`), the option's or the words' own where the table gives them (init --bridge's `e.g. vmbr1`, not a
storage's name), never the pattern itself, as the refusal of a value outside it says it too, but for the words of a
verb that refuses any. An option the table marks as a later card's (`until`) is left out until that card. A verb no
card has built yet is listed and helped as today: it is refused only when it runs. A second word after the verb is
refused before this runs, in today's sentence (rules.py's help.1); a word that is no verb is refused here in the one
check's sentence, which never says it.

Every command help prints is the program as it was started, quoted, as the printer writes it: the usage lines, which
begin with the check's `command` (the program and the verb) and whose words and options' spellings are help's own and
the command reference's tables are written from (reference.py), the program the closing paragraph names, and every
whole command line, for a person to paste, which the printer writes, the table's sentences' among them (printer.py's
`said`). Help names no part of the program itself. It is one of the few modules that may use the check's `command`
(tests/unit/test_aw_cli_printer.py's COMMAND_USERS): a usage line is a form in brackets no line of the printer's
writes, and the closing paragraph names the program alone; neither is a line to run.

Every row is wrapped at one width for the whole of help, the terminal's columns, at most WIDTH and at least NARROWEST,
and WIDTH where there is no terminal to ask (columns): in aw help each verb's row and each row of the options given
before any verb, in aw help <verb> its doc, each option's row and the words' row, and the closing paragraph's sentences,
each by the one wrap (_wrapped), the lines after a row's first indented to where its text starts. A line breaks only at
a space, and never inside what a person pastes or reads as one: a command line the printer writes for a sentence, the
program with it, an option and the word after it where it takes a value (--only configure, --set <name>=<value>), a word
and the option that follows it (ansible-playbook --check --diff), a form's example (e.g. 8), or a hyphenated word; so a
line is longer than the width only where one such piece is, alone on it. The usage line is not wrapped: it is a form of
the command, whole. A refusal is never wrapped, and the command reference writes its rows from the same functions
unwrapped.

Standard library and schema/, the printer among it, only: nothing of the engine, no argparse and no child.
"""

from __future__ import annotations

import re
import shutil
import sys
import textwrap
from typing import TextIO

from claude_vm.schema import check as checks
from claude_vm.schema import printer
from claude_vm.schema.printer import said
from claude_vm.schema.words import BEFORE, EXITS, LINES, SET, VERBOSE, VERBS, Option, Verb, placeholder

# The heading of the verbs, of the options given before any verb, and of a verb's options and words.
LISTING = "Available commands:"
# What the listing shows after a verb that has options to show.
WITH_OPTIONS = "[options]"
BEFORE_ANY = "Options before the command:"
USAGE = "Usage:"
OPTIONS = "Options:"
WORDS = "Words:"
# Today's closing paragraph of just help (the justfile's help recipe), each sentence wrapped on lines of its own: aw
# where it said just, and command where it said recipe; -v and --set where it named ANSIBLE_ARGS, which aw reads no
# more, each on the verbs whose row has it; the commands to paste written by the printer.
CLOSING = (
    "Without names, deploy and destroy act on agent-warehouse-default; status, login and deploy --only configure, "
    "--check or --only ssh-config act on every VM this project created.",
    "Options go after the command's name: {verbose} says more of what the system layer does in {verbose_verbs}, {set} "
    "gives one setting a value for one run in {set_verbs}, and LOG_DIR=<dir>, exported or before {aw}, moves the run "
    "logs: {example}.",
    "With more than one Proxmox host, every command runs against the current one ({hosts} lists them), or the one "
    "--host names.",
)
# The closing paragraph's example: deploy alpha, saying the most of what the system layer does, as today's said it.
EXAMPLE = ("deploy", {"names": ["alpha"], "verbosity": 3})
# The widest help is wrapped at, whatever the terminal's width: longer lines are hard to read. Where there is no
# terminal to ask (a pipe, a file, a test), help is this wide.
WIDTH = 110
# The narrowest help is wrapped at, whatever the terminal's width: below it the left column leaves too little.
NARROWEST = 72
# What holds the words of a piece together while its sentence is wrapped: no character a sentence has.
_GLUE = "\x00"
# The spellings of the options that take a value, of every verb and before any: one is never cut from the word after it.
_VALUED = sorted(
    {
        spelling
        for option in [*BEFORE, *(option for verb in VERBS.values() for option in checks.options_of(verb))]
        if option.kind not in ("flag", "counted")
        for spelling in ([f"--{option.long}"] * bool(option.long) + [f"-{option.short}"] * bool(option.short))
    },
    key=len,
    reverse=True,
)
# The spaces a line never breaks at, in a sentence's own words: after an option that takes a value or a form's e.g.,
# and before an option that follows a word (ansible-playbook --check --diff, deploy --only), not punctuation.
_KEPT = re.compile(rf"((?<!\S)(?:e\.g\.|{'|'.join(map(re.escape, _VALUED))})) |(?<=[\w>\]]) (?=--?[A-Za-z])")
# The fields of the commands the table's sentences name (words.py's LINES), each a sentence of one field, so that the
# printer's said writes each.
_LINE_FIELDS = {name: "{" + name + "}" for name in LINES}


def _taken(verb: Verb) -> list[str]:
    """The words a verb takes, as its usage line shows them: [<name>...] for several, [<name>] for one, <name> for one
    it needs (the table's Words.needed), nothing where it takes none or refuses any (Words.refused)."""
    words = verb.words
    if words is None or words.refused:
        return []
    if words.needed:
        return [placeholder(words)]
    return [f"[{placeholder(words)}...]" if words.plural else f"[{placeholder(words)}]"]


def usage(verb: Verb) -> str:
    """A verb's usage line, as today's command reference writes a recipe's: the program as it was started and the verb,
    as the printer writes them (the check's command), and the words it takes, its options in the rows below it
    (aw deploy [<name>...])."""
    return " ".join([checks.command(verb.name), *_taken(verb)])


def shown(option: Option) -> str:
    """An option as help and the command reference show it: -t, --tags <roles>; --check for a flag; -v for a count;
    --set <name>=<value>, given once for each; its value's placeholder, from words.py's placeholder."""
    names = ", ".join([f"-{option.short}"] * bool(option.short) + [f"--{option.long}"] * bool(option.long))
    if option.kind in ("flag", "counted"):
        return names
    return f"{names} {placeholder(option)}"


def options(verb: Verb) -> list[Option]:
    """The options of a verb that help and the command reference show: the parser's, --detach where its run is a job,
    but one the table marks as a later card's (until)."""
    return [option for option in checks.options_of(verb) if option.help is not None and option.until is None]


def helped(option: Option, kept: dict[str, str] | None = None) -> str:
    """An option's help as aw help says it: its sentence said, and the form its value may take where the table holds
    it to a pattern; each command it names kept whole where kept gives it (_kept)."""
    text = said(option.help or "", kept)
    return f"{text} {checks.form_of(option)}" if option.pattern is not None else text


def columns() -> int:
    """The one width of the whole of help: the terminal's columns, COLUMNS first, as shutil reads them and every program
    does, at most WIDTH and at least NARROWEST; WIDTH where there is no terminal to ask."""
    return max(NARROWEST, min(WIDTH, shutil.get_terminal_size(fallback=(WIDTH, 24)).columns))


def _kept() -> dict[str, str]:
    """The commands the table's sentences name, as the printer's said writes each, every space in one glued, for said to
    fill a row's sentence with: so no command, nor the program it starts with, is cut where the row is wrapped."""
    lines = {name: said(field) for name, field in _LINE_FIELDS.items()}
    return {name: text.replace(" ", _GLUE) for name, text in lines.items()}


def _wrapped(first: str, text: str, width: int) -> list[str]:
    """text after first, wrapped at width, the lines after the first indented to where text starts: the one wrap of
    help's rows and of its closing paragraph. It breaks a line only at a space, never inside a hyphenated word or a
    piece glued (a command, the program, an option and its value, a word and the option after it, a form's example), so
    a line is longer than width only where one such piece is, alone on it."""
    glued = _KEPT.sub(lambda kept: (kept.group(1) or "") + _GLUE, text)
    lines = textwrap.wrap(
        glued,
        width,
        initial_indent=first,
        subsequent_indent=" " * len(first),
        break_long_words=False,
        break_on_hyphens=False,
    )
    return [line.replace(_GLUE, " ") for line in lines] or [first.rstrip()]


def _named(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _having(option: Option) -> list[str]:
    """The verbs whose row has option, in the table's order."""
    return [name for name, verb in VERBS.items() if option in verb.options]


def closing(width: int | None = None) -> list[str]:
    """Today's closing paragraph, as aw says it: each sentence wrapped at width, the terminal's by default (columns),
    never inside an option and its value, the program or a command a person pastes."""
    width = columns() if width is None else width
    whole = {
        "set": shown(SET),
        "aw": checks.command(""),
        "example": printer.line(*EXAMPLE, host_flag="", refused=False),
        "hosts": printer.line("host", {}, refused=False),
    }
    filled = {
        "verbose": shown(VERBOSE),
        "verbose_verbs": _named(_having(VERBOSE)),
        "set_verbs": _named(_having(SET)),
        **{name: text.replace(" ", _GLUE) for name, text in whole.items()},
    }
    return [line for sentence in CLOSING for line in _wrapped("", sentence.format_map(filled), width)]


def _rows(rows: list[tuple[str, str]], indent: str, between: str, width: int) -> list[str]:
    """Each row's left side and its text, the texts in one column, each wrapped at width in it."""
    widest = max(len(left) for left, _ in rows)
    return [line for left, text in rows for line in _wrapped(f"{indent}{left:<{widest}}{between}", text, width)]


def listing(width: int | None = None) -> str:
    """aw help: the verbs in the table's order, help first and then each group under its name, each with the words it
    takes and its doc; the options given before any verb, but the hidden one; then the closing paragraph; each row
    wrapped at width, the terminal's by default (columns)."""
    width = columns() if width is None else width
    kept = _kept()
    groups: dict[str | None, list[tuple[str, str]]] = {}
    for verb in VERBS.values():
        listed = [verb.name, *_taken(verb), *[WITH_OPTIONS] * bool(options(verb))]
        groups.setdefault(verb.group, []).append((" ".join(listed), said(verb.doc, kept)))
    before = [(shown(option), helped(option, kept)) for option in BEFORE if option.help is not None]
    widest = max(len(left) for rows in (*groups.values(), before) for left, _ in rows)
    lines = [LISTING]
    for group, rows in groups.items():
        if group is not None:
            lines.append(f"    [{group}]")
        lines += [line for left, doc in rows for line in _wrapped(f"    {left:<{widest}} # ", doc, width)]
        lines.append("")
    lines.append(BEFORE_ANY)
    lines += [line for left, text in before for line in _wrapped(f"    {left:<{widest}} # ", text, width)]
    lines.append("")
    return "\n".join(lines + closing(width)) + "\n"


def _left(option: Option) -> str:
    """An option's left side in a verb's help: a long option with no short one in line with those that have one."""
    return shown(option) if option.short else f"    {shown(option)}"


def of(name: str, width: int | None = None) -> str:
    """aw help <verb>: its usage line and its doc, as the command reference's row of the verb has both; then each of its
    options with the name of its value and its help, --detach last where its run is a job; then the words it takes with
    theirs; each help followed by the form a value may take where the table holds it to a pattern; the doc and each row
    wrapped at width, the terminal's by default (columns), and the usage line whole."""
    width = columns() if width is None else width
    kept = _kept()
    verb = VERBS[name]
    rows = [(_left(option), helped(option, kept)) for option in options(verb)]
    lines = [f"{USAGE} {usage(verb)}", "", *_wrapped("", said(verb.doc, kept), width)]
    if rows:
        lines += ["", OPTIONS, *_rows(rows, "  ", "  ", width)]
    if verb.words is not None:
        words = said(verb.words.help, kept)
        if verb.words.pattern is not None and not verb.words.refused:
            words = f"{words} {checks.form_of(verb.words)}"
        lines += ["", WORDS, *_rows([(placeholder(verb.words), words)], "  ", "  ", width)]
    return "\n".join(lines) + "\n"


def main(run: object, command: str | None = None, *, out: TextIO | None = None, err: TextIO | None = None) -> int:
    """help's handler (words.py's row): the listing, or one verb's help, on standard output, and the exit status. A
    word that is no verb is refused on standard error in the one check's sentence, which never says it."""
    if command is not None and command not in VERBS:
        print(checks.no_such_command(), file=err or sys.stderr)
        return EXITS["refused"]
    width = columns()
    (out or sys.stdout).write(listing(width) if command is None else of(command, width))
    return EXITS["done"]
