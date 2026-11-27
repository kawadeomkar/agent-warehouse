"""The printer of a command: the one way the new engine writes the command line a person types, from a verb and its
values, and the one way a sentence names a command (docs/architecture/engine.md §16.2). NEXT STEPS, status's repair
lines, a refusal's way out, the lines a detached run's start prints and the session notes on a machine all print
through it, and every sentence of the new engine that names a command names it as a Line, said through `said`.

`argv(verb, values, ...)` is the line as its words, and `line(verb, values, ...)` the same line as one string a person
pastes, quoted for a POSIX shell, sh, bash or dash, and for zsh (`quoted`): shlex.quote wherever such a shell would
read a word otherwise, and a word that starts with =, which zsh expands to a command's path, quoted too. Both read the
one table (words.py) and nothing else, in this order: the program as it was started; the words given before the verb
(`before`: --attended 1, --detached-run), in the table's order; the verb, deploy's whole chain as its one verb; the
verb's words as given, straight after it; its options in the table's order, --detach last as the parser gives it: a
value as --long value, and as --long=value only where it starts with - or is empty, so that the parser reads it as a
value; a flag bare; a counted option in one word, -v repeated (-vvv); a repeatable one once for each value; then the
host's flag, --host <name>; and last, in a form, its placeholders (below). So a line reads as a person types it, and
parsing it (lib/claude_vm/cli/parse.py) gives the same values back.

The program is aw where aw on PATH is this checkout's launcher, else what the person typed to start it (./aw), as the
launcher at the checkout's top says and cli/main.py hands on (`started_as`); aw where nothing says otherwise, as for
tools/aw run directly and the runs of the agents' server. A program word no line could print as it is, one with a
character a terminal would not show, a byte that is no UTF-8 among them, or one that starts with -, is aw. Every line
and every sentence says it quoted, as `quoted` quotes any word: a line's first word, and the field program of a
sentence, which only this module and check.py's `command` name.

The values are a verb's as the parser gives them, or as its handler receives them: `default_vm`, which the habits add
where a verb that takes several names was given none, is said by naming none, and a value that is off (None, a flag
not given, a count of 0, an empty list) is not said. Each is held as the one check holds it, but for the rules between
options (check.py's `formed`): of its option's kind, held by its pattern, not the placeholder the printer writes for
it, and the verb's words as many as it takes. A value that is not raises Unwritten with the check's sentence, which
never says the value, and so does a word that starts with -, which the parser would read as an option, and a value or
a word that holds a character of Unicode's categories Cc, Cf, Cs, Zl or Zp, which a terminal would act on or not show as
it is (a C1 control, a bidi override, a lone surrogate, a line separator). A rule between options is the run's to say,
so a line may name a run that a rule refuses: deploy's question names the stage before its answer meets the rules. A
value or a verb's words given as `PLACEHOLDER` are named by the name help gives them (words.py's `placeholder`): <name>,
<name>... for several, and an option's value as <cores>, never quoted. Such a line is a form, which shows a command's
shape (aw destroy <name>... --confirm <names>), and so is a line whose value of free text is masked (`refused`, below):
a line offered as the command to run holds none, and the one check refuses a placeholder given back to aw.

A form writes its placeholders last: the verb's words, then each option in the table's order, whose value is a
placeholder or is masked, after every part with a real value, the host's flag among them (aw host --remove <name>, aw
init --host far --address <address>). Pasted at a shell, a placeholder is two redirections, <name> reading a file named
name and writing one named as the word after it, so no real value stands where a shell would take it for a file's name
and hand the word before the placeholder a value. A form that ends in a placeholder's > is no line a shell reads, and
nor is one where a > meets the next placeholder's <: every form but one whose one placeholder is a verb's names,
<name>..., which ends in ..., the name of the file its > writes. That one stops at its < where no file is named name,
nothing run, made or emptied; where one is, a shell runs the line's other words, its real values, so the printer writes
it only where the one check, its rules among them, refuses those (check.py's `takes`), and raises Unwritten where it
takes them: aw deploy <name>... alone would leave aw deploy, the default VM's. aw deploy --tags claude_code <name>...
leaves aw deploy --tags claude_code, which a rule refuses. So no form the printer writes leaves a whole command.

The forms it so refuses are those of the four verbs whose words are several names, the names their one placeholder:
aw deploy <name>..., aw deploy --detach <name>..., aw login [--only github] <name>..., aw status <name>... and aw
destroy <name>..., and each of them with any other option or host's flag the one check takes beside it (aw status
--json <name>..., aw login --host lab2 <name>...). A sentence that would show one names a real VM in its place, or
another placeholder beside it (aw destroy <name>... --confirm <names>), or a value the rules refuse there; help's usage
line, aw deploy [<name>...], is help's own and no form of the printer's.

`host_flag` is the host a line names. Every caller of a verb whose row has the shared --host (deploy, code, login,
status, destroy) gives it where the values give no --host: a host's name, or "" where the fleet has one host or the
line reaches the host its own command does; None is Unwritten there, so no caller forgets it. Only the verbs whose
--host means "run against this host" take one: those, and settings, vault and init.

`refused`, which every caller says: a line made from the values of a run that was refused, or that nothing has taken
yet, prints each value of free text a person may paste a secret into by mistake, --set's and settings --value's, as
its placeholder, so a secret pasted there is never said back. The table holds no option whose value is a secret.

The printer holds a line to the one check, and the check's sentences, and the rules', name their commands through the
printer: each of the three imports the others as a module, never a name from one, so any of them may be imported first
(schema/__init__.py). Standard library only, and schema/.
"""

