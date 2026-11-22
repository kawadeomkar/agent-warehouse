# 16. The engine that replaces the playbooks at 3.0

Today the engine is the `justfile`'s recipes over bash over `ansible-playbook`. The one that replaces it at 3.0 is
being built beside it, in this repository: one program, `aw`, whose engine is plain Python in the command's own
process on this machine, with Ansible kept only for the system layer inside a machine. Until the cutover nothing a
person sees changes. Every command is still `just`'s, the playbooks, roles and recipes stay and keep passing their
tests, and the new code is reached through `tools/aw` alone. Verbs do not move to it one at a time: the two engines
record what a VM is and what the settings are in different ways, and a VM one of them made must never be changed by
the other. So the cutover moves every verb at once.

This page describes the new engine's code as it is in the repository, and grows with it. Each subpackage has a heading
below, which says what goes there until the change that builds a part writes how it works under that heading. The
package's own rules, from [§7.10](custom-code.md#710-libclaude_vm--the-controllers-python-as-one-package), hold here as
everywhere in `lib/claude_vm/`: importing a module loads none of `ansible`, `yaml`, `asyncio` or `subprocess`, and a
package's `__init__.py` imports nothing.

## 16.1 The rule between the two engines

The rule is one-way, and `tests/unit/test_aw_layout.py` holds both halves of it.

- **Nothing of today's product reaches the new engine.** No file of the `justfile`, `deploy.yml`, `ansible.cfg`, the
  `Vagrantfile`, `playbooks/`, `roles/`, `filter_plugins/`, `callback_plugins/`, `inventory/` or `tools/` (but
  `tools/aw` itself), and no module of `lib/claude_vm/` outside the new subpackages, names `tools/aw` or imports a
  module of the new engine. A Python file is read as a syntax tree: its imports, the relative ones resolved, the
  strings it holds, and the module it hands to `plugin.filters`. Any other file is read as text, so `ansible.cfg`,
  which every run of today's reads, never names the new engine's Ansible folder: only the configuration the engine
  writes for its own Ansible child does. The test's allow list is empty, and only the cutover fills it.
- **The new engine reuses only what it names.** Of today's `lib/claude_vm/`, the new subpackages and `tools/aw` import
  only the modules and packages in the test's `REUSED`, each with the reason it is reused, at the top of a file or
  inside a function. `records` and `refs` are not among them, since the table of verbs and the printer of a command
  take their place; nor is `ansible`, today's engine of playbooks, nor the rest of `run`. A change that needs another
  module adds it to `REUSED`, with its reason, in the same commit. Never a module that imports Ansible when it is
  imported.
- **What it reuses brings in only what is named too.** The test follows the imports of every module `REUSED` names,
  and of what those import in turn, all the way down, at the top of a file or inside a function. Each module it
  reaches is in `REUSED`, or in a second list, `REACHED`, with the reason it is brought in. Today `REACHED` holds four
  modules the new engine's own code never imports: `records` and `refs`, on which `catalogue`, `editors`, `hosts`,
  `status` and `vpn` build their records, and `run.model` and `run.recipes`, which `tui.run` draws. None of them is
  `ansible`, by any road.

