"""The printer of a command (lib/claude_vm/cli/inverse.py; docs/architecture/engine.md §16.3): the one way the new
engine writes the command line a person types, the program as it was started and a verb's values, from the one table.

For every verb of the table, its words, each option alone and beside the words, and each pair of options the rules
between options allow, with the words and without, are printed, parsed again by aw's parser and held by the one check,
and come back as they went in; and no verb, option or verb's words of the table lacks such a case. Every example line
of the contract of today's command line that aw takes is parsed, printed and parsed again to the same values, before
today's habits and after them. A line is the program, the verb, its words, then its options, a value after a space but
where it starts with - or is empty. A value with a space, a quote, a leading -, each character a POSIX shell reads,
a backslash and a trailing one, two values of free text on one line, an empty value and a name that starts with a digit
survive the trip, through shlex, /bin/sh and bash; a value a terminal would not show as it is is never printed. A line
made from values no run has taken never says a value of --set or settings --value back, and every call of the printer
says which it is. A value that is off is not said, and a placeholder is the name help gives it. The host's flag is
given by every caller of a verb with the shared --host, and only a verb whose --host runs it against a host takes one.
A value the one check refuses is never printed, and a rule between options is the run's to say. Every line, and every
command a sentence names, is the program as it was started: aw where aw on PATH is the launcher, else what was typed,
through the launcher; the one check's own sentences and the rules' among them. No other module of the new engine's
subpackages builds a string that names a command, aw or just and a verb anywhere in its text, outside the printer, and
only the printer and help name its PROGRAM. The printer imports nothing outside the standard library and schema/, and
schema/, pve/ and vendors/ import nothing of cli/ when they are imported.
"""

from __future__ import annotations

import ast
import itertools
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import cli_contract as contract
import pytest
from aw_kit import LAUNCHER, imports_of, on_path, run_aw, run_launcher
from harness import REPO
from test_aw_cli_parse import SAMPLES
from test_aw_cli_words import SECRET_TEXT
from test_aw_layout import NEW

from claude_vm.cli import habits, inverse, main, parse
from claude_vm.schema import check as checks
from claude_vm.schema import rules
from claude_vm.schema.patterns import full
from claude_vm.schema.words import BEFORE, LINES, PLACEHOLDER, VERBS, Line, Option, Verb, Words, said
from claude_vm.schema.words import HOST as SHARED_HOST

CONTRACT = json.loads((Path(__file__).resolve().parent / "cli_contract.json").read_text())
# A value with a space, a quote and a leading -, which a value of free text may be: given as --long=value, quoted.
AWKWARD = '-it\'s "two" words'
# A value a person may have pasted where it does not belong: a line made from values no run took never says it.
TYPED = "swordfish"
# A VM's name that starts with a digit, as one may.
DIGIT_FIRST = "2nd-vm"
MODULE = "claude_vm.cli.inverse"
# The verbs whose row has the shared --host, which every caller of the printer gives a host's flag.
SHARED = ("deploy", "code", "login", "status", "destroy")


def _line(verb: str, values: dict, **given) -> str:
    """The printer's line made from a run's own values, naming no host where the verb takes the shared one."""
    return inverse.line(verb, values, **{"host_flag": "", "refused": False, **given})


def _argv(verb: str, values: dict, **given) -> list[str]:
    return inverse.argv(verb, values, **{"host_flag": "", "refused": False, **given})


def _samples(option: Option) -> list[object]:
    """The values option is given in the round trip: one its pattern takes, and where its pattern takes them, the
    awkward one, an empty one and a name that starts with a digit; a count once and more; a value or nothing alone."""
    if option.kind == "flag":
        return [True]
    if option.kind == "counted":
        return [1, 3]
    pattern = option.pattern or ""
    sample = SAMPLES[pattern]
    more = {
        "text": [AWKWARD],
        "assignment": [f"vm_note={AWKWARD}"],
        "vm_names": [f"{DIGIT_FIRST},alpha"],
        "host_name": ["9lab"],
    }.get(pattern, [])
    if option.kind == "repeatable":
        return [[sample], [sample, *more]]
    return [sample, *more, *([""] if full(pattern, "") else [])]


def _word_samples(words: Words) -> list[object]:
    """The words a verb is given in the round trip: one or several as it takes them, a name that starts with a digit
    among them; none where it takes none (init's, whose pattern is none)."""
    if words.pattern is None:
        return []
    singles = {
        "vm_name": ["alpha", DIGIT_FIRST],
        "text": ["two words", 'it\'s "two" words'],
    }.get(words.pattern, [SAMPLES[words.pattern]])
    return [singles] if words.plural else singles


