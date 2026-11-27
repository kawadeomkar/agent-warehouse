"""The one table of the command line: every verb a person types after aw, with its words and its options, and the exit
statuses beside it (docs/architecture/engine.md §16.2).

Each verb is a row: its words and options, each option with its kind, the name of the pattern its value is held to,
what a value may be in words and an example of one where its own differ from the pattern's, its help and its fate for
an agent's tool; the codes of the rules between its options; its handler, or the card that will build it; the locks a
run of it takes; whether a run of it is a job, and so can be started detached; and the tools of the agents' server
that run it, or why none does. The parser, the one check, help and the reference page, the printer of a command, the
locks and the agents' schemas read it when they run. Written by hand as literals: nothing is generated from it into
another file or into it from another.

Today's verbs keep today's words and help, with aw where they said just, but where a later card changed one. The rules
are named by code (deploy.4, check.names.1): the sentence is the rule's own, written where the rule is (rules.py).

A sentence of the table, of the rules or of any part of the new engine that names a command names it as a `Line` in a
field of the sentence, never as text: the printer of a command's `said` (printer.py) fills it with the line the printer
writes, so that every command a person reads is the program as it was started, spelled as the printer spells one.
`LINES` holds the commands the table's own sentences name. `placeholder` is the name help gives a value or a verb's
words, which a line that shows a command's form writes in its place (<cores>, <name>). Standard library only, and
nothing else of schema/: every command reads it, a refusal and help among them.
"""

from __future__ import annotations

import keyword
from collections.abc import Mapping
from dataclasses import dataclass, field

# What an option takes: a value; nothing (a flag); a value, given once for each (--set); or nothing, given once or more
# and counted (-vvv).
KINDS = ("value", "flag", "repeatable", "counted")
# The locks a run of a verb takes. machines: with names, the fleet's lock shared and each machine's lock; with none, the
# fleet's lock alone. fleet: the fleet's lock alone, whatever is named. none. words: worked out from the words given, by
# a function of the verb's name in lib/claude_vm/runs/lock_sets.py.
LOCKS = ("machines", "fleet", "none", "words")
# What an agent's tool does with an option: offers it as a property of its schema; gives it itself (fixed); or never
# takes it (refused).
FATES = ("property", "fixed", "refused")


@dataclass(frozen=True)
class Fate:
    """An option's fate for an agent's tool. property: a property of the tool's schema. fixed: the server gives it to
    every call, value being what it gives (True for a flag), or None where it works the value out for each call, which
    why says. refused: the tool never takes it, and why says why."""

    kind: str
    value: str | bool | None = None
    why: str = ""


PROPERTY = Fate("property")


def fixed(value: str | bool | None, why: str = "") -> Fate:
    """The server gives the option to every call: value, or, where value is None, what why says."""
    return Fate("fixed", value, why)


def refused(why: str) -> Fate:
    """The tool never takes the option, for why."""
    return Fate("refused", None, why)


# A fate for every tool that runs the verb, by the tool's name, where two tools run it (settings and settings_set);
# else the one fate.
Agent = Fate | dict[str, Fate]


def fate(agent: Agent, tool: str) -> Fate:
    """The fate an option or a verb's words have for one tool."""
    return agent[tool] if isinstance(agent, dict) else agent


@dataclass(frozen=True)
class Option:
    """An option. long and short are its spellings without their dashes (short alone for -v). kind is one of KINDS.
    pattern names the pattern of schema/patterns.py that its value is held to, None for a flag and a count. field is
    the name the handler receives it by: the long option in lowercase with _ for -, with _ after a word Python keeps
    for itself (for_). help is its sentence, None for the one hidden word, which no person types. agent is its fate for
    an agent's tool. metavar names what its value is, in lower case, as placeholder writes it (host, for --host <host>;
    name=value, for --set <name>=<value>), None for a flag and a count. until is None, or the card that builds what
    the option does: help and the command reference leave the option out until then, and the parser and the check take
    it all the same, its verb refusing it (install's options of the installed program, H14's and H15's, which a
    checkout refuses). value_words and example are what its value may be in words and an example of one, the option's
    own in place of its pattern's, which help and a refusal say after its help (patterns.py's form), where the pattern
    is shared with values of another kind (init --bridge's vmbr1, not a storage's name): None for the pattern's. Its
    pattern holds the example, which is said only where the pattern shows one, and the words are never wider than the
    pattern's text."""

    long: str | None
    short: str | None
    kind: str
    pattern: str | None
    field: str
    help: str | None
    agent: Agent
    metavar: str | None = None
    until: str | None = None
    value_words: str | None = None
    example: str | None = None


