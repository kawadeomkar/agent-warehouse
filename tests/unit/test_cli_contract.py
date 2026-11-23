"""Today's command line is a contract, tests/unit/cli_contract.json, held here to the justfile and tools/check.

Every later parity test reads the contract and never the justfile, so until the cutover a change to today's command
line - a recipe, an option, its pattern or its help, a setting, a refusal's sentence or the line it is on - changes the
contract in the same commit, made again by tests/unit/cli_contract.py (its docstring says how). Each runnable case is
run again against today's task runner with stand-in tools, as test_justfile.py runs a recipe, and must give the status
and the sentence the contract holds; each example must be one test_commands.py's check accepts.

This tests today's justfile, not the new engine: it runs in the unit suite alone, and goes with today's engine.
"""

from __future__ import annotations

import functools
import json
import re

import cli_contract as contract
import pytest
from harness import just_settings, public_recipes
from test_commands import _matches, _problem

AGAIN = (
    "make it again in the same commit: tools/env-run python tests/unit/cli_contract.py --write "
    "(--measure, where the tests run, for a case's status or sentence)"
)
# The lines of the justfile that print error:, each a rule's: one more fails here, naming it.
ERROR_LINES = 52


@functools.cache
def _held() -> dict:
    return json.loads(contract.CONTRACT.read_text())


@functools.cache
def _made() -> dict:
    """Every part made again from this tree, the cases' status and sentence as the contract holds them."""
    return contract.build(_held())


# ----------------------------------------------------------------------------------------------------- the file ----


def test_the_file_is_its_six_parts_in_order_as_the_builder_writes_it() -> None:
    assert list(_held()) == list(contract.PARTS)
    assert contract.CONTRACT.read_text() == contract.text(_held()), AGAIN


def test_a_change_to_today_s_command_line_changes_the_contract_in_the_same_commit() -> None:
    # Everything but the examples, which are where a command is told, not the command line itself.
    for part in ("recipes", "settings", "rules", "cases", "exits"):
        assert _made()[part] == _held()[part], f"the contract's {part} are not the justfile's: {AGAIN}"


# -------------------------------------------------------------------------------------------------- the recipes ----


def test_the_recipes_are_the_justfile_s() -> None:
    said = contract.differences(_held()["recipes"], _made()["recipes"])
    assert said == [], "\n".join([*said, AGAIN])


def test_thirteen_recipes_and_sixty_parameters() -> None:
    # 69 [arg( attributes, less the nine of the developer's recipes, which stay the task runner's own.
    held = _held()["recipes"]
    assert [recipe["name"] for recipe in held] == [
        "help",
        "init",
        "install",
        "vault",
        "deploy",
        "code",
        "login",
        "status",
        "settings",
        "destroy",
        "host",
        "logs",
        "mcp",
    ]
    assert sum(len(recipe["options"]) for recipe in held) == 60
    names = {recipe["name"] for recipe in held}
    developer = {name: recipe for name, recipe in public_recipes().items() if name not in names}
    assert set(developer) == {"lint", "test", "docs", "vagrant", "clean"}
    assert sum(len(recipe["parameters"]) for recipe in developer.values()) == 9
    assert len(re.findall(r"^\[arg\(", contract.JUSTFILE.read_text(), re.M)) == 69


def test_a_help_string_changed_in_the_justfile_is_named_by_its_recipe_and_option(tmp_path) -> None:
    copy = tmp_path / "justfile"
    text = contract.JUSTFILE.read_text()
    help_text = "Configure only these roles, comma-separated"
    assert text.count(help_text) == 1
    copy.write_text(text.replace(help_text, "Configure only some roles"))
    said = contract.differences(_held()["recipes"], contract.recipes(copy))
    assert len(said) == 1 and said[0].startswith("deploy --tags: its help is "), said


def test_words_and_plural_follow_the_mcp_server_s_rule() -> None:
    from claude_vm.mcp import recipes

    for held in _held()["recipes"]:
        parameters = public_recipes()[held["name"]]["parameters"]
        for option, parameter in zip(held["options"], parameters, strict=True):
            assert option["words"] == (parameter["kind"] in ("star", "plus")), (held["name"], option["name"])
            assert option["plural"] == recipes.words(parameter), (held["name"], option["name"])


# ------------------------------------------------------------------------------------------------- the settings ----


def test_every_setting_read_with_env_is_held_and_the_mcp_server_passes_each_at_its_default() -> None:
    from claude_vm.mcp import recipes

    read_with_env = {
        name
        for name, assignment in contract.dump()[0]["assignments"].items()
        if isinstance(assignment["value"], list) and assignment["value"][:2] == ["call", "env"]
    }
    held = _held()["settings"]
    with_env = {item["name"] for part in ("promised", "unpromised") for item in held[part]}
    assert with_env == read_with_env == set(recipes.SETTINGS)
    # The two read from the command line alone are settings a person types too.
    assert {item["name"] for item in held["command_line"]} <= just_settings()


