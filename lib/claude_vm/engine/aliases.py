"""The aliases: one SSH alias a VM in the managed SSH config on this computer, so that `ssh alpha` and the editor follow
the VM's address, each block naming the host the VM is on (card G8; docs/architecture/engine.md §16.4).

The sequence of today's playbooks/tasks/ssh_config.yml in plain code, over the pure functions it calls, unchanged:
proxmox.py's `ssh_config_blocks`, `ssh_config_elsewhere` and `ssh_config_prune`, and checks.py's `ssh_alias_refusals`
and `ssh_hostname`. The files (`Files.of`) are under the person's home: the managed file `~/.ssh/agent-warehouse.conf`
(fleet/hosts.py's ALIASES), the person's own `~/.ssh/config`, which gets one `Include` line and nothing else, and the
product's known-hosts file beside them, which each block names.

**A write** (`write`) is refused, nothing written, where a name or an address is not one plain word, or an identity is
not one of this product's (`checks.HOST_KEY_ALIAS_RE`); where a name is one ssh would take for another host
(`checks.ssh_alias_refusals`: an address, a name this computer reaches a host of the fleet by, a dot the person did not
name) or one whose block records another host of the fleet (`ssh_config_elsewhere`), named; where the person's own
config has a `Host` line of the name; and where `claude_ssh_options` sets what the block writes itself. Fleet-wide (no
name given) a VM of such a name is left out and said, and the rest written. A block holds what ssh reads and one
comment, `# origin: <host>`: `HostName` (the address, or `<name>.<domain>` with `claude_ssh_host_domain` and the route
direct), `User`, a `ProxyJump` where the route gives one, `HostKeyAlias` the VM's identity, `UserKnownHostsFile` the
product's file, `CheckHostIP no`, `UpdateHostKeys no`, `StrictHostKeyChecking` accept-new (or stricter, through the
options), `IdentityFile` where set, `ForwardAgent` (`no` unless `claude_ssh_forward_agent`), then the options as given.
The file is read, its new text worked out and handed to the real ssh, `ssh -G -F <it> <alias>` for each alias written,
from a scratch file of that check's own in the run's directory, and only then written: under `store.locked`, the file
read again and replaced whole (`store.replace`) only while it is what was read, else worked out again. Then the
`Include`: none where the line is there before the file's first `Host` or `Match` line (where ssh reads it for every
host); otherwise only where `ssh -G -F ~/.ssh/config <first alias>` does not come back with the `HostName` just written,
or no line of the file names the managed file in a spelling ssh reads: one line at its top, ending as the file's first
line does, the rest of the file byte for byte and its mode kept; never where `claude_ssh_config_include` is off, which
says the line to add. The person's file is read with its line endings as they are; the managed file, which the product
writes, with every line ending read as LF, so a CRLF copy's blocks are still found.

**`code`'s alias** (`one`, card G17) is its one VM's, written the same way from the read `code` made, before the
window that goes through it: refused, nothing written, where it is not written.

**A removal** (`remove`) takes each named block out, under the same lock, and nothing else; the `Include` stays.
**The prune** (`refresh` with no names) reads the host's listing and its pool, and removes only what `ssh_config_prune`
says: a block of this host's whose VM no longer exists, and one no alias may have; never another host's. What is the
fleet's is engine/fleet.py's `refusal`, pve/ownership.py's one rule, never a tag.

`~/.ssh/known_hosts` is never read or written here, and a VM's key is never filed or removed here: the alias points the
person's own ssh at the product's file, under the VM's identity, where the engine's connections filed it
(ssh/hostkeys.py), and a deleted VM's key stays there. Every ssh is started by runs/children.py's `start`, under a
limit; asyncio is imported where it is used.
"""

from __future__ import annotations

import dataclasses
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from .. import checks, store
from .. import route as hop_rules
from ..fleet import hosts, paths
from ..proxmox import ssh_config_blocks, ssh_config_elsewhere, ssh_config_prune
from ..pve.answers import ProxmoxError
from ..runs.stages import Declared, Stage
from ..schema.printer import line, said
from ..schema.words import Line
from ..ssh.resolved import resolved
from ..ssh.runner import SSH_PROGRAM
from ..text import one_line
from ..vendors.transport import TransportError
from . import fleet, report, routes

if TYPE_CHECKING:
    from ..runs.attached import Context
    from ..runs.job import Job
    from ..ssh import runner

