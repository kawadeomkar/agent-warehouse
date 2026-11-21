"""The printer of a command (lib/claude_vm/cli/inverse.py; docs/architecture/engine.md §16.3): the one way the new
engine writes the command line a person types, aw and a verb's values, from the one table.

For every verb of the table, its words, each option alone and beside the words, and each pair of options the rules
between options allow, with the words and without, are printed, parsed again by aw's parser and held by the one check,
and come back as they went in; and no verb, option or verb's words of the table lacks such a case. Every example line
of the contract of today's command line that aw takes is parsed, printed and parsed again to the same values, before
today's habits and after them. A value with a space, a quote and a leading -, an empty value and a name that starts
with a digit survive the trip, through shlex and through /bin/sh. A line made from values no run has taken never says
a value of --set or settings --value back. A value the one check refuses is never printed, and a rule between options
is the run's to say. The one check's own sentences name the command as the printer writes it, and no other module of
the new engine's subpackages builds a string that starts with a command, aw or just and a verb, outside the printer
and help. The printer imports nothing outside the standard library and schema/.
"""

from __future__ import annotations

import ast
import itertools
import json
import shlex
import subprocess
import sys
from pathlib import Path

import cli_contract as contract
import pytest
from aw_kit import imports_of
from harness import REPO
from test_aw_cli_parse import SAMPLES
from test_aw_cli_rules import REPOSITORYS
from test_aw_cli_words import SECRET_TEXT
from test_aw_layout import NEW

from claude_vm.cli import habits, inverse, parse
from claude_vm.schema import check as checks
from claude_vm.schema import rules
from claude_vm.schema.patterns import full
from claude_vm.schema.words import BEFORE, VERBS, Option, Verb, Words

CONTRACT = json.loads((Path(__file__).resolve().parent / "cli_contract.json").read_text())
# A value with a space, a quote and a leading -, which a value of free text may be: given as --long=value, quoted.
AWKWARD = '-it\'s "two" words'
# A value a person may have pasted where it does not belong: a line made from values no run took never says it.
TYPED = "swordfish"
# A VM's name that starts with a digit, as one may.
DIGIT_FIRST = "2nd-vm"
MODULE = "claude_vm.cli.inverse"


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
    if option.kind == "optional-value":
        return [True, sample, *more]
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
    printed = inverse.argv(verb, values)
    assert printed[:1] == ["aw"] and shlex.split(inverse.line(verb, values)) == printed, (verb, values)
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
    for before in [{"no_lock": True}, {"attended": "1"}, {"detached_run": True}]:
        printed = inverse.argv("deploy", {"names": ["alpha"]}, before=before)
        assert parse.read(printed[1:]) == parse.Reading("deploy", before, {"names": ["alpha"]})
    everything = {option.field: SAMPLES.get(option.pattern or "", True) for option in BEFORE}
    printed = inverse.argv("status", {}, before=everything)
    assert printed == ["aw", "--no-lock", "--attended=1", "--detached-run", "status"]
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
        printed = inverse.argv(reading.verb, reading.values, before=reading.before)
        assert parse.read(printed[1:]) == reading, (line, printed)
        assert shlex.split(inverse.line(reading.verb, reading.values, before=reading.before)) == printed
        # And after today's habits, as a handler receives the values: names split and with - for _, no names said.
        verb = VERBS[reading.verb]
        try:
            values, _ = habits.names(verb, reading.values)
        except parse.Refused:
            continue
        again = parse.read(inverse.argv(reading.verb, values)[1:])
        assert habits.names(verb, again.values)[0] == values, (line, values)
    assert taken >= EXAMPLES_TAKEN, taken


# ---------------------------------------------------------------- the form of a line ----


def test_a_line_is_aw_the_options_in_the_table_s_order_then_the_words() -> None:
    values = {
        "names": ["alpha", DIGIT_FIRST],
        "detach": True,
        "verbosity": 2,
        "set": ["vm_cores=4", "vm_memory=8G"],
        "tags": "claude_code",
        "only": "configure",
    }
    assert inverse.line("deploy", values) == (
        "aw deploy --only=configure --tags=claude_code --set=vm_cores=4 --set=vm_memory=8G -vv --detach alpha 2nd-vm"
    )
    # deploy's whole chain is its one verb, and no names is said by none, whatever the habits made of it.
    assert inverse.line("deploy", {"names": ["alpha"]}) == "aw deploy alpha"
    assert inverse.line("deploy", {"default_vm": True}) == "aw deploy"
    assert inverse.line("status", {"default_vm": False}) == "aw status"
    assert inverse.argv("deploy", {"default_vm": True, "cores": None}) == ["aw", "deploy"]
    assert inverse.line("help", {"recipe": "deploy"}) == "aw help deploy"
    assert inverse.line("init", {"migrate": True}) == "aw init --migrate"
    folder = "/old checkout"
    assert inverse.line("init", {"migrate": folder}) == f"aw init --migrate={shlex.quote(folder)}"
    assert inverse.line("deploy", {"cores": "", "names": ["alpha"]}) == "aw deploy --cores= alpha"
    assert inverse.argv("deploy", {"cores": "", "names": ["alpha"]}) == ["aw", "deploy", "--cores=", "alpha"]


