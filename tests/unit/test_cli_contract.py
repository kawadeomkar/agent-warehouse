"""Today's command line is a contract, tests/unit/cli_contract.json, held here to the justfile and tools/check.

Every later parity test reads the contract and never the justfile, so until the cutover a change to today's command
line - a recipe, an option, its pattern or its help, a setting, a refusal's sentence or the line it is on - changes the
contract in the same commit, made again by tests/unit/cli_contract.py (its docstring says how). Each runnable case is
run again against today's task runner with stand-in tools, as test_justfile.py runs a recipe, and must give the status
and the sentence the contract holds; each example must be one test_commands.py's check accepts, a setting given empty
before the recipe among them, and the examples are kept as held: none is dropped, and one is added only by --scan.
Every line of the use recipes and the six word checks that exits with a status other than 0 is a rule's, or named as
none. One Ctrl-C while a task runs, outside a prompt, exits as ansible-playbook does, which a stand-in measures: the
stand-in is held to ansible-core's own. At one of the playbook's prompts one Ctrl-C asks Continue or Abort, and C goes
on while A ends the run with 2: a real ansible-playbook measures it at a pseudo-terminal, and Ansible's words there are
held to ansible-core's own.

This tests today's justfile, not the new engine: it runs in the unit suite alone, and goes with today's engine.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
import re
import signal
import subprocess
import sys
import time

import cli_contract as contract
import pytest
from harness import _default_signals, just_settings, public_recipes
from test_commands import _problem

AGAIN = (
    "make it again in the same commit: tools/env-run python tests/unit/cli_contract.py --write "
    "(--measure, where the tests run, for a case's status or sentence)"
)
# The lines of the justfile that print error:, each a rule's: one more fails here, naming it.
ERROR_LINES = 52
# The examples the contract was first written with (H1, at 16b32074): how many, and the SHA-256 of their lines, one to a
# line, in their order. They stay first and as they are: --write keeps every example held, and only --scan adds a line,
# after them.
FIRST_EXAMPLES = (1175, "6b9369438c625e2b5a4b0ce511e98ae10b5ed82dd6cec04767e90ffeaa34ee3f")
# What a setting's case saw, for RUN_LOCK= and without it: today's run takes no lock with it, and is refused without.
NO_LOCK_TAKEN = "the playbook ran, and the lock another run holds is as it was"
LOCK_REFUSED = "the playbook did not run, and the lock another run holds is as it was"


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
    # The examples too, which --write keeps as held.
    for part in contract.PARTS:
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


def test_each_option_s_default_is_held_and_a_changed_one_is_named(tmp_path) -> None:
    # Every option of today's defaults to '' (not given), and its words to none.
    for recipe in _held()["recipes"]:
        for option in recipe["options"]:
            assert option["default"] == (None if option["words"] else ""), (recipe["name"], option["name"])
    copy = tmp_path / "justfile"
    text = contract.JUSTFILE.read_text()
    assert text.count("\nlogin ON='' SIGN_IN='' *NAMES:\n") == 1
    copy.write_text(text.replace("\nlogin ON='' SIGN_IN='' *NAMES:\n", "\nlogin ON='' SIGN_IN='claude' *NAMES:\n"))
    said = contract.differences(_held()["recipes"], contract.recipes(copy))
    assert said == ["login --only: its default is 'claude' in the justfile, '' in the contract"], said


def test_words_and_plural_follow_the_mcp_server_s_rule() -> None:
    from claude_vm.mcp import recipes

    for held in _held()["recipes"]:
        parameters = public_recipes()[held["name"]]["parameters"]
        names = [parameter["name"] for parameter in parameters]
        assert [option["name"] for option in held["options"]] == names, (held["name"], names, AGAIN)
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


def test_codes_are_one_each_never_given_again_and_every_rule_has_a_case() -> None:
    codes = [rule["code"] for rule in _held()["rules"]]
    assert len(codes) == len(set(codes))
    # A rule taken out keeps its code in RETIRED, and a recipe's rules are numbered from 1 with none missing: so a
    # code taken out without a word, which a later rule could be given, fails here.
    written = [code for code, *_ in contract.RULES] + contract.RETIRED
    assert len(written) == len(set(written)) and set(codes) == {code for code, *_ in contract.RULES}
    numbered: dict[str, set[int]] = {}
    for code in written:
        prefix, _, number = code.rpartition(".")
        if number.isdigit():
            numbered.setdefault(prefix, set()).add(int(number))
    assert {prefix: n for prefix, n in numbered.items() if n != set(range(1, max(n) + 1))} == {}
    cases = [item["code"] for item in _held()["cases"]]
    assert len(cases) == len(set(cases))
    for code in codes:
        assert any(contract._rule_of(item) == code for item in cases), code
    for code in cases:
        assert contract._rule_of(code) in codes or code.startswith(("exit.", "setting.")), code
    # deploy.1 is the refusal of a stage word with no terminal to ask, as the card that made the contract has it.
    deploy_1 = next(rule for rule in _held()["rules"] if rule["code"] == "deploy.1")
    assert "and there is no terminal to ask which was meant" in deploy_1["sentence"], deploy_1


def test_every_line_that_exits_non_zero_in_the_use_recipes_and_the_word_checks_is_a_rule_s() -> None:
    names = [recipe["name"] for recipe in _held()["recipes"]]
    for where in ("justfile", "tools/check"):
        unruled = contract.unruled_exits(where, names)
        assert unruled == [], (
            f"{where}: a refusal no rule holds: a rule and a case for each in cli_contract.py, {AGAIN}"
        )


@pytest.mark.parametrize(
    ("where", "after", "refusal"),
    [
        # A refusal in a recipe that prints no error:, as deploy.2, deploy.3 and host.6 print none.
        ("justfile", "\nlogs:\n", '    [ -d "$1" ] || { echo "No such folder: nothing was listed." >&2; exit 1; }\n'),
        # One that does, said on the line above its exit.
        (
            "justfile",
            "\nlogs:\n",
            '    if [ -n "${1:-}" ]; then\n      echo "error: logs takes no word" >&2\n      exit 1\n    fi\n',
        ),
        # A refusal in one of the word checks.
        (
            "tools/check",
            "\nnames)\n",
            '  [ "$2" != forbidden ] || { echo "error: forbidden is no VM name" >&2; exit 1; }\n',
        ),
    ],
)
def test_a_new_refusal_no_rule_holds_is_found(where, after, refusal) -> None:
    names = [recipe["name"] for recipe in _held()["recipes"]]
    text = (contract.REPO / where).read_text()
    assert text.count(after) == 1
    # Found alone: every other exit is still its rule's, though the lines below the new one moved.
    (found,) = contract.unruled_exits(where, names, text.replace(after, after + refusal))
    assert found[1].endswith("exit 1; }") or found[1] == "exit 1", found


# ---------------------------------------------------------------------------------------------------- the cases ----


@pytest.mark.parametrize("code", [item["code"] for item in contract.CASES])
def test_each_case_gives_the_status_and_the_sentence_the_contract_holds(tmp_path, code) -> None:
    item = next(item for item in contract.CASES if item["code"] == code)
    held = next(item for item in _held()["cases"] if item["code"] == code)
    status, sentence, output = contract.measure(item, tmp_path)
    assert (status, sentence) == (held["status"], held["sentence"]), output
    if not code.startswith(("exit.", "setting.")):
        assert status != 0 and sentence, output


def test_a_refusal_exits_1_and_the_report_as_the_contract_says() -> None:
    cases = {item["code"]: item for item in _held()["cases"]}
    refused = {item["status"] for code, item in cases.items() if not code.startswith(("exit.", "setting."))}
    assert refused == {1}, refused
    assert [cases[f"exit.report-{name}-1"]["status"] for name in ("ok", "problem", "unread", "failed")] == [0, 1, 2, 3]


def _exit_row(code: str) -> dict:
    (row,) = [row for row in _held()["exits"] if f"(case {code})" in row["meaning"]]
    return row


def test_one_ctrl_c_exits_99_while_a_task_runs_outside_a_prompt_and_130_before_the_playbook_starts() -> None:
    # 99 is ansible-playbook's: the recipe ends in it. aw's 130 for a cancelled run is a change against it.
    from cli_contract_changes import CHANGED

    cases = {item["code"]: item for item in _held()["cases"]}
    assert (cases["exit.ctrl-c-1"]["status"], cases["exit.ctrl-c-2"]["status"]) == (99, 130)
    for code in ("exit.ctrl-c-1", "exit.ctrl-c-2"):
        assert _exit_row(code)["status"] == cases[code]["status"], code
    # Only while a task runs: at one of the playbook's prompts the same key ends nothing (the test below).
    assert _exit_row("exit.ctrl-c-1")["meaning"].startswith("one Ctrl-C while a task runs, outside a prompt: ")
    assert "exit cancelled" in CHANGED


def test_at_one_of_the_playbook_s_prompts_one_ctrl_c_asks_and_c_goes_on_and_a_ends_the_run_with_2() -> None:
    # Ansible's question, in its own words; C goes on with the run, A fails the task. aw ends the run there, with a
    # cancelled run's 130: a behaviour it has otherwise on purpose (BEHAVIOURS).
    from cli_contract_changes import BEHAVIOURS

    cases = {item["code"]: item for item in _held()["cases"]}
    asked = f'the playbook asked "{contract.GO_ON}", and '
    went_on, aborted = cases["exit.ctrl-c-3"], cases["exit.ctrl-c-4"]
    assert (went_on["status"], went_on["sentence"]) == (0, asked + "said nothing of an abort")
    assert (aborted["status"], aborted["sentence"]) == (2, asked + f'said "{contract.ABORTED}"')
    for code in ("exit.ctrl-c-3", "exit.ctrl-c-4"):
        assert cases[code]["terminal"] and cases[code]["words"] == ["deploy", "alpha"], code
        assert _exit_row(code)["status"] == cases[code]["status"], code
        assert _exit_row(code)["meaning"].startswith("one Ctrl-C at one of the playbook's prompts"), code
    assert "exit cancelled at a prompt" in BEHAVIOURS


def test_the_prompt_case_s_words_are_ansible_core_s_own_and_its_play_waits_at_one_prompt() -> None:
    import ansible.plugins.action.pause
    import yaml

    # What pause asks once a Ctrl-C was typed at its prompt, and what it fails with when A answers.
    source = inspect.getsource(ansible.plugins.action.pause)
    assert re.search(rf"""prompt = (["']){re.escape(contract.GO_ON)} \\r\1""", source), "pause's question"
    assert re.search(rf"""raise AnsibleError\((["']){re.escape(contract.ABORTED)}\1\)""", source), "pause's abort"
    # The case's play is one task, a pause with a prompt and no time, which waits for a key at the terminal.
    (play,) = yaml.safe_load(contract.PROMPT_PLAY)
    (task,) = play["tasks"]
    assert play["hosts"] == "localhost" and task["ansible.builtin.pause"] == {"prompt": contract.PROMPT}


def test_the_ctrl_c_case_s_stand_in_does_what_ansible_playbook_does(tmp_path) -> None:
    import ansible.cli
    from ansible.errors import ExitCode
    from test_justfile import _stand_in

    from claude_vm.ansible.parts import ANSIBLE_INTERRUPTED

    status, says = contract.PLAYBOOK_INTERRUPTED
    # What ansible-core does on one Ctrl-C, read from its own source: says this as an error, and exits that.
    source = inspect.getsource(ansible.cli.CLI.cli_executor)
    handler = r"except KeyboardInterrupt:\s+display\.error\(\"(.+?)\"\)\s+exit_code = ExitCode\.KEYBOARD_INTERRUPT\s"
    (sentence,) = re.findall(handler, source)
    assert status == ExitCode.KEYBOARD_INTERRUPT == ANSIBLE_INTERRUPTED
    # And what its display prints for that error, at no terminal.
    printed = subprocess.run(
        [sys.executable, "-I", "-c", f"from ansible.utils.display import Display; Display().error({sentence!r})"],
        cwd=tmp_path,
        env={"PATH": os.defpath, "HOME": str(tmp_path), "LANG": "en_US.UTF-8"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert printed.stderr.splitlines() == [says], printed.stderr
    # The stand-in, given one Ctrl-C as the case gives it, to its process group while it waits: the same.
    started = tmp_path / "started"
    _stand_in(tmp_path / "bin", "ansible-playbook", contract.playbook_interrupted(started))
    process = subprocess.Popen(
        [str(tmp_path / "bin" / "ansible-playbook")],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        preexec_fn=_default_signals,  # noqa: PLW1509 - only signal calls, safe beside xdist's thread
    )
    deadline = time.monotonic() + 30
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    os.killpg(process.pid, signal.SIGINT)
    _, stderr = process.communicate(timeout=30)
    assert (process.returncode, stderr.splitlines()) == (status, [says]), stderr


def test_run_lock_given_empty_is_an_example_and_its_case_takes_no_lock() -> None:
    assert "just RUN_LOCK= deploy" in {example["line"] for example in _held()["examples"]}
    cases = {item["code"]: item for item in _held()["cases"]}
    given, without = cases["setting.run-lock-1"], cases["setting.run-lock-2"]
    assert given["words"] == ["RUN_LOCK=", *without["words"]]
    assert (given["status"], given["sentence"]) == (0, NO_LOCK_TAKEN)
    assert (without["status"], without["sentence"]) == (1, LOCK_REFUSED)


def test_an_option_given_twice_is_refused_by_just_itself() -> None:
    cases = {item["code"]: item for item in _held()["cases"]}
    for code, option in (("just.repeated-option-1", "--cores"), ("just.repeated-option-2", "--check")):
        assert cases[code]["sentence"] == f"error: recipe `deploy` option `{option}` cannot be passed more than once"


# ------------------------------------------------------------------------------------------------- the examples ----


def test_the_examples_are_held_the_first_ones_first_and_none_dropped() -> None:
    lines = [example["line"] for example in _held()["examples"]]
    count, digest = FIRST_EXAMPLES
    assert len(lines) >= count and len(set(lines)) == len(lines)
    assert hashlib.sha256("\n".join(lines[:count]).encode()).hexdigest() == digest, (
        "an example the contract was first written with is gone or moved: put it back where it was; a new one comes "
        "after them, by --scan"
    )


def test_a_write_keeps_an_example_a_page_no_longer_has_and_takes_a_new_one_only_with_scan(monkeypatch) -> None:
    held = _held()["examples"]
    new = {"line": "just status --only vms", "source": "README.md"}
    assert new["line"] not in {example["line"] for example in held}
    scans = []

    def scan() -> list[dict]:
        # A fresh scan in which a page no longer has the first example, and another has a new one.
        scans.append(1)
        return [*held[1:], new]

    monkeypatch.setattr(contract, "scan", scan)
    assert contract.build(_held())["examples"] == held and scans == []
    assert contract.build(_held(), scanned=True)["examples"] == [*held, new] and scans == [1]


def test_each_example_is_one_today_s_justfile_accepts() -> None:
    recipes = public_recipes()
    problems = []
    for example in _held()["examples"]:
        before = re.match(r"((?:[A-Z][A-Z_]*=(?:'[^']*'|\S+) )*)just ", example["line"])
        assert before, example
        unknown = set(re.findall(r"([A-Z][A-Z_]*)=(?:'[^']*'|\S+)", before.group(1))) - just_settings()
        found = list(contract.matches(example["line"]))
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
