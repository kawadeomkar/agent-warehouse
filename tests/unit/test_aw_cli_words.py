"""The one table of the command line (lib/claude_vm/schema/words.py; docs/architecture/engine.md §16.2).

Every verb has its handler or the card that builds it, the locks a run of it takes, whether a run of it is a job, and
the tools of the agents' server that run it or why none does; every option a kind, a pattern of schema/patterns.py
unless it takes no value, a name of what its value is, which help and a line's form write as its placeholder (<cores>),
and a fate for an agent's tool, by tool where two tools run its verb. Which pattern each option's value and each verb's
words are held to is the one a map of this file's own names, written by hand, both ways; and so are the example the
form of each shows, or none, and the words of an option's own, in maps written beside it. An option's own example is
held by its pattern, shown, and not its pattern's, and its own words are prose, as a pattern's are; --set's sentence
names the placeholder its row prints. No verb of the add-on is a verb of the base's table, and no word the trims took
out of the base is a word of it. Each reason a verb no tool runs gives is said with its commands the printer's. Every
option and word of a verb a tool runs is a property of the tool's schema, but those ruled otherwise, each named here.
No option's value is a secret, but the two that take free text a person may type one into, which the run layer refuses
(--set's, D5's) or is to refuse (settings --value's, D10's) and never says back; no sentence says just, a reason why an
agent's tool does not take a word among them; the verbs that need their one word and those that refuse any are the
ones named here; each meaning of an exit status has one number and each number one meaning but status's 3, which
today gives a read that failed and one a person cancelled, in its run of one host and of several, the pair named with
its reason; and the module imports the standard library alone.
"""

from __future__ import annotations

import ast
import keyword
import re
import sys
from pathlib import Path

import pytest
from aw_kit import imports_of

from claude_vm.schema import printer, words
from claude_vm.schema.patterns import PATTERNS, full
from claude_vm.schema.words import (
    BEFORE,
    DETACH,
    DETACH_AGENT,
    EXITS,
    FATES,
    KINDS,
    LOCKS,
    PROPERTY,
    STATUS_EXITS,
    VERBS,
    Fate,
    Option,
    fate,
)

MODULE = "claude_vm.schema.words"
# Today's status, whose end on a Ctrl-C the table keeps: its run of one host, and its run of several.
STATUS_ENGINE = Path(__file__).resolve().parents[2] / "lib" / "claude_vm" / "status" / "engine.py"
STATUS_HOSTS = STATUS_ENGINE.with_name("hosts.py")
# The verbs whose run is a job, with its own record, text log and locks.
JOBS = {"deploy", "login", "code", "destroy", "release", "init", "install"}
# The tools of the agents' server that run each verb, and the verbs no tool runs.
TOOLS_OF = {
    "init": ("init",),
    "install": ("install",),
    "deploy": ("deploy",),
    "code": ("code",),
    "login": ("login",),
    "status": ("status",),
    "settings": ("settings", "settings_set"),
    "destroy": ("destroy",),
    "release": ("release",),
    "host": ("hosts",),
    "logs": ("logs",),
    "cancel": ("cancel",),
}
NO_TOOL = {"help", "vault", "mcp"}
# The add-on's verbs, which the base's table never has, and why: the add-on adds each row when it is built.
ADD_ON = {
    "control": "the owner's answer 3 of 2026-11-20 (the plan's log): control is the add-on's, out of the base's table; "
    "the add-on's card that builds it (L18) adds its row, with the fleet's lock",
}
# The one number of a mapping of exit statuses that has two meanings, with why.
SHARED = {
    ("STATUS_EXITS", 3): (
        {"failed", "cancelled"},
        "today's status exits 3 for a read that failed and for one a person cancelled (lib/claude_vm/status/engine.py's"
        " _end_for_an_interrupt, held by tests/unit/test_status_live.py, and status/hosts.py's run of several hosts),"
        " and a script may read it: the owner's answer 2 of 2026-11-20 keeps it",
    ),
}
# The verbs that need their one word, which a usage line shows without brackets, and those whose words are no word to
# give, since they refuse any (words.py's Words.needed and Words.refused).
NEEDED = {"code", "release"}
REFUSED = {"init", "mcp"}
# The locks a run of each verb takes, as the locks are ruled: one machine's by its name, the fleet's for what changes
# the whole fleet, and for three verbs a kind worked out from the words given.
LOCKS_OF = {
    "help": "none",
    "init": "fleet",
    "install": "fleet",
    "vault": "words",
    "deploy": "words",
    "code": "machines",
    "login": "machines",
    "status": "none",
    "settings": "none",
    "destroy": "machines",
    "release": "machines",
    "host": "words",
    "logs": "none",
    "cancel": "none",
    "mcp": "none",
}
# The verbs that read the fleet's settings to do their work, which take --set; and those of them that run the system
# layer, which take -v.
SET = {"deploy", "login", "code", "destroy", "release", "init", "status"}
VERBOSE = SET - {"status"}
# Options whose name speaks of a secret, a key or a token and whose value is none: a file's path, read by the verb, ends
# its name in _file; these are names and fingerprints.
NOT_SECRETS = {
    "token_user": "the API token's user, a name",
    "token_id": "the API token's ID, a name",
    "remove_key": "a public key's fingerprint",
}
# The options whose value is free text, in which a person may type a secret, each by its verb and the name the handler
# receives it by, and the rule that holds it: the run layer types the value and refuses a secret's name or value there,
# never saying it back (D5's --set, built; D10's settings --value, when it is built), and the printer of a command never
# prints one a person typed in a refused run (H9).
SECRET_TEXT = {
    **{
        (verb, "set"): "the run layer refuses a secret's name or value for one run, and never says it back (D5)"
        for verb in ("init", "deploy", "code", "login", "status", "destroy", "release")
    },
    ("settings", "value"): "settings --value is to refuse a secret's name or value, and never say it back, as today"
    " (D10)",
}
# The other options whose value is free text, and what it is: a thing named, never a secret.
NAMED_TEXT = {
    ("mcp", "browser"): "the program that opens the sign-in pages",
}
# Patterns that take a line of any text.
FREE_PATTERNS = {"text", "assignment"}
# What a value's name is, as its placeholder writes it in angle brackets: lower-case words joined by -, two of them
# joined by = for --set's <name>=<value> (the owner's ruling of 2026-11-22, review/code/OWNER-9.md 1).
METAVAR = re.compile(r"[a-z]+(-[a-z]+)*(=[a-z]+(-[a-z]+)*)?")
CARD = re.compile(r"[A-Z][0-9]+[a-z]?")
HANDLER = re.compile(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)*:[a-z_][a-z0-9_]*")
RULE = re.compile(r"([a-z]+|check\.[a-z]+)\.[0-9]+")
TOOL = re.compile(r"[a-z][a-z_]*")