`tools/aw` is the program, on the model of `tools/mcp`. Run as `python -I`, it puts this checkout's `lib/` first on
`sys.path`, checks with `plugin.own` that the `claude_vm` imported is this checkout's, and runs
`lib/claude_vm/cli/main.py`'s `main` with its own path first and then the words typed, which 16.3 reads. A person
starts it with the launcher at the checkout's top, `aw` (16.3). `--version` alone answers with one line, `aw from the
checkout <path>`. The path is worked out from `tools/aw`'s own path and from nothing else, and a checkout has no
version number of its own.

Beside the subpackages, `lib/claude_vm/secret.py` holds what the engine wraps a secret in as soon as it reads one: a
`Secret`, which `repr`, `str`, an f-string, `format` and a log line show as a mark, which refuses to be pickled or
turned into JSON, and whose plain value only `reveal()` reads, at the one place that sends it on.
`tests/unit/test_aw_guards.py` holds the modules that call `reveal()` to its `REVEALS`, each with the reason it sends
the secret on; the list is empty until the first one does. The engine's tests, `tests/unit/test_aw_*.py`, run alone
with `just test engine`, are kept off this machine in their own process as a run of `tools/aw` is, and reach `tools/aw`
only through their kit ([§11.1](testing.md#111-unit--just-test-unit)).

## 16.2 `schema/` — the command line's patterns and its table of verbs and options

`lib/claude_vm/schema/` is where the command line goes as data. That is the patterns a typed value is held to, and the
one table of verbs and options that the parser, help, the reference page, the printer of a command and the agents'
schemas all read. They are literals written by hand, and no code is generated from them into another file. It imports
the standard library and its own modules and nothing else, because every command reads it, a refusal and help among
them. Any part of the new engine may import it.

**The patterns: `patterns.py`.** `PATTERNS` is the one home of every pattern the command line holds a value to: one
literal mapping from a name (`vm_name`, `host_name`, `cores`, `repos`, `session`, `job_id` and the rest) to the
pattern's inner text, with no anchor. The table of verbs and options names a pattern for each option and for a verb's
words, and nothing else in the new engine writes one. `full(name, value)` is the one way to hold a value to a pattern:
it holds the value whole, with `re.fullmatch` of the named pattern compiled once on its first use, so a value with a
newline after it is refused, and a name `PATTERNS` does not have raises `KeyError`. An option's pattern takes the empty
value where today's does, since an empty value is no value given. A rule that is more than one pattern is the check's,
never this module's: two values together, a word that starts with `-`, the cap of 2000 characters on `--repos`, the
`.` and `..` that `--project`'s pattern takes and a rule refuses. There are no records, no registry of operations and
no coded refusals.

Until the cutover the `justfile` holds a person's values to its own copy of each pattern, so every pattern of a
recipe a person runs is in `PATTERNS` under a name with the same text; the developer's recipes (`lint`, `test`, `docs`,
`vagrant`, `clean`) keep theirs, which are not the command line's. `tests/unit/test_aw_schema.py` holds the two equal,
holds each pattern to the values today's tests of the `justfile` give the same pattern, each value it takes refused
with a newline after it, and holds that importing the module loads nothing outside the standard library. A pattern
that only the new command line uses (`job_id`, `assignment` for `--set`, `secret_name`, `ssh_fingerprint`, `release`,
`dependency_group`) is listed there with where it comes from.

**The table: `words.py`.** `VERBS` is the one table of the command line: every verb a person types after `aw`, in the
order help lists them, today's thirteen and `release` and `cancel`: the base's alone, since `control` is the add-on's,
which adds its row when it is built. Each row is a `Verb`: its group and its sentence; its `Words`, what the usage calls
one, whether it takes several, the name the handler receives them by, their pattern and help; its `Option`s; the codes
of the rules between its options, as the contract of today's command line names them (`deploy.4`, `check.names.1`),
which the one check runs in that order; its handler, as `module:function`, or none while no change has built the verb,
and then the card that builds it; the locks a run of it takes, one of `machines`, `fleet`, `none` or `words`, where the
kind depends on the words given (16.8, `lock_sets.py`); whether a run of it is a job, with its own record, text log and locks; and the tools of
the agents' server that run it, or why none does. An option has its spellings, its kind (`value`, `flag`, `repeatable`
for `--set`, `counted` for `-v`, `optional-value` for `--migrate`), the name of its pattern, the name the handler
receives it by (the long option in lowercase with `_` for `-`, as the agents' tools name it today, and `for_` for
`host --for`), its help, the name help gives its value (`--host ON`, as `just --usage` prints it today), and its fate
for an agent's tool: a property of the tool's schema, a value the server always gives, or refused with the reason; where
two tools run one verb (`settings` and `settings_set`), a fate for each. `BEFORE` holds the words given before any verb:
`--attended`, and `--detached-run`, which no person types. `--detach` is no row's option: the parser gives
it, with the one sentence `DETACH`, to every verb whose run is a job, and the agents' server starts every such run
detached.

Today's verbs keep today's words and help, with `aw` where a sentence said `just`, and `mcp --vault-password-file` is
`--vault-key-file`. Beside the table, `EXITS` names the exit status of every verb but `status` (0 done, 1 refused before
anything ran, 130 cancelled) and `STATUS_EXITS` those of `status` (today's 0, 1, 2 and 3, and 3 for a read a person
cancelled, as today's `status` gives it); within each, no meaning has two numbers and no number two meanings, but
`status`'s 3, which today means a read that failed and one a person cancelled, and a script may read. The table holds no
option whose value is a secret: a secret reaches a verb from a file it names or from the terminal. Two options take free
text a person may type one into, `--set` and `settings --value`, and the run layer that reads them refuses a secret's
name or value there and never says it back. Nothing is generated from the table or into it, and it has no column for
the migration: which words are new and which of today's it changes, each with its ruling, is
`tests/unit/cli_contract_changes.py`'s, beside the contract (`tests/unit/cli_contract.json`), and goes with it at the
cutover, and so is `BEHAVIOURS`, what today's words do that `aw` does otherwise on purpose.
`tests/unit/test_aw_cli_words.py` holds the table's rows, today's fates for today's words (against
`lib/claude_vm/mcp/recipes.py`, until the cutover), every other option and word of a verb a tool runs a property of the
tool's schema but where ruled otherwise, the two values of free text, the exit statuses and the module's imports;
`tests/unit/test_aw_cli_contract_words.py` holds every word of the contract to the table or to the maps `CHANGED` and
`KEPT`, every word the table adds to `NEW`, and each exit status `aw` gives otherwise to `CHANGED` or `BEHAVIOURS`.

**The one check: `check.py`.** `check(verb, values)` is the check every entry runs on a verb's values: the parser of
the command line, the agents' server through `aw`, and the add-on's broker on JSON. It holds each value to its kind and
to the pattern the table names, with `full`; then runs the rules between options the verb's row names, in that order;
then holds the verb's words to their number. It returns nothing or the one sentence of the first refusal: an option the
verb has not got, a value of the wrong type, or one outside its pattern, each naming the option and its help and never
the value; then a rule's sentence; then a second word where the verb takes one, or any word where it takes none. It
takes values as the parser gives them and as JSON with exact types, and coerces nothing: a value option's is a string, a
flag's a boolean, `--set`'s a list of strings, `-v`'s a whole number, `--migrate`'s true or a string, the words a list
where the verb takes several and a string where it takes one, or a list where the parser was given more than one, which
the rules see before the number is held; `detach` is a flag of every verb whose run is a job. `check_before` holds the
words given before any verb the same way, and `formed` is the check but for the rules between options, which the
printer of a command (16.3) holds a line to.

**The rules between options: `rules.py`.** Each rule between options of today's command line is one plain function
with the code the contract of today's command line gives it (`deploy.4`, `check.names.1`) and today's sentence, word
for word, with `aw` where it said `just`: `Rule(code, verbs, sentence, breaks)`, where `breaks` takes a verb's values
and returns nothing where they keep the rule, or what fills the sentence's values where they break it, and
`Rule.check` gives nothing or the sentence. They are today's rules of `deploy`, `code`, `settings`, `destroy`, `host`,
`help`, `init` and `mcp`, said before any check or tool, and the three argument checks of `tools/check` that are more
than one pattern: `host --for`'s VM names, split at its commas, the 2000 characters of `--repos`, and the `.` and `..`
of `--project`; `RULES` holds them by code, and `run` runs the codes a row names in order and stops at the first
sentence. No rule repeats a value's own pattern: `tools/check`'s other checks of one value (the size, the
repositories' spelling, the project's characters and length, the session, status's sections, every other VM name) held
the JSON the `justfile`'s scripts join, behind `just`'s pattern, and are the pattern's alone here; nor does `aw` keep
`init`'s refusal of a token file named relative to a folder whose path has a space, which held that JSON too. A
sentence says a value back only where today's does, and then a value that has met its pattern first, as `just` holds a
value to its pattern before a recipe's script runs. What reads more than the values is no rule here but its verb's own:
the fleet's files (`vault`'s, and `deploy`'s VM name another host's alias records), the file system, `claude` and the
branch (`mcp`'s), and a terminal (`host --remove`'s question).

## 16.3 `cli/` — the `aw` program

`lib/claude_vm/cli/` is the program: reading the command line against the table of `schema/`, the dispatch of a verb
to its workflow, the printer of a command, help and the command reference's tables, below. `main.py` is the entry
`tools/aw` runs, as 16.1 says.
It imports `schema/` and what it needs to answer at once. It imports `engine/` only inside the function that dispatches
a verb that changes something, so a refusal, help or a read never loads it. It never imports Ansible, which the engine
only ever runs as a child, nor `control/` or pydantic. `tools/aw` imports it, and so does the agents' server's own entry
once it is built; that server runs `aw` as a child for every run.

**Reading the words: `parse.py`.** `read(words)` takes the words typed after `aw`: first the words before the verb
(`--attended`, the hidden `--detached-run`), then the verb, then its words and options, split by
`argparse` into the table's slots for the one verb typed only. No verb is `help`, as `just` alone lists the recipes.
`argparse` is set as the design requires: abbreviations off, so a prefix of an option is an option the verb has not
got; its own help off; its errors raised rather than printed and exited on; and its colours off from Python 3.14. An
option is taken as `--long value` and as `--long=value`, which is how a value that starts with `-` is given, and is
given once, but for `--set`, given once for each setting, and `-v`, counted. `--`, which elsewhere ends the options, is
refused wherever it stands, before `argparse` sees it, in a sentence that says how a value that starts with `-` is
given: no verb takes a word that starts with `-`, and `argparse`'s intermixed parse would drop it and read the words
after it as options all the same. Every verb whose run is a job gets
`--detach`, from the table's column, and no other verb has it. The values, only those given, then go through the one
check (16.2). Anything refused raises `Refused` with one sentence of the project's own, which names the option where
one was wrong and never a value or a verb typed: no text of `argparse`'s, no usage block, no exit status of its own.

**Today's habits: `habits.py`.** After the check, as ordinary code, a VM's name with `_` is taken with `-` and said in
today's words, `alpha,beta` is two names as two words are, and a verb that takes one name refuses two. Where a verb
that takes several names is given none, the two meanings of no names are one field, `default_vm`: true for a whole
`deploy`, `deploy --only provision` and `destroy`, which act on the default VM; false for the rest, which act on every
VM the fleet made. The default VM's name is the handler's to read from the settings; the parser reads no file of the
fleet. A stage of `deploy` given where the names go, and neither `--only` nor `--check`, is asked about at a terminal,
read from the standard input the command was given: a menu of `lib/claude_vm/tui/choose.py` where the terminal can draw
one (the stage, the VMs so named, or nothing, which is marked as it is drawn), and today's typed question where it
cannot. Each answer names the command line it runs as the printer writes it, a value of `--set` never said back, since
nothing has taken it yet. The answer meets the rules as if typed, the one check run again on what it comes to; with no
terminal, and for any answer but the stage or the VMs, it is refused in today's sentences, and a Ctrl-C there exits
130.

**The run's own words: `before.py`.** `RunWords` is one frozen record every handler receives first: `-v`'s count and
the `--set` words as typed, from after the verb; `--detach`; `--attended=1`, the agents' server's word that someone is
at the screen with no terminal; `--detached-run`; and `LOG_DIR` and `LOG_RETENTION_DAYS`, read from the environment as
today. Nothing else is read from the environment: an exported `RUN_LOCK` or `ATTENDED` changes nothing, and no word
makes a run take no lock (16.8).

**The dispatch: `dispatch.py`.** The handler the verb's row names, `module:function` under `claude_vm`, is imported only
when that verb runs and called with the run's own words and then the verb's values as keyword arguments; what it returns
is the exit status. Before it, in this order: a Python that is no virtualenv's (`sys.prefix` is its base's) is refused
for every verb, in a sentence that names `PYTHON`, since the project runs only from a virtualenv and the launcher runs
whatever `PYTHON` names; an environment that lacks the packages the engine needs is refused, naming `aw install`, for
every verb but `install` and `help`; a verb no card has built yet is refused naming the card that builds it and, where
today's `justfile` has a recipe of that name, `just <verb>`, which does its work until the cutover; and a verb whose run
is a job, but `init` and `install`, which come before any fleet, is refused where the fleet's files do not exist, naming
`aw init`, and says on standard error which fleet it drives where `AGENT_WAREHOUSE_HOME` or a `fleet_home` line chose
it. A refusal is one line on standard error and exit status 1.

**The printer of a command: `inverse.py`.** The one way the new engine writes a command line a person types, for NEXT
STEPS, `status`'s repair lines, a refusal's way out, the lines a detached run's start prints and the session notes on a
machine: `argv(verb, values)` gives its words and `line(verb, values)` the one string a person pastes, each value
quoted with `shlex.quote` where a shell would read it otherwise. Both read the one table and nothing else. The program
is `aw`, always; then the words given before the verb, in the table's order; the verb, `deploy`'s whole chain as its
one verb; its options in the table's order, `--detach` last as the parser gives it, a value as `--long=value`, so that
one that starts with `-` is read as a value, a flag bare, `-v` repeated in one word, `--set` once for each value; the
verb's words as given; and last the host flag, ` --host <name>` as today's lines end with it, on a verb that takes
`--host` and was not given one. The values are the parser's or a handler's: `default_vm` is said by naming no VM. Each
is held as the one check holds it but for the rules between options (`formed`), and one it refuses, or a word that
starts with `-`, raises `ValueError` with the check's sentence, which never says the value; a rule is the run's to say,
so a line may name a run that one refuses, as `deploy`'s question names the stage before its answer meets the rules.
The table holds no option whose value is a secret, so nothing is masked but `--set`'s and `settings --value`'s, the
two values of free text a person may paste one into: a line made from values no run has taken, a refused run's or a
question's, prints each as the name help gives it. The one check's own refusals name the command refused and its help
themselves, since `schema/` imports nothing of `cli/`, and are held equal to the printer's; no other module of the new
engine builds a string that starts with a command, but help's usage lines. It imports the standard library and
`schema/` alone.

**Help: `help.py`, and the command reference: `reference.py`.** `help` is a verb of the table whose handler is
`help.py`'s `main`, so `aw help` runs as any verb does, and `aw` alone is `aw help`. It prints today's two levels of
`just help` from the one table, whole from the first day: a verb no card has built yet is listed and helped as today,
and refused only when it runs. `aw help` lists every verb in the table's order, `help` first and then each group under
its name, each with the words it takes and its doc; then the options given before any verb, but the hidden
`--detached-run`, since the one check's refusal of such an option sends a person there; then today's closing
paragraph, with `aw` where it said `just`, and `-v` and `--set` where it named `ANSIBLE_ARGS`, each named on the verbs
whose row has it. `aw help <verb>` prints the verb's usage line as today's command reference writes a recipe's
(`aw deploy [<name>...]`), its doc, each option with the name of its value and its help as `just --usage` shows one
today (`-t, --tags ROLES`), `--detach` last on a verb whose run is a job, and then its words with their help. A
second word is refused by the one check in today's sentence, and a word that is no verb by help, in the check's
sentence, which never says it. The usage lines are help's own; a whole command line it prints, for a person to paste (the closing
paragraph's example, `aw host`), comes from the printer. A usage line shows a verb's word without brackets only where
the verb needs one, `code` and `release`, which help names, since the table says how many words a verb takes and not
whether it needs one. `reference.py`'s `commands()` writes the tables of the command reference from the same
functions, in the form `tools/docs.py` writes them from the `justfile` today: a table for each group of verbs, `help`
aside, with a row for each verb, each of its options and its words, and one of the options given before any verb, each
cell escaped as `tools/docs.py` escapes one. Nothing calls it before the cutover, which points `tools/docs.py` at it.
Both import the standard library, `schema/` and the printer alone, and `aw help` loads nothing of the engine.

**The launcher: `aw`.** At the checkout's top, in POSIX `sh`, the launcher finds its checkout from its own path, every
link to it followed and the folder made by its physical path (`cd -P`), so that a relative link in a folder that is
itself a link lands in the target's checkout, and never from the working directory, so no `justfile` or checkout found
upward runs in its place, and the checkout guard of today's recipes is not needed. It asks `tools/env-run --which` which
environment the checkout runs, a virtualenv of the person's own or the checkout's own, from the checkout's own folder,
as every other caller of `tools/env-run` does: nothing of the folder it is run from decides, neither a version manager's
choice of `python3` there nor a module there that the question's Python would import. A Python of the person's own is
asked in the checkout's folder which interpreter it is, so that the one run is the one found there, a bare name, a
relative `PYTHON` (the checkout's, as for `just`) or a version manager's shim alike, and `tools/aw` runs with it by its
absolute path under `-I`. A Python that cannot be run is the launcher's own sentence, never the shell's words for a
failed `exec`. `install`'s one home is its function `install_env`: what `just install` does to the environment, a sync
of a virtualenv of the person's own with `--inexact` and no download after `tools/stale_scripts.py`'s reinstall, or of
the checkout's own exactly the lock's on the Python version `managed_python` names, which the `justfile`'s holds equal;
then git's pre-commit hook, as `just init` puts it, but nowhere where `core.hooksPath` is set. It installs no Galaxy
collection and moves nothing of 1.x. It runs in three cases and no other: by itself, once, where the checkout's own
environment does not exist yet, saying so on standard error before it runs the program; for `aw install --lock`, which
writes `uv.lock` and needs no environment; and for its one word of its own, `--install-env`, which `aw install`'s run
passes it as a child carrying the job's marker, `AGENT_WAREHOUSE_JOB`, and which is refused, naming `aw install`,
without it. It never installs into a virtualenv of the person's own unasked: the program names `aw install` where one
lacks the packages.

`tests/unit/test_aw_cli_parse.py` holds all of it: every option of every verb in both spellings, the refusals and that
none says a value or has a usage block, `--detach`, `default_vm`, `RunWords`, in `run_words` and in the program, the
dispatch's refusals, what a refusal imports, and the launcher run in checkouts the test makes, with a stand-in `uv`,
through links, from folders whose version manager picks another Python or that hold a planted module, and with a
`PYTHON` that is no virtualenv's or cannot be run. `tests/unit/test_aw_cli_rules.py` holds the rules and `deploy`'s
question: every rule of the contract (`tests/unit/cli_contract.json`) is a rule or a habit with today's sentence, or
named as another verb's own, as a check whose value's pattern refuses first (on `aw`'s line as on `just`'s), or as a
rule `aw` drops on purpose; each of the contract's cases of a rule or a habit is run against `aw` and gives today's
status and sentence; the cases not yet run are named one by one, and a code comes out once its case runs; the check
answers alike as words and as JSON; the question is asked at a pseudo-terminal, typed and with none; each behaviour of
today's words that `aw` has otherwise on purpose, and that is no exit status, has a test of its own there; and each
test of today's options' rules in `tests/unit/test_justfile.py` has a twin there by its name.
`tests/unit/test_aw_cli_inverse.py` holds the printer: for every verb, its words, each option alone and beside them and
each pair of options the rules allow come back from the line through the parser and the one check, and no verb or
option lacks such a case; every example of the contract that `aw` takes comes back from its line, before today's habits
and after; an awkward value, an empty one and a name that starts with a digit survive `/bin/sh`; a value of free text
is never said back from values no run took; a value the check refuses is never printed; and, by a scan of their
syntax, that no other module of the new engine's subpackages builds a command. `tests/unit/test_aw_cli_help.py` holds
help and the reference: for every verb of the contract, `aw help <verb>` holds each option and help sentence `just
--usage` prints today, with `aw` where it said `just`, but a word the cutover's map records as changed, and every row
today's page shows for it; every verb's help shows its options and words in the table's order; `aw help` lists every
verb and no recipe of the repository's own, the options before a verb, and today's closing paragraph with `-v` and
`--set`; through `tools/aw` and the launcher; what each imports; and that the reference gives a row for every verb but
`help`, every option and the words, holds today's rows with `aw`, and escapes a cell as `tools/docs.py` does.

## 16.4 `engine/` — the workflows, one module a verb

`lib/claude_vm/engine/` is where the workflows go, one module a verb. Each says what a run of `deploy`, `destroy`,
`login`, `status`, `code` and the rest does, in the command's own process, in place of the playbooks. It imports the
clients (`pve/`, `ssh/` and `vendors/`), and `runs/`, `fleet/`, `guest/` and `schema/`. It never imports `cli/`,
`control/`, pydantic or today's `claude_vm.ansible`, and it runs Ansible only as a child process. `cli/` imports it,
and only when it dispatches a verb that changes something. No client imports it.

## 16.5 `pve/` — the typed Proxmox client and the one table of privileges

`lib/claude_vm/pve/` is where the typed client of the Proxmox VE API goes, with the one table that names each call's
privilege, which `init` grants from and the permission check reads, and the wait for a started VM's address. It imports `vendors/` for its one HTTPS transport,
`schema/`, `secret.py` and, of today's modules, what `REUSED` names: `trust` for the anchor's context, `proxmox` for
its parsers as they stand, `checks` for the names Proxmox gives, and `text`. It imports nothing of `engine/`, `cli/` or
`control/`, and no pydantic. It is Proxmox's own folder in `tests/unit/test_ratchets.py`'s `PROXMOX_OWN`, beside
`lib/claude_vm/proxmox.py`: it may name the API's paths and a VM's ID, which a file outside it may not name more often
than it does today.

**The pinned connection: `pin.py`.** The token goes only on a connection whose certificate verified against what the
host keeps, before a byte of the request is written. A host keeps one of two things, which `init` works out and keeps.

- **An anchor file**, its node's own certificate or its CA, which is then the context's only source of roots.
  `context(anchor_pem)` makes that context with today's `trust.anchored_context`, the one way a context is made from a
  certificate the host gave: the name the API was reached at checked, and Python 3.13's flags (strict, and a partial
  chain, so that the node's own certificate is an anchor of its own) on every Python. So a CA with no `keyUsage`, as
  Proxmox VE 8 made one and VE 9 did before pve-cluster 9.1.8, is no anchor on any Python, and such a host's anchor is
  its node's own certificate.
- **No file**, where the host's certificate verifies against the computer's certificates, the vendors' (16.7):
  `certifi`'s list, with the file the setting `tls_ca_file` names added to it.

Where the anchor is the node's own certificate, its SHA-256 fingerprint as it was recorded is held too: `check(expected)`
is the function the transport's `peer_check` calls with the certificate the server presented, and a certificate that is
not the recorded one is refused there, with nothing sent. `kept(anchor_pem, fingerprint, tls_ca_file)` gives a `Pin` of
the three, the context, whether it is an anchor's and the fingerprint, and refuses a fingerprint with no anchor beside
it, or one that is not one as `openssl` prints it.

- **The certificate is always checked**: no context checks nothing, and none can be asked for. **Its name is checked
  but where a fingerprint names the certificate.** With a fingerprint recorded, `kept`'s context has `check_hostname`
  off, whatever the anchor, a CA's included: the anchor still verifies the certificate, and the peer check holds it to
  the one certificate recorded, which says more than a name could, so a host reached at an address its certificate
  does not name, a tailnet's or a forwarded port's, is reached through its node's own certificate. With none, an
  anchor and no anchor keep the name checked, since each verifies any certificate it signed. So a `Pin`'s context,
  where a fingerprint is recorded, checks no name of itself: the fingerprint's check is `transport`'s, handed beside
  it, and nothing but `pin.py` reads a `Pin`'s context (`tests/unit/test_aw_vendor_trust.py`). A `Pin` keeps it under
  the private name `_context` and as no field of the dataclass, so no `astuple`, `asdict`, `repr` or class pattern of
  one holds it, a read of it elsewhere is a private name read off another object, and a run's own `context` is none of
  these. A `Pin` is equal only to itself.
- **No session's keys are logged.** Each kind of context is made by hand - today's `trust.anchored_context` as the
  vendors' `context` is - so that neither opens the file `SSLKEYLOGFILE` names, as the standard library's ready-made
  context does as it is made, with OpenSSL's header, unless Python ignores the environment: a path that cannot be
  opened would refuse the client, and a FIFO nobody reads would hold it. `context` sets `keylog_filename` to none on
  both kinds too, so no key of the one connection that carries the token is ever written, however Python was started.
  `transport(url, pin, proxy)` makes the one connection a token goes
on, so `pin.py` alone hands the transport a check of the peer (`PEER_CHECKS`), and it touches neither `http.client` nor
a socket. It reads no setting and no variable: the anchor's text, the fingerprint, `tls_ca_file` and the proxy, which
`proxy.choose` works out for the API's address as today's Proxmox calls take it, are its caller's.

**The client: `client.py`.** `Proxmox(url, pin, token, proxy)` is the one class that sends the engine's token to
Proxmox, and the only place the new engine writes the `PVEAPIToken` header. The `Token(id, secret)` is the engine's one
token on the fleet's pool, `user@realm!name` and a `Secret`, handed to the transport as parts of the header's value and
revealed there alone. No method returns it, and no error says it: where an error quotes Proxmox's words, the token's
secret is struck from them first (`Secret.struck`), as they came and again once made plain, and before they are cut,
so that a host, or a proxy the computer's certificates trust, that echoes what it was sent has none of it said.

- **The first request is `GET /version`**, asked until a client has it. A host below Proxmox VE 9 is refused, `TooOld`,
  with a sentence that names the version found, before any other request goes, and every later call says so again
  with nothing sent. No lock is held across the request, so a call made while another asks it waits for nothing but
  its own request, within its own deadline: two first calls at once, two VMs' first polls, may each ask it.
- **What an answer is, by its status alone.** A 401 or a 403 is `Denied`, never by its words, since Proxmox names a VM
  in its reasons ("VM 4013 is not running"); a workflow stops at it, since it holds for every VM. A 3xx is an answer,
  never followed, and so is every other status but a 200: `Refused`, with its status and Proxmox's reason, the status
  line's words that the transport's `Answer` carries, made one line of plain text. A 200 that is not Proxmox's JSON, or
  lacks what its record cannot be without, is `Malformed`. The transport's own errors pass as they are.
- **A certificate that is not what the host keeps** - the transport's `Untrusted`, anchor or none, and the pin's
  refusal of another fingerprint - is `Unverified`, with nothing sent and nothing tried again without the check. Its
  remedy is told by OpenSSL's verify code (`Untrusted.code`, kept as its `code`), never by its words. A certificate that
  does not name the address it was reached at (`MISNAMED`, codes 62 and 64) is an address to change: the sentence
  names the address the host's record gives, and the node's own name on a tailnet, or the node's own certificate kept
  by `aw init`, held to its fingerprint and not to a name. A certificate out of its dates is said so: one that has
  expired (`EXPIRED`, code 10), or one that issued it, is renewed on the host, and where the host keeps an anchor `aw
  init` then reads the new one and asks before it keeps it; one not valid yet (`NOT_YET_VALID`, code 9) means a clock
  is behind, this computer's, whose time now the sentence says, or the host's when the certificate was made. The
  client cannot say the certificate's own dates: a refused handshake hands it none. Any other refusal names `aw
  init`, which reads the host's own certificate and asks before it keeps one as the host's anchor; where the host
  keeps none and the issuer is
  unknown (`UNKNOWN_ISSUER`), a root of the person's own in `tls_ca_file` comes second, never alone: the root of a
  Proxmox host's certificate is almost always the host's own CA, whose key is on the host, and a root added there is
  trusted for every vendor too.
- **The reads**, each returning a frozen record of `answers.py`, built by a plain parsing function there:
  `resources(kind)` (`/cluster/resources` of a `vm`, `storage`, `node`, `pool` or `sdn`), `pool(poolid)` (its comment
  and members), `config(node, vmid, nic)` (with its `digest`, its identity, the SMBIOS UUID, through
  `proxmox.smbios_uuid`, and the MAC of `nic` through `proxmox.net_mac`), `current(node, vmid)` (the VM's own state
  now), `next_id()`, `node_status(node)` (its CPU threads and memory, for a VM's size: `Sys.Audit` on the node),
  `task_status(node, upid)` and `task_log(node, upid)`, `storages(node)` and `bridges(node)` (as `init` asks them
  today), `permissions(path)` (the token's privileges at an ACL path), and `read(path, query)` for the reads `status`
  adds. A listing's status is the one the cluster last polled, up to ten seconds old, and is never read for what the
  same run changed: `resources`' docstring says so and names `current`.
- **Proxmox's loose JSON.** 0 and 1 for booleans, numbers sent as strings and keys that come and go by version are read
  as they come; what a record does not name is kept as it was sent, frozen, a mapping read-only and a list a tuple. An
  answer nested deeper than `MOST_DEPTH` (100), which no answer of Proxmox's is, is `Malformed`, never a
  `RecursionError`: `json.loads`, in C, lets one reach past Python's recursion limit.
- **"Gone" is no error of the client's.** A read of a VM that is not there is `Refused`, Proxmox's 500 naming its
  configuration's file: a workflow knows a VM is gone from `resources("vm")`, never from those words.
- **Held before anything is sent.** Every name and ID that goes into a path is held to the form Proxmox gives it (a
  node, a pool, a VMID from 100, a UPID, an ACL path, a network card, a path below the API with no `.` or `..` part
  and no query), so that none can reach another path or a query; so is every value a write sends (a VM's name, a disk
  and its size, a tag, a digest).
- **The writes, each sent once.** The transport sends no request but a `GET` twice, and one that left with no answer is
  its `Unknown`, passed as it is: a workflow settles it by reading the VM again, never by sending the write again.
  `clone(node, source, newid, name, pool, full, storage, description)` clones into the pool, a full clone by default,
  with the notes given; `resize(node, vmid, disk, size)` grows a disk, never shrinking one; `start`, `shutdown` (with
  its `timeout` and `force_stop`), `stop`, `to_template` and `delete(node, vmid, purge=False)`, which sends
  `destroy-unreferenced-disks` alone: it takes the disks its configuration no longer lists but that carry its VMID
  too, as today's role does, and nothing outside the VM, so Proxmox leaves the host's backup jobs, replication and HA as
  they are and refuses to delete a VM a person put under HA or replication, as it refuses today's deletion. `purge`,
  which would take the VMID out of each of them, is sent only where a caller asks for it. Each write answers a `Task`
  (its node, UPID, kind and VM; a clone's, the new VM's, with its `source`). A clone whose answer was lost is `Unknown`
  with the clone perhaps running at `newid`, its configuration locked `clone` until its worker ends: `clone`'s docstring
  says it is settled by reading `newid` again until that lock is gone, never by a clone under another VMID before then.
  No write changes a pool: a new VM joins the fleet's pool through its clone's `pool`. The VMID to clone onto, the record's text and any sequence of calls are the
  workflows' (16.4), not the client's.
- **Every write of a VM's configuration sends `digest`.** `write_config(node, vmid, changes, digest)` is one `PUT`,
  synchronous: Proxmox writes the changes only while the configuration is still the one whose digest the write sends,
  in the call itself and under the VM's lock, so a `POST`'s window, a check at the call and the write in a task that a
  write made between them fails, never opens. A stale digest is `DigestMismatch`, nothing written; `edit_config(node,
  vmid, edit)` reads, applies `edit` to what it read, writes with that read's digest, and on a mismatch reads and
  applies again, `EDITS` (three) times at most, then raises. `changes` holds only the keys the table's rows for the
  write cover (`privileges.keys`), each under the row of the privilege Proxmox checks it with (qemu-server's
  `check_vm_modify_config_perm`): a key outside them, a network card's or `startup` above all, is refused before
  anything is sent, and needs its row first. Tags are a list, sent comma-joined, and read back through `proxmox.proxmox_tags`, which takes
  both spellings; `sshkeys` is the keys' text, sent percent-encoded whole, as Proxmox's format wants it, and read back
  as Proxmox keeps it, encoded. The rule is held where every write passes and by what was sent: the client's
  `_request`, which every request goes through, refuses a `PUT` or a `POST` to a configuration's path whose form holds
  no `digest` of the form Proxmox gives one (`DIGEST`) before anything is sent - an empty one among them, which
  Proxmox reads as none - and every test of the new engine that runs the fake
  (`harness.fake_fleet(engine=True)`) ends by reading the fake's request log, failing where a `PUT` or a `POST` of the
  client's user agent to a configuration's path carried none. Both read a path as Proxmox reads it, its `%`-escapes
  decoded, so that `.../confi%67` is a configuration's path too, and an escaped `/` counts as one; the path is still
  sent as it was given, never decoded or escaped again, so a `%` is no refusal of its own.
- **Proxmox's refusals of a write, typed.** Proxmox answers each with a 500, so its words tell them apart, each held
  equal to the fake's constant, which experiment 6 corrects: `DigestMismatch`; `Locked` (a clone, a deletion or a backup
  holds the VM, with Proxmox's word for the lock, or another task held its configuration's file longer than a write
  waits, the lock `file`), which a workflow may try again - from a clone, its source's, at the call and in its task;
  and a clone's `Exists`, whose VMID is
  taken, at the call or in its task in any of Proxmox's three sentences (the file exists; the new VM's lock timed out
  while another run's clone holds it; a container or another node's VM holds it). On `Exists` the workflow asks for the
  next free VMID again, and nothing is ever deleted at the VMID asked for, which on a host would be the VM of the run
  that came first. The new VM is proved by reading its own configuration once its task has ended, never by an ID a call
  returned. The engine's own clone joins the pool only when its task ends, so until then a write to it is `Denied`.
- **A task followed to its end.** `follow(task, deadline, stop)` polls the task's status in one loop, its pauses
  growing to a second, within a deadline of its kind (`DEADLINES`: today's waits, 600 s for what `roles/proxmox_vm`
  follows, 300 for a conversion, 120 for a resize) or the one given, and never longer: each poll is held to what is left
  of it. A task that ended `OK` is a success, and so is one that ended `WARNINGS: <n>`, as Proxmox counts it and today's
  `community.proxmox` takes it: its worker logged a warning and went on (a person's template with `iothread` on another
  controller logs one at its start). Its status then holds how many (`warnings`) and the last lines of its log
  (`lines`), for the workflow to say. A task still running at the deadline is `TaskTimedOut`; one that ended otherwise
  is `Exists` or `Locked` as above, else `TaskFailed` with Proxmox's error and the last lines of its log, each one line
  of plain text. The log is read once the task has ended, each page held to what is left of the same deadline and
  `stop` read before it, and it never decides the outcome: the task's end is Proxmox's answer, already read, and its
  lines a courtesy. Where they cannot be read in that time, a read of them fails, or the run stops, a success is
  returned with no lines, and a failure is `TaskFailed` with none, whatever stopped the read; lines read before then
  are not the last, and none is kept. `Stopped` is only for a task left before its end: once a status read says the
  task ended, `follow` says how it ended. A poll that fails while the run
  goes on raises its own error - the transport's `TimedOut`, where one poll had no answer within its read's wait with
  time still left, its `Unreachable`, or the client's - and the task may then still run on Proxmox: the workflow
  follows it again by the `Task` it holds, or reads the VM. `stop` is the run's
  `Context.stopping` (§16.8), a `threading.Event`, since a workflow calls the client in a thread: it is read before each
  poll and waited on in place of each pause, so once the run stops, at a Ctrl-C or wherever it is to end, `follow`
  raises `Stopped` within a pause's first moment, never asking again, and the task, left before its end, may still run
  on Proxmox, which `Stopped`'s sentence says with the task's kind and VM; a poll already sent is never cut by the
  stop, and ends within its own limit. One that the run's stop made fail - its client closed under it, whose transport
  then sends nothing more, the poll cut or its transport found closed, each the transport's error marked `closed`
  (16.7) - is `Stopped` too, its cause kept. Any other failure is the poll's own and raised as itself, though the run
  has stopped: the transport's `Unreachable` or `TimedOut` of the network's making, and a refusal, `Denied`,
  `Unverified` or `Malformed`, which says what the host said and which `Stopped` would hide. A phase that must finish,
  a deletion inside a shield, passes no `stop`, and is followed to its end.

**The one table of privileges: `privileges.py`.** `CALLS` is a literal tuple of
`Call(name, method, route, privilege, on, keys)`: one row for each privilege of each call the engine makes, its route
below `/api2/json` with each name in braces (`/nodes/{node}/qemu/{vmid}/config`), and `on` the kind of ACL path Proxmox
checks it on - `vm` (`/vms/<vmid>`, which a member of a pool also takes from the pool's roles), `new` (`/vms/<newid>`,
the VM a clone makes, named by its `newid`), `pool`, `storage`, `bridge` (`/sdn/zones/localnetwork/<bridge>`) or `node` -
or no privilege and no kind for a call that needs none (the version, the next VMID, the token's own permissions). A
call that needs several privileges has a row for each, and needs every one. The rows are Proxmox VE 9's names alone,
read for each call from Proxmox's source, until a real host is read for them. A row says what Proxmox checks, in the
three shapes its checks take:

- **Either of two privileges.** A row's `privilege` may be a tuple of alternatives, any of which suffices: a node's
  storages and a storage's disks take `Datastore.Audit` or `Datastore.AllocateSpace` (pve-storage's `check_any`), a
  node's bridges `SDN.Use` or `SDN.Audit` (pve-manager's `check_sdn_bridge`), and of a configuration's keys
  `nameserver` and `searchdomain` `VM.Config.Cloudinit` or `VM.Config.Network`.
- **Either of two paths.** A row's `on` may be a tuple of kinds, the privilege held on any of them sufficing: a clone's
  `VM.Allocate` is held on the pool it names or on its new VM's path (`pool`, then `new`), and with no pool named on the
  new VM's alone, as the call's declared check, an `or` of the two, asks it.
- **Key by key.** A configuration's write is checked for each key it sends or deletes, with the privilege of the key's
  group (qemu-server's `check_vm_modify_config_perm`): its rows each carry the `keys` they cover - the record's notes,
  its tags, its name and the general options; cloud-init's, `ipconfig0` to `ipconfig31` among them; the CPU's; the
  memory's - and a row with keys is checked only for a request that sends one of them. `keys(name)` is the keys the
  rows of a call cover, which the client holds every write to; a key no row covers has no privilege granted for it.

A resize needs `Datastore.AllocateSpace` on the storage its disk is on, which its VM's configuration names. No call
writes a pool, so of `Pool.*` the table names `Pool.Audit` alone, and the role on the pool holds no other: a new VM joins
the fleet's pool through its clone's `pool`, which needs `VM.Allocate` on the pool, and the VMs a fleet already had join
it over root SSH, as `init --migrate` adds them. A token whose role is on its pool alone could add no guest it did not
already hold, and `Pool.Allocate` would let it rewrite its pool's comment, the fleet's mark, and delete the pool once
empty. Two functions read it: `needed(on)`, the privileges the table needs on
one kind of path, which `init` makes the engine's role of and grants, and the permission check compares with what the
token holds (on a pool, with every privilege checked on a VM, which the pool's members take from it) - each row's first
privilege on its first kind, never an alternative, so a second way Proxmox lets a token through widens no role; and
`for_request(method, path)`, the rows of one request, each with, for each of its kinds, the concrete ACL path the
request's path names - none where the call names it in its parameters, or lists such objects, each listed only where
one of the row's privileges is held on it. It imports only the standard library: the fake Proxmox reads it with nothing
but `lib/` on its path, enforces it when a test turns that on, and keeps no table of its own, so a test proves what the
token can and cannot do from the table `init` grants. `tests/unit/test_aw_pve_privileges.py` holds every privilege to a
list of Proxmox VE 9's, the module to the standard library, the rows' shape, `needed` to each row's first privilege on
its first kind, each key of a configuration's write to one row, and the fake and the harness to no privilege name of
their own but today's.

**A started VM's address: `agent.py`.** The client's one call to the guest agent, `agent_interfaces(node, vmid,
deadline)`, asks what the VM's agent reports of its network cards (`network-get-interfaces`, `VM.GuestAgent.Audit`) on a
transport of its own, whose reads wait `AGENT_READ` (10 s) where every other read waits 30, since Proxmox asks the VM
before it answers. Its connections are pinned as the client's others are. It answers an `Interfaces` record; a VM
that is not running is `NotRunning`, and one whose configuration turns no guest agent on `NoAgent` (Proxmox says so
before whether it runs), each told by Proxmox's words on its 500 as the writes' refusals are, read as Proxmox's whole
sentence and never found within its words, where a VM's could be; an agent that does not answer yet is `Refused`. The agent runs inside the VM, so
all it reports is the VM's to choose: of its answer the record keeps each card's name and MAC and each address's
address, type and prefix where each is a plain value, and nothing the VM nests deeper.

- `address(interfaces, mac)` is the VM's address: the IPv4 address today's `checks.guest_ipv4` takes, as it stands, on
  the card whose MAC is `mac`, the one the VM's configuration gives - never a bridge the guest grows later - and only
  what `ipaddress.IPv4Address` parses as one address a card answers at, never `0.0.0.0`, multicast, broadcast, the
  reserved range, or an address with a newline and a `ProxyCommand` after it. Of two good addresses on the card it
  takes the first, as today. An answer of any other shape is no address, never an error.
- `wait_for_address(client, node, vmid, mac, deadline, cancelled)` is a coroutine that polls the agent, each poll the
  blocking client in a thread, held to what is left of the deadline and to the longest one agent call usefully runs
  (the client's `agent_call_most`, `AGENT_READ` and `pin.CONNECT`: 15 s), with pauses growing to today's five seconds
  (`PAUSES`; `DEADLINE`, 300 s, is today's 60 polls), until the card has an address. A new client's first poll asks
  the version within the same bound. It stops at once where waiting cannot help - `Denied` at the first 401 or 403,
  `NotRunning` for a VM that is not running, at the first poll or the first after it stopped, `NoAgent` for one with
  no guest agent configured, whose sentence names the `agent` setting that turns one on, and `Unverified` (or the
  transport's `Untrusted`) for a certificate that is not the host's. A connection refused or reset, the transport's
  `Unreachable`, is not yet, as a node's 595 is: pveproxy restarting, or the computer's network coming back, seconds
  after a start. At the deadline it raises `NoAddress`, which says what the last poll met. It ends with
  `asyncio.CancelledError` once the run's `cancelled` is set, between polls or during one; that poll's thread is not
  read, ends within its bound, and, once the run has closed its client, at its first error, sending nothing more.

`status` moves onto the client in its own workstream. `tests/unit/test_aw_pve_reads.py` runs every read against the fake
Proxmox, which serves a node's certificate from `tests/unit/pki.py` (`harness.fake_fleet`'s `certificate`), with the
engine's pool and token, granted as `init` grants them and held to the table, so every read is a call the table has a
row for. For an anchor of another node's certificate, of another CA, the right certificate with another fingerprint
recorded, and no anchor with a computer's file that holds no root of the fake's, the fake's request log holds no request
of the client's, also with the fake answering no request without a token, and each refusal names `aw init`,
`tls_ca_file` second with no anchor. A certificate that names `127.0.0.2`, the fake at `127.0.0.1`, is reached through
its node's certificate with its fingerprint, and refused, with the address to change and nothing sent, through that
certificate with none, its CA and no anchor; a node's certificate that ended yesterday or begins tomorrow, kept or
under its CA, is said to be out of its dates; each sentence is told by its verify code alone. No connection logs a
key under `SSLKEYLOGFILE`, and the file it names is never opened. Two first calls at once each ask the version within
their own deadline. It holds that `PVEAPIToken` is written in one module of the new engine, that no method returns
the token, and that no error, its words, its arguments or the names of its traceback's frames, says the token's secret.
`tests/unit/test_aw_pve_writes.py` runs every write against the fake in the same way, under enforcement, so a write the
token may not make is `Denied` by the fake as Proxmox refuses it: a clone into the pool proved by its own configuration;
`Exists` at the call and in each of the three sentences of a task, through the fake's `clone-race`, with no `DELETE` in
its request log; two `edit_config` calls that both land, and a writer that always loses raising after three tries;
`Locked` while a deletion or a clone holds the VM, by a configuration's file lock, and from a clone's task its
source's; a clone whose answer a tunnel of the test's own cuts, `Unknown` with one request in the fake's log; a failed
task with its log, one that ended with warnings a success with its warnings' lines (the fake's `task-warn`), one past
its deadline, each poll held to what is left of it; a log that cannot be read once its task ended - each of the
transport's errors and the client's, a host that holds it past the deadline, no deadline left, and the run stopping as
the task ends or during the read - a success with no lines and a failure `TaskFailed` with none, never `Stopped`; a
follow whose run stops while its task still runs, between polls, during one, and before the first, each `Stopped` with
no poll after it and the task still running on the fake; a poll that fails once the run has stopped, `Stopped` where
the run's close made it fail (cut, or its transport found closed) and raised as itself otherwise (dropped, timed out,
`Denied`, `Unverified`, `Malformed`), and each raised as itself while the run goes on; a log whose read could not be
made, whose lines are `()` as a log with none's are; a deletion's parameters, `destroy-unreferenced-disks` alone and
`purge` only where asked for, read from the fake's log; and a UPID with a `/` in a part of its own `Malformed`. Its
rules: a `PUT` or a `POST` to a configuration's path with no `digest`, or an empty one, is refused before it is sent,
`%`-escaped spellings of the path among them, while a path with a `%` is sent as it was given, `harness.fake_fleet`'s
end fails a test where the fake was sent one, the path read decoded there too, and every method that sends one sends
`digest` (a syntax-tree test of the module); a configuration's write sends only
the keys the table covers for it; both ways, every row of the table is a call the client makes and every call it makes
has its rows, each method of the client driven against the fake under enforcement and what it sent read from the
fake's log, a configuration's row with one of its keys; the words the client tells refusals
and warnings apart by match the fake's constants; and no error of a write or a read, the version's included, says the
token's secret where Proxmox's words echo it. `tests/unit/test_aw_pve_agent.py` runs the wait against the fake in the
same way, with what a VM's agent reports planted as its root would make it say: an agent that fails its first two
polls; one that never answers, ended at the deadline and not later; a token without the agent's privilege, `Denied`
with one request made; a stopped VM, and one that stops while it is waited for; a VM with no guest agent configured,
`NoAgent` with one request made; a connection refused or reset, not yet, and a certificate that is not the host's, at
once; the agent's own connection pinned, another CA's certificate and another fingerprint each `Unverified` with no
request of its; each poll held to `agent_call_most`, and a new client's first, the version included, to the wait's
deadline; a poll a stopping run left on a host that holds it, ended within its bound with one request sent once the
client is closed; `0.0.0.0`, a multicast address, two addresses in one, a `ProxyCommand` after an address, and an
address on another card, each no address; shapes of an answer that raise nothing; and the run stopping between polls
and during one. Its rules: the agent's calls go on connections whose reads wait less; the words that tell a stopped VM
are the fake's; and no error of the wait says the token's secret. `tests/unit/test_aw_pve_privileges.py` holds that no
call writes a pool and that the role on the pool holds no `Pool.*` privilege but `Pool.Audit`.

## 16.6 `ssh/` — the runner over OpenSSH

`lib/claude_vm/ssh/` is where the runner over OpenSSH goes. It runs `ssh` as a child under hard limits, takes a
machine's host key at first use and files it under the machine's identity, keeps one shared control directory, and holds
the `ssh` children a run holds. It imports `schema/`, and nothing of `engine/`, `cli/` or `control/`, and no pydantic.

## 16.7 `vendors/` — one HTTPS transport, Tailscale's calls, verified downloads

`lib/claude_vm/vendors/` is where the vendors' side goes: one HTTPS transport, which the Proxmox client uses too,
the computer's certificates, which it uses too where a host keeps no anchor, Tailscale's calls, and downloads held to
their vendor's signature or digest. It
imports `schema/`, `secret.py` and, of today's modules, what `REUSED` names (`text`, to make what a peer said one
line); of other packages, `certifi`, in `trust.py` alone. It imports nothing of `engine/`, `cli/` or `control/`, and no
pydantic.

**One transport: `transport.py`.** Every HTTPS request the engine sends goes through
`Transport(base_url, context, user_agent, connect=5, read=30, most=16 MiB, proxy=None, peer_check=None)`, and its
`request(method, path, headers, query, form, json, stream, deadline)` returns an `Answer(status, headers, body,
reason)`, the reason being the status line's words as the server sent them, which its caller makes plain before it says
any of them (the Proxmox client's refusals quote Proxmox's, which is where Proxmox puts them). It
is written over `http.client` and `ssl` alone, so three things hold by construction. It follows no redirect: a 3xx is
an answer. It reads no certificate variable, no netrc and no proxy variable, because the caller builds the
`ssl.SSLContext` its connections verify with and gives it the proxy too. And it takes an `https://` address only. It
builds no context and reads no setting.

