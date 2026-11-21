"""The rules between options, as plain functions the one check runs, and today's habits of the command line
(lib/claude_vm/schema/rules.py, lib/claude_vm/schema/check.py, lib/claude_vm/cli/habits.py; docs/architecture/engine.md
§16.2 and §16.3).

Every rule sentence of the contract of today's command line (tests/unit/cli_contract.json) has a rule, or a habit, with
that sentence, aw where it said just, or is named below in one class with why: another verb's own, the repository's
own recipe's, just's own, a check of tools/check that the value's pattern refuses first (PATTERN_FIRST), or a rule aw
drops on purpose (DROPPED); every rule of rules.py is named by a verb of the table, and every code a verb names is a
rule. Every case of the contract whose check is a rule or a habit is run against aw, through the launcher, at a
pseudo-terminal of its own where today's case had one, and gives today's status and sentence; a case of a check whose
pattern refuses first is refused by that pattern on aw's line, as on just's; a case of a rule dropped is taken by aw.
The cases not yet run against aw are named one by one (NOT_YET), each with its reason, and a code comes out of the set
once it runs. The check gives the same answer to the same values as words and as JSON, and refuses a JSON string where a
number is wanted. deploy's question is asked as a menu at a pseudo-terminal opened through
harness._at_a_pseudo_terminal, typed where the terminal cannot draw one, and refused with no terminal. No rule says a
value back where today's does not. Each behaviour of today's words that aw has otherwise on purpose, and that is no exit
status, is held here by a test of its own (BEHAVIOURS_HELD).

Each test of today's options' rules in tests/unit/test_justfile.py has a twin here by its name (TWINS): what aw does
with the same words, up to the dispatch, where today's ran a playbook.
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
from pathlib import Path

import pytest
from aw_kit import aw_at_a_terminal, run_aw, run_launcher
from cli_contract_changes import BEHAVIOURS
from harness import REPO

from claude_vm.cli import before, dispatch, habits, parse
from claude_vm.schema import check as checks
from claude_vm.schema import rules
from claude_vm.schema.patterns import PATTERNS, full
from claude_vm.schema.words import VERBS

CONTRACT = json.loads((Path(__file__).resolve().parent / "cli_contract.json").read_text())
TODAY_RULES = {rule["code"]: rule for rule in CONTRACT["rules"]}
CASES = {case["code"]: case for case in CONTRACT["cases"]}
DEPLOY = VERBS["deploy"]
# A value a person may have typed where it does not belong: no sentence that does not say a value today may say it.
TYPED = "swordfish"
# Today's value in a sentence, as the justfile and tools/check write one; and ours, as rules.py writes one.
TODAYS_VALUE = re.compile(r"\{\{.*?\}\}|\$\{[^}]*\}|\$\*|\$[A-Za-z_][A-Za-z0-9_]*|\$[0-9]")
OUR_VALUE = re.compile(r"\{[a-z_]+\}")
VALUE = "<value>"

# The rules of the contract that are no rule between options, each with whose own check it is and why: each reads what
# the values alone do not say, and is made where its verb is built.
ELSEWHERE = {
    "vault.1": "it reads the fleet's files, the vault's: vault's own (D11)",
    "deploy.13": "it reads the fleet, another host's alias of the name: deploy's own, at its workflow's start (G's)",
    "host.5": "it asks whether a terminal is there to type the host's name at: host --remove's own (D12)",
    "host.6": "it is the answer typed at host --remove's question: host's own (D12)",
    "mcp.3": "it reads the file system for --project-dir's folder: mcp's own (I6)",
    "mcp.4": "it reads the file system for the key's file: mcp's own (I6)",
    "mcp.5": "it looks for claude on PATH: mcp's own (I6)",
    "mcp.6": "it reads the environment's packages: mcp's own (I6)",
    "mcp.7": "it asks Claude Code its version: mcp's own (I6)",
    "mcp.8": "it reads the checkout's branch and its changes: mcp's own (I6)",
    "mcp.9": "it reads the vault and its key's file: mcp's own (I6)",
    "mcp.10": "it asks Claude Code what it registered: mcp's own (I6)",
}
# The recipes that stay the repository's own, run by just and never by aw (the owner's word 1), whose rules are theirs.
REPOSITORYS = ("test", "docs", "vagrant", "clean")
REPOSITORYS_WHY = "a recipe of the repository's own, which stays just's (the owner's word 1): no verb of aw's"
# just's own refusals, which aw makes in its own words: an option the verb has not got is named, a value outside its
# pattern is never said back (H4).
JUSTS = {
    "just.unknown-option": "aw names the option the verb has not got in its own sentence (H4)",
    "just.pattern": "aw holds a value to its pattern in its own sentence, which never says the value (A8, H4)",
    "just.repeated-option": "aw refuses an option given twice in its own sentence, which names the option (H4)",
}
EXITS_WHY = "a run's exit status, no refusal: a run's end is C4's, status's G16's"
# The contract's cases of a setting given before the recipe today: RUN_LOCK= given empty takes no lock.
SETTINGS_WHY = "a setting given before the recipe today, RUN_LOCK=, which aw spells --no-lock (CHANGED): C6's lock"
# The habits' sentences of today's rules.
HABITS = {"deploy.1": habits.NO_TERMINAL, "deploy.2": habits.ONE_STAGE, "deploy.3": habits.NOTHING_RUN}

# The rules of the contract that are tools/check's checks of one value whose pattern refuses, on aw's line as on just's,
# every value they would: the value meets its pattern first, so today's sentence never reaches a person, and the check
# is the pattern's alone, by its name here. tools/check held the JSON the justfile's scripts join, whatever its
# assignments were overridden to, which aw has neither of (review/code/H5.md item (1)).
PATTERN_FIRST = {
    "check.size.1": "cores",
    "check.size.2": "memory",
    "check.size.3": "disk",
    "check.repos.1": "repos",
    "check.project.1": "project",
    "check.project.3": "project",
    "check.session.1": "session",
    "check.sections.1": "sections",
    "check.sections.2": "sections",
}
# Their cases run tools/check alone, since just's pattern refuses the value first: the fields each tools/check label's
# arguments are, and the verb and words that give them on aw's command line.
TOOLS_CHECK = {
    "size": (("cores", "memory", "disk"), ("deploy", "alpha")),
    "repos": (("repos",), ("deploy", "alpha")),
    "project": (("project",), ("code", "alpha")),
    "session": (("session",), ("code", "alpha")),
    "sections": (("only",), ("status",)),
}
# The rules of the contract that aw drops on purpose, each by the behaviour of BEHAVIOURS that records it for H7.
# init.4 held a relative --token-secret-file, made absolute from a folder whose path has a space, to the characters
# the JSON the justfile joins in bash can take: aw joins no JSON, and holds the value to its pattern as typed.
DROPPED = {"init.4": "init --token-secret-file: a relative file from a folder whose path has a space"}
# Each case of the contract not yet run against aw, by its code: another verb's own (ELSEWHERE), a recipe of the
# repository's own, a run's end, or a setting given before the recipe today, each with the reason _not_yet gives it. A
# code comes out once its case runs; one goes in only for a case newly added to the contract that no built verb can
# run, in the commit that adds it, with its reason (plan.md section 4, and the coordinator's ruling of 2026-11-20 on
# H19), so a case moved here shows in the diff.
NOT_YET = {
    "clean.1-1",
    "clean.2-1",
    "clean.3-1",
    "deploy.13-1",
    "docs.1-1",
    "exit.ctrl-c-1",
    "exit.ctrl-c-2",
    "exit.ctrl-c-3",
    "exit.ctrl-c-4",
    "exit.report-failed-1",
    "exit.report-ok-1",
    "exit.report-problem-1",
    "exit.report-unread-1",
    "host.5-1",
    "host.6-1",
    "mcp.10-1",
    "mcp.3-1",
    "mcp.4-1",
    "mcp.5-1",
    "mcp.6-1",
    "mcp.7-1",
    "mcp.8-1",
    "mcp.9-1",
    "setting.run-lock-1",
    "setting.run-lock-2",
    "test.1-1",
    "test.2-1",
    "test.3-1",
    "test.4-1",
    "test.5-1",
    "test.6-1",
    "test.7-1",
    "vagrant.1-1",
    "vault.1-1",
}
# Each behaviour of BEHAVIOURS (tests/unit/cli_contract_changes.py) that is no exit status, and the test here that
# holds it: the owner's answer 5 of 2026-11-20 made each aw's behaviour, and H7 measures it against today's.
BEHAVIOURS_HELD = {
    "init <extra>: an empty word": "test_an_empty_word_after_init_s_options_is_refused_which_today_s_just_takes",
    "deploy <name>: a stage, beside a rule broken": (
        "test_a_stage_where_the_names_go_beside_a_rule_broken_says_the_rule"
    ),
    "a VM name with _, beside a rule broken": "test_the_underscore_s_warning_is_not_said_where_a_rule_refuses",
    "init --token-secret-file: a relative file from a folder whose path has a space": (
        "test_a_token_file_relative_to_a_folder_whose_path_has_a_space_is_taken"
    ),
}


def _aw(sentence: str) -> str:
    """Today's sentence as aw says it: aw where it said just."""
    return re.sub(r"\bjust\b", "aw", sentence)


