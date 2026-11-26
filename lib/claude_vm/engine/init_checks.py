"""init's checks: what a first deploy needs of the host, each said as one of today's `ok` and `!!` lines
(docs/architecture/engine.md §16.4).

`check(home, host, environ=, job=, write=)` opens the host as every workflow does (fleet/opened.py's `connect`, lenient
as `settings` and `vault` are, so that a host whose own settings.yml sets no node yet is opened), and `run(host, job=,
own=, write=)` checks it, in today's order: the API answers and is Proxmox VE 9 or later, and the token is accepted; the
pool is there and its mark names this fleet with `controller=none` (fleet/mark.py's `may_change`, D2's sentence for any
other); the node, settled where the host's own settings.yml sets none (the one node online, else the node root SSH says
the host is, `hostname -s`); the base template, compared with its settings (engine/template.py's `compare`, E11); the
permission check, the token's privileges on each path read from `/access/permissions` with no `userid` (with one, a
token is refused on `/access` even for its own user) and held to what pve/privileges.py's one table needs there
(`needed`), on the pool, the storage, each bridge a clone's source has a network card on and the node, each missing
privilege named with its path; `VM.Audit` on `/vms` where `proxmox_audit_scope` is `node`, a `--` line where it is not
held, and a `!!` line where the token holds more there, which lets it change every VM of the node; a template of the
person's own held to the clone grant on its own path; root SSH, where the template is missing or is to be rebuilt; the
route to the VMs, settled by engine/routes.py, and the hop's login where it goes through the host, an SSH connection
that runs nothing; the storage and, before the template is built, the bridge, each as the node lists it to the token;
and that the machines get an SSH key.

Each check is a `Check(state, text, fix)`, its state one of today's four prefixes (`ok`, `!!`, `--` and `note`), its
line today's form (`line`). A `!!` is a problem for deploy, and init exits 1 exactly when it said one (`Checked.exit`).
Every call the checks make is a read: the client's GETs, which follow no redirect (B's client's rule, the transport
following none), and over root SSH `true` and `hostname -s`. Nothing is written but, under init's own writing rule
(write true: never `init --check`), the node, the storage and the bridge settled, each only where the host's own
settings.yml sets none, through fleet/overrides.py's one writer. No request carries the token before init settled
the certificate: the host is opened through the anchor init kept, and a run whose certificate was refused (an
`impostor`'s among them) never reaches here.

Standard library, fleet/, hosts/, pve/, ssh/, schema/, engine/access.py, routes.py and template.py and today's text.py
at import; asyncio inside the coroutines.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ..checks import proxmox_name, proxmox_names, proxmox_names_refused
from ..fleet import hosts, mark, opened, overrides, paths
from ..hosts import model
from ..pve import privileges
from ..pve import template as judging
from ..schema.printer import said
from ..schema.words import EXITS, Line
from ..ssh import runner
from ..text import one_line
from . import access, routes, template

if TYPE_CHECKING:
    from ..pve.client import Proxmox
    from ..runs.job import Job

__all__ = ["NOTE", "OK", "PROBLEM", "STATES", "UNCHECKED", "Check", "Checked", "check", "run"]

# Today's four prefixes: what passed, a problem for deploy, what could not be checked, and what is said beside them.
OK, PROBLEM, UNCHECKED, NOTE = STATES = ("ok", "!!", "--", "note")
PREFIX = {OK: "  ok   ", PROBLEM: "  !!   ", UNCHECKED: "  --   ", NOTE: "  note "}
# The most characters of what the host or ssh said that a line says.
SAID_MOST = 300
# Root SSH asked whether it answers in batch mode, and which node the host is: template.py's standing way to build.
TRUE = ("true",)
NODE_COMMAND = ("hostname", "-s")
ROOT_SSH = template.STANDING
# The settings the checks settle where the host's own settings.yml sets none, in the order they are settled.
SETTLED = ("proxmox_node", "proxmox_storage", "proxmox_bridge")
BRIDGE = re.compile(r"(?:^|,)bridge=([^,]+)")
NET = re.compile(r"net[0-9]{1,2}")
ONLINE = "online"


@dataclasses.dataclass(frozen=True)
class Check:
    """One check: its state (one of STATES), what it found, and what fixes it, "" for nothing."""

    state: str
    text: str
    fix: str = ""

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"a check's state is one of {', '.join(STATES)}")

    @property
    def line(self) -> str:
        """The line said, today's form: the prefix, what was found and, after a colon, what fixes it."""
        return f"{PREFIX[self.state]}{self.text}{f': {self.fix}' if self.fix else ''}"


