"""The rules between options: each a plain function of one verb's values that says today's sentence, run by the one
check (check.py) after the patterns, in the order the verb's row of the table names them, and stopping at the first
that refuses (docs/architecture/engine.md §16.2).

A rule is `Rule(code, verbs, sentence, breaks)`: the code the contract of today's command line gives it
(tests/unit/cli_contract.json: `deploy.4`, `check.names.1`), the verbs whose row of the table (words.py) names it,
today's sentence with aw where it said just and `{name}` where it says a value, and `breaks`, which takes the verb's
values and returns None where they keep the rule, or what fills the sentence where they break it. `Rule.check(values)`
gives nothing or the sentence filled in. A sentence says a value back only where today's does, and then a value that
has already met its pattern: the check holds every value to its pattern first, as just does before a recipe's script.

The values are the verb's as the check takes them: by the field the handler receives them by, only what was given, a
value option's a string, a flag's a boolean, the words a list where the verb takes several and a string where it takes
one, or a list where the parser was given more than one; an empty string is no value given, as today's empty
parameter is. The rules are today's rules between options (the justfile's, said before any check or tool) and the
argument checks of tools/check, word for word. What is not a rule between options is a habit
(lib/claude_vm/cli/habits.py); a check that reads the fleet's files, the environment, the file system or a terminal is
its verb's own, made where the verb is built.

Standard library only: every command reads it, a refusal among them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

# A verb's values, by the field the handler receives each by.
Values = Mapping[str, object]
# What fills a sentence's {name}s where a rule is broken.
Filled = Mapping[str, str]

# The roles configuration can be narrowed to (deploy --tags), as today's configure.yml tags them: with the guest's
# second layer out of Ansible they stay these six, as the parts of a configure (tests/unit/test_aw_cli_rules.py holds
# them equal to the justfile's configure_roles until the cutover).
CONFIGURE_ROLES = ("common", "vscode_server", "dev_tools", "claude_code", "github_projects", "tailscale")
# The sections of status's report (status --only), as tools/check sections names them.
SECTIONS = ("vms", "storage", "capacity", "access", "sign-ins", "system", "work")

# The characters the checks spell out, as tools/check does, so no locale can widen them.
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_DIGITS = "0123456789"
_LOWER = "abcdefghijklmnopqrstuvwxyz"
_HEX = "0123456789abcdef"


@dataclass(frozen=True)
class Rule:
    """A rule between options: its code, the verbs whose row names it, its sentence, and what breaks it."""

    code: str
    verbs: tuple[str, ...]
    sentence: str
    breaks: Callable[[Values], Filled | None]

    def check(self, values: Values) -> str | None:
        """None where values keep the rule, else its sentence filled in."""
        filled = self.breaks(values)
        return None if filled is None else self.sentence.format_map(filled)


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


def _only(value: str, allowed: str) -> bool:
    return all(character in allowed for character in value)


def _whole(value: str, most: int) -> bool:
    """A whole number of at most most digits that starts with no 0, as tools/check size's whole."""
    return value != "" and value[0] != "0" and _only(value, _DIGITS) and len(value) <= most


def _vm_name(name: str) -> bool:
    """tools/check names: from a letter or a digit, then letters, digits and . _ - , only."""
    return name != "" and name[0] in _LETTERS + _DIGITS and _only(name, _LETTERS + _DIGITS + "._,-")


def _names(values: Values) -> list[str]:
    """The VM names a verb's values give: each word as typed, whole (deploy, login, status, destroy, code; host's word
    is a host's name, whose pattern every VM name's check takes); destroy's --confirm, whole; host's --for, split at
    its commas, as just host splits it before the check."""
    found = _words(values, "names") + _words(values, "name")
    confirm = _text(values, "confirm")
    found += [confirm] if confirm else []
    found += [name for name in _text(values, "for_").split(",") if name]
    return found


# ---------------------------------------------------------------- tools/check's argument checks ----


def _a_name(values: Values) -> Filled | None:
    bad = next((name for name in _names(values) if not _vm_name(name)), None)
    return None if bad is None else {"name": bad}


def _cores(values: Values) -> Filled | None:
    cores = _text(values, "cores")
    return {"value": cores} if cores and not _whole(cores, 3) else None


def _memory(values: Values) -> Filled | None:
    memory = _text(values, "memory")
    number = memory[:-1] if memory[-1:] in ("M", "G") else memory
    return {"value": memory} if memory and not _whole(number, 7) else None


def _disk(values: Values) -> Filled | None:
    disk = _text(values, "disk")
    number = disk[:-1] if disk[-1:] in ("G", "T") else ""
    return {"value": disk} if disk and not _whole(number, 6) else None


def _repos_spelled(repos: str) -> bool:
    return ",," not in f",{repos}," and _only(repos, _LETTERS + _DIGITS + "._,-")