# deploy --only ssh-config's one stage, as today's declaration labels it (runs/stages.py's DEPLOY): the host's work.
STAGE = "aliases"
DECLARED = Declared("deploy", (Stage(STAGE, "SSH aliases", each_machine=False),))
# NEXT STEPS' matter of every report the aliases say (run/next_steps.py's MATTERS).
MATTER = "aliases"
# A block's markers, as today's blockinfile writes them.
MARK = "agent-warehouse"
# The product's known-hosts file, beside the managed one, and the person's own config.
KNOWN_HOSTS = "agent-warehouse.known_hosts"
USER_CONFIG = "config"
# One plain word of an ssh config; a ProxyJump as route.jump_spec writes one.
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
JUMP = re.compile(r"[A-Za-z0-9_.@:\[\]-]+")
# What the block writes itself, which claude_ssh_options may not set, and what it may make StrictHostKeyChecking.
PINNED = ("hostname", "user", "hostkeyalias", "userknownhostsfile", "checkhostip", "updatehostkeys", "forwardagent",
          "identityfile")  # fmt: skip
STRICTER = ("yes", "ask", "accept-new")
STRICT = "stricthostkeychecking"
JUMPS = ("proxyjump", "proxycommand")
# The start of the name of each scratch file in the run's directory that ssh validates the managed file's next text
# from: one of its own for each check, so that writes of one run side by side never read or remove each other's.
SCRATCH = "ssh-config-check-"
# The first line of an ssh config's first block: an Include after it is read for the hosts that block matches alone.
BLOCK_START = re.compile(r"\s*(?:host|match)(?:\s|=)", re.I)
# How many times a write is worked out again where the file changed between its read and its write.
TRIES = 5
# The tag the prune's listing gives a VM the fleet owns, or one the pool lists with no name (a node that is down), and
# the pattern ssh_config_prune is handed for it.
OWNED = "owned"
OWNED_PATTERN = r"\Aowned\Z"
# How to reach an alias written: ssh to it, and the editor's window on it, with the workspace it opens where known.
CONNECT = "{name}: ssh {name}   |   {line}"
CONNECT_OPENS = "{name}: ssh {name}   |   {line}   (opens {opens})"


class Refused(Exception):
    """An alias not written, nothing written: why, in one sentence with no "error: " before it."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


@dataclasses.dataclass(frozen=True)
class Files:
    """The managed SSH config, the person's own ssh config and the product's known-hosts file: under the person's home,
    or a test's own folder."""

    config: Path
    user: Path
    known_hosts: Path

    @classmethod
    def of(cls, environ: Mapping[str, str]) -> Files:
        """The files under the home environ names (HOME), as fleet/hosts.py's ALIASES names the managed one."""
        managed = paths.user_home(environ).joinpath(*hosts.ALIASES)
        return cls(managed, managed.parent / USER_CONFIG, managed.parent / KNOWN_HOSTS)


@dataclasses.dataclass(frozen=True)
class Alias:
    """One VM's alias: its name, its address ("" where a domain names it), the account, its identity
    (agent-warehouse-<SMBIOS UUID>, "" for none), the ProxyJump the route gives it ("" for none), and the workspace,
    for the line that says how to connect alone."""

    name: str
    address: str
    user: str
    identity: str = ""
    jump: str = ""
    workspace: str = ""


@dataclasses.dataclass(frozen=True)
class Rules:
    """What a write is held to: the host the VMs are on (origin), the fleet's other hosts, the names this computer
    reaches a host by, the names given (none: fleet-wide), the settings of the block (claude_ssh_config_include,
    claude_ssh_host_domain where the route is direct, claude_ssh_identity_file, claude_ssh_forward_agent,
    claude_ssh_options), the host's flag a line ends with, the ssh to ask, and the environment it is started with."""

    origin: str
    others: tuple[str, ...] = ()
    reached_by: tuple[str, ...] = ("localhost",)
    chosen: tuple[str, ...] = ()
    include: bool = True
    domain: str = ""
    identity_file: str = ""
    forward_agent: bool = False
    options: Mapping[str, object] = dataclasses.field(default_factory=dict)
    host_flag: str = ""
    ssh: str = SSH_PROGRAM
    environ: Mapping[str, str] = dataclasses.field(default_factory=dict, repr=False)

    @property
    def named(self) -> bool:
        """Whether names were given: then a name that cannot be an alias is refused, never left out."""
        return bool(self.chosen)

    @property
    def own_jump(self) -> bool:
        """Whether claude_ssh_options names a jump of the person's own, which takes the hop's place."""
        return any(str(key).lower() in JUMPS for key in self.options)

    @classmethod
    def of(cls, host: fleet.Host, names: Sequence[str], *, through: bool = False) -> Rules:
        """The rules of a run against host with names given; through, whether the route goes through the host, where
        no domain is used: the host's sshd would resolve the name itself."""
        values = {name: value.value for name, value in host.values.items()}
        options = values.get("claude_ssh_options") or {}
        return cls(
            origin=host.name,
            others=tuple(name for name in host.hosts_named if name not in ("localhost", host.name)),
            reached_by=tuple(host.reached_by),
            chosen=tuple(names),
            include=values.get("claude_ssh_config_include") is not False,
            domain="" if through else str(values.get("claude_ssh_host_domain") or ""),
            identity_file=str(values.get("claude_ssh_identity_file") or ""),
            forward_agent=values.get("claude_ssh_forward_agent") is True,
            options=dict(options) if isinstance(options, Mapping) else {},
            host_flag=host.host_flag,
            ssh=str(values.get("ssh_executable") or SSH_PROGRAM),
            environ=dict(host.environ),
        )


