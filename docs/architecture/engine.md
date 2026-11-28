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

`lib/claude_vm/schema/` is where the command line goes as data. That is the patterns a typed value is held to, the
one table of verbs and options that the parser, help, the reference page, the printer of a command and the agents'
schemas all read, the rules between options, the one check, and the printer of a command itself. The patterns, the table
and the rules are literals written by hand, and no code is generated from them into another file. It imports the
standard library and its own modules and nothing else, because every command reads it, a refusal and help among them.
`patterns.py` and `words.py` import no other module of it. The check, the rules and the printer call one another, since
the printer holds a line to the check and every sentence of the check and the rules names its commands through the
printer: each of the three imports the others as a module (`from claude_vm.schema import check`), never a name from
one, so that any of them may be imported first. Any part of the new engine may import it, and does so plainly, at the
top of a file.

**The patterns: `patterns.py`.** `PATTERNS` is the one home of every pattern the command line holds a value to: one
literal mapping from a name (`vm_name`, `host_name`, `cores`, `repos`, `session`, `job_id` and the rest) to a `Pattern`,
written by hand: the pattern's inner text, with no anchor, which holds a value; its form in words, what a value may be
as a person reads it; and values it takes, as examples, with whether the form shows the first. The table of verbs and
options names a pattern for each option and for a verb's words, and nothing else in the new engine writes one: a rule or
a habit that holds a part of a value to a pattern, `host --for`'s names and each name a verb's words are split into,
holds it with `full`. One pattern no option takes: `fleet_id`, the ID the pool's mark and an adopted machine's marker
carry, a UUID in its lowercase canonical form (16.9). `full(name, value)` is the one way to hold a value to a pattern:
it holds the value whole, with `re.fullmatch` of the named pattern's text compiled once on its first use, so a value
with a newline after it is refused, and a name `PATTERNS` does not have raises `KeyError`. An option's pattern takes the
empty value where today's does, since an empty value is no value given. A rule that is more than one pattern is the
check's, never this module's: two values together, a word that starts with `-`, the cap of 2000 characters on `--repos`,
the `.` and `..` that `--project`'s pattern takes and a rule refuses. A `Pattern` is a pattern's text with its words and
examples and nothing more: there are no request records, no registry of operations and no coded refusals. A size's unit
is taken capital or small (`--memory 16g` as `16G`, `--disk 1t` as `1T`), the examples capital, where today's `justfile`
takes a capital one alone: the card that reads a size makes the two spellings one value.

`form(name)` is what help and a refusal say a value may be, after the option's or the words' help: the words and the
first example, `[a whole number from 1 to 999 with no leading 0, e.g. 8]`, or the words alone where they list the
values (`[one of template, provision, configure, ssh-config]`), where an example would be too long to print (the two
fingerprints, a session's ID) or where it would say nothing more than the words (a line of text). Where a pattern is
shared with values of another kind, an option or a verb's words of the table give an example of their own, or words of
their own, in its place (`form(name, words, example)`, from the table's `value_words` and `example`): `init --bridge`
says `e.g. vmbr1` and `--pool` `e.g. dev-vms`, never a storage's `local-lvm`, `mcp --vault-key-file` a file and not a
folder, and `release`'s word and `--confirm` one adopted machine's name, not VMs' joined by commas; an example only
where the pattern shows one, held by the pattern. It never shows the pattern itself, and nothing says "or empty": an
option not given is not given. The words are prose, with none of a pattern's own characters, and may be narrower than
the pattern, never wider, the table's own words too: they say the bounds of the digits ("from 1 to 9999999 with no
leading 0"), the characters taken where they are fewer than a name or an address could hold, and which part of a
program's printed line is the value ("the part after Fingerprint= as openssl prints it", "the second word
ssh-keygen -l prints"). Where the two differ, the pattern is what holds a value. The pattern itself is shown where it
is read as a definition: the command reference's rows (16.3).

The developer's recipes (`lint`, `test`, `docs`, `vagrant`, `clean`) keep their own patterns in the `justfile`, which
are not the command line's. `tests/unit/test_aw_schema.py` holds each pattern to values it takes and values it refuses,
most of them those today's tests of the `justfile` give the same pattern, each value it takes refused with a newline
after it, a size's unit small as well as capital; each pattern's words and examples, every example held by its pattern
and a near miss of each, written there, refused, no words with a pattern's own characters, the form as it is made from
them, an option's own words and example in it, the patterns that show no example, a set written there, and the owner's
five rows of before and after as they are worded; that the words are never wider than the text, each value a reviewer
read them as taking refused and its words holding the clause, written there, that says why; and that importing the
module loads nothing outside the standard library.

**The table: `words.py`.** `VERBS` is the one table of the command line: every verb a person types after `aw`, in the
order help lists them, today's thirteen and `release` and `cancel`: the base's alone, since `control` is the add-on's,
which adds its row when it is built. Each row is a `Verb`: its group and its sentence; its `Words`, what the usage calls
one, whether it takes several, the name the handler receives them by, their pattern and help, and side by side whether
the verb needs its one word (`needed`: `code`'s VM and `release`'s machine, which a usage line shows without brackets)
and whether the words are no word to give (`refused`: `init`'s and `mcp`'s, which refuse any), and whether an agent's
tool needs a word the terminal does not, where none would reach what nobody named (`agent_needed`: `destroy`'s names,
the default VM, and `cancel`'s job, the one job that runs); its `Option`s; the codes
of the rules between its options (`deploy.4`, `check.names.1`), which the one check runs in that order; its handler, as
`module:function`, or none while no change has built the verb, and then the card that builds it; the locks a run of it
takes, one of `machines`, `fleet`, `none` or `words`, where the kind depends on the words given (16.8, `lock_sets.py`);
whether a run of it is a job, with its own record, text log and locks; and the tools of the agents' server that run it,
or why none does. An option has its spellings, its kind (`value`, `flag`, `repeatable` for `--set`, `counted` for `-v`),
the name of its pattern, the name the handler receives it by (the long option in lowercase with `_` for `-`, and `for_`
for `host --for`), its help, the name of what its value is, in lower case (`host`, and `name=value` for `--set`), which
`placeholder` writes as `<host>` and `<name>=<value>`, what its value may be in words and an example of one, its own,
where its pattern's are of another kind (`value_words` and `example`, none for the pattern's), its fate for an agent's
tool: a property of the tool's schema, a value the server always gives, or refused with the reason; where two tools run
one verb (`settings` and `settings_set`), a fate for each; and `until`, the card that builds what it does, which help
and the command reference leave it out until (`install`'s options of the installed program, H14's and H15's). `BEFORE`
holds the words given before any verb: `--attended`, and `--detached-run`, which no person types. `--detach` is no row's
option: the parser gives it, with the one sentence `DETACH`, to every verb whose run is a job, and the agents' server
starts every such run detached. The words a verb refuses whole (`Words.refused`) have no form in help or in a refusal,
no row in the command reference, no place in a usage line and no placeholder refusal: the verb's own rule refuses any
word, `<extra>` among them.

**A command a sentence names: `Line` and `said`.** Every sentence of the new engine that names a command, the table's
help, a rule's refusal, the check's own and the Proxmox client's and the vendors' errors among them, names it as a
`Line`, a verb and its values (any of them `PLACEHOLDER`, which the printer writes as its placeholder), in a field of
the sentence, and never as text. The printer's `said(sentence, filled)` fills each field, a `Line` with the line the
printer writes for it (below), so that every command a person reads is the program as it was started, spelled as the
printer spells one; `LINES`, in the table, holds the commands the table's own sentences name (`aw host`,
`aw help <command>`, `aw deploy alpha --tags claude_code`). `pve/` and `vendors/` say theirs through it too, importing
the printer as every module does. `placeholder` gives the name help gives a value or a verb's words, as help's rows, the
command reference and a line that shows a command's form write it: lower case, in angle brackets, never quoted
(`--cores <cores>`, `--set <name>=<value>`, `aw code <name>`, `<name>...` for several).

Today's verbs keep today's words and help, with `aw` where a sentence said `just`, but where a later card changed one:
`mcp` takes the vault's key file as `--vault-key-file`, `init --host` names a host of the fleet alone, held to the
pattern of a host's name, and `settings`' word may be `edit`, which opens the settings in your editor. Beside the
table, `EXITS` names the exit status of every verb but `status` (0 done, 1 refused before anything ran, 2 a run that
began and failed, a machine of it or the whole, 130 cancelled)
and `STATUS_EXITS` those of `status` (today's 0, 1, 2 and 3, and 3 for a read a person cancelled, as today's `status`
gives it, in its run of one host and of several); within each, no meaning has two numbers and no number two meanings,
but `status`'s 3, which today means a read that failed and one a person cancelled, and a script may read. A `status`
cancelled before its read starts ends as any verb's run does, with `EXITS`' 130, as today's recipe does in its checks
before `tools/status` reads anything: a cancelled `status` exits 130 before its read and 3 during it. The table holds no
option whose value is a secret: a secret reaches a verb from a file it names or from the terminal. Two options take free
text a person may type one into: `--set`, whose values `fleet/overrides.py`'s `run_values` types and refuses a secret's
name or value in, never saying it back (§16.9), and `settings --value`, whose write is to hold its value the same way.
Nothing is generated from the table or into it. `tests/unit/test_aw_cli_words.py` holds the table's rows, the pattern
each option's value and each verb's words are held to, against a map of the test's own written by hand, both ways; the
example the form of each shows, or none, and the words of an option's own, against two maps written beside it, so that
an option a later card adds names its example by hand; an option's own example held by its pattern, shown, and not its
pattern's; `--set`'s sentence naming the placeholder its row prints; the words the trims took out of the base, each
absent, the tools that run each verb, each reason why no tool runs one said with its commands the printer's, every
option and word of a verb a tool runs a property of the tool's schema but where a ruling the test names says otherwise,
each value's name as its placeholder writes it, the two values of free text, the verbs that need their word and those
that refuse any, the exit statuses, the one pair that shares a number held with its reason, and `status`'s 3 for a read
a person cancelled read from today's `lib/claude_vm/status/engine.py` and `lib/claude_vm/status/hosts.py`, and the
module's imports.

**The one check: `check.py`.** `check(verb, values)` is the check every entry runs on a verb's values: the parser of the
command line, the agents' server through `aw`, and the add-on's broker on JSON. It holds each value to its kind and to
the pattern the table names, with `full`; then runs the rules between options the verb's row names, in that order; then
holds the verb's words to their number. It returns nothing or the one sentence of the first refusal: an option the verb
has not got, a value of the wrong type, the placeholder a line that shows a command's form writes for it, or one outside
its pattern, each naming the option and never the value, a pattern's with its help and what a value may be after it, in
words (`form_of`, which help and the reference print too: `patterns.py`'s `form` with the option's or the words' own
words and example), but for the words of a verb that refuses any, told no form; then a rule's sentence; then a second
word where the verb takes one, or any word where it takes none. A placeholder given back to `aw`, pasted from a form or sent
by an agent, is refused before its pattern is read, since a value of free text takes one (`error: aw settings --value:
<value> stands for the value: put yours in its place`, or `... <name> stands for the name ...` for a verb's words): its
sentence says `aw`'s own text, so no value is said back. The words of a verb that refuses any have none: no word could
be put in their place, and the verb's own rule refuses `<extra>` as any word. `word_not_held` is the sentence of a word
outside its pattern, which the names habit says too (16.3). Each command a sentence names, the verb refused (`command`,
the program alone for the words before any verb, which is the one name outside the printer that fills a sentence's
program field), where its options are listed (`help_of`) and those of the rules, is the printer's, through `said`. It
takes values as the parser gives them and as JSON with exact types, and coerces nothing: a value option's is a string, a
flag's a boolean, `--set`'s a list of strings, `-v`'s a whole number, the words a list where the verb takes several and
a string where it takes one, or a list where the parser was given more than one, which the rules see before the number
is held; `detach` is a flag of every verb whose run is a job. `check_before` holds the words given before any verb the
same way, and `formed` is the check but for the rules between options, which the printer of a command (16.3) holds a
line to.

**The rules between options: `rules.py`.** Each rule between options of today's command line is one plain function with
its code (`deploy.4`, `check.names.1`) and today's sentence: `Rule(code, verbs, sentence, breaks, lines)`, where
`breaks` takes a verb's values and returns nothing where they keep the rule, or what fills the sentence's fields where
they break it, a value or a `Line`, and `lines` are the commands the sentence names whatever the values; `Rule.check`
gives nothing or the sentence, said, so each command in it is the printer's line (`aw code <name>`,
`aw deploy --only configure --tags claude_code`). They are today's rules of `deploy`, `code`, `settings`, `destroy`,
`host`, `help`, `init` and `mcp`, said before any check or tool, and the three argument checks of `tools/check` that are
more than one pattern: `host --for`'s VM names, split at its commas and each held with `full` to the one pattern of a
VM's name, the 2000 characters of `--repos`, and the `.` and `..` of `--project`; `RULES` holds them by code, and `run`
runs the codes a row names in order and stops at the first sentence. No rule repeats a value's own pattern:
`tools/check`'s other checks of one value (the size, the repositories' spelling, the project's characters and length,
the session, status's sections, every other VM name) held the JSON the `justfile`'s scripts join, behind `just`'s
pattern, and are the pattern's alone here, and so is a token file's name, named relative to the working folder whatever
its path. A sentence says back no value that may be a secret, and only a value that has met its pattern first, as `just`
holds a value to its pattern before a recipe's script runs. What reads more than the values is no rule here but its
verb's own: the fleet's files (`vault`'s, and `deploy`'s VM name another host's alias records), the file system,
`claude` and the branch (`mcp`'s), and a terminal (`host --remove`'s question).

**The printer of a command: `printer.py`.** The one way the new engine writes a command line a person types, for NEXT
STEPS, `status`'s repair lines, a refusal's way out, the lines a detached run's start prints and the session notes on a
machine, and for every command a sentence names (`said`, above): `argv(verb, values, ...)` gives its words and
`line(verb, values, ...)` the one string a person pastes, each word quoted by `quoted` wherever a shell would read it
otherwise: `shlex.quote`, for a POSIX shell, `sh`, `bash` or `dash`, and a word that starts with `=` too, which
`shlex.quote` leaves bare and zsh, macOS's shell, expands to the path of the command it names. Both read the one table
and nothing else, and write a line as a person types one: the program as it was started; the words given before the
verb, in the table's order; the verb, `deploy`'s whole chain as its one verb; the verb's words straight after it; its
options in the table's order, `--detach` last as the parser gives it, a value after a space (`--only configure`) and as
`--long=value` only where it starts with `-` or is empty, so the parser reads it as a value, a flag bare, `-v` repeated
in one word, `--set` once for each value; then the host's flag, `--host <name>`; and last, in a form, its placeholders
(below). Parsing the line gives the same values back.

The program is `aw` where `aw` on `PATH` is the checkout's launcher, else what the person typed to start it (`./aw`):
the launcher says which in `AGENT_WAREHOUSE_STARTED_AS`, which `cli/main.py` takes out of its environment at once and
hands to `started_as` (16.3); with nothing said, as for `tools/aw` run directly and the agents' server's runs, it is
`aw`, and so is a word no line could print as it is (a character of the categories below, a byte of a folder's name
that is no UTF-8 among them, or a leading `-`). Every line and every sentence says it quoted, as one word a shell reads
back: a line's first word, the field `program` of a sentence, which only the printer fills and only the check's `command`
names, and the program help's usage lines and closing paragraph name, which they take from `command`. `program()` gives
it unquoted, for a child's environment, never for a line.

A line that gives a value or a verb's words as `PLACEHOLDER` is a form: it shows a command's shape, each placeholder
written as `placeholder` writes it and never quoted (`aw destroy <name>... --confirm <names>`), and so is a line made
from values no run has taken, whose free text is masked (below). A form is shown, never offered as the command to run:
a line a person is told to run, NEXT STEPS' and the session notes' (16.4), holds none. A form writes its placeholders
last: the verb's words, then each option in the table's order, whose value is a placeholder or is masked, after every
part with a real value, the host's flag among them (`aw host --remove <name>`,
`aw init --host far --address <address>`). Pasted at a shell, a placeholder is two redirections, `<name>` reading a
file named `name` and writing one named as the word after it, so a real value after a placeholder would be taken for a
file's name and the word before the placeholder handed a value; written last, none is. A form that ends in a
placeholder's `>` is no line a shell reads at all, and nor is one where a `>` meets the next placeholder's `<`: every
form but one whose one placeholder is a verb's names, `<name>...`, which ends in `...`, the name of the file its `>`
writes. That one stops at its `<` where the folder holds no file named `name`, nothing run, made or emptied; where it
holds one, the shell reads it, makes or empties `...`, and runs
`aw` with the line's other words, its real values. So the printer writes it only where the one check, its rules among
them, refuses those words (`aw deploy --tags claude_code <name>...` leaves `aw deploy --tags claude_code`, which
`deploy.7` refuses), and raises its `Unwritten`, a `ValueError`, where it takes them (`aw deploy <name>...` alone
would leave `aw deploy`, the default VM's), asking the check's `takes`, which says no rule's sentence, since a rule's
sentence names such a form. The forms it so refuses are those of the four verbs whose words are several names, the
names their one placeholder: `aw deploy <name>...`, `aw deploy --detach <name>...`, `aw login <name>...`,
`aw login --only github <name>...`, `aw status <name>...` and `aw destroy <name>...`, each with any other word the
check takes beside it (`aw status --json <name>...`, `aw login --host lab2 <name>...`); a sentence that would show one
names a real VM in its place, or another placeholder beside it (`aw destroy <name>... --confirm <names>`), or a value
the rules refuse there, and help's `aw deploy [<name>...]` is help's own. No form the printer writes leaves a whole
command, with no exception. Only the code asks the printer for a line, so a line it will not write is the code's own
fault: `cli/main.py` says an `Unwritten` that reaches it in its sentence and exits 1, never as a traceback (16.3), and
a run that ends the process at once with a call still in a thread, which `main` then never sees, says it the same way
(16.8): both through the printer's `own_fault`, the sentence and `OWN_FAULT`, that the fault is the program's own.
Given back to `aw` as a word or as JSON,
each placeholder is refused by the one check, in `aw`'s own words. The values are the parser's or a handler's:
`default_vm` is said by naming no VM, and a value that is off (a flag not given, a count of 0, an empty
list) is not said. Each is held as the one check holds it but for the rules between options (`formed`), and one it
refuses, a word that starts with `-`, or a value or word that holds a character of Unicode's categories Cc, Cf, Cs, Zl
or Zp (a C1 control, a bidi override, a lone surrogate, a line separator), which a terminal would act on, not show as
it is, or which no UTF-8 output can write, raises `Unwritten` with the check's kind of sentence, which never says the
value; a rule is the run's to say, so a line may name a run that one refuses, as `deploy`'s question names the stage
before its answer meets the rules.

`host_flag` is the host a line names. Every caller of a verb whose row has the shared `--host` (`deploy`, `code`,
`login`, `status`, `destroy`) gives it where the values give no `--host`: a host's name, or `""` where the fleet has
one host or the line names none, as a command's form said before any fleet is read does; `None` there is
`Unwritten`, so no caller forgets it. Only the verbs whose `--host` runs them against a host take one: those, and
`settings`, `vault` and `init`. `refused`, keyword-only with no default, is said by every caller: where no run has taken
the values, a refused run's or a question's, the two values of free text a person may paste a secret into, `--set`'s
and `settings --value`'s, are printed as their placeholders; the table holds no option whose value is a secret.

It imports the standard library and `schema/` alone. No module of the new engine's subpackages builds a string that
names a command outside it: a syntax-tree scan reads the whole text of every string, constants and f-strings alike,
for `aw`, as a word of its own and from `./` too, followed by a verb of the table, a recipe of the repository's own, a
formatted value, a field of `str.format` or of a `%` template, a `Template`'s `$`, an option or a space that ends the
string; and for today's `just` where it reads as a command, not as prose (`just deploy it`): before a value, a field or
an option, after `run`, a backtick or a quote, or with no word of prose before it and a recipe that ends the string or
that a value, an option or a closing mark follows. Its one exception is `cli/dispatch.py`'s `not_built`, whose
`just <verb>` is today's program, which the flip takes out. Only the printer names the program, as started or as its
constant (`program`, `_program`, `PROGRAM`), by any name a module binds to the printer or imports from it; only the
printer and the check name a sentence's field `program`; the check's `command`, the program and a verb, is used only by
the modules that refuse (the check, the printer, `cli/parse.py` and `cli/dispatch.py`) and by help, for its usage
lines and the program its closing paragraph names, each named with why (`COMMAND_USERS`), by any name a module binds to
the check, its package or the function; every use of `line` or `argv`, through any name or a `functools.partial`, says
`refused`; and a printer's line is never extended: a call of `line` or `argv`, of `said` whose sentence is one field
(`"{line}"`) or ends, but for spaces, in a field a `Line` fills (`"run {line}"`: one of `LINES`', or one given at the
call in a mapping written there), of a function of the module's own that returns one, a name bound to one in the same
function, an f-string that ends in one, what hands one on (`str` of it, a subscript or a slice of it, its `strip`,
`lstrip`, `rstrip`, `ljust`, `rjust` or `center`, any function of `textwrap` given it first, by any name), and lines
joined by a newline, which end in their last, is never an operand of `+` or `%`, an element of what a `%` template
takes, added to, anywhere in what `join`, `format` or `format_map` takes, or an argument of `print` but the last
(`print` joins its arguments with a space), is never edited by its `replace`, and in an f-string is followed by the
string's end, punctuation or a word of prose (letters alone, no verb of the table's, no word that starts with `-` before
the next punctuation), never by a field; an f-string that holds one anywhere is never an operand of `+` or `%` or an
argument of `print` but the last, and a sentence that ends in one is a line, words before it or not. Lines each on a
line of its own are whole: a `join` whose separator is a newline alone, of a list or tuple written there whose every
item is a line, or of a comprehension of lines, and a line with a newline added, the one argument of a `write`
(`out.write(line + "\n")`). And a sentence `said` says is never built at its call (by `+`, `%`, an f-string, `join` or
`format`), and in a literal one, or a constant of its module's, each field a `Line` fills (given at the call, from
`LINES`, or each field, where the call gives its values otherwise than as a mapping written there) is followed as a
line in an f-string must be; the rules' and the table's sentences are held so through the Lines they are said with.
The check's own `command` and `help_of`, which name the command a refusal is about with the option it refuses after
it, are no line in its own module: the guard of the check's `command` holds them. What the scans cannot see is a
command joined at run time from pieces that name none (`'aw' + ' cancel '`, a list joined), a path to the program
other than `./aw` (`../aw deploy`), another case, a name reached through `getattr`, `vars` or `importlib`, and a
printer's line carried where the scan of a line extended does not follow it (a parameter of a function of the
module's own it is handed to, which then extends it; an attribute; a container, such as a list a value is appended to
and then joined, or a mapping given to `said` otherwise than written at its call; another module's function; a word
of prose that is a value, as after a form, which a person who copies the word too hands the command as a value): a
line to run is the printer's line, whole, and a sentence names its command as a `Line`, which is how such a line is
made.
`tests/unit/test_aw_cli_printer.py` holds the printer: for every verb, its words, each option alone and beside them and
each pair of options the rules allow come back from the line through the parser and the one check, and no verb or
option lacks such a case; every example line the test gives, of every verb, comes back from its line, before the habits
and after; a line's form; an awkward value, each character a POSIX shell reads otherwise, a backslash and a trailing
one, two values of free text on one line, an empty value and a name that starts with a digit survive `/bin/sh` and
`bash`, and a value that starts with `=` is quoted; a value a terminal would not show as it is is never printed; a
value that is off is not said, a placeholder is the one `placeholder` writes, in a form line and a masked one alike,
and each placeholder given back, as a word and as JSON, is refused; a form's placeholders last, over the printer's
whole domain (for every verb, each field a form may give as a placeholder and each value of free text masked, alone,
beside each other field given a value or a placeholder, and beside every other field at once, refused and not, with
each host's flag the verb takes) and help's usage lines, each pasted into `/bin/sh` and `bash`, running nothing in a
folder with no file of a placeholder's name, and in one with a file of each nothing or words `aw` refuses, with no
exception, and the printer refusing only a form whose one placeholder is its names beside words the check takes, as
its `Unwritten`, which `aw` says in one sentence as its own fault and exits 1 with, no traceback, raised before a run,
in a run's body with no thread, with a call still in a thread, and in a step of a `gather` while the other waits in
one; the host's flag; a
value of free text is never said back from values no run took, and every call says `refused`; a value the check
refuses is never printed; the program as it was started, quoted, in this process, from a path a shell would split and
through the launcher, the check's sentences and help's among them; by scans of their syntax, the five guards above,
which a module planted with the roads the reviews found fails, but for the roads the scans cannot see, while lines
joined by a newline and a line written with its end pass; that no sentence of the rules or the table holds a command
as text; and that each module of `schema/` imports alone and first and nothing outside `schema/`, and `pve/` and
`vendors/` import the printer when they are imported, none of them inside a function.

## 16.3 `cli/` — the `aw` program

`lib/claude_vm/cli/` is the program: reading the command line against the table of `schema/`, the dispatch of a verb
to its workflow, help and the command reference's tables, below. `main.py` is the entry `tools/aw` runs, as 16.1 says:
it takes the program as it was started out of its environment at once, as the launcher says it in
`AGENT_WAREHOUSE_STARTED_AS`, so that nothing a run starts inherits it, and hands it to the printer of a command, which is
`schema/`'s (16.2). Every command the program prints, and every command a sentence names, is the printer's, the program
quoted as one word a shell reads back. A line with a placeholder (`aw code <name>`) is a form, which help, a refusal and
a question show; a line offered as the command to run holds none. A line the printer will not write, its `Unwritten`,
which only the code asks it for, is said by `main.py` as a refusal is, in the printer's sentence and with the words
that it is the program's own fault, and exits 1, wherever it is raised, a handler's run included: where the run ends
the process at once with a call still in a thread, so that the error never reaches `main`, the run says it in the same
line (16.8). Never a traceback.
It imports `schema/` and what it needs to answer at once. It imports `engine/` only inside the function that dispatches
a verb that changes something, so a refusal, help or a read never loads it. It never imports Ansible, which the engine
only ever runs as a child, nor `control/` or pydantic. `tools/aw` imports it, and so does the agents' server's own entry
once it is built; that server runs `aw` as a child for every run.

**Reading the words: `parse.py`.** `read(words)` takes the words typed after `aw`: first the words before the verb
(`--attended`, the hidden `--detached-run`), then the verb, then its words and options, split by
`argparse` into the table's slots for the one verb typed only. No verb is `help`, as `just` alone lists the recipes.
`argparse` is set as the design requires: abbreviations off, so a prefix of an option is an option the verb has not
got; its own help off; its errors raised rather than printed and exited on; its colours off from Python 3.14; and which
word that starts with `-` and a digit it takes for a negative number, and so for a value, is Python 3.13's rule on every
Python (`-1`, `-.5`, `-1.5`; any other is given with `=`), read where a word is parsed, each argument group testing an
option string by its own matcher, as no option of the table looks like a number. An
option is taken as `--long value` and as `--long=value`, which is how a value that starts with `-` is given, and is
given once, but for `--set`, given once for each setting, and `-v`, counted. `--`, which elsewhere ends the options, is
refused wherever it stands, before `argparse` sees it, in a sentence that says how a value that starts with `-` is
given: no verb takes a word that starts with `-`, and `argparse`'s intermixed parse would drop it and read the words
after it as options all the same. That has `argparse`'s own two exceptions, on every Python: a word with a space in it
(`--value '-1 x'`) and a lone `-` are taken as a word or a value, and each then meets its pattern. Every verb whose
run is a job gets `--detach`, from the table's column, and no other verb has it. The values, only those given, then go
through the one check (16.2). Anything refused raises `Refused` with one sentence of the project's own, which names the
option where one was wrong and never a value or a verb typed: no text of `argparse`'s, no usage block, no exit status
of its own.

**Today's habits: `habits.py`.** After the check, as ordinary code, a VM's name with `_` is taken with `-` and said in
today's words, `alpha,beta` is two names as two words are, an empty one dropped, and a verb that takes one name refuses
two. Each name a word is split into is held with `full` to the pattern the word was: the pattern takes `alpha,-b` whole,
and `-b`, `_b` or `.b` is no name, so it is refused there, in the check's sentence of a word outside its pattern
(`word_not_held`), before anything is said or asked and before any handler: the one place every verb's names pass, where
today the playbook's `vm_names.yml` refused such a name later. Where a verb that takes several names is given none, the
two meanings of no names are one field, `default_vm`: true for a whole `deploy`, `deploy --only provision` and
`destroy`, which act on the default VM; false for the rest, which act on every VM the fleet made. The default VM's name
is the handler's to read from the settings; the parser reads no file of the fleet. A stage of `deploy` given where the
names go, and neither `--only` nor `--check`, is asked about at a terminal, read from the standard input the command was
given: a menu of `lib/claude_vm/tui/choose.py` where the terminal can draw one (the stage, the VMs so named, or nothing,
which is marked as it is drawn), and today's typed question where it cannot. Each answer names the command line it runs
as the printer writes it, a value of `--set` never said back, since nothing has taken it yet. The answer meets the rules
as if typed, the one check run again on what it comes to; with no terminal, and for any answer but the stage or the VMs,
it is refused in today's sentences, and a Ctrl-C there exits 130.

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

**Help: `help.py`, and the command reference: `reference.py`.** `help` is a verb of the table whose handler is
`help.py`'s `main`, so `aw help` runs as any verb does, and `aw` alone is `aw help`. It prints two levels, as
`just help` does, from the one table, whole from the first day: a verb no card has built yet is listed and helped as
today, and refused only when it runs. `aw help` lists every verb in the table's order, `help` first and then each group
under its name, each with the words it takes and its doc; then the options given before any verb, but the hidden
`--detached-run`, since the one check's refusal of such an option sends a person there; then a closing paragraph, with
`-v` and `--set`, each named on the verbs whose row has it, and `LOG_DIR`. `aw help <verb>` prints the verb's usage line
as today's command reference writes a recipe's (`aw deploy [<name>...]`), its doc, each option with its value's
placeholder and its help (`-t, --tags <roles>`, the same `placeholder` a line's form writes, as the reference's rows
do), `--detach` last on a verb whose run is a job, and then its words with their help. After the help of each option
and of the words the table holds to a pattern comes what a value may be, in words and with an example
(`[a whole number from 1 to 999 with no leading 0, e.g. 8]`, 16.2), the option's own example and words where the table
gives them (`--bridge <bridge>` says `e.g. vmbr1`, not a storage's name), never the pattern itself, as the refusal of a
value outside it says it, but for the words of a verb that refuses any; an empty default, which says nothing, is not
printed. `--set`'s sentence names the placeholder its row prints, `--set <name>=<value>`. An option the table marks
with `until` is left out of help and the reference until that card. The listing shows `[options]` after a verb that has
any. A second word is refused by the one check (`help.1`), and a word that is no verb by help, in the check's sentence,
which never says it. The usage lines are help's own, after the check's `command`, the program as it was started and the
verb as the printer writes them; the closing paragraph names the program the same way, never cut where it is wrapped;
a whole command line it prints, for a person to paste (the closing paragraph's example, `aw host`, the table's
sentences' commands), comes from the printer. Help names no part of the program itself. Every row is wrapped at one
width for the whole of help, the terminal's columns (`COLUMNS` first, as `shutil.get_terminal_size` reads them), at
most `WIDTH` (110, longer lines being hard to read) and at least `NARROWEST` (72, below which the left column leaves
too little), and `WIDTH` where there is no terminal to ask (a pipe, a file, a test): in `aw help` each verb's row and
each row of the options before any verb, in `aw help <verb>` its doc, each option's row and the words' row, and the
closing paragraph's sentences, each by the one function that wraps, the lines after a row's first indented to where
its text starts. A line breaks only at a space, and never inside a hyphenated word, a command a sentence names (the
printer's line, the program at its start), an option and the word after it where it takes a value
(`--only configure`, `--set <name>=<value>`), a word and an option that follows it (`ansible-playbook --check --diff`),
or a form's example (`e.g. 8`): a line is longer than the width only where one such piece stands alone on it. What a row
says does not change, only where its lines break. The usage line is not wrapped, a form of the command whole; nor is a
refusal, one line on standard error that a script and an agent read whole, nor the reference. A usage line shows a
verb's word without brackets only where the table says the verb needs it (`Words.needed`: `code` and `release`), and
none for the words of a verb that refuses any (`Words.refused`). `reference.py`'s `commands()` writes the tables of the
command reference from the same functions, in the form `tools/docs.py` writes them from the `justfile` today: a table
for each group of verbs, `help` aside, with a row for each verb, each of its options and its words, and one of the
options given before any verb, each cell escaped as `tools/docs.py` escapes one. The cell of a value the table holds to
a pattern ends as help's row does, with what the value may be in words, and then gives the pattern itself, as its
definition, in code: a code span, or an HTML code element with `&#124;` for each `|` where the pattern has one, as
`tools/docs.py` writes a setting's value with one, since a `|` splits a GitHub table's cell and an escaped one inside a
code span shows its backslash on the site. Nothing calls it before the cutover, which points `tools/docs.py` at it.
`help.py` imports the standard library and `schema/`, the printer among it; `reference.py` those and `help.py`; and
`aw help` loads nothing of the engine.

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
`command -v aw` is this launcher by its physical path, else what was typed to start it; and its own sentences that say
what to run (`--install-env`'s refusal, the environment it makes first, a uv that made none) say the program the same
way, each word as the printer's `quoted` writes it (`word`: bare where every POSIX shell and zsh read it back as it is,
else quoted as `shlex.quote` quotes it, and a word that starts with `=`). It does not hold the word to the printer's
categories of characters, which POSIX `sh` cannot read. It says every line through `printf '%s\n'`, never `echo`,
whose backslashes `dash` and macOS's `sh` read as escapes: a checkout in a folder whose name holds `\n`, `\t` or `\c`
is said as it is, never as another path or cut short. It hands the program no job's
marker it inherited: past `--install-env`, which reads it, `AGENT_WAREHOUSE_JOB` is unset, so the program's own
environment, as the system shows it, carries none; and `main.py` takes one out of its environment at its start too,
for `tools/aw` run directly, so that a child a run starts outside `children.start` (16.8) carries no other job's ID.
`install_env` ends, where `aw` on `PATH` is not this launcher, with one sentence of how to put it there, that folder
being one `PATH` must name (`mkdir -p ~/.local/bin && ln -s <checkout>/aw ~/.local/bin/aw`, the checkout's path quoted
as a shell reads it back), and writes nothing outside the checkout for it.

`tests/unit/test_aw_cli_parse.py` holds all of it: every option of every verb in both spellings, the refusals and that
none says a value or has a usage block, a negative number by 3.13's rule and `argparse`'s two exceptions each held to
its pattern, `--detach`, `default_vm`, `RunWords`, in `run_words` and in the program, the
dispatch's refusals, what a refusal imports, and the launcher run in checkouts the test makes, with a stand-in `uv`,
through links, from folders whose version manager picks another Python or that hold a planted module, and with a
`PYTHON` that is no virtualenv's or cannot be run; every line it says that names a path or the program said as it is,
from checkouts in folders whose names a shell would split or hold a backslash before `n`, `t` or `c`, its three
sentences with the program as the printer quotes it, a path typed that starts with `=` among them, and no `echo` in
it. `tests/unit/test_aw_cli_rules.py` holds the rules and `deploy`'s
question: every rule is named by the verbs whose rows run it; each refusal of a rule or a habit, and each of the
parser's own, is run through the launcher, the words, status and sentence of each written there; each case of a rule is
refused by the rule its code names, each case of a habit says its habit's sentence, and each of the parser's own is the
parser's sentence in the test's process too;
`--repos`' 2000 characters taken and 2001 refused; each name the names habit splits a verb's words into held to the
pattern, through the launcher and with the pattern narrowed, as `host --for`'s are; no rule says back a value that may
be a secret, over every case of a rule with each value of free text its verb takes (held to `text`, `assignment`,
`path` or `secret_file`) typed as one, and one it says back
has met its pattern first; the check answers alike as words and as JSON; the question
is asked at a pseudo-terminal, typed and with none; and no file of the tree names the recorded command line of v2.7.0,
which no test holds `aw` or the `justfile` to.
The printer's own test is 16.2's.
`tests/unit/test_aw_cli_help.py` holds help and the reference: every verb's help shows its options and words in the
table's order; `aw help` lists every verb and no recipe of the repository's own, the options before a verb, and its
closing paragraph with `-v` and `--set`; each option with its value's placeholder; each verb's usage line, written
there, a verb that needs its word refusing none once built, and the placeholder of words a verb refuses whole refused by
its own rule; the form of every value the table holds to a pattern, in words, for every option and words of the table
and of the maps `tests/unit/test_aw_cli_words.py` writes by hand, with the example and the words they name, in help's
row and in the refusal of a value outside it alike, `--attended` before any verb among them, and none for the words of a
verb that refuses any, never the pattern itself, and no empty default; `--set`'s row naming its placeholder, never
`name=value`; `release`'s rows saying one adopted machine; an option of a later card's left out; `[options]` in the
listing; help as wide as its terminal, `COLUMNS` first, between 72 and `WIDTH`, and `WIDTH` with none; with no terminal
and at 80 columns, every row of the listing and of every verb joined again equal to the row unwrapped, its lines after
the first at its text's column, and at every width from 72 to `WIDTH` no line broken inside a piece and none longer
than the width but one piece, the program as `aw` and from a path with a space; a refusal and the reference unwrapped;
the tests that read a row's end reading it joined again; through `tools/aw` and the launcher, `control` refused as no
verb; what each imports; and that the reference gives a row for every verb but `help`, every option but a later card's
and the words, each value held to a pattern with its words and then its pattern as code, and no row split by a `|` or a
code span holding one; and escapes a cell as `tools/docs.py` does.
`tests/unit/test_aw_cli_imports.py` holds the quick commands to what this section says they import: `aw` alone, help,
`--version`, a refusal of each kind, and the reads `logs`, `cancel`, `host`, `settings` and `status`, each read once its
verb has a handler and skipped by name until then, run through the launcher of a checkout the test makes with a fleet of
the test's own, load none of pydantic, Ansible, Jinja2, the MCP SDK or `engine/`, and nothing beyond the standard
library and `claude_vm` but a YAML reader, for the reads of the fleet's files, and `pyrage`, for the two that open the
vault, `settings --all` and `status`. What a run loaded is `sys.modules` at its exit, written by a hook of the test's
own in the virtualenv it gives the launcher as `PYTHON`, since `-X importtime` lists no module the dispatch loads by
`importlib`.

`aw status` runs today's status engine, `lib/claude_vm/status/engine.py`, in the command's own process, with settings
worked out from the fleet's files in place of the ones `status/settings.py` asks Ansible for or keeps.
`cli/status_inputs.py`'s `load` gives the engine the dict `settings.load` gives it, key for key: the registry's
defaults under the fleet's `settings.yml`, the host's own file and `client.yml`, with the run's `--set` values on top
of them all, and the host's `host.yml`, its anchor and the vault's secrets. What `--set` gives is typed by
`fleet/overrides.py`'s `run_values` before any file is read, so a name that is no setting's, a secret's row and a value
of the wrong type are each refused first, never said back, and once the vault is open a value that is one of its
secrets is refused too. The host is the one `fleet/hosts.py`'s `reach` picks; its token's secret is read from the vault
at each run and goes into the API's one header, and its certificate is always checked: against the anchor the host
keeps, else this computer's certificates. That header is the one the new engine writes outside the Proxmox client,
for today's engine's reader, whose GETs alone, with no redirect followed, are every read aw status makes. Status's own
ssh options open no shared
connection, where today's did: only the SSH runner's `ensure` opens a master, so a probe rides one that is open. Nothing
of 1.x's is named, and nothing is kept but apt's answers for a VM, in the host's folder of the fleet's state
(`fleet/paths.py`'s `status_cache`). `cli/status.py`'s `main` is the handler:
a refusal is said before the engine starts, and the run then ends as a read that failed (3). The engine's `run` takes
the loader as a parameter, today's `settings.load` unless given, so `tools/status` passes nothing new; and every wait
of its main thread on the API's answers and on the probes takes slices, as `_off_main`'s does, so a Ctrl-C lands within
one even where a signal wakes no wait of a lock (macOS). `status_inputs` imports nothing of today's status, and
`cli/status.py` takes one name of it, `run`. One host is read: reading every host at once, a child each as
`tools/status` does, needs the table's word that starts the child, and is not built.

The engine takes the reader of the fleet as a parameter beside the loader, today's `Tagged` unless given, and the
program its own messages name, `just` unless given, so `tools/status` passes nothing new and prints what it prints. aw
status gives it `cli/status_pool.py`'s `Pool` and `aw`, and its settings give every command the model and the drawing
name printed whole by the printer, with the host's flag (`status_inputs.lines`, read by `status/model.py`'s
`printed_line`, each VM's name put in its place), so that no line of aw's is written by hand. The fleet is the pool's
members, never a tag: each one's configuration is read before a VM is drawn and judged by `pve/ownership.py`'s one rule
(`Member.read`, `refusal`), with the members that carry its UUID as its twins, templates among them, found by
`ownership.twins`, the one function the engine's reader calls too, so that status and every verb that changes a VM never
part; a member on a node that is down cannot be read, so it is never judged and never a VM of the report, only one
"unread" line; a member the rule refuses is a line of its own (one with no record says it is not this fleet's, never
changed, and gives the `destroy` line that deletes it under `destroy_allow_unrecorded`), and one whose configuration the
token may not read is not read. A record's job that is no job's ID is a problem line, never a `logs` line. Its record
gives what today's tags gave: its stage, its own repositories, whether it joined the tailnet, whether it was made with a
size of its own; an unapplied change is shown "not known" while the record keeps no digest to compare, and the template
is judged by `pve/template.py`'s `judge`. A VM this computer has filed no host key for is never dialled (status takes
none): its SSH sections are one line, naming the run that files its key. A job a VM's record names that this computer
has no record of is said as another computer's run, and one of this computer's that was lost as lost, each with its `aw
logs` line. Under `proxmox_audit_scope: node` a storage lists another VM's disk only where the token holds
`VM.Config.Disk` on it, which the grant on `/vms` does not give: so each VM outside the pool's configuration is read,
after every read of the fleet's and only where the storage or capacity section is asked for, and only each disk's
storage and size are kept of it, so the storage's figure is a floor, and says so. Under `pool` they are not read at all,
the node's other VMs are shown empty and one line says so. Every line a report says names `aw`, each the printer's; the
JSON document gains `known_at`, the run's own time.
`tests/unit/test_aw_status_pool.py` holds it against the fake: the same report as `tools/status` on the same VMs, one
fleet by its tags and the other by its pool and records, but for the commands, `known_at` and `unapplied`; GETs alone;
each new line; two hosts; both scopes; and nothing of a VM outside the pool but its disks' sizes in the report, the
JSON or the log.

`tests/unit/test_aw_status_inputs.py` holds `load` equal to today's `settings.load`,
difference by difference with why, for E4's fleet folder beside the example files and with a setting of each file's
and each secret set, and the refusals; `tests/unit/test_aw_status_hosts.py` holds a fleet of two hosts, each read with
its own API, token and settings; `tests/unit/test_aw_status_live.py` runs `aw status --json` against the fake beside
`tools/status --json`, and a Ctrl-C while `read_fleet` waits on a probe and on the API's answers.

**The agents' tools: `cli/mcp_backend.py`.** The new backend of the agents' server, beside today's
`lib/claude_vm/mcp/recipes.py` ([§7.12](custom-code.md#712-libclaude_vmmcp--the-mcp-server)), which stays until the
cutover: it is no module of `mcp/`, since nothing of today's imports the new side (16.1). `tools()` lists the sixteen
tools: one for each tool the table's column `tools` names (`settings` and `settings_set` both run `settings`, `hosts`
runs `host`), and the server's own reads of the state folder's jobs, `log`, `jobs` and `job`, which run no verb. A
tool's schema is a small function of its verb's row (16.2): one flat object, `additionalProperties` false, a property
for each option and the verb's words whose fate for that tool is `property`, with exact JSON types (a string, a
boolean, a list of strings for several words), the table's pattern as `^(?:<text>)$`, since a JSON Schema pattern is not
anchored, and the table's help said through `said` with its form as the description; a word the verb needs (`code`'s,
`release`'s) or its tool does (`agent_needed`: `destroy`'s names, `cancel`'s job) is required, and refused when none is
given, so that no default is implied through an agent, and a tool whose verb's run is a job has the server's own `wait_seconds` beside them. The
module holds no fate, reason or tool's name of its own: a fate the table does not write for a tool raises `Unfated`,
naming the verb, the word and the tool. `command(tool, arguments, given)` is the command line a call runs, the
printer's `argv` with `refused` false, so `settings_set`'s value reaches the run: each value one argument word, never
through a shell; each fixed value the table writes (`--json`, `init --no-prompt`, `--detach` on every verb whose run
is a job), and in `given` those the server works out for each call (`--confirm`'s names, `--attended`), each a value
(an empty one is the server's fault, `TypeError`, since `--confirm=` with no names would reach the default VM), the only
words before the verb, since `aw` reads its settings from the fleet's files. A call is refused with `Refused`, before any
child starts and in a sentence for the agent: an argument the tool does not take, with the table's reason (`--set`,
`-v`, `--adopt`, `logs --follow`, which would wait until the job ends, and the rest); a verb's word that starts with
`-`, which `aw` would read as an option, while an option's value that does is written `--long=value`, one word; then
what the one check refuses, its rules among them (`settings.5` refuses `settings edit` beside `--json`, which both settings
tools give, so neither opens an editor on the person's screen), so that the printer's words that the program is at fault are never
said of an agent's value; and last a line the printer will not write, its `Unwritten` said as the refusal. It imports
`schema/` and the standard library alone. `tests/unit/test_aw_mcp_tools.py` carries `test_mcp_tools.py`'s holds against
the table: every verb a tool or a reason, every option and word one fate for each tool that runs it, one added with
no fate failing by name, every verb whose run is a job started detached; each word a tool does not take tried and
refused with no child started; a value with a space one word, a word with a leading `-` refused; the schemas valid,
exactly typed and anchored; the descriptions the table's; the check before the printer; and a scan that no fate is
written in the module.

**The agents' server over the program: `cli/mcp_serve.py` and `tools/aw-mcp`.** The server's reads answered by `aw`
run as a child, through one seam in today's `lib/claude_vm/mcp/server.py`: its `Server` takes a backend, today's
`Recipes` by default, and asks it a read's command line, its environment, its name in a sentence, the vault's refusal
and each tool's schema, so that `tools/mcp` and every 2.x registration run as before (the 2.x
`tests/unit/test_mcp_server.py` and `tests/unit/test_mcp_hosts.py` hold it), while `tools/aw-mcp` builds the same
`Server` with `Program`, this module's; nothing of `mcp/` imports the new side (16.1). Its configuration is the
registered command's arguments alone: today's, read by `mcp/config.py`'s `parse` unchanged, and six of its own, each
`--name=value`: `--program`, the executable `aw` every read runs; `--fleet-home`, given to each run as
`AGENT_WAREHOUSE_HOME`; `--state`, the state folder, for the server's own reads of the jobs; `--vault-key-file`,
given as `AGENT_WAREHOUSE_VAULT_KEY_FILE` (16.9), a path the server never opens; and `--config-home` and
`--state-home`, the terminal's `XDG_CONFIG_HOME` and `XDG_STATE_HOME` where they are not the defaults, given as those
two, since `fleet/paths.py` places the installed home's state folder and key by them: a run's state is then `--state`
and its key the terminal's. The module names each variable only where a run's environment is given it, and opens no
file, which a test holds by its syntax and on which the fleet scans' exceptions for it rest. A command line that lacks
one of the first three, as 2.x's `just mcp` registered it, names one twice, or gives today's
`--vault-password-file`, is the refusal each call is answered with, naming `aw mcp`: the server never exits at its
start. A call that gives an argument its tool's schema has no property for is refused with `cli/mcp_backend.py`'s
`command`'s sentence for it, before the SDK, which checks a call against the tool's function alone, would drop it;
each tool's function takes its schema's properties exactly. A run's environment is today's allow list
(`mcp/environment.py`) with those variables, never the session's: nothing of Ansible's,
Python's, `just`'s or Claude Code's, no `LOG_DIR`, and no proxy (`HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, in either
case), since any project's settings set the session's environment and a proxy named there would carry the run's every
request, the token's among them: a run of the server's reaches the network as `network_proxy` says, and a proxy that
needs a user and password is a terminal's alone. Its standard input is `/dev/null`, its session its own, its signals at
their defaults. It lists the reads alone: `status`, `settings` and `hosts`, today's tools with their bodies unchanged
and the table's schemas; `logs`, which takes the table's job; and `log`, which reads one job's text log through `aw
logs <job>`, by the job's ID or by its log's name, whose job the list `aw logs` prints names (`job_of_log`), a part at
a time as today's `log` does. Every result is today's `results.py`'s, under 40 000 characters with the note that text
from the machines is data. The server process imports nothing of `engine/`, `pve/` or `fleet/`, nor Ansible.
`tests/unit/test_aw_mcp_server.py` drives it over stdio with a stand-in `aw` that records what each run was given and
prints the canned reports of `tests/unit/test_mcp_server.py`, and holds the same results for them: the tools and their
schemas; the report with the head and the note; each run's words, its environment against every variable a session
could set, `ANSIBLE_ARGS` and the proxies among them, its standard input, session and signals; `logs` and `log` by a
job and by a name, a part at a time, and the names refused; a warning on standard error; a change to what the server
runs; the refusals of a command line; the variables equal to the engine's own; and, in a process of its own, that
building the server and answering each read loads none of the engine's modules.

## 16.4 `engine/` — the workflows, one module a verb

`lib/claude_vm/engine/` is where the workflows go, one module a verb. Each says what a run of `deploy`, `destroy`,
`login`, `status`, `code` and the rest does, in the command's own process, in place of the playbooks. It imports the
clients (`pve/`, `ssh/` and `vendors/`), and `runs/`, `fleet/`, `guest/` and `schema/`. It never imports `cli/`,
`control/`, pydantic or today's `claude_vm.ansible`, and it runs Ansible only as a child process. `cli/` imports it,
and only when it dispatches a verb that changes something. No client imports it.

**A VM's record: `record.py`.** What the engine keeps of a VM is one record in its notes in Proxmox, where the VM cannot
write: what it is of (`vm`, `build`, `golden` or `template`), the SMBIOS UUID it was written for, the name the run asked
for, its stage (`cloning`, `unfinished` or `finished`), `provisioned` once its first start went well, its own list of
repositories, the size it was created with, a template's build, the digest of its image, a template's image's URL and
the packages put into that image, the VPNs a login of which was seen on it (`joined`), the digest it last converged to,
whether an agent's run made it, and the run on it now (`job`). `Record` is a frozen dataclass over those fields, each
held as it is made: the kind and the stage to the format's words, the UUID to the form Proxmox gives a VM's, the name,
the size and the repositories to the command line's patterns (`schema/patterns.py`), the digests to SHA-256's hex, a
template's build to a whole number, its image's URL to an http or https URL and its packages to Debian's names, the VPNs
to today's mark of one (`vpn/model.py`), and every text to one line of plain text: each field's form is
`pve/record.py`'s (16.5), the one definition, which its plain `parse` holds a block to as well, so that `status`'s
reader refuses every value this one refuses, and `Record` adds its types. Every record names the UUID it is written
for, but a VM's first form, which its clone carries before its UUID can be read; and a VM's names its stage: the rules
across fields, which `Record` holds here and each plain reader's judge holds for itself.

- `read(config)` is the record a VM's configuration holds, or none where its notes hold no block of the engine's,
  whatever else they hold: a VM, a template or a golden with none is not this engine's. A block it cannot read, or one
  whose value is not what its field may be, is `Unreadable`, which names the VM. One that names a UUID that is not the
  VM's own is `Copy`, an `Unreadable` of its own kind: a clone made without a description of its own carries its
  source's notes, record and all (experiment 6), so a copy is never read as the VM's own, and no reader makes that
  check itself. A VM's first form alone names no UUID, and cannot be told from a copy of one; a record of any other
  kind or stage that names none is `Unreadable`, so a template's, a golden's and a build's record names its UUID from
  its first write.
- `write(client, vm, change)` is the one writer. It reads the VM's configuration, applies `change` to the record its
  notes hold, renders the notes with that block alone changed (`pve/record.py`), and writes them with the digest of that
  read through the client's `edit_config`: where another writer, a person in Proxmox's page among them, changed the
  configuration in between, the write is refused and read and applied again, three times at most, so `change` is a
  function of the record it is given alone. Notes that, as Proxmox will give them back, are those it read send
  nothing, so a second run of the same change sends no write. It never writes a record that names a UUID other than
  the VM's own, nor one of another kind than the one read, a VM's for an `Owned` (so no write makes a VM of the
  fleet a build VM or a golden, which `building`, 16.5, would take), never rewrites a block it cannot read or a
  copy, and never sends notes past 8192 characters (`TooLong`), each said before anything is sent.
- `label(client, vm, value)` gives a VM the one label it carries in Proxmox's list, the setting `vm_label`'s value,
  `agent-warehouse` by default, beside a person's tags, under the same digest. Nothing decides anything from it: what is
  the fleet's is its pool's members with a record.
- Both take the VM as an `Owned` (16.5), which the one check alone makes, and refuse anything else, a target a read gave
  above all, before anything is sent.

No other module of the new engine sends a VM's description to Proxmox, but the base template's first write: its build's
`qm create` over root SSH (`pve/commands.py`, 16.5), which gives the template the record the build made, naming the UUID
made for it, before the template exists, so that no client can send it. `tests/unit/test_aw_record.py` holds it: every
field round-trips; a person's lines beside the block are kept byte for byte, today's record lines among them; a block of
a format this program does not read, and every other it cannot read, is refused and never rewritten; a value with any
line break, or anything else that is not plain text on one line, is refused; the notes' bound is counted in characters;
and `tests/unit/test_vm_repos.py`'s cases of a VM's own list are carried. Against the fake Proxmox under enforcement: a
record written reads back field for field; of two writers the second is refused, reads again and lands with the first's
change kept, as does a write a person's edit came between; a second write of the same record sends nothing; a block the
engine cannot read, a copy's record, a record that names another VM's UUID, a VM's record rewritten as a build's or a
golden's (its first form's rest still written), any record over one of another kind read, and notes past the bound send
nothing; a record of a template, a golden or a build that names no UUID is refused, made or read; and the label is
`vm_label`'s value beside a person's tags. A syntax-tree scan holds that no module of the new engine names a description
to send but this one, the client, its table, its reader of answers, the template's judgement (16.5), which holds no
client, and the build's commands, whose `--description` it finds too, each with why.

**The base template: `template.py`.** Every VM is a full clone of the base template, so `deploy`, as its first link,
asks whether the template is what its settings would build. `compare(client, settings, pool=, host_flag=)` reads it
through the API with the engine's one token, by GETs alone: the cluster's listing; where nothing is listed at the
template's ID, what the token may read there (GET /access/permissions, which needs no privilege); and, only where the
listing names a guest there that the judgement reads further, its configuration with the record in its notes, read by
`read` above, so that a block it cannot read, a copy and a template's record that names no UUID are each refused. It
judges what it read by `pve/template.py`'s functions (16.5), which say the outcome and its lines; `status`, which may
not import the engine, judges by the same functions what its own client read, so the two say the same: the judgement
holds the record to the guest's own UUID whichever reader read it, and each field's form is the reader's. `wanted`,
`BUILD`, `OPTIONS`, `Compared` and the six outcomes are that module's, given here as the comparison's.

`tests/unit/test_aw_engine_template.py` holds it: `wanted` is today's role's under its defaults, and its options are
the roles' own; against the fake Proxmox under enforcement, with `VM.Audit` on `/vms` and without it, each outcome, each
difference, the fake's own template with this engine's record as this version builds it, a difference kept under
`proxmox_template_rebuild: false` cloned from as it is, each refusal of the role's scenario that a comparison makes,
and the engine's own beside them, a person's template with a record that names no UUID pasted into its notes and the
record a build would write before it knew the template's UUID among them, each leaving the template as it was; an ID
outside the pool is `unseen` under `pool`, never `missing`, and a person's own template outside it is `unseen` too,
said with the `aw init` line that grants the token `VM.Audit` and `VM.Clone` on that template alone, once granted read
and cloned from while it stays out of the pool; the judgement, given what `status`'s reads give and the record by the
plain `parse`, says what `compare` says, a template's record that names no UUID and a copy of another's refused among
them, and imports nothing of the engine and not the client; and `compare` sends nothing but GETs.

`build(ctx, host, settings)` builds the base template where it is missing or differs, with today's role's steps, over
root SSH to the host as `routes.host_target` reaches it, holding the host's `template` section exclusively (16.8), so
that it waits for clones in flight and they for it. The image's checksum is held to its form first: an image with no
checksum to hold it to, a custom image with the default's, and a signature with no list to sign are refused before
anything is read. Then it reads, as root, the whole cluster's listing and the configuration of the guest at the
template's ID (`pve/commands.py`'s `pvesh` reads, which give what the API gives, the configuration's digest and decoded
notes among it, where `qm config` prints neither), and judges them by `pve/template.py`'s functions, root seeing what
the token may not: a guest there that is no template of this engine's, outside the pool or on another node is refused as
`compare` refuses it and never touched; the same template is left as it is, and so is one whose difference
`proxmox_template_rebuild: false` keeps; a template of the person's own is never built. Then, on this computer, the
image's SHA-256 from its checksum list, verified by `vendors/downloads.py` (16.7) by Ubuntu's key or the person's own,
from an unsigned list, or as the checksum gives it. On the host, each step one argument list of `pve/commands.py`'s
through the SSH runner, nothing on its standard input: the image fetched by the host's `curl` into the engine's own
image directory (`/var/lib/vz/aw-images`, or `proxmox_template_image_dir`), made 0755 and read by `stat` as it is, never
through a link, and refused unless it is a directory of the account that builds (root's, by `id -u`) that no other may
write in, since any account that can would leave a link there for root to write through; under a name of its own,
anything there cleared first, held by the host's `sha256sum` to the digest verified here and removed, naming both, where
it differs, then renamed into place by `mv -T`, over a link with the image's name and never into where it leads, and
left where it is already there with that digest; where the guest agent goes in, the image tool installed by apt where
`proxmox_template_manage_libguestfs` is on and dpkg's own state of it (`${db:Status-Status}`) says it is not installed,
one the administrator holds counted as there, and one neither installed nor absent, or whose state could not be read,
refused, installing nothing; the image's system read with `virt-cat` and one outside the Debian family refused by name,
and a copy, its name cleared first, customised with `virt-customize`; the template that differs removed only once judged
again just before it is destroyed, the listing and the configuration read anew: still this engine's template that
differs, with its record, its name, its node and the pool, under the SMBIOS UUID first judged (`Compared.identity`); one
taken out of the pool, stripped of its record, renamed or replaced in the minutes the image took is left as it is and
said with the judgement's sentence; a refusal of Proxmox's for a template a linked clone uses said with
`proxmox_template_rebuild: false` as the way on; `qm create` with today's settings, the SMBIOS UUID made here, the
record as its notes and the fleet's pool; the disk imported; the cloud-init drive, the boot order and DHCP, with no
`ciupgrade`; `qm template`; and the template read back and judged again, which must find it as its settings would build
it. The customised copy is removed whatever happens. What the build leaves on the host is recorded in `host.yml`'s
`made` through `fleet/hosts.py`'s one writer as it is made (16.9): each image file it downloaded (`file`), the template
by its ID, and each package of the image tool this run installed (`package`), none that was there before, so that a
cleanup removes exactly that.

`tests/unit/test_aw_engine_template.py` holds the build against a stand-in Proxmox host of `tests/unit/aw_machine.py`'s,
reached as root through the stand-in ssh, whose `qm`, `virt-customize` and `virt-cat` are the role's scenario's fakes
(`tests/molecule/`), its `pvesh` reading `qm`'s state, its `apt-get` and `dpkg-query` stand-ins of the test's own, and
the image and its signed checksum list served by `tests/molecule/fake_downloads.py`, the list signed by a key of the
test's: the first build runs today's steps in their order, its `qm` calls, its customisation and its template's settings
those of the role's scenario, and records the image file and the template in `made`, the copy and the download's own
name gone; a link planted at the download's name, at the copy's or at the image's, to a directory, is never written
through, and the directory a link, writable by another account or another account's stops the build before anything is
fetched; a second run reads and changes nothing; a list whose signature does not verify, or that names no image, fetches
nothing on the host; an image whose digest differs is removed and both digests are said; every command is one of
`pve/commands.py`'s, nothing on its standard input, under the section's lock; a template a linked clone uses is said
with the way on; a template with no record of the build and a guest outside the pool are never removed, and nor is a
template that, after it was judged to differ, was taken out of the pool, moved to another, stripped of its record,
renamed or given another UUID; an image outside the Debian family, a custom image with no checksum of its own and a
template of the person's own build nothing; and the image tool's packages are recorded only where this run installed
them: a package held is left as it is, one dpkg says is not installed is installed, and one half configured or whose
read failed stops the build before apt runs.

`ensure(ctx, host, settings, terminal=)` is `deploy`'s first link and the whole of `deploy --only template`, as the run's
`template` stage. It compares first. A template a VM may be cloned from as it is (`usable`: the same, a difference
`proxmox_template_rebuild: false` keeps, a person's own lacking nothing) is said and left; this engine's, where its
configuration has no cloud-init drive, which the judgement does not read (`pve/template.py`'s `cloud_init`, the check
`create.py` makes before a clone), is refused with the way on rather than called the same. A refused template, a cut
build's VM at the ID among them, is refused with the judgement's sentence, never built over or retried. Otherwise it
asks for a standing way to build: root SSH that answers in batch mode, one connection that runs nothing but `true`.
Where it answers, `build` runs, at a terminal or with nobody there. Where it does not, nothing is created: at a terminal
(`runs/ask.py`'s `terminal`, decided before the run draws) the sentence says what would give one (the account's key on
the host, the agent, `ssh_identity_file`); with no terminal, `Compared.unbuilt`'s: what the template has and what is
wanted, that this run cannot build it and why, and the `deploy --only template` line to run at a terminal. Every refusal
of its own is said as the stage's failure, and a report after it records the printer's `aw deploy --only template`
line, with the host's flag, as the run's NEXT STEPS line (matter `template`, through `report.py`'s `say`). It returns only a usable template, or raises, so no
run clones from a template that is to be built and was not. A refusal exits 1; a build begun that did not end well
(`Failed`) exits 2. `tests/unit/test_aw_engine_template.py` holds it through `aw deploy --only template` against the fake
with standard input `/dev/null` and a refusing `ssh`: missing, or differing, with root SSH refused, the sentence, exit 1,
no write, one connection in batch mode, NEXT STEPS and the fleet's lock exclusive; the same template and a kept
difference said and left, root SSH never asked; a template with no drive refused; a cut build's VM handed on; another
stage still refused; and in process, on the stand-in host, the build run where root SSH answers, and at a terminal with
it refused, what would give one, nothing run on the host.

**What `init` makes on a host: `access.py`.** One list names every object a first `init` makes on a Proxmox host with
its administrator's authority, and one check tells what is there of each; the script that makes them is their one
rendering, `as_script`'s, run over root SSH on a `y` or printed for a person to run, and this module runs nothing.
`objects(host)` is the list, in the order it is made: the pool, its comment the fleet's mark (`fleet/mark.py`'s
`render`, `controller=none`); the engine's role, `AgentWarehouse`, whose privileges are what the one table of privileges
needs on every kind of path (16.5), so that one table says both what the client calls and what `init` grants, and a call
added to it is granted with no list of this module's to change; under `proxmox_audit_scope: node`, its default, the
audit role, `AgentWarehouseAudit`, `VM.Audit` alone; where `proxmox_template_custom` names a template of the person's
own outside the pool, the clone role, `AgentWarehouseClone`, `VM.Audit` and `VM.Clone`; the user, `agent-warehouse@pve`;
the user's grants; the token, `aw`, with privilege separation; and the token's grants, the user's again, since such a
token holds what both hold. The engine's role is granted on the pool (`aw-fleet` where none is named), the storage for
VM disks, each bridge a clone's source has a network card on, which a clone asks `SDN.Use` on, and the node; the audit
role on `/vms`, read only, so that `status` shows the node's other VMs and a capacity made from all of them; the clone
role on the person's template alone. A role is one set of privileges granted on a path, so the engine's role, which
writes, is granted on no guest outside the pool; under `pool`, the stricter opt-in, the token's writes and its reads of
single VMs stay within the pool and that template. Under both, `Sys.Audit` on the node, which `status` needs for the
node's capacity, also reads the node's report, and that report holds every guest's configuration. The user's grants come
before the token and the token's after it, since an ACL entry can name only a token that exists. `Host` holds each value
of a host to the form Proxmox gives it before any object is made of it, a pool's name from a letter and with no `/`,
which on Proxmox VE 9.2 makes a nested pool (`POOL_NAME`, and `POOL_RULE` its words: the settings table's one rule of a
pool's name, `registry.POOL_NAME`, which `proxmox_pool`'s form holds), and `Host.of` reads them from a host's settings.
`check(object, found, host_flag=)`, given what one read of the host holds (`Found`), answers missing or the engine's own
and nothing else: a pool whose comment does not carry this fleet's mark is refused with `fleet/mark.py`'s sentence,
never taken, and so is a token of the list's ID made without privilege separation, which holds every privilege of its
user, with the line that deletes it and init's line that makes one beside it, for the host `host_flag` names as the
printer takes it, which every caller gives, a further host's name or `""` with one (`Found` holds each token's
separation by its full ID); a role is told with what it lacks and what it holds beyond its privileges; and the audit and
clone roles, granted on guests outside the pool, are held to their privileges exactly. `plan(objects, found,
host_flag=)` makes each object that is missing and appends to a role what it lacks, never taking one away. The engine's
role holding more is granted all the same and said, with `pveum role modify AgentWarehouse --privs` and its fifteen as
the line that keeps it to them, so that a privilege a later table drops is said, never kept in silence. A role held
exactly that holds more is neither changed nor granted, and said with the line that fixes it (`pveum role modify
AgentWarehouseAudit --privs VM.Audit`, as root on the host), since on `/vms` it would hand each privilege it holds over
every VM of the node; where its grants are there already, it is said to give each of them now. A grant of one of the
engine's three roles to the list's user or token on a path the list does not make with these settings - `/vms` once
`proxmox_audit_scope` went from `node` to `pool`, a template no longer named, a storage or a bridge given before - is
left, since init never takes a grant away, and said with the line that removes it (`pveum acl delete /vms --roles
AgentWarehouseAudit --users agent-warehouse@pve`, as root on the host), once, in order of its path, its role and whom it
is for; an entry whose path is not of the form Proxmox writes one (`ACL_PATH`) is not said.

`tests/unit/test_aw_access_objects.py` holds it: the list, its order and its count, fifteen objects with the defaults
and twelve under `pool`; no object but the pool with the mark, the roles, the user, the token and their grants, each of
a kind and an ID `host.yml`'s `made` records; the engine's role as the table's needs, each need granted where Proxmox
checks it, and a call added to the table granted, or found where no grant could reach it; the token's `VM.Audit` on
`/vms` through the audit role alone under `node`, nothing on a guest outside the pool under `pool`, and a person's
template through the clone role on its own path under both; every value Proxmox makes no object by refused, never said
back; and the invariant, over a host holding nothing, everything, everything but one object, a role lacking or holding
more and a grant that does not propagate: `check` answers missing or the engine's own and nothing else, a pool of
another fleet's, of no mark or of a control VM refused with nothing planned, a role of the engine's own given what it
lacks and losing none and one holding more granted and said, a role held exactly that holds more, or lacks one and holds
more, said and left ungranted, or said to give it now where its grants are there, and a token known by its full ID and
its separation, another user's of the same ID missing and one made without separation refused, its init line naming the
host its caller gives, which no caller of `check` or `plan` may leave out; and a grant of the engine's roles that a
host's settings no longer make said with its delete line and never taken away, once, and only for the list's user or
token, one of the three roles and a path of Proxmox's form.

**The script that makes them: `as_script`.** `as_script(host, custom, host_flag=)` renders the list as the script `init`
runs on the host as root, `/bin/sh` run as `sh -s` with itself on standard input: over root SSH on a `y`, or printed by
`aw init --print-commands` for a person to run there (`aw init --print-commands | ssh root@<host> sh -s -- --dry-run` to
see each command, then the same without `--dry-run`). It is rendered each time and never written into the checkout; its
header says it was rendered by `aw init` for the host's node and fleet, and its lines that print and run it are init's
as the printer writes them (16.2), the header said through `said` with each a `Line`, with `--host` and the host's name
for a further host (`host_flag`, which every caller gives, as the token's sentence names it too). Its options change the
values the list is made of: `--storage`, `--bridge`, `--user`, `--token`, `--pool` and `--audit-scope node|pool`, each
the host's value where not given; and a person's own template, which `proxmox_template_custom` names (`custom`), is
looked up on the host by its name, the one guest of that name and a template on the node, with the bridges its network
cards are on, so the host it is rendered for has the base template's bridge alone and no template's ID. Each object is
one call of the script's fixed interpreter (16.5, `pve/script.py`), its values the script's shell variables: the list is
made for a host of placeholders, each rendered as its variable, the objects of one scope under that scope's test, the
clone role and its grants under the template's lookup, and a bridge's grants in a loop over the bridges; and at every
rendering the shape is held to the list itself, under each scope, with and without the template, over one bridge and
two. The calls run twice: first each reads what is there and decides as `check` and `plan` decide, a refusal ending the
run before anything is made; then each makes what was decided, in the list's order, and the token's secret is printed
last, the one line on standard output, everything said to a person going to standard error. Every check of a value and
every sentence the script says of the list is this module's or `fleet/mark.py`'s, rendered: a value of another form than
Proxmox makes an object by is refused before any `pveum` and never said back; a pool another fleet marked, one naming a
control VM and one whose mark cannot be read are refused; a pool with no mark is refused naming `--take-unmarked`, and
taken only given `--pool <name> --take-unmarked`, the mark added at its comment's end and the rest kept (a comment
it would take past `COMMENT_MOST` characters refused by name, Proxmox declaring no length for one: card E8), while
`--take-unmarked` without `--pool`, or for a marked pool, is refused; a token made without privilege separation is
refused with its delete line, and one whose comment carries a mark that is not this fleet's (another fleet's, a control
VM's, one that does not read) with `check`'s sentence naming `--token-id`, since two fleets share the user and never a
token, whose comment carries the fleet's mark as the pool's does, a token whose comment carries none taken as it is; a role holding more than its own is said and granted, or withheld, as `plan` says; and a
grant of the engine's roles these settings do not make is said with its `pveum acl delete` line and left. The script
runs only as root, on Proxmox VE 9 or later, and refuses `root@pam` and any option it does not have, today's `--move`
and `--template` among them. `--regenerate` (card E8's take-over) makes the token's secret again, `pveum user token
modify <user> <id> --regenerate --output-format json`, whose `value` is printed as a new token's is, only of a token
whose comment carries this fleet's mark, and makes the token where it is not there; with `--no-token` it is refused.
`--no-token` makes no token and changes nothing where the token is not there; a step that
fails after the token was made says that it is there with its secret unsaid, and the line that deletes it, after which a
run again makes it anew. The dry run prints the commands, one an object, every token ID quoted, the token's own line
asking for JSON, whose `value` is the secret; `--list` prints the node's storages and bridges.

`tests/unit/test_aw_access_script.py` holds it, each case the rendered script run as `sh -s` under `/bin/sh` against the
stand-in `pveum` and `pvesh`: a fresh host gets every object in the list's order and the secret alone on standard
output; a second run changes nothing and prints no secret; a role an older version made gets what it lacks and loses
nothing; the dry run prints one line, quoted as `shlex` quotes it, for each step of `plan(objects(host), found)` and
says each of `plan`'s sentences, the run makes those steps, and once made nothing is left to make, over a fresh host,
`pool`, values of another host, a host an older version half made, a grant that does not propagate, roles holding more,
`--no-token`, a person's template inside and outside the pool, a host set up under `node` and run under `pool`, and
grants of the engine's roles these settings do not make beside some that are not said; `--no-token` with no token there,
`--dry-run` and `--list` change nothing; under `pool` nothing is granted on a guest; every printed token ID is quoted
and the token's line asks for JSON; a grant that fails after the token says its delete line and prints no secret; a host
set up under `node` and run under `pool` says each `/vms` grant with its delete line, removes none, and a line run
removes what it names; and each refusal leaves the stand-in's state byte for byte as it was: not root, Proxmox VE 8, a
value of the wrong form before any call of the stand-in's, `root@pam`, an option it does not have, a token without
privilege separation (its init line naming a further host's `--host`), a pool of another fleet's, a pool with no mark
without `--take-unmarked`, `--take-unmarked` without `--pool` or for a marked pool, and a template that is not the one
guest of its name on the node or has a network card on a bridge not there. Card E8's: a token another mark names is
refused under every option and one with no mark taken, over the same corpus; `--regenerate` prints the one line and the
new secret, makes a missing token, and refuses with `--no-token` and for a token no mark names; and two fleets on one
host share the user, granted on both pools, each token's own entries on its own pool alone, the second fleet's default
token ID refused naming `--token-id`, and a token without privilege separation refused. A corpus of comments holds the
script's reading of the mark to `fleet/mark.py`'s, separators and spaces of every kind among them. The header's lines are the
printer's, a further host's flag in them. The rule of V2-9 has a test of its own: no file of the checkout, tracked or
untracked and not ignored, holds a rendering, found by its header. The stand-in reads no guest's configuration, so a
`pvesh` of the test's own, first on `PATH`, answers that one read and hands every other to the stand-in.

**The API's certificate: `certificate.py`.** Before `init` sends a Proxmox host anything that carries a credential,
`work_out(host, given, root_ssh, ask)` settles what its API's certificate is held to, by today's `trust.py`'s probes,
which carry none, and returns `Kept` (the anchor to keep, or none) or a `Refusal`. First what checking uses now: the
host's anchor, its name checked unless a fingerprint is recorded beside it, which the certificate presented must then
have, as the client holds it; or, where it keeps none, this computer's certificates, certifi's list with the file
`tls_ca_file` names added, handed to the probe as their certificates' text, so that `trust.py`'s own idea of a store,
which reads `REQUESTS_CA_BUNDLE`, is never asked. Where that verifies, nothing changes: a host the computer's
certificates verify keeps no anchor, and one its CA renews verifies still. A `--certificate` given is compared there
too, with the kept anchor's own fingerprint or, where the host keeps none, with what the address presents: a match is
said and keeps no anchor where there was none, and a mismatch is a refusal that names both. Otherwise the host's
certificate files read over root SSH (`read_over_root_ssh`: one command, each file looked at before it is read, none
larger than a certificate file is): its CA where what it presents chains to it with the name checked (`trust_kind: ca`,
no fingerprint, so the CA's renewals verify), else the node's own certificate without its chain (`node`), its
fingerprint recorded beside it; where neither verifies and what it presents is the host's own under another name (the
node's own certificate, byte for byte, or one the host's CA signed, and nothing else), that certificate as the node's
own, held to its fingerprint and not to a name, which the client then reaches at any address. The node's file is read
for its first certificate alone: a custom certificate is served with its chain, and an intermediate of it taken for an
anchor would pass whatever it issued, for any name, for the host's own. Where what it presents is none of the host's, or
nothing, a refusal marked `impostor`. Where root SSH does not answer, is not tried or reads no certificate, nothing on
the host vouches for what the address presents: its fingerprint is shown, and it is kept, as the node's own, only on the
person's yes or where it is `--certificate`'s; with nobody there (`ask` None) and none given, a refusal that names
`--certificate`. An anchor that no longer verifies is put in place of the one kept only on a yes that shows both
fingerprints, or where the new one was given. No sentence says to check less. What is kept is made through
`pve/pin.py`'s `kept` once before it is returned, the one way a context is made here; the probes' contexts are
`trust.py`'s, and its probe that checks no name (`check_hostname=False`), which carries no credential, is handed only an
anchor held to a fingerprint, the certificate the address presents, or, inside `proxmox_trust_presented`, the host's CA
as root SSH read it: never the node's file, whose chain would make an anchor of each intermediate. A yes is the person's
answer `True` and nothing else. A `Kept` carries the `Pin` made of what it keeps (`pinned`), the one connection its
caller sends the token through, so that no caller makes it again from what it wrote or read back; a `Refusal` carries
none, and its `impostor` says only why. The caller writes what is kept through `fleet/hosts.py`'s `write`, the anchor
with its kind and fingerprint. `tests/unit/test_aw_engine_certificate.py` holds it against local TLS servers made as Proxmox
makes its certificates, each case of today's `init_check.yml` carried, the host's files read over root SSH from a
stand-in machine (`tests/unit/aw_machine.py`, 16.10), and the rules that nothing in `engine/` builds a TLS context but
through `pin` and nothing of the new engine reads `trust.py`'s `_store_files`.

**`init` with nobody there: `init.py`.** `aw init --no-prompt`, or `aw init` with no terminal, asks nothing and makes
nothing on Proxmox. It takes one typed mapping, `Given`: each value held to its kind by today's `init.init_answer` and
Proxmox's own format of a name, and the token's secret from `--token-secret-file`, made absolute from the folder `init`
was started in, or from `AGENT_WAREHOUSE_TOKEN_SECRET`, never from the command line and never both; a word after the
options is the one check's refusal, never said. The variable is taken out of the process's own environment into a
`Secret` at the very start of `init` (`taken`), as today's engine takes its own such variable out at once, so that
nothing the run starts holds it: not root SSH's child, nor any other the runner or `runs/children.py` starts. A home
whose root or `fleet/` is a link is refused before the run's job is made, and again by `unattended` before anything is
sent or written, so that nothing is written through it, and the same command finishes once the folder itself is in its
place. As its run, under the fleet's lock, `unattended` reads what is set (the host's folder, the three files of
overrides, `fleet.json` and the vault, opened as `vaultkey.py` finds its key: a fleet
with no key file yet has its key made once every check has passed, and a key named elsewhere for a fleet with no vault
yet is `NoVaultYet`'s refusal, as is a pool the host's `settings.yml` sets that is no name of a pool, which the read
holds to `proxmox_pool`'s form, the rule `--pool` is held to, naming the file); refuses a value given that differs from
the one set, naming both, the secret unsaid, with nothing written; works out the API's certificate with
`certificate.py`'s `work_out` and nobody to ask, the host's own certificates read over root SSH where nothing else
vouches for them; and, where the secret was given, reads the pool with it through the `Pin` the `Kept` carries. Holding
a token Proxmox takes for a pool whose comment carries a mark with `controller=none` is the authority: `fleet.json`
takes the mark's fleet ID. A mark that names a control VM, another fleet than `fleet.json`'s, none or none that reads,
and a pool or a token Proxmox refuses, are refusals with nothing written; with no secret given the fleet keeps its ID,
or a new one gets one. Then each file through its one writer, none of whose locks is held around another's, each only
where nothing is set: the key (`vaultkey.make_key` at `vault_key_file`'s place), `fleet.json`, the vault
(`sealed.create`, then the secret as the host's `token`), `host.yml` and its anchor (`hosts.write`), with `made`
recording by ID what the administrator made by hand where the secret was given, or what the offer made (below), the
host's storage, bridge and pool (`aw-fleet` where none is given or set), the fleet's `github_username` and `vpn`, and
`client.yml`'s current host. The names written where none is given are the engine's own: the user
`agent-warehouse@pve`, the token `aw`, the pool `aw-fleet`, and the first host `pve`. `made` records the pool, the user
and the token only where each is that default (`OWN_NAMES`), the offer's roles where each is one of `access.ROLES` and
its grants where the role is, the user or token is the engine's own and the path is of Proxmox's form, never a name the
person gave or `host.yml` sets, which may be an object of the person's own that a
cleanup by `made` would then remove; the run's report names the fleet the pool's mark gives wherever the secret was
given, though `made` may then record nothing. A run stopped after any write goes on from what it wrote, and the same
run again writes nothing. The mode another card builds (`--machines`) is refused by name until it lands.

**`init` at a terminal: `attended`.** With standard input a terminal whose foreground the run is, as `runs/ask.py`'s
`terminal()` decides for each question (16.8), and no `--no-prompt`, the same steps run drawn live (`INIT`, one stage
whose steps are the questions), and each question whose value is still unset is asked, in plain Python, through
`runs/ask.py`'s `ask` alone (16.8): nothing in `engine/` or `fleet/` reads `/dev/tty` or uses `getpass`. The questions
are today's in their words, in the report's first-run order, the host first: its address; the token's ID, where no
`host.yml` names one (Enter keeps the engine's own, and so does a second answer that is not a token ID, said, as its
second asking says); E3's question whether to trust a certificate nothing on the host vouches for, a yes or no whose
safe answer is no; the token's secret, hidden, where the vault holds none and none is given; the node, the storage and
the bridge, each a menu of what the host lists to the token, read with it, today's rows above it saying what the token
lacks on each (`init.init_candidates`, against the privileges `pve/privileges.py`'s `needed` gives each kind), a yes or
no where only one can be used, yes marked first, so that Enter at the menu writes the name the host reported and no or
Esc leaves it (typed, where no menu can be drawn, Enter there leaves it, as its prompt says, since `ask` gives a typed
Enter and Esc the one default); the SSH key, asked only where `vm_ssh_public_keys` is unset and `~/.ssh/id_ed25519.pub`,
which its default reads at every run, gives none, as a menu of the other public keys in `~/.ssh` (each a regular file of
its own, never read through a link or waited on as a pipe); whose repositories are cloned; the VPN, a menu, `none`
first, then `tailscale`, written as `vpn`; and Tailscale's auth key, hidden, only once `tailscale` is chosen, given or
set, where the vault holds none and `tailscale_auth_method` is not `web`, sealed in the vault and never taken from the
command line. A typed answer is held to its kind, asked once more saying the rule where it does not fit, and left where
Enter (Esc at a menu) is pressed, or where a second does not fit, said. Each answer is then a value given, and the run
goes on as with nobody there, writing only what is unset; with the host's address left, only the fleet's own answers are
written. What was left is said, and NEXT STEPS keeps the line that asks again; one Ctrl-C at a question ends the run
with nothing written. A run in the background of its terminal (`aw init &`) is one with nobody there, never drawn: the
live region's change of the terminal's settings from there would have the terminal stop it (`SIGTTOU`) before its first
question. `tests/unit/test_aw_engine_init.py` holds it at a pseudo-terminal of its own, against the fake.

**The offer, and `--print-commands`.** At a terminal alone, between the token's ID and its secret and once the
certificate is settled, `attended` offers to make what is missing on the host: `as_script` rendered for the host's
values and the fleet's ID (`fleet.json`'s where there is one) is run there as root over root SSH in batch mode, `/bin/sh
-s` reading it from standard input, no copy left on the host. The node is read first where `settings.yml` names none
(`hostname -s`); the script's `--list` gives the storages and bridges where none is given or set (the one in effect
where the node lists it, else a question); then its `--dry-run`, with `--no-token` where the vault holds the token's
secret or one is given. Where something is missing, the plan (each object of the list with its privileges, and whether
it is there), the dry run's commands and what the script said are shown with today's question, "Set it up now? y or n",
no first, and the script runs on a `y` alone. On the `y`, `fleet.json` is written first where there is none, with the
ID the script marks the pool with, so that a run cut after it finds the same ID; the script, the vault's write, `made`'s
record and any deletion then run as one shielded phase (a Ctrl-C lets it finish), and where the run is ended inside it
anyway the token's delete line is said (a token whose secret the dry run said it makes again excepted, card E8: then
that the next `init` asks again; the rescue itself reads the run's own token line, since the script decides again).
Its standard output, the token's secret, is held to a UUID and sealed in the
vault before anything else runs, then read back; where it is not there as printed, where the script printed none, or
where it failed after it made the token, the token the run made is deleted again and `init` stops, and where the
connection is lost after the token, the delete line is said. The token is the run's own only where the script said a
command after its line, finished, or said it made it (in an `error:` line of its own, never a `+` line, which may quote
a pool's comment): its line is said before the add, so a run that ended by itself on
that line had its add refused, and nothing is deleted. What it made of the engine's own names, the pool, the user,
the token, the three roles and their grants for the engine's user or token, is recorded in `host.yml`'s `made` with
this engine its maker, read from the commands the script said it ran (its `+` lines). No, Esc, Enter, root SSH that
does not answer, a script that refuses, nothing missing, and a token there whose secret is held nowhere here, in a
pool nothing shows is this fleet's, leave the host as it was, said, and the secret is then asked. With nobody there
no offer is made.

**The pool's mark on the offer** (card E8). Before the dry run the pool is read as root reads it (`pvesh get /pools
--poolid`, `pve/script.py`'s `pool_read`); it decides only what is asked, and the script decides again. A pool whose
comment carries no mark is taken only where it is named (`--pool`, or the host's `proxmox_pool`), its comment and its
members shown in the question and the script run on the `y` with `--pool <name> --take-unmarked`; else it is refused
naming both ways, the by-hand road in that same form, as it is where a secret given reads it with nobody there. Where no
token's secret is held or given and the comment carries a mark with no control VM, another fleet's, or this fleet's with
its token there, `init` asks which of two things is meant and does neither unasked: taking the fleet over (where
`fleet.json` names no fleet or the mark's) runs the script with the mark's ID and `--regenerate`, every other copy of the
token's secret then failing, and `fleet.json` keeps the mark's ID; using another pool (where `settings.yml` names none)
asks its name and, where no token was named, the token's full ID, and makes a fleet of this computer's own there. Where
the mark's ID is `fleet.json`'s, a run of this computer's cut before the secret was kept or another computer of the
fleet's, the first is asked as making the fleet's own token's secret again, never as a take-over. A token whose secret
the run said it makes again (`token modify`) is never deleted where the vault cannot be written: the run did not
make it, and deleting it would leave the fleet with none; the next `init` asks again. One a take-over adds, the token
gone, is the run's own, and deleted as any token the run made (review 76). Enter or Esc does neither; a mark naming a control VM is the script's
refusal, nothing asked. `aw init --print-commands` prints the same script on standard output, and on standard error how to
run it as root, reading `host.yml`, the settings and `fleet.json` alone: no vault, run, lock or request. Its one write
(card E8): with no `fleet.json`, the fleet's ID it renders is written there and said, so that a second print and the
offer render the same ID, and a by-hand run again after a partial one is never refused as another fleet's.

**The checks: `init_checks.py`.** Once `init` has written, at a terminal or with nobody there, it checks what a first
`deploy` needs of the host and says each check as one of today's lines, `ok`, `!!`, `--` or `note` and its text, with
the fix after a colon; it exits 1 exactly where it said a `!!` line, and `aw init --check` runs the same checks under
the same lock, takes no value but `--host` (it refuses `--set` too), and writes nothing. The host is opened as every
workflow opens it (`fleet/opened.py`'s `connect`, lenient as `settings` and `vault` are, from the fleet's files alone,
but a further host's (below): never a `--set` value of the run), through the anchor `init` kept, so no check runs before
the certificate is settled. In today's order: the API answers, is Proxmox VE 9 or later and takes the token; the pool is
there and its mark names this fleet with `controller=none` (`fleet/mark.py`'s sentence for any other); the node, settled
where the host's own `settings.yml` sets none (the one node online, else the node root SSH's `hostname -s` names); the
base template, by `template.py`'s `compare`; the permission check, `/access/permissions` read with no `userid` on the
pool, the storage, each bridge a clone's source has a network card on and the node, each held to what
`pve/privileges.py`'s `needed` says there and each missing privilege named with its path; `VM.Audit` on `/vms` under
`proxmox_audit_scope: node` (a `--` line where it is not held) and anything more there, which changes every VM of the
node (a `!!` line); a template of the person's own held to its clone grant; root SSH, where the template is missing or
is to be rebuilt; the route, by `routes.py`'s `settle`, and the hop's login where it goes through the host; the storage
and, before the template is built, the bridge, each as the node lists it; and that the machines get an SSH key. Each
name the host reports (the nodes, `hostname -s`, the storages and bridges listed, the template's cards' bridges) is held
to Proxmox's own format (`checks.proxmox_name`) before it is settled, written or put in a path; a name outside it is
said as JSON alone, and a name the client refuses is a `!!` line. Every call is a read: the client's GETs, which follow
no redirect, and `true` and `hostname -s` over root SSH. The node, the storage and the bridge settled are written, under
`init`'s own rule, only where the host's `settings.yml` sets none and only with nobody there, but a further host's
(below): at a terminal the person's answers are the rule, so what they left for later stays unset, and `--check` writes
nothing. At a terminal the lines are one report of matter `init_check`, said through `report.py`'s `say` after
`init_settings`' own, its NEXT STEPS line init's printer's line with what running it again does; with nobody there they
are printed, a sentence after them saying what is left. `tests/unit/test_aw_engine_init_checks.py` holds them against
the fake Proxmox and a refusing `ssh`.

**A further host: `aw init --host <name>`** (card E10). Where the fleet holds another host's folder than the one the run
sets up (`init.py`'s `_further`; `fleet/hosts.py`'s `first` once the host's own folder is there), the run is a further
host's, with nobody there or at a terminal, by the same road: `--host` held to the host name's pattern and
`hosts.model.name_refusal`, so that a value with a dot or a colon is refused as any other name outside it, never taken
for `--address`; its address given or asked; the administrator's authority taken in the same two ways, the offer over
root SSH and `--print-commands`, each rendered with `fleet.json`'s ID, so that the host's pool carries the fleet's own
mark, and a mark a secret given reads that names another fleet is refused. Its token's secret is that host's named
secret in the one vault (`token@<name>`); `AGENT_WAREHOUSE_TOKEN_SECRET` is refused, since it may hold the first host's
secret, exported for it, and the secret then comes from a file or is typed. Nothing of the fleet's own is taken or
asked: `--github-user` and `--tailscale` are refused, and at a terminal neither the SSH key, GitHub, the VPN nor
Tailscale's auth key is asked, nor the node, the storage and the bridge, which its checks settle. What the host sets
for itself in its own `settings.yml` (the storage, the bridge and the pool given, `aw-fleet` where none is, and the
node) is not written by the run but staged (`init.py`'s `Further`), and the checks, given it (`init_checks.py`'s
`staged`, which they read as set, the host opened with it as a run's values), write it with what they settle only once
every check has passed, no `!!` and no `--` line, each where the file sets none (`_keep`); else none of it, said, and
runs against the host refuse it until a run of init has passed them all (the node, the storage and the bridge are each
host's `always`). The first host's files, the fleet's own and `client.yml`'s current host are left as they are.
`tests/unit/test_aw_engine_init.py` holds it against two fake Proxmox hosts with different secrets.

`tests/unit/test_aw_engine_init.py` holds `init` against the fake Proxmox, through `tools/aw` with a refusing `ssh`
first on `PATH` and in this process with root SSH a reader of the test's own; its runs through `tools/aw` from a home
short enough for the host's shared socket, so that root SSH starts its child with that socket, as from a person's home,
and a recording `ssh` holds that the child's environment has neither the variable nor the secret; with the invariant
that no file under the fleet's home holds the secret in clear and the vault on disk is sealed. `tests/unit/fleet_fixture.py`
gives any other test a whole fleet folder in one call, made by this run.

**The fleet as the engine reads it: `fleet.py`.** A host is opened by `fleet/opened.py`'s `connect(home, reached)`
(16.9), which `status` opens one by too; `fleet.py` gives it, its `Host` and its `Refused` as its own. A `Target` is a
machine a run reads: its kind (`vm`), name, identity (`agent-warehouse-<its SMBIOS UUID>`), host, node, VMID, the
account a run logs in as, its address, its record, whether it runs, and the digest of the configuration it was read
from. What is the fleet's is its pool's members with a valid record, never a label, by `pve/ownership.py`'s one rule
(16.5), which `status` judges by too. Each target a read makes carries the `Member` that module's `Member.read` made of
its configuration as the client read it, with the other members of the pool that carry its UUID (`Target.member`; the
one call of `Member.read` in `lib/`), which no constructor takes and `dataclasses.replace` leaves none of;
`refusal(target)` asks the rule of it, and `owned(target, job=, clone=)` raises it or returns the `Owned` only
that module makes, the identity and the digest the decision was made on. A target made by hand, or changed after its
read, a record given to it above all, is a `TypeError` to both: what is judged is what was read, never a target's
fields. A changing read gives each target its `Owned` (`Target.owned`); `members` and a read that changes nothing give
targets for reading alone. No write takes a `Target`: the client's writes of a VM and `record.py`'s take an `Owned`, so
every target a change is made on passes `owned`, and the client holds the VM at that VMID to the read again (16.5), so
that no write reaches a VM that took the VMID since (`failure-modes.md`'s racy VMIDs), and before a deletion, which
Proxmox takes with no digest, to the read's digest too; `destroy` owns a VM again from a read made after its stop,
which may apply pending changes. It refuses a member with no record of this engine's, whatever tags it carries; a record
that names another VM's UUID, or is a template's, a golden's or a build's on a VM that is no template (a copy: a clone
sent without a description carries its source's notes); a VM's record that names no stage; a record still in its
clone's first form, but to the run that cloned it, by its own job, which the record names, and its own clone's task,
whose VMID is the member's (`job`, `clone`: never read from the record judged, whose job anyone may read), which writes
the rest; a member whose configuration or record cannot be read, one the token holds `NoAccess` on among them; two
members with one UUID, both; and a template.

- `requested(host, names, create=..., change=...)` holds each name before Proxmox is asked anything: the pattern of a
  VM's name, no host's name of the fleet's nor `localhost`, with the aliases on no name `ssh` would take for another
  host, and for a deploy that would create no name another host's alias records and none Proxmox takes for no VM (the
  client's `VM_NAME`). A deploy that would create is a changing run: asked without `change`, it is refused. A changing
  run then reads the pool's mark by `mark.py`'s rule before anything else. Named, each is the one member of its name: a
  name two members carry is refused, one none carries is `Missing`, or for a deploy that would create, a name to
  create; with none, a creating deploy asks for the default VM and every other run for every member, a member named
  after a host, one whose name another carries and, changing, one it may not change each left out and said. A deploy
  that would create refuses a VM of a new name outside the pool wherever the token may read `/vms` (`VM.Audit`), with
  today's sentence, a clone in flight there said as one, and says where it may not. An entry the listing gives with no
  name, as it lists a guest its node has not reported yet, a clone in flight above all, is read by its own
  configuration first: one of a new name is refused as above, a clone in flight that names no VM yet is refused as one
  that may be of the name, and one that cannot be read (a node that is down) is said and passed. And it asks each other
  host whether the name is a VM of its pool, refusing one found there with today's sentence, which names the line that
  deploys it on that host, and saying and passing a host that does not answer, or whose files say no client that can
  be made.
- Whether a VM runs is read from its own endpoint, never a listing's status, the cluster's or a pool's, which lags a
  run's own change. Its address is its guest agent's alone, on its own network card, through `pve/agent.py`'s
  `address`, and held against every other running member the fleet owns, whether the read asked for it or not: one that
  reports the same is left without one, both said, so that a VM asked alone is never given its sibling's address; a
  token refused the agent's call ends the read at once.
- `members(host)` gives every VM of the pool as a target for reading, `address(host, target)` one VM's address, asked
  once and held against the others' as a read holds it (`Shared` where another reports it too: whatever answers there
  would be taken for the VM, its host key filed under its identity at its first use), and `count_made_by_agent(host)`
  how many of the fleet's own an agent's run made.

`tests/unit/test_aw_fleet.py` holds it on a whole fleet folder against the fake Proxmox under enforcement, carrying
`tests/unit/test_vm_names.py`'s cases and the guest addresses' of `tests/unit/test_filters.py`, with scans that nothing
here reads a label or a listing's status (the fake lags a pool's listing as it lags the cluster's), that `requested`
returns a target for a change through `owned`, that nothing here holds a record to its VM and nothing but
`ownership.py`'s `refusal` (and `build_refusal`) reads the UUID a record names; and that every writer refuses a target,
a deletion of a VM changed since its read is refused and the VM stays, a VM asked alone or just started that reports a
sibling's address is not given it, a target given a record after its read and a `Member` made by hand are never
owned, and a first form is owned by the run that cloned it alone, never by the job its record names.

**The route to the machines: `routes.py`.** A run settles how it reaches its host's machines once, and every
connection it makes holds to that: `settle(run, host)` is today's rule, `route.py` as it stands, called through
`ssh/connections.py` (16.6). The setting `vm_ssh_route` is this computer's own for the run's host, read from
`client.yml`'s map `hosts` under the host's name and never another host's; `direct` and `host` say what they say, and
`auto` is `host` where every address `ssh` dials for the host is a tailnet one - the host as its own `ssh -G` resolves
it, a `HostName` of the person's among it, then that name's addresses here - worked out at each run and never kept. The
hop is the Proxmox host as `ssh` reaches it, its own `Target` made by `host_target`, which root SSH's read of the host's
certificates takes too: `host.yml`'s address, `ssh_user` (or `jump`, another account there) and `ssh_port`, this
computer's `ssh_identity_file` and `ssh_executable` for it. Where the route goes through the host and `vm_ssh_options`
for every machine names no jump, the hop's login is made there, before anything else of the run; otherwise the first
machine that takes the hop makes it, and a run where none does makes none. A route that cannot be settled - an `ssh -G`
under `auto` that gave no answer or exited with an error (a line of the person's configuration it refuses for the host,
said in `ssh`'s own words), the route through the host from a home too long for its socket - and a hop whose login fails
stop the run (`Unusable`, a `Refused`) with the sentence that names `vm_ssh_route`, and nothing dials a machine: there
is no fallback either way.

`Route.targets(machines)` is the one function that makes a machine's SSH arguments: a `runner.Target` of its identity,
its one address, the account a run logs in as, `ssh_executable`, and the person's `vm_ssh_options` for it (the run's,
else `client.yml`'s for that machine, else for every machine); where the route goes through the host and the machine
brings no jump of its own - a `ProxyJump`, a `-J` or a `ProxyCommand` other than `none` among those options, or in what
`ssh -G` resolves for its address from the person's own `~/.ssh/config` - the hop's `ProxyCommand` to its own address,
after the product's options, which `ssh` keeps since they come first (`target_for`), which marks each `Target` with the
route it was given (`routed_as`: `direct`, `hop` or `own`) and with what it decided that from (`routed_from`: its
address, account, port, `ssh` program and options, and the hop it gave). The runner makes no command line of a
machine's `Target` unmarked, nor of one marked through the hop that no longer has it, nor of one whose fields of
`routed_from` changed since, by a `dataclasses.replace` or any other way: a `Target` made anywhere but here, or changed
after `targets` but in a first use's known-hosts file (`connections.first_use`), dials nothing, and a step that needs
another takes it again from `targets`. Only
a Proxmox VM takes the hop. Before anything connects, each machine's own `ssh` is asked with its whole line and no
configuration file which proxy it takes, and any other than the route's is refused (`held`): every machine first, then
the hop's login, then the forward. Where any takes the hop, its login is made, once a run, then one forward of its own
to the first of them by name (`Connections.forward`): a host that refuses to forward there, or an `ssh` that cannot use
it as a hop, stops the run, its sentence naming the account `host.yml`'s `jump` gives, and every later `targets` of the
run raises it again, as does one that waited on that forward, so a step that caught it for one machine, or reaches its
machines at once, hands out no hop through that host; a forward cut before
its verdict is made again; a machine that says nothing to the forward, or a login for it that does not finish, is said
once and stops nothing, the machine failing alone when it is first reached. `Route.ready()` makes sure of the shared
connection again wherever its login was made, before a step that reaches many machines at once and after a wait on a
person, and fails no machine: a hop that is gone shows as each machine not answering. The run says once, as its
report, how the machines
are reached through the host (with `aw status`, the printer's), each machine reached by a jump of its own, and each of
the connections' notes. `Route.proxy_jump` is the hop as an alias's `ProxyJump` names it, only once its login was made,
so an alias gains it only through a hop that was checked. Every `ssh` for the hop itself is `route.py`'s, under its hard
limits; never `-O exit`.

`tests/unit/test_aw_routes.py` holds it on a whole fleet folder, the host's VMs and the host itself stand-ins
(`tests/unit/aw_machine.py`, 16.10), the host the hop at the fake's own address, carrying
`tests/unit/test_vm_route.py`'s cases: each VM through the host after the product's options, its socket named with the
hop, the login once at the run's start and one forward to the first VM by name; `auto` both ways, worked out at each run;
a login refused, forwarding prohibited and `ssh` failing to use the host each stopping the run before any VM is
reached; a VM that says nothing said and stopping nothing; a jump of a VM's own, from its options or the person's
configuration, and one every VM is given, which puts the login off for good; a line that would lose the product's
options refused before anything connects; a stale shared connection made again. And a configure through the stand-in
hop, layer 1's own `ssh` riding it, which with the hop refused stops before any connection to the VM; the route this
computer's own for each host, one with no setting of its own reading the default and never another's; only a VM
taking the hop; the notes said once; each road on which the route cannot be settled stopping the run with nothing
reached (a long home, an `ssh -G` past its limit, ended by a signal, or exiting with an error, the stand-in's `slow-G`
and `killed-G` and a configuration whose `ControlPath` `ssh` refuses); every machine held before the login and the
forward; a forward's verdict that stopped the run raised again, to a `targets` that waited on it too, and one cut
before its verdict made again; a `Target` the route did not mark, or marked through the hop and without it, or one
whose address, account, port, `ssh` program or options changed since it was marked straight, by a jump of its own or
through the hop, refused by the runner. And two rules: every fleet a
test makes pins `vm_ssh_route: direct` before anything reads it (`tests/unit/fleet_fixture.py`'s `pinned`, each maker
read back, `whole_fleet`'s as `aw init` starts); and no module of the new engine but `routes.py` and `ssh/`'s own makes
a `Target` or changes one's route after it was made, by a scan that reads every subpackage and sees the runner under
any name (an alias, an attribute chain, a bound name, `partial`, `type(target)(...)`, `importlib`), a `replace` of a
`Target`'s route and the mark, or what it was decided from, set anywhere but `target_for`; a change the scan cannot
see is the runner's to refuse.

**A VM made: `create.py`, and `deploy`'s handler: `deploy.py`.** `aw deploy --only provision` makes or resumes each VM
named, the default VM where none is (`vm_default_name`), side by side, under each VM's lock (C7's `machines`); `aw
deploy --only template` is `template.ensure` and nothing after it (above), under the fleet's lock held exclusively, since
it names no machine; every other stage of `deploy` but `--only configure` (`configure.py`, below) and `--only
ssh-config` (`aliases.py`, below: under the fleet's lock where it names no VM, each VM's where it does), and
`--check`, `--adopt` and `--take-over`, is refused naming the card that builds it; `--remove-key` is a configure's
alone (below). Before anything is created, the run reads the fleet (`fleet.requested`, a changing read, so a VM it may not
change refuses the run), settles the route (`routes.settle`, whose hop that cannot be used stops the run), refuses a VM
with no key in `vm_ssh_public_keys` and no console password in the vault (`vm_password`), and compares the base template
(`template.compare`): only one it may clone from is cloned from, any other said with why, and none with no cloud-init
drive, which a VM's user and keys reach it by (its configuration read once, as today's custom template check reads it;
the comparison does not hold it). Then for each VM, each step an event of the run and each safe to run again:

- **The clone.** Under the short section `nextid-<host>` the next free VMID is asked for and the clone sent onto it,
  full, onto `proxmox_storage`, into the pool, carrying the record's first form (kind `vm`, stage `cloning`, the run's
  job); `template-<host>` is held shared until its task ends, so a rebuild waits for it, and `nextid` is let go once
  the call has answered. A VMID taken - at the call, or in the task by another run's clone that came first - is asked
  for again, and nothing is deleted there. A clone whose answer was lost is settled by reading that VMID until no
  clone's lock holds it; where this run's first form landed there, the VM fails, since no task of the run's owns it.
  A clone joins the pool only once its task ends, so where the token may read no VM outside the pool (`VM.Audit` on
  `/vms`, `proxmox_audit_scope: pool`) or that VMID's configuration is refused, the VM fails at once, saying to delete
  the clone where it landed: nothing is cloned again. A clone cannot be undone, so from its send to its record's write
  the run's shield holds a Ctrl-C off (`Context.shield`), its task followed with no stop; a second Ctrl-C still ends it.
- **Its record and its label.** The new member is owned by the run's job and its own clone's task (`fleet.owned`), its
  SMBIOS UUID read (a VM with none fails, saying what to set), its record written - stage `unfinished`, its own list of
  repositories where `--repos` gives one, the agent's mark where the job's starter is an agent's - and then, in a call
  of its own, `vm_label`. Its events, named by a stand-in key until then, are joined to its identity.
- **Needs configuration.** `need_configuration(record, config, force)` decides alone whether the settings call, the
  start and the record write after it are made: a VM with no cloud-init user, a record still the first form or not
  `provisioned`, or a repair (`proxmox_vm_force_update`). It reads no ownership. A provisioned VM is otherwise left as
  it is - a stopped one stays stopped unless `--start` - and a second run sends nothing but reads.
- **The settings call** (`settings_call`), through the client's `edit_config`, holding the VM's UUID: the cloud-init
  user, its keys, its network, the nameservers and search domain where set, the start at boot, the guest agent, the
  cores, the memory and its minimum, `ciupgrade=0` only where `vm_first_boot_upgrade` is false, and `cipassword` only
  where the vault holds `vm_password`. A repair of a VM created with a size of its own sends no part of it the run
  does not give. The root disk is grown at the VM's create alone, a create resumed included, and only where it is
  smaller than asked; a repair of a provisioned VM given a `--disk` larger than its disk is refused, as another size
  for a VM that exists is. A stop that came while the settings call's answer was outstanding is looked at once it has
  answered: nothing more is written, neither the repositories nor the resize, and the next run resumes the VM, not yet
  `provisioned`, from its settings.
- **The start and the address.** The VM is started where it does not run, its task followed; its address is waited
  for from its guest agent on its own network card, then held against every running member's; then one record write
  sets `provisioned` and the size the run gave it, its unit in capital (`fleet/overrides.py`'s `capital`, the one
  reader of a size, the settings' and the options' alike).
- **The host key.** The VM's `Target` comes from `Route.targets`; for a VM the run created, or one resumed before its
  first configure, with no key filed under its identity, `Route.first_use` gives the connection of its own that takes
  the key into a fresh scratch file in the run's directory, tried again with the same `Target` while its `sshd` comes
  up; the key is read back (`hostkeys.taken`) and filed under the identity (`hostkeys.file`, which refuses a second
  one), and only then is the machine's master made sure of (`Route.ensure`).

A step that fails fails its VM alone, said; the run then exits 2 (`EXITS`' `failed`). A step a stop ended ends its VM
stopped (`cancelled` in the job's record, the stage it reached kept), never failed, and so does a wait of the run's own
that a stop ends by raising `asyncio.CancelledError` - the guest agent's for the address, a shield not begun - which
`Making.run` takes as the VM's end; where the run itself is being cancelled (the grace's end, a second Ctrl-C that
comes while the run waits) the VM's end is written and the cancellation goes on. Where a second Ctrl-C, a SIGTERM or a
SIGHUP ends the run before the VM's handler runs, the ladder's end writes it, `cancelled` at the stage it reached
(16.8). Each of the VM's writes, its settings, its resize and its start, is given the run's stop and sends nothing
once a stop came during its own read (`Unsent`, 16.5). One after the record's write
leaves the VM a member without `provisioned` that the next run resumes. One before it - a clone whose answer was lost,
whose follow failed or whose record could not be written - leaves the clone, where it landed, in its first form, which
no later run may own: each later run is refused, and the refusal (`pve/ownership.py`) says to delete that VMID in
Proxmox, then run it again. A refusal before anything was created exits 1, and a lock another run holds is refused by
`cli/main.py` in `runs/locks.py`'s `Held`'s sentence. `tests/unit/test_aw_create.py` holds it on a whole fleet folder
against the fake, the VMs stand-in machines (16.10): two VMs made in one run, then a second run that sends nothing but
reads; a VMID taken before the clone landed; a clone whose answer was lost sent once, under either audit scope; a failed
start resumed, and a running VM resumed never started again; a Ctrl-C during the clone held until its record is written;
a Ctrl-C during the agent's wait ending the VM `cancelled` at `provision` in the job's record; a Ctrl-C while the
settings call's answer is held by the fake (`fake/hold`) sending nothing after it, the next run resizing the VM; a
Ctrl-C during the resize's own read, and during the read before the start, sending neither; a second Ctrl-C right after
the first, and a SIGTERM, at the agent's wait still ending the VM `cancelled` at `provision`;
a stopped VM left stopped, and started with `--start`; a size for a VM the run creates, kept on a repair and refused for
one that exists, a larger disk on a repair too; `--repos` given, added to and refused; and, without the fake, the keys
of today's settings call, `need_configuration`'s cases and that it reads no ownership, the one size reader, and each
stage refused by its card.

**A VM deleted: `destroy.py`, and the names confirmed: `confirm.py`.** `aw destroy` deletes each VM named, the default
VM where none is (`vm_default_name`), with its disks, under each VM's lock (C7's `machines`). Before any job is made:
`--confirm=<names>` is held to the VMs given again in the command's own process, by `confirm.names`, the one validator
of a confirmation by names, which takes both as sets, each name with - for _, and says which name differs (the parse's
rule destroy.2 refused it first); the run is refused while `destroy_save_sessions` or `destroy_release` is on (both are
by default), naming cards G13 and G14, which save a VM's sessions and release it, and the two settings, so that no run
loses what only a VM holds; and with no terminal to ask at (`runs/ask.py`'s `terminal()`) and no `--confirm`, the answer
is no, said as the person's: the question not asked, then `Nothing was deleted: the deletion was not confirmed.` and the
line that answers it, exit 1. In the run, the live view made first:

- **The read.** The host is opened by `fleet.open_for_run` (the vault opened once, each `--set` value held to its
  secrets: deploy's way too), and the VMs read by `fleet.requested`'s deletion read: changing, so a name two members
  carry, a name no member carries (naming the pool: a VM outside it is out of the token's reach) and a VM the fleet does
  not own are refused; `whole`, refused while any member of the pool cannot be read, since one unread may carry a
  target's UUID (a member that the pool, listed once more, no longer names is gone, not unread: a sibling's deletion
  that ended between the listing and its configuration's read); and `addresses=False`, no guest agent asked.
  `destroy_allow_unrecorded` is `unrecorded`, which lifts pve/ownership.py's one refusal of a member with no record,
  said for each such VM before the question, and no other: never a template, a VM whose record names another UUID or is
  of another kind, one with no SMBIOS UUID, which its deletion is held to, or twins. Only `engine/destroy.py` asks for
  it in `lib/`.
- **The question**, today's: a `CONFIRM DELETION` block naming the VMs by the names Proxmox gives them, each made one
  line, their disks and, where the fleet has more than one host, the host; then `Type yes to continue:`, whose safe
  answer is "" (no). `yes`, or today's `true`, in any case, takes it; under `destroy_confirm: names` the VMs' names are
  typed in its place, held by `confirm.names`. Anything else is the person's answer, said in today's words (a report
  of the run), its check stage done and nothing deleted, and the run ends declined (`Context.decline`), exit 1: its
  record reads `declined`, and the view's head says `declined`, with no ✗ and no failed count (section 16.8).
- **Each VM, side by side.** Whether it runs is read from its own endpoint; one that runs is stopped (`stop`, its task
  followed with the run's stopping event, so that a Ctrl-C leaves it stopped and undeleted; sent once more where
  Proxmox's file lock refused it, as a stop seconds after a start may meet), then owned again through
  `fleet.requested`, since a stop may apply pending changes, and held to the VM confirmed, its identity and VMID. Then,
  in the run's shield (a Ctrl-C lets it finish and says so): the client's `delete` (the configuration read again and
  held to the Owned's identity and digest, `destroy-unreferenced-disks` alone, no `purge`), its task followed with no
  stop, and the VM confirmed gone from `resources("vm")` - no VM at its VMID, or another's - never from a refusal's
  words; a deletion whose answer was lost, or whose task's end could not be read, is decided there too. A task that
  ended `WARNINGS: <n>` is a success whose lines are said.
- **Its alias.** Still in the shield, only for a VM said gone, its stage `aliases` (declared beside the deletion's):
  `aliases.forget` takes its block out of the managed SSH config, unless the block records another host of the fleet,
  whose VM of the name it is (kept, and said); with `claude_ssh_config` off the stage is skipped. A removal that fails
  is said with the fleet-wide `aw deploy --only ssh-config` that prunes it, and fails nothing: the VM is gone.

Every VM is said in one line, which the view draws under its name: `deleted, with its disks.`, or `not deleted - <why>,
so it is still there: run aw destroy <name> again once that is fixed.` The run exits 0 where every VM was deleted, 2
(`EXITS`' `failed`) where one was not, the others gone on, and 130 where a person stopped it. Each report goes
through `report.py`'s `say`, matter `destroy`: a VM a stopped run left records `aw destroy <name>`, with the host's
flag, as its NEXT STEPS line; every other report, none. The VM's line in the
product's known-hosts file stays, the tombstone of its identity; nothing here writes a record or a session.
`tests/unit/test_aw_destroy.py` holds it on a whole fleet folder against the fake: a VM and its disks deleted
after its stop, a stopped one with none; the refusal while either setting is on, and the declined deletion with no
terminal, each before anything is sent; `--confirm` naming another VM refused, in the handler too; an unrecorded member
refused and deleted under the override, and a template, a VM outside the pool and another's record refused whatever is
given; a name two VMs carry; a member the token may not read; one VM of two failing, both lines and exit 2; a stop the
file lock refused sent once more, and one that failed otherwise never; the question at a terminal, yes and no, and the
names typed; a Ctrl-C during the deletion; a VM owned again after a stop that changed it, held to the VM confirmed; and,
without the fake, the names' validator, the question's words and what takes it, and the override's rule.

**A VM configured, the frame: `configure.py`.** `aw deploy --only configure` configures each VM named, or every VM of
the pool this fleet may change where none is (the fleet's lock then held whole); `--tags` beside VM names, with no
`--only`, is the same run. A configure asks for parts, today's six (`rules.CONFIGURE_ROLES`), every one where `--tags`
names none. The system play runs the three of layer 1 (`system.PARTS`); `claude_code`, `github_projects` and
`tailscale` are layer 2's (card G9): until then a `--tags` that names one is refused before anything is read, naming
G9, and a configure with no `--tags` runs the three and says by name that the other three were not run. The record's
stage becomes `finished` only where one configure ran all six (`stage_after`), so a VM stays `unfinished` until G9; the
converged digest stays empty, and so unwritten, until F28. Before anything reaches a VM the run reads the fleet by a
changing read (`fleet.requested`: a VM it may not change refuses the run where it is named, and is left out and said
where none is) and settles the route; `Route.ready()` is asked before the machines are reached side by side, under a
task group. Then for each VM, each step an event of the run, declared as configure's (`DECLARED`: start, reach, key
taken off, system layer, home), and layer 1's own events lines of that machine's work:

- **Provisioned and running.** A VM whose record is not provisioned fails, naming the provision line. A stopped one is
  said and left, never waited on, unless the plan starts it (`start`, which only the whole chain, G10, gives: `deploy`
  refuses `--start` beside `--only configure`), through its Owned, its address then waited for.
- **Reached.** Its identity's key filed in the product's known-hosts file is held first (`layer1.key_filed`): with none,
  the VM fails before any connection, with the line that files it. Its `Target` is the route's (`Route.targets`, one VM
  a call, so that one refused fails it alone), its master made sure of (`Route.ensure`) and `true` run through the
  runner until its `sshd` answers, for `SSHD` seconds at most.
- **A key taken off.** Where `--remove-key` names a key (`Plan.remove_key`, its fingerprint held as `ssh-keygen -l`
  prints one), `keys.remove` takes it off the VM's account before its system layer (§16.10, "A person's keys"): a key
  `vm_ssh_public_keys` lists, or one this computer's ssh offers, is refused by `deploy` before the run, and again for
  each VM; one on no line of the VM's fails that VM with why, nothing changed; the lines it took are said.
- **The system layer.** The play's variables are made for the parts run (`wiring.role_variables`, the same parts as the
  child's `--tags`) and written in the run's directory through `system.variables`, held to every secret the run holds
  (each host's token, the console password), which layer 1 also strikes from what it says. A failed task fails the VM
  with the task named, the machine's name in place of its identity; cloud-init's own failure (`CLOUD_INIT_TASK`) is
  said in this engine's lines, `aw destroy` and the provision and configure lines with
  `--set configure_wait_for_cloud_init=false`, never today's `just` and `ANSIBLE_ARGS`; a VM layer 1 could not reach,
  and a child that ended otherwise before any task failed (a play that did not load among them), fail with its words;
  and a run that ended well with no recap heard from the product's callback fails too, since a callback of the same
  name beside the play or in a role was loaded in its place. A child the run's own stop ended (ansible-core's "User
  interrupted execution", or killed at the grace's end) fails nothing by its exit status alone: where the run is
  stopping and layer 1 said no task of the VM failed and none could not reach it, the step is not ended failed but left
  to the VM's `cancelled` end, which the model draws stopped (`runs/model.py`'s `BY_OUTCOME`), so the summary says
  where it stopped and no failure is drawn. A task that failed, or could not reach the VM, is still that VM's failure:
  its step ended failed and the VM's end `failed`, the run stopping or not (`Failed`'s `own`). A wait of the run's own a
  stop ends (the address of a VM it starts) ends the VM stopped, as `create.py`'s does.
- **The home.** The VM user's real home is read from the password database through the runner (`getent passwd`) and
  the workspace made from it as every reader makes it (`sessions.projects_dir`): each VM's outcome (`Configured`)
  carries both only where it succeeded, for G8's alias.
- **The record**, written through its Owned only where the stage changed; otherwise nothing is sent.

A step that fails fails its VM alone, and the run exits 2; a route that cannot be used stops it. The interpreter and
`sudo` are named by their paths in what layer 1 generates, and nothing here passes an option that could change them,
nor a test's connection. `tests/unit/test_aw_configure.py` holds it against the fake and the stand-in machines, each VM
made first by `--only provision`: a stopped VM left and never reached, a VM with no key filed refused before any
connection, a part of layer 2 refused naming G9, a VM the fleet may not change refused or left out; then in the test's
process, the system play swapped for a play of one task a part over F3's test-only local connection, which the test
alone hands layer 1: the three parts run and the VM left `unfinished` with layer 2's named and nothing written, every
connection by its identity, strict, with the person's `vm_ssh_options`; `--tags common` alone, and `--tags` with no
names reaching every VM; a failed task, cloud-init's, a planted callback, the run's secrets struck; a stopped VM
started where the plan says, a stop during its agent's wait ending it `cancelled` at `configure`, and a task that
failed as the stop came drawn and ended `failed`, its step with it; through the kit
at a terminal with the play swapped the same way (`aw_kit.Layer1`), a Ctrl-C in the middle of a task of the child
ending the run 130 with the system layer drawn stopped and nothing drawn failed; and, over the stand-in's real
connection, a configure through the stand-in hop, and a hop
refused that stops before the VM.

**The aliases: `aliases.py`.** `aw deploy --only ssh-config` gives each VM named, or every VM of the pool the fleet
owns where none is, its alias in the managed SSH config (`~/.ssh/agent-warehouse.conf` under the run's home,
`hosts.ALIASES`), from a live read of the fleet that changes nothing (`fleet.requested`, `change=False`); `deploy` runs
it under the fleet's lock where it names no VM, each VM's where it does, and it is read-only against Proxmox. A VM
`fleet.refusal` says the fleet does not own refuses the run where it is named and is left out, said, where none is; a
VM with no address (stopped, booting, without its agent) keeps the alias it had, said. A block is today's
(`playbooks/tasks/ssh_config.yml`), between `# BEGIN agent-warehouse: <name>` and its `# END`: `# origin: <host>`,
`Host`, `HostName` (the address, or `<name>.<claude_ssh_host_domain>`, a domain never used through the hop), `User`,
`ProxyJump` where it goes through the host, `HostKeyAlias` (the VM's identity), `UserKnownHostsFile` (the product's
known-hosts file), `CheckHostIP no`, `UpdateHostKeys no`, `StrictHostKeyChecking` (accept-new, or a stricter one the
options give), `IdentityFile`, `ForwardAgent`, then `claude_ssh_options`. So a VM's key is looked up under its
identity, never an address, in the product's file alone, which the alias never writes but through the person's own ssh
taking a key at first use under that identity; a deleted VM's key stays there. Refused before anything is written: a
name, an account, an address, an identity (`checks.HOST_KEY_ALIAS_RE`) or a jump that is not one plain word; an option
the block writes itself, or a `StrictHostKeyChecking` looser than accept-new; a name a `Host` line of the person's own
config names; and, named, a name `ssh` would take for another host (`checks.ssh_alias_refusals`: an address, a name
this computer reaches a host of the fleet by, a dotted name given by no run that names it) or whose block records
another host of the fleet (`proxmox.ssh_config_elsewhere`), which fleet-wide are left out and said, the dotted one with
the line that names it and an alias it has left as it was. A block of a host the fleet no longer has is taken over.

Each write is a compare-and-swap: the file read, the text worked out, validated by `ssh -G -F` of a scratch copy of that
check's own in the run's directory (`tempfile.mkstemp`, `ssh-config-check-`, removed after, so that writes of one run
side by side never read or remove each other's) for each alias written, and replaced (`store.replace`, 0600 when new,
its folder 0700) under the file's lock only where it is still what was read, otherwise worked out again, at most `TRIES`
times; a text that changes nothing is not written. Then the `Include` of the managed file at the top of the person's
`~/.ssh/config`, once: none with `claude_ssh_config_include` off (the line said to add by hand), none where the line is
there before the file's first `Host` or `Match` line (an `Include` below one is read for that block's hosts alone, so
there `ssh` is asked), or where a line names the file in a spelling `ssh` reads (its path, `~/`, `${HOME}/`, its bare
name) and `ssh -G` through that config gives the alias just written; otherwise the one line put above the rest, ending
as the file's first line does, the rest byte for byte (the person's file is read with its line endings as they are, so
a CRLF file stays one; the managed file with universal newlines, so a CRLF copy's blocks are found and it is written
back as LF) and the file's mode kept, never another line rewritten or removed. Every `ssh -G` is
`ssh/resolved.py`'s, a child of the run's of the ssh kind, its group ended past `LIMIT` seconds. Through the Proxmox
host an alias gains `ProxyJump` (`Route.proxy_jump`) only once `Route.targets` returned for its VM, and only where that
`Target` takes the hop: a VM with a jump of its own keeps its own, a `ProxyJump` or `ProxyCommand` in
`claude_ssh_options` takes the hop's place for every alias, a VM the route refuses is left out and said, and a host that
cannot be the hop (at the route's settling or a target's) gives no alias through it, said with `vm_ssh_route: direct`,
while the prune still runs; where `ssh -G` of the hop through the person's config would not offer the key the host's
settings name, the lines that give it are said. Each alias written is said with its connect line (`ssh <name>`,
`aw code <name>`, the workspace it opens).

With no VM named, the run prunes: the host's listing (`resources("vm")` and `fleet.members`, a member tagged owned
where `fleet.refusal` says the fleet owns it, or where the pool lists it with no name) handed to
`proxmox.ssh_config_prune` with this host as the origin and the fleet's other hosts. It removes this host's blocks of
VMs gone (a VM outside the pool is one gone where the token cannot see it, without `VM.Audit` on `/vms`: under
`proxmox_audit_scope: pool`) and those whose name no alias may have, and says what it left: a block of a VM the fleet
does not own, one recorded for a host the fleet does not have, one with no record while the fleet has other hosts; a
pool member listed with no name prunes nothing. A block recorded for another host of the fleet is never touched. With
`claude_ssh_config` off nothing is written, and the run says so. `aw destroy` removes a deleted VM's (`aliases.forget`,
above). Its reports are matter `aliases`. `tests/unit/test_aw_aliases.py` holds it, every file in the test's own home
and `ssh -G` this computer's own through F30's stand-in: today's cases of `tests/unit/test_ssh_config.py` and the key's
of `tests/unit/test_host_keys.py` (but the 1.x file's move, not built), the compare-and-swap against a write between the
read and the swap, the jump's rules against a route of the test's own, and against the fake: the same file
`playbooks/ssh_config.yml` gives for the same fleet, the prune, a VM the fleet does not own, and a destroy that leaves
no block of its VM and its key in place.

**One VM's window: `code.py`.** `aw code <name>` (card G17) opens one VM in the editor over its alias: the window step
of today's `playbooks/code.yml` in plain code, over today's pure decisions unchanged - the editor's plan as data
(`editors/registry.py`'s `editor_open_plan`), the projects directory and `code`'s own directory (`sessions.py`), and the
editors list (`claude_editors`, `cli/status_inputs.py`'s `EDITORS`). Refused before anything is written, each in one
sentence, exit 1: an editor `code` cannot open; the managed SSH file off (`claude_ssh_config`); more than one name and
a `--project` that is not one folder name (the schema's `code.2` and `check.project.2`); `--session`, naming card G18;
then, from a live read that changes nothing (`fleet.requested`, `change=False`, and `fleet.refusal`): a VM not there or
not the fleet's, a stopped one with the line that starts it, one with no address, one with no key filed; a home (the
password database's, read as the VM's account) or `vm_projects_dir` that is not one plain absolute path; `code`'s own
directory (`editor_files_dir`, else `<projects>/.agent-warehouse`) that is not one, is `/`, is the projects directory or
the folder opened with the Claude terminal; a folder that is not there, said with what the projects directory holds;
and `code`'s directory that is a link, or has one on the way below the projects directory, that resolves outside it
(read as it resolves on the VM), or that is inside a checkout (a `.git` in a folder from the first below the projects
directory down to it), wherever anything goes into it. Then, in this order - every read and refusal before the alias, so that a plan's
refusal that says the alias is in place holds - the alias (`aliases.one`, written as `refresh` writes, refused where it
is not), the editor found here (VS Code: `code` on `PATH`, its Remote - SSH extension installed through its own command
line where `--list-extensions` lacks it, and its settings read, never written, for what `task.allowAutomaticTasks`
does, where VS Code itself keeps them (the person's configuration folder, `fleet/paths.py`'s `config_home`, the one
name of `XDG_CONFIG_HOME`); Zed: each `zeditor`, then `zed`, on `PATH` asked `--version` until one says Zed, which is taken and no command after
it started, and its tasks file's first line read for the marker, one there that cannot be read taken as someone
else's, left and said), the plan, refused where it refuses, and its writes on the VM as its account, all in
`code`'s own directory: that directory where anything goes into it (0755 where new), the plan's directories (the same,
one there keeping its mode), the helpers it names (`playbooks/files/`, unchanged, 0644) and its files, each into a file
beside it and renamed into place where its bytes or its mode differ. Last the registry's command, started to outlive the
run (`runs/children.py`'s `outlive`), and the plan's report. No trust is set and no question of VS Code's, Zed's or
Claude Code's answered. Every command on the VM is one of `READS` or `WRITES`, by name, a fixed script given its values
as arguments, run by one function (`Opening.on_machine`); every command here one of `HERE`'s, by `Opening.here`. The
steps are `deploy`'s editor stage's, but the sessions' (G18) and the notes' (G19); inside `deploy` (`window(...,
in_deploy=True)`, which G10 composes) every failure is said under matter `editor` with the `code` line that opens it
later - for a VM found stopped, under `vm`, the deploy that starts it - and the stage ends skipped, stopping nothing.
`tests/unit/test_aw_code.py` holds it: the commands by name against what F30's stand-in machine was sent, Zed's and VS
Code's windows through stand-ins of their commands that record how they were started, each refusal with nothing written
and no editor started, and `tests/unit/test_code_live.py`'s cases at a terminal.

**What the engine writes on this computer: `controller_writes.py`.** One row a place the new engine writes outside the
checkout, in the form of today's `playbooks/vars/controller_writes.yml`: where (as a person reads it, or the function of
`fleet/paths.py` that gives it), what, who, when, what takes it out, the setting that moves or turns it off, and its
sources, the engine's files that make the write; a write no card has built yet names its card and no source. Data alone.
`tests/unit/test_aw_controller_writes.py` finds each row's write where the engine makes it by its own spelling, and
scans every file of the new engine for a write (a file written, replaced, created, appended to, a folder, a scratch
file, a link): one that is the source of no row fails, so a card that writes somewhere new adds its row in the same
commit. Today's list and `tests/unit/test_controller_writes.py` hold today's product until the cutover.

**What a run says, and NEXT STEPS: `report.py`.** `say(run, machine, matter, text, line)` is the one way a workflow or a
layer-2 step tells a person something: it emits the `report`, printed above the region and kept for the summary, and
straight after it the `next` record of that machine and matter, the line while something is left and `""` once nothing
is, which takes back a line an earlier report of the same machine and matter left. So NEXT STEPS is recorded by each
report and never read off what was said, as today's report tasks record theirs
([§7.5](custom-code.md#75-callback_pluginsagent_warehousepy--the-waits-and-next-steps)). The machine is its identity, or a VM's stand-in key until its join, or none for the
run's own; the matter is one of `run/next_steps.py`'s `MATTERS`, today's and the engine's own, which no report of today's
records, each at a named place (`fleet`, `route` and `template` after `init`'s two, `configure` after `vm`, `aliases`
after
`configure`), so today's
NEXT STEPS keeps its lines and their order. The line is the printer's whole line, or a sentence `said` writes, made by
the caller with the host's flag, so that with more than one host it ends `--host <name>`, and handed on whole: one line,
never a form. The run's model folds the records in today's order and the run's end prints them after the summary, which
leaves them out, under NEXT STEPS (§16.8). Where `status/lines.py`'s words name the program, its name is the parameter
`program`, `just` for today's callers by default and `aw` from this engine. `tests/unit/test_aw_report.py` holds it: a
syntax-tree scan of `engine/` and `guest/` for a `report` or `next` event emitted any other way; a configure of two of
the stand-in machines printing a summary line each and the NEXT STEPS block in today's order, each line ending with its
host where the fleet has two; and the order of the engine's matters beside today's.

## 16.5 `pve/` — the typed Proxmox client and the one table of privileges

`lib/claude_vm/pve/` is where the typed client of the Proxmox VE API goes, with the one table that names each call's
privilege, which `init` grants from and the permission check reads, the wait for a started VM's address, a VM's
record as the text of its notes, and the one rule of what is the fleet's, with the type every write of a VM takes. It
imports `vendors/` for its one HTTPS transport,
`schema/`, `secret.py` and, of today's modules, what `REUSED` names: `trust` for the anchor's context, `proxmox` for
its parsers as they stand, `checks` for the names Proxmox gives, and `text`. It imports nothing of `engine/`, `cli/` or
`control/`, and no pydantic. It is Proxmox's own folder in `tests/unit/test_ratchets.py`'s `PROXMOX_OWN`, beside
`lib/claude_vm/proxmox.py`: it may name the API's paths and a VM's ID, which a file outside it may not name more often
than it does today.

**The pinned connection: `pin.py`.** The token goes only on a connection whose certificate verified against what the
host keeps, before a byte of the request is written. A host keeps one of two things, which `init` works out and keeps.

- **An anchor file**, its node's own certificate or its CA, which is then the context's only source of roots.
  `context(anchor_pem)` makes that context with today's `trust.anchored_context`, the one way a context is made from a
  certificate the host gave: the name the API was reached at checked, and the flags the standard library's ready-made
  context has (strict, and a partial chain, so that the node's own certificate is an anchor of its own), set by hand.
  So a CA with no `keyUsage`, as Proxmox VE 8 made one and VE 9 did before pve-cluster 9.1.8, is no anchor, and such a
  host's anchor is its node's own certificate.
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
  `resources(kind)` (`/cluster/resources` of a `vm`, `storage` or `node`: Proxmox's schema takes no `pool`, which its
  answer names but a request may not ask for, and no row reads the SDN's zones), `pool(poolid)` (its comment and
  members), `config(node, vmid, nic)` (with its `digest`, its identity, the SMBIOS UUID, through
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
  with the notes given; `resize(vm, disk, size)` grows a disk, never shrinking one; `start`, `shutdown` (with its
  `timeout` and `force_stop`), `stop`, `to_template(vm)` and `delete(vm, purge=False)`, which sends
  `destroy-unreferenced-disks` alone: it takes the disks its configuration no longer lists but that carry its VMID
  too, as today's role does, and nothing outside the VM, so Proxmox leaves the host's backup jobs, replication and HA as
  they are and refuses to delete a VM a person put under HA or replication, as it refuses today's deletion. `purge`,
  which would take the VMID out of each of them, is sent only where a caller asks for it. Each write of a VM the fleet
  has - its configuration's, a resize, a start, a shutdown, a stop, a deletion - takes the VM as an `Owned`, which the
  one check alone makes (`ownership.py`, below), and refuses anything else, a target a read gave above all, before
  anything is sent; a clone, which makes a VM from a template at a VMID none holds, does not. Each holds the VM at the
  `Owned`'s VMID to the `Owned`, since between the read ownership was decided on and the write that VMID can be another
  VM's (a VM deleted and another cloned there): `write_config` sends the `Owned`'s own digest (below); `edit_config`,
  `resize` (which sends that read's digest too), `start`, `shutdown` and `stop` first read the configuration and hold
  its SMBIOS UUID to the `Owned`'s; and `delete`, since Proxmox's `DELETE` takes no digest and cannot be undone, holds
  its SMBIOS UUID and its digest: another VM at the VMID now, or before a deletion a configuration changed since the
  read the VM was owned on, a stop that applied pending changes among them, is `Changed`, with nothing sent, and the
  workflow owns it again from a read made after. `edit_config`, `resize` and `start` take the run's `stop` too
  (`Context.stopping`, as `follow` does), asked once that read is made and before the write is sent: where it is set,
  a Ctrl-C during the read among them, the call is `Unsent`, with nothing sent but the read (card G31). A conversion to
  a template is made on a `Building` alone, a build VM
  of the asking run's own (`ownership.py`'s `building`, below), and holds its SMBIOS UUID and digest as a deletion does,
  for the same reasons; nothing else is ever made a template. Each write answers a `Task`
  (its node, UPID, kind and VM; a clone's, the new VM's, with its `source`). A clone whose answer was lost is `Unknown`
  with the clone perhaps running at `newid`, its configuration locked `clone` until its worker ends: `clone`'s docstring
  says it is settled by reading `newid` again until that lock is gone, never by a clone under another VMID before then.
  No write changes a pool: a new VM joins the fleet's pool through its clone's `pool`. The VMID to clone onto, the record's text and any sequence of calls are the
  workflows' (16.4), not the client's.
- **Every write of a VM's configuration sends `digest`.** `write_config(vm, changes)` is one `PUT`, synchronous,
  that sends the digest of the read `vm` was owned on and takes none of its caller's: Proxmox writes the changes only
  while the configuration is still the one whose digest the write sends, in the call itself and under the VM's lock, so
  a `POST`'s window, a check at the call and the write in a task that a write made between them fails, never opens, and
  nothing is written to a VM that took the VMID since. A stale digest is `DigestMismatch`, nothing written, a second
  `write_config` of one `Owned` among them; `edit_config(vm,
  edit)` reads, holds what it read to the `Owned`'s SMBIOS UUID (`Changed` where another VM holds the VMID now), applies
  `edit` to it, writes with that read's digest, and on a mismatch reads and applies again, `EDITS` (three) times at
  most, then raises. `changes` holds only the keys the table's rows for the
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
token can and cannot do from the table `init` grants. The table is held by the checks the fake makes, not only by the
routes the client sends: the fake records each row it decides a request by - held on the path the request names, read
by its listing for its entries, or needing nothing - and places a `GET` row that neither the request's path nor its
parameters name only where its route lists entries of that kind (its `LISTINGS`), refusing one it cannot place as a
call no row names is refused, since nothing would check it. So a row added on a route the client already sends, which
would widen `init`'s role, fails a test unless the fake checks it. `tests/unit/test_aw_pve_privileges.py` holds every
privilege to a list of Proxmox VE 9's, the module to the standard library, the rows' shape, `needed` to each row's
first privilege on its first kind and to a literal set for every kind (a row that widens `init`'s role, which the fake
checks and every other test passes, fails there until its pin is changed with it, where a reviewer reads it), each key
of a configuration's write to one row, and the fake and the harness to no privilege name of their own but today's.

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

**A VM's record as text: `record.py`.** The record is one block of a VM's notes, `key: value` lines between a line that
names their format, `agent-warehouse record (format 1)`, and `end of agent-warehouse record`. Only a block under that
first line, with a format number this program reads, is a record; anything else in the notes is a person's text, kept
byte for byte and never parsed: a person's own lines, and the lines today's product writes.

- `parse(notes)` gives the block's fields, each value a string and the three lists a tuple each, or none where the
  notes hold no block. `copied(fields, identity)` says whether they name a UUID that is not the VM's own: a copy, which
  `engine/record.py`'s `read` refuses and `status` reads by the same function; a VM's first form alone names none
  (`first_form`), and any other record that names none is refused with `NO_UUID`'s sentence. A block of a format this
  program does not read, a line that is no field of its format, a field twice, two blocks, or a start with no end, is
  `Unreadable`: refused, and never rewritten, so that what a newer program or a person wrote is never lost to an older
  one.
- `render(notes, fields)` gives the notes with that block alone written from the fields, or added last after a blank
  line, every other line as it was. It writes only what `parse` reads back as it was given: no value on more than one
  line, or with a control, a format or a separator character, none with white space at an end, no item of a list with
  a comma. Both read the notes as Proxmox gives them back, their white space at the end taken off (`kept`), so a block
  rendered again from the same fields is the same text.
- A VM's own list of repositories keeps today's rule, as `lib/claude_vm/proxmox.py` reads its line of repositories:
  names split on commas, each kept only where it is a repository's name, so a mangled list is still the VM's own and
  clones nothing it does not name, and an empty one is a list of none, where a line left out is no list.
- The notes hold at most `MOST`, 8192 characters, counted in characters and not bytes, with whatever a person typed
  there: Proxmox VE 9.2's bound on a configuration write's description (experiment 6), which a clone's does not have.
  `render` refuses notes past it (`TooLong`), so nothing is sent that Proxmox would refuse.

It imports the standard library, `text.py` and the command line's patterns (`schema/patterns.py`, the standard
library's alone) and nothing else, so that `status` reads a record with no other part of the engine loaded, which
`tests/unit/test_aw_record.py` reads from a fresh interpreter. Every field's value is held to its form here, by `parse`
and `render` both, the one definition `engine/record.py`'s `Record` holds its values to (16.4): the UUID to the form
Proxmox gives a VM's, the name, the size and the job to the command line's patterns, a build to a whole number from 1,
the digests to SHA-256's hex, an image's URL to an http or https URL, its packages to Debian's names, and the VPNs to
today's mark of one (`VPN`, a copy of `vpn/model.py`'s, which a test holds equal). So `parse` refuses every value
`Record` refuses, and `status` judges what the engine judges. The rules across fields - a VM's record names its stage,
and every record but a VM's first form names its UUID - are each plain reader's judge's: `ownership.py` and
`template.py` below hold them.

**What is the fleet's: `ownership.py`.** Ownership is pool membership and a valid record of this engine's, never a
label, and its one rule is here, in plain code with no client, so that every workflow, through `engine/fleet.py` (16.4),
and `status`, which may not import the engine, judge a member of the pool by the same function. A `Member` is what a
reader read of one: its name, node and VMID, its SMBIOS UUID and its configuration's digest, whether it is a VM and a
template, why it could not be read, what its record names (its kind, UUID, stage and job) and the other members that
carry its UUID. `Member.read(config, unread=, twins=)` makes one from a VM's configuration as the client read it
(`answers.VmConfig`), its record from its notes as `parse` reads them, and marks it read, a field no constructor takes
and `dataclasses.replace` and `copy.replace` leave unset: what `owned` and `building` judge is a member a reader made,
never one made by hand or changed after its read, which is a `TypeError` to both (a record written into a member's
fields would be its verdict). The mark says a configuration was read, not the pool beside it, so `twins`, the other
members its reader read that carry its UUID, is a keyword with no default, made by `twins(configs)` over every member
read, templates too (the one rule both readers call), and in `lib/` only the reader of the pool calls
`Member.read`, `engine/fleet.py`'s (and `status`'s, card G16): a workflow owns a VM again through that reader, never
through `Member.read` of one configuration, which would own one of two twins, or a VM outside the pool.
`refusal(member, job="", clone=None)` says why it is not the fleet's to change, "" where it is: a guest that is no
VM; a template; one that could not be read; one with no record of this engine's, whatever tags it carries; a record
of a template, a golden or a build on a VM that is no template, or one that names another VM's UUID (`copied`), each a
copy; a VM's record that names no stage; a record that names no UUID, but the first form of a clone (`first_form`),
which the run that cloned it alone owns, by its own job, which the record names, and its own clone's task, whose VMID
is the member's (`clone`, the clone call's answer or its own job record's; never read from the
record judged, whose job anyone may read), before it writes the rest; a VM with no SMBIOS UUID; and two members of one
UUID, both. `owned(member, job=, clone=)` returns an `Owned` where it passes, `NotOwned` with that sentence where it
does not, and is the one maker of one: its name, node, VMID, SMBIOS UUID and the digest of the configuration it was
owned on. Made by hand, or by `dataclasses.replace` or `copy.replace` of one, it is a `TypeError`. The client's writes
of a VM above and `engine/record.py`'s writer take an `Owned` and nothing else, and the writer keeps the record a
VM's, the kind an `Owned` is made of, so no write makes one a build VM or a golden: those records are written by a
road of their own beside `owned` (card E18). `building(member, job=, clone=)`
returns a `Building`, which nothing else makes either: the VM a golden image is built in (card E18), the one thing
`to_template` takes, made from a fresh read of a VM that is no template, whose record is of kind `build`, or `golden`
once the run has written the golden's record on it, and names its own UUID and the asking run's job, at the VMID the
asking run's own clone made; `build_refusal` says why not, as `refusal` does for `owned`: a template, a VM of the fleet
(a record of kind `vm`), another run's build VM, one at another clone's VMID, a copy are each refused, a clone of a
golden among the copies, since its record names the golden's UUID. E18's order is today's: the golden's record
written on the build VM, read back as a clone would read it, the `Building` made from that read, then the conversion,
which holds the digest of the configuration that holds the golden's record. An `Owned` is never a `Building`, nor the
other way.
`tests/unit/test_aw_ownership.py` holds each refusal, a record judged alike whichever reader read it, the records the
typed reader refuses among them, the first form owned by its run's job and clone's task alone, an `Owned` made by
`owned` alone and a `Building` by `building` alone of the asking run's own build VM, its record the build's or the
golden's the run wrote on it, never a copy of a golden's, a member no reader made never judged for a change, a member
read with no twins said a `TypeError` and one with a twin refused by both judges, and,
by scans, that a member is marked read in its readers alone (`tests/unit/aw_kit.py`'s one helper for the tests of what
a write sends, allowed by name), that `Member.read` is reached in `lib/` by `engine/fleet.py`'s reader of the pool
alone, under any name a `from` import gives it, that the module imports `record.py` and `answers.py` and
nothing else of `pve/`, the client above all, and nothing of the engine, and opening a host (`fleet/opened.py`, 16.9)
nothing of the engine, pydantic, Ansible, Jinja2 or the MCP SDK, so that `status` may import both.


**The base template judged: `template.py`.** What was read of the base template is judged here, in plain code with no
client, so that `deploy`, through `engine/template.py`'s `compare` (16.4), and `status`, which may not import the
engine and reads through a client of its own, judge it by the same functions and say the same lines. It imports nothing
of `engine/` and not the client.

- `wanted(settings)` is what a build makes, in the fields today's `lib/claude_vm/proxmox.py` compares a template on
  (`TEMPLATE_FIELDS`): the settings every clone inherits (its CPU type, OS type, bridge and storage, and the SCSI
  controller, guest agent, serial port and display every build gives it), then what went into its disk, which its
  configuration cannot show and its record keeps: `BUILD`, the build steps' version, raised whenever a change to them
  changes what a template is, the image's URL, and the packages put into the image, none without the guest agent. It
  is the one definition the build makes a template from and the judgement holds one to. Its settings are a run's
  against one host, every row's value as `fleet/overrides.py` resolves them; the roles' options no row names
  (`OPTIONS`) are read where the settings give them and are otherwise the roles' own defaults, which a test holds equal
  to the roles' files, and the template's bridge and storage are the rows `proxmox_bridge` and `proxmox_storage`'s
  unless their options name others, as today's inventory wires them.
- `judge(base, entry, config, found, want, readable=)` says what the guest at the base template's ID is, from its entry
  in the cluster's listing, its configuration, its record and `want`, what a build makes; `base` is the template the
  settings name, its name and ID, the node, the fleet's pool, whether a difference rebuilds it and the line that does
  (`Base.of`). `found` is what reading the record gave, by either reader: its fields, as `parse` gives them or the
  typed record renders them, none, or the `Unreadable` the read raised, a copy among them. Whichever reader made it,
  `judge` holds the record to the guest's own UUID itself: one that names none, which only a VM's first form may
  (`first_form`), or another guest's (`copied`) is refused with the sentence `engine/record.py`'s `read` gives it. Each
  field's form is the reader's: the typed record holds every one, and `parse` a block's shape alone, so a value of the
  wrong form that it passes is judged as it stands. `listed(base, entry, readable=)` is the part the listing alone
  decides, and none where the guest's configuration is to be read, so that a reader reads one only where it is judged:
  a qemu guest at the ID, in the pool, on `proxmox_node`. `custom` judges a template of the person's own.
- The six outcomes. `missing`: nothing has the ID, said only where the token may read it, which Proxmox says (GET
  /access/permissions), not the setting, so a token that holds less than its setting says reads `unseen`. `unseen`:
  under `proxmox_audit_scope: pool` a guest outside the pool is invisible, so missing cannot be told from another's
  guest, and the build asks the host over root SSH first. `same`, and `different` with a line for each difference,
  `<field> <has> → <wanted>`, from today's `proxmox_template_drift`, which stays where it is and is handed the record's
  template fields as the line of notes it reads; the image's digest is kept and never compared. Under
  `proxmox_template_rebuild: false` a difference is only said, with the `deploy --only template` line, printed by the
  printer of a command, and the template is kept and cloned from as it is (`usable`), as today. `refused`: a container
  at the ID, a guest that is no member of the fleet's pool, a template on another node than `proxmox_node`, a VM that
  is no template, a template of another name, and a template whose notes hold no record of this engine's build, one
  that cannot be read, a copy, or a record of another kind, each said in one sentence with the way on and left as it
  is, never rebuilt: so no VM of the new engine stands on a template it may not rebuild, and a person's own template at
  the ID is never destroyed. `custom`: `proxmox_template_custom` names a template of the person's own, only read and
  cloned, held by today's `custom_template_problems` to what a clone needs; one the token may not read is `unseen`,
  said with the `aw init` line, since `init` grants the token `VM.Audit` and `VM.Clone` on that template's own path
  (16.4), never on the pool, whose role would reach it. Whether it is a template, and its name, are read from the
  guest's own configuration, never from the listing, which lags what a run just did.

**The base template's commands over root SSH: `commands.py`.** What a build of the base template runs on the host
(16.4), each an argument list, in plain code that runs nothing: `engine/template.py`'s `build` runs each through the SSH
runner, which joins its words with `shlex.join`, so the host's shell reads each value as one word, and sends nothing on
standard input. They are the host's own programs, by name as root's PATH finds them: `pvesh`'s two reads, the cluster's
listing and a guest's configuration, as JSON as the API gives them; `qm create`, `set`, `template` and `destroy`;
`curl`, `sha256sum` and coreutils for the image; `virt-cat` and `virt-customize` under libguestfs's own appliance;
`dpkg-query` and `apt-get`. Every value is held to its form before a list is made, so no setting's word is read as
another option, a second word or a path outside the one meant: a guest's ID in Proxmox's range, a path absolute and of
plain parts, none `.` or `..` or beginning with a dash, a node, storage, bridge and pool as `checks.proxmox_name` holds
them, a package as Debian names one, and a URL one word of http or https. `create`'s notes are the record
`pve/record.py` renders, the template's first write, made before it exists so that its record names its UUID from the
start; `test_aw_record.py`'s scan names this module for it.

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
`Denied`, `Unverified`, `Malformed`), and each raised as itself while the run goes on, told by the mark and never the
words - a marked failure in other words is `Stopped` too, and one in `CUT`'s words with no mark is raised as itself; a
log whose read could not be made, whose lines are `()` as a log with none's are; a deletion's parameters,
`destroy-unreferenced-disks` alone and `purge` only where asked for, read from the fake's log; a deletion of a VM whose
configuration changed since it was owned, and of one whose VMID another VM holds now, `Changed` with no `DELETE` sent,
as is an `edit_config` of the latter; no write of an `Owned` reaching another VM at its VMID, each of the seven
refused (`write_config` by Proxmox's digest, the rest `Changed` with nothing sent but the read) and the VM there as it
was; a resize sending its read's digest; a write given anything but an `Owned` refused with nothing sent, and a
conversion anything but a `Building`; a VM of the fleet, a template, another run's build VM and one at another clone's
VMID never made a template, and the run's own build VM made one; a golden made in E18's order, a `Building` made before
its record was written `Changed`, and neither it nor a clone of it made a template again; and a UPID with a `/` in a
part of its own
`Malformed`. Its
rules: a `PUT` or a `POST` to a configuration's path with no `digest`, or an empty one, is refused before it is sent,
`%`-escaped spellings of the path among them, while a path with a `%` is sent as it was given, `harness.fake_fleet`'s
end fails a test where the fake was sent one, the path read decoded there too, and every method that sends one sends
`digest` (a syntax-tree test of the module); a configuration's write sends only
the keys the table covers for it; both ways, every row of the table is one the fake decided a request of the client's
by (its `decided.log`, which `tests/unit/test_aw_fake_pve.py` holds whole, request by request, so that a row the fake
logged and did not decide fails), a configuration's row by a write that sent one of its keys, and every call it makes
has its rows, each method of the client driven against the fake under enforcement, a task another made read among them so
that a task's rows are checked, a deletion's second look by the configuration's rows, and what it sent read from the
fake's log; the words the client tells refusals
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

**The access script's fixed text: `script.py`.** The script `init` runs on a host as root (16.4, `engine/access.py`'s
`as_script`) is the list's calls, rendered there, inside the fixed text here, after its first lines, which `access.py`
says through the printer: the Perl program; the body, which reads the options, holds each value and the host (root,
`pveum` and `pvesh` there, Proxmox VE 9 or later), lists the node's storages and bridges, looks a person's template up,
and defines the five calls, `Pool`, `Role`, `User`, `Grant` and `Token`; and the run, which calls the list twice, to
read and decide, then to make, the secret last. It is here, in Proxmox's own folder, since it names the paths of
Proxmox's command line, which no file of the engine's outside `pve/` names. The script is `/bin/sh` run as `sh -s`,
itself on standard input, so each command it starts reads `/dev/null` or a pipe, never the rest of it. Every answer of
`pveum` and `pvesh` is asked for as JSON and read by the one Perl program, with JSON::PP, core Perl and on every Proxmox
VE host, never by grep or sed: a role's privileges it lacks and those it holds beyond its own; a pool's state by the
fleet's mark, read as `fleet/mark.py` reads it, white space being what Python reads as such (Perl's, and the separators
`\x1c` to `\x1f`); the comment taking an unmarked pool gives it; whether an ACL entry is there, propagating; a token's
privilege separation and the mark in its comment, read as a pool's is (card E8); a person's template, the one guest of its name and a template on the node, and the bridges its
network cards are on; a new token's secret, held to the form of one; and a command's words, quoted as Python's
`shlex.quote` quotes them, for each line the script prints. Its one cut of what the host holds, of the privileges a role
holds beyond its own, makes each run of characters outside printable ASCII one space where `text.py`'s `one_line` keeps
what it can print: Proxmox names a privilege in ASCII alone. `pool_read(pool)` is the one read `init`'s offer makes of the pool over root SSH before the
script's dry run (card E8), `pvesh get /pools --poolid <pool>` as JSON, its comment and members read by
`pve/answers.py`'s `pool`. It imports nothing of the project's.

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
characters, where a machine whose socket's path would pass 86 bytes (a long home: 103, the most macOS binds a socket's
path to, less the 17 `ssh` adds while it binds one) takes no shared connection (`shared`: each of its connections logs
in alone, `ControlPath=none`); an identity file or
none (this computer's `claude_ssh_identity_file`); the hop through the Proxmox host or none (`Hop`, the route settles
it: route.py's `ProxyCommand` to the machine's own address, and its connection's name); the person's own options for the
machine (the setting `vm_ssh_options`); whether this is a first use; the `ssh` program; and the route's mark
(`routed_as`, one of `ROUTED_AS`: `direct`, `hop` or `own`), which `connections.target_for` alone gives and
`dataclasses.replace` carries, with what the route decided it from (`routed_from`, made by `decided_from`: the address,
account, port, `ssh` program, options and hop, `ROUTED_FROM`). `machine()` makes one, unmarked, and the runner makes no
command line of a machine's `Target` unmarked (`options`, which every line goes through, layer 1's inventory among
them), nor of one marked `hop` without a hop, refused already as it is made, so a `replace` that takes the hop off
fails there, nor of one whose fields of `ROUTED_FROM` are no longer those it was marked with: refused as it is made
where a `replace` changed one, and at every line where anything else did. The known-hosts file, a first use, the
identity file and the control directory decide no road and go with the mark. The Proxmox host is
its second form, `proxmox_host()`, for root SSH and as the hop: no identity, no mark, its account, port and address
from the host's file, its key from this computer's, and its settings held by `route.hop_settings`, the rules the
hop is made by, whose socket's name it shares: a connection to it rides the hop's master where one lives. From a
home so long that the hop's socket would pass `route.SOCKET_MOST` it is still made, and each connection to it logs in
alone (`shared` false, `ControlPath=none`), as a machine's does: the hop's master is opened only by `ensure_hop`, only
where the settled route goes through the host, and from such a home that route is refused (`LONG_HOP`), so no master
could have been there to ride. A `Target` with no identity that carries what only a machine's takes - a known-hosts file
of the product's, a hop, the person's options, a first use or a route's mark - is refused as it is made and again at
every line, so a machine's made the host's in place, its identity taken off a copy, reaches nothing. Every line of the
runner's carries `ControlMaster=no`, the host's among them.

- **A person's own options.** `vm_ssh_options` is a mapping of `ssh`'s option names to values, given as `-o Name=value`
  after the product's options: `ssh` keeps the first value it is given, so a person's option changes whatever the
  product leaves, a `ProxyJump` or a `ProxyCommand` among them, and never a value it pins. What the product writes
  itself is refused by name (`REFUSED_OPTIONS`) - `HostKeyAlias`, `UserKnownHostsFile`, `CheckHostIP`, `UpdateHostKeys`,
  `BatchMode`, `ControlPath`, `ControlMaster`, `ForwardAgent` (`claude_ssh_forward_agent`'s), `IdentityFile`
  (`claude_ssh_identity_file`'s), and `HostName`, `User` and `Port`, where and as whom it connects - and
  `StrictHostKeyChecking` may be given only as `yes`; with them `IdentityFile2`, `ssh`'s old name for
  `IdentityFile`, and what would hand the strict check a key the product's file does not hold, `GlobalKnownHostsFile`
  and `KnownHostsCommand` (a program run here for each connection, whose keys the check takes). So is what would undo
  what every connection holds (`UNDOING_OPTIONS`): `StdinNull`, which would leave a secret never arrived while the call
  reads `Done`; `ForkAfterAuthentication`, which takes `ssh` out of its group and the run's reach; `SessionType` and
  `RemoteCommand`, another command or none; `ForwardX11`, `ForwardX11Trusted` and `ForwardX11Timeout`;
  `PermitLocalCommand` and `LocalCommand`, a program run here for each connection; and `PKCS11Provider`, with
  `SmartcardDevice`, its other name, and `SecurityKeyProvider`, a library loaded here. Each is refused by the name it
  was given. They are the person's own setting, which a `Host *` of their own `~/.ssh/config` could still set; what is
  refused is the setting the product reads. A machine given a jump of its own takes no hop.
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
passes a shell. `shown_argv(target)` is the same line with `-G` and no command, which dials nothing, and `shown` runs it
as `run` runs a command: what this computer's ssh would use for the machine, which `keys.py` reads its agent and
identity files from (§16.10). No `-F`: the person's own `~/.ssh/config` is read, as today, where a key, an agent or a
jump of theirs is named. The product's options, in order: `checks.vm_ssh_args` as it stands (the known-hosts file,
`CheckHostIP=no`, `UpdateHostKeys=no`, `HostKeyAlias` the identity, `ForwardAgent=no`; for the Proxmox host
`UpdateHostKeys=no` and `ForwardAgent=no` alone), `ControlPath=<the shared directory>/<checks.vm_control_name>-%r-%p`
(`none` for a first use), `ControlMaster=no`, `BatchMode=yes`, `ForwardX11=no` and `PermitLocalCommand=no` (pinned,
where a person's `ForwardX11 yes`, or Ubuntu's own `ForwardX11Trusted yes`, would hand the machine this computer's
display), `StrictHostKeyChecking=yes` (or `accept-new` for the two forms above), `ConnectTimeout=10`,
`ServerAliveInterval=15`, `ServerAliveCountMax=3`, `PreferredAuthentications=publickey`, `ClearAllForwardings=yes`,
`RequestTTY=no`, `LogLevel=ERROR`, and the identity file where there is one. `BatchMode=yes` on every `ssh` the engine
starts means a key that wants a passphrase fails in one sentence and never waits at a terminal it cannot reach. Every
connection authenticates by public key alone, the Proxmox host's too: today's Ansible connection also offers GSSAPI and
host-based authentication, and those are added only if someone asks. `ControlMaster=no` means a child rides a master the
engine opened in the shared directory (`connections.py`'s `ensure`), or connects alone where none lives, and is never a
master itself. The hop's own `ssh` keeps `LogLevel=INFO`, which route.py pins because it reads `ssh`'s words, and the
new engine's pins `ForwardX11=no`, `PermitLocalCommand=no` and `ClearAllForwardings=yes` first on every line it is run
with, the `ProxyCommand` a machine is given among them (`route.hop_settings`' `pinned`, which today's callers do not
pass): a master the hop's open makes, or a `ProxyCommand`'s `ssh` that finds none live and becomes one, would otherwise
run a person's `LocalCommand` here, and `-W` clears forwards only where a person's configuration says nothing of it, so
their `ClearAllForwardings no` would have the `ProxyCommand` and the forward check open each `LocalForward`,
`RemoteForward`, `DynamicForward` and `Tunnel` they give the host. `ansible_vars(target)` gives layer 1's Ansible child
the address, the port, the account, the program and `ansible_ssh_common_args` holding exactly the same options in the
same order, so that its `ssh`, `sftp` and `scp` ride the same master and a jump of the person's reaches the file
transfers too.

**A bounded call.** `run(job, target, command, stdin, limits, env, started)` is a coroutine. The child is started
through `children.start` as an `ssh` child of the job's run, in a process group of its own, with `env` and the job's
marker, entered in the run's registry; `started` is handed asyncio's process object, never a bare pid. Its standard
input is written, drained and closed: bytes, text, a `Secret` or a list of them, a `Secret` revealed in `_bytes` alone,
so a password or a code is never in a word, a variable or a file. Its standard output is read a line at a time under
`Limits`: a bound for one line, one for the whole, a deadline, and a time to say anything at all, and, where the caller
sets one (`lines`, none by default), a bound on its lines; its standard error is kept to a fixed size, and read to its
end so the child never blocks on it. On a breach the child's group is killed, its `ProxyCommand` with it. The result is
`Done(status, lines, said)`, or `Cut` (a line past its bound, more than the whole, more lines than its bound, a line
with no end), `Silent`, `TimedOut` or `Unreachable(said, lines)` (status 255), each line and `said` made
plain (`text.plain_text`). The default limits are today's, `status/reach.py`'s, held equal by the test: a line of 16
MiB, 45 s, 20 s to say anything, 64 KiB of standard error, and four of the longest lines in all. A call cancelled by its
run ends its child's group too.

- **255 is `ssh`'s or the command's own, and cannot tell which.** `ssh` exits 255 where it could not reach the machine
  or lost it, and also where the command on the machine exited 255 or was ended by a signal, which `ssh` has no status
  to pass on for. `Unreachable` keeps the lines the command printed, and no more: a command that ran and printed nothing
  reads as a machine never reached, and one that printed may go on on the machine after a connection lost (a cut
  network, keepalives unanswered). So `Unreachable` never says that a command did not run, and a caller whose command
  changes something - above all one that must run once, as Tailscale's key goes once - never tries a machine again on
  `Unreachable` alone: it reads the machine's state again first (`tailscale status --json`, for the key), and decides
  from that. A line of the runner's own printed before every command would tell a command that started from a login
  that failed, but not the other way: a connection lost after the line was printed and before it arrived reads as one
  never made, so a second try would be no safer, while every command line would grow a word of the login shell's and
  `Silent` would bound the login alone; so there is none.
- **The local `ssh` is ended, never the command on the machine.** A breach, a cancel and a held child's `end()` end the
  `ssh` here: with no terminal on the session, `sshd` sends the command no signal, so one that neither reads its input
  nor writes goes on after `TimedOut`, `Cut`, `Silent` and `end()`, as under today's Ansible. A caller whose command
  changes something bounds it on the machine (`timeout`) or reads its state again.

**A held child.** The sign-ins hold their own `ssh` children, and so does a helper call where `sudo` is handed a
password (§16.10). `hold(job, target, command, env, started, deadline, limits)` starts the same command line the same
way and returns a `Held`: `send(line)` writes one line to the child's open standard input, a `Secret` revealed only
there; `write(data)` writes bytes as they are, never a `Secret` or text, what follows a line once the child has
answered it; `lines()` yields what the child prints while it runs, a line at a time
under the same bounds; `close()` closes its standard input; `end()` ends its group, `SIGTERM` and then `SIGKILL`;
`result()` reads what is left and says how it ended. Its deadline is the caller's; the run's end ends every held child
through the registry of `children.py`. `forward(job, target, specs, env, started, deadline=)` is a held `ssh` with
each `-L` the caller gives, `ExitOnForwardFailure=yes`, `ClearAllForwardings=no` and `AddressFamily=any` (each loopback
family bound, as today's forward does whatever a person's settings say), made as a connection of its own
(`ControlPath=none`), since a forward asked of a shared master would outlive the run on it; its standard input is
`/dev/null`. Its deadline is required, and its far end is the command `sleep <deadline>`, rounded up, in place of `-N`:
the local end ends it first, at its deadline, by its caller or at the run's end, and where the run was killed outright
(SIGKILL, an OOM kill), which ends nothing it holds, the far end's `sleep` ends the forward by itself and lets this
computer's port go, once its deadline has passed and the connections through it have closed: `ssh` ends once its command
has and no forwarded connection is open, so a browser's kept connection to the callback holds it until it closes.
Nothing is recorded on the machine.

**The one control directory and the route: `connections.py`.** Every shared connection, a machine's master and the
hop's, lives in one control directory that every run on the computer shares, `~/.ansible/cp`, which `fleet/paths.py`'s
`control_dir()` names: short, as a socket's path must be, the account's alone, outside every repository, and the one
layer 1's Ansible child and the status probe use. `Connections(control_dir, env)` is made once a run and refuses a
target or a hop whose socket is anywhere else. It holds the directory to its owner by one `lstat`: a link, a directory
of another account's, and one its group or others may write in are refused (`Unsafe`, with the `chmod 700` to run),
since anyone who may write there could put a socket at a machine's path before its master and be handed what every child
sends it, secrets included; one not made yet is made 0700 by the first master's open. A machine's socket is
`checks.vm_control_name` of its identity, its address and its hop; where its path would pass `route.SOCKET_MOST`, the
machine takes no shared connection, `ensure` opens nothing for it, and the run says so once (`notes`), as it does for
the Proxmox host's own `Target` from such a home, whose connections log in alone too (`UNSHARED` names both). Three
rules hold every master. A machine's master is opened only by `ensure`, which starts it with `children.MASTER`, the one
kind of child that carries no job's marker and is entered in no registry, in a session of its own. The hop's master is
`route.py`'s: `ensure_hop` opens it with the engine's own environment, and so may any connection through the hop whose
`ProxyCommand` finds none live - a bounded child, a first use, a forward - since that `ProxyCommand` carries
`ControlMaster=auto`, and such a master carries that child's job's marker, so nothing may tell a master from a run's
child by the marker alone. No run ever ends a master, the hop's or a machine's: it ends by itself, `ControlPersist`
after its last client (`60s` for a machine, today's), and two runs never ride one machine's master at once, since each
holds that machine's lock. And `-O exit` is never sent, which would cut another run's sessions; `-O stop` only through
`route.hop_stop`, for the hop. The route is `route.py`'s, called as it stands, its `ssh` children started in a process
group of their own (`process_group=0`, which today's callers do not pass), so that a terminal's first Ctrl-C, which the
run takes as "finish the step", never kills one and has its silence read as an answer, and `route`, `ensure_hop` and
`held` are coroutines that run it off the event loop, so that the run takes the Ctrl-C at once and its other tasks go on
while one of them waits on its `ssh`. Each `ssh` route.py
starts, the `ssh -G` of `route_settle` and `ssh_shown` too, is checked against the door, started and recorded in one
step under the lock `route.end_all` takes, and ended by the thread that waits for it, at its limit or when its caller
ends; a run that is stopped ends them with the rest of its tree, and `end_all`, which shuts the door under the same lock,
ends every one still running and starts none after, so that no `ssh -G` goes on to run a person's `Match exec`, is for
a caller about to end at once. Each of route.py's ends ends the `ssh` with its process group, which it leads, so that
the program a person's `Match exec` started, or a `ProxyCommand`'s, ends with it: one the `Match exec` put in the
background too, which outlives an `ssh -G` that has exited, since a leader not yet reaped is still ended with its group
(`route._kill` decides by `returncode`, set once it is reaped, never by `poll()`, which would reap it and leave the
group running); today's callers' `ssh`, in their own process's group, is ended by its pid alone. An `ssh -G`'s
standard error is read from a file, as the hop's own `ssh`'s is, so that a program its `Match exec` left holding it
never holds the answer back to the limit; one left by an `ssh -G` that exited by itself is the person's, as their own
`ssh` leaves it. Today's one caller of `end_all`, `status`, starts none of them on its main thread, where Python raises
a Ctrl-C and could raise it between a start and its record, and starts and records its own probes (`status/reach.py`)
in one step under the lock of its own `end_all`, which a run's end calls before `route.end_all` and which ends a probe by
its pid. A run watches each of route.py's for a stop at the terminal as it watches its own children (`route.leading`,
§16.8). `route(setting, host)`
settles it for the Proxmox host's own `Target` - its account, or `vm_ssh_jump`'s, its port, key and `ssh` - by
`route.hop_settings` and `route.route_settle`, as `{route, setting, hop, policy, addresses}`, and raises
`route.HopRefused` as it is, which names `vm_ssh_route: direct`; under `auto` only where the rule would go through the
host, and where the `ssh -G` that says where `ssh` dials for the host gave no answer - ended by a signal, out of its
time, whose kill is a signal too, not started, since the run is ending, or not run at all, the `ssh` program or the
temporary file its errors are read from not made, said with what could not be - or exited with an error, a line of
the person's configuration it refuses for the host alone (a `ControlPath` with a token it does not know, which the
hop's own lines outrank with theirs), said in `ssh`'s own words, which settles nothing (`signalled_settles=False`,
where today's callers settle from the address as written). Where the route goes through the host and the hop's socket
would pass `route.SOCKET_MOST` (a long home), `route` refuses it in this engine's words (`LONG_HOP`), naming
`vm_ssh_route: direct` or a shorter home: the hop is one shared connection, and none can be bound there.
`target_for(job, machine,
settled, given)` gives a machine's `Target` the person's own options for it and, where the route goes through the host,
the hop's `ProxyCommand` to its own address (`route.vm_hop`, `-W <address>:%p`), unless it brings a jump of its own: a
`ProxyJump`, a `-J` or a `ProxyCommand` other than `none` in `vm_ssh_options`, or in what `ssh -G` resolves for its
address from the person's own `~/.ssh/config`, read once a run for each machine; it marks the `Target` with the route it
gave (`routed_as`) and what it decided that from (`routed_from`), the one place a `Target` is marked.
`held(target, settled)` asks the
machine's own `ssh` which proxy its whole line takes (`route.ssh_shown`, `-G -F /dev/null`) and refuses any other
(`route.vm_route_refusal`) before anything connects; a jump only the person's configuration gives is the one the line
cannot outrank. `ensure_hop(settled)` is `route.hop_held`, the one place the hop's master is opened, only where the
settled route goes through the host, and while that open runs a run remembers nothing of its tree (`children.opening`),
as for a machine's; a hop that cannot be used raises `Unusable` with `route.login_said`'s sentence in the new engine's
names (the key this computer's `ssh_identity_file`), so the run stops and nothing dials a machine. `ensure(job,
target)` makes sure of a machine's master before anything rides it, and refuses the Proxmox host's own `Target`: `-O
check`; where nothing answers, a socket file nothing listens at removed (never one whose check ran out its time), and
the master opened once, the runner's options with `ControlMaster=yes` and `ControlPersist` in place of
`ControlMaster=no`, and `-N`, ended where `ssh` says the socket exists already; then checked once more. Each answers a
`Master(ok, opened, said)`, `ok` where a master answers at the socket, or `None` where no master is used, and none is
made sure of: a machine that takes no shared connection, and the hop where the route is direct. Never for a first use:
a master opened before the key is filed would carry later runs with no key checked. `first_use(job, target)`
makes a machine's `Target` one where the product's file holds no key under its identity (`hostkeys.kept`), its scratch
file fresh in the run's own directory (`rundir.made`): a changing verb's first connection to a machine calls it, files
the key it took, and only then asks `ensure`; `status`, which takes no key, never calls it. `forward(target, settled)`
is the one forward of its own through the hop to a machine that takes it (`route.hop_forward`: a connection of its own,
`ControlPath=none`, at `LogLevel=VERBOSE`, under its hard limit), its `ssh` in a process group of its own and off the
loop, and answers `route.forward_verdict`'s verdict; it refuses a machine with no hop and a route that is direct. Nothing
in `ssh/` tries a connection again. The engine settles its route once a run, and gives every machine its `Target`,
through `engine/routes.py` alone (16.4).

`tests/unit/test_aw_ssh_runner.py` holds it against a stand-in `ssh` that records its words, its environment and its
standard input: the options and their order, word for word, for every form; the values they hold as this machine's own
`ssh -G` shows them, a person's options among them and a person's configuration outranked; each option refused by its
name, ssh's other names among them, which this machine's own `ssh -G` reads as the one refused; the Proxmox host from a
long home, its lines and its forward's carrying `ControlPath=none`, nothing refused; no secret in a word or a
variable; each bound's result, a 255 after output with its lines and no more, and nothing left in the process table;
a held child's lines while it runs and a line sent later; a forward's far end ending it within its deadline after its
run was killed with SIGKILL; the route's mark, one of three, through the hop only with a hop and none on the host,
carried by `dataclasses.replace`, and no command line made of a machine's `Target` unmarked; and four rules by scans:
a child named `ssh` is started in the new subpackages only from `ssh/`; no option is made of a value a machine supplied
but its address; `accept-new` is written in `ssh/` only for a first use and the Proxmox host; and the runner carries
no `ControlMaster` but `no`. `tests/unit/test_aw_hostkeys.py` holds `hostkeys.py`, with blobs of every type it
files and real keys of this machine's own `ssh-keygen`, whose `-l` gives each fingerprint, `-F` a name as `ssh` reads
one and `-H` one hashed: a key filed once and found, a second of its type refused with the file unchanged byte for byte,
two writers at once, a deleted machine's line kept through every call, a first use's scratch file taken and filed or
refused with nothing filed, the person's own files never read or written, and, by the signatures, no function that takes
an address. `tests/unit/test_aw_ssh_connections.py` holds `connections.py` against a stand-in `ssh` that plays OpenSSH's
control commands and its master, as `tests/unit/test_route.py` does, every socket in a short directory of the test's
own, and that runs this machine's own `ssh -G` given the test's own `~/.ssh/config`: each route settled, the hop's
command word for word, a jump of a machine's own from its options and from its configuration, each marked as
`target_for` gave its route and a marked machine refused there, the held check, a first use decided by the key filed,
its scratch file in the run's directory and no master opened for it, a master opened once and found again, unmarked
and in a session of its own, a dead socket file removed and a busy one kept, a hop that cannot
be used stopping the run with nothing dialled, a control directory that is a link, another's or writable by others
refused, a long one connecting alone, said once for the machines and the host together, a first Ctrl-C sent to the
run's group while `ssh -G` settles the route killing no `ssh` (its stand-in waiting for a file the test writes after
the Ctrl-C, never for a time), `route`, `ensure_hop` and `held` each waiting on an `ssh` held until a task on the same
loop lets it go, an `ssh -G` ended by a signal or out of its time settling nothing, the hop's lines holding the three
pins first, the route through the host refused from a long home while `direct` settles there, a master's open, a
machine's and the hop's, left out of what a run remembers, and the forward, `route.hop_forward`'s, in a group of its
own and only for a machine that takes the hop; and three rules by scans: no control
command but `-O check` in the new subpackages, never `-O exit`; nothing in `ssh/` tries a connection again; and only
`ensure` opens a master, with the kind that carries no marker, an f-string's `ControlMaster` and ssh's flags run
together (`-NM`) read too.

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
  on a socket `close()` closed; before the take nothing reads its socket, and the take reads it under that lock, so
  that a `close()` that found it idle and let it go just before is that request's `Closed`, and once taken it is busy,
  so that `close()` only shuts it down and every later read of it meets the socket the take found; and a connection
  still being made is refused as `Closed` once made.
- **One place closes a socket.** A connection's socket is closed by `_forget` alone, once no request is under way on
  it, and only after it is taken out of what the transport holds, under the same lock: `close()` shuts down only a
  connection it finds there, so its shutdown never meets a close. The socket `http.client` is handed (`_Wire`) closes
  nothing of itself: `http.client`'s close of it, as it lets it go for an answer's `Connection: close` or after a
  connection error, and an answer's close of what it read, are marks that leave it open for `_forget`.
- **A cut is said as one.** A request whose send or read `close()` shut down is cut, whatever the shutdown made the
  send or the read raise - on Linux almost never a clean end: a TLS read meets the end of the stream, tries to send
  its alert on a socket shut for writing and fails, "Broken pipe", or meets "Connection reset by peer" - and an answer
  read so is never handed back, since one that ends with its connection would seem to end there. A `GET`'s is
  `Unreachable`, "the transport was closed while the request was under way" (`CUT`), words true of every road a cut
  takes - its request still being written, its answer awaited or being read - and any other method's `Unknown`, in
  the same words first. **The mark is an attribute**, `TransportError.closed`: true for `Closed` and for a cut, false
  for every other failure, the peer's or the network's, a try's own failure with the close after that try has ended
  among them (a close that comes before it has ended, while its connection is still taken, cuts it, as any other), so
  a caller tells the run's own close by it and never by words; a copy or a pickle keeps it. A `GET` whose next try
  a close forestalls - its connection dropped or its answer 502, 503 or 504, the transport closed before the pause or
  during it - is `Closed`: the close is what ended the call.
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
it is that proxy. A URL the setting names has a host and no user or password, which a file of settings, a run's record
and what `settings` prints would hold in the clear: `setting_refusal`, the one check of the setting, which `choose`
makes and `fleet/overrides.py`'s `typed` makes of every value a file or `--set` gives it, refuses one by the setting's
name and never says it. A proxy that needs them is given as `HTTPS_PROXY` in the environment, read at `environment`,
whose user and password the transport holds as a `Secret`. Of `urllib.request` it calls only the readers that take what
they read as an argument (`proxy_bypass_environment`) or read the system's settings (macOS's `getproxies_macosx_sysconf`
and `proxy_bypass_macosx_sysconf`). It never calls `getproxies` or `proxy_bypass` themselves, which read the process's
own environment, and it never uses an opener.

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
its far end is looked at, and a `close()` just before the take, stood in for at its entry, makes the request `Closed`.
A `GET`'s last try dropped and a `POST` dropped, each with the close once that try has ended, are unmarked, in their
own words; a `GET` whose pause before its next try is held until the transport is closed is `Closed`. A `_Wire`
closes nothing,
and on every road a connection's socket is let go by - kept, an answer's `Connection: close`, a connection dropped, a
kept one the server closed - it is closed only once the transport holds it no more. It holds four rules over the new
engine's modules.
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
- **Checked as Python's own context checks.** The server's name is checked and a certificate required. The flags
  that the standard library's ready-made context, and today's `requests` on it, set are set by hand, since a context
  made by hand has neither: strict checking, and a chain that may end at an intermediate a file holds. So a CA with no
  keyUsage, as Proxmox VE 8 makes one, does not verify.
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
- **An image.** `signed_digest(list_url, signature_url, file_name, *, key, fingerprint, tls_ca_file, list_proxy,
  signature_proxy)` fetches the checksum list and then its detached signature, each from a URL of its own (today's
  image checksum and signature settings give the two), so a person's own signed image may name its list and its
  signature as it likes, on two hosts, a pre-signed URL's query kept; each over https only, on a transport of its own
  URL's host, through the proxy its caller chose for that URL (`proxy.choose`, which decides by the URL's host, so a
  list on a host `no_proxy` names and its signature elsewhere are each reached as they should be), each under 1 MiB.
  A URL is held to one `https://` address with no user, password or fragment, and a path that does not begin with
  `//` (which the transport would send as another), before anything is fetched, and said without its query, which may
  carry a signature. The key is Ubuntu's cloud image key by default, a `keys.Key`, or the armored text of a person's
  own with the `fingerprint` of a primary key in it, as today's image key and fingerprint settings give one: a
  fingerprint that names none of its primary keys (a subkey's) is refused before anything is fetched, never said to be
  missing from the vendored keys. It returns the digest of the one line that names the image, read as today's role
  reads it, or `NotListed` where no line or more than one does. `listed_digest(list_url, file_name, *, tls_ca_file,
  proxy)` reads an unsigned list, an image held to its checksum alone (`sha256:<the list's URL>`), with the same line
  reader, through the proxy chosen for its URL.
- **Bounded, and nothing unverified.** Every document is read on a transport whose `most` is 1 MiB, so none is read
  past it, and an answer's status is read before the document's own cap, so that an error page, longer than a
  channel's pointer, is said as the status it came with. A document one byte past its cap is `Oversized`, and nothing
  after it is fetched. A 3xx is an answer, never followed (`NotServed`, as is any answer but 200), and a document the
  transport could not fetch is `NotFetched`, which names the document and its URL before the transport's own words and
  keeps the transport's error (`Untrusted`, `Unreachable`, `TimedOut`) as its `error`, so that with two hosts a person
  can tell which one failed. Nothing is returned from a document whose signature has not verified (`Unverified`, with
  `openpgp`'s reason), decided by whether `openpgp.verify` returned, never by what a refusal says, so that a refusal
  with no words is one; and of a document only its version and digests are. What a server named is said through
  `plain_text`, cut, as JSON.

`tests/unit/test_aw_downloads.py` serves every document from `tests/molecule/fake_downloads.py` over TLS, its root
standing in for one of `certifi`'s, signed by keys `tests/unit/signing.py` makes or as the vendors published them
(`tests/unit/openpgp/`). It holds each refusal, with the fake's log of requests showing nothing fetched after it: a
document changed by one byte, a signature by another key or dated more than a day ahead, a refusal of `openpgp`'s with
no words, a version below the first signed release, a document one byte past its cap, a list with no line or two for
the image, a redirect, a certificate that does not verify, a URL that is not one `https://` address, a key that is not
one, a fingerprint that names no primary key of a key given as text, a path that begins with `//`. A list and its
signature on two servers, with names of their own and a query, verify, each through the proxy chosen for it, and so
does a key given as its text; a document that could not be fetched is said by its name and URL. It holds the
release's pattern, the first signed release, the caps, the files' names and the line of a list equal to today's roles',
and one rule: of the new engine, only `downloads.py` reaches `openpgp` - by an import, by its
`openpgp` or `verify` taken from `downloads.py`, or read off a name - and only `keys.py` names a vendored key file.

## 16.8 `runs/` — a run's record and text log, the locks, a detached run

`lib/claude_vm/runs/` is where a run goes: its one record and its text log, the kernel's file locks the run holds, the
run's end on a signal, a run detached as the same program in a session of its own, and the run as a person reads it,
from the stages its verb declares and the events it emits, drawn as today's runs are. It imports
`schema/`, the printer of a command among it for the line that cancels a run, and `fleet/`, and nothing of `cli/`,
`engine/` or `control/`, and no pydantic.

**A job: `job.py`.** Every run is a job, at a terminal or detached. `create(jobs_dir, verb, command, host, machines,
starter, attached)` makes `jobs/<id>/` in the state folder (`paths.jobs_dir`), with any folder above it that is
missing, at mode 0700, the jobs folder through `fleet/files.py` (16.9), refused where a link stands in its place, the ID
sixteen hex digits as today's MCP jobs have. Before anything else is written it opens the
job's `lock` (0600) and takes `flock(LOCK_EX)` on it; then it writes `job.json` (0600). The descriptor stays open for
the life of the process that made the job, and, as Python opens every descriptor, no child inherits it: the kernel lets
the lock go when that process ends, however it ends, so nothing is ever left to clear. Every value is checked before
anything is made, and the module is the standard library alone, with `store`, `text`, `secret`, the patterns and the
fleet's folders (`fleet/files.py`).

| Field of `job.json` | Holds |
|---|---|
| `id`, `verb`, `command` | The job's ID, the verb, and the command as typed, with no secret |
| `inputs` | The run's resolved inputs, a typed `--set` value among them |
| `starter` | What started it: `{"kind": "terminal"}`, `{"kind": "detach"}`, or `{"kind": "mcp", "server": <id>}`, that server's own ID |
| `host`, `machines` | The host the run is on, and the machines it names, by identity |
| `making` | The machines it is making that have no identity yet, each by its stand-in key with the name a person chose, until its join |
| `names` | The name of each machine, by its identity, `{identity: name}`: given with `machines` at `create`, or joined from `making`, which the join keeps there |
| `locks` | The names of the locks it holds |
| `attached` | Whether it runs at a terminal |
| `pid`, `started` | Its process and when it started: information for a person and for a machine's `job` line, never read to decide a state |
| `deadline`, `stop` | When a detached run is stopped; a stop asked for, `{"by", "at"}` |
| `directory`, `log` | The run's short-lived directory, and its plain-text log |
| `waiting` | What it waits for, such as a page in the browser, or nothing |
| `next` | NEXT STEPS, records `{machine, matter, line}`, a line removed once nothing is left |
| `machine_outcomes` | Each machine's outcome, the stage it reached, and why |
| `outcome`, `exit`, `ended`, `why` | At the end: `succeeded`, `failed`, `cancelled` or `declined` (the person's answer ended it, never a failure), the exit status, when, and why |

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
with `listen` (the text log and the live view), in order and in the run's own process: each hears it whatever one
before it raised, and the first error is raised once the last has heard, so that a view whose terminal has gone never
keeps the run's end from its log. The kinds are `stage`, `step`,
`report`, `waiting`, `next`, `join` and `end`; another is refused, `child` among them, since a child the run started is
its registry's (`children.py`). Every string an event carries goes through
`text.plain_text`, and a `Secret` anywhere in one is refused before anything is written or heard. The job holds the
run's redaction list: every secret the run opens is added to it (`redact`, a `Secret` alone, each once; `redacted` is
the list), and every string of an event, its text, its data and their keys, is struck of each of them
(`secret.struck_all`), before and after it is made plain, so that neither the record nor a listener has one; layer 1's
child is struck of the same list (§16.10). `tests/unit/test_aw_job.py` holds it. What an agent and
`logs` read is also kept in the record: a `waiting` (empty text: nothing), a `next` with its `matter` (an empty line
removes it), a machine's `end` with its outcome and the stage its last `stage` event named, and the run's `end` (no
machine) with its outcome and exit status. Only the process that made a job emits its events, listens to them or drops
it.

**A machine not yet made.** A machine is named by its identity, which a VM the run is making has not until its clone
gives it its SMBIOS UUID. Until then the run names it by a stand-in key, `job.stand_in()`: `making:` and sixteen hex
digits, a form no identity (`agent-warehouse-<SMBIOS UUID>`) and no VM's name (which has no colon) takes, so it is never
taken for either. Its first event carries the name a person chose (`name=`), as an identity's first event does, and the
record keeps it in `making`, the stand-in with that name, so that a run killed before the clone, read by `logs` or an
agent, says what it was making, and `machines` keeps identities alone (`create` refuses a stand-in there). One `join`
event, its machine the identity and its text the stand-in, joins the two: the identity joins `machines`, the stand-in
leaves `making` and its name is kept under the identity in `names`, so that a view made after the join, `logs` or an
agent still says the VM by its name, and what the record keeps under the stand-in moves to the identity, in its place:
its outcome in `machine_outcomes`, its lines of `next` where they stood among the other machines', and the run's
`waiting` where it is the machine's, a section's wait among them. From then on the stand-in names nothing. Refused before anything is written or heard: a stand-in's first event with no name; an event that names a
stand-in joined already; a join with no identity, or a stand-in for one; a join of a key that is no stand-in, of a
stand-in joined already, or of one the run is not making; and a join to an identity the run has named before, given or
in an event, which would make two machines one.

**Reading and dropping.** `find(jobs_dir, job_id)` takes only what the pattern `job_id` holds, sixteen hex digits,
and `listed(jobs_dir)` gives every job newest first. Each passes over a folder whose `job.json` is not there yet, or is gone, and a job that vanishes
while it is read is never an error. `find` reads strictly, as `pathlib` did before Python 3.14: no record is one that is
not there (no such file, a folder that is no directory, a loop of links) or is no file, and any other error of its look,
a folder whose mode forbids it among them, is raised as itself, where 3.14's `Path.is_file()` takes every error for no
file and a refusal at the locks would say the holder was starting or ending; `listed` never refuses. A run refused at
its locks leaves no job, and its drop never fails the refusal it
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

**The text log: `textlog.py`.** Each run writes one plain-text log, a listener of its job's events registered before
the live view: `<LOG_DIR>/<stamp>-<verb>[-<part>].log`, the stamp the run's start (`YYYYMMDD-HHMMSS`, as
`tools/run-logged` names today's), the verb a row of the table whose run is a job, and the part one of the values of a
pattern that is no machine's (deploy's `--only`, login's `--only`) or `check`, so that no word a person typed as a name
becomes a file's (`name`). The folder is made where it is missing and kept at mode 0700; the file is made new at 0600,
never through a link, and a second run of a verb in the same second takes the next free name (`-2.log`). Its first
line names the job and its command; then each event is one line a line of its text, with its time, its kind, its
machine by its name (the record's `names` and `making`, a first event's `name`, a join's: never a stand-in where a
name is known; a join says the identity it joins) and what it carries; and a line of the program's own for what the
ladder keeps off the terminal (the loop's own reports: `attached.py`'s `_unsaid`). Each line is written whole with one
`os.write`, so it is on disk through any end of the process, `os._exit` and a kill among them; the descriptor is no
child's. A log that cannot be made or written is said once on standard error, and the run goes on without it: the
listener never raises. The record's `log` names the file. Where it goes (`logs(home, run)`): `LOG_DIR` as the
environment gives it (`RunWords`), a relative one from the folder the run started in; `LOG_DIR` empty, no text log for
a run at a terminal and the job's own folder for a detached one, which nobody could follow otherwise; not set, a
checkout's `.logs/` where the fleet's home is the checkout's own, as today's justfile has it, and the state folder's
`logs/` for the installed program and for a home `AGENT_WAREHOUSE_HOME` names, a test's among them. `runs.run` opens
it once the run's locks are taken, after the prune, and closes it once the run has ended; a run whose ladder never
began, and so left no job, leaves no log either.

**The prune: `prune.py`.** When a run that writes a log starts, `prune(log_dir, jobs_dir, retention)` deletes what is
older than `LOG_RETENTION_DAYS` (14 where it is not set; a value that is no whole number prunes nothing and says so),
as `find -mtime +<days>` counts it, and three kinds of thing alone: a file at `LOG_DIR`'s top named as a log is
(`LOG_NAME`, today's `[0-9]*-*.log`), never a link, and only where `LOG_DIR` is itself a folder: `runs.run` holds it
to be one (textlog's `_kept`, never a link) before the prune, and a `LOG_DIR` that is a link has nothing in it pruned
and the run no log; a job's folder, named by a job's ID, a directory and never a link, whose lock is free
(`lock_held`), aged by its end, else its record's last change (a run killed outright), else the folder's own (a folder
a drop could not empty, which has no record), the age read first and the lock only of a job old enough, so another
run's `create`, whose lock is taken without waiting, never meets the prune's; and the short-lived directory that job's run
left (`rundir.py`'s, which nothing else removes once its run was killed), only where the record names one of its form,
`aw-run-<that job's ID>-…`, a directory, this account's. A job whose lock is held is a run that lives and is never
pruned, whatever its age; one whose lock cannot be read is said by its ID and kept, never read as free. A log and a
job's folder are held, before they go, to be entries of `LOG_DIR` and the jobs folder by their real paths, and a run's
directory to be of its job's name and this account's, so the vault's key file, `client.yml` and the
fleet's folder, which lie outside every one of them, are never reached, whatever a record says. Today's MCP server
prunes its own jobs at a fixed 14 days (`lib/claude_vm/mcp/server.py`); this prune is the new engine's.

**`logs`: `listing.py`.** The handler of `logs`, which loads no pydantic, Ansible, asyncio, workflow or MCP module
(`tests/unit/test_aw_cli_imports.py`). With no job, every job newest first (`listed`): its ID, its state (`state`), its
start, how long it ran or has run, its command and its log's path; a lost job is said so, its record never written.
With a job, its text log as written, each line made plain; `--save` says where it is already, since a run's log is
written as it goes. With `--follow`, the log and then each line added, read as the file grows, until the job's lock is
free, and then whatever the file gained, since a run writes its end before its lock goes; then its outcome, and each
machine's by its name (`names`, and `making` for one not yet made). Ctrl-C ends the follow, says the job goes on and
exits 130. A job with no log is said so; its follow waits for its end all the same. A job whose lock cannot be read is
said so, never as `lost`. Before any job is read, a fleet's record whose jobs store is newer than this program reads is
refused by name (`record.refuse_newer`). `tests/unit/test_aw_logs.py` holds the log, the prune and `logs`.

**A run's children: `children.py`.** Every process a run starts carries the job's marker, `AGENT_WAREHOUSE_JOB`, the
name today's MCP jobs set, with the job's ID: whatever reads it matches the ID, never the name alone. `start` gives it
whatever environment it is handed; `unmarked(env)` takes it away, for the two kinds of child that must not carry it,
which no end of the run reaches: a shared SSH master, which other runs may ride after this one ends, and a child meant
to outlive its run. Only the process that made a job marks a child with it, and a run carries no marker of its own: one
it inherited (a run started from an agent's job, or from a terminal of an editor a run opened) is unset by the launcher
and taken out of the program's environment at its start (16.3), so the children a run starts outside `start` - the
`ssh` that `route.py` runs for the hop and `ssh -G`, the `ps` that `proc.py` runs for the tree - carry none.
`start(job, argv, own, env, stdin, stdout, stderr, kind, pass_fds=(), cwd=None)` is the one way the engine starts a
child that ends with its run, the SSH runner's `ssh` children among them, a coroutine over asyncio's own, in the folder
`cwd` names, an absolute path (a relative one is refused before anything starts), or else where the run is:

- `own` is `group`, a process group of its own, so that the terminal's Ctrl-C reaches the run alone; or `session`, a
  session of its own, its standard input `/dev/null`. Standard input is always named, and never the run's own
  descriptor 0: a child in a group of its own that read the terminal would be stopped, and one in a session of its own
  would take what the person types.
- The child gets the marker whatever environment it is handed, and a master loses it. Before it runs, every signal a
  handler can be given is put back to its default and none is blocked: a process starts with what its parent ignored
  still ignored, a run started under `nohup` or with `&` ignores SIGINT, and Python ignores SIGPIPE. That is a
  `preexec_fn`, which Python runs in a child forked beside the run's threads, before its exec, where a lock another
  thread held at the fork stays held for good: so it calls `signal.signal` and `signal.pthread_sigmask` alone, two
  system calls each that take no lock, and nothing else, no import, log or file, which a test holds; Python makes the
  forking thread the child's main thread, so `signal.signal` works there whatever thread forked.
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
holds once the transport is closed or collected. Nothing ends it; a caller that wants its status polls it. **A detached
run** is `start_detached(argv, env, answer)`'s, the one child started before any job exists, by the starter of
`--detach` (`detached.py`, below): unmarked, since it makes its own job and marks its own children, in a session of its
own, its standard input `/dev/null`, the signals at their defaults, its standard output and error both `answer`, the
pipe its starter reads, and nothing else open, a lock's descriptor refused by the lock's name before anything starts;
entered in no registry, since it is no child of a run's.

**A child the terminal stopped.** `watched(job)` gives the job's children still running and, beside them, `route.py`'s
`ssh` still running in a process group of its own (`route.leading`: the hop's and its `ssh -G`, which the new engine
starts there, and `start` never starts), each as a child of kind `ssh`: the children of this process a question at the
terminal can stop. `asking(job)` gives those of them that the terminal has stopped, with the signal: SIGTTIN, for a
child in a group of its own, or of its group (the `ssh` of a person's own `ProxyJump`, which `BatchMode` does not
reach), that read the terminal to ask a question there, a passphrase or a second factor, or `git`'s or `sudo`'s; or
SIGTTOU, for one that changed its settings or wrote to it to ask, as OpenSSH's `readpassphrase` turns the echo off
before it reads, which stops an `ssh` in the terminal's background before any read. It reads with `waitid` and
`WNOWAIT`, reaping nothing, and a child stopped for any other reason is not one; where Python has no `waitid` (macOS
before Python 3.13) none is found.

Nothing is written to a file, and nothing reads the process table. `tests/unit/test_aw_children.py` holds it, with real
children that report what they were given (on Linux, a program that is not Python shows the kernel's own masks of the
signals ignored and blocked), and holds four rules by scans of the new engine's code: a child process is started only
through this module, by any of Python's ways, `posix`'s, a browser's opener, the standard library's own starts of
`file` in `platform.architecture()` and of `uname -p` in `platform.processor()` and `platform.platform()`, and a module
or an attribute asked for by its name among them; `close_fds` is never anything but `True`; no process is forked
without an exec (`os.fork`,
`os.forkpty`, `pty.fork`, `posix`'s, a `fork` start method or context, or one named by a variable), since a forked child
keeps every lock until it execs; and `pass_fds` and `set_inheritable(..., True)` appear only in `start`, and nothing
else makes a descriptor inheritable (`os.dup2`, `F_SETFD`, `F_DUPFD`, `os.pipe2`). A syntax-tree test holds what a
forked child runs before its exec to `signal`'s two calls.

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
`locks/` (`paths.locks_dir`, made through `fleet/files.py`, never a link), each opened in this module alone at mode
0600, never through a link, and kept open for as long as it is held. As Python opens every descriptor, no child
inherits one, and `children.start` refuses one by its name (`HOLDERS` reads `locks_held`). The kernel lets a lock go when its process ends, however it ends, so nothing is
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
once the run is stopping (`asyncio.CancelledError`), and is cleared the same way however it ends, so that no wait of a
run stopped, or of one machine of several, stays in the record through the stop's grace. Since it ends at the stop, a
section is entered outside any shield: a Ctrl-C sets the run's stop inside a shield too, and would end the wait the
shield was there to outlast. The lock is let go when the
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
SIGTERM, SIGHUP and SIGQUIT (a `Ctrl-\`), but one ignored on the way in, as `nohup` or a shell's `&` leaves one, which
stays ignored. The ladder:

| The end | What happens | The record | The process |
|---|---|---|---|
| Ctrl-C, once | `cancelled` and `stopping` set; the tree under the run remembered with `proc.snapshot`, the interrupts held off while the table is read; the stop recorded, `{"by": "terminal", "at": …}`, so the job reads `cancelling` through its grace (one a `cancel` recorded is kept); SIGINT to each Ansible child's group, but one started inside a shield still up, which gets it once that shield is done, since the line says the phase finishes first; a line on standard error that the run is stopping, or that it stops once the shielded phase is finished; a question up ended there. Once the body and every child it started have ended, what is left is ended at once; 30 seconds after the Ctrl-C (`STOP_GRACE`, today's MCP jobs' grace) whatever still runs is ended and the body cancelled, but a shielded phase is waited for | `cancelled`, `Ctrl-C` | exits 130 |
| Ctrl-C, twice | the tree ended at once from the signal's handler itself, then `Abandoned`, a `KeyboardInterrupt`, raised there while the loop runs, so that a step stuck in it cannot hold the end | `cancelled`, `Ctrl-C twice` | exits 130 |
| SIGHUP or SIGTERM | the tree ended with that signal from the handler, as above; what ignores it is killed after five seconds (`SETTLE`), after a Ctrl-C too, never at the Ctrl-C's grace | `cancelled`, the signal's name, exit 128 and its number, over a Ctrl-C that came first | its handler put back to the default, and dies of it |
| SIGQUIT (a `Ctrl-\`) | as SIGTERM, what was typed and not read thrown away first, as at a Ctrl-C; the tree ended with SIGTERM, never SIGQUIT, whose default writes a core | `cancelled`, `SIGQUIT`, exit 131 | dies of it, its core limit set to none first (`RLIMIT_CORE`) |
| An error the body did not handle | the tree ended | `failed`, the error | the error raised again |
| The body returns | what it still holds of its children ended | `succeeded` for 0, `cancelled` for 130, else `failed` with the status, or `declined` with the status and why where the body said so by `decline(why)` (destroy's no): an exit status alone never makes it | exits with the status |
| SIGKILL | nothing | none: the job reads `lost` once its lock is free | killed |
| A detached run's limit (`limit`, below) | as SIGTERM: the tree ended with SIGTERM, what ignores it killed after `SETTLE`, the body cancelled; a line says the limit came. Where a stop was asked first (a Ctrl-C's, a cancel's), its grace ends there instead, and the stop stays the run's end | `failed`, `the deadline: run_detached_limit's <limit> came`, exit 2; each machine begun and not ended `failed` at its stage | exits 2, as a failed run does |

On every path but SIGKILL, every child of the registry has ended or been ended before the loop closes, since asyncio
kills an `ansible-playbook` it still holds when its loop closes and leaves the playbook's detached task workers running;
the body's asynchronous generators still suspended are closed, for `SETTLE` seconds at most, so that one whose `finally`
does not return holds the run no longer; the run's directory is removed; for a run cancelled, each machine its events
gave a stage and no end has one, `cancelled` at that stage (`attached.UNENDED`, from `job.unended()`), since Ctrl-C
twice, a SIGTERM or a SIGHUP raises `Abandoned` wherever the loop's thread is and a machine's own handler may never
run, and one that has an end, a runner's (`fleet_run.py`) or its body's, is never given a second; the exit record is
written, which has the live view say the run's last words, with the signals held off and then taken as notes only, so
that a late one changes nothing but what the process dies of; and last the terminal's settings are put back as the run
found them, its echo among them: where anything changed them, drained (`TCSADRAIN`), so that the terminal has read those
words first, and for a run a Ctrl-C or the ladder stopped flushed (`TCSAFLUSH`) whatever they are, so that what was
typed meanwhile and not read is thrown away, never left for the shell. Each change only while the run is the terminal's
foreground, SIGTTOU held off, so that a run a shell put in the background leaves the shell's settings and is not stopped
by its own end; a run started in the background reads none to put back. Each Ctrl-C and `Ctrl-\` the ladder handles
throws away what was typed and not read yet (`live_region.discard_typed`, SIGTTOU held off, only while the run is the
terminal's foreground), as the terminal's own key does where `NOFLSH` is off, since the live region and a line keep what
the terminal has not written at one. A Ctrl-Z is no end of the ladder's: the terminal's hold takes it, below. "The tree"
is everything under the run's process, ended by `proc.end_under(pid, remembered, signum)`, which signals every process
under a process and what was remembered of it, by each one's start, with their groups, and never the process, its group
or the caller's: today's `end` signals the root too, which here is the run. A shared SSH master daemonises out of the
tree, so no end reaches it, and its open, while it runs - a machine's, `ensure`'s, or the hop's, `route.py`'s - is left
out of what is remembered (`children.opening`), since the master it leaves keeps its `ProxyCommand` in the open's group;
a second Ctrl-C during an open ends that open, and the master it was making. A child meant to outlive the run
(`children.outliving`, the editor, a page's opener) is left out of both, of what is remembered and of what is ended,
with what is under it: no end of the run reaches it. No child subreaper and no systemd scope are adopted, since under a
subreaper one run's end would cut another run's connection through the host. The ladder is built against stand-ins;
experiment 37 tries it with real children, and if a foreground run cannot end its tree by itself, `runs.run` is where a
small watcher would go.

**A child the terminal stopped.** While the run lives it looks, every 0.2 seconds, for a child the terminal has stopped
(`children.asking`): one in a group of its own, `route.py`'s `ssh` among them, that asked a question at the terminal,
which a run never answers and where it would wait, stopped and silent, holding its step until the run's end. A stop at
the terminal stops the reader's whole process group, so a reader in such a child's group, as the jump's `ssh` of a
person's `ProxyJump` is, stops the child too. What the watch sees is the run's own children and `route.py`'s `ssh`,
read by `waitid`: a program that made a group of its own under one of them stops alone, its parent waiting on it, and
is no child of the run's for `waitid` to read, so it is not seen. Nothing the engine starts makes such a reader, but a
program of the person's own ssh configuration may, since the runner reads `~/.ssh/config`: a `ProxyCommand` under GNU
`timeout` without `--foreground`, which makes a group of its own to signal its command's. Its step then waits in
silence until its own bound ends it (the runner's 20 seconds to say anything, `route.py`'s 5 to 20) or a Ctrl-C does,
and fails as a silence, not as a question with its remedy; once its parent is ended, the kernel ends the reader's
orphaned group with it. The run never reads the process table to find one: the process table, the one way to see it,
says no signal, so it cannot tell a terminal's stop from a person's SIGSTOP and would end what a person paused; and it
would read every process there is, each 0.8 seconds, through every run at a terminal with an `ssh` child. If a program
the engine runs ever makes such a reader, the look comes back with that program as its test. Each child seen is said at
once on standard error, in one line that names its program, the leader of the group the terminal stopped, which is
what `waitid` sees (the runner's `ssh`, for a question the `ssh` of its jump asked inside its group), and the question
it may be (a passphrase, a host key to accept, a password); what to do is said only where that program is `ssh`: hold
the key in an agent (`ssh-add`), give a `ProxyJump` of the person's own what it asks for in its own settings, or
connect through it once by hand to accept its host key. It is ended with what is under it and its group, within a
second; the step that waited on it sees it killed, its machine's failure. Giving such a question the terminal, so that
it reaches the person, is the
owner's to ask for.

**Nothing holds the process once its record is written.** The engine's blocking calls (`asyncio.to_thread`, a
`run_in_executor` of the default) run on the ladder's own executor, the loop's default: each call on a daemon thread of
its own, and never joined, nor known to the interpreter's exit, which joins a `ThreadPoolExecutor`'s threads and would
hold a cancelled run's process for the rest of a call, up to `follow`'s ten minutes. Where one of them is still in a
call once the record is written and the terminal put back, or any thread Python would wait for at its exit, the process
ends at once: its streams flushed, an error the body did not handle said as Python says one it ends on, then `os._exit`
with the status. A line the printer would not write, its `Unwritten`, which `cli/main.py` then never sees, is said as
`main.py` says it, in one line, the code's own fault (`printer.own_fault`), never a traceback, and the status is 1
(16.2, 16.3).

**No thread blocks a signal.** A process forked from a thread starts with that thread's signal mask, which stays through
`exec`, so a program a call in a thread starts with `subprocess` - `route.py`'s `ssh`, the hop's shared master among
them, which outlives the run, or a vendor's tool - would take no Ctrl-C, hangup or kill with SIGINT, SIGTERM or SIGHUP
blocked. So each of the executor's calls runs with no signal blocked, whatever its maker held (a thread starts with its
maker's mask, which the run's thread blocks while it holds the interrupts), and so does every program it starts;
`children.start` and `outlive` clear the mask in their children all the same. A signal the kernel hands another thread
still runs its handler in the run's thread, wherever that is; so while the run reads the process table and signals what
it leads to, and while it takes its outcome and writes its record, it holds the interrupts off (`_held`): blocked in the
run's thread, and the handler, which a signal handed another thread still runs there, only takes note; once the
outermost hold ends, each signal noted is handled as it came, and a hold inside another hands on nothing. The hold is
real whatever thread is alive, a body's own pool and asyncio's watcher thread on macOS among them. No hold runs the
loop: the close's loop, which ends the watch and closes the body's generators, runs unheld, so no code of the body's
runs with the interrupts blocked and nothing it starts takes them so; a second Ctrl-C there ends the tree, and a SIGTERM
or SIGHUP is what the run dies of, as anywhere else.

**A question.** `typed(fd)` reads what the person types on the loop (`add_reader`), never in a thread, which would hold
the process as above: a line in a terminal's cooked mode, a key in raw mode. It reads from a terminal or a pipe: any
other descriptor, a regular file or `/dev/null` as standard input is in a detached run, an agent's job or under
`< file`, is refused before anything waits, in a sentence of `aw`'s own, since Linux's epoll cannot wait on it and
macOS's kqueue would end the read at once. While it waits a question is up, and the
first Ctrl-C ends the run there, its read raising `asyncio.CancelledError` as the run stops, so one Ctrl-C at a prompt
ends the run with 130 rather than leaving it to wait for an answer through the grace; the line that the run is stopping
then begins on a line of its own. A prompt that reads the terminal raw, where Ctrl-C is a byte, sends the run SIGINT for
it. No question is begun once the run is stopping, and one descriptor is read for one question at a time. The run's
questions, a line, a menu or a hidden answer, are built on it.

**What Python and asyncio say.** While the ladder runs, an `Abandoned` that Python drops where it cannot raise one (a
fork's hook) is dropped unsaid (`sys.unraisablehook`, Python's own again once the run has returned), since the run's
end is taken by its wake, and the loop's exception handler keeps the loop's own reports of what the ladder ended or
left - a task destroyed pending, a callback a cut left half done, a body's own callback - off the person's terminal (the
run's text log takes them once there is one). An error of the ladder's own callbacks, a Ctrl-C's stop, the grace's end
or the wake, is never handed it: each says its own at once, in one line of `aw`'s own. Work that will not take its
cancel, still pending once the run has settled, gets one line of `aw`'s own.

`tests/unit/test_aw_attached.py` holds it, with runs in child processes whose bodies start four stand-in Ansible
children (one that ends on SIGINT, one that ignores it, one with a grandchild in a session of its own, one that records
each signal it gets, with when, and ends on none): every way out but SIGKILL, a body's own `KeyboardInterrupt`,
`SystemExit` and `CancelledError` among them, leaves its exit record, its status, the signals each child got, and
nothing running, read once the run has ended and before the test's own cleanup; the ways walk every signal whose handler
is the ladder's as a running body finds them and every outcome. Ctrl-C twice ends the tree from the handler while the
body holds the loop; a call in a thread holds the process no longer than the ladder takes, on a daemon thread that
blocks no signal, a program it starts (`route.py`'s `ssh`) with none blocked, made by the run's thread as it is or while
it holds the signals off (as the program reads its own mask, and on Linux its `SigBlk`), and ended by a SIGTERM; a
SIGTERM the kernel hands that thread while the table is read is handled after the read, a SIGTERM and then a SIGHUP
there in the order they came, one noted in a hold inside another only once the outer hold has ended the tree, and one
during the record of a Ctrl-C's run only after its record, which still says the Ctrl-C; a call in a thread handed
`stopping` has it within a moment of a first Ctrl-C and of a SIGTERM, and at the close of a body that returned; the
close runs a body's generator unheld, a program its `finally` starts with no signal blocked, and for `SETTLE` at most;
an Ansible child started in a shield has its SIGINT once the shield is done, the phase held on a file the test removes;
a SIGTERM after a Ctrl-C whose body returned ends the run within `SETTLE`, recorded as itself; one Ctrl-C at a question
ends the run at a pseudo-terminal, cooked or raw, a question on `/dev/null` or a regular file is refused in `aw`'s own
words, and a second question on a descriptor while the first waits is refused; a ladder that cannot be built leaves no
job; a record that cannot be written is said; asyncio's reports stay off the terminal, an error of the ladder's own
callbacks is said in `aw`'s own words, and Python's unraisable hook is back once the run has returned; the grace, the
stop recorded, one a cancel recorded first kept, a shielded phase, a signal ignored on the way in, and the echo at a
pseudo-terminal after each end; a page's opener meant to outlive the run alive and unmarked after each end; and a child
that asks a question at a pseudo-terminal said within a second, named, given `ssh`'s remedies only where it is `ssh`,
and ended, its run ending with its body's own status: one that reads it (SIGTTIN), an `ssh` that turns the echo off
first as `readpassphrase` does (SIGTTOU), and `route.py`'s `ssh`; and a run at a pseudo-terminal, its terminal its
controlling one, whose child of kind `ssh`, in a group of its own in the run's session, asks nothing for three seconds
starts no `ps`, counted by a stand-in first on its `PATH`.

**A detached run: `detached.py`.** `--detach` is the parser's for every verb whose row says `changes` (16.3), `deploy`,
`login`, `code`, `destroy`, `release`, `init` and `install`, and this module serves them all alike, with nothing of one
verb's: a verb the table marks changing is detachable with no change here. There is no supervisor (T5): the run is the
same program in a session of its own, which makes its own job, takes its own locks, writes its own record and keeps its
own limit.

- **The starter.** The command is parsed, held by the one check, and refused at once where it would be refused, by
  `cli/main.py` and `cli/dispatch.py` as any command is, a fleet that is not there among it; then the dispatch hands it
  to `start(program, verb, run, values, env)` in place of the verb's handler, which builds the run's command
  (`argv_of`: this Python under `-I`, the program by the path `main` was handed, and the printer's words of the same
  values with the run's own `-v` and `--set`, after `--detached-run` and, where it was given, `--attended 1`) and starts
  it through `children.start_detached` (above). Its environment is the caller's, with the program as it was started
  handed on (`printer.handed()`, `AGENT_WAREHOUSE_STARTED_AS`), so that the lines the run prints say it: the run is the
  person's own command, as it would be at their terminal, so the proxy's variables (`HTTPS_PROXY`, `ALL_PROXY`,
  `NO_PROXY` and their lower-case forms) reach it, the one place a proxy that needs a user and a password is given,
  since `network_proxy` refuses one (16.9); the allow list of today's MCP server (`lib/claude_vm/mcp/environment.py`)
  stays that server's own. `detach(argv, env)` then reads the pipe for `STARTING` seconds (60): each line the run says
  before it is under way is said again on the starter's standard error as it came, made plain, so that a refusal at its
  locks (which leaves no job), a refusal of its handler's and a line the printer would not write are all said; the line
  `started <job id>` ends the read, and the starter prints the job's ID, then `aw logs <id> --follow` and
  `aw cancel <id>`, each a whole line of the printer's printed by itself, and exits 0. A run that ends before it is
  under way ends the starter with its own status, 1 where it said nothing and ended otherwise; with no `started` in
  time, or at a Ctrl-C of the starter's, the run is ended with everything under it (`proc.end`, never its group
  alone), and the starter says so and exits 1, or 130.
- **The run.** `--detached-run` has the dispatch call `begin()` before the handler, and the handler's run goes through
  `runs.run` as any run does, which reads `running()`: the job is made with the starter `detach` (an MCP server's own
  where the server named itself) and `attached` false, whatever the verb gave; its locks are taken by the run itself
  (C6), so a second run on its machine is refused while it lives and proceeds once it has ended; its text log is opened
  (16.8's text log: `LOG_DIR`'s, or the job's own folder where `LOG_DIR` is empty or the verb gave none), the prune's
  lines said before it, on the pipe; and then `under_way(job)` writes `started <id>` on a line of its own (a newline
  first, which ends a line said without one, so that it never hides the word) and points its standard output and
  error at that log, or `/dev/null` where it has none, so that nothing it says from then on reaches its starter, and a
  starter that has gone is no reason to stop. The live view it draws writes to that log as to any stream that is no
  terminal. From there it runs with no terminal: it asks nothing and takes each question's safe answer (`ask.py`), it
  never changes a terminal's settings, so the echo of a shell it was started from stays the shell's, and it signs in and
  opens pages only where `--attended 1` was given (16.3). It outlives its starter, the terminal its starter ran at, and
  an MCP server; `cancel` stops it as Ctrl-C stops a run at a terminal; a SIGKILL leaves no exit record, and the job
  reads `lost` once its lock is free.
- **The limit.** The setting `run_detached_limit`, in minutes, 120 by the owner's decision and 0 for none, which a
  verb gives `runs.Draft` as seconds (`limit_of`; `detached_limit` defaults to the setting's default, so a verb that
  gives none is held to 120 minutes): the record's `deadline` says when by the computer's clock, and the run counts it
  itself on the loop's monotonic clock from its ladder's start (`attached.run(..., limit=)`), which leaves out a time
  the computer slept where the system's monotonic clock does (experiment 61 tries it across a closed lid). At it the
  run stops as at a SIGTERM and its record says `failed`, the deadline as why (the ladder's table above). A run at a
  terminal has no limit, as today.

**A run over several machines: `fleet_run.py`.** `each(context, machines, body, most, names=...)` starts `body(machine)`
for each machine, by the key its events name it by (its identity, or a VM's stand-in until its join), never more than
`most` at once: the setting `run_forks`, 20 by default as today's `forks` (`ansible.cfg`), which `--set run_forks=30`
changes for one run, and which the verb passes. The machines begin in the order given, each as soon as another ends. A
machine's failure, an `Exception` its body raised, is caught and recorded, and ends no other machine; a body that raises
`asyncio.CancelledError` by itself, as a section's wait the run's stop ended does, is that machine cancelled. Once the
run is cancelled (`context.cancelled`), no machine begins: each left is recorded cancelled, "not begun", and those under
way end by their own steps. Where the runner itself is cancelled, at the stop's grace's end or by an enclosing group's
or a timeout's cancel with no stop set, or its own record fails, no machine begins after it, every machine still under
way is cancelled and waited for before it returns or raises, as an `asyncio.TaskGroup` does, so that nothing of the run,
a question among it, runs on past the loop's end. Each machine's end is one `end` event the runner alone emits, with its
outcome and its why (what the body returned where it went well, the error's sentence where it failed); its stage is the
record's rule's, the stage the machine's own last `stage` event named since the job was made, moved to its identity by a
join (`job.py`): what the record keeps in `machine_outcomes`, and what `each` returns, by the machine's key at its end.
A body that emits an end of its own is refused as its error, the machine failed and saying so: the listeners hear the
body's end and then the runner's, and the record keeps the runner's. The body emits its own stages, the first of a
stand-in with its name and before any section for it, and its join; a stand-in's end carries its name (`names`, refused
before anything begins where one is missing), so that one cancelled before its body began is still said and kept by
name. A machine named twice, or a bound under one, is refused before anything begins. `one_at_a_time` is the same with a
bound of one, for the sign-ins, and `status` gives the run's exit status from the outcomes: 0 only where every machine
succeeded, 130 where those that did not were all cancelled, else 1, a machine failed or `declined` (C38's outcome, a
person's answer, which exits 1 as a run declined does) or one it does not know. Whether layer 1 over a fleet is one
Ansible child a machine or one child over every machine, and the bound's default, are experiment 62's (C18).
`tests/unit/test_aw_fleet_run.py` holds it: thirty machines never more than twenty at once and every free turn taken,
and a bound given as five holds five; one that fails leaves twenty-nine done, none of them cut short, and the run
failed; a cancel lets no machine begin, those under way finishing, at a real Ctrl-C too, the run's record then
`cancelled` with every machine's outcome; the runner's task cancelled at each turn of the loop with no stop set lets no
machine begin after it; the runner cancelled, or its own record refused, cancels and waits for every machine before it
raises, the error as itself; the exit record holds thirty outcomes, each with the stage it reached, one reached before
the runner ran among them; a body's own end refused, the machine failed; a stand-in's end kept under its identity once
joined, and by its name where it never began; one at a time never two at once; the exit status from each mix of
outcomes, `declined` among them; and what is refused before anything begins.

**A run as a person reads it: `stages.py` and `model.py`.** A verb declares what its run is made of, and its workstream
writes the declaration: `Declared(title, stages)`, each `Stage` with a key, which the run's events name, the label a
person reads, its `Step`s (each a key and a label), whether its work is each machine's (its rows mark each machine; the
template's and the golden template's are the host's and mark none), and whether it is quiet (counted, and drawn only
once something in it failed). Keys are lower-case words joined by hyphens; a stage declares every step its events may
name, or none. Pure data. `stages.DEPLOY` is `deploy`'s, with today's labels (`lib/claude_vm/run/recipes.py`): template,
provision, configure, SSH aliases, sign in (Claude, GitHub, Tailscale), editor (today's window's ten steps), a quiet
finish, golden template.

`model.Run(declared, now, machines)` takes each event of the run in its own process, `feed(event, now)`, the time given
by its caller, so nothing in it reads a clock, a file or the terminal, and nothing is kept between two processes; it
imports nothing of `tui/`, which draws it. A machine is keyed by its identity (`agent-warehouse-<SMBIOS UUID>`), as
`job.json`'s `machines`, a machine's `end` and layer 1's step events name it: one key for every event of a machine. The
name a person chose rides beside it, as an event's `name` (layer 1's steps carry it) or as the run's machines given as a
mapping of identity to name; the model keeps each identity's name (`names`, `name(machine)`), and a drawer says the name
wherever it says a machine. Every emitter passes the identity: an event whose machine is the name the run keeps for
another key, or whose `name` is another key of the run's, is refused, so one machine is never two rows drawn under one
name. A VM the run is making has its row from its first event all the same, as today's run draws each VM by its name
from the start: its events name it by its stand-in key, the first of them with its `name` (or the run's machines given
with it and its name), and the `join` event moves everything the model keeps under the stand-in to the identity, in its
place (`JOINED`): the machine's row in `machines`, its name, its part in every stage and step with their times and
outcomes so far, the order its end came in (`_Book.ends`, which says what of every machine's it reads), the stage it
reached, its waits, its reports, its failures, its outcome, its latest line and its NEXT STEPS lines. So the identity's
events go on where the stand-in's left off, a failure said before the join is not said again at the machine's failed end
after it (B2's rule and the end's "nothing of it failed" read a failure's machine), and nothing drawn changes: one row,
before and after. From then on the stand-in is no key of the run's. The events it reads, their text plain as `emit`
makes it (a tab, a line break and Unicode's format characters stay, and a drawer makes them visible):

| Event | What the model makes of it |
|---|---|
| `stage`, its text a stage's key | No `outcome`: the stage begins. `outcome` done, skipped or failed (with `why`): it ends, and so does whatever of its steps is still open for it |
| `step`, its text a step's key, `stage` its stage | Begins or ends that step as a stage does; its start begins its stage where that had not begun for its machine, never one that has ended for it (its failure and its time kept) |
| `step` that names no stage | A line of a machine's work (each of layer 1's events): kept as that machine's latest line, beginning and ending nothing |
| `report` | Kept whole, in order, to be printed above the region |
| `waiting` | Opens the wait of its machine and its `section`, in place of one open; an empty text ends that wait alone |
| `next` | A line of NEXT STEPS for its machine and `matter`; an empty line removes it |
| `join`, its machine the identity, its text the stand-in | Everything kept under the stand-in is kept under the identity from then on, each in its place (`JOINED`); the stand-in names nothing more |
| `end` | With a machine: its outcome, the stage it reached (`stage`, else its own last `stage` event, as the job's record keeps it), why, and what of it is open ends (done, failed, stopped). With none: the run's end; everything open ends, done where the run succeeded and stopped where it failed, was cancelled or declined, and no event is taken after it; a declined end says no failure |

A `stage` or a `step` is for one machine, or with no machine for every machine of the run: a machine with no part of its
own in a stage or a step reads every machine's part where that began before the machine's own end, by the order the
events came in, so a machine that has ended takes no part in a stage begun after it; its own begin while that part runs
takes the part's start, so its time in the stage goes on. A stage that ended reads failed for a machine where a step of
it failed for that machine, and for the run where one did for any: the failure wins, as today's. A wait for the person
is one with no section (a short section's wait, `locks.section`'s, is for another run); a wait also ends when its
machine ends, at the cancel (`cancel(now)`, when a person stops the run: nobody is asked any more) and at the run's end.
So a clear of one machine's wait, or of a wait with no section, never ends another machine's or a section's, where the
job's record keeps one wait for the whole run. No count of tasks is guessed: a stage or a step is done when its event
says so, and one still open at the run's end is done where the run succeeded and stopped where it failed or was
cancelled: cut short, never failed, since what failed said so itself. An event of a stage the declaration lacks, of a
step its stage does not declare, of a kind the job lacks, an ending other than done, skipped or failed, a NEXT STEPS
line with no matter, a name that is no str, a machine named by the name the run keeps for another key or a name that is
another key, a stand-in's first event with no name (and a stand-in given with none), an event that names a stand-in
joined already, a join with no identity or a stand-in for one, a join of a key that is no stand-in, of a stand-in joined
already or of one the run does not know, a join to an identity the run keeps anything for (two machines would be one
row), and anything after the run's end are refused before anything is kept, the order of the events counted included.
`tests/unit/test_aw_run_join.py` holds the stand-in and its join: a run that makes a VM, its events under the stand-in
from the first, the join, then the identity's, read as one row with its parts, its times and one outcome, equal at each
event to the same run keyed by the identity from its first event; each thing the join moves, in its place among another
machine's, a join to an identity kept in any one of them refused, each refusal leaving the model as it was; the record
across the join, at each event the record of the run keyed by the identity, the VM's name kept in `names`, a section's
wait moved and a stand-in's nameless first wait refused; and the live view through it, nothing drawn twice, its refusal
line and its `-v` lines saying the VM by its name.

What a drawer reads: `state(stage, step)` (to come, running, waiting, done, skipped, failed, stopped), `mark(machine,
stage, step)` and `marks(stage, step)` (each machine's, none for a stage that is not each machine's), `running()` and
`running_stage()`, `seconds(now, stage, step, machine)`, the waits (`waiting()`, `waiting_for_you()`, `waited(now, ...)`
for the person's time), `reports`, `failures` (each said once: a step that ended failed; a stage that ended failed for a
machine with no failure of it said for that machine, or for every machine where none of it was said or where it cut
short a part still open; a machine that ended failed with nothing of it failed before, or where its end cut short a
step still open or a stage in which no failure was said for it; and the run's own `why`, where it ended failed with
nothing failed before or where it cut short a part still open; so a machine that ends failed only because of a failure
already said, a step's of the stage it is in among them, says nothing twice, nor does a run that fails only because its
machines did, with nothing left open), `outcomes` (each machine's), the run's `outcome`, `exit` and `why`, `latest`,
`names` and `name(machine)`, and `next_steps()`: NEXT STEPS by machine, by the name a person chose as today's orders by
VM name, the run's own first, then by matter in today's order, each line once, through
`lib/claude_vm/run/next_steps.py` as it stands.
`tests/unit/test_aw_run_model.py` holds it with canned runs (one machine, three, one failing in configure, one waiting for
a sign-in, one cancelled), each event's effect, every refusal leaving the model as it was, a run fed by a job's own
`emit`, and each rule above with a test of its own.

**The run drawn: `view.py`.** `View(run, started_at=..., title, log, host, check)` draws the model as today's runs are
drawn, with `tui/screen.py` alone: its lines, palette and cut, and the marks, a report's and a failure's lines, a
report's first sentence and a summary's head and parts (`MARKS`, `report_lines`, `failure_lines`, `first_sentence`,
`cut_head`, `flow`), which `tui/run.py` draws today's runs with from there too. `tui/run.py` draws today's model of
Ansible's tasks, whose counts, bars and windows of tasks the new model has not, so no base class is shared, and an
import of `view.py` loads neither it nor today's model (`run/model.py`, `run/recipes.py`):
`tests/unit/test_aw_run_view.py` holds it in a fresh interpreter. It gives `frame(now, cols, rows, glyph, color)`: the header (the run's title,
its host beside a second, the clock it started, a dry run), every stage drawn but a quiet one where nothing failed, each
row its mark, its label and its time at the right edge and, where the run has two machines or more and the work is each
machine's, each machine's mark (`alpha ✓ · beta ⠴ · gamma ○`, or their count where they do not fit; a machine that has
ended is to come, `○`, in a stage begun after its end); under the running
stage its steps, and under the stage or step under way each machine's latest line of work (`latest`, where today's
window of tasks stood) and each wait for another run's section, with the time since; a wait for you on the stage's row,
with what the run waits for, whose and since when; folded to the height as today's (the steps to come, then the stages to
come, on a line each); a run of one stage drawn as its steps; and a footer (the run's clock, the stage or step it is at,
waiting for you, stopping once a person stopped it, the log). `title_text(now)` says where the run is, waiting for you, or
how it ended (done, failed, stopped, declined), with no percentage, since nothing counts tasks. `ended_lines(now)` gives a line as
each step and stage ends, and as each wait begins, for output that is no terminal, in today's columns and the order they
happened: a stage of each machine's work is said once every machine of the run is through it or ended (or the run is
over), so a fleet whose machines reach a stage one after another says it once; a wait is said once, with its stage and
step, whose and what it waits for, a wait for you said so (`⏸ sign in / Claude ... waiting for you · alpha: Approve the
sign-in in your browser`), a short section's in its own words. `above(cols, color)` gives what is said above the region
since its last call, in the order said: each report whole, under its machine, wrapped and never cut, and each failure
with where, whose, why and the log, the run's own among them. `summary(now, cols, color, next_steps)` gives how the run
ended (done; done with the machines that failed; failed, and where, with the run's own why in place of the clock where
it failed of its own error; stopped, and where; or `declined · <time>`, a run the person declined, its head alone, as
today's, with no ✗ and no failed count), each stage's mark and time with each machine's mark and its steps' times and your time in
them, what it never reached, each machine's reports a line each (the run's own under THIS MACHINE) but what NEXT STEPS
says whole, and the totals; NEXT STEPS itself is the caller's to print after it, as today's run prints it. No line of a
frame or a summary is wider than `cols - 1`. A machine is said by the name a person chose (the model's `name`), never by
its identity, wherever a machine is said: the title, the marks, a line of work, a wait, a report's head, a failure's
whose and the summary's headings. What the events said is made visible before it is drawn, as today's engine makes every
report: a wait's words, a line of work and a name one line (`text.one_line`: no line break, tab, control or format
character), and each line of a report or a failure visible (`text.visible_text`), its tabs expanded and its blank first
and last lines dropped. Nothing in it prints, reads a clock, a file or the terminal: the caller, the live view, prints
what it gets. Provision's row says no VM ID or address: nothing declared carries them, and the
run's report that the machine is up says its address above and under the machine in the summary.
`tests/unit/test_aw_run_view.py` holds it with canned runs, each timed as the matching run of today's
`tests/unit/test_run_view.py` and compared with it row for row where the two have the same stages, at every width and
with `tests/unit/vt.py`, which counts no line wrapped at 80 or 100 columns, and each rule above with a test of its own.

**The live view: `live.py`.** A run draws itself, in its own process, from the events it emits: no event file, and no
other process redraws it (following a run from another terminal reads its text log). `Live(job, declared, stream)`, made
in the run's thread once its job exists, listens to the job's events, feeds each to the model at its own clock
(`time.monotonic()`, never the event's wall-clock time) and says the run in one of three ways, decided at its start.
Where the stream is a terminal that can take the region (`live_region.can_draw`), the view's frame is drawn on the
stream through the one live region every live output shares (`tui/live_region.py`'s `LiveRegion`, given a small
`Display` of its own, as the status engine gives it one: a lock, `display()` and the two prompt methods, which refuse,
since a run's questions are asked by a module of their own that steps the region aside): redrawn after each event and
ten times a second for the spinner, the terminal's title saying where the run is, the echo off while it draws and at a
Ctrl-C what the terminal has not read yet kept (`NOFLSH`), where it would throw away a line said above or part of a
frame (keys typed unseen before it are thrown away all the same, by the ladder's Ctrl-C), and for a question the
terminal's settings put back exactly as found; at the run's end `NOFLSH` alone kept on until its summary and NEXT STEPS
are said, and then the settings exactly as found (`LiveRegion.finish(kept=True)`, then `release()`), so that a Ctrl-C
the terminal has not read them by throws none of them away. Each change of the settings is made only while the run is
the terminal's foreground, SIGTTOU held off (`live_region.change` and `put_back`), so that a run a shell put in the
background leaves the shell's settings and is not stopped again by one; and each change back with the run's signals
held off till it is made (`live_region.uninterrupted`), so that a second Ctrl-C, a SIGTERM, a SIGHUP, a Ctrl-Z or a
`Ctrl-\` never leaves it half made. A Ctrl-Z while the region draws (SIGTSTP, at its default) is taken by the
terminal's hold (`live_region`'s `_Hold`): the region erased and none drawn, the settings put back as found and what was
typed unseen thrown away with them (`TCSAFLUSH`), and then the stop; once the run goes on (SIGCONT, a shell's `fg`) the
mode made again and the region drawn below the shell's lines. A `Ctrl-\` while it draws is the ladder's, as SIGTERM;
where its default is in place instead, the hold does the same as at a Ctrl-Z before the quit. A Ctrl-Z or a `Ctrl-\`
held off while the settings go back is raised at its default once they are back, what was typed unseen thrown away
first (`uninterrupted`), as the region's `NOFLSH` kept it from the terminal's own key. What is
said above (`View.above`: each report whole, each failure) printed above the region after each event; and while it
draws, standard error said above the region too, a whole line at a time, so that the ladder's `stopping` line at a
Ctrl-C, or a warning, never lands inside the region. Where the stream is no terminal, what is said above and a line as
each step and stage ends (`View.ended_lines`). With any `-v` (`RunWords.verbosity`), each event as one line, its kind,
machine, text and data (`streamed`), and nothing drawn, as today any `-v` gives the stream. At the run's own end (the
`end` event with no machine, which `attached.py` emits last, after the terminal is put back) the region goes and the
echo comes back, standard error is the run's own again, and the summary is printed with NEXT STEPS after it, whole, in
the `#` block every run ends with (`text.banner`, in the palette's yellow); with `-v`, NEXT STEPS alone. A wait for you
that begins rings the bell, and so does the end. A person's stop (the run's `Context.cancelled`, which the first Ctrl-C
sets and no event carries) reaches the model at the next event or frame (`Run.cancel`), so the footer says the run is
stopping. An event the model refuses (an undeclared step, a malformed end) is said in one line on standard error and
left out: the run goes on, and every listener after the view still hears it. That line and each line under `-v` say a
machine by its name, as the frame does (the model's name for its key, a join's by the stand-in it joins, else the
`name` the event carries), never by its key, a stand-in or an identity, but where no name is known; a join's text, the
stand-in, is never said. Nothing in `runs/` writes an escape
sequence or reads the terminal itself: the region, the palette, the title and the echo are `tui/`'s. The model's
machines are the job's own, `job.json`'s `machines`, by identity, the key every event of a machine names, each with the
name a person chose where the caller gives it (`names`): the record's `machines` keeps identities alone, with their
names in its `names`, and the view says a machine
by its name. A row is keyed by the machine's identity, or, for a VM the run is making, by its stand-in key until its
join: drawn by its name from its first event (from `job.json`'s `making` where the view is made after that event, and
from its `names` where it is made after the join), and
the join changes nothing drawn, one row before and after, the identity's events going on in it. A job has one view: a
second is refused (`RuntimeError`), the first keeping it. What the view says in the run's thread, each event's lines and
standard error's lines above the region, is said whole, a Ctrl-C, a SIGTERM, a SIGHUP, a Ctrl-Z and a `Ctrl-\` held
off meanwhile and each delivered once it is said (`live_region.uninterrupted`, which also holds each of the region's
writes and its count of
the lines drawn): the ladder raises from its handler wherever the run's thread is, and a frame drawn but not counted
would be left above the summary, an erase not counted would wipe lines said above it, and
a line counted as said but not written would be lost. Once a write to the stream fails nothing more is written there,
and a stream whose pipe is broken (`aw … | head -1`) is pointed at `/dev/null`, so that Python's flush at the exit
succeeds and the run ends with its own status. A question at the run's terminal finds the run's view by the job its
`Context` carries, `Live.of(context.job)` (None for a run with no view), and asks inside `with view.aside():`, which
steps the region aside as it does for a prompt: erased and paused, the echo on; after, the echo off again and the region
drawn once the run has worked half a second more. `tests/unit/test_aw_live.py` holds it at a pseudo-terminal, a run made
by `runs.run` in a child process emitting a canned run: each redraw one write, no line wrapped at 80 or 100 columns, the
echo off while it draws and back after a return, a Ctrl-C, a SIGTERM and a SIGHUP, the terminal's settings then those it
had before the run, a report and a failure said above and left there, the stopping line above the region and the footer
saying so, the summary and then NEXT STEPS once; 300 reports said above with nobody reading and a Ctrl-C typed before
the terminal has read any, each still on the screen; a SIGTERM or a SIGHUP at a random moment while events flow, no
frame's remains left and no line said above lost; a
question asked aside; piped, a line as each step ends, and into a reader that leaves after the first line, the run's
own status; with `-v`, each event a line; in its own process, with a clock of its own, that what a run says off a
terminal is exactly what the view gives after each event, a refused event said once with the run going on, a person's
stop reaching the model, what an event says and standard error's lines said whole with a signal meanwhile delivered
after, a second view refused, a stream gone written no more, and nothing said after the end; and by a scan, that no
module of `runs/` names an escape sequence or `/dev/tty`.

**A question: `ask.py`.** `await ask(context, question, default, choices, hidden, machine=...)` asks the person at the
run's terminal, from a verb's body on the run's loop. Every question has a default, the safe answer it takes where it is
not answered, and one without cannot be built (`Question` refuses it, with whatever else is malformed: no words, a
hidden question with choices, a default that is none of the choices, a hidden answer's default that is no `Secret`). It
gives back the default itself where nothing was answered (Enter, the end of the input, Esc at the menu, no terminal),
and otherwise the line typed, the value of the choice picked, or for a hidden answer a `Secret`. It asks only where the
standard input the command was given is a terminal whose foreground the run is (`terminal()`, which a verb that asks,
`init`, decides by too before its run draws anything): anything else (`/dev/null`, a pipe, a file, a run sent to the
background, a detached run, an agent's job) is nobody to ask, and the default is taken, with one line on standard error
that says the question was not asked and what it took; `--attended` changes nothing here.
At the terminal, one question at a time (one asked while another is up, by two machines' work at once, waits till that
one has ended), the live view steps aside (`Live.aside`, below), a `waiting` event says what the run waits for, the
keys typed before the question was there are flushed, the question is written to standard output and the answer read,
every read through `Context.typed`, on the loop and never in a thread, so one Ctrl-C there ends the run; never from the
process's controlling terminal opened by its name, and never through `getpass`. Then an empty `waiting` event ends that
wait alone, and a `step` event, a line of the machine's work, says what was answered: the line, the choice's words, or
of a hidden answer only that one was given. A line is read in the terminal's cooked mode with its echo on, and at a
Ctrl-C keeps what the terminal has not read yet (`NOFLSH`), where the terminal would throw away the echo of what was
typed and leave the question's line saying `^C` where `192^C` was typed; a hidden answer the same with the echo off and
without `NOFLSH`: with nothing typed on the screen to keep, each signal key throws away what was typed, as it would;
either question's line is ended wherever the person's Enter did not end it on the
screen, however its read ends (a hidden answer, the end of the input, a Ctrl-C, a SIGTERM), so that what the view kept
while it was up is said from a line of its own; a choice is `tui/choose.py`'s menu where the terminal can draw it
(`choose.can_choose`), picked with the arrow keys through `choose.asked`, the menu's keys read as a read on the loop
gives them (`choose.split` cuts them as `choose.ask` reads them one at a time), its keys raw, where Ctrl-C is a byte:
the terminal's settings, its signal keys among them, are put back first and the run is then sent SIGINT, as for a
Ctrl-C anywhere else; where the menu cannot be drawn, the choice is typed, a number, a value or its words, asked once
more where it is none of them, and left at its default after a second. A choice's words and its note are one line of
plain text (`Choice`, through `text.one_line`), as the question's own words are: a node, a storage, a bridge or a
machine's name comes from Proxmox or a machine and may hold an escape sequence or a newline, which the menu would
otherwise write to the terminal as it is; the value is given back as it was given. The terminal's settings are put back
as found once each question ends, however it ends, and each change of them, the question's mode and the change back, is
`TCSAFLUSH`'s, as `getpass` makes it: the keys typed before are flushed by the change that sets the mode, never one
typed between a flush and the change, and what was typed at the question and not read (the rest of a paste, the line
after a token, the part of a line a Ctrl-C kept) is discarded by the change back, never left in the terminal's input for
the shell to run once the run has ended. The question's line is ended before the change back, in the question's mode,
whose `NOFLSH` keeps it at a Ctrl-C, and the menu's words above it are written once its raw mode is set, where a Ctrl-C
is a key (`choose.asked`'s `above`); the change back is made with the run's signals held off till it is made
(`live_region.put_back`, inside `uninterrupted`), so that a second Ctrl-C, a SIGTERM, a SIGHUP, a Ctrl-Z or a `Ctrl-\`
ends or stops the run once it is made, never half way, and only while the run is the terminal's foreground, SIGTTOU held
off, while the question's mode
is made at once, where a run in the background waits for the terminal (SIGTTOU), as a question needs it. A run the
ladder stopped puts the settings back once more at its end, what is unread discarded. A Ctrl-Z at a question (SIGTSTP,
at its default) is taken by the terminal's hold as the region's is: the settings put back as found and what was typed
and not read thrown away with them (`TCSAFLUSH`), and then the stop, so that nothing typed reaches the shell's line or
its history; once the run goes on in the foreground (`fg`) the question's mode is made again and its words written again
below the shell's lines, where the answer is typed afresh (`change`'s `back`); a run the shell goes on with in the
background (`bg`) stops at the question till it is the foreground: once it goes on, the hold's own Ctrl-Z raises
SIGTTOU at its default from Python, in a loop (`_once_foreground`), and never leaves the stop to the terminal inside the
change, whose stop the kernel would make again at each SIGCONT without coming back to Python where another thread took
the signals; before each stop it waits a moment (`ENDING_GRACE`), for a kill another of the run's threads took to reach
the ladder, and looks again whether the run is the foreground. A run whose parent is not in its session, its group maybe
orphaned with its shell gone, is never stopped there, since Linux throws that stop away and the question would loop for
good: the change fails with `EIO`, as the kernel's own change fails there. The SIGCONT's own handler never stops the
run: Python runs a handler still due at the first line of another's, so a kill's SIGCONT can come there before its
SIGTERM's handler has done anything. A run the shell ends while it is stopped, at a Ctrl-Z or after `bg` (`kill %1`:
SIGTERM, then SIGCONT), ends at once: the kill's handler raises the end through the stop, and the ladder's end tells the
hold it waits for the terminal no more (`live_region.ending`), so that an end that raises nothing, the run already
closing, stops the question no more either. A `Ctrl-\` is the ladder's, as SIGTERM, what was typed thrown away first.
The edges: at the menu, whose raw mode makes them keys, a
Ctrl-Z and a
`Ctrl-\` are keys it has no use for, and the menu goes on; a key between two questions, or while the region is aside
and no question is up yet, is the terminal's own. A change that a signal interrupts is made again
(`live_region.set_terminal`, which the region's own switch
of the echo uses too): `TCSAFLUSH` and `TCSADRAIN` wait for the output to drain, and a signal whose handler raises
nothing, the run's first Ctrl-C or a resize, ends that wait with `EINTR`.

The live view steps aside for a question as the view's paragraph above says, `Live.of(context.job)` and `with
view.aside():` (`LiveRegion.aside`: erased and paused, the echo on; after, the echo off and nothing drawn for half a
second, `live_region.SETTLE_SECONDS`, so questions one after another show no frame between them). Beside the region,
`aside` keeps what the view would say meanwhile - its lines above the region, the lines of standard error it says above
the region (a line of the ladder's, a warning), and the line of an event it refuses - each with the stream it goes to,
and says them once the question ends, however it ends, in the order they came (and at the run's end, before the
summary, where a second Ctrl-C ends the run with the question still up), so that nothing lands inside the question;
while it is up the terminal's title says the run waits for you, and the wait it opens rings the bell only where the
person has had nothing to answer for `QUIET_SECONDS` (ten seconds, since the view began, the last bell or the last
answer), as today's prompt rings it.
`tests/unit/test_aw_ask.py` holds it at a pseudo-terminal, a run made by `runs.run` in a child process asking through a
live view: a yes or no picked with the arrow keys, a choice typed and picked at a terminal that cannot draw the menu, a
choice whose words and note hold an OSC, an erase of the screen, a colour and a newline drawn as plain text (the
screen read back with `tests/unit/vt.py`), two questions asked at once from two tasks, the second drawn only once the
first is answered, a report, a line on standard error and an event the view refuses, each said while a question is up,
shown after it and never on its line, and so when a Ctrl-C or a SIGTERM cuts the question short, the question's line
ended where it was cut and what was typed on it kept there (`192^C`, the keys and the Ctrl-C in one write too) and never
left in the terminal's input, however a run ends the terminal's settings those it had before the run; from an
interactive shell (`harness._in_a_shell`), a line and a hidden answer half typed and then a Ctrl-Z (`fg` bringing the
question back in its mode, its words written again), a `Ctrl-\` or a Ctrl-C, what was typed never on the shell's line
or in its history, and a Ctrl-Z there and then `kill %1` or `kill -HUP %1`, or `bg` and then `kill %1`, the run
ended at once, of the signal, and its lock free for the next; a line, a hidden answer whose characters reach
neither the screen, the record nor
the run's events, two lines pasted at a line and at a hidden answer, the second never left in the terminal's input once
the run has ended, the region gone while a question is up and back after it, Enter taking the default, Ctrl-C at a
line, a hidden answer and the menu ending the run, keys typed before the question not taken as its answer, the run's
summary and 150 lines of NEXT STEPS said with nobody reading and then keys typed unseen and a Ctrl-C, every line on the
screen and the keys never left for the shell, and from an interactive shell keys typed unseen there and a Ctrl-Z in
place of the Ctrl-C, never on the shell's line or in its history, and keys typed while a stopped run winds down never
left either; with
standard input on `/dev/null`, the default at once and the line that says so; and in its own process, every refusal of
`Question`, a question with no default among them, a choice's words and note made plain, two questions asked at once
taking turns, how the menu's keys are cut, what is typed at the menu after its answer never left, a question and the
menu putting the terminal back through an `EINTR`, each change `TCSAFLUSH`'s, a line's mode with `NOFLSH` and a hidden
answer's without, and the settings put back exactly as found, a question's own words written while its mode is up, a
signal landing as a
question ends delivered once its line is ended and the settings are back, a question a shell puts in the background
making no change of the settings there, what the view says kept while a question
is up, a refused event's line among it, or said before the summary where the run's end cuts the question short, and the
bell rung only after a quiet while. `tests/unit/test_live_region.py` holds the region's own switch of the echo to the
same through an `EINTR`, `NOFLSH` beside the echo while it draws and the settings back exactly as found, `NOFLSH` kept
at a run's end till `release()`, a Ctrl-C throwing away what was typed unseen and handing the signal on, no change and
no settings read in the background, a question going on in the background letting an end another thread took land
before its change and stopped there by the hold's Ctrl-Z alone, never by the SIGCONT's handler, stopping no more once
the end came at its first stop, never stopped where its parent is not in its session (`EIO`) nor once it is the
foreground after the wait, a signal landing as the settings go back delivered once they are, a Ctrl-Z coming back to
them, a Ctrl-Z and a `Ctrl-\` at their default taken while it draws - the region erased, the settings put back
as found and what was typed unseen thrown away before the stop or the quit, the mode again after a stop - a handler of
the program's own left in place, the handlers the hold took put back where it ends with the signals held off (a
question's end), and from an interactive shell, nothing typed unseen left on its line or in its history at either, and
300 lines said above the region with nobody reading and then a Ctrl-C, none of them lost;
`tests/unit/test_aw_attached.py` a run started in the background reading no settings to put back, a `Ctrl-\` ending
the run as a SIGTERM does, the tree ended with SIGTERM and the run dying of SIGQUIT, and an end that raises nothing, the
run already closing, still telling the terminal's hold it waits no more. In every in-process test on
Linux, a draining change of a pseudo-terminal's settings made with output unread at its master and nothing reading it
fails the test (`tests/unit/conftest.py`'s `drainwatch`), as macOS would wait there for good, an end of input typed in
a canonical mode echoed as the `^D` macOS echoes for it and Linux does not; off Linux it notes nothing, since
`FIONREAD` on a master there counts what its slave has not read, never the slave's output.

## 16.9 `fleet/` — the fleet's files, the settings, the secrets and the fleet's mark

`lib/claude_vm/fleet/` is where a fleet's files and its state go. `paths.py`, below, says where they are; the hosts as
folders, the typed table of settings and its overrides, the one encrypted file of secrets, the fleet's ID and mark, and
a host opened from them go here beside it. It imports `schema/`, `pve/` for the client a host is opened with, and
nothing of `engine/`, `cli/` or `control/`, and no pydantic.

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
is refused, one that names its own folder is none, and under `AGENT_WAREHOUSE_HOME` no pointer is read. The returned
home names the `client.yml` it followed, so a changing run can say which fleet it drives. The value is one absolute
path, plain or quoted, or `~/`; anything else YAML would read as more than a string is refused, and so is a form YAML
would read as a pointer that the line does not say: the value on a line after the key, and a flow mapping that names
it. `pointer_in` reads that line from a file's text, for `overrides.py`'s reader of `client.yml`, which is held to it.

**The state and the key go with the home.** Whichever way the installed program's home is reached, by the variable or
by a pointer, and however its path is spelled, its state folder and its key file are the installed program's own: a
home is told to be that one by the file system's identity of both, never by their text (`inside`: each made a real
path, every link read, then the deepest part of the one that exists found among the other's parts by
`os.path.samefile`, and the names below it, not made yet, compared as text), so a `~/.config` that is a link into a
dotfiles folder changes nothing, and neither does a home spelled in another case on a disk that ignores case, as
macOS's does by default. Two programs that drive one fleet share its jobs and its locks and
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
for a module's run, and `harness.PASSED_ON` passes it to every run. It is never session-scoped: a session-scoped
autouse fixture would join every test into one xdist group. The session reads the names `paths` owns in the
developer's fleet folders before the first test and after the last, from their metadata alone, and fails if one was
made, changed or removed. Those folders (`developers_folders`) are the installed program's three, the checkout's own
`.aw/` and, where the suite runs from a worktree, the main worktree's (git's common directory), and the
`AGENT_WAREHOUSE_HOME` the developer exported, read before the session replaces it. In a checkout's `.aw/` only
`client.yml`, `fleet/` and the key are watched (`CHECKOUT`), never `state/`: the developer's own `aw` run from that checkout writes
its jobs there while a suite runs, and state can be made again. No file there is opened, so no `fleet_home` is
followed. `tests/unit/test_aw_fleet_paths.py` holds the homes, the pointer, the key's place, the names,
the scans for who names the folders and the key, and the guard, a child `pytest` among its tests.

**The settings table: `registry.py`.** One typed row for every setting a person may write, written by hand as literals,
standard library only: `ROWS`, the 93 settings today's `local.yml.example` documents that the engine keeps as settings
(not its five older names - the four 1.0 names of the editor's settings, and `tailscale_enabled`, the old form of
`vpn` - nor the API's port, the token's user and its ID, which are fields of each host's `host.yml`, nor whether the
API's certificate is checked, which it always is); `ADDED`, counted apart, the settings the engine adds: the vault's key
file and command, `tls_ca_file`, `network_proxy`, `run_lock`, `run_forks`, `run_detached_limit`, `machine_claims`,
`agents_vm_cap`, `proxmox_audit_scope`, `vm_label`, `vm_first_boot_upgrade`, `destroy_confirm`,
`destroy_allow_unrecorded`, `github_username`, and two for one run alone; and `CONNECTION`, counted apart, this
computer's own SSH that today's guides document outside the example (`ssh_identity_file`, `ssh_executable`,
`vm_ssh_options`). A `Row` is the setting's name; its type (`TYPES`: `bool`, `int`, `str`, `path`, `list[str]`, `dict`,
`size`, `text`; a `text`, no row's but a role's option of several lines alone (`passthrough.py`'s `TEXT`, below), a
string that may hold line breaks; a `size`, `vm_disk_size`'s alone, a whole number with G or T after it, in either case, as `deploy --disk` takes
one, and kept with its unit capital; a `path`, of which there are seven, is a path on this computer, and `overrides.py`
reads one, typed or in a file, with `~` at its start the user's home, refuses one still relative, `~user` among them, by
the setting's name, and keeps `""` as unset, never the working folder) and choices, the argument spec's of the role
variable it feeds, or for one that feeds none its literal default's type (`path` for the three of today's whose literal
is `""` that the cutover's check names with why) and the choices today's checks hold it to; its default, a literal, or
one of the module's named functions (`COMPUTED`) where it is worked out from something else, whose keyword-only
parameters name what it reads, each a row or one of `GIVEN`, the machine's name, this computer's user home and the key
file `paths.py` names; its scope (`SCOPES`: `global`, `host`, `machine`); its home (`HOMES`: the fleet's `settings.yml`,
which holds a row of scope `machine` in its map `machines`, a host's, `client.yml`, which holds a row of scope `host` in
its map `hosts` and one of scope `machine` in its map `machines`, or `run`, no file, a value given with `--set` for one
run alone); its group, the role today's `just settings` lists it with, `common` for a setting that reaches no role's
variable; its description, today's comment above it for today's settings and written here for the added ones, which
names a verb alone and says nothing of an older version; `secret`, whether its value may hold a secret
(`claude_code_settings`, whose role marks it `no_log`); `agent`, whether an agent may ask to change it, the 36 the
agents' server allows today; `values`, a `ValueRule` for what an agent may give a setting whose type lets more through
(`claude_code_version`: a channel or a release, never another spec for npm); and, where its readers need more than its
type, `form`, a `ValueRule` every value of a string must match (`vm_label` one Proxmox tag, which a `,` or a `;` would
make two, held equal to `pve/client.py`'s `TAG`; `github_username` a GitHub account's name, as `init --github-user`
takes one; `proxmox_pool` a pool's name as Proxmox makes one, or none, by `POOL_NAME`, the one rule of a pool's name,
which `engine/access.py` reads), or `least`, the least an integer may be (`run_forks` 1). `run_lock`'s choices are
`runs/locks.py`'s `POLICIES`, and `network_proxy` is held by `vendors/proxy.py`'s one check of it (§16.7). A row that
names a secret and that an agent may change, that has a rule for an agent's values and that no agent may change, or that
has a form or a least value its type has not, raises as it is made. A setting each host sets for itself today, or that
the shared files may never supply, is each host's own, and so is `vm_searchdomain`, one network's as the nameservers
are; of them `claude_ssh_host_domain` and `vm_ssh_route`, how this computer finds and reaches a host's VMs, are this
computer's, in `client.yml`'s map `hosts`, so that another computer that copies the fleet's folder reaches them its own
way. A setting whose default is made from the machine's name is each machine's own. A list's default is a tuple and a
mapping's read-only, so no reader changes the table: `plain(default)` is the copy a reader hands on or prints, and a row
is compared by identity alone, so that one whose default is a mapping can be hashed. A description names no Ansible,
playbook, today's `local.yml` or `hosts.yml`, nor a vault variable: a name with `vault_` before it is a row's. `rows()`,
`row(name)` and `group(name)` read it. Nothing generates
it, and nothing is generated from it into a tracked file. `tests/unit/test_aw_fleet_registry.py` holds the table, with
the place of each of today's settings in two literal sets (this computer's, by scope, and each host's, by home), every
other one the fleet's; `tests/unit/test_aw_fleet_registry_today.py`, the cutover's check, which goes with today's files,
holds it to them: every documented setting has exactly one row but the nine, each named, and the four that left with
where each went, each type, choice, literal default and group as today's, a secret where the role variable is `no_log`,
the rows an agent may change today's `ALLOWED` with `VALUES`' rule, every name of `ALLOWED` and `REFUSED` a row but the
nine, `run_forks` as many as `ansible.cfg`'s `forks`, and a setting added to the example without a row fails by its
name.

**The overrides: `overrides.py`.** What a person sets is kept in plain files of literals, written by hand or by the
engine, never sealed, each a YAML mapping of setting names to values and nothing else: no expression, no anchor, alias,
merge or key given twice, no line break inside a value but a `text`'s. Each row is read from the file of its home
alone: the fleet's `fleet/settings.yml` (a row of scope `machine` in its map `machines`, by `<host>/<machine>`, never at
its top level, which would give every machine one value), each host's `fleet/hosts/<name>/settings.yml`, which no other
host reads, and this computer's `client.yml` (a row of scope `host` in its map `hosts`, by the host's name, never at its
top level; `vm_ssh_options` at its top level for every machine and in its map `machines` for one, which outranks it). A
setting in a file that is not its home is refused by name, with its line and where it belongs; a row of home `run` is in no file.
`client.yml` also keeps `fleet_home`, read by YAML and held equal to what `paths.pointer_in` reads, so the file and the
fleet's home never read it apart, and `current_host`, a host's name, which `set_current` alone writes; every other write
keeps both as they are. `read(home, host)`
returns the three files of a host's run (`Layers`, each a `File` with its digest); `resolve(home, host, run)` every
row's `Value` with the layer that set it (`LAYERS`: `default`, `fleet`, `host`, `client`, `run`): the value given for
the run, else its home's file's, else the table's default, a default worked out from another row, the machine's name
(none with no machine named), this computer's home or the home's key file worked out there. `typed(row, text)` holds
typed text to its row's type, choices, form and least value, a path by the path rule and `network_proxy` by
`vendors/proxy.py`'s one check, which refuses a user and a password, refusing an expression and a control character in
the text and again in the value YAML's escapes make of it; a value in a file is held the same way, so
`vm_disk_size: 128g` in a file, `--set vm_disk_size=128g` and `128G` typed are one value. A `text` is never typed:
`typed`, `--set` and a value given for one run refuse it, naming the fleet's `settings.yml`, where a person writes it as
YAML's block (`name: |`, then its lines indented) and where it holds line breaks, no other control character and no
expression. `run_values(texts, secrets=...)` types the words of `--set`: a name that is no row's is refused unsaid, a row that may
hold a secret is never given for one run, and a value that holds one of the vault's secrets (every caller names them,
each a `Secret`, struck and never revealed) is refused in the text and in the value; the result is what a run records as
its inputs and `resolve`'s top layer. `set` and `unset` write one value into the file of its home, under `store.locked`,
only while the file's digest is the one its caller read (`expect`), whole and in UTF-8 whatever the locale's
encoding, read back as meant before `store.replace` puts it in place, a new file at `paths.FILE_MODE` in folders made
through `files.py` at `paths.DIR_MODE`, a link where one goes refused. A write lays the whole file out anew, so it keeps
no comment a person wrote in it (the owner's answer of 2026-11-22). A scratch file a write killed part way left beside
the file, which would travel with `fleet/`, is taken out by the next write of that file, under its lock, before it
reads (`files.sweep`), by the name `mkstemp` gives a scratch alone, so that a person's own `.settings.yml.<x>.tmp`
stays; a scratch stays until that file's next write. A file is opened once, with no link followed and no wait on a pipe, and read only where it is a
regular file: a link or a FIFO there is refused by its path. A file's integer is YAML 1.2's, its digits in decimal, as typed text's
is (`WHOLE`): `0777` in a file is 777, as typed, where YAML 1.1 reads 511, and `0x10`, `1_000` or `1:30` is text,
refused for a row of type `int` by its line; `set_current` writes
`client.yml`'s `current_host` the same way. `literals(path)` is the one reader of a file of literals the fleet keeps, a
host's `host.yml` too. Every refusal is a `Refused`, whose sentence never says a value back. Nothing else in `fleet/`
writes a file but `files.py`, for `fleet.json` and `vault.age`, and `hosts.py`'s `write`, a host's `host.yml` and
anchor. A machine's recorded choices,
the design's third layer, are in its record, which is not read here. `tests/unit/test_aw_fleet_overrides.py` holds it:
each layer over the one before, each home's file and none other, the path rule for each path row, the proxy's user and
password refused at every way in by one check, each refusal with the file unchanged, the compare-and-swap, `--set`'s
refusals with no value said, a size read alike, the reader of `client.yml` against `paths.py`'s own cases of
`fleet_home`, and a scan that only `set`, `unset` and `set_current` write, and `files.py`'s `write` and `hosts.py`'s
`write`.

**The role options passed through: `passthrough.py`.** Every typed option of the roles that no row names - neither a
row's name nor reached by a row's setting through today's wiring (`wiring.py`: `vm_cores` reaches `proxmox_vm_cores`) -
may be set under the role's own name, as today's settings verb lets a person set it, held to its role's own
`meta/argument_specs.yml` and handed to the role variable of the same name unchanged: no row is made for it and no fate
entered by hand, so the spec is the one source of its type, choices and description. `options()` lists each, with its
role, the entry point that first lists it (main first), the spec's type, choices, list elements, `no_log` and
description, and `refused`: none for one passed through, else why it is no setting, as today's `tools/settings` refuses
it, in the words a refusal says after its name. The vault's is one the spec marks `no_log` or that a name the vault
holds reaches, so no secret is ever passed through; each machine's own is one made from the machine's name; a run's own
is one a run sets for itself (`BY_A_RUN`); and `LEFT_OUT` names twenty-three with their reasons, each held elsewhere: the
host's connection, a field of each host's `host.yml` or the anchor beside it, and the certificate's switch the client
has not (the four options today's connection settings fed, the address and the anchor's path); the client's own bound
on a call; the ownership tags, since a VM is the fleet's by its pool and its record; the two the run sets from `vpn` and
the one that names it in the VPN's messages; the template a run chooses and the golden's build VM a role sets for
itself; a folder of this computer's; each machine's own repositories and whether its Tailscale had a login, which its
record keeps; the three lines that name a machine's host and the two routes to it, the run's; and one a row of another
name stands for (`destroy_allow_unrecorded`). `options()` lists too each option a row's setting reaches, refused as set
through that row alone, with the row's name (`through`), and the one whose spec takes any value, which has no type: so a
role's every option is there for the settings verb to list beside its rows. `refusal(name)` says the same why by name,
`default(option)` gives the role's own default, which applies where an option is set nowhere, and `described()` each
role's short description. Of today's 203 options, 71 are passed through.
The two whose value is several lines by what it is (`TEXT`: `proxmox_template_image_key`, an armored key, and
`claude_code_global_instructions`, a whole `CLAUDE.md`) are each read by a row of type `text`.
`overrides.py` takes the name of one passed through where it takes a row's, at home `fleet` and scope `global`: at the
top of the fleet's `settings.yml`, and with `--set`, by `passthrough.row(name)`, its spec's type with no default; a path
there is one on the machine the role runs on, held as one line of text and kept as typed, never made this computer's.
`resolve` returns, after the rows, each such option the fleet's file or the run sets, and none set nowhere, since the
role's own default applies where it runs; any other option of the roles is refused by its name and why, in a file and
for one run, never saying the value. `typed(option, text)` holds typed text to the spec as `overrides.typed` holds text
to a row. A kind of option no row's type holds (a list of anything but strings, choices of anything but a string) is
refused as the table is made, before it could be passed through. `tests/unit/test_aw_fleet_passthrough.py` holds it:
every option a row's, passed through or refused, none left out; each passed through carrying its spec; no secret passed
through, nor an option that `defaults.yml`, a role or a play sets for itself, by each road a scan reads: of every folder
under `roles/`, with an `argument_specs.yml` or none, its task files and handlers, `.yaml` too, and every file of its
`vars/` folder, and of every play, its tasks and keywords, that reads a `set_fact` by any of its names, given its names
directly (a templated name as every name it can make), through `action:` or `local_action:` (text split at its first
whitespace of any kind, or a mapping's `module` split the same way, beside the rest of the mapping and its own `args:`),
`args:` or a `module_defaults` (on the play, a role entry, a block or a task), a `register`, a `vars:`, a play's
`vars_files` and `vars_prompt`, and a role entry's parameters; and that finds no road whose names it cannot read - an
`include_vars`, a template in their place, a free-form name of no variable's shape - no `include_tasks`, `import_tasks`,
`import_playbook`, `include_role` or `import_role` of a file or a role it does not read (outside a role's `tasks/`,
`handlers/` or `vars/` and the plays, a hidden file or one of another suffix, a role outside `roles/`), no role's
`dependencies`, and no `module_defaults` group that holds a `set_fact` (a loop's variable, which outranks an extra
variable, is not read);
each refusal's reason; a value held to its spec typed and in a file; each `text` read back whole from the fleet's file,
the vendored key among them, a control character but the line's end and an expression refused by its line, and neither
typed; and, against today's `tools/settings` run
as a child over an inventory made from the example files, every name it lets a person set taken here as a row or an
option, but the nine of today's settings with no row and the options left out by name, which the test names; every
option it writes through another setting refused here through that setting's row; and every name it refuses no option
here, refused for today's reason, role by role.

**The hosts: `hosts.py`.** Each host is a folder of the fleet's files, `fleet/hosts/<name>/`, the first included, named
as any host is (`hosts/model.py`'s `name_refusal`): `host.yml`, its own fields, which travel with the folder;
`settings.yml`, its overrides; and, where it keeps one, `proxmox-trust.pem`, the anchor its API's certificate is
verified against. `host.yml`'s fields are `FIELDS`, each held to what it takes: `address`, one word that starts with a
letter or a digit; `api_port` (8006); the token's `token_user`, `user@realm`, and `token_id`, the three it must give;
`ssh_user` (`root`) and `ssh_port` (22), which root SSH and the hop use, and `jump`, the account the hop logs in as
where it is another; `trust_kind`, `node` or `ca`, beside the anchor and only there, with the `fingerprint` recorded for
it; and `made`, what this fleet made on the host, each by what it is, its ID and who made it (`engine` or
`administrator`), which a cleanup removes and nothing else. No field is one computer's: a key's path is the row
`ssh_identity_file` in `client.yml`'s map `hosts`, and a setting given in `host.yml` is refused, saying where it is
kept. `Layout(home)` is a second layout beside today's `hosts/layout.py`, with every method and attribute today's core
uses of one, so `hosts/choose.py` and `hosts/model.py` read it as they stand: `hosts_rel` is `fleet/hosts`; a `Host`'s
`local` is its resolved settings of scope `host`, from its own `settings.yml` and `client.yml`'s map for it over the
table's defaults, which `defaults()` gives; its `first` marks the fleet's one host. The current host is `client.yml`'s
`current_host`, this computer's own; with one host and none current a run reaches it, and with several and none current
a run that names none is refused, since which one is this computer's to choose. A VM named is reached on the host its
alias records, `# origin:` in the managed SSH config under this computer's home, as today, with `claude_ssh_config` off
recording none. `reach(layout, request)` decides a run's host by today's rules (`choose.choose`), then keeps the
refusals that still have a meaning: a host whose files cannot be used, and one whose `settings.yml` does not set each of
`FLEET_KINDS`' `always` (`hosts/kinds.py`'s second table: the node, the storage and the bridge, which `init` writes;
the template's ID has the table's default, and a host may set its own), taken all the same by a lenient request
(`settings`, `vault`) that may put it right; a setting the fleet's file may not supply is refused by the overrides'
reader there. It returns the host, its fields and anchor, and with several hosts the others' names and
addresses a run keeps apart from its machines. What a host's files say is that host's problem, said in its place, and a
run against another goes on. `make_current` writes `current_host` through `overrides.set_current`, held to a host of the
fleet that can be used; `init` writes it there too, for the host it sets up where none is current. `write(layout, name, expect, change=..., anchor=...)` is `host.yml`'s one writer, and the
anchor's: a compare-and-swap on the digest of what `Layout.state` read (the host's `host.yml` and its anchor, read under
the lock a write takes), under `store.locked` on the host's folder, every field held to what the reader takes before
anything is written, then read back through `fields` and `anchor`; never through a link, its folders made and held
through `files.py`, which refuses a link where the fleet's, its hosts' or the host's folder goes. `change` edits any
field but `trust_kind` and `fingerprint`, which go with the anchor alone: an anchor's file is PEM certificates and
nothing else, at mode 0644. Its steps are planned in `_commit` and each is on disk before the next, in an order a run
stopped after any step leaves readable: a first anchor is staged beside the host (`.proxmox-trust.pem.new`), then
`host.yml` written, then the anchor put in place, so that where `host.yml` gives a kind and no anchor is in its place,
the staged one is it, and where it gives none, the staged one is no anchor; one taken out goes the other way; and one
put in the place of another leaves the old beside the new kind or the new beside the old, the fingerprint the new one
records written first and the one it drops last, each holding the host to no more than one of the two. The next write
puts right what a stopped one left. A `host.yml` that gives a kind beside no anchor and none staged, whose anchor a hand
took out, every reader refuses, naming `init` as the way back; `Layout.state` gives it as a host that keeps none, so
that a write that keeps an anchor, or none, mends it, and a write that leaves the anchor as it is is refused.
`tests/unit/test_aw_fleet_host_writer.py` holds it, a run stopped after each step of each kind of change among its
cases. A `Host`'s `local_file`, `vault_file`, `hosts_file` and `sources` name today's
inventory and are read by no module of the new engine. `tests/unit/test_aw_fleet_hosts.py` holds it: today's cases of
the layout and the choice on the new folders, each field's rule with no value said, the anchor beside its kind, the one
writer of the current host, the literal list of `host.yml`'s fields, and the scan for today's files.

**The fleet's folders: `files.py`.** Every folder of the fleet's and of its state is made by `folder(path)`: where it is
missing, it and each missing folder above it at 0700, from the top down, the mode set and never left to the umask;
where it is there, it must be a folder itself, never a link to one (`Unsafe`, said with what to do), and it keeps its
mode, since the data folder already holds `tools/env-run`'s `checkouts/` at the umask's mode. A folder above the one
asked for that is there is the person's and taken as it is, a link among them (a `~/.config` kept in a dotfiles folder,
macOS's `/var`). `write(path, text)` writes a file whole through `store.replace`, its folder made by `folder`, a new
file at 0600, one that is there keeping its mode, and through a link the file it names replaced: a file is one a person
may keep elsewhere on purpose, a folder everything beneath it. `sweep(path)` takes out the scratch files
`store.replace` left beside a file where a write was killed between its scratch file and its rename, its caller holding
`store.locked(path)`. `fleet.json`, `vault.age` (`sealed.py`), the overrides'
folders (`overrides.py`), a host's folders (`hosts.py`'s `write`), the jobs folder (`runs/job.py`) and the locks
folder (`runs/locks.py`) are made through it, and nothing else of `fleet/` or `runs/` makes a folder but a job's own,
inside the jobs folder, and a run's short-lived directory (16.8).

**The fleet's ID and its record: `record.py`.** `fleet/fleet.json` is the computer's own record of a fleet:
`FleetRecord(format, fleet, stores)`, the format of the file itself, the fleet's ID and the format number of every
store (`STORES`: the vault, the settings, the hosts, and the jobs, whose `job.json` carries no number of its own).
`new_id()` makes a fleet's ID, a random UUID in its lowercase canonical form, held to `fleet_id` (16.2), the one
pattern the pool's mark and an adopted machine's marker share, and `first(fleet)` is the record a new fleet starts
with, at this program's formats (`KNOWN`). The record names no controller: the pool's mark says that, and nothing in the
base copies the mark into a record. `read(where)` returns the record of the fleet at a home, or none before `init`;
anything there that is no record is `Unreadable`, said by its path and what is wrong, never by what it holds, and a
`fleet.json` in a format newer than this program reads is `Newer`, refused by name. `refuse_newer(record, known)` is
the sentence an older program says for each store a caller reads whose number is above what it reads, naming the
store; a store a later release adds is read and kept, and this program reads nothing of it. `write(where, record)`
reads what is there under the lock on the folder first: it never writes over another fleet's record, since a fleet's ID
never changes, over one it cannot read or a newer one, or a record that would lower or drop a store's number, since no
program undoes the format a newer one wrote; so of two writers neither undoes the other.

**The pool's mark: `mark.py`.** The pool's comment carries one mark, `agent-warehouse fleet=<id> controller=none`: the
fleet's ID and whether the fleet has a control VM, `none` or its ID. It names the fleet and its mode, never a computer,
so a second computer that holds the fleet's folder is not refused; only an administrator of the host writes the comment,
and the caller reads it with one call of the Proxmox client. `parse(comment)` finds it wherever it stands, its three
words joined by one space with white space or the comment's ends around it, so a note of the person's own beside it,
on its line or another, changes nothing; a comment that begins a mark that does not read as one (an ID outside the
pattern, a field missing or out of place, a line broken inside it), or begins one twice, is `Unreadable`, never a pool
with no mark, and no sentence says a byte of the comment but an ID that matched its pattern. `render(mark, comment)`
puts the mark at the comment's end, after one space where it does not end in white space, since Proxmox's page edits
a pool's comment on one line, and keeps the rest byte for byte; it writes `controller=none` alone, and never over
another mark or one it cannot read. `may_change(mark, record)` is the rule a changing run reads before its first
change: nothing where the mark is the fleet's that `fleet.json` names, with no control VM; else one sentence, for a pool
with no mark, a mark of another fleet (naming both IDs), or a mark that names any control VM: "this fleet has a control
VM: change it there, or retire it", which the base never writes and always refuses.
`tests/unit/test_aw_fleet_mark.py` holds the three modules, with a scan that nothing else of `fleet/` or `runs/` makes
a folder, and that each imports the standard library alone.

**The vault: `secrets.py` and `sealed.py`.** The fleet's secrets are one file, `fleet/vault.age`: one JSON document,
`{"format": 1, "secrets": {"<entry>": "<value>"}}`, encrypted whole with age, armored, to the X25519 recipient of one
random key, through `pyrage`, which only the functions that need it import. A read decrypts the whole file and returns
the map, each value a `Secret`, and a write encrypts the whole map again, so no value can be read or swapped inside the
file without the key, or its public half; what that gives up, reading one secret without opening the others, a run that
holds the key for its whole life never needs. The key is handed in, a `Secret` that holds an age identity: `sealed.py`
never looks for it. age seals to the key's public half and says nothing of who sealed, so one who held that half and
could write `fleet/` could seal a vault of their own that opens with the key - a Tailscale auth key of theirs, which
every new VM would join with. The vault is left so, since one who can write `fleet/` can already change the fleet's
settings and its host's address, and the public half is never kept: `_seal` alone makes it, to hand it to age in the
one call that seals, and no module writes or prints it.
The names are a closed list, `secrets.ROWS`, each a `Row(name, scope, kind, description)`: each host's `token`, the
secret of the engine's one token there; `vm_password`, the VMs' user's, the console's at a create and sudo's where it
asks; an adopted machine's `become_password`; `anthropic_api_key`; and the optional `github_token`,
`tailscale_auth_key`, and the credential that deletes a VM's device at a destroy, `tailscale_api_token` or an OAuth
client's `tailscale_oauth_client_id` and `tailscale_oauth_client_secret`. No name is a setting's, and each is a secret
the program is built to read: a name comes into the list with what reads it. A secret of the fleet's is kept under its
name, a host's own as `<name>@<host>` and an adopted machine's as `<name>@<machine>` (`entry` makes the key, `parsed`
reads one back); a name outside the list is refused, by its name only where it has a name's shape, and no refusal says
a value. Each value is held to its row's kind (`KINDS`): a token's secret a UUID as Proxmox prints one, a password one
line with no control character, a vendor's token one word, and Tailscale's auth key as today's `init` holds one, never
an API token. `fleet.json`'s number for the vault is the one
`record.refuse_newer` reads, before anything is decrypted, and the vault's own `format` must equal it: a vault whose
number differs is refused by name. `create` writes an empty vault beside the fleet's record, once; `write` replaces the
map, only while the vault still holds `expect`, the map its caller read, compared under the lock, so a caller that read
before another's put is refused (`Changed`) and the put kept, as the overrides' writer is; `put` and `remove` change one
entry of the map they read under the lock. Each holds `store.locked` on the fleet's folder, the lock `fleet.json`'s
writer holds, across reading `fleet.json`, opening the vault with the key and replacing it through `files.write`, and
writes it at `fleet.json`'s number, so the two numbers are read and written under one lock; a key that no longer opens
the vault reads nothing and writes nothing, two writers lose neither change, and a writer killed part way leaves the old
file. The plaintext is never on disk, and `_plain` and `_identity` alone read a `Secret`'s value there (`REVEALS`).
`tests/unit/test_aw_fleet_sealed.py` holds it, with every file under a case's own directory read for every fake secret,
key and public half after the case, and a scan of every module of `lib/claude_vm/` and every program of `tools/` that
holds the public half to that one call.

**The vault's key: `vaultkey.py`.** The key is a random age key, one line in a file that holds it and nothing else, by
default `paths.key_file`: at the top of a checkout's `.aw/` or of a named home, or in the data folder for the installed
program. `make_key(path)` makes one with `pyrage` and writes it `O_EXCL`, never through a link, at 0600 whatever the
umask, its folder made at 0700 through `files.folder` where it is missing, and only where nothing is there: a key is
never written over, and nothing here removes, moves or rewrites one. A run finds it in one order, from the values its
caller resolved (`vault_key_file` and `vault_key_command`), so the module reads no file of settings: first
`AGENT_WAREHOUSE_VAULT_KEY_FILE`, the one override, for a script and the agents' server, the path of a file read and
never run, even where its mode lets it run, and never waited on (a pipe or a folder there is no file); then
`vault_key_command`, whose place this is and whose running is not built yet, so a run where it is set refuses rather
than read the file after it; then the file `vault_key_file` names, `""` the default. `ANSIBLE_VAULT_PASSWORD_FILE` is
never read and is no refusal. Whichever place it is, a key file whose mode gives the group or others any right to it,
to read, write or run (ssh's own 077), is refused before anything of it is read, as `ssh` refuses such a private key,
in one sentence that names its mode and says `chmod 600 <file>` (`Failed`): the key opens every secret of the fleet,
so it is taken only from a file this account alone can read, and one who could write it could put a key of theirs in
its place. `make_key` makes its file so; a key kept on a volume that shows every file as open to all cannot be used
from there. `source` returns the first place that exists; `open` returns the key once the fleet's
vault opens with it (with no vault yet, once the key file holds an age key: a new fleet's vault is sealed to its own key
file's key, so there the variable's file and a command are refused, nothing read or run, `NoVaultYet`), or one
sentence: nothing here opens the vault, with what would, by `vault_key_file` and never the variable - `init` where
there is no vault, the key copied from the computer that made the fleet, or its file named in `vault_key_file`, where
there is one (`NoKey`); the place failed, in the system's words made one line, or is a file open to others (`Failed`);
or the place answered with what does not open this vault: no age key, or another fleet's key or a vault changed since,
which age does not tell apart (`WrongKey`, from `sealed.py`'s `NotOpened`). A place that exists and fails is a
refusal, and nothing after it is tried. A key file named in the fleet's folder, the state folder or the installed
program's config folder is refused by name (`Misplaced`), by `paths.inside`, and a value that is no path is never
said, since a key pasted there would be on the screen. Every refusal is a `sealed.Refused`, so a caller catches the
vault's and the key's as one, and exits 1, or 3 from `status`; nothing here reads the terminal, so a run with nobody
there opens the vault as one at a terminal does.
`tests/unit/test_aw_fleet_vaultkey.py` holds it, with a vault opened in a child with no terminal and standard input from
`/dev/null`, every mode of a key file's group and others in turn, every public half a case makes looked for in its
files and its output, and the scan that only `vaultkey.py` names the variable and only `paths.py` and `vaultkey.py` the
key file.

**A host opened: `opened.py`.** `connect(home, reached)` opens the host a run is against, as `hosts.py`'s `reach`
chose it: `fleet.json`'s record, the vault opened with its key as `vaultkey.py` finds it, every host's token's secret
it holds, and one client (`pve/client.py`) pinned as the host keeps its anchor (`pve/pin.py`'s `kept`), through the
proxy `network_proxy` gives; it sends nothing, and its `Host`, a context manager, closes the client. Beside them a
`Host` keeps what the route to its machines is settled from (`engine/routes.py`, 16.4): the fleet's home, `host.yml`
as it was read (`fields`: its address, `ssh_user`, `ssh_port` and `jump`) and every setting's value for the run
(`values`, read-only). `client_for` makes another host's client the same way, for a run that asks the fleet's other
hosts. Where a host's files say no client that can be made - its address, its anchor or fingerprint, a file of
certificates `tls_ca_file` names that is not there, cannot be read or holds none (`vendors/trust.py`'s `Unusable`), its
proxy - it is `Refused`, one sentence, never a traceback, and asking another host says it and goes on. A `Host`'s repr
holds neither the environment its settings were read from nor the hosts' layout that keeps it, nor the run's values (a
path among them made from `HOME`), nor the secrets: a proxy that needs a password is given in `HTTPS_PROXY`. It is here
and not in `engine/` so that every workflow (`engine/fleet.py`, 16.4) and `status`, which may not import the engine,
open a host the one way; `tests/unit/test_aw_ownership.py` holds that it imports nothing of the engine, pydantic,
Ansible, Jinja2 or the MCP SDK, and `tests/unit/test_aw_fleet.py` the refusal on each road, the repr and what the route
is settled from.

**The settings verb: `settings_verb.py`.** `settings` is the verb's handler, here and not in `engine/` so that a read
imports no workflow: `--value` is `write`'s, `--reset` `reset`'s, the word `edit` `edit`'s, and anything else a read,
`show`'s. Alone it says how to use it and lists the groups (`registry.GROUPS` and each role, its spec's
short description beside it); a group lists its rows and, for a role, every option of its spec no row names
(`passthrough.options()`), passed through with its value or its role's own default (an expression of the role's shown
as text, `wiring` and `wiring_in`), or said why it is no setting, with the value of the row it is set through (`via`);
a name explains one row or option, its file, its default and the line that changes it; `--all` lists every row, each
option the fleet's file sets and each name of the vault's closed list, set or not set; `--changed` only what the files
change; `--host` names whose values. The host is the one `hosts.reach` gives a lenient request, or none where the fleet
has none yet; the values are `overrides.resolve`'s, each row naming its file and its home, the computer's own among
them (O16 item 17 waits on the owner). A secret is masked by its row's kind alone: a row that may hold one
(`claude_code_settings`), an option its role marks the vault's, and each of the vault's names are set or not set, never
a value; the check of every printed value against every decrypted secret that today's `tools/settings` makes goes for
the reads. `--all` alone opens the vault, with the key `vaultkey.open` finds from `vault_key_file` and
`vault_key_command`, to read which names it holds, and says in one note where it cannot; no read writes, starts a child
or imports Ansible. `--json` prints today's keys (`target`, `changed_only`, `settings`, `notes`; each row every key of
today's, `home` added, and each row of `--all` its `section`, its group).

The writes change one setting in the file that is its home, through `overrides.set` and `overrides.unset`,
compare-and-swap on the digest `overrides.read` gave at the start, under the file's short lock those take and no lock of
a run, as today's `just settings` takes none; `--host` names the host whose file a host's row is written in, and a
fleet's row says that it is the fleet's. What a name can be written as is one function, `settable`: a name the vault
holds (its closed list, or today's `vault_` names), the host's address and the other fields of its `host.yml` (today's
`ansible_host` and `vm_ssh_jump` among them), each role option `passthrough.refusal` refuses (a secret, set through a
row, made from each machine's name, set by a run), an internal value of today's defaults, a row that may hold a secret,
one given for one run alone (`--set`) and one that is each machine's own are refused with today's sentence, which names
`aw vault` where today's named `just vault`, and says a name back only where it is one the program knows. A value is
typed by `overrides.typed`; before that and after it, a value that holds a secret the vault holds is refused, looked for
by `overrides.holds_a_secret` in the text and in every string `overrides.strings` finds in the value, the vault opened
with its key (a vault that cannot be opened refuses the write; no vault yet holds nothing), as today but for the
identifier and a secret of fewer than four characters, whose length `_hunted` alone reads (`REVEALS`). No refusal says the value back. A file the overrides' reader
refuses (an inline `!vault` among what it refuses) is never written over; the file is written whole, which the
sentence after a write says, with what applies the value, by the roles of the variables it sets (`wiring`; the roles
configure runs are `CONFIGURED`, held to `playbooks/configure.yml`), and `--json` today's `wrote`/`reset`, `now` and
`notes`. `--reset` takes the setting out of its file, and that is all: a file of overrides keeps no example's line to
put back. `edit` refuses with no terminal; it writes every setting `settable` takes and one line holds, each under its
description, set or commented out with its value, to a file in a folder of its own, opens `VISUAL`, `EDITOR` or `vi` on
it (the one child a verb that is no run starts, in the foreground and holding no lock: `STARTERS` in
`tests/unit/test_aw_children.py`), and on return types each changed line as `--value` does and takes a line that is
gone as `--reset`: all of them once no line is refused, a line refused said by its number and the document offered
again. A host's row is refused by its line in a fleet with no host. Before writing it reads the files again, and where
another write changed one meanwhile it writes nothing and offers the document again, its changes still those made to
what was first read. Each file is then written once with all its changes (`overrides.set_many`); where a later file's
write is refused, the files written before it are named. An editor that fails, a document it took away, and a
`VISUAL` or `EDITOR` no shell would split each refuse with nothing written. A row that may hold a secret is never in
the document. `tests/unit/test_aw_settings_verb.py` holds it.

**The host verb: `host_verb.py`.** `host` is `host`'s handler. The list and its JSON are today's `hosts/choose.py`'s
`listing` and `where` over `hosts.py`'s layout, from the files alone, the JSON through `wire.to_wire` with today's keys;
`--for` and `--host` beside `--json` say which host that run would reach, and `hosts/choose.py`'s warning of a name
another host's alias records names the runs that would write it as the layout's `writing_runs`, in `aw`'s words. With a
name it is no run and makes no job: where `runs/lock_sets.py`'s `locks_for` says `fleet`, it holds the fleet's lock
through `runs/locks.py`'s `exclusive` for its one change, titled with the command as the printer writes it, so it is
refused naming the run while any run lives, and a run is refused while it holds it. `host <name>` writes
`client.yml`'s `current_host` through `hosts.make_current`. `host <name> --remove`, with no terminal refused before
anything is read, refuses the fleet's one host and the current host, asks for the name to be typed holding no lock, so
that no run is refused while the question waits, then takes the fleet's lock, makes the same checks again and deletes
the host's folder under the lock its `host.yml`'s writer takes; its VMs and their aliases stay, and so does its token in
the vault, which the line it prints names (`aw vault --host <name>`; O16 item 18 waits on the owner). The typed
confirmation is the verb's own, not `engine/confirm.py`'s. `tests/unit/test_aw_host_verb.py` holds it.

## 16.10 `guest/` — the helper, the system layer's inputs, layer 2

`lib/claude_vm/guest/` is where what the engine does inside a machine goes: the helper, which is sent to the machine
and never installed there, and the inputs of the system layer, which Ansible converges as a child with built-in modules
only, and layer 2, the rest of the work, in plain code. It imports `ssh/`, `schema/`, `fleet/` and `runs/` (the one
starter of a child, and the run's directory), and of `vendors/` the verified downloads, the vendored keys, the proxy's
choice and the trust's refusal, which layer 2's install of Claude Code reads its release by, and the one transport,
which layer 2's clones read GitHub's published SSH host keys by; nothing of `engine/`, `cli/` or `control/`, and no pydantic. A plugin
of that Ansible child is the one code of the new engine that imports `ansible`. It lives under `guest/ansible/`, where
the child's own configuration names it.

**The helper, sent on standard input.** The helper is one Python program for a machine's own `python3`, held to Python
3.10 and to the standard library, and never installed: each use sends it whole on the machine's standard input.
`lib/claude_vm/guest/bundle.py`'s `script(entry, asked)` packs its sources into one text, each defined in memory under
the module name it is imported by, and ends with a call of the dispatcher, `lib/claude_vm/guest/helper/main.py` (`helper
<verb> [words]`), handed the verb, its words and what it is asked, as JSON inside the text and never on a command line.
The text has a marked end (`frame`): a newline, then a line of its own, `MARK` and the script's length in bytes, then
that many bytes. The command line holds the bootstrap alone (`command()`: `/usr/bin/python3 -I -B -c` and `BOOTSTRAP`),
which first prints `STARTED`, a line of its own, so that its caller knows the machine's Python ran (and that `sudo` let
it); then reads standard input a line at a time up to the mark's line, reads exactly the length it says, and executes
that. What comes before the mark's line, a password's line that `sudo -S` was handed and left there, is read and
dropped, never executed or said back: the mark's line is the whole of a line, and such a line holds a newline only at
its end. What comes after the script is the verb's own standard input, which it reads on from where the bootstrap
stopped (`sys.stdin.buffer`), never compiled with the script; no whole script is one line on standard error and exit
status 2, nothing executed. Its verbs are `probe`, today's status probe unchanged, with its fixed list of reads, and
`clones` (`lib/claude_vm/guest/helper/clones.py`), the checkouts' writer of layer 2's clones, below; each script packs
its own verb's sources alone (`sources(verb)`, `OWN` naming a verb of the helper's own files), so that the probe's,
which `just status`'s rule holds to reads, carries no writer, and a verb whose module the script does not carry is
refused. A verb it does not know, a word too many or an ask that is not a JSON object is one line on standard error and
exit status 2, nothing run. The sources are read by `sources()` alone, the one function of `lib/claude_vm/` outside `status/` that
names `playbooks/module_utils/`: the probe's three files stay there until the cutover, since today's Ansible modules
import them as `module_utils`, and `sources()` finds them from the package's own place, two folders below the checkout's
top, never the working directory. A release is a checkout of a signed tag, so it holds the same files; the cutover moves
them into `guest/helper/` (card K28), and `sources()` then reads the package's own files alone. The script defines
`ansible` and `ansible.module_utils` as empty modules, each source put in `sys.modules` before the one that imports it,
so that the probe's own imports resolve on a machine with no Ansible. Today's `just status` bundles the same three files
with `lib/claude_vm/status/reach.py`'s `probe_script`, which this module neither changes nor imports, since today's
product never imports the new engine: the two print the same document for the same ask. The floor is held by a run, not
a parser's flag: `tests/unit/test_aw_guest_helper.py` runs the script on a Python 3.10 (`python:3.10-slim` under docker
where it is pulled, as on a lane, or a `python3.10` of the computer's, the test skipped where neither is), which
compiles every carried file with its own parser, finds each import in its own standard library, runs the probe and hands
a verb what follows the script; and ruff's rules that would write past 3.10 (UP017, B905) are kept off the helper's
folder, which `lib/**`'s `py313` would otherwise reach. The rule `just status` rests on is held over the whole bundle at
run time: the script started by a test-only bootstrap that first sets an audit hook (PEP 578), every program its Python
starts one of `ALLOWED`'s reads or `git_checkouts.py`'s, every file opened without a flag that writes, no file-system
write, no socket made, connected or looked up through, no signal sent and no library loaded (`ctypes`); beside it,
statically, each carried file starts a program only in the functions named with what each starts, those the probe never
reaches among them (`process_argv`, `running_claude`, `overwrites`), and reaches past the machine's files and programs
(a socket, a signal, `ctypes`, by module or `os.kill`) only in the functions named likewise: `alive`'s `os.kill(pid, 0)`
alone, called by `running_claude` alone. Each call is read as it is spelt and as what a name an import bound stands for,
so `from os import kill as k` then `k(pid, 0)` is `os.kill`'s, and no import names a module of them or a name from one.
The audit runs as the test's user, so the probe's root branch - a fork of itself, as root, that becomes the machine's
user for the user's reads (`setgroups`, `setgid`, `setuid`) - never runs under it: that branch is held by the static
rule alone, which names `probe` as starting no program but that fork. The same file holds the script against today's
stand-in VMs and through the SSH runner to a stand-in machine, with the sources' syntax and imports and the scan of what
names the folder.

**A helper verb run on a machine.** `lib/claude_vm/guest/remote.py`'s `helper(job, target, verb, words, ...)` is the one
way the engine sends a program to a machine: it sends the script, always, then `stdin`, the verb's own input, after its
marked end, over the SSH runner (§16.6), under the runner's limits, today's (`limits.seconds` the call's whole time, a
second session's and the parse of the answer included), and at most `LINES` (4096) lines where the limits set no bound
on them; so no word of the verb's, nothing it is asked and nothing of its input is ever on the argument list of the
`ssh` child. As root (the default) it goes through `/usr/bin/sudo` by its path, under `/usr/bin/env LC_ALL=C` so that
sudo's words are its own: `sudo -n` first, and only where that is refused for want of a password - a line of its
standard error that is sudo's `sudo: a password is required` or sudo-rs's `interactive authentication is required`,
Ubuntu's sudo from 25.10, under either name it says itself by - and the run's `Password` gives one, a `Secret`, a second
session with `sudo -S -p ''`, held (§16.6's `hold`): the password's line alone, then nothing until the bootstrap's line
says the program runs, within the time to say anything and two seconds (`REFUSAL_GRACE`) before the call's end, where
standard input is closed so that sudo says its own refusal in time, and only then the script and the verb's input. So a
wrong password costs one of sudo's attempts, never three, and no line of the script is ever read by sudo as a password;
and where it was sent and the bootstrap's line never came, however the session ended (by itself, by the call's time, cut
or not reached), the run's `Password` is marked refused, and the run sends it to no machine again: that call's outcome
says so, once, and every later one takes `sudo -n`'s refusal as it is, since a password sent on every read could lock
the account where PAM counts the failures. A right password that sudo is slow to take is so withheld too: it costs a
read, never the account. The run's first send settles it: calls side by side wait for that send, within their own time,
and then send it only where sudo took it. `Password(source)` is made once by a run and handed to each call: its source
is asked at the first call that needs it, and never again. Where sudo asks nothing, no password is sent. The password is
read from its `Secret` by the runner alone, to send it, and by `remote.py`'s `_forms` alone, once a run at its first ask
(`REVEALS`), to make the forms a machine may say it back in, each a `Secret`: as sent; as the runner reads it, plain
(`text.plain_text`) and stripped; one plain line; JSON's escapes, in ASCII and not; and a C string's and a shell's
backslashes (`printf %q` of printable text). Each is put as its mark (`secret.struck_all`) wherever a machine says it
back: in what it said on its standard error, and where that came within 1 KiB of its bound (`CUT_NEAR`), an end of it
that begins a form (`secret.struck_end`), where the runner's cut may fall partway through the password; in every line
before it is parsed (a number said back is then no JSON, and the answer refused whole), in every string and key after,
and in why the call was cut. Not struck: a number a machine spells another way, a password split across lines or written
in another escape (`printf %q`'s `$'...'` of a control character, a URL's); nothing else secret follows the helper, so
`stdin` is bytes and never a `Secret`. What comes back is an `Answer` - the verb ran (its line of the bootstrap's came:
what a shell printed at login before it is dropped, as the bootstrap drops what comes before its mark) and ended by
itself: its exit status, each line after the bootstrap's parsed as JSON, NaN, Infinity, a number past a float and a
nesting past 64 levels refused, every string in it, keys too, made one plain line (`text.one_line`), and what it said on
its standard error as one plain line - a `NotRun` - it never started, no line of the bootstrap's: sudo refused it, or
the machine's interpreter could not be run - or a `NotAnswered`: not reached (ssh's 255, or nothing said within its
time), or out of its time, its answer's parse too, ended with what the run started for it, the lines it sent before kept
where every one parses within a second; or more than it may send, in bytes or in lines, or a line that is no JSON,
refused whole. `lib/claude_vm/guest/states.py`'s `read(job, target, parts, settings=...)` runs `probe` for the parts
asked (`system`, `claude`, `github`, `work`, `tailscale`) and returns today's `lib/claude_vm/status/found.py`'s records
of its answer (`ssh_found`), the parts not asked left out, so that a layer-2 step and status say each state by one
definition, today's, which `tests/unit/test_status_vms.py` holds to the roles' own tasks; `settings` is one machine's in
the shape status keeps, the probe's ask under `asked`. It runs as root only where a part root reads is asked
(Tailscale's), and where sudo refuses, as the machine's user once more, within what is left of `limits.seconds`, root's
part said not read, as today's `just status` does; every other read is the user's, with no `sudo` at all. found.py is
reused as it stands, one name taken from it, so `states.py` is one of the modules `tests/unit/test_aw_vendor_trust.py`'s
`CONTEXTS` names (today's `status` makes a TLS context elsewhere), and no module of Ansible is loaded.
`tests/unit/test_aw_guest_remote.py` holds it on F30's stand-in machines: each bound, each refusal and each answer
above, the tree of a verb out of its time ended (`claude_vm.proc`'s), the command line held to the bootstrap with every
canary of a verb's words, its ask, its input and the password in no word of `ssh`'s and no file of the machine's,
`sudo`'s rule call by call, a wrong password sent once however its session ended and however many calls were under way
side by side, a password a stand-in `sudo` says back in each form above, a quote, a control character and a space at its
end among them, and cut by the bound five bytes in, in no field of an outcome, of the states' record or of the job's
folder, a `.bashrc` that prints, the states equal to `found.py`'s records of today's probe on the same machine, and a
layer-2 step that asks whether Claude is signed in, in a Python of its own, with no Ansible loaded.

**Layer 1's child.** `lib/claude_vm/guest/layer1.py`'s `run` converges one machine with one `ansible-playbook`, started
through `children.start` as an `ansible` child of the run, in a session of its own and in the run's directory (`start`'s
`cwd`), never the caller's working folder, where Ansible looks for a task file too: it has no terminal, a terminal's
Ctrl-C reaches the run alone, and the run's ladder hands it on as SIGINT to the child's group (§16.8). Its standard
input is `/dev/null` and its one pipe is the events', whose write end goes over through `start`'s descriptors and whose
read end the run reads while the child runs; what it prints goes to a file of its own, never the terminal, and its last
64 KiB come back, plain, with its exit status. Two files go into the run's directory: `layer1.cfg`, one for the run, and
an inventory of one host for each machine, `inventory-<identity>.yml`. The configuration names one stdout callback and
no other, one fork, facts kept under `ansible_facts`, pipelining, the `yaml` inventory plugin alone, this checkout's
`roles/`, Ansible's local temporary files in the run's directory, and every folder Ansible searches for a plugin or a
collection: the product's own (the callback's, and the engine's filter folder,
`lib/claude_vm/guest/ansible/filter_plugins/`, whose one adapter, `aw_filters.py`, gives `plain_text` and
`sshd_match_spec`, the two the roles call, each the core's function of its name - never today's `filter_plugins/`, whose
filters run on this computer as a play calls them, `proxmox_trust_holds` opening a connection to the address it is
given; a role that calls another filter adds it to the adapter) and, for every other kind and for the collections, an
empty folder of the run's directory, Python's path not scanned for collections, so that nothing under the caller's home
or `/usr/share/ansible` is searched (a vars plugin in `~/.ansible/plugins/vars` runs without being enabled): every
module the system play's roles call is `ansible.builtin`'s (the ratchet, below), so the play needs no Galaxy
collection. Ansible still adds the play's own folders beside it
(`playbooks/library/`, `action_plugins/` and `module_utils/`, today's) and each role's, and searches a `collections/`
beside the play whatever the configuration names: the ratchet (below) holds the play's folder to those three, none
holding a name of Ansible's own, and the roles to none. `ssh_args`, the one place ahead
of Ansible's own words to `ssh`, holds `-o PreferredAuthentications=publickey -o GSSAPIAuthentication=no -o
HostbasedAuthentication=no`, a key and no other way to log in, as the SSH runner offers, and never Ansible's default,
which would put `ControlMaster=auto` ahead of the host's options and make the child a connection's master. The one
callback named is the product's, `aw_events`, in
`lib/claude_vm/guest/ansible/callback_plugins/`, which only this configuration names. The one host is named by the
machine's identity, never the name a person
chose, which is the variable `aw_name`; its variables are the address and the account, `/usr/bin/python3` and
`/usr/bin/sudo` by their paths, and the connection as the SSH runner makes it (`runner.ansible_vars`, §16.6): its
options first, `StrictHostKeyChecking=yes` against the product's own known-hosts file under the identity, the shared
socket with `ControlMaster=no`, so that the child rides the master the run opened, and the hop only where the target
carries one. Ansible's ssh connection puts words of its own ahead of them, none of them the host key's: the port, the
account, a connect timeout of 10 s, keyboard-interactive and password authentication off, and a
`PreferredAuthentications` with GSSAPI and host-based ahead of public keys, which the configuration's `ssh_args`, ahead
of it, outrank.
The child never takes a key: a target whose identity has no key filed is refused by name before anything is written,
naming the line that files it, and so is a first use's target, the Proxmox host, an identity that does not start
`agent-warehouse-`, and an inventory value that would open a template, since Ansible templates an inventory's values.
The environment is an allow list: `PATH`, `HOME`, `LANG` and `SSH_AUTH_SOCK` of the caller's, then `ANSIBLE_CONFIG`
naming the configuration, the untrusted-template switch at `ignore`, Ansible's own default pinned (an untrusted template
is handed back as its text and never rendered; `error` would fail every task that reads a machine's output holding `{{`,
for no protection more), the events pipe's descriptor
(`AGENT_WAREHOUSE_EVENTS`), `ANSIBLE_VERBOSITY` only for a run given `-v`, and the job's marker; no other `ANSIBLE_*`,
`PYTHON*` or exported stdout callback reaches it. The command line gives the variables only as `-e @<file>`, never
`key=value`, `--tags` with the parts asked for, and `--check` and `--diff` where asked. A call that ends while its child
runs (cancelled) ends the child with its tree (`claude_vm.proc`), its detached task worker among it. Only a test passes
`test_connection`, the host variables of a test-only connection: `local` for a play of one task on a stand-in machine,
`community.docker.docker` for a Molecule container, with the folder of collections that connection is found in beside it
(`test_collections`), named in place of the empty folder. `tests/unit/test_aw_layer1.py` holds it: the configuration
read back by `ansible-config dump --only-changed`, every folder ansible-core's own definitions search under its home
named, a run's collections' path its empty folder whatever the caller's environment names, a vars plugin under the
caller's home never run, a
play of one ping on a stand-in with its child in the run's registry, the environment as a task's module sees it, each
refusal before anything is written, SIGINT to the child's group and a cancelled call each leaving nothing, the words
Ansible's own ssh connection gave the stand-in `ssh` held by this computer's `ssh -G` over a `Host *` that loosens
them, a key alone among them, and scans that the argument list never holds `key=value` after `-e` and that no product
code passes the test-only connection or its folder of collections.

**The system play.** `playbooks/system.yml` is layer 1's one play: one play over the one host of the inventory the
engine writes, which runs today's three system roles, `common`, `vscode_server` and `dev_tools`, as they stand and under
today's tags (`lib/claude_vm/guest/system.py`'s `PARTS`: a part asked for is a `--tags`, and Ansible checks every role's
arguments against its spec under any tag), after the opening tasks of today's guest play in `playbooks/configure.yml` -
the wait, the base's check by `raw`, the facts, cloud-init's wait and root's temporary directory - copied as they stand.
Its first task is today's check of the options in effect (`playbooks/tasks/host_keys.yml`'s), made whole: each variable
of the connection - every variable of Ansible's ssh connection and of `sudo`, the connection's name, the interpreter and
become's own, held to ansible-core's own documentation - is the inventory's the engine wrote, as written, or set
nowhere, the inventory read back by its own source, `ansible_inventory_sources`, which no source of a host's variables
outranks, never by `inventory_file`, a host's variable that a file beside the play could point at a forged copy. It is
read as the host's own variables are, so that an extra variable, a variables file beside the play or a
vars plugin that would loosen the host key's checking or replace the program fails it by name before anything
connects; the play's own vars and its roles' are none of those, and the ratchet holds every file the play reaches to
setting no `ansible_` variable. `system.variables(path, document, secrets=...)` writes the play's variables through
`variables.write` alone, given `system.NAMES` (guest/wiring.py's `NAMES`) as the names the play takes, after refusing by
its name a setting the engine cannot serve yet: `common_user_passwordless_sudo` other than true, which needs a become
password that no play is handed before F24.

Layer 1 is held to state modules, and reaches nothing of this computer: `tests/unit/test_aw_layer1_ratchet.py` holds
what the play reaches through a walk of its own that reads what Ansible reads. It follows the play's pre-tasks, tasks,
post-tasks and handlers, a block's three parts among them; an include or import of tasks in any spelling (short,
`ansible.builtin.`, `ansible.legacy.`), from the including file's folder or a role's `tasks/`; a role the play names,
one included or imported from anywhere, and one a role's `meta/main.yml` names among its dependencies, found in the
roles' folder; each role's tasks, handlers, defaults, vars and meta in every spelling Ansible's own `_load_role_yaml`
takes (`main.yml`, `main.yaml`, `main.json`, `main`, a `main/` folder of defaults or vars, the file an include's
`_from` names), read through Ansible's own `find_vars_files`, and its argument specs, which Ansible templates here as
its check's arguments; the play's `vars_files`; each template a role's template task renders; and each file of a role's
`files/` that a `copy`, `unarchive`, `assemble` or `uri` sends from this computer (`src`, and `uri`'s multipart
`filename`s, unless `remote_src` is true as written, as it is for `assemble` unless said). What it does not follow it
never passes over: a playbook imported, an include in a handler
(Ansible resolves those otherwise), a file or role named by a template, a name Ansible reads otherwise than the walk
(one that starts with `~`, which `DataLoader` takes from the person's home, holds a `$`, which it expands, or is quoted,
which it takes off: the walk follows a relative name each part of which starts with a letter, a digit or `_`), a
collection's role, a `collections` keyword (under which a short name can stand for a collection's module or role),
an include's `apply` among them, which Ansible loads as a block (review 67), a role in the play's own roles folder
(searched before `roles_path`), and a template outside a role each raise `Unfollowed`, which fails every test (review
61). So do what Ansible renders that the walk would read as text - a template, or any string
of any file the walk read, that names another template (`include`, `import`, `from` or `extends`, `{%-` and `{%+` too:
Ansible renders a task's string with the play's folder to load from), or that sets its own delimiters, under which such
a statement reads as text (a `#jinja2:` line, ansible-core's `JINJA2_OVERRIDE`, or one of the template action's six
delimiter arguments) - and arguments the walk does not read, which ansible-core merges into a task's once its parser
has read them: `module_defaults` on a play, a role entry, a block, a task or an include's `apply`; an `args:` given as
one template (the parser's `_variable_params`); and a module given one template as its whole value, but a command's,
whose words `REASONS` holds. So do a task file Ansible looks for elsewhere before the walk's (review 50): an
`import_tasks` in a role's
file that an include or import brought in, which Ansible looks for first under that file's own folder's `tasks/` and
then the working folder's, and any include or import two files below the play, looked for beside the play first (one
file below, the walk's file is Ansible's first); a file sent from this computer outside a role, or one that is no
regular file of its role's `files/` as the checkout holds it (a name under `files/`, a folder, a link out); a role, or
one of its own folders (`tasks/`, `files/` and the rest), that is a link, and any file the play reaches that is a link
out of the checkout,
through which Ansible would read a folder the test never saw, on the computer the play runs on (review 67); and what
Ansible takes from the
play's folder that the walk does not read - `group_vars/` or `host_vars/`, whose variables reach the host whenever it
joins a group, `collections/`, a folder of any kind of plugin but the three `BESIDE_THE_PLAY` lists with its reason
(`library`, `action_plugins`, `module_utils`), and in those three a name of Ansible's own module, action or
module_utils, which Ansible looks for there first under `ansible.legacy` (a local `normal.py` runs here for every module
that has no action of its own) - and any folder of plugins in a role, each kind and name read from Ansible itself.
Four tests read what it found. A `command`, `shell`, `raw`, `script` or `expect` is listed in `REASONS` with its reason
and with its module and arguments as Ansible's `ModuleArgsParser` reads them, so that a reason is held to what runs: a
new one, one no longer reached and one whose words changed under its name each fail, and two such tasks of one file and
name fail as one key. A module of `ansible.builtin` is one written so that ansible-core's own routing table
(`ansible_builtin_runtime.yml`, read from the installed package) keeps within `ansible.builtin` for its action and its
module, neither removed, and that names one of ansible-core's own actions or modules there: the table sends
`ansible.builtin.synchronize` (whose rsync starts here), `ansible.builtin.patch` and hundreds more to a collection, and
a test holds that no name it keeps within `ansible.builtin` comes to one of those the scans read by name. A module
outside `ansible.builtin` fails, and none is allowed, so that the product needs no Galaxy collection: the person's keys
are `file` and `lineinfile`, the watch limit `copy` and a `sysctl -p` of its file, the global npm packages a read of
`npm ls -g` and an `npm install -g` of each it lacks, its three commands in `REASONS` with the rest. Nothing it reaches sets a variable
whose name begins `ansible_` - the vars of a play, block, task, role entry or dependency, `vars_files`, `vars_prompt`, a
role's defaults and vars, a fact (a templated name refused), a register, a loop's variable, an include's `apply`,
`add_host` - nor includes variables at all, which the play's first check cannot see. And nothing runs or writes on this
computer: no `delegate_to`, `local_action` or `connection` on a play, role entry, block, task or `apply`, no `fetch`,
no `group_by` or `add_host` (a group joined after the play's check, whose variables the check never saw), no play over
any host but `all`, no `with_` loop and no private field of ansible-core's task, handler or block, read from its
`fattributes` (`loop_with` runs a `with_` loop's lookup over `loop`, review 67), and no lookup but the five `LOOKUPS`
lists by its file and whole
string (the check's read of the inventory, `remote_tmp.yml`'s setting, and the three vendors' keys), found as `lookup`,
`query` or `q` as a word (Jinja calls one wherever it is named: `(q)(...)`, or a name set to it) in every string of
every file the walk read (the roles' argument specs among them) and in the templates. Nor does it ask this computer of
its paths, which tells a name's existence, kind, real name, home or environment where the play is rendered, or open a
file there (review 67): no test of ansible-core's `files` test plugin but `abs` (`exists`, `file`, `link` and the
rest, read from the plugin) and no `vaulted_file` (core's, which opens the file to see whether it is a vault); no
`fileglob`, `realpath`, `relpath`, `expanduser` or `expandvars` filter, whether written or named to `select`,
`reject`, `selectattr`, `rejectattr` or `map`, or to one of those five that another is given by name
(`map('select', 'exists')`); and none of the five given a name only a run can say, or its arguments by `*` or `**`.
They are read as Jinja's own parser reads each string Ansible renders, not as words, which would take the same names
in the roles' prose and paths: a template and a string that opens one; and, as an expression, the way Ansible's
`_normalize_and_evaluate_conditional` and its debug and assert actions evaluate one, the value of a `when`,
`changed_when`, `failed_when`, `until` and a loop's `break_when`, a debug's `var` and an assert's `that` (read from the
arguments as the parser reads them, `var=` and `that=` among them), with the template a string in it holds, which
Ansible renders as it evaluates it. An expression made of one template, whose result Ansible evaluates as an
expression the walk never sees (the roles have none), and a string the parser cannot read are refused (review 67's
second reading). Each road review 61 ran is run as Ansible runs it too: planted under the test's own folder, found by
the four tests, then run by `layer1.run` over the test-only local connection on a stand-in machine, where its decoy
writes a mark in the test's own folder - a copy given its `src` by `module_defaults` or by `args:`, a fact of the
interpreter so given, `ansible.builtin.synchronize`'s rsync, a copy, template or include of a name that starts with `~`,
`q` named apart from its call, a template's own delimiters and its `#jinja2:` line, an include in a task's string, and
an import two files below the play; and review 67's `loop_with: pipe` and a role's `files/` linked to a folder
elsewhere. The `system` scenario
(`molecule/system/`, [§11.3](testing.md#113-playbook-scenarios--just-test-integration-and-provision)) converges a
container through `layer1.run` with the test-only `community.docker.docker` connection, twice, the second changing
nothing. `tests/unit/test_aw_layer1_play.py` holds the opening tasks equal to the guest play's, a part asked for running
that role alone, the check passing the inventory the engine wrote and failing a fact or a variables file beside the play
by the variable's name, a machine's output holding `{{` read as its text by a message, a fact, a condition and an
assert, a file beside the play that points `inventory_file` at a forged copy failing the check, the refusal, and the
product's own child, started as the engine starts it, loading the play and starting it with no folder of collections
at all (ansible-core resolves a role's modules as the play loads, and a collection's would fail the load).

**A play's variables.** ansible-core loads an extra-variables file as a trusted source, so a string in it that holds
`{{`, `{%` or `{#` would be rendered as a template: a setting would be code. `lib/claude_vm/guest/variables.py`'s
`write(path, document, secrets=..., names=...)` writes the play's variables as one JSON file, `vars.json` in the run's
directory,
in which every string is `{"__ansible_unsafe": <the string>}` (`wrap`): Ansible's loader reads the file as JSON first,
and its decoder hands such a string on as text, without the trusted mark. The standard library writes it, at 0600,
whole, beside the path and then put in its place, never through a link. Nothing is written where a variable's name is
not one a role variable's is (held first, so that a refusal never says a name with a control character), where a
variable names the play's connection (any `ansible_` name: an extra variable outranks the inventory, where the SSH
runner's options are, so no variable of the file names a connection's option), where a variable is named as one of
`FIXED`, which an extra variable would outrank for every task of the play - the inventory's own name of the machine
(`aw_name`), Ansible's magic variables (`inventory_hostname`, `group_names`, `inventory_file`, `hostvars`, ...) and
every name ansible-core reserves (`get_reserved_names()`: `omit`, whose value would make every `default(omit)` of the
roles pass one, the keywords of a play, a block and a task, and the templates' globals, such as `lookup` and `range`),
where a key anywhere begins `__ansible`, which the decoder reads as its own, or anything else would fail Ansible's JSON decoding (an integer past 64 bits, a float that is not finite, a
nesting past 32 levels), since its loader then reads the very same text as YAML, where a wrapped string is a plain
mapping and its string a trusted template; where a string is no text UTF-8 holds, which fails the YAML too; where a
value is of a type no variable holds, a `Secret` above all; where the document holds a secret the run opened, in a
name, a string or the file's text, which the refusal never says back: no secret is ever in this file; or, those first,
whatever it lists, where a variable is none of `names`, the names the play takes, which its caller gives and never leaves
out. An extra variable outranks a play's vars, a task's vars, a `set_fact` and a `register` too, so a name of the play's
own logic would turn it off for every task: the system play's check that the connection is the engine's
(`system_not_written`, `system_connection_variables`), a register of its opening tasks (`configure_base`), a role's
internal or register (`common_names_1x`, a list of files moved as root; `common_sshd_hardening`;
`common_user_password_salt`), a loop's `item`. For the system play the names are `guest/wiring.py`'s `NAMES`, the one
list, made from its `PLAY`, `TABLE` and `INTERNALS` and written once, which `guest/system.py` names beside the play
(`system.NAMES`); a later variable of the play is added there. `layer1.run` holds the file it is given to the same rules
(`held(path, names)`) and to the names of its own list of each play's (`PLAY_NAMES`, the system play to `system.NAMES`),
never a caller's: it takes no names, so a caller cannot widen the list by handing the file's own names or the play's
with more, and a variables file beside a play it lists none for is refused, a copy of the system play elsewhere among
them, before anything is written or started. The decoder is under `ansible._internal`, so the tests, not Ansible's
documentation, hold this across releases:
`tests/unit/test_aw_variables.py` holds each rule, a name outside `names` among them, by `write`, by `held` and by
`layer1.run`, and `tests/unit/test_aw_layer1_play.py` each name above refused by `system.variables` and by layer 1's hold
beside the system play, and a caller's own list refused (the file's names, a set made from them, `NAMES` and two
more); `test_aw_variables.py` holds `FIXED` equal to what a child of the locked ansible-core gives a host, a play
and a role's task beside an inventory's and a play's own, and its reserved names; it loads a written file through
Ansible's own loader, in a Python of its own, and finds no string trusted (the same document unwrapped, every string trusted), and through a real child on a stand-in machine a
wrapped `{{ 7 * 7 }}` reaches a task as that text, an unwrapped one is templated, with the untrusted-template switch at
`error` the wrapped one used as a template fails its task, and every kind of value comes back as it went; a walk of
every file written finds no bare string leaf. They are among the tests that must pass before the ansible-core pin moves
([§13](toolchain.md)).

**The play's variables from the settings.** Layer 1 hands the system play one variables file and an inventory of one
host, and nothing else: no `group_vars` or `host_vars`, and none of today's wiring in
`inventory/group_vars/all/defaults.yml`, is read. `lib/claude_vm/guest/wiring.py`'s
`role_variables(settings, target, parts, profile={})` makes that file's document as a pure function of the settings:
`settings` is `overrides.resolve`'s for the machine's run, `target` the machine's name, one VM name as layer 1 holds it,
`parts` the play's parts asked for, whose roles' variables it gives after the play's own
(`configure_wait_for_cloud_init`, its row's value), and `profile` what a kind of machine lays under a person's settings
(empty for a VM), by a setting's or an option's name, each laid only where the person set nothing: a row at its default,
an option set nowhere. Its `TABLE`, written by hand as plain data and evaluating no template, gives each variable of the
three roles' specs, every entry point's, its source: a row of the settings table (`common_user`, `vscode_server_user`
and `dev_tools_user` are `vm_user`'s value, `common_user_ssh_public_keys` `vm_ssh_public_keys`', `common_extra_packages`
`vm_extra_packages`', `vscode_server_enabled` `vscode_remote`'s, and the six of `dev_tools` that are rows of their own
name those rows'); the machine's name (`common_hostname`, whose role default reads `inventory_hostname`, the identity in
layer 1's inventory); or the role's own option of the same name, passed through (`passthrough.py`), handed only where
the settings hold it, so that set nowhere the role's own default applies, those worked out from the machine's facts
among them. `INTERNALS` gives `common_user_password` the empty string, with its reason: it matters only with
passwordless sudo off, which `system.variables` refuses until F24 hands the password to Ansible's own become-password
file on a pipe, so no secret is ever in the file. A name of the settings that is a variable made here and no row of its
own name - `common_user`, `common_hostname`, `common_user_password` - is refused by name, as `--set` and the fleet's
file refuse `common_user_password` as the vault's; so are a part that is none of the play's, a target that is no VM name
and a profile's name that is no setting or option of the table. Every value is a copy. `NAMES` is every name of `PLAY`,
`TABLE` and `INTERNALS`, the names the play's variables may hold, and with every option set the function makes each of
them. `tests/unit/test_aw_guest_wiring.py` holds the table to the roles' specs, every variable in it or among the
internals once; each setting to the row today's wiring reaches the variable from, both ways; each option to
`passthrough.py`'s, both ways; `NAMES` to the table, every name the function makes among them and taken by the writer
and by layer 1's hold, with layer 1's own list; and the output, for the registry's defaults and four sets of overrides
(the rows the play reads, twice, with their switches set apart; every option of its roles; values given for one run),
equal variable by variable, in type and value (`true` is not `1`), to what a child of the locked ansible-core gives the
same roles, with a machine's facts set, from today's example inventory with the same values in `local.yml`, against the
one host's inventory layer 1 writes and the file the play's own writer made, loaded as extra variables. No two rows the
play reads carry one value in every case, which the test holds too, so a row read in another's place is a difference.
The
`system` scenario converges its container from `role_variables` alone, its settings written into a fleet home of its own
and given with `--set`, with no `group_vars` or `host_vars` beside the play or the inventory.

**Layer 1's events.** The product's stdout callback (`guest/ansible/callback_plugins/aw_events.py`) writes each event
of the play as one line of JSON on the events pipe, whose descriptor `AGENT_WAREHOUSE_EVENTS` names and which it makes
non-inheritable at once, and nothing on the terminal; a child started without the pipe refuses to start. The events are
a play's start, a task's, each result (`ok`, `changed`, `failed`, `skipped`, `unreachable`, a loop's item with `item`),
a retry, a diff and the recap, each with its task's name and its part, the role it is in. A result says its message,
and where it failed, was unreachable or the run was given `-v`, its output, return code and exception: never more of a
result than those, and of a result Ansible censored for `no_log` only its kind and its task, since ansible-core 2.21
keeps a censored result's `exception` and `warnings` and hands a callback them whole. A diff is written only in a run
given `--diff`, never for a task's own `diff: true` alone and never for a censored task. A fault of one of its hooks is
an event of its own (`fault`, the hook and the error's type), never Ansible's warning alone, under which a result would
vanish in a run that exits 0. A result's message, its item and its output are written as the values they are, a
mapping or a list as one, never as JSON text inside a string, whose escaping would hide a secret holding a quote, a
backslash, a line break or a character outside ASCII from the strike by its exact text. Every string, a mapping's keys
among them, passes through one function, `masked`, the identity until ansible-core 2.22's `mask_secrets()` goes there
(F5a); a string past 256 KiB keeps its end from a line break, never a part of a line. A pipe whose reader has gone ends
the writing, and the play goes on, without a traceback. `lib/claude_vm/guest/events.py`'s `Heard` reads the pipe in the
run's process, a line at most 4 MiB, and hands each event, as it comes, to the job's `emit` (§16.8) as a `step` event of
the machine, keyed by its identity, the one key of a machine in every event of a run, as `job.json`'s `machines` and a
machine's `end` have it, with the name a person chose beside it (`name`) for what is drawn: its text the task's or the
play's name, its data a fixed set of fields each held to its type (`status`, `part`, `task`, and where there is one the
item, the message, the output, the return code, the exception, the warnings, the diff, the attempts, and whether the
result was censored or its failure ignored). Every string is struck of every secret of the run's redaction list
(the job's, `Job.redacted`: every `Secret` the run opened, which the run adds with `redact`), the longest first (`secret.struck_all`), so
that of two where one holds the other the longer is struck whole; made plain (`text.plain_text`); and struck again, so
that a secret an escape sequence split is struck once whole; then cut to its field's bound, after the strike. A
message, an item or an output written as a structure stays one until each of its strings is so made, and is then made
one text, JSON's form of it, struck once more, then cut. The strike meets a secret in the forms Ansible itself quotes
one in too (`secret.struck_all`): JSON's escape of it, with and without ASCII's, as a mapping's diff is written, and
`repr`'s, as an exception's text quotes a value or a key that is a tuple is made text. What it does not promise: a
secret in base64 or a URL's escapes, one split across two strings, or one split by a character `plain_text` keeps (a
zero-width space). A line that is no event, or
too long, is said as one that could not be read; a line the child did not finish at the pipe's end is none.
`layer1.run` returns, with the exit status, the counts a caller needs (`events.Counts`: the failed, the unreachable, the
changed), from Ansible's own recap or, where the child ended before it, counted from the results heard; and the end of
what the child printed, from a line's start, struck and plain the same way, which a person sees line by line, never
whole. A listener that fails while the child runs ends the child with its tree, and its error is raised. Nothing is
drawn here, and NEXT STEPS are not derived. `tests/unit/test_aw_layer1_events.py` holds it on the pinned ansible-core,
with a listener of its own standing for the job's text log: a ping's events, in order, handed to `emit`, written in the
log and nothing printed on the terminal; a `no_log` task that fails, with a message, an output, an exception and a
loop's item, absent from the pipe, from `emit` and from the log, where the same failure without `no_log` says both; a
secret of the redaction list printed by tasks, in a task's name, an output, an item and split by an escape sequence,
absent from every event, the log and the record; a secret holding a quote, a backslash, a line break and characters
outside ASCII in a `debug` and a `fail` whose message is a mapping or a list and in a loop over mappings, absent in every
form, raw and escaped; of two secrets one inside the other, the longer struck whole in either order; one machine's steps
and its end keyed by the one identity the record names; a control sequence reaching the log as plain text; the diff's
two rules; the pipe closed by its reader before and in the play; a listener's failure; and the reader's own rules on
lines of its own.

**The deep diff (`deploy --check`, card F21).** `layer1.check` runs the same child as `layer1.run` with `--check
--diff`, so nothing on the machine changes, and returns `layer1.Checked`: how the child ended, and each task as check
mode makes it (`events.Checked`, from `Heard.checked`), in the order the tasks started. A task is "would change" where
a result said changed, with every diff heard of it; "would not change" where every result said ok or was skipped by a
false condition; and "not evaluated" where check mode skipped it unrun, and where it failed, was unreachable or had no
result. Which skip is which the callback says, a yes or no alone (`condition`): Ansible marks a skip of a false `when`
(`false_condition`, `skip_reason`), of the task or of every item of its loop, and any other skip is check mode's own,
of a module that does not support it (`tempfile`, `expect`) or a `command`, `shell`, `script` or `raw` it would have
run, so no module is named here. A diff is written only in this mode and never for a task censored for `no_log`, and
is struck as every event's text. Layer 2 has no check mode: `step.check_steps(ctx, steps)` answers each step from its
`probe` and its `difference`, applies nothing and emits nothing: "would change" with the difference, "would not
change" where there is none, "would apply" for a step with no read of its own (its `reads` False), never probed, and
"not evaluated" where its probe could not go on, with why, struck of the run's secrets. Each step reads the machine as
it is, so a later step never sees what an earlier one would write. `tests/unit/test_aw_layer1_check.py` holds both on
the pinned ansible-core against the stand-in machine.

**Layer 2's steps.** `lib/claude_vm/guest/layer2/step.py` is the one shape of a step: a `name` (its events' key, as a
stage's step is keyed), a `part` (one of a configure's six), `probe(ctx)`, which reads the machine and changes nothing,
`apply(ctx, state)`, which brings it from that state to its settings and says whether it changed anything (nothing
where the state is what the settings name, so a second run applies nothing), and `difference(state)`, what `apply`
would change, what it is about, as it is and as it would be, or none: what F21's `--check --diff` says, with no
`apply`. A step that cannot go on raises `Failed` with its sentence. The `Context` is one machine's in one run: the job,
the route's `Target`, the key its events name it by, the settings by name, the environment of its children and the
cancel flag; `run` is the SSH runner's one bounded call on the machine, as its account or as root through its sudo by
its path under `sudo -n` (where sudo wants a password the call fails, since a VM the engine makes has a passwordless
sudo and no secret follows a step's command), and `emit` one event about the machine. `run_steps(ctx, steps)` runs a
machine's steps in order, each between two `step` events, its part and the run's stage on each, the end `done` with
whether it changed anything or `failed` with why; the first that fails stops the machine, and a run that is stopping
starts no next step. It returns the machine's outcome, each step's change and why it failed.

**Claude Code's install.** `lib/claude_vm/guest/layer2/claude_install.py`'s step `claude-install` is every way Claude
Code is installed, today's `install_native.yml` and `install_npm.yml` in plain code, which nothing of layer 1 does:
it reads `claude_code_install_method` and `claude_code_download_url`, the role's options passed through by their own
names (the role's own defaults where none is set, held equal to them), `claude_code_version`, held to stable, latest or
a release's version before anything runs, and `tls_ca_file` and `network_proxy` for this computer's reads. Natively, the
default, the probe reads as the machine's account what `~/.local/bin/claude` resolves to and that file's SHA-256, and
runs nothing of it: an install counts only where the launcher resolves to a release's file under
`~/.local/share/claude/versions/` (the layout the binary's own `install` keeps, which the helper's probe reads the
version from) and that file is the binary the signed manifest of its version gives, and anything else is installed
over; a channel installs only where no such install is and an exact version where another is; the release is settled here by `vendors/downloads.py`'s
`claude_release` (§16.7), which returns the version and the digests only once Anthropic's signature of its manifest
verified by the vendored key, so this module verifies nothing itself. The machine's platform is read (`uname -m`,
today's map) and a release with no binary for it refused; then, as the account, three calls: the binary downloaded by
the machine's `curl` (https alone, bounded) into `~/.claude/downloads` and its SHA-256 said back and held here to the
manifest's; its digest checked again on the machine and only then its own `install` of that exact release run, never a
channel's name, bounded there by `timeout`, and what `~/.local/bin/claude` resolves to held to that release's file
under `~/.local/share/claude/versions/` and to the same digest, the launcher removed where it is not; and the download
removed, whatever happened. The installer script is never used. Under `npm`, as root:
`npm --version` must answer, `npm ls -g --depth=0 --json` is read, and `@anthropic-ai/claude-code` is installed with
`npm install -g` where it is absent, with its version only where an exact one is wanted, so a channel installs once and
an exact version moves it, and what npm lists after is held to it (`guest/npm.py` reads the list; `guest/states.py`
reads it too, as the account, for a machine whose method is npm, since the probe reads the native launcher alone). The difference names the release installed and the
one wanted. `tests/unit/test_aw_claude_install.py` carries `test_signed_downloads.py`'s cases against the step, on F30's
stand-in machines and the fake download server over TLS with keys of the test's own: each method installing once, a
second run applying nothing, an exact version moving it, a manifest that does not verify, of another release or past
its cap, a binary not the manifest's never run (on this computer, and on the machine where the download changes between
the two calls), a launcher that resolves elsewhere refused and removed on every run, a launcher the step did not verify
installed over, the download gone whatever happened, `stable` never npm's version, npm not answering and a sudo that wants a password each a named failure, the
version `guest.states` reads after it by each method, the steps' runner, and a scan of the step's source: no channel's name in an
`install` argument and no URL of the installer script.

**Claude Code's settings and Remote Control.** Two steps follow it, today's `settings.yml`, `main.yml` and
`remote_control.yml` in plain code. `claude_settings.py`'s `claude-settings` merges the managed settings
(`claude_code_settings`, the vault's `anthropic_api_key` in its `env` block, natively the update channel where
`claude_code_version` is one) into `~/.claude/settings.json` as Ansible's recursive `combine` does, read and written as
the machine's account, its content on the call's standard input and renamed into place, mode 0600; an empty file is
`{}`, one that is no JSON object fails naming it, and a key an earlier run wrote that no setting manages now is
removed, with an `env` block it leaves empty. `claude_code_global_instructions` is `~/.claude/CLAUDE.md` as it is. The
key is on the run's redaction list before the file is read, and no value is an argument, an event or a difference: the
difference names the settings that change by name. `remote_control.py`'s `remote-control` decides once whether Remote
Control is wanted (`remote_control_wanted`, today's rule: `claude_code_remote_control` and no key, since a key outranks
the claude.ai login), the one definition `aw status` reads too. Not wanted, each unit installed is stopped, disabled
and removed, the path unit first, and a key that turned it off is said. Wanted, it reads the binary, `claude auth
status`, settings.json's blockers, `~/.claude.json`'s answers and both units, and is eligible only where the login is
claude.ai's with no `apiKeySource` of any kind and nothing in settings.json in the way. It answers the three prompts
where one is unanswered, installs the service rendered here from today's template with jinja2 alone (held byte for
byte to Ansible's template module, since a unit whose text changed is restarted), removes the path unit unless the
login is the one thing missing, and then starts the service and holds it to have stayed up, or installs and starts the
path unit (`PathChanged=` on the credentials file, never `PathExists=`), or leaves the service stopped; each outcome
emits its report and its NEXT STEPS records, and none starts a sign-in. systemctl runs as root through the machine's
sudo, and a value of the settings that is no single line is refused before a unit is made. The units' folder is a
test's own on a stand-in, whose systemctl keeps each unit's state where `guest.states` reads it and does what systemd's
path watch does on a tick of its own. `tests/unit/test_aw_claude_settings.py` carries the cases of
`test_remote_control_playbooks.py` and `test_remote_control_unit.py` against the steps: both directions converging, a
login landing starting the service through the path unit, a stopped service left stopped, and the path unit's
`PathChanged=` with a test of its own.

**gh's sign-in.** `lib/claude_vm/guest/layer2/github.py`'s step `github-auth` is today's `auth.yml` and
`auth_status.yml` in plain code, over the rows `github_auth`, `github_auth_method` and `github_login_scopes` and the
store's `github_token`; a method other than auto, web and token is refused before the machine is read, a GitHub App's
hourly token named, since nothing standalone renews one. Its probe reads, as the machine's account: whether gh is
there, `gh auth status --json` (a gh too old for it, before 2.81.0, a named failure where the run manages gh, never
"signed out"), the active account, and, wherever gh holds one, its check passed or not (state `error`), the token
gh holds piped on the machine into its own `python3`,
which prints its digest and the kind its prefix tells and never the token, so that the token never leaves the machine;
the marker `~/.config/agent-warehouse/gh-login`, the digest of a login the product made, `github_token`'s alone (a
device-flow login's marker is `gh-device-login`, which the step never reads or signs out by); and git's helpers for
github.com. `decide(found, wanted)` is auth.yml's decisions in one function, by digests alone, never by a token's
prefix: the token signs gh in where gh is signed out, signed in by the product, or the method is token, and a login
made by hand stays, the token only listing there; with the method taking no token, only the marked login is signed out,
by account, and the marker goes once gh holds no login; a login whose check failed is a login all the same, decided
by its digest and said as unchecked or refused, never "not signed in"; the marker is written wherever the login is the token's; gh is
made git's helper where git has no entry of gh's. The acts are fixed commands of the runner: `gh auth login
--with-token --insecure-storage` with the token on its standard input alone (a refusal fails quoting gh, but one for a
missing scope under auto, which keeps listing and leaves gh signed out, the scopes named), the logout, the marker
written on its command's standard input or removed, `gh auth setup-git`; the state is read again after a sign-in or a
sign-out, and nothing is ever revoked. The report is `status/lines.py`'s line with its NEXT STEPS record, but for a
token short of a scope, a token beside a login of the person's own and a sign-in that follows, auth.yml's own
sentences. The token joins the run's redaction list before anything is read, and no message, event, argument or
difference carries it. `tests/unit/test_aw_github_credential.py` carries `test_github_auth.py`'s cases of the sign-in
against `tests/molecule/fake_gh.sh` on stand-in machines, greps every event, file of the run and call of the stand-in
`ssh` for every canned token, and holds that no sign-out is decided from a token's prefix.

**The listing and the clones.** `lib/claude_vm/guest/layer2/clones.py`'s step `clones` is today's
`roles/github_projects/tasks/clone.yml` and the two modules it runs, `github_repos.py` and `git_clone_many.py`, in plain
code; the modules stay in the role until the cutover. The listing runs on the machine, where the token is: `LISTING`, a
program for the machine's `python3` of the standard library alone, started with the store's token on its standard input
where one is held, else with the token gh stored there, which `gh auth token` pipes into it on the machine, so that gh's
token never leaves the machine and this computer makes no call to GitHub with one. It asks GitHub's API as
`github_repos.py` did (the token's own account first; the account's every repository where the token is its, else its
public ones; a next link followed only to the API's own scheme and host; no redirect followed; a token over plain http to
loopback alone), and prints a line a repository, then the count, the account and whether a token listed. The selection
is in the engine: `filter_repos`, `github_repos.py`'s copied whole (a test hands both the same listings), then
`select`, the role's `vars/main.yml`: forks and archived ones out but where the machine's own list names them, empty
ones out, a name with a leading dot never cloned and named, the exclude list out, and in, the machine's own list where
its record keeps one (it replaces `github_projects`, and a name is only compared with the listing), else
`github_projects`, else every one. A private repository is cloned only where git has a credential for it, over ssh,
or over https with gh signed in and gh git's helper (made so by this run under `github_auth`, or by hand), never keyed
off `github_auth` alone; otherwise it is skipped and named. The git identity is read first, each key on its own, and
only under `github_auth`: a value of `git_user_name` or `git_user_email` is written; else the account's name and
GitHub's noreply address, where the key is unset, so one set by hand stays; an organisation gives none, which NEXT
STEPS' `github_identity` says. The checkouts are the helper's verb `clones`, run as the account: asked first to change
nothing, the probe's and the check's read, then to make each missing clone in a partial folder renamed into place once
whole, and under `github_projects_update_existing` to pull a checkout that was there by a fast-forward alone: only a
folder that is no link, is its own work tree's top, has the repository as its origin in its config (host and
owner/name compared in lower case, `.git` and the scheme's spelling aside, never as an `insteadOf` rewrites it), and is
on the default branch with no local change, fetched with no depth; any other is left as it is and said in the report.
Nothing removes a checkout.
Over ssh, GitHub's published SSH host keys (the API's `/meta`, which takes no token) are read on this computer by the
one transport, and the verb puts them in the account's `known_hosts`, replacing a stale line of github.com's. A
repository not cloned or a checkout git failed to pull fails the step, each named with git's last line, the rest
cloned. The
token joins the run's redaction list before anything is read (`github.wanted`). `tests/unit/test_aw_clones.py`
carries the cases of `test_github_repos.py`, `test_git_clone_many.py` and `test_vm_repos.py` against
`tests/molecule/fake_github_api.py`, local bare repositories and stand-in machines, holds that the copy chooses what
today's module chooses, and that a redirect is never followed with the token.

**A person's keys.** The keys a machine accepts are the person's own, `vm_ssh_public_keys`, and layer 1 alone adds them:
`common`'s keys tasks (`roles/common/tasks/keys.yml`) put each listed key on the account, every line of it made the
listed form, and leave every other line, so the list only adds and a key that is on a machine and not in the list stays.
They find a listed key where `sshd` reads a line's key, at its start or after one word of options
(`common_ssh_key_options`, a backslash escaping a double quote alone), so a line of another key whose comment or forced
command names a listed key is not its line. No task the system play reaches takes a key off `authorized_keys` or writes
the file whole: each task that names the file or `~/.ssh`, or a variable that does, in any of its keys (its arguments,
`vars`, `loop` and `with_` values, `when`), is a read, `~/.ssh` made a folder, a `lineinfile` that adds its line with no
pattern, or a `lineinfile` or `replace` whose pattern, its task's and its role's variables put in and each value a
template gives escaped, matches within one line, and which, rendered for a listed key as Ansible renders it, writes the
listed line and finds no line `sshd` reads as another key's (`tests/unit/test_aw_keys.py`, through the ratchet's walk).
And nothing of the engine sends Proxmox's `sshkeys` but the create's settings call (`create.settings_call`), since a
changed `sshkeys` makes a living VM's next start a new cloud-init instance; the same test holds it by a scan. Taking a
key off is an act of its own, never the side effect of two lists that differ: `aw deploy --only configure --remove-key
<fingerprint>`, whose `lib/claude_vm/guest/keys.py` `remove(job, target, fingerprint, ...)` reads the account's
`~/.ssh/authorized_keys` through the runner, finds each line whose key has that SHA256 fingerprint (read as `sshd` reads
it: options in front skipped, a quoted one whole, a backslash escaping a quote alone, an RSA key under `rsa-sha2-256` or
`rsa-sha2-512` its key, a line commented out no key's), and takes those lines off with `ansible.builtin.lineinfile`,
`state: absent`, in a one-task play of its own, `lib/claude_vm/guest/plays/remove_key.yml`, which layer 1's child runs
as the account, never as root (`layer1.PLAY_NAMES` holds its one variable, the key's pattern, given in the play's
variables file, which no template reads). The pattern is the key's type and base64 at the place `sshd` reads a key,
within one line, so a line of another key whose option or comment names this one is kept; `lineinfile` writes through a
linked `~/.ssh` and keeps the file's mode. The file is read again after, and must be what it was less those lines.
Refused with nothing changed: a key the list names, which the next configure would put back; a key this computer's ssh
offers the machine, which the run may be using; and a fingerprint on no line. What ssh offers is asked of ssh for each
machine (`runner.shown`, `ssh -G` with the machine's whole line: the product's options, `vm_ssh_options`, the person's
own `~/.ssh/config`): the agent its `identityagent` names (a path, `${VAR}` or ssh's older `$VAR`, else the run's
`SSH_AUTH_SOCK`), asked for its keys, and each `identityfile`'s public half; an identity file that is there whose public
half cannot be read, one named with a token `keys.py` does not expand, or a `pkcs11provider`, whose token's keys it
cannot list, refuses the key too. `deploy` refuses a listed key, and one the run's own environment offers, before the
run. A computer that is lost has the one procedure: its key out of `vm_ssh_public_keys`, then `--remove-key` from
another computer.

**How the engine's tests reach a machine.** A test of the new engine reaches no real machine: it reaches a stand-in,
a directory of the test's own that `tests/unit/aw_machine.py` makes, through the SSH runner (§16.6) as it reaches a
VM. `machine()` gives each an identity made for the test (`agent-warehouse-<a UUID>`), a made-up ed25519 host key, an
address from RFC 5737's documentation ranges, never a real network's, and a directory that holds its home, made for a
state as today's stand-in VMs make theirs (`tests/unit/test_status_vms.py`), the files the probe reads, `root/`, the
home a command run under its `sudo` is given, and a `bin/` of stand-ins found first on its `PATH`: today's
`systemctl`, `dpkg-query` and `getent`, the fakes' `gh`, `tailscale` and `claude`, a `python3` that is the test's own
Python, on every system (macOS's own is 3.9), and a `sudo` of its own. That `sudo` runs as the same user, with sudo's
`env_reset` and `secure_path` (`root/` its `HOME`, the machine's `PATH` whatever the caller's), takes `-l`, `-v`, `-k`,
`-K` and the long options and refuses a shell, records each call's `-n` and `-S`, and, made with a password, refuses
`-n` for want of one and on `-S` reads a line an attempt, each to its newline or carriage return: a wrong one is
followed by the next line, up to sudo's three, as experiment M-2 saw sudo do, so a caller that sends anything after the
password's line before sudo has answered loses it as an attempt. `ssh_stand_in()` gives the folder of an `ssh` that a
test puts first on its own `PATH` (`first_on_path`): it reads the runner's words as `ssh` reads them, refuses with 255
an address no machine has and words whose first `StrictHostKeyChecking` is neither `yes` nor `accept-new` or whose
first `BatchMode` is not `yes`, and checks the key of the machine at the address as `ssh` checks it, under the first
`HostKeyAlias` (or the address) in the files of the first `UserKnownHostsFile` (or `~/.ssh`'s two, `~` the test's own
home), never in this computer's own `~/.ssh`: the machine's key filed there passes; none filed is refused under `yes`
and filed under `accept-new`, as a first use files it; another filed is refused; each refusal in `ssh`'s words. So a
connection that names one machine's identity at another's address is refused, as the rule that a VM reporting a
sibling's address is refused rests on. `filed()` files a machine's key under its identity, in the file `target()`
checks unless given another. The stand-in records every call in that machine's `calls.log` (its words and the names of
its variables, never their values), and runs the command with `/bin/sh -c` in the machine's home, with no terminal (so
`/dev/tty` fails at once, as under `sshd`), with that home, its `bin/` first on `PATH` and the test's own failing
stand-ins next, and nothing else of the test's environment, its standard input passed through untouched; `-O check`
and `-O stop` answer as a live master would, and a forward is recorded and never opened. A `ProxyCommand` other than
`none`, the first given, is run as `ssh` runs one before the key is checked, its tokens expanded, and the connection goes
on only where it answers as an SSH server first does; `ssh -G`, which dials nothing, is answered by this computer's own
`ssh` by its path, given the test's own configuration (`-F` with `HOME`'s `.ssh/config`, or `/dev/null`, where the line
gives none), since OpenSSH finds `~` in the account database and would read the developer's. `hop()` makes a stand-in
Proxmox host the hop to the machines (16.4's routes), at one of RFC 5737's addresses or the fake Proxmox's own: its
`-O check` and `-O stop` answered from a file at the socket's path (live, or stale for a socket nothing listens at), its
master's open writing that file or refused as a login is, and a forward through it (`-W`) meeting, in the mode
`hop_modes()` sets, the host refusing to forward, `ssh` failing to use it, a machine that says nothing, a login that
never ends, or the machine's `sshd`'s first words. `planted()` makes a VM of the
Proxmox fake that machine: its guest agent reports the machine's address on the VM's own card, and its SMBIOS UUID is
the identity's. An adopted machine is a stand-in made without it. Layer 1 meets a stand-in only in a play of one task
over its test-only `local` connection (`layer1_host()`), which touches only the machine's directory, since the real
roles would run as root on the computer running the tests: they converge in a Molecule container.

What the module's own code writes is under the test's directory, but a machine is a directory, not a confinement: a
command on one can still write the computer's `/tmp`, reach a file by its absolute path and read its ancestors'
environment, and finds the system's programs past its `bin/` (on macOS BSD `tar`, and no `timeout`, `sha256sum` or
`flock`). So each card points the absolute paths and URLs of its steps at the machine's directory or at a fake, and a
command that needs GNU's tools brings a stand-in for each into the machine's `bin/`, or marks its macOS case with why.
No test of the engine writes an `ssh` stand-in of its own, or hands the runner one, but the SSH layer's own tests, the
guard's, and one that tests a child named `ssh`, which `tests/unit/test_aw_machine.py` names with why.

## 16.11 `control/` — what exists only with a control VM

`lib/claude_vm/control/` is where the add-on goes: everything that exists only with a control VM, its services and its
page.
The base, the command line alone, writes nothing here. It may import the rest of the new engine, and it is the one
subpackage that may import pydantic. Nothing outside it imports it.
