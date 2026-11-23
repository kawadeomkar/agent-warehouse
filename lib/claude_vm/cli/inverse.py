"""The printer of a command: the one way the new engine writes the command line a person types, from a verb and its
values (docs/architecture/engine.md §16.3). NEXT STEPS, status's repair lines, a refusal's way out, the lines a
detached run's start prints and the session notes on a machine all print through it.

`argv(verb, values)` is the line as its words, and `line(verb, values)` the same line as one string a person pastes,
each value quoted with shlex.quote where a shell would read it otherwise. Both read the one table
(lib/claude_vm/schema/words.py) and nothing else, in this order: the program, aw, always; the words given before the
verb (`before`: --no-lock, --attended=1, --detached-run), in the table's order; the verb, deploy's whole chain as its
one verb; its options in the table's order, --detach last as the parser gives it: a value as --long=value, so that one
that starts with - is read as a value, a flag bare, a counted option in one word, -v repeated (-vvv), a repeatable one
once for each value; then the verb's words as given; and last `host_flag`, where it is given: ` --host <name>`, as
today's lines end with claude_host_flag, on a verb that takes --host and was not given one. Parsing the line
(lib/claude_vm/cli/parse.py) gives the same values back.

The values are a verb's as the parser gives them, or as its handler receives them: `default_vm`, which the habits add
where a verb that takes several names was given none, is said by naming none. Each is held as the one check holds it,
but for the rules between options (lib/claude_vm/schema/check.py's `formed`): of its option's kind, held by its
pattern, and the verb's words as many as it takes; a value that is not raises ValueError with the check's sentence,
which never says the value, and so does a word that starts with -, which the parser would read as an option. A rule
between options is the run's to say, so a line may name a run that a rule refuses: deploy's question names the stage
before its answer meets the rules.

The table holds no option whose value is a secret, so nothing is masked but the two values of free text a person may
paste a secret into by mistake, --set's and settings --value's. A line made from the values of a run that was refused,
or that nothing has taken yet (`refused`), prints each of them as the name help gives its value, so a secret pasted
there is never said back.

Standard library only, and schema/.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping

from claude_vm.schema import check as checks
from claude_vm.schema.patterns import full
from claude_vm.schema.words import BEFORE, VERBS, Option, Verb

# The program every line names: the product's command line is aw alone (the owner's word 1).
PROGRAM = "aw"
# What the habits add to a verb's values (lib/claude_vm/cli/habits.py), which a line says by its words alone: no names,
# for the default VM or for every VM, as the verb takes none.
HABITS_OWN = ("default_vm",)
# The fields of the options whose value is free text a person may paste a secret into by mistake: --set, on every verb
# that takes it, whose run layer refuses a secret's name there (D5), and settings --value, which refuses a secret's
# value (D10). A line made from values no run has taken prints each as its option's metavar.
FREE_TEXT = ("set", "value")
# The option host_flag gives, and how it is given: today's claude_host_flag, a space and then the option and its value.
HOST = "host"
HOST_FLAG = f" --{HOST} "


def _given(values: Mapping[str, object]) -> dict[str, object]:
    """values as a line says them: a value that is None is not given, and the habits' own are said by the words."""
    return {key: value for key, value in values.items() if value is not None and key not in HABITS_OWN}


def _option(option: Option, value: object, masked: bool) -> list[str]:
    """The words that give option its value, held by the one check before: a value as --long=value, so that one that
    starts with - is read as a value; masked, the value is the name help gives it."""
    spelled = checks.spelled(option)
    if option.kind == "flag":
        return [spelled] if value is True else []
    if option.kind == "counted":
        times = value if isinstance(value, int) else 0
        return [f"-{option.short * times}"] if option.short and times else [spelled] * times
    if option.kind == "optional-value" and value is True:
        return [spelled]
    items = value if option.kind == "repeatable" and isinstance(value, list) else [value]
    shown = option.metavar or "VALUE"
    return [f"{spelled}={shown if masked else item}" for item in items]


def _host_flag(row: Verb, given: Mapping[str, object], host_flag: str) -> list[str]:
    """host_flag's words, held to its one form, ` --host <name>`, on a verb that takes --host and was not given it."""
    if not host_flag:
        return []
    option = next((option for option in row.options if option.long == HOST), None)
    name = host_flag.removeprefix(HOST_FLAG)
    if option is None or option.pattern is None:
        raise ValueError(f"error: {checks.command(row.name)} takes no --{HOST}, so no line of it ends with one")
    if HOST in given:
        raise ValueError(f"error: {checks.command(row.name)} takes --{HOST} once: in its values or as host_flag")
    if not host_flag.startswith(HOST_FLAG) or not name or not full(option.pattern, name):
        raise ValueError(f"error: host_flag is '{HOST_FLAG}' and a host's name, or empty")
    return [f"--{HOST}", name]


def _words(
    verb: str, values: Mapping[str, object], before: Mapping[str, object] | None, refused: bool
) -> tuple[Verb, dict[str, object], list[str]]:
    """The verb's row, its values as given, and the line's words up to the host flag; ValueError where the values are
    not what the one check takes, but for the rules between options."""
    row = VERBS.get(verb)
    if row is None:
        raise ValueError(checks.NO_SUCH_COMMAND)
    given, before_given = _given(values), _given(before or {})
    held = checks.check_before(before_given) or checks.formed(verb, given)
    if held:
        raise ValueError(held)
    words = [PROGRAM]
    for option in BEFORE:
        if option.field in before_given:
            words += _option(option, before_given[option.field], False)
    words.append(verb)
    for option in checks.options_of(row):
        if option.field in given:
            words += _option(option, given[option.field], refused and option.field in FREE_TEXT)
    if row.words is not None and row.words.field in given:
        typed = given[row.words.field]
        typed = [typed] if isinstance(typed, str) else list(typed) if isinstance(typed, list) else []
        if any(word.startswith("-") for word in typed):
            raise ValueError(f"error: {checks.command(verb)} takes no {row.words.name} that starts with -")
        words += typed
    return row, given, words


def argv(
    verb: str,
    values: Mapping[str, object],
    *,
    host_flag: str = "",
    before: Mapping[str, object] | None = None,
    refused: bool = False,
) -> list[str]:
    """The command line a person types for verb with values, as its words: aw first. before holds the words given
    before the verb, by their fields; host_flag ends the line, as today's claude_host_flag does; refused says that no
    run has taken values (one was refused, or none has started yet), and then a value of free text is its metavar.
    ValueError where a value is not one the one check takes, its rules between options aside."""
    row, given, words = _words(verb, values, before, refused)
    return words + _host_flag(row, given, host_flag)


def _quoted(word: str) -> str:
    """A word as a person pastes it: an option's value quoted where the shell would read it otherwise, the option's
    name left bare; an empty value as nothing after its =."""
    if word.startswith("-") and "=" in word:
        spelled, _, value = word.partition("=")
        return f"{spelled}={shlex.quote(value) if value else ''}"
    return shlex.quote(word)


def line(
    verb: str,
    values: Mapping[str, object],
    *,
    host_flag: str = "",
    before: Mapping[str, object] | None = None,
    refused: bool = False,
) -> str:
    """argv's line as one string a person pastes: its words quoted with shlex.quote, and host_flag last as given."""
    row, given, words = _words(verb, values, before, refused)
    _host_flag(row, given, host_flag)
    return " ".join(_quoted(word) for word in words) + host_flag