- **The peer's certificate.** `peer_check`, a function of the peer's certificate as DER, is called after each new
  connection's handshake and before anything is written on it. Its raising ends the call as `PeerRefused`, with
  nothing sent. This is the one way into the connection that the Proxmox client's pin uses.
- **A certificate that does not verify** against the caller's context is `Untrusted`, an `Unreachable` of its own, so
  that a caller can tell it from a connection refused, and one that catches `Unreachable` still catches it. It is not
  tried again, and nothing was sent. Its `reason` is what OpenSSL said of the certificate and its `code` OpenSSL's
  verify code. Where the code says OpenSSL knows no issuer it trusts (`UNKNOWN_ISSUER`: no issuer found, a
  self-signed certificate or one in the chain, the first certificate unverifiable), the sentence says what to do where
  a root of the person's own signed it, such as a company's: add that root to the file of PEM certificates the setting
  `tls_ca_file` names, which is added to `certifi`'s list. For any other failure, a wrong name or an expired
  certificate among them, where a root is no remedy, it gives the reason alone. A copy or a pickle of it is made again
  from the two, and says its sentence once.
- **A proxy** is an `http://host:port` URL or none, with a user and password where the URL holds them, held as a
  `Secret` from the moment the URL is read and sent as the CONNECT's Basic credentials to the proxy alone, revealed in
  the one call that writes the CONNECT's head. The connection is a CONNECT tunnel, asked for and read by the
  transport itself, so TLS and the peer's check are end to end with the server, and the proxy sees the server's name
  and port and nothing else. Of the proxy's answer only its status is ever said, never its words. Another scheme,
  `socks5://` or `https://`, is refused by name, as `requests` refuses SOCKS today without its extra package. No
  message quotes the URL, which may hold a password.