def _spelled(option: Option) -> str:
    return f"--{option.long}" if option.long else f"-{option.short}"


def _received(verb: words.Verb) -> dict[str, Option | words.Words]:
    """Each option and the verb's words, by the name the handler receives it by."""
    received: dict[str, Option | words.Words] = {option.field: option for option in verb.options}
    if verb.words is not None:
        received[verb.words.field] = verb.words
    return received


@pytest.mark.parametrize("verb", list(VERBS.values()), ids=list(VERBS))
def test_no_two_options_of_a_verb_share_a_spelling_or_a_name_and_none_is_detach(verb: words.Verb) -> None:
    longs = [option.long for option in verb.options if option.long]
    shorts = [option.short for option in verb.options if option.short]
    assert len(longs) == len(set(longs)) and len(shorts) == len(set(shorts))
    assert "detach" not in longs
    assert len(_received(verb)) == len(verb.options) + (verb.words is not None)
    assert VERBS[verb.name] is verb


def test_the_words_before_a_verb_are_the_two_ruled_and_none_is_detach() -> None:
    # No word makes a run take no lock (the clean slate's decision 8): each verb's run takes the locks its row gives.
    assert [_spelled(option) for option in BEFORE] == ["--attended", "--detached-run"]
    assert [option.help is None for option in BEFORE] == [False, True]


@pytest.mark.parametrize("verb", list(VERBS.values()), ids=list(VERBS))
def test_every_option_names_a_pattern_or_takes_no_value(verb: words.Verb) -> None:
    for option in verb.options + BEFORE:
        assert option.kind in KINDS, option
        assert option.long or option.short, option
        if option.kind in ("flag", "counted"):
            assert option.pattern is None and option.metavar is None, option
        else:
            assert option.pattern in PATTERNS and option.metavar and METAVAR.fullmatch(option.metavar), option
            assert words.placeholder(option) == "=".join(f"<{part}>" for part in option.metavar.split("=")), option
    if verb.words is not None:
        assert verb.words.pattern is None or verb.words.pattern in PATTERNS


def test_the_table_has_no_word_for_a_migration_and_init_s_host_is_a_host_s_name() -> None:
    # The clean slate (review/clean-slate/PROPOSAL.md, section 2 and decision 10): no init --migrate or --finish, no
    # kind of option that existed for --migrate alone, and init --host a host of the fleet by its name, never an
    # address.
    init = {option.long: option for option in VERBS["init"].options}
    assert KINDS == ("value", "flag", "repeatable", "counted")
    assert "migrate" not in init and "finish" not in init and not hasattr(words, "MIGRATE_REFUSED")
    assert init["host"].pattern == "host_name" and VERBS["init"].rules == ("init.1",)