@dataclass(frozen=True)
class Words:
    """The words a verb takes after its options: name is what the usage calls one (deploy [<name>...]), plural whether
    it takes several, field the name the handler receives them by, pattern the pattern each is held to (None where the
    verb refuses any word: init's), help its sentence, agent its fate for an agent's tool. value_words and example are
    what a word may be in words and an example of one, their own in place of the pattern's, as an option's are
    (release's one machine, not a VM's name): None for the pattern's. needed and refused say, side by side, what the
    number does not: needed, that the verb needs its one word (code's VM, release's machine), which a usage line then
    shows without brackets (aw code <name>); refused, that the words are no word to give, as today's EXTRA is (init's
    and mcp's, which each refuse any): help leaves them out of a usage line and the command reference out of its rows,
    and says no form for them, nor does the refusal of one outside its pattern, and the one check refuses no
    placeholder of them, which the verb's own rule refuses as any word. agent_needed, that an agent's tool needs a word
    the terminal does not, where none would reach what nobody named (destroy's names, the default VM; cancel's job, the
    one job that runs, a terminal's among them)."""

    name: str
    plural: bool
    field: str
    pattern: str | None
    help: str
    agent: Agent
    value_words: str | None = None
    example: str | None = None
    needed: bool = False
    refused: bool = False
    agent_needed: bool = False


@dataclass(frozen=True)
class Verb:
    """A verb. group is use for every verb but help, as the justfile groups them. doc is its sentence. words are the
    words it takes, None for none. rules are the codes of the rules between its options, in the order they are said.
    handler is module:function under claude_vm, or None while no card has built the verb, and card names the card that
    builds it. locks is one of LOCKS. changes: a run of it is a job, with its own record, text log and locks, so
    --detach starts it in a session of its own and the agents' server starts it so. tools names the tools of the
    agents' server that run it, or is why none does."""

    name: str
    group: str | None
    doc: str
    words: Words | None
    options: tuple[Option, ...]
    rules: tuple[str, ...]
    handler: str | None
    card: str
    locks: str
    changes: bool
    tools: tuple[str, ...] | str


def _field(long: str) -> str:
    """The name the handler receives an option by: the long option in lowercase with _ for -, and _ after a keyword."""
    name = long.lower().replace("-", "_")
    return f"{name}_" if keyword.iskeyword(name) else name


def _value(
    long: str,
    pattern: str,
    metavar: str,
    help: str,
    agent: Agent = PROPERTY,
    short: str | None = None,
    until: str | None = None,
    *,
    value_words: str | None = None,
    example: str | None = None,
) -> Option:
    return Option(long, short, "value", pattern, _field(long), help, agent, metavar, until, value_words, example)


def _flag(long: str, help: str | None, agent: Agent = PROPERTY, until: str | None = None) -> Option:
    return Option(long, None, "flag", None, _field(long), help, agent, None, until)


# ---------------------------------------------------------------- a command a sentence names ----


class Placeholder:
    """What a line gives a value or a verb's words in place of one, so that it names them by the name help gives them
    (placeholder): <name> for the words, <name>... for several, and <value> for a value (aw destroy <name>... --confirm
    <names>). Never a value: the one check refuses it as one, since it is no string, and refuses the text the printer
    writes for it as well."""

    def __repr__(self) -> str:
        return "PLACEHOLDER"


PLACEHOLDER = Placeholder()


@dataclass(frozen=True)
class Line:
    """A command line a sentence names: a verb and its values, as the parser gives them, any of which may be
    PLACEHOLDER; and host_flag as the printer of a command takes it, which a line of a verb that takes the shared --host
    gives: "" where it names no host, as a line that shows a command's form, said before any fleet is read, does. said
    writes it."""

    verb: str
    values: Mapping[str, object] = field(default_factory=dict)
    host_flag: str | None = None


# The commands the table's own sentences name, by the field each names one with.
LINES: dict[str, Line] = {
    "help": Line("help"),
    "help_of_one": Line("help", {"command": PLACEHOLDER}),
    "hosts": Line("host"),
    "vault": Line("vault"),
    "mcp": Line("mcp"),
    # deploy --tags' help: beside VM names it means --only configure.
    "tags_beside_names": Line("deploy", {"names": ["alpha"], "tags": "claude_code"}, host_flag=""),
}