- **Bounded.** An answer's body is held to `most` bytes and read no further (`TooLarge`). Every wait is held to
  `connect` or `read` seconds. A whole call, its tries and their pauses included, is held to `deadline` (120 s where
  none is given). Every step of making a connection obeys it: each of the name's addresses in turn, each for at most
  `connect` (where `socket.create_connection` would give every address the whole limit), an address whose family the
  system cannot open passed over as `socket.create_connection` passes it, a proxy's CONNECT and the head of its
  answer, read a byte at a time and held to 64 KiB, and the TLS handshake. So does every read and every send of the
  connection, so a server or a proxy that drips its answer is cut off too. `http.client` is handed the connection
  made, and never makes one itself. Only the name's lookup is not held to the deadline: the system's resolver alone
  bounds it. A socket made for a connection that is not kept is closed however the making ends, a Ctrl-C included.
- **Sent once.** A GET or HEAD is tried again, at most three times in all, after 0.5 s and then 1 s, when its
  connection could not be made or was dropped, when its answer was cut short, or when the answer is 502, 503 or 504.
  Any other method is sent once. A failure once its request has left is `Unknown`, never a second send, and its
  caller reads the state to know. Before a request is written on a kept connection, one whose far end has closed it is
  put aside, which costs no try.
- **A body cut short is no answer.** A body that ends before its `Content-Length` is "the answer was cut short", as
  one that ends part way through a chunk is: `http.client`'s read takes the early end for the body's end and says
  nothing (its own comment says it ideally would), so the transport compares what is left of the length once the read
  has ended. A GET is then tried again, and a write is `Unknown`. So a JSON read meets a GET tried again, and after
  three short answers `Unreachable`, never `Malformed`; and a download's document, the transport's `Unreachable` in
  the same way, never a signature or a digest that does not verify for part of a body.