def _repos(values: Values) -> Filled | None:
    # A repository that starts with . or - or runs past 100 characters is looked for only once the whole is within its
    # 2000 characters, as tools/check repos does.
    repos = _text(values, "repos")
    if not repos:
        return None
    if not _repos_spelled(repos):
        return {"value": repos}
    if len(repos) <= 2000 and any(repo[:1] in (".", "-") or len(repo) > 100 for repo in repos.split(",")):
        return {"value": repos}
    return None


def _repos_long(values: Values) -> Filled | None:
    repos = _text(values, "repos")
    return {} if repos and _repos_spelled(repos) and len(repos) > 2000 else None


def _project_characters(values: Values) -> Filled | None:
    project = _text(values, "project")
    return {"value": project} if not _only(project, _LETTERS + _DIGITS + "._-") else None


def _project_folder(values: Values) -> Filled | None:
    project = _text(values, "project")
    return {"value": project} if project in (".", "..") else None


def _project_long(values: Values) -> Filled | None:
    return {} if len(_text(values, "project")) > 100 else None


def _session(values: Values) -> Filled | None:
    session = _text(values, "session")
    groups = session.split("-")
    held = [len(group) for group in groups] == [8, 4, 4, 4, 12] and all(_only(group, _HEX) for group in groups)
    return {"value": session} if session and not held else None


def _sections_spelled(values: Values) -> Filled | None:
    only = _text(values, "only")
    spelled = ",," not in f",{only}," and _only(only, _LOWER + ",-")
    return {"value": only} if only and not spelled else None


def _sections_known(values: Values) -> Filled | None:
    bad = next((section for section in _text(values, "only").split(",") if section and section not in SECTIONS), None)
    return None if bad is None else {"section": bad}


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


def _tags_stage(values: Values) -> Filled | None:
    roles, stage = _text(values, "tags"), _text(values, "only")
    return {"stage": stage, "roles": roles} if roles and stage in _UNCONFIGURED else None


def _tags_alone(values: Values) -> Filled | None:
    roles = _text(values, "tags")
    alone = not _words(values, "names") and not _on(values, "only") and not _on(values, "check")
    return {"roles": roles} if roles and alone else None


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


def _host_address(values: Values) -> Filled | None:
    host, address = _text(values, "host"), _text(values, "address")
    return {"host": host, "address": address} if ("." in host or ":" in host) and address else None


def _host_name(values: Values) -> Filled | None:
    host = _text(values, "host")
    if not host or "." in host or ":" in host:
        return None
    held = host[0] in _LOWER + _DIGITS and _only(host, _LOWER + _DIGITS + "-")
    return None if held else {"host": host}


# init's values, each held to its characters again as the justfile's init does (each sits whole inside the JSON its
# playbook is given): the option, its field, the characters it may contain, and how the sentence names them.
_INIT_CHARACTERS = (
    ("address", "address", _LETTERS + _DIGITS + ".:-", "letters, digits and . : -"),
    ("token-user", "token_user", _LETTERS + _DIGITS + "._@-", "letters, digits and . _ - @"),
    ("token-id", "token_id", _LETTERS + _DIGITS + "._-", "letters, digits and . _ -"),
    (
        "token-secret-file",
        "token_secret_file",
        _LETTERS + _DIGITS + "._/~+-",
        "letters, digits and . _ - / ~ + (give an absolute path where the directory has others)",
    ),
    ("github-user", "github_user", _LETTERS + _DIGITS + "-", "letters, digits and -"),
    ("storage", "storage", _LETTERS + _DIGITS + "._-", "letters, digits and . _ -"),
    ("bridge", "bridge", _LETTERS + _DIGITS + "._-", "letters, digits and . _ -"),
    ("certificate", "certificate", _LETTERS + _DIGITS + ":", "hex digits and :"),
)


def _init_characters(values: Values) -> Filled | None:
    for option, field, allowed, said in _INIT_CHARACTERS:
        value = _text(values, field)
        if not _only(value, allowed):
            return {"option": option, "allowed": said, "value": value}
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


