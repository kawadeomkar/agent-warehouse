"""Today's habits of the command line, which are no rule between options: ordinary code after the parse and the one
check, before anything is imported or sent (docs/architecture/engine.md §16.3).

- A VM's name with `_`, which Proxmox refuses in a name, is taken with `-` in its place, and said in today's words
  (the justfile's hyphenate_names); and `alpha,beta` is two names, as two words are.
- The two meanings of no names (docs/reference/commands.md, the words a recipe takes): with none, `deploy`, `deploy
  --only provision` and `destroy` act on the default VM, and the commands that read VMs or configure them again on
  every VM the fleet made. Resolved into one field, `default_vm`, given only where a verb that takes several names was
  given none: true for the default VM, false for every VM. A verb that takes one name (code, release) is given no
  default. The default VM's name is the handler's to read from the settings (the row vm_default_name), never this
  module's, which reads no file of the fleet.
- A stage of deploy typed where the names go (`aw deploy configure`), by habit from `just test unit`, would make a VM
  so named. Where neither --only nor --check is given, deploy asks at a terminal which was meant, as a menu
  (lib/claude_vm/tui/choose.py) where the terminal can draw one and as today's typed question where it cannot, read
  from the standard input the command was given; with no terminal it is refused. The answer meets the rules between
  options as if typed: the one check runs again on what it comes to. The sentences of its three refusals are today's,
  the contract's deploy.1 to deploy.3, with aw for just.

Standard library only, and the menu's modules of lib/claude_vm/tui/, imported only to ask.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TextIO

from claude_vm.cli.parse import Refused
from claude_vm.schema.check import check, one_word
from claude_vm.schema.words import Verb

# The pattern of the words that name VMs, the ones these habits apply to.
VM_NAMES = "vm_name"
# Today's words for a name taken with a hyphen (the justfile's hyphenate_names).
HYPHENATED = "warning: Proxmox does not allow underscores in VM names, so {name} was changed to {changed}"


def _default_vm(verb: Verb, values: dict[str, object]) -> bool:
    """What no names means for verb: the default VM (True) or every VM the fleet made (False)."""
    if verb.name == "destroy":
        return True
    if verb.name == "deploy":
        # A whole deploy, and its stage that creates, act on the default VM; --check, configure and ssh-config on
        # every VM; the template takes no names.
        return not values.get("check") and values.get("only") in (None, "", "provision")
    return False


def names(verb: Verb, values: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """values with the VMs' names as the verb takes them, and default_vm where a verb that takes several was given none;
    and what to say of each name taken with a hyphen. Raises Refused where names that came to more than one were given
    to a verb that takes one."""
    words = verb.words
    if words is None or words.pattern != VM_NAMES:
        return values, []
    given = values.get(words.field)
    typed = [given] if isinstance(given, str) else list(given) if isinstance(given, list) else []
    split = [name for word in typed for name in word.split(",") if name]
    said = [HYPHENATED.format(name=name, changed=name.replace("_", "-")) for name in split if "_" in name]
    taken = [name.replace("_", "-") for name in split]
    out = {key: value for key, value in values.items() if key != words.field}
    if words.plural:
        if taken:
            out[words.field] = taken
        else:
            out["default_vm"] = _default_vm(verb, values)
    elif len(taken) > 1:
        raise Refused(one_word(verb.name, words))
    elif taken:
        out[words.field] = taken[0]
    return out, said


# ---------------------------------------------------------------- a stage of deploy where the names go ----

# deploy's stages, as the justfile's stages names them: check is the dry run's flag, the rest --only's values.
STAGES = ("template", "provision", "configure", "check", "ssh-config")
# deploy's options that say what a VM it creates is given, each in the commands its question names, as today's do.
SAID_OPTIONS = ("tags", "start", "cores", "memory", "disk", "repos")
# Today's sentences (the contract's deploy.1 to deploy.3), with aw for just: no terminal to ask at; the stage answered
# where two were given; and any other answer, Enter, the end of the input or Esc.
NO_TERMINAL = (
    "error: {words} {what}, and there is no terminal to ask which was meant: give a stage as aw deploy {hint}, or a VM "
    "so named beside one, as aw deploy --only provision {word}"
)
ONE_STAGE = "One stage runs at a time, given with --only: nothing was run."
NOTHING_RUN = "Nothing was run."
# The question, and its answers, in today's words.
ASKED = "{words} {what}. Did you mean"
AS_STAGE = "s  the stage: aw deploy {meant}{options}{rest}"
AS_VMS = "v  {as_vm} created if there is none: aw deploy {names}{options}"
TYPED = "{either}v, or Enter to run nothing: "
# The menu's own answer for running nothing, the one marked when it is drawn, as Enter is today's.
RUN_NOTHING = "run nothing"


def stages_given(verb: Verb, values: Mapping[str, object]) -> tuple[list[str], list[str]] | None:
    """The names given to deploy that are its stages, and the others, where neither --only nor --check says the stage;
    None where there is nothing to ask. values are the names habit's, each name its own and with - for _."""
    if verb.name != "deploy" or values.get("only") or values.get("check"):
        return None
    given = values.get("names")
    names = [name for name in given if isinstance(name, str)] if isinstance(given, list) else []
    stages = [name for name in names if name in STAGES]
    return (stages, [name for name in names if name not in STAGES]) if stages else None