@dataclasses.dataclass(frozen=True)
class Checked:
    """What the checks found, in their order, and what they wrote (each file's path in the fleet's home)."""

    checks: tuple[Check, ...]
    wrote: tuple[str, ...] = ()

    @property
    def problems(self) -> tuple[Check, ...]:
        return tuple(each for each in self.checks if each.state == PROBLEM)

    @property
    def exit(self) -> int:
        """1 exactly where a `!!` line is said, else 0."""
        return EXITS["refused"] if self.problems else EXITS["done"]

    def summary(self, host_flag: str) -> str:
        """The sentence printed after the lines with nobody there: the problems to fix, else what could not be
        checked; "" where nothing is left."""
        again = {"init": Line("init", {}, host_flag=host_flag)}
        count = len(self.problems)
        if count:
            one = count == 1
            words = {"count": count, "problems": "problem" if one else "problems", "them": "it" if one else "them"}
            return said(
                "init found {count} {problems} for deploy, each marked !! above: fix {them}, then run {init} again",
                {**again, **words},
            )
        if any(each.state == UNCHECKED for each in self.checks):
            return said(
                "init could not check all that deploy needs, each marked -- above: run {init} again once it can", again
            )
        return ""

    def next_line(self, host_flag: str) -> str:
        """The checks' line of NEXT STEPS at a terminal (matter init_check, said through engine/report.py's say): the
        printer's line of init with its host flag, as init_settings' is, and what running it again does; "" where
        nothing is left."""
        again = {"init": Line("init", {}, host_flag=host_flag)}
        count = len(self.problems)
        if count:
            one = count == 1
            words = {"them": "the problem" if one else f"the {count} problems", "are": "is" if one else "are"}
            return said("{init}   checks again once {them} marked !! above {are} fixed", {**again, **words})
        if any(each.state == UNCHECKED for each in self.checks):
            return said("{init}   checks again what could not be checked, once it can", again)
        return ""

    def lines(self, host_flag: str) -> list[str]:
        """Every line said: each check's, then the summary where there is one."""
        summary = self.summary(host_flag)
        return [each.line for each in self.checks] + ([summary] if summary else [])


class _Said:
    """The run engine/routes.py's settle is given: its children the job's, and what it emits (its report of the route
    found) left unsaid, since the check's own line says the route."""

    def __init__(self, job: Job) -> None:
        self.job = job

    def emit(self, _kind: str, _machine: str | None, _text: str = "", **_data: object) -> None:
        return None


def _name(value: object, kind: str) -> str:
    """value as a line says it: as it is where it is a name Proxmox gives a kind (checks.proxmox_name, the rule E5
    holds the names the host reports to), else as JSON, so that whatever it holds is shown and never acted on."""
    text = str(value)
    return text if proxmox_name(text, kind) else json.dumps(text)


def _listed(values: Sequence[str], kind: str, empty: str = "none listed") -> str:
    """The names Proxmox listed, each as _name says it."""
    return ", ".join(_name(each, kind) for each in values) or empty


def _error(error: BaseException) -> str:
    return one_line(str(error), SAID_MOST).removeprefix("error: ")


def _why(result: runner.Result) -> str:
    """Why a command over root SSH did not end well, in plain words."""
    if isinstance(result, runner.Done):
        return one_line(f"it ended with status {result.status}: {result.said}", SAID_MOST)
    if isinstance(result, runner.Silent):
        return "it said nothing in time"
    if isinstance(result, runner.TimedOut):
        return "it did not end in time"
    return one_line(getattr(result, "said", "") or "it could not be reached", SAID_MOST)