from __future__ import annotations

import shlex
import unicodedata
from collections.abc import Mapping

from claude_vm.schema import check as checks
from claude_vm.schema.patterns import full
from claude_vm.schema.words import BEFORE, LINES, PLACEHOLDER, VERBS, Line, Option, Verb, Words, placeholder
from claude_vm.schema.words import HOST as SHARED_HOST

# The program a line names where nothing says otherwise: the product's command line is aw alone (the owner's word 1).
PROGRAM = "aw"
# The variable the launcher at the checkout's top says the program in (cli/main.py takes it out of the environment at
# once), and the one a detached run's starter hands its run (handed), so that the run's lines say the program too.
STARTED_AS = "AGENT_WAREHOUSE_STARTED_AS"
# What the habits add to a verb's values (lib/claude_vm/cli/habits.py), which a line says by its words alone: no names,
# for the default VM or for every VM, as the verb takes none.
HABITS_OWN = ("default_vm",)
# The fields of the options whose value is free text a person may paste a secret into by mistake: --set, on every verb
# that takes it, whose run layer refuses a secret's name there (D5), and settings --value, which refuses a secret's
# value (D10). A line made from values no run has taken prints each as its placeholder.
FREE_TEXT = ("set", "value")
# The option a host's flag gives; and the verbs beside those whose row has the shared --host whose --host means "run
# against this host", as host's does not (it says which host a run would reach, with --json).
HOST = "host"
HOSTS_OWN = ("settings", "vault", "init")
# The field a sentence names the program by, which only this module fills and only check.py's command names.
PROGRAM_FIELD = "program"
# The categories of a character no printed word holds: the controls, the formats (a bidi override among them), the
# surrogates (a byte of a folder's name that is no UTF-8, as Python decodes one, which no UTF-8 output can write) and
# the line and paragraph separators, each of which a terminal acts on, does not show as it is, or cannot be given.
UNSHOWN = ("Cc", "Cf", "Cs", "Zl", "Zp")

# The program as it was started, which started_as sets once, from what the launcher says.
_program = PROGRAM


class Unwritten(ValueError):
    """A line the printer will not write, raised with the check's kind of sentence, which never says a value: only the
    code asks the printer for a line, from values the one check held or the engine read, so a line refused is the
    code's own fault, never the words a person typed. Said as own_fault says it, never as a traceback: by cli/main.py
    where it reaches main, and by a run that ends the process at once with a call still in a thread
    (runs/attached.py's _end_now), where it never does. A ValueError, as every caller that catches one expects."""


# What a line the printer would not write is said with, after the printer's own sentence: the code asked for it.
OWN_FAULT = "a fault of the program's own, not of the words typed"


def own_fault(unwritten: Unwritten) -> str:
    """The one line an Unwritten is said in to a person: the printer's sentence, then that the fault is the code's."""
    return f"{unwritten} ({OWN_FAULT})"


