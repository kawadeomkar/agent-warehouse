"""aw help, two levels printed from the one table, and the command reference's tables written from it
(lib/claude_vm/cli/help.py, lib/claude_vm/cli/reference.py; docs/architecture/engine.md §16.3).

For every verb of the table aw help <verb> shows its usage, its doc, each of its options with its help, --detach last
where its run is a job, and its words, each sentence said with the printer's commands; after the help of every option
and words the table holds to a pattern, what a value may be in words, never the pattern itself, and never an empty
default; and so, in help's row and in the refusal of a value outside the pattern alike, for every option and words the
hand-written maps of test_aw_cli_words.py name, each with the example they name, or none, and an option's own words
where they name them, but no form for the words of a verb that refuses any. --set's row names the placeholder it
prints, never name=value, and release's rows say one adopted machine, never a VM. An option is shown with its short
form and the name of its value. A usage line is the one written here for each verb, a word without brackets where the
table says the verb needs it; a verb that needs its word refuses none once built, and the placeholder of words a verb
refuses whole is refused by its own rule. An option of a later card's is left out of help and the reference until that
card. aw help lists every verb of the table in its order, each with its doc and [options] where it has any, and no
recipe of the repository's own; the options given before a verb, but the hidden one, with their form; and its closing
paragraph, with -v and --set each named on the verbs whose row has it, its commands written by the printer. Help is as
wide as its terminal, COLUMNS first, at most WIDTH and at least 72, and WIDTH with none to ask; at that width, with no
terminal and at 80 columns, every row of the listing and of every verb, joined again, is the row unwrapped, its lines
after the first at its text's column, and no line breaks inside a command the table's sentences name, the program, an
option and its value, a word and the option after it or a form's example, at any width from 72 to WIDTH, the program
as aw and started from a path with a space; a line is longer than the width only where it holds one such piece alone.
A refusal and the reference are not wrapped. The tests that read a row's end read it joined again. Through
tools/aw and the launcher, aw alone is aw help, a word that is no verb is refused unsaid, control among them, and a
second word in help.1's sentence. help and the reference import the standard library and schema/ alone, the printer
among it, and aw help loads nothing of the engine. The reference gives a row for every verb but help, every option but
a later card's and the words of each, and every option given before a verb but the hidden one, each held to a pattern
with what a value may be in words and then the pattern as code, never split by a | nor shown with a backslash; and
escapes a cell as tools/docs.py does.
"""

from __future__ import annotations

import io
import os
import re
import shlex
import sys

import harness
import pytest
from aw_kit import imports_of, on_path, run_aw, run_launcher
from harness import REPO
from test_aw_cli_words import EXAMPLE_OF, PATTERN_OF, WORDS_OF

from claude_vm.cli import help as helps
from claude_vm.cli import parse, reference
from claude_vm.schema import check as checks
from claude_vm.schema import printer, rules
from claude_vm.schema.patterns import PATTERNS
from claude_vm.schema.printer import said
from claude_vm.schema.words import BEFORE, EXITS, LINES, SET, VERBOSE, VERBS, Option, Words, placeholder

MODULES = ("claude_vm.cli.help", "claude_vm.cli.reference")
# Of the new engine's own, what help and the reference may load: schema/, and these.
OURS = {"claude_vm.cli.help", "claude_vm.cli.reference"}
# What aw help never loads: the engine's packages and its workflows.
HEAVY = ("pydantic", "ansible", "yaml", "asyncio", "claude_vm.engine")


# A value no pattern takes: a control character in it, and a word a person may have typed.
BAD = "swordfish\x07"


def _form(item: Option | Words) -> str:
    """What a row ends with after its help, for a value held to a pattern: what a value may be, in words (the check's
    form_of, with the item's own words and example where the table gives them); nothing for none."""
    return "" if item.pattern is None else f" {checks.form_of(item)}"


def _helped(option: Option) -> str:
    return said(option.help or "") + _form(option)


def _named_form(verb: str, word: str, name: str) -> str:
    """The form the hand-written maps of test_aw_cli_words.py give an option or words, made here and not by form: the
    words they name or the pattern's, and the example they name, or none."""
    words = WORDS_OF.get((verb, word), PATTERNS[name].words)
    example = EXAMPLE_OF[verb][word]
    return f"[{words}]" if example is None else f"[{words}, e.g. {example}]"


def _refused_whole(verb: str, word: str) -> bool:
    """Whether word, as the hand-written maps name it, is the placeholder of the words verb refuses whole
    (Words.refused): no form, and no row of the reference."""
    held = VERBS[verb].words if verb else None
    return held is not None and held.refused and word == placeholder(held)


