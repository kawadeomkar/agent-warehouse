"""The rules between options, as plain functions the one check runs, and the habits of the command line
(lib/claude_vm/schema/rules.py, lib/claude_vm/schema/check.py, lib/claude_vm/cli/habits.py; docs/architecture/engine.md
§16.2 and §16.3).

Every rule of rules.py is named by a verb of the table, and every code a verb names is a rule. Each refusal of a rule or
a habit, and each of the parser's own, is run end to end, through the launcher, at a pseudo-terminal of its own where
deploy's question is answered, and gives the status and the sentence written beside its words here; each case of a
rule is refused by the rule its code names, and each of the parser's own is the sentence the parser gives in this
process. The check gives the same answer to the same values as words and as JSON, and refuses a JSON string where a
number is wanted. deploy's question is asked as a menu at a pseudo-terminal opened through
harness._at_a_pseudo_terminal, typed where the terminal cannot draw one, and refused with no terminal. No rule says back
a value that may be a secret, and one it says back has met its pattern first. No file of the tree names the recorded
contract of v2.7.0's command line, which was taken out of the tree (H26).
"""

from __future__ import annotations

import io
import json
import os

import pytest
from aw_kit import aw_at_a_terminal, on_path, run_aw, run_launcher
from harness import REPO, repo_files, repo_texts

from claude_vm.cli import before, dispatch, habits, parse
from claude_vm.schema import check as checks
from claude_vm.schema import printer, rules
from claude_vm.schema.patterns import form, full
from claude_vm.schema.printer import said
from claude_vm.schema.words import VERBS

DEPLOY = VERBS["deploy"]
# A value a person may have typed where it does not belong: no sentence that need not say a value may say it.
TYPED = "swordfish"
# A --repos value of 21 names, 2079 characters: each name its pattern takes, the whole past check.repos.2's 2000.
LONG_REPOS = ",".join(["r" * 99] * 21)

