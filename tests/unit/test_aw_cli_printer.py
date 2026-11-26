"""The printer of a command (lib/claude_vm/schema/printer.py; docs/architecture/engine.md §16.2 and §16.3): the one way
the new engine writes the command line a person types, the program as it was started and a verb's values, from the one
table, and the one way a sentence names a command.

For every verb of the table, its words, each option alone and beside the words, and each pair of options the rules
between options allow, with the words and without, are printed, parsed again by aw's parser and held by the one check,
and come back as they went in; and no verb, option or verb's words of the table lacks such a case. Every example line
here, of every verb, is taken, parsed, printed and parsed again to the same values, before the habits and after them.
A line is the program, the verb, its words, then its options, a value after a space but where it starts with - or is
empty. A value with a space, a quote, a leading -, each character a POSIX shell reads,
a backslash and a trailing one, two values of free text on one line, an empty value and a name that starts with a digit
survive the trip, through shlex, /bin/sh and bash, and a value that starts with = is quoted, which zsh would expand; a
value a terminal would not show as it is is never printed. A line made from values no run has taken never says a value
of --set or settings --value back, and every call of the printer, through any name or a partial, says which it is. A
value that is off is not said; a placeholder is <name>, the name help gives it, in a form line and a masked one alike,
and each placeholder given back to aw, as a word or as JSON, is refused in a sentence of aw's own. A form writes its
placeholders last, and over the printer's whole domain, every verb with each placeholder beside the rest, a form pasted
into /bin/sh and bash runs nothing, or words aw refuses, with no exception; a form the printer refuses is its
Unwritten, which aw says in one sentence as its own fault and never as a traceback, before a handler's run and in one,
a call in a thread under way or none; and no module of the new engine
extends a printer's line with words of its own, by +, %, a join, an f-string, print or its replace, through anything
that hands it on, or says a sentence built at its call, while lines joined by a newline, and a line written with its
end, are whole. The host's flag is given by every caller of a verb with the shared --host, and only a verb whose --host
runs it against a host takes one.
A value the one check refuses is never printed, and a rule between options is the run's to say. Every line, and every
command a sentence names, is the program as it was started, quoted: aw where aw on PATH is the launcher, else what was
typed, through the launcher; the one check's own sentences, the rules', help's usage lines and its closing paragraph
among them; a program word a terminal would not show as it is, a byte that is no UTF-8 among them, is aw. No other
module of the new engine's subpackages builds a string that names a command, aw or just and a verb anywhere in its text
(prose that says just aside), outside the printer; only the printer names the program, as started or as its constant;
only the printer and the check name a sentence's program field. Each module of schema/ imports alone, first, and
nothing outside the standard library and schema/, and pve/ and vendors/ import the printer plainly, at the top of a
file, as every module does.
"""

from __future__ import annotations

import ast
import itertools
import os
import re
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from aw_kit import LAUNCHER, environment, imports_of, on_path, run_aw, run_launcher
from harness import REPO
from test_aw_cli_parse import SAMPLES
from test_aw_cli_rules import REFUSALS
from test_aw_cli_words import SECRET_TEXT
from test_aw_layout import NEW

from claude_vm.cli import dispatch, habits, main, parse
from claude_vm.cli import help as helps
from claude_vm.fleet import paths
from claude_vm.schema import check as checks
from claude_vm.schema import printer, rules
from claude_vm.schema.patterns import full
from claude_vm.schema.printer import said
from claude_vm.schema.words import BEFORE, EXITS, LINES, PLACEHOLDER, VERBS, Line, Option, Verb, Words, placeholder
from claude_vm.schema.words import HOST as SHARED_HOST

# A value with a space, a quote and a leading -, which a value of free text may be: given as --long=value, quoted.
AWKWARD = '-it\'s "two" words'
# A value a person may have pasted where it does not belong: a line made from values no run took never says it.
TYPED = "swordfish"
# A VM's name that starts with a digit, as one may.
DIGIT_FIRST = "2nd-vm"
MODULE = "claude_vm.schema.printer"
# The verbs whose row has the shared --host, which every caller of the printer gives a host's flag.
SHARED = ("deploy", "code", "login", "status", "destroy")


def _line(verb: str, values: dict, **given) -> str:
    """The printer's line made from a run's own values, naming no host where the verb takes the shared one."""
    return printer.line(verb, values, **{"host_flag": "", "refused": False, **given})


def _argv(verb: str, values: dict, **given) -> list[str]:
    return printer.argv(verb, values, **{"host_flag": "", "refused": False, **given})


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


# Command lines as a person types them after aw, of every verb: the words given before a verb, the names habits split
# and hyphenate, each kind of option, a value given after a space and with =.
EXAMPLES = (
    "help",
    "help deploy",
    "init",
    "init --no-prompt",
    "init --host lab2",
    "init --tailscale off",
    "init --address pve.example --github-user octocat --token-secret-file ~/.config/pve-token",
    "install",
    "install --lock",
    "vault",
    "vault --host lab2",
    "deploy",
    "deploy alpha",
    "deploy alpha beta",
    "deploy alpha,beta",
    "deploy my_vm",
    "deploy alpha --only configure",
    "deploy --only provision",
    "deploy --only configure",
    "deploy --only ssh-config",
    "deploy --only template",
    "deploy --check",
    "deploy alpha --tags claude_code",
    "deploy alpha --repos a,b",
    "deploy alpha --cores 8 --memory 16G",
    "deploy alpha --set vm_cores=4 -vvv",
    "deploy alpha --detach",
    "--attended=1 deploy alpha",
    "code alpha",
    "code my_vm",
    "code alpha --project repo",
    "code alpha --editor zed",
    "login",
    "login alpha",
    "login alpha beta",
    "login alpha --only github",
    "login --only claude",
    "status",
    "status --json",
    "status --only=vms,work",
    "status --only capacity",
    "settings",
    "settings --all",
    "settings --changed",
    "settings vm_cores",
    "settings editor --value zed",
    "destroy",
    "destroy alpha",
    "destroy alpha,beta",
    "destroy --confirm=alpha alpha",
    "release alpha",
    "host",
    "host --json",
    "host lab2",
    "logs",
    "logs 0123456789abcdef --follow",
    "cancel",
    "mcp",
    "mcp --remove",
    "mcp --any-branch",
)


def test_every_example_comes_back_from_its_line() -> None:
    for example in EXAMPLES:
        reading = parse.read(shlex.split(example))
        printed = _argv(reading.verb, reading.values, before=reading.before)
        assert parse.read(printed[1:]) == reading, (example, printed)
        assert shlex.split(_line(reading.verb, reading.values, before=reading.before)) == printed
        # And after the habits, as a handler receives the values: names split and with - for _, no names said.
        verb = VERBS[reading.verb]
        values, _ = habits.names(verb, reading.values)
        again = parse.read(_argv(reading.verb, values)[1:])
        assert habits.names(verb, again.values)[0] == values, (example, values)


def test_the_examples_name_every_verb() -> None:
    assert {next(word for word in shlex.split(example) if not word.startswith("-")) for example in EXAMPLES} == set(
        VERBS
    )


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
    assert _line("help", {"command": "deploy"}) == "aw help deploy"
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
        with pytest.raises(printer.Unwritten):
            _argv("deploy", values)


def test_a_placeholder_is_the_name_help_gives_it() -> None:
    # The owner's ruling of 2026-11-22 (review/code/OWNER-9.md 1): <name>, lower case in angle brackets, never quoted,
    # an option's value too, from words.py's placeholder, which help's rows show as well.
    assert _line("destroy", {"names": PLACEHOLDER, "confirm": PLACEHOLDER}) == "aw destroy <name>... --confirm <names>"
    assert _line("code", {"name": PLACEHOLDER}) == "aw code <name>"
    assert _line("settings", {"name": PLACEHOLDER, "value": PLACEHOLDER}) == "aw settings <name> --value <value>"
    assert _line("help", {"command": PLACEHOLDER}) == "aw help <command>"
    assert _line("deploy", {"names": PLACEHOLDER, "set": PLACEHOLDER}) == "aw deploy <name>... --set <name>=<value>"
    assert _line("init", {"token_secret_file": PLACEHOLDER}) == "aw init --token-secret-file <file>"
    assert _line("code", {"name": "alpha", "host": PLACEHOLDER}) == "aw code alpha --host <host>"
    assert _line("host", {"name": PLACEHOLDER, "remove": True}) == "aw host --remove <name>"
    # Every option's, as help shows it: one function for both.
    for verb in VERBS.values():
        for option in checks.options_of(verb):
            if option.kind in ("value", "repeatable"):
                assert _argv(verb.name, {option.field: PLACEHOLDER})[-1] == placeholder(option), option
                assert helps.shown(option).endswith(f" {placeholder(option)}"), option
    # A flag and a count have no value to name; and a placeholder is never a value the one check takes.
    for values in ({"check": PLACEHOLDER}, {"verbosity": PLACEHOLDER}):
        with pytest.raises(printer.Unwritten):
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
    assert printer.line("code", {"name": "alpha", "host": "lab2"}, refused=False) == "aw code alpha --host lab2"
    # None, on a verb with the shared --host whose values give none: refused, so no caller forgets the host.
    for verb in SHARED:
        for printing in (printer.line, printer.argv):
            with pytest.raises(printer.Unwritten):
                printing(verb, {}, refused=False)
    # settings, vault and init run against the host their --host names: a flag there, and none for None.
    for verb in printer.HOSTS_OWN:
        assert printer.line(verb, {}, refused=False) == f"aw {verb}"
        printed = printer.argv(verb, {}, host_flag="lab2", refused=False)
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
        for printing in (printer.line, printer.argv):
            with pytest.raises(printer.Unwritten):
                printing(verb, values, host_flag=given, refused=False)


# ---------------------------------------------------------------- values a shell must read as given ----

# Each character a POSIX shell reads otherwise than as itself, in a word or at its start: a pasted line must give it
# back as typed, never run a second command, expand or glob.
SHELL = ["$(id)", "${x}", "`id`", ";", "|", "&", ">", "*", "?", "[a]", "~", "!", "#", "\\", "'", '"', " ", "="]
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


@pytest.mark.parametrize(
    "character", ["\x85", "\x9b", "\u200b", "\u202e", "\u2028", "\u2029", "\ufeff", "\xad", "\udce9"]
)
def test_a_value_a_terminal_would_not_show_as_it_is_is_never_printed(character: str) -> None:
    # A C1 control, a format character (a bidi override, a zero width space, a soft hyphen), a line or paragraph
    # separator, a lone surrogate (a byte that is no UTF-8, as Python decodes one: review/code/H25.md 7): the patterns
    # of free text take them, and the printer refuses each, saying nothing of it.
    for verb, values in [
        ("settings", {"name": "vm_note", "value": f"a{character}b"}),
        ("deploy", {"names": ["alpha"], "set": [f"vm_note={character}"]}),
        ("mcp", {"browser": character}),
        ("mcp", {"extra": f"a{character}"}),
    ]:
        for printing in (_line, _argv):
            with pytest.raises(printer.Unwritten) as refused:
                printing(verb, values)
            assert character not in str(refused.value) and str(refused.value).startswith("error: aw"), refused.value
    # Masked, the value is not said, so nothing is refused.
    masked = _line("settings", {"name": "vm_note", "value": f"a{character}"}, refused=True)
    assert masked == "aw settings vm_note --value <value>"