def placeholder(item: Option | Words) -> str:
    """The name help gives an option's value or a verb's words, as help's rows, the command reference and a line that
    shows a command's form write it: lower case, in angle brackets, never quoted (<cores>, <name>=<value>; <name> for
    the words, which a line writes <name>... where the verb takes several). The one check refuses a value or a word that
    is exactly it: a placeholder given back to aw stands for a value, and is never taken for one."""
    if isinstance(item, Words):
        return f"<{item.name}>"
    return "=".join(f"<{part}>" for part in (item.metavar or "value").split("="))


# ---------------------------------------------------------------- the fates an agent's tool gives ----

# Why no tool runs a verb, as today's agents' server says it (lib/claude_vm/mcp/recipes.py's NO_TOOL), with aw where it
# said just.
NO_TOOL_HELP = "the tools' own descriptions say what {help} says"
NO_TOOL_VAULT = "the vault is edited at a terminal, by the person: no tool takes or returns a secret"
NO_TOOL_MCP = "it registers this server: the person's command"

# Why a tool does not take an option, as today's agents' server says it (lib/claude_vm/mcp/recipes.py's LEFT_OUT).
LOCK_LEFT_OUT = "it rewrites uv.lock after a pin is edited: a contributor's, at a terminal"
VALUE_LEFT_OUT = "a write into local.yml: settings_set's, after the person confirms"
ONE_SETTING = "settings_set changes the one setting named"
CONFIRMED = "the server gives it, the names the person typed in its dialog"
TOKEN_SECRET_FILE_LEFT_OUT = (
    "a file's contents would go into the vault and to the Proxmox host as the token: any file this user can read, "
    "an SSH key among them"
)
INIT_EXTRA_LEFT_OUT = "init refuses any word after its options"
HOST_NAME_LEFT_OUT = (
    "it makes a host the current one, which is the terminal's, as the default VM is: no agent changes it, and each "
    "run tool takes the host it runs against as host"
)
HOST_REMOVE_LEFT_OUT = "it deletes a host's settings and token: the person's, at a terminal, after typing its name"
HOST_ASKED = (
    "the server asks it for itself, for the host a run tool's call would reach: each run tool takes the host it runs "
    "against as host"
)

# Why a tool does not take a word new to the table.
SET_REFUSED = (
    "a value for one run would reach past settings_set's list and its value rules with no dialog: an agent changes a "
    "setting through settings_set alone"
)
VERBOSE_REFUSED = "how much the system layer says is the person's, at a terminal: an agent follows a run by its job"
ADOPT_REFUSED = "an agent never adopts a machine: the person's, at a terminal or by --adopt in a script of their own"
REMOVE_KEY_REFUSED = "it takes a key off each VM's account: the person's, at a terminal"
TAKE_OVER_REFUSED = "it clears another computer's run recorded on a machine: the person's, at a terminal"
PRINT_COMMANDS_REFUSED = "it prints the commands the Proxmox host's root runs: the administrator's, by hand"
FOLLOW_REFUSED = "it waits until the job ends: an agent follows a job with job, which returns when it waits or ends"
INSTALLED_REFUSED = "the installed program's, after 3.0: a checkout refuses it, and takes a newer version by git pull"
DETACHED_RUN_REFUSED = "the run --detach starts is given it by the program itself: nobody types it"

# Given by the server.
JSON = fixed(True)
ATTENDED = fixed(
    None, "for each run: what {mcp} recorded, else 1 exactly when the session is an interactive one at this computer"
)
# --detach is no row's option: the parser gives it to every verb whose changes is true, with this help, and the server
# starts each such run detached and follows its job.
DETACH = "Start the run in a session of its own and say its job's ID once it is under way: it goes on after this ends"
DETACH_AGENT = fixed(True, "the server starts every run that is a job detached, and follows the job")

# What an adopted machine's name may be, for release's word and its --confirm: one name, where the patterns they are
# held to are a VM's, and several joined by commas.
_MACHINE = (
    "an adopted machine's name: letters, digits, dots, underscores and hyphens, starting with a letter or a digit"
)

# ---------------------------------------------------------------- the words several verbs share ----

