"""aw help, today's two levels printed from the one table, and the command reference's tables written from it
(lib/claude_vm/cli/help.py, lib/claude_vm/cli/reference.py; docs/architecture/engine.md §16.3).

For every verb of the contract of today's command line (tests/unit/cli_contract.json), aw help <verb> holds each option
as just --usage <verb> shows it today and each help sentence just --usage prints, with aw where it said just, but a
word the cutover's map records as changed for another reason (tests/unit/cli_contract_changes.py's CHANGED); its usage
line is the one today's command reference writes, where the verb takes the words it takes today; and it holds every row
today's page shows for the verb. For every verb of the table it shows its usage, its doc, each of its options with its
help, --detach last where its run is a job, and its words, each sentence said with the printer's commands; after the
help of every option and words the table holds to a pattern, the form a value may take, and never an empty default. An
option of a later card's is left out of help and the reference until that card. aw help lists every verb of the table
in its order, each with its doc and [options] where it has any, and no recipe of the repository's own; the options
given before a verb, but the hidden one, with their form; and today's closing paragraph, with aw where it said just and
-v and --set, each named on the verbs whose row has it, where it named ANSIBLE_ARGS, its commands written by the
printer. Through tools/aw and the launcher, aw alone is aw help, a word that is no verb is refused unsaid, control
among them, and a second word in today's sentence. help and the reference import the standard library, schema/ and
the printer alone, and aw help loads nothing of the engine. The reference gives a row for every verb but help, every
option but a later card's and the words of each, and every option given before a verb but the hidden one; holds
today's page's rows with aw where they said just; and escapes a cell as tools/docs.py does.
"""

from __future__ import annotations

import html
import io
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import harness
import pytest
from aw_kit import imports_of, on_path, run_aw, run_launcher
from cli_contract_changes import CHANGED, NEW, WORD_1
from harness import REPO
from test_aw_cli_contract_words import RECIPES, _key

from claude_vm.cli import help as helps
from claude_vm.cli import inverse, parse, reference
from claude_vm.schema import check as checks
from claude_vm.schema.patterns import PATTERNS
from claude_vm.schema.words import BEFORE, EXITS, SET, VERBOSE, VERBS, Option, said

CONTRACT = json.loads((Path(__file__).resolve().parent / "cli_contract.json").read_text())
CASES = {case["code"]: case for case in CONTRACT["cases"]}
# Today's page of the command reference, which just docs writes from the justfile.
PAGE = REPO / "docs" / "reference" / "commands.md"
MODULES = ("claude_vm.cli.help", "claude_vm.cli.reference")
# Of the new engine's own, what help and the reference may load: schema/, and these.
OURS = {"claude_vm.cli.help", "claude_vm.cli.inverse", "claude_vm.cli.reference"}
# What aw help never loads: the engine's packages and its workflows.
HEAVY = ("pydantic", "ansible", "yaml", "asyncio", "claude_vm.engine")


def _aw(text: str) -> str:
    """Today's text as aw says it: aw where it said just (the owner's word 1)."""
    return re.sub(r"\bjust\b", inverse.PROGRAM, text)


# The words whose help the clean slate changed, beside the maps, which go with the contract (H26): init --host is a
# host's name alone, with no address taken for --address (review/clean-slate/PROPOSAL.md, decision 10; H25).
CLEAN_SLATE = {"init --host"}


def _changed(key: str) -> bool:
    """Whether the cutover's map records the word, or its help, as changed for more than aw where it said just, or the
    clean slate changed it."""
    return key in CHANGED or CHANGED.get(f"{key}: help", WORD_1) != WORD_1 or key in CLEAN_SLATE


def _plain(text: str) -> str:
    """A table cell's text as a terminal shows it: unescaped as tools/docs.py's cell escapes it."""
    return html.unescape(text.replace("\\|", "|"))


def _new_words(name: str) -> bool:
    """Whether the verb takes words today's recipe does not, so that its usage line is not today's."""
    return any(key.startswith(f"{name} <") for key in NEW)