# The words the trims took out of the base, by the verb ("" for every verb and before any, which is where an add-on's
# word is absent: vault, which D11 and D13 build, is the likeliest home of an --export or a --rekey) and the option as a
# person would type it, each with the ruling that took it out: none is a word of the table. Only init's --take-over and
# --confirm are keyed to their verb, since destroy, release and deploy take a --confirm or a --take-over rightly.
TAKEN_OUT = {
    ("", "--export"): "the owner's word 3: export, import and rekey are the add-on's",
    ("", "--import"): "the owner's word 3: export, import and rekey are the add-on's",
    ("", "--rekey"): "the owner's word 3: export, import and rekey are the add-on's",
    ("", "--recipient"): "the owner's word 3: an export's recipient is the add-on's",
    ("init", "--take-over"): "the owner's word 3: the take-over of an import is the add-on's",
    ("init", "--confirm"): "the owner's word 3: an import's confirmation is the add-on's",
    ("", "--vault-key"): "the owner's word 2: the vault's key comes from a file or a command, chosen by a setting",
    ("", "--audit-token-id"): "T2: one token, kept to the pool, and no audit token",
    ("", "--audit-token-secret-file"): "T2: one token, kept to the pool, and no audit token",
    ("", "--run-lock"): "T8, and the clean slate's decision 8: no word names a run's lock or skips one",
}
# The verbs the owner's word 3 made the add-on's: no verb of the base's table has their names.
ADD_ON_VERBS = ("export", "import", "rekey")


@pytest.mark.parametrize(("verb", "word"), list(TAKEN_OUT), ids=[" ".join(key).strip() for key in TAKEN_OUT])
def test_a_word_the_trims_took_out_of_the_base_is_absent(verb: str, word: str) -> None:
    assert TAKEN_OUT[verb, word].strip()
    assert not verb or verb in VERBS
    rows = [VERBS[verb]] if verb else list(VERBS.values())
    spelled = {_spelled(option) for row in rows for option in row.options}
    if not verb:
        spelled |= {_spelled(option) for option in BEFORE}
    assert word not in spelled


def test_no_verb_of_the_table_is_one_the_add_on_took() -> None:
    assert [name for name in VERBS if name in ADD_ON_VERBS] == []


# Which pattern of schema/patterns.py each option's value and each verb's words are held to, by the verb ("" for the
# words before any verb) and the option as a person types it, or the words as help names them (<name>): written here by
# hand, so that a slip in the table's hand-written names fails, as deploy --disk held to memory's pattern would. For the
# agents' server and the add-on's broker, which run the one check on JSON, the pattern is the only gate on a value. An
# option that takes no value names none, nor do init's words, which init refuses whatever they are: neither is here.
PATTERN_OF: dict[str, dict[str, str]] = {
    "": {"--attended": "attended"},
    "help": {"<command>": "verb"},
    "init": {
        "--address": "address",
        "--host": "host_name",
        "--token-user": "token_user",
        "--token-id": "proxmox_id",
        "--token-secret-file": "secret_file",
        "--github-user": "github_user",
        "--storage": "proxmox_id",
        "--bridge": "proxmox_id",
        "--tailscale": "on_off",
        "--certificate": "fingerprint",
        "--pool": "proxmox_id",
        "--set": "assignment",
    },
    "install": {"--version": "release", "--without": "dependency_group"},
    "vault": {"<name>": "secret_name", "--host": "host_name", "--file": "secret_file"},
    "deploy": {
        "<name>": "vm_name",
        "--only": "stage",
        "--host": "host_name",
        "--tags": "roles",
        "--cores": "cores",
        "--memory": "memory",
        "--disk": "disk",
        "--repos": "repos",
        "--remove-key": "ssh_fingerprint",
        "--set": "assignment",
    },
    "code": {
        "<name>": "vm_name",
        "--project": "project",
        "--editor": "editor",
        "--host": "host_name",
        "--session": "session",
        "--set": "assignment",
    },
    "login": {"<name>": "vm_name", "--host": "host_name", "--only": "sign_in", "--set": "assignment"},
    "status": {"<name>": "vm_name", "--host": "host_name", "--only": "sections", "--set": "assignment"},
    "settings": {"<name>": "setting_name", "--host": "host_name", "--value": "text"},
    "destroy": {"<name>": "vm_name", "--confirm": "vm_names", "--host": "host_name", "--set": "assignment"},
    "release": {"<machine>": "vm_name", "--confirm": "vm_names", "--set": "assignment"},
    "host": {"<name>": "current_host", "--for": "vm_names", "--host": "host_name"},
    "logs": {"<job>": "job_id"},
    "cancel": {"<job>": "job_id"},
    "mcp": {
        "<extra>": "text",
        "--attended": "attended",
        "--browser": "text",
        "--project-dir": "path",
        "--vault-key-file": "path",
    },
}