_ALL: tuple[Rule, ...] = (
    Rule(
        "help.1",
        ("help",),
        "error: aw help shows one recipe's options at a time (got: [{words}])",
        _count("recipe", 1),
    ),
    Rule(
        "init.1",
        ("init",),
        "error: aw init takes only its options (aw help init), and nothing after them - never the token secret, which "
        "the command line would put in the shell's history and the process list: put it in a file only you can read, "
        "aw init --token-secret-file <file>, or export AGENT_WAREHOUSE_TOKEN_SECRET from wherever your secrets live - "
        "a CI secret, or read -rs at a prompt",
        _any_word("extra"),
    ),
    Rule(
        "init.2",
        ("init",),
        "error: --host {host} and --address {address}: --host names a host of this checkout's, and its address is "
        "--address",
        _host_address,
    ),
    Rule(
        "init.3",
        ("init",),
        "error: a host's name is lowercase letters, digits and -, from a letter or a digit (got: [{host}])",
        _host_name,
    ),
    Rule("init.4", ("init",), "error: --{option} may contain only {allowed} (got: [{value}])", _init_characters),
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
        "error: --tags narrows configuration, which --only {stage} does not run: aw deploy --only configure --tags "
        "{roles}",
        _tags_stage,
    ),
    Rule(
        "deploy.7",
        ("deploy",),
        "error: --tags on its own configures the VMs named beside it: aw deploy <name>... --tags {roles}, or every VM "
        "this project created with aw deploy --only configure --tags {roles}",
        _tags_alone,
    ),
    Rule(
        "deploy.8",
        ("deploy",),
        "error: --check is a dry run of configuration, which --only {stage} is not: aw deploy --check",
        _check_stage,
    ),
    Rule(
        "deploy.9",
        ("deploy",),
        "error: aw deploy --only template builds the template, which takes no VM names",
        _template_names,
    ),
    Rule(
        "deploy.10",
        ("deploy",),
        "error: --start starts a VM the run would leave stopped, which only aw deploy and aw deploy --only provision "
        "start",
        _start,
    ),
    Rule(
        "deploy.11",
        ("deploy",),
        "error: --cores, --memory and --disk size a VM the run creates, which only aw deploy and aw deploy --only "
        "provision do",
        _size,
    ),
    Rule(
        "deploy.12",
        ("deploy",),
        "error: --repos gives a VM its list as it is provisioned, which only aw deploy and aw deploy --only provision "
        "do",
        _repos_given,
    ),
    Rule("code.1", ("code",), "error: code opens one VM: aw code <name>", _code_none),
    Rule("code.2", ("code",), "error: code opens one VM at a time, not [{words}]", _code_many),
    Rule(
        "settings.1",
        ("settings",),
        "error: aw settings takes one role or variable, and --value its value",
        _count("name", 1),
    ),
    Rule(
        "settings.2",
        ("settings",),
        "error: aw settings --all lists every setting: give it no role or variable",
        _settings_all,
    ),
    Rule(
        "settings.3",
        ("settings",),
        "error: --value and --reset each change the one setting named beside it: aw settings <name> --value <value>, "
        "or --reset",
        _settings_one,
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
        "error: --confirm answers for the VMs named beside it: aw destroy --confirm=<names> <names>",
        _confirm_alone,
    ),
    Rule(
        "destroy.2",
        ("destroy",),
        "error: --confirm names {confirm}, but the VMs given are {names}: nothing was deleted",
        _confirm_differs,
    ),
    Rule("host.1", ("host",), "error: aw host takes one host's name (got: [{words}])", _count("name", 1)),
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
        "error: --remove deletes the host named beside it: aw host <name> --remove",
        _host_remove,
    ),
    Rule(
        "mcp.1",
        ("mcp",),
        "error: aw mcp takes only its options (aw help mcp), and nothing after them",
        _any_word("extra"),
    ),
    Rule(
        "mcp.2",
        ("mcp",),
        "error: --remove takes the server out and --show prints how it is added: one or the other",
        _mcp_both,
    ),
    Rule(
        "check.names.1",
        ("deploy", "code", "login", "status", "destroy", "host"),
        "error: VM names start with a letter or a digit and may contain only letters, digits and . _ - , "
        "(got: [{name}])",
        _a_name,
    ),
    Rule("check.size.1", ("deploy",), "error: --cores is a number of cores, as 8 (got: [{value}])", _cores),
    Rule(
        "check.size.2",
        ("deploy",),
        "error: --memory is MiB, as 3072, or takes M or G, as 16G (got: [{value}])",
        _memory,
    ),
    Rule("check.size.3", ("deploy",), "error: --disk takes G or T, as 64G (got: [{value}])", _disk),
    Rule(
        "check.repos.1",
        ("deploy",),
        "error: --repos names GitHub repositories, comma-separated: each starts with a letter, a digit or _, and has "
        "at most 100 letters, digits, . _ and - (got: [{value}])",
        _repos,
    ),
    Rule("check.repos.2", ("deploy",), "error: --repos is longer than the 2000 characters it may be", _repos_long),
    Rule(
        "check.project.1",
        ("code",),
        "error: --project may contain only letters, digits and . _ - (got: [{value}])",
        _project_characters,
    ),
    Rule("check.project.2", ("code",), "error: --project names one project folder, not [{value}]", _project_folder),
    Rule(
        "check.project.3",
        ("code",),
        "error: --project is longer than the 100 characters a repository name can be",
        _project_long,
    ),
    Rule(
        "check.session.1",
        ("code",),
        "error: --session is a Claude Code session ID such as 0a608ad8-1b2c-4d3e-8f90-123456789abc, as /resume lists "
        "it (got: [{value}])",
        _session,
    ),
    Rule(
        "check.sections.1",
        ("status",),
        "error: --only takes the report's sections, comma-separated: vms storage capacity access sign-ins system work "
        "(got: [{value}])",
        _sections_spelled,
    ),
    Rule(
        "check.sections.2",
        ("status",),
        "error: the report has no section [{section}]: its sections are vms storage capacity access sign-ins system "
        "work",
        _sections_known,
    ),
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
