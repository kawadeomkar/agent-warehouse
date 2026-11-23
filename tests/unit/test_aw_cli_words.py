"""The one table of the command line (lib/claude_vm/schema/words.py; docs/architecture/engine.md §16.2).

Every verb has its handler or the card that builds it, the locks a run of it takes, whether a run of it is a job, and
the tools of the agents' server that run it or why none does; every option a kind, a pattern of schema/patterns.py
unless it takes no value, and a fate for an agent's tool, by tool where two tools run its verb. Today's words keep
today's fates (lib/claude_vm/mcp/recipes.py), held until the cutover. No verb of the add-on is a verb of the base's
table. Every option and word of a verb a tool runs that is new to the table is a property of the tool's schema, but
those ruled otherwise. No option's value is a secret, but the two that take free text a person may type one into, which
the run layer refuses and never says back; no sentence says just, a reason why an agent's tool does not take a word
among them; each meaning of an exit status has one number and each number one meaning but status's 3, which today
gives a read that failed and one a person cancelled; and the module imports the standard library alone.
"""

from __future__ import annotations

import ast
import json
import keyword
import re
import sys
from pathlib import Path

import pytest
from aw_kit import imports_of

from claude_vm.mcp import recipes
from claude_vm.schema import words
from claude_vm.schema.patterns import PATTERNS
from claude_vm.schema.words import (
    BEFORE,
    DETACH,
    DETACH_AGENT,
    EXITS,
    FATES,
    KINDS,
    LOCKS,
    PROPERTY,
    STATUS_EXITS,
    VERBS,
    Fate,
    Option,
    fate,
)

MODULE = "claude_vm.schema.words"
CONTRACT = json.loads((Path(__file__).resolve().parent / "cli_contract.json").read_text())
# Today's status, whose end on a Ctrl-C the table keeps.
STATUS_ENGINE = Path(__file__).resolve().parents[2] / "lib" / "claude_vm" / "status" / "engine.py"
# The verbs whose run is a job, with its own record, text log and locks.
JOBS = {"deploy", "login", "code", "destroy", "release", "init", "install"}
# The add-on's verbs, which the base's table never has, and why: the add-on adds each row when it is built.
ADD_ON = {
    "control": "the owner's answer 3 of 2026-11-20 (the plan's log): control is the add-on's, out of the base's table; "
    "the add-on's card that builds it (L18) adds its row, with the fleet's lock",
}
# The one number of a mapping of exit statuses that has two meanings, and why: today's status exits 3 for a read that
# failed and for one a person cancelled (lib/claude_vm/status/engine.py's _end_for_an_interrupt, held by
# tests/unit/test_status_live.py), and a script may read it. The owner's answer 2 of 2026-11-20 keeps it.
SHARED = {("STATUS_EXITS", 3): {"failed", "cancelled"}}
# The locks a run of each verb takes, as the locks are ruled: one machine's by its name, the fleet's for what changes
# the whole fleet, and for three verbs a kind worked out from the words given.
LOCKS_OF = {
    "help": "none",
    "init": "fleet",
    "install": "fleet",
    "vault": "words",
    "deploy": "words",
    "code": "machines",
    "login": "machines",
    "status": "none",
    "settings": "none",
    "destroy": "machines",
    "release": "machines",
    "host": "words",
    "logs": "none",
    "cancel": "none",
    "mcp": "none",
}
# The verbs that read the fleet's settings to do their work, which take --set; and those of them that run the system
# layer, which take -v.
SET = {"deploy", "login", "code", "destroy", "release", "init", "status"}
VERBOSE = SET - {"status"}
# Options whose name speaks of a secret, a key or a token and whose value is none: a file's path, read by the verb, ends
# its name in _file; these are names and fingerprints.
NOT_SECRETS = {
    "token_user": "the API token's user, a name",
    "token_id": "the API token's ID, a name",
    "remove_key": "a public key's fingerprint",
}
# The options whose value is free text, in which a person may type a secret, each by its verb and the name the handler
# receives it by, and the rule that holds it: the run layer types the value and refuses a secret's name or value there,
# never saying it back (D5's --set, D10's settings --value), and the printer of a command never prints one a person
# typed in a refused run (H9).
SECRET_TEXT = {
    **{
        (verb, "set"): "the run layer refuses a secret's name or value for one run, and never says it back (D5)"
        for verb in ("init", "deploy", "code", "login", "status", "destroy", "release")
    },
    ("settings", "value"): "settings --value refuses a secret's name or value, and never says it back, as today (D10)",
}
# The other options whose value is free text, and what it is: a thing named, never a secret.
NAMED_TEXT = {
    ("init", "migrate"): "a 2.x checkout's folder",
    ("mcp", "browser"): "the program that opens the sign-in pages",
}
# Patterns that take a line of any text.
FREE_PATTERNS = {"text", "assignment"}
CARD = re.compile(r"[A-Z][0-9]+[a-z]?")
HANDLER = re.compile(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)*:[a-z_][a-z0-9_]*")
RULE = re.compile(r"([a-z]+|check\.[a-z]+)\.[0-9]+")
TOOL = re.compile(r"[a-z][a-z_]*")