@dataclasses.dataclass
class _Run:
    """One run of the checks against an opened host: what it found so far, the settings as they now are, and what it
    wrote."""

    host: opened.Host
    job: Job
    own: Mapping[str, object]
    write: bool
    settings: dict[str, object]
    checks: list[Check] = dataclasses.field(default_factory=list)
    wrote: list[str] = dataclasses.field(default_factory=list)

    @property
    def client(self) -> Proxmox:
        return self.host.client

    @property
    def flag(self) -> str:
        return self.host.host_flag

    @property
    def token(self) -> str:
        return f"{self.host.fields.token_user}!{self.host.fields.token_id}"

    @property
    def settings_file(self) -> str:
        return f"{hosts.HOSTS_REL}/{self.host.name}/{overrides.SETTINGS}"

    def say(self, state: str, text: str, fix: str = "") -> None:
        self.checks.append(Check(state, text, fix))

    def fix(self, sentence: str, **fields: object) -> str:
        """sentence said whole, as a fix says a command (no line joined to words of the check's own): {init} init's
        line for this host, {print_commands} the line that prints its commands, {vault} the vault's for the token's
        secret, and fields as given."""
        lines = {
            "init": Line("init", {}, host_flag=self.flag),
            "print_commands": Line("init", {"print_commands": True}, host_flag=self.flag),
            "vault": Line("vault", {"name": "token", "host": self.host.name}),
        }
        return said(sentence, {**lines, **fields})

    async def call(self, function: Any, *args: Any) -> Any:
        import asyncio

        return await asyncio.to_thread(function, *args)

    async def root_ssh(self, command: Sequence[str]) -> runner.Result:
        fields = self.host.fields
        try:
            values, environ = self.host.values, self.host.environ
            target = routes.host_target(fields.address, fields.ssh_user, fields.ssh_port, values, environ)
            return await runner.run(self.job, target, list(command), "", ROOT_SSH, self.host.environ)
        except (runner.Refused, paths.Refused) as error:
            return runner.Unreachable(_error(error))

    def settle(self, setting: str, value: str) -> bool:
        """value written as setting for the host, where its own settings.yml sets none and the run writes (init's own
        writing rule): whether it was written. The settings the later checks read take it either way."""
        self.settings[setting] = value
        if not self.write or self.own.get(setting) not in (None, ""):
            return False
        home, name, environ = self.host.home, self.host.name, self.host.environ
        try:
            layers = overrides.read(home, name, environ=environ)
            digest = layers.host.digest if layers.host is not None else overrides.MISSING
            overrides.set(home, setting, value, name, digest, environ=environ)
        except (overrides.Refused, paths.Refused, OSError) as error:
            self.say(PROBLEM, f"{setting} {value} could not be written into {self.settings_file} ({_error(error)})")
            return False
        if self.settings_file not in self.wrote:
            self.wrote.append(self.settings_file)
        return True


# ---------------------------------------------------------------- the API, the token and the pool ----


async def _api(at: _Run) -> bool:
    """The API answers, is Proxmox VE 9 or later, and accepts the token: whether the checks that need it go on."""
    from ..pve.answers import ProxmoxError
    from ..pve.client import Denied, TooOld
    from ..vendors.transport import TransportError

    fields = at.host.fields
    where = f"{fields.address}:{fields.api_port}"
    try:
        version = await at.call(at.client.version)
    except TooOld as error:
        at.say(PROBLEM, f"Proxmox at {where} is {_error(error)}", "the engine speaks to Proxmox VE 9 or later alone")
        return False
    except Denied as error:
        at.say(
            PROBLEM,
            f"Proxmox at {where} refused the token {at.token} ({_error(error)})",
            at.fix(
                "a token named otherwise goes in {file} as token_user and token_id, and {vault} puts a wrong secret"
                " right",
                file=f"{hosts.HOSTS_REL}/{at.host.name}/{hosts.HOST_FILE}",
            ),
        )
        return False
    except (ProxmoxError, TransportError, ValueError) as error:
        at.say(PROBLEM, f"nothing that reads as Proxmox answered at https://{where} ({_error(error)})")
        return False
    at.say(OK, f"Proxmox VE {one_line(version.version, 40)} at {where} accepted {at.token}")
    return True


async def _pool(at: _Run) -> None:
    """The pool is there, and its mark names this fleet with controller=none (D2)."""
    from ..pve.answers import ProxmoxError
    from ..pve.client import Denied
    from ..vendors.transport import TransportError

    pool = at.host.pool
    try:
        found = await at.call(at.client.pool, pool)
    except Denied as error:
        at.say(
            PROBLEM,
            f"the token may not read the pool {pool} ({_error(error)})",
            at.fix("{init} at a terminal, with root SSH to the host, grants it there (answer y)"),
        )
        return
    except (ProxmoxError, TransportError, ValueError) as error:
        at.say(
            PROBLEM,
            f"the pool {pool} could not be read ({_error(error)})",
            at.fix(
                "where it is not on the host, {init} at a terminal makes it (answer y), and {print_commands} prints the"
                " commands that do"
            ),
        )
        return
    try:
        found_mark = mark.parse(found.comment)
    except mark.Refused as error:
        at.say(PROBLEM, _error(error))
        return
    rule = mark.may_change(found_mark, at.host.fleet)
    if rule is not None:
        at.say(PROBLEM, rule.removeprefix("error: "))
        return
    at.say(OK, f"the pool {pool} carries this fleet's mark (fleet {at.host.fleet.fleet}, controller=none)")


