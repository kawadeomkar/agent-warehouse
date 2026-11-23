"""The rules between options: each a plain function of one verb's values that says today's sentence, run by the one
check (check.py) after the patterns, in the order the verb's row of the table names them, and stopping at the first
that refuses (docs/architecture/engine.md §16.2).

A rule is `Rule(code, verbs, sentence, breaks, lines)`: its code (`deploy.4`, `check.names.1`: the verb, or the
check, and a number), the verbs whose row of the table (words.py) names it, its sentence with `{name}` where it says a
value or names a command, and `breaks`, which takes the verb's values and returns None where they keep the rule, or
what fills the sentence where they break it; `lines` are the commands the sentence names whatever the values. A
command is a `Line` (words.py), never text: `Rule.check(values)` gives nothing or the sentence said through printer.py's
`said`, so each command in it is the printer's line, the program as it was started, and a value or a verb's words it
does not know is named by its placeholder (`aw code <name>`). A sentence says back no value that may be a secret, and
only a value that has already met its pattern: the check holds every value to its pattern first, as just does before a
recipe's script.

The values are the verb's as the check takes them: by the field the handler receives them by, only what was given, a
value option's a string, a flag's a boolean, the words a list where the verb takes several and a string where it takes
one, or a list where the parser was given more than one; an empty string is no value given, as today's empty
parameter is. The rules are today's rules between options (the justfile's, said before any check or tool), and the
three argument checks of tools/check that are more than one pattern: host --for's names, split at its
commas; --repos' 2000 characters; and --project's . and .., which its pattern takes. tools/check's other checks hold
one value to what its pattern already holds, behind just's pattern, for the JSON the justfile's scripts join: they
are the pattern's alone here, and no rule repeats a value's own pattern. What is not a rule between options is a habit
(lib/claude_vm/cli/habits.py); a check that reads the fleet's files, the environment, the file system or a terminal is
its verb's own, made where the verb is built.

Standard library only, words.py, patterns.py's `full`, the one way to hold a value to a pattern, and the printer,
imported as a module, since the printer holds a line to the check that runs these rules (schema/__init__.py): every
command reads it, a refusal among them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from claude_vm.schema import printer
from claude_vm.schema.patterns import full
from claude_vm.schema.words import PLACEHOLDER, Line

# A verb's values, by the field the handler receives each by.
Values = Mapping[str, object]
# What fills a sentence's {name}s where a rule is broken: a value, as a string, or a command, as a Line.
Filled = Mapping[str, object]

# The roles configuration can be narrowed to (deploy --tags), as today's configure.yml tags them: with the guest's
# second layer out of Ansible they stay these six, as the parts of a configure.
CONFIGURE_ROLES = ("common", "vscode_server", "dev_tools", "claude_code", "github_projects", "tailscale")
# The pattern a VM's name is held to, patterns.py's: host --for's names, each, as a verb's words are.
VM_NAME = "vm_name"


@dataclass(frozen=True)
class Rule:
    """A rule between options: its code, the verbs whose row names it, its sentence, what breaks it, and the commands
    its sentence names whatever the values."""

    code: str
    verbs: tuple[str, ...]
    sentence: str
    breaks: Callable[[Values], Filled | None]
    lines: Mapping[str, Line] = field(default_factory=dict)

    def check(self, values: Values) -> str | None:
        """None where values keep the rule, else its sentence filled in and said, each command the printer's."""
        filled = self.breaks(values)
        return None if filled is None else printer.said(self.sentence, {**self.lines, **filled})


def _text(values: Values, field: str) -> str:
    """A value option's value, or "" where it was not given: an empty value is none, as today's."""
    value = values.get(field)
    return value if isinstance(value, str) else ""


def _on(values: Values, field: str) -> bool:
    """Whether a flag was given, or a value option given a value."""
    value = values.get(field)
    return value is True or (isinstance(value, str) and value != "")


def _words(values: Values, field: str) -> list[str]:
    """A verb's words as typed: none, one, or each of several."""
    value = values.get(field)
    if isinstance(value, list):
        return [word for word in value if isinstance(word, str)]
    return [value] if isinstance(value, str) else []


