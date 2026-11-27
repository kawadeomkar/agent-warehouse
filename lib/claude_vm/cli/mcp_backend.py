"""The agents' server's tools over the program: each tool's schema made from the one table of verbs and options, and the
command line of aw that a call runs (docs/architecture/engine.md §16.3).

A tool runs one verb, as the table's column tools says (schema/words.py), or is one of the server's own reads of the
state folder's jobs (log, jobs, job), which run no verb. Its schema is one flat set of properties, a small function of
the verb's row: each option and the verb's words whose fate for that tool is a property (words.py's agent), with exact
JSON types (a string, a boolean, a list of strings), its pattern anchored whole, and its description the table's
sentence said through the printer's said, with its form. A fate the server gives (fixed) is given to every call, the
value the table writes or, where it writes none, the one the server works out for that call (given); a refused one is
refused with the table's reason. This module holds no fate, no reason and no tool's name of its own: it reads them all
from the table, and tests/unit/test_aw_mcp_tools.py scans it for one.

A call's command line is the printer's (schema/printer.py's argv): every value one argument word, never through a
shell, the words given before the verb (--attended) as the server gives them, and nothing of the environment's. Its
arguments are held first by the one check (schema/check.py), its rules between options among them, so that a refusal
says what is wrong with the call and not that the program is at fault; a verb's word that starts with -, which the
parser would read as an option, is refused before that; and a line the printer will not write is said as the call's
refusal. Nothing here starts a child: a refusal comes before any. Standard library only, and schema/.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from claude_vm.schema import check as checks
from claude_vm.schema import printer
from claude_vm.schema.patterns import PATTERNS
from claude_vm.schema.words import BEFORE, FATES, VERBS, Fate, Option, Verb, Words, fate
from claude_vm.schema.words import HOST as SHARED_HOST

# The kinds of fate, as the table names them.
PROPERTY_KIND, FIXED_KIND, _ = FATES
# An argument's name a sentence may say back: what a property's name can be. Any other is said as none.
_NAMEABLE = re.compile(r"[a-z][a-z0-9_]{0,40}")

# The property a tool whose verb's run is a job has beside the verb's: the server starts the run detached and follows
# it (I3).
WAIT = "wait_seconds"
WAIT_SCHEMA = {
    "type": "integer",
    "minimum": 0,
    "description": "Return after this many seconds with the job still running, its ID to follow with job; none: at the "
    "run's end, or when it waits for the user in the browser",
}


@dataclass(frozen=True)
class Tool:
    """A tool of the agents' server: its name; the verb it runs, None for one of the server's own reads; its
    description; and its input schema, one flat object of properties."""

    name: str
    verb: str | None
    description: str
    schema: dict


class Refused(ValueError):
    """A call refused before any child starts, with the one sentence said to the agent, which never says a value."""


class Unfated(LookupError):
    """An option or a verb's words with no fate for a tool that runs its verb: the table's fault, raised naming the
    verb, the word and the tool, never a call's."""


def _anchored(pattern: str) -> str:
    """A pattern of the table as a JSON Schema pattern, which is not anchored: the whole value, as full holds it."""
    return f"^(?:{PATTERNS[pattern].text})$"


def _job_id(description: str) -> dict:
    return {"type": "string", "pattern": _anchored("job_id"), "description": description}


