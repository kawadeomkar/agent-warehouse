"""The one check of a verb's values, which every entry runs: the command line's parser (lib/claude_vm/cli/parse.py), the
agents' server through aw, and the add-on's broker on JSON (docs/architecture/engine.md §16.2).

`check(verb, values)` holds each value the table of verbs and options (words.py) names to its kind, to not being the
placeholder a line that shows a command's form writes for it (words.py's `placeholder`: <value>, <name>), and to its
pattern, with patterns.py's `full`; then runs the rules between options its row names (rules.py), in that order; then
holds the verb's words to their number. It returns None, or the one sentence of the first refusal. A placeholder's
sentence says that it stands for a value, which says no value back: the placeholder is aw's own text. The words of a
verb that refuses any (words.py's `Words.refused`: init's and mcp's) have no placeholder refusal, since no word could
be put in its place: the verb's own rule refuses it, as it refuses any word. A pattern's sentence never says the value,
since a word typed may be a secret, and says what the value may be, in words (`form_of`: patterns.py's `form`, with the
option's or the words' own words and example where the table gives them), after the option's help, but for the words
of a verb that refuses any, which have no form to take; a rule's says back no value that may be a secret, and only one
that has met its pattern. Values come as the parser gives them and as JSON with exact types, and nothing is coerced: a
value option's is a string, a flag's a boolean, a repeatable option's a list of strings, a counted option's a whole
number; a verb's words are a list of strings where it takes several and a string where it takes one, or a list where
the parser was given more than one, which the rules see as given before the verb's one word is held to its number; and
`detach` is a flag of every verb whose run is a job. A key the verb has no option for is refused, and one whose value is
None is taken as not given. `formed(verb, values)` is the check but for the rules between options: what the printer of
a command holds a line to (printer.py).

Every command a sentence here names, the verb refused and where its options are listed among them, is the printer's,
through printer.py's `said`, so it is the program as it was started, quoted. `command` names the verb a refusal is
about, the program alone for the words before any verb, and is the only name outside the printer that fills a
sentence's program field: never a line to run, which the printer writes whole.

Standard library only, and schema/: every command reads it, a refusal among them. It and the printer call each other,
each importing the other as a module (schema/__init__.py).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from claude_vm.schema import printer, rules
from claude_vm.schema.patterns import form, full
from claude_vm.schema.words import (
    BEFORE,
    DETACH,
    DETACH_AGENT,
    VERBS,
    Line,
    Option,
    Verb,
    Words,
    placeholder,
)

# A key of JSON's values a sentence may name: a field's name, never a value.
_NAMEABLE = re.compile(r"[a-z][a-z0-9_]{0,40}")

# What a value of each kind of option is, in a sentence that refuses a value of another type.
_TYPES = {
    "value": "a string",
    "flag": "true or false",
    "repeatable": "a list of strings",
    "counted": "a whole number",
}

# The flag the parser gives every verb whose run is a job (words.py's DETACH): no row of the table holds it.
DETACH_FLAG = Option("detach", None, "flag", None, "detach", DETACH, DETACH_AGENT)


def spelled(option: Option) -> str:
    """An option as a person types it: --long, or -v for the one with no long form."""
    return f"--{option.long}" if option.long else f"-{option.short}"


def command(verb: str) -> str:
    """The command a refusal is about: the program and the verb, or the program alone for the words before any verb, as
    the printer writes them, the program quoted."""
    return printer.said("{line}", {"line": Line(verb, host_flag="")}) if verb else printer.said("{program}")


def help_of(verb: str) -> str:
    """Where a sentence sends a person for a verb's options, or for the verbs: aw help <verb>, or aw help."""
    return printer.said("{line}", {"line": Line("help", {"recipe": verb} if verb else {})})


def no_such_command() -> str:
    """The sentence of a verb the table has not got, which never says the word: it may be anything typed."""
    return f"error: {command('')} has no such command ({help_of('')} lists them)"


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


