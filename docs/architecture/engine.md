# 16. The engine that replaces the playbooks at 3.0

Today the engine is the `justfile`'s recipes over bash over `ansible-playbook`. The one that replaces it at 3.0 is
being built beside it, in this repository: one program, `aw`, whose engine is plain Python in the command's own
process on this machine, with Ansible kept only for the system layer inside a machine. Until the cutover nothing a
person sees changes. Every command is still `just`'s, the playbooks, roles and recipes stay and keep passing their
tests, and the new code is reached through `tools/aw` alone. Verbs do not move to it one at a time: the two engines
record what a VM is and what the settings are in different ways, and a VM one of them made must never be changed by
the other. So the cutover moves every verb at once.

This page describes the new engine's code as it is in the repository, and grows with it. Each subpackage has a heading
below, and the change that builds a part writes how it works under that heading. The package's own rules, from
[§7.10](custom-code.md#710-libclaude_vm--the-controllers-python-as-one-package), hold here as everywhere in
`lib/claude_vm/`: importing a module loads none of `ansible`, `yaml`, `asyncio` or `subprocess`, and a package's
`__init__.py` imports nothing.

## 16.1 The rule between the two engines

The rule is one-way, and `tests/unit/test_aw_layout.py` holds both halves of it.

- **Nothing of today's product reaches the new engine.** No file of the `justfile`, `deploy.yml`, `playbooks/`,
  `roles/`, `filter_plugins/`, `callback_plugins/`, `inventory/` or `tools/` (but `tools/aw` itself), and no module of
  `lib/claude_vm/` outside the new subpackages, names `tools/aw` or imports a module of the new engine. A Python file is
  read as a syntax tree: its imports, the relative ones resolved, the strings it holds, and the module it hands to
  `plugin.filters`. Any other file is read as text. The test's allow list is empty, and only the cutover fills it.
- **The new engine reuses only what it names.** Of today's `lib/claude_vm/`, the new subpackages and `tools/aw` import
  only the modules and packages in the test's `REUSED`, each with the reason it is reused, at the top of a file or
  inside a function. `records` and `refs` are not among them, since the table of verbs and the printer of a command
  take their place; nor is `ansible`, today's engine of playbooks, nor the rest of `run`. A change that needs another
  module adds it to `REUSED`, with its reason, in the same commit. Never a module that imports Ansible when it is
  imported.

`tools/aw` is the program, on the model of `tools/mcp`. Run as `python -I`, it puts this checkout's `lib/` first on
`sys.path`, checks with `plugin.own` that the `claude_vm` imported is this checkout's, and runs
`lib/claude_vm/cli/main.py`'s `main` with its own path first and then the words typed. Until the parser and the
first verb are built, `main` answers `--version` with one line, `aw from the checkout <path>`. The path is worked
out from `tools/aw`'s own path and from nothing else, and a checkout has no version number of its own. Anything else
is refused in one sentence, with exit status 1, which says that the engine is not built yet and that the commands are
`just`'s. The sentence never repeats what was typed.

Beside the subpackages, `lib/claude_vm/secret.py` holds what the engine wraps a secret in as soon as it reads one: a
`Secret`, which `repr`, `str`, an f-string, `format` and a log line show as a mark, which refuses to be pickled or
turned into JSON, and whose plain value only `reveal()` reads, at the one place that sends it on.
`tests/unit/test_aw_guards.py` holds the modules that call `reveal()` to its `REVEALS`, each with the reason it sends
the secret on; the list is empty until the first one does. The engine's tests, `tests/unit/test_aw_*.py`, run alone
with `just test engine`, and reach `tools/aw` only through their kit
([§11.1](testing.md#111-unit--just-test-unit)).

## 16.2 `schema/` — the command line's patterns and its table of verbs and options

`lib/claude_vm/schema/` holds the command line as data. That is the patterns a typed value is held to, and the one
table of verbs and options that the parser, help, the reference page, the printer of a command and the agents'
schemas all read. They are literals written by hand, and nothing is generated from them into another file. It imports
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
with a newline after it, and holds that importing the module loads nothing outside the standard library.

## 16.3 `cli/` — the `aw` program

`lib/claude_vm/cli/` is the program. It reads the command line against the table of `schema/`, prints help,
dispatches a verb to its workflow and prints a command. `main.py` is the entry `tools/aw` runs, as 16.1 says. It
imports `schema/` and what it needs to answer at once. It imports `engine/`, and with it Ansible, only inside the
function that dispatches a verb that changes something, so a refusal, help or a read never loads them. It never imports
`control/` or pydantic. Only `tools/aw` imports it; the agents' server runs `aw` as a child.

## 16.4 `engine/` — the workflows, one module a verb

`lib/claude_vm/engine/` holds the workflows, one module a verb. Each says what a run of `deploy`, `destroy`, `login`,
`status`, `code` and the rest does, in the command's own process, in place of the playbooks. It imports the clients
(`pve/`, `ssh/` and `vendors/`), and `runs/`, `fleet/`, `guest/` and `schema/`. It never imports `cli/`, `control/`,
pydantic or today's `claude_vm.ansible`, and it runs Ansible only as a child process. `cli/` imports it, and only when
it dispatches a verb that changes something. No client imports it.

## 16.5 `pve/` — the typed Proxmox client and the one table of privileges

`lib/claude_vm/pve/` holds the typed client of the Proxmox VE API. It also holds the one table that names each call's
privilege, which `init` grants from and the permission check reads. It imports `vendors/` for its one HTTPS transport,
and `schema/`. It imports nothing of `engine/`, `cli/` or `control/`, and no pydantic.

## 16.6 `ssh/` — the runner over OpenSSH

`lib/claude_vm/ssh/` is the runner over OpenSSH. It runs `ssh` as a child under hard limits, takes a machine's host key
at first use and files it under the machine's identity, keeps one shared control directory, and holds the `ssh`
children a run holds. It imports `schema/`, and nothing of `engine/`, `cli/` or `control/`, and no pydantic.

## 16.7 `vendors/` — one HTTPS transport, Tailscale's calls, verified downloads

`lib/claude_vm/vendors/` is the vendors' side. It holds one HTTPS transport, which the Proxmox client uses too,
Tailscale's calls, and downloads held to their vendor's signature or digest. It imports `schema/`, and nothing of
`engine/`, `cli/` or `control/`, and no pydantic.

## 16.8 `runs/` — a run's record and text log, the locks, a detached run

`lib/claude_vm/runs/` is a run. It holds the run's one record and its text log, the kernel's file locks the run
holds, the run's end on a signal, and a run detached as the same program in a session of its own. It imports
`schema/` and `fleet/`, and nothing of `engine/`, `cli/` or `control/`, and no pydantic.

## 16.9 `fleet/` — the fleet's files, the settings, the secrets and the fleet's mark

`lib/claude_vm/fleet/` is a fleet's files and its state. It says where they are, and holds the hosts as folders, the
typed table of settings and its overrides, the one encrypted file of secrets, and the fleet's ID and mark. It imports
`schema/`, and nothing of `engine/`, `cli/` or `control/`, and no pydantic.

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
`AGENT_WAREHOUSE_HOME` the developer exported, read before the session replaces it. No file there is opened, so no
`fleet_home` is followed. `tests/unit/test_aw_fleet_paths.py` holds the homes, the pointer, the key's place, the names,
the scans for who names the folders and the key, and the guard, a child `pytest` among its tests.

## 16.10 `guest/` — the helper, the system layer's inputs, layer 2

`lib/claude_vm/guest/` is what the engine does inside a machine. It holds the helper, which is sent to the machine and
never installed there, and the inputs of the system layer, which Ansible converges as a child with built-in modules
only. It also holds layer 2, the rest of the work, in plain code. It imports `ssh/`, `schema/` and `fleet/`, and
nothing of `engine/`, `cli/` or `control/`, and no pydantic. A plugin of that Ansible child is the one code of the new
engine that imports `ansible`. It lives under `guest/ansible/`, where the child's own configuration names it.

## 16.11 `control/` — what exists only with a control VM

`lib/claude_vm/control/` is the add-on: everything that exists only with a control VM, its services and its page.
The base, the command line alone, writes nothing here. It may import the rest of the new engine, and it is the one
subpackage that may import pydantic. Nothing outside it imports it.