# The example the form of each option and words of PATTERN_OF shows after its words in help's row and in the refusal of
# a value outside its pattern, or None where it shows none, by the same verb and the same spelling: written here by
# hand, so that an option a later card adds names its example, and an option sharing a pattern with values of another
# kind shows one of its own kind (H30: init --bridge's vmbr1, never a storage's local-lvm; mcp --vault-key-file's file,
# never a folder).
EXAMPLE_OF: dict[str, dict[str, str | None]] = {
    "": {"--attended": None},
    "help": {"<command>": "deploy"},
    "init": {
        "--address": "pve.example",
        "--host": "lab2",
        "--token-user": "ansible@pve",
        "--token-id": "automation",
        "--token-secret-file": "~/secrets/token",
        "--github-user": "octocat",
        "--storage": "local-lvm",
        "--bridge": "vmbr1",
        "--tailscale": None,
        "--certificate": None,
        "--pool": "dev-vms",
        "--set": "vm_cores=8",
    },
    "install": {"--version": "v3.0.0", "--without": None},
    "vault": {"<name>": "github_token", "--host": "lab2", "--file": "~/secrets/token"},
    "deploy": {
        "<name>": "alpha",
        "--only": None,
        "--host": "lab2",
        "--tags": "claude_code,dev_tools",
        "--cores": "8",
        "--memory": "16G",
        "--disk": "128G",
        "--repos": "my-app,my-lib",
        "--remove-key": None,
        "--set": "vm_cores=8",
    },
    "code": {
        "<name>": "alpha",
        "--project": "my-app",
        "--editor": None,
        "--host": "lab2",
        "--session": None,
        "--set": "vm_cores=8",
    },
    "login": {"<name>": "alpha", "--host": "lab2", "--only": None, "--set": "vm_cores=8"},
    "status": {"<name>": "alpha", "--host": "lab2", "--only": None, "--set": "vm_cores=8"},
    "settings": {"<name>": "vm_cores", "--host": "lab2", "--value": None},
    "destroy": {"<name>": "alpha", "--confirm": "alpha,beta", "--host": "lab2", "--set": "vm_cores=8"},
    "release": {"<machine>": "alpha", "--confirm": "alpha", "--set": "vm_cores=8"},
    "host": {"<name>": "lab2", "--for": "alpha,beta", "--host": "lab2"},
    "logs": {"<job>": "3f9a0c1e7b2d4a58"},
    "cancel": {"<job>": "3f9a0c1e7b2d4a58"},
    "mcp": {
        "<extra>": None,
        "--attended": None,
        "--browser": None,
        "--project-dir": "~/projects/app",
        "--vault-key-file": "~/keys/fleet.key",
    },
}
# What an adopted machine's name may be, in release's own words: one machine, never a VM's name nor several joined by
# commas, which its patterns' words say (review/code/H29.md 2).
MACHINE_WORDS = (
    "an adopted machine's name: letters, digits, dots, underscores and hyphens, starting with a letter or a digit"
)
# The words of an option's own, or a verb's words' own, in place of its pattern's, by verb and spelling, as EXAMPLE_OF:
# every other item says its pattern's.
WORDS_OF: dict[tuple[str, str], str] = {
    ("release", "<machine>"): MACHINE_WORDS,
    ("release", "--confirm"): MACHINE_WORDS,
}


def _items() -> dict[tuple[str, str], Option | words.Words]:
    """Every option and verb's words the table holds to a pattern, by verb ("" before any) and spelling, as PATTERN_OF
    names them."""
    items: dict[tuple[str, str], Option | words.Words] = {
        ("", _spelled(option)): option for option in BEFORE if option.pattern is not None
    }
    for name, verb in VERBS.items():
        if verb.words is not None and verb.words.pattern is not None:
            items[name, words.placeholder(verb.words)] = verb.words
        items |= {(name, _spelled(option)): option for option in verb.options if option.pattern is not None}
    return items


def test_every_option_and_word_held_to_a_pattern_shows_the_example_this_file_names() -> None:
    # Compared one item at a time, so that a difference names the verb and the option.
    assert {verb: set(named) for verb, named in EXAMPLE_OF.items()} == {
        verb: set(named) for verb, named in PATTERN_OF.items()
    }
    shown = {}
    for key, item in _items().items():
        pattern = PATTERNS[item.pattern or ""]
        shown[key] = (pattern.examples[0] if item.example is None else item.example) if pattern.shown else None
    assert shown == {(verb, word): example for verb, named in EXAMPLE_OF.items() for word, example in named.items()}