# Each refusal of a rule between options or of a habit given no terminal, by its rule's code and a number: the words
# after aw, the exit status and the one line it says on standard error. aw on PATH is the launcher, so each command a
# sentence names is aw.
REFUSALS: dict[str, tuple[list[str], int, str]] = {
    "help.1-1": (
        ["help", "deploy", "login"],
        1,
        "error: aw help shows one recipe's options at a time (got: [deploy login])",
    ),
    "init.1-1": (
        ["init", "extra"],
        1,
        "error: aw init takes only its options (aw help init), and nothing after them - never the token secret, which"
        " the command line would put in the shell's history and the process list: put it in a file only you can read,"
        " aw init --token-secret-file <file>, or export AGENT_WAREHOUSE_TOKEN_SECRET from wherever your"
        " secrets live - a CI secret, or read -rs at a prompt",
    ),
    "deploy.1-1": (
        ["deploy", "configure"],
        1,
        "error: configure is a stage of aw deploy and also a name a VM could have, and there is no terminal to ask"
        " which was meant: give a stage as aw deploy --only configure, or a VM so named beside one, as aw deploy"
        " configure --only provision",
    ),
    "deploy.4-1": (
        ["deploy", "alpha", "--tags=common,,tailscale"],
        1,
        "error: --tags names roles separated by single commas (got: [common,,tailscale])",
    ),
    "deploy.5-1": (
        ["deploy", "alpha", "--tags=claude"],
        1,
        "error: --tags may name only common, vscode_server, dev_tools, claude_code, github_projects, tailscale (got:"
        " [claude])",
    ),
    "deploy.6-1": (
        ["deploy", "--only=provision", "--tags=common"],
        1,
        "error: --tags narrows configuration, which --only provision does not run: aw deploy --only configure --tags"
        " common",
    ),
    "deploy.7-1": (
        ["deploy", "--tags=claude_code"],
        1,
        "error: --tags on its own configures the VMs named beside it: aw deploy <name>... --tags claude_code, or every"
        " VM this project created with aw deploy --only configure --tags claude_code",
    ),
    "deploy.8-1": (
        ["deploy", "--check", "--only=configure"],
        1,
        "error: --check is a dry run of configuration, which --only configure is not: aw deploy --check",
    ),
    "deploy.9-1": (
        ["deploy", "--only=template", "alpha"],
        1,
        "error: aw deploy --only template builds the template, which takes no VM names",
    ),
    "deploy.10-1": (
        ["deploy", "--only=configure", "--start"],
        1,
        "error: --start starts a VM the run would leave stopped, which only aw deploy and aw deploy --only provision"
        " start",
    ),
    "deploy.11-1": (
        ["deploy", "--only=configure", "--cores=8"],
        1,
        "error: --cores, --memory and --disk size a VM the run creates, which only aw deploy and aw deploy --only"
        " provision do",
    ),
    "deploy.12-1": (
        ["deploy", "--only=configure", "--repos=my-app"],
        1,
        "error: --repos gives a VM its list as it is provisioned, which only aw deploy and aw deploy --only provision"
        " do",
    ),
    "code.1-1": (["code"], 1, "error: code opens one VM: aw code <name>"),
    "code.2-1": (["code", "alpha", "beta"], 1, "error: code opens one VM at a time, not [alpha beta]"),
    "settings.1-1": (
        ["settings", "vm_cores", "vm_memory_mb"],
        1,
        "error: aw settings takes one role or variable, and --value its value",
    ),
    "settings.2-1": (
        ["settings", "--all", "tailscale"],
        1,
        "error: aw settings --all lists every setting: give it no role or variable",
    ),
    "settings.3-1": (
        ["settings", "--value=4"],
        1,
        "error: --value and --reset each change the one setting named beside it: aw settings <name> --value <value>, or"
        " --reset",
    ),
    "settings.4-1": (
        ["settings", "vm_cores", "--value=4", "--reset"],
        1,
        "error: --value sets a value and --reset takes one out: one or the other",
    ),
    "destroy.1-1": (
        ["destroy", "--confirm=alpha"],
        1,
        "error: --confirm answers for the VMs named beside it: aw destroy <name>... --confirm <names>",
    ),
    "destroy.2-1": (
        ["destroy", "--confirm=alpha", "beta"],
        1,
        "error: --confirm names alpha, but the VMs given are beta: nothing was deleted",
    ),
    "host.1-1": (["host", "nuc", "lab"], 1, "error: aw host takes one host's name (got: [nuc lab])"),
    "host.2-1": (["host", "--json", "nuc"], 1, "error: --json lists the hosts: give it no name"),
    "host.3-1": (
        ["host", "--for=alpha"],
        1,
        "error: --for and --host say which host a run would reach, in the list: give them with --json",
    ),
    "host.4-1": (
        ["host", "--remove"],
        1,
        "error: --remove deletes the host named beside it: aw host <name> --remove",
    ),
    "mcp.1-1": (["mcp", "extra"], 1, "error: aw mcp takes only its options (aw help mcp), and nothing after them"),
    "mcp.2-1": (
        ["mcp", "--remove", "--show"],
        1,
        "error: --remove takes the server out and --show prints how it is added: one or the other",
    ),
    "check.names.1-1": (
        ["host", "--json", "--for=alpha,-b"],
        1,
        "error: VM names start with a letter or a digit and may contain only letters, digits and . _ - , (got: [-b])",
    ),
    "check.repos.2-1": (
        ["deploy", "alpha", f"--repos={LONG_REPOS}"],
        1,
        "error: --repos is longer than the 2000 characters it may be",
    ),
    "check.project.2-1": (
        ["code", "alpha", "--project=.."],
        1,
        "error: --project names one project folder, not [..]",
    ),
}
# The program, as a field of the parser's sentences below: each says aw and then the verb's words, which the check of
# every aw command a file names (test_commands.py) would read as a command line those words break.
AW = "aw"
# The parser's own refusals, before any rule, as REFUSALS are written: an option the verb has not got, a value outside
# its pattern, an option given twice. Each names the verb and the option, and never the value.
PARSER_REFUSALS: dict[str, tuple[list[str], int, str]] = {
    "unknown-option-1": (
        ["deploy", "--bogus"],
        1,
        f"error: {AW} deploy has no --bogus option (aw help deploy lists them)",
    ),
    "pattern-1": (
        ["deploy", "--cores=0"],
        1,
        f"error: {AW} deploy --cores takes no such value: Each new VM's vCPU cores, in place of the vm_cores setting"
        " [a whole number from 1 to 999, e.g. 8]",
    ),
    "pattern-2": (
        ["deploy", "--only=check"],
        1,
        f"error: {AW} deploy --only takes no such value: Run one stage and nothing after it: template (the cloud-init"
        " template, if missing), provision (create and start), configure (every tagged VM, unless named) or ssh-config"
        " (refresh the SSH aliases; without names, also prune those of VMs that are gone)"
        " [one of template, provision, configure, ssh-config]",
    ),
    "pattern-3": (
        ["destroy", "alpha;beta"],
        1,
        f"error: {AW} destroy takes no such name: The VMs; none means agent-warehouse-default"
        " [a VM's name: letters, digits, dots, underscores and hyphens, starting with a letter or a digit, e.g. alpha]",
    ),
    "repeated-option-1": (["deploy", "--cores=8", "--cores=4", "alpha"], 1, f"error: {AW} deploy takes --cores once"),
    "repeated-option-2": (["deploy", "--check", "--check"], 1, f"error: {AW} deploy takes --check once"),
}
# Each refusal at a terminal, by its rule's code and a number: the words, what is typed at deploy's question, the exit
# status and the sentence the run ends with.
AT_A_TERMINAL: dict[str, tuple[list[str], bytes, int, str]] = {
    "deploy.2-1": (
        ["deploy", "configure", "check"],
        b"s\n",
        1,
        "One stage runs at a time, given with --only: nothing was run.",
    ),
    "deploy.3-1": (["deploy", "configure"], b"\n", 1, "Nothing was run."),
}
# The recorded contract of v2.7.0's command line, which H26 took out of the tree (the plan keeps it as a reference):
# nothing holds aw or today's justfile to it. Spelled in two parts, so that this file does not name it.
GONE = "_".join(("cli", "contract"))


