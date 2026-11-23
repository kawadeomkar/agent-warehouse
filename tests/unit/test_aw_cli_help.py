"""aw help, two levels printed from the one table, and the command reference's tables written from it
(lib/claude_vm/cli/help.py, lib/claude_vm/cli/reference.py; docs/architecture/engine.md §16.3).

For every verb of the table aw help <verb> shows its usage, its doc, each of its options with its help, --detach last
where its run is a job, and its words, each sentence said with the printer's commands; after the help of every option
and words the table holds to a pattern, what a value may be in words, never the pattern itself, and never an empty
default; and so for every option and words the hand-written map of test_aw_cli_words.py names. An option is shown with
its short form and the name of its value. An option of a later card's is left out of help and the reference until that
card. aw help lists every verb of the table in its order, each with its doc and [options] where it has any, and no
recipe of the repository's own; the options given before a verb, but the hidden one, with their form; and its closing
paragraph, with -v and --set each named on the verbs whose row has it, its commands written by the printer. Through
tools/aw and the launcher, aw alone is aw help, a word that is no verb is refused unsaid, control among them, and a
second word in help.1's sentence. help and the reference import the standard library and schema/ alone, the printer
among it, and aw help loads nothing of the engine. The reference gives a row for every verb but help, every option but
a later card's and the words of each, and every option given before a verb but the hidden one, each held to a pattern
with what a value may be in words and then the pattern as code, never split by a | nor shown with a backslash; and
escapes a cell as tools/docs.py does.
"""

from __future__ import annotations

import io
import re
import shlex
import sys

import harness
import pytest
from aw_kit import imports_of, on_path, run_aw, run_launcher
from harness import REPO
from test_aw_cli_words import PATTERN_OF

from claude_vm.cli import help as helps
from claude_vm.cli import parse, reference
from claude_vm.schema import check as checks
from claude_vm.schema import printer
from claude_vm.schema.patterns import PATTERNS, form
from claude_vm.schema.printer import said
from claude_vm.schema.words import BEFORE, EXITS, SET, VERBOSE, VERBS, Option, placeholder

MODULES = ("claude_vm.cli.help", "claude_vm.cli.reference")
# Of the new engine's own, what help and the reference may load: schema/, and these.
OURS = {"claude_vm.cli.help", "claude_vm.cli.reference"}
# What aw help never loads: the engine's packages and its workflows.
HEAVY = ("pydantic", "ansible", "yaml", "asyncio", "claude_vm.engine")


def _form(name: str | None) -> str:
    """What a row ends with after its help, for a value held to the pattern name: what a value may be, in words
    (patterns.py's form); nothing for none."""
    return "" if name is None else f" {form(name)}"


def _helped(option: Option) -> str:
    return said(option.help or "") + _form(option.pattern)


def _described(help: str, pattern: str | None) -> str:
    """A reference row's text: the help said and escaped and, where the value is held to a pattern, what it may be in
    words, then the pattern itself as code, an HTML code element with &#124; for each | where it has one."""
    text = reference.cell(said(help) + _form(pattern))
    if pattern is None:
        return text
    raw = PATTERNS[pattern].text
    assert not set("<>&`") & set(raw), raw
    return f"{text}, held to " + (f"<code>{raw.replace('|', '&#124;')}</code>" if "|" in raw else f"`{raw}`")


# ---------------------------------------------------------------- the help of one verb ----


