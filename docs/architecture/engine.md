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
  reaches is in `REUSED`, or in a second list, `REACHED`, with the reason it is brought in. Today `REACHED` holds five
  modules the new engine's own code never imports: `records` and `refs`, on which `catalogue`, `editors`, `hosts`,
  `status` and `vpn` build their records; `run.model` and `run.recipes`, which `tui.run` draws; and `vault`, an
  ansible-vault file read without Ansible, which `hosts` and `status`'s settings read. None of them is `ansible`, by any
  road.

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
the secret on: the transport (16.7), for a request's header or form, and the SSH runner (16.6), for a child's standard
input. The engine's tests, `tests/unit/test_aw_*.py`, run alone
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

The developer's recipes (`lint`, `test`, `docs`, `vagrant`, `clean`) keep their own patterns in the `justfile`, which
are not the command line's. `tests/unit/test_aw_schema.py` holds each pattern to values it takes and values it refuses,
most of them those today's tests of the `justfile` give the same pattern, each value it takes refused with a newline
after it, and holds that importing the module loads nothing outside the standard library.

**The table: `words.py`.** `VERBS` is the one table of the command line: every verb a person types after `aw`, in the
order help lists them, today's thirteen and `release` and `cancel`: the base's alone, since `control` is the add-on's,
which adds its row when it is built. Each row is a `Verb`: its group and its sentence; its `Words`, what the usage calls
one, whether it takes several, the name the handler receives them by, their pattern and help; its `Option`s; the codes
of the rules between its options (`deploy.4`, `check.names.1`), which the one check runs in that order; its handler, as
`module:function`, or none while no change has built the verb, and then the card that builds it; the locks a run of it
takes, one of `machines`, `fleet`, `none` or `words`, where the kind depends on the words given (16.8, `lock_sets.py`);
whether a run of it is a job, with its own record, text log and locks; and the tools of the agents' server that run it,
or why none does. An option has its spellings, its kind (`value`, `flag`, `repeatable` for `--set`, `counted` for `-v`),
the name of its pattern, the name the handler receives it by (the long option in lowercase with `_` for `-`, and `for_`
for `host --for`), its help, the name help gives its value (`--host ON`), its fate for an agent's tool: a property of
the tool's schema, a value the server always gives, or refused with the reason; where two tools run one verb (`settings`
and `settings_set`), a fate for each; and `until`, the card that builds what it does, which help and the command
reference leave it out until (`install`'s options of the installed program, H14's and H15's). `BEFORE` holds the words
given before any verb: `--attended`, and `--detached-run`, which no person types. `--detach` is no row's option: the
parser gives it, with the one sentence `DETACH`, to every verb whose run is a job, and the agents' server starts every
such run detached.

**A command a sentence names: `Line` and `said`.** Every sentence of the new engine that names a command, the table's
help, a rule's refusal, the check's own and the Proxmox client's and the vendors' errors among them, names it as a
`Line`, a verb and its values (any of them `PLACEHOLDER`, which the printer writes as the name help gives it), in a
field of the sentence, and never as text. `said(sentence, filled)` fills each field, a `Line` with the line the printer
of a command writes for it (16.3), so that every command a person reads is the program as it was started, spelled as the
printer spells one; `LINES` holds the commands the table's own sentences name (`aw host`, `aw help <recipe>`,
`aw deploy alpha --tags claude_code`). `said` imports the printer when a sentence is said, and `schema/` imports nothing
of `cli/` when it is imported, which every command does; `pve/` and `vendors/`, which import nothing of `cli/`, say
theirs through it too.

Today's verbs keep today's words and help, with `aw` where a sentence said `just`; `mcp` takes the vault's key file as
`--vault-key-file`, and `init --host` names a host of the fleet alone, held to the pattern of a host's name. Beside the
table, `EXITS` names the exit status of every verb but `status` (0 done, 1 refused before anything ran, 130 cancelled)
and `STATUS_EXITS` those of `status` (today's 0, 1, 2 and 3, and 3 for a read a person cancelled, as today's `status`
gives it); within each, no meaning has two numbers and no number two meanings, but `status`'s 3, which today means a
read that failed and one a person cancelled, and a script may read. The table holds no option whose value is a secret: a
secret reaches a verb from a file it names or from the terminal. Two options take free text a person may type one into,
`--set` and `settings --value`, and the run layer that reads them refuses a secret's name or value there and never says
it back. Nothing is generated from the table or into it. `tests/unit/test_aw_cli_words.py` holds the table's rows, the
tools that run each verb, every option and word of a verb a tool runs a property of the tool's schema but where a ruling
the test names says otherwise, the two values of free text, the exit statuses and the module's imports.

**The one check: `check.py`.** `check(verb, values)` is the check every entry runs on a verb's values: the parser of the
command line, the agents' server through `aw`, and the add-on's broker on JSON. It holds each value to its kind and to
the pattern the table names, with `full`; then runs the rules between options the verb's row names, in that order; then
holds the verb's words to their number. It returns nothing or the one sentence of the first refusal: an option the verb
has not got, a value of the wrong type, or one outside its pattern, each naming the option and its help and never the
value, and the form a value may take after the help (`patterns.py`'s `form`, as `[pattern: '...']`, which help prints
too); then a rule's sentence; then a second word where the verb takes one, or any word where it takes none. Each command
a sentence names, the verb refused (`command`), where its options are listed (`help_of`) and those of the rules, is the
printer's, through `said`. It takes values as the parser gives them and as JSON with exact types, and coerces nothing: a
value option's is a string, a flag's a boolean, `--set`'s a list of strings, `-v`'s a whole number, the words a list
where the verb takes several and a string where it takes one, or a list where the parser was given more than one, which
the rules see before the number is held; `detach` is a flag of every verb whose run is a job. `check_before` holds the
words given before any verb the same way, and `formed` is the check but for the rules between options, which the printer
of a command (16.3) holds a line to.

