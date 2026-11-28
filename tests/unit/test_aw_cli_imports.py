"""The quick commands import nothing heavy (docs/architecture/engine.md §16.3).

A quick command answers at once: help, a refusal, and the reads, logs, cancel, host, settings and status. QUICK names
each command line, what it may import beyond the standard library and claude_vm - nothing, a YAML reader, or a YAML
reader and pyrage, for a read that opens the vault, one file encrypted whole - and the exit status it ends with. A line
runs as a person runs it, through the launcher at the top of a checkout the test makes, with a fleet folder of the
test's own (AGENT_WAREHOUSE_HOME), and fails where any Python of the run loaded any of HEAVY - pydantic, pydantic_core,
Ansible, Jinja2, the MCP SDK, or claude_vm.engine, the workflows - or anything beyond the standard library and claude_vm
that its line does not give it, pyrage among them. What a line may import is what importing the packages its row names
loads, worked out in the suite's own Python (`adds`): PyYAML's C extension brings Cython's runtime modules with it,
whose names move with the wheel. A line whose verb has no handler yet is skipped by name, its card named: it runs once
that card fills the handler. A refusal is held to say its one sentence, so that what it loads once it is said is
counted too.

A read is held to its real work, not to its first check: `_fleet` makes the fleet a read finishes against - two hosts,
lab the current one, each with the settings it sets for itself; a setting of the fleet's; the vault, with its key,
holding each host's token; a job that has ended, for logs, and one that runs, for cancel - and a read's line is held to
its exit status as well as to having loaded its handler's module. A made-up handler for each read, which reads what its
read reads by the engine's own readers, refuses at its first check where the fleet lacks it, and loads Jinja2 past it,
fails its line against that fleet by Jinja2 and against a fleet of its record alone by its exit status. The card that
fills a read's handler gives `_fleet` what its read reads beyond this (a run's text log, an API to reach: the hosts are
on loopback at port 1, which no test can bind, so a read that reaches for one is refused at once, never kept waiting on
the network).

What a run loaded is read from sys.modules at the exit of each Python it ran, never from python's -X importtime, which
lists no module loaded through importlib.import_module, as the dispatch loads a verb's handler. A hook of the test's
own writes it: the checkout's PYTHON is a virtualenv the test makes, whose site-packages hold the suite's own packages,
added as site adds them, and one module, imported by a .pth line that site runs at the interpreter's start, -I and all.
It records every process of that Python - the program, the launcher's own questions to it, and any Python the program
starts, as today's status starts one for each host - each with what its program's start had loaded, taken at the
interpreter's audit event for that start (cpython.run_file, run_command, run_module), once site has run every .pth file
and a sitecustomize, which the virtualenv has, as Ubuntu's and Homebrew's Pythons have one; and sys.modules at its exit.
What a start loaded is the interpreter's, not the command's: a heavy module there fails the line all the same, and the
rest of it is no import of the command's. A process whose start is recorded and whose exit is not (os._exit, a signal,
an exec) fails its line: what it loaded is not known.
"""

from __future__ import annotations

import ast
import contextlib
import functools
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import NamedTuple

import harness
import pytest
from aw_kit import ENTRY, LAUNCHER, run_launcher
from fleet_fixture import pinned
from harness import REPO

from claude_vm.fleet import hosts, overrides, paths, record, sealed, vaultkey
from claude_vm.schema.words import EXITS, STATUS_EXITS, VERBS, Verb
from claude_vm.secret import Secret


class Quick(NamedTuple):
    """A quick command line: the words after aw; what it may import beyond the standard library and claude_vm, by the
    packages it may import; whether it is a refusal, which runs whatever the table's handlers, where any other line
    waits for its verb's; and the exit status any line but a refusal ends with, against the fleet _fleet makes."""

    words: tuple[str, ...]
    may: frozenset[str] = frozenset()
    refusal: bool = False
    exit: int = EXITS["done"]


# The YAML reader, which parses the fleet's files: its hosts', its settings', the overrides'.
READER = "yaml"
# What opens the vault, one file encrypted whole: settings --all, to say which secrets are set; status, for the token.
VAULT = "pyrage"
# A job's ID, as logs lists one, and a host's name, each as its pattern takes it. A line runs with JOB as the ID of the
# job _fleet made for it.
JOB = "3f9a0c1e7b2d4a58"
HOST = "lab2"
# The reads, each a verb whose handler a card of its own fills.
READS = ("logs", "cancel", "host", "settings", "status")