def _fills(values: Mapping[str, object], stages: list[str], rest: list[str]) -> dict[str, str]:
    """What fills the question's sentences, as today's deploy works it out."""
    word = stages[0]
    hint = "--check" if word == "check" else f"--only {word}"
    options = ""
    for long in SAID_OPTIONS:
        given = values.get(long)
        options += f" --{long}" if given is True else f" --{long} {given}" if isinstance(given, str) and given else ""
    one = len(stages) == 1
    return {
        "words": ", ".join(stages),
        "what": "is a stage of aw deploy and also a name a VM could have"
        if one
        else "are stages of aw deploy and also names VMs could have",
        "hint": hint,
        "word": word,
        "meant": hint if one else "",
        "as_vm": f"a VM named {word}," if one else "VMs so named, each",
        "options": options,
        "rest": f" {','.join(rest)}" if rest else "",
        "names": ",".join(name for name in values.get("names") or () if isinstance(name, str)),
        "either": "s or " if one else "",
    }


def answered(verb: Verb, values: Mapping[str, object], answer: str) -> dict[str, object]:
    """values as the answer to deploy's question leaves them: v, the names as given; s, the one stage given, with the
    other names, held by the one check again as if typed; anything else, Refused, nothing run."""
    found = stages_given(verb, values)
    if found is None:
        return dict(values)
    stages, rest = found
    if answer in ("v", "V"):
        return dict(values)
    if answer not in ("s", "S"):
        raise Refused(NOTHING_RUN)
    if len(stages) > 1:
        raise Refused(ONE_STAGE)
    typed = {key: value for key, value in values.items() if key not in ("names", "default_vm")}
    if rest:
        typed["names"] = rest
    if stages[0] == "check":
        typed["check"] = True
    else:
        typed["only"] = stages[0]
    refused = check(verb.name, typed)
    if refused:
        raise Refused(refused)
    if not rest:
        typed["default_vm"] = _default_vm(verb, typed)
    return typed


def _at_a_terminal(stdin: TextIO) -> bool:
    try:
        return stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def _ask(fills: Mapping[str, str], stdin: TextIO, stream: TextIO, environ: Mapping[str, str]) -> str:
    """The answer to the question, from stdin: chosen from a menu where the terminal can draw one, else typed as today.
    The terminal's interrupt key is KeyboardInterrupt."""
    from claude_vm.tui import choose

    one = bool(fills["meant"])
    stream.write(ASKED.format_map(fills) + "\n")
    if choose.can_choose(stdin, stream, environ):
        options = [{"value": "s", "text": AS_STAGE.format_map(fills), "note": ""}] if one else []
        options += [
            {"value": "v", "text": AS_VMS.format_map(fills), "note": ""},
            {"value": "", "text": RUN_NOTHING, "note": ""},
        ]
        menu = {"title": "", "label": "", "options": options, "chosen": len(options) - 1}
        stream.flush()
        try:
            answer = choose.ask(menu, stdin, stream)
        except choose.Interrupted:
            raise KeyboardInterrupt from None
        chosen = next((option["text"] for option in options if option["value"] == answer and answer), None)
        if chosen:
            stream.write(f"  {chosen}\n")
        stream.flush()
        return answer
    if one:
        stream.write(f"  {AS_STAGE.format_map(fills)}\n")
    stream.write(f"  {AS_VMS.format_map(fills)}\n")
    stream.write(TYPED.format_map(fills))
    stream.flush()
    return stdin.readline().strip()


def stage(
    verb: Verb,
    values: dict[str, object],
    stdin: TextIO | None,
    stream: TextIO,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """values, after deploy's question where a stage was given where the names go (the names habit's values); raises
    Refused with today's sentence where there is no terminal to ask at, or the answer runs nothing."""
    found = stages_given(verb, values)
    if found is None:
        return values
    fills = _fills(values, *found)
    if stdin is None or not _at_a_terminal(stdin):
        raise Refused(NO_TERMINAL.format_map(fills))
    return answered(verb, values, _ask(fills, stdin, stream, os.environ if environ is None else environ))