# ---------------------------------------------------------------------------------------------------- the rules ----


def test_each_rule_s_sentence_is_as_written_at_its_line() -> None:
    for rule in _held()["rules"]:
        if rule["by"] == "just":
            assert rule["line"] is None and rule["sentence"], rule
            continue
        line = (contract.REPO / rule["by"]).read_text().splitlines()[rule["line"] - 1]
        assert rule["sentence"] in line, (rule["code"], rule["line"], AGAIN)


def test_every_error_line_of_the_justfile_is_a_rule_s() -> None:
    printed = [number for number, line in enumerate(contract.JUSTFILE.read_text().splitlines(), 1) if "error:" in line]
    named = {rule["line"] for rule in _held()["rules"] if rule["by"] == "justfile"}
    assert [number for number in printed if number not in named] == [], f"a rule and a case for each: {AGAIN}"
    assert len(printed) == ERROR_LINES, f"the justfile has {len(printed)} lines that print error:: {AGAIN}"


def test_codes_are_one_each_and_every_rule_has_a_case() -> None:
    codes = [rule["code"] for rule in _held()["rules"]]
    assert len(codes) == len(set(codes))
    cases = [item["code"] for item in _held()["cases"]]
    assert len(cases) == len(set(cases))
    for code in codes:
        assert any(contract._rule_of(item) == code for item in cases), code
    for code in cases:
        assert contract._rule_of(code) in codes or code.startswith("exit."), code
    # deploy.1 is the refusal of a stage word with no terminal to ask, as the card that made the contract has it.
    assert next(rule for rule in _held()["rules"] if rule["code"] == "deploy.1")["line"] == 570


# ---------------------------------------------------------------------------------------------------- the cases ----


@pytest.mark.parametrize("code", [item["code"] for item in contract.CASES])
def test_each_case_gives_the_status_and_the_sentence_the_contract_holds(tmp_path, code) -> None:
    item = next(item for item in contract.CASES if item["code"] == code)
    held = next(item for item in _held()["cases"] if item["code"] == code)
    status, sentence, output = contract.measure(item, tmp_path)
    assert (status, sentence) == (held["status"], held["sentence"]), output
    if not code.startswith("exit."):
        assert status != 0 and sentence, output


def test_a_refusal_exits_1_and_a_ctrl_c_as_the_contract_says() -> None:
    cases = {item["code"]: item for item in _held()["cases"]}
    refused = {item["status"] for code, item in cases.items() if not code.startswith("exit.")}
    assert refused == {1}, refused
    assert [cases[f"exit.report-{name}-1"]["status"] for name in ("ok", "problem", "unread", "failed")] == [0, 1, 2, 3]
    (ctrl_c,) = [item for item in _held()["exits"] if "Ctrl-C" in item["meaning"]]
    assert ctrl_c["status"] == cases["exit.ctrl-c-1"]["status"] and isinstance(ctrl_c["status"], int)


# ------------------------------------------------------------------------------------------------- the examples ----


def test_each_example_is_one_today_s_justfile_accepts() -> None:
    recipes = public_recipes()
    problems = []
    for example in _held()["examples"]:
        before = re.match(r"((?:[A-Z][A-Z_]*=(?:'[^']*'|\S+) )*)just ", example["line"])
        assert before, example
        unknown = set(re.findall(r"([A-Z][A-Z_]*)=(?:'[^']*'|\S+)", before.group(1))) - just_settings()
        found = list(_matches(example["line"]))
        if unknown or not found:
            problems.append(f"{example}: {unknown or 'no command'}")
            continue
        for _, _, settings, recipe, words in found:
            problem = _problem(recipes[recipe], recipes, words) if recipe in recipes else f"no recipe {recipe}"
            if problem or set(settings) - just_settings():
                problems.append(f"{example}: {problem or settings}")
    assert problems == [], "\n".join(problems)


def test_the_examples_hold_every_one_of_the_command_reference() -> None:
    lines = {example["line"] for example in _held()["examples"]}
    for line in (
        "just deploy alpha --only configure",
        "just test integration",
        "just help deploy",
        "just deploy configure",
        "ANSIBLE_ARGS='-f 30' just deploy --only provision",
        "just deploy alpha --tags claude_code",
        "just test --role common,claude_code",
        "just test unit --shard 2/3",
        "just test molecule --jobs 4",
    ):
        assert line in lines, line
    assert len(lines) > 200