def _described(item: Option | Words) -> str:
    """A reference row's text: the help said and escaped and, where the value is held to a pattern, what it may be in
    words, then the pattern itself as code, an HTML code element with &#124; for each | where it has one."""
    text = reference.cell(said(item.help or "") + _form(item))
    if item.pattern is None:
        return text
    raw = PATTERNS[item.pattern].text
    assert not set("<>&`") & set(raw), raw
    return f"{text}, held to " + (f"<code>{raw.replace('|', '&#124;')}</code>" if "|" in raw else f"`{raw}`")


# A row's first line in aw help <verb>: its indent and its left side, then two spaces or more before its text.
_LEFT = re.compile(r" +\S+(?: \S+)*?  +(?=\S)")
# The lines of help that are no row's: its headings and a verb's usage line.
HEADINGS = (helps.LISTING, helps.BEFORE_ANY, helps.OPTIONS, helps.WORDS)


def _column(line: str) -> int | None:
    """Where the text of the row a line begins starts: after the listing's #, after a verb's row's left side, 0 for a
    line of a paragraph (a verb's doc, the closing paragraph); None for a line of no row (a heading, a group's name, the
    usage line, a blank one)."""
    if line.startswith("    ") and " # " in line:
        return line.index(" # ") + 3
    if line.startswith(" "):
        left = _LEFT.match(line)
        return left.end() if left else None
    if not line or line in HEADINGS or line.startswith(f"{helps.USAGE} "):
        return None
    return 0


def _joined(text: str) -> list[str]:
    """help's lines with each row joined again: a line that starts at the column where the row before it has its text
    (a paragraph's next line, at 0) is that row's next line. The closing paragraph's sentences join into one line."""
    joined: list[str] = []
    column = None
    for line in text.splitlines():
        if column is not None and line[:column] == " " * column and line[column : column + 1] not in ("", " "):
            joined[-1] += f" {line[column:]}"
            continue
        joined.append(line)
        column = _column(line)
    return joined


# ---------------------------------------------------------------- the help of one verb ----


@pytest.mark.parametrize("name", list(VERBS))
def test_help_of_a_verb_shows_its_usage_doc_options_and_words_in_the_table_s_order(name: str) -> None:
    verb = VERBS[name]
    lines = _joined(helps.of(name))
    assert lines[:3] == [f"{helps.USAGE} {helps.usage(verb)}", "", said(verb.doc)]
    # Only a word before any verb is hidden from help: every option of a verb has its sentence.
    assert all(option.help for option in checks.options_of(verb)), name
    options = [option for option in checks.options_of(verb) if option.until is None]
    assert helps.options(verb) == options, name
    shown = [(helps.shown(option), _helped(option)) for option in options]
    words = []
    if verb.words is not None:
        formed = not verb.words.refused
        words = [(placeholder(verb.words), said(verb.words.help) + (_form(verb.words) if formed else ""))]
    rows = [line for line in lines if line.startswith("  ")]
    assert len(rows) == len(shown) + len(words), name
    for row, (left, text) in zip(rows, shown + words, strict=True):
        assert row.lstrip().startswith(left) and row.endswith(f"  {text}"), (row, left)
    assert (helps.OPTIONS in lines) is bool(options) and (helps.WORDS in lines) is bool(words)
    detach = helps.shown(checks.DETACH_FLAG)
    detached = [row for row in rows if row.lstrip().startswith(f"{detach} ")]
    assert detached == (rows[len(shown) - 1 : len(shown)] if verb.changes else []), name


@pytest.mark.parametrize("name", list(VERBS))
def test_help_says_the_form_of_each_value_the_table_holds_to_a_pattern_and_no_empty_default(name: str) -> None:
    # The owner's frame and answer 7 of 2026-11-21: help says what each value may be, as just --usage did, and leaves
    # out [default: ''], which says nothing.
    verb = VERBS[name]
    text = "\n".join(_joined(helps.of(name)))
    rows = [line for line in text.splitlines() if line.startswith("  ")]
    for option in helps.options(verb):
        [row] = [row for row in rows if row.lstrip().startswith(f"{helps.shown(option)} ")]
        if option.pattern is not None:
            assert row.endswith(f" {checks.form_of(option)}"), row
        else:
            assert not row.endswith("]"), row
    if verb.words is not None:
        [row] = [row for row in rows if row.lstrip().startswith(f"<{verb.words.name}> ")]
        formed = verb.words.pattern is not None and not verb.words.refused
        assert row.endswith(f" {checks.form_of(verb.words)}") if formed else not row.endswith("]"), row
    # The pattern itself is never what help says (the owner's ruling, review/code/OWNER-9.md 2).
    assert [name for name, pattern in PATTERNS.items() if pattern.text in text] == []
    listed = "\n".join(_joined(helps.listing()))
    assert "[pattern: " not in text and "[default:" not in text and "[default:" not in listed