@dataclasses.dataclass(frozen=True)
class Written:
    """What a write did: the names written, those left out ({name, why, unnamed}), those whose block records another
    host ({name, origin}), and what became of the Include: added, there, off or none (nothing written)."""

    written: tuple[str, ...] = ()
    left_out: tuple[dict, ...] = ()
    theirs: tuple[dict, ...] = ()
    include: str = "none"


# ------------------------------------------------------------------------------------------- the text ----


def _marked(name: str) -> re.Pattern[str]:
    return re.compile(rf"^# BEGIN {MARK}: {re.escape(name)}\n.*?^# END {MARK}: {re.escape(name)}$\n?", re.M | re.S)


def put(text: str, name: str, body: str) -> str:
    """text with name's block holding body: in its place where it is there, else at the end, as blockinfile puts it."""
    block = f"# BEGIN {MARK}: {name}\n{body}# END {MARK}: {name}\n"
    found = _marked(name).search(text)
    if found:
        return text[: found.start()] + block + text[found.end() :]
    if text and not text.endswith("\n"):
        text += "\n"
    return text + block


def cut(text: str, name: str) -> str:
    """text with name's block taken out, and nothing else."""
    return _marked(name).sub("", text, count=1)


def strictness(options: Mapping[str, object]) -> str:
    """The StrictHostKeyChecking a block writes: the options' own, else accept-new."""
    given = [str(value) for key, value in options.items() if str(key).lower() == STRICT]
    return given[0] if given else "accept-new"


def hostname(alias: Alias, rules: Rules) -> str:
    """Where alias points: checks.ssh_hostname's answer, under the rules' domain."""
    return checks.ssh_hostname({"name": alias.name, "address": alias.address}, rules.domain)


def body(alias: Alias, rules: Rules, files: Files) -> str:
    """The lines of alias's block, as today's task file renders them: its origin, then what ssh reads."""
    lines = [f"# origin: {rules.origin}"] if rules.origin else []
    lines += [f"Host {alias.name}", f"    HostName {hostname(alias, rules)}", f"    User {alias.user}"]
    lines += [f"    ProxyJump {alias.jump}"] if alias.jump else []
    lines += [f"    HostKeyAlias {alias.identity}"] if alias.identity else []
    lines += [f"    UserKnownHostsFile {checks.vm_ssh_word(str(files.known_hosts))}", "    CheckHostIP no"]
    lines += ["    UpdateHostKeys no", f"    StrictHostKeyChecking {strictness(rules.options)}"]
    lines += [f"    IdentityFile {rules.identity_file}"] if rules.identity_file else []
    lines += [f"    ForwardAgent {'yes' if rules.forward_agent else 'no'}"]
    lines += [f"    {key} {value}" for key, value in rules.options.items() if str(key).lower() != STRICT]
    return "".join(f"{each}\n" for each in lines)


def read(path: Path, *, exact: bool = False) -> str | None:
    """path's text, None where there is no file: Refused where it cannot be read. exact (the person's own config), its
    line endings as they are, so a CRLF file is put back byte for byte; else (the managed file, which the product
    writes) every line ending read as LF, so a CRLF copy's blocks are found and the file is written back as LF."""
    try:
        with path.open(encoding="utf-8", newline="" if exact else None) as stream:
            return stream.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise Refused(f"{path} cannot be read ({one_line(str(error), 200)}), so nothing was written") from None


def user_hosts(text: str) -> list[str]:
    """Every name a Host line of the person's config names, several on one line among them."""
    return [word for found in re.findall(r"^\s*Host\s+(.+?)\s*$", text, re.M | re.I) for word in found.split()]


def _spellings(files: Files, environ: Mapping[str, str]) -> list[str]:
    """Each way a line of the person's config may name the managed file that ssh reads as it: its path, ~/... and
    ${HOME}/... under the home, and its bare name beside the person's config."""
    spelled = [str(files.config)]
    home = str(environ.get("HOME") or "").rstrip("/")
    if home and str(files.config).startswith(home + "/"):
        rest = str(files.config)[len(home) :]
        spelled += [f"~{rest}", f"${{HOME}}{rest}"]
    if files.config.parent == files.user.parent:
        spelled.append(files.config.name)
    return spelled