def _spelled(option: Option) -> str:
    return f"--{option.long}" if option.long else f"-{option.short}"


def _today(recipe: str) -> set[str]:
    """The names today's agents' tools give the options and the words of one of today's recipes (recipes.prop)."""
    [found] = [one for one in CONTRACT["recipes"] if one["name"] == recipe]
    return {recipes.prop(parameter) for parameter in found["options"]}


def _received(verb: words.Verb) -> dict[str, Option | words.Words]:
    """Each option and the verb's words, by the name the handler receives it by."""
    received: dict[str, Option | words.Words] = {option.field: option for option in verb.options}
    if verb.words is not None:
        received[verb.words.field] = verb.words
    return received


def _named(verb: words.Verb, name: str) -> Option | words.Words:
    """The option, or the verb's words, that today's agents' tools name so (recipes.prop): an option by its long."""
    if verb.words is not None and verb.words.field == name:
        return verb.words
    [option] = [option for option in verb.options if option.long and recipes.prop({"long": option.long}) == name]
    return option


@pytest.mark.parametrize("verb", list(VERBS.values()), ids=list(VERBS))
def test_no_two_options_of_a_verb_share_a_spelling_or_a_name_and_none_is_detach(verb: words.Verb) -> None:
    longs = [option.long for option in verb.options if option.long]
    shorts = [option.short for option in verb.options if option.short]
    assert len(longs) == len(set(longs)) and len(shorts) == len(set(shorts))
    assert "detach" not in longs
    assert len(_received(verb)) == len(verb.options) + (verb.words is not None)
    assert VERBS[verb.name] is verb


def test_the_words_before_a_verb_are_the_three_ruled_and_none_is_detach() -> None:
    assert [_spelled(option) for option in BEFORE] == ["--no-lock", "--attended", "--detached-run"]
    assert [option.help is None for option in BEFORE] == [False, False, True]


@pytest.mark.parametrize("verb", list(VERBS.values()), ids=list(VERBS))
def test_every_option_names_a_pattern_or_takes_no_value(verb: words.Verb) -> None:
    for option in verb.options + BEFORE:
        assert option.kind in KINDS, option
        assert option.long or option.short, option
        if option.kind in ("flag", "counted"):
            assert option.pattern is None and option.metavar is None, option
        else:
            assert option.pattern in PATTERNS and option.metavar, option
    if verb.words is not None:
        assert verb.words.pattern is None or verb.words.pattern in PATTERNS


@pytest.mark.parametrize("verb", list(VERBS.values()), ids=list(VERBS))
def test_every_option_is_received_by_its_long_name_as_today_s_tools_name_it(verb: words.Verb) -> None:
    for option in verb.options + BEFORE:
        if option.long is None:
            assert (option.short, option.field) == ("v", "verbosity"), option
            continue
        name = recipes.prop({"long": option.long})
        # A word Python keeps for itself (host's --for) gets an _ after it, as a keyword argument must.
        assert option.field == (f"{name}_" if keyword.iskeyword(name) else name), option


