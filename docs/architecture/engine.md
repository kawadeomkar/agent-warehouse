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

**The table: `words.py`.** `VERBS` is the one table of the command line: every verb a person types after `aw`, in
the order help lists them, today's thirteen and `release`, `cancel` and `control`. Each row is a `Verb`: its group
and its sentence; its `Words`, what the usage calls one, whether it takes several, the name the handler receives them
by, their pattern and help; its `Option`s; the codes of the rules between its options, as the contract of today's
command line names them (`deploy.4`, `check.names.1`), which the one check runs in that order; its handler, as
`module:function`, or none while no change has built the verb, and then the card that builds it; the locks a run of
it takes, one of `machines`, `fleet`, `none` or `words`, where the kind depends on the words given; whether a run of
it is a job, with its own record, text log and locks; and the tools of the agents' server that run it, or why none
does. An option has its spellings, its kind (`value`, `flag`, `repeatable` for `--set`, `counted` for `-v`,
`optional-value` for `--migrate`), the name of its pattern, the name the handler receives it by (the long option in
lowercase with `_` for `-`, as the agents' tools name it today, and `for_` for `host --for`), its help, the name help
gives its value (`--host ON`, as `just --usage` prints it today), and its fate for an agent's tool: a property of the
tool's schema, a value the server always gives, or refused with the reason; where two tools run one verb (`settings`
and `settings_set`), a fate for each. `BEFORE` holds the words given before any verb: `--no-lock`, `--attended`, and
`--detached-run`, which no person types. `--detach` is no row's option: the parser gives it, with the one sentence
`DETACH`, to every verb whose run is a job, and the agents' server starts every such run detached.

Today's verbs keep today's words and help, with `aw` where a sentence said `just`, and `mcp --vault-password-file`
is `--vault-key-file`. Beside the table, `EXITS` names the exit status of every verb but `status` (0 done, 1 refused
before anything ran, 130 cancelled) and `STATUS_EXITS` those of `status` (today's 0, 1, 2 and 3, and 130); within
each, no meaning has two numbers and no number two meanings. The table holds no option whose value is a secret: a
secret reaches a verb from a file it names or from the terminal. Nothing is generated from the table or into it, and
it has no column for the migration: which words are new and which of today's it changes, each with its ruling, is
`tests/unit/cli_contract_changes.py`'s, beside the contract (`tests/unit/cli_contract.json`), and goes with it at the
cutover. `tests/unit/test_aw_cli_words.py` holds the table's rows, today's fates for today's words (against
`lib/claude_vm/mcp/recipes.py`, until the cutover), the exit statuses and the module's imports;
`tests/unit/test_aw_cli_contract_words.py` holds every word of the contract to the table or to the two maps.

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
words given before any verb the same way.

**The rules between options: `rules.py`.** Each rule between options of today's command line is one plain function
with the code the contract of today's command line gives it (`deploy.4`, `check.names.1`) and today's sentence, word
for word, with `aw` where it said `just`: `Rule(code, verbs, sentence, breaks)`, where `breaks` takes a verb's values
and returns nothing where they keep the rule, or what fills the sentence's values where they break it, and
`Rule.check` gives nothing or the sentence. They are today's rules of `deploy`, `code`, `settings`, `destroy`, `host`,
`help`, `init` and `mcp`, said before any check or tool, and `tools/check`'s argument checks of VM names, the size, the
repositories, the project, the session and status's sections; `RULES` holds them by code, and `run` runs the codes a row
names in order and stops at the first sentence. A sentence says a value back only where today's does, and then a value
that has met its pattern first, as `just` holds a value to its pattern before a recipe's script runs; some of
`tools/check`'s checks, as today's, refuse nothing their pattern has not refused first. What reads more than the values
is no rule here but its verb's own: the fleet's files (`vault`'s, and `deploy`'s VM name another host's alias
records), the file system, `claude` and the branch (`mcp`'s), and a terminal (`host --remove`'s question).

## 16.3 `cli/` — the `aw` program

`lib/claude_vm/cli/` is the program: reading the command line against the table of `schema/` and the dispatch of a
verb to its workflow, below, and help and the printer of a command, which go here when they are built. `main.py` is
the entry `tools/aw` runs, as 16.1 says.
It imports `schema/` and what it needs to answer at once. It imports `engine/` only inside the function that dispatches
a verb that changes something, so a refusal, help or a read never loads it. It never imports Ansible, which the engine
only ever runs as a child, nor `control/` or pydantic. `tools/aw` imports it, and so does the agents' server's own entry
once it is built; that server runs `aw` as a child for every run.