# Each quick command line, in the card's order (H11), what it may import beyond the standard library and claude_vm, and
# the exit status a read ends with where it is not done.
QUICK: tuple[Quick, ...] = (
    # aw alone, which is help, and help of one verb.
    Quick(()),
    Quick(("help",)),
    Quick(("help", "deploy")),
    # The checkout aw runs from: argparse, which main.py imports at its top, is the standard library's.
    Quick(("--version",)),
    # A refusal: an option the verb has not got, a value outside its pattern, a verb the table has not got, a rule's
    # sentence of help's, a rule between options, and a habit's, a second name where one is taken.
    Quick(("deploy", "--core", "8"), refusal=True),
    Quick(("deploy", "--cores=0"), refusal=True),
    Quick(("bogus",), refusal=True),
    Quick(("help", "deploy", "login"), refusal=True),
    Quick(("deploy", "--only=provision", "--tags=common"), refusal=True),
    Quick(("code", "alpha", "beta"), refusal=True),
    # The jobs: a text file read, or tailed until its job ends; and the one job that runs stopped, or that one.
    Quick(("logs",)),
    Quick(("logs", JOB)),
    Quick(("logs", JOB, "--follow")),
    Quick(("cancel",)),
    Quick(("cancel", JOB)),
    # The hosts, each a folder of files a YAML reader parses.
    Quick(("host",), frozenset({READER})),
    Quick(("host", "--json", "--for", "alpha"), frozenset({READER})),
    Quick(("host", HOST), frozenset({READER})),
    # The settings: a group, a name, what changed; and every setting, which says which secrets the vault holds.
    Quick(("settings",), frozenset({READER})),
    Quick(("settings", "tailscale"), frozenset({READER})),
    Quick(("settings", "vm_cores"), frozenset({READER})),
    Quick(("settings", "--changed"), frozenset({READER})),
    Quick(("settings", "--all"), frozenset({READER, VAULT})),
    # status, which opens the vault for the fleet's one token, and ends with its report's code.
    Quick(("status", "--only", "vms"), frozenset({READER, VAULT}), exit=STATUS_EXITS["nothing_wrong"]),
    Quick(("status",), frozenset({READER, VAULT}), exit=STATUS_EXITS["nothing_wrong"]),
)