def test_the_line_ends_with_the_host_flag_on_a_verb_that_takes_one() -> None:
    flag = " --host lab2"
    assert inverse.line("code", {"name": "alpha", "project": "app"}, host_flag=flag) == (
        "aw code --project=app alpha --host lab2"
    )
    printed = inverse.argv("code", {"name": "alpha"}, host_flag=flag)
    assert printed == ["aw", "code", "alpha", "--host", "lab2"]
    assert parse.read(printed[1:]).values == {"name": "alpha", "host": "lab2"}
    assert inverse.line("deploy", {"names": ["alpha"]}, host_flag="") == "aw deploy alpha"
    # A verb with no --host; one given --host already; and a flag not in today's form, or a name no host has.
    for verb, values, given in [
        ("release", {"name": "alpha"}, flag),
        ("logs", {}, flag),
        ("code", {"name": "alpha", "host": "lab2"}, flag),
        ("code", {"name": "alpha"}, "--host lab2"),
        ("code", {"name": "alpha"}, " --host "),
        ("code", {"name": "alpha"}, " --host Lab_2"),
        ("code", {"name": "alpha"}, " --host=lab2"),
    ]:
        for printer in (inverse.line, inverse.argv):
            with pytest.raises(ValueError):
                printer(verb, values, host_flag=given)


# ---------------------------------------------------------------- values a shell must read as given ----

AWKWARD_LINES = [
    ("settings", {"name": "vm_note", "value": AWKWARD}),
    ("settings", {"name": "vm_cores", "value": "--"}),
    ("deploy", {"names": [DIGIT_FIRST], "set": [f"vm_note={AWKWARD}"], "cores": ""}),
    ("destroy", {"names": [DIGIT_FIRST, "alpha"], "confirm": f"{DIGIT_FIRST},alpha"}),
    ("init", {"migrate": f"/old {AWKWARD}"}),
    ("mcp", {"browser": AWKWARD}),
    ("code", {"name": DIGIT_FIRST}),
]


