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

## 16.2 `schema/` — the command line's patterns and its table of verbs and options

`lib/claude_vm/schema/` holds the command line as data. That is the patterns a typed value is held to, and the one
table of verbs and options that the parser, help, the reference page, the printer of a command and the agents'
schemas all read. They are literals written by hand, and nothing is generated from them into another file. It imports
the standard library and its own modules and nothing else, because every command reads it, a refusal and help among
them. Any part of the new engine may import it.

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