def test_an_item_s_own_example_is_held_by_its_pattern_shown_and_not_its_pattern_s() -> None:
    own = {key: item for key, item in _items().items() if item.example is not None}
    assert {key for key, item in own.items()} == {
        ("init", "--token-id"),
        ("init", "--bridge"),
        ("init", "--pool"),
        ("release", "--confirm"),
        ("mcp", "--vault-key-file"),
    }
    for key, item in own.items():
        pattern = PATTERNS[item.pattern or ""]
        assert full(item.pattern or "", item.example or ""), key
        assert pattern.shown and item.example != pattern.examples[0], key


def test_the_words_of_an_item_s_own_are_those_this_file_names_and_are_prose() -> None:
    own = {key: item.value_words for key, item in _items().items() if item.value_words is not None}
    assert own == WORDS_OF
    for key, said in own.items():
        # As a pattern's words are (test_aw_schema.py): none of a pattern's characters, no full stop, no example.
        assert said and not set("[]{}\\|?*^$") & set(said) and not said.endswith(".") and "e.g." not in said, key
        assert said != PATTERNS[_items()[key].pattern or ""].words, key
    # An option of the table that holds no value carries neither.
    for verb in VERBS.values():
        for option in (*verb.options, *BEFORE):
            if option.pattern is None:
                assert (option.value_words, option.example) == (None, None), option


def _patterns_named(options: tuple[Option, ...], held: words.Words | None = None) -> dict[str, str]:
    """The pattern each of options and the words held name, by the option's spelling and the words' placeholder."""
    named = {words.placeholder(held): held.pattern} if held is not None and held.pattern is not None else {}
    return named | {_spelled(option): option.pattern for option in options if option.pattern is not None}


def test_each_option_and_each_verb_s_words_are_held_to_the_pattern_this_file_names() -> None:
    # Compared one option at a time, so that a difference names the verb and the option.
    table = {
        "": _patterns_named(BEFORE),
        **{name: _patterns_named(verb.options, verb.words) for name, verb in VERBS.items()},
    }
    flat = {(verb, word): name for verb, named in table.items() for word, name in named.items()}
    assert flat == {(verb, word): name for verb, named in PATTERN_OF.items() for word, name in named.items()}
    assert {name for one in PATTERN_OF.values() for name in one.values()} <= set(PATTERNS)


@pytest.mark.parametrize("verb", list(VERBS.values()), ids=list(VERBS))
def test_every_option_is_received_by_its_long_name(verb: words.Verb) -> None:
    for option in verb.options + BEFORE:
        if option.long is None:
            assert (option.short, option.field) == ("v", "verbosity"), option
            continue
        # In lowercase, with _ for -.
        name = option.long.lower().replace("-", "_")
        # A word Python keeps for itself (host's --for) gets an _ after it, as a keyword argument must.
        assert option.field == (f"{name}_" if keyword.iskeyword(name) else name), option


@pytest.mark.parametrize("verb", list(VERBS.values()), ids=list(VERBS))
def test_every_option_and_word_has_a_fate_for_each_tool_that_runs_its_verb(verb: words.Verb) -> None:
    tools = verb.tools if isinstance(verb.tools, tuple) else ()
    for item in _received(verb).values():
        if len(tools) > 1:
            assert isinstance(item.agent, dict) and sorted(item.agent) == sorted(tools), item
            fates = list(item.agent.values())
        else:
            assert isinstance(item.agent, Fate), item
            fates = [item.agent]
        for one in fates:
            assert one.kind in FATES, item
            if one.kind == "property":
                assert one == PROPERTY, item
            elif one.kind == "fixed":
                assert one.value is not None or one.why, item
            else:
                assert one.value is None and one.why, item


@pytest.mark.parametrize("verb", [verb for verb in VERBS.values() if isinstance(verb.tools, str)], ids=lambda v: v.name)
def test_a_verb_no_tool_runs_refuses_its_every_word_with_why(verb: words.Verb) -> None:
    assert verb.tools.strip()
    assert [item for item in _received(verb).values() if item.agent != words.refused(verb.tools)] == []


