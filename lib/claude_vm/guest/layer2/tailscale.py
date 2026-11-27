"""Tailscale's join on a machine: layer 2's step `vpn-join`, today's roles/tailscale/tasks/state.yml, connect.yml,
settings.yml and report.yml in plain code (docs/architecture/engine.md §16.10). Layer 1's `tailscale` part installs
the package where Tailscale is chosen (roles/tailscale/tasks/system.yml, playbooks/system.yml); this step joins it.

Its settings: the rows `vpn` (Tailscale is managed only where it is `tailscale`), `tailscale_auth_method`,
`tailscale_ssh`, `tailscale_accept_routes`, `tailscale_accept_dns`, `tailscale_advertise_tags`,
`tailscale_login_server`, `tailscale_hostname` (the machine's name made one DNS label where none is set),
`tailscale_up_args` and `vm_label` (the tag its lines name); the role's option `tailscale_marker_dir`, read by its name
(fleet/passthrough.py), where the role's markers are; the store's `tailscale_auth_key`, a `Secret` or none; and what
the run sets for itself (`RUN_SET`): the machine's name, the host its lines name, whether its Tailscale had a login
before, which the VM's record keeps in Proxmox (G1's `joined`) and never the machine, and whether a sign-in follows in
this run. Where Tailscale is chosen, a machine name that is no DNS label, and the method key with no key, are refused
by name before the machine is read.

**The probe** reads, as root and changing nothing: what dpkg says of the package, the role's marker
`tailscale-installed` and the node key of the login its join made, `tailscale-joined` (in `tailscale_marker_dir`,
2.0's paths alone), and a Tailscale installed outside dpkg (`FOREIGN`); then, on a machine with the package where
Tailscale is chosen, `tailscale status --json`, read again a second apart until it is neither `NoState` nor
`Starting`, for `SETTLE_SECONDS` at most, a daemon that does not answer read again only where the Tailscale is the
role's, and, where it runs, `tailscale debug prefs`. Every decision comes from those, never from human output.

**The decisions** are `decide(found, wanted)`, connect.yml's and settings.yml's conditions in one function. Only a
Tailscale the role's marker marks, or none at all, is the role's: one installed any other way is reported and left
alone. The key is sent only from a settled `NeedsLogin` with no `HaveNodeKey`, on a VM whose record says it never had a
login, and where the method takes the key (auto or key, with a key set) and tailscale would not refuse it itself (an
OAuth client's secret with no tags); a node that has a login is never handed it: one Tailscale no longer accepts logs
it out (tailscale#19501). Under the method key, a VM that had a login before and has none fails, naming why. A sign-in
of the role's that ended after its run (the marker `pending`, a login now) is recorded as the role's. The settings of
the role's own login alone, a `Running` node whose node key is the marker's, are held to the rows by one `tailscale
set` naming only those that differ; tags are compared, never set.

**The acts** are fixed commands of the runner, as root: `tailscale up --auth-key=file:/dev/stdin --timeout=<n>s` with
the join's flags and the key on its standard input alone, sent once; the state read again, settled, after it; the
marker written on its command's standard input (the node key, `pending` for a join that ran out of time, which
tailscaled goes on with) or removed after a join that failed otherwise; and `tailscale set`. After an `up` that ends
`Unreachable`, which may have run on the machine, the key is never sent again in the run: the state is read again, and
a login there is recorded; with none, the step fails, and the next run decides from a settled state again.

**What it returns**: the step's apply says whether it changed anything; `seen`, by the machine's key, where Tailscale
is the VPN, whether the machine's Tailscale has had a login or has a sign-in under way, as connect.yml's
`tailscale_login_seen` says it (any login on the Tailscale the role installed), which G9 writes into the VM's record,
where the machine cannot change it. **Off** (`vpn` not `tailscale`) is left as it is: the probe reads the markers
alone, and the step says, where the role installed Tailscale, that it is left unmanaged; nothing logs a node out or
removes a package. **The report** is status/lines.py's `tailscale_report` line, what `aw
status` says of the VPN too, with its NEXT STEPS record under `tailscale`.

The key is on the run's redaction list before anything is read, revealed here in `_key` alone, for whether one is set
and whether it is an OAuth client's, and no message, event, argument, file or difference carries it. Standard library;
asyncio inside the coroutine that waits.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Mapping, Sequence

from claude_vm import proxmox, text
from claude_vm.fleet import registry
from claude_vm.guest.layer2.step import Context, Difference, Failed
from claude_vm.schema import printer
from claude_vm.secret import Secret
from claude_vm.ssh import runner
from claude_vm.status.lines import tailscale_report

# The settings this step reads, by name: the rows, the role's option, the store's secret, and what the run sets for
# itself, each with its value where it sets none (the machine's name is required).
ROWS = (
    "vpn",
    "tailscale_auth_method",
    "tailscale_ssh",
    "tailscale_accept_routes",
    "tailscale_accept_dns",
    "tailscale_advertise_tags",
    "tailscale_login_server",
    "tailscale_hostname",
    "tailscale_up_args",
    "vm_label",
)
OPTIONS = {"tailscale_marker_dir": "/var/lib/agent-warehouse"}
SECRET = "tailscale_auth_key"
RUN_SET = {"vm_name": None, "host_flag": "", "tailscale_joined_before": False, "tailscale_sign_in_follows": False}
CHOSEN = "tailscale"
METHODS = ("auto", "key", "web")
# The role's markers in tailscale_marker_dir: the one install.yml writes first, and the node key of the role's login.
INSTALLED = "tailscale-installed"
JOINED = "tailscale-joined"
PENDING = "pending"
# A Tailscale dpkg does not know (Tailscale's static binaries, the snap): someone else's (roles/tailscale/vars).
FOREIGN = ("/usr/sbin/tailscaled", "/usr/local/sbin/tailscaled", "/usr/local/bin/tailscaled", "/snap/bin/tailscale")
# How long a state is waited for to settle, a read a second; how long a join waits for Running (roles/tailscale/vars).
SETTLE_SECONDS = 30
PAUSE = 1.0
JOIN_SECONDS = 90
UNSETTLED = ("NoState", "Starting")
SIGNED_IN = ("Running", "NeedsMachineAuth")
# The page of a machine's login in Tailscale's console.
ADMIN = "https://login.tailscale.com/admin/machines"
# One machine name: one DNS label, as Tailscale takes one (roles/tailscale/tasks/main.yml).
LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
# What tailscale says when a join's --timeout runs out: tailscaled goes on with it.
TIMED_OUT = "timeout waiting for Tailscale service to enter a Running state"
REFUSAL = re.compile(r"invalid key: .*")
OAUTH_REFUSED = "oauth authkeys require --advertise-tags"
CLIENT = "tskey-client-"

# A read of tailscale's JSON, its last line ended as the runner takes one: $@ the read's words.
READ_JSON = 'tailscale "$@"; status=$?; echo; exit "$status"'
UP = ("tailscale", "up", "--auth-key=file:/dev/stdin")
SET = ("tailscale", "set")
QUICK = runner.Limits(seconds=30.0, silent=None)


def _sh(script: str, *words: str) -> list[str]:
    return ["sh", "-c", script, "sh", *words]


# The acts and reads, each a fixed command.
STATUS = tuple(_sh(READ_JSON, "status", "--json"))
PREFS = tuple(_sh(READ_JSON, "debug", "prefs"))
# What dpkg says of the package, the role's markers and a Tailscale outside dpkg: $1 the markers' folder, then the
# paths of FOREIGN.
OWNERSHIP = """set -u
dir=$1
shift
printf 'dpkg %s\\n' "$(dpkg-query --show --showformat='${db:Status-Status}' tailscale 2>/dev/null)"
if [ -e "$dir/tailscale-installed" ]; then echo 'installed yes'; else echo 'installed no'; fi
if [ -f "$dir/tailscale-joined" ]; then printf 'joined '; head -n 1 "$dir/tailscale-joined"; echo; fi
for path in "$@"; do if [ -e "$path" ]; then echo 'foreign yes'; fi; done
true
"""
# A marker written from standard input: $1 the markers' folder, $2 its name; into a file beside it, then renamed.
MARK = """set -eu
umask 022
mkdir -p "$1"
tmp=$(mktemp "$1/.$2.XXXXXX")
trap 'rm -f "$tmp"' EXIT
cat > "$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$1/$2"
trap - EXIT
"""
FORGET = 'rm -f "$1/$2"'


def _said(said: str) -> str:
    return text.one_line(said or "", 300)


def _failure(result: runner.Result) -> str:
    if isinstance(result, runner.Done):
        said = _said(result.said)
        return f"exit {result.status}" + (f": {said}" if said else "")
    if isinstance(result, runner.Unreachable):
        return "the machine was not reached (ssh's exit 255)"
    if isinstance(result, runner.TimedOut):
        return "it took longer than its time"
    if isinstance(result, runner.Silent):
        return "it said nothing in its time"
    return _said(getattr(result, "why", ""))


# ------------------------------------------------------------------ the settings ----


@dataclasses.dataclass(frozen=True)
class Wanted:
    """The settings as the step reads them: whether Tailscale is chosen, the method, the key (None where none is set)
    and whether it is an OAuth client's, the node's settings, the markers' folder, whether the VM had a login before
    and whether a sign-in follows in this run, and the lines' words."""

    chosen: bool
    method: str
    key: Secret | None = dataclasses.field(repr=False)
    client: bool = dataclasses.field(repr=False)
    hostname: str
    ssh: bool
    routes: bool
    dns: bool
    tags: tuple[str, ...]
    server: str
    up_args: tuple[str, ...]
    marker_dir: str
    joined_before: bool
    follows: bool
    name: str
    joined_tag: str
    configure_command: str
    login_command: str
    reconfigure_command: str

    @property
    def key_wanted(self) -> bool:
        """Whether the method takes the key: auto or key, with one set."""
        return self.method != "web" and self.key is not None

    @property
    def refused_locally(self) -> bool:
        """Whether tailscale would refuse the key itself: an OAuth client's secret with no tags."""
        return self.key_wanted and self.client and not self.tags

    @property
    def up_flags(self) -> list[str]:
        """The join's flags beside the key, as connect.yml's tailscale_up_flags: every managed setting said, from a
        reset."""
        flags = [
            "--reset",
            f"--hostname={self.hostname}",
            f"--ssh={str(self.ssh).lower()}",
            f"--accept-routes={str(self.routes).lower()}",
            f"--accept-dns={str(self.dns).lower()}",
        ]
        if self.tags:
            flags.append("--advertise-tags=" + ",".join(self.tags))
        if self.server:
            flags.append(f"--login-server={self.server}")
        return [*flags, *self.up_args]


