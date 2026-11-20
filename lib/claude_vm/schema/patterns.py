"""The one home of the patterns the command line holds a value to, and `full`, the one way to hold a value to one
(docs/architecture/engine.md §16.2).

Each pattern is its inner text, with no anchor, under a name that the table of verbs and options gives an option or a
verb's words. `full` holds a value whole, with re.fullmatch, so nothing comes before or after it, a newline included:
`$` would take "web\\n" (tests/unit/test_anchors.py). An option's pattern takes the empty value where today's does,
since an empty value is no value given. A rule that is more than one pattern is the check's, never this module's: two
values together, a word that starts with -, a length beside a list (--repos' 2000 characters), a value the pattern
takes and a rule refuses (--project's . and ..), a list of names held to the roles there are (--tags).

Until the cutover every pattern of a person's recipe in the justfile is here under a name with the same text, and
tests/unit/test_aw_schema.py holds the two equal: the justfile holds a value to a pattern before its script runs, and
the new engine holds the same value to the same pattern here. A pattern of the developer's recipes (lint, test, docs,
vagrant, clean) is not the command line's, and stays the justfile's.

Standard library only: every command reads it, a refusal and help among them. No records, no registry of operations,
no coded refusals: a refusal is the check's one sentence, which never says the value back.
"""

from __future__ import annotations

import re

# Every pattern of the command line, by its name: its inner text, written by hand, which nothing generates or copies
# into another file. Where today's justfile uses it is said above each.
PATTERNS: dict[str, str] = {
    # The VMs a verb is given as its words (deploy, login, status and destroy; code's one): a name starts with a letter
    # or a digit, and one word may name several, comma-separated.
    "vm_name": "[A-Za-z0-9][A-Za-z0-9._,-]*",
    # VMs given to an option, comma-separated, or none: destroy's --confirm, host's --for.
    "vm_names": "([A-Za-z0-9][A-Za-z0-9._,-]*)?",
    # --host on every verb that takes it but init: a host of the fleet's, or none for the current one.
    "host_name": "([a-z0-9][a-z0-9-]{0,30})?",
    # host's word: the host to make the current one.
    "current_host": "[a-z0-9][a-z0-9-]{0,30}",
    # deploy's --cores, --memory and --disk, for each VM it creates: 1 to 999 cores; memory in MiB, or with M or G; a
    # disk with G or T. Each a whole number that starts with no 0.
    "cores": "([1-9][0-9]{0,2})?",
    "memory": "([1-9][0-9]{0,6}[MG]?)?",
    "disk": "([1-9][0-9]{0,5}[GT])?",
    # deploy's --repos: GitHub repositories, comma-separated, each of at most 100 characters and none starting with .
    # or -.
    "repos": "([A-Za-z0-9_][A-Za-z0-9._-]{0,99}(,[A-Za-z0-9_][A-Za-z0-9._-]{0,99})*)?",
    # deploy's --tags: roles, comma-separated.
    "roles": "[a-z_,]*",
    # deploy's --only.
    "stage": "(template|provision|configure|ssh-config)?",
    # login's --only.
    "sign_in": "(claude|github|tailscale)?",
    # status's --only: its sections, comma-separated.
    "sections": (
        "((vms|storage|capacity|access|sign-ins|system|work)(,(vms|storage|capacity|access|sign-ins|system|work))*)?"
    ),
    # code's --project: one repository's folder, by its GitHub name.
    "project": "([A-Za-z0-9._-]{1,100})?",
    # code's --session: a Claude Code session ID as /resume lists it, lowercase hex in groups of 8-4-4-4-12.
    "session": "([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?",
    # settings' word: a role, or one variable or setting.
    "setting_name": "[a-z][a-z0-9_]*",
    # A job's ID, which logs and cancel take: sixteen hex digits, as the agents' server makes and finds one today
    # (lib/claude_vm/mcp/jobs.py). No recipe of the justfile takes one.
    "job_id": "[0-9a-f]{16}",
    # code's --editor: the editors it can open.
    "editor": "(vscode|zed)?",
    # help's word: a verb.
    "verb": "[a-z]+",
    # One line of text, with no control character: settings' --value, mcp's --browser, and the words after mcp's
    # options, which mcp refuses.
    "text": r"[^\x00-\x1f\x7f]*",
    # A path from / or ~, with no control character, or none: mcp's --project-dir and --vault-password-file.
    "path": r"([/~][^\x00-\x1f\x7f]*)?",
    # mcp's --attended: whether someone is at the screen, or unset.
    "attended": "(0|1)?",
    # init's --address, and its --host, which through 2.x takes an address too.
    "address": "[A-Za-z0-9.:-]*",
    # init's --storage, --bridge and --token-id: the names Proxmox gives a storage, a bridge and a token.
    "proxmox_id": "[A-Za-z0-9._-]*",
    # init's --token-user: a user with its realm.
    "token_user": "[A-Za-z0-9._@-]*",
    # init's --github-user.
    "github_user": "[A-Za-z0-9-]*",
    # init's --token-secret-file.
    "secret_file": "[A-Za-z0-9._/~+-]*",
    # init's --tailscale.
    "on_off": "(on|off)?",
    # init's --certificate: a SHA-256 fingerprint, as openssl prints it.
    "fingerprint": "(([0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2})?",
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
        compiled = _COMPILED[name] = re.compile(PATTERNS[name])
    return compiled.fullmatch(value) is not None