def test_every_verb_has_a_handler_or_its_card_its_locks_and_its_tools() -> None:
    for name, verb in VERBS.items():
        assert verb.group == (None if name == "help" else "use"), name
        assert verb.handler is None or HANDLER.fullmatch(verb.handler), name
        assert CARD.fullmatch(verb.card), name
        assert verb.locks in LOCKS, name
        assert isinstance(verb.changes, bool), name
        if isinstance(verb.tools, tuple):
            assert verb.tools and all(TOOL.fullmatch(tool) for tool in verb.tools), name
        else:
            assert isinstance(verb.tools, str) and verb.tools.strip(), name
        assert all(RULE.fullmatch(code) for code in verb.rules) and len(set(verb.rules)) == len(verb.rules), name
    tools = [tool for verb in VERBS.values() if isinstance(verb.tools, tuple) for tool in verb.tools]
    assert len(tools) == len(set(tools)), "a tool runs one verb"


def test_the_verbs_whose_run_is_a_job_are_the_seven_ruled() -> None:
    assert {name for name, verb in VERBS.items() if verb.changes} == JOBS


def test_the_verbs_that_need_their_word_and_those_that_refuse_any_are_those_named() -> None:
    # Side by side in the table (Words.needed and Words.refused), never decided by a verb's or the words' name.
    taking = {name: verb.words for name, verb in VERBS.items() if verb.words is not None}
    assert {name for name, held in taking.items() if held.needed} == NEEDED
    assert {name for name, held in taking.items() if held.refused} == REFUSED
    # A verb needs one word, never several, and one that refuses any word takes none.
    assert all(not taking[name].plural for name in NEEDED | REFUSED) and NEEDED & REFUSED == set()


def test_no_verb_of_the_add_on_is_a_verb_of_the_base_s_table() -> None:
    assert all(why.strip() for why in ADD_ON.values())
    assert sorted(set(ADD_ON) & set(VERBS)) == []
    # Nor any verb's card the add-on's own, a row that would wait in the base for the add-on: L is its workstream.
    assert [name for name, verb in VERBS.items() if verb.card.startswith("L")] == []


def test_each_verb_takes_the_locks_ruled() -> None:
    assert {name: verb.locks for name, verb in VERBS.items()} == LOCKS_OF


def test_set_is_on_the_verbs_that_read_the_settings_and_v_on_those_that_run_the_system_layer() -> None:
    assert {name for name, verb in VERBS.items() if any(option.long == "set" for option in verb.options)} == SET
    assert {name for name, verb in VERBS.items() if any(option.short == "v" for option in verb.options)} == VERBOSE


def test_the_tools_that_run_each_verb_are_those_ruled() -> None:
    assert {name: verb.tools for name, verb in VERBS.items() if isinstance(verb.tools, tuple)} == TOOLS_OF
    assert {name for name, verb in VERBS.items() if isinstance(verb.tools, str)} == NO_TOOL


# Why no tool runs each verb, as said: each command in it the printer's, the program as it was started.
NO_TOOL_SAID = {
    "help": "the tools' own descriptions say what {program} help says",
    "vault": "the vault is edited at a terminal, by the person: no tool takes or returns a secret",
    "mcp": "it registers this server: the person's command",
}


@pytest.mark.parametrize("started", [None, "./aw", "/opt/my checkout/aw"])
def test_the_reason_no_tool_runs_a_verb_is_said_with_the_printer_s_commands(started, monkeypatch) -> None:
    # The hold H26's cut took (review/code/H26.md, its earlier findings): the reasons I1's schemas say, each command in
    # them through said, so the program as it was started, quoted, and never a field left unfilled.
    monkeypatch.setattr(printer, "_program", printer.PROGRAM)
    printer.started_as(started)
    program = printer.said("{line}", {"line": words.Line("help")}).removesuffix(" help")
    said = {name: printer.said(verb.tools) for name, verb in VERBS.items() if isinstance(verb.tools, str)}
    assert said == {name: why.replace("{program}", program) for name, why in NO_TOOL_SAID.items()}
    assert program == (printer.quoted(started) if started else "aw")


# Each fate for an agent's tool that is no property of the tool's schema, by the verb, the name the handler receives the
# option or the words by, and the tool: a value the server always gives, or refused with why. A word of several verbs
# whose fate is ruled once (--set, -v, --confirm and the rest the test below names) is not repeated here.
OTHER_FATES = {
    ("init", "token_secret_file", "init"): words.refused(words.TOKEN_SECRET_FILE_LEFT_OUT),
    ("init", "no_prompt", "init"): words.fixed(True),
    ("init", "extra", "init"): words.refused(words.INIT_EXTRA_LEFT_OUT),
    ("install", "lock", "install"): words.refused(words.LOCK_LEFT_OUT),
    ("status", "json", "status"): words.JSON,
    ("settings", "all", "settings_set"): words.refused(words.ONE_SETTING),
    ("settings", "changed", "settings_set"): words.refused(words.ONE_SETTING),
    ("settings", "json", "settings"): words.JSON,
    ("settings", "json", "settings_set"): words.JSON,
    ("settings", "reset", "settings"): words.refused(words.VALUE_LEFT_OUT),
    ("settings", "value", "settings"): words.refused(words.VALUE_LEFT_OUT),
    ("host", "json", "hosts"): words.JSON,
    ("host", "remove", "hosts"): words.refused(words.HOST_REMOVE_LEFT_OUT),
    ("host", "for_", "hosts"): words.refused(words.HOST_ASKED),
    ("host", "host", "hosts"): words.refused(words.HOST_ASKED),
    ("host", "name", "hosts"): words.refused(words.HOST_NAME_LEFT_OUT),
    # A call given it would wait until the job ends (I1): the job tool follows a job, and returns at its waits.
    ("logs", "follow", "logs"): words.refused(words.FOLLOW_REFUSED),
}


