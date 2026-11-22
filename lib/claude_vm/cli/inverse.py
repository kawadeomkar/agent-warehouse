"""The printer of a command: the one way the new engine writes the command line a person types, from a verb and its
values (docs/architecture/engine.md §16.3). NEXT STEPS, status's repair lines, a refusal's way out, the lines a
detached run's start prints and the session notes on a machine all print through it, and every sentence of the new
engine that names a command names it as a Line, said through `said` (schema/words.py's `said` hands it here).

`argv(verb, values, ...)` is the line as its words, and `line(verb, values, ...)` the same line as one string a person
pastes, quoted for a POSIX shell, sh, bash or dash (shlex.quote), wherever such a shell would read a word otherwise.
Both read the one table (lib/claude_vm/schema/words.py) and nothing else, in this order: the program as it was started
(`program`); the words given before the verb (`before`: --attended 1, --detached-run), in the table's order; the verb,
deploy's whole chain as its one verb; the verb's words as given, straight after it; its options in the table's order,
--detach last as the parser gives it: a value as --long value, and as --long=value only where it starts with - or is
empty, so that the parser reads it as a value; a flag bare; a counted option in one word, -v repeated (-vvv); a
repeatable one once for each value; and last the host's flag, --host <name>. So a line reads as a person types it, and
parsing it (lib/claude_vm/cli/parse.py) gives the same values back.

The program is aw where aw on PATH is this checkout's launcher, else what the person typed to start it (./aw), as the
launcher at the checkout's top says and main.py hands on (`started_as`); aw where nothing says otherwise, as for
tools/aw run directly and the runs of the agents' server. A program word no line could print as it is, one with a
character a terminal would not show or one that starts with -, is aw.

The values are a verb's as the parser gives them, or as its handler receives them: `default_vm`, which the habits add
where a verb that takes several names was given none, is said by naming none, and a value that is off (None, a flag
not given, a count of 0, an empty list) is not said. Each is held as the one check holds it, but for the rules between
options (lib/claude_vm/schema/check.py's `formed`): of its option's kind, held by its pattern, and the verb's words as
many as it takes. A value that is not raises ValueError with the check's sentence, which never says the value, and so
does a word that starts with -, which the parser would read as an option, and a value or a word that holds a character
of Unicode's categories Cc, Cf, Zl or Zp, which a terminal would act on or not show as it is (a C1 control, a bidi
override, a line separator). A rule between options is the run's to say, so a line may name a run that a rule
refuses: deploy's question names the stage before its answer meets the rules. A value or a verb's words given as
`PLACEHOLDER` are named by the name help gives them, <name>, <name>... for several and an option's VALUE, never quoted:
such a line shows a command's form (aw destroy <name>... --confirm CONFIRM).

`host_flag` is the host a line names. Every caller of a verb whose row has the shared --host (deploy, code, login,
status, destroy) gives it where the values give no --host: a host's name, or "" where the fleet has one host or the
line reaches the host its own command does; None is a ValueError there, so no caller forgets it. Only the verbs whose
--host means "run against this host" take one: those, and settings, vault and init.

`refused`, which every caller says: a line made from the values of a run that was refused, or that nothing has taken
yet, prints each value of free text a person may paste a secret into by mistake, --set's and settings --value's, as
the name help gives it, so a secret pasted there is never said back. The table holds no option whose value is a secret.

Standard library only, and schema/.
"""

from __future__ import annotations

import shlex
import unicodedata
from collections.abc import Mapping

from claude_vm.schema import check as checks
from claude_vm.schema.patterns import full
from claude_vm.schema.words import BEFORE, PLACEHOLDER, VERBS, Line, Option, Verb
from claude_vm.schema.words import HOST as SHARED_HOST

# The program a line names where nothing says otherwise: the product's command line is aw alone (the owner's word 1).
PROGRAM = "aw"
# What the habits add to a verb's values (lib/claude_vm/cli/habits.py), which a line says by its words alone: no names,
# for the default VM or for every VM, as the verb takes none.
HABITS_OWN = ("default_vm",)
# The fields of the options whose value is free text a person may paste a secret into by mistake: --set, on every verb
# that takes it, whose run layer refuses a secret's name there (D5), and settings --value, which refuses a secret's
# value (D10). A line made from values no run has taken prints each as its option's metavar.
FREE_TEXT = ("set", "value")
# The option a host's flag gives; and the verbs beside those whose row has the shared --host whose --host means "run
# against this host", as host's does not (it says which host a run would reach, with --json).
HOST = "host"
HOSTS_OWN = ("settings", "vault", "init")
# The field a sentence names the program by.
PROGRAM_FIELD = "program"
# The categories of a character no printed word holds: the controls, the formats (a bidi override among them) and the
# line and paragraph separators, each of which a terminal acts on or does not show as it is.
UNSHOWN = ("Cc", "Cf", "Zl", "Zp")