# What no quick command loads, each a module and everything under it: pydantic and its core, Ansible, Jinja2, the MCP
# SDK, and the engine's workflows.
HEAVY = ("pydantic", "pydantic_core", "ansible", "jinja2", "mcp", "claude_vm.engine")
# The hook's module, which the virtualenv's .pth imports at the interpreter's start, and has watch the records' folder.
HOOK_MODULE = "aw_imports_hook"
HOOK = '''"""What each Python of a run loaded, one record a process: its argv and what its program's start had loaded,
once site had run every .pth file and sitecustomize; then, at its exit, sys.modules. A process that ends without its
exit (os._exit, a signal, an exec) leaves its start alone."""
import atexit
import os
import sys

# The events the interpreter raises as its program starts, once site is done: a script, -c, -m, standard input.
STARTS = ("cpython.run_file", "cpython.run_command", "cpython.run_module", "cpython.run_stdin")
# Until the program starts, its start is what this module's import found: a process none of STARTS reaches keeps it.
_record = {"folder": None, "pid": None, "path": None, "start": sorted(sys.modules)}


def _note(mode, data):
    with open(_record["path"], mode) as out:
        out.write(repr(data) + "\\n")


def _begin():
    _record["pid"] = os.getpid()
    _record["path"] = os.path.join(_record["folder"], "%d-%s.record" % (os.getpid(), os.urandom(4).hex()))
    _note("x", {"argv": list(sys.argv), "start": _record["start"]})


def _audit(event, args):
    if event in STARTS and _record["pid"] is None:
        _record["start"] = sorted(sys.modules)
        _begin()


def _end():
    # A fork of a process recorded, or a process whose program never started, has no record of its own yet.
    if _record["pid"] != os.getpid():
        _begin()
    _note("a", {"exit": sorted(sys.modules)})


def watch(folder):
    _record["folder"] = folder
    sys.addaudithook(_audit)
    atexit.register(_end)
'''
# The virtualenv's sitecustomize, which imports a module of its own, as Ubuntu's imports apport's hook: site imports it
# after every .pth file, so the start the hook takes is the program's, never where the hook is imported.
SITE_OWN = "aw_site_own"
# A fleet's ID, as fleet.json holds one.
FLEET = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
# The fleet's hosts and their addresses, the first the current host. Each is on loopback at port 1, a privileged port
# no test can bind and nothing answers on: a read that reaches for a host's API is refused at once, never kept waiting
# on the network, until the card that fills it gives _fleet a fake to reach.
HOSTS = {"lab": "127.0.0.1", HOST: "127.0.0.2"}
CLOSED_PORT = 1
TOKEN = {"token_user": "aw@pve", "token_id": "aw"}
# Every setting each host sets for itself but its node, as init writes them, and one of the fleet's, which a read of
# what the files change finds.
ALWAYS = {"proxmox_storage": "local-lvm", "proxmox_bridge": "vmbr0"}
FLEET_SETTING = ("vm_cores", 4)
# Each host's token in the vault: made up, a UUID as Proxmox makes a token's secret.
TOKENS = {"lab": "11111111-1111-4111-8111-111111111111", HOST: "22222222-2222-4222-8222-222222222222"}
# A job made by a Python of its own, never by the test's process, which would hold its lock for the test's life: one
# that has ended, for logs; and one that runs in a session of its own until the Ctrl-C cancel sends it, for cancel.
JOB_CODE = """import signal, sys
sys.path.insert(0, sys.argv[1])
from claude_vm.runs import job
running = sys.argv[3] == "running"
signal.signal(signal.SIGINT, signal.default_int_handler)
starter = {"kind": "detach" if running else "terminal"}
made = job.create(sys.argv[2], "deploy", "aw deploy alpha", "lab", ["alpha"], starter, not running)
if not running:
    made.emit("end", None, "deployed", outcome="succeeded", exit=0)
print(made.id, flush=True)
try:
    while running:
        signal.pause()
except KeyboardInterrupt:
    made.emit("end", None, "cancelled", outcome="cancelled", exit=130)
"""
JOB_ID = re.compile(r"[0-9a-f]{16}")
# The standard library's own data of sysconfig, a module whose name says the platform it was built for, which
# sys.stdlib_module_names leaves out.
SYSCONFIG_DATA = "_sysconfigdata_"