def _rule_of(case: str) -> str:
    return case.rsplit("-", 1)[0]


# ---------------------------------------------------------------- the rules ----


def test_every_rule_is_named_by_the_verbs_whose_rows_run_it_and_every_code_a_verb_names_is_a_rule() -> None:
    named: dict[str, set[str]] = {}
    for verb in VERBS.values():
        for code in verb.rules:
            named.setdefault(code, set()).add(verb.name)
    assert sorted(set(named) - set(rules.RULES)) == [], "a verb names a rule that rules.py has not got"
    assert {code: set(rule.verbs) for code, rule in rules.RULES.items()} == named
    for verb in VERBS.values():
        assert len(set(verb.rules)) == len(verb.rules), verb.name


def test_each_rule_and_habit_refuses_in_a_case_of_its_own() -> None:
    # Every rule of rules.py and every habit's refusal has a case run end to end below.
    cases = {_rule_of(code) for code in (*REFUSALS, *AT_A_TERMINAL)}
    assert sorted(set(rules.RULES) - cases) == [], "a rule with no case: add one to REFUSALS"
    assert sorted(cases - set(rules.RULES)) == ["deploy.1", "deploy.2", "deploy.3"]


@pytest.mark.parametrize("code", [code for code in REFUSALS if _rule_of(code) in rules.RULES])
def test_each_case_is_refused_by_the_rule_its_code_names(monkeypatch, code) -> None:
    # Given the case's values, the rule its code names says the case's sentence: a case relabelled with another rule's
    # words and sentence fails here, so every rule's case is its own.
    monkeypatch.setattr(printer, "_program", printer.PROGRAM)
    words, _, sentence = REFUSALS[code]
    _, values = _parsed(monkeypatch, words)
    assert rules.RULES[_rule_of(code)].check(values) == sentence


def test_no_rule_says_back_a_value_that_may_be_a_secret() -> None:
    # settings --value's value, and a word after init's or mcp's options, which may be a secret.
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


# ---------------------------------------------------------------- each refusal, end to end ----


@pytest.mark.parametrize("code", list(REFUSALS))
def test_each_refusal_gives_its_status_and_sentence_through_the_launcher(home, code) -> None:
    # aw on PATH is the launcher, so each command a sentence names is aw.
    words, status, sentence = REFUSALS[code]
    result = run_launcher(*words, home=home, cwd=home, first_on_path=(on_path(home),))
    assert (result.returncode, result.stdout, result.stderr) == (status, "", sentence + "\n")


@pytest.mark.parametrize("code", list(PARSER_REFUSALS))
def test_each_refusal_of_the_parser_s_own_gives_its_status_and_sentence_through_the_launcher(
    home, monkeypatch, code
) -> None:
    words, status, sentence = PARSER_REFUSALS[code]
    result = run_launcher(*words, home=home, cwd=home, first_on_path=(on_path(home),))
    assert (result.returncode, result.stdout, result.stderr) == (status, "", sentence + "\n")
    # And it is the parser's own sentence, in this process too.
    monkeypatch.setattr(printer, "_program", printer.PROGRAM)
    with pytest.raises(parse.Refused) as refused:
        parse.read(words)
    assert refused.value.sentence == sentence