# --host on deploy, code, login, status and destroy.
HOST = _value("host", "host_name", "host", "Run against this host rather than the current one ({hosts} lists them)")
# A setting's value for one run, on every verb that reads the fleet's settings to do its work.
SET = Option(
    "set",
    None,
    "repeatable",
    "assignment",
    "set",
    "One setting's value for this run alone, never a secret, held to its type as settings --value holds it: "
    "--set <name>=<value>, once for each",
    refused(SET_REFUSED),
    "name=value",
)
# The system layer's verbosity, on the same verbs but status, which runs no system layer.
VERBOSE = Option(
    None,
    "v",
    "counted",
    None,
    "verbosity",
    "Say more of what the system layer does on each machine, -v to -vvvvv: any -v shows its stream in place of the run "
    "drawn live",
    refused(VERBOSE_REFUSED),
)

# The words given before any verb.
BEFORE: tuple[Option, ...] = (
    _value(
        "attended",
        "attended",
        "attended",
        "Someone is at this computer's screen with no terminal: 1, the sign-ins run and open their pages here and "
        "nothing asks; the agents' server gives it",
        ATTENDED,
    ),
    # The run a detached start began, in a session of its own (lib/claude_vm/runs/detached.py): hidden from help.
    _flag("detached-run", None, refused(DETACHED_RUN_REFUSED)),
)

# ---------------------------------------------------------------- the verbs ----