# Handlers the made-up checkout gives the reads' rows: words.py's VERBS, appended to a copy of it.
MADE_UP_ROWS = """

# Made up by tests/unit/test_aw_cli_imports.py, in a checkout of its own: the reads' rows given handlers.
import dataclasses as _made_up

for _name, _handler in {handlers!r}.items():
    VERBS[_name] = _made_up.replace(VERBS[_name], handler=_handler)
"""
MADE_UP = '''"""Handlers tests/unit/test_aw_cli_imports.py makes up for the reads, in a checkout of its own.

Each of logs, cancel, host, settings and status reads what its read reads, by the engine's own readers, refuses at its
first check where the fleet lacks it, as a read does, and past it loads Jinja2, which a read's work must never load.
child starts a Python of its own that loads Jinja2, and cut one that ends through os._exit.
"""

import subprocess
import sys
from pathlib import Path


def _home():
    from claude_vm.fleet import paths

    return paths.home(Path(sys.argv[0]).resolve().parent.parent)


def _past(check):
    """The read's work, past its first check: where the check refuses, the refusal said and the work never reached."""
    try:
        refusal = check()
    except Exception as error:
        refusal = f"{type(error).__name__}: {error}"
    if refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return 1
    import jinja2  # noqa: F401

    return 0


def _reached(**asked):
    from claude_vm.fleet import hosts
    from claude_vm.hosts.model import Request

    return hosts.reach(hosts.Layout(_home()), Request(**asked))


def _token(host):
    from claude_vm.fleet import sealed, secrets, vaultkey

    key = vaultkey.open(_home(), key_file=None, key_command=())
    entry = secrets.entry("token", host=host)
    return None if entry in (sealed.read(_home(), key) or {}) else f"the vault holds no {entry}"


def logs(run, **values):
    def check():
        from claude_vm.fleet import paths
        from claude_vm.runs import job

        jobs = paths.jobs_dir(_home())
        if values.get("job_id") is None:
            return None if job.listed(jobs) else "no job to list"
        found = job.find(jobs, values["job_id"])
        if found is None:
            return "no such job"
        if values.get("follow") and job.state(found) not in job.OUTCOMES:
            return "the job has not ended, so following it would not end"
        return None

    return _past(check)


def cancel(run, **values):
    def check():
        from claude_vm.fleet import paths
        from claude_vm.runs import job

        running = [each.id for each in job.listed(paths.jobs_dir(_home())) if job.state(each) == "running"]
        if values.get("job_id") is not None:
            return None if values["job_id"] in running else "that job does not run"
        return None if len(running) == 1 else f"{len(running)} jobs run, where one would be stopped"

    return _past(check)


def host(run, **values):
    def check():
        from claude_vm.fleet import hosts

        _reached()
        if values.get("current_host") is not None:
            hosts.Layout(_home()).named(values["current_host"], "the host to make current is")
        return None

    return _past(check)


def settings(run, **values):
    def check():
        from claude_vm.fleet import overrides

        reached = _reached(lenient=True)
        if not overrides.read(_home(), reached.host.name).fleet.settings:
            return "the fleet's settings.yml sets nothing"
        return _token(reached.host.name) if values.get("all") else None

    return _past(check)


def status(run, **values):
    return _past(lambda: _token(_reached(read_only=True).host.name))


def child(run, **values):
    subprocess.run([sys.executable, "-c", "import jinja2"], check=True)
    return 0


def cut(run, **values):
    subprocess.run([sys.executable, "-c", "import os; os._exit(0)"], check=True)
    return 0
'''


class Process(NamedTuple):
    """One Python of a run, as the hook recorded it: its argv, what its program's start had loaded, and what it had
    loaded at its exit, None where it ended before its exit was recorded (os._exit, a signal, an exec)."""

    argv: tuple[str, ...]
    start: frozenset[str]
    exit: frozenset[str] | None


class Run(NamedTuple):
    """A line run: what it ended with and said, each Python of it as the hook recorded it, the checkout's tools/aw, and
    the handlers the checkout gave the table's rows over its own."""

    ran: subprocess.CompletedProcess[str]
    processes: tuple[Process, ...]
    program: Path
    handlers: Mapping[str, str]


class Fault(NamedTuple):
    """What a line's run did that its row does not allow: its kind, and what was found."""

    kind: str
    text: str


def _id(quick: Quick) -> str:
    return " ".join(quick.words) or "(no words)"


def _verb(words: tuple[str, ...]) -> Verb | None:
    """The verb a line runs: help for no words, none for a word the table has not got (--version, a refusal's)."""
    return VERBS["help"] if not words else VERBS.get(words[0])


def _top(name: str) -> str:
    return name.split(".")[0]


def heavy(loaded: frozenset[str]) -> list[str]:
    """The modules of loaded that HEAVY names, or that are under one it names."""
    return sorted(name for name in loaded if any(name == top or name.startswith(f"{top}.") for top in HEAVY))


def beyond(loaded: frozenset[str], start: frozenset[str]) -> set[str]:
    """The top-level packages of what a run loaded past its interpreter's start, beyond the standard library and
    claude_vm."""
    tops = {_top(name) for name in loaded - start} - set(sys.stdlib_module_names) - {"claude_vm"}
    return {top for top in tops if not top.startswith(SYSCONFIG_DATA)}