def ahead(text: str, wanted: str) -> bool:
    """Whether wanted is a line of text before its first Host or Match line, where ssh reads it for every host."""
    for each in text.splitlines():
        if each == wanted:
            return True
        if BLOCK_START.match(each):
            return False
    return False


def included(text: str, files: Files, environ: Mapping[str, str], shown: str, name: str) -> bool:
    """Whether the person's config reaches the managed file: a line of it names the file in a spelling ssh reads, and
    ssh -G of the first alias through it (shown) gives the HostName just written (name)."""
    names = "|".join(re.escape(each) for each in _spellings(files, environ))
    there = re.search(rf"^\s*include\s+({names})\s*$", text, flags=re.M | re.I) is not None
    return there and f"hostname {name}" in shown.splitlines()


# ------------------------------------------------------------------------------------------- ssh -G ----


async def shown(job: Job, rules: Rules, config: Path | str, *destination: str) -> tuple[bool, str, str]:
    """ssh -G -F config, then destination's words, through ssh/resolved.py: whether it answered 0, what it said and its
    standard error."""
    return await resolved(job, rules.ssh, config, rules.environ, *destination)


# ------------------------------------------------------------------------------------------- a write ----


def _decided(aliases: Sequence[Alias], rules: Rules, kept: list[dict]) -> tuple[list[Alias], list[dict], list[dict]]:
    """Which aliases are written, which are left out ({name, why, unnamed}) and which keep another host's block
    ({name, origin}): named, each of those refuses the write instead."""
    refusals = checks.ssh_alias_refusals([{"name": each.name} for each in aliases], list(rules.reached_by),
                                         list(rules.chosen) or None)  # fmt: skip
    asked = {each.name for each in aliases}
    theirs = [block for block in ssh_config_elsewhere(kept, rules.origin, list(rules.others)) if block["name"] in asked]
    if rules.named and refusals:
        whys = "; ".join(f"{each['name']}: {each['why']}" for each in refusals)
        raise Refused(
            f"Not writing an alias for {', '.join(each['name'] for each in refusals)}: an alias is the VM's name, and"
            f" ssh would take it for another host ({whys}). Rename the VM in Proxmox."
        )
    if rules.named and theirs:
        that = "that name" if len(theirs) == 1 else "those names"
        origins = ", ".join(dict.fromkeys(block["origin"] for block in theirs))
        raise Refused(
            f"Not writing an alias for {', '.join(block['name'] for block in theirs)}: the alias of {that} is the VM of"
            f" {origins}, as its block in the managed SSH config records, and a VM name belongs to one host. Rename"
            " this host's VM in Proxmox."
        )
    out = {each["name"] for each in refusals} | {block["name"] for block in theirs}
    return [each for each in aliases if each.name not in out], refusals, theirs


def _held(aliases: Sequence[Alias], rules: Rules, user_text: str) -> None:
    """Every refusal of a write that reads nothing of the managed file: Refused for the first that holds."""
    unsafe = [
        each.name
        for each in aliases
        if not WORD.fullmatch(each.name)
        or not WORD.fullmatch(each.user)
        or (not rules.domain and not WORD.fullmatch(each.address))
        or (each.identity and not checks.HOST_KEY_ALIAS_RE.fullmatch(each.identity))
        or (each.jump and not JUMP.fullmatch(each.jump))
    ]
    if unsafe:
        named = ", ".join(repr(name) for name in dict.fromkeys(unsafe))
        raise Refused(
            f"Not writing an alias for {one_line(named, 400)}: its name, account, address or identity is not one plain"
            " word, and ssh would read the rest as options of its own. The address is what the VM's guest agent"
            " reported."
        )
    pinned = [str(key) for key in rules.options if str(key).lower() in PINNED]
    loose = [str(value) for key, value in rules.options.items() if str(key).lower() == STRICT]
    loose = [value for value in loose if value.lower() not in STRICTER]
    if pinned or loose:
        sets = ", ".join(pinned + [f"StrictHostKeyChecking {value}" for value in loose])
        raise Refused(
            f"claude_ssh_options sets {sets}, which the alias block writes itself: agent forwarding is"
            " claude_ssh_forward_agent, and the host key's checking may only be made stricter (yes or ask). Take that"
            " out of claude_ssh_options."
        )
    theirs = set(user_hosts(user_text))
    taken = [each.name for each in aliases if each.name in theirs]
    if taken:
        raise Refused(
            f"your own ssh config already has a Host entry named {', '.join(taken)}, and the alias this project keeps"
            " for a VM is the VM's name. The managed file is included ahead of your own entries, so writing it would"
            " silently override yours. Rename the VM, or rename that entry in your ssh config."
        )