# ---------------------------------------------------------------- tools/check's argument checks ----


def _a_name(values: Values) -> Filled | None:
    # host --for's names, split at its commas as just host splits them before the check, an empty one dropped as the
    # shell's word splitting drops it, each held to a VM name's pattern with full: the pattern takes alpha,-b whole, and
    # this refuses -b. A verb's words that name VMs are split and held the same way by the names habit (habits.py).
    bad = next((name for name in _text(values, "for_").split(",") if name and not full(VM_NAME, name)), None)
    return None if bad is None else {"name": bad}


def _repos_long(values: Values) -> Filled | None:
    # Its pattern holds each repository to its characters and its 100; the whole's 2000 is no pattern's.
    return {} if len(_text(values, "repos")) > 2000 else None


def _project_folder(values: Values) -> Filled | None:
    project = _text(values, "project")
    return {"value": project} if project in (".", "..") else None


# ---------------------------------------------------------------- deploy ----

# deploy's stages that take no part in configuring: --tags narrows nothing there.
_UNCONFIGURED = ("template", "provision", "ssh-config")


def _creates_nothing(values: Values) -> bool:
    """Whether the run creates and starts no VM: a dry run, a configuration narrowed by --tags, or a stage other than
    provision."""
    stage = _text(values, "only")
    return _on(values, "check") or _on(values, "tags") or (stage != "" and stage != "provision")


def _tags_commas(values: Values) -> Filled | None:
    roles = _text(values, "tags")
    return {"roles": roles} if roles and ",," in f",{roles}," else None


def _tags_roles(values: Values) -> Filled | None:
    roles = _text(values, "tags")
    if any(role not in CONFIGURE_ROLES for role in roles.split(",") if role):
        return {"known": ", ".join(CONFIGURE_ROLES), "roles": roles}
    return None


def _configure(roles: str) -> Line:
    """deploy's configuration of every VM, narrowed to roles."""
    return Line("deploy", {"only": "configure", "tags": roles}, host_flag="")


def _tags_stage(values: Values) -> Filled | None:
    roles, stage = _text(values, "tags"), _text(values, "only")
    return {"stage": stage, "configure": _configure(roles)} if roles and stage in _UNCONFIGURED else None


def _tags_alone(values: Values) -> Filled | None:
    roles = _text(values, "tags")
    alone = not _words(values, "names") and not _on(values, "only") and not _on(values, "check")
    if not (roles and alone):
        return None
    return {"named": Line("deploy", {"names": PLACEHOLDER, "tags": roles}, host_flag=""), "every": _configure(roles)}


def _check_stage(values: Values) -> Filled | None:
    stage = _text(values, "only")
    return {"stage": stage} if _on(values, "check") and stage else None


def _template_names(values: Values) -> Filled | None:
    return {} if _text(values, "only") == "template" and _words(values, "names") else None


def _start(values: Values) -> Filled | None:
    return {} if _on(values, "start") and _creates_nothing(values) else None


def _size(values: Values) -> Filled | None:
    sized = any(_on(values, field) for field in ("cores", "memory", "disk"))
    return {} if sized and _creates_nothing(values) else None


def _repos_given(values: Values) -> Filled | None:
    return {} if _on(values, "repos") and _creates_nothing(values) else None


# ---------------------------------------------------------------- the other verbs ----


def _set_of(names: str) -> set[str]:
    """destroy's names as a set, each with - for _, as just destroy compares --confirm with the names given: split at
    the commas, an empty one kept but for the one a last comma leaves, which sort -u never saw."""
    parts = names.split(",")
    if parts[-1:] == [""]:
        parts.pop()
    return {part.replace("_", "-") for part in parts}


def _confirm_alone(values: Values) -> Filled | None:
    return {} if _text(values, "confirm") and not _words(values, "names") else None


def _confirm_differs(values: Values) -> Filled | None:
    confirm, names = _text(values, "confirm"), ",".join(_words(values, "names"))
    if confirm and names and _set_of(confirm) != _set_of(names):
        return {"confirm": confirm, "names": names.replace("_", "-")}
    return None


