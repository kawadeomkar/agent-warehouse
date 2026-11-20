"""The one check of a verb's values, which every entry runs: the command line's parser (lib/claude_vm/cli/parse.py), the
agents' server through aw, and the add-on's broker on JSON (docs/architecture/engine.md §16.2).

`check(verb, values)` holds each value the table of verbs and options (words.py) names to its kind and its pattern,
with patterns.py's `full`; then runs the rules between options its row names (rules.py), in that order; then holds the
verb's words to their number. It returns None, or the one sentence of the first refusal. A pattern's sentence never
says the value, since a word typed may be a secret; a rule's says one back only where today's does, once the value has
met its pattern. Values come as the parser gives them and as JSON with exact types, and nothing is coerced: a value
option's is a string, a flag's a boolean, a repeatable option's a list of strings, a counted option's a whole number,
an optional value's true (given alone) or a string; a verb's words are a list of strings where it takes several and a
string where it takes one, or a list where the parser was given more than one, which the rules see as given before the
verb's one word is held to its number; and `detach` is a flag of every verb whose run is a job. A key the verb has no
option for is refused, and one whose value is None is taken as not given.

Standard library only: every command reads it, a refusal among them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from claude_vm.schema import rules
from claude_vm.schema.patterns import full
from claude_vm.schema.words import BEFORE, DETACH, DETACH_AGENT, VERBS, Option, Verb, Words

# The sentence of a verb the table has not got, which never says the word: it may be anything typed.
NO_SUCH_COMMAND = "error: aw has no such command (aw help lists them)"
# A key of JSON's values a sentence may name: a field's name, never a value.
_NAMEABLE = re.compile(r"[a-z][a-z0-9_]{0,40}")

# What a value of each kind of option is, in a sentence that refuses a value of another type.
_TYPES = {
    "value": "a string",
    "flag": "true or false",
    "repeatable": "a list of strings",
    "counted": "a whole number",
    "optional-value": "true or a string",
}

# The flag the parser gives every verb whose run is a job (words.py's DETACH): no row of the table holds it.
DETACH_FLAG = Option("detach", None, "flag", None, "detach", DETACH, DETACH_AGENT)


def spelled(option: Option) -> str:
    """An option as a person types it: --long, or -v for the one with no long form."""
    return f"--{option.long}" if option.long else f"-{option.short}"


def command(verb: str) -> str:
    """The command a sentence names: aw and the verb, or aw alone for the words before any verb."""
    return f"aw {verb}" if verb else "aw"


def help_of(verb: str) -> str:
    return f"aw help {verb}" if verb else "aw help"


def options_of(verb: Verb) -> tuple[Option, ...]:
    """A verb's options as the parser takes them: the row's, and --detach where its run is a job."""
    return (*verb.options, DETACH_FLAG) if verb.changes else verb.options


def only_options(verb: str) -> str:
    """The sentence of a word given to a verb that takes none, or refuses any (init's): never the word."""
    return f"error: {command(verb)} takes only its options ({help_of(verb)}), and nothing after them"


def one_word(verb: str, words: Words) -> str:
    """The sentence of a second word given to a verb that takes one."""
    return f"error: {command(verb)} takes one {words.name} ({help_of(verb)})"


def no_option(verb: str, name: str | None) -> str:
    """The sentence of an option the verb has not got, named only where name could be one, never with a value."""
    said = f" {name}" if name else " such"
    where = " before its command" if not verb else ""
    return f"error: {command(verb)} has no{said} option{where} ({help_of(verb)} lists them)"


def _not_held(verb: str, option: Option) -> str:
    return f"error: {command(verb)} {spelled(option)} takes no such value: {option.help}"


def _wrong_type(verb: str, option: Option) -> str:
    return f"error: {command(verb)} {spelled(option)} takes {_TYPES[option.kind]}"


def _word_not_held(verb: str, words: Words) -> str:
    return f"error: {command(verb)} takes no such {words.name}: {words.help}"


def _words_type(verb: str, words: Words) -> str:
    what = "a list of strings" if words.plural else "a string"
    return f"error: {command(verb)} takes its {words.name} as {what}"


def _string(value: object) -> bool:
    return type(value) is str


def _option(verb: str, option: Option, value: object) -> str | None:
    """The sentence that refuses value for option, or None where it holds."""
    kind = option.kind
    if kind == "flag":
        return None if type(value) is bool else _wrong_type(verb, option)
    if kind == "counted":
        return None if type(value) is int and value >= 0 else _wrong_type(verb, option)
    if kind == "repeatable":
        if type(value) is not list or not all(_string(item) for item in value):
            return _wrong_type(verb, option)
        items = value
    elif kind == "optional-value":
        if value is True:
            return None
        if not _string(value):
            return _wrong_type(verb, option)
        items = [value]
    else:
        if not _string(value):
            return _wrong_type(verb, option)
        items = [value]
    if option.pattern is not None and not all(full(option.pattern, item) for item in items):
        return _not_held(verb, option)
    return None


def _words(verb: str, words: Words, value: object) -> str | None:
    """The sentence that refuses a verb's words by their type or their pattern, or None where they hold. Their number is
    held after the rules (_number), which say today's sentence for more than one where today's has one."""
    if words.plural:
        if type(value) is not list or not all(_string(item) for item in value):
            return _words_type(verb, words)
        items = value
    elif type(value) is list and len(value) > 1 and all(_string(item) for item in value):
        # More than one where one is taken: the parser hands them on as a list, as JSON would give them.
        items = value
    elif _string(value):
        items = [value]
    else:
        return _words_type(verb, words)
    if words.pattern is not None and not all(full(words.pattern, item) for item in items):
        return _word_not_held(verb, words)
    return None


def _number(verb: str, words: Words | None, values: Mapping[str, object]) -> str | None:
    """The sentence of words given beyond what the verb takes, once its rules have had their say: a second word where it
    takes one, and any word where it refuses every one (init's, whose rule says so first), unsaid."""
    if words is None or values.get(words.field) is None:
        return None
    if words.pattern is None:
        return only_options(verb)
    if not words.plural and type(values[words.field]) is list:
        return one_word(verb, words)
    return None


def _hold(verb: str, options: tuple[Option, ...], words: Words | None, values: Mapping[str, object]) -> str | None:
    by_field = {option.field: option for option in options}
    for field, value in values.items():
        if value is None:
            continue
        if words is not None and field == words.field:
            refused = _words(verb, words, value)
        elif field in by_field:
            refused = _option(verb, by_field[field], value)
        else:
            refused = no_option(verb, field if _string(field) and _NAMEABLE.fullmatch(field) else None)
        if refused:
            return refused
    return None


def check(verb: str, values: Mapping[str, object]) -> str | None:
    """None where every value of values holds for verb, else the one sentence of the first refusal: an option the verb
    has not got, a value of the wrong type, or one outside its pattern, which never says the value; then the first rule
    between options its row names that the values break; then a word beyond what the verb takes."""
    row = VERBS.get(verb)
    if row is None:
        return NO_SUCH_COMMAND
    return (
        _hold(verb, options_of(row), row.words, values)
        or rules.run(row.rules, values)
        or _number(verb, row.words, values)
    )


def check_before(values: Mapping[str, object]) -> str | None:
    """check, for the words given before any verb (words.py's BEFORE)."""
    return _hold("", BEFORE, None, values)