def _rule_of(case: str) -> str:
    return case.rsplit("-", 1)[0]


def _not_yet() -> dict[str, str]:
    """Each case of the contract not yet run against aw, with why."""
    found = {}
    for code in CASES:
        rule = _rule_of(code)
        if rule in ELSEWHERE:
            found[code] = ELSEWHERE[rule]
        elif rule.startswith("exit."):
            found[code] = EXITS_WHY
        elif rule.startswith("setting."):
            found[code] = SETTINGS_WHY
        elif TODAY_RULES[rule]["recipe"] in REPOSITORYS:
            found[code] = REPOSITORYS_WHY
    return found


def _pattern_first() -> list[str]:
    return [code for code in CASES if _rule_of(code) in PATTERN_FIRST]


def _dropped() -> list[str]:
    return [code for code in CASES if _rule_of(code) in DROPPED]


def _launched(terminal: bool) -> list[str]:
    """The cases run through the launcher: their rule is a rule of rules.py or a habit, and today's case had a terminal
    on its standard input, or had none."""
    skip = set(_not_yet())
    return [
        code
        for code, case in CASES.items()
        if code not in skip
        and (_rule_of(code) in rules.RULES or _rule_of(code) in HABITS)
        and case["terminal"] is terminal
    ]


def _justs() -> list[str]:
    return [code for code in CASES if _rule_of(code) in JUSTS]


# ---------------------------------------------------------------- the rules, against the contract ----


def test_every_rule_of_the_contract_is_a_rule_a_habit_or_another_s_own_with_why() -> None:
    placed = {}
    for code, rule in TODAY_RULES.items():
        where = [
            name
            for name, holds in (
                ("rules", code in rules.RULES),
                ("habits", code in HABITS),
                ("elsewhere", code in ELSEWHERE),
                ("repository", rule["recipe"] in REPOSITORYS),
                ("just", code in JUSTS),
                ("pattern", code in PATTERN_FIRST),
                ("dropped", code in DROPPED),
            )
            if holds
        ]
        placed[code] = where
    assert {code: where for code, where in placed.items() if len(where) != 1} == {}
    assert set(ELSEWHERE) | set(JUSTS) | set(PATTERN_FIRST) | set(DROPPED) <= set(TODAY_RULES)
    assert all(why.strip() for why in (*ELSEWHERE.values(), *JUSTS.values()))
    assert set(PATTERN_FIRST.values()) <= set(PATTERNS) and set(DROPPED.values()) <= set(BEHAVIOURS)


def test_no_rule_repeats_a_value_s_own_pattern_and_the_names_check_is_host_for_s_alone() -> None:
    # The nine checks of one value and init.4 left the rows and rules.py; check.names.1 holds host --for's names, split
    # at its commas, where the pattern takes alpha,-b whole, and is named by no other row: every other VM name a verb
    # takes is held whole by its pattern.
    named = {code for verb in VERBS.values() for code in verb.rules}
    assert sorted(named & (set(PATTERN_FIRST) | set(DROPPED))) == []
    assert sorted(set(rules.RULES) & (set(PATTERN_FIRST) | set(DROPPED))) == []
    assert [name for name, verb in VERBS.items() if "check.names.1" in verb.rules] == ["host"]


@pytest.mark.parametrize("code", sorted(set(rules.RULES) | set(HABITS)))
def test_each_rule_says_today_s_sentence_with_aw_for_just(code: str) -> None:
    ours = rules.RULES[code].sentence if code in rules.RULES else HABITS[code]
    today = _aw(TODAY_RULES[code]["sentence"])
    assert OUR_VALUE.sub(VALUE, ours) == TODAYS_VALUE.sub(VALUE, today)
    # A value said back only where today's says one.
    assert bool(OUR_VALUE.search(ours)) is bool(TODAYS_VALUE.search(today)), code