**Reading the words: `parse.py`.** `read(words)` takes the words typed after `aw`: first the words before the verb
(`--no-lock`, `--attended`, the hidden `--detached-run`), then the verb, then its words and options, split by
`argparse` into the table's slots for the one verb typed only. No verb is `help`, as `just` alone lists the recipes.
`argparse` is set as the design requires: abbreviations off, so a prefix of an option is an option the verb has not
got; its own help off; its errors raised rather than printed and exited on; and its colours off from Python 3.14. An
option is taken as `--long value` and as `--long=value`, which is how a value that starts with `-` is given, and is
given once, but for `--set`, given once for each setting, and `-v`, counted. Every verb whose run is a job gets
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
cannot. The answer meets the rules as if typed, the one check run again on what it comes to; with no terminal, and for
any answer but the stage or the VMs, it is refused in today's sentences, and a Ctrl-C there exits 130.

**The run's own words: `before.py`.** `RunWords` is one frozen record every handler receives first: `-v`'s count and
the `--set` words as typed, from after the verb; `--detach`; `--no-lock`, the one word for a run that takes no lock,
at the person's own risk; `--attended=1`, the agents' server's word that someone is at the screen with no terminal;
`--detached-run`; and `LOG_DIR` and `LOG_RETENTION_DAYS`, read from the environment as today. Nothing else is read from
the environment: an exported `RUN_LOCK` or `ATTENDED` changes nothing.

**The dispatch: `dispatch.py`.** The handler the verb's row names, `module:function` under `claude_vm`, is imported
only when that verb runs and called with the run's own words and then the verb's values as keyword arguments; what it
returns is the exit status. Before it, in this order: an environment that lacks the packages the engine needs is
refused, naming `aw install`, for every verb but `install` and `help`; a verb no card has built yet is refused naming
the card that builds it and, where today's `justfile` has a recipe of that name, `just <verb>`, which does its work
until the cutover; and a verb whose run is a job, but `init` and `install`, which come before any fleet, is refused
where the fleet's files do not exist, naming `aw init`, and says on standard error which fleet it drives where
`AGENT_WAREHOUSE_HOME` or a `fleet_home` line chose it. A refusal is one line on standard error and exit status 1.

**The launcher: `aw`.** At the checkout's top, in POSIX `sh`, the launcher finds its checkout from its own path, every
link to it followed, and never from the working directory, so no `justfile` or checkout found upward runs in its
place, and the checkout guard of today's recipes is not needed. It asks `tools/env-run --which` which environment the
checkout runs, a virtualenv of the person's own or the checkout's own, and runs `tools/aw` with that Python under
`-I`. `install`'s one home is its function `install_env`: what `just install` does to the environment, a sync of a
virtualenv of the person's own with `--inexact` and no download after `tools/stale_scripts.py`'s reinstall, or of the
checkout's own exactly the lock's on the Python version `managed_python` names, which the `justfile`'s holds equal;
then git's pre-commit hook, as `just init` puts it, but nowhere where `core.hooksPath` is set. It installs no Galaxy
collection and moves nothing of 1.x. It runs in three cases and no other: by itself, once, where the checkout's own
environment does not exist yet, saying so on standard error before it runs the program; for `aw install --lock`, which
writes `uv.lock` and needs no environment; and for its one word of its own, `--install-env`, which `aw install`'s run
passes it as a child carrying the job's marker, `AGENT_WAREHOUSE_JOB`, and which is refused, naming `aw install`,
without it. It never installs into a virtualenv of the person's own unasked: the program names `aw install` where one
lacks the packages.

`tests/unit/test_aw_cli_parse.py` holds all of it: every option of every verb in both spellings, the refusals and that
none says a value or has a usage block, `--detach`, `default_vm`, `RunWords`, the dispatch's refusals, what a refusal
imports, and the launcher run in checkouts the test makes, with a stand-in `uv`. `tests/unit/test_aw_cli_rules.py`
holds the rules and `deploy`'s question: every rule of the contract (`tests/unit/cli_contract.json`) is a rule or a
habit with today's sentence, or named as another verb's own; each of the contract's cases of a rule or a habit is run
against `aw` and gives today's status and sentence; the list of those not yet run only shrinks; the check answers
alike as words and as JSON; the question is asked at a pseudo-terminal, typed and with none; and each test of today's
options' rules in `tests/unit/test_justfile.py` has a twin there by its name.

## 16.4 `engine/` — the workflows, one module a verb

`lib/claude_vm/engine/` is where the workflows go, one module a verb. Each says what a run of `deploy`, `destroy`,
`login`, `status`, `code` and the rest does, in the command's own process, in place of the playbooks. It imports the
clients (`pve/`, `ssh/` and `vendors/`), and `runs/`, `fleet/`, `guest/` and `schema/`. It never imports `cli/`,
`control/`, pydantic or today's `claude_vm.ansible`, and it runs Ansible only as a child process. `cli/` imports it,
and only when it dispatches a verb that changes something. No client imports it.

