"""aw help: today's two levels of just help, printed from the one table (docs/architecture/engine.md §16.3).

`aw help`, and `aw` alone, lists every verb of the table in its order, each with the words it takes, [options] where
it has any, and its doc, as `just` alone lists the recipes: help first, then each group under its name. Then the
options given before any verb, but the hidden one, which the one check's refusal of such an option sends a person here
for; then today's closing paragraph, with aw where it said just, and -v and --set where it named ANSIBLE_ARGS, each
named on the verbs the table gives it. `aw help <verb>` prints one verb: its usage line as today's command reference
writes a recipe's (`aw deploy [<name>...]`) and its doc, each option with its value's placeholder (`--cores <cores>`,
words.py's `placeholder`) and its help, `--detach` last on a verb whose run is a job, then the words it takes with
theirs. After the help of each option and of the words that the table holds to a pattern comes the form a value may
take (patterns.py's `form`), as the refusal of a value outside it says it too, but for the words of a verb that refuses
any. An option the table marks as a later card's (`until`) is left out until that card. A verb no card has built yet is
listed and helped as today: it is refused only when it runs. A second word after the verb is refused before this runs,
in today's sentence (rules.py's help.1); a word that is no verb is refused here in the one check's sentence, which
never says it.

Every command help prints is the program as it was started, quoted, as the printer writes it: the usage lines, which
begin with the check's `command` (the program and the verb) and whose words and options' spellings are help's own and
the command reference's tables are written from (reference.py), the program the closing paragraph names, and every
whole command line, for a person to paste, which the printer writes, the table's sentences' among them (printer.py's
`said`). Help names no part of the program itself.

Standard library and schema/, the printer among it, only: nothing of the engine, no argparse and no child.
"""

from __future__ import annotations

import sys
import textwrap
from typing import TextIO

from claude_vm.schema import check as checks
from claude_vm.schema import printer
from claude_vm.schema.patterns import form
from claude_vm.schema.printer import said
from claude_vm.schema.words import BEFORE, EXITS, SET, VERBOSE, VERBS, Option, Verb, placeholder

# The words a verb takes that are no word to give: init's and mcp's, which each refuse any, as today's EXTRA does. A
# usage line leaves them out, as today's command reference does; aw help <verb> says them with why, as just --usage
# does.
REFUSED_WORDS = "extra"
# The verbs whose one word is needed, which a usage line shows without brackets, each with why: the table says how many
# words a verb takes, not whether it needs one.
NEEDS_ITS_WORD = {
    "code": "it opens one VM, and refuses none (rules.py's code.1), as today's command reference shows it",
    "release": "it releases one adopted machine, as the design's table of words writes it: release <machine>",
}
# The heading of the verbs, of the options given before any verb, and of a verb's options and words.
LISTING = "Available commands:"
# What the listing shows after a verb that has options to show.
WITH_OPTIONS = "[options]"
BEFORE_ANY = "Options before the command:"
USAGE = "Usage:"
OPTIONS = "Options:"
WORDS = "Words:"
# Today's closing paragraph of just help (the justfile's help recipe), each sentence wrapped at WIDTH on lines of its
# own: aw where it said just; -v and --set where it named ANSIBLE_ARGS, which aw reads no more, each on the verbs whose
# row has it; the commands to paste written by the printer.
CLOSING = (
    "Without names, deploy and destroy act on agent-warehouse-default; status, login and deploy --only configure, "
    "--check or --only ssh-config act on every VM this project created.",
    "Options go after the recipe's name: {verbose} says more of what the system layer does in {verbose_verbs}, {set} "
    "gives one setting a value for one run in {set_verbs}, and LOG_DIR=<dir>, exported or before {aw}, moves the run "
    "logs: {example}.",
    "With more than one Proxmox host, every command runs against the current one ({hosts} lists them), or the one "
    "--host names.",
)
# The closing paragraph's example: deploy alpha, saying the most of what the system layer does, as today's said it.
EXAMPLE = ("deploy", {"names": ["alpha"], "verbosity": 3})
WIDTH = 110
# What holds the words of a command together while its sentence is wrapped: no character a sentence has.
_GLUE = "\x00"