def test_every_rule_is_named_by_the_verbs_whose_rows_run_it_and_every_code_a_verb_names_is_a_rule() -> None:
    named: dict[str, set[str]] = {}
    for verb in VERBS.values():
        for code in verb.rules:
            named.setdefault(code, set()).add(verb.name)
    assert sorted(set(named) - set(rules.RULES)) == [], "a verb names a rule that rules.py has not got"
    assert {code: set(rule.verbs) for code, rule in rules.RULES.items()} == named
    for verb in VERBS.values():
        assert len(set(verb.rules)) == len(verb.rules), verb.name


def test_no_rule_says_a_value_back_where_today_s_does_not() -> None:
    # settings --value's value, and init's word after its options, which may be a secret.
    said = [
        rules.RULES["settings.3"].check({"value": TYPED}),
        rules.RULES["settings.4"].check({"name": "vm_cores", "value": TYPED, "reset": True}),
        rules.RULES["init.1"].check({"extra": TYPED}),
        rules.RULES["init.1"].check({"extra": [TYPED, TYPED]}),
        rules.RULES["mcp.1"].check({"extra": TYPED}),
    ]
    assert all(sentence and TYPED not in sentence for sentence in said), said
    for words in (["settings", f"--value={TYPED}"], ["init", TYPED], ["mcp", TYPED]):
        with pytest.raises(parse.Refused) as refused:
            parse.read(words)
        assert TYPED not in refused.value.sentence


def test_the_tags_are_configure_s_own() -> None:
    # Today's test holds the justfile's configure_roles to configure.yml's tags; this one the rule's to the justfile's.
    found = re.search(r"(?m)^configure_roles := '([^']*)'\n", (REPO / "justfile").read_text())
    assert found and tuple(found[1].split()) == rules.CONFIGURE_ROLES


# ---------------------------------------------------------------- the contract's cases, against aw ----


@pytest.mark.parametrize("code", _launched(terminal=False))
def test_each_case_of_a_rule_or_a_habit_gives_today_s_status_and_sentence_through_the_launcher(home, code) -> None:
    case = CASES[code]
    result = run_launcher(*case["words"], home=home, cwd=home)
    assert (result.returncode, result.stdout, result.stderr) == (case["status"], "", _aw(case["sentence"]) + "\n")


@pytest.mark.parametrize("code", _pattern_first())
def test_each_case_of_a_check_whose_pattern_refuses_first_is_refused_by_it_on_aw_s_line_as_on_just_s(code) -> None:
    # Today's case runs tools/check alone, since just's pattern refuses the value first: on aw's command line the same
    # value meets the same pattern first, whose sentence never says it.
    case = CASES[code]
    _, label, *given = case["words"]
    assert case["words"][0] == "tools/check", code
    fields, words = TOOLS_CHECK[label]
    values = {field: value for field, value in zip(fields, given, strict=True) if value}
    options = {option.field: option for option in VERBS[words[0]].options}
    assert values, code
    for field, value in values.items():
        option = options[field]
        assert option.pattern == PATTERN_FIRST[_rule_of(code)] and not full(option.pattern, value), (code, field)
        with pytest.raises(parse.Refused) as refused:
            parse.read([*words, f"--{option.long}={value}"])
        assert refused.value.sentence == f"error: aw {words[0]} --{option.long} takes no such value: " + (
            option.help or ""
        )


@pytest.mark.parametrize("code", _dropped())
def test_each_case_of_a_rule_aw_drops_on_purpose_is_taken_by_aw(home, code) -> None:
    # init.4-1: the words reach the dispatch from a folder whose path has a space, where today's are refused.
    case = CASES[code]
    spaced = home / "a b"
    spaced.mkdir()
    result = run_launcher(*case["words"], home=home, cwd=spaced)
    verb = VERBS[case["words"][0]]
    assert (result.returncode, result.stdout, result.stderr) == (1, "", dispatch.not_built(verb) + "\n")
    assert DROPPED[_rule_of(code)] in BEHAVIOURS


@pytest.mark.parametrize("code", _justs())
def test_each_refusal_of_just_s_own_is_aw_s_in_its_own_words(home, code) -> None:
    case = CASES[code]
    result = run_launcher(*case["words"], home=home, cwd=home)
    with pytest.raises(parse.Refused) as refused:
        parse.read(case["words"])
    assert (result.returncode, result.stdout) == (case["status"], "")
    assert result.stderr == refused.value.sentence + "\n" and refused.value.sentence != _aw(case["sentence"])
    assert refused.value.sentence.startswith("error: aw ")


def test_every_case_is_run_against_aw_or_named_with_why_and_those_not_run_are_named_one_by_one() -> None:
    run = [*_launched(terminal=False), *_launched(terminal=True), *_pattern_first(), *_justs(), *_dropped()]
    not_yet = _not_yet()
    assert len(run) == len(set(run)) and set(run) & set(not_yet) == set()
    assert sorted(set(CASES) - set(run) - set(not_yet)) == [], "a case neither run against aw nor said why not"
    assert all(why.strip() for why in not_yet.values())
    assert sorted(set(not_yet) - NOT_YET) == [], (
        "a case not run against aw that NOT_YET does not name: run it; only a case newly added to the contract that no "
        "built verb can run is named there, in the commit that adds it, with its reason"
    )
    assert sorted(NOT_YET - set(CASES)) == [], "NOT_YET names a code that is no case of the contract: take it out"
    assert sorted(NOT_YET & set(run)) == [], "NOT_YET names a code that is run against aw now: take it out"
    # Each not run is another verb's own, a recipe of the repository's own, or a run's end: never a rule of this card's.
    assert [code for code in not_yet if _rule_of(code) in rules.RULES or _rule_of(code) in HABITS] == []


# ---------------------------------------------------------------- the one check, as words and as JSON ----


def _parsed(monkeypatch, words: list[str]) -> tuple[str, dict]:
    """The verb and values the parser hands the check for words, before it checks them."""
    seen: dict = {}

    def keep(verb, values):
        seen.update(verb=verb, values=dict(values))

    with monkeypatch.context() as patched:
        patched.setattr(parse.checks, "check", keep)
        parse.read(words)
    return seen["verb"], seen["values"]