@pytest.mark.parametrize("verb", list(VERBS.values()), ids=list(VERBS))
def test_every_option_and_word_has_a_fate_for_each_tool_that_runs_its_verb(verb: words.Verb) -> None:
    tools = verb.tools if isinstance(verb.tools, tuple) else ()
    for item in _received(verb).values():
        if len(tools) > 1:
            assert isinstance(item.agent, dict) and sorted(item.agent) == sorted(tools), item
            fates = list(item.agent.values())
        else:
            assert isinstance(item.agent, Fate), item
            fates = [item.agent]
        for one in fates:
            assert one.kind in FATES, item
            if one.kind == "property":
                assert one == PROPERTY, item
            elif one.kind == "fixed":
                assert one.value is not None or one.why, item
            else:
                assert one.value is None and one.why, item


@pytest.mark.parametrize("verb", [verb for verb in VERBS.values() if isinstance(verb.tools, str)], ids=lambda v: v.name)
def test_a_verb_no_tool_runs_refuses_its_every_word_with_why(verb: words.Verb) -> None:
    assert verb.tools.strip()
    assert [item for item in _received(verb).values() if item.agent != words.refused(verb.tools)] == []


def test_every_verb_has_a_handler_or_its_card_its_locks_and_its_tools() -> None:
    for name, verb in VERBS.items():
        assert verb.group == (None if name == "help" else "use"), name
        assert verb.handler is None or HANDLER.fullmatch(verb.handler), name
        assert CARD.fullmatch(verb.card), name
        assert verb.locks in LOCKS, name
        assert isinstance(verb.changes, bool), name
        if isinstance(verb.tools, tuple):
            assert verb.tools and all(TOOL.fullmatch(tool) for tool in verb.tools), name
        else:
            assert isinstance(verb.tools, str) and verb.tools.strip(), name
        assert all(RULE.fullmatch(code) for code in verb.rules) and len(set(verb.rules)) == len(verb.rules), name
    tools = [tool for verb in VERBS.values() if isinstance(verb.tools, tuple) for tool in verb.tools]
    assert len(tools) == len(set(tools)), "a tool runs one verb"


def test_the_verbs_whose_run_is_a_job_are_the_seven_ruled() -> None:
    assert {name for name, verb in VERBS.items() if verb.changes} == JOBS


def test_no_verb_of_the_add_on_is_a_verb_of_the_base_s_table() -> None:
    assert all(why.strip() for why in ADD_ON.values())
    assert sorted(set(ADD_ON) & set(VERBS)) == []
    # Nor any verb's card the add-on's own, a row that would wait in the base for the add-on: L is its workstream.
    assert [name for name, verb in VERBS.items() if verb.card.startswith("L")] == []


def test_each_verb_takes_the_locks_ruled() -> None:
    assert {name: verb.locks for name, verb in VERBS.items()} == LOCKS_OF


def test_set_is_on_the_verbs_that_read_the_settings_and_v_on_those_that_run_the_system_layer() -> None:
    assert {name for name, verb in VERBS.items() if any(option.long == "set" for option in verb.options)} == SET
    assert {name for name, verb in VERBS.items() if any(option.short == "v" for option in verb.options)} == VERBOSE


def test_the_verbs_and_tools_are_today_s_until_the_cutover() -> None:
    for tool, recipe in recipes.TOOLS.items():
        assert tool in VERBS[recipe.recipe].tools, tool
    for name, why in recipes.NO_TOOL.items():
        if name in VERBS:
            # Today's reason, with aw where it said just (the owner's word 1).
            assert VERBS[name].tools == re.sub(r"\bjust\b", "aw", why), name
        else:
            assert name in ("lint", "test", "docs", "vagrant", "clean"), name
    every = {recipe["name"] for recipe in CONTRACT["recipes"]}
    assert every <= set(VERBS)
    assert {tool for verb in VERBS.values() if isinstance(verb.tools, tuple) for tool in verb.tools} == set(
        recipes.TOOLS
    ) | {"cancel", "release"}