def _usage_today(name: str) -> str:
    """What just --usage prints today for a recipe of the justfile."""
    return subprocess.run(
        [harness.JUST, "--justfile", str(REPO / "justfile"), "--usage", name],
        cwd=REPO,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout


def _unformed(row: str) -> str:
    """A row of aw help without the form it ends with, as today's page shows the row."""
    return row.rsplit(" [pattern: ", 1)[0] if " [pattern: " in row else row


def _form(name: str | None) -> str:
    """What a row ends with after its help, for a value held to the pattern name: the pattern's text, as just --usage
    prints it today; nothing for none."""
    return "" if name is None else f" [pattern: '{PATTERNS[name]}']"


def _helped(option: Option) -> str:
    return said(option.help or "") + _form(option.pattern)


def _flags(option: dict) -> str:
    """An option of the contract as just --usage shows it: -t, --tags ROLES; --check for a flag."""
    spelled = ", ".join([f"-{option['short']}"] * bool(option["short"]) + [f"--{option['long']}"])
    return spelled if option["value"] is not None else f"{spelled} {option['name']}"


# ---------------------------------------------------------------- the help of one verb ----


@pytest.mark.parametrize("name", list(RECIPES))
def test_help_of_a_verb_holds_each_option_and_sentence_just_usage_prints_today(name: str) -> None:
    today, ours = _usage_today(name), helps.of(name)
    for option in RECIPES[name]["options"]:
        key = _key(name, option)
        # The flag string and the sentence are what just --usage prints today: the contract holds them as just reads
        # them.
        assert option["help"] in today, key
        if not option["words"]:
            assert _flags(option) in today, key
        if key in CHANGED:
            continue  # the word itself is another (mcp --vault-password-file is --vault-key-file)
        if not option["words"]:
            assert _flags(option) in ours, key
        if not _changed(key):
            assert _aw(option["help"]) in ours, key
    if not _new_words(name):
        words = RECIPES[name]["words"]
        assert ours.splitlines()[0] == " ".join([helps.USAGE, inverse.PROGRAM, name, *([words] if words else [])])


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
        words = [(f"<{verb.words.name}>", said(verb.words.help) + _form(verb.words.pattern if formed else None))]
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
            assert row.endswith(f" [pattern: '{PATTERNS[option.pattern]}']"), row
        else:
            assert "[pattern: " not in row, row
    if verb.words is not None:
        [row] = [row for row in rows if row.lstrip().startswith(f"<{verb.words.name}> ")]
        formed = verb.words.pattern is not None and verb.words.name != helps.REFUSED_WORDS
        assert row.endswith(f" [pattern: '{PATTERNS[verb.words.pattern]}']") if formed else "[pattern: " not in row
    assert "[default:" not in text and "[default:" not in helps.listing()


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
        assert helps.usage(verb).split()[2:] == [f"<{verb.words.name}>"], name
    # code's is today's (rules.py's code.1 refuses none), as today's page shows it.
    assert "code" in helps.NEEDS_ITS_WORD and checks.check("code", {}) is not None


def test_an_option_is_shown_as_just_usage_shows_one_today() -> None:
    deploy = {option.field: option for option in checks.options_of(VERBS["deploy"])}
    assert helps.shown(deploy["tags"]) == "-t, --tags ROLES"
    assert helps.shown(deploy["only"]) == "--only STAGE"
    assert helps.shown(deploy["check"]) == "--check"
    assert helps.shown(deploy["detach"]) == "--detach"
    assert helps.shown(VERBOSE) == "-v"
    assert helps.shown(SET) == "--set NAME=VALUE"


def _rows(text: str) -> dict[str, list[tuple[str, str]]]:
    """Each verb's rows of a page's command tables, by the verb: its usage row first, then its options' and its
    words', each as its code and its text."""
    tables: dict[str, list[tuple[str, str]]] = {}
    verb = ""
    for line in text.splitlines():
        row = re.fullmatch(r"\| (&emsp;)?`([^`]+)` \| (.*) \|", line)
        if row is None:
            continue
        if row.group(1) is None:
            # A verb's usage row starts a table of its own; an option given before any verb goes under "".
            words = row.group(2).split()
            verb = words[1] if words[0] in ("just", inverse.PROGRAM) else ""
            if verb:
                tables[verb] = []
        tables.setdefault(verb, []).append((row.group(2), row.group(3)))
    return tables


def _row_key(name: str, code: str) -> str:
    """The cutover's map's name of the word an option's or words' row is for: deploy --tags, deploy <name>."""
    if code.startswith("<"):
        return f"{name} {code}"
    long = re.search(r"--([a-z][a-z-]*)", code)
    assert long is not None, code
    return f"{name} --{long.group(1)}"


def _today_s_rows() -> dict[str, list[tuple[str, str]]]:
    """Today's page's rows, of the recipes the contract holds, but help, which the page lists no row for."""
    rows = _rows(PAGE.read_text())
    assert set(RECIPES) - {"help"} <= set(rows), sorted(rows)
    return {name: rows[name] for name in RECIPES if name != "help"}


def test_help_of_a_verb_shows_every_row_today_s_page_shows_for_it() -> None:
    for name, ((usage, doc), *rows) in _today_s_rows().items():
        ours = helps.of(name)
        assert _plain(_aw(doc)) in ours, name
        if not _new_words(name):
            assert f"{helps.USAGE} {_aw(usage)}\n" in ours, name
        for code, text in rows:
            key = _row_key(name, code)
            if _changed(key):
                continue
            [line] = [line for line in ours.splitlines() if line.lstrip().startswith(f"{code} ")]
            assert _unformed(line).endswith(f"  {_plain(_aw(text))}"), key


def test_help_of_deploy_shows_the_eleven_rows_of_today_s_page() -> None:
    # The card's Done when, read on a lane as ./aw help deploy: the usage and doc, nine options and the words.
    (usage, doc), *rows = _today_s_rows()["deploy"]
    assert len(rows) + 1 == 11
    ours = helps.of("deploy")
    assert f"{helps.USAGE} {_aw(usage)}" in ours and _plain(doc) in ours
    assert all(code in ours and _plain(_aw(text)) in ours for code, text in rows)


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
    assert checks.no_option("", "--bogus").endswith(f"({inverse.line('help', {}, refused=False)} lists them)")


def _today_s_closing() -> list[str]:
    """The closing paragraph of today's help recipe, from its echo lines in the justfile, sentence by sentence."""
    justfile = (REPO / "justfile").read_text()
    recipe = justfile[justfile.index("\nhelp *RECIPE:") :]
    recipe = recipe[: recipe.index("\n\n")]
    text = " ".join(re.findall(r'^    echo "(.*)"$', recipe, re.M))
    return re.split(r"(?<=\.) (?=[A-Z])", text)


def test_aw_help_ends_in_today_s_closing_paragraph_with_aw_and_v_and_set_for_ansible_args() -> None:
    today = _today_s_closing()
    assert len(today) == 3 and "ANSIBLE_ARGS" in today[1], today
    lines = helps.listing().splitlines()
    closing = lines[len(lines) - len(helps.closing()) :]
    assert closing == helps.closing() and all(len(line) <= helps.WIDTH for line in closing)
    ours = re.split(r"(?<=\.) (?=[A-Z])", " ".join(closing))
    assert len(ours) == 3, ours
    assert ours[0] == _aw(today[0]) and ours[2] == _aw(today[2])
    assert "ANSIBLE_ARGS" not in ours[1] and "just" not in " ".join(ours)
    assert ours[1].startswith(today[1].split(",")[0] + ":"), ours[1]
    for option in (VERBOSE, SET):
        having = [name for name, verb in VERBS.items() if option in verb.options]
        named = ", ".join(having[:-1]) + " and " + having[-1]
        assert f"{helps.shown(option)} " in ours[1] and f" in {named}" in ours[1], (option.field, ours[1])
    assert "LOG_DIR=<dir>" in ours[1]
    # Its example is the printer's, and runs: deploy alpha, saying the most of the system layer, as today's did.
    example = inverse.line(*helps.EXAMPLE, host_flag="", refused=False)
    assert ours[1].endswith(f": {example}.")
    reading = parse.read(shlex.split(example)[1:])
    assert (reading.verb, reading.values) == ("deploy", {"names": ["alpha"], "verbosity": 3})
    hosts = inverse.line("host", {}, refused=False)
    assert f"({hosts} lists them)" in ours[2]
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


def test_a_second_word_is_refused_in_today_s_sentence(home) -> None:
    case = CASES["help.1-1"]
    result = run_aw(*case["words"], home=home)
    assert (result.returncode, result.stdout, result.stderr) == (case["status"], "", _aw(case["sentence"]) + "\n")


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
            f"| {reference.INDENT_CELL}`{helps.shown(option)}` | {reference.cell(said(option.help or ''))} |"
            for option in checks.options_of(verb)
            if option.until is None
        ]
        if verb.words is not None and verb.words.name != helps.REFUSED_WORDS:
            under.append(f"| {reference.INDENT_CELL}`<{verb.words.name}>` | {reference.cell(said(verb.words.help))} |")
        at = rows.index(first) + 1
        assert rows[at : at + len(under)] == under, name
        assert not rows[at + len(under)].startswith(f"| {reference.INDENT_CELL}"), name
    before = [f"| `{helps.shown(option)}` | {reference.cell(said(option.help))} |" for option in BEFORE if option.help]
    at = rows.index(f"### {reference.BEFORE_ANY}")
    assert rows[at + 1 :] == ["", *reference.BEFORE_HEAD, *before]
    groups = [*dict.fromkeys((verb.group or reference.OTHER) for name, verb in VERBS.items() if name != "help")]
    headings = [f"### {group.capitalize()}" for group in [*groups, reference.BEFORE_ANY]]
    assert [row for row in rows if row.startswith("### ")] == headings


