"""Which words of the new command line's table (lib/claude_vm/schema/words.py) are new against today's, and which of
today's it changes, each with the ruling or the source that decided it: the cutover's check, beside the contract of
today's command line (tests/unit/cli_contract.json), that nothing typed today was lost unsaid and that no word nobody
ruled on came in. tests/unit/test_aw_cli_contract_words.py holds both maps to the table and the contract, and the
parity suite reads them. Removed with the contract at the cutover: the table carries no column for the migration.

A word is named as a person sees it:

- a verb: `deploy`;
- an option: `deploy --tags`, or `deploy -v` for one spelled short alone; one given before any verb: `--no-lock`;
- `--detach`, which the parser gives every verb whose run is a job;
- a verb's words, by what its usage calls one: `logs <job>`;
- one part of a verb, an option or a verb's words: `help: doc`, `deploy --tags: help`, `settings <name>: help` (a
  verb's group and doc; an option's short, kind, pattern, metavar and help; the words' name, plural, pattern and help);
- a setting given before the recipe today: `RUN_LOCK=`;
- a rule between options that the contract lacks: `rule <code>`;
- an exit status that the table's `EXITS` gives another number than the contract measured today: `exit <its name in
  EXITS>`.

Beside them, BEHAVIOURS: what today's words do that aw does otherwise on purpose, not a word of the contract changed but
what the same words do, each with the ruling that decided it; the parity suite (H7) lists each in its ROWS_CHANGED and
measures it. One the check of the words finds is named as the check finds it: where today asks a question aw does not
ask, so that no number of today's stands for aw's, `exit <its name in EXITS> at a prompt`. Any other is named by the
word it concerns, as above, and what is given with it (`init <extra>: an empty word`), or by what is given alone where
it concerns the words of several verbs; tests/unit/test_aw_cli_rules.py holds each such one to a test of its own.

The sources name the rulings of the plan the new engine is built by: the owner's words and the trims and their
verification (review/trim-rulings.md: T8, V2-5, V2-8), the rulings on words (N-23 section 4, N-22 section 4), the
owner's answers (O16, and those of 2026-11-20 in the plan's log) and the design report's table of verbs.
"""

from __future__ import annotations

# The owner's word 1: aw alone, with just kept for the repository's own tasks.
WORD_1 = "the owner's word 1, aw alone: a sentence that said just says aw (review/trim-rulings.md)"
# V2-8: a setting for one run is --set on every verb that reads the fleet's settings, status among them.
SET = "V2-8: --set name=value on every verb that reads the fleet's settings to do its work, status among them"
VERBOSE = "N-23 section 4: -v to -vvvvv after the verb, the system layer's verbosity, on the verbs that run it"

# Each word of the contract that the table does not have as it is, and why.
CHANGED: dict[str, str] = {
    "help: doc": WORD_1,
    "init --host: help": WORD_1,
    "vault --host: help": WORD_1,
    "deploy --host: help": WORD_1,
    "deploy --tags: help": WORD_1,
    "code --host: help": WORD_1,
    "login --host: help": WORD_1,
    "status --host: help": WORD_1,
    "settings --value: help": WORD_1,
    "settings <name>: help": "the word edit is new, which opens the settings in an editor (the report's table of "
    "verbs, row settings: every form, and edit is new)",
    "destroy --host: help": WORD_1,
    "mcp --vault-password-file": "V2-8: the agents' server's registration takes the vault key file's path as "
    "--vault-key-file, in place of the password file",
    "ANSIBLE_ARGS=": "T8, which dropped its translation: aw reads no ANSIBLE_ARGS, and -v and --set after the verb are "
    "the words",
    "RUN_LOCK=": "T8 and O16 item 15 as trimmed: a run with no lock is --no-lock before the verb, with no path",
    "ATTENDED=": "T8 and O16 item 15: --attended=1 before the verb, the agents' server's own word",
    "exit cancelled": "the report's run ladder (C4): a run a person cancelled exits 130, where one Ctrl-C while a "
    "task of today's playbook runs, outside a prompt, exits ansible-playbook's 99, the recipe ending in it (the "
    "contract's case exit.ctrl-c-1); before the playbook starts today's is 130 too (exit.ctrl-c-2); at one of its "
    "prompts, BEHAVIOURS",
}

# The owner's answer 5 (2026-11-20; the plan's log), to review/code/SUMMARY-2.md: the three differences
# review/code/H5.md item (2) found, and init.4's refusal dropped, are aw's behaviour, each held by a test now and
# measured by H7.
ANSWER_5 = "the owner's answer 5 (2026-11-20; the plan's log)"