# ---------------------------------------------------------------- the node ----


async def _node(at: _Run) -> str:
    """The node VMs are cloned on: the one the host's own settings.yml sets, held to the cluster's online nodes, else
    settled - the one node online, else the node root SSH says the host is. "" where it is not known."""
    from ..pve.answers import ProxmoxError
    from ..vendors.transport import TransportError

    try:
        listed = await at.call(at.client.resources, "node")
    except (ProxmoxError, TransportError, ValueError) as error:
        at.say(PROBLEM, f"Proxmox would not list its nodes ({_error(error)})")
        return ""
    # Each name the cluster lists held to Proxmox's own format before it is settled, written, put in a path or said
    # as it is: one outside it is said as JSON alone, and never offered or written.
    reported = [each.node for each in listed if each.node]
    every = proxmox_names(reported, "node")
    online = proxmox_names([each.node for each in listed if each.node and each.status == ONLINE], "node")
    names = _listed(reported, "node", "none")
    refused = proxmox_names_refused(reported, "node")
    if refused:
        listing = ", ".join(refused)
        at.say(NOTE, f"the cluster lists {listing}, no name Proxmox gives a node: it is never offered or written")
    set_now = at.own.get("proxmox_node")
    if set_now not in (None, ""):
        node = str(set_now)
        if node not in every:
            at.say(
                PROBLEM,
                f"the cluster has no node {_name(node, 'node')} ({names})",
                f"set proxmox_node in {at.settings_file} to the node that holds the template",
            )
            return ""
        if node not in online:
            at.say(PROBLEM, f"node {node} is not online, so nothing can be made on it")
            return ""
        at.say(OK, f"node {node}: deploy creates the VMs there")
        return node
    if len(online) == 1:
        node, why = online[0], f"it is the only node online ({names})"
    else:
        address = at.host.fields.address
        result = await at.root_ssh(NODE_COMMAND)
        done = isinstance(result, runner.Done) and result.status == 0
        # One line, one name: held to the online nodes, which are held to Proxmox's format, as E5 holds it.
        named = result.lines[0].strip() if done and len(result.lines) == 1 else ""
        if named not in online:
            if named:
                seen = f"root SSH to {address} says it is {_name(one_line(named, 60), 'node')}"
            elif done:
                seen = f"root SSH to {address} gave no one line for {' '.join(NODE_COMMAND)}"
            else:
                seen = _why(result)
            at.say(
                PROBLEM,
                f"which node {address} is cannot be told ({seen}), and the cluster has {names} online",
                f"set proxmox_node in {at.settings_file}",
            )
            return ""
        node, why = named, f"root SSH to {address} says it is that node"
    if at.settle("proxmox_node", node):
        at.say(OK, f"node {node}: written into {at.settings_file} as proxmox_node, since {why}")
    elif not at.write:
        at.say(
            PROBLEM,
            f"proxmox_node is not set for {at.host.name}",
            at.fix("it is {node}, since {why}, and {init} writes it", node=node, why=why),
        )
    return node


# ---------------------------------------------------------------- the template ----


def _there(found: judging.Compared | None) -> bool:
    """Whether a template was found at its ID, to be read further: not missing, nor unseen."""
    return (
        found is not None and found.template_id is not None and found.outcome not in (judging.MISSING, judging.UNSEEN)
    )


def _bridges(client: Proxmox, found: judging.Compared, nic: str) -> tuple[str, ...]:
    """The bridges of every network card of the template found, which a clone asks SDN.Use on each of."""
    assert found.template_id is not None
    config = client.config(found.node, found.template_id, nic)
    bridges = [
        match.group(1)
        for key, value in sorted(config.values.items())
        if NET.fullmatch(key) and (match := BRIDGE.search(str(value)))
    ]
    return tuple(dict.fromkeys(bridges))


