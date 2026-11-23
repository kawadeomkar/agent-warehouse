"""The command line read against the one table of verbs and options (docs/architecture/engine.md §16.3).

`read(words)` takes the words typed after aw: the words before the verb (words.py's BEFORE), the verb, and its words
and options, split by `argparse` into the table's slots, for the one verb typed only, and held by the one check
(lib/claude_vm/schema/check.py). It returns what was given, or raises `Refused` with one sentence of the project's own:
argparse's own error text, usage block and exit status never reach a person, and no sentence says a value back.

`argparse` is set as the design requires: abbreviations off (a prefix of an option is an option the verb has not got),
its own help off (aw help is H6's, from the table), errors raised rather than printed and exited on, and its colours
off from Python 3.14, where they would write escape codes into a file. An option is taken as `--long value` and as
`--long=value`, which is how a value that starts with - is given; an option is given once, but `--set`, given once for
each setting, and `-v`, counted. Every verb whose run is a job takes `--detach` (words.py's `changes`), and no other.
`--`, which elsewhere ends the options, is refused wherever it stands, before argparse sees it: no verb takes a word
that starts with -, a value that does is given as `--option=value`, and argparse's intermixed parse would drop it and
read the words after it as options all the same.

Standard library only: a refusal imports nothing the engine needs.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field

from claude_vm.schema import check as checks
from claude_vm.schema.words import BEFORE, VERBS, Option, Verb

# The verb help prints with, where no verb is typed: aw alone is aw help, as just alone lists the recipes.
NO_VERB = "help"
# An option a sentence may name, as a person types one; anything else is "no such option", never said back.
_OPTION = re.compile(r"--?[a-z][a-z0-9-]{0,40}")
# argparse's dest for a verb's words: no field of the table starts with _.
_WORDS = "_words"
# What argparse says when an option lacks its value, or a flag is given one; and what an option given twice raises.
_LACKS = "expected"
_EXPLICIT = "ignored explicit argument"
_TWICE = "_given twice"
# The word that ends the options elsewhere, which aw refuses: argparse's intermixed parse takes it and drops it, and
# reads the words after it as options all the same.
END_OF_OPTIONS = "--"
# Python 3.14's argparse colours its text unless told not to; earlier ones have no such switch.
_PLAIN = {"color": False} if sys.version_info >= (3, 14) else {}


class Refused(Exception):
    """A command line refused before anything ran: the sentence says why, and never says a value given."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


@dataclass(frozen=True)
class Reading:
    """What the words typed came to: the verb, the words given before it, and the verb's values, each by the field the
    handler receives it by, with only what was given (a flag that was given is True)."""

    verb: str
    before: dict[str, object] = field(default_factory=dict)
    values: dict[str, object] = field(default_factory=dict)


class _Unreadable(Exception):
    """argparse's own error, which only reaches a person as the project's sentence."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        raise _Unreadable(message)


class _Once(argparse.Action):
    """A value, or a value or nothing, given once."""

    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            raise argparse.ArgumentError(self, _TWICE)
        setattr(namespace, self.dest, values)


class _Flag(argparse.Action):
    """A flag, given once."""

    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, default=None, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None):
            raise argparse.ArgumentError(self, _TWICE)
        setattr(namespace, self.dest, True)


def _spellings(option: Option) -> list[str]:
    return [f"--{option.long}"] * bool(option.long) + [f"-{option.short}"] * bool(option.short)


def _parser(verb: str, options: tuple[Option, ...], takes_words: bool) -> tuple[_Parser, dict[str, Option]]:
    """The parser of one verb's words, or of the words before any verb, with each option argparse names an error by."""
    parser = _Parser(prog=checks.command(verb), allow_abbrev=False, add_help=False, exit_on_error=False, **_PLAIN)
    named: dict[str, Option] = {}
    for option in options:
        spellings = _spellings(option)
        if option.kind == "flag":
            parser.add_argument(*spellings, dest=option.field, action=_Flag)
        elif option.kind == "repeatable":
            parser.add_argument(*spellings, dest=option.field, action="append")
        elif option.kind == "counted":
            parser.add_argument(*spellings, dest=option.field, action="count")
        elif option.kind == "optional-value":
            parser.add_argument(*spellings, dest=option.field, action=_Once, nargs="?", const=True)
        else:
            parser.add_argument(*spellings, dest=option.field, action=_Once)
        named["/".join(spellings)] = option
    if takes_words:
        parser.add_argument(_WORDS, nargs="*")
    return parser, named