@pytest.mark.parametrize(
    "code",
    [code for code in _launched(terminal=False) if _rule_of(code) in rules.RULES]
    # just's refusal of an unknown option is the parser's, before any value reaches the check.
    + [code for code in _justs() if _rule_of(code) == "just.pattern"],
)
def test_the_check_gives_the_same_answer_to_the_same_values_as_words_and_as_json(monkeypatch, code) -> None:
    words = CASES[code]["words"]
    verb, values = _parsed(monkeypatch, words)
    with pytest.raises(parse.Refused) as refused:
        parse.read(words)
    assert checks.check(verb, json.loads(json.dumps(values))) == refused.value.sentence


def test_the_check_refuses_a_json_string_where_a_number_is_wanted_and_coerces_nothing() -> None:
    assert checks.check("deploy", {"names": ["alpha"], "verbosity": 3}) is None
    number = f"error: {checks.command('deploy')} -v takes a whole number"
    assert checks.check("deploy", {"names": ["alpha"], "verbosity": "3"}) == number
    assert checks.check("deploy", {"only": "configure", "start": "true"}) is not None
    assert checks.check("deploy", {"only": "configure", "start": True}) == rules.RULES["deploy.10"].check(
        {"only": "configure", "start": True}
    )


def test_a_rule_runs_after_the_patterns_so_a_value_it_would_say_back_has_met_its_pattern_first() -> None:
    # code.2 says the names back, as today: a second name with a control character meets the pattern's sentence first.
    sentence = checks.check("code", {"name": ["alpha", f"{TYPED}\x07"]})
    assert sentence and TYPED not in sentence and "takes no such name" in sentence
    assert checks.check("code", {"name": ["alpha", "beta"]}) == "error: code opens one VM at a time, not [alpha beta]"


def test_the_rules_run_in_the_order_the_row_names_them_and_stop_at_the_first() -> None:
    values = {"only": "configure", "check": True, "start": True, "tags": "claude"}
    assert checks.check("deploy", values) == rules.RULES["deploy.5"].check(values)
    assert rules.run(("deploy.10", "deploy.5"), values) == rules.RULES["deploy.10"].check(values)
    assert rules.run((), values) is None


def test_a_word_beyond_what_a_verb_takes_is_refused_once_its_rules_had_their_say() -> None:
    # No rule of today's is a second word's for release: the check's own sentence, after the rules.
    assert checks.check("release", {"name": ["alpha", "beta"]}) == checks.one_word("release", VERBS["release"].words)
    with pytest.raises(parse.Refused) as refused:
        parse.read(["release", "alpha", "beta"])
    assert refused.value.sentence == checks.one_word("release", VERBS["release"].words)


# ---------------------------------------------------------------- deploy's question, in process ----


class _Typed(io.StringIO):
    """A terminal that cannot draw a menu: it is one, and what is read from it is what was typed."""

    def isatty(self) -> bool:
        return True


def _typed(words: list[str], answer: str) -> tuple[dict | str, str]:
    """deploy's question asked in process at a terminal that cannot draw a menu, answer typed: what the values came to,
    or the sentence of the refusal, and what was said."""
    reading = parse.read(words)
    values, _ = habits.names(VERBS[reading.verb], reading.values)
    said = io.StringIO()
    try:
        return habits.stage(VERBS[reading.verb], values, _Typed(answer), said), said.getvalue()
    except parse.Refused as refused:
        return refused.sentence, said.getvalue()


def _values(*words: str) -> dict:
    """What words come to, up to the dispatch, with no terminal to ask at."""
    reading = parse.read(list(words))
    verb = VERBS[reading.verb]
    values, _ = habits.names(verb, reading.values)
    return habits.stage(verb, values, None, io.StringIO())


def _refused(*words: str) -> str:
    with pytest.raises(parse.Refused) as refused:
        _values(*words)
    return refused.value.sentence


def test_the_question_typed_says_today_s_lines_with_aw() -> None:
    _, said = _typed(["deploy", "alpha", "configure", "--tags", "claude_code"], "v\n")
    assert said.splitlines() == [
        "configure is a stage of aw deploy and also a name a VM could have. Did you mean",
        "  s  the stage: aw deploy --only configure --tags claude_code alpha",
        "  v  a VM named configure, created if there is none: aw deploy alpha,configure --tags claude_code",
        "s or v, or Enter to run nothing: ",
    ]


def test_the_answer_meets_the_rules_and_says_no_default_where_names_are_left() -> None:
    values = {"names": ["alpha", "configure"], "set": ["vm_cores=4"]}
    assert habits.answered(DEPLOY, values, "s") == {"names": ["alpha"], "only": "configure", "set": ["vm_cores=4"]}
    assert habits.answered(DEPLOY, {"names": ["provision"]}, "S") == {"only": "provision", "default_vm": True}
    assert habits.answered(DEPLOY, values, "V") == values
    assert habits.answered(DEPLOY, {"names": ["alpha"]}, "x") == {"names": ["alpha"]}  # no stage given, no question
    with pytest.raises(parse.Refused) as refused:
        habits.answered(DEPLOY, {"names": ["template", "alpha"]}, "s")
    assert refused.value.sentence == rules.RULES["deploy.9"].check({"only": "template", "names": ["alpha"]})


def test_no_question_for_another_verb_or_beside_a_stage_given() -> None:
    assert habits.stages_given(VERBS["destroy"], {"names": ["configure"]}) is None
    assert habits.stages_given(DEPLOY, {"names": ["configure"], "only": "provision"}) is None
    assert habits.stages_given(DEPLOY, {"names": ["configure"], "check": True}) is None
    assert habits.stages_given(DEPLOY, {"names": ["configure"], "only": ""}) == (["configure"], [])
    assert habits.stages_given(DEPLOY, {"names": ["alpha", "ssh-config", "beta"]}) == (
        ["ssh-config"],
        ["alpha", "beta"],
    )


# ---------------------------------------------------------------- deploy's question, at a pseudo-terminal ----

# A window wide enough for each answer whole: a pseudo-terminal opens at no size.
WINDOW = (200, 40)
# What the menu says under its answers once it is drawn, and the typed question's last words.
DRAWN = b"to choose"
PROMPTED = b"Enter to run nothing: "