def _naming(files: list[tuple[str, str]]) -> list[str]:
    """The files of files, (name, text), whose name or text names GONE."""
    return [name for name, text in files if GONE in name or GONE in text]


def test_no_file_of_the_tree_names_the_recorded_contract_of_today_s_command_line() -> None:
    texts = {str(path.relative_to(REPO)): text for path, text in repo_texts(untracked=True)}
    listed = [str(path.relative_to(REPO)) for path in repo_files(untracked=True)]
    assert listed
    assert _naming([(name, texts.get(name, "")) for name in listed]) == []
    # A reader of it put back is found, by its name or by what it reads.
    put_back = [(f"tests/unit/{GONE}.json", "{}"), ("tests/unit/test_aw_x.py", f'json.loads(Path("{GONE}.json"))')]
    assert _naming(put_back) == [name for name, _ in put_back]


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
    "words",
    [pytest.param(words, id=code) for code, (words, _, _) in REFUSALS.items() if _rule_of(code) in rules.RULES]
    # An option the verb has not got, or given twice, is the parser's refusal, before any value reaches the check.
    + [pytest.param(words, id=code) for code, (words, _, _) in PARSER_REFUSALS.items() if code.startswith("pattern-")],
)
def test_the_check_gives_the_same_answer_to_the_same_values_as_words_and_as_json(monkeypatch, words) -> None:
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


def test_the_question_names_each_answer_s_command_line_as_the_printer_writes_it() -> None:
    # The two lines are the printer's (lib/claude_vm/schema/printer.py): the names as words straight after the verb,
    # every option given after them as --long value, and a value of --set never said back, since nothing took it yet.
    words = ["deploy", "alpha", "configure", "--tags", "claude_code", "--set", f"github_user={TYPED}", "-v"]
    _, said = _typed(words, "v\n")
    given = {"tags": "claude_code", "set": [f"github_user={TYPED}"], "verbosity": 1}
    stage = printer.line("deploy", {**given, "only": "configure", "names": ["alpha"]}, host_flag="", refused=True)
    vms = printer.line("deploy", {**given, "names": ["alpha", "configure"]}, host_flag="", refused=True)
    assert said.splitlines() == [
        "configure is a stage of aw deploy and also a name a VM could have. Did you mean",
        f"  s  the stage: {stage}",
        f"  v  a VM named configure, created if there is none: {vms}",
        "s or v, or Enter to run nothing: ",
    ]
    masked = "<name>=<value>"
    assert stage == f"aw deploy alpha --only configure --tags claude_code --set {masked} -v"
    assert vms == f"aw deploy alpha configure --tags claude_code --set {masked} -v"
    assert TYPED not in said
    # And with no --set: each answer's command line in full.
    _, said = _typed(["deploy", "alpha", "configure", "--tags", "claude_code"], "v\n")
    assert "  s  the stage: aw deploy alpha --only configure --tags claude_code" in said.splitlines()
    assert "  v  a VM named configure, created if there is none: aw deploy alpha configure --tags claude_code" in (
        said.splitlines()
    )


def test_a_name_no_line_can_give_is_refused_at_the_question() -> None:
    # The names habit splits alpha,-b into a piece that is no name: the question's lines cannot give it, so it is
    # refused there in the check's sentence, saying nothing typed, and nothing is asked.
    sentence, said = _typed(["deploy", "alpha,-b", "configure"], "v\n")
    assert sentence == checks.check("deploy", {"names": ["-b"]}) and said == ""


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


@pytest.mark.parametrize("code", list(AT_A_TERMINAL))
def test_each_refusal_at_a_terminal_gives_its_status_and_sentence(home, code) -> None:
    # deploy.2's s picks no stage from a menu that offers none where two were given: it is typed, at a terminal that
    # cannot draw one; Enter (deploy.3) runs nothing either way.
    words, typed, status, sentence = AT_A_TERMINAL[code]
    for menu in (False, True) if code == "deploy.3-1" else (False,):
        rc, said = _at_the_question(home, words, typed.replace(b"\n", b"\r") if menu else typed, menu=menu)
        assert rc == status and said.rstrip("\n").endswith(sentence), said