## 16.5 `pve/` — the typed Proxmox client and the one table of privileges

`lib/claude_vm/pve/` is where the typed client of the Proxmox VE API goes, with the one table that names each call's
privilege, which `init` grants from and the permission check reads. It imports `vendors/` for its one HTTPS transport,
and `schema/`. It imports nothing of `engine/`, `cli/` or `control/`, and no pydantic.

## 16.6 `ssh/` — the runner over OpenSSH

`lib/claude_vm/ssh/` is where the runner over OpenSSH goes. It runs `ssh` as a child under hard limits, takes a
machine's host key at first use and files it under the machine's identity, keeps one shared control directory, and holds
the `ssh` children a run holds. It imports `schema/`, and nothing of `engine/`, `cli/` or `control/`, and no pydantic.

## 16.7 `vendors/` — one HTTPS transport, Tailscale's calls, verified downloads

`lib/claude_vm/vendors/` is where the vendors' side goes: one HTTPS transport, which the Proxmox client uses too,
the vendors' one certificate file, Tailscale's calls, and downloads held to their vendor's signature or digest. It
imports `schema/`, `secret.py` and, of today's modules, what `REUSED` names (`text`, to make what a peer said one
line); of other packages, `certifi`, in `trust.py` alone. It imports nothing of `engine/`, `cli/` or `control/`, and no
pydantic.

**One transport: `transport.py`.** Every HTTPS request the engine sends goes through
`Transport(base_url, context, user_agent, connect=5, read=30, most=16 MiB, proxy=None, peer_check=None)`, and its
`request(method, path, headers, query, form, json, stream, deadline)` returns an `Answer(status, headers, body)`. It
is written over `http.client` and `ssl` alone, so three things hold by construction. It follows no redirect: a 3xx is
an answer. It reads no certificate variable, no netrc and no proxy variable, because the caller builds the
`ssl.SSLContext` its connections verify with and gives it the proxy too. And it takes an `https://` address only. It
builds no context and reads no setting.

- **The peer's certificate.** `peer_check`, a function of the peer's certificate as DER, is called after each new
  connection's handshake and before anything is written on it. Its raising ends the call as `PeerRefused`, with
  nothing sent. This is the one way into the connection that the Proxmox client's pin uses.
- **A proxy** is an `http://host:port` URL or none, with a user and password where the URL holds them, sent as the
  CONNECT's Basic credentials to the proxy alone. The connection is a CONNECT tunnel (`http.client`'s `set_tunnel`),
  so TLS and the peer's check are end to end with the server, and the proxy sees the server's name and port and
  nothing else. Another scheme, `socks5://` or `https://`, is refused by name, as `requests` refuses SOCKS today
  without its extra package. No message quotes the URL, which may hold a password.
- **Bounded.** An answer's body is held to `most` bytes and read no further (`TooLarge`). Every wait is held to
  `connect` or `read` seconds. A whole call, its tries and their pauses included, is held to `deadline` (120 s where
  none is given), which every read and every send of the connection obeys, so a server that drips its answer is cut
  off too.
- **Sent once.** A GET or HEAD is tried again, at most three times in all, after 0.5 s and then 1 s, when its
  connection could not be made or was dropped, or when the answer is 502, 503 or 504. Any other method is sent once.
  A failure once its request has left is `Unknown`, never a second send, and its caller reads the state to know.
  Before a request is written on a kept connection, one whose far end has closed it is put aside, which costs no try.
- **A secret is never said.** A header's value may be a `Secret`, revealed in `_put` and nowhere else. Every header
  is held to what `http.client` sends as it is (a name that is an HTTP token, a value of visible ASCII) before a byte of
  the request is made, and anything else is `NotMade`, "the request could not be made", because `http.client`'s own
  words quote the header. A `Secret` in a query or a form is refused, since it would be sent as its mark. Each error
  (`Unreachable`, `TimedOut`, `TooLarge`, `Unknown`, `PeerRefused`, `NotMade`) is raised after the handler that caught
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
`tests/unit/pki.py` makes, and against a stand-in CONNECT proxy. It holds four rules over the new engine's modules.
Only `transport.py` imports `http.client` or wraps a socket. Only `proxy.py` imports `urllib.request`, and only for
those readers. No module names a certificate variable. And `reveal(` appears once in the transport. It also holds the
modules that hand the transport a `peer_check` to a list, `PEER_CHECKS`, each with why.