def _cases(verb: Verb) -> list[dict[str, object]]:
    """Every case of verb's round trip: the verb alone; its words; each option alone and beside the words; and each
    pair of options the rules between options allow, with the words and without."""
    options = checks.options_of(verb)
    words = [{verb.words.field: given} for given in _word_samples(verb.words)] if verb.words else []
    cases: list[dict[str, object]] = [{}, *words]
    for option in options:
        for value in _samples(option):
            cases += [{option.field: value}, *({option.field: value, **given} for given in words[:1])]
    for first, second in itertools.combinations(options, 2):
        for given in [{}, *words[:1]]:
            case = {first.field: _samples(first)[0], second.field: _samples(second)[0], **given}
            if checks.check(verb.name, case) is None:
                cases.append(case)
    return cases


def _read(words: list[str], monkeypatch, *, with_rules: bool) -> parse.Reading:
    """aw's parser on words, the rules between options run or not: a case a rule refuses still parses."""
    if with_rules:
        return parse.read(words)
    with monkeypatch.context() as patched:
        patched.setattr(rules, "run", lambda codes, values: None)
        return parse.read(words)


def _round_trip(verb: str, values: dict[str, object], monkeypatch) -> None:
    """values printed, read by a shell as the person's would be, parsed again and held by the one check: as they went
    in, and the check's answer to both the same."""
    printed = _argv(verb, values)
    assert printed[:1] == ["aw"] and shlex.split(_line(verb, values)) == printed, (verb, values)
    takes = checks.check(verb, values)
    reading = _read(printed[1:], monkeypatch, with_rules=takes is None)
    assert (reading.verb, reading.before, reading.values) == (verb, {}, values), printed
    assert checks.check(verb, reading.values) == takes


# ---------------------------------------------------------------- the round trip, verb by verb ----


@pytest.mark.parametrize("name", list(VERBS))
def test_every_verb_s_values_come_back_from_its_line(name: str, monkeypatch) -> None:
    for values in _cases(VERBS[name]):
        _round_trip(name, values, monkeypatch)


def test_no_verb_option_or_word_of_the_table_lacks_a_round_trip_case() -> None:
    for name, verb in VERBS.items():
        cases = _cases(verb)
        given = {field for case in cases for field in case}
        fields = {option.field for option in checks.options_of(verb)}
        if verb.words is not None and verb.words.pattern is not None:
            fields.add(verb.words.field)
        assert given == fields and len(cases) > 1, name
        if len(checks.options_of(verb)) > 1:
            pairs = [case for case in cases if len(set(case) - {verb.words.field if verb.words else ""}) == 2]
            assert pairs, f"{name}: no pair of options the rules allow"


def test_the_words_before_the_verb_come_back_from_the_line() -> None:
    for before in [{"attended": "1"}, {"detached_run": True}]:
        printed = _argv("deploy", {"names": ["alpha"]}, before=before)
        assert parse.read(printed[1:]) == parse.Reading("deploy", before, {"names": ["alpha"]})
    everything = {option.field: SAMPLES.get(option.pattern or "", True) for option in BEFORE}
    printed = _argv("status", {}, before=everything)
    assert printed == ["aw", "--attended", "1", "--detached-run", "status"]
    assert parse.read(printed[1:]).before == everything


# The examples of the contract aw takes, at least: as many as when the printer was built (the contract keeps its
# examples and only adds), so their round trip is not vacuous, and a change that makes aw refuse one shows here.
EXAMPLES_TAKEN = 431


def _examples():
    """Each command of the contract's examples whose verb is the table's and that gives no setting before it: its line
    and its words, aw's verb first."""
    for example in CONTRACT["examples"]:
        for _, _, settings, recipe, words in contract.matches(example["line"]):
            if not settings and recipe in VERBS:
                yield example["line"], [recipe, *words]


def test_every_example_of_the_contract_aw_takes_comes_back_from_its_line() -> None:
    # An example aw refuses (prose read as words, a <placeholder>, a word aw has otherwise) is the parity suite's to
    # compare, not the printer's.
    taken = 0
    for line, words in _examples():
        try:
            reading = parse.read(words)
        except parse.Refused:
            continue
        taken += 1
        printed = _argv(reading.verb, reading.values, before=reading.before)
        assert parse.read(printed[1:]) == reading, (line, printed)
        assert shlex.split(_line(reading.verb, reading.values, before=reading.before)) == printed
        # And after today's habits, as a handler receives the values: names split and with - for _, no names said.
        verb = VERBS[reading.verb]
        try:
            values, _ = habits.names(verb, reading.values)
        except parse.Refused:
            continue
        again = parse.read(_argv(reading.verb, values)[1:])
        assert habits.names(verb, again.values)[0] == values, (line, values)
    assert taken >= EXAMPLES_TAKEN, taken