def test_help_ends_the_row_of_every_option_and_words_the_hand_written_map_names_with_what_its_value_may_be() -> None:
    # test_aw_cli_words.py's PATTERN_OF, EXAMPLE_OF and WORDS_OF, written by hand: the form each row ends with hangs on
    # their names and examples, not the table's, so a slip in either fails one of the two, a pattern's shown flipped and
    # an option's own example among them. An option of a later card's is not in help, nor are the words of a verb that
    # refuses any.
    for verb, named in PATTERN_OF.items():
        text = helps.of(verb) if verb else helps.listing()
        rows = [line for line in _joined(text) if line.startswith("  " if verb else "    ")]
        options = {checks.spelled(option): option for option in (checks.options_of(VERBS[verb]) if verb else BEFORE)}
        for word, name in named.items():
            if (word in options and options[word].until is not None) or _refused_whole(verb, word):
                continue
            [row] = [row for row in rows if re.match(rf"\s*(-[a-z], )?{re.escape(word)} ", row)]
            assert row.endswith(f" {_named_form(verb, word, name)}"), (verb, word, row)


def test_the_refusal_of_every_option_and_words_the_hand_written_map_names_ends_with_what_its_value_may_be() -> None:
    # The same maps, through the parser: a value outside its pattern is refused with the form help's row ends with, an
    # option given before any verb among them (--attended, review/code/H29.md 5), and an option of a later card's,
    # which the parser takes all the same; the words of a verb that refuses any are refused with their help alone
    # (H29.md 3), since no word could take a form.
    for verb, named in PATTERN_OF.items():
        for word, name in named.items():
            given = BAD if word.startswith("<") else f"{word}={BAD}"
            words = [given, "status"] if not verb else [verb, given]
            with pytest.raises(parse.Refused) as refused:
                parse.read(words)
            sentence = refused.value.sentence
            assert "swordfish" not in sentence and "\n" not in sentence, sentence
            if _refused_whole(verb, word):
                held = VERBS[verb].words
                assert held is not None and sentence.endswith(f": {said(held.help)}"), sentence
                assert "[" not in sentence, sentence
                continue
            assert sentence.endswith(f" {_named_form(verb, word, name)}"), (verb, word, sentence)


def test_set_s_row_names_the_placeholder_it_prints_and_never_name_equals_value() -> None:
    # The sentence says --set <name>=<value>, as the row's left side shows it (words.placeholder), on every verb that
    # has it, and so does the reference.
    having = [name for name, verb in VERBS.items() if SET in verb.options]
    assert having
    for name in having:
        [row] = [row for row in _joined(helps.of(name)) if row.lstrip().startswith(f"{helps.shown(SET)} ")]
        assert f": {helps.shown(SET)}, once for each " in row and "name=value" not in row, row
    assert "name=value" not in reference.commands() and "name=value" not in helps.listing()


def test_release_s_rows_say_one_adopted_machine_never_a_vm() -> None:
    # Its words and --confirm are held to a VM's patterns, which take several names: their form says one machine
    # (review/code/H29.md 2).
    rows = [row for row in _joined(helps.of("release")) if row.startswith("  ")]
    formed = [row for row in rows if row.lstrip().startswith(("--confirm ", "<machine> "))]
    assert len(formed) == 2 and all(f"[{WORDS_OF['release', '<machine>']}, e.g. alpha]" in row for row in formed)
    assert [row for row in rows if "VM" in row or "commas" in row] == []


def test_an_option_of_a_later_card_is_left_out_of_help_and_the_reference_until_that_card() -> None:
    # The installed program's update (H14, H15), which a checkout refuses: the table marks it, and help reads the mark.
    later = {
        (name, option.long): option.until for name, verb in VERBS.items() for option in verb.options if option.until
    }
    assert later == {
        ("install", "version"): "H14",
        ("install", "newer"): "H15",
        ("install", "rollback"): "H15",
        ("install", "without"): "H14",
    }
    for (name, long), _ in later.items():
        assert f"--{long}" not in helps.of(name) and f"`--{long} " not in reference.commands(), long
        assert f"`--{long}`" not in reference.commands(), long
    assert "--lock" in helps.of("install")
    # The parser and the check take them all the same: the verb refuses them.
    assert parse.read(["install", "--newer"]).values == {"newer": True}