def _count(field: str, most: int) -> Callable[[Values], Filled | None]:
    """A rule broken where more than most words are given, filling {words} with them as typed, space-separated."""

    def breaks(values: Values) -> Filled | None:
        words = _words(values, field)
        return {"words": " ".join(words)} if len(words) > most else None

    return breaks


def _any_word(field: str) -> Callable[[Values], Filled | None]:
    """A rule broken by any word at all after a verb's options: never said back."""
    return lambda values: {} if _words(values, field) else None


def _code_none(values: Values) -> Filled | None:
    return {} if not _words(values, "name") else None


def _code_many(values: Values) -> Filled | None:
    words = _words(values, "name")
    return {"words": " ".join(words)} if "," in ",".join(words) else None


def _settings_all(values: Values) -> Filled | None:
    return {} if _on(values, "all") and _words(values, "name") else None


def _settings_one(values: Values) -> Filled | None:
    if not (_on(values, "reset") or _on(values, "value")):
        return None
    one = len(_words(values, "name")) == 1 and not _on(values, "all") and not _on(values, "changed")
    return None if one else {}


def _settings_both(values: Values) -> Filled | None:
    return {} if _on(values, "reset") and _on(values, "value") else None


def _host_json(values: Values) -> Filled | None:
    return {} if _on(values, "json") and _words(values, "name") else None


def _host_preview(values: Values) -> Filled | None:
    return {} if not _on(values, "json") and (_on(values, "for_") or _on(values, "host")) else None


def _host_remove(values: Values) -> Filled | None:
    return {} if _on(values, "remove") and len(_words(values, "name")) != 1 else None


def _mcp_both(values: Values) -> Filled | None:
    return {} if _on(values, "remove") and _on(values, "show") else None


# The commands the rules' sentences name whatever the values.
_DEPLOY = Line("deploy", host_flag="")
_PROVISION = Line("deploy", {"only": "provision"}, host_flag="")

