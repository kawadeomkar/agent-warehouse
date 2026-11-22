"""The one home of the patterns the command line holds a value to, each with its form in words, and `full`, the one way
to hold a value to one (docs/architecture/engine.md §16.2).

Each pattern is a `Pattern` under a name that the table of verbs and options gives an option or a verb's words: its
inner text, with no anchor, which holds a value; its form in words, what a value may be as a person reads it; and values
it takes, for an example. `full` holds a value whole to the text, with re.fullmatch, so nothing comes before or after
it, a newline included: `$` would take "web\\n" (tests/unit/test_anchors.py). An option's pattern takes the empty value
where today's does, since an empty value is no value given. A rule that is more than one pattern is the check's, never
this module's: two values together, a word that starts with -, a length beside a list (--repos' 2000 characters), a
value the pattern takes and a rule refuses (--project's . and ..), a list of names held to the roles there are
(--tags).

A pattern of the developer's recipes (lint, test, docs, vagrant, clean) is not the command line's, and stays the
justfile's.

`form(name)` is how help and a refusal say what a value may be, after the option's or the words' help: its words and
an example, `[a whole number from 1 to 999, e.g. 8]`, or its words alone where they list the values, where an example
would be too long to print or where it would say nothing, so a person learns it from aw help <verb> and from the refusal
of a value outside it alike. Nothing says "or empty": an option not given is not given. The words describe the value
in prose, never with a pattern's own characters, and may be narrower than the text, never wider: where they differ,
the text is what holds a value. The text itself is shown where it is read as a definition, the command reference's
rows (cli/reference.py) and the agents' tools' schemas.

Standard library only: every command reads it, a refusal and help among them. No records, no registry of operations,
no coded refusals: a refusal is the check's one sentence, which never says the value back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Pattern:
    """A pattern of the command line. text is its inner text, with no anchor, which holds a value; words what a value
    may be, in prose, as help and a refusal say it; examples values it takes, each held by text, the first of which the
    form says after the words where shown is true: not where the words list the values, where it would be too long to
    print, or where it says nothing more than the words."""

    text: str
    words: str
    examples: tuple[str, ...]
    shown: bool = True


# What a host's name is, which two patterns hold: one that may be left out, and one that may not.
_HOST = (
    "a host's name: lower-case letters, digits and hyphens, starting with a letter or a digit, at most 31 characters"
)
# What a name of the vault's and a setting's are made of.
_LOWER_NAME = "lower-case letters, digits and underscores, starting with a letter"

# Every pattern of the command line, by its name: its text, its words and its examples, written by hand, which nothing
# generates or copies into another file. Where today's justfile uses it is said above each.
PATTERNS: dict[str, Pattern] = {
    # The VMs a verb is given as its words (deploy, login, status and destroy; code's one, and aw's release): a name
    # starts with a letter or a digit, and one word may name several, comma-separated, which the words leave to
    # habits.py, since code and release take one.
    "vm_name": Pattern(
        "[A-Za-z0-9][A-Za-z0-9._,-]*",
        "a VM's name: letters, digits, dots, underscores and hyphens, starting with a letter or a digit",
        ("alpha", "dev-2.lab"),
    ),
    # VMs given to an option, comma-separated, or none: destroy's --confirm (and aw's release's), host's --for.
    "vm_names": Pattern(
        "([A-Za-z0-9][A-Za-z0-9._,-]*)?",
        "VMs' names joined by commas: letters, digits, dots, underscores and hyphens, each starting with a letter or a "
        "digit",
        ("alpha,beta", "alpha"),
    ),
    # --host on every verb that takes it but init: a host of the fleet's, or none for the current one.
    "host_name": Pattern("([a-z0-9][a-z0-9-]{0,30})?", _HOST, ("lab2",)),
    # host's word: the host to make the current one.
    "current_host": Pattern("[a-z0-9][a-z0-9-]{0,30}", _HOST, ("lab2",)),
    # deploy's --cores, --memory and --disk, for each VM it creates: 1 to 999 cores; memory in MiB, or with M or G; a
    # disk with G or T. Each a whole number that starts with no 0.
    "cores": Pattern("([1-9][0-9]{0,2})?", "a whole number from 1 to 999", ("8",)),
    "memory": Pattern(
        "([1-9][0-9]{0,6}[MG]?)?",
        "a whole number of MiB, or a whole number with M or G after it",
        ("16G", "2048"),
    ),
    "disk": Pattern("([1-9][0-9]{0,5}[GT])?", "a whole number with G or T after it", ("128G", "1T")),
    # deploy's --repos: GitHub repositories, comma-separated, each of at most 100 characters and none starting with .
    # or -.
    "repos": Pattern(
        "([A-Za-z0-9_][A-Za-z0-9._-]{0,99}(,[A-Za-z0-9_][A-Za-z0-9._-]{0,99})*)?",
        "GitHub repositories' names joined by commas: letters, digits, dots, underscores and hyphens, none starting "
        "with a dot or a hyphen, at most 100 characters each",
        ("my-app,my-lib",),
    ),
    # deploy's --tags: roles, comma-separated.
    "roles": Pattern(
        "[a-z_,]*",
        "roles' names joined by commas: lower-case letters and underscores",
        ("claude_code,dev_tools",),
    ),
    # deploy's --only.
    "stage": Pattern(
        "(template|provision|configure|ssh-config)?",
        "one of template, provision, configure, ssh-config",
        ("configure", "ssh-config"),
        shown=False,
    ),
    # login's --only.
    "sign_in": Pattern("(claude|github|tailscale)?", "one of claude, github, tailscale", ("github",), shown=False),
    # status's --only: its sections, comma-separated.
    "sections": Pattern(
        "((vms|storage|capacity|access|sign-ins|system|work)(,(vms|storage|capacity|access|sign-ins|system|work))*)?",
        "one or more of vms, storage, capacity, access, sign-ins, system, work, joined by commas",
        ("vms,work", "sign-ins"),
        shown=False,
    ),
    # code's --project: one repository's folder, by its GitHub name.
    "project": Pattern(
        "([A-Za-z0-9._-]{1,100})?",
        "a repository's name: letters, digits, dots, underscores and hyphens, at most 100 characters",
        ("my-app",),
    ),
    # code's --session: a Claude Code session ID as /resume lists it, lowercase hex in groups of 8-4-4-4-12.
    "session": Pattern(
        "([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?",
        "a Claude Code session's ID, as /resume lists it: lower-case hex digits in groups of 8, 4, 4, 4 and 12, joined "
        "by hyphens",
        ("0a608ad8-1b2c-4d3e-8f90-123456789abc",),
        shown=False,
    ),
    # settings' word: a role, or one variable or setting.
    "setting_name": Pattern("[a-z][a-z0-9_]*", f"a setting's or a role's name: {_LOWER_NAME}", ("vm_cores",)),
    # A job's ID, which logs and cancel take: sixteen hex digits, as the agents' server makes and finds one today
    # (lib/claude_vm/mcp/jobs.py). No recipe of the justfile takes one.
    "job_id": Pattern("[0-9a-f]{16}", "a job's ID: sixteen lower-case hex digits", ("3f9a0c1e7b2d4a58",)),
    # code's --editor: the editors it can open.
    "editor": Pattern("(vscode|zed)?", "vscode or zed", ("zed",), shown=False),
    # help's word: a verb.
    "verb": Pattern("[a-z]+", "a command's name: lower-case letters", ("deploy",)),
    # One line of text, with no control character: settings' --value, mcp's --browser, and the words after mcp's
    # options, which mcp refuses. An example of any line says nothing the words do not.
    "text": Pattern(r"[^\x00-\x1f\x7f]*", "one line of text, with no control character", ("two words",), shown=False),
    # A path from / or ~, with no control character, or none: mcp's --project-dir and --vault-password-file, which aw
    # calls --vault-key-file.
    "path": Pattern(r"([/~][^\x00-\x1f\x7f]*)?", "a path that starts with / or ~", ("~/projects/app", "/srv/aw")),
    # mcp's --attended: whether someone is at the screen, or unset; and aw's --attended before a verb.
    "attended": Pattern("(0|1)?", "0 or 1", ("1",), shown=False),
    # init's --address.
    "address": Pattern("[A-Za-z0-9.:-]*", "a host's name or an IP address", ("pve.example", "192.0.2.10")),
    # init's --storage, --bridge and --token-id: the names Proxmox gives a storage, a bridge and a token; and aw's init
    # --pool, a pool's.
    "proxmox_id": Pattern(
        "[A-Za-z0-9._-]*",
        "a name as Proxmox gives one: letters, digits, dots, underscores and hyphens",
        ("local-lvm",),
    ),
    # init's --token-user: a user with its realm.
    "token_user": Pattern("[A-Za-z0-9._@-]*", "a Proxmox user and its realm, joined by @", ("ansible@pve",)),
    # init's --github-user.
    "github_user": Pattern("[A-Za-z0-9-]*", "a GitHub account's name: letters, digits and hyphens", ("octocat",)),
    # init's --token-secret-file; and aw's vault --file, the file that holds one secret.
    "secret_file": Pattern(
        "[A-Za-z0-9._/~+-]*",
        "a file's path with no space or quote: letters, digits, dots, underscores, hyphens, plus signs, slashes and "
        "tildes",
        ("~/secrets/token",),
    ),
    # init's --tailscale.
    "on_off": Pattern("(on|off)?", "on or off", ("on",), shown=False),
    # init's --certificate: a SHA-256 fingerprint, as openssl prints it.
    "fingerprint": Pattern(
        "(([0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2})?",
        "a SHA-256 fingerprint as openssl prints it: 32 pairs of hex digits joined by colons",
        (":".join(["AB", "CD", "EF", "01", "23", "45", "67", "89"] * 4),),
        shown=False,
    ),
    # No recipe of the justfile takes one of these: they are aw's.
    # --set's: a setting's name, =, and its value for one run, a line of text.
    "assignment": Pattern(
        r"[a-z][a-z0-9_]*=[^\x00-\x1f\x7f]*",
        "a setting's name, then = and its value for this run",
        ("vm_cores=8",),
    ),
    # vault's word: one of the vault's named secrets.
    "secret_name": Pattern("[a-z][a-z0-9_]*", f"a secret's name in the vault: {_LOWER_NAME}", ("github_token",)),
    # deploy's --remove-key: one SSH key's SHA256 fingerprint, as ssh-keygen -l prints it.
    "ssh_fingerprint": Pattern(
        "(SHA256:[A-Za-z0-9+/]{43})?",
        "an SSH key's fingerprint as ssh-keygen -l prints it: SHA256: and 43 characters of base64",
        ("SHA256:" + "Ab0+" * 10 + "xyz",),
        shown=False,
    ),
    # install's --version: a release, as its tag names it, with or without its v.
    "release": Pattern(
        r"(v?[0-9]+\.[0-9]+\.[0-9]+(-[0-9a-z]+(\.[0-9a-z]+)*)?)?",
        "a release as its tag names it: three whole numbers joined by dots, with or without a v before them",
        ("v3.0.0", "3.0.0-rc.1"),
    ),
    # install's --without: the optional part an installed program goes without.
    "dependency_group": Pattern("(mcp)?", "mcp", ("mcp",), shown=False),
}

# Each pattern compiled, on its first use.
_COMPILED: dict[str, re.Pattern[str]] = {}


def full(name: str, value: str) -> bool:
    """Whether value, whole, is held by the pattern PATTERNS names name: re.fullmatch, so with nothing before or after
    it, a newline included. The pattern is compiled once, on its first use. A name that is not in PATTERNS raises
    KeyError: an option that names no pattern is the table's fault, never a refusal of the value."""
    compiled = _COMPILED.get(name)
    if compiled is None:
        if name not in PATTERNS:
            raise KeyError(f"no pattern of the command line is named {name!r}")
        compiled = _COMPILED[name] = re.compile(PATTERNS[name].text)
    return compiled.fullmatch(value) is not None


def form(name: str) -> str:
    """What a value of the pattern PATTERNS names name may be, as help and a refusal say it after the option's or the
    words' help: [<its words>, e.g. <its first example>], or [<its words>] where the pattern shows no example. A name
    that is not in PATTERNS raises KeyError, as full does."""
    pattern = PATTERNS[name]
    return f"[{pattern.words}, e.g. {pattern.examples[0]}]" if pattern.shown else f"[{pattern.words}]"