# Each verb's usage line after aw and the verb, written by hand: [<name>...] for several words, [<name>] for one,
# <name> for one the verb needs (Words.needed), nothing where it takes none or refuses any (Words.refused).
USAGE_WORDS = {
    "help": ["[<command>]"],
    "init": [],
    "install": [],
    "vault": ["[<name>]"],
    "deploy": ["[<name>...]"],
    "code": ["<name>"],
    "login": ["[<name>...]"],
    "status": ["[<name>...]"],
    "settings": ["[<name>]"],
    "destroy": ["[<name>...]"],
    "release": ["<machine>"],
    "host": ["[<name>]"],
    "logs": ["[<job>]"],
    "cancel": ["[<job>]"],
    "mcp": [],
}


def test_a_usage_line_needs_a_word_only_where_its_verb_needs_one() -> None:
    # Read from the table's Words.needed and Words.refused, never from a list of verbs or the words' name.
    assert {name: helps.usage(verb).split()[2:] for name, verb in VERBS.items()} == USAGE_WORDS
    assert helps.usage(VERBS["code"]) == "aw code <name>"
    bare = [name for name, shown in USAGE_WORDS.items() if shown and not shown[0].startswith("[")]
    assert bare == [name for name, verb in VERBS.items() if verb.words is not None and verb.words.needed]


def test_a_verb_that_needs_its_word_refuses_none_once_built() -> None:
    # Help shows <name> without brackets only where the verb needs it: once its handler is built, the one check refuses
    # it given none. code's is refused today, built or not, by its rule code.1, as today's page shows it.
    needed = [name for name, verb in VERBS.items() if verb.words is not None and verb.words.needed]
    assert needed == ["code", "release"]
    for name in needed:
        if VERBS[name].handler is not None:
            assert checks.check(name, {}) is not None, name
    assert checks.check("code", {}) == rules.RULES["code.1"].check({})


@pytest.mark.parametrize(
    "name", [name for name, verb in VERBS.items() if verb.words is not None and verb.words.refused]
)
def test_the_placeholder_of_words_a_verb_refuses_whole_is_refused_by_its_own_rule(monkeypatch, name: str) -> None:
    # init and mcp refuse any word after their options, so "put yours in its place" would be no way out: their
    # placeholder, which help prints, is refused as any word is, by the verb's own rule, never by the placeholder's
    # sentence (H27's hand-back).
    monkeypatch.setattr(printer, "_program", printer.PROGRAM)
    words = VERBS[name].words
    assert words is not None
    sentence = rules.run(VERBS[name].rules, {words.field: "x"})
    assert sentence is not None and checks.check(name, {words.field: "x"}) == sentence
    assert checks.check(name, {words.field: placeholder(words)}) == sentence
    with pytest.raises(parse.Refused) as refused:
        parse.read([name, placeholder(words)])
    assert refused.value.sentence == sentence and "stands for" not in sentence


def test_an_option_is_shown_with_its_short_form_and_its_value_s_placeholder() -> None:
    # The owner's ruling of 2026-11-22 (review/code/OWNER-9.md 1): a value's placeholder is <name>, lower case in angle
    # brackets, the same in help's rows, the reference and a line that shows a command's form, from words.py's
    # placeholder; and the name says what the value is (--host <host>, never today's parameter name, ON).
    deploy = {option.field: option for option in checks.options_of(VERBS["deploy"])}
    assert helps.shown(deploy["tags"]) == "-t, --tags <roles>"
    assert helps.shown(deploy["only"]) == "--only <stage>"
    assert helps.shown(deploy["cores"]) == "--cores <cores>"
    assert helps.shown(deploy["host"]) == "--host <host>"
    assert helps.shown(deploy["check"]) == "--check"
    assert helps.shown(deploy["detach"]) == "--detach"
    assert helps.shown(VERBOSE) == "-v"
    assert helps.shown(SET) == "--set <name>=<value>"
    init = {option.field: option for option in VERBS["init"].options}
    assert helps.shown(init["token_secret_file"]) == "--token-secret-file <file>"
    for verb in VERBS.values():
        for option in (*checks.options_of(verb), *BEFORE):
            if option.kind in ("value", "repeatable"):
                assert helps.shown(option).endswith(f" {placeholder(option)}"), option
            else:
                assert "<" not in helps.shown(option), option


# ---------------------------------------------------------------- the listing ----