# The server's own reads of the state folder's jobs, which run no verb (I3 builds their bodies): each one's description
# and schema.
OWN_TOOLS: dict[str, tuple[str, dict]] = {
    "log": (
        "Read-only: one job's text log, by its ID as logs lists it: its last lines, or from line start (1 is its "
        "first) to read the rest. A log names the Proxmox host and VMs by address.",
        {
            "type": "object",
            "properties": {
                "job": _job_id("The job whose log to read, by its ID as logs lists it"),
                "lines": {"type": "integer", "minimum": 20, "maximum": 2000, "description": "How many lines"},
                "start": {"type": "integer", "minimum": 0, "description": "The first line to read; none: the end"},
            },
            "required": ["job"],
            "additionalProperties": False,
        },
    ),
    "jobs": (
        "Read-only: the jobs of the state folder, newest first: ID, state, start, what ran.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    "job": (
        "Follows one job: from since (the last answer's), to its next wait for the user in the browser, its end, or "
        "wait_seconds. A run goes on whatever happens to the call that started it.",
        {
            "type": "object",
            "properties": {
                "id": _job_id("The job to follow, by its ID as jobs lists it"),
                "since": {"type": "integer", "minimum": 0, "description": "Where the last answer ended"},
                WAIT: WAIT_SCHEMA,
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    ),
}


def runs() -> dict[str, str]:
    """The verb each tool of the table runs, by the tool's name, in the table's order."""
    return {tool: verb.name for verb in VERBS.values() if isinstance(verb.tools, tuple) for tool in verb.tools}


def items(row: Verb) -> dict[str, Option | Words]:
    """What a call of a tool that runs row may give, by the name the handler receives each by: the verb's words, then
    its options as the parser takes them, --detach among them where its run is a job."""
    found: dict[str, Option | Words] = {} if row.words is None else {row.words.field: row.words}
    return found | {option.field: option for option in checks.options_of(row)}


def fate_of(row: Verb, item: Option | Words, tool: str) -> Fate:
    """The fate of an option or of a verb's words for tool, as the table writes it: Unfated where it writes none, or one
    of no kind it knows."""
    agent = item.agent
    one = agent.get(tool) if isinstance(agent, dict) else agent
    if not isinstance(one, Fate) or one.kind not in FATES:
        word = item.name if isinstance(item, Words) else checks.spelled(item)
        raise Unfated(f"{row.name}'s {word} has no fate for the tool {tool} in schema/words.py's column agent")
    return one


def _before(tool: str) -> dict[str, Fate]:
    """The fate of each word given before any verb, for tool, by its field."""
    return {option.field: fate(option.agent, tool) for option in BEFORE}


def _property(item: Option | Words) -> dict:
    """The schema of one property: exact JSON types, its pattern anchored, and the table's sentence with its form."""
    kind = "words" if isinstance(item, Words) else item.kind
    several = (isinstance(item, Words) and item.plural) or kind == "repeatable"
    if kind == "flag":
        one: dict = {"type": "boolean"}
    elif kind == "counted":
        one = {"type": "integer", "minimum": 0}
    else:
        one = {"type": "string"} | ({"pattern": _anchored(item.pattern)} if item.pattern else {})
    described = printer.said(item.help or "")
    if item.pattern:
        described = f"{described} {checks.form_of(item)}"
    if several:
        return {"type": "array", "items": one, "description": described}
    return one | {"description": described}


def schema(tool: str) -> dict:
    """The input schema of tool: a property for each option and word of its verb whose fate for it is one, and the
    server's wait where its run is a job. Unfated where the table gives an option no fate for it."""
    if tool in OWN_TOOLS:
        return OWN_TOOLS[tool][1]
    row = VERBS[runs()[tool]]
    properties = {}
    required = []
    for field, item in items(row).items():
        if fate_of(row, item, tool).kind != PROPERTY_KIND:
            continue
        properties[field] = _property(item)
        if isinstance(item, Words) and item.needed:
            required.append(field)
            if item.plural:
                properties[field]["minItems"] = 1
    for field, one in _before(tool).items():
        if one.kind == PROPERTY_KIND:
            properties[field] = _property(next(option for option in BEFORE if option.field == field))
    if row.changes:
        properties[WAIT] = WAIT_SCHEMA
    out = {"type": "object", "properties": properties, "additionalProperties": False}
    return out | ({"required": required} if required else {})


def tools() -> list[Tool]:
    """Every tool of the agents' server: the table's, each with its verb's sentence and its schema, then the server's
    own reads."""
    made = [Tool(name, verb, printer.said(VERBS[verb].doc), schema(name)) for name, verb in runs().items()]
    return made + [Tool(name, None, described, own) for name, (described, own) in OWN_TOOLS.items()]


def _off(item: Option | Words | None, value: object) -> bool:
    """Whether a call's value is none given: null, an empty string or list, or false for a flag. Of its exact type
    only, so a value of another type reaches the check, which refuses it."""
    if value is None:
        return True
    if type(value) in (str, list):
        return not value
    return value is False and isinstance(item, Option) and item.kind == "flag"


def _named(name: object) -> str:
    return name if isinstance(name, str) and _NAMEABLE.fullmatch(name) else "of that name"


def _not_taken(tool: str, name: str, one: Fate) -> str:
    """The sentence of an argument the tool does not take, with the table's reason."""
    reason = printer.said(one.why) if one.why else "the server gives it to every call"
    if one.kind == FIXED_KIND and one.why:
        reason = f"the server gives it: {reason}"
    return f"error: the {tool} tool takes no {name}: {reason}"


def command(tool: str, arguments: Mapping[str, object], given: Mapping[str, object] | None = None) -> list[str]:
    """The command line of aw that a call of tool with arguments runs, as its words, the program first: each property
    given, each fixed value the table writes, and given, the values the server works out for this call by their field
    (each fate fixed with no value: destroy's --confirm, --attended). Refused, before any child starts, with the
    sentence said to the agent: an argument the tool does not take, with the table's reason; a verb's word that starts
    with -; what the one check refuses; a line the printer will not write. The server's wait is not aw's, and is left
    out. A given value the table does not ask for, or one it asks for and is not given, is the server's fault:
    TypeError."""
    if tool in OWN_TOOLS or tool not in runs():
        raise Refused(f"error: {_named(tool)} is no tool that runs a command")
    row = VERBS[runs()[tool]]
    offered = items(row)
    fates = {field: fate_of(row, item, tool) for field, item in offered.items()}
    before_fates = _before(tool)
    asked = {field for each in (fates, before_fates) for field, one in each.items() if one.kind == FIXED_KIND}
    asked = {field for field in asked if (fates.get(field) or before_fates[field]).value is None}
    given = dict(given or {})
    if set(given) != asked:
        raise TypeError(f"the {tool} tool's call is given {sorted(given)}, where the table asks for {sorted(asked)}")
    values: dict[str, object] = {}
    for name, value in arguments.items():
        if row.changes and name == WAIT:
            continue
        one = (fates.get(name) or before_fates.get(name)) if isinstance(name, str) else None
        if one is None:
            raise Refused(f"error: the {tool} tool has no argument {_named(name)}")
        if one.kind != PROPERTY_KIND:
            raise Refused(_not_taken(tool, name, one))
        if not _off(offered.get(name), value):
            values[name] = value
    before: dict[str, object] = {}
    for each, into in ((fates, values), (before_fates, before)):
        for field, one in each.items():
            if one.kind == FIXED_KIND:
                into[field] = given[field] if one.value is None else one.value
    before = {field: value for field, value in before.items() if not _off(None, value)}
    words = row.words
    if words is not None and words.field in values:
        typed = values[words.field]
        if any(isinstance(word, str) and word.startswith("-") for word in (typed if type(typed) is list else [typed])):
            raise Refused(
                f"error: the {tool} tool takes no {words.field} that starts with -, which aw would read as an option"
            )
    held = checks.check_before(before) or checks.check(row.name, values)
    if held:
        raise Refused(held)
    # A line of a verb that takes the shared --host names the host it reaches: with none given, the current one, which
    # the run's own command reaches.
    host_flag = "" if SHARED_HOST in row.options and SHARED_HOST.field not in values else None
    try:
        return printer.argv(row.name, values, refused=False, host_flag=host_flag, before=before)
    except printer.Unwritten as unwritten:
        raise Refused(str(unwritten)) from unwritten