_VERBS: tuple[Verb, ...] = (
    Verb(
        name="help",
        group=None,
        doc="Show the commands, or the options of one: {help_of_one}",
        words=Words(
            "command", False, "command", "verb", "The one command whose options to show", refused(NO_TOOL_HELP)
        ),
        options=(),
        rules=("help.1",),
        handler="cli.help:main",
        card="H6",
        locks="none",
        changes=False,
        tools=NO_TOOL_HELP,
    ),
    Verb(
        name="init",
        group="use",
        doc="Set up this checkout, then ask for what a first deploy needs",
        words=Words(
            "extra",
            False,
            "extra",
            None,
            "Nothing: any word after the options is refused, unsaid",
            refused(INIT_EXTRA_LEFT_OUT),
            refused=True,
        ),
        options=(
            _value("address", "address", "address", "The Proxmox host's address, for its hosts.yml"),
            _value(
                "host", "host_name", "host", "Set up another host of this checkout, by its name ({hosts} lists them)"
            ),
            _value("token-user", "token_user", "user", "The API token's user, when it is not agent-warehouse@pve"),
            _value(
                "token-id",
                "proxmox_id",
                "id",
                "The API token's ID, the part after its user's !, when it is not aw",
                example="automation",
            ),
            _value(
                "token-secret-file",
                "secret_file",
                "file",
                "A file holding the token's secret (or AGENT_WAREHOUSE_TOKEN_SECRET in the environment; never on the "
                "command line)",
                refused(TOKEN_SECRET_FILE_LEFT_OUT),
            ),
            _value("github-user", "github_user", "account", "The GitHub account whose repositories are cloned"),
            _value(
                "storage",
                "proxmox_id",
                "storage",
                "The storage for VM disks, for local.yml: written when the token can use it there",
            ),
            _value(
                "bridge",
                "proxmox_id",
                "bridge",
                "The bridge the template is built on, for local.yml (before the template exists)",
                example="vmbr1",
            ),
            _value(
                "tailscale",
                "on_off",
                "answer",
                "Answers init's VPN question: on, each VM joins your tailnet; off, none - a VPN of yours that routes "
                "the LAN, or none at all (vpn in local.yml)",
            ),
            _value(
                "certificate",
                "fingerprint",
                "fingerprint",
                "The SHA-256 fingerprint of the certificate to check the API against, as the host prints it (openssl "
                "x509 -noout -fingerprint -sha256): kept, or put in place of one that changed, only when it is that "
                "one",
            ),
            _flag("no-prompt", "Ask nothing, even at a terminal", fixed(True)),
            _flag("check", "Run init's checks again and print their ok and !! lines, as the end of a first init does"),
            _flag(
                "print-commands",
                "Print the commands that make what init needs on the Proxmox host, for its root to run, rather than "
                "running them over root SSH",
                refused(PRINT_COMMANDS_REFUSED),
            ),
            _value(
                "pool",
                "proxmox_id",
                "pool",
                "The Proxmox pool that holds the fleet's VMs, when it is not aw-fleet",
                example="dev-vms",
            ),
            _flag(
                "machines",
                "Answer init's first question with machines: a fleet of the machines machines.yml lists, with no "
                "Proxmox host",
            ),
            SET,
            VERBOSE,
        ),
        rules=("init.1",),
        handler="engine.init:init",
        card="E4",
        locks="fleet",
        changes=True,
        tools=("init",),
    ),
    Verb(
        name="install",
        group="use",
        doc="Install the locked Python packages and the Galaxy collections: into your virtualenv, or this checkout's "
        "own",
        words=None,
        options=(
            _flag(
                "lock",
                "Update uv.lock after editing a pin in pyproject.toml, installing nothing (uv from PATH)",
                refused(LOCK_LEFT_OUT),
            ),
            _value(
                "version",
                "release",
                "version",
                "Bring the installed program to this release rather than the newest; refused in a checkout",
                refused(INSTALLED_REFUSED),
                until="H14",
            ),
            _flag(
                "newer",
                "Say whether a newer release exists, and the interpreter's version and age, changing nothing; refused "
                "in a checkout",
                refused(INSTALLED_REFUSED),
                until="H15",
            ),
            _flag(
                "rollback",
                "Go back to the release kept before this one; refused in a checkout",
                refused(INSTALLED_REFUSED),
                until="H15",
            ),
            _value(
                "without",
                "dependency_group",
                "group",
                "Install the release without this optional part: mcp, the agents' server; refused in a checkout",
                refused(INSTALLED_REFUSED),
                until="H14",
            ),
        ),
        rules=(),
        handler=None,
        card="H18",
        locks="fleet",
        changes=True,
        tools=("install",),
    ),
    Verb(
        name="vault",
        group="use",
        doc="Encrypt the vault, or edit it once it is encrypted",
        words=Words(
            "name",
            False,
            "name",
            "secret_name",
            "One secret by its name: put from the terminal, hidden, or from --file, or taken out with --remove; none "
            "opens every secret in your editor",
            refused(NO_TOOL_VAULT),
        ),
        options=(
            _value(
                "host",
                "host_name",
                "host",
                "That host's own vault, which holds its token, rather than the shared one ({hosts} lists them)",
                refused(NO_TOOL_VAULT),
            ),
            _flag("list", "Each secret's name, and whether it is set, never a value", refused(NO_TOOL_VAULT)),
            _value(
                "file",
                "secret_file",
                "file",
                "With a name: the file that holds the secret's value, read once; never the value on the command line",
                refused(NO_TOOL_VAULT),
            ),
            _flag("remove", "With a name: take that secret out of the vault", refused(NO_TOOL_VAULT)),
            _flag(
                "rotate",
                "Make a new key and encrypt the vault again under it, while no run changes anything",
                refused(NO_TOOL_VAULT),
            ),
        ),
        rules=(),
        handler=None,
        card="D11",
        locks="words",
        changes=False,
        tools=NO_TOOL_VAULT,
    ),
    Verb(
        name="deploy",
        group="use",
        doc="Create, configure and sign in the VM(s), then open them in your editor",
        words=Words(
            "name",
            True,
            "names",
            "vm_name",
            "The VMs; none means agent-warehouse-default, or every VM this project created for --only configure, "
            "--check and --only ssh-config",
            PROPERTY,
        ),
        options=(
            _value(
                "only",
                "stage",
                "stage",
                "Run one stage and nothing after it: template (the cloud-init template, if missing), provision (create "
                "and start), configure (every tagged VM, unless named) or ssh-config (refresh the SSH aliases; without "
                "names, also prune those of VMs that are gone)",
            ),
            _flag("check", "Dry-run configuration: ansible-playbook --check --diff (every tagged VM, unless named)"),
            HOST,
            _value(
                "tags",
                "roles",
                "roles",
                "Configure only these roles, comma-separated: beside VM names it means --only configure "
                "({tags_beside_names})",
                short="t",
            ),
            _flag("start", "Start a VM this run would leave stopped (proxmox_vm_start_existing)"),
            _value("cores", "cores", "cores", "Each new VM's vCPU cores, in place of the vm_cores setting"),
            _value(
                "memory", "memory", "memory", "Each new VM's memory: MiB, or 16G, in place of the vm_memory_mb setting"
            ),
            _value(
                "disk", "disk", "disk", "Each new VM's root disk, as 64G or 1T, in place of the vm_disk_size setting"
            ),
            _value(
                "repos",
                "repos",
                "repos",
                "Only these of your GitHub repositories on each VM named, comma-separated, in place of the "
                "github_projects setting: kept for that VM, and added to by a later --repos",
            ),
            _flag(
                "adopt",
                "Adopt each machine named that this fleet has not adopted, with nobody there to answer the question",
                refused(ADOPT_REFUSED),
            ),
            _value(
                "remove-key",
                "ssh_fingerprint",
                "fingerprint",
                "With --only configure: take the key of this SHA256 fingerprint, as ssh-keygen -l prints it, off each "
                "VM's account",
                refused(REMOVE_KEY_REFUSED),
            ),
            _flag(
                "take-over",
                "Under machine_claims: refuse, go on where another computer's run is recorded on a machine, and clear "
                "its record",
                refused(TAKE_OVER_REFUSED),
            ),
            SET,
            VERBOSE,
        ),
        rules=(
            "check.repos.2",
            "deploy.4",
            "deploy.5",
            "deploy.6",
            "deploy.7",
            "deploy.8",
            "deploy.9",
            "deploy.10",
            "deploy.11",
            "deploy.12",
        ),
        handler="engine.deploy:deploy",
        card="G3",
        locks="words",
        changes=True,
        tools=("deploy",),
    ),
    Verb(
        name="code",
        group="use",
        doc="Open a VM in your editor, VS Code by default, with Claude on the VM and this machine's recent Claude "
        "sessions copied there",
        words=Words("name", False, "name", "vm_name", "The one VM to open", PROPERTY, needed=True),
        options=(
            _value(
                "project",
                "project",
                "project",
                "Open one project folder on the VM rather than the whole projects directory",
            ),
            _value(
                "editor",
                "editor",
                "editor",
                "Open it in this editor for this run, rather than the one the editor setting names: vscode or zed",
            ),
            HOST,
            _value(
                "session",
                "session",
                "session",
                "Continue one of this machine's Claude Code sessions there, by its ID as /resume lists it: its "
                "checkout brought to the session's pushed commit, ready for claude --resume",
            ),
            SET,
            VERBOSE,
        ),
        rules=("code.1", "code.2", "check.project.2"),
        handler="engine.code:run",
        card="G17",
        locks="machines",
        changes=True,
        tools=("code",),
    ),
    Verb(
        name="login",
        group="use",
        doc="Sign the VM(s) in to Claude, GitHub and Tailscale, or report how each is signed in",
        words=Words(
            "name",
            True,
            "names",
            "vm_name",
            "The VMs to sign in, one after another; none reports every VM this project created",
            PROPERTY,
        ),
        options=(
            HOST,
            _value(
                "only",
                "sign_in",
                "sign-in",
                "One sign-in: claude (by the browser, then Remote Control), github (the device flow, then the private "
                "repositories) or tailscale (its web login)",
            ),
            SET,
            VERBOSE,
        ),
        rules=(),
        handler=None,
        card="G12",
        locks="machines",
        changes=True,
        tools=("login",),
    ),
    Verb(
        name="status",
        group="use",
        doc="What every VM this project created is doing, and what needs doing next, read without changing anything",
        words=Words(
            "name",
            True,
            "names",
            "vm_name",
            "Only these VMs; none reads every VM this project created",
            PROPERTY,
        ),
        options=(
            HOST,
            _value(
                "only",
                "sections",
                "sections",
                "Only these sections, comma-separated: vms storage capacity access sign-ins system work; vms, storage "
                "and capacity read only the Proxmox API, never a VM",
            ),
            _flag("json", "The report as JSON, alone on standard output, with the same exit code", JSON),
            SET,
        ),
        rules=(),
        handler="cli.status:main",
        card="G16",
        locks="none",
        changes=False,
        tools=("status",),
    ),
    Verb(
        name="settings",
        group="use",
        doc="Each setting's value and where it was set, for a role, a variable or all of them; --value sets one in "
        "local.yml, --reset puts it back",
        words=Words(
            "name",
            False,
            "name",
            "setting_name",
            "A role (tailscale) or one variable or setting (tailscale_ssh), or edit, which opens the settings in your "
            "editor; none shows how to use this",
            {"settings": PROPERTY, "settings_set": PROPERTY},
        ),
        options=(
            _flag(
                "all",
                "Every setting of local.yml, and which secrets of vault.yml are set",
                {"settings": PROPERTY, "settings_set": refused(ONE_SETTING)},
            ),
            _flag(
                "changed",
                "Only what local.yml or vault.yml changes from the defaults; alone, all of it",
                {"settings": PROPERTY, "settings_set": refused(ONE_SETTING)},
            ),
            _flag("json", "The report as JSON, alone on standard output", {"settings": JSON, "settings_set": JSON}),
            _value(
                "host",
                "host_name",
                "host",
                "That host's values rather than the current host's; beside --value or --reset, its own local.yml is "
                "the one changed",
                {"settings": PROPERTY, "settings_set": PROPERTY},
            ),
            _flag(
                "reset",
                "Put the one setting named back to its default: its line out of local.yml, its example's back",
                {"settings": refused(VALUE_LEFT_OUT), "settings_set": PROPERTY},
            ),
            _value(
                "value",
                "text",
                "value",
                "Set the one setting named to this value, in local.yml (never a secret: {vault}; an empty value is "
                "none: --reset)",
                {"settings": refused(VALUE_LEFT_OUT), "settings_set": PROPERTY},
            ),
        ),
        rules=("settings.1", "settings.2", "settings.3", "settings.4", "settings.5"),
        handler="fleet.settings_verb:settings",
        card="D9",
        locks="none",
        changes=False,
        tools=("settings", "settings_set"),
    ),
    Verb(
        name="destroy",
        group="use",
        doc="Save the VM(s)' own Claude sessions here, release them from Claude, GitHub and Tailscale, then delete "
        "them",
        words=Words(
            "name", True, "names", "vm_name", "The VMs; none means agent-warehouse-default", PROPERTY, agent_needed=True
        ),
        options=(
            _value(
                "confirm",
                "vm_names",
                "names",
                "Delete without asking, these VMs only: the same names as given, comma-separated",
                fixed(None, CONFIRMED),
            ),
            HOST,
            SET,
            VERBOSE,
        ),
        rules=("destroy.1", "destroy.2"),
        handler="engine.destroy:destroy",
        card="G5",
        locks="machines",
        changes=True,
        tools=("destroy",),
    ),
    Verb(
        name="release",
        group="use",
        doc="End the logins this project made on an adopted machine, remove its alias and this fleet's marker, and "
        "uninstall nothing",
        words=Words(
            "machine",
            False,
            "name",
            "vm_name",
            "The one adopted machine to release",
            PROPERTY,
            value_words=_MACHINE,
            needed=True,
        ),
        options=(
            _value(
                "confirm",
                "vm_names",
                "name",
                "Release without asking, this machine only: the same name as given",
                fixed(None, CONFIRMED),
                value_words=_MACHINE,
                example="alpha",
            ),
            SET,
            VERBOSE,
        ),
        rules=(),
        handler=None,
        card="J13",
        locks="machines",
        changes=True,
        tools=("release",),
    ),
    Verb(
        name="host",
        group="use",
        doc="List the hosts, or make one the current host, which every command runs against unless --host names "
        "another",
        words=Words(
            "name",
            False,
            "name",
            "current_host",
            "The host to make the current one: every command runs against it from now on",
            refused(HOST_NAME_LEFT_OUT),
        ),
        options=(
            _flag("json", "The list as JSON, alone on standard output", JSON),
            _flag(
                "remove",
                "Delete the host's folder in hosts/, after you type its name: its VMs and their SSH aliases stay",
                refused(HOST_REMOVE_LEFT_OUT),
            ),
            _value(
                "for",
                "vm_names",
                "names",
                "With --json: the VMs a run would name, comma-separated, to say which host it would reach",
                refused(HOST_ASKED),
            ),
            _value(
                "host",
                "host_name",
                "host",
                "With --json: the host a run would be given, to say which host it would reach",
                refused(HOST_ASKED),
            ),
        ),
        rules=("host.1", "host.2", "host.3", "check.names.1", "host.4"),
        handler="fleet.host_verb:host",
        card="D12",
        locks="words",
        changes=False,
        tools=("hosts",),
    ),
    Verb(
        name="logs",
        group="use",
        doc="List the run logs in .logs/, newest first",
        words=Words(
            "job",
            False,
            "job",
            "job_id",
            "One job's log, by its ID as logs lists it; none lists the jobs, newest first",
            PROPERTY,
        ),
        options=(
            _flag(
                "follow",
                "With a job: print its log, then each line it adds, until the job ends",
                refused(FOLLOW_REFUSED),
            ),
            _flag("save", "With a job: say where its log is kept on this computer"),
        ),
        rules=(),
        handler="runs.listing:logs",
        card="C12",
        locks="none",
        changes=False,
        tools=("logs",),
    ),
    Verb(
        name="cancel",
        group="use",
        doc="Stop a job by its ID, from any terminal, as Ctrl-C stops it at its own",
        words=Words(
            "job",
            False,
            "job",
            "job_id",
            "The job to stop, by its ID as logs lists it; none stops the one job that runs",
            PROPERTY,
            agent_needed=True,
        ),
        options=(),
        rules=(),
        handler=None,
        card="C14",
        locks="none",
        changes=False,
        tools=("cancel",),
    ),
    Verb(
        name="mcp",
        group="use",
        doc="Let Claude Code sessions in any project run these commands, through an MCP server",
        words=Words(
            "extra",
            False,
            "extra",
            "text",
            "Nothing: any word after the options is refused",
            refused(NO_TOOL_MCP),
            refused=True,
        ),
        options=(
            _flag(
                "any-branch",
                "Let the server run this checkout on any branch, or with tracked changes; without it, main with no "
                "tracked change only",
                refused(NO_TOOL_MCP),
            ),
            _value(
                "attended",
                "attended",
                "attended",
                "Whether someone is at this machine's screen for the sign-ins: 1, as for Claude Desktop, or 0; unset, "
                "the server decides per session",
                refused(NO_TOOL_MCP),
            ),
            _value(
                "browser",
                "text",
                "browser",
                "What opens the sign-in pages for the runs it starts, when the default does not (BROWSER)",
                refused(NO_TOOL_MCP),
            ),
            _value(
                "project-dir",
                "path",
                "folder",
                "Register it for this one project's folder only (Claude Code's local scope), not for every project",
                refused(NO_TOOL_MCP),
            ),
            _flag("remove", "Take the server out of Claude Code again", refused(NO_TOOL_MCP)),
            _flag(
                "show",
                "Print the command, and an entry for Claude Desktop, and register nothing",
                refused(NO_TOOL_MCP),
            ),
            _value(
                "vault-key-file",
                "path",
                "file",
                "The file that holds the vault's key, for the runs the server starts with no terminal",
                refused(NO_TOOL_MCP),
                example="~/keys/fleet.key",
            ),
        ),
        rules=("mcp.1", "mcp.2"),
        handler=None,
        card="I6",
        locks="none",
        changes=False,
        tools=NO_TOOL_MCP,
    ),
)

