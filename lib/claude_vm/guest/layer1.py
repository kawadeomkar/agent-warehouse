"""Layer 1's child: one `ansible-playbook` that converges one machine, started as a child of the run in a session of its
own, with a configuration, an inventory and an environment the engine makes and nothing of the caller's (docs/
architecture/engine.md §16.10).

`run(job, target, play, variables_file, ...)` writes two files into the run's directory (runs/rundir.py): the child's
configuration, `layer1.cfg`, and an inventory of one host, `inventory-<identity>.yml`, so that a run over several
machines starts one child a machine, each with an inventory of its own. The configuration names the stdout callback
(`CALLBACK`) and no other, one fork, facts kept under `ansible_facts`, pipelining, the `yaml` inventory plugin alone,
the roles of this checkout or release (`ROLES`), Ansible's local temporary files in the run's directory, and every
folder Ansible looks for a plugin or a collection in: the product's own (the callback's, `CALLBACKS`, and today's
filters, which the roles call, `FILTERS`), and an empty folder of the run's directory (`NONE`) for every other kind and
for the collections, with Python's path not scanned for them, so that nothing under the caller's HOME or under
/usr/share/ansible is searched (a vars plugin in ~/.ansible/plugins/vars runs without being enabled). `ssh_args`, the
one place ahead of Ansible's own words to ssh, holds `SSH_ARGS`: public keys and no other way to log in, as the runner
offers (Ansible's own `PreferredAuthentications` puts GSSAPI and host-based first), never Ansible's default, which would
put `ControlMaster=auto` ahead of the host's options and make the child a connection's master. The inventory's one host
is named by the machine's identity, `agent-warehouse-<its SMBIOS UUID>`, never by a name a person
chose, which is a variable (`NAME_VARIABLE`): a VM named after an inventory host once had today's status send the API
token to the address its own agent reported. Its variables are the address, the account, `/usr/bin/python3` and
`/usr/bin/sudo` by their paths, and the connection exactly as the SSH runner makes it (`runner.ansible_vars`): its
options first, `StrictHostKeyChecking=yes` against the product's own known-hosts file under the identity, the shared
control socket (the master `connections.ensure` opened before) with `ControlMaster=no`, and the hop only where the
target carries one. The child never takes a key at first use: a target whose identity has no key filed is refused by
name before anything starts, with the line that files it (`NoKey`), and so is a first use's target. The inventory is a
source Ansible templates, so a value of it that would open a template is refused, never written. The variables file
is held first to what guest/variables.py writes, since an extra variable outranks the inventory: no `ansible_` name,
which would replace the connection's, and every string wrapped, so that none is read as a template.

The child's environment is an allow list (`KEPT`, then the engine's own): `PATH`, `HOME`, `LANG` and `SSH_AUTH_SOCK`
where the caller's environment has them, `ANSIBLE_CONFIG` naming the generated configuration, the untrusted-template
switch at `ignore` (Ansible hands an untrusted template back as its text and never renders it; `error` would fail
every task that reads a machine's output holding `{{`, for no protection more), the number of the events pipe's
descriptor (`EVENTS_VARIABLE`), `ANSIBLE_VERBOSITY` only for a run
given `-v`, and the job's marker, which children.start adds. Nothing else of the caller's reaches it: no other
`ANSIBLE_*`, no `PYTHON*`, no stdout callback exported. Its command line is the program, `-i` and the inventory, the
variables only as `-e @<file>` (never `key=value`, which splits on whitespace), `--tags` with the parts asked for,
`--check` and `--diff` where asked, and the play. It is started through children.start as an `ansible` child of the run,
in a session of its own, so that it has no terminal and a terminal's Ctrl-C reaches the run alone, which hands it on as
SIGINT to the child's group (runs/attached.py); its standard input is `/dev/null`, and its one pipe is the events',
whose write end goes over through start's descriptors and whose read end the run reads to its end, while the child
runs: the product's callback (`CALLBACK`, in `CALLBACKS`, which only this configuration names) writes each event of the
play there as a line of JSON, and guest/events.py hands each to the run's own `emit` as a `step` event of the machine,
keyed by its identity as the run's record keys it, with the name a person chose beside it, every secret of the run's
redaction list (`redacted`) struck from it. A listener that fails while the child runs ends the child, and its error is
raised. What the child prints on its standard output and error - with the product's callback, Ansible's own words
alone: a warning, an error before a play starts - goes to a file of its own, never the terminal, and its last `MOST`
bytes from a line's start come back, struck and plain the same way, with the exit status and the counts the events gave
(`Ended`). However the call ends, nothing it started is left: a child still running when it ends is ended with its tree
(claude_vm.proc).

Only tests pass `test_connection`: the host variables of a test-only connection, `local` for a play of one task on a
stand-in machine (tests/unit/aw_machine.py's `layer1_host`) and `community.docker.docker` for a Molecule container,
whose collection the configuration finds only in the folder a test gives beside it (`test_collections`); no product
code passes either, which tests/unit/test_aw_layer1.py holds. The real roles never run over `local`: they would
run as root on the computer running the tests.

Standard library only; asyncio, tempfile and claude_vm.proc are imported inside the coroutine that starts the child. The
callback, which imports Ansible, runs in the child alone: nothing here imports it.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from claude_vm import checks
from claude_vm.guest import events
from claude_vm.guest import variables as play_variables
from claude_vm.runs import children, rundir
from claude_vm.schema import patterns, printer
from claude_vm.schema.words import Line
from claude_vm.secret import Secret
from claude_vm.ssh import hostkeys, runner

if TYPE_CHECKING:
    from asyncio.subprocess import Process

    from claude_vm.runs.job import Job

# The generated configuration's name in the run's directory, and the folder of Ansible's own local temporary files
# there: one of each for a run, whatever its machines.
CONFIG = "layer1.cfg"
LOCAL_TMP = "ansible-local"
# An inventory's name in the run's directory, before the machine's identity: one for each machine of the run.
INVENTORY_PREFIX = "inventory-"
INVENTORY_SUFFIX = ".yml"
# What a machine's inventory name starts with: its identity, agent-warehouse-<its SMBIOS UUID>.
PREFIX = "agent-warehouse-"
# The host variable that carries the name a person chose for the machine, which is never the inventory's name.
NAME_VARIABLE = "aw_name"
# The machine's own interpreter and sudo, by their paths: a python3 or a sudo on the account's PATH (~/.local/bin is
# first there) would run as root.
PYTHON = "/usr/bin/python3"
SUDO = "/usr/bin/sudo"
# The stdout callback the configuration names, and no other prints: the product's own, which writes one event a line to
# the events pipe, from its folder beside this module, which only this configuration names.
CALLBACK = "aw_events"
CALLBACKS = Path(__file__).resolve().parent / "ansible" / "callback_plugins"
# The roles of this checkout or release: the folder beside lib/ that holds this module.
ROLES = Path(__file__).resolve().parents[3] / "roles"
# Today's filters, which the roles call (plain_text, sshd_match_spec): the folder beside lib/ that holds this module.
FILTERS = Path(__file__).resolve().parents[3] / "filter_plugins"
# The empty folder of the run's directory that every other plugin folder and the collections' path name, so that none
# of Ansible's defaults under the caller's HOME, or /usr/share/ansible, is searched.
NONE = "ansible-none"
# Each setting of a folder Ansible searches for a plugin of a kind, by its key in the configuration: those not the
# product's own folders name NONE.
PLUGIN_FOLDERS = (
    "action_plugins",
    "become_plugins",
    "cache_plugins",
    "cliconf_plugins",
    "connection_plugins",
    "doc_fragment_plugins",
    "httpapi_plugins",
    "inventory_plugins",
    "library",
    "lookup_plugins",
    "module_utils",
    "netconf_plugins",
    "strategy_plugins",
    "terminal_plugins",
    "test_plugins",
    "vars_plugins",
)
# What ssh_args holds, ahead of Ansible's own words to ssh: a key, and no other way to log in, as the runner offers.
SSH_ARGS = "-o PreferredAuthentications=publickey -o GSSAPIAuthentication=no -o HostbasedAuthentication=no"

# The caller's variables the child keeps, where the caller's environment has them; every other is left out.
KEPT = ("PATH", "HOME", "LANG", "SSH_AUTH_SOCK")
# The engine's own: the generated configuration, the untrusted-template switch (Ansible's own variable, experimental),
# the events pipe's descriptor, and the verbosity of a run given -v. The switch is at ignore, Ansible's own default,
# pinned: an untrusted template is handed back as its text and never rendered, where error fails every task that reads
# a module's result holding {{ (a machine's own words), for no protection more. guest/variables.py's wrapping, not
# the switch, is what keeps a setting from being a template.
CONFIG_VARIABLE = "ANSIBLE_CONFIG"
UNTRUSTED_VARIABLE = "_ANSIBLE_TEMPLAR_UNTRUSTED_TEMPLATE_BEHAVIOR"
UNTRUSTED = "ignore"
EVENTS_VARIABLE = "AGENT_WAREHOUSE_EVENTS"
VERBOSITY_VARIABLE = "ANSIBLE_VERBOSITY"
# -v to -vvvvv, as the table's counted option takes them.
VERBOSITY_MOST = 5

# How much of what the child printed comes back: its last 64 KiB, from a line's start, where Ansible says why it
# stopped. And how long the events pipe is read once the child has ended: a task worker it left behind holds the pipe's
# write end.
MOST = 64 * 1024
EVENTS_GRACE = 1.0

# The test-only connections, and the host variables a test's connection may give: its kind, where it reaches, the
# interpreter there, the module's folder there, and become with the stand-in's own sudo.
TEST_CONNECTIONS = ("local", "community.docker.docker")
TEST_HOST_VARIABLES = frozenset(
    {
        "ansible_connection",
        "ansible_host",
        "ansible_user",
        "ansible_python_interpreter",
        "ansible_remote_tmp",
        "ansible_become",
        "ansible_become_exe",
    }
)

# A part asked for with --tags: a role's name, as today's tags are.
_TAG = re.compile(r"[a-z][a-z0-9_]{0,63}")
# What opens a template where Ansible templates a value: an inventory's strings are trusted as templates.
_TEMPLATE = ("{{", "{%", "{#")
# What a path in the configuration may not hold: an ini file's comment, configparser's interpolation, a pathspec's
# separator, a line's end.
_INI_UNSAFE = re.compile(r"[%;#:\x00-\x1f\x7f]")


class Refused(ValueError):
    """A target, a play, a file or a value the layer-1 child does not take, said by what is wrong: nothing was started
    and nothing was written."""


class NoKey(Refused):
    """A machine whose identity has no host key filed in the product's known-hosts file: the child never takes one, so
    nothing was started."""


@dataclasses.dataclass(frozen=True)
class Ended:
    """How the child ended: its exit status (negative, the signal that ended it); the last MOST bytes of what it printed
    on its standard output and error, from a line's start, every secret of the run's redaction list struck from it and
    made plain; and the counts its events gave (guest/events.py's Counts). said is Ansible's own words of the machine's
    run, many lines, a machine's data: the run's text log takes it, and a person's screen only line by line, never whole
    (one_line)."""

    status: int
    said: str
    counts: events.Counts


# ---------------------------------------------------------------- what is held before anything starts ----


def _machine(target: object) -> runner.Target:
    """target held to a machine's own: a runner.Target with an identity that starts PREFIX, which is the inventory's
    name, and its key checked strictly, never a first use's."""
    if not isinstance(target, runner.Target):
        raise Refused("layer 1 reaches a machine given as a runner.Target")
    if target.is_host:
        raise Refused("the Proxmox host is no machine of layer 1's: its inventory names one machine by its identity")
    identity = target.identity
    named = isinstance(identity, str) and identity.startswith(PREFIX)
    if not named or not checks.HOST_KEY_ALIAS_RE.fullmatch(str(identity)):
        raise Refused(f"an inventory names a machine by its identity, {PREFIX}<its UUID>, and {identity!r} is not one")
    if target.first_use:
        raise Refused(
            f"{identity} is a first use's target: layer 1's child checks the key filed for it and never takes one"
        )
    return target


def _name(name: object) -> str:
    """The name a person chose for the machine: one VM name, never several."""
    if not isinstance(name, str) or "," in name or not patterns.full("vm_name", name):
        raise Refused("a machine's name is one VM name: letters, digits, dots, underscores and hyphens")
    return name


def _key_filed(target: runner.Target, name: str, host_flag: str) -> None:
    """NoKey where the known-hosts file target names files no key under its identity: its first connection files it,
    before its first configure, and the line that makes it is said. The line is made first, whatever the file holds, so
    that a host's flag the printer would refuse is refused here too."""
    identity = str(target.identity)
    files = Line("deploy", {"names": [name], "only": "provision"}, host_flag=host_flag)
    try:
        printer.line(files.verb, files.values, refused=True, host_flag=files.host_flag)
    except ValueError as error:
        raise Refused(f"the line that files a machine's key cannot be made: {error}") from None
    if hostkeys.kept(target.known_hosts, identity):
        return
    raise NoKey(
        printer.said(
            "no host key of {identity} is filed in {known_hosts}, and layer 1 never takes one: a machine's key is"
            " filed at its first connection, before its first configure, which {files} makes",
            {"identity": identity, "known_hosts": target.known_hosts, "files": files},
        )
    )


def _file(path: object, what: str) -> Path:
    """An absolute path to a file that is there."""
    if not isinstance(path, (str, Path)) or not Path(path).is_absolute():
        raise Refused(f"{what} is an absolute path")
    found = Path(path)
    if not found.is_file():
        raise Refused(f"{what}, {found}, is not a file")
    return found


def _variables(path: Path) -> Path:
    """The variables file held to what guest/variables.py writes: a variable it gives outranks the inventory, so none
    may name the play's connection (an ansible_ name), and none of its strings may be read as a template."""
    try:
        return play_variables.held(path)
    except play_variables.Refused as refused:
        raise Refused(str(refused)) from None


def _tags(tags: object) -> tuple[str, ...]:
    """The parts asked for, each a role's name, each once."""
    if isinstance(tags, (str, bytes)) or not isinstance(tags, Sequence):
        raise Refused("the parts asked for are a list of names")
    if not all(isinstance(tag, str) and _TAG.fullmatch(tag) for tag in tags) or len(set(tags)) != len(tags):
        raise Refused("each part asked for is a role's name, once: lower-case letters, digits and underscores")
    return tuple(tags)


def _flag(value: object, what: str) -> bool:
    if not isinstance(value, bool):
        raise Refused(f"{what} is True or False")
    return value


def _verbosity(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= VERBOSITY_MOST:
        raise Refused(f"the verbosity is a count from 0 to {VERBOSITY_MOST}, as -v to -vvvvv give it")
    return value


def _test_connection(given: object) -> dict[str, object]:
    """A test's own connection held: one of TEST_CONNECTIONS, with no host variable but TEST_HOST_VARIABLES."""
    if given is None:
        return {}
    if not isinstance(given, Mapping) or given.get("ansible_connection") not in TEST_CONNECTIONS:
        raise Refused(f"a test-only connection is one of {', '.join(TEST_CONNECTIONS)}")
    extra = sorted(set(given) - TEST_HOST_VARIABLES)
    if extra:
        raise Refused(f"a test-only connection gives no {', '.join(map(str, extra))}")
    return dict(given)


def _untemplated(variables: Mapping[str, object]) -> None:
    """Refused, naming the variable, where a string of the inventory would open a template: Ansible reads an inventory
    as a trusted source and templates its values."""
    for key, value in variables.items():
        if isinstance(value, str) and any(opener in value for opener in _TEMPLATE):
            raise Refused(f"the inventory's {key} would open a template: a value of an inventory is never one")


def _collections(given: object, test_connection: object) -> Path | None:
    """A test's own folder of collections, where its connection is a collection's: an absolute folder that is there,
    given only beside a test-only connection."""
    if given is None:
        return None
    if test_connection is None:
        raise Refused("a folder of collections is a test's, given only beside a test-only connection")
    if not isinstance(given, (str, Path)) or not Path(given).is_absolute() or not Path(given).is_dir():
        raise Refused("a test's folder of collections is an absolute path to a folder that is there")
    return Path(given)


def _redacted(given: object) -> tuple[Secret, ...]:
    """The run's redaction list: every secret the run opened, each a Secret."""
    if isinstance(given, (str, bytes, Secret)) or not isinstance(given, Sequence):
        raise Refused("the run's redaction list is a list of Secrets")
    if not all(isinstance(secret, Secret) for secret in given):
        raise Refused("the run's redaction list holds Secrets alone: every secret the run opened, as it opened it")
    return tuple(given)


def _ini(path: Path, what: str) -> str:
    """path as one value of the configuration file: absolute, with no character an ini file or a pathspec reads."""
    value = str(path)
    if not path.is_absolute() or _INI_UNSAFE.search(value) or value != value.strip():
        raise Refused(f"{what}, {value!r}, is an absolute path with no %, ;, #, : or control character in it")
    return value


# ---------------------------------------------------------------- what the child is given ----


def program() -> Path:
    """ansible-playbook of the environment this engine runs in, by its path: never one found on PATH."""
    found = Path(sys.executable).with_name("ansible-playbook")
    if not found.is_file():
        raise Refused(f"there is no ansible-playbook beside {sys.executable}: the environment lacks ansible-core")
    return found


def configuration_text(directory: Path, collections: Path | None = None) -> str:
    """The child's configuration for a run whose directory is directory: every plugin folder named, the product's own or
    NONE there, and the collections' path NONE, or a test's own folder of collections where one is given."""
    none = _ini(directory / NONE, "the empty plugin folder")
    found = none if collections is None else _ini(collections, "the folder of collections")
    return "\n".join(
        [
            "[defaults]",
            f"stdout_callback = {CALLBACK}",
            f"callback_plugins = {_ini(CALLBACKS, 'the callback folder')}",
            f"filter_plugins = {_ini(FILTERS, 'the filter folder')}",
            *(f"{key} = {none}" for key in PLUGIN_FOLDERS),
            f"collections_path = {found}",
            "collections_scan_sys_path = false",
            "forks = 1",
            "inject_facts_as_vars = false",
            f"roles_path = {_ini(ROLES, 'the roles path')}",
            f"local_tmp = {_ini(directory / LOCAL_TMP, 'the local temporary folder')}",
            "",
            "[inventory]",
            "enable_plugins = yaml",
            "",
            "[connection]",
            "pipelining = true",
            "",
            "[ssh_connection]",
            f"ssh_args = {SSH_ARGS}",
            "",
        ]
    )


def host_variables(
    target: runner.Target, name: str, test_connection: Mapping[str, object] | None = None
) -> dict[str, object]:
    """The one host's variables: the connection as the SSH runner makes it, over Ansible's own ssh connection, which no
    setting changes (its default, and no variable of the caller's reaches the child), the machine's interpreter and sudo
    by their paths, and the name a person chose; a test's own connection over them, where a test gives one."""
    variables: dict[str, object] = {
        **runner.ansible_vars(target),
        "ansible_python_interpreter": PYTHON,
        "ansible_become_exe": SUDO,
        NAME_VARIABLE: name,
    }
    variables.update(_test_connection(test_connection))
    _untemplated(variables)
    return variables


def inventory_document(
    target: runner.Target, name: str, test_connection: Mapping[str, object] | None = None
) -> dict[str, object]:
    """The inventory of one host, named by target's identity."""
    return {"all": {"hosts": {str(target.identity): host_variables(target, name, test_connection)}}}


def environment(env: Mapping[str, str], config: Path, events: int, verbosity: int = 0) -> dict[str, str]:
    """The child's environment: KEPT's variables where env has them, then the engine's own. The job's marker is
    children.start's to add."""
    if not isinstance(env, Mapping):
        raise Refused("the run's environment is a mapping of names to values")
    built = {name: env[name] for name in KEPT if isinstance(env.get(name), str)}
    built[CONFIG_VARIABLE] = str(config)
    built[UNTRUSTED_VARIABLE] = UNTRUSTED
    built[EVENTS_VARIABLE] = str(events)
    if _verbosity(verbosity):
        built[VERBOSITY_VARIABLE] = str(verbosity)
    return built


def argv(
    inventory: Path,
    play: Path,
    variables_file: Path | None = None,
    tags: Sequence[str] = (),
    check: bool = False,
    diff: bool = False,
) -> list[str]:
    """The child's command line: the program, its inventory, the variables only as -e @<file>, the parts asked for,
    check mode and its diff where asked, and the play."""
    words = [str(program()), "-i", str(_file(inventory, "the inventory"))]
    if variables_file is not None:
        words += ["-e", f"@{_file(variables_file, 'the variables file')}"]
    if _tags(tags):
        words += ["--tags", ",".join(tags)]
    if _flag(check, "check"):
        words.append("--check")
    if _flag(diff, "diff"):
        words.append("--diff")
    return [*words, str(_file(play, "the play"))]


def _write(path: Path, content: str) -> None:
    """content written to path at 0600, whole: a file of its own beside it, never through a link, then put in place."""
    staged = path.with_name(f".{path.name}.writing")
    descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as out:
            descriptor = -1
            out.write(content)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    os.replace(staged, path)


def _directory(job: Job) -> Path:
    made = rundir.made(job)
    if made is None:
        raise Refused(f"job {job.id} has no run directory, where layer 1's files go: rundir.make")
    return made


# ---------------------------------------------------------------- the child ----


def _said(printed, heard: events.Heard) -> str:
    """The last MOST bytes the child printed, from the first line's start among them where they are not all of it,
    so that no secret on one line is cut in two before it is struck; struck and plain as an event's strings are."""
    size = os.fstat(printed.fileno()).st_size
    data = os.pread(printed.fileno(), MOST, max(size - MOST, 0))
    if size > MOST:
        data = data.partition(b"\n")[2]
    return heard.clean(data.decode("utf-8", "replace"), MOST, end=True).strip()


async def run(
    job: Job,
    target: runner.Target,
    play: Path,
    variables_file: Path | None = None,
    *,
    name: str,
    host_flag: str,
    env: Mapping[str, str],
    tags: Sequence[str] = (),
    check: bool = False,
    diff: bool = False,
    verbosity: int = 0,
    redacted: Sequence[Secret] = (),
    started: Callable[[Process], None] | None = None,
    test_connection: Mapping[str, object] | None = None,
    test_collections: Path | None = None,
) -> Ended:
    """play run on target by one ansible-playbook, a child of job's run in a session of its own, with the configuration
    and the inventory written into the run's directory and the environment built from env's allow list; started(process)
    called once it runs; each event of the play handed to job's emit as a step event of the machine, keyed by target's
    identity with name beside it, as it comes, every secret of redacted (every secret the run opened) struck from it;
    its exit status, the end of what it printed, and the counts.
    name is the name a person chose for the machine, and host_flag the host a line that names it names (the printer's).
    Everything is held before anything is written or started: a target that is no machine's, a first use's, or whose
    identity has no key filed, a play or a variables file that is not there, a variables file that is not what
    guest/variables.py writes, a part, a flag, a verbosity or a redaction list that is not one, a test's folder of
    collections given without a test-only connection. A listener of job's that fails while the child runs ends the
    child, and its error is raised."""
    import asyncio
    import tempfile

    from claude_vm import proc

    machine = _machine(target)
    name = _name(name)
    _key_filed(machine, name, host_flag)
    play = _file(play, "the play")
    variables = None if variables_file is None else _variables(_file(variables_file, "the variables file"))
    tags = _tags(tags)
    check, diff, verbosity = _flag(check, "check"), _flag(diff, "diff"), _verbosity(verbosity)
    if not isinstance(env, Mapping):
        raise Refused("the run's environment is a mapping of names to values")
    heard = events.Heard(job, str(machine.identity), name, _redacted(redacted))
    document = inventory_document(machine, name, test_connection)
    collections = _collections(test_collections, test_connection)
    directory = _directory(job)
    config = directory / CONFIG
    configuration = configuration_text(directory, collections)
    program()

    with contextlib.suppress(FileExistsError):
        os.mkdir(directory / NONE, 0o700)
    _write(config, configuration)
    inventory = directory / f"{INVENTORY_PREFIX}{machine.identity}{INVENTORY_SUFFIX}"
    _write(inventory, json.dumps(document, indent=2, sort_keys=True) + "\n")
    words = argv(inventory, play, variables, tags, check, diff)

    loop = asyncio.get_running_loop()
    reading, writing = os.pipe()
    pipe = process = transport = None
    tasks: list[asyncio.Future] = []
    try:
        given = environment(env, config, writing, verbosity)
        with tempfile.TemporaryFile() as printed:
            # The events pipe's write end goes over through start's descriptors, its last argument: no other is handed.
            process = await children.start(
                job,
                words,
                children.SESSION,
                given,
                asyncio.subprocess.DEVNULL,
                printed,
                printed,
                children.ANSIBLE,
                (writing,),
            )
            os.close(writing)
            writing = -1
            pipe, reading = os.fdopen(reading, "rb", buffering=0), -1
            reader = asyncio.StreamReader(limit=events.LINE_MOST)
            transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), pipe)
            heeding = asyncio.ensure_future(heard.read(reader))
            waiting = asyncio.ensure_future(process.wait())
            tasks += [heeding, waiting]
            if started is not None:
                started(process)
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if heeding.done() and not waiting.done():
                # A listener's failure ends the child, in the finally below, and is raised; a pipe at its end, read
                # whole, lets the child end in its own time.
                heeding.result()
            await waiting
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(heeding), EVENTS_GRACE)
            status = process.returncode
            assert status is not None
            return Ended(status, _said(printed, heard), heard.counts)
    finally:
        # The pipe's reading and the wait for the child, where the call ends before them.
        unfinished = [task for task in tasks if not task.done()]
        for task in unfinished:
            task.cancel()
        if unfinished:
            await asyncio.wait(unfinished)
        # However the call ends - cancelled, started raising - nothing the child started is left running.
        if process is not None and process.returncode is None:
            proc.end(process.pid)
            await process.wait()
        if transport is not None:
            transport.close()
        elif pipe is not None:
            pipe.close()
        for left in (reading, writing):
            if left >= 0:
                os.close(left)