def _at_the_question(home, words: list[str], typed: bytes, *, menu: bool = True, asked: bool = True) -> tuple[int, str]:
    """aw with words at a pseudo-terminal of its own, typed once the question is asked: as a menu with a terminal that
    can draw one, typed with one that cannot (TERM=dumb). Its status and what it wrote, as text. With asked False, the
    run is held to ask nothing, and nothing is typed."""
    seen = []

    def act(chunk, output, master, process) -> None:
        if not seen and (DRAWN if menu else PROMPTED) in output:
            seen.append(True)
            os.write(master, typed)

    # A terminal the menu can draw on is one of a known type, and UTF-8 for its glyphs (live_region.can_draw).
    env = {"TERM": "xterm-256color" if menu else "dumb", "LC_ALL": "C.UTF-8"}
    rc, output = aw_at_a_terminal(*words, home=home, env=env, act=act, size=WINDOW, timeout=60)
    assert bool(seen) is asked, output
    return rc, output.decode(errors="replace").replace("\r\n", "\n")


@pytest.mark.parametrize("code", _launched(terminal=True))
def test_each_case_at_a_terminal_gives_today_s_status_and_sentence(home, code) -> None:
    # deploy.2's s picks no stage from a menu that offers none where two were given: it is typed, as today, at a
    # terminal that cannot draw one; Enter (deploy.3) runs nothing either way.
    case = CASES[code]
    typed = {"deploy.2-1": b"s\n", "deploy.3-1": b"\n"}[code]
    for menu in (False, True) if code == "deploy.3-1" else (False,):
        rc, said = _at_the_question(home, case["words"], typed.replace(b"\n", b"\r") if menu else typed, menu=menu)
        assert rc == case["status"] and said.rstrip("\n").endswith(_aw(case["sentence"])), said


def test_deploy_asks_and_takes_a_stage_word_for_a_vm_name_when_that_was_meant(home) -> None:
    rc, said = _at_the_question(home, ["deploy", "alpha", "configure"], b"v\r")
    assert "configure is a stage of aw deploy and also a name a VM could have. Did you mean" in said
    assert "s  the stage: aw deploy --only configure alpha" in said
    assert "v  a VM named configure, created if there is none: aw deploy alpha,configure" in said
    # The names as given reach the dispatch, which says deploy is not built yet.
    assert rc == 1 and said.rstrip("\n").endswith(dispatch.not_built(DEPLOY)), said
    values = {"names": ["alpha", "configure"]}
    assert habits.answered(DEPLOY, values, "v") == values


@pytest.mark.parametrize(
    ("words", "taken"),
    [
        (["configure"], {"only": "configure", "default_vm": False}),
        (["alpha", "configure"], {"only": "configure", "names": ["alpha"]}),
        (["provision"], {"only": "provision", "default_vm": True}),
        (["alpha,ssh-config"], {"only": "ssh-config", "names": ["alpha"]}),
    ],
)
def test_deploy_runs_the_stage_when_that_was_meant(words, taken) -> None:
    assert _typed(["deploy", *words], "s\n")[0] == taken


def test_the_stage_chosen_from_the_menu_reaches_the_dispatch(home) -> None:
    rc, said = _at_the_question(home, ["deploy", "alpha", "configure"], b"s\r")
    assert "  s  the stage: aw deploy --only configure alpha" in said
    assert rc == 1 and said.rstrip("\n").endswith(dispatch.not_built(DEPLOY)), said


def test_check_meant_as_the_stage_is_the_dry_run() -> None:
    values, said = _typed(["deploy", "check"], "s\n")
    assert "  s  the stage: aw deploy --check" in said
    assert values == {"check": True, "default_vm": False}


@pytest.mark.parametrize("answer", ["\n", "x\n", ""])
def test_deploy_runs_nothing_unless_the_question_is_answered(home, answer: str) -> None:
    # Enter, anything else, or the end of the input: nothing runs, and no VM is created.
    assert _typed(["deploy", "configure"], answer)[0] == habits.NOTHING_RUN
    # At the menu: Enter on its first answer, another key then Enter, Ctrl-D, Esc.
    typed = {"\n": b"\r", "x\n": b"x\r", "": b"\x04"}[answer]
    rc, said = _at_the_question(home, ["deploy", "configure"], typed)
    assert rc == 1 and said.rstrip("\n").endswith(habits.NOTHING_RUN), said


def test_ctrl_c_at_the_question_ends_it_and_runs_nothing(home) -> None:
    rc, said = _at_the_question(home, ["deploy", "configure"], b"\x03")
    assert rc == 130 and habits.NOTHING_RUN not in said and dispatch.not_built(DEPLOY) not in said, said


def test_two_stage_words_are_vm_names_or_nothing(home) -> None:
    stage, said = _typed(["deploy", "configure", "check"], "s\n")
    assert stage == habits.ONE_STAGE and "  s  " not in said
    assert _typed(["deploy", "configure", "check"], "v\n")[0] == {"names": ["configure", "check"]}
    rc, said = _at_the_question(home, ["deploy", "configure", "check"], b"v\r")
    assert "s  the stage" not in said and "v  VMs so named, each created if there is none" in said
    assert rc == 1 and said.rstrip("\n").endswith(dispatch.not_built(DEPLOY)), said


def test_the_stage_meant_still_meets_the_rules(home) -> None:
    # As if typed: --start starts nothing under --only configure.
    start = rules.RULES["deploy.10"].check({"start": True, "only": "configure"})
    assert _typed(["deploy", "configure", "--start"], "s\n")[0] == start
    rc, said = _at_the_question(home, ["deploy", "configure", "--start"], b"s\r")
    assert rc == 1 and said.rstrip("\n").endswith(start), said


def test_a_stage_word_beside_a_stage_is_a_vm_name_without_a_question() -> None:
    assert _values("deploy", "--only", "provision", "configure") == {"only": "provision", "names": ["configure"]}
    assert _values("deploy", "--check", "check") == {"check": True, "names": ["check"]}


@pytest.mark.parametrize(
    ("words", "names"),
    [(["test"], ["test"]), (["alpha", "lint"], ["alpha", "lint"]), (["docs,code"], ["docs", "code"])],
)
def test_deploy_takes_a_vm_named_like_a_recipe(words: list[str], names: list[str]) -> None:
    assert _values("deploy", *words) == {"names": names}


@pytest.mark.parametrize(
    "words",
    [["configure"], ["alpha", "template"], ["provision"], ["ssh-config"], ["alpha,check"], ["check", "provision"]],
)
def test_deploy_without_a_terminal_takes_a_stage_word_for_neither(home, words: list[str]) -> None:
    result = run_aw("deploy", *words, home=home)
    assert (result.returncode, result.stdout) == (1, "")
    assert "there is no terminal to ask which was meant" in result.stderr
    assert "or a VM so named beside one, as aw deploy --only provision" in result.stderr


