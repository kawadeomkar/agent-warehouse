"""The one table of the command line (lib/claude_vm/schema/words.py; docs/architecture/engine.md §16.2).

Every verb has its handler or the card that builds it, the locks a run of it takes, whether a run of it is a job, and
the tools of the agents' server that run it or why none does; every option a kind, a pattern of schema/patterns.py
unless it takes no value, and a fate for an agent's tool, by tool where two tools run its verb. Today's words keep
today's fates (lib/claude_vm/mcp/recipes.py), held until the cutover. No option's value is a secret, no sentence says
just, each meaning of an exit status has one number and each number one meaning, and the module imports the standard
library alone.
"""

from __future__ import annotations

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
# The verbs whose run is a job, with its own record, text log and locks.
JOBS = {"deploy", "login", "code", "destroy", "release", "init", "install", "control"}
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
    "control": "none",
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


def test_the_verbs_whose_run_is_a_job_are_the_eight_ruled() -> None:
    assert {name for name, verb in VERBS.items() if verb.changes} == JOBS


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
            assert VERBS[name].tools == why, name
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


def test_fate_reads_one_tool_s_fate_where_two_tools_run_a_verb() -> None:
    [value] = [option for option in VERBS["settings"].options if option.long == "value"]
    assert fate(value.agent, "settings") == words.refused(words.VALUE_LEFT_OUT)
    assert fate(value.agent, "settings_set") == PROPERTY
    assert fate(PROPERTY, "deploy") == PROPERTY


def test_no_option_takes_a_secret_as_its_value() -> None:
    # A secret reaches a verb from a file it names or from the terminal, hidden, never on the command line: so nothing
    # the printer of a command writes from the table need be masked.
    speaks = re.compile(r"secret|password|passphrase|key|token")
    fields = {option.field for verb in VERBS.values() for option in verb.options + BEFORE if option.kind != "flag"}
    for field in fields:
        if speaks.search(field):
            assert field.endswith("_file") or field in NOT_SECRETS, field
    assert set(NOT_SECRETS) <= fields


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


def test_each_exit_status_has_one_meaning_and_each_meaning_one_status() -> None:
    for statuses in (EXITS, STATUS_EXITS):
        assert len(set(statuses.values())) == len(statuses)
        assert all(isinstance(status, int) and 0 <= status <= 255 for status in statuses.values())
    assert (EXITS["done"], EXITS["refused"], EXITS["cancelled"]) == (0, 1, 130)
    assert list(STATUS_EXITS.values()) == [0, 1, 2, 3, 130]


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