@functools.cache
def adds(package: str) -> frozenset[str]:
    """What importing package loads beyond the standard library and claude_vm, by its top-level names, worked out once
    in the suite's own Python: the package, and what it brings, as PyYAML built with libyaml brings Cython's runtime
    modules (cython_runtime, _cython_<its version>)."""
    assert package.isidentifier(), package
    code = f"import sys; start = sorted(sys.modules); import {package}; print(repr((start, sorted(sys.modules))))"
    done = subprocess.run(
        [sys.executable, "-I", "-c", code], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    start, loaded = ast.literal_eval(done.stdout)
    found = beyond(frozenset(loaded), frozenset(start))
    assert package in found, f"importing {package} loaded no module of its name: {sorted(found)}"
    return frozenset(found)


def allowance(may: frozenset[str]) -> frozenset[str]:
    """What a line whose row gives it the packages may loads beyond the standard library and claude_vm."""
    return frozenset().union(*(adds(package) for package in may))


def _overlaid(lib: Path, handlers: Mapping[str, str]) -> None:
    """lib for a made-up checkout: this checkout's claude_vm, each file and folder by a link, but for a copy of the
    table that gives the rows of handlers made-up handlers, and the module that holds them."""
    real = REPO / "lib" / "claude_vm"
    package = lib / "claude_vm"
    (package / "schema").mkdir(parents=True)
    for folder, kept in ((real, package), (real / "schema", package / "schema")):
        for entry in folder.iterdir():
            if entry.name not in ("__pycache__", "schema", "words.py"):
                (kept / entry.name).symlink_to(entry)
    table = (real / "schema" / "words.py").read_text()
    (package / "schema" / "words.py").write_text(table + MADE_UP_ROWS.format(handlers=dict(handlers)))
    (package / "made_up.py").write_text(MADE_UP)


def _checkout(where: Path, handlers: Mapping[str, str]) -> Path:
    """A checkout the test makes: the launcher and the tools it runs, copied, this checkout's own program, and its
    lib/ by a link, or, with handlers, made up over this checkout's (_overlaid)."""
    checkout = where / "checkout"
    (checkout / "tools").mkdir(parents=True)
    shutil.copy2(LAUNCHER, checkout / "aw")
    for tool in ("aw", "env-run"):
        shutil.copy2(REPO / "tools" / tool, checkout / "tools" / tool)
    if handlers:
        _overlaid(checkout / "lib", handlers)
    else:
        (checkout / "lib").symlink_to(REPO / "lib")
    return checkout


def _python(where: Path, records: Path) -> Path:
    """A virtualenv the test makes, and its python: its site-packages hold the suite's own packages, added as site adds
    them, the hook, which writes into records what each process of this python loaded, and a sitecustomize."""
    venv = where / "venv"
    made = [sys.executable, "-m", "venv", "--without-pip", str(venv)]
    subprocess.run(made, check=True, timeout=120, capture_output=True)
    [site] = venv.glob("lib/python*/site-packages")
    suite = dict.fromkeys(sysconfig.get_path(kind) for kind in ("purelib", "platlib"))
    lines = [f"import site; site.addsitedir({path!r})" for path in suite]
    lines.append(f"import {HOOK_MODULE}; {HOOK_MODULE}.watch({str(records)!r})")
    (site / f"{HOOK_MODULE}.pth").write_text("\n".join(lines) + "\n")
    (site / f"{HOOK_MODULE}.py").write_text(HOOK)
    (site / "sitecustomize.py").write_text(f"import {SITE_OWN}\n")
    (site / f"{SITE_OWN}.py").write_text('"""A module of the sitecustomize\'s own."""\n')
    return venv / "bin" / "python"


def processes(records: Path) -> tuple[Process, ...]:
    """Each process the hook recorded in records: one whose record cannot be read has no argv and no exit."""
    found = []
    for each in sorted(records.iterdir()):
        lines = each.read_text().splitlines()
        try:
            first = ast.literal_eval(lines[0])
            last = frozenset(ast.literal_eval(lines[1])["exit"]) if len(lines) > 1 else None
            process = Process(tuple(first["argv"]), frozenset(first["start"]), last)
        except (IndexError, KeyError, TypeError, SyntaxError, ValueError):
            process = Process((), frozenset(), None)
        found.append(process)
    return tuple(found)


def modules_of(*words: str, home: Path, where: Path, handlers: Mapping[str, str] | None = None) -> Run:
    """Run aw with words through the launcher of a checkout made in where, made up with handlers where given, with the
    hook's python as PYTHON: the run, and each Python of it as the hook recorded it. A run that wrote no record fails
    the test: nothing was read."""
    checkout = _checkout(where, handlers or {})
    records = where / "modules"
    records.mkdir()
    python = _python(where, records)
    ran = run_launcher(*words, home=home, launcher=checkout / "aw", env={"PYTHON": str(python)})
    found = processes(records)
    if not found:
        pytest.fail(f"the hook wrote nothing, so nothing was read of what the run loaded:\n{ran.stderr}")
    return Run(ran, found, checkout / "tools" / "aw", dict(handlers or {}))


def _is(argv: tuple[str, ...], program: Path) -> bool:
    return bool(argv) and os.path.realpath(argv[0]) == os.path.realpath(program)


def faults(quick: Quick, run: Run) -> list[Fault]:
    """What a line's run did that its row does not allow, each Python of it held: none where the line holds."""
    ran, verb = run.ran, _verb(quick.words)
    programs = [each for each in run.processes if _is(each.argv, run.program)]
    if len(programs) != 1:
        return [Fault("record", f"{len(programs)} records of the program, where it is one:\n{ran.stderr}")]
    loaded = programs[0].exit or frozenset()
    found = [] if ENTRY in loaded else [Fault("entry", f"the program never loaded {ENTRY}:\n{ran.stderr}")]
    if quick.refusal:
        # Said, in one sentence, before the run's exit: what saying it loaded is counted.
        if (ran.returncode, ran.stdout) != (EXITS["refused"], "") or not (
            ran.stderr.startswith("error: ") and len(ran.stderr.splitlines()) == 1
        ):
            found.append(Fault("refusal", f"exit {ran.returncode}, not one sentence refused:\n{ran.stderr}"))
    else:
        # A read finishes its work, against the fleet _fleet makes: one that stops at its first check exits otherwise.
        if ran.returncode != quick.exit:
            found.append(Fault("exit", f"exit {ran.returncode}, where it is {quick.exit}:\n{ran.stderr}"))
        if verb is None:
            if not ran.stdout.startswith("aw from the checkout "):
                found.append(Fault("version", ran.stdout))
        else:
            # The verb's handler ran: what it loads is counted, never only what a refusal before it would.
            handler = run.handlers.get(verb.name, verb.handler) or ""
            if f"claude_vm.{handler.partition(':')[0]}" not in loaded:
                found.append(Fault("handler", f"{handler} was never loaded:\n{ran.stderr}"))
    allowed = allowance(quick.may)
    for each in run.processes:
        named = " ".join(each.argv) or "a Python whose record cannot be read"
        if each.exit is None:
            found.append(Fault("cut", f"{named} ended before its exit was recorded, so what it loaded is not known"))
        everything = each.start | (each.exit or frozenset())
        if heavy(everything):
            found.append(Fault("heavy", f"{named} loaded {heavy(everything)}"))
        if each.exit is not None and (past := beyond(each.exit, each.start) - allowed):
            found.append(Fault("beyond", f"{named} loaded {sorted(past)}, beyond what its line gives it"))
        if VAULT not in quick.may and VAULT in {_top(name) for name in everything}:
            found.append(Fault("vault", f"{named} loaded {VAULT}, which its line does not give it"))
    return found


def _job(jobs: Path, running: bool) -> subprocess.Popen[str]:
    """A job made by a Python of its own (JOB_CODE), in a session of its own: its ID is the first line it says."""
    argv = [sys.executable, "-I", "-c", JOB_CODE, str(REPO / "lib"), str(jobs), "running" if running else "ended"]
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, start_new_session=True)