def test_check_refused_as_a_name_points_at_the_dry_run_option() -> None:
    sentence = _refused("deploy", "check")
    assert "give a stage as aw deploy --check," in sentence and "--only check" not in sentence


def test_code_opens_exactly_one_vm() -> None:
    assert _values("code", "alpha") == {"name": "alpha"}
    assert _refused("code") == rules.RULES["code.1"].sentence
    assert _refused("code", "alpha", "beta") == "error: code opens one VM at a time, not [alpha beta]"
    assert _refused("code", "alpha", "list") == "error: code opens one VM at a time, not [alpha list]"
    assert _refused("code", "alpha,beta") == "error: code opens one VM at a time, not [alpha,beta]"


# ---------------------------------------------------------------- what aw does otherwise on purpose ----


def test_each_behaviour_aw_has_otherwise_on_purpose_is_held_by_a_test_here() -> None:
    # An exit status's is held where the contract's words are (tests/unit/test_aw_cli_contract_words.py); each other is
    # held here, by a test of its own that fails with today's behaviour put back.
    assert set(BEHAVIOURS_HELD) == {key for key in BEHAVIOURS if not key.startswith("exit ")}
    assert sorted(set(BEHAVIOURS_HELD.values()) - set(_tests_of(Path(__file__)))) == []


def test_an_empty_word_after_init_s_options_is_refused_which_today_s_just_takes(home) -> None:
    # A deliberate difference: init takes nothing after its options, an empty word too. Today's init joins its words
    # with spaces and finds nothing, so just init with one empty word runs; with two it is refused.
    result = run_launcher("init", "", home=home, cwd=home)
    assert (result.returncode, result.stdout, result.stderr) == (1, "", rules.RULES["init.1"].sentence + "\n")
    assert _refused("init", "") == rules.RULES["init.1"].sentence


def test_a_stage_where_the_names_go_beside_a_rule_broken_says_the_rule(home) -> None:
    # A deliberate difference: the rules between options are held before anything is asked, so a stage given where the
    # names go on a line that breaks one is refused by the rule, with no terminal as at one. Today's deploy asks first
    # at a terminal, and with none says deploy.1's sentence: refused either way.
    words = ("deploy", "configure", "--tags", "claude_code", "--start")
    start = rules.RULES["deploy.10"].check({"names": ["configure"], "tags": "claude_code", "start": True})
    result = run_launcher(*words, home=home, cwd=home)
    assert (result.returncode, result.stdout, result.stderr) == (1, "", f"{start}\n")
    # At a terminal too: nothing is asked, and the rule's sentence is the one said.
    rc, said = _at_the_question(home, list(words), b"s\r", asked=False)
    assert rc == 1 and "Did you mean" not in said and said.rstrip("\n").endswith(start), said


@pytest.mark.parametrize(
    ("words", "code", "values"),
    [
        (["deploy", "my_vm", "--tags", "common,,tailscale"], "deploy.4", {"tags": "common,,tailscale"}),
        (["code", "my_vm,b"], "code.2", {"name": "my_vm,b"}),
        (["destroy", "--confirm=x", "my_vm"], "destroy.2", {"confirm": "x", "names": ["my_vm"]}),
    ],
    ids=["deploy", "code", "destroy"],
)
def test_the_underscore_s_warning_is_not_said_where_a_rule_refuses(home, words, code, values) -> None:
    # A deliberate difference: the one check refuses before the names are taken with - and said, so a line that breaks
    # a rule says the rule's sentence alone, where today's just says the underscore's warning first.
    result = run_launcher(*words, home=home, cwd=home)
    assert (result.returncode, result.stdout, result.stderr) == (1, "", rules.RULES[code].check(values) + "\n")
    # Where no rule is broken, the warning is said, as today.
    kept = run_launcher("deploy", "my_vm", home=home, cwd=home)
    assert kept.stderr.splitlines()[0] == habits.HYPHENATED.format(name="my_vm", changed="my-vm")


def test_a_token_file_relative_to_a_folder_whose_path_has_a_space_is_taken() -> None:
    # A deliberate difference: init.4 is dropped, and the file's name is held to its pattern as typed; making it
    # absolute from the working folder is init's own (E4), and holds it to nothing more. Today's init refuses it there.
    assert "init.4" not in VERBS["init"].rules and "init.4" not in rules.RULES
    assert parse.read(["init", "--token-secret-file=secret"]).values == {"token_secret_file": "secret"}
    assert checks.check("init", {"token_secret_file": "secret"}) is None


def test_host_for_s_names_are_split_at_its_commas_and_an_empty_one_is_dropped() -> None:
    # As today's just host splits --for before tools/check names, whose shell drops an empty word.
    assert checks.check("host", {"json": True, "for_": "alpha,,beta"}) is None
    assert checks.check("host", {"json": True, "for_": "alpha,-b"}) == rules.RULES["check.names.1"].check(
        {"for_": "alpha,-b"}
    )
    assert rules.RULES["check.names.1"].check({"for_": "alpha,-b"}).endswith("(got: [-b])")


# ---------------------------------------------------------------- the twins of today's options' rules ----

SIGN_INS = ("claude", "github", "tailscale")
SIGNING_IN = [["deploy", "alpha"], ["login", "alpha"], *(["login", "alpha", "--only", only] for only in SIGN_INS)]
SESSION_ID = "0a608ad8-1b2c-4d3e-8f90-123456789abc"
BAD_SESSIONS = [
    pytest.param(SESSION_ID.upper(), id="uppercase"),
    pytest.param(SESSION_ID + "0", id="too-long"),
    pytest.param(SESSION_ID[:-1], id="too-short"),
    pytest.param(SESSION_ID.replace("-", ""), id="no-dashes"),
    pytest.param("../" + SESSION_ID, id="path"),
    pytest.param(SESSION_ID + "' -e 'x=1", id="quote"),
    pytest.param(SESSION_ID + '"}', id="json"),
    pytest.param("$(x)", id="command-substitution"),
    pytest.param(SESSION_ID + "\nother", id="newline"),
    pytest.param("-" * 36, id="dashes"),
]