- **Closed, it sends nothing more.** `close()` marks the transport closed, never to open again: a request started
  after it is `Closed`, nothing sent, and one still under way on another thread - a call its caller let go, as a
  stopping run leaves a poll's thread - ends at its first error, never tried again and never given a new connection.
  Without the mark, the closed connection's error read as a dropped one, and a `GET` that carries a token went out
  again after its caller had stopped. `close()` closes only a connection no request is under way on. One that a
  request is being written on, or its answer read from, it **shuts down** (the system's shutdown of both ways under the
  TLS socket, never `SSLSocket.shutdown`, which takes the TLS state from under the read using it), which wakes that
  thread's read or send at once, and leaves the socket to be closed once its request has ended. A socket closed
  under a wait frees its number while the wait goes on: on Linux, nothing wakes a read blocked on a socket another
  thread closes, and at its limit the read takes the bytes of whatever socket or pipe took the number next and writes
  a TLS alert onto its connection. The decision is made under the lock a request takes its connection under, so
  a connection is either found in use and shut down, or found idle and closed before any request can take it; a kept
  connection is taken before its far end is looked at, so that the look, a poll of its socket's number, is never made
  on a socket `close()` closed; and a connection still being made is refused as `Closed` once made.
- **One place closes a socket.** A connection's socket is closed by `_forget` alone, once no request is under way on
  it, and only after it is taken out of what the transport holds, under the same lock: `close()` shuts down only a
  connection it finds there, so its shutdown never meets a close. The socket `http.client` is handed (`_Wire`) closes
  nothing of itself: `http.client`'s close of it, as it lets it go for an answer's `Connection: close` or after a
  connection error, and an answer's close of what it read, are marks that leave it open for `_forget`.
- **A cut is said as one.** A request whose send or read `close()` shut down is cut, whatever the shutdown made the
  send or the read raise - on Linux almost never a clean end: a TLS read meets the end of the stream, tries to send
  its alert on a socket shut for writing and fails, "Broken pipe", or meets "Connection reset by peer" - and an answer
  read so is never handed back, since one that ends with its connection would seem to end there. A `GET`'s is
  `Unreachable`, "the transport was closed while its answer was read" (`CUT`), and any other method's `Unknown`, in
  the same words first. **The mark is an attribute**, `TransportError.closed`: true for `Closed` and for a cut, false
  for every other failure, the peer's or the network's, so a caller tells the run's own close by it and never by
  words; a copy or a pickle keeps it. A `GET` whose next try a close forestalls - its connection dropped or its answer
  502, 503 or 504, the transport closed before the pause or during it - is `Closed`: the close is what ended the
  call.
- **A secret is never said.** A header's value may be a `Secret`, or a tuple or list of `str` and `Secret` parts, as
  the Proxmox client's `PVEAPIToken=<id>=<secret>` is; and a form's value may be a `Secret`, or a list or tuple of
  values that may be, as Tailscale's `client_secret` is. A `Secret` is revealed in `_plain` and nowhere else, for the
  header it is put in, the form it is encoded in or the CONNECT a proxy's credentials are written into, and no frame an
  error is raised through holds the plain text in a name. A `Secret` anywhere else is refused, never sent as its mark:
  deeper in a form (in a list of a list, a mapping, a name), anywhere in a query, at any depth, since it would be sent
  in the request's line, and in JSON, which refuses it.
  Every header is held to what `http.client` sends as it is (a name that is an HTTP token, a value of visible ASCII)
  before a byte of the request is made, and anything else is `NotMade`, "the request could not be made", because
  `http.client`'s own words quote the header. What `http.client` raised of an answer is said by what it means ("it
  answered something that is no HTTP", "the answer was cut short"), never in its words, which quote the answer's
  status line, and so a header a server or a proxy that inspects TLS echoed there. Each error (`Unreachable`,
  `Untrusted`, `TimedOut`, `TooLarge`, `Unknown`, `PeerRefused`, `NotMade`) is raised after the handler that caught
  its cause has ended, so it carries no cause that could quote one.
- **Threads.** The class is blocking and safe to call from several threads, with one connection kept for each thread.
  A caller on an `asyncio` loop uses `asyncio.to_thread`.

**Today's proxy: `proxy.py`.** `choose(setting, environ, url)` gives the proxy for one URL, reading only the `environ`
it is given. With the setting `network_proxy` at `environment`, its default, it is the proxy today's clients take,
under `requests`' rule, which is the wider of today's two: whatever went direct or through the proxy under either
still does.

- It takes the `https` entry of the environment's proxies, else the `all` entry: `HTTPS_PROXY`, `ALL_PROXY` and their
  lower-case forms, the lower-case one first. A value with no scheme is an `http://` proxy.
- It takes none where `no_proxy` or `NO_PROXY` names the host: by its name, by a suffix at a dot, by `*`, or, for an
  IPv4 address, by the address or a CIDR range that holds it.
- Where the environment names no proxy at all, it reads macOS's network settings as the standard library reads them,
  with their exceptions. Linux has no such settings.

With `none` there is no proxy, whatever the environment says: the stricter form, and an opt-in. With an `http://` URL,
it is that proxy. Of `urllib.request` it calls only the readers that take what they read as an argument
(`proxy_bypass_environment`) or read the system's settings (macOS's `getproxies_macosx_sysconf` and
`proxy_bypass_macosx_sysconf`). It never calls `getproxies` or `proxy_bypass` themselves, which read the process's own
environment, and it never uses an opener.

`tests/unit/test_aw_transport.py` runs the transport against TLS servers it starts on loopback, with the certificates
`tests/unit/pki.py` makes, and against a stand-in CONNECT proxy, which also refuses a tunnel, or drips its answer to the
CONNECT a byte at a time. A server that echoes the token in a status line `http.client` cannot read shows no error
saying it. A read another thread left on a server that holds what it would send next - its answer, a body sent with its
length, a body that ends with its connection - ends at once when the transport is closed, well inside its limit, and
the socket pairs the process opens meanwhile, the first of which takes the number of a socket closed under the read,
keep every byte sent to them and are sent none; so does a send waiting on full buffers; each is said in `CUT`'s words
with the mark, and an answer that seemed to end whole on a socket shut down is handed back for neither a `GET` nor a
`POST`. A body of 10 bytes under a `Content-Length` of 100, with and with no TLS `close_notify`, is no answer: a `GET`
is asked three times, or answered whole on its second try, and a `POST` is `Unknown`. A kept connection is taken before
its far end is looked at, a `_Wire` closes nothing, and on every road a connection's socket is let go by - kept, an
answer's `Connection: close`, a connection dropped, a kept one the server closed - it is closed only once the
transport holds it no more. It holds four rules over the new engine's modules.
Only `transport.py` imports `http.client` or wraps a socket. Only `proxy.py` imports `urllib.request`, and only for
those readers. No module names a certificate variable. And `reveal(` appears once in the transport. It also holds the
modules that hand the transport a `peer_check` to a list, `PEER_CHECKS`, each with why, found by the calls of
`Transport` that pass one: by keyword, through `*` or `**`, or as the eighth argument.

**The computer's certificates: `trust.py`.** GitHub, Tailscale and the download servers are verified against
`certifi`'s list, named by its path, the same on every system, with the file of PEM certificates the computer's
setting `tls_ca_file` names added to it, never put in its place; and so is the Proxmox API wherever its host keeps no
anchor (16.5). `files(setting)` gives the files in the order they are loaded, `certifi`'s and then the setting's where
it is set and not empty, and `context(setting)` makes the context the transport is given. This is a recorded change
from 2.x, whose vendor calls are Ansible's `uri`, verified against the system's own store of the machine that makes
them, which `SSL_CERT_FILE` and `SSL_CERT_DIR` change. Neither the system's store nor `SSL_CERT_FILE`, `SSL_CERT_DIR`
or `REQUESTS_CA_BUNDLE` is read: `tls_ca_file` is the one way to add a root, such as a company's, and the refusal of a
certificate whose issuer is unknown (`Untrusted`) says to add it there. It adds, so a person behind a proxy that
inspects TLS keeps every vendor the proxy does not inspect; what it gives up is narrowing the vendors' trust to roots
of one's own, which nothing does today.