def form_of(item: Option | Words) -> str:
    """What a value of an option, or a verb's word, may be, as help and a refusal say it after its help: patterns.py's
    form of its pattern, with the item's own words and example in place of the pattern's where the table gives it any.
    Only for an item the table holds to a pattern: one that names none raises KeyError, as form does."""
    return form(item.pattern or "", item.value_words, item.example)


def _not_held(verb: str, option: Option) -> str:
    """The sentence of a value outside its option's pattern: the option's help and the form it may take, never the
    value."""
    helped = printer.said(option.help or "")
    return f"error: {command(verb)} {spelled(option)} takes no such value: {helped} {form_of(option)}"


def _stands_for(verb: str, option: Option) -> str:
    """The sentence of a value that is its option's placeholder, given back from a line that shows a command's form:
    the placeholder is aw's own text, so it says no value back."""
    return (
        f"error: {command(verb)} {spelled(option)}: {placeholder(option)} stands for the value: put yours in its place"
    )


def _word_stands_for(verb: str, words: Words, given: str) -> str:
    """The sentence of a verb's word that is their placeholder, <name>, or <name>... where the verb takes several."""
    what, where = (f"the {words.name}s", "their") if given.endswith("...") else (f"the {words.name}", "its")
    return f"error: {command(verb)}: {given} stands for {what}: put yours in {where} place"


def _placeholders(words: Words) -> tuple[str, ...]:
    """What a line or help writes in place of a verb's words: <name>, and <name>... where the verb takes several."""
    one = placeholder(words)
    return (one, f"{one}...") if words.plural else (one,)


def _wrong_type(verb: str, option: Option) -> str:
    return f"error: {command(verb)} {spelled(option)} takes {_TYPES[option.kind]}"


def word_not_held(verb: str, words: Words) -> str:
    """The sentence of a word outside its pattern: the words' help and the form a word may take, never the word; the
    help alone for the words of a verb that refuses any, which have no form to take. The names habit says it too, of a
    name it splits off a word that its pattern takes whole (cli/habits.py: alpha,-b)."""
    sentence = f"error: {command(verb)} takes no such {words.name}: {printer.said(words.help)}"
    return sentence if words.refused else f"{sentence} {form_of(words)}"


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
    else:
        if not _string(value):
            return _wrong_type(verb, option)
        items = [value]
    if placeholder(option) in items:
        return _stands_for(verb, option)
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
    # The words of a verb that refuses any are no word to put in a placeholder's place: its rule refuses <extra>.
    given = None if words.refused else next((item for item in items if item in _placeholders(words)), None)
    if given is not None:
        return _word_stands_for(verb, words, given)
    if words.pattern is not None and not all(full(words.pattern, item) for item in items):
        return word_not_held(verb, words)
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
    has not got, a value of the wrong type, its placeholder, or one outside its pattern, which never says the value;
    then the first rule between options its row names that the values break; then a word beyond what the verb takes."""
    row = VERBS.get(verb)
    if row is None:
        return no_such_command()
    return (
        _hold(verb, options_of(row), row.words, values)
        or rules.run(row.rules, values)
        or _number(verb, row.words, values)
    )


def formed(verb: str, values: Mapping[str, object]) -> str | None:
    """check, but for the rules between options: None where every value of values is one an option of verb takes, of
    its kind and held by its pattern, and its words are as many as it takes; else the sentence, which never says a
    value. What the printer of a command holds a line to (printer.py): a rule between options is the run's to say, and a
    line may name a run that one refuses."""
    row = VERBS.get(verb)
    if row is None:
        return no_such_command()
    return _hold(verb, options_of(row), row.words, values) or _number(verb, row.words, values)


def check_before(values: Mapping[str, object]) -> str | None:
    """check, for the words given before any verb (words.py's BEFORE)."""
    return _hold("", BEFORE, None, values)