@contextlib.contextmanager
def _fleet(words: tuple[str, ...], *, whole: bool = True) -> Iterator[tuple[str, ...]]:
    """The test's own fleet, in the home AGENT_WAREHOUSE_HOME names, and the line's words, its JOB the ID of the job
    made for it. Whole, as init and the runs before a read leave one, each written by its one writer: the fleet's
    record; HOSTS, each with its host.yml and the settings it sets for itself, and the route to its VMs pinned direct
    (fleet_fixture.pinned), the first the current host; a setting of the fleet's; the vault, with its key, holding each
    host's token; and for logs a job that has ended, for cancel one that runs until cancel stops it, ended once the
    line is done if it did not. Not whole, its record alone."""
    where = paths.home(REPO)
    record.write(where, record.first(FLEET))
    if not whole:
        yield words
        return
    layout = hosts.Layout(where)
    for name, address in HOSTS.items():
        fields = {"address": address, "api_port": CLOSED_PORT, **TOKEN}
        hosts.write(layout, name, hosts.MISSING, change=lambda raw, fields=fields: raw.update(fields))
        digest = overrides.MISSING
        for setting, value in {"proxmox_node": name, **ALWAYS}.items():
            digest = overrides.set(where, setting, value, name, digest)
    hosts.make_current(layout, next(iter(HOSTS)), overrides.MISSING)
    for name in HOSTS:
        pinned(name)
    overrides.set(where, *FLEET_SETTING, None, overrides.MISSING)
    key = vaultkey.make_key(paths.key_file(where))
    sealed.create(where, key)
    for name, token in TOKENS.items():
        sealed.put(where, key, "token", Secret(token), host=name)
    if words[:1] not in (("logs",), ("cancel",)):
        yield words
        return
    job = _job(paths.jobs_dir(where), running=words[0] == "cancel")
    try:
        made = (job.stdout.readline() if job.stdout else "").strip()
        assert JOB_ID.fullmatch(made), f"the job's Python said no ID: {made!r}"
        if words[0] == "logs":
            assert job.wait(timeout=60) == 0
        yield tuple(made if word == JOB else word for word in words)
    finally:
        if job.poll() is None:
            harness._end(job)
        if job.stdout:
            job.stdout.close()