def test_aw_help_lists_every_verb_in_the_table_s_order_with_its_doc_and_no_recipe_of_the_repository_s_own() -> None:
    lines = _joined(helps.listing())
    assert lines[0] == helps.LISTING
    end = lines.index(helps.BEFORE_ANY)
    listed = [line for line in lines[1:end] if line.startswith("    ") and not line.lstrip().startswith("[")]
    assert [line.split()[0] for line in listed] == list(VERBS)
    for line, verb in zip(listed, VERBS.values(), strict=True):
        assert line.endswith(f" # {said(verb.doc)}"), line
        # Its words as its usage line shows them, and [options] where it has any to show (review/code/H6.md 4).
        options = [helps.WITH_OPTIONS] if helps.options(verb) else []
        assert line.split(" # ")[0].split() == [*helps.usage(verb).split()[1:], *options], line
    assert [line.split()[0] for line in listed if helps.WITH_OPTIONS not in line] == ["help", "cancel"]
    groups = [line.strip() for line in lines[1:end] if line.lstrip().startswith("[")]
    assert groups == [f"[{group}]" for group in dict.fromkeys(verb.group for verb in VERBS.values()) if group]
    develop = {
        name for name, recipe in harness.public_recipes().items() if {"group": "develop"} in recipe["attributes"]
    }
    assert develop and not develop & {line.split()[0] for line in lines if line.startswith("    ")}


def test_aw_help_lists_the_options_given_before_a_verb_but_the_hidden_one() -> None:
    lines = _joined(helps.listing())
    before = lines[lines.index(helps.BEFORE_ANY) + 1 :]
    before = before[: before.index("")]
    assert [line.split(" # ")[0].strip() for line in before] == [
        helps.shown(option) for option in BEFORE if option.help is not None
    ]
    assert [line.split(" # ", 1)[1] for line in before] == [_helped(option) for option in BEFORE if option.help]
    hidden = [option for option in BEFORE if option.help is None]
    assert hidden and not [option for option in hidden if f"--{option.long}" in helps.listing()]
    # The one check's refusal of such an option sends a person here.
    assert checks.no_option("", "--bogus").endswith(f"({printer.line('help', {}, refused=False)} lists them)")


def test_aw_help_ends_in_its_closing_paragraph_with_v_and_set_on_the_verbs_that_have_them() -> None:
    lines = helps.listing().splitlines()
    closing = lines[len(lines) - len(helps.closing()) :]
    assert closing == helps.closing() and all(len(line) <= helps.columns() for line in closing)
    ours = re.split(r"(?<=\.) (?=[A-Z])", " ".join(closing))
    assert len(ours) == 3, ours
    assert ours[0] == (
        "Without names, deploy and destroy act on agent-warehouse-default; status, login and deploy --only configure, "
        "--check or --only ssh-config act on every VM this project created."
    )
    assert "ANSIBLE_ARGS" not in ours[1] and "just" not in " ".join(ours)
    assert ours[1].startswith("Options go after the command's name:"), ours[1]
    for option in (VERBOSE, SET):
        having = [name for name, verb in VERBS.items() if option in verb.options]
        named = ", ".join(having[:-1]) + " and " + having[-1]
        assert f"{helps.shown(option)} " in ours[1] and f" in {named}" in ours[1], (option.field, ours[1])
    assert "LOG_DIR=<dir>, exported or before aw, moves the run logs" in ours[1]
    # Its example is the printer's, and runs: deploy alpha, saying the most of the system layer.
    example = printer.line(*helps.EXAMPLE, host_flag="", refused=False)
    assert ours[1].endswith(f": {example}.")
    reading = parse.read(shlex.split(example)[1:])
    assert (reading.verb, reading.values) == ("deploy", {"names": ["alpha"], "verbosity": 3})
    hosts = printer.line("host", {}, refused=False)
    assert ours[2] == (
        f"With more than one Proxmox host, every command runs against the current one ({hosts} lists them), or the one "
        "--host names."
    )
    # A command a person pastes, and an option with its value, is never cut across two lines.
    for whole in (example, hosts, helps.shown(SET)):
        assert [line for line in closing if whole in line], whole


# ---------------------------------------------------------------- where help's lines break ----

# A width no row of help is as wide as: help unwrapped, each row on one line.
UNWRAPPED = sys.maxsize
# The programs help is read as started by: aw, and from a path with a space, as test_aw_cli_printer.py's tests start it.
PROGRAMS = ["aw", "/opt/my checkout/aw"]
# The spellings of the options that take a value, of every verb and before any: the word after one is its value.
VALUED = "|".join(
    re.escape(spelling)
    for option in [*BEFORE, *(option for verb in VERBS.values() for option in checks.options_of(verb))]
    if " " in helps.shown(option)
    for spelling in [f"--{option.long}"] * bool(option.long) + [f"-{option.short}"] * bool(option.short)
)