def swap(path: Path, before: str | None, after: str, *, exact: bool = False) -> bool:
    """path replaced whole by after, under the short file lock, only while it is still before (None: no file): whether
    it was, read as read reads it (exact). Its folder is made at the folders' mode where it is missing; a file made has
    the files' mode, and one there keeps its own."""
    path.parent.mkdir(mode=paths.DIR_MODE, parents=True, exist_ok=True)
    with store.locked(path):
        if read(path, exact=exact) != before:
            return False
        store.replace(path, after, mode_if_new=paths.FILE_MODE)
    return True


def _scratch(folder: Path, text: str) -> Path:
    """A scratch file of its own in folder, made 0600 by mkstemp, holding text, for ssh to validate: its path."""
    handle, name = tempfile.mkstemp(dir=folder, prefix=SCRATCH)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


async def _validated(job: Job, rules: Rules, text: str, names: Sequence[str]) -> None:
    """text handed to the real ssh as a config of its own, from a scratch file of this check's own in the run's
    directory, removed after, for each alias in names: Refused where ssh refuses it."""
    import asyncio

    from ..runs import rundir

    # path_for holds the job to this process's own and to a run with its directory; the file is mkstemp's.
    check = await asyncio.to_thread(_scratch, rundir.path_for(job, SCRATCH).parent, text)
    try:
        for name in names:
            ok, _, err = await shown(job, rules, check, name)
            if not ok:
                said_back = one_line(err.replace(str(check), "the managed SSH config").strip(), 300) or "nothing"
                raise Refused(
                    f"The alias of {name} failed to validate (ssh -G said: {said_back}), so the managed SSH config was"
                    " left as it was. Look at claude_ssh_options."
                )
    finally:
        await asyncio.to_thread(check.unlink, missing_ok=True)


async def write(job: Job, files: Files, aliases: Sequence[Alias], rules: Rules) -> Written:
    """Each of aliases written into the managed file, then the Include, as the module says: Refused, nothing written,
    for a refusal. The text is validated in the run's directory (runs/rundir.py), which job's run has."""
    import asyncio

    for _ in range(TRIES):
        before = await asyncio.to_thread(read, files.config)
        chosen, left_out, theirs = _decided(aliases, rules, ssh_config_blocks(before or ""))
        if not chosen:
            return Written((), tuple(left_out), tuple(theirs))
        _held(chosen, rules, await asyncio.to_thread(read, files.user, exact=True) or "")
        after = before or ""
        for each in chosen:
            after = put(after, each.name, body(each, rules, files))
        if after != (before or "") or before is None:
            await _validated(job, rules, after, [each.name for each in chosen])
            if not await asyncio.to_thread(swap, files.config, before, after):
                continue
        include = await _include(job, files, rules, chosen[0])
        return Written(tuple(each.name for each in chosen), tuple(left_out), tuple(theirs), include)
    raise Refused(f"{files.config} changed under every one of {TRIES} writes, so the aliases were not written")


async def _include(job: Job, files: Files, rules: Rules, first: Alias) -> str:
    """The Include at the top of the person's config, where ssh says it does not reach the managed file yet and the line
    is not there already: added, there, or off."""
    import asyncio

    if not rules.include:
        return "off"
    wanted = f"Include {files.config}"
    for _ in range(TRIES):
        text = await asyncio.to_thread(read, files.user, exact=True)
        if text is not None:
            if ahead(text, wanted):
                return "there"
            _, out, _ = await shown(job, rules, files.user, first.name)
            if included(text, files, rules.environ, out, hostname(first, rules)):
                return "there"
        # The line ends as the file's first line does, so a CRLF file stays one.
        end = "\r\n" if (text or "").partition("\n")[0].endswith("\r") else "\n"
        if await asyncio.to_thread(swap, files.user, text, f"{wanted}{end}" + (text or ""), exact=True):
            return "added"
    raise Refused(f"{files.user} changed under every one of {TRIES} writes, so its Include line was not added")


async def remove(files: Files, names: Sequence[str]) -> tuple[str, ...]:
    """Each of names' blocks taken out of the managed file, and nothing else: the names whose block was there."""
    import asyncio

    for _ in range(TRIES):
        before = await asyncio.to_thread(read, files.config)
        if before is None:
            return ()
        there = tuple(name for name in dict.fromkeys(names) if _marked(name).search(before))
        after = before
        for name in there:
            after = cut(after, name)
        if not there or await asyncio.to_thread(swap, files.config, before, after):
            return there
    raise Refused(f"{files.config} changed under every one of {TRIES} writes, so no alias was removed")


# ------------------------------------------------------------------------------------------- what is said ----


def _say(ctx: Context, text: str, then: str = "") -> None:
    report.say(ctx, None, MATTER, text, then)