@pytest.mark.parametrize(("verb", "values"), AWKWARD_LINES)
def test_a_value_with_a_space_a_quote_and_a_leading_dash_survives_the_shell(verb, values, monkeypatch) -> None:
    printed = inverse.argv(verb, values)
    # /bin/sh reads the line a person pastes into the words the program is given: printf says each on a line of its
    # own, none of them holding a newline, which no pattern takes.
    shell = subprocess.run(
        ["/bin/sh", "-c", f"printf '%s\\n' {inverse.line(verb, values)}"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert shell.stdout.split("\n")[:-1] == printed
    _round_trip(verb, values, monkeypatch)


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
        assert TYPED in inverse.line(verb, values)
        masked = inverse.line(verb, values, refused=True)
        assert TYPED not in masked and TYPED not in " ".join(inverse.argv(verb, values, refused=True)), masked
        assert f"--{field}={metavar}" in masked and "vm_cores=4" not in masked, masked
    # Only the free text: the rest of the line is said as it was given.
    said = inverse.line("settings", {"name": "github_user", "value": TYPED, "host": "lab2"}, refused=True)
    assert said == "aw settings --host=lab2 --value=VALUE github_user"


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
        for printer in (inverse.line, inverse.argv):
            with pytest.raises(ValueError) as refused:
                printer(verb, values)
            assert TYPED not in str(refused.value) and str(refused.value).startswith("error: aw"), refused.value
    with pytest.raises(ValueError):
        inverse.argv("deploy", {}, before={"attended": "2"})


def test_a_rule_between_options_is_the_run_s_to_say() -> None:
    # deploy's question names the stage before its answer meets the rules: --start starts nothing under --only
    # configure, which the check refuses and the printer prints.
    values = {"only": "configure", "start": True}
    assert checks.check("deploy", values) == rules.RULES["deploy.10"].check(values)
    assert inverse.line("deploy", values) == "aw deploy --only=configure --start"


# ---------------------------------------------------------------- the one printer ----

# The modules that write a command line: the printer, and help, which prints the table's own usage lines.
PRINTERS = {"lib/claude_vm/cli/inverse.py", "lib/claude_vm/cli/help.py"}
# The functions outside them that name a command in a sentence, each with why.
NAMED_IN_A_SENTENCE = {
    ("lib/claude_vm/schema/check.py", "command"): "the one check's refusals name the command refused, aw <verb>, as "
    "argparse's prog: schema/ imports nothing of cli/, so it writes it itself, held equal to the printer's below",
    ("lib/claude_vm/schema/check.py", "help_of"): "the one check's refusals say where the verb's options are listed, "
    "aw help <verb>: schema/ imports nothing of cli/, so it writes it itself, held equal to the printer's below",
}
# What a formatted value of an f-string is read as.
FIELD = "\x00"
# What may follow aw or just where a string starts a command: a verb of the table, a recipe of today's justfile, the
# developer's among them, a formatted value, an option, or nothing yet.
COMMAND_WORDS = set(VERBS) | {recipe["name"] for recipe in CONTRACT["recipes"]} | set(REPOSITORYS) | {"lint"}


def _text(node: ast.AST) -> str | None:
    """A string's text, a formatted value read as FIELD; None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else FIELD for part in node.values)
    return None


def _starts_a_command(text: str) -> bool:
    program, space, rest = text.partition(" ")
    if program not in ("aw", "just") or not space:
        return False
    first = rest.split(" ", 1)[0].rstrip(".,:;")
    return not first or first.startswith((FIELD, "-")) or first in COMMAND_WORDS


def _commands_built(source: str) -> list[tuple[int, str]]:
    """Each string source builds that starts with a command, by its line and the function it is in ('' outside one):
    never a docstring, or a string standing alone as a statement."""
    found: list[tuple[int, str]] = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Expr) and _text(node.value) is not None:
            return
        text = _text(node)
        if text is not None:
            if _starts_a_command(text):
                found.append((getattr(node, "lineno", 0), function))
            return
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(ast.parse(source), "")
    return found


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
    assert outside == {}, "print a command through lib/claude_vm/cli/inverse.py's line or argv"
    assert named == set(NAMED_IN_A_SENTENCE), "an exception that names nothing any more: take it out"
    assert (REPO / "lib" / "claude_vm" / "cli" / "inverse.py").is_file()


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ('x = f"aw {verb}"', [(1, "")]),
        ('x = "aw deploy alpha"', [(1, "")]),
        ('x = "aw " + verb', [(1, "")]),
        ('x = f"aw --no-lock {verb}"', [(1, "")]),
        ('def f(recipe):\n    return f"just {recipe} --help"', [(2, "f")]),
        ('def g():\n    return ("aw help", "x")', [(2, "g")]),
        ('x = "aw from the checkout " + path', []),
        ('x = "aw help deploy"', [(1, "")]),
        ('x = f"aw drives the fleet in {root}"', []),
        ('x = "error: aw deploy refused"', []),
        ('"""aw deploy alpha is told in a docstring."""', []),
        ('x = "aw"', []),
        ('x = b"aw deploy"', []),
    ],
)
def test_the_scan_sees_a_command_built(source: str, found: list) -> None:
    assert _commands_built(source) == found


def test_the_check_s_sentences_name_the_command_as_the_printer_writes_it() -> None:
    assert checks.help_of("") == inverse.line("help", {})
    for name in VERBS:
        assert checks.command(name) == inverse.line(name, {}), name
        assert checks.help_of(name) == inverse.line("help", {"recipe": name}), name


def test_the_printer_imports_nothing_outside_the_standard_library_and_schema(home) -> None:
    # What importing its package alone loads is taken away: what is left is what the module itself brings.
    added = imports_of(module=MODULE, home=home) - imports_of(module="claude_vm.cli", home=home)
    assert MODULE in added, sorted(added)
    assert sorted({name.split(".")[0] for name in added} - set(sys.stdlib_module_names) - {"claude_vm"}) == []
    ours = {name.split(".")[1] for name in added if name.startswith("claude_vm.")}
    assert ours <= {"schema", "cli"}, sorted(ours)
    assert "argparse" not in added and "subprocess" not in added