async def _template(at: _Run) -> tuple[judging.Compared | None, tuple[str, ...]]:
    """The base template compared with its settings (E11), said; and the bridges a clone of it asks SDN.Use on: its
    network cards' where it is there, else the bridge it would be built on."""
    from ..pve.answers import ProxmoxError
    from ..vendors.transport import TransportError

    planned = (str(judging.option(at.settings, "proxmox_template_bridge")),)
    try:
        found = await at.call(lambda: template.compare(at.client, at.settings, pool=at.host.pool, host_flag=at.flag))
        there = _there(found) and not found.refused
        bridges = await at.call(_bridges, at.client, found, at.host.nic) if there else planned
    except (ProxmoxError, TransportError, ValueError) as error:
        at.say(PROBLEM, f"the base template could not be read ({_error(error)})")
        return None, planned
    if there:
        # The bridges its cards name, held to Proxmox's format before any is written, put in a path or said as it is.
        refused = proxmox_names_refused(bridges, "bridge")
        bridges = tuple(proxmox_names(bridges, "bridge"))
        if refused:
            at.say(
                PROBLEM,
                f"the template's network cards name {', '.join(refused)}, no name Proxmox gives a bridge, so it is"
                " never written nor checked",
                "set each card's bridge in Proxmox to one the node lists",
            )
    if found.refused:
        at.say(PROBLEM, found.said())
    elif found.usable:
        at.say(OK, f"{found.said()} So deploy needs no root SSH for it")
    else:
        at.say(OK, f"{found.said()} So deploy builds it, over root SSH")
    return found, bridges


async def _root_ssh(at: _Run, found: judging.Compared | None) -> None:
    """Root SSH, where the template is missing, unseen or to be rebuilt: one connection that runs nothing but true."""
    if found is None or found.usable or found.refused:
        return
    fields = at.host.fields
    result = await at.root_ssh(TRUE)
    if isinstance(result, runner.Done) and result.status == 0:
        at.say(OK, f"root SSH to {fields.address} works, as {fields.ssh_user}: deploy builds the template over it")
        return
    at.say(
        PROBLEM,
        f"root SSH to {fields.address} failed ({_why(result)}): deploy builds the template over it",
        f"install your key with ssh-copy-id {fields.ssh_user}@{fields.address}",
    )


# ---------------------------------------------------------------- the privileges ----


def _held(client: Proxmox, path: str) -> frozenset[str]:
    return frozenset(client.permissions(path).privileges)


async def _privileges(at: _Run, node: str, bridges: Sequence[str]) -> None:
    """The permission check: on each path the engine's calls are checked on, what the one table needs there."""
    from ..pve.answers import ProxmoxError
    from ..vendors.transport import TransportError

    kinds = [("pool", at.host.pool), ("storage", str(at.settings["proxmox_storage"]))]
    kinds += [("bridge", bridge) for bridge in bridges]
    if node:
        kinds.append(("node", node))
    checked = []
    for kind, value in kinds:
        key, form = privileges.KINDS[kind]
        path = form.format(**{key: value})
        needed = privileges.needed(kind)
        try:
            held = await at.call(_held, at.client, path)
        except (ProxmoxError, TransportError, ValueError) as error:
            shown = json.dumps(path) if isinstance(error, ValueError) else path
            at.say(PROBLEM, f"Proxmox would not say what the token may do on {shown} ({_error(error)})")
            continue
        missing = sorted(needed - held)
        if not missing:
            checked.append(path)
            continue
        them = "it" if len(missing) == 1 else "them"
        if not needed & held:
            fix = at.fix(
                "grant the role {role} there, which {init} at a terminal, with root SSH to the host, offers to (answer"
                " y), or as root on the host: pveum acl modify {path} --roles {role} --users {user} --tokens '{token}'",
                role=access.ROLE,
                path=path,
                user=at.host.fields.token_user,
                token=at.token,
            )
        else:
            fix = at.fix(
                "add {them} to the token's role, which {init} at a terminal, with root SSH to the host, offers to"
                " (answer y), or as root on the host: pveum role modify {role} --append 1 --privs '{privs}'",
                them=them,
                role=access.ROLE,
                privs=",".join(missing),
            )
        at.say(PROBLEM, f"the token lacks {', '.join(missing)} on {path}", fix)
    if checked:
        at.say(OK, f"the token holds every privilege the engine uses on {', '.join(checked)}")