def test_every_fate_is_a_property_but_those_ruled() -> None:
    refused = {
        "set": words.SET_REFUSED,
        "adopt": words.ADOPT_REFUSED,
        "remove-key": words.REMOVE_KEY_REFUSED,
        "take-over": words.TAKE_OVER_REFUSED,
        "print-commands": words.PRINT_COMMANDS_REFUSED,
        "version": words.INSTALLED_REFUSED,
        "newer": words.INSTALLED_REFUSED,
        "rollback": words.INSTALLED_REFUSED,
        "without": words.INSTALLED_REFUSED,
    }
    for name, verb in VERBS.items():
        if not isinstance(verb.tools, tuple):
            continue
        for option in verb.options:
            for tool in verb.tools:
                given = fate(option.agent, tool)
                if option.long in refused:
                    assert given == words.refused(refused[option.long]), (name, option.long)
                if option.short == "v":
                    assert given == words.refused(words.VERBOSE_REFUSED), name
                if option.long == "confirm":
                    assert given == words.fixed(None, words.CONFIRMED), name
    assert [fate(option.agent, "any") for option in BEFORE] == [
        words.ATTENDED,
        words.refused(words.DETACHED_RUN_REFUSED),
    ]
    assert DETACH_AGENT == words.fixed(True, DETACH_AGENT.why) and DETACH_AGENT.why
    assert VERBS["release"].words.agent == PROPERTY and VERBS["cancel"].words.agent == PROPERTY
    # Every other option and word of a verb a tool runs is a property of each tool's schema, but where OTHER_FATES names
    # it, so none is refused or given by the server by a slip. The agents' server (I1) reads this column as the whole
    # truth.
    seen = set()
    for name, verb in VERBS.items():
        if not isinstance(verb.tools, tuple):
            continue
        for field, item in _received(verb).items():
            if isinstance(item, Option) and (item.long in refused or item.long == "confirm" or item.short == "v"):
                continue
            for tool in verb.tools:
                seen.add((name, field, tool))
                assert fate(item.agent, tool) == OTHER_FATES.get((name, field, tool), PROPERTY), (name, field, tool)
    assert sorted(set(OTHER_FATES) - seen) == [], "OTHER_FATES names a word no tool runs"


def test_fate_reads_one_tool_s_fate_where_two_tools_run_a_verb() -> None:
    [value] = [option for option in VERBS["settings"].options if option.long == "value"]
    assert fate(value.agent, "settings") == words.refused(words.VALUE_LEFT_OUT)
    assert fate(value.agent, "settings_set") == PROPERTY
    assert fate(PROPERTY, "deploy") == PROPERTY


def test_no_option_takes_a_secret_as_its_value_but_the_two_of_free_text() -> None:
    # A secret reaches a verb from a file it names or from the terminal, hidden, never on the command line: so nothing
    # the printer of a command writes from the table need be masked, but the two values of free text a person may type
    # one into, --set and settings --value, which the run layer refuses and never says back.
    speaks = re.compile(r"secret|password|passphrase|key|token")
    fields = {option.field for verb in VERBS.values() for option in verb.options + BEFORE if option.kind != "flag"}
    for field in fields:
        if speaks.search(field):
            assert field.endswith("_file") or field in NOT_SECRETS, field
    assert set(NOT_SECRETS) <= fields
    free = {
        (name, option.field)
        for name, verb in VERBS.items()
        for option in verb.options
        if option.pattern in FREE_PATTERNS
    }
    assert free == set(SECRET_TEXT) | set(NAMED_TEXT), "a value of free text: say whether a secret may be typed there"
    assert set(SECRET_TEXT) & set(NAMED_TEXT) == set() and all((*SECRET_TEXT.values(), *NAMED_TEXT.values()))
    assert not any(option.pattern in FREE_PATTERNS for option in BEFORE)