def _key(settings: Mapping[str, object]) -> tuple[Secret | None, bool]:
    """The store's Tailscale key in the run's settings, or None where none is set, and whether it is an OAuth client's
    secret: the one place its value is read."""
    held = settings.get(SECRET)
    if held is None:
        return None, False
    if not isinstance(held, Secret):
        raise Failed(f"{SECRET} reaches a step as the store's Secret, never as plain text")
    value = held.reveal()
    return (held if value else None), value.startswith(CLIENT)


def _setting(ctx: Context, name: str) -> object:
    if name in RUN_SET and RUN_SET[name] is not None:
        return ctx.settings.get(name, RUN_SET[name])
    if name in OPTIONS:
        found = ctx.settings.get(name)
        return OPTIONS[name] if found in (None, "") else found
    if name not in ctx.settings:
        raise Failed(f"the run's settings hold no {name}, which Tailscale's join reads")
    return ctx.settings[name]


def _flag(ctx: Context, name: str) -> bool:
    value = _setting(ctx, name)
    if not isinstance(value, bool):
        raise Failed(f"{name} is true or false")
    return value


def _words(ctx: Context, name: str) -> tuple[str, ...]:
    value = _setting(ctx, name)
    value = () if value is None else value
    if not isinstance(value, (list, tuple)) or not all(isinstance(each, str) for each in value):
        raise Failed(f"{name} is a list of words")
    return tuple(value)