def _again(names: Sequence[str], host_flag: str) -> Line:
    """deploy --only ssh-config, for names or (none) fleet-wide."""
    values: dict[str, object] = {"only": "ssh-config"}
    if names:
        values["names"] = list(names)
    return Line("deploy", values, host_flag=host_flag)


def _line(found: Line) -> str:
    return line(found.verb, found.values, refused=False, host_flag=found.host_flag)


def said_written(ctx: Context, done: Written, rules: Rules, files: Files, aliases: Sequence[Alias]) -> None:
    """What a write says: who keeps another host's alias, who got none and why, how to connect to each alias written,
    and the Include line to add where it is off."""
    if done.theirs:
        listed = "; ".join(f"{each['name']} (the alias of that name is {each['origin']}'s VM)" for each in done.theirs)
        vms = "VM" if len(done.theirs) == 1 else "VMs"
        _say(
            ctx,
            f"No alias for {listed}: a VM name belongs to one host, and the alias stays with the host its block"
            f" records. Rename this host's {vms} in Proxmox.",
        )
    if done.left_out:
        whys = "; ".join(f"{each['name']} ({each['why']})" for each in done.left_out)
        unnamed = [each["name"] for each in done.left_out if each["unnamed"]]
        renamed = [each["name"] for each in done.left_out if not each["unnamed"]]
        text = (
            f"No alias for {whys}: an alias is the VM's name, and the file ssh reads first would send this computer's"
            " own connections there. The other VMs' aliases are written."
        )
        if unnamed:
            one = len(unnamed) == 1
            text += said(
                " A name with a dot gets its alias only from a run that names it, so that a VM someone else named"
                " github.com never gets one: {line}, if {it} yours. An alias {has} already is left as it was.",
                {
                    "line": _again(unnamed, rules.host_flag),
                    "it": "it is" if one else "they are",
                    "has": "it has" if one else "they have",
                },
            )
        if renamed:
            whom = "it" if len(renamed) == 1 else "them"
            text += f" Rename {', '.join(renamed)} in Proxmox, or ask whoever made {whom}."
        _say(ctx, text, _line(_again(unnamed, rules.host_flag)) if unnamed else "")
    by_name = {each.name: each for each in aliases}
    for name in done.written:
        workspace = by_name[name].workspace if name in by_name else ""
        filled = {"name": name, "line": Line("code", {"name": name}, host_flag=rules.host_flag), "opens": workspace}
        connect = CONNECT_OPENS if workspace else CONNECT
        _say(ctx, said(connect, filled))
    if done.written and done.include == "off":
        _say(
            ctx,
            f"Not added to {files.user} (claude_ssh_config_include is off). Put this line at its top: Include"
            f" {files.config}",
        )


# ------------------------------------------------------------------------------------------- the workflow ----


def listing(host: fleet.Host) -> list[dict]:
    """The host's VMs as ssh_config_prune reads a cluster's listing: each the cluster lists as far as the token may see,
    and each member of the pool, tagged OWNED where engine/fleet.py's refusal says the fleet owns it, or where the pool
    lists it with no name (a node that is down: any block could be its). fleet.Refused where either cannot be read."""
    everyone = fleet.members(host)
    try:
        listed = host.client.resources("vm")
    except (ProxmoxError, TransportError) as error:
        raise fleet.Refused(f"{host.name}'s VMs could not be listed: {fleet.sentence(error)}") from None
    found: dict[int, dict] = {}
    for entry in listed:
        if entry.type == fleet.QEMU and entry.vmid is not None:
            found[entry.vmid] = {"type": fleet.QEMU, "vmid": entry.vmid, "name": entry.name or "", "tags": ""}
    for each in everyone:
        found.setdefault(each.vmid, {"type": fleet.QEMU, "vmid": each.vmid, "name": each.name, "tags": ""})
        if not each.name or not fleet.refusal(each):
            found[each.vmid]["tags"] = OWNED
    return list(found.values())


async def _jumps(ctx: Context, route: routes.Route | None, machines: Sequence[fleet.Target], rules: Rules) -> dict:
    """Each machine whose alias may be written, by name, with the ProxyJump it gains ("" for none). Direct, or through
    a jump of the person's own in claude_ssh_options, none. Through the host, only once the route's targets returned for
    it (its forward checked), and only for one whose Target takes the hop: one with a jump of its own keeps its own.
    Through a host that cannot be the hop (route None: its login failed), no machine's: said, and nothing written."""
    if route is not None and (not route.through or rules.own_jump):
        return {machine.name: "" for machine in machines}
    if route is None:
        _say(ctx, _unusable_said("the Proxmox host", ""))
        return {}
    jumps: dict[str, str] = {}
    for machine in machines:
        if not machine.address:
            continue
        try:
            [target] = await route.targets([machine])
        except routes.Unusable as unusable:
            _say(ctx, _unusable_said(route.label, unusable.sentence))
            return {}
        except routes.Refused as refused:
            _say(ctx, f"No alias for {machine.name}: {one_line(refused.sentence, 400)}")
            continue
        jumps[machine.name] = route.proxy_jump if target.hop is not None else ""
    return jumps