@pytest.mark.parametrize("tool", sorted(recipes.TOOLS))
def test_today_s_words_keep_today_s_fates_until_the_cutover(tool: str) -> None:
    recipe = recipes.TOOLS[tool]
    verb = VERBS[recipe.recipe]
    for name in _today(recipe.recipe):
        given = fate(_named(verb, name).agent, tool)
        if name in recipe.fixed:
            assert given == words.fixed(recipe.fixed[name]), (tool, name)
        elif (tool, name) in recipes.LEFT_OUT:
            # Left out of the schema: refused, or, as destroy's --confirm, given by the server from its dialog.
            assert given.kind in ("refused", "fixed") and given.why == recipes.LEFT_OUT[tool, name], (tool, name)
        else:
            assert given == PROPERTY, (tool, name)
    assert {name for one, name in recipes.LEFT_OUT if one == tool} | set(recipe.fixed) <= _today(recipe.recipe)


def test_the_new_words_fates_are_those_ruled() -> None:
    refused = {
        "set": words.SET_REFUSED,
        "adopt": words.ADOPT_REFUSED,
        "remove-key": words.REMOVE_KEY_REFUSED,
        "take-over": words.TAKE_OVER_REFUSED,
        "migrate": words.MIGRATE_REFUSED,
        "finish": words.MIGRATE_REFUSED,
        "print-commands": words.PRINT_COMMANDS_REFUSED,
        "version": words.INSTALLED_REFUSED,
        "newer": words.INSTALLED_REFUSED,
        "rollback": words.INSTALLED_REFUSED,
        "without": words.INSTALLED_REFUSED,
    }
    for name, verb in VERBS.items():
        if not isinstance(verb.tools, tuple):
            continue
        for option in verb.options:
            for tool in verb.tools:
                given = fate(option.agent, tool)
                if option.long in refused:
                    assert given == words.refused(refused[option.long]), (name, option.long)
                if option.short == "v":
                    assert given == words.refused(words.VERBOSE_REFUSED), name
                if option.long == "confirm":
                    assert given == words.fixed(None, words.CONFIRMED), name
    assert [fate(option.agent, "any") for option in BEFORE] == [
        words.refused(words.NO_LOCK_REFUSED),
        words.ATTENDED,
        words.refused(words.DETACHED_RUN_REFUSED),
    ]
    assert DETACH_AGENT == words.fixed(True, DETACH_AGENT.why) and DETACH_AGENT.why
    assert VERBS["release"].words.agent == PROPERTY and VERBS["cancel"].words.agent == PROPERTY
    # Every other option and word of a verb a tool runs is a property of each tool's schema: today's words keep today's
    # fates (the test above), and a word new to the table is a property but where ruled otherwise here, so none is
    # refused or given by the server by a slip. The agents' server (I1) reads this column as the whole truth.
    today = {recipe["name"]: {recipes.prop(one) for one in recipe["options"]} for recipe in CONTRACT["recipes"]}
    for name, verb in VERBS.items():
        if not isinstance(verb.tools, tuple):
            continue
        for field, item in _received(verb).items():
            # Today's tools name an option by its long (recipes.prop), where the handler takes for_ for host's --for.
            named = recipes.prop({"long": item.long}) if isinstance(item, Option) and item.long else field
            if named in today.get(name, set()):
                continue
            if isinstance(item, Option) and (item.long in refused or item.long == "confirm" or item.short == "v"):
                continue
            assert [fate(item.agent, tool) for tool in verb.tools] == [PROPERTY] * len(verb.tools), (name, field)


def test_fate_reads_one_tool_s_fate_where_two_tools_run_a_verb() -> None:
    [value] = [option for option in VERBS["settings"].options if option.long == "value"]
    assert fate(value.agent, "settings") == words.refused(words.VALUE_LEFT_OUT)
    assert fate(value.agent, "settings_set") == PROPERTY
    assert fate(PROPERTY, "deploy") == PROPERTY