**The vendors' trust: `trust.py`.** GitHub, Tailscale and the download servers are verified against one file of PEM
certificates, named by its path: `certifi`'s bundle, the same on every system, unless the computer's setting
`tls_ca_file` names another. `ca_file(setting)` gives that path, `certifi`'s where the setting is unset or empty, and
`context(path)` makes the context the transport is given. That bundle is what today's `requests` verifies against
where no variable names another, so the vendors are trusted as today; a root a person added to the system's own store
and not to `certifi`'s is named with `tls_ca_file`, the opt-in.

- **That file, and nothing else.** The context is made by hand, a client context with the file loaded by
  `load_verify_locations`, and never the standard library's ready-made one, which loads the interpreter's own store
  where it is given no file (a store the environment's variables change) and reads the environment for a file to log
  the session's keys to. Nothing of the environment is read, macOS's keychain is never asked, and there is no table of
  each system's paths.
- **Checked as Python 3.13 checks.** The server's name is checked and a certificate required. The flags that Python
  3.13's own context, and today's `requests` on it, set are set on every Python: strict checking, and a chain that may
  end at an intermediate the file holds. So an interpreter before 3.13 is no looser, and a CA with no keyUsage, as
  Proxmox VE 8 makes one, does not verify.
- **A file that cannot be used** (not there, not readable, or holding no certificate, such as a revocation list alone)
  is `Unusable`, whose sentence names `tls_ca_file`; so is a missing `certifi` where no setting names a file.
- **The proxy is not this module's.** `proxy.choose` gives it to the transport beside this context.

`tests/unit/test_aw_vendor_trust.py` reaches TLS servers on loopback through the transport with such a context. It
puts a server's root in the interpreter's own store to show that the context still refuses that server, and sets every
certificate variable to a trap to show that neither the file chosen nor what it loads changes. It holds two rules: no
module of `vendors/` names the standard library's ways to the interpreter's store (`create_default_context`,
`load_default_certs`, `set_default_verify_paths`), and of the new engine only `trust.py` imports `certifi`.

## 16.8 `runs/` — a run's record and text log, the locks, a detached run

`lib/claude_vm/runs/` is where a run goes: its one record and its text log, the kernel's file locks the run holds, the
run's end on a signal, and a run detached as the same program in a session of its own. It imports
`schema/` and `fleet/`, and nothing of `engine/`, `cli/` or `control/`, and no pydantic.

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
`store.locked`, so the run and another process recording a stop lose neither change; a name `FIELDS` lacks, and `id`,
are refused. The record has no schema of its own and no event file beside it: `events.jsonl` is the add-on's.

**The states are derived, never stored.** `state(job)` reads `running` while the job's lock is held, which a
`LOCK_SH | LOCK_NB` attempt on the file finds (let go at once where it was taken), and `cancelling` while it is held
and a stop is recorded; the outcome where the record has one; and `lost` where it has none and the lock is free: a run
killed outright, or one that ended without saying how. It asks the lock before it reads the record: a run writes its
outcome before it ends, so a lock found free means any outcome is already there. A job the reading process made
itself is read from what it holds and never probed: on NFS, Linux emulates `flock` with POSIX locks, which a process
holds once per file, so closing a probe's descriptor would let the run's own lock go. No module of `runs/` keeps a pid
to decide a state, reads a process's start or reads the process table for one, which a scan in the test holds.

**Events.** `emit(kind, machine, text, **data)` hands one event, with its time, to each listener the run registered
with `listen` (the live view and the text log), in order and in the run's own process. The kinds are `stage`, `step`,
`report`, `waiting`, `next`, `child` and `end`; another is refused. Every string an event carries goes through
`text.plain_text`, and a `Secret` anywhere in one is refused before anything is written or heard. What an agent and
`logs` read is also kept in the record: a `waiting` (empty text: nothing), a `next` with its `matter` (an empty line
removes it), a machine's `end` with its outcome and the stage its last `stage` event named, and the run's `end` (no
machine) with its outcome and exit status. Only the process that made a job emits its events, listens to them or drops
it.

**Reading and dropping.** `find(jobs_dir, job_id)` takes only what the pattern `job_id` holds, sixteen hex digits,
and `listed(jobs_dir)` gives every job newest first. Each passes over a folder whose `job.json` is not there yet, or is gone, and a job that vanishes
while it is read is never an error. A run refused at its locks leaves no job: `drop(job)` removes `job.json` first, so
readers pass over the folder, then closes the lock, then removes the folder. Removed while the lock file is still open,
the file would be renamed `.nfsXXXX` on NFS and the folder would stay. `tests/unit/test_aw_job.py` holds it all, each
state made by a run in a child process: a SIGKILL reads `lost` with nothing written after it, and a child that outlives
its killed run holds no lock.

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