**The rules between options: `rules.py`.** Each rule between options of today's command line is one plain function with
its code (`deploy.4`, `check.names.1`) and today's sentence: `Rule(code, verbs, sentence, breaks, lines)`, where
`breaks` takes a verb's values and returns nothing where they keep the rule, or what fills the sentence's fields where
they break it, a value or a `Line`, and `lines` are the commands the sentence names whatever the values; `Rule.check`
gives nothing or the sentence, said, so each command in it is the printer's line (`aw code <name>`,
`aw deploy --only configure --tags claude_code`). They are today's rules of `deploy`, `code`, `settings`, `destroy`,
`host`, `help`, `init` and `mcp`, said before any check or tool, and the three argument checks of `tools/check` that are
more than one pattern: `host --for`'s VM names, split at its commas, the 2000 characters of `--repos`, and the `.` and
`..` of `--project`; `RULES` holds them by code, and `run` runs the codes a row names in order and stops at the first
sentence. No rule repeats a value's own pattern: `tools/check`'s other checks of one value (the size, the repositories'
spelling, the project's characters and length, the session, status's sections, every other VM name) held the JSON the
`justfile`'s scripts join, behind `just`'s pattern, and are the pattern's alone here, and so is a token file's name,
named relative to the working folder whatever its path. A sentence says a value back only where today's does, and then a
value that has met its pattern first, as `just` holds a value to its pattern before a recipe's script runs. What reads
more than the values is no rule here but its verb's own: the fleet's files (`vault`'s, and `deploy`'s VM name another
host's alias records), the file system, `claude` and the branch (`mcp`'s), and a terminal (`host --remove`'s question).

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
machine, and for every command a sentence names (16.2's `said`): `argv(verb, values, ...)` gives its words and
`line(verb, values, ...)` the one string a person pastes, quoted for a POSIX shell, `sh`, `bash` or `dash`, with
`shlex.quote` wherever such a shell would read a word otherwise. Both read the one table and nothing else, and write a
line as a person types one: the program as it was started; the words given before the verb, in the table's order; the
verb, `deploy`'s whole chain as its one verb; the verb's words straight after it; its options in the table's order,
`--detach` last as the parser gives it, a value after a space (`--only configure`) and as `--long=value` only where it
starts with `-` or is empty, so the parser reads it as a value, a flag bare, `-v` repeated in one word, `--set` once
for each value; and last the host's flag, `--host <name>`. Parsing the line gives the same values back.

The program is `aw` where `aw` on `PATH` is the checkout's launcher, else what the person typed to start it (`./aw`):
the launcher says which in `AGENT_WAREHOUSE_STARTED_AS`, which `main.py` takes out of its environment at once, so that
nothing a run starts inherits it, and hands to `started_as`; with nothing said, as for `tools/aw` run directly and the
agents' server's runs, it is `aw`, and so is a word no line could print as it is. A line that shows a command's form
gives a value or a verb's words as `PLACEHOLDER`, written as the name help gives it and never quoted
(`aw destroy <name>... --confirm CONFIRM`). The values are the parser's or a handler's: `default_vm` is said by naming
no VM, and a value that is off (a flag not given, a count of 0, an empty list) is not said. Each is held as the one
check holds it but for the rules between options (`formed`), and one it refuses, a word that starts with `-`, or a value
or word that holds a character of Unicode's categories Cc, Cf, Zl or Zp (a C1 control, a bidi override, a line
separator), which a terminal would act on or not show as it is, raises `ValueError` with the check's kind of sentence,
which never says the value; a rule is the run's to say, so a line may name a run that one refuses, as `deploy`'s
question names the stage before its answer meets the rules.

`host_flag` is the host a line names. Every caller of a verb whose row has the shared `--host` (`deploy`, `code`,
`login`, `status`, `destroy`) gives it where the values give no `--host`: a host's name, or `""` where the fleet has
one host or the line names none, as a command's form said before any fleet is read does; `None` there is a
`ValueError`, so no caller forgets it. Only the verbs whose `--host` runs them against a host take one: those, and
`settings`, `vault` and `init`. `refused`, keyword-only with no default, is said by every caller: where no run has taken
the values, a refused run's or a question's, the two values of free text a person may paste a secret into, `--set`'s
and `settings --value`'s, are printed as the name help gives them; the table holds no option whose value is a secret.

It imports the standard library and `schema/` alone. No module of the new engine's subpackages builds a string that
names a command outside it: a syntax-tree scan reads the whole text of every string, constants and f-strings alike,
for `aw` or `just`, as a word of its own and from `./` too, followed by a verb of the table, a recipe of the
repository's own, a formatted value, a field of `str.format`, an option or a space that ends the string; but
`dispatch.py`'s `not_built`, whose `just <verb>` is today's program, which the flip takes out. Only the printer and help
name its `PROGRAM`, and every call of `line` or `argv` says `refused`. What the scan cannot see is a command joined at
run time from pieces that name none (`'aw' + ' cancel '`, a list joined): only the printer writes the program's name,
which is how such a line is made.

**Help: `help.py`, and the command reference: `reference.py`.** `help` is a verb of the table whose handler is
`help.py`'s `main`, so `aw help` runs as any verb does, and `aw` alone is `aw help`. It prints two levels, as
`just help` does, from the one table, whole from the first day: a verb no card has built yet is listed and helped as
today, and refused only when it runs. `aw help` lists every verb in the table's order, `help` first and then each group
under its name, each with the words it takes and its doc; then the options given before any verb, but the hidden
`--detached-run`, since the one check's refusal of such an option sends a person there; then a closing paragraph, with
`-v` and `--set`, each named on the verbs whose row has it, and `LOG_DIR`. `aw help <verb>` prints the verb's usage line
as today's command reference writes a recipe's (`aw deploy [<name>...]`), its doc, each option with the name of its
value and its help (`-t, --tags ROLES`), `--detach` last on a verb whose run is a job, and then its words with their
help. After the help of each option and of the words the table holds to a pattern comes the form a value may take,
`[pattern: '...']`, as the refusal of a value outside it says it, but for the words of a verb that refuses any; an empty
default, which says nothing, is not printed. An option the table marks with `until` is left out of help and the
reference until that card. The listing shows `[options]` after a verb that has any. A second word is refused by the one
check (`help.1`), and a word that is no verb by help, in the check's sentence, which never says it. The usage lines are
help's own, the program as it was started first; a whole command line it prints, for a person to paste (the closing
paragraph's example, `aw host`, the table's sentences' commands), comes from the printer. A usage line shows a verb's
word without brackets only where the verb needs one, `code` and `release`, which help names, since the table says how
many words a verb takes and not whether it needs one. `reference.py`'s `commands()` writes the tables of the command
reference from the same functions, in the form `tools/docs.py` writes them from the `justfile` today: a table for each
group of verbs, `help` aside, with a row for each verb, each of its options and its words, and one of the options given
before any verb, each cell escaped as `tools/docs.py` escapes one. Nothing calls it before the cutover, which points
`tools/docs.py` at it. `help.py` imports the standard library, `schema/` and the printer; `reference.py` those and
`help.py`; and `aw help` loads nothing of the engine.

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
lacks the packages. It tells the program how a person runs it again, in `AGENT_WAREHOUSE_STARTED_AS`: `aw` where
`command -v aw` is this launcher by its physical path, else what was typed to start it. It hands the program no job's
marker it inherited: past `--install-env`, which reads it, `AGENT_WAREHOUSE_JOB` is unset, so the program's own
environment, as the system shows it, carries none; and `main.py` takes one out of its environment at its start too,
for `tools/aw` run directly, so that a child a run starts outside `children.start` (16.8) carries no other job's ID.
`install_env` ends, where `aw` on `PATH` is not this launcher, with one sentence of how to put it there
(`ln -s <checkout>/aw ~/.local/bin/aw`), and writes nothing outside the checkout for it.

`tests/unit/test_aw_cli_parse.py` holds all of it: every option of every verb in both spellings, the refusals and that
none says a value or has a usage block, `--detach`, `default_vm`, `RunWords`, in `run_words` and in the program, the
dispatch's refusals, what a refusal imports, and the launcher run in checkouts the test makes, with a stand-in `uv`,
through links, from folders whose version manager picks another Python or that hold a planted module, and with a
`PYTHON` that is no virtualenv's or cannot be run. `tests/unit/test_aw_cli_rules.py` holds the rules and `deploy`'s
question: every rule is named by the verbs whose rows run it; each refusal of a rule or a habit, and each of the
parser's own, is run through the launcher, the words, status and sentence of each written there; the check answers
alike as words and as JSON; the question is asked at a pseudo-terminal, typed and with none; and no file of the tree
names the recorded command line of v2.7.0, which no test holds `aw` or the `justfile` to.
`tests/unit/test_aw_cli_inverse.py` holds the printer: for every verb, its words, each option alone and beside them and
each pair of options the rules allow come back from the line through the parser and the one check, and no verb or option
lacks such a case; every example line the test gives, of every verb, comes back from its line, before the habits and
after; a line's form; an awkward value, each character a POSIX shell reads otherwise, a backslash and a trailing one,
two values of free text on one line, an empty value and a name that starts with a digit survive `/bin/sh` and `bash`; a
value a terminal would not show as it is is never printed; a value that is off is not said, and a placeholder is the
name help gives it; the host's flag; a value of free text is never said back from values no run took, and every call
says `refused`; a value the check refuses is never printed; the program as it was started, in this process and through
the launcher; and, by scans of their syntax, that no other module of the new engine's subpackages names a command
anywhere in a string, that no sentence of the rules or the table holds one as text, that only the printer and help name
its `PROGRAM`, and that `schema/`, `pve/` and `vendors/` import nothing of `cli/` when imported.
`tests/unit/test_aw_cli_help.py` holds help and the reference: every verb's help shows its options and words in the
table's order; `aw help` lists every verb and no recipe of the repository's own, the options before a verb, and its
closing paragraph with `-v` and `--set`; the form of every value the table holds to a pattern, and no empty default; an
option of a later card's left out; `[options]` in the listing; through `tools/aw` and the launcher, `control` refused as
no verb; what each imports; and that the reference gives a row for every verb but `help`, every option but a later
card's and the words, and escapes a cell as `tools/docs.py` does.

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
  it, and nothing but `pin.py` reads a `Pin`'s context (`tests/unit/test_aw_vendor_trust.py`).
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
  client's user agent to a configuration's path carried none.
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
  returned with no lines, and a failure is `TaskFailed` with none, or `Stopped` once the run has stopped, the
  `TaskFailed` its cause; lines read before then are not the last, and none is kept. A poll that fails while the run
  goes on raises its own error - the transport's `TimedOut`, where one poll had no answer within its read's wait with
  time still left, its `Unreachable`, or the client's - and the task may then still run on Proxmox: the workflow
  follows it again by the `Task` it holds, or reads the VM. `stop` is the run's
  `Context.stopping` (§16.8), a `threading.Event`, since a workflow calls the client in a thread: it is read before each
  poll and waited on in place of each pause, so once the run stops, at a Ctrl-C or wherever it is to end, `follow`
  raises `Stopped` within a pause's first moment, never asking again, and the task goes on on Proxmox, which
  `Stopped`'s sentence says with the task's kind and VM; a poll already sent is never cut, and ends within its own
  limit, and one that fails once the run has stopped (its client closed under it, whose transport then sends nothing
  more: 16.7) is `Stopped` too, never the poll's own error. A phase that must finish, a deletion inside a shield,
  passes no `stop`, and is followed to its end.

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
the fleet's pool through its clone's `pool`, which needs `VM.Allocate` on the pool. A token whose role is on its pool
alone could add no guest it did not already hold, and `Pool.Allocate` would let it rewrite its pool's comment, the
fleet's mark, and delete the pool once empty. Two functions read it: `needed(on)`, the privileges the table needs on
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
the task ends or during the read - a success with no lines and a failure `TaskFailed` with none, or `Stopped`; a
follow whose run stops between polls, during one, and before the first, each `Stopped` with no poll after it and the
task still running on the fake, and a poll that fails once the run
has stopped; a deletion's parameters, `destroy-unreferenced-disks` alone and `purge` only where asked for, read from the
fake's log; and a UPID with a `/` in a part of its own `Malformed`. Its rules: a `PUT` or a `POST` to a configuration's
path with no `digest`, or an empty one, is refused before it is sent, `harness.fake_fleet`'s end fails a test where
the fake was sent one,
and every method that sends one sends `digest` (a syntax-tree test of the module); a configuration's write sends only
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
the `ssh` children a run holds. It imports `schema/`, `runs/` for the one way a child is started, `fleet/` for the modes
of a file it makes, and `secret.py`; of today's modules what `REUSED` names (`checks`, `route`, `store` and `text`), and
nothing of `status/`, which makes TLS contexts; nothing of `engine/`, `cli/` or `control/`, and no pydantic.

**A target: `runner.py`'s `Target`.** A frozen record of everything a connection is made from, each value held before
any child starts: the machine's identity (`checks.HOST_KEY_ALIAS_RE`, `agent-warehouse-<its SMBIOS UUID>`); the one IPv4
address it is reached at, the one value a machine supplies, held with `ipaddress.IPv4Address` and never `0.0.0.0`,
loopback, multicast, the reserved range or broadcast; the account and the port; the known-hosts file its key is checked
against, the product's own `~/.ssh/agent-warehouse.known_hosts` (`KNOWN_HOSTS`), one file for every fleet beside the
person's own, which the editor's alias names too, or a scratch file; the one shared control directory, of plain
characters, where a machine whose socket's path would pass 86 bytes, the most macOS binds (a long home), takes no shared
connection (`shared`: each of its connections logs in alone, `ControlPath=none`); an identity file or
none (this computer's `claude_ssh_identity_file`); the hop through the Proxmox host or none (`Hop`, the route settles
it: route.py's `ProxyCommand` to the machine's own address, and its connection's name); the person's own options for the
machine (the setting `vm_ssh_options`); whether this is a first use; and the `ssh` program. `machine()` makes one. The
Proxmox host is its second form, `proxmox_host()`, for root SSH and as the hop: no identity, its account, port and
address from the host's file, its key from this computer's, and its settings held by `route.hop_settings`, the rules the
hop is made by, whose socket's name it shares.

- **A person's own options.** `vm_ssh_options` is a mapping of `ssh`'s option names to values, given as `-o Name=value`
  after the product's options: `ssh` keeps the first value it is given, so a person's option changes whatever the
  product leaves, a `ProxyJump` or a `ProxyCommand` among them, and never a value it pins. What the product writes
  itself is refused by name - `HostKeyAlias`, `UserKnownHostsFile`, `CheckHostIP`, `UpdateHostKeys`, `BatchMode`,
  `ControlPath`, `ControlMaster`, `ForwardAgent` (`claude_ssh_forward_agent`'s), `IdentityFile`
  (`claude_ssh_identity_file`'s), and `HostName`, `User` and `Port`, where and as whom it connects - and
  `StrictHostKeyChecking` may be given only as `yes`. So is what would undo what every connection holds
  (`UNDOING_OPTIONS`): `StdinNull`, which would leave a secret never arrived while the call reads `Done`;
  `ForkAfterAuthentication`, which takes `ssh` out of its group and the run's reach; `SessionType` and `RemoteCommand`,
  another command or none; `ForwardX11`, `ForwardX11Trusted` and `ForwardX11Timeout`; and `PermitLocalCommand` and
  `LocalCommand`, a program run here for each connection. A machine given a jump of its own takes no hop.
- **A first use.** A machine's first connection - one the run created, one being adopted, or the first a changing verb
  makes from a computer that has filed no key for it - takes the key with `StrictHostKeyChecking=accept-new` into a
  fresh scratch file of its own, under the machine's identity as `HostKeyAlias` and never by its address, and never into
  the product's file or the person's own; the caller files the key it took, and every later connection checks the
  product's file with `yes`. A first use is a connection of its own (`ControlPath=none`): a master of the identity and
  the address that still lives would carry it, and `ssh` would write no key. The Proxmox host's key is taken at first
  use into the person's own `known_hosts`, as `ansible.cfg`'s `ssh_args` take it today.

**A host key: `hostkeys.py`.** A machine's key is filed under its identity in the product's own known-hosts file, and no
function there takes an address. `parse` holds one line of a public key: its type one of `checks.KNOWN_KEY_TYPES`, its
body the base64 (`checks.KNOWN_KEY_RE`) of a key of that very type, its comment left off; an option or a name before
the type, a second line and a control character are refused, and nothing of the line is said back. `line` is the
known-hosts line under the identity. `kept` reads the product's file as `ssh` reads it - a name, names and patterns
split by commas with `*`, `?` and `!`, or a name `ssh` hashed under `HashKnownHosts` - and returns the keys filed under
the identity, passing over a comment, a marker's line and a key of a type it does not file. `file` adds the line under
`store.locked`, the lock on the file's folder that every writer through `store` takes, reading the file only once it
holds it, so that two writers at once lose neither line: only where no key of the key's type is filed for the identity
(the three ECDSA curves count as one), and where another is, it raises `Changed`, which names both fingerprints and the
`ssh-keygen -R` that takes the old one out, the file left byte for byte. It never replaces or removes a line, so a
deleted machine's key stays under an identity no machine made later has. For a first use, `fresh` makes an empty
scratch file, 0600, in the run's own directory, named for the identity, which is the file a first use's `Target`
names; `taken` reads back the one line `ssh` wrote there - three words, under the identity by name or hashed - and
refuses a file with no line, two, or one under another name, an address among them, with nothing filed. The caller
shows the key where it asks a person (an adopted machine's question) and files it with `file`, by name, never by the
hash `ssh` wrote. Neither ever reads or writes the person's own `known_hosts`.

**The command line.** `argv(target, command)` is `ssh`, then the product's options, then the hop's, then the person's,
then `-l <account> -p <port> -- <address>` and the command joined with `shlex.join`; every value is one word and none
passes a shell. No `-F`: the person's own `~/.ssh/config` is read, as today, where a key, an agent or a jump of theirs
is named. The product's options, in order: `checks.vm_ssh_args` as it stands (the known-hosts file, `CheckHostIP=no`,
`UpdateHostKeys=no`, `HostKeyAlias` the identity, `ForwardAgent=no`; for the Proxmox host `UpdateHostKeys=no` and
`ForwardAgent=no` alone), `ControlPath=<the shared directory>/<checks.vm_control_name>-%r-%p` (`none` for a first use),
`ControlMaster=no`, `BatchMode=yes`, `ForwardX11=no` and `PermitLocalCommand=no` (pinned, where a person's
`ForwardX11 yes`, or Ubuntu's own `ForwardX11Trusted yes`, would hand the machine this computer's display),
`StrictHostKeyChecking=yes` (or `accept-new` for the two forms above), `ConnectTimeout=10`, `ServerAliveInterval=15`,
`ServerAliveCountMax=3`, `PreferredAuthentications=publickey`, `ClearAllForwardings=yes`, `RequestTTY=no`,
`LogLevel=ERROR`, and the identity file where there is one. `BatchMode=yes` on every `ssh` the engine starts means a key
that wants a passphrase fails in one sentence and never waits at a terminal it cannot reach. Every connection
authenticates by public key alone, the Proxmox host's too: today's Ansible connection also offers GSSAPI and host-based
authentication, and those are added only if someone asks. `ControlMaster=no` means a child rides a master the engine
opened in the shared directory (`connections.py`'s `ensure`), or connects alone where none lives, and is never a master
itself. The hop's own `ssh`
keeps `LogLevel=INFO`, which route.py pins because it reads `ssh`'s words. `ansible_vars(target)` gives layer 1's
Ansible child the address, the port, the account, the program and `ansible_ssh_common_args` holding exactly the same
options in the same order, so that its `ssh`, `sftp` and `scp` ride the same master and a jump of the person's reaches
the file transfers too.

**A bounded call.** `run(job, target, command, stdin, limits, env, started)` is a coroutine. The child is started
through `children.start` as an `ssh` child of the job's run, in a process group of its own, with `env` and the job's
marker, entered in the run's registry; `started` is handed asyncio's process object, never a bare pid. Its standard
input is written, drained and closed: bytes, text, a `Secret` or a list of them, a `Secret` revealed in `_bytes` alone,
so a password or a code is never in a word, a variable or a file. Its standard output is read a line at a time under
`Limits`: a bound for one line, one for the whole, a deadline, and a time to say anything at all; its standard error is
kept to a fixed size, and read to its end so the child never blocks on it. On a breach the child's group is killed, its
`ProxyCommand` with it. The result is `Done(status, lines, said)`, or `Cut` (a line past its bound, more than the whole,
a line with no end), `Silent`, `TimedOut` or `Unreachable(said, lines, heard)` (status 255), each line and `said` made
plain (`text.plain_text`). The default limits are today's, `status/reach.py`'s, held equal by the test: a line of 16
MiB, 45 s, 20 s to say anything, 64 KiB of standard error, and four of the longest lines in all. A call cancelled by its
run ends its child's group too.

- **255 is `ssh`'s or the command's own.** `ssh` exits 255 where it could not reach the machine or lost it, and also
  where the command on the machine exited 255 or was ended by a signal, which `ssh` has no status to pass on for.
  `Unreachable` keeps the lines the command printed and whether the machine was heard at all (`heard`: any byte on
  standard output), so a 255 after output reads as a command that ended without a status, and a caller that tries an
  unreachable machine again never takes a command that ran for one never reached.
- **The local `ssh` is ended, never the command on the machine.** A breach, a cancel and a held child's `end()` end the
  `ssh` here: with no terminal on the session, `sshd` sends the command no signal, so one that neither reads its input
  nor writes goes on after `TimedOut`, `Cut`, `Silent` and `end()`, as under today's Ansible. A caller whose command
  changes something bounds it on the machine (`timeout`) or reads its state again.

**A held child.** The sign-ins hold their own `ssh` children. `hold(job, target, command, env, started, deadline)`
starts the same command line the same way and returns a `Held`: `send(line)` writes one line to the child's open
standard input, a `Secret` revealed only there; `lines()` yields what the child prints while it runs, a line at a time
under the same bounds; `close()` closes its standard input; `end()` ends its group, `SIGTERM` and then `SIGKILL`;
`result()` reads what is left and says how it ended. Its deadline is the caller's; the run's end ends every held child
through the registry of `children.py`. `forward(job, target, specs, env, started, deadline=)` is a held `ssh` with
each `-L` the caller gives, `ExitOnForwardFailure=yes`, `ClearAllForwardings=no` and `AddressFamily=any` (each loopback
family bound, as today's forward does whatever a person's settings say), made as a connection of its own
(`ControlPath=none`), since a forward asked of a shared master would outlive the run on it; its standard input is
`/dev/null`. Its deadline is required, and its far end is the command `sleep <deadline>`, rounded up, in place of `-N`:
the local end ends it first, at its deadline, by its caller or at the run's end, and where the run was killed outright
(SIGKILL, an OOM kill), which ends nothing it holds, the far end's `sleep` ends the forward by itself and lets this
computer's port go. Nothing is recorded on the machine.

**The one control directory and the route: `connections.py`.** Every shared connection, a machine's master and the
hop's, lives in one control directory that every run on the computer shares, `~/.ansible/cp`, which `fleet/paths.py`'s
`control_dir()` names: short, as a socket's path must be, the account's alone, outside every repository, and the one
layer 1's Ansible child and the status probe use. `Connections(control_dir, env)` is made once a run and refuses a
target or a hop whose socket is anywhere else. It holds the directory to its owner by one `lstat`: a link, a directory
of another account's, and one its group or others may write in are refused (`Unsafe`, with the `chmod 700` to run),
since anyone who may write there could put a socket at a machine's path before its master and be handed what every child
sends it, secrets included; one not made yet is made 0700 by the first master's open. A machine's socket is
`checks.vm_control_name` of its identity, its address and its hop; where its path would pass `route.SOCKET_MOST`, the
machine takes no shared connection, `ensure` opens nothing for it, and the run says so once (`notes`). Three rules hold
every master. A machine's master is opened only by `ensure`, which starts it with `children.MASTER`, the one kind of
child that carries no job's marker and is entered in no registry, in a session of its own. The hop's master is
`route.py`'s: `ensure_hop` opens it with the engine's own environment, and so may any connection through the hop whose
`ProxyCommand` finds none live - a bounded child, a first use, a forward - since that `ProxyCommand` carries
`ControlMaster=auto`, and such a master carries that child's job's marker, so nothing may tell a master from a run's
child by the marker alone. No run ever ends a master, the hop's or a machine's: it ends by itself, `ControlPersist`
after its last client (`60s` for a machine, today's), and two runs never ride one machine's master at once, since each
holds that machine's lock. And `-O exit` is never sent, which would cut another run's sessions; `-O stop` only through
`route.hop_stop`, for the hop. The route is `route.py`'s, called as it stands, its `ssh` children started in a process
group of their own (`process_group=0`, which today's callers do not pass), so that a terminal's first Ctrl-C, which the
run takes as "finish the step", never kills one and has its silence read as an answer, and `route`, `ensure_hop` and
`held` are coroutines that run it off the event loop, so that the run takes the Ctrl-C at once. `route(setting, host)`
settles it for the Proxmox host's own `Target` - its account, or `vm_ssh_jump`'s, its port, key and `ssh` - by
`route.hop_settings` and `route.route_settle`, as `{route, setting, hop, policy, addresses}`, and raises
`route.HopRefused` as it is, which names `vm_ssh_route: direct`; under `auto` only where the rule would go through the
host, and where the `ssh -G` that says where `ssh` dials for the host was ended by a signal, which settles nothing
(`signalled_settles=False`, where today's callers settle from the address as written). `target_for(job, machine,
settled, given)` gives a machine's `Target` the person's own options for it and, where the route goes through the host,
the hop's `ProxyCommand` to its own address (`route.vm_hop`, `-W <address>:%p`), unless it brings a jump of its own: a
`ProxyJump`, a `-J` or a `ProxyCommand` other than `none` in `vm_ssh_options`, or in what `ssh -G` resolves for its
address from the person's own `~/.ssh/config`, read once a run for each machine. `held(target, settled)` asks the
machine's own `ssh` which proxy its whole line takes (`route.ssh_shown`, `-G -F /dev/null`) and refuses any other
(`route.vm_route_refusal`) before anything connects; a jump only the person's configuration gives is the one the line
cannot outrank. `ensure_hop(settled)` is `route.hop_held`, and a hop that cannot be used raises `Unusable` with
`route.login_said`'s sentence in the new engine's names (the key this computer's `ssh_identity_file`), so the run stops
and nothing dials a machine. `ensure(job, target)` makes sure of a machine's master before anything rides it: `-O
check`; where nothing answers, a socket file nothing listens at removed (never one whose check ran out its time), and
the master opened once, the runner's options with `ControlMaster=yes` and `ControlPersist` in place of
`ControlMaster=no`, and `-N`, ended where `ssh` says the socket exists already; then checked once more. Never for a
first use: a master opened before the key is filed would carry later runs with no key checked. `first_use(job, target)`
makes a machine's `Target` one where the product's file holds no key under its identity (`hostkeys.kept`), its scratch
file fresh in the run's own directory (`rundir.made`): a changing verb's first connection to a machine calls it, files
the key it took, and only then asks `ensure`; `status`, which takes no key, never calls it. Nothing in `ssh/` tries a
connection again. Until the engine settles its route once a run and holds every connection to it, `direct_only(settled)`
refuses every settled route but `direct`, by name, with the sentence that names `vm_ssh_route: direct`: the workflows
call it before their first connection to a machine, and it never dials one the rule would take through the host.

`tests/unit/test_aw_ssh_runner.py` holds it against a stand-in `ssh` that records its words, its environment and its
standard input: the options and their order, word for word, for every form; the values they hold as this machine's own
`ssh -G` shows them, a person's options among them and a person's configuration outranked; no secret in a word or a
variable; each bound's result, a 255 after output with its lines, and nothing left in the process table; a held child's
lines while it runs and a line sent later; a forward's far end ending it within its deadline after its run was killed
with SIGKILL; and four rules by
scans: a child named `ssh` is started in the new subpackages only from `ssh/`; no option is made of a value a machine
supplied but its address; `accept-new` is written in `ssh/` only for a first use and the Proxmox host; and the runner
carries no `ControlMaster` but `no`. `tests/unit/test_aw_hostkeys.py` holds `hostkeys.py`, with blobs of every type it
files and real keys of this machine's own `ssh-keygen`, whose `-l` gives each fingerprint, `-F` a name as `ssh` reads
one and `-H` one hashed: a key filed once and found, a second of its type refused with the file unchanged byte for byte,
two writers at once, a deleted machine's line kept through every call, a first use's scratch file taken and filed or
refused with nothing filed, the person's own files never read or written, and, by the signatures, no function that takes
an address. `tests/unit/test_aw_ssh_connections.py` holds `connections.py` against a stand-in `ssh` that plays OpenSSH's
control commands and its master, as `tests/unit/test_route.py` does, every socket in a short directory of the test's
own, and that runs this machine's own `ssh -G` given the test's own `~/.ssh/config`: each route settled, the hop's
command word for word, a jump of a machine's own from its options and from its configuration, the held check, a first
use decided by the key filed, its scratch file in the run's directory and no master opened for it, a master opened once
and found again, unmarked and in a session of its own, a dead socket file removed and a busy one kept, a hop that cannot
be used stopping the run with nothing dialled, a control directory that is a link, another's or writable by others
refused, a long one connecting alone, a first Ctrl-C sent to the run's group while `ssh -G` settles the route killing
no `ssh`, an `ssh -G` ended by a signal settling nothing, a master's open left out of what a run remembers, and
`direct_only`; and three rules by scans: no control command but `-O check` in the new subpackages, never `-O exit`;
nothing in `ssh/` tries a connection again; and only `ensure` opens a master, with the kind that carries no marker,
an f-string's `ControlMaster` and ssh's flags run together (`-NM`) read too.

## 16.7 `vendors/` — one HTTPS transport, Tailscale's calls, verified downloads

`lib/claude_vm/vendors/` is where the vendors' side goes: one HTTPS transport, which the Proxmox client uses too,
the computer's certificates, which it uses too where a host keeps no anchor, Tailscale's calls, and downloads held to
their vendor's signature or digest. It imports `schema/`, `secret.py` and, of today's modules, what `REUSED` names
(`text`, to make what a peer said one line, and `openpgp`, to check a vendor's signature, in `downloads.py` alone); of
other packages, `certifi`, in `trust.py` alone. It imports nothing of `engine/`, `cli/` or `control/`, and no
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
  connection could not be made or was dropped, or when the answer is 502, 503 or 504. Any other method is sent once.
  A failure once its request has left is `Unknown`, never a second send, and its caller reads the state to know.
  Before a request is written on a kept connection, one whose far end has closed it is put aside, which costs no try.
- **Closed, it sends nothing more.** `close()` marks the transport closed, never to open again: a request started
  after it is `Closed`, nothing sent, and one still under way on another thread - a call its caller let go, as a
  stopping run leaves a poll's thread - ends at its first error, never tried again and never given a new connection.
  Without the mark, the closed connection's error read as a dropped one, and a `GET` that carries a token went out
  again after its caller had stopped. `close()` closes only a connection no request is under way on. One that a
  request is being written on, or its answer read from, it **shuts down** (the system's shutdown of both ways under the
  TLS socket, never `SSLSocket.shutdown`, which takes the TLS state from under the read using it), which wakes that
  thread's read or send at once, and leaves the socket for that thread to close once its request has ended. A socket
  closed under a wait frees its number while the wait goes on: on Linux, nothing wakes a read blocked on a socket
  another thread closes, and at its limit the read takes the bytes of whatever socket or pipe took the number next and
  writes a TLS alert onto its connection. The decision is made under the lock a request takes its connection under, so
  a connection is either found in use and shut down, or found idle and closed before any request can take it; a
  connection still being made is refused as `Closed` once made. An answer read on a socket `close()` shut down is
  never handed back, since one that ends with its connection would seem to end there: a `GET`'s is `Unreachable`,
  "the transport was closed while its answer was read" (`CUT`), and any other method's `Unknown`.
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

**The proxy: `proxy.py`.** `choose(setting, environ, url)` gives the proxy for one URL, reading only the `environ` it is
given. With the setting `network_proxy` at `environment`, its default, it is the one the environment names by
`requests`' rule, the one a person behind a proxy expects of a Python program.

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
keep every byte sent to them and are sent none. It holds four rules over the new engine's modules.
Only `transport.py` imports `http.client` or wraps a socket. Only `proxy.py` imports `urllib.request`, and only for
those readers. No module names a certificate variable. And `reveal(` appears once in the transport. It also holds the
modules that hand the transport a `peer_check` to a list, `PEER_CHECKS`, each with why, found by the calls of
`Transport` that pass one: by keyword, through `*` or `**`, or as the eighth argument.

**The computer's certificates: `trust.py`.** GitHub, Tailscale and the download servers are verified against `certifi`'s
list, named by its path, the same on every system, with the file of PEM certificates the computer's setting
`tls_ca_file` names added to it, never put in its place; and so is the Proxmox API wherever its host keeps no anchor
(16.5). `files(setting)` gives the files in the order they are loaded, `certifi`'s and then the setting's where it is
set and not empty, and `context(setting)` makes the context the transport is given. Neither the system's store nor
`SSL_CERT_FILE`, `SSL_CERT_DIR` or `REQUESTS_CA_BUNDLE` is read: `tls_ca_file` is the one way to add a root, such as a
company's, and the refusal of a certificate whose issuer is unknown (`Untrusted`) says to add it there. It adds, so a
person behind a proxy that inspects TLS keeps every vendor the proxy does not inspect; what it gives up is narrowing the
vendors' trust to roots of one's own.

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
  `lib/claude_vm/trust.py`'s `anchored_context`, which `pin.py` binds alone, never the module, so that nothing reaches
  the rest of today's `trust` through its names. Today's modules that make a context are a door the names cannot see,
  so the rule holds imports too: of the new engine, only the modules `CONTEXTS` names import today's `trust` or
  `status`, which make contexts, or any module the new engine may reuse that makes one or imports one that does; no
  module takes `trust` or `status` from a module `CONTEXTS` names; and those take names of today's modules, never a
  module whole. And no module of the new engine, those two included, names the interpreter's own store.
- **What the rules cannot see.** They hold what a module writes, by any written spelling, and what it imports. A name
  built at run time (`getattr` of a string made there, `sys.modules`, `import_module` of a relative name or a
  variable), a keyword `check_hostname=False` handed to a function, and the standard library's own TLS clients
  (`urllib.request.urlopen`, `xmlrpc.client`, `ftplib`, `smtplib`, `imaplib`, `poplib`), which make the interpreter's
  ready-made context where they are called, are past them: a reviewer looks for those.

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
`set_default_verify_paths`, by any written spelling but as a type; and a call's `ssl=True`, asyncio's way to a ready-made
one), set one of its checks (`CHECKS`: `check_hostname`, `verify_mode`, `verify_flags`, `hostname_checks_common_name`),
or import today's modules that do (`TODAYS_CONTEXTS`, `trust` and `status`, which a test holds to every reused module
that makes a context or imports one that does).

**Tailscale's calls: `tailscale.py`.** `Tailscale(base_url, tls_ca_file=None, proxy=None)` is the client of Tailscale's
API at the address its caller gives (today's `tailscale_api_url`, `https://api.tailscale.com`), on the one transport,
with the context `trust.context(tls_ca_file)` makes and the proxy `proxy.choose` gave. It makes the three calls today's
release makes (`roles/tailscale/tasks/api_delete.yml`), and no other:

- `access_token(client_id, client_secret)`: `POST /api/v2/oauth/token`, the OAuth client's ID and secret as form
  fields with `grant_type=client_credentials`, OAuth's grant for a client's own credentials, which Tailscale's own Go
  client sends too. The access token it answers, which lasts an hour, is a `Secret` from the moment it is read, in a
  frame of its own that has ended before any error is raised, so that no frame a `Malformed` goes through holds it
  plain. The client secret is a `Secret`, and so may the ID be, as the vault holds it: each is a form's value the
  transport reveals, so this module adds no entry to `REVEALS`.
- `device(token, node_id)`: `GET /api/v2/device/<node ID>`, the device as a `Device(node_id, name, hostname)`, its
  MagicDNS name and its host name each made one line of plain text; `None` for a 404, a device that is not there.
- `delete_device(token, node_id)`: `DELETE /api/v2/device/<node ID>`, sent once: `True` where it deleted the device
  (200, 204), `False` for a 404, a device deleted already or in a tailnet the token does not reach.

The token of the last two is the one an OAuth client was exchanged for, or an API access token held as it is, a
`Secret` either way, sent as a Bearer token. A base address written with the API's own path,
`https://api.tailscale.com/api/v2`, as Tailscale's documentation writes it, has that path taken off, where the path put
on twice would read every device as gone already.

- **The node's ID** is what a machine reported, and an access token may delete any device of the tailnet: it goes
  into a path only once it is held whole, with `fullmatch`, to `NODE_ID`, letters and digits, the pattern of today's
  `tailscale_node_id_pattern`, so that no `/` or `..` takes the token to another endpoint. One that is not is
  `NotANodeId`, with nothing sent, whose sentence shows what was given as JSON, cut, so that a newline is seen. The
  machine chose its length: every error says an ID cut at 200 characters (`SHOWN`, today's release's cut), and the
  path holds it whole.
- **Refusals.** A 401 or a 403 is `Denied`, by its status alone; why a 403 refused a device is the release workflow's
  to say, which knows which credential the token is. Any other answer but the call's own is `Refused`, with
  Tailscale's words - its JSON's `message`, an OAuth error's `error_description` or
  `error`, else its status line's - the secrets the call sent struck from them as they came and again once made plain,
  before they are cut (`Secret.struck`), so that a peer that echoes one has none of it said. A 3xx is one, never
  followed. A 200 that is not the answer asked for is `Malformed`, and never quoted: an exchange's body holds a token.
  The transport's own errors pass as they are: a deletion that left with no answer is its `Unknown`, never sent again,
  which the workflow settles by reading the device. Each error survives a copy and a pickle (`__reduce__`), made again
  from what it was made of and saying its sentence once, as the transport's `Untrusted` and the Proxmox client's
  `Denied` and `Refused` do.
- **Not here.** Which device a release may delete (today's rules of its name), which credential is used where none is
  held (open, in the standalone resolutions' section 7), and what is said to a person are the release workflow's. No
  key is minted for a join: a join takes the vault's `tailscale_auth_key`, as today.

`tests/unit/test_aw_tailscale_api.py` runs each call against `tests/molecule/fake_tailscale_api.py`, which serves
HTTPS when given a certificate and its key (`--tls`) and plain HTTP to today's tests as before, its certificate's CA
standing in for one of `certifi`'s roots: the exchange, the read, the deletion and its 404, and each refusal. It holds
that an ID with a slash, a newline or a dot never reaches the fake (its `/fake/asked` lists every request made of the
API), that `NODE_ID` is the role's pattern by its text and by its answer for every ID tried, that a base address with
`/api/v2` reads devices, and that a certificate that does not verify sends nothing; and, through a stand-in transport,
a redirect, a body that is no answer with no frame holding the token, a long ID cut where it is said and sent whole,
each error through a copy and a pickle, and Tailscale's words echoing the client secret or a token, which no error
says.

**Verified downloads: `keys.py` and `downloads.py`.** What a machine installs that its vendor signs is held to the
signature here, on the computer, before a digest from it goes anywhere, as today's roles hold it
([§7.15](custom-code.md#715-libclaude_vmopenpgppy--a-vendors-signature-checked-here)). Their two task files are the
specification, `roles/claude_code/tasks/install_native.yml` and `roles/proxmox_template/tasks/image_signature.yml`:
their URLs, caps, first signed release and order of checks are kept, and `lib/claude_vm/openpgp.py` is called
directly, where they call it as a filter. Nothing is downloaded on a machine here, installed or imported: the steps
that do call these two functions, and never `openpgp` themselves.

- **The keys.** `keys.py` is a table of each vendored key file and the fingerprint its key is found by: Anthropic's
  Claude Code release key and Ubuntu's cloud image key, read when a key is asked for from where today's roles keep
  them, `roles/claude_code/files/claude-code.asc` and `roles/proxmox_template/files/ubuntu-cloudimage.asc`. The
  cutover moves the files, and this table is the one place that changes; `tests/unit/test_pins.py` holds it to the
  files and fingerprints it holds the vendors' to.
- **Claude Code.** `claude_release(base, wanted, *, key, tls_ca_file, proxy)` takes a channel, `stable` or `latest`,
  or a version, and returns a `Release(version, checksums)`: the SHA-256 of each platform's binary, by the manifest's
  name for the platform, read-only. A version asked for is held to the release's pattern and to 2.1.89, the first
  release whose manifest Anthropic signed, before anything is fetched. A channel's pointer, at most 255 bytes, names a
  version held the same way: it is signed by nobody and needs no signature, since whatever it names must still verify.
  Then the release's `manifest.json` and, once it is in hand, `manifest.json.sig`, each under 1 MiB; `openpgp.verify`
  with the vendored key; and only then is the manifest read, which must name the version it was fetched as.
- **An image.** `signed_digest(list_url, signature_url, file_name, *, key, fingerprint, tls_ca_file, proxy)` fetches
  the checksum list and then its detached signature, each from a URL of its own (today's image checksum and signature
  settings give the two), so a person's own signed image may name its list and its signature as it likes, on two
  hosts, a pre-signed URL's query kept; each over https only, on a transport of its own URL's host, each under 1 MiB.
  A URL is held to one `https://` address with no user, password or fragment before anything is fetched, and said
  without its query, which may carry a signature. The key is Ubuntu's cloud image key by default, a `keys.Key`, or the
  armored text of a person's own with the `fingerprint` it is found by, as today's image key and fingerprint settings
  give one. It returns the digest of the one line that names the image, read as today's role reads it, or `NotListed`
  where no line or more than one does. `listed_digest(list_url, file_name, *, tls_ca_file, proxy)` reads an unsigned
  list, an image held to its checksum alone (`sha256:<the list's URL>`), with the same line reader.
- **Bounded, and nothing unverified.** Every document is read on a transport whose `most` is 1 MiB, so none is read
  past it, and an answer's status is read before the document's own cap, so that an error page, longer than a
  channel's pointer, is said as the status it came with. A document one byte past its cap is `Oversized`, and nothing
  after it is fetched. A 3xx is an answer, never followed (`NotServed`, as is any answer but 200), and the transport's
  own errors pass as they are. Nothing is returned from a document whose signature has not verified (`Unverified`, with
  `openpgp`'s reason), decided by whether `openpgp.verify` returned, never by what a refusal says, so that a refusal
  with no words is one; and of a document only its version and digests are. What a server named is said through
  `plain_text`, cut, as JSON.

`tests/unit/test_aw_downloads.py` serves every document from `tests/molecule/fake_downloads.py` over TLS, its root
standing in for one of `certifi`'s, signed by keys `tests/unit/signing.py` makes or as the vendors published them
(`tests/unit/openpgp/`). It holds each refusal, with the fake's log of requests showing nothing fetched after it: a
document changed by one byte, a signature by another key or dated more than a day ahead, a refusal of `openpgp`'s with
no words, a version below the first signed release, a document one byte past its cap, a list with no line or two for
the image, a redirect, a certificate that does not verify, a URL that is not one `https://` address, a key that is not
one. A list and its signature on two servers, with names of their own and a query, verify, and so does a key given as
its text. It holds the release's pattern, the first signed release, the caps, the files' names and the line of a list
equal to today's roles', and one rule: of the new engine, only `downloads.py` reaches `openpgp` - by an import, by its
`openpgp` or `verify` taken from `downloads.py`, or read off a name - and only `keys.py` names a vendored key file.

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
nothing. A change reads the record strictly: where it is gone (the job dropped) or is no record (it does not parse, is
not a mapping, is nested past what Python's parser takes, or is a directory), `Gone` is raised and nothing is written;
any other error of the read (a descriptor limit, a disk's error, NFS's `ESTALE`) is raised as itself, so that `Gone` is
said only of a record that is gone or damaged, never of a run whose record is whole. A reader is never refused: `read`
gives `{}` for a record it cannot read, and `listed` passes over it. The record has no schema of its own and no event
file beside it: `events.jsonl` is the add-on's.

**The states are derived, never stored.** `state(job)` reads `running` while the job's lock is held, which a
`LOCK_SH | LOCK_NB` attempt on the file finds (let go at once where it was taken), and `cancelling` while it is held and
a stop is recorded; the outcome where the record has one; and `lost` where it has none and the lock is free: a run
killed outright, or one that ended without saying how. **Only a lock file that is gone is free**: `lock_held(job)`, the
lock's own answer, which a stop waits on since `state` reads a written outcome whatever the lock, takes
`FileNotFoundError` and `NotADirectoryError` for a free lock and raises any other error of its probe as itself (a
descriptor limit, the file's or the folder's mode, a disk's error), and so does `state`: read as free, a living run read
`lost`, and another process could act on that. A refusal at the locks says that error in its sentence. Where the record
is gone (not written yet, or the job dropped) there is no state: `state` is `None`, no such job, as `find` says,
whatever the lock, so a job read through a handle taken before its drop is, from the record's unlink on, neither
`running` with an empty record nor `lost`, which a test holds with the drop made by another process. A record that is
there and cannot be read is read as `{}`. It asks the lock before it reads the record: a run writes its outcome before
it ends, so a lock found free means any outcome is already there, and a run that ends just after its record is read
reads `running`, never `lost`, which a test holds. A job the reading process made itself is read from what it holds, a
shortcut that saves a probe and no rule: a probe reads the same, since a `flock` belongs to its open file description
and closing a second descriptor of a lock file does not let the first's lock go, as experiment 40 measured on Linux, on
NFSv3 and NFSv4.2, where Linux emulates `flock` with POSIX locks, as on a local disk (macOS's half is not measured yet);
a test probes the run's own lock from its own process. No module of `runs/` keeps a pid to decide a state, reads a
process's start or reads the process table for one, which a scan in the test holds.

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
whatever environment it is handed; `unmarked(env)` takes it away, for the two kinds of child that must not carry it,
which no end of the run reaches: a shared SSH master, which other runs may ride after this one ends, and a child meant
to outlive its run. Only the process that made a job marks a child with it, and a run carries no marker of its own: one
it inherited (a run started from an agent's job, or from a terminal of an editor a run opened) is unset by the launcher
and taken out of the program's environment at its start (16.3), so the children a run starts outside `start` - the
`ssh` that `route.py` runs for the hop and `ssh -G`, the `ps` that `proc.py` runs for the tree - carry none.
`start(job, argv, own, env, stdin, stdout, stderr, kind, pass_fds=())` is the one way the engine starts a child that
ends with its run, the SSH runner's `ssh` children among them, a coroutine over asyncio's own:

- `own` is `group`, a process group of its own, so that the terminal's Ctrl-C reaches the run alone; or `session`, a
  session of its own, its standard input `/dev/null`. Standard input is always named, and never the run's own
  descriptor 0: a child in a group of its own that read the terminal would be stopped, and one in a session of its own
  would take what the person types.
- The child gets the marker whatever environment it is handed, and a master loses it. Before it runs, every signal a
  handler can be given is put back to its default and none is blocked: a process starts with what its parent ignored
  still ignored, a run started under `nohup` or with `&` ignores SIGINT, and Python ignores SIGPIPE.
- Every descriptor is closed in the child but its standard streams and those `pass_fds` names, which is how a pipe is
  handed over on purpose. A descriptor of one of the run's locks, or any duplicate of one (the same file), is refused
  with the lock's name before anything starts, a standard stream given as `None`, which the child inherits from the
  run, among them: the kernel lets a lock go only once every descriptor of it is closed, so a child that held one would
  keep it past its run's end. The locks are read from `HOLDERS`, one function of each module of `runs/` that takes a
  lock, which gives the descriptors it holds them by.
- The kinds are `ssh`, the SSH runner's bounded and held children, a forward among them; `ansible`, layer 1's
  `ansible-playbook`; `program`, a program of the computer's own that ends with the run (`rsync`); and `master`.

The children a run started are kept by kind, in memory, for the run's end (`attached.py`): `start` enters each, with its
program's name, and `live(job, kind)` gives those still running, in the order they started. A master is entered
nowhere, since no run ever ends one. **A run's end ends `ssh`, `ansible` and `program` children, and nothing else.**

**A child meant to outlive its run**, the editor or a page's opener, is `outlive(job, argv, env)`'s, the kind
`outliving`: unmarked, in a session of its own, so that the terminal neither stops it nor hangs it up, the signals at
their defaults, its standard streams `/dev/null` and nothing else open; entered in no registry, so the run's end neither
waits for it nor ends it; and left out, with what is under it, of every tree the ladder remembers and ends
(`outliving()`). It is started by `subprocess` itself, never through asyncio, whose transport kills a child it still
holds once the transport is closed or collected. Nothing ends it; a caller that wants its status polls it.

**A child the terminal stopped.** `asking(job)` gives the job's children that the terminal has stopped, with the signal:
SIGTTIN, for a child in a group of its own, or of its group (the `ssh` of a person's own `ProxyJump`, which `BatchMode`
does not reach), that read the terminal to ask a question there, a passphrase or a second factor, or `git`'s or
`sudo`'s; or SIGTTOU, for one that wrote to it or changed its settings to ask. It reads with `waitid` and `WNOWAIT`,
reaping nothing, and a child stopped for any other reason is not one; where Python has no `waitid` (macOS before
Python 3.13) none is found.

Nothing is written to a file, and nothing reads the process table. `tests/unit/test_aw_children.py` holds it, with real
children that report what they were given (on Linux, a program that is not Python shows the kernel's own masks of the
signals ignored and blocked), and holds four rules by scans of the new engine's code: a child process is started only
through this module, by any of Python's ways, `posix`'s, a browser's opener and a module or an attribute asked for by
its name among them; `close_fds` is never anything but `True`; no process is forked without an exec (`os.fork`,
`os.forkpty`, `pty.fork`, `posix`'s, a `fork` start method or context, or one named by a variable), since a forked child
keeps every lock until it execs; and `pass_fds` and `set_inheritable(..., True)` appear only in `start`, and nothing
else makes a descriptor inheritable (`os.dup2`, `F_SETFD`, `F_DUPFD`, `os.pipe2`).

**A run's short-lived directory: `rundir.py`.** A run's generated files, the layer-1 child's configuration, its
inventory and its variables, go in one directory of the run's own, and so does a machine's first use's scratch
known-hosts file (`ssh/connections.py`). `make(job)` makes it with `tempfile.mkdtemp` under the per-user temporary
directory (`tempfile.gettempdir()`: the first of `TMPDIR`, `TEMP` and `TMP` that names a directory it can write in,
else `/tmp`, `/var/tmp` or `/usr/tmp`, and at last the folder the run started in), at mode 0700 whatever the umask,
named `aw-run-`, the job's ID, a dash and mkdtemp's letters, and writes its path into the job's record as `directory`;
where the record cannot take it, the job dropped, the directory is removed again. It is never made inside
`XDG_RUNTIME_DIR`, compared by the real paths of both: systemd removes that directory at the person's last logout
while a detached run may still live, so where the temporary directory lies inside it, the run's goes in `/tmp`. The ID
in the name tells which job left a directory, and is in the command line of every child that reads a file there.
`made(job)` gives it back to the process that made it, and `path_for(job, name)` the path of one plain file name there,
each writing nothing: the module makes and removes
the directory and writes no file in it. Only the process that made a job makes its directory, one a run, or names a
file in it. `remove(job)` deletes the directory with what it holds, says whether anything was left, and is safe to
call twice: only the process that made the directory removes it, even once its job is dropped, and what it could not
remove it tries again at the same path, whatever the record says by then; a call for a job another process made is
refused, as `make` and `path_for` refuse one, so that another process deletes nothing whatever the job's lock and
record say: a living run's directory is its own, and one a killed run left is no other run's to judge. No secret is
ever written there: a secret travels on standard input or stays in memory, as today. No socket is made there either: a
shared connection lives in the one control directory. `tests/unit/test_aw_rundir.py` holds it, with a scan that finds
no write in the module, by any of the codebase's ways (`store.replace`, a copy, a link, a rename, a folder made).

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
workers running; the run's directory is removed; and the exit record is written last, with the signals held off and then
taken as notes only, so that a late one changes nothing but what the process dies of. "The tree" is everything under the
run's process, ended by `proc.end_under(pid, remembered, signum)`, which signals every process under a process and what
was remembered of it, by each one's start, with their groups, and never the process, its group or the caller's: today's
`end` signals the root too, which here is the run. A shared SSH master daemonises out of the tree, so no end reaches it,
and its open, while it runs, is left out of what is remembered (`children.opening`), since the master it leaves keeps
its `ProxyCommand` in the open's group; a second Ctrl-C during an open ends that open, and the master it was making. A
child meant to outlive the run (`children.outliving`, the editor, a page's opener) is left out of both, of what is
remembered and of what is ended, with what is under it: no end of the run reaches it. No child subreaper and no systemd
scope are adopted, since under a subreaper one run's end would cut another run's connection through the host. The ladder
is built against stand-ins; experiment 37 tries it with real children, and if a foreground run cannot end its tree by
itself, `runs.run` is where a small watcher would go.

**A child the terminal stopped.** While the run lives it looks, every 0.2 seconds, for a child the terminal has
stopped (`children.asking`): one in a group of its own that asked a question at the terminal, which a run never answers
and where it would wait, stopped and silent, holding its step until the run's end. Such a child is said at once on
standard error, in one line that names its program and what to do (hold the key in an agent, `ssh-add`, or give a
`ProxyJump` of the person's own what it asks for in its own settings), and ended with what is under it; the step that
waited on it sees it killed, its machine's failure. Giving such a question the terminal, so that it reaches the person,
is the owner's to ask for.

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
each end; a page's opener meant to outlive the run alive and unmarked after each end; and a child that asks a question
at a pseudo-terminal said within a second and ended, its run ending with its body's own status.

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