# The program as it was started, which started_as sets once, from what the launcher says.
_program = PROGRAM


def _shown(text: str) -> bool:
    """Whether a terminal shows text as it is: no character of UNSHOWN's categories."""
    return all(unicodedata.category(character) not in UNSHOWN for character in text)


def started_as(said: str | None) -> None:
    """Make the program every line starts with what the launcher said: aw where aw on PATH is this launcher, else what
    the person typed to start it; aw where it said nothing, or a word no line could print as it is."""
    global _program
    _program = said if said and _shown(said) and not said.startswith("-") else PROGRAM


def program() -> str:
    """The program every line starts with, as it was started."""
    return _program


def _off(option: Option | None, value: object) -> bool:
    """Whether value is one a line does not say: none, a flag not given, a count of 0, an empty list."""
    if value is None:
        return True
    if option is None:
        return False
    if option.kind == "flag":
        return value is False
    if option.kind == "counted":
        return type(value) is int and value == 0
    return option.kind == "repeatable" and value == []


def _unshown(verb: str, option: Option) -> str:
    return (
        f"error: {checks.command(verb)} {checks.spelled(option)} takes no value with a character a terminal does not"
        " show as it is, so no line says it"
    )


def _option(verb: str, option: Option, value: object, masked: bool) -> list[tuple[str, bool]]:
    """The words that give option its value, each with whether a shell must have it quoted, held by the one check
    before: a value as --long value, or --long=value where it starts with - or is empty; masked or a placeholder, the
    value is the name help gives it."""
    spelled = checks.spelled(option)
    if option.kind == "flag":
        return [(spelled, False)]
    if option.kind == "counted":
        times = value if type(value) is int else 0
        return [(f"-{option.short * times}", False)] if option.short else [(spelled, False)] * times
    named = option.metavar or "VALUE"
    items = value if option.kind == "repeatable" and isinstance(value, list) else [value]
    words: list[tuple[str, bool]] = []
    for item in items:
        if item is PLACEHOLDER or masked:
            words += [(spelled, False), (named, False)]
        elif not isinstance(item, str):
            raise ValueError(checks.formed(verb, {option.field: value}) or _unshown(verb, option))
        elif not _shown(item):
            raise ValueError(_unshown(verb, option))
        elif item == "" or item.startswith("-"):
            words.append((f"{spelled}={item}", True))
        else:
            words += [(spelled, False), (item, True)]
    return words


def _host_flag(row: Verb, given: Mapping[str, object], host_flag: str | None) -> list[tuple[str, bool]]:
    """The host's flag's words: --host and the host's name, on a verb whose --host means run against this host and that
    was not given one; none for "". None is refused on a verb whose row has the shared --host and whose values give
    none, so no caller forgets the host a line reaches."""
    shared = SHARED_HOST in row.options
    if host_flag is None:
        if shared and HOST not in given:
            raise ValueError(
                f"error: a line of {checks.command(row.name)} names the host it reaches: host_flag is a host's name, or"
                ' "" with one host'
            )
        return []
    if not host_flag:
        return []
    option = next((option for option in row.options if option.long == HOST), None)
    if option is None or option.pattern is None or not (shared or row.name in HOSTS_OWN):
        raise ValueError(f"error: {checks.command(row.name)} takes no --{HOST} that runs it against a host")
    if HOST in given:
        raise ValueError(f"error: {checks.command(row.name)} takes --{HOST} once: in its values or as host_flag")
    if not full(option.pattern, host_flag):
        raise ValueError(f"error: host_flag is a host's name, or empty, for {checks.command(row.name)}")
    return [(f"--{HOST}", False), (host_flag, True)]