# ---------------------------------------------------------------- what is never printed ----


def test_a_line_from_values_no_run_took_never_says_a_value_of_free_text_back() -> None:
    # The free-text options are exactly those test_aw_cli_words.py's SECRET_TEXT says a secret may be typed into.
    free = {(name, option.field) for name, verb in VERBS.items() for option in verb.options}
    assert {(name, field) for name, field in free if field in printer.FREE_TEXT} == set(SECRET_TEXT)
    cases = [("settings", {"name": "github_user", "value": TYPED}, "value")]
    settings = [f"github_user={TYPED}", "vm_cores=4"]
    cases += [(name, {"set": settings}, "set") for name, field in SECRET_TEXT if field == "set"]
    for verb, values, field in cases:
        [option] = [option for option in VERBS[verb].options if option.field == field]
        assert TYPED in _line(verb, values)
        masked = _line(verb, values, refused=True)
        assert TYPED not in masked and TYPED not in " ".join(_argv(verb, values, refused=True)), masked
        assert f"--{field} {placeholder(option)}" in masked and "vm_cores=4" not in masked, masked
    # Only the free text: the rest of the line is said as it was given.
    told = _line("settings", {"name": "github_user", "value": TYPED, "host": "lab2"}, refused=True)
    assert told == "aw settings github_user --host lab2 --value <value>"


# ---------------------------------------------------------------- the printer's names, by any road ----

# The printer's module and the check's, by their dotted names.
PRINTER_MODULE = "claude_vm.schema.printer"
CHECK_MODULE = "claude_vm.schema.check"


def _bindings(tree: ast.AST, module: str = PRINTER_MODULE) -> tuple[set[str], set[str], dict[str, str]]:
    """The names a module binds to module (its last part, printer or check, as schema/'s own modules name them, among
    them), to the package that holds it (from claude_vm import schema), and to a name of module's, each by its name
    there: every import, absolute or relative, under any alias."""
    package, _, short = module.rpartition(".")
    parent, _, package_name = package.rpartition(".")
    modules, packages, names = {short}, set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.asname for alias in node.names if alias.name == module and alias.asname}
            packages |= {alias.asname for alias in node.names if alias.name == package and alias.asname}
        elif isinstance(node, ast.ImportFrom):
            source = node.module or ""
            for alias in node.names:
                bound = alias.asname or alias.name
                if alias.name == short and (source.endswith(package_name) or (node.level and not source)):
                    modules.add(bound)
                elif alias.name == package_name and (source == parent or (node.level and not source)):
                    packages.add(bound)
                elif source == short or source.endswith(f".{short}"):
                    names[bound] = alias.name
    return modules, packages, names


def _of_module(node: ast.AST, modules: set[str], packages: set[str], module: str) -> bool:
    """Whether node is module: a name bound to it, an attribute of a name bound to its package, or its dotted name
    written whole."""
    if isinstance(node, ast.Name):
        return node.id in modules
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in packages:
        return node.attr == module.rpartition(".")[2]
    return ast.unparse(node) == module


def _references(tree: ast.AST, wanted: tuple[str, ...], own: bool, module: str = PRINTER_MODULE) -> list[ast.AST]:
    """Each node of tree that reaches one of module's names in wanted (the printer's, unless another is named): an
    attribute of the module, a name imported from it under any alias, and in the module's own source (own) the name
    itself."""
    modules, packages, names = _bindings(tree, module)
    found: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in wanted:
            if _of_module(node.value, modules, packages, module):
                found.append(node)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if names.get(node.id) in wanted or (own and node.id in wanted):
                found.append(node)
    return found


def _printer_calls(source: str, own: bool = False) -> list[tuple[int, bool]]:
    """Each use of the printer's line or argv in source, by its line, and whether it says refused: a call through any
    name, a functools.partial of either whose own words say it, and any other use (a name bound to it, which a later
    call would reach) as not saying it."""
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    found = []
    for node in _references(tree, ("line", "argv"), own):
        call = parents.get(node)
        if isinstance(call, ast.Call) and (
            call.func is node or (call.args and call.args[0] is node and _callee(call) == "partial")
        ):
            found.append((call.lineno, any(keyword.arg == "refused" for keyword in call.keywords)))
        else:
            found.append((getattr(node, "lineno", 0), False))
    return sorted(found)