@pytest.mark.parametrize("quick", QUICK, ids=_id)
def test_a_quick_command_imports_nothing_heavy(home, tmp_path, quick: Quick) -> None:
    verb = _verb(quick.words)
    if not quick.refusal and verb is not None and verb.handler is None:
        pytest.skip(f"{verb.name} has no handler yet: card {verb.card} fills it, and this line runs then")
    with _fleet(quick.words) as words:
        run = modules_of(*words, home=home, where=tmp_path)
    assert faults(quick, run) == []


@pytest.mark.parametrize("whole", [True, False], ids=["the whole fleet", "its record alone"])
@pytest.mark.parametrize("quick", [quick for quick in QUICK if quick.words[:1] in {(read,) for read in READS}], ids=_id)
def test_a_read_is_held_to_its_work_past_its_first_check(home, tmp_path, quick: Quick, whole: bool) -> None:
    # A made-up handler reads what its read reads and refuses at its first check where the fleet lacks it; past the
    # check, it loads Jinja2, as a read's work would load what it loads. Against the fleet _fleet makes, it gets past
    # its check, so its work is counted and Jinja2 fails the line; against a fleet of its record alone it stops at its
    # check, and its exit status fails the line, where a line held only to loading the handler's module passed it.
    verb = quick.words[0]
    with _fleet(quick.words, whole=whole) as words:
        run = modules_of(*words, home=home, where=tmp_path, handlers={verb: f"made_up:{verb}"})
    found = {fault.kind for fault in faults(quick, run)}
    if whole:
        assert run.ran.returncode == quick.exit, run.ran.stderr
        assert "heavy" in found and "exit" not in found, found
    else:
        assert run.ran.returncode == EXITS["refused"], run.ran.stderr
        assert found == {"exit"}, found


@pytest.mark.parametrize(("handler", "kind"), [("child", "heavy"), ("cut", "cut")])
def test_a_python_a_read_starts_is_held_and_one_that_ends_unrecorded_fails(home, tmp_path, handler, kind) -> None:
    # The read's own process loads nothing heavy; the Python it starts does (child), or ends through os._exit, so what
    # it loaded is not known (cut): either fails the line, as a status that starts a child per host would.
    [quick] = [quick for quick in QUICK if quick.words == ("settings",)]
    with _fleet(quick.words) as words:
        run = modules_of(*words, home=home, where=tmp_path, handlers={"settings": f"made_up:{handler}"})
    assert run.ran.returncode == quick.exit, run.ran.stderr
    found = faults(quick, run)
    assert kind in {fault.kind for fault in found} and all(fault.text.startswith("-c ") for fault in found), found