async def _every_guest(at: _Run, found: judging.Compared | None) -> None:
    """VM.Audit on /vms where proxmox_audit_scope is node, read only: a `--` line where it is not held, and under
    either scope a `!!` line where the token holds more there, which changes every VM of the node; and a template of
    the person's own held to the clone grant on its own path."""
    from ..pve.answers import ProxmoxError
    from ..vendors.transport import TransportError

    scope = str(at.settings["proxmox_audit_scope"])
    try:
        held = await at.call(_held, at.client, judging.GUESTS)
    except (ProxmoxError, TransportError, ValueError) as error:
        at.say(PROBLEM, f"Proxmox would not say what the token may do on {judging.GUESTS} ({_error(error)})")
        return
    beyond = sorted(held - {judging.AUDIT})
    if beyond:
        at.say(
            PROBLEM,
            f"the token holds {one_line(', '.join(beyond), SAID_MOST)} on {judging.GUESTS}, beyond {judging.AUDIT}, so"
            " it can change every VM of the node",
            f"as root on the host, pveum acl list shows the grant on {judging.GUESTS} or above that gives"
            f" {'it' if len(beyond) == 1 else 'them'} to {at.token} or its user, and pveum acl delete removes it",
        )
    elif scope == access.NODE_SCOPE and judging.AUDIT not in held:
        at.say(
            UNCHECKED,
            f"the token may not read {judging.GUESTS} ({judging.AUDIT}), so status will not show the node's other VMs",
            at.fix(
                "{init} at a terminal, with root SSH to the host, grants it (answer y), or set proxmox_audit_scope:"
                " pool to read the pool's alone"
            ),
        )
    elif scope == access.NODE_SCOPE:
        at.say(OK, f"the token reads every guest of the node ({judging.AUDIT} on {judging.GUESTS}) and changes none")
    if found is None or not found.custom or found.template_id is None:
        return
    path = f"{judging.GUESTS}/{found.template_id}"
    try:
        lacking = sorted({judging.AUDIT, access.CLONE} - await at.call(_held, at.client, path))
    except (ProxmoxError, TransportError, ValueError) as error:
        at.say(PROBLEM, f"Proxmox would not say what the token may do on {path} ({_error(error)})")
        return
    if lacking:
        at.say(
            PROBLEM,
            f"the token lacks {', '.join(lacking)} on {path}, the template {found.name} (proxmox_template_custom), so"
            " a clone of it is refused",
            at.fix(
                "{init} at a terminal, with root SSH to the host, grants {role} there (answer y)",
                role=access.CLONE_ROLE,
            ),
        )


# ---------------------------------------------------------------- the route, the storage, the bridge, the key ----


async def _route(at: _Run) -> None:
    """The route to the VMs, settled by engine/routes.py, and where it goes through the host the hop's login: an SSH
    connection that runs nothing."""
    run = _Said(at.job)
    try:
        found = await routes.settle(run, at.host)
        if found.through:
            await found.login()
    except routes.Refused as refused:
        at.say(PROBLEM, refused.sentence)
        return
    if found.through:
        at.say(OK, f"the VMs are reached through {found.label}, and its login works")


async def _storage(at: _Run, node: str) -> None:
    """The storage for VM disks, as the node lists it to the token, settled where the host's settings.yml sets none."""
    from ..pve.answers import ProxmoxError
    from ..vendors.transport import TransportError

    storage = str(at.settings["proxmox_storage"])
    if not node:
        at.say(UNCHECKED, f"the storage {_name(storage, 'storage')} is not checked: the node is not known")
        return
    try:
        reported = [each.storage for each in await at.call(at.client.storages, node)]
    except (ProxmoxError, TransportError, ValueError) as error:
        at.say(PROBLEM, f"Proxmox would not list the storages on {node} ({_error(error)})")
        return
    listed = proxmox_names(reported, "storage")
    if storage not in listed:
        at.say(
            PROBLEM,
            f"storage {_name(storage, 'storage')}: the token cannot see it on {node} ({_listed(reported, 'storage')})",
            f"set proxmox_storage in {at.settings_file} to one it can use",
        )
        return
    written = at.settle("proxmox_storage", storage)
    at.say(OK, f"storage {storage}: the token can use it{f', written into {at.settings_file}' if written else ''}")