def test_deploy_asks_and_takes_a_stage_word_for_a_vm_name_when_that_was_meant(home) -> None:
    rc, said = _at_the_question(home, ["deploy", "alpha", "configure"], b"v\r")
    assert "configure is a stage of aw deploy and also a name a VM could have. Did you mean" in said
    assert "s  the stage: aw deploy alpha --only configure" in said
    assert "v  a VM named configure, created if there is none: aw deploy alpha configure" in said
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
    assert "  s  the stage: aw deploy alpha --only configure" in said
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
    assert "or a VM so named beside one, as aw deploy " in result.stderr and " --only provision" in result.stderr


def test_check_refused_as_a_name_points_at_the_dry_run_option() -> None:
    sentence = _refused("deploy", "check")
    assert "give a stage as aw deploy --check," in sentence and "--only check" not in sentence


def test_code_opens_exactly_one_vm() -> None:
    assert _values("code", "alpha") == {"name": "alpha"}
    assert _refused("code") == rules.RULES["code.1"].check({}) == "error: code opens one VM: aw code <name>"
    assert _refused("code", "alpha", "beta") == "error: code opens one VM at a time, not [alpha beta]"
    assert _refused("code", "alpha", "list") == "error: code opens one VM at a time, not [alpha list]"
    assert _refused("code", "alpha,beta") == "error: code opens one VM at a time, not [alpha,beta]"


# ---------------------------------------------------------------- the rules first, then the habits ----


def test_an_empty_word_after_init_s_options_is_refused(home) -> None:
    # init takes nothing after its options, an empty word too.
    result = run_launcher("init", "", home=home, cwd=home, first_on_path=(on_path(home),))
    sentence = rules.RULES["init.1"].check({"extra": ""})
    assert (result.returncode, result.stdout, result.stderr) == (1, "", f"{sentence}\n")
    assert _refused("init", "") == sentence


def test_a_stage_where_the_names_go_beside_a_rule_broken_says_the_rule(home) -> None:
    # The rules between options are held before anything is asked, so a stage given where the names go on a line that
    # breaks one is refused by the rule, with no terminal as at one.
    words = ("deploy", "configure", "--tags", "claude_code", "--start")
    start = rules.RULES["deploy.10"].check({"names": ["configure"], "tags": "claude_code", "start": True})
    result = run_launcher(*words, home=home, cwd=home, first_on_path=(on_path(home),))
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
    # The one check refuses before the names are taken with - and said, so a line that breaks a rule says the rule's
    # sentence alone.
    result = run_launcher(*words, home=home, cwd=home)
    assert (result.returncode, result.stdout, result.stderr) == (1, "", rules.RULES[code].check(values) + "\n")
    # Where no rule is broken, the warning is said.
    kept = run_launcher("deploy", "my_vm", home=home, cwd=home)
    assert kept.stderr.splitlines()[0] == habits.HYPHENATED.format(name="my_vm", changed="my-vm")


def test_a_token_file_relative_to_a_folder_whose_path_has_a_space_is_taken(home) -> None:
    # The file's name is held to its pattern as typed; making it absolute from the working folder is init's own (E4),
    # and holds it to nothing more.
    assert "init.4" not in VERBS["init"].rules and "init.4" not in rules.RULES
    assert parse.read(["init", "--token-secret-file=secret"]).values == {"token_secret_file": "secret"}
    assert checks.check("init", {"token_secret_file": "secret"}) is None
    # Through the launcher, from such a folder: the words reach the dispatch.
    spaced = home / "a b"
    spaced.mkdir()
    result = run_launcher("init", "--token-secret-file=secret", home=home, cwd=spaced, first_on_path=(on_path(home),))
    assert (result.returncode, result.stdout, result.stderr) == (1, "", dispatch.not_built(VERBS["init"]) + "\n")


def test_host_for_s_names_are_split_at_its_commas_and_an_empty_one_is_dropped() -> None:
    # As today's just host splits --for before tools/check names, whose shell drops an empty word.
    assert checks.check("host", {"json": True, "for_": "alpha,,beta"}) is None
    assert checks.check("host", {"json": True, "for_": "alpha,-b"}) == rules.RULES["check.names.1"].check(
        {"for_": "alpha,-b"}
    )
    assert rules.RULES["check.names.1"].check({"for_": "alpha,-b"}).endswith("(got: [-b])")