- **Those files, and nothing else.** The context is made by hand, a client context with each file loaded by
  `load_verify_locations`, and never the standard library's ready-made one, which loads the interpreter's own store
  where it is given no file (a store the environment's variables change) and reads the environment for a file to log
  the session's keys to. Nothing of the environment is read, macOS's keychain is never asked, and there is no table of
  each system's paths.
- **Checked as Python 3.13 checks.** The server's name is checked and a certificate required. The flags that Python
  3.13's own context, and today's `requests` on it, set are set on every Python: strict checking, and a chain that may
  end at an intermediate a file holds. So an interpreter before 3.13 is no looser, and a CA with no keyUsage, as
  Proxmox VE 8 makes one, does not verify.
- **A file that cannot be used** (not there, not readable, or holding no certificate, such as a revocation list alone,
  each file told by a context of its own, so that one holding only certificates `certifi`'s list already has is no
  fault) is `Unusable`: the setting's file with a sentence that names `tls_ca_file`, and `certifi`'s, or `certifi`
  missing, with the remedy the command line gives for any package its environment lacks, `aw install`.
- **The proxy is not this module's.** `proxy.choose` gives it to the transport beside this context.
- **The one context.** Of the new engine, only `trust.py` makes a TLS context, gives one roots or sets its checks:
  the transport takes any `ssl.SSLContext`, so a module that made its own could hand it one that checks nothing. The
  Proxmox client's pin (16.5) is the other, with its reason: the host's anchor alone, made by today's
  `lib/claude_vm/trust.py`'s `anchored_context`. Today's modules that make a context are a door the names cannot
  see, so the rule holds imports too: of the new engine, only the modules `CONTEXTS` names import today's `trust` or
  `status`, which make contexts, or any module the new engine may reuse that makes one or imports one that does. And
  no module of the new engine, those two included, names the interpreter's own store.

`tests/unit/test_aw_vendor_trust.py` reaches TLS servers on loopback through the transport with such a context. A
server signed by a root in the setting's file alone verifies, and so does one signed by a root of `certifi`'s, stood in
for, with the setting set to another root. It puts a server's root in either half of the interpreter's own store, the
file `SSL_CERT_FILE` names or the folder `SSL_CERT_DIR` names, there under its subject's hash as OpenSSL reads a folder
(`openssl x509 -subject_hash`, the case skipped where there is no `openssl`), with `REQUESTS_CA_BUNDLE` and
`CURL_CA_BUNDLE` naming it too. It shows that the context, of `certifi`'s file alone or with a root of another subject,
which OpenSSL would look past to any other source, still refuses that server, and that the refusal names
`tls_ca_file`; and that a certificate of another name, or one that has expired, is refused with OpenSSL's reason alone.
It sets every certificate variable to a trap to show that neither the files chosen nor what they load changes. It
holds three rules: no module of the new engine names the standard library's ways to the interpreter's store or its
paths (`DEFAULT_STORE`: `create_default_context`, `load_default_certs`, `set_default_verify_paths`,
`get_default_verify_paths`); of the new engine only `trust.py` imports `certifi`; and of the new engine only the
modules `CONTEXTS` names, each with why, name what makes a TLS context or gives one roots (`MAKERS`: `SSLContext`, the
ready-made and unverified ones, `get_server_certificate`, `load_verify_locations`, `load_default_certs`,
`set_default_verify_paths`, by any spelling but as a type; and a call's `ssl=True`, asyncio's way to a ready-made
one), set one of its checks (`CHECKS`: `check_hostname`, `verify_mode`, `verify_flags`, `hostname_checks_common_name`),
or import today's modules that do (`TODAYS_CONTEXTS`, `trust` and `status`, which a test holds to every reused module
that makes a context or imports one that does).

## 16.8 `runs/` — a run's record and text log, the locks, a detached run

`lib/claude_vm/runs/` is where a run goes: its one record and its text log, the kernel's file locks the run holds, the
run's end on a signal, and a run detached as the same program in a session of its own. It imports
`schema/` and `fleet/`, of `cli/` only the printer of a command (`cli/inverse.py`) for the line that cancels a run, and
nothing of `engine/` or `control/`, and no pydantic.

**A job: `job.py`.** Every run is a job, at a terminal or detached. `create(jobs_dir, verb, command, host, machines,
starter, attached)` makes `jobs/<id>/` in the state folder (`paths.jobs_dir`), with any folder above it that is
missing, at mode 0700, the ID sixteen hex digits as today's MCP jobs have. Before anything else is written it opens the
job's `lock` (0600) and takes `flock(LOCK_EX)` on it; then it writes `job.json` (0600). The descriptor stays open for
the life of the process that made the job, and, as Python opens every descriptor, no child inherits it: the kernel lets
the lock go when that process ends, however it ends, so nothing is ever left to clear. Every value is checked before
anything is made, and the module is the standard library alone, with `store`, `text`, `secret` and the patterns.

| Field of `job.json` | Holds |
|---|---|
| `id`, `verb`, `command` | The job's ID, the verb, and the command as typed, with no secret |
| `inputs` | The run's resolved inputs, a typed `--set` value among them |
| `starter` | What started it: `{"kind": "terminal"}`, `{"kind": "detach"}`, or `{"kind": "mcp", "server": <id>}`, that server's own ID |
| `host`, `machines` | The host the run is on, and the machines it names, by identity |
| `locks` | The names of the locks it holds |
| `attached` | Whether it runs at a terminal |
| `pid`, `started` | Its process and when it started: information for a person and for a machine's `job` line, never read to decide a state |
| `deadline`, `stop` | When a detached run is stopped; a stop asked for, `{"by", "at"}` |
| `directory`, `log` | The run's short-lived directory, and its plain-text log |
| `waiting` | What it waits for, such as a page in the browser, or nothing |
| `next` | NEXT STEPS, records `{machine, matter, line}`, a line removed once nothing is left |
| `machine_outcomes` | Each machine's outcome, the stage it reached, and why |
| `outcome`, `exit`, `ended`, `why` | At the end: `succeeded`, `failed` or `cancelled`, the exit status, when, and why |