def wanted(ctx: Context) -> Wanted:
    """The settings, held: a method of none of METHODS, a machine name that is no DNS label and the method key with no
    key refused by name, where Tailscale is chosen, before anything is read."""
    name = _setting(ctx, "vm_name")
    if not isinstance(name, str) or not name or name.splitlines() != [name]:
        raise Failed("vm_name is the machine's name, one line")
    held, client = _key(ctx.settings)
    if held is not None:
        # Before anything is read or said: a machine's words may echo it.
        ctx.job.redact(held)
    chosen = _setting(ctx, "vpn") == CHOSEN
    method = str(_setting(ctx, "tailscale_auth_method"))
    hostname = _setting(ctx, "tailscale_hostname") or registry.tailnet_label(machine=name)
    if chosen:
        if method not in METHODS:
            raise Failed(f"tailscale_auth_method is {text.one_line(method, 40)!r}, not one of {', '.join(METHODS)}")
        if not isinstance(hostname, str) or not LABEL.fullmatch(hostname):
            raise Failed(
                f"tailscale_hostname is {json.dumps(text.one_line(str(hostname), 80))}, which Tailscale refuses as a"
                " machine name: it must be one DNS label - letters, digits and hyphens, at most 63, not starting or"
                " ending with a hyphen."
            )
        if method == "key" and held is None:
            raise Failed(
                f"tailscale_auth_method is key, but tailscale_auth_key is empty, so {name} has nothing to join with."
                " Put a key in the store, or set tailscale_auth_method to auto or web for the web login."
            )
    server = _setting(ctx, "tailscale_login_server")
    host = _setting(ctx, "host_flag")
    host = "" if host is None else str(host)
    marker_dir = _setting(ctx, "tailscale_marker_dir")
    if not isinstance(marker_dir, str) or not marker_dir.startswith("/") or marker_dir.splitlines() != [marker_dir]:
        raise Failed("tailscale_marker_dir is an absolute path on the machine, one line")
    return Wanted(
        chosen=chosen,
        method=method,
        key=held,
        client=client,
        hostname=str(hostname),
        ssh=_flag(ctx, "tailscale_ssh"),
        routes=_flag(ctx, "tailscale_accept_routes"),
        dns=_flag(ctx, "tailscale_accept_dns"),
        tags=_words(ctx, "tailscale_advertise_tags"),
        server="" if server is None else str(server),
        up_args=_words(ctx, "tailscale_up_args"),
        marker_dir=marker_dir.rstrip("/") or "/",
        joined_before=_flag(ctx, "tailscale_joined_before"),
        follows=_flag(ctx, "tailscale_sign_in_follows"),
        name=name,
        joined_tag=f"{_setting(ctx, 'vm_label')}-tailscale",
        configure_command=printer.line("deploy", {"names": [name], "tags": CHOSEN}, refused=False, host_flag=host),
        login_command=printer.line("login", {"names": [name], "only": CHOSEN}, refused=False, host_flag=host),
        reconfigure_command=printer.line(
            "deploy", {"names": [name], "only": "configure"}, refused=False, host_flag=host
        ),
    )