# ---------------------------------------------------------------- the form of a line ----


def test_a_line_is_the_program_the_verb_its_words_then_its_options_in_the_table_s_order() -> None:
    values = {
        "names": ["alpha", DIGIT_FIRST],
        "detach": True,
        "verbosity": 2,
        "set": ["vm_cores=4", "vm_memory=8G"],
        "tags": "claude_code",
        "only": "configure",
    }
    # As a person types it: the words straight after the verb, a value after a space.
    assert _line("deploy", values) == (
        "aw deploy alpha 2nd-vm --only configure --tags claude_code --set vm_cores=4 --set vm_memory=8G -vv --detach"
    )
    assert _line("login", {"names": ["alpha"], "only": "claude"}) == "aw login alpha --only claude"
    # deploy's whole chain is its one verb, and no names is said by none, whatever the habits made of it.
    assert _line("deploy", {"names": ["alpha"]}) == "aw deploy alpha"
    assert _line("deploy", {"default_vm": True}) == "aw deploy"
    assert _line("status", {"default_vm": False}) == "aw status"
    assert _argv("deploy", {"default_vm": True, "cores": None}) == ["aw", "deploy"]
    assert _line("help", {"recipe": "deploy"}) == "aw help deploy"
    # A value that starts with - or is empty is given as --long=value, which the parser reads as a value.
    assert _line("deploy", {"cores": "", "names": ["alpha"]}) == "aw deploy alpha --cores="
    assert _argv("deploy", {"cores": "", "names": ["alpha"]}) == ["aw", "deploy", "alpha", "--cores="]
    assert _line("settings", {"name": "vm_note", "value": "-x"}) == "aw settings vm_note --value=-x"
    assert _argv("settings", {"name": "vm_note", "value": "-x y"}) == ["aw", "settings", "vm_note", "--value=-x y"]
    for printed in (
        _argv("deploy", values),
        _argv("settings", {"name": "vm_note", "value": "-x y"}),
        _argv("deploy", {"cores": "", "names": ["alpha"]}),
    ):
        reading = parse.read(printed[1:])
        assert _argv(reading.verb, reading.values) == printed


def test_a_value_that_is_off_is_not_said() -> None:
    # The check takes each, and the parser gives each back as not given: a line says none of them.
    for verb, values in [
        ("status", {"json": False}),
        ("deploy", {"set": []}),
        ("deploy", {"names": []}),
        ("deploy", {"verbosity": 0}),
        ("deploy", {"start": False, "detach": False}),
    ]:
        assert checks.formed(verb, values) is None, values
        printed = _argv(verb, values)
        assert printed == ["aw", verb] and parse.read(printed[1:]).values == {}, values
    assert _argv("status", {}, before={"detached_run": False}) == ["aw", "status"]
    # A value of another type is no value that is off: refused, as the check refuses it.
    for values in ({"cores": False}, {"check": 0}, {"tags": []}):
        with pytest.raises(ValueError):
            _argv("deploy", values)


def test_a_placeholder_is_the_name_help_gives_it() -> None:
    assert _line("destroy", {"names": PLACEHOLDER, "confirm": PLACEHOLDER}) == "aw destroy <name>... --confirm CONFIRM"
    assert _line("code", {"name": PLACEHOLDER}) == "aw code <name>"
    assert _line("settings", {"name": PLACEHOLDER, "value": PLACEHOLDER}) == "aw settings <name> --value VALUE"
    assert _line("help", {"recipe": PLACEHOLDER}) == "aw help <recipe>"
    assert _line("deploy", {"names": PLACEHOLDER, "set": PLACEHOLDER}) == "aw deploy <name>... --set NAME=VALUE"
    assert _line("host", {"name": PLACEHOLDER, "remove": True}) == "aw host <name> --remove"
    # A flag and a count have no value to name; and a placeholder is never a value the one check takes.
    for values in ({"check": PLACEHOLDER}, {"verbosity": PLACEHOLDER}):
        with pytest.raises(ValueError):
            _line("deploy", values)
    assert checks.check("code", {"name": PLACEHOLDER}) is not None
    assert checks.check("settings", {"name": "vm_note", "value": PLACEHOLDER}) is not None