@pytest.mark.parametrize("name", list(VERBS))
def test_help_of_a_verb_shows_its_usage_doc_options_and_words_in_the_table_s_order(name: str) -> None:
    verb = VERBS[name]
    lines = helps.of(name).splitlines()
    assert lines[:3] == [f"{helps.USAGE} {helps.usage(verb)}", "", said(verb.doc)]
    # Only a word before any verb is hidden from help: every option of a verb has its sentence.
    assert all(option.help for option in checks.options_of(verb)), name
    options = [option for option in checks.options_of(verb) if option.until is None]
    assert helps.options(verb) == options, name
    shown = [(helps.shown(option), _helped(option)) for option in options]
    words = []
    if verb.words is not None:
        formed = verb.words.name != helps.REFUSED_WORDS
        words = [(placeholder(verb.words), said(verb.words.help) + _form(verb.words.pattern if formed else None))]
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
    text = helps.of(name)
    rows = [line for line in text.splitlines() if line.startswith("  ")]
    for option in helps.options(verb):
        [row] = [row for row in rows if row.lstrip().startswith(f"{helps.shown(option)} ")]
        if option.pattern is not None:
            assert row.endswith(f" {form(option.pattern)}"), row
        else:
            assert not row.endswith("]"), row
    if verb.words is not None:
        [row] = [row for row in rows if row.lstrip().startswith(f"<{verb.words.name}> ")]
        formed = verb.words.pattern is not None and verb.words.name != helps.REFUSED_WORDS
        assert row.endswith(f" {form(verb.words.pattern or '')}") if formed else not row.endswith("]"), row
    # The pattern itself is never what help says (the owner's ruling, review/code/OWNER-9.md 2).
    assert [name for name, pattern in PATTERNS.items() if pattern.text in text] == []
    assert "[pattern: " not in text and "[default:" not in text and "[default:" not in helps.listing()


def test_help_ends_the_row_of_every_option_and_words_the_hand_written_map_names_with_what_its_value_may_be() -> None:
    # test_aw_cli_words.py's PATTERN_OF, written by hand: the form each row ends with hangs on its names, not the
    # table's, so a slip in either fails one of the two. An option of a later card's is not in help, nor are the
    # words of a verb that refuses any.
    for verb, named in PATTERN_OF.items():
        text = helps.of(verb) if verb else helps.listing()
        rows = [line for line in text.splitlines() if line.startswith("  " if verb else "    ")]
        options = {checks.spelled(option): option for option in (checks.options_of(VERBS[verb]) if verb else BEFORE)}
        for word, name in named.items():
            if (word in options and options[word].until is not None) or word == f"<{helps.REFUSED_WORDS}>":
                continue
            [row] = [row for row in rows if re.match(rf"\s*(-[a-z], )?{re.escape(word)} ", row)]
            assert row.endswith(f" {form(name)}"), (verb, word, row)


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


def test_a_usage_line_needs_a_word_only_where_its_verb_needs_one() -> None:
    assert helps.usage(VERBS["deploy"]).split()[2:] == ["[<name>...]"]
    assert helps.usage(VERBS["settings"]).split()[2:] == ["[<name>]"]
    assert helps.usage(VERBS["init"]).split()[2:] == helps.usage(VERBS["install"]).split()[2:] == []
    for name in helps.NEEDS_ITS_WORD:
        verb = VERBS[name]
        assert verb.words is not None and not verb.words.plural, name
        assert helps.usage(verb).split()[2:] == [placeholder(verb.words)], name
    # code's is today's (rules.py's code.1 refuses none), as today's page shows it.
    assert "code" in helps.NEEDS_ITS_WORD and checks.check("code", {}) is not None


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
    lines = helps.listing().splitlines()
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
    lines = helps.listing().splitlines()
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
    assert closing == helps.closing() and all(len(line) <= helps.WIDTH for line in closing)
    ours = re.split(r"(?<=\.) (?=[A-Z])", " ".join(closing))
    assert len(ours) == 3, ours
    assert ours[0] == (
        "Without names, deploy and destroy act on agent-warehouse-default; status, login and deploy --only configure, "
        "--check or --only ssh-config act on every VM this project created."
    )
    assert "ANSIBLE_ARGS" not in ours[1] and "just" not in " ".join(ours)
    assert ours[1].startswith("Options go after the recipe's name:"), ours[1]
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
    sentence = "error: aw help shows one recipe's options at a time (got: [deploy login])"
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
            f"| {reference.INDENT_CELL}`{helps.shown(option)}` | {_described(option.help or '', option.pattern)} |"
            for option in checks.options_of(verb)
            if option.until is None
        ]
        if verb.words is not None and verb.words.name != helps.REFUSED_WORDS:
            words = _described(verb.words.help, verb.words.pattern)
            under.append(f"| {reference.INDENT_CELL}`<{verb.words.name}>` | {words} |")
        at = rows.index(first) + 1
        assert rows[at : at + len(under)] == under, name
        assert not rows[at + len(under)].startswith(f"| {reference.INDENT_CELL}"), name
    before = [
        f"| `{helps.shown(option)}` | {_described(option.help or '', option.pattern)} |"
        for option in BEFORE
        if option.help
    ]
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