def _shown(text: str) -> bool:
    """Whether a terminal shows text as it is: no character of UNSHOWN's categories."""
    return all(unicodedata.category(character) not in UNSHOWN for character in text)


def quoted(word: str) -> str:
    """word as one word of a pasted line, which every POSIX shell and zsh read back as it is: shlex.quote's, and a word
    that starts with = quoted too, which shlex takes as safe and zsh expands to the path of the command it names."""
    said = shlex.quote(word)
    return f"'{word}'" if said == word and word.startswith("=") else said


def started_as(said: str | None) -> None:
    """Make the program every line starts with what the launcher said: aw where aw on PATH is this launcher, else what
    the person typed to start it; aw where it said nothing, or a word no line could print as it is."""
    global _program
    _program = said if said and _shown(said) and not said.startswith("-") else PROGRAM


def program() -> str:
    """The program every line starts with, as it was started, as one word: unquoted, for a child's environment, never
    for a line (line and said quote it)."""
    return _program


def handed() -> dict[str, str]:
    """What a detached run's starter hands its run in its environment (runs/detached.py), so that every line the run
    prints says the program as this one was started: STARTED_AS with it, which the run's main takes as this one did."""
    return {STARTED_AS: _program}


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
    value is its placeholder (<value>)."""
    spelled = checks.spelled(option)
    if option.kind == "flag":
        return [(spelled, False)]
    if option.kind == "counted":
        times = value if type(value) is int else 0
        return [(f"-{option.short * times}", False)] if option.short else [(spelled, False)] * times
    items = value if option.kind == "repeatable" and isinstance(value, list) else [value]
    words: list[tuple[str, bool]] = []
    for item in items:
        if item is PLACEHOLDER or masked:
            words += [(spelled, False), (placeholder(option), False)]
        elif not isinstance(item, str):
            raise Unwritten(checks.formed(verb, {option.field: value}) or _unshown(verb, option))
        elif not _shown(item):
            raise Unwritten(_unshown(verb, option))
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
            raise Unwritten(
                f"error: a line of {checks.command(row.name)} names the host it reaches: host_flag is a host's name, or"
                ' "" with one host'
            )
        return []
    if not host_flag:
        return []
    option = next((option for option in row.options if option.long == HOST), None)
    if option is None or option.pattern is None or not (shared or row.name in HOSTS_OWN):
        raise Unwritten(f"error: {checks.command(row.name)} takes no --{HOST} that runs it against a host")
    if HOST in given:
        raise Unwritten(f"error: {checks.command(row.name)} takes --{HOST} once: in its values or as host_flag")
    if not full(option.pattern, host_flag):
        raise Unwritten(f"error: host_flag is a host's name, or empty, for {checks.command(row.name)}")
    return [(f"--{HOST}", False), (host_flag, True)]


def _words(
    verb: str,
    values: Mapping[str, object],
    host_flag: str | None,
    before: Mapping[str, object] | None,
    refused: bool,
) -> list[tuple[str, bool]]:
    """The line's words, each with whether a shell must have it quoted; Unwritten where the values are not what the one
    check takes, but for the rules between options, or a host's flag is not one the verb takes."""
    row = VERBS.get(verb)
    if row is None:
        raise Unwritten(checks.no_such_command())
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
        raise Unwritten(held)
    words: list[tuple[str, bool]] = [(_program, True)]
    for option in BEFORE:
        if option.field in before_given:
            words += _option("", option, before_given[option.field], False)
    words.append((verb, False))
    # A form's placeholders, its verb's words first, then each option in the table's order whose value is one or is
    # masked: written after every part with a real value, the host's flag among them.
    last: list[tuple[str, bool]] = []
    if row.words is not None and row.words.field in given:
        (last if row.words.field in placeheld else words).extend(_verb_words(verb, row, given[row.words.field]))
    for option in checks.options_of(row):
        if option.field in given:
            if option.field in placeheld and option.kind not in ("value", "repeatable"):
                raise Unwritten(f"error: {checks.command(verb)} {checks.spelled(option)} has no value to name")
            masked = refused and option.field in FREE_TEXT
            shown = _option(verb, option, given[option.field], masked)
            (last if masked or option.field in placeheld else words).extend(shown)
    words += _host_flag(row, given, host_flag)
    if row.words is not None and len(last) == 1 and row.words.plural and row.words.field in placeheld:
        _leaves_no_command(verb, row.words, _real(given, placeheld, host_flag))
    return words + last