def _outside(verb: str, field: str) -> str:
    """The sentence of a value outside the pattern of verb's option received as field: it never says the value."""
    option = next(option for option in VERBS[verb].options if option.field == field)
    return f"error: aw {verb} {checks.spelled(option)} takes no such value: {option.help}"


def test_an_option_may_come_before_or_after_the_names() -> None:
    after = _values("deploy", "alpha", "--tags", "claude_code")
    assert after == _values("deploy", "--tags=claude_code", "alpha") == _values("deploy", "-t", "claude_code", "alpha")
    assert after == {"names": ["alpha"], "tags": "claude_code"}


@pytest.mark.parametrize("only", ["", *SIGN_INS])
def test_each_sign_in_runs_its_playbook_and_none_all_three(only: str) -> None:
    # The sign-in each runs is login's own (G12): aw hands it the one named, or none for all three.
    values = _values("login", "alpha", "beta", *(["--only", only] if only else []))
    assert values == {"names": ["alpha", "beta"], **({"only": only} if only else {})}


@pytest.mark.parametrize(
    ("args", "said"),
    [
        pytest.param(["deploy", "--only", "bogus"], _outside("deploy", "only"), id="unknown-stage"),
        pytest.param(["deploy", "--only", "check"], _outside("deploy", "only"), id="check-is-a-flag"),
        pytest.param(["login", "--only", "configure"], _outside("login", "only"), id="another-recipe-s"),
        pytest.param(["deploy", "--type", "configure"], checks.no_option("deploy", "--type"), id="make-s-type"),
        pytest.param(["deploy", "--tags", "a", "--tags", "b"], "takes --tags once", id="twice"),
        pytest.param(["test", "lint"], checks.NO_SUCH_COMMAND, id="lint-is-its-own"),
        pytest.param(["status", "--only", "x"], _outside("status", "only"), id="status-section"),
        pytest.param(["destroy", "--project", "repo"], checks.no_option("destroy", "--project"), id="destroy"),
        pytest.param(["deploy", "alpha", "TAGS=claude_code"], "takes no such name", id="make-s-spelling"),
    ],
)
def test_an_option_is_checked_before_anything_runs(args: list[str], said: str) -> None:
    assert said in _refused(*args)


def test_a_setting_just_does_not_have_is_refused() -> None:
    # A setting before the verb, make's TYPE among them, is no word of aw's: taken for the verb, which the table has not
    # got. Spelled in two parts, so the scan for make's variables left in the sources does not take it for one.
    assert _refused("=".join(("TYPE", "configure")), "deploy") == checks.NO_SUCH_COMMAND


def test_template_takes_no_names() -> None:
    assert _refused("deploy", "alpha", "--only", "template") == rules.RULES["deploy.9"].sentence


@pytest.mark.parametrize("stage", ["template", "provision", "ssh-config"])
def test_check_with_a_stage_is_refused(stage: str) -> None:
    assert "--check is a dry run of configuration" in _refused("deploy", "--check", "--only", stage)


def test_tags_beside_names_means_only_configure() -> None:
    # Accepted: that it configures those roles alone is deploy's own (G10).
    assert _values("deploy", "alpha", "--tags", "claude_code,tailscale") == {
        "names": ["alpha"],
        "tags": "claude_code,tailscale",
    }


def test_tags_without_names_is_refused_and_the_fleet_takes_only_configure() -> None:
    sentence = _refused("deploy", "--tags", "claude_code")
    assert "--tags on its own configures the VMs named beside it: aw deploy <name>... --tags claude_code" in sentence
    assert "aw deploy --only configure --tags claude_code" in sentence
    fleet = _values("deploy", "--only", "configure", "--tags", "claude_code")
    assert fleet == {"only": "configure", "tags": "claude_code", "default_vm": False}


def test_tags_with_check_are_a_dry_run_of_those_roles() -> None:
    assert _values("deploy", "--check", "--tags", "github_projects") == {
        "check": True,
        "tags": "github_projects",
        "default_vm": False,
    }


@pytest.mark.parametrize("stage", ["template", "provision", "ssh-config"])
def test_tags_with_a_stage_that_has_none_are_refused(stage: str) -> None:
    sentence = _refused("deploy", "--only", stage, "--tags", "common")
    assert f"--tags narrows configuration, which --only {stage} does not run" in sentence


@pytest.mark.parametrize("tags", ["claude", "common,always", "common,,tailscale", ","])
def test_tags_may_name_only_the_roles(tags: str) -> None:
    sentence = _refused("deploy", "alpha", "--tags", tags)
    assert "--tags may name only" in sentence or "separated by single commas" in sentence


@pytest.mark.parametrize("tags", ["claude_code;rm -rf x", "$(touch x)", "--skip-tags=common", "Common"])
def test_tags_outside_their_characters_are_refused_by_the_pattern(tags: str) -> None:
    assert _refused("deploy", "alpha", f"--tags={tags}") == _outside("deploy", "tags")


@pytest.mark.parametrize("args", [[], ["--only", "provision"]])
def test_start_starts_a_vm_the_run_would_leave_stopped(args: list[str]) -> None:
    assert _values("deploy", "gamma", "--start", *args) == {"names": ["gamma"], "start": True} | (
        {"only": "provision"} if args else {}
    )


@pytest.mark.parametrize(
    "args",
    [["--only", "configure"], ["--check"], ["--tags", "common"], ["--only", "template"], ["--only", "ssh-config"]],
)
def test_start_with_a_run_that_starts_nothing_is_refused(args: list[str]) -> None:
    names = [] if "template" in args else ["gamma"]
    assert "--start starts a VM the run would leave stopped" in _refused("deploy", *names, "--start", *args)


@pytest.mark.parametrize(
    "value", ["discord-music-bot", "ParkBnb", "djangoTest", "omkar_kv", "site.github.io", "x", "a" * 100]
)
def test_code_passes_a_project_as_its_own_json_extra_var(value: str) -> None:
    assert _values("code", "alpha", "--project", value) == {"name": "alpha", "project": value}


def test_code_without_its_options_passes_none_of_their_vars() -> None:
    assert _values("code", "alpha") == {"name": "alpha"}