def test_set_s_sentence_names_the_placeholder_its_row_prints() -> None:
    # --set <name>=<value>, as words.placeholder writes it, never the bare name=value (review/code/H29.md, H27 (5)).
    assert words.SET.help is not None
    assert f"--set {words.placeholder(words.SET)}, once for each" in words.SET.help
    assert "name=value" not in words.SET.help


def test_every_sentence_is_one_line_and_says_aw_never_just() -> None:
    sentences = [DETACH, *(option.help for option in BEFORE if option.help)]
    for verb in VERBS.values():
        sentences.append(verb.doc)
        sentences += [option.help for option in verb.options]
        if verb.words is not None:
            sentences.append(verb.words.help)
    for sentence in sentences:
        assert sentence and not re.search(r"[\x00-\x1f\x7f]", sentence), sentence
        assert not re.search(r"\bjust\b", sentence), sentence
        assert not sentence.endswith("."), sentence
    # The reasons an agent's tool is given for a word it does not take, or for a verb no tool runs, which I1 shows.
    reasons = [verb.tools for verb in VERBS.values() if isinstance(verb.tools, str)]
    for verb in VERBS.values():
        for item in [*verb.options, *([verb.words] if verb.words else [])]:
            agent = item.agent if isinstance(item.agent, dict) else {"": item.agent}
            reasons += [one.why for one in agent.values() if one.why]
    reasons += [one.why for option in BEFORE for one in [option.agent] if isinstance(one, Fate) and one.why]
    assert reasons and [reason for reason in reasons if re.search(r"\bjust\b", reason)] == []


def test_each_exit_status_has_one_meaning_and_each_meaning_one_status_but_the_one_pair_named() -> None:
    # A meaning is a key, so it has one number; a number has one meaning but where SHARED names the pair, with why.
    assert all(isinstance(why, str) and why.strip() for _, why in SHARED.values())
    shared = {}
    for name, statuses in (("EXITS", EXITS), ("STATUS_EXITS", STATUS_EXITS)):
        assert all(isinstance(status, int) and 0 <= status <= 255 for status in statuses.values())
        for status in set(statuses.values()):
            meanings = {meaning for meaning, given in statuses.items() if given == status}
            if len(meanings) > 1:
                shared[name, status] = meanings
    assert shared == {pair: meanings for pair, (meanings, _) in SHARED.items()}
    assert (EXITS["done"], EXITS["refused"], EXITS["failed"], EXITS["cancelled"]) == (0, 1, 2, 130)
    assert STATUS_EXITS == {"nothing_wrong": 0, "problem": 1, "unread": 2, "failed": 3, "cancelled": 3}


def _function(path: Path, name: str) -> ast.FunctionDef:
    """The function of a module's top level named name, read from its syntax tree."""
    tree = ast.parse(path.read_text())
    [found] = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    return found


def test_a_cancelled_status_exits_as_today_s_does() -> None:
    # Today's status ends a read a person cancelled in _end_for_an_interrupt, by os._exit
    # (tests/unit/test_status_live.py holds it to 3 at a terminal): the table's cancelled status is that number.
    end = _function(STATUS_ENGINE, "_end_for_an_interrupt")
    calls = [node for node in ast.walk(end) if isinstance(node, ast.Call) and ast.unparse(node.func) == "os._exit"]
    assert [ast.literal_eval(call.args[0]) for call in calls] == [STATUS_EXITS["cancelled"]]


def test_a_cancelled_status_of_several_hosts_exits_as_today_s_does() -> None:
    # Today's status/hosts.py, which reads several hosts at once, returns from its run what a Ctrl-C there ends it
    # with, and the table's comment cites it (review/code/H22.md 2): the table's cancelled status is that number too.
    run = _function(STATUS_HOSTS, "run")
    handlers = [
        handler
        for node in ast.walk(run)
        if isinstance(node, ast.Try)
        for handler in node.handlers
        if handler.type is not None and ast.unparse(handler.type) == "KeyboardInterrupt"
    ]
    returned = [node.value for handler in handlers for node in ast.walk(handler) if isinstance(node, ast.Return)]
    assert len(handlers) == 1 and [ast.literal_eval(value) for value in returned] == [STATUS_EXITS["cancelled"]]


def test_the_table_imports_nothing_outside_the_standard_library(home) -> None:
    # What importing the package alone loads is taken away: what is left is what the module itself brings.
    added = imports_of(module=MODULE, home=home) - imports_of(module="claude_vm", home=home)
    assert MODULE in added, sorted(added)
    tops = {name.split(".")[0] for name in added}
    assert sorted(tops - set(sys.stdlib_module_names) - {"claude_vm"}) == []
    assert (
        sorted(name for name in added if name.startswith("claude_vm.") and not name.startswith("claude_vm.schema"))
        == []
    )