def test_no_option_takes_a_secret_as_its_value_but_the_two_of_free_text() -> None:
    # A secret reaches a verb from a file it names or from the terminal, hidden, never on the command line: so nothing
    # the printer of a command writes from the table need be masked, but the two values of free text a person may type
    # one into, --set and settings --value, which the run layer refuses and never says back.
    speaks = re.compile(r"secret|password|passphrase|key|token")
    fields = {option.field for verb in VERBS.values() for option in verb.options + BEFORE if option.kind != "flag"}
    for field in fields:
        if speaks.search(field):
            assert field.endswith("_file") or field in NOT_SECRETS, field
    assert set(NOT_SECRETS) <= fields
    free = {
        (name, option.field)
        for name, verb in VERBS.items()
        for option in verb.options
        if option.pattern in FREE_PATTERNS
    }
    assert free == set(SECRET_TEXT) | set(NAMED_TEXT), "a value of free text: say whether a secret may be typed there"
    assert set(SECRET_TEXT) & set(NAMED_TEXT) == set() and all((*SECRET_TEXT.values(), *NAMED_TEXT.values()))
    assert not any(option.pattern in FREE_PATTERNS for option in BEFORE)


def test_every_sentence_is_one_line_and_says_aw_never_just() -> None:
    sentences = [DETACH, *(option.help for option in BEFORE if option.help)]
    for verb in VERBS.values():
        sentences.append(verb.doc)
        sentences += [option.help for option in verb.options]
        if verb.words is not None:
            sentences.append(verb.words.help)
    for sentence in sentences:
        assert sentence and not re.search(r"[\x00-\x1f\x7f]", sentence), sentence
        assert not re.search(r"\bjust\b", sentence), sentence
        assert not sentence.endswith("."), sentence
    # The reasons an agent's tool is given for a word it does not take, or for a verb no tool runs, which I1 shows.
    reasons = [verb.tools for verb in VERBS.values() if isinstance(verb.tools, str)]
    for verb in VERBS.values():
        for item in [*verb.options, *([verb.words] if verb.words else [])]:
            agent = item.agent if isinstance(item.agent, dict) else {"": item.agent}
            reasons += [one.why for one in agent.values() if one.why]
    reasons += [one.why for option in BEFORE for one in [option.agent] if isinstance(one, Fate) and one.why]
    assert reasons and [reason for reason in reasons if re.search(r"\bjust\b", reason)] == []


def test_each_exit_status_has_one_meaning_and_each_meaning_one_status_but_the_one_pair_named() -> None:
    # A meaning is a key, so it has one number; a number has one meaning but where SHARED names the pair, with why.
    assert all(why for why in SHARED.values())
    shared = {}
    for name, statuses in (("EXITS", EXITS), ("STATUS_EXITS", STATUS_EXITS)):
        assert all(isinstance(status, int) and 0 <= status <= 255 for status in statuses.values())
        for status in set(statuses.values()):
            meanings = {meaning for meaning, given in statuses.items() if given == status}
            if len(meanings) > 1:
                shared[name, status] = meanings
    assert shared == SHARED
    assert (EXITS["done"], EXITS["refused"], EXITS["cancelled"]) == (0, 1, 130)
    assert STATUS_EXITS == {"nothing_wrong": 0, "problem": 1, "unread": 2, "failed": 3, "cancelled": 3}


def test_a_cancelled_status_exits_as_today_s_does() -> None:
    # Today's status ends a read a person cancelled in _end_for_an_interrupt, by os._exit
    # (tests/unit/test_status_live.py holds it to 3 at a terminal): the table's cancelled status is that number.
    tree = ast.parse(STATUS_ENGINE.read_text())
    [end] = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_end_for_an_interrupt"]
    calls = [node for node in ast.walk(end) if isinstance(node, ast.Call) and ast.unparse(node.func) == "os._exit"]
    assert [ast.literal_eval(call.args[0]) for call in calls] == [STATUS_EXITS["cancelled"]]


def test_the_table_imports_nothing_outside_the_standard_library(home) -> None:
    # What importing the package alone loads is taken away: what is left is what the module itself brings.
    added = imports_of(module=MODULE, home=home) - imports_of(module="claude_vm", home=home)
    assert MODULE in added, sorted(added)
    tops = {name.split(".")[0] for name in added}
    assert sorted(tops - set(sys.stdlib_module_names) - {"claude_vm"}) == []
    assert (
        sorted(name for name in added if name.startswith("claude_vm.") and not name.startswith("claude_vm.schema"))
        == []
    )