def test_the_line_ends_with_the_host_s_flag_which_every_caller_of_a_verb_with_the_shared_host_gives() -> None:
    assert {name for name, verb in VERBS.items() if SHARED_HOST in verb.options} == set(SHARED)
    assert _line("code", {"name": "alpha", "project": "app"}, host_flag="lab2") == (
        "aw code alpha --project app --host lab2"
    )
    printed = _argv("code", {"name": "alpha"}, host_flag="lab2")
    assert printed == ["aw", "code", "alpha", "--host", "lab2"]
    assert parse.read(printed[1:]).values == {"name": "alpha", "host": "lab2"}
    # "": one host, or a line that names none; a --host in the values needs no flag.
    assert _line("deploy", {"names": ["alpha"]}) == "aw deploy alpha"
    assert inverse.line("code", {"name": "alpha", "host": "lab2"}, refused=False) == "aw code alpha --host lab2"
    # None, on a verb with the shared --host whose values give none: refused, so no caller forgets the host.
    for verb in SHARED:
        for printer in (inverse.line, inverse.argv):
            with pytest.raises(ValueError):
                printer(verb, {}, refused=False)
    # settings, vault and init run against the host their --host names: a flag there, and none for None.
    for verb in inverse.HOSTS_OWN:
        assert inverse.line(verb, {}, refused=False) == f"aw {verb}"
        printed = inverse.argv(verb, {}, host_flag="lab2", refused=False)
        assert printed == ["aw", verb, "--host", "lab2"] and parse.read(printed[1:]).values == {"host": "lab2"}
    # A verb with no --host; host's, which says which host a run would reach; one given --host already; a name no host
    # has; today's spelling of the flag as one string; and init's --host, a host's name alone (a value that starts
    # with -, which once printed a line the parser refused).
    for verb, values, given in [
        ("release", {"name": "alpha"}, "lab2"),
        ("logs", {}, "lab2"),
        ("host", {"json": True}, "lab2"),
        ("code", {"name": "alpha", "host": "lab2"}, "lab2"),
        ("code", {"name": "alpha"}, "Lab_2"),
        ("code", {"name": "alpha"}, " --host lab2"),
        ("init", {}, "-x"),
    ]:
        for printer in (inverse.line, inverse.argv):
            with pytest.raises(ValueError):
                printer(verb, values, host_flag=given, refused=False)


# ---------------------------------------------------------------- values a shell must read as given ----

# Each character a POSIX shell reads otherwise than as itself, in a word or at its start: a pasted line must give it
# back as typed, never run a second command, expand or glob.
SHELL = ["$(id)", "${x}", "`id`", ";", "|", "&", ">", "*", "?", "[a]", "~", "!", "#", "\\", "'", '"', " "]
AWKWARD_LINES = [
    ("settings", {"name": "vm_note", "value": AWKWARD}),
    ("settings", {"name": "vm_cores", "value": "--"}),
    ("deploy", {"names": [DIGIT_FIRST], "set": [f"vm_note={AWKWARD}"], "cores": ""}),
    ("destroy", {"names": [DIGIT_FIRST, "alpha"], "confirm": f"{DIGIT_FIRST},alpha"}),
    ("mcp", {"browser": AWKWARD}),
    ("code", {"name": DIGIT_FIRST}),
    # A backslash, a trailing one, and two values of free text on one line, one ending in a backslash.
    ("settings", {"name": "vm_note", "value": "a\\b\\"}),
    ("deploy", {"names": ["alpha"], "set": ["vm_note=a\\", "vm_x=;touch p;#"]}),
    ("mcp", {"browser": "a'b\\", "project-dir": "~/a b", "vault_key_file": "/k'\\"}),
    ("deploy", {"names": ["alpha"], "set": ["vm_note=a;echo${IFS}SECOND-COMMAND-RAN"]}),
    *(("settings", {"name": "vm_note", "value": value}) for c in SHELL for value in (c, f"a{c}b", f"{c}a")),
    *(("deploy", {"names": ["alpha"], "set": [f"vm_note={c}", f"vm_x=a{c}"]}) for c in SHELL),
]


