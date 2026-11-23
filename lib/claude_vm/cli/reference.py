"""The command reference's tables, written from the one table (docs/architecture/engine.md §16.3): what the wiki's
docs/reference/commands.md shows between its generated markers, in the form tools/docs.py's commands() writes them from
the justfile today. The cutover points tools/docs.py at commands() (K's); until then only its test calls it.

One table for each group of verbs, help aside as today's page leaves it: a row for each verb, its usage line and its
doc, and under it a row for each of its options, --detach last on a verb whose run is a job, and one for the words it
takes, each with its help, but the words of a verb that refuses any and an option the table marks as a later card's.
Then a table of the options given before any verb, but the hidden one. The usage lines and options are help's
(help.py), each sentence is said as help says it (words.py's `said`), and each cell is escaped as tools/docs.py escapes
one, so a placeholder such as <name> reads as text on GitHub and on the site: tests/unit/test_aw_cli_help.py holds the
two escapes equal. Where the table holds an option's value or the words to a pattern, the cell ends as help's row does,
with what a value may be in words (the check's `form_of`, the item's own words and example where the table gives
them), and then, as a definition, the pattern itself as code: a code span, or an HTML code element with each | written
&#124; where the pattern has one, since a | splits a GitHub table's cell and \\| inside a code span shows its backslash
on the site, as tools/docs.py writes a value with one.

Standard library, schema/ (the printer among it) and help.py only.
"""

from __future__ import annotations

import html
import re

from claude_vm.cli import help as helps
from claude_vm.schema import check as checks
from claude_vm.schema.patterns import PATTERNS
from claude_vm.schema.printer import said
from claude_vm.schema.words import BEFORE, VERBS, Option, Words, placeholder

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
# What comes between a cell's form and its pattern.
HELD_TO = ", held to "


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


def code(text: str) -> str:
    """text as code in a table's cell: a code span, or, where text has a |, an HTML code element with each | written
    &#124;, as tools/docs.py writes a value with one, so the cell is neither split on GitHub nor shown with a backslash
    on the site."""
    if "|" in text:
        return f"<code>{html.escape(text, quote=False).replace('|', '&#124;')}</code>"
    return f"`{text}`"


def described(item: Option | Words) -> str:
    """The cell of an option or a verb's words: its help said, and where the table holds a value to a pattern, what the
    value may be in words, as help says it, then the pattern itself as code."""
    text = said(item.help or "")
    if item.pattern is None:
        return cell(text)
    return f"{cell(f'{text} {checks.form_of(item)}')}{HELD_TO}{code(PATTERNS[item.pattern].text)}"


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
        rows.append(f"| `{helps.usage(verb)}` | {cell(said(verb.doc))} |")
        for option in helps.options(verb):
            rows.append(f"| {INDENT_CELL}`{helps.shown(option)}` | {described(option)} |")
        if verb.words is not None and verb.words.name != helps.REFUSED_WORDS:
            words = verb.words
            rows.append(f"| {INDENT_CELL}`{placeholder(words)}` | {described(words)} |")
    out = [line for group, rows in groups.items() for line in _table(group, VERB_HEAD, rows)]
    before = [f"| `{helps.shown(option)}` | {described(option)} |" for option in BEFORE if option.help is not None]
    return "\n".join(out + _table(BEFORE_ANY, BEFORE_HEAD, before))