def _callee(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")


def test_every_call_of_the_printer_says_whether_its_values_were_refused() -> None:
    # refused is keyword-only with no default, so a caller that leaves it out fails when it runs: this holds every call
    # site, the ones no test reaches among them, whatever name the printer or its function is reached by.
    assert "refused" not in {name for name, kind in _defaults(printer.line).items() if kind} | {
        name for name, kind in _defaults(printer.argv).items() if kind
    }
    calls = {}
    for path in _new_modules():
        where = path.relative_to(REPO).as_posix()
        for line, said_it in _printer_calls(path.read_text(), own=where in PRINTERS):
            calls[(where, line)] = said_it
    assert calls and [call for call, said_it in calls.items() if not said_it] == []


@pytest.mark.parametrize(
    ("source", "calls"),
    [
        ("printer.line('logs', {})\nprinter.argv('x', {}, refused=True)", [(1, False), (2, True)]),
        ("line('x', {})", []),
        # Through an alias of the module, of the function, or the module's whole name (review/code/H25.md 6).
        (
            "from claude_vm.schema import printer as p\np.line('x', {}, refused=True)\np.argv('x', {})",
            [(2, True), (3, False)],
        ),
        ("from claude_vm.schema.printer import line as say\nsay('logs', {})", [(2, False)]),
        ("from ..schema.printer import argv\nargv('x', {}, refused=False)", [(2, True)]),
        ("import claude_vm.schema.printer\nclaude_vm.schema.printer.line('x', {})", [(2, False)]),
        ("import claude_vm.schema.printer as shown\nshown.line('x', {}, refused=True)", [(2, True)]),
        # A partial says it in its own words, or not at all; a name bound to the function is a call no scan follows.
        ("import functools\nf = functools.partial(printer.line, refused=True)", [(2, True)]),
        ("from functools import partial\nf = partial(printer.line, 'logs')", [(2, False)]),
        ("say = printer.line\nsay('x', {}, refused=True)", [(1, False)]),
    ],
)
def test_the_scan_of_the_printer_s_calls_follows_every_name(source: str, calls: list) -> None:
    assert _printer_calls(source) == calls


def test_the_printer_s_own_calls_are_read_by_their_names() -> None:
    assert _printer_calls("line('x', {})\nline('x', {}, refused=True)", own=True) == [(1, False), (2, True)]


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
        for printing in (_line, _argv):
            with pytest.raises(printer.Unwritten) as refused:
                printing(verb, values)
            assert TYPED not in str(refused.value) and str(refused.value).startswith("error: aw"), refused.value
    with pytest.raises(printer.Unwritten):
        _argv("deploy", {}, before={"attended": "2"})


def test_a_rule_between_options_is_the_run_s_to_say() -> None:
    # deploy's question names the stage before its answer meets the rules: --start starts nothing under --only
    # configure, which the check refuses and the printer prints.
    values = {"only": "configure", "start": True}
    assert checks.check("deploy", values) == rules.RULES["deploy.10"].check(values)
    assert _line("deploy", values) == "aw deploy --only configure --start"


# ---------------------------------------------------------------- a placeholder never runs ----


def _given_back() -> list[tuple[str, list[str], dict[str, object], str]]:
    """Each placeholder the printer or help writes, given back to aw as a word: every option that takes a value, of
    every verb and before one, and every verb's words, <name> and, where the verb takes several, <name>...; each with
    the verb, the words typed after aw, the values as JSON and the sentence that refuses them, aw's own text. The words
    of a verb that refuses any (Words.refused: init's and mcp's <extra>) are no word to put in a placeholder's place:
    the verb's own rule refuses theirs, as any word (test_aw_cli_help.py)."""
    cases = []
    for option in BEFORE:
        if option.kind == "value":
            shown = placeholder(option)
            sentence = f"error: aw --{option.long}: {shown} stands for the value: put yours in its place"
            cases.append(("", [f"--{option.long}", shown, "status"], {option.field: shown}, sentence))
    for name, verb in VERBS.items():
        for option in verb.options:
            if option.kind not in ("value", "repeatable"):
                continue
            shown = placeholder(option)
            spelled = checks.spelled(option)
            value = [shown] if option.kind == "repeatable" else shown
            sentence = f"error: aw {name} {spelled}: {shown} stands for the value: put yours in its place"
            cases.append((name, [name, spelled, shown], {option.field: value}, sentence))
        words = verb.words
        if words is not None and not words.refused:
            one = placeholder(words)
            sentence = f"error: aw {name}: {one} stands for the {words.name}: put yours in its place"
            cases.append((name, [name, one], {words.field: [one] if words.plural else one}, sentence))
            if words.plural:
                several = f"{one}..."
                sentence = f"error: aw {name}: {several} stands for the {words.name}s: put yours in their place"
                cases.append((name, [name, several], {words.field: [several]}, sentence))
    return cases


GIVEN_BACK = _given_back()


@pytest.mark.parametrize(("verb", "words", "values", "sentence"), GIVEN_BACK, ids=[" ".join(c[1]) for c in GIVEN_BACK])
def test_a_placeholder_given_back_is_refused_as_a_word_and_as_json(verb, words, values, sentence) -> None:
    # The owner's ruling of 2026-11-22 (review/code/OWNER-9.md 1): a placeholder stands for a value and never runs as
    # one, through the command line, an agent's tool or the add-on's broker alike, in a sentence that says only aw's own
    # text; and the printer never prints one as a value.
    with pytest.raises(parse.Refused) as refused:
        parse.read(words)
    assert refused.value.sentence == sentence
    check = checks.check_before(values) if not verb else checks.check(verb, values)
    assert check == sentence
    with pytest.raises(printer.Unwritten, match="stands for"):
        _argv(verb or "status", {} if not verb else values, before=values if not verb else None)


def test_only_the_values_of_free_text_would_pass_their_own_pattern_as_a_placeholder() -> None:
    # Which is why the check refuses a placeholder before the pattern (review/code/OWNER-9.md 1, "What it gives"):
    # settings --value and mcp --browser take one as text; every other placeholder its pattern refuses too. The words
    # of init and mcp, which their rules refuse whatever they are, are not given back here.
    passing = []
    for verb, words, values, _ in GIVEN_BACK:
        row = VERBS.get(verb)
        if row is None:
            continue
        held = [item for item in (*row.options, *([row.words] if row.words else [])) if item.field in values]
        if all(item.pattern is None or full(item.pattern, words[-1]) for item in held):
            passing.append(" ".join(words))
    assert sorted(passing) == ["mcp --browser <browser>", "settings --value <value>"]


def test_a_masked_line_and_a_form_line_each_print_a_placeholder() -> None:
    masked = _line("settings", {"name": "vm_note", "value": TYPED}, refused=True)
    form = _line("destroy", {"names": PLACEHOLDER, "confirm": PLACEHOLDER})
    assert (masked, form) == ("aw settings vm_note --value <value>", "aw destroy <name>... --confirm <names>")
    # Pasted at a shell, neither runs the command it shows: < and > are redirections, and a line that ends in > is no
    # line a shell reads at all.
    for printed in (masked, form):
        ran = subprocess.run(
            ["/bin/sh", "-n", "-c", printed], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30
        )
        assert ran.returncode != 0, printed
    # Given back word by word, each is refused, never taken as the value it shows.
    for printed in (masked, form):
        with pytest.raises(parse.Refused, match="stands for"):
            parse.read(shlex.split(printed.replace("<", "'<").replace(">", ">'"))[1:])


def test_a_form_writes_its_placeholders_last() -> None:
    # review/code/H31.md 1: every option or word whose value is a placeholder, or is masked, after every part with a
    # real value, the host's flag among them; the words first, then the options in the table's order.
    assert _line("init", {"host": "far", "address": PLACEHOLDER}) == "aw init --host far --address <address>"
    assert printer.line("init", {"address": PLACEHOLDER, "no_prompt": True, "host": "far"}, refused=False) == (
        "aw init --host far --no-prompt --address <address>"
    )
    assert _line("host", {"name": PLACEHOLDER, "remove": True}) == "aw host --remove <name>"
    assert _line("code", {"name": PLACEHOLDER}, host_flag="lab2") == "aw code --host lab2 <name>"
    assert _line("code", {"name": PLACEHOLDER, "project": "app", "editor": PLACEHOLDER}) == (
        "aw code --project app <name> --editor <editor>"
    )
    assert _line("deploy", {"names": PLACEHOLDER, "tags": "claude_code"}) == "aw deploy --tags claude_code <name>..."
    masked = _line("deploy", {"names": ["alpha"], "set": ["a=b"], "verbosity": 2, "detach": True}, refused=True)
    assert masked == "aw deploy alpha -vv --detach --set <name>=<value>"
    # A line with no placeholder is as it was: the words straight after the verb, the options in the table's order.
    assert _line("code", {"name": "alpha", "project": "app"}, host_flag="lab2") == (
        "aw code alpha --project app --host lab2"
    )


def test_a_form_whose_one_placeholder_is_its_names_is_written_only_where_the_rest_is_refused() -> None:
    # <name>... ends in the file its > writes, so a shell runs the rest where a file named name is there: the printer
    # writes such a form only where the one check, its rules among them, refuses the rest (aw deploy --tags claude_code,
    # deploy.7's), and asks it without saying a rule's sentence, which names this very form.
    assert checks.check("deploy", {"tags": "claude_code"}) == rules.RULES["deploy.7"].check({"tags": "claude_code"})
    assert not checks.takes("deploy", {"tags": "claude_code"}) and checks.takes("deploy", {})
    # The five forms printer.py's docstring names (review/code/H36.md 3), and beside other words the check takes: each
    # its Unwritten, which cli/main.py says.
    for verb, values, flag in [
        ("deploy", {}, ""),
        ("deploy", {"detach": True}, ""),
        ("deploy", {"only": "configure"}, ""),
        ("login", {}, ""),
        ("login", {"only": "github"}, ""),
        ("login", {}, "lab2"),
        ("status", {}, ""),
        ("status", {"json": True}, ""),
        ("destroy", {}, ""),
    ]:
        for printing in (printer.line, printer.argv):
            with pytest.raises(printer.Unwritten, match="would leave a command the one check takes") as refused:
                printing(verb, {"names": PLACEHOLDER, **values}, host_flag=flag, refused=False)
            assert str(refused.value).startswith(f"error: a form of aw {verb} "), refused.value
    # Beside another placeholder, or a value masked, it is no line a shell reads: written.
    assert _line("deploy", {"names": PLACEHOLDER, "set": PLACEHOLDER}) == "aw deploy <name>... --set <name>=<value>"
    assert _line("deploy", {"names": PLACEHOLDER, "set": ["a=b"]}, refused=True) == (
        "aw deploy <name>... --set <name>=<value>"
    )


def _placeheld(verb: Verb) -> list[str]:
    """The fields of verb a form may give as PLACEHOLDER: each option that takes a value, and its words, but words it
    refuses whole (init's and mcp's <extra>), which no form names."""
    fields = [option.field for option in checks.options_of(verb) if option.kind in ("value", "repeatable")]
    return fields + ([verb.words.field] if verb.words is not None and not verb.words.refused else [])


def _real(verb: Verb, field: str) -> object:
    """A real value for one of verb's fields: its option's first sample of the round trip, or the verb's first words."""
    if verb.words is not None and field == verb.words.field:
        return _word_samples(verb.words)[0]
    [option] = [option for option in checks.options_of(verb) if option.field == field]
    return _samples(option)[0]


def _host_flags(verb: str, values: dict[str, object]) -> list[str | None]:
    """The host's flags a line of verb is printed with: none and one for a verb whose --host runs it against a host,
    "" and one for a verb with the shared --host, and none where the values give --host."""
    if printer.HOST in values:
        return [None]
    if verb in SHARED:
        return ["", "lab2"]
    return [None, "lab2"] if verb in printer.HOSTS_OWN else [None]


def _domain() -> list[tuple[str, dict[str, object], bool, str | None]]:
    """The printer's whole domain of forms, each as a verb, its values, refused and the host's flag: for every verb,
    each field a form may give as PLACEHOLDER, and each value of free text masked, alone; beside each other field
    given a real value, or a placeholder; and beside every other field given a real value at once. Each made from
    values as a run took them and as none has, and with each host's flag the verb takes."""
    cases = []
    for verb in VERBS.values():
        held = _placeheld(verb)
        words = [verb.words.field] if verb.words is not None and not verb.words.refused else []
        fields = [option.field for option in checks.options_of(verb)] + words
        shown = [(field, PLACEHOLDER) for field in held]
        shown += [(field, _real(verb, field)) for field in fields if field in printer.FREE_TEXT]
        for field, value in shown:
            others = [other for other in fields if other != field]
            company: list[dict[str, object]] = [{}]
            company += [{other: _real(verb, other)} for other in others]
            company += [{other: PLACEHOLDER} for other in held if other != field]
            company.append({other: _real(verb, other) for other in others})
            for more in company:
                values = {field: value, **more}
                for refused in (False, True) if value is PLACEHOLDER else (True,):
                    cases += [(verb.name, values, refused, flag) for flag in _host_flags(verb.name, values)]
    return cases


# A placeholder as the printer writes it, as one word of a form: <name>, <name>... and <name>=<value>.
PLACEHOLDER_WORD = re.compile(r"<[a-z-]+>(?:=<[a-z-]+>)?(?:\.\.\.)?")


def _placeholders_last(form: str) -> bool:
    """Whether no word with a real value stands after a placeholder in form: after its first placeholder, each word is
    a placeholder, or an option spelled before one."""
    words = shlex.split(form)
    first = next(index for index, word in enumerate(words) if PLACEHOLDER_WORD.fullmatch(word))
    after = words[first:]
    return all(
        PLACEHOLDER_WORD.fullmatch(word)
        or (word.startswith("-") and index + 1 < len(after) and PLACEHOLDER_WORD.fullmatch(after[index + 1]))
        for index, word in enumerate(after)
    )


# Each form pasted as a person pastes it, read by the shell that runs this, in the folder of its own its number names:
# the line is read as typed, and a syntax error ends only the subshell it is read in.
PASTED = """\
cd "$1" || exit 9
number=0
while IFS= read -r form; do
    ( cd "$number" && eval "$form" ) < /dev/null > /dev/null 2>&1
    number=$((number + 1))
done < "$2"
"""


def _paste(tmp_path: Path, shell: str, forms: list[str], held: bool) -> tuple[dict[int, list[str]], dict[int, list]]:
    """Each form pasted into shell in a folder of its own, which holds a file named as each of its placeholders where
    held, else nothing: the words the stand-in aw was given, by the form's number, where it ran; and the names of what
    the shell made in its folder, where it made any."""
    root = tmp_path / f"{Path(shell).name}-{'held' if held else 'empty'}"
    root.mkdir()
    for number, form in enumerate(forms):
        (root / str(number)).mkdir()
        for name in re.findall(r"<([^<>]+)>", form) if held else []:
            (root / str(number) / name).write_text("x\n")
    (tmp_path / "forms").write_text("".join(f"{form}\n" for form in forms))
    (tmp_path / "pasted.sh").write_text(PASTED)
    subprocess.run(
        [shell, str(tmp_path / "pasted.sh"), str(root), str(tmp_path / "forms")],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=600,
    )
    left, made = {}, {}
    for number, form in enumerate(forms):
        if (said_to := root / f"{number}.said").exists():
            left[number] = said_to.read_text().splitlines()
        named = set(re.findall(r"<([^<>]+)>", form)) if held else set()
        if there := sorted(path.name for path in (root / str(number)).iterdir() if path.name not in named):
            made[number] = there
    return left, made


@pytest.fixture
def pasted_aw(tmp_path, monkeypatch) -> str:
    """A stand-in aw that every line names, by its absolute path: it writes the words it was given into a file beside
    the folder it runs in, and does nothing else. The program as each line says it."""
    program = tmp_path / "bin" / "aw"
    program.parent.mkdir()
    program.write_text('#!/bin/sh\nfor word in "$@"; do printf \'%s\\n\' "$word"; done > "$(pwd).said"\n')
    program.chmod(0o755)
    monkeypatch.setattr(printer, "_program", printer.PROGRAM)
    printer.started_as(str(program))
    return printer.quoted(str(program))


def test_a_form_pasted_at_a_shell_never_leaves_a_whole_command(tmp_path, pasted_aw) -> None:
    # review/code/H31.md 1: held over the printer's whole domain, never a list of the forms the code writes, so a form
    # any module writes is held. Its placeholders last, no real value stands where a shell would take it for a file's
    # name: in a folder with no file of a placeholder's name nothing runs or is made, and in one with a file of each a
    # shell runs nothing, or words aw refuses. No form is an exception. Help's usage lines are pasted too.
    forms, unwritten = set(), []
    for verb, values, refused, flag in _domain():
        try:
            forms.add(printer.line(verb, values, refused=refused, host_flag=flag))
        except printer.Unwritten:
            unwritten.append((verb, values, refused, flag))
    printed = sorted(form for form in forms if "<" in form)
    usages = sorted(form for form in (helps.usage(verb) for verb in VERBS.values()) if "<" in form)
    assert len(printed) > 1000 and all(form.startswith(f"{pasted_aw} ") for form in printed + usages), len(printed)
    assert [form for form in printed if not _placeholders_last(form)] == []
    # Each placeholder of the table is in a form the printer writes.
    for verb in VERBS.values():
        own = [form for form in printed if form.startswith(f"{pasted_aw} {verb.name} ")]
        for item in (*checks.options_of(verb), *([verb.words] if verb.words is not None else [])):
            if item.field in _placeheld(verb):
                shown = placeholder(item) if isinstance(item, Words) else f"{checks.spelled(item)} {placeholder(item)}"
                assert any(shown in form for form in own), (verb.name, shown)
    # The printer refuses only a form whose one placeholder is its names, beside values the one check takes.
    assert unwritten
    for verb, values, refused, flag in unwritten:
        row = VERBS[verb]
        shown = {key for key, value in values.items() if value is PLACEHOLDER or (refused and key in printer.FREE_TEXT)}
        assert row.words is not None and row.words.plural and shown == {row.words.field}, (verb, values, refused)
        real = {key: value for key, value in values.items() if value is not PLACEHOLDER}
        assert checks.takes(verb, {**real, **({printer.HOST: flag} if flag else {})}), (verb, values)
    forms = printed + usages
    taken = {}
    for shell in ("/bin/sh", "bash"):
        assert _paste(tmp_path, shell, forms, held=False) == ({}, {}), shell
        left, _ = _paste(tmp_path, shell, forms, held=True)
        assert left, "no form reached a shell's words: the held folders hold nothing a form reads"
        for number, words in left.items():
            try:
                parse.read(words)
            except parse.Refused:
                continue
            taken[forms[number]] = words
    assert taken == {}


def test_the_forms_review_39_pasted_run_nothing_in_a_folder_holding_their_placeholder(tmp_path, pasted_aw) -> None:
    # review/code/H31.md 1: fleet/hosts.py's way out, once aw init --address <address> --host far, left aw init
    # --address far run in a folder holding a file named address, and engine/init.py's left init with the host's name as
    # its address; written placeholder last, no shell reads either, and nothing is made.
    forms = [
        said("{init}", {"init": Line("init", {"host": "far", "address": PLACEHOLDER})}),
        said("{init}", {"init": Line("init", {"address": PLACEHOLDER, "no_prompt": True, "host": "far"})}),
    ]
    assert forms == [
        f"{pasted_aw} init --host far --address <address>",
        f"{pasted_aw} init --host far --no-prompt --address <address>",
    ]
    for shell in ("/bin/sh", "bash"):
        assert _paste(tmp_path, shell, forms, held=True) == ({}, {}), shell


# ---------------------------------------------------------------- zsh's =word ----


def test_a_value_that_starts_with_equals_is_quoted_which_zsh_would_expand() -> None:
    # zsh, macOS's shell, expands a word that starts with = to the path of the command it names (review/code/H25.md 5):
    # shlex.quote leaves such a word bare, so the printer quotes it; an = later in a word is left as it is.
    assert _line("settings", {"name": "vm_note", "value": "=ls"}) == "aw settings vm_note --value '=ls'"
    assert _line("mcp", {"browser": "=ls"}) == "aw mcp --browser '=ls'"
    assert _line("deploy", {"names": ["alpha"], "set": ["vm_note==ls"]}) == "aw deploy alpha --set vm_note==ls"
    assert printer.quoted("=") == "'='" and printer.quoted("a=b") == "a=b" and printer.quoted("") == "''"
    for word in ("=ls", "=", "==x", "a b", "it's", "plain"):
        assert shlex.split(printer.quoted(word)) == [word], word


# ---------------------------------------------------------------- the program as it was started ----


def test_every_line_and_every_command_a_sentence_names_is_the_program_as_it_was_started(monkeypatch) -> None:
    monkeypatch.setattr(printer, "_program", printer.PROGRAM)
    printer.started_as("./aw")
    assert printer.program() == "./aw" and _line("status", {}) == "./aw status"
    # The one check's own sentences, a rule's and the table's, each through said.
    assert (checks.command("deploy"), checks.help_of("deploy")) == ("./aw deploy", "./aw help deploy")
    assert checks.no_such_command() == "error: ./aw has no such command (./aw help lists them)"
    assert checks.check("code", {}) == "error: code opens one VM: ./aw code <name>"
    assert said(SHARED_HOST.help or "").endswith("(./aw host lists them)")
    assert said("{line}", {"line": Line("vault")}) == "./aw vault"
    printer.started_as("/opt/my checkout/aw")
    assert _line("status", {}) == "'/opt/my checkout/aw' status"
    assert _argv("status", {}) == ["/opt/my checkout/aw", "status"]
    # Nothing said, or a word no line could print as it is, a byte that is no UTF-8 among them: aw.
    for word in (None, "", "-x/aw", "./a\x9bw", "./a‮w", "./a\nb", "./caf\udce9/aw"):
        printer.started_as(word)
        assert printer.program() == "aw", word


# Programs started from a path a shell would split or end at: with a space, with a ', and one longer than help's width
# with spaces in it, which a paragraph wrapped there would cut, were it not kept whole.
AWKWARD_PROGRAMS = ["/opt/my checkout/aw", "/opt/it's/aw", "/opt/" + " ".join(["dir"] * 30) + "/aw"]


@pytest.mark.parametrize("started", AWKWARD_PROGRAMS, ids=["space", "quote", "many-spaces"])
def test_started_from_a_path_a_shell_would_split_every_line_says_the_program_quoted(started: str, monkeypatch) -> None:
    # review/code/H25.md 1, 3 and 4: the lines, the sentences that name the program alone, help's usage lines and its
    # closing paragraph, each the program quoted as one word a shell reads back, never cut where it is wrapped.
    monkeypatch.setattr(printer, "_program", printer.PROGRAM)
    printer.started_as(started)
    program = shlex.quote(started)
    assert program != started and (len(program) > helps.WIDTH) is (started == AWKWARD_PROGRAMS[-1])
    assert _line("status", {}) == f"{program} status"
    assert checks.command("deploy") == f"{program} deploy" and checks.command("") == program
    assert said("{program}") == program
    assert checks.no_such_command() == f"error: {program} has no such command ({program} help lists them)"
    assert checks.no_option("", "--bogus") == (
        f"error: {program} has no --bogus option before its command ({program} help lists them)"
    )
    assert helps.usage(VERBS["deploy"]) == f"{program} deploy [<name>...]"
    closing = helps.closing()
    joined = " ".join(closing)
    assert f"exported or before {program}, moves the run logs" in joined
    assert f": {program} deploy alpha -vvv." in joined and f"({program} host lists them)" in joined
    # The program LOG_DIR goes before, the example and aw host each on one line, never cut where the paragraph wraps.
    for whole in (f"{program},", f"{program} deploy alpha -vvv", f"{program} host"):
        assert [line for line in closing if whole in line], (whole, closing)
    for line in (_line("status", {}), helps.usage(VERBS["status"]).replace(" [<name>...]", "")):
        assert _words_read("/bin/sh", line)[0] == started, line


def test_main_takes_what_the_launcher_said_out_of_the_environment_at_once(monkeypatch, capsys) -> None:
    monkeypatch.setattr(printer, "_program", printer.PROGRAM)
    monkeypatch.setenv(main.STARTED_AS, "./aw")
    assert main.main([str(REPO / "tools" / "aw"), "help", "deploy"]) == 0
    assert main.STARTED_AS not in os.environ and printer.program() == "./aw"
    assert capsys.readouterr().out.startswith("Usage: ./aw deploy [<name>...]\n")
    main.main([str(REPO / "tools" / "aw"), "help", "deploy"])
    assert printer.program() == "aw"


def test_a_line_the_printer_will_not_write_is_said_by_main_never_shown_as_a_traceback(monkeypatch, capsys) -> None:
    # review/code/H36.md 3: a handler that says a form the printer refuses (aw status <name>..., whose rest is taken)
    # meets its Unwritten, which main says in the printer's sentence as the program's own fault, and exits 1.
    monkeypatch.setattr(printer, "_program", printer.PROGRAM)
    monkeypatch.delenv(main.STARTED_AS, raising=False)
    form = Line("status", {"names": PLACEHOLDER}, host_flag="")
    with pytest.raises(printer.Unwritten) as refused:
        said("{line}", {"line": form})
    asked = []

    def handler(*given: object) -> int:
        asked.append(given)
        print(said("{line}", {"line": form}))
        return 0

    monkeypatch.setattr(dispatch, "dispatch", handler)
    assert main.main([str(REPO / "tools" / "aw"), "status"]) == EXITS["refused"] == 1
    out, err = capsys.readouterr()
    assert len(asked) == 1 and out == ""
    assert err.splitlines() == [printer.own_fault(refused.value)]
    assert printer.own_fault(refused.value) == f"{refused.value} (a fault of the program's own, not of the words typed)"
    assert str(refused.value).startswith("error: a form of aw status ") and "Traceback" not in err


# A handler's run that meets a line the printer will not write, in a child of the test's: a run with a call still in a
# thread ends its process at once (runs/attached.py's _end_now). main.main dispatches a handler that runs runs.run
# with a body that says aw status <name>... the way given: with no thread (plain), with a call still in a thread
# (thread, an asyncio.to_thread left under way), or in one step of a gather while the other waits in a thread
# (gather). Its argv: the lib folder, the checkout, the jobs folder and the way.
IN_A_RUN = r"""
import asyncio, sys, threading, time
lib, checkout, jobs_dir, way = sys.argv[1:5]
sys.path.insert(0, lib)
from pathlib import Path
from claude_vm import runs
from claude_vm.cli import dispatch, main
from claude_vm.schema.printer import said
from claude_vm.schema.words import PLACEHOLDER, Line

FORM = Line("status", {"names": PLACEHOLDER}, host_flag="")
CALLING = threading.Event()
KEPT = []


def waits():
    CALLING.set()
    time.sleep(600)


async def says():
    while way != "plain" and not CALLING.is_set():
        await asyncio.sleep(0.01)
    print(said("{line}", {"line": FORM}))
    return 0


async def body(ctx):
    if way == "thread":
        KEPT.append(asyncio.ensure_future(asyncio.to_thread(waits)))
    if way == "gather":
        await asyncio.gather(asyncio.to_thread(waits), says())
    return await says()


def handler(*given):
    draft = runs.Draft(
        Path(jobs_dir),
        "deploy",
        "aw deploy alpha",
        "pve",
        ["alpha"],
        {"kind": "terminal"},
        True,
        values={"names": ["alpha"]},
        lock_machines=[("pve", "alpha")],
    )
    return runs.run(draft, body)


dispatch.dispatch = handler
sys.exit(main.main([str(Path(checkout) / "tools" / "aw"), "status"]))
"""


@pytest.mark.parametrize("way", ["plain", "thread", "gather"])
def test_a_line_the_printer_will_not_write_in_a_handler_s_run_is_said_in_one_line_never_a_traceback(
    way, home, monkeypatch
) -> None:
    # review/code/H37.md 1: with no thread the error reaches main, which says it; with a call still in a thread the run
    # ends the process at once and main never sees it, so the run says it as main does: one line, exit 1.
    monkeypatch.setattr(printer, "_program", printer.PROGRAM)
    with pytest.raises(printer.Unwritten) as refused:
        said("{line}", {"line": Line("status", {"names": PLACEHOLDER}, host_flag="")})
    env = environment(home)
    env.pop(main.STARTED_AS, None)
    jobs_dir = paths.jobs_dir(paths.home(REPO))
    ended = subprocess.run(
        [sys.executable, "-I", "-c", IN_A_RUN, str(REPO / "lib"), str(REPO), str(jobs_dir), way],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        timeout=90,
        check=False,
    )
    assert (ended.returncode, ended.stdout) == (EXITS["refused"], ""), ended.stderr
    assert ended.stderr.splitlines() == [printer.own_fault(refused.value)], ended.stderr


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
    # Through a link in a folder whose name a shell would split and end at: the program quoted (review/code/H25.md 1).
    awkward = tmp_path / "it's my dir"
    awkward.mkdir()
    (awkward / "aw").symlink_to(LAUNCHER)
    quoted = run_launcher("help", "deploy", home=home, launcher=awkward / "aw", cwd=tmp_path, first_on_path=(other,))
    assert _program_said(quoted) == shlex.quote(str(awkward / "aw"))
    assert _words_read("/bin/sh", _program_said(quoted)) == [str(awkward / "aw")]
    for launcher in ("./aw", LAUNCHER):
        linked = run_launcher("help", "deploy", home=home, launcher=launcher, cwd=REPO, first_on_path=(on_path(home),))
        assert _program_said(linked) == "aw", launcher
    # tools/aw run directly, as the agents' server runs it, is told nothing and says aw; told, it says what it is told.
    assert _program_said(run_aw("help", "deploy", home=home)) == "aw"
    told = run_aw("help", "deploy", home=home, env={main.STARTED_AS: "./aw"})
    assert _program_said(told) == "./aw"


def test_a_launcher_started_through_a_folder_whose_name_is_no_utf_8_says_aw_and_help_prints(home, tmp_path) -> None:
    # review/code/H25.md 7: Linux takes such a name, Python reads the byte as a lone surrogate, and a UTF-8 terminal's
    # strict output ended aw help in a traceback; the program is aw then, as for any word a terminal would not show.
    folder = tmp_path / os.fsdecode(b"caf\xe9")
    try:
        folder.mkdir()
    except OSError:
        pytest.skip("this file system takes only UTF-8 names (macOS's)")
    (folder / "aw").symlink_to(LAUNCHER)
    locale = {"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}
    for words in (("help", "deploy"), ("help",)):
        result = run_launcher(*words, home=home, launcher=folder / "aw", cwd=tmp_path, env=locale)
        assert result.returncode == 0 and "Traceback" not in result.stderr, result.stderr
    said = run_launcher("help", "deploy", home=home, launcher=folder / "aw", cwd=tmp_path, env=locale)
    assert _program_said(said) == "aw"


# ---------------------------------------------------------------- the one printer ----

PRINTER = "lib/claude_vm/schema/printer.py"
# The modules that write a command line: the printer alone.
PRINTERS = {PRINTER}
# The functions outside it that name a command in a sentence, each with why.
NAMED_IN_A_SENTENCE = {
    ("lib/claude_vm/cli/dispatch.py", "not_built"): "today's just <verb>, which does a verb's work until the flip, "
    "when K14 takes the clause out: no command of aw's, so none the printer writes",
    ("lib/claude_vm/guest/layer1.py", "_unready"): "today's just install, which fills the checkout's Galaxy collections"
    " that layer 1 reads until F6a takes them and the sentence out: no command of aw's, so none the printer writes",
}
# The printer's names of the program: as it was started (program, _program) and its constant (PROGRAM). Only the
# printer names them: help says the program through the check's command, and main.py hands it on by started_as.
PROGRAM_NAMES = ("program", "_program", "PROGRAM")
PROGRAMS = {PRINTER}
# A sentence's field the printer fills with the program, and the modules whose strings may name it: the printer, and
# the check, whose command names the program alone.
PROGRAM_FIELD = "{" + printer.PROGRAM_FIELD + "}"
PROGRAM_FIELDS = {PRINTER, "lib/claude_vm/schema/check.py"}
# What a formatted value of an f-string is read as.
FIELD = "\x00"
# What may follow aw or just where a string names a command: a verb of the table, a recipe of the repository's own, a
# formatted value, a field of str.format or of a % template, a Template's $, an option, or nothing yet.
COMMAND_WORDS = set(VERBS) | {"lint", "test", "docs", "vagrant", "clean", "install"}
_WORD = "(?:" + "|".join(sorted(map(re.escape, COMMAND_WORDS))) + r")(?![\w-])"
_FILLED = re.escape(FIELD) + r"|\{|%|\$|-"
COMMAND = re.compile(
    # aw, which is no word of prose: before any of those, anywhere in a string.
    rf"(?<![\w/.-])(?:\./)?aw\s+(?:\Z|{_FILLED}|{_WORD})"
    # Today's just, an English word too: before a value, a field or an option; after run, a backtick or a quote; and
    # where no word of prose comes before it, a recipe the string ends at, or that a value, an option or a closing
    # mark follows. So prose that says just (just deploy it, you just install uv) is left alone.
    rf"|(?<![\w/.-])just\s+(?:\Z|{_FILLED})"
    rf"|(?:(?<=\b[Rr]un\s)|(?<=[`'\"(]))just\s+{_WORD}"
    rf"|(?<![\w/.-])(?<![A-Za-z]\s)just\s+{_WORD}(?=\Z|\s*(?:{_FILLED}|[`'\")]))"
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
    command of; just as a command, never as prose."""
    return COMMAND.search(text) is not None


def _strings(source: str) -> list[tuple[int, str, str]]:
    """Each string source builds, with its line and the function it is in ('' outside one): never a docstring, or a
    string standing alone as a statement."""
    found: list[tuple[int, str, str]] = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Expr) and _text(node.value) is not None:
            return
        text = _text(node)
        if text is not None:
            found.append((getattr(node, "lineno", 0), function, text))
            return
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(ast.parse(source), "")
    return found


def _commands_built(source: str) -> list[tuple[int, str]]:
    """Each string source builds that names a command, by its line and the function it is in."""
    return [(line, function) for line, function, text in _strings(source) if _names_a_command(text)]


def _program_fields(source: str) -> list[int]:
    """The lines of each string source builds that names a sentence's program field."""
    return [line for line, _, text in _strings(source) if PROGRAM_FIELD in text]


def _names_program(source: str) -> bool:
    """Whether source names the printer's program, as started or as its constant: an attribute of the printer's module
    under any name, or a name imported from it."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (
            (node.module or "") == "printer" or (node.module or "").endswith(".printer")
        ):
            if any(alias.name in PROGRAM_NAMES for alias in node.names):
                return True
    return bool(_references(tree, PROGRAM_NAMES, own=False))


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
    assert outside == {}, "name a command as a Line said through schema/printer.py's said, or print it with its line"
    assert named == set(NAMED_IN_A_SENTENCE), "an exception that names nothing any more: take it out"
    assert (REPO / PRINTER).is_file()


def test_only_the_printer_names_the_program() -> None:
    # review/code/H25.md 1: the program as it was started is reachable by printer.program() and printer._program, not
    # only by the constant, and a line made from either is a command outside the printer.
    naming = {path.relative_to(REPO).as_posix() for path in _new_modules() if _names_program(path.read_text())}
    assert naming <= PROGRAMS, sorted(naming - PROGRAMS)


@pytest.mark.parametrize(
    ("source", "names"),
    [
        ('x = f"{printer.program()} cancel {job}"', True),
        ("x = printer._program", True),
        ("x = printer.PROGRAM", True),
        ("from claude_vm.schema.printer import program", True),
        ("from ..schema.printer import PROGRAM as AW", True),
        ("from claude_vm.schema import printer as p\nx = p.program()", True),
        ("import claude_vm.schema.printer\nx = claude_vm.schema.printer._program", True),
        ("x = program()", False),
        ("x = printer.line('status', {}, refused=False)", False),
        ("printer.started_as('./aw')", False),
        ("x = checks.command('')", False),
    ],
)
def test_the_scan_sees_the_program_named_by_any_road(source: str, names: bool) -> None:
    assert _names_program(source) is names


def test_only_the_printer_and_the_check_name_a_sentence_s_program_field() -> None:
    # review/code/H25.md 1: said("{program} cancel {job}", ...) is a command by hand, through the sanctioned said.
    found = {
        where: lines
        for path in _new_modules()
        if (where := path.relative_to(REPO).as_posix()) not in PROGRAM_FIELDS
        and (lines := _program_fields(path.read_text()))
    }
    assert found == {}, "a sentence names a command as a Line; the program alone is the check's command('')"
    assert _program_fields((REPO / "lib/claude_vm/schema/check.py").read_text()), "the check's command names it"
    assert _program_fields('x = said("{program} cancel {job}", {"job": job})') == [1]
    assert _program_fields('x = f"{{program}} cancel"') == [1]
    assert _program_fields('"""{program} in a docstring is said by nothing."""') == []


# The module review/code/H25.md planted, with the three roads it took to a command outside the printer.
PLANTED = """
from claude_vm.schema import printer
from claude_vm.schema.printer import line as say
from claude_vm.schema.printer import said


def by_the_program(job):
    return f"{printer.program()} cancel {job}"


def by_the_field(job):
    return said("{program} cancel {job}", {"job": job})


def by_a_template(verb):
    return "aw %s --only configure" % verb


def by_an_alias():
    return say("logs", {})
"""


def test_the_module_the_review_planted_fails_every_guard() -> None:
    assert _names_program(PLANTED)
    assert _program_fields(PLANTED) == [12]
    assert _commands_built(PLANTED) == [(16, "by_a_template")]
    assert _printer_calls(PLANTED) == [(20, False)]


# ---------------------------------------------------------------- the check's command, by any road ----

CHECK = "lib/claude_vm/schema/check.py"
# The modules that may use the check's command, the program and a verb as the printer writes them, each with why: each
# refuses, and help writes its usage lines and its closing paragraph's program with it. Anywhere else, words of a
# module's own joined after it make a command line no other scan sees, its values unquoted, unmasked and held by no
# check (review/code/H27.md 1): a line to run is the printer's line, and a sentence names a command as a Line, said.
COMMAND_USERS = {
    CHECK: "the one check's refusals, each naming the command it refuses and where its options are listed",
    PRINTER: "the printer's refusals of a line it will not write, raised as Unwritten, each naming the verb's command",
    "lib/claude_vm/cli/parse.py": "the parser's refusals, argparse's errors said in the project's own sentences, and "
    "the parser's prog, which nothing prints: every error of argparse's is said by those sentences",
    "lib/claude_vm/cli/dispatch.py": "not_built's refusal of a verb no card has built yet",
    "lib/claude_vm/cli/help.py": "aw help's usage lines, the program and the verb before the words in brackets "
    "(aw deploy [<name>...]), a form no line of the printer's writes, and the program alone its closing paragraph "
    "names",
}


def _command_uses(source: str, own: bool = False) -> list[int]:
    """The line of each use of the check's command in source: an attribute of the check's module by any name a module
    binds to it or its package, or the dotted name, a name imported from it under any alias, and in the check's own
    source (own) the name itself; a call, or any other use, a name bound to it among them."""
    return sorted(node.lineno for node in _references(ast.parse(source), ("command",), own, CHECK_MODULE))


def test_only_the_modules_that_refuse_and_help_use_the_check_s_command() -> None:
    # review/code/H27.md 1, the third road H25.md 1 named: f"{checks.command('cancel')} {job}" is a command written by
    # hand, the program quoted and the rest not, and passes every scan of strings above.
    using = {}
    for path in _new_modules():
        where = path.relative_to(REPO).as_posix()
        if lines := _command_uses(path.read_text(), own=where == CHECK):
            using[where] = lines
    outside = {where: lines for where, lines in using.items() if where not in COMMAND_USERS}
    assert outside == {}, "print the line with the printer's line, or name it in a sentence as a Line said through said"
    assert set(using) == set(COMMAND_USERS), "a module named that uses the check's command no more: take it out"
    assert all(why.strip() for why in COMMAND_USERS.values())


@pytest.mark.parametrize(
    ("source", "lines"),
    [
        # The roads review/code/H27.md 1 planted, through every binding: the module by an alias, as schema/'s own name
        # it, relatively, by its package and by its whole dotted name; the function imported under any alias; a name
        # bound to it, which a later call reaches.
        ("from claude_vm.schema import check as checks\nx = f\"{checks.command('login')} {vm} --only github\"", [2]),
        ("from claude_vm.schema import check\nx = f\"{check.command('')} cancel {job}\"", [2]),
        ("from ..schema import check as c\nx = c.command('cancel') + ' ' + job", [2]),
        ("from claude_vm import schema\nx = schema.check.command('x')", [2]),
        ("import claude_vm.schema as s\nx = s.check.command('x')", [2]),
        ("import claude_vm.schema.check\nx = claude_vm.schema.check.command('x')", [2]),
        ("import claude_vm.schema.check as c\nx = c.command('x')", [2]),
        ("from claude_vm.schema.check import command as said_as\nx = said_as('cancel')", [2]),
        ("from .check import command\nx = command('cancel')", [2]),
        ("from claude_vm.schema import check as checks\nnamed = checks.command\nx = named('x')", [2]),
        # Not the check's: today's claude_vm.checks, a command of the module's own, another of the check's names.
        ("from claude_vm import checks\nx = checks.command('x')", []),
        ("x = command('x')", []),
        ("from claude_vm.schema import check as checks\nx = checks.help_of('x')", []),
    ],
)
def test_the_scan_of_the_check_s_command_follows_every_name(source: str, lines: list) -> None:
    assert _command_uses(source) == lines


def test_the_check_s_own_command_is_read_by_its_name() -> None:
    assert _command_uses("def command(verb):\n    return verb\n\nx = command('x')", own=True) == [4]


# The module review/code/H27.md 1 planted, by its four plain roads: the check's command, and the program alone, with
# words of the module's own after it; and a line and a sentence the printer made, with words of the module's own joined
# to them. The first two fail the guard above; the last two pass it and every scan of strings, and fail the scan of a
# printer's line extended (review/code/H31.md 2, below).
PLANTED_COMMAND = """
from claude_vm.schema import check as checks
from claude_vm.schema import printer
from claude_vm.schema.words import Line


def by_the_command(vm):
    return f"{checks.command('login')} {vm} --only github"


def by_the_program_alone(job):
    return f"{checks.command('')} cancel {job}"


def by_a_line_and_words(job):
    return printer.line("cancel", {}, refused=False) + " " + job


def by_a_sentence_and_words(value):
    return printer.said("{line} vm_note --value " + value, {"line": Line("settings")})
"""


def test_the_module_h27_s_review_planted_fails_the_guard_of_the_check_s_command() -> None:
    assert _command_uses(PLANTED_COMMAND) == [8, 12]
    # The printer's own output with words joined to it: no scan of strings sees it; the scan of a line extended does.
    seen = (
        _names_program(PLANTED_COMMAND),
        _program_fields(PLANTED_COMMAND),
        _commands_built(PLANTED_COMMAND),
        [said_it for _, said_it in _printer_calls(PLANTED_COMMAND)],
    )
    assert seen == (False, [], [], [True])
    assert _extended(PLANTED_COMMAND) == [16, 20]


# ---------------------------------------------------------------- a printer's line, whole ----

# The calls that build a string from what they are given, which a line among it would be extended by: but a join by a
# newline whose every item is a line, which puts each whole on a line of its own (review/code/H36.md 1).
JOINING = ("join", "format", "format_map")
# A line's own methods that hand it on, whole or cut, as the line: what they give is held as the line is. A line's
# replace is found wherever it is, since a line is never edited.
HANDED_ON = ("strip", "lstrip", "rstrip", "ljust", "rjust", "center")
EDITED = ("replace",)
# What may follow a printer's line in a string that does not end there: punctuation, which ends the line's words, or a
# space and a word of prose, letters alone, up to the next punctuation or field holding no word that starts with -.
_PUNCTUATION = re.compile(r"\s*[^\w\s{-]")
_PROSE = re.compile(r"\s+([A-Za-z]+(?:'[a-z]+)?)(?=[\s,.;:)!?`'\"]|\Z)")
# The modules whose own function of a command is no line: the check's command and help_of name the command a refusal is
# about, the option it refuses after it, held to COMMAND_USERS by the guard of the check's command.
NOT_LINES = {CHECK: "command and help_of name the command a refusal is about, with the option refused after it"}


def _kept_whole(text: str, field_next: bool) -> bool:
    """Whether text, which follows a printer's line in a string, leaves it whole: the string's end, spaces at its end,
    punctuation, or a space and a word of prose that is no verb of the table's, with no word that starts with - before
    the next punctuation; never a field straight after it."""
    if not text.strip():
        return not field_next
    if _PUNCTUATION.match(text):
        return True
    words = re.split(r"[^\w\s'-]", text, maxsplit=1)[0]
    first = _PROSE.match(text)
    if first is None or re.search(r"(?:^|\s)-", words):
        return False
    return first.group(1).lower() not in COMMAND_WORDS


def _one_field(node: ast.AST | None) -> bool:
    """Whether node is a sentence that is one field and nothing else ("{line}"), which said makes a line."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.fullmatch(r"\{\w+\}", node.value) is not None
    )


def _fields_kept_whole(sentence: str, fields: set[str] | None) -> bool:
    """Whether each of fields in a sentence said (each of its fields, for None) is followed by what leaves a line
    whole."""
    for found in re.finditer(r"\{(\w+)\}", sentence):
        if fields is None or found.group(1) in fields:
            rest = sentence[found.end() :]
            if not _kept_whole(rest.split("{", 1)[0], "{" in rest):
                return False
    return True


def _a_line_object(node: ast.AST | None) -> bool:
    """Whether node makes a Line: Line(...), by its name or as an attribute."""
    return isinstance(node, ast.Call) and (
        getattr(node.func, "id", None) == "Line" or getattr(node.func, "attr", None) == "Line"
    )


def _extended(source: str, own: bool = False, functions: bool = True) -> list[int]:
    """The line of each place in source where a printer's line is extended, by any name source gives the printer or its
    functions. A printer's line is a call of line or argv, a call of said whose sentence is one field or ends, but for
    spaces, in a field a Line fills (LINES', or one given at the call in a mapping written there), a call of a function
    of the module's own that returns one (but where functions is false), a name bound to one in the same function, an
    f-string that ends in one, what hands one on (str of it, a subscript of it, its strip, lstrip, rstrip, ljust, rjust
    or center, any function of textwrap given it first), and lines joined by a newline (a join whose separator is a
    newline alone, of a list or tuple written there whose every item is a line, or of a comprehension of lines), which
    ends in its last. It is extended where it is an operand of + or %, an element of what a % template takes, or added
    to; anywhere in what join, format or format_map takes, but as one of lines joined by a newline; any argument of
    print but the last; edited by its replace; or followed in an f-string by a field, a word that starts with -, or
    anything but the string's end, punctuation or a word of prose. An f-string that holds one anywhere is never an
    operand of + or % nor an argument of print but the last; and a line with a newline added, the one argument of a
    write, is whole. And a sentence said is never built at its call (+, %, an f-string, join, format, format_map), and
    in a literal one, or a constant of the module's, each field a Line fills, given at the call or from LINES, or each
    of its fields where the call gives them as anything but a mapping written there, is followed as a line in an
    f-string must be. What it cannot see: a line handed to a function as its parameter, or carried in an attribute or a
    container, a mapping given to said among them."""
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    makers = {id(node) for node in _references(tree, ("line", "argv"), own)}
    sayers = {id(node) for node in _references(tree, ("said",), own)}
    wrappers = {id(node) for node in _references(tree, tuple(textwrap.__all__), False, "textwrap")}
    constants = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    # Filled below, once the functions that read them are defined: a line returned by a function of the module's own,
    # and a name bound to one.
    lines_returned: set[str] = set()
    bound: set[tuple[int, str]] = set()

    def said_of(node: ast.AST) -> bool:
        return isinstance(node, ast.Call) and id(node.func) in sayers

    def sentence_of(node: ast.Call) -> ast.AST | None:
        """said's sentence at its call: as written, or a constant of the module's that it names."""
        sentence = node.args[0] if node.args else None
        if isinstance(sentence, ast.Name) and sentence.id in constants:
            return ast.Constant(constants[sentence.id])
        return sentence

    def lines_filled(node: ast.Call) -> set[str] | None:
        """The fields a Line fills in said's call: LINES' names the call does not give, and each it gives a Line or a
        printer's line; None, each field, where it gives them as anything but a mapping written there."""
        filled = node.args[1] if len(node.args) > 1 else None
        if filled is not None and not isinstance(filled, ast.Dict):
            return None
        pairs = zip(filled.keys, filled.values, strict=True) if isinstance(filled, ast.Dict) else []
        keys = {key.value: value for key, value in pairs if isinstance(key, ast.Constant)}
        return (set(LINES) - set(keys)) | {key for key, value in keys.items() if a_line_given(value)}

    def ends_in_a_line(node: ast.Call) -> bool:
        """Whether said's sentence at node ends, but for spaces, in a field a Line fills: one of LINES' the call does
        not give, or one it gives a Line or a printer's line in a mapping written there. Where it gives its values
        otherwise, only LINES' names: a Line carried in a mapping is in a container, which the scan does not see."""
        sentence = sentence_of(node)
        if not (isinstance(sentence, ast.Constant) and isinstance(sentence.value, str)):
            return False
        last = re.search(r"\{(\w+)\}\s*\Z", sentence.value)
        if last is None:
            return False
        fields = lines_filled(node)
        return last.group(1) in (set(LINES) if fields is None else fields)

    def made(node: ast.AST | None) -> bool:
        return isinstance(node, ast.Call) and (
            id(node.func) in makers
            or (said_of(node) and bool(node.args) and (_one_field(node.args[0]) or ends_in_a_line(node)))
        )

    def scope(node: ast.AST) -> ast.AST:
        while node in parents and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            node = parents[node]
        return node

    def either(test, node: ast.AST | None) -> bool:
        """Whether test takes node, or either value of a conditional expression node is."""
        if isinstance(node, ast.IfExp):
            return either(test, node.body) or either(test, node.orelse)
        return test(node)

    def returning(test) -> set[str]:
        """The module's own functions that return what test takes, in either branch of a conditional."""
        return {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(isinstance(inner, ast.Return) and either(test, inner.value) for inner in ast.walk(node))
        }

    objects_returned = returning(_a_line_object)
    objects_named = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and _a_line_object(node.value)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    def called(node: ast.AST, names: set[str]) -> bool:
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names

    def handed_on(node: ast.AST) -> bool:
        """Whether node hands on a line, whole or cut: str of it, a subscript of it, a method of HANDED_ON's, or a
        function of textwrap given it first."""
        if isinstance(node, ast.Subscript):
            return a_line(node.value)
        if not isinstance(node, ast.Call):
            return False
        if isinstance(node.func, ast.Attribute) and node.func.attr in HANDED_ON:
            return a_line(node.func.value)
        given = node.args[0] if node.args else None
        named_str = isinstance(node.func, ast.Name) and node.func.id == "str" and len(node.args) == 1
        return given is not None and (named_str or id(node.func) in wrappers) and a_line(given)

    def joined_whole(node: ast.AST | None) -> bool:
        """Whether node is lines joined by a newline, each whole on a line of its own: a join whose separator is a
        newline alone, of a list or tuple written there whose every item is a line, or of a comprehension of lines."""
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join"
            and isinstance(node.func.value, ast.Constant)
            and node.func.value.value == "\n"
            and len(node.args) == 1
            and not node.keywords
        ):
            return False
        items = node.args[0]
        if isinstance(items, (ast.List, ast.Tuple)):
            return bool(items.elts) and all(a_line(item) for item in items.elts)
        return isinstance(items, (ast.ListComp, ast.GeneratorExp)) and a_line(items.elt)

    def a_line(node: ast.AST) -> bool:
        if made(node) or called(node, lines_returned) or handed_on(node) or joined_whole(node):
            return True
        if isinstance(node, ast.IfExp):
            return a_line(node.body) or a_line(node.orelse)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and (id(scope(node)), node.id) in bound:
            return True
        last = node.values[-1] if isinstance(node, ast.JoinedStr) and node.values else None
        return isinstance(last, ast.FormattedValue) and a_line(last.value)

    def holding(node: ast.AST) -> bool:
        """Whether node is an f-string that holds a line anywhere."""
        return isinstance(node, ast.JoinedStr) and any(
            isinstance(value, ast.FormattedValue) and a_line(value.value) for value in node.values
        )

    def a_line_given(node: ast.AST) -> bool:
        """Whether a value given at said's call fills its field with a command: a Line, a printer's line, or a name or
        a call of the module's own that is one."""
        named = isinstance(node, ast.Name) and node.id in objects_named
        return _a_line_object(node) or called(node, objects_returned) or named or a_line(node)

    def operand(node: ast.AST) -> ast.AST | None:
        """What node is given to, past the tuples and lists it is an element of."""
        parent = parents.get(node)
        while isinstance(parent, (ast.Tuple, ast.List)):
            parent = parents.get(parent)
        return parent

    def written(node: ast.AST, joined: ast.AST | None) -> bool:
        """Whether node, a line, is whole in joined: node with a newline added, the one argument of a write."""
        call = parents.get(joined) if joined is not None else None
        return (
            isinstance(joined, ast.BinOp)
            and isinstance(joined.op, ast.Add)
            and joined.left is node
            and isinstance(joined.right, ast.Constant)
            and joined.right.value == "\n"
            and isinstance(call, ast.Call)
            and getattr(call.func, "attr", None) == "write"
            and len(call.args) == 1
            and call.args[0] is joined
            and not call.keywords
        )

    lines_returned |= returning(made) if functions else set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and either(
            lambda value: made(value) or called(value, lines_returned), node.value
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            bound.update((id(scope(node)), target.id) for target in targets if isinstance(target, ast.Name))

    found: set[int] = set()
    for node in ast.walk(tree):
        if said_of(node) and node.args:
            sentence = sentence_of(node)
            built = isinstance(sentence, (ast.BinOp, ast.JoinedStr)) or (
                isinstance(sentence, ast.Call)
                and isinstance(sentence.func, ast.Attribute)
                and sentence.func.attr in JOINING
            )
            if built:
                found.add(node.lineno)
            elif isinstance(sentence, ast.Constant) and isinstance(sentence.value, str):
                if not _fields_kept_whole(sentence.value, lines_filled(node)):
                    found.add(node.lineno)
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            if (id(scope(node)), node.target.id) in bound or a_line(node.value):
                found.add(node.lineno)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            # print joins its arguments with a space: a line, or an f-string holding one, before another is extended.
            if any(a_line(given) or holding(given) for given in node.args[:-1]):
                found.add(node.lineno)
        if isinstance(node, ast.Attribute) and node.attr in EDITED and a_line(node.value):
            found.add(node.lineno)
        if holding(node) and not a_line(node):
            joined = operand(node)
            if isinstance(joined, ast.BinOp) and isinstance(joined.op, (ast.Add, ast.Mod)):
                found.add(node.lineno)
        if not a_line(node):
            continue
        parent = operand(node)
        if isinstance(parent, ast.BinOp) and isinstance(parent.op, (ast.Add, ast.Mod)) and not written(node, parent):
            found.add(node.lineno)
        above, below = parents.get(node), node
        while above is not None and not isinstance(above, ast.stmt):
            if (
                isinstance(above, ast.Call)
                and getattr(above.func, "attr", None) in JOINING
                and below is not above.func
                and not joined_whole(above)
            ):
                found.add(node.lineno)
                break
            above, below = parents.get(above), above
        holder = parents.get(node)
        if isinstance(holder, ast.FormattedValue) and isinstance(parents.get(holder), ast.JoinedStr):
            joined = parents[holder]
            rest = joined.values[joined.values.index(holder) + 1 :]
            text = rest[0].value if rest and isinstance(rest[0], ast.Constant) else ""
            field_next = len(rest) > (1 if rest and isinstance(rest[0], ast.Constant) else 0)
            if not _kept_whole(str(text), field_next):
                found.add(node.lineno)
    return sorted(found)


def test_no_module_of_the_new_engine_extends_a_printer_s_line() -> None:
    # review/code/H31.md 2: a line to run is the printer's whole line, and a sentence names its command as a Line: a
    # line with words of a module's own joined to it is unquoted, unmasked and held by no check.
    extended = {}
    for path in _new_modules():
        where = path.relative_to(REPO).as_posix()
        if lines := _extended(path.read_text(), own=where in PRINTERS, functions=where not in NOT_LINES):
            extended[where] = lines
    assert extended == {}, "say the line whole, or name the command as a Line said through said"
    assert all(why.strip() for why in NOT_LINES.values()) and set(NOT_LINES) <= set(COMMAND_USERS)
    # The check's own command, read as a line, is followed by the option a refusal names: what NOT_LINES is for.
    assert _extended((REPO / CHECK).read_text())


@pytest.mark.parametrize(
    ("source", "lines"),
    [
        # The roads review/code/H31.md 2 planted: a line with words joined by +, in an f-string, and a sentence said
        # with a value joined to it.
        ("x = printer.line('cancel', {}, refused=False) + ' ' + job", [1]),
        ("x = f\"{printer.line('cancel', {}, refused=False)} {job}\"", [1]),
        ("x = printer.said('{line} vm_note --value ' + value, {'line': Line('settings')})", [1]),
        # The roads review/code/H36.md 1 planted: print, an f-string holding a line then joined, a sentence that ends in
        # a line then joined, and what hands a line on: str, a slice, replace, textwrap.
        ("print(printer.line('cancel', {}, refused=False), job)", [1]),
        ("x = f\"{printer.line('cancel', {}, refused=False)} \" + job", [1]),
        ("x = printer.said('run {line}', {'line': Line('cancel')}) + ' ' + job", [1]),
        ("x = printer.said('{line} ', {'line': Line('cancel')}) + job", [1]),
        ("x = str(printer.line('cancel', {}, refused=False)) + ' ' + job", [1]),
        ("x = printer.line('cancel', {}, refused=False)[:] + ' ' + job", [1]),
        ("x = printer.line('cancel', {}, refused=False).replace('cancel', 'cancel ' + job)", [1]),
        ("import textwrap\nx = textwrap.fill(printer.line('cancel', {}, refused=False)) + ' ' + job", [2]),
        # And beside them: an f-string holding a line given to print or a % template, a line's strip, textwrap by
        # another name, a sentence ending in LINES' field, there too where the values are not written at the call, and a
        # sentence ending in a line with words before it, which is a line all the same (said whole, it is no operand).
        ("print(f\"{printer.line('cancel', {}, refused=False)} \", job, file=err)", [1]),
        ("x = f\"run {printer.line('cancel', {}, refused=False)} for %s\" % job", [1]),
        ("x = printer.line('cancel', {}, refused=False).strip() + job", [1]),
        ("from textwrap import indent as pad\nx = pad(printer.line('logs', {}, refused=False), '  ') + job", [2]),
        ("x = printer.said('see {help}') + ' deploy'", [1]),
        ("x = printer.said('run {help}', filled) + job", [1]),
        ("x = 'error: ' + printer.said('run {line}', {'line': Line('cancel')})", [1]),
        # Lines joined by a newline are a line, the last: extended, as any line.
        (
            "a = printer.line('logs', {}, refused=False)\n"
            "x = '\\n'.join([a, printer.line('status', {}, refused=False)]) + job",
            [2],
        ),
        ("x = '\\n'.join([printer.line('logs', {}, refused=False), job])", [1]),
        ("out.write(printer.line('logs', {}, refused=False) + ' ' + job)", [1]),
        ("out.write(printer.line('logs', {}, refused=False) + '\\n' + job)", [1]),
        # By any name, and through a name bound to it, a function that returns one, or an f-string that ends in one.
        ("from claude_vm.schema.printer import line as say\nx = say('logs', {}, refused=False) + ' -v'", [2]),
        ("from ..schema import printer as p\nx = '%s %s' % (p.line('logs', {}, refused=False), job)", [2]),
        ("def f(job):\n    run = printer.line('cancel', {}, refused=False)\n    return run + ' ' + job", [3]),
        ("def f(job):\n    run = printer.line('cancel', {}, refused=False)\n    run += job", [3]),
        ("def f(job):\n    run = printer.line('cancel', {}, refused=False)\n    return f'{run} --force'", [3]),
        ("def g():\n    return printer.said('{init}', {'init': Line('init')})\n\nx = g() + ' --check'", [4]),
        ("def g(v):\n    return printer.said('{x}', {}) if v else ''\n\nx = g(1) + ' -v'", [4]),
        ("x = (printer.line('logs', {}, refused=False) if a else '') + job", [1]),
        ("x = f\"run {printer.line('logs', {}, refused=False)}\" + job", [1]),
        ("x = ' '.join([printer.line('logs', {}, refused=False), job])", [1]),
        ("x = ' '.join(printer.argv('logs', {}, refused=False))", [1]),
        ("x = '{} {}'.format(printer.line('logs', {}, refused=False), job)", [1]),
        ("x = printer.argv('logs', {}, refused=False) + [job]", [1]),
        # In an f-string: a field, a word that starts with -, a verb, or a word a value would be after it.
        ("x = f\"{printer.line('logs', {}, refused=False)}{job}\"", [1]),
        ("x = f\"{printer.line('logs', {}, refused=False)} cancel\"", [1]),
        ("x = f\"{printer.line('logs', {}, refused=False)} vm_note\"", [1]),
        ("x = f\"{printer.line('logs', {}, refused=False)} and --all\"", [1]),
        # A sentence said: built at its call, or a Line's field, from the call, LINES or a constant, followed by words
        # of a command; every field where the values come in a mapping not written at the call.
        ("x = printer.said(f'{{line}} {value}', {'line': Line('logs')})", [1]),
        ("from claude_vm.schema.printer import said as say\nx = say('{line} ' + v, {'line': Line('x')})", [2]),
        ("x = printer.said('{line} {job}', {'line': Line('cancel'), 'job': job})", [1]),
        ("x = printer.said('{line} -v', {'line': Line('logs')})", [1]),
        ("x = printer.said('see {help} {more}', {'more': more})", [1]),
        ("FORM = '{line} --all'\nx = printer.said(FORM, {'line': Line('logs')})", [2]),
        ("ASK = Line('logs')\nx = printer.said('{line} {job}', {'line': ASK, 'job': job})", [2]),
        ("def ask():\n    return Line('logs')\n\nx = printer.said('{a} {b}', {'a': ask(), 'b': b})", [4]),
        ("x = printer.said('{line} {job}', filled)", [1]),
        # Whole: the string's end, punctuation, prose; a sentence whose fields hold text; a prefix of prose.
        ("x = f\"error: run {printer.line('init', {}, refused=False)}\"", []),
        ("x = f\"`{printer.line('logs', {}, refused=False)}` lists them\"", []),
        ("def f():\n    line = printer.said('{line}', {'line': Line('x')})\n    return f'{a} {line} rebuilds it.'", []),
        ("x = f\"{printer.said('{init}', {'init': Line('init')})} writes {what}\"", []),
        ("x = printer.said('{init} sets one up', {'init': Line('init')})", []),
        ("x = printer.said('{file} (its name) holds it: {init}', {'file': shown(f), 'init': Line('init')})", []),
        ("x = 'error: ' + printer.said('{init} sets one up', {'init': Line('init')})", []),
        ("x = printer.said('{names} {be} no VM', {'names': listed, 'be': 'is'})", []),
        ("x = printer.said(option.help or '')", []),
        # Whole lines, each on a line of its own (review/code/H36.md 1, what G6 prints): joined by a newline, from a
        # list or a comprehension, indented or bound to names; a line written with its end; print given a line last.
        ("x = '\\n'.join(printer.line(v, {}, refused=False) for v in verbs)", []),
        ("x = '\\n'.join([f\"  {printer.line(v, {}, refused=False)}\" for v in verbs])", []),
        (
            "def f():\n    a = printer.line('logs', {}, refused=False)\n"
            "    b = printer.line('status', {}, refused=False)\n    return '\\n'.join((a, b))",
            [],
        ),
        ("out.write(printer.line('logs', {}, refused=False) + '\\n')", []),
        ("print('run', printer.line('logs', {}, refused=False), file=sys.stderr)", []),
        ("x = printer.said('run {line}.', {'line': Line('cancel')}) + ' ' + job", []),
        # Not the printer's: another module's line, said or join.
        ("x = other.line('x') + job", []),
        ("x = ' '.join([checks.spelled(option), job])", []),
        # What the scan cannot see, said in §16.2 and CLAUDE.md, left for a later card: a line handed to a helper of the
        # module as its parameter, which appends; a line put in a list, a value appended, the list joined; a Line given
        # in a mapping not written at said's call, whose field the sentence ends in.
        ("x = printer.said('run {x}', filled) + job", []),
        (
            "def tail(run, job):\n    return run + ' ' + job\n\n"
            "x = tail(printer.line('cancel', {}, refused=False), job)",
            [],
        ),
        (
            "def f(job):\n    words = [printer.line('cancel', {}, refused=False)]\n"
            "    words.append(job)\n    return ' '.join(words)",
            [],
        ),
    ],
)
def test_the_scan_of_a_line_extended_sees_every_road(source: str, lines: list) -> None:
    assert _extended(source) == lines


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
        # A % template and a Template's $, as review/code/H25.md 6 found them missed.
        ('x = "aw %s" % verb', [(1, "")]),
        ('x = "aw %s --only %s" % (verb, stage)', [(1, "")]),
        ('x = Template("aw $verb")', [(1, "")]),
        # Today's just as a command: alone, before an option or a value, in backticks, after a sentence's colon.
        ('x = "just deploy"', [(1, "")]),
        ('x = "error: just deploy --only configure"', [(1, "")]),
        ("x = 'use `just test unit` first'", [(1, "")]),
        ('x = f"until then, use today\'s just {verb}"', [(1, "")]),
        # Prose that says just, which the scan leaves alone (review/code/H25.md 6).
        ('x = "just deploy it"', []),
        ('x = "you just install uv"', []),
        ('x = "it will just help"', []),
        # Prose that names the program, and what the scan cannot see: pieces joined at run time, which only the
        # printer's names of the program may make, and only the printer does.
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


def test_each_command_a_rule_or_the_table_names_is_said_whole(monkeypatch) -> None:
    # review/code/H31.md 2: a sentence said from the table or a rule is no literal at its call, so the scan of a line
    # extended reads it here: each field a Line fills (the rule's own, LINES, and those a broken rule gives, as each
    # case of test_aw_cli_rules.py's REFUSALS breaks it) is followed by its end, punctuation or prose.
    given: dict[str, set[str]] = {}
    for key, (words, _, _) in REFUSALS.items():
        code = key.rpartition("-")[0]
        if code not in rules.RULES:
            continue
        try:
            values = _read(words, monkeypatch, with_rules=False).values
        except parse.Refused:
            continue
        filled = rules.RULES[code].breaks(values) or {}
        given.setdefault(code, set()).update(name for name, value in filled.items() if isinstance(value, Line))
    assert given.get("deploy.7") == {"named", "every"}, given.get("deploy.7")
    for code, rule in rules.RULES.items():
        assert _fields_kept_whole(rule.sentence, set(rule.lines) | set(LINES) | given.get(code, set())), code
    texts = [verb.doc for verb in VERBS.values()] + [verb.words.help for verb in VERBS.values() if verb.words]
    texts += [option.help for verb in VERBS.values() for option in verb.options if option.help]
    for text in texts:
        assert _fields_kept_whole(text, set(LINES)), text
    assert not _fields_kept_whole("error: {remove} --force", {"remove"})


def test_the_check_s_sentences_name_the_command_as_the_printer_writes_it() -> None:
    assert checks.help_of("") == _line("help", {})
    for name in VERBS:
        assert checks.command(name) == _line(name, {}), name
        assert checks.help_of(name) == _line("help", {"command": name}), name


# ---------------------------------------------------------------- what schema/ imports ----

SCHEMA = sorted(f"claude_vm.schema.{path.stem}" for path in (REPO / "lib" / "claude_vm" / "schema").glob("[!_]*.py"))


@pytest.mark.parametrize("module", SCHEMA)
def test_each_module_of_schema_imports_alone_and_first_and_nothing_outside_schema(home, module) -> None:
    # The check, the rules and the printer import one another as modules, so any of them may be the first imported:
    # a name taken from one of them at its import would fail here for one order (schema/__init__.py). What importing
    # the package alone loads is taken away: what is left is what the module itself brings.
    added = imports_of(module=module, home=home) - imports_of(module="claude_vm.schema", home=home)
    assert module in added, sorted(added)
    assert sorted({name.split(".")[0] for name in added} - set(sys.stdlib_module_names) - {"claude_vm"}) == []
    assert (
        sorted(name for name in added if name.startswith("claude_vm.") and not name.startswith("claude_vm.schema"))
        == []
    )
    assert "argparse" not in added and "subprocess" not in added


def test_the_printer_is_a_module_of_schema_beside_the_check_and_the_rules() -> None:
    assert {MODULE, "claude_vm.schema.check", "claude_vm.schema.rules", "claude_vm.schema.words"} <= set(SCHEMA)
    assert not (REPO / "lib" / "claude_vm" / "cli" / "inverse.py").exists()


@pytest.mark.parametrize("module", ["claude_vm.pve.client", "claude_vm.vendors.trust"])
def test_pve_and_vendors_import_the_printer_when_they_are_imported_and_nothing_of_cli(home, module) -> None:
    # review/code/H25.md 2: the printer is schema/'s, which they import plainly; no import waits for a sentence.
    loaded = imports_of(module=module, home=home)
    assert {module, MODULE} <= loaded
    assert sorted(name for name in loaded if name.startswith("claude_vm.cli")) == []


def test_no_module_of_the_new_engine_imports_the_printer_inside_a_function() -> None:
    late = []
    for path in _new_modules():
        for function in ast.walk(ast.parse(path.read_text())):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Import) and any(alias.name == PRINTER_MODULE for alias in node.names):
                    late.append((path.relative_to(REPO).as_posix(), node.lineno))
                elif isinstance(node, ast.ImportFrom) and (
                    (node.module or "").endswith("printer") or any(alias.name == "printer" for alias in node.names)
                ):
                    late.append((path.relative_to(REPO).as_posix(), node.lineno))
    assert late == [], "import the printer at the top of the file"