async def _bridge(at: _Run, node: str, found: judging.Compared | None, bridges: Sequence[str]) -> None:
    """The bridge: where the template is there, its network cards' (each held to SDN.Use above), the first settled
    where none is set; before it is built, the one in effect, as the node lists it to the token."""
    from ..pve.answers import ProxmoxError
    from ..vendors.transport import TransportError

    if _there(found) and found is not None and not found.refused:
        # bridges holds the names of Proxmox's format alone: where the cards name none, nothing is settled.
        written = bool(bridges) and at.settle("proxmox_bridge", bridges[0])
        if written:
            at.say(OK, f"bridge {bridges[0]}, the template's: written into {at.settings_file}")
        return
    bridge = str(at.settings["proxmox_bridge"])
    if not node:
        at.say(UNCHECKED, f"the bridge {_name(bridge, 'bridge')} is not checked: the node is not known")
        return
    try:
        reported = [each.iface for each in await at.call(at.client.bridges, node)]
    except (ProxmoxError, TransportError, ValueError) as error:
        at.say(PROBLEM, f"Proxmox would not list the bridges on {node} ({_error(error)})")
        return
    listed = proxmox_names(reported, "bridge")
    if bridge not in listed:
        at.say(
            PROBLEM,
            f"bridge {_name(bridge, 'bridge')}: the token cannot see it on {node} ({_listed(reported, 'bridge')})",
            f"set proxmox_bridge in {at.settings_file} to one it can use, before the template is built",
        )
        return
    written = at.settle("proxmox_bridge", bridge)
    at.say(OK, f"bridge {bridge}: the token can use it{f', written into {at.settings_file}' if written else ''}")


def _key(at: _Run) -> None:
    keys = at.host.values["vm_ssh_public_keys"].value or ()
    if keys:
        at.say(OK, f"the VMs get {len(keys)} SSH key{'' if len(keys) == 1 else 's'}")
        return
    at.say(
        PROBLEM,
        "the VMs would get no SSH key, so deploy refuses them",
        "make one with ssh-keygen -t ed25519, which they then get (~/.ssh/id_ed25519.pub), or name yours in"
        " vm_ssh_public_keys",
    )


# ---------------------------------------------------------------- the checks ----


async def run(host: opened.Host, *, job: Job, own: Mapping[str, object], write: bool) -> Checked:
    """Every check against host, opened (fleet/opened.py's connect), in the module's order: own is what the host's
    own settings.yml sets, where a value the checks settle is written only where it sets none, and only with write
    (init's own writing rule; never init --check). The checks that need the API are left, one `--` line, where it
    does not answer or does not take the token; the route and the key are checked either way."""
    at = _Run(host, job, own, write, {name: value.value for name, value in host.values.items()})
    if await _api(at):
        await _pool(at)
        node = await _node(at)
        found, bridges = (None, ()) if not node else await _template(at)
        if not node:
            at.say(UNCHECKED, "the base template is not checked: the node is not known")
        await _privileges(at, node, bridges or (str(judging.option(at.settings, "proxmox_template_bridge")),))
        await _every_guest(at, found)
        await _root_ssh(at, found)
        await _storage(at, node)
        await _bridge(at, node, found, bridges)
    else:
        at.say(UNCHECKED, "nothing else of the Proxmox host is checked: the API did not take the token, as above")
    await _route(at)
    _key(at)
    return Checked(tuple(at.checks), tuple(at.wrote))


async def check(
    home: paths.Home, host: str, *, environ: Mapping[str, str], job: Job, write: bool
) -> tuple[Checked, str]:
    """The checks against the host called host ("" for the one a run reaches with none named), opened from the
    fleet's files as every workflow opens it, lenient as settings and vault are, since the node is the checks' to
    settle; and the host's flag a line names. A host that cannot be opened is one `--` line: nothing is checked."""
    layout = hosts.Layout(home, environ)
    try:
        reached = hosts.reach(layout, model.Request(host=host, lenient=True))
        own_layers = overrides.read(home, reached.host.name, environ=environ).host
        opened_host = opened.connect(home, reached, environ=environ)
    except (hosts.Refused, opened.Refused, overrides.Refused, paths.Refused) as error:
        flag = host
        return Checked((Check(UNCHECKED, f"nothing is checked on the Proxmox host: {_error(error)}"),)), flag
    with opened_host:
        own = {} if own_layers is None else dict(own_layers.settings)
        found = await run(opened_host, job=job, own=own, write=write)
        return found, opened_host.host_flag