@pytest.mark.parametrize(
    ("value", "said"),
    [
        pytest.param("../etc", _outside("code", "project"), id="parent-path"),
        pytest.param("org/repo", _outside("code", "project"), id="slash"),
        pytest.param("/etc", _outside("code", "project"), id="absolute"),
        pytest.param(".", "error: --project names one project folder, not [.]", id="dot"),
        pytest.param("..", "error: --project names one project folder, not [..]", id="dot-dot"),
        pytest.param("a b", _outside("code", "project"), id="space"),
        pytest.param('a"b', _outside("code", "project"), id="double-quote"),
        pytest.param("a,b", _outside("code", "project"), id="comma"),
        pytest.param("repo\nother", _outside("code", "project"), id="newline"),
        pytest.param("répo", _outside("code", "project"), id="non-ascii"),
        pytest.param("a" * 101, _outside("code", "project"), id="too-long"),
    ],
)
def test_code_refuses_a_bad_project_before_the_playbook(value: str, said: str) -> None:
    assert _refused("code", "alpha", "--project", value) == said


def test_code_passes_an_editor_as_its_own_json_extra_var() -> None:
    assert _values("code", "alpha", "--editor", "vscode") == {"name": "alpha", "editor": "vscode"}
    assert _values("code", "alpha", "--editor", "zed") == {"name": "alpha", "editor": "zed"}


@pytest.mark.parametrize("value", ["emacs", "VSCode", "vscode,zed", '"}', "$(x)", "-e"])
def test_code_refuses_an_editor_it_cannot_open_before_the_playbook(value: str) -> None:
    sentence = _refused("code", "alpha", "--editor", value)
    assert sentence.startswith("error: aw code --editor ") and (value not in sentence or value == "-e"), sentence


def test_code_passes_a_session_as_its_own_json_extra_var() -> None:
    assert _values("code", "alpha", "--project", "repo", "--session", SESSION_ID) == {
        "name": "alpha",
        "project": "repo",
        "session": SESSION_ID,
    }


@pytest.mark.parametrize("value", BAD_SESSIONS)
def test_code_refuses_a_bad_session_before_the_playbook(value: str) -> None:
    assert _refused("code", "alpha", f"--session={value}") == _outside("code", "session")


@pytest.mark.parametrize("value", BAD_SESSIONS)
def test_the_session_check_holds_the_json_whatever_reaches_it(value: str) -> None:
    # The session's pattern, which every entry's values meet first in the one check, as tools/check session was behind
    # just's: the words of aw's line, and the JSON of the agents' server and the add-on's broker alike.
    assert not full("session", value), value
    assert checks.check("code", {"name": "alpha", "session": value}) == _outside("code", "session")


@pytest.mark.parametrize("value", [SESSION_ID, ""])
def test_the_session_check_takes_a_session_id_or_none(value: str) -> None:
    assert full("session", value)
    assert checks.check("code", {"name": "alpha", "session": value}) is None


@pytest.mark.parametrize(
    ("option", "value"),
    [("--project", "repo"), ("--session", SESSION_ID), ("--editor", "vscode")],
    ids=["project", "session", "editor"],
)
@pytest.mark.parametrize("recipe", ["deploy", "login", "status", "destroy"])
def test_only_code_takes_its_options(recipe: str, option: str, value: str) -> None:
    assert _refused(recipe, "alpha", option, value) == checks.no_option(recipe, option)


@pytest.mark.parametrize("args", SIGNING_IN)
def test_a_run_with_no_terminal_turns_the_desktop_steps_and_deploy_s_sign_ins_off(args) -> None:
    # Whether a terminal is there is the sign-ins' own to read (G12): aw hands on that nobody said someone is at the
    # screen, whatever the environment says.
    reading = parse.read(args)
    run, values = before.run_words(reading.before, _values(*args), {"ATTENDED": "1"})
    assert run.attended is False and values == _values(*args)


@pytest.mark.parametrize("args", SIGNING_IN)
def test_attended_with_no_terminal_opens_the_pages_and_signs_in_but_asks_nothing(args) -> None:
    # --attended=1 before the verb, as the agents' server gives an interactive session's run; an exported ATTENDED, a
    # name other tools may use, changes nothing.
    run, _ = before.run_words(parse.read(["--attended=1", *args]).before, {}, {})
    assert run.attended is True
    assert before.run_words(parse.read(args).before, {}, {"ATTENDED": "1"})[0].attended is False


@pytest.mark.parametrize("args", SIGNING_IN[:2])
def test_a_run_at_a_terminal_leaves_the_desktop_steps_on(home, args) -> None:
    # At a terminal nothing is asked of a run that names no stage where its names go: it reaches the dispatch, and
    # what the sign-ins do there is theirs (G12).
    rc, output = aw_at_a_terminal(*args, home=home, size=WINDOW, timeout=60)
    said = output.decode(errors="replace")
    assert rc == 1 and dispatch.not_built(VERBS[args[0]]) in said and "Did you mean" not in said, said


def test_destroy_confirm_answers_the_question_for_exactly_the_vms_named() -> None:
    # The names as a set, hyphenated as the names given are: the agents' server passes them once the person confirmed.
    assert _values("destroy", "--confirm=beta,my_vm", "my_vm", "beta") == {
        "confirm": "beta,my_vm",
        "names": ["my-vm", "beta"],
    }
    for args in (
        ["--confirm=alpha", "alpha", "beta"],
        ["--confirm=alpha"],
        ["--confirm=gamma", "alpha"],
        ["--confirm=alpha,beta", "alpha"],
    ):
        assert "--confirm" in _refused("destroy", *args), args
    # Without it, the question is destroy's own (G5).
    assert _values("destroy", "alpha") == {"names": ["alpha"]}


# Each test of today's options' rules (tests/unit/test_justfile.py, from the first to the last of each group below,
# whatever lines they come to), whose twin here has its name.
GROUPS = (
    ("test_deploy_without_a_terminal_takes_a_stage_word_for_neither", "test_code_opens_exactly_one_vm"),
    ("test_an_option_may_come_before_or_after_the_names", "test_start_with_a_run_that_starts_nothing_is_refused"),
    (
        "test_code_passes_a_project_as_its_own_json_extra_var",
        "test_a_run_at_a_terminal_leaves_the_desktop_steps_on",
    ),
)


def _tests_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    return [node.name for node in tree.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]


def test_each_test_of_today_s_options_rules_has_a_twin_here_by_name() -> None:
    today, here = _tests_of(REPO / "tests" / "unit" / "test_justfile.py"), set(_tests_of(Path(__file__)))
    twins = []
    for first, last in GROUPS:
        assert first in today and last in today and today.index(first) < today.index(last), (first, last)
        twins += today[today.index(first) : today.index(last) + 1]
    assert len(twins) > 35 and sorted(set(twins) - here) == []