# Each behaviour of today's words that aw has otherwise on purpose, and the ruling that decided it.
BEHAVIOURS: dict[str, str] = {
    "exit cancelled at a prompt": "the owner's answer 4 (2026-11-20; the plan's log): one Ctrl-C at a prompt ends the "
    "run, as it does anywhere else in aw, with a cancelled run's 130 (the report's run ladder, C4), where today "
    "Ansible asks Continue or Abort there, C going on with the run (the contract's case exit.ctrl-c-3) and A ending "
    "it as a failed task, 2 (exit.ctrl-c-4); an empty answer stays the way to skip a sign-in, as today",
    "init <extra>: an empty word": f"{ANSWER_5}: an empty word after aw init's options is refused with init.1's "
    "sentence, as any word there is, where today's just accepts one only by an accident of the join: init's words, "
    "joined by spaces, come to nothing (two empty words are refused today)",
    "deploy <name>: a stage, beside a rule broken": f"{ANSWER_5}: a stage of deploy given where the names go, on a "
    "line that also breaks a rule between options, is refused at once with the rule's sentence, at a terminal or not; "
    "today asks which was meant first at a terminal and refuses after any answer, and with no terminal says deploy.1's "
    "sentence: refused either way, with another sentence",
    "a VM name with _, beside a rule broken": f"{ANSWER_5}: where the line breaks a rule between options, aw says only "
    "the rule's sentence, since the one check refuses before the name is taken with - and said; today's just says the "
    "underscore's warning first, then the rule's (deploy, code and destroy alike): refused either way",
    "init --token-secret-file: a relative file from a folder whose path has a space": f"{ANSWER_5}, on the "
    "coordinator's ruling: init.4 is dropped, so aw takes a token file named relative to a folder whose path has a "
    "space, or another character the file's pattern does not take, where today's init refuses it once made absolute "
    "(the contract's case init.4-1); the refusal held the JSON the justfile joins in bash, which aw has not, and its "
    "value is held to its pattern as typed",
    "deploy <name>: a stage, the question's two command lines": "the coordinator's ruling on H9 (2026-11-20, the "
    "plan's card): the question a stage given where the names go asks names each answer's command line as the printer "
    "of a command writes it (lib/claude_vm/cli/inverse.py), every option given as --long=value in the table's order, "
    "the names as words after them and a value of --set as its NAME=VALUE, since nothing has taken it yet; today's "
    "writes --only configure, six of deploy's options with their values after a space, and the names joined by commas",
}

# The settings given before the recipe today that aw still takes from the environment, as today, and their source: the
# rest are in CHANGED.
KEPT: dict[str, str] = {
    "PYTHON": "the report's table of the words given before a verb: PYTHON= and UV= are kept in a checkout",
    "UV": "the report's table of the words given before a verb: PYTHON= and UV= are kept in a checkout",
    "LOG_DIR": "the report's table of the words given before a verb: LOG_DIR, LOG_RETENTION_DAYS are kept",
    "LOG_RETENTION_DAYS": "the report's table of the words given before a verb: LOG_DIR, LOG_RETENTION_DAYS are kept",
}

# Each word the table has and the contract lacks, and its source.
NEW: dict[str, str] = {
    "--attended": "T8 and O16 item 15: kept as the agents' server's own word, as ATTENDED=1 was",
    "--detached-run": "T5: a detached run is the same program in a session of its own, started with this hidden word",
    "--detach": "V2-5: every verb whose run is a job can be started detached, as the agents' server starts it",
    "init --check": "the report's table of verbs, row init: its modes --check and --print-commands",
    "init --print-commands": "N-22 section 4: init --migrate, --finish, --print-commands, sound as words",
    "init --pool": "O16 item 4: --pool names another pool",
    "init --machines": "O16 item 5: --machines answers init's first question under --no-prompt",
    "install --version": "the report's table of verbs, row install: the installed program's --version and --rollback",
    "install --newer": "N-23 section 4: whether a newer release exists is install --newer, not --check",
    "install --rollback": "the report's table of verbs, row install: the installed program's --version and --rollback",
    "install --without": "the report's table of verbs, row install: --without mcp",
    "vault <name>": "N-22 section 4: vault --list, vault <name>, --file, --remove, sound",
    "vault --list": "N-22 section 4: vault --list, vault <name>, --file, --remove, sound",
    "vault --file": "N-22 section 4: vault --list, vault <name>, --file, --remove, sound",
    "vault --remove": "N-22 section 4: vault --list, vault <name>, --file, --remove, sound",
    "vault --rotate": "N-23 section 4: a new key for the vault is vault --rotate",
    "deploy --adopt": "N-23 section 4: release <machine> and deploy --adopt, from the owner's machines plan",
    "deploy --remove-key": "the report's part on SSH keys, the person's own: one key taken off by its fingerprint",
    "deploy --take-over": "N-23 section 4: --take-over on the one command under machine_claims: refuse",
    "release": "N-23 section 4: release <machine>, from the owner's machines plan of 2026-11-11",
    "release <machine>": "N-23 section 4: release <machine>, from the owner's machines plan of 2026-11-11",
    "release --confirm": "the report's table of verbs, row release: a typed confirmation, as destroy's --confirm",
    "logs <job>": "N-22 section 4: logs <job>, --follow, --save, sound",
    "logs --follow": "N-22 section 4: logs <job>, --follow, --save, sound",
    "logs --save": "N-22 section 4: logs <job>, --follow, --save, sound",
    "cancel": "N-23 section 4: cancel [<job>], a verb of its own as O10 records",
    "cancel <job>": "N-23 section 4: cancel [<job>], a verb of its own as O10 records",
    "mcp --vault-key-file": "V2-8: the agents' server's registration takes the vault key file's path",
}
NEW |= {f"{verb} --set": SET for verb in ("init", "deploy", "code", "login", "status", "destroy", "release")}
NEW |= {f"{verb} -v": VERBOSE for verb in ("init", "deploy", "code", "login", "destroy", "release")}
