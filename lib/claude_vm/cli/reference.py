"""The command reference's tables, written from the one table (docs/architecture/engine.md §16.3): what the wiki's
docs/reference/commands.md shows between its generated markers, in the form tools/docs.py's commands() writes them from
the justfile today. The cutover points tools/docs.py at commands() (K's); until then only its test calls it.

One table for each group of verbs, help aside as today's page leaves it: a row for each verb, its usage line and its
doc, and under it a row for each of its options, --detach last on a verb whose run is a job, and one for the words it
takes, each with its help, but the words of a verb that refuses any. Then a table of the options given before any verb,
but the hidden one. The usage lines and options are help's (help.py), and each cell is escaped as tools/docs.py escapes
one, so a placeholder such as <name> reads as text on GitHub and on the site: tests/unit/test_aw_cli_help.py holds the
two escapes equal.

Standard library, schema/ and help.py only.
"""

from __future__ import annotations

import html
import re

from claude_vm.cli import help as helps
from claude_vm.schema import check as checks
from claude_vm.schema.words import BEFORE, VERBS

# Spans left as they are: Markdown code, an HTML code element, a Markdown link's target (tools/docs.py's CODE).
CODE = re.compile(r"(`+)(?:.+?)\1|<code>.*?</code>|\]\([^)]*\)")
# How a verb's options and words sit under it in its table: an em space in (tools/docs.py's INDENT_CELL).
INDENT_CELL = "&emsp;"
# The group of a verb that names none, as tools/docs.py names a recipe's; and the verb the page lists no row for.
OTHER = "other"
HELP = "help"
# The heading and the head of each table: a verb's, and the options given before any verb.
VERB_HEAD = ("| Command | What it does |", "|---|---|")
BEFORE_ANY = "Before the command"
BEFORE_HEAD = ("| Option | What it does |", "|---|---|")


def escape(text: str) -> str:
    """HTML-escape text outside code spans, so `<name>` stays text in both renderers (tools/docs.py's escape)."""
    out, last = [], 0
    for m in CODE.finditer(text):
        out += [html.escape(text[last : m.start()], quote=False), m.group(0)]
        last = m.end()
    return "".join(out + [html.escape(text[last:], quote=False)])


def cell(text: str) -> str:
    """A table cell's text (tools/docs.py's cell)."""
    return escape(text).replace("|", "\\|").strip()


def _table(heading: str, head: tuple[str, str], rows: list[str]) -> list[str]:
    return [f"### {heading.capitalize()}", "", *head, *rows, ""]


def commands() -> str:
    """One table for each group of the table's verbs, help aside, and one of the options given before any verb: each
    verb's usage and doc, and a row for each of its options and the words it takes, with each one's help."""
    groups: dict[str, list[str]] = {}
    for name, verb in VERBS.items():
        if name == HELP:
            continue
        rows = groups.setdefault(verb.group or OTHER, [])
        rows.append(f"| `{helps.usage(verb)}` | {cell(verb.doc)} |")
        for option in checks.options_of(verb):
            if option.help is not None:
                rows.append(f"| {INDENT_CELL}`{helps.shown(option)}` | {cell(option.help)} |")
        if verb.words is not None and verb.words.name != helps.REFUSED_WORDS:
            rows.append(f"| {INDENT_CELL}`<{verb.words.name}>` | {cell(verb.words.help)} |")
    out = [line for group, rows in groups.items() for line in _table(group, VERB_HEAD, rows)]
    before = [f"| `{helps.shown(option)}` | {cell(option.help)} |" for option in BEFORE if option.help is not None]
    return "\n".join(out + _table(BEFORE_ANY, BEFORE_HEAD, before))