_ALL: tuple[Rule, ...] = (
    Rule(
        "help.1",
        ("help",),
        "error: {help} shows one command's options at a time (got: [{words}])",
        _count("command", 1),
        {"help": Line("help")},
    ),
    Rule(
        "init.1",
        ("init",),
        "error: {init} takes only its options ({help_init}), and nothing after them - never the token secret, which "
        "the command line would put in the shell's history and the process list: put it in a file only you can read, "
        "{secret_file}, or export AGENT_WAREHOUSE_TOKEN_SECRET from wherever your secrets live - a CI secret, or read "
        "-rs at a prompt",
        _any_word("extra"),
        {
            "init": Line("init"),
            "help_init": Line("help", {"command": "init"}),
            "secret_file": Line("init", {"token_secret_file": PLACEHOLDER}),
        },
    ),
    Rule(
        "deploy.4",
        ("deploy",),
        "error: --tags names roles separated by single commas (got: [{roles}])",
        _tags_commas,
    ),
    Rule("deploy.5", ("deploy",), "error: --tags may name only {known} (got: [{roles}])", _tags_roles),
    Rule(
        "deploy.6",
        ("deploy",),
        "error: --tags narrows configuration, which --only {stage} does not run: {configure}",
        _tags_stage,
    ),
    Rule(
        "deploy.7",
        ("deploy",),
        "error: --tags on its own configures the VMs named beside it: {named}, or every VM this project created with "
        "{every}",
        _tags_alone,
    ),
    Rule(
        "deploy.8",
        ("deploy",),
        "error: --check is a dry run of configuration, which --only {stage} is not: {dry_run}",
        _check_stage,
        {"dry_run": Line("deploy", {"check": True}, host_flag="")},
    ),
    Rule(
        "deploy.9",
        ("deploy",),
        "error: {template} builds the template, which takes no VM names",
        _template_names,
        {"template": Line("deploy", {"only": "template"}, host_flag="")},
    ),
    Rule(
        "deploy.10",
        ("deploy",),
        "error: --start starts a VM the run would leave stopped, which only {deploy} and {provision} start",
        _start,
        {"deploy": _DEPLOY, "provision": _PROVISION},
    ),
    Rule(
        "deploy.11",
        ("deploy",),
        "error: --cores, --memory and --disk size a VM the run creates, which only {deploy} and {provision} do",
        _size,
        {"deploy": _DEPLOY, "provision": _PROVISION},
    ),
    Rule(
        "deploy.12",
        ("deploy",),
        "error: --repos gives a VM its list as it is provisioned, which only {deploy} and {provision} do",
        _repos_given,
        {"deploy": _DEPLOY, "provision": _PROVISION},
    ),
    Rule(
        "code.1",
        ("code",),
        "error: code opens one VM: {code}",
        _code_none,
        {"code": Line("code", {"name": PLACEHOLDER}, host_flag="")},
    ),
    Rule("code.2", ("code",), "error: code opens one VM at a time, not [{words}]", _code_many),
    Rule(
        "settings.1",
        ("settings",),
        "error: {settings} takes one role or variable, and --value its value",
        _count("name", 1),
        {"settings": Line("settings")},
    ),
    Rule(
        "settings.2",
        ("settings",),
        "error: {all} lists every setting: give it no role or variable",
        _settings_all,
        {"all": Line("settings", {"all": True})},
    ),
    Rule(
        "settings.3",
        ("settings",),
        "error: --value and --reset each change the one setting named beside it: {value}, or --reset",
        _settings_one,
        {"value": Line("settings", {"name": PLACEHOLDER, "value": PLACEHOLDER})},
    ),
    Rule(
        "settings.4",
        ("settings",),
        "error: --value sets a value and --reset takes one out: one or the other",
        _settings_both,
    ),
    Rule(
        "destroy.1",
        ("destroy",),
        "error: --confirm answers for the VMs named beside it: {confirmed}",
        _confirm_alone,
        {"confirmed": Line("destroy", {"names": PLACEHOLDER, "confirm": PLACEHOLDER}, host_flag="")},
    ),
    Rule(
        "destroy.2",
        ("destroy",),
        "error: --confirm names {confirm}, but the VMs given are {names}: nothing was deleted",
        _confirm_differs,
    ),
    Rule(
        "host.1",
        ("host",),
        "error: {host} takes one host's name (got: [{words}])",
        _count("name", 1),
        {"host": Line("host")},
    ),
    Rule("host.2", ("host",), "error: --json lists the hosts: give it no name", _host_json),
    Rule(
        "host.3",
        ("host",),
        "error: --for and --host say which host a run would reach, in the list: give them with --json",
        _host_preview,
    ),
    Rule(
        "host.4",
        ("host",),
        "error: --remove deletes the host named beside it: {remove}",
        _host_remove,
        {"remove": Line("host", {"name": PLACEHOLDER, "remove": True})},
    ),
    Rule(
        "mcp.1",
        ("mcp",),
        "error: {mcp} takes only its options ({help_mcp}), and nothing after them",
        _any_word("extra"),
        {"mcp": Line("mcp"), "help_mcp": Line("help", {"command": "mcp"})},
    ),
    Rule(
        "mcp.2",
        ("mcp",),
        "error: --remove takes the server out and --show prints how it is added: one or the other",
        _mcp_both,
    ),
    Rule(
        "check.names.1",
        ("host",),
        "error: VM names start with a letter or a digit and may contain only letters, digits and . _ - , "
        "(got: [{name}])",
        _a_name,
    ),
    Rule("check.repos.2", ("deploy",), "error: --repos is longer than the 2000 characters it may be", _repos_long),
    Rule("check.project.2", ("code",), "error: --project names one project folder, not [{value}]", _project_folder),
)

# Every rule, by its code.
RULES: dict[str, Rule] = {rule.code: rule for rule in _ALL}


def run(codes: tuple[str, ...], values: Values) -> str | None:
    """The sentence of the first rule of codes, in their order, that values break; None where they keep them all."""
    for code in codes:
        said = RULES[code].check(values)
        if said is not None:
            return said
    return None