def _terminal(monkeypatch, columns: int | None, environ: int | None = None) -> None:
    """help's standard output as a terminal of columns, or as none to ask (a pipe, a file); COLUMNS as environ."""

    def size(fd: int = 1) -> os.terminal_size:
        if columns is None:
            raise OSError("not a terminal")
        return os.terminal_size((columns, 24))

    monkeypatch.setattr(os, "get_terminal_size", size)
    if environ is None:
        monkeypatch.delenv("COLUMNS", raising=False)
    else:
        monkeypatch.setenv("COLUMNS", str(environ))


def _kept(text: str) -> set[int]:
    """Where in a row's text help may not break a line, by this test's own reading of a piece: each space inside a
    command the table's sentences name (the printer's line for each of LINES, the program at its start) or the program
    alone, an option that takes a value and the word after it, a form's e.g. and its example, and each space before an
    option that follows a word."""
    spans = [
        found.span()
        for whole in [*(said(f"{{{name}}}") for name in LINES), checks.command("")]
        for found in re.finditer(re.escape(whole), text)
    ]
    for piece in (rf"(?<!\S)(?:{VALUED}) \S+", r"\be\.g\. \S+", r"(?<=[\w>\]]) (?=--?[A-Za-z])"):
        spans += [found.span() for found in re.finditer(piece, text)]
    return {at for start, end in spans for at in range(start, end) if text[at] == " "}


def _held(wrapped: str, unwrapped: str, width: int) -> list[str]:
    """Each row of help unwrapped, as its lines wrapped at width give it: each line after its first starting at the
    column where its text starts, the lines joined again the row; no line broken inside a piece, and none longer than
    width but one that is one piece, after its row's left side or indent. Returns the rows that were wrapped."""
    lines = wrapped.splitlines()
    at = 0
    broken = []
    for row in unwrapped.splitlines():
        column = _column(row) or 0
        text = row[column:]
        assert lines[at][:column] == row[:column], (row, lines[at])
        pieces = [lines[at][column:]]
        at += 1
        while " ".join(pieces) != text:
            assert at < len(lines) and len(" ".join(pieces)) < len(text), (row, pieces)
            assert lines[at][:column] == " " * column and lines[at][column : column + 1] not in ("", " "), lines[at]
            pieces.append(lines[at][column:])
            at += 1
        kept = _kept(text)
        start = 0
        for number, piece in enumerate(pieces):
            assert not number or start - 1 not in kept, ("broken inside a piece", row, pieces)
            spaces = [start + i for i, character in enumerate(piece) if character == " "]
            assert column + len(piece) <= width or set(spaces) <= kept, ("longer than the width", width, piece)
            start += len(piece) + 1
        broken += [row] * (len(pieces) > 1)
    assert at == len(lines), lines[at:]
    return broken


@pytest.mark.parametrize(
    ("terminal", "environ", "width"),
    [
        (None, None, helps.WIDTH),
        (80, None, 80),
        (72, None, 72),
        (40, None, helps.NARROWEST),
        (300, None, helps.WIDTH),
        (0, None, helps.WIDTH),
        (80, 90, 90),
        (None, 100, 100),
    ],
    ids=["no-terminal", "80", "72", "40", "300", "0", "COLUMNS-90-at-80", "COLUMNS-100"],
)
def test_help_is_as_wide_as_its_terminal_at_most_width_and_at_least_the_narrowest(
    monkeypatch, terminal: int | None, environ: int | None, width: int
) -> None:
    # review/code/OWNER-11.md 2: the terminal's columns, COLUMNS first, at most WIDTH and at least 72; WIDTH where there
    # is no terminal to ask. One width for the whole of help, its closing paragraph among it.
    _terminal(monkeypatch, terminal, environ)
    assert helps.NARROWEST == 72 and helps.columns() == width
    for name, printed in [(None, helps.listing(width)), ("deploy", helps.of("deploy", width))]:
        out = io.StringIO()
        assert helps.main(None, name, out=out) == EXITS["done"] and out.getvalue() == printed
    assert helps.listing().endswith("\n".join(helps.closing(width)) + "\n")