def _words_read(shell: str, line: str) -> list[str]:
    """The words a shell reads line into, the program among them: printf says each on a line of its own, none of them
    holding a newline, which no pattern takes."""
    ran = subprocess.run(
        [shell, "-c", f"printf '%s\\n' {line}"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return ran.stdout.split("\n")[:-1]


@pytest.mark.parametrize(("verb", "values"), AWKWARD_LINES)
def test_a_value_any_posix_shell_reads_otherwise_survives_the_shell(verb, values, monkeypatch) -> None:
    values = {key.replace("-", "_"): value for key, value in values.items()}
    printed = _argv(verb, values)
    for shell in ("/bin/sh", "bash"):
        assert _words_read(shell, _line(verb, values)) == printed, shell
    _round_trip(verb, values, monkeypatch)


@pytest.mark.parametrize("character", ["\x85", "\x9b", "\u200b", "\u202e", "\u2028", "\u2029", "\ufeff", "\xad"])
def test_a_value_a_terminal_would_not_show_as_it_is_is_never_printed(character: str) -> None:
    # A C1 control, a format character (a bidi override, a zero width space, a soft hyphen), a line or paragraph
    # separator: the patterns of free text take them, and the printer refuses each, saying nothing of it.
    for verb, values in [
        ("settings", {"name": "vm_note", "value": f"a{character}b"}),
        ("deploy", {"names": ["alpha"], "set": [f"vm_note={character}"]}),
        ("mcp", {"browser": character}),
        ("mcp", {"extra": f"a{character}"}),
    ]:
        for printer in (_line, _argv):
            with pytest.raises(ValueError) as refused:
                printer(verb, values)
            assert character not in str(refused.value) and str(refused.value).startswith("error: aw"), refused.value
    # Masked, the value is not said, so nothing is refused.
    masked = _line("settings", {"name": "vm_note", "value": f"a{character}"}, refused=True)
    assert masked == "aw settings vm_note --value VALUE"


# ---------------------------------------------------------------- what is never printed ----


def test_a_line_from_values_no_run_took_never_says_a_value_of_free_text_back() -> None:
    # The free-text options are exactly those test_aw_cli_words.py's SECRET_TEXT says a secret may be typed into.
    free = {(name, option.field) for name, verb in VERBS.items() for option in verb.options}
    assert {(name, field) for name, field in free if field in inverse.FREE_TEXT} == set(SECRET_TEXT)
    cases = [("settings", {"name": "github_user", "value": TYPED}, "value")]
    settings = [f"github_user={TYPED}", "vm_cores=4"]
    cases += [(name, {"set": settings}, "set") for name, field in SECRET_TEXT if field == "set"]
    for verb, values, field in cases:
        metavar = next(option.metavar for option in VERBS[verb].options if option.field == field)
        assert TYPED in _line(verb, values)
        masked = _line(verb, values, refused=True)
        assert TYPED not in masked and TYPED not in " ".join(_argv(verb, values, refused=True)), masked
        assert f"--{field} {metavar}" in masked and "vm_cores=4" not in masked, masked
    # Only the free text: the rest of the line is said as it was given.
    told = _line("settings", {"name": "github_user", "value": TYPED, "host": "lab2"}, refused=True)
    assert told == "aw settings github_user --host lab2 --value VALUE"


def _printer_calls(source: str, own: bool = False) -> list[tuple[int, bool]]:
    """Each call of the printer's line or argv in source, by its line, and whether it says refused: inverse.line and
    inverse.argv, and, in the printer's own source (own), line and argv by their names."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
            name = function.attr if function.value.id == "inverse" else None
        else:
            name = function.id if own and isinstance(function, ast.Name) else None
        if name in ("line", "argv"):
            found.append((node.lineno, any(keyword.arg == "refused" for keyword in node.keywords)))
    return found


def test_every_call_of_the_printer_says_whether_its_values_were_refused() -> None:
    # refused is keyword-only with no default, so a caller that leaves it out fails when it runs: this holds every call
    # site, the ones no test reaches among them.
    assert "refused" not in {name for name, kind in _defaults(inverse.line).items() if kind} | {
        name for name, kind in _defaults(inverse.argv).items() if kind
    }
    calls = {}
    for path in _new_modules():
        where = path.relative_to(REPO).as_posix()
        for line, said_it in _printer_calls(path.read_text(), own=where in PRINTERS):
            calls[(where, line)] = said_it
    assert calls and [call for call, said_it in calls.items() if not said_it] == []
    assert _printer_calls("inverse.line('logs', {})\nline('x', {}, refused=True)", own=True) == [(1, False), (2, True)]
    assert _printer_calls("line('x', {})") == []


def _defaults(function) -> dict[str, bool]:
    """Each keyword-only parameter of function, and whether it has a default."""
    import inspect

    return {
        name: parameter.default is not inspect.Parameter.empty
        for name, parameter in inspect.signature(function).parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    }


def test_a_value_the_one_check_refuses_is_never_printed() -> None:
    # The check's sentence, which never says the value: an option the verb has not got, a value of another type, one
    # outside its pattern, a word where the verb takes none or one where it takes one, a word that starts with -.
    for verb, values in [
        ("deploy", {"core": "8"}),
        ("deploy", {"cores": 8}),
        ("deploy", {"cores": TYPED}),
        ("deploy", {"names": [f"{TYPED}\n"]}),
        ("status", {"detach": True}),
        ("init", {"extra": TYPED}),
        ("code", {"name": ["alpha", "beta"]}),
        ("mcp", {"extra": f"-{TYPED}"}),
        ("nothing", {}),
    ]:
        for printer in (_line, _argv):
            with pytest.raises(ValueError) as refused:
                printer(verb, values)
            assert TYPED not in str(refused.value) and str(refused.value).startswith("error: aw"), refused.value
    with pytest.raises(ValueError):
        _argv("deploy", {}, before={"attended": "2"})


def test_a_rule_between_options_is_the_run_s_to_say() -> None:
    # deploy's question names the stage before its answer meets the rules: --start starts nothing under --only
    # configure, which the check refuses and the printer prints.
    values = {"only": "configure", "start": True}
    assert checks.check("deploy", values) == rules.RULES["deploy.10"].check(values)
    assert _line("deploy", values) == "aw deploy --only configure --start"


# ---------------------------------------------------------------- the program as it was started ----


def test_every_line_and_every_command_a_sentence_names_is_the_program_as_it_was_started(monkeypatch) -> None:
    monkeypatch.setattr(inverse, "_program", inverse.PROGRAM)
    inverse.started_as("./aw")
    assert inverse.program() == "./aw" and _line("status", {}) == "./aw status"
    # The one check's own sentences, a rule's and the table's, each through said.
    assert (checks.command("deploy"), checks.help_of("deploy")) == ("./aw deploy", "./aw help deploy")
    assert checks.no_such_command() == "error: ./aw has no such command (./aw help lists them)"
    assert checks.check("code", {}) == "error: code opens one VM: ./aw code <name>"
    assert said(SHARED_HOST.help or "").endswith("(./aw host lists them)")
    assert said("{line}", {"line": Line("vault")}) == "./aw vault"
    inverse.started_as("/opt/my checkout/aw")
    assert _line("status", {}) == "'/opt/my checkout/aw' status"
    assert _argv("status", {}) == ["/opt/my checkout/aw", "status"]
    # Nothing said, or a word no line could print as it is: aw.
    for word in (None, "", "-x/aw", "./a\x9bw", "./a\u202ew", "./a\nb"):
        inverse.started_as(word)
        assert inverse.program() == "aw", word


def test_main_takes_what_the_launcher_said_out_of_the_environment_at_once(monkeypatch, capsys) -> None:
    monkeypatch.setattr(inverse, "_program", inverse.PROGRAM)
    monkeypatch.setenv(main.STARTED_AS, "./aw")
    assert main.main([str(REPO / "tools" / "aw"), "help", "deploy"]) == 0
    assert main.STARTED_AS not in os.environ and inverse.program() == "./aw"
    assert capsys.readouterr().out.startswith("Usage: ./aw deploy [<name>...]\n")
    main.main([str(REPO / "tools" / "aw"), "help", "deploy"])
    assert inverse.program() == "aw"


def _program_said(result: subprocess.CompletedProcess[str]) -> str:
    """The program a run of aw help deploy said: its usage line's first word."""
    assert result.returncode == 0, result.stderr
    usage = result.stdout.splitlines()[0]
    assert usage.startswith("Usage: ") and usage.endswith(" deploy [<name>...]"), usage
    return usage.removeprefix("Usage: ").removesuffix(" deploy [<name>...]")


def test_the_launcher_says_aw_where_aw_on_path_is_it_and_else_what_was_typed(home, tmp_path) -> None:
    # An aw first on PATH that is another program, so that the developer's own PATH decides nothing.
    other = tmp_path / "another-aw"
    other.mkdir()
    (other / "aw").write_text("#!/bin/sh\nexit 9\n")
    (other / "aw").chmod(0o755)
    typed = run_launcher("help", "deploy", home=home, launcher="./aw", cwd=REPO, first_on_path=(other,))
    assert _program_said(typed) == "./aw"
    whole = run_launcher("help", "deploy", home=home, cwd=tmp_path, first_on_path=(other,))
    assert _program_said(whole) == shlex.quote(str(LAUNCHER))
    for launcher in ("./aw", LAUNCHER):
        linked = run_launcher("help", "deploy", home=home, launcher=launcher, cwd=REPO, first_on_path=(on_path(home),))
        assert _program_said(linked) == "aw", launcher
    # tools/aw run directly, as the agents' server runs it, is told nothing and says aw; told, it says what it is told.
    assert _program_said(run_aw("help", "deploy", home=home)) == "aw"
    told = run_aw("help", "deploy", home=home, env={main.STARTED_AS: "./aw"})
    assert _program_said(told) == "./aw"


# ---------------------------------------------------------------- the one printer ----

# The modules that write a command line: the printer alone.
PRINTERS = {"lib/claude_vm/cli/inverse.py"}
# The functions outside it that name a command in a sentence, each with why.
NAMED_IN_A_SENTENCE = {
    ("lib/claude_vm/cli/dispatch.py", "not_built"): "today's just <verb>, which does a verb's work until the flip, "
    "when K14 takes the clause out: no command of aw's, so none the printer writes",
}
# The modules that may name the printer's PROGRAM: the printer, and help.
PROGRAMS = {"lib/claude_vm/cli/inverse.py", "lib/claude_vm/cli/help.py"}
# What a formatted value of an f-string is read as.
FIELD = "\x00"
# What may follow aw or just where a string names a command: a verb of the table, a recipe of the repository's own, a
# formatted value, a field of str.format, an option, or nothing yet.
COMMAND_WORDS = set(VERBS) | {"lint", "test", "docs", "vagrant", "clean", "install"}
COMMAND = re.compile(
    r"(?<![\w/.-])(?:\./)?(?:aw|just)\s+(?:\Z|"
    + re.escape(FIELD)
    + r"|\{|-|(?:"
    + "|".join(sorted(map(re.escape, COMMAND_WORDS)))
    + r")(?![\w-]))"
)


def _text(node: ast.AST) -> str | None:
    """A string's text, a formatted value read as FIELD; None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else FIELD for part in node.values)
    return None


def _names_a_command(text: str) -> bool:
    """Whether text names a command anywhere in it: aw or just, as a word of its own, then a verb, a recipe, a value
    formatted in, a field, an option, or a space that ends the string, which a piece joined after it would make a
    command of."""
    return COMMAND.search(text) is not None


def _commands_built(source: str) -> list[tuple[int, str]]:
    """Each string source builds that names a command, by its line and the function it is in ('' outside one): never a
    docstring, or a string standing alone as a statement."""
    found: list[tuple[int, str]] = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Expr) and _text(node.value) is not None:
            return
        text = _text(node)
        if text is not None:
            if _names_a_command(text):
                found.append((getattr(node, "lineno", 0), function))
            return
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(ast.parse(source), "")
    return found


def _names_program(source: str) -> bool:
    """Whether source names the printer's PROGRAM: inverse.PROGRAM, or PROGRAM imported from it."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr == "PROGRAM":
            if isinstance(node.value, ast.Name) and node.value.id == "inverse":
                return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("inverse"):
            if any(alias.name == "PROGRAM" for alias in node.names):
                return True
    return False


def _new_modules() -> list[Path]:
    return sorted(path for sub in NEW for path in (REPO / "lib" / "claude_vm" / sub).rglob("*.py"))


def test_no_module_of_the_new_engine_builds_a_command_outside_the_printer() -> None:
    outside: dict[str, list[int]] = {}
    named = set()
    for path in _new_modules():
        where = path.relative_to(REPO).as_posix()
        if where in PRINTERS:
            continue
        for line, function in _commands_built(path.read_text()):
            if (where, function) in NAMED_IN_A_SENTENCE:
                named.add((where, function))
            else:
                outside.setdefault(where, []).append(line)
    assert outside == {}, (
        "name a command as a Line said through schema/words.py's said, or print it with cli/inverse.py"
    )
    assert named == set(NAMED_IN_A_SENTENCE), "an exception that names nothing any more: take it out"
    assert (REPO / "lib" / "claude_vm" / "cli" / "inverse.py").is_file()


def test_only_the_printer_and_help_name_its_program() -> None:
    naming = {path.relative_to(REPO).as_posix() for path in _new_modules() if _names_program(path.read_text())}
    assert naming <= PROGRAMS and "lib/claude_vm/cli/inverse.py" not in naming - PROGRAMS
    assert _names_program("x = inverse.PROGRAM") and _names_program("from claude_vm.cli.inverse import PROGRAM")
    assert not _names_program("x = inverse.program()")


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ('x = f"aw {verb}"', [(1, "")]),
        ('x = "aw deploy alpha"', [(1, "")]),
        ('x = "aw " + verb', [(1, "")]),
        ('x = f"aw --attended=1 {verb}"', [(1, "")]),
        ('def f(recipe):\n    return f"just {recipe} --help"', [(2, "f")]),
        ('def g():\n    return ("aw help", "x")', [(2, "g")]),
        ('x = "aw help deploy"', [(1, "")]),
        # Anywhere in a string, as review/code/H9.md found them: in a sentence, indented, as ./aw, after a tab, a
        # field of str.format, and today's just.
        ('x = "error: aw deploy refused"', [(1, "")]),
        ('x = f"error: the fleet is held by job {job}: `aw cancel {job}` stops it"', [(1, "")]),
        ("x = f'  aw login {vm} --only github'", [(1, "")]),
        ("x = './aw cancel ' + job", [(1, "")]),
        ("x = 'aw\\tcancel'", [(1, "")]),
        ("x = 'aw {} {}'.format('cancel', job)", [(1, "")]),
        ("x = 'run just test unit'", [(1, "")]),
        # Prose that names the program, and what the scan cannot see: pieces joined at run time, which only names
        # PROGRAM may make, and the printer alone does.
        ('x = "aw from the checkout " + path', []),
        ('x = f"aw drives the fleet in {root}"', []),
        ('x = "aw runs only from a virtualenv"', []),
        ('x = "the program is aw, always"', []),
        ('x = "draw deploy"', []),
        ('x = "tools/aw deploy"', []),
        ('"""aw deploy alpha is told in a docstring."""', []),
        ('x = "aw"', []),
        ('x = b"aw deploy"', []),
        ("x = 'aw' + ' cancel ' + job", []),
        ("x = ' '.join(['aw', 'cancel', job])", []),
    ],
)
def test_the_scan_sees_a_command_named_anywhere_in_a_string(source: str, found: list) -> None:
    assert _commands_built(source) == found