# Every verb, by its name, in the order help lists them: the base's alone. The add-on's verb, control, is no row
# here: the add-on adds its row, with the fleet's lock, when it is built.
VERBS: dict[str, Verb] = {verb.name: verb for verb in _VERBS}

# ---------------------------------------------------------------- the exit statuses ----

# What every verb but status exits with, by meaning: no meaning has two numbers, and no number two meanings. A changing
# verb that failed, and the machines' outcomes, each come with the card that first needs it, as one more entry.
EXITS: dict[str, int] = {
    # The run did what was asked, or there was nothing to do.
    "done": 0,
    # Refused before anything ran: a value outside its pattern, an option the verb has not got, a rule between options,
    # or a check before the work.
    "refused": 1,
    # A changing run that began and failed: a machine's step failed, the others going on, or the run stopped short of
    # what was asked (card G3: the first changing verb settles it, as no contract of today's records one).
    "failed": 2,
    # A run a person cancelled: Ctrl-C at its terminal, or aw cancel from any other.
    "cancelled": 130,
}

# What status exits with, by meaning, as today: its report's code (lib/claude_vm/status/model.py's status_report), the
# read that failed, and a read a person cancelled. A refusal of status before it reads anything exits 1, as today, and
# says so in its one sentence. Today's status gives 3 for a read that failed and for one a person cancelled
# (lib/claude_vm/status/engine.py's _end_for_an_interrupt, and status/hosts.py's run with several hosts), and a script
# may read it: so 3 keeps both meanings here, the one number of these two mappings that has two. A status cancelled
# before its read starts ends as any verb's run does, with EXITS' cancelled, 130, as today's recipe does in its checks
# before tools/status reads anything: so a cancelled status exits 130 before its read and 3 during it.
STATUS_EXITS: dict[str, int] = {
    # The report: nothing is wrong.
    "nothing_wrong": 0,
    # The report: a problem.
    "problem": 1,
    # The report: something could not be read.
    "unread": 2,
    # The read itself failed.
    "failed": 3,
    # A read a person cancelled: 3, as a read that failed, as today.
    "cancelled": 3,
}