def test_the_reference_holds_today_s_rows_with_aw_where_they_said_just() -> None:
    ours = _rows(reference.commands())
    for name, ((usage, doc), *rows) in _today_s_rows().items():
        (our_usage, our_doc), *our_rows = ours[name]
        assert our_doc == _aw(doc), name
        if not _new_words(name):
            assert our_usage == _aw(usage), name
        for code, text in rows:
            if not _changed(_row_key(name, code)):
                assert (code, _aw(text)) in our_rows, (name, code)


def test_the_reference_escapes_a_cell_as_tools_docs_does() -> None:
    docs = harness.load_module("tools/docs.py", "docs_generator", register=False)
    assert (reference.CODE.pattern, reference.INDENT_CELL) == (docs.CODE.pattern, docs.INDENT_CELL)
    texts = [verb.doc for verb in VERBS.values()] + [verb.words.help for verb in VERBS.values() if verb.words]
    texts += [option.help or "" for verb in VERBS.values() for option in (*verb.options, *BEFORE)]
    texts += ["<name> & a | b", "`<code> | x` <y>", "<code>a|b</code> [x](<y>) `` ` ``", " padded "]
    for text in texts:
        assert (reference.escape(text), reference.cell(text)) == (docs.escape(text), docs.cell(text)), text