def _real(given: Mapping[str, object], placeheld: set[str], host_flag: str | None) -> dict[str, object]:
    """The values a line gives a real value, its host's flag among them: what a shell leaves of a form whose one
    placeholder is a verb's names."""
    real = {key: value for key, value in given.items() if key not in placeheld}
    return {**real, HOST: host_flag} if host_flag else real


def _leaves_no_command(verb: str, words: Words, real: Mapping[str, object]) -> None:
    """Unwritten where a form whose one placeholder is a verb's names, <name>..., would leave a whole command at a
    shell. Every other form ends in a placeholder's >, or puts a > before the next placeholder's <, which no shell
    reads; this one ends in ..., the file its > writes, so a shell in a folder holding a file named as the placeholder
    runs the line's other words, its real values: a form is written only where the one check, its rules among them,
    refuses those. Asked without a sentence said, since a rule's sentence names forms this printer writes."""
    if checks.takes(verb, real):
        raise Unwritten(
            f"error: a form of {checks.command(verb)} whose one placeholder is its {words.name}s would leave a command"
            " the one check takes, where a shell reads it: give it a value the rules refuse there, or another"
            " placeholder"
        )


def _verb_words(verb: str, row: Verb, typed: object) -> list[tuple[str, bool]]:
    """A verb's words as given, held by the one check before; a placeholder as the name help gives them."""
    words = row.words
    assert words is not None
    if typed is PLACEHOLDER:
        return [(f"{placeholder(words)}..." if words.plural else placeholder(words), False)]
    given = [typed] if isinstance(typed, str) else list(typed) if isinstance(typed, list) else []
    if any(word.startswith("-") for word in given):
        raise Unwritten(f"error: {checks.command(verb)} takes no {words.name} that starts with -")
    if not all(_shown(word) for word in given):
        raise Unwritten(
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
    run has taken the values (one was refused, or none has started yet), and then a value of free text is its
    placeholder; host_flag the host the line reaches, where the verb takes the shared --host and the values give none;
    before the words given before the verb, by their fields. Unwritten where a value is not one the one check takes,
    its rules between options aside, or the host's flag is not one the verb takes."""
    return [word for word, _ in _words(verb, values, host_flag, before, refused)]


def line(
    verb: str,
    values: Mapping[str, object],
    *,
    refused: bool,
    host_flag: str | None = None,
    before: Mapping[str, object] | None = None,
) -> str:
    """argv's line as one string a person pastes into a POSIX shell or zsh: each word a shell would read otherwise
    quoted (`quoted`), the program among them, a value given as --long=value quoted after its =, and a placeholder as it
    is."""
    out = []
    for word, quote in _words(verb, values, host_flag, before, refused):
        if not quote:
            out.append(word)
        elif word.startswith("-") and "=" in word:
            spelled, _, value = word.partition("=")
            out.append(f"{spelled}={quoted(value) if value else ''}")
        else:
            out.append(quoted(word))
    return " ".join(out)


class _Fields(dict):
    """A sentence's fields, each filled when the sentence names it: a Line with the line printed for it, refused, since
    a sentence's lines are made from the values of a refused run or from none; the program with the program, quoted
    as a line's first word is; anything else as given."""

    def __init__(self, filled: Mapping[str, object]) -> None:
        super().__init__()
        self._filled = filled

    def __missing__(self, name: str) -> object:
        if name == PROGRAM_FIELD and name not in self._filled:
            return quoted(_program)
        value = self._filled[name]
        if isinstance(value, Line):
            return line(value.verb, value.values, host_flag=value.host_flag, refused=True)
        return value


def said(sentence: str, filled: Mapping[str, object] | None = None) -> str:
    """sentence as a person reads it: each field filled, a Line with the command line this printer writes for it, the
    table's LINES among them, the field program with the program, quoted, and anything else as given. The one way a
    sentence of the new engine names a command."""
    return sentence.format_map(_Fields({**LINES, **(filled or {})}))