def _refusal(verb: str, error: argparse.ArgumentError, named: dict[str, Option]) -> str:
    """The project's sentence for one of argparse's errors about an option: it never says what was typed with it."""
    option = named.get(error.argument_name or "")
    if option is None:
        return f"error: {checks.command(verb)} cannot read these words ({checks.help_of(verb)})"
    said = checks.spelled(option)
    if error.message == _TWICE:
        return f"error: {checks.command(verb)} takes {said} once"
    if error.message.startswith(_EXPLICIT):
        return f"error: {checks.command(verb)} {said} takes no value"
    if error.message.startswith(_LACKS):
        value = option.metavar or "VALUE"
        return (
            f"error: {checks.command(verb)} {said} takes a value: {said} {value}, or {said}={value} where the value"
            " starts with -"
        )
    return f"error: {checks.command(verb)} cannot read these words ({checks.help_of(verb)})"


def _parse(verb: str, options: tuple[Option, ...], row: Verb | None, words: list[str]) -> dict[str, object]:
    """One verb's words (or, with no row, the words before any verb) split into the fields given."""
    takes_words = row is not None and row.words is not None
    parser, named = _parser(verb, options, takes_words)
    try:
        namespace, extras = parser.parse_known_intermixed_args(words)
    except argparse.ArgumentError as error:
        raise Refused(_refusal(verb, error, named)) from None
    except _Unreadable:
        raise Refused(f"error: {checks.command(verb)} cannot read these words ({checks.help_of(verb)})") from None
    unknown = [extra for extra in extras if extra.startswith("-") and extra != "-"]
    if unknown:
        name = unknown[0].split("=", 1)[0]
        raise Refused(checks.no_option(verb, name if _OPTION.fullmatch(name) else None))
    if extras:
        raise Refused(checks.only_options(verb))
    given: dict[str, object] = {}
    for option in options:
        value = getattr(namespace, option.field)
        if value is not None:
            given[option.field] = value
    if takes_words and row is not None and row.words is not None:
        typed = getattr(namespace, _WORDS) or []
        if typed:
            given[row.words.field] = typed if row.words.plural or len(typed) > 1 else typed[0]
    return given


def no_end_of_options(verb: str) -> str:
    """The sentence of a -- given anywhere, before the verb (verb "") or after it."""
    return f"error: {checks.command(verb)} takes no --: a value that starts with - is given as --option=value"


def _split(words: list[str]) -> tuple[list[str], list[str]]:
    """The words before the verb, and the verb with what follows it. A word before the verb starts with -; a value
    option of those takes the next word as its value unless it is given with =."""
    by_spelling = {spelling: option for option in BEFORE for spelling in _spellings(option)}
    i = 0
    while i < len(words) and words[i].startswith("-") and words[i] != "-":
        name, given, _ = words[i].partition("=")
        option = by_spelling.get(name)
        i += 2 if option is not None and option.kind == "value" and not given else 1
    return words[:i], words[i:]


def read(words: list[str]) -> Reading:
    """What the words typed after aw come to, held by the one check; raises Refused with the sentence otherwise."""
    before_words, rest = _split(words)
    if END_OF_OPTIONS in before_words:
        raise Refused(no_end_of_options(""))
    before = _parse("", BEFORE, None, before_words)
    refused = checks.check_before(before)
    if refused:
        raise Refused(refused)
    verb, *verb_words = rest or [NO_VERB]
    row = VERBS.get(verb)
    if row is None:
        raise Refused(checks.NO_SUCH_COMMAND)
    if END_OF_OPTIONS in verb_words:
        raise Refused(no_end_of_options(verb))
    values = _parse(verb, checks.options_of(row), row, verb_words)
    refused = checks.check(verb, values)
    if refused:
        raise Refused(refused)
    return Reading(verb, before, values)