# ---------------------------------------------------------------- each option's rules ----

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
    # The option's help, and what its value may be in words (H25; the owner's ruling, review/code/OWNER-9.md 2).
    helped = f"{said(option.help or '')} {form(option.pattern or '')}"
    return f"error: aw {verb} {checks.spelled(option)} takes no such value: {helped}"


def test_an_option_may_come_before_or_after_the_names() -> None:
    after = _values("deploy", "alpha", "--tags", "claude_code")
    assert after == _values("deploy", "--tags=claude_code", "alpha") == _values("deploy", "-t", "claude_code", "alpha")
    assert after == {"names": ["alpha"], "tags": "claude_code"}


@pytest.mark.parametrize("only", ["", *SIGN_INS])
def test_login_hands_on_the_sign_in_named_or_none_for_all_three(only: str) -> None:
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
        pytest.param(["test", "lint"], checks.no_such_command(), id="lint-is-its-own"),
        pytest.param(["status", "--only", "x"], _outside("status", "only"), id="status-section"),
        pytest.param(["destroy", "--project", "repo"], checks.no_option("destroy", "--project"), id="destroy"),
        pytest.param(["deploy", "alpha", "TAGS=claude_code"], "takes no such name", id="make-s-spelling"),
    ],
)
def test_an_option_is_checked_before_anything_runs(args: list[str], said: str) -> None:
    assert said in _refused(*args)


def test_a_setting_before_the_verb_is_no_word_of_aw_s() -> None:
    # A setting before the verb, make's TYPE among them, is no word of aw's: taken for the verb, which the table has not
    # got. Spelled in two parts, so the scan for make's variables left in the sources does not take it for one.
    assert _refused("=".join(("TYPE", "configure")), "deploy") == checks.no_such_command()


def test_template_takes_no_names() -> None:
    assert _refused("deploy", "alpha", "--only", "template") == rules.RULES["deploy.9"].check(
        {"only": "template", "names": ["alpha"]}
    )


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
def test_code_takes_a_project(value: str) -> None:
    assert _values("code", "alpha", "--project", value) == {"name": "alpha", "project": value}


def test_code_without_its_options_takes_none_of_them() -> None:
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
def test_code_refuses_a_bad_project(value: str, said: str) -> None:
    assert _refused("code", "alpha", "--project", value) == said


def test_code_takes_an_editor() -> None:
    assert _values("code", "alpha", "--editor", "vscode") == {"name": "alpha", "editor": "vscode"}
    assert _values("code", "alpha", "--editor", "zed") == {"name": "alpha", "editor": "zed"}


@pytest.mark.parametrize("value", ["emacs", "VSCode", "vscode,zed", '"}', "$(x)", "-e"])
def test_code_refuses_an_editor_it_cannot_open(value: str) -> None:
    sentence = _refused("code", "alpha", "--editor", value)
    assert sentence.startswith("error: aw code --editor ") and (value not in sentence or value == "-e"), sentence


def test_code_takes_a_session() -> None:
    assert _values("code", "alpha", "--project", "repo", "--session", SESSION_ID) == {
        "name": "alpha",
        "project": "repo",
        "session": SESSION_ID,
    }


@pytest.mark.parametrize("value", BAD_SESSIONS)
def test_code_refuses_a_bad_session(value: str) -> None:
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
def test_a_run_is_unattended_whatever_the_environment_says(args) -> None:
    # Whether a terminal is there is the sign-ins' own to read (G12): aw hands on that nobody said someone is at the
    # screen, whatever the environment says.
    reading = parse.read(args)
    run, values = before.run_words(reading.before, _values(*args), {"ATTENDED": "1"})
    assert run.attended is False and values == _values(*args)


@pytest.mark.parametrize("args", SIGNING_IN)
def test_attended_before_the_verb_makes_a_run_attended_and_an_exported_one_does_not(args) -> None:
    # --attended=1 before the verb, as the agents' server gives an interactive session's run; an exported ATTENDED, a
    # name other tools may use, changes nothing.
    run, _ = before.run_words(parse.read(["--attended=1", *args]).before, {}, {})
    assert run.attended is True
    assert before.run_words(parse.read(args).before, {}, {"ATTENDED": "1"})[0].attended is False


@pytest.mark.parametrize("args", SIGNING_IN[:2])
def test_a_run_at_a_terminal_reaches_the_dispatch_unasked(home, args) -> None:
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