def _taken(verb: Verb) -> list[str]:
    """The words a verb takes, as its usage line shows them: [<name>...] for several, [<name>] for one, <name> for one
    it needs, nothing where it takes none or refuses any."""
    words = verb.words
    if words is None or words.name == REFUSED_WORDS:
        return []
    if verb.name in NEEDS_ITS_WORD:
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


def helped(option: Option) -> str:
    """An option's help as aw help says it: its sentence said, and the form its value may take where the table holds
    it to a pattern."""
    text = said(option.help or "")
    return f"{text} {form(option.pattern)}" if option.pattern is not None else text


def _named(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _having(option: Option) -> list[str]:
    """The verbs whose row has option, in the table's order."""
    return [name for name, verb in VERBS.items() if option in verb.options]


def closing() -> list[str]:
    """Today's closing paragraph, as aw says it: each sentence wrapped at WIDTH, never inside an option and its value,
    the program or a command a person pastes."""
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
    return [
        line.replace(_GLUE, " ")
        for sentence in CLOSING
        for line in textwrap.wrap(sentence.format_map(filled), WIDTH, break_long_words=False, break_on_hyphens=False)
    ]


def _rows(rows: list[tuple[str, str]], indent: str, between: str) -> list[str]:
    """Each row's left side and its text, the texts in one column."""
    width = max(len(left) for left, _ in rows)
    return [f"{indent}{left:<{width}}{between}{text}" for left, text in rows]


def listing() -> str:
    """aw help: the verbs in the table's order, help first and then each group under its name, each with the words it
    takes and its doc; the options given before any verb, but the hidden one; then the closing paragraph."""
    groups: dict[str | None, list[tuple[str, str]]] = {}
    for verb in VERBS.values():
        listed = [verb.name, *_taken(verb), *[WITH_OPTIONS] * bool(options(verb))]
        groups.setdefault(verb.group, []).append((" ".join(listed), said(verb.doc)))
    before = [(shown(option), helped(option)) for option in BEFORE if option.help is not None]
    width = max(len(left) for rows in (*groups.values(), before) for left, _ in rows)
    lines = [LISTING]
    for group, rows in groups.items():
        if group is not None:
            lines.append(f"    [{group}]")
        lines += [f"    {left:<{width}} # {doc}" for left, doc in rows]
        lines.append("")
    lines.append(BEFORE_ANY)
    lines += [f"    {left:<{width}} # {text}" for left, text in before]
    lines.append("")
    return "\n".join(lines + closing()) + "\n"


def _left(option: Option) -> str:
    """An option's left side in a verb's help: a long option with no short one in line with those that have one."""
    return shown(option) if option.short else f"    {shown(option)}"


def of(name: str) -> str:
    """aw help <verb>: its usage line and its doc, as the command reference's row of the verb has both; then each of its
    options with the name of its value and its help, --detach last where its run is a job; then the words it takes with
    theirs; each help followed by the form a value may take where the table holds it to a pattern."""
    verb = VERBS[name]
    rows = [(_left(option), helped(option)) for option in options(verb)]
    lines = [f"{USAGE} {usage(verb)}", "", said(verb.doc)]
    if rows:
        lines += ["", OPTIONS, *_rows(rows, "  ", "  ")]
    if verb.words is not None:
        words = said(verb.words.help)
        if verb.words.pattern is not None and verb.words.name != REFUSED_WORDS:
            words = f"{words} {form(verb.words.pattern)}"
        lines += ["", WORDS, *_rows([(placeholder(verb.words), words)], "  ", "  ")]
    return "\n".join(lines) + "\n"


def main(run: object, recipe: str | None = None, *, out: TextIO | None = None, err: TextIO | None = None) -> int:
    """help's handler (words.py's row): the listing, or one verb's help, on standard output, and the exit status. A
    word that is no verb is refused on standard error in the one check's sentence, which never says it."""
    if recipe is not None and recipe not in VERBS:
        print(checks.no_such_command(), file=err or sys.stderr)
        return EXITS["refused"]
    (out or sys.stdout).write(listing() if recipe is None else of(recipe))
    return EXITS["done"]