def _unusable_said(label: str, why: str) -> str:
    because = f" ({one_line(why, 300).rstrip('.')})" if why else " (said above)"
    return (
        f"No alias was written through {label}, which cannot be used as the hop{because}; those already in the"
        " managed SSH config are left as they are. Set vm_ssh_route: direct where this computer reaches the VMs'"
        " network, or give claude_ssh_options a ProxyJump of your own."
    )


async def _hop_key(ctx: Context, route: routes.Route, files: Files, rules: Rules) -> None:
    """Where the aliases go through the host and ssh would not offer it the key the host's settings name, the lines
    that give the jump that key: a ProxyJump carries none, and the jump reads the person's own config for the host."""
    hop = route.settled.get("hop")
    key = str(hop.get("key") or "") if isinstance(hop, Mapping) else ""
    if not key or not isinstance(hop, Mapping):
        return
    config = files.user if files.user.is_file() else Path(os.devnull)
    place = ["-p", str(hop["port"])] if hop.get("port") else []
    where = f"{hop['user']}@{hop['host']}" if hop.get("user") else str(hop["host"])
    _, out, _ = await shown(ctx.job, rules, config, *place, where)
    if not hop_rules.hop_key_offered(out, key):
        _say(
            ctx,
            f"The aliases reach their VMs through {route.label}, and ssh may not offer it the key the host's settings"
            f" name for it ({key}): a ProxyJump carries none, and the jump reads your own ssh config for the host. A"
            f" key the agent holds works either way; otherwise add to {files.user}:\n  Host {hop['host']}\n"
            f"      IdentityFile {key}",
        )


async def refresh(ctx: Context, host: fleet.Host, names: Sequence[str]) -> bool:
    """deploy --only ssh-config: each VM named, or every VM of the pool the fleet owns, given its alias from a live read
    of the fleet; with no names, the prune. Read-only against Proxmox. True once done: Refused (this module's,
    engine/fleet.py's or engine/routes.py's) for a refusal, nothing written."""
    import asyncio

    ctx.emit("stage", None, STAGE)
    if not host.aliases:
        _say(ctx, "claude_ssh_config is false, so this project keeps no SSH aliases on this computer. Nothing to do.")
        ctx.emit("stage", None, STAGE, outcome="skipped")
        return True
    files = Files.of(host.environ)
    # This run connects to no VM: a host that cannot be the hop stops nothing here, and the prune still runs.
    route: routes.Route | None
    try:
        route = await routes.settle(ctx, host)
    except routes.Unusable:
        route = None
    through = route is None or route.through
    found = await asyncio.to_thread(fleet.requested, host, tuple(names), change=False)
    for each in found.said:
        _say(ctx, each)
    rules = Rules.of(host, names, through=through)
    owned = []
    for target in found.targets:
        why = fleet.refusal(target)
        if why and names:
            raise Refused(f"Not writing an alias for {target.name}: {why}")
        if why:
            _say(ctx, f"Left out: {why}.")
        elif target.name:
            owned.append(target)
    reached = [target for target in owned if target.address or rules.domain]
    unaddressed = [target.name for target in owned if target not in reached]
    jumps = await _jumps(ctx, route, reached, rules)
    workspace = str(host.values["vm_projects_dir"].value or "") if "vm_projects_dir" in host.values else ""
    aliases = [
        Alias(target.name, target.address, target.user, target.identity, jumps[target.name], workspace)
        for target in reached
        if target.name in jumps
    ]
    done = await write(ctx.job, files, aliases, rules) if aliases else Written()
    said_written(ctx, done, rules, files, aliases)
    if route is not None and any(each.jump for each in aliases if each.name in done.written):
        await _hop_key(ctx, route, files, rules)
    count = len(done.written)
    _say(ctx, f"{count} alias{'es' if count != 1 else ''} refreshed in {files.config}.")
    if unaddressed:
        _say(
            ctx,
            f"Not refreshed, no address (stopped, booting, or without the agent): {', '.join(unaddressed)}. An alias"
            " they had is kept as it was.",
        )
    if names:
        _say(ctx, said("Only the VMs named were refreshed; a fleet-wide {line} also prunes.",
                       {"line": _again((), rules.host_flag)}))  # fmt: skip
    else:
        await _prune(ctx, host, files, rules)
    ctx.emit("stage", None, STAGE, outcome="done")
    return True