`update(**fields)` reads the record, merges the fields and replaces it whole with `store.replace` under
`store.locked`, so the run and another process recording a stop lose neither change. A name `FIELDS` lacks is refused,
and so are `id`, `pid` and `started` (`CREATED`), which only `create` writes: `cancel` signals the recorded pid only
while the job's lock is held, which holds only while nothing else writes it. Only `create` writes a record from
nothing. A change reads the record strictly: where it is gone (the job dropped) or is no record (it does not parse, or
is not a mapping), `Gone` is raised and nothing is written; any other error of the read (a descriptor limit, a disk's
error, NFS's `ESTALE`) is raised as itself, so that `Gone`, which `cancel` says as "no such job", is never said of a run
that runs. A reader is never refused: `read` gives `{}` for a record it cannot read. The record has no schema of its
own and no event file beside it: `events.jsonl` is the add-on's.

**The states are derived, never stored.** `state(job)` reads `running` while the job's lock is held, which a
`LOCK_SH | LOCK_NB` attempt on the file finds (let go at once where it was taken), and `cancelling` while it is held
and a stop is recorded; the outcome where the record has one; and `lost` where it has none and the lock is free: a run
killed outright, or one that ended without saying how. Where the record is gone (not written yet, or the job dropped)
there is no state: `state` is `None`, no such job, as `find` says, whatever the lock, so a job read through a handle
taken before its drop is, from the record's unlink on, neither `running` with an empty record nor `lost`, which a test
holds with the drop made by another process. A record that is there and cannot be read is read as `{}`. It asks the
lock before it reads the record: a run writes its outcome before it ends, so a lock found free means any outcome is
already there, and a run that ends just after its record is read reads `running`, never `lost`, which a test holds. A
job the reading process made itself is read from what it holds, a shortcut that saves a probe and no rule: a probe
reads the same, since a `flock` belongs to its open file description and closing a second descriptor of a lock file
does not let the first's lock go, as experiment 40 measured on Linux, on NFSv3 and NFSv4.2, where Linux emulates
`flock` with POSIX locks, as on a local disk (macOS's half is not measured yet); a test probes the run's own lock from
its own process. No module of `runs/` keeps a pid to decide a state, reads a process's start or reads the process table
for one, which a scan in the test holds.

**Events.** `emit(kind, machine, text, **data)` hands one event, with its time, to each listener the run registered
with `listen` (the live view and the text log), in order and in the run's own process. The kinds are `stage`, `step`,
`report`, `waiting`, `next` and `end`; another is refused, `child` among them, since a child the run started is its
registry's (`children.py`). Every string an event carries goes through
`text.plain_text`, and a `Secret` anywhere in one is refused before anything is written or heard. What an agent and
`logs` read is also kept in the record: a `waiting` (empty text: nothing), a `next` with its `matter` (an empty line
removes it), a machine's `end` with its outcome and the stage its last `stage` event named, and the run's `end` (no
machine) with its outcome and exit status. Only the process that made a job emits its events, listens to them or drops
it.

**Reading and dropping.** `find(jobs_dir, job_id)` takes only what the pattern `job_id` holds, sixteen hex digits,
and `listed(jobs_dir)` gives every job newest first. Each passes over a folder whose `job.json` is not there yet, or is gone, and a job that vanishes
while it is read is never an error. A run refused at its locks leaves no job, and its drop never fails the refusal it
comes before: `drop(job)` removes `job.json` first, under `store.locked` as every change is, so a writer that holds the
record (a `cancel` recording a stop) lands its change before it and one that comes after is refused, and readers pass
over the folder from then on; then it closes the lock; then it removes the folder, leaving what it cannot remove, since
a folder with no record is no job. Removed while the lock file is still open, the file would be renamed `.nfsXXXX` on
NFS and the folder would stay. `drop` returns `None` where no record of the job is left, and otherwise one line that
says where the record is left and why, for the refusal to say: a record left whole is a job that reads `lost` for good,
a run that never ran. Only a record already gone is no error of the unlink's, and whether one is left is read after the
folder's removal. `tests/unit/test_aw_job.py` holds it all, each state made by a run in a child process: a SIGKILL
reads `lost` with nothing written after it, a child that outlives its killed run holds no lock, and the record is
unlinked while the drop holds the folder's lock.

**A run's children: `children.py`.** Every process a run starts carries the job's marker, `AGENT_WAREHOUSE_JOB`, the
name today's MCP jobs set, with the job's ID: whatever reads it matches the ID, never the name alone. `start` gives it
whatever environment it is handed; `unmarked(env)` takes it away, for the one kind of child that must not carry it, a
shared SSH master, which other runs may ride after this one ends. Only the process that made a job marks a child with
it. `start(job, argv, own, env, stdin, stdout, stderr, kind, pass_fds=())`
is the one way the engine starts a child, a coroutine over asyncio's own:

- `own` is `group`, a process group of its own, so that the terminal's Ctrl-C reaches the run alone; or `session`, a
  session of its own, its standard input `/dev/null`. Standard input is always named: a child in a group of its own
  that read the terminal would be stopped, and one in a session of its own would take what the person types.
- The child gets the marker whatever environment it is handed, and a master loses it. Before it runs, SIGINT, SIGTERM,
  SIGHUP and SIGQUIT are put back to their defaults and unblocked: a process starts with what its parent ignored still
  ignored, and a run started under `nohup` or with `&` ignores SIGINT.
- Every descriptor is closed in the child but its standard streams and those `pass_fds` names, which is how a pipe is
  handed over on purpose. A descriptor of one of the run's locks, or any duplicate of one (the same file), is refused
  with the lock's name before anything starts: the kernel lets a lock go only once every descriptor of it is closed,
  so a child that held one would keep it past its run's end. The locks are read from `HOLDERS`, one function of each
  module of `runs/` that takes a lock, which gives the descriptors it holds them by.
- The kinds are `ssh`, the SSH runner's bounded and held children, a forward among them; `ansible`, layer 1's
  `ansible-playbook`; `program`, a program of the computer's own (`rsync`, an opener, the editor); and `master`.

The children a run started are kept by kind, in memory, for the run's end (`attached.py`): `start` enters each, and
`live(job, kind)` gives those still running, in the order they started. A master is entered nowhere, since no run ever
ends one.
Nothing is written to a file, and nothing reads the process table. `tests/unit/test_aw_children.py` holds it, with real
children that report what they were given, and holds four rules by scans of the new engine's code: a child process is
started only through this module and the SSH runner, by any of Python's ways; `close_fds=False` appears nowhere; no
process is forked without an exec (`os.fork`, `os.forkpty`, `pty.fork`, a `fork` start method or context), since a
forked child keeps every lock until it execs; and `pass_fds` and `set_inheritable(..., True)` appear only in `start`.

**A run's short-lived directory: `rundir.py`.** A run's generated files, the layer-1 child's configuration, its
inventory and its variables, go in one directory of the run's own. `make(job)` makes it with `tempfile.mkdtemp` under
the per-user temporary directory (`TMPDIR` where it is set, as on macOS, else `/tmp`), at mode 0700 whatever the umask,
named `aw-run-`, the job's ID, a dash and mkdtemp's letters, and writes its path into the job's record as `directory`;
where the record cannot take it, the job dropped, the directory is removed again. It is never made inside
`XDG_RUNTIME_DIR`, compared by the real paths of both: systemd removes that directory at the person's last logout
while a detached run may still live, so where the temporary directory lies inside it, the run's goes in `/tmp`. The ID
in the name tells which job left a directory, and is in the command line of every child that reads a file there.
`made(job)` gives it back to the process that made it, and `path_for(job, name)` the path of one plain file name there,
each writing nothing: the module makes and removes
the directory and writes no file in it. Only the process that made a job makes its directory, one a run, or names a
file in it. `remove(job)` deletes the directory with what it holds, says whether anything was left, and is safe to
call twice: the run removes the one it made, even once its job is dropped; any other process removes only the one the
record names, held to the form `make` gives it (absolute, the job's ID in its name, a real directory of this account's
and not a link), and never while the job's lock is held, since a living run's directory is its own, nor where the job
is no job by the time its state is asked (its run dropped it since the record was read, and removes what it made
itself). No secret is ever written there: a secret travels on standard input or stays in memory, as today, and every
workflow's tests search the directory for the secrets they gave (`aw_kit.searched_for`). No socket is made there
either: a shared connection lives in the one control directory. `tests/unit/test_aw_rundir.py` holds it, with a scan
that finds no write in the module.

**The locks: `locks.py`.** Two runs are kept apart by the kernel's file locks, `flock` on files of the state folder's
`locks/` (`paths.locks_dir`), each opened in this module alone at mode 0600, never through a link, and kept open for as
long as it is held. As Python opens every descriptor, no child inherits one, and `children.start` refuses one by its
name (`HOLDERS` reads `locks_held`). The kernel lets a lock go when its process ends, however it ends, so nothing is
ever released by hand and nothing is left to clear; no pid and no process's start is read to decide a lock. That rests
on one rule: **a lock file is never removed**, nor `locks/`, nor the state folder that holds it, and every cleanup keeps
`locks/`, a few empty files. A `flock` is held on the file, not on its name: once a file is unlinked under its holder,
the next run makes a new one of the same name and takes the same lock beside the first, so two runs would act on one
machine. Nothing in `runs/` removes one.

| Lock file | Taken |
|---|---|
| `fleet` | Shared by a run that names machines; exclusively by a run that names none, by every run under `run_lock: fleet`, and by `exclusive` |
| `machine-<host>@<name>` | Exclusively, one for each machine a run names: `<host>` is the Proxmox host's name, or `machines` for an adopted machine, which `hosts/model.py`'s `RESERVED` keeps from every host; `@` is in neither name's pattern. A host and a name exist before the VM does, so a run that makes `alpha` and one that configures it take one lock, from before the VM exists to its deletion. The name is folded to lower case, so `Alpha` and `alpha` are one lock on every disk, as they are on macOS's: it only ever refuses more |
| a short section, `<word>-<host>` | Waited for, by `section`: `nextid-<host>`, the next free VMID asked of the host with the clone sent onto it, exclusively; `template-<host>`, the host's template, exclusively around a build, a rebuild or a collection and shared around a clone. Their words are `lock_sets.py`'s, and no other is taken |

`take(job, kind, machines, policy)` takes a run's locks once, for the job this process made: the kind is `machines`
(the fleet's lock shared and each machine's, or with no machine named the fleet's exclusively), `fleet` (the fleet's
exclusively, whatever is named) or `none`, as `lock_sets.locks_for` works it out from the table; the machines are
`(host, name)` pairs, a machine named twice taken once; the policy is the setting `run_lock`'s, `machine` or `fleet`,
under which every run that takes a lock takes the fleet's exclusively, today's one lock (`policy_of` holds it to the
two). Nothing waits: each lock is tried with `LOCK_NB`, and all or none: on the first refused, what was taken is let go
and `Held` is raised. They are tried in the order of the files' names, whatever order the machines were given in, **so
that of two runs over overlapping machines one always goes on**: the run refused at a lock was refused by a holder that
is past it, so a chain of refusals ends in a run that went on, where `deploy a,b` beside `deploy b,a`, each in its own
order, would each take its first machine and both be refused at their second. A test starts the two with a pause after
the first lock and finds one through every time. A run writes its job's ID into each file it holds exclusively, one
line at offset 0 by one `pwrite`, never truncated, since on NFS a truncate costs about 3 ms a lock (experiment 40); a
reader takes the first line and believes it only for a job that still runs by its own lock and names the lock in its
record, the names of the locks a run holds being written into its record (`fleet:shared`,
`machine-pve@alpha:exclusive`). So a refusal names the holder's job, its command, when it started, and the line that
cancels it, printed by the printer:

    error: alpha on pve is held by job 3f2a9c4d5e6b7a80, `aw deploy alpha`, started Sun Nov 8 14:02:10 2026 UTC: `aw cancel 3f2a9c4d5e6b7a80` stops it

A refusal never fails on what it reads: where the holder's record cannot be read, names a job that is starting or
ending (one refused a moment later under all or none, which experiment 40 met in up to 13% of its refusals), or gives a
start no clock can say (not finite, or out of the platform's range), it says the lock "is held by a run that is
starting or ending: try again". The fleet's lock held shared, refused to a run that needs the whole fleet, names no one
holder: runs on machines of this fleet are under way, and `aw logs` lists them.

`exclusive(state, command)` is for a verb that is no run but must have the fleet to itself for one write, `host <name>`
and `vault --rotate`: it takes the fleet's lock exclusively without waiting, writes the command as typed in place of a
job's ID, for a person who reads the file, refuses with `Held` where any run holds the fleet's lock, and lets it go at
the block's end. A refusal never names that command: the record stays after the write ends, and the fleet's lock is
also held for microseconds by holders that write no record of their own (the probe of `changing_run_lives`, a run in the
moment before it writes its job's ID), so a run refused while the fleet is held with no running job named is told that
"the fleet is held for one write, or by a run that is starting or ending: try again", with no line to cancel it.
`changing_run_lives(state)`, which `clean` asks, says whether any run holds the fleet's lock, by an exclusive attempt
let go at once where it is taken; a run whose kind is `none` holds none and is not seen.

`section(ctx, name, shared=False, machine=None)` is an asynchronous context for one short section: it takes the
section's lock and, where another run holds it, waits, with a `waiting` event that names the section (and the machine
it is about, or the whole run), and once the lock is taken clears that wait alone: the clear names the section, and
the record clears only a wait of that section and that machine, never another machine's or a later one's. The wait ends
once the run is stopping (`asyncio.CancelledError`), so a section is entered outside any shield: a Ctrl-C sets the
run's stop inside a shield too, and would end the wait the shield was there to outlast. The lock is let go when the
block ends, however it ends, a block that raises among them. **The sections nest one way: `nextid` outside
`template`** (the next free VMID taken, then the template's section shared around the clone inside it), never the
other way round: they have no time limit, and two runs that nested them in opposite orders would each wait for the
other until a person stopped one. It waits by trying again every 20 ms, since a thread blocked in `flock` cannot be
cancelled, so the sections keep no queue: a stream of clones can keep a template's build waiting, which the sections'
shortness makes acceptable. The writes of the managed SSH config and the known-hosts file, of the overrides and of the
vault are short sections too, under `store.locked`, which their owners take. `tests/unit/test_aw_locks.py` holds it,
each run a child process of the test: two runs on two machines go side by side, a second on one machine is refused
naming the first, a run that makes `alpha` and one that configures it take one lock, and so do `Alpha` and `alpha`;
twenty runs at once on one machine let exactly one through, two runs over the same two machines given in opposite
orders never both refuse, a holder killed with SIGKILL holds nothing, the sections wait, end their wait on a stop, clear
only their own and are let go when their block raises, and `take` of twenty machines beside 400 lock files stays under a
tenth of a second; with scans that the module keeps no pid, removes no file, never truncates a record in any spelling
and names a machine's lock from its host and name alone, that only it and `job.py` take a `flock` (by any import of it)
or a POSIX lock, and only it names the locks' folder (by `paths`' names or the literal `locks`), that nothing of the
new engine asks `F_GETLK`, which does not see a `flock` on Linux, and that no module of the new engine but
`cli/before.py` and `schema/words.py` names `RUN_LOCK` or `ATTENDED` in code.

**Each verb's locks: `lock_sets.py`.** Which locks a run of a verb takes is a column of the table of verbs (16.2,
`locks`), one of four words: three are the kinds `take` acts on (`KINDS`), and `words` says the kind is worked out from
the words given, by the function of this module that has the verb's name (`BY_WORDS`). `locks_for(verb, values)` reads
the verb's row and returns its kind, from the verb's values as the dispatch hands them, and `runs.run` hands it to
`take` with the machines the draft names, in the words' order:

| Verb | Kind |
|---|---|
| `login`, `code`, `destroy`, `release` | `machines` |
| `init` (every mode), `install` | `fleet` |
| `deploy` | `words`: `--check`, a dry run of configuration, takes `none`; any other run `machines`. So `deploy --only template`, which names no machine, takes the fleet's lock exclusively and is refused while any run lives, as today's one lock refuses it; a `deploy <name>` that finds while it runs that it must rebuild the template takes no more lock of a run for it, and waits in the template's section instead |
| `host` | `words`: a host named, to switch to it or to remove it, `fleet`; the list, `none` |
| `vault` | `words`: `--rotate`, `fleet`, so that no run writes the vault under the key it read before a rotation; the list, a put, a removal and the editor, `none` |
| `status`, `logs`, `settings` (a read or a write), `help`, `cancel`, `mcp` | `none` |

`host` and `vault` are no runs (the table's `changes`): where their kind is `fleet`, their own handler holds the fleet's
lock for its one write through `exclusive`, and makes no job; `runs.run` refuses a verb whose run is no job before
anything is made. `control` is the add-on's, and adds its row with the fleet's lock when it is built. The two sections
are named here once, `nextid` and `template` (`SECTIONS`, in the order they nest), and `section_name(word, host)` gives
the file a run passes to `locks.section`; `section` takes no other word. The other short sections, each write of the
managed SSH config, of the known-hosts file, of the overrides and of the vault under `store.locked` by their owners, are
why `settings` and a vault's put take no lock of a run, as today. No word of the command line makes a run take no lock.
There is no desk: two runs that sign in at once go side by side, since a port another run holds makes Claude's forward
fall back to the paste, as today, two pages open in two tabs, and a device code copied over another is also printed at
the terminal. `tests/unit/test_aw_lock_sets.py` holds the table of every verb and its kind, a function for every `words`
row and a row for every function, `locks_for` over each verb's words, `vault --rotate` and `host <name>` refused through
`exclusive` while a run holds the fleet's lock, `deploy --only template` refused while a run lives and `deploy --check`
going on beside one, the writes under `store.locked` going on beside a run and waiting for each other, and that the
table holds no `control` and no word for a run that takes no lock.

**A run, and its end: `run` and `attached.py`.** `runs.run(draft, body)` is the one entry a verb whose run is a job
calls, at a terminal or detached, and refuses any other verb before anything is made: it makes the job its `Draft`
describes (`create`'s arguments), which takes the job's own lock, makes the run's directory, takes the locks the table
gives the verb (`lock_sets.locks_for` over the draft's `values`, which are always given, then `take` with
`lock_machines` and `lock_policy`), and hands the job to `attached.run(job, body)`. A run whose locks are refused, or
whose directory cannot be made, drops its job, as a run that never began, and where the drop leaves the record the refusal's sentence, or the
error's note, says where. From before the job is made until the ladder's handlers are set, the signals the ladder takes
are held off and then given to it, so that a Ctrl-C, a hangup or a kill in that window ends the run as the ladder says,
never leaving a job that reads `lost`; a ladder that cannot be built (its loop not made) drops its job and removes its
directory the same way. `attached.run` runs the body, a coroutine function, in the command's own process on one
`asyncio` loop, and gives it a `Context`: the job, for the SSH runner and `children.start`; `cancelled`, an event a
workflow checks between its steps or waits on; `stopping`, a `threading.Event` set with it and whenever the run is to
end, which a blocking call in a thread is handed to stop on (the Proxmox client's `follow`, §16.5), and a phase in a
shield is not; `shield(reason)`, for a phase that cannot be cancelled, such as a deletion already sent; `typed(fd)`, a
question's read; `emit`; `live(kind)`, the children it still holds; and the run's directory. Its handlers take SIGINT,
SIGTERM and SIGHUP, but one ignored on the way in, as `nohup` or a shell's `&` leaves one, which stays ignored. The
ladder:

| The end | What happens | The record | The process |
|---|---|---|---|
| Ctrl-C, once | `cancelled` and `stopping` set; the tree under the run remembered with `proc.snapshot`, the interrupts held off while the table is read; the stop recorded, `{"by": "terminal", "at": …}`, so the job reads `cancelling` through its grace (one a `cancel` recorded is kept); SIGINT to each Ansible child's group, but one started inside a shield still up, which gets it once that shield is done, since the line says the phase finishes first; a line on standard error that the run is stopping, or that it stops once the shielded phase is finished; a question up ended there. Once the body and every child it started have ended, what is left is ended at once; 30 seconds after the Ctrl-C (`STOP_GRACE`, today's MCP jobs' grace) whatever still runs is ended and the body cancelled, but a shielded phase is waited for | `cancelled`, `Ctrl-C` | exits 130 |
| Ctrl-C, twice | the tree ended at once from the signal's handler itself, then `Abandoned`, a `KeyboardInterrupt`, raised there while the loop runs, so that a step stuck in it cannot hold the end | `cancelled`, `Ctrl-C twice` | exits 130 |
| SIGHUP or SIGTERM | the tree ended with that signal from the handler, as above; what ignores it is killed after five seconds (`SETTLE`), after a Ctrl-C too, never at the Ctrl-C's grace | `cancelled`, the signal's name, exit 128 and its number, over a Ctrl-C that came first | its handler put back to the default, and dies of it |
| An error the body did not handle | the tree ended | `failed`, the error | the error raised again |
| The body returns | what it still holds of its children ended | `succeeded` for 0, `cancelled` for 130, else `failed` with the status | exits with the status |
| SIGKILL | nothing | none: the job reads `lost` once its lock is free | killed |

On every path but SIGKILL, the terminal's settings are put back as the run found them, its echo among them (where
anything changed them, SIGTTOU held off); every child of the registry has ended or been ended before the loop closes,
since asyncio kills an `ansible-playbook` it still holds when its loop closes and leaves the playbook's detached task
workers running; the run's directory is removed; and the exit record is written last, with the signals held off and
then taken as notes only, so that a late one changes nothing but what the process dies of. "The tree" is everything
under the run's process, ended by `proc.end_under(pid, remembered, signum)`, which signals every process under a process
and what was remembered of it, by each one's start, with their groups, and never the process, its group or the
caller's: today's `end` signals the root too, which here is the run. A shared SSH master daemonises out of the tree, so
no end reaches it; no child subreaper and no systemd scope are adopted, since under a subreaper one run's end would cut
another run's connection through the host. The ladder is built against stand-ins; experiment 37 tries it with real
children, and if a foreground run cannot end its tree by itself, `runs.run` is where a small watcher would go.

**Nothing holds the process once its record is written.** The engine's blocking calls (`asyncio.to_thread`, a
`run_in_executor` of the default) run on the ladder's own executor, the loop's default: each call on a daemon thread of
its own, started with the signals the ladder takes blocked (a thread starts with its maker's mask), and never joined,
nor known to the interpreter's exit, which joins a `ThreadPoolExecutor`'s threads and would hold a cancelled run's
process for the rest of a call, up to `follow`'s ten minutes. Where one of them is still in a call once the record is
written and the terminal put back, or any thread Python would wait for at its exit, the process ends at once: its
streams flushed, an error the body did not handle said as Python says one it ends on, then `os._exit` with the status.
With the signals blocked in every thread of its own, the kernel hands a signal to the run's thread alone, so holding the
interrupts off while the table is read holds them for real; on macOS asyncio waits for each child in a thread of its
own, which the ladder does not start, and there the handler's taking notes once the run is closing is what keeps the end
right.

**A question.** `typed(fd)` reads what the person types on the loop (`add_reader`), never in a thread, which would hold
the process as above: a line in a terminal's cooked mode, a key in raw mode. While it waits a question is up, and the
first Ctrl-C ends the run there, its read raising `asyncio.CancelledError` as the run stops, so one Ctrl-C at a prompt
ends the run with 130 rather than leaving it to wait for an answer through the grace; the line that the run is stopping
then begins on a line of its own. A prompt that reads the terminal raw, where Ctrl-C is a byte, sends the run SIGINT for
it. No question is begun once the run is stopping, and one descriptor is read for one question at a time. The run's
questions, a line, a menu or a hidden answer, are built on it.

**What Python and asyncio say.** While the ladder runs, an `Abandoned` that Python drops where it cannot raise one (a
fork's hook) is dropped unsaid (`sys.unraisablehook`), since the run's end is taken by its wake, and the loop's
exception handler keeps asyncio's own reports, a callback's error or a task destroyed pending, off the person's terminal
(the run's text log takes them once there is one). Work that will not take its cancel, still pending once the run has
settled, gets one line of `aw`'s own and is kept until the process ends.

`tests/unit/test_aw_attached.py` holds it, with runs in child processes whose bodies start four stand-in Ansible
children (one that ends on SIGINT, one that ignores it, one with a grandchild in a session of its own, one that records
each signal it gets, with when, and ends on none): every way out but SIGKILL, a body's own `KeyboardInterrupt`,
`SystemExit` and `CancelledError` among them, leaves its exit record, its status, the signals each child got, and
nothing running, read once the run has ended and before the test's own cleanup; the ways walk every signal whose
handler is the ladder's as a running body finds them and every outcome. Ctrl-C twice ends the tree from the handler
while the body holds the loop; a call in a thread holds the process no longer than the ladder takes, on a daemon thread
that blocks the signals, and (on Linux) a SIGTERM while the table is read waits for the read; an Ansible child started
in a shield has its SIGINT once the shield is done; a SIGTERM after a Ctrl-C whose body returned ends the run within
`SETTLE`, recorded as itself; one Ctrl-C at a question ends the run at a pseudo-terminal, cooked or raw; a ladder that
cannot be built leaves no job; a record that cannot be written is said; asyncio's reports stay off the terminal; the
grace, the stop recorded, a shielded phase, a signal ignored on the way in, and the echo at a pseudo-terminal after
each end.

## 16.9 `fleet/` — the fleet's files, the settings, the secrets and the fleet's mark

`lib/claude_vm/fleet/` is where a fleet's files and its state go. `paths.py`, below, says where they are; the hosts as
folders, the typed table of settings and its overrides, the one encrypted file of secrets, and the fleet's ID and mark
go here beside it. It imports `schema/`, and nothing of `engine/`, `cli/` or `control/`, and no pydantic.

**Where a fleet is: `paths.py`.** `home(program_root, environ)` works out the fleet's home for the program at
`program_root`, and returns it with the state folder and the key file derived from it. It is standard library only,
reads at most two `client.yml` files, and creates, writes and removes nothing: the code that writes makes folders at
`DIR_MODE` (0700) and files at `FILE_MODE` (0600). The home is the first of these.

| Program | Home | State folder | Key file |
|---|---|---|---|
| `AGENT_WAREHOUSE_HOME` set | that folder | `<home>/state/` (the installed program's own home: as below) | `<home>/vault-key` (the same) |
| A checkout (a `justfile` and a `.git`, a folder or a worktree's file) | `.aw/` at its top | `.aw/state/` | `.aw/vault-key` |
| An installed release (`release.json`, told before its `.git`, since a release is a clone of its tag) | `${XDG_CONFIG_HOME:-~/.config}/agent-warehouse/` | `${XDG_STATE_HOME:-~/.local/state}/agent-warehouse/` | `${XDG_DATA_HOME:-~/.local/share}/agent-warehouse/vault-key` |

A program that is neither is refused, and told to set `AGENT_WAREHOUSE_HOME`: a copy with no `.git` never drives the
installed program's fleet by default. A checkout drives a fleet of its own, so a new worktree has none and changes
nothing until `init` runs in it, which is the isolation the checkout guard keeps today
([§15.3](decisions.md#153-the-command-line-is-just-not-make)).

Under the home, `client.yml` is the computer's own settings and never travels, and `fleet/` is what a person copies to
a second computer: `hosts/<name>/`, `settings.yml`, `machines.yml`, `vault.age` and `fleet.json`. `OWN` names the four
names at the top, `client.yml`, `fleet`, `state` and `vault-key`, and `BENEATH_STATE` the names beneath the state
folder that `jobs_dir`, `locks_dir`, `logs_dir` and `status_cache` return: `jobs`, `locks`, `logs` and `status`.
`control_dir()` is the one shared control directory of every SSH connection, Ansible's `~/.ansible/cp`, which today's
hop and `just status` use, and the same for every fleet on the computer.

**One way from one home to the other.** A line `fleet_home: <path>` in the default home's `client.yml` points a
checkout at the installed program's home, or the reverse. It is followed once: a `client.yml` there that points again
is refused, and under `AGENT_WAREHOUSE_HOME` no pointer is read. The returned home names the `client.yml` it followed,
so a changing run can say which fleet it drives. The value is one absolute path, plain or quoted, or `~/`; anything else
YAML would read as more than a string is refused.

**The state and the key go with the home.** Whichever way the installed program's home is reached, by the variable or
by a pointer, and however its path is spelled, its state folder and its key file are the installed program's own: a
home is told to be that one by the real path of both, every link read, never by their text, so a `~/.config` that
is a link into a dotfiles folder changes nothing. Two programs that drive one fleet share its jobs and its locks and
open its vault with one key, and two fleets share neither. The key file is never in `fleet/`, which a person copies
and may publish, nor in the state folder, which can be made again and which a cleanup may remove. In a checkout it is
at the top of `.aw/`, and for the installed program in the data folder, never in the config folder that people keep
in a public repository. Any other home keeps its key at its top, `<home>/vault-key`: a person who names a home
chooses where its key lives. A home inside the installed program's state folder is refused, naming
`AGENT_WAREHOUSE_HOME` (or the `fleet_home` that named it), since a cleanup may remove it. What a person backs up is
`fleet/` and, apart from it, the key.

**No cleanup reaches the key.** Only `paths.py` and `vaultkey.py` name the key file, `vault-key` (an option such as
`--vault-key-file` is not its name). Outside `fleet/` no module names `paths.KEY` or `paths.OWN`, no removal
(`unlink`, `remove`, `rmtree`, `rmdir`) reaches `key_file`, `KEY` or `OWN`, and a module reads `key_file` only where
the literal list `READ_THE_KEY` in `tests/unit/test_aw_fleet_paths.py` names it with its reason. The rest is held where
it can be: each change that removes files keeps the key byte for byte in its own test.

**The folder the roles share.** On a machine the product configured, today's roles keep a sign-in's files in
`~/.config/agent-warehouse/`, the installed program's home by the same name. Their names are reserved in
`RESERVED_ON_A_MACHINE` (`claude-login.*`, `gh-login*`, `gh-device-login`), which share nothing with `OWN`, and
`paths.py` never reads, writes or names one as its own. The code that runs on a machine names that folder as
`paths.ON_A_MACHINE`, or its literal, in `guest/` alone and always joined to one reserved name; nowhere else names the
fleet's folders but `paths.py`.

**Tests never reach the developer's.** An autouse fixture in `tests/unit/conftest.py`, `aw_home`, sets
`AGENT_WAREHOUSE_HOME` to a folder in each test's own directory, a directory of the session's own stands in before it
for a module's run, and `harness.PASSED_ON` passes it to every run. The session reads the names `paths` owns in the
developer's fleet folders before the first test and after the last, from their metadata alone, and fails if one was
made, changed or removed. Those folders (`developers_folders`) are the installed program's three, the checkout's own
`.aw/` and, where the suite runs from a worktree, the main worktree's (git's common directory), and the
`AGENT_WAREHOUSE_HOME` the developer exported, read before the session replaces it. In a checkout's `.aw/` only
`client.yml`, `fleet/` and the key are watched, never `state/`: the developer's own `aw` run from that checkout writes
its jobs there while a suite runs, and state can be made again. No file there is opened, so no `fleet_home` is
followed. `tests/unit/test_aw_fleet_paths.py` holds the homes, the pointer, the key's place, the names,
the scans for who names the folders and the key, and the guard, a child `pytest` among its tests.

## 16.10 `guest/` — the helper, the system layer's inputs, layer 2

`lib/claude_vm/guest/` is where what the engine does inside a machine goes: the helper, which is sent to the machine
and never installed there, and the inputs of the system layer, which Ansible converges as a child with built-in modules
only, and layer 2, the rest of the work, in plain code. It imports `ssh/`, `schema/` and `fleet/`, and
nothing of `engine/`, `cli/` or `control/`, and no pydantic. A plugin of that Ansible child is the one code of the new
engine that imports `ansible`. It lives under `guest/ansible/`, where the child's own configuration names it.

## 16.11 `control/` — what exists only with a control VM

`lib/claude_vm/control/` is where the add-on goes: everything that exists only with a control VM, its services and its
page.
The base, the command line alone, writes nothing here. It may import the rest of the new engine, and it is the one
subpackage that may import pydantic. Nothing outside it imports it.