@pytest.mark.parametrize("program", PROGRAMS, ids=["aw", "space"])
@pytest.mark.parametrize("terminal", [None, 80], ids=["no-terminal", "80-columns"])
def test_every_row_of_help_is_wrapped_in_its_own_column_and_nothing_pasted_is_broken(
    monkeypatch, terminal: int | None, program: str
) -> None:
    # The listing and every verb, as aw help prints them: each row joined again is the row unwrapped, each line after
    # its first at the text's column, and no line longer than the width but one that holds a single piece.
    monkeypatch.setattr(printer, "_program", printer.PROGRAM)
    printer.started_as(program)
    _terminal(monkeypatch, terminal)
    width = helps.WIDTH if terminal is None else terminal
    broken = []
    for name in [None, *VERBS]:
        out = io.StringIO()
        helps.main(None, name, out=out)
        broken += _held(out.getvalue(), helps.listing(UNWRAPPED) if name is None else helps.of(name, UNWRAPPED), width)
    # Rows are wrapped at all: the closing paragraph's, the listing's and a verb's options'.
    assert [row for row in broken if not row.startswith(" ")] and [row for row in broken if " # " in row]
    assert [row for row in broken if row.startswith("  ") and " # " not in row]


@pytest.mark.parametrize("program", PROGRAMS, ids=["aw", "space"])
def test_no_width_from_the_narrowest_to_width_breaks_a_piece(monkeypatch, program: str) -> None:
    # A piece is cut only at some widths: every width help may take, the listing and every verb.
    monkeypatch.setattr(printer, "_program", printer.PROGRAM)
    printer.started_as(program)
    for width in range(helps.NARROWEST, helps.WIDTH + 1):
        for name in [None, *VERBS]:
            if name is None:
                _held(helps.listing(width), helps.listing(UNWRAPPED), width)
            else:
                _held(helps.of(name, width), helps.of(name, UNWRAPPED), width)


def test_the_reference_and_a_refusal_are_not_wrapped(monkeypatch) -> None:
    # A refusal stays one line on standard error, which a script and an agent read whole; the reference is a table.
    _terminal(monkeypatch, helps.NARROWEST)
    with pytest.raises(parse.Refused) as refused:
        parse.read(["deploy", "--only=check"])
    assert "\n" not in refused.value.sentence and len(refused.value.sentence) > helps.WIDTH
    rows = [row for row in reference.commands().splitlines() if row.startswith("| `")]
    assert rows and max(len(row) for row in rows) > helps.WIDTH


# ---------------------------------------------------------------- through tools/aw ----


@pytest.mark.parametrize("words", [(), ("help",), ("--attended=1",)])
def test_aw_alone_is_aw_help(home, words) -> None:
    result = run_aw(*words, home=home)
    assert (result.returncode, result.stdout, result.stderr) == (EXITS["done"], helps.listing(), "")


def test_the_launcher_prints_help_of_deploy(home) -> None:
    # aw on PATH is the launcher, so help names aw, as in this process.
    result = run_launcher("help", "deploy", home=home, cwd=REPO, first_on_path=(on_path(home),))
    assert (result.returncode, result.stdout, result.stderr) == (EXITS["done"], helps.of("deploy"), "")


@pytest.mark.parametrize("word", ["lint", "swordfish", "control"])
def test_a_word_that_is_no_verb_is_refused_unsaid(home, word) -> None:
    # lint is a recipe of the repository's own, which stays just's: no verb of aw's. control is the add-on's, whose row
    # the add-on adds (H22): none of the base's.
    result = run_aw("help", word, home=home)
    expected = (EXITS["refused"], "", checks.no_such_command() + "\n")
    assert (result.returncode, result.stdout, result.stderr) == expected
    assert word not in result.stderr


def test_a_second_word_is_refused_in_help_1_s_sentence(home) -> None:
    result = run_aw("help", "deploy", "login", home=home)
    sentence = "error: aw help shows one command's options at a time (got: [deploy login])"
    assert (result.returncode, result.stdout, result.stderr) == (EXITS["refused"], "", sentence + "\n")


def test_the_handler_writes_where_it_is_told() -> None:
    out, err = io.StringIO(), io.StringIO()
    assert helps.main(None, out=out, err=err) == EXITS["done"] and out.getvalue() == helps.listing()
    out = io.StringIO()
    assert helps.main(None, "logs", out=out, err=err) == EXITS["done"] and out.getvalue() == helps.of("logs")
    assert err.getvalue() == "" and VERBS["help"].handler == "cli.help:main"


# ---------------------------------------------------------------- what help imports ----