# ------------------------------------------------------------------ the state ----


def _map(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


@dataclasses.dataclass(frozen=True)
class Found:
    """Tailscale on the machine as read: whether dpkg has the package, the role's marker, a Tailscale outside dpkg, the
    joined marker's first line ("" for none); whether the state was read, whether the daemon answered and what it said
    where it did not, the state and the settings of a running node."""

    package: bool
    marker: bool
    foreign: bool
    joined_text: str
    read: bool = False
    daemon_up: bool = False
    said: str = ""
    state: Mapping = dataclasses.field(default_factory=dict)
    prefs: Mapping = dataclasses.field(default_factory=dict)

    @property
    def installed(self) -> bool:
        return self.package or self.foreign

    @property
    def ours(self) -> bool:
        """Whether this Tailscale is the role's: its marker, or no Tailscale at all."""
        return self.marker or not self.installed

    @property
    def backend(self) -> str:
        return str(self.state.get("BackendState") or "NoState") if self.daemon_up else "NoState"

    @property
    def joined(self) -> bool:
        """Whether the node has a key: a login, live or expired."""
        return self.state.get("HaveNodeKey") is True

    @property
    def auth_url(self) -> str:
        return str(self.state.get("AuthURL") or "")

    @property
    def me(self) -> Mapping:
        return _map(self.state.get("Self"))

    @property
    def node_key(self) -> str:
        return str(self.me.get("PublicKey") or "")

    @property
    def signed_in(self) -> bool:
        return self.joined and self.backend in SIGNED_IN

    @property
    def pending(self) -> bool:
        return self.joined_text == PENDING

    @property
    def login_ours(self) -> bool:
        """Whether the node's login is the role's: its node key the marker's, or a sign-in of the role's that ended."""
        return bool(self.node_key) and (
            (self.joined and self.joined_text == self.node_key) or (self.signed_in and self.pending)
        )

    @property
    def login_seen(self) -> bool:
        """Whether the role's Tailscale has had a login or has a sign-in under way, for the record."""
        return self.marker and (self.joined or self.pending or bool(self.auth_url))

    @property
    def no_login(self) -> bool:
        return self.backend == "NeedsLogin" and not self.joined

    @property
    def health(self) -> list[str]:
        found = self.state.get("Health")
        return [text.plain_text(str(each)) for each in found] if isinstance(found, list) else []


def _ownership(lines: Sequence[str]) -> Found:
    words = dict(line.split(" ", 1) for line in lines if " " in line)
    return Found(
        package=words.get("dpkg", "").strip() == "installed",
        marker=words.get("installed") == "yes",
        foreign=words.get("foreign") == "yes",
        joined_text=words.get("joined", "").strip(),
    )


async def _run(ctx: Context, words: Sequence[str], limits: runner.Limits = QUICK, stdin: runner.Input = b""):
    return await ctx.run(list(words), as_root=True, limits=limits, stdin=stdin)


async def ownership(ctx: Context, want: Wanted) -> Found:
    """What dpkg says, the role's markers and a Tailscale outside dpkg, as root: nothing changed."""
    result = await _run(ctx, _sh(OWNERSHIP, want.marker_dir, *FOREIGN))
    if not (isinstance(result, runner.Done) and result.status == 0):
        raise Failed(f"Tailscale's state could not be read on {want.name}: {_failure(result)}")
    return _ownership(result.lines)


async def settle(ctx: Context, want: Wanted, found: Found) -> Found:
    """found with tailscale status --json read until it is neither NoState nor Starting, a second apart, for
    SETTLE_SECONDS at most; a daemon that does not answer read again only where the Tailscale is the role's. The
    settings of a running node read beside it."""
    import asyncio

    state: Mapping = {}
    up, said = False, ""
    for attempt in range(SETTLE_SECONDS + 1):
        result = await _run(ctx, STATUS)
        if isinstance(result, runner.Unreachable):
            raise Failed(f"{want.name} was not reached while Tailscale's state was read: {_failure(result)}")
        up, said, state = False, _failure(result), {}
        if isinstance(result, runner.Done) and result.status == 0:
            try:
                state = _map(json.loads("\n".join(result.lines)))
                up = True
            except ValueError:
                said = "tailscale status said no JSON"
        settled = up and str(state.get("BackendState") or "NoState") not in UNSETTLED
        if settled or (not up and not found.ours):
            break
        if attempt < SETTLE_SECONDS:
            await asyncio.sleep(PAUSE)
    prefs: Mapping = {}
    if up and state.get("BackendState") == "Running":
        result = await _run(ctx, PREFS)
        if isinstance(result, runner.Done) and result.status == 0:
            try:
                prefs = _map(json.loads("\n".join(result.lines)))
            except ValueError:
                prefs = {}
    return dataclasses.replace(found, read=True, daemon_up=up, said=said, state=state, prefs=prefs)


async def read(ctx: Context, want: Wanted) -> Found:
    """Tailscale on the machine: the ownership, then, where Tailscale is chosen and one is installed, its settled
    state. Nothing changed."""
    found = await ownership(ctx, want)
    if want.chosen and found.installed:
        found = await settle(ctx, want, found)
    return found


# ------------------------------------------------------------------ the decisions ----


def set_flags(found: Found, want: Wanted) -> list[str]:
    """What tailscale set would change, as settings.yml holds the role's login to the rows."""
    prefs = found.prefs
    flags = []
    if prefs.get("Hostname", "") != want.hostname:
        flags.append(f"--hostname={want.hostname}")
    if prefs.get("RunSSH", False) != want.ssh:
        flags += [f"--ssh={str(want.ssh).lower()}", "--accept-risk=lose-ssh"]
    if prefs.get("RouteAll", False) != want.routes:
        flags.append(f"--accept-routes={str(want.routes).lower()}")
    if prefs.get("CorpDNS", True) != want.dns:
        flags.append(f"--accept-dns={str(want.dns).lower()}")
    return flags


@dataclasses.dataclass(frozen=True)
class Acts:
    """What the run does: the case (off, not ours, absent, down, or managed), whether a sign-in that ended is recorded,
    whether the run fails for a key held back under the method key, whether the key is sent, and the settings set."""

    case: str
    adopt: bool = False
    held_back: bool = False
    join: bool = False
    flags: tuple[str, ...] = ()

    @property
    def any(self) -> bool:
        return self.adopt or self.join or bool(self.flags) or self.case == "absent"


def decide(found: Found, want: Wanted) -> Acts:
    """What the run does to Tailscale, from the state and the settings alone: see the module's docstring."""
    if not want.chosen:
        return Acts("off")
    if not found.ours:
        return Acts("not ours")
    if not found.package:
        return Acts("absent")
    if not found.daemon_up:
        return Acts("down")
    held_back = found.no_login and want.joined_before and want.key_wanted
    first_join = found.no_login and not want.joined_before
    join = want.key_wanted and first_join and not want.refused_locally
    flags = tuple(set_flags(found, want)) if found.backend == "Running" and found.login_ours else ()
    return Acts(
        "managed",
        adopt=found.pending and found.signed_in,
        held_back=held_back and want.method == "key",
        join=join,
        flags=flags,
    )


# ------------------------------------------------------------------ the report ----


@dataclasses.dataclass(frozen=True)
class Joined:
    """What a join did: whether it ran and changed the node, what tailscale said, and what the state said before."""

    ran: bool = False
    changed: bool = False
    said: str = ""
    health_before: tuple[str, ...] = ()


def refused_why(found: Found, want: Wanted, joined: Joined) -> str:
    """Why Tailscale refused the key, in its words, or "": tailscale's own refusal of an OAuth secret with no tags,
    else what the join said, else a refusal in the state's health that was not there before the join."""
    if want.refused_locally:
        return OAUTH_REFUSED
    said = REFUSAL.search(text.plain_text(joined.said))
    if said:
        return said.group(0).strip()
    for line in found.health:
        if line in joined.health_before or "invalid key: " not in line:
            continue
        refused = REFUSAL.search(line)
        if refused:
            return refused.group(0).strip()
    return ""


def facts(found: Found, want: Wanted, joined: Joined, *, set_cmd: Sequence[str] = ()) -> dict[str, object]:
    """What status/lines.py's tailscale_report says the line from, as report.yml hands it: never the key."""
    me = found.me
    dns = re.sub(r"[.]$", "", str(me.get("DNSName") or ""))
    addresses = me.get("TailscaleIPs")
    mine = me.get("Tags") if isinstance(me.get("Tags"), list) else []
    why = refused_why(found, want, joined)
    return {
        "name": want.name,
        "hostname": want.hostname,
        "check_mode": False,
        "ours": found.ours,
        "check_without_package": False,
        "daemon_up": found.daemon_up,
        "backend": found.backend,
        "joined": found.joined,
        "auth_url": found.auth_url,
        "first_join": found.no_login and not want.joined_before,
        "key_wanted": want.key_wanted,
        "key_refused_locally": want.refused_locally,
        "key_held_back": found.no_login and want.joined_before and want.key_wanted,
        "join_ran": joined.ran,
        "join_stderr": joined.said,
        "refused_why": why,
        "dns_name": dns,
        "self_name": me.get("HostName") or "",
        "tailnet": _map(found.state.get("CurrentTailnet")).get("Name") or "",
        "login_server": want.server,
        "ipv4": proxmox.tailnet_ipv4(addresses if isinstance(addresses, list) else []),
        "joined_now": joined.changed and found.backend == "Running",
        "login_ours": found.login_ours,
        "name_taken": bool(re.match("^" + re.escape(want.hostname.lower()) + r"-[0-9]+\Z", dns.split(".")[0])),
        "set_changed": bool(set_cmd),
        "set_cmd": list(set_cmd),
        "from_login": False,
        "set_flags": set_flags(found, want) if found.backend == "Running" and found.login_ours else [],
        "tags_missing": [tag for tag in dict.fromkeys(want.tags) if tag not in mine] if want.tags else [],
        "settle_seconds": SETTLE_SECONDS,
        "expired_elsewhere": found.joined and not found.login_ours and not found.pending,
        "joined_tag": want.joined_tag,
        "joined_before": want.joined_before,
        "sign_in_follows": want.follows,
        "auth_method": want.method,
        "admin_page": "your control server" if want.server else ADMIN,
        "configure_command": want.configure_command,
        "login_command": want.login_command,
        "reconfigure_command": want.reconfigure_command,
    }


def reported(found: Found, want: Wanted, joined: Joined, *, set_cmd: Sequence[str] = ()) -> tuple[str, str]:
    """The report's text and its NEXT STEPS line ("" once nothing is left)."""
    made = tailscale_report(facts(found, want, joined, set_cmd=set_cmd), "aw")
    said = f"{want.name}: {made['line']}"
    return said, said if made["left"] and found.ours else ""


def unmanaged(want: Wanted) -> str:
    """What is said of a Tailscale the role installed on a machine where Tailscale is not chosen (unmanaged.yml)."""
    return (
        f"{want.name}: vpn is not {CHOSEN}, so the Tailscale this project installed here is left as it is, unmanaged:"
        f" no run changes it until vpn is {CHOSEN} again. Taking it off is by hand"
        ' ("Turning it off" in docs/guides/tailscale.md).'
    )


# ------------------------------------------------------------------ the step ----


@dataclasses.dataclass(frozen=True)
class State:
    """What the probe found, the settings as held, and what the run would do."""

    found: Found
    want: Wanted
    acts: Acts


class VpnJoin:
    """The step: see the module's docstring. seen, by each machine's key, whether its Tailscale has had a login, once
    the step has run on it."""

    name = "vpn-join"
    part = "tailscale"

    def __init__(self) -> None:
        self.seen: dict[str, bool] = {}

    async def probe(self, ctx: Context) -> State:
        """Tailscale's state and what the run would do: nothing changed."""
        want = wanted(ctx)
        found = await read(ctx, want)
        return State(found, want, decide(found, want))

    async def apply(self, ctx: Context, state: State) -> bool:
        found, want, acts = state.found, state.want, state.acts
        if acts.case == "off":
            # Left as it is, and its record too: nothing is seen where Tailscale is not the VPN.
            if found.marker and found.package:
                self._say(ctx, unmanaged(want), "")
            return False
        self.seen[ctx.machine] = found.login_seen
        if acts.case == "not ours":
            self._say(ctx, *reported(found, want, Joined()))
            return False
        if acts.case == "absent":
            raise Failed(
                f"Tailscale is not installed on {want.name}, which layer 1's tailscale part installs: run"
                f" {want.configure_command}"
            )
        if acts.case == "down":
            said = found.said or "it did not answer"
            raise Failed(f"tailscaled is not running on {want.name} ({said}); journalctl -u tailscaled on it says why.")
        changed = False
        if acts.adopt:
            await self._mark(ctx, want, found.node_key)
            found = dataclasses.replace(found, joined_text=found.node_key)
            changed = True
        self.seen[ctx.machine] = found.login_seen
        if acts.held_back:
            raise Failed(
                f"{want.name} had a Tailscale login before, as its record in Proxmox keeps, and has none now: something"
                " on the VM logged it out. tailscale_auth_key is not sent to a VM again, and tailscale_auth_method key"
                f" signs none in by the browser. Set tailscale_auth_method to auto and run {want.login_command}"
            )
        joined = Joined()
        if acts.join and want.key is not None:
            joined, found = await self._join(ctx, want, found)
            changed = changed or joined.changed or found.signed_in
        set_cmd: list[str] = []
        flags = set_flags(found, want) if found.backend == "Running" and found.login_ours else []
        if flags:
            result = await _run(ctx, [*SET, *flags])
            if not (isinstance(result, runner.Done) and result.status == 0):
                raise Failed(f"tailscale set on {want.name} failed: {_failure(result)}")
            set_cmd, changed = ["set", *flags], True
            found = await settle(ctx, want, found)
        self.seen[ctx.machine] = found.login_seen
        self._say(ctx, *reported(found, want, joined, set_cmd=set_cmd))
        return changed

    async def _join(self, ctx: Context, want: Wanted, found: Found) -> tuple[Joined, Found]:
        """The key sent once, on tailscale up's standard input alone, the state read again, and the marker made to say
        what the join left: the node key of a login, pending for a join still under way, none for a refusal."""
        before = tuple(found.health)
        words = [*UP, f"--timeout={JOIN_SECONDS}s", *want.up_flags]
        assert want.key is not None
        result = await _run(ctx, words, runner.Limits(seconds=JOIN_SECONDS + 60.0, silent=None), [want.key, "\n"])
        said = getattr(result, "said", "") or ""
        joined = Joined(
            ran=True,
            changed=isinstance(result, runner.Done) and result.status == 0,
            said=want.key.struck(said),
            health_before=before,
        )
        # Read again, settled: never decided from what the up said alone, and never sent again in this run.
        found = await settle(ctx, want, found)
        if found.signed_in:
            await self._mark(ctx, want, found.node_key)
            found = dataclasses.replace(found, joined_text=found.node_key)
        elif isinstance(result, runner.Unreachable):
            self.seen[ctx.machine] = found.login_seen
            raise Failed(
                f"{want.name} was not reached while Tailscale's key was sent, so whether it was used is not known, and"
                " Tailscale is not signed in there now: the key is not sent again in this run."
                f" {want.configure_command} reads it again."
            )
        elif TIMED_OUT in text.plain_text(joined.said) and not refused_why(found, want, joined):
            await self._mark(ctx, want, PENDING)
            found = dataclasses.replace(found, joined_text=PENDING)
        elif found.joined_text:
            result = await _run(ctx, _sh(FORGET, want.marker_dir, JOINED))
            if not (isinstance(result, runner.Done) and result.status == 0):
                raise Failed(f"the marker {JOINED} could not be removed on {want.name}: {_failure(result)}")
            found = dataclasses.replace(found, joined_text="")
        return joined, found

    @staticmethod
    async def _mark(ctx: Context, want: Wanted, content: str) -> None:
        result = await _run(ctx, _sh(MARK, want.marker_dir, JOINED), stdin=f"{content}\n".encode())
        if not (isinstance(result, runner.Done) and result.status == 0):
            raise Failed(f"the marker {JOINED} could not be written on {want.name}: {_failure(result)}")

    @staticmethod
    def _say(ctx: Context, said: str, left: str) -> None:
        # Here alone, as github.py does: guest/ is imported where the engine is not.
        from claude_vm.engine import report

        report.say(ctx.job, ctx.machine, CHOSEN, said, left)

    def difference(self, state: State) -> Difference | None:
        """What the run would do to Tailscale, and its state as it is now: never the key."""
        found, want, acts = state.found, state.want, state.acts
        if not acts.any:
            return None
        if acts.case == "absent":
            then = "joined with tailscale_auth_key" if want.key_wanted else "left for the web login"
            return Difference("Tailscale", "not installed", f"installed by layer 1, then {then}")
        said = []
        if acts.adopt:
            said.append("the login a sign-in of this product's ended in recorded as this product's")
        if acts.join:
            said.append("joined with tailscale_auth_key, and this product's marker of the login written")
        if acts.flags:
            said.append("tailscale set " + " ".join(acts.flags))
        now = found.backend + (", signed in by this product" if found.login_ours else "")
        return Difference("Tailscale", now, "; ".join(said))