@pytest.mark.parametrize("package", [READER, VAULT])
def test_a_bare_import_of_a_package_a_row_names_is_within_its_allowance(tmp_path, package: str) -> None:
    # What a row gives is what importing the package loads, in the Python the line runs: the locked PyYAML, built with
    # libyaml, brings Cython's runtime modules beside yaml, whose names move with the wheel, so no name of theirs is
    # written here.
    records = tmp_path / "modules"
    records.mkdir()
    python = _python(tmp_path, records)
    done = subprocess.run(
        [str(python), "-I", "-c", f"import {package}"], stdin=subprocess.DEVNULL, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr
    [process] = processes(records)
    assert process.argv[:1] == ("-c",) and process.exit is not None
    assert package in beyond(process.exit, process.start) <= allowance(frozenset({package}))
    assert adds(package) == beyond(process.exit, process.start)


def test_the_hook_records_each_python_of_the_run_from_its_program_s_start(home, tmp_path) -> None:
    # help.py is the dispatch's to import, by importlib, which -X importtime lists nothing of: the hook lists it.
    with _fleet(("help",)) as words:
        run = modules_of(*words, home=home, where=tmp_path)
    assert run.ran.returncode == EXITS["done"] and VERBS["help"].handler == "cli.help:main"
    [program] = [each for each in run.processes if _is(each.argv, run.program)]
    assert program.exit is not None and {"claude_vm.cli.help", "claude_vm.cli.dispatch", ENTRY} <= program.exit
    # The program's start held the hook, site's sitecustomize (the virtualenv's, which imports SITE_OWN, or one of the
    # base Python's own ahead of it on the path), and nothing of the program's: it is taken once site is done and before
    # the program runs, so neither is counted as the command's.
    assert {HOOK_MODULE, "sitecustomize"} <= program.start
    assert [name for name in program.start if _top(name) == "claude_vm"] == []
    # The launcher asks the Python which interpreter it is, and tools/env-run whether it is a virtualenv's, each a
    # python -c: each is recorded too, loads nothing of the line's, and holds the line.
    others = [each for each in run.processes if each is not program]
    assert others and all(each.argv[:1] == ("-c",) and each.exit is not None for each in others), others
    assert faults(Quick(("help",)), run) == []


def test_quick_runs_verbs_of_the_table_and_gives_the_vault_to_two_reads() -> None:
    # A line waits for its verb's row: a verb the table has not got would be skipped by nothing, and refused.
    assert all(quick.refusal or _verb(quick.words) is not None or quick.words == ("--version",) for quick in QUICK)
    verbs = {quick.words[0] for quick in QUICK if quick.words and not quick.refusal}
    assert verbs == {"help", "--version", *READS}
    assert all(quick.may <= {READER, VAULT} for quick in QUICK)
    vault = [quick.words for quick in QUICK if VAULT in quick.may]
    assert vault == [("settings", "--all"), ("status", "--only", "vms"), ("status",)]
    # A read is held to finishing; a refusal ends refused, whatever its row says.
    assert all(quick.exit == EXITS["done"] for quick in QUICK if quick.words[:1] != ("status",))


@pytest.mark.parametrize(
    ("loaded", "start", "found", "past"),
    [
        ({"json", "claude_vm.cli.main"}, set(), [], set()),
        ({"ansible.module_utils", "jinja2"}, set(), ["ansible.module_utils", "jinja2"], {"ansible", "jinja2"}),
        ({"claude_vm.engine.record", "claude_vm.cli"}, set(), ["claude_vm.engine.record"], set()),
        ({"mcp", "mcpx", "pydantic_core.core"}, set(), ["mcp", "pydantic_core.core"], {"mcp", "mcpx", "pydantic_core"}),
        ({"yaml", "_virtualenv"}, {"_virtualenv"}, [], {"yaml"}),
        ({"yaml", "cython_runtime", "_cython_9_9_9"}, set(), [], {"yaml", "cython_runtime", "_cython_9_9_9"}),
        ({"claude_vm.engineering", "_sysconfigdata__linux_x86_64-linux-gnu"}, set(), [], set()),
    ],
)
def test_heavy_and_beyond_read_a_module_by_its_name(loaded, start, found, past) -> None:
    # A module under one HEAVY names is heavy, and one whose name only begins as one does is not; what the
    # interpreter's start loaded is its own, and only past it is a package beyond the standard library and claude_vm
    # the command's: an extension's Cython runtime modules too, which only a row's allowance admits.
    assert heavy(frozenset(loaded)) == found
    assert beyond(frozenset(loaded), frozenset(start)) == past