@pytest.mark.parametrize("module", MODULES)
def test_help_and_the_reference_import_the_standard_library_schema_and_the_printer_alone(home, module) -> None:
    # What importing its package alone loads is taken away: what is left is what the module itself brings.
    added = imports_of(module=module, home=home) - imports_of(module="claude_vm.cli", home=home)
    assert module in added, sorted(added)
    assert sorted({name.split(".")[0] for name in added} - set(sys.stdlib_module_names) - {"claude_vm"}) == []
    ours = {name for name in added if name.startswith("claude_vm.") and not name.startswith("claude_vm.schema")}
    assert ours <= OURS, sorted(ours)
    assert "argparse" not in added and "subprocess" not in added


@pytest.mark.parametrize("words", [(), ("help", "deploy")])
def test_aw_help_loads_nothing_of_the_engine(home, words) -> None:
    # The dispatch imports help.py by importlib, which -X importtime does not time: what help.py imports is listed.
    loaded = imports_of(*words, home=home)
    assert [name for name in loaded if name.split(".")[0] in HEAVY or name.startswith(HEAVY[-1])] == []
    assert {name.split(".")[1] for name in loaded if name.startswith("claude_vm.")} <= {"cli", "schema", "plugin"}


# ---------------------------------------------------------------- the command reference ----


def test_the_reference_gives_a_row_for_every_verb_but_help_every_option_and_the_words() -> None:
    rows = reference.commands().splitlines()
    for name, verb in VERBS.items():
        first = f"| `{helps.usage(verb)}` | {reference.cell(said(verb.doc))} |"
        assert (first in rows) is (name != "help"), name
        if name == "help":
            continue
        under = [
            f"| {reference.INDENT_CELL}`{helps.shown(option)}` | {_described(option)} |"
            for option in checks.options_of(verb)
            if option.until is None
        ]
        if verb.words is not None and not verb.words.refused:
            words = _described(verb.words)
            under.append(f"| {reference.INDENT_CELL}`<{verb.words.name}>` | {words} |")
        at = rows.index(first) + 1
        assert rows[at : at + len(under)] == under, name
        assert not rows[at + len(under)].startswith(f"| {reference.INDENT_CELL}"), name
    before = [f"| `{helps.shown(option)}` | {_described(option)} |" for option in BEFORE if option.help]
    at = rows.index(f"### {reference.BEFORE_ANY}")
    assert rows[at + 1 :] == ["", *reference.BEFORE_HEAD, *before]
    groups = [*dict.fromkeys((verb.group or reference.OTHER) for name, verb in VERBS.items() if name != "help")]
    headings = [f"### {group.capitalize()}" for group in [*groups, reference.BEFORE_ANY]]
    assert [row for row in rows if row.startswith("### ")] == headings


def test_a_pattern_in_the_reference_neither_splits_its_cell_nor_shows_a_backslash() -> None:
    # A | splits a GitHub table's cell, and \| inside a code span shows its backslash on the site (tools/docs.py's
    # value): a pattern with one is an HTML code element with &#124;, and every row keeps its two cells.
    text = reference.commands()
    rows = [row for row in text.splitlines() if row.startswith("| ") and not row.startswith("|---")]
    assert rows
    for row in rows:
        assert len(re.findall(r"(?<!\\)\|", row)) == 3, row
        assert [span for span in re.findall(r"`[^`]*`", row) if "|" in span] == [], row
    piped = {name for name, pattern in PATTERNS.items() if "|" in pattern.text}
    shown = {option.pattern for verb in VERBS.values() for option in helps.options(verb)} | {
        option.pattern for option in BEFORE if option.help
    }
    assert piped & shown >= {"stage", "sign_in", "sections", "editor", "attended", "on_off"}
    for name in piped & shown:
        assert f"<code>{PATTERNS[name].text.replace('|', '&#124;')}</code>" in text, name
    assert reference.code("(a|b)?") == "<code>(a&#124;b)?</code>" and reference.code("[a-z]+") == "`[a-z]+`"


def test_the_reference_escapes_a_cell_as_tools_docs_does() -> None:
    docs = harness.load_module("tools/docs.py", "docs_generator", register=False)
    assert (reference.CODE.pattern, reference.INDENT_CELL) == (docs.CODE.pattern, docs.INDENT_CELL)
    texts = [verb.doc for verb in VERBS.values()] + [verb.words.help for verb in VERBS.values() if verb.words]
    texts += [option.help or "" for verb in VERBS.values() for option in (*verb.options, *BEFORE)]
    texts += ["<name> & a | b", "`<code> | x` <y>", "<code>a|b</code> [x](<y>) `` ` ``", " padded "]
    for text in texts:
        assert (reference.escape(text), reference.cell(text)) == (docs.escape(text), docs.cell(text)), text