def test_no_sentence_of_the_rules_or_the_table_holds_a_command_as_text() -> None:
    # Each names its commands as Lines, which said fills with the printer's: so the scan above holds them, and each
    # sentence a rule or the table says fills every field it names.
    for code, rule in rules.RULES.items():
        assert not _names_a_command(rule.sentence), code
    texts = [verb.doc for verb in VERBS.values()] + [verb.words.help for verb in VERBS.values() if verb.words]
    every = [option for verb in VERBS.values() for option in verb.options] + list(BEFORE)
    texts += [option.help for option in every if option.help]
    texts += [verb.tools for verb in VERBS.values() if isinstance(verb.tools, str)]
    agents = [option.agent for option in every] + [verb.words.agent for verb in VERBS.values() if verb.words]
    texts += [
        one.why for agent in agents for one in (agent.values() if isinstance(agent, dict) else [agent]) if one.why
    ]
    for text in texts:
        assert not _names_a_command(text) and "{" not in said(text) and "}" not in said(text), text
    assert set(LINES) == {name for text in texts for name in re.findall(r"\{([a-z_]+)\}", text)}, (
        "a line of LINES no sentence of the table names, or a sentence naming one LINES has not got"
    )


def test_the_check_s_sentences_name_the_command_as_the_printer_writes_it() -> None:
    assert checks.help_of("") == _line("help", {})
    for name in VERBS:
        assert checks.command(name) == _line(name, {}), name
        assert checks.help_of(name) == _line("help", {"recipe": name}), name


@pytest.mark.parametrize("module", ["claude_vm.schema.check", "claude_vm.pve.client", "claude_vm.vendors.trust"])
def test_schema_pve_and_vendors_import_nothing_of_cli_when_they_are_imported(home, module) -> None:
    # They reach the printer through words.said alone, which imports it when a sentence is said.
    loaded = imports_of(module=module, home=home)
    assert module in loaded and sorted(name for name in loaded if name.startswith("claude_vm.cli")) == []


def test_the_printer_imports_nothing_outside_the_standard_library_and_schema(home) -> None:
    # What importing its package alone loads is taken away: what is left is what the module itself brings.
    added = imports_of(module=MODULE, home=home) - imports_of(module="claude_vm.cli", home=home)
    assert MODULE in added, sorted(added)
    assert sorted({name.split(".")[0] for name in added} - set(sys.stdlib_module_names) - {"claude_vm"}) == []
    ours = {name.split(".")[1] for name in added if name.startswith("claude_vm.")}
    assert ours <= {"schema", "cli"}, sorted(ours)
    assert "argparse" not in added and "subprocess" not in added