def _words(
    verb: str,
    values: Mapping[str, object],
    host_flag: str | None,
    before: Mapping[str, object] | None,
    refused: bool,
) -> list[tuple[str, bool]]:
    """The line's words, each with whether a shell must have it quoted; ValueError where the values are not what the one
    check takes, but for the rules between options, or a host's flag is not one the verb takes."""
    row = VERBS.get(verb)
    if row is None:
        raise ValueError(checks.no_such_command())
    options = {option.field: option for option in checks.options_of(row)}
    given = {
        key: value
        for key, value in values.items()
        if key not in HABITS_OWN
        and not _off(options.get(key), value)
        and not (row.words is not None and key == row.words.field and value == [])
    }
    before_options = {option.field: option for option in BEFORE}
    before_given = {key: value for key, value in (before or {}).items() if not _off(before_options.get(key), value)}
    placeheld = {key for key, value in given.items() if value is PLACEHOLDER}
    held = checks.check_before(before_given) or checks.formed(
        verb, {key: value for key, value in given.items() if key not in placeheld}
    )
    if held:
        raise ValueError(held)
    words: list[tuple[str, bool]] = [(_program, True)]
    for option in BEFORE:
        if option.field in before_given:
            words += _option("", option, before_given[option.field], False)
    words.append((verb, False))
    if row.words is not None and row.words.field in given:
        words += _verb_words(verb, row, given[row.words.field])
    for option in checks.options_of(row):
        if option.field in given:
            if option.field in placeheld and option.kind not in ("value", "repeatable"):
                raise ValueError(f"error: {checks.command(verb)} {checks.spelled(option)} has no value to name")
            words += _option(verb, option, given[option.field], refused and option.field in FREE_TEXT)
    return words + _host_flag(row, given, host_flag)


def _verb_words(verb: str, row: Verb, typed: object) -> list[tuple[str, bool]]:
    """A verb's words as given, held by the one check before; a placeholder as the name help gives them."""
    words = row.words
    assert words is not None
    if typed is PLACEHOLDER:
        return [(f"<{words.name}>..." if words.plural else f"<{words.name}>", False)]
    given = [typed] if isinstance(typed, str) else list(typed) if isinstance(typed, list) else []
    if any(word.startswith("-") for word in given):
        raise ValueError(f"error: {checks.command(verb)} takes no {words.name} that starts with -")
    if not all(_shown(word) for word in given):
        raise ValueError(
            f"error: {checks.command(verb)} takes no {words.name} with a character a terminal does not show as it is,"
            " so no line says it"
        )
    return [(word, True) for word in given]


def argv(
    verb: str,
    values: Mapping[str, object],
    *,
    refused: bool,
    host_flag: str | None = None,
    before: Mapping[str, object] | None = None,
) -> list[str]:
    """The command line a person types for verb with values, as its words, the program first. refused says whether no
    run has taken the values (one was refused, or none has started yet), and then a value of free text is its metavar;
    host_flag the host the line reaches, where the verb takes the shared --host and the values give none; before the
    words given before the verb, by their fields. ValueError where a value is not one the one check takes, its rules
    between options aside, or the host's flag is not one the verb takes."""
    return [word for word, _ in _words(verb, values, host_flag, before, refused)]


def line(
    verb: str,
    values: Mapping[str, object],
    *,
    refused: bool,
    host_flag: str | None = None,
    before: Mapping[str, object] | None = None,
) -> str:
    """argv's line as one string a person pastes into a POSIX shell: each word a shell would read otherwise quoted with
    shlex.quote, a value given as --long=value quoted after its =, and a placeholder as it is."""
    out = []
    for word, quoted in _words(verb, values, host_flag, before, refused):
        if not quoted:
            out.append(word)
        elif word.startswith("-") and "=" in word:
            spelled, _, value = word.partition("=")
            out.append(f"{spelled}={shlex.quote(value) if value else ''}")
        else:
            out.append(shlex.quote(word))
    return " ".join(out)


class _Fields(dict):
    """A sentence's fields, each filled when the sentence names it: a Line with the line printed for it, refused, since
    a sentence's lines are made from the values of a refused run or from none; the program with the program; anything
    else as given."""

    def __init__(self, filled: Mapping[str, object]) -> None:
        super().__init__()
        self._filled = filled

    def __missing__(self, name: str) -> object:
        if name == PROGRAM_FIELD and name not in self._filled:
            return _program
        value = self._filled[name]
        if isinstance(value, Line):
            return line(value.verb, value.values, host_flag=value.host_flag, refused=True)
        return value


def said(sentence: str, filled: Mapping[str, object]) -> str:
    """sentence with each of its fields filled: a Line as this printer writes it, the field program as the program,
    and anything else as given (schema/words.py's said, the one way a sentence names a command, hands it here)."""
    return sentence.format_map(_Fields(filled))