async def one(
    ctx: Context,
    host: fleet.Host,
    route: routes.Route,
    machine: fleet.Target,
    reached: runner.Target,
    rules: Rules,
    workspace: str,
) -> Alias:
    """`code`'s alias (card G17): machine's alone, from the live read `code` made, written before its window opens,
    which goes through it, and said as a write is: through the hop only where the route's Target for it takes it
    (reached, from Route.targets), a jump of the person's own in claude_ssh_options in its place, with the lines that
    give the hop the host's key where it goes through it. Refused, nothing written, where it is not written: the window
    would have nothing to reach the VM through."""
    files = Files.of(host.environ)
    jump = route.proxy_jump if route.through and not rules.own_jump and reached.hop is not None else ""
    alias = Alias(machine.name, machine.address, machine.user, machine.identity, jump, workspace)
    done = await write(ctx.job, files, [alias], rules)
    said_written(ctx, done, rules, files, [alias])
    if machine.name not in done.written:
        raise Refused(f"No alias was written for {machine.name}, so the editor would have nothing to reach it through")
    if jump:
        await _hop_key(ctx, route, files, rules)
    return alias


async def _prune(ctx: Context, host: fleet.Host, files: Files, rules: Rules) -> None:
    """The fleet-wide prune: ssh_config_prune's answer, from the host's listing, removed and said."""
    import asyncio

    listed = await asyncio.to_thread(listing, host)
    kept = ssh_config_blocks(await asyncio.to_thread(read, files.config) or "")
    plan = ssh_config_prune(kept, listed, OWNED_PATTERN, list(rules.reached_by), rules.origin, list(rules.others))
    if plan["prune"] or plan["refused"]:
        await remove(files, [*plan["prune"], *plan["refused"]])
    if plan["prune"]:
        _say(ctx, f"Pruned, no VM of that name exists any more: {', '.join(plan['prune'])}.")
    if plan["refused"]:
        _say(
            ctx,
            "Removed, a name no alias may have (an address, or one this computer reaches a host of the fleet by):"
            f" {', '.join(plan['refused'])}. Rename the VM in Proxmox.",
        )
    if plan["untagged"]:
        _say(
            ctx,
            f"Left alone: {', '.join(plan['untagged'])}. A VM of that name exists but is not the fleet's, so it is not"
            " this project's. Remove the alias by hand if it is stale.",
        )
    if plan["elsewhere"]:
        listed_said = ", ".join(f"{each['name']} ({each['origin']})" for each in plan["elsewhere"])
        _say(
            ctx,
            f"Left alone, recorded for a host this fleet does not have: {listed_said}. The prune reads only"
            f" {rules.origin}'s VMs: remove such a block from {files.config} by hand once its VM is gone.",
        )
    if plan["unknown"]:
        _say(
            ctx,
            f"Left alone, no host recorded: {', '.join(plan['unknown'])}. With more than one host, any of them could"
            " have that VM; the next write of its alias records its host, and a prune against that host removes it.",
        )
    if plan["nameless"]:
        could = ", ".join(plan["held"]) if plan["held"] else "any alias"
        _say(
            ctx,
            f"Nothing pruned: VMID {', '.join(plan['nameless'])} is a member of the pool {host.pool} listed without a"
            f" name, which is what a node that is down looks like, and {could} could be its. Run this again once every"
            " node reports.",
            _line(_again((), rules.host_flag)),
        )


async def forget(ctx: Context, host: fleet.Host, target: fleet.Target, matter: str) -> str:
    """A deleted VM's alias removed (destroy, inside the deletion's shield), unless its block records another host of
    the fleet, whose VM of the name it is: then kept, and said under matter. Its host key stays where it is filed. A
    removal that fails is said, with the run that prunes it, and fails nothing: the VM is gone. The stage's outcome:
    done, or skipped where no alias is kept or it could not be removed."""
    import asyncio

    if not host.aliases:
        return "skipped"
    files = Files.of(host.environ)
    rules = Rules.of(host, (target.name,))
    try:
        kept = ssh_config_blocks(await asyncio.to_thread(read, files.config) or "")
        theirs = [each for each in ssh_config_elsewhere(kept, rules.origin, list(rules.others))
                  if each["name"] == target.name]  # fmt: skip
        if theirs:
            report.say(
                ctx,
                target.identity,
                matter,
                f"Kept the SSH alias of {target.name} (it records {theirs[0]['origin']}): the alias of a name is the VM"
                f" of the host its block records, and this run deleted {host.name}'s.",
                "",
            )
            return "done"
        await remove(files, [target.name])
    except (Refused, OSError) as error:
        why = one_line(getattr(error, "sentence", None) or str(error), 300).rstrip(".")
        again = _again((), host.host_flag)
        report.say(
            ctx,
            target.identity,
            matter,
            said("Its SSH alias was not removed ({why}): {line} prunes it.", {"why": why, "line": again}),
            _line(again),
        )
        return "skipped"
    return "done"
