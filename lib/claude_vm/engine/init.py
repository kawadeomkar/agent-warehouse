"""init with nobody there: a whole fleet folder written from the values given, the vault sealed from its first write
(docs/architecture/engine.md §16.4).

`aw init --no-prompt`, or `aw init` with no terminal, asks nothing and makes nothing on Proxmox. It takes one typed
mapping, `Given`: each value held to its kind as today's init holds it (`claude_vm.init.init_answer`, and Proxmox's own
format of a name, `checks.proxmox_name`), and the token's secret from `--token-secret-file` (made absolute from the
working folder) or `AGENT_WAREHOUSE_TOKEN_SECRET`, never from the command line, and never both. The variable is
taken out of the process's own environment into a `Secret` at the very start of `init` (`taken`), as today's engine
takes its own such variable out at once, so that nothing the run starts holds it: not root SSH's child, nor any other
the runner or `runs/children.py` starts. A home whose root or `fleet/` is a link is refused before the run's job and
before anything is sent or written (`_held`), so that nothing is written through it and the same command finishes once
the folder itself is put in its place. Under the fleet's lock (the verb's row: `fleet`), `unattended` then:

1. Reads what is set: the host's folder (fleet/hosts.py's state), the three files of overrides, `fleet.json`, and the
   vault, opened with its key as fleet/vaultkey.py finds it: a fleet with no key file yet has its key made once every
   check has passed; a key named elsewhere for a fleet with no vault yet is `NoVaultYet`'s refusal, as is any other of
   the vault's or the key's.
2. Refuses a given value that differs from the one set, naming both (the secret unsaid), with nothing written: init
   never writes over a value. A pool the host's settings.yml sets that is no name of a pool is refused before it, by
   the read of the file, which holds proxmox_pool to the settings table's rule of a pool's name, as --pool is held.
3. Works out the API's certificate with no credential sent (engine/certificate.py), with nobody to ask: what checking
   uses now, else the host's own certificates read over root SSH (the owner's answer of 2026-11-23 to review 20), else
   only `--certificate`. No request carries the token before it is settled, and every one after goes through the
   `Pin` it carries.
4. Where the secret was given, reads the pool with it: holding a token Proxmox takes for a pool whose comment carries a
   mark with `controller=none` is the authority, and `fleet.json` takes the mark's fleet ID (fleet/mark.py); a mark
   that names a control VM, another fleet than `fleet.json`'s, none, or none that reads, and a pool or a token Proxmox
   refuses, are refusals with nothing written. With no secret given the fleet keeps its ID, or a new fleet gets one.
5. Writes, each file through its one writer, which takes its own lock (no lock of a file's is held around another's
   writer): the vault's key where there is none (`vaultkey.make_key` at `vault_key_file`'s place), `fleet.json`, the
   vault (`sealed.create`, then the secret under the host's `token`), `host.yml` and its anchor (`hosts.write`), the
   host's overrides (storage, bridge and pool, the pool's name `aw-fleet` where none is given or set), the fleet's
   (`github_username`, `vpn`), and `client.yml`'s current host where none is set. Each only where nothing is set, so
   the same run again writes nothing. Where the secret was given, `host.yml`'s `made` records by ID what the
   administrator made by hand from the printed commands (ruling B1, rule 5): the pool, the user and the token, each
   only where its name is the engine's own default (card E19, the owner's answer of 2026-11-23), never a name the
   person gave or set in host.yml, which may name an object of theirs that a cleanup by `made` would then remove.

The names written where none is given are the engine's own (card E1; ruling B1, rule 1): the user `agent-warehouse@pve`,
the token `aw` and the pool `aw-fleet`; and the first host is `pve`, as today's. The modes built by other cards are
refused by name until they land: `--check` (E6), `--print-commands` (E7) and `--machines` (J14).

**At a terminal** (standard input a terminal whose foreground the run is, as runs/ask.py's `terminal()` decides for each
question, and no `--no-prompt`; card E5), `attended` runs the same steps and asks, in plain Python, each question whose
value is still unset, every one through runs/ask.py's `ask` and nothing else of its own: nothing here reads the
terminal, by its path or any other way. The run is drawn live (INIT, one stage whose steps are the questions), and each
question is today's in its words, in the report's first-run order, the host first: the host's address; the API token's
ID, where no host.yml names one yet (Enter keeps the engine's own, and so does a second answer that is not a token ID,
said); whether to trust a certificate nothing on the host vouches for (E3's question, a yes or no whose safe answer is
no); the token's secret, hidden, where the vault holds none and none is given; the node, the storage and the bridge,
each a menu of what the host lists, read with the token, every row of it with what the token lacks there (today's
`init_candidates`), a yes or no where only one can be used, yes marked first, so that Enter at the menu writes the
name the host reported and no or Esc leaves it, where the host's own settings.yml sets none; the SSH key the machines
get, asked only where `vm_ssh_public_keys` is unset and `~/.ssh/id_ed25519.pub`, which its default reads, gives none, as
a menu of the other public keys in `~/.ssh`; whose repositories are cloned; which VPN the machines join, a choice,
`none` first, then `tailscale`, written as `vpn: tailscale` or `vpn: none`; and Tailscale's auth key, hidden, only once
`tailscale` is chosen (or given, or set), where the vault holds none and `tailscale_auth_method` is not `web`, put
straight into the vault and never taken from the command line. Each typed answer is held to its kind
(`claude_vm.init.init_answer`), asked once more saying the rule where it does not fit, and left where the person presses
Enter (Esc at a menu); a second that does not fit is left too, said. An answer is then a value given: the run goes on as
with nobody there, and writes only what is still unset. A question whose value is set is never asked. What was left is
said, with the line that asks again, in NEXT STEPS. With the host's address left, the host's questions are left with it,
and only the fleet's own answers are written.

Every sentence that names a command names it through the printer. Standard library, fleet/, pve/, ssh/, vendors/,
schema/, runs/stages.py and today's init.py, checks.py and text.py at import; asyncio, the rest of runs/ and the
Proxmox client inside the functions that use them.
"""

from __future__ import annotations

import dataclasses
import errno
import json
import os
import stat
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import init as answers
from ..checks import KNOWN_KEY_RE, KNOWN_KEY_TYPES, proxmox_name
from ..fleet import files as fleet_files
from ..fleet import hosts, mark, overrides, paths, record, sealed, secrets, vaultkey
from ..hosts import model
from ..pve import privileges
from ..runs.stages import Declared, Stage, Step
from ..schema.printer import line, said
from ..schema.words import EXITS, PLACEHOLDER, Line
from ..secret import Secret
from ..ssh import runner
from ..text import banner, one_line, plain_text
from . import access, certificate, routes

if TYPE_CHECKING:
    from ..pve.client import Proxmox
    from ..runs.ask import Choice
    from ..runs.attached import Context
    from ..runs.job import Job

# The tree of the program that runs, whose fleet's home init writes: this module's own checkout or release, which
# tools/aw holds to the one it runs from (plugin.own), as cli/main.py's checkout works it out from tools/aw's path.
PROGRAM_ROOT = Path(__file__).resolve().parents[3]
# The names init writes for a host where none is given: the engine's own (card E1; ruling B1, rule 1), never one
# today's product makes: its user, its token's ID and its pool; and the first host's name, today's first host's.
TOKEN_USER = "agent-warehouse@pve"
TOKEN_ID = "aw"
POOL = "aw-fleet"
FIRST_HOST = "pve"
# Where the token's secret is given when no file is: the environment, never the command line; the most of a file of
# it read, more than a secret is.
SECRET_VARIABLE = "AGENT_WAREHOUSE_TOKEN_SECRET"
SECRET_MOST = 4096
# Who made what init records, where the secret was given: the administrator, by hand, from the printed commands. Only
# the engine's own default names are recorded so (card E19, the owner's answer of 2026-11-23): the pool, the token's
# user and the token by its full ID, each where the run's name for it equals OWN_NAMES's, and nothing for a name the
# person gave or host.yml sets, which may name an object of the person's own.
BY_HAND = "administrator"
OWN_NAMES = {"pool": POOL, "user": TOKEN_USER, "token": f"{TOKEN_USER}!{TOKEN_ID}"}
# The modes of init built by other cards, refused by name until each lands.
NOT_YET = {"check": "E6", "print_commands": "E7", "machines": "J14"}
# The settings each host sets for itself that init writes where given or answered, in the order a sentence names them.
HOST_OWN = ("proxmox_storage", "proxmox_bridge", "proxmox_pool", "proxmox_node")
# The vault's entry of Tailscale's auth key, one for the fleet.
AUTH_KEY = "tailscale_auth_key"

# ---------------------------------------------------------------- the questions at a terminal (card E5) ----

# init's run as a person reads it at a terminal: one stage, whose steps are its questions in the order asked, then the
# writes. A question whose value is set ends its step done at once, and one left ends it skipped.
SETTINGS = "settings"
INIT = Declared(
    "init",
    (
        Stage(
            SETTINGS,
            "settings",
            steps=(
                Step("host", "Proxmox host"),
                Step("token", "API token"),
                Step("certificate", "certificate"),
                Step("secret", "secret"),
                Step("node", "node"),
                Step("storage", "storage"),
                Step("bridge", "bridge"),
                Step("key", "SSH key"),
                Step("github", "GitHub"),
                Step("vpn", "VPN"),
                Step("tailscale", "Tailscale auth key"),
                Step("write", "the fleet's files"),
            ),
            each_machine=False,
        ),
    ),
)
# The block every question is asked under, as today's.
ASKED = "USER INPUT REQUIRED"
LATER = "Enter to decide later."
# The token the engine's own names make, which Enter keeps at the token's ID's question.
OWN_TOKEN = f"{TOKEN_USER}!{TOKEN_ID}"
# A yes or no, as a menu: yes first, as today's.
YES, NO = "y", "n"


@dataclasses.dataclass(frozen=True)
class _Words:
    """One question's words, today's: what it asks (its label: the prompt's last words, and what is said of it left),
    the lines above it, and the kind init_answer holds a typed answer to ("" for a menu's)."""

    label: str
    text: tuple[str, ...]
    kind: str = ""


ADDRESS = _Words(
    "Proxmox host address",
    (
        "The address of your Proxmox host as this machine reaches it: an IP address or a",
        f"host name, without https:// or a port. {LATER}",
    ),
    "host",
)
TOKEN = _Words(
    "API token ID",
    (
        "The API token to use, as user@realm!name: the one made for this fleet on the host. Its ID",
        f"only: the secret is asked for after it. Enter keeps {OWN_TOKEN}.",
    ),
    "token_id",
)
SECRET = _Words(
    "API token secret",
    (f"The token's secret, which Proxmox printed once, when the token was made. {LATER}",),
    "uuid",
)
GITHUB = _Words(
    "GitHub username",
    (f"The GitHub account whose repositories are cloned onto each VM. {LATER}",),
    "github_login",
)
VPN = _Words(
    "VPN",
    (
        "A VPN you already run that routes this LAN - WireGuard on your router, a Tailscale subnet",
        "router, any other - reaches the VMs at their LAN addresses, from anywhere it reaches you:",
        "choose none, and that is all. tailscale joins each VM to your tailnet itself, which adds",
        "  - a name for each VM, reached from any of your devices, with no route or port of yours;",
        "  - each VM a device of its own: your tailnet policy can keep the VMs off your other devices;",
        "  - with an auth key in the vault, the join with nothing to click, and with an API",
        "    credential, the VM's device deleted when the VM is.",
        "tailscale needs a Tailscale account. none puts Tailscale on no VM, and leaves a VM it is",
        f"already on as it is. Left for later, it is none until answered. vpn in {paths.FLEET}/{overrides.SETTINGS}",
        "changes it.",
    ),
)
# The VPN's two answers, none first, each with a few words beside it, as today's.
VPN_CHOICES = (
    ("none", "your LAN, or a VPN of yours that routes it"),
    ("tailscale", "each VM joins your tailnet"),
)
TAILSCALE_KEY = _Words(
    "Tailscale auth key",
    (
        "Optional: an auth key joins each new VM to your tailnet as it is deployed, with nothing",
        "to click. Make one in Tailscale's admin console (Settings, Keys): reusable, pre-approved,",
        "tagged, never ephemeral. Enter to go without one, and sign each VM in on Tailscale's page",
        "as it is deployed; this is asked again next time. To always use the page and never be",
        f"asked, set tailscale_auth_method: web in {paths.FLEET}/{overrides.SETTINGS}.",
    ),
    "auth_key",
)
SSH_KEY = _Words(
    "SSH key",
    (
        "The VMs would get no SSH key: ~/.ssh/id_ed25519.pub, which they get by default, is not there,",
        "and SSH to them takes one. Which of your public keys do they get? vm_ssh_public_keys in",
        f"{paths.FLEET}/{overrides.SETTINGS} lists keys of your own.",
    ),
)
# The node, the storage and the bridge, each as the host lists them: its setting, the word a sentence says one by, the
# menu's title, and the permission path a name of one is checked at (pve/privileges.py's KINDS).
LISTED = {
    "node": ("proxmox_node", "node", "Node", "/nodes/"),
    "storage": ("proxmox_storage", "storage", "Storage", "/storage/"),
    "bridge": ("proxmox_bridge", "bridge", "Bridge", "/sdn/zones/localnetwork/"),
}
# What each of them is, above today's rows of what the token can see: where, the host's address or the node.
QUESTIONED = {
    "node": "The node {where} that holds the template, where new VMs are cloned.",
    "storage": "The storage for VM disks, and for the template's, {where}.",
    "bridge": "The bridge the template's network card is on, which every clone inherits, {where}.",
}
SEEN_THERE = "What your token can see there:"
# The most of a public key file read.
PUBLIC_KEY_MOST = 16384


class Refused(Exception):
    """init refuses, in one sentence, and nothing was written."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence if sentence.startswith("error: ") else f"error: {sentence}"


@dataclasses.dataclass(frozen=True)
class Given:
    """What init was given, each value held to its kind, "" where it was not given: the host's address and name, the
    token's user and ID, the GitHub account, the storage, the bridge and the pool, the VPN's answer (on, off), the
    certificate's fingerprint (upper case), the --set words for this run, and the token's secret, a Secret, with what
    gave it (secret_from: --token-secret-file and the file, or the variable), None where none was. At a terminal the
    person's answers are given too (attended): those, and the node, the SSH keys the machines get and Tailscale's auth
    key, which no option gives."""

    address: str = ""
    host: str = ""
    token_user: str = ""
    token_id: str = ""
    github_user: str = ""
    storage: str = ""
    bridge: str = ""
    pool: str = ""
    tailscale: str = ""
    certificate: str = ""
    settings: tuple[str, ...] = ()
    secret: Secret | None = None
    secret_from: str = ""
    notes: tuple[str, ...] = ()
    node: str = ""
    keys: tuple[str, ...] = ()
    auth_key: Secret | None = None


@dataclasses.dataclass(frozen=True)
class Variable:
    """AGENT_WAREHOUSE_TOKEN_SECRET as init took it out of its environment (taken): whether it was set, and what it
    held, held to its kind at once, as a Secret: None where it holds no secret, which given refuses unsaid."""

    given: bool = False
    secret: Secret | None = None


# The variable not set: what given takes where its caller took none.
UNSET = Variable()


# ---------------------------------------------------------------- what was given, held to its kind ----


def _text(values: Mapping[str, object], field: str) -> str:
    value = values.get(field)
    return value if isinstance(value, str) else ""


def _absolute(path: str, cwd: str, environ: Mapping[str, str]) -> Path:
    """--token-secret-file's path made absolute: ~ at its start the user's home, and a relative one from the folder init
    was started in, held to nothing more (H21: a folder with a space included)."""
    if path == "~" or path.startswith("~/"):
        try:
            return Path(str(paths.user_home(environ)) + path[1:])
        except paths.Refused as error:
            raise Refused(
                f"--token-secret-file starts with ~, and the home it names is not known here ({error})"
            ) from None
    return Path(os.path.normpath(os.path.join(cwd, path)))


def _rule(kind: str) -> str:
    return answers.init_answer_rule(kind)


def _pool_name(pool: object) -> bool:
    """Whether pool is a name Proxmox makes a pool by: card E1's access.POOL_NAME, the settings table's one rule of a
    pool's name, which --pool is held to before anything is sent, as the pool the host's settings.yml sets is held to
    it where the file is written and read (fleet/overrides.py, proxmox_pool's form)."""
    return isinstance(pool, str) and access.POOL_NAME.fullmatch(pool) is not None


def _bounded(path: Path, most: int, *, links: bool = True) -> bytes:
    """The first most + 1 bytes of the regular file at path: the one file read init opens itself, so that a file longer
    than most is known to be. Opened without waiting on a pipe, and with links False never through a link, then held to
    a regular file by what was opened, before anything of it is read, as fleet/vaultkey.py's _read: a FIFO or a link put
    where a file was is never read and never blocks the run. OSError where it cannot be read or is no regular file."""
    handle = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | (0 if links else os.O_NOFOLLOW))
    try:
        if not stat.S_ISREG(os.fstat(handle).st_mode):
            raise OSError(errno.EINVAL, "not a regular file", str(path))
        stream = open(handle, "rb")
    except BaseException:
        os.close(handle)
        raise
    with stream:
        return stream.read(most + 1)


def _secret_from_file(path: Path) -> tuple[Secret, tuple[str, ...]]:
    """The token's secret in the file at path, held to its kind and wrapped at once; and a note where others can read
    the file. Refused where it is not a file, cannot be read, or holds no secret, which is never said."""
    said_path = plain_text(str(path))
    try:
        info = os.stat(path)
    except OSError:
        raise Refused(f"--token-secret-file names {said_path}, which is not a file") from None
    if not stat.S_ISREG(info.st_mode):
        raise Refused(f"--token-secret-file names {said_path}, which is not a file")
    try:
        data = _bounded(path, SECRET_MOST)
    except OSError as error:
        why = error.strerror or error
        raise Refused(f"--token-secret-file names {said_path}, which cannot be read ({why})") from None
    try:
        text = data.decode("ascii") if len(data) <= SECRET_MOST else ""
    except UnicodeDecodeError:
        text = ""
    held = answers.init_answer(text, "uuid")
    if not held:
        raise Refused(f"--token-secret-file is not {_rule('uuid')}")
    notes = ()
    if stat.S_IMODE(info.st_mode) & 0o077:
        notes = (
            f"note: {said_path} can be read by others (mode {stat.S_IMODE(info.st_mode):04o}): chmod 600 it, or delete"
            " it now that the secret is in the vault",
        )
    return Secret(held), notes


def taken(environ: MutableMapping[str, str] | None = None) -> Variable:
    """AGENT_WAREHOUSE_TOKEN_SECRET taken out of environ, the process's own environment unless another is given, and
    held to its kind at once, its secret a Secret: init's first step, so that no mapping made from the environment
    after it, and no child started after it, holds the variable - root SSH's, the runner's, any of runs/children.py's -
    as today's engine takes its own such variable out at once (claude_vm.ansible.engine). Set and empty is none given,
    and is taken out too."""
    value = (os.environ if environ is None else environ).pop(SECRET_VARIABLE, "")
    if not value:
        return Variable()
    held = answers.init_answer(value, "uuid")
    return Variable(True, Secret(held) if held else None)


def given(
    values: Mapping[str, object],
    settings: Sequence[str] = (),
    *,
    cwd: str,
    environ: Mapping[str, str],
    variable: Variable = UNSET,
) -> Given:
    """values as the dispatch hands them (only what was given, each held by its pattern), each held to its kind, and
    the token's secret read from --token-secret-file or taken from the environment (variable, taken's): the typed
    mapping init takes. environ is read for the home a ~ names, never for the secret. Refused, in today's words, for the
    first that is not of its kind, the secret never said."""
    address = _text(values, "address")
    if address and not answers.init_answer(address, "host"):
        raise Refused(f"--address ({address}) is not {_rule('host')}")
    host = _text(values, "host")
    if host and (refusal := model.name_refusal(host)):
        raise Refused(f"--host ({host}) is no host's name: {refusal}")
    token_user, token_id = _text(values, "token_user"), _text(values, "token_id")
    if token_user or token_id:
        token = f"{token_user or TOKEN_USER}!{token_id or TOKEN_ID}"
        if not answers.init_answer(token, "token_id"):
            raise Refused(f"--token-user and --token-id ({token}) are not {_rule('token_id')}")
    github_user = _text(values, "github_user")
    if github_user and not answers.init_answer(github_user, "github_login"):
        raise Refused(f"--github-user ({github_user}) is not {_rule('github_login')}")
    for option, kind in (("storage", "storage"), ("bridge", "bridge")):
        named = _text(values, option)
        if named and not proxmox_name(named, kind):
            raise Refused(f"--{option} ({named}) is no name Proxmox gives a {kind}")
    pool = _text(values, "pool")
    if pool and not _pool_name(pool):
        raise Refused(f"--pool ({pool}) is no name of a pool: {access.POOL_RULE}")
    secret_file = _text(values, "token_secret_file")
    if secret_file and variable.given:
        raise Refused(
            f"the token's secret was given both by --token-secret-file and by {SECRET_VARIABLE}: give it one way"
        )
    secret, secret_from, notes = None, "", ()
    if secret_file:
        path = _absolute(secret_file, cwd, environ)
        secret, notes = _secret_from_file(path)
        secret_from = f"the token's secret in {plain_text(str(path))} (--token-secret-file)"
    elif variable.given:
        if variable.secret is None:
            raise Refused(f"{SECRET_VARIABLE} is not {_rule('uuid')}")
        secret, secret_from = variable.secret, f"the token's secret in {SECRET_VARIABLE}"
    return Given(
        address=answers.init_answer(address, "host") if address else "",
        host=host,
        token_user=token_user,
        token_id=token_id,
        github_user=github_user,
        storage=_text(values, "storage"),
        bridge=_text(values, "bridge"),
        pool=pool,
        tailscale=_text(values, "tailscale"),
        certificate=_text(values, "certificate").upper(),
        settings=tuple(str(word) for word in settings),
        secret=secret,
        secret_from=secret_from,
        notes=notes,
    )


# ---------------------------------------------------------------- what is set ----


def _init(**values: object) -> Line:
    return Line("init", values)


def _host_name(layout: hosts.Layout, named: str) -> str:
    """The host this run sets up: the one named, else the fleet's one host or its current host, else the first host's
    name for a fleet with none. Another host than the fleet's own is card E10's to set up, refused until it lands."""
    try:
        there = [name for name in layout.folders() if not model.name_refusal(name)]
        if named:
            if there and named not in there:
                raise Refused(
                    said(
                        "{named} is no host of the fleet's ({known}): a further host is set up by {init}, not built yet"
                        " (card E10 builds it)",
                        {"named": named, "known": ", ".join(there), "init": _init(host=named, address=PLACEHOLDER)},
                    )
                )
            return named
        if not there:
            return FIRST_HOST
        if len(there) == 1:
            return there[0]
        current = layout.current_name()
        if current in there:
            return current
        layout.first()
    except (hosts.Refused, paths.Refused, overrides.Refused) as error:
        raise Refused(str(error)) from None
    raise Refused(f"the fleet has more than one host ({', '.join(there)}), and none is this computer's current host")


@dataclasses.dataclass(frozen=True)
class _Set:
    """What one read found set, under the fleet's lock: the host's name, its state (host.yml and its anchor), the three
    files of overrides, fleet.json's record, every setting's value for this run, the vault's key (None where there is
    no key file yet) and its map (None with no vault yet)."""

    name: str
    state: hosts.State
    layers: overrides.Layers
    fleet: record.FleetRecord | None
    values: Mapping[str, overrides.Value]
    run: Mapping[str, object]
    key: Secret | None
    vault: Mapping[str, Secret] | None


def _run_values(words: Sequence[str], known: Sequence[Secret], environ: Mapping[str, str]) -> dict[str, object]:
    try:
        return overrides.run_values(words, secrets=known, environ=environ)
    except overrides.Refused as error:
        raise Refused(str(error)) from None


def _read(home: paths.Home, layout: hosts.Layout, typed: Given, environ: Mapping[str, str]) -> _Set:
    """Everything that is set, read once under the fleet's lock: Refused, saying why, where any of it cannot be read."""
    name = _host_name(layout, typed.host)
    known = [typed.secret] if typed.secret is not None else []
    try:
        state = layout.state(name)
        layers = overrides.read(home, name, environ=environ)
        fleet = record.read(home)
        run = _run_values(typed.settings, known, environ)
        values = overrides.resolve(home, name, run, environ=environ)
    except (hosts.Refused, overrides.Refused, paths.Refused, record.Refused) as error:
        raise Refused(str(error)) from None
    key_file, key_command = values["vault_key_file"].value, values["vault_key_command"].value
    try:
        key = vaultkey.open(home, key_file=key_file, key_command=key_command, environ=environ)
    except vaultkey.NoKey as error:
        if os.path.lexists(sealed.path(home)):
            # The fleet has a vault, which a key made here could never open.
            raise Refused(str(error)) from None
        key = None
    except sealed.Refused as error:
        raise Refused(str(error)) from None
    try:
        vault = None if key is None else sealed.read(home, key)
    except sealed.Refused as error:
        raise Refused(str(error)) from None
    if vault:
        # A value given for this run is never one of the vault's secrets, which the run's record would keep.
        run = _run_values(typed.settings, [*known, *vault.values()], environ)
    return _Set(name, state, layers, fleet, values, run, key, vault)


def _held(home: paths.Home) -> None:
    """The home's root and its fleet/, each that is there held to be a folder itself (fleet/files.py's folder, which
    makes nothing that is there): a link where one goes, or anything but a folder, is refused before anything is
    written, so that nothing is written through it - the run's job and lock, the key, fleet.json and the vault, which
    their writers put beneath a link they do not hold, before host.yml's writer would refuse it - and the same command
    finishes once the folder itself is in its place. One that is missing is made by the first writer whose file goes in
    it, a folder and never a link."""
    for each in (home.root, paths.fleet_dir(home)):
        try:
            if os.path.lexists(each):
                fleet_files.folder(each)
        except fleet_files.Unsafe as unsafe:
            raise Refused(str(unsafe)) from None
        except OSError as error:
            why = error.strerror or error
            raise Refused(f"{plain_text(str(each))} cannot be read ({why}): nothing was written") from None


def _same(held: Secret, secret: Secret) -> bool:
    """Whether the secret given is the one the vault holds: one of the two places init reads a Secret's value, to
    compare it with another, never to send it on or say it (tests/unit/test_aw_guards.py's REVEALS)."""
    return held.reveal() == secret.reveal()


def _kind_of(typed: Secret, kind: str) -> Secret | None:
    """A hidden answer held to its kind (init_answer's: the token's secret, Tailscale's auth key), wrapped again at
    once: the other place init reads a Secret's value, never to send it on or say it; None where it is not of its
    kind."""
    held = answers.init_answer(typed.reveal(), kind)
    return Secret(held) if held else None


def _differs(typed: Given, found: _Set) -> list[str]:
    """Each value given that differs from the one set, naming both, in today's words."""
    name, fields = found.name, found.state.fields
    host_file = f"{hosts.HOSTS_REL}/{name}/{hosts.HOST_FILE}"
    host_settings = f"{hosts.HOSTS_REL}/{name}/{overrides.SETTINGS}"
    fleet_settings = f"{paths.FLEET}/{overrides.SETTINGS}"
    own = {} if found.layers.host is None else found.layers.host.settings
    shared = found.layers.fleet.settings
    said_now: list[str] = []
    if fields is not None:
        for option, given_value, set_value, folded in (
            ("--address", typed.address, fields.address, True),
            ("--token-user", typed.token_user, fields.token_user, False),
            ("--token-id", typed.token_id, fields.token_id, False),
        ):
            now = (answers.init_answer(set_value, "host") or set_value) if folded else set_value
            if given_value and (given_value.lower() != now.lower() if folded else given_value != now):
                said_now.append(f"{option} {given_value}, but {host_file} already sets {set_value}.")
    for option, given_value, setting in (
        ("--storage", typed.storage, "proxmox_storage"),
        ("--bridge", typed.bridge, "proxmox_bridge"),
        ("--pool", typed.pool, "proxmox_pool"),
    ):
        now = own.get(setting)
        if given_value and now not in (None, "") and given_value != now:
            said_now.append(f"{option} {given_value}, but {host_settings} already sets {setting}: {now}.")
    github = shared.get("github_username")
    if typed.github_user and github not in (None, "") and typed.github_user.lower() != str(github).lower():
        said_now.append(f"--github-user {typed.github_user}, but {fleet_settings} already sets {github}.")
    vpn = shared.get("vpn")
    if typed.tailscale and vpn not in (None, "") and (vpn == "tailscale") != (typed.tailscale == "on"):
        said_now.append(f"--tailscale {typed.tailscale}, but {fleet_settings} already sets vpn: {vpn}.")
    return said_now


# ---------------------------------------------------------------- the certificate, and the pool's mark ----


def _root_ssh(job: Job, address: str, fields: hosts.HostFile | None, values: Mapping[str, overrides.Value], environ):
    """The read of the host's certificate files over root SSH, as a child of job's run: the host's own Target as
    routes.host_target makes it, the one function that does - its account and port (host.yml's ssh_user and
    ssh_port), this computer's key for it and its ssh (client.yml's ssh_identity_file and ssh_executable). A target the
    runner refuses is root SSH that did not answer."""

    async def read() -> certificate.Read:
        try:
            target = routes.host_target(
                address,
                fields.ssh_user if fields is not None else "root",
                fields.ssh_port if fields is not None else 22,
                values,
                environ,
            )
            return await certificate.read_over_root_ssh(job, target, environ)
        except (runner.Refused, paths.Refused) as error:
            raise certificate.NoRootSsh(one_line(str(error), 300)) from None

    return read


def _connect(
    url: str, kept: certificate.Kept, token_id: str, secret: Secret, proxy_setting: str, environ: Mapping[str, str]
) -> Proxmox:
    """The Proxmox client the run's token goes through: on the connection the certificate's step made (its Pin), and
    through the proxy the setting chooses. The caller closes it."""
    from ..pve.client import Proxmox, Token
    from ..vendors import proxy

    try:
        through = proxy.choose(proxy_setting, environ, url)
    except ValueError as error:
        raise Refused(str(error)) from None
    return Proxmox(url, kept.pinned, Token(token_id, secret), through)


async def _mark(client: Proxmox, token_id: str, pool: str) -> mark.Mark:
    """The mark in the pool's comment, read with the token given, through client: Refused where Proxmox does not take
    the token for the pool, the pool is not there, or its comment carries no mark, or one that does not read."""
    import asyncio

    from ..pve.answers import ProxmoxError
    from ..pve.client import Denied
    from ..vendors.transport import TransportError

    try:
        found = await asyncio.to_thread(client.pool, pool)
    except Denied as error:
        raise Refused(
            f"{error}: {token_id} with the secret given does not read the pool {pool}, so nothing shows the fleet it"
            " belongs to, and nothing was written. Check the token's ID, its secret and its grant on the pool"
        ) from None
    except (ProxmoxError, TransportError) as error:
        raise Refused(f"{one_line(str(error), 300)}: nothing was written") from None
    try:
        found_mark = mark.parse(found.comment)
    except mark.Refused as error:
        raise Refused(f"{str(error).removeprefix('error: ')}: nothing was written") from None
    if found_mark is None:
        raise Refused(
            f"the pool {pool} carries no fleet's mark in its comment, so it is not shown to be the pool the"
            " administrator's commands made for this fleet: with nobody there no pool is taken over, and nothing was"
            " written"
        )
    return found_mark


# ---------------------------------------------------------------- what the host lists, for its menus ----


def _held_at(client: Proxmox, path: str) -> tuple[int, dict]:
    """What the token holds at an ACL path, as today's init reads it for each name listed: 200 and its privileges, or
    0 and none where the read failed, which then lacks everything."""
    from ..pve.answers import ProxmoxError
    from ..vendors.transport import TransportError

    try:
        return 200, dict(client.permissions(path).privileges)
    except (ProxmoxError, TransportError, ValueError):
        return 0, {}


def _listed(client: Proxmox, what: str, node: str) -> list[dict]:
    """What the host lists of what (a node; a storage for VM disks or a bridge on node) that the token may see, each as
    today's init_candidates gives one: its name, its type, its space free, what the token lacks of the engine's table
    there (pve/privileges.py's needed), whether it is inactive (a node not online), and whether its name is one Proxmox
    would not give, in the listing's order. Synchronous, so run in a thread."""
    path = LISTED[what][3]
    required = sorted(privileges.needed(what))
    if what == "node":
        found = []
        for each in client.resources("node"):
            refused = not proxmox_name(each.node, "node")
            _status, held = (0, {}) if refused else _held_at(client, path + each.node)
            found.append(
                {
                    "name": json.dumps(each.node) if refused else each.node,
                    "type": "",
                    "free": "",
                    "lacks": [privilege for privilege in required if privilege not in held],
                    "inactive": each.status != "online",
                    "refused": refused,
                }
            )
        return found
    key = "storage" if what == "storage" else "iface"
    entries = client.storages(node) if what == "storage" else client.bridges(node)
    results = []
    for each in entries:
        name = str(each.entry.get(key, ""))
        status, held = _held_at(client, path + name) if proxmox_name(name, what) else (0, {})
        results.append({"item": dict(each.entry), "status": status, "json": {"data": {path + name: held}}})
    return answers.init_candidates(results, key, path, required)


def _usable(candidate: Mapping[str, Any]) -> bool:
    return not candidate["lacks"] and not candidate.get("inactive") and not candidate.get("refused")


def _rows(what: str, candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    """The lines a menu's question shows above it, as today's: what can be used numbered, then the rest each with why
    not, what the token lacks there above all (init_choose_rows); a node not online is said to be offline first."""
    path = LISTED[what][3]
    if what != "node":
        return answers.init_choose_rows(list(candidates), path)
    usable = [each for each in candidates if _usable(each)]
    width = max((len(each["name"]) for each in candidates), default=0)
    lines = [f"  {f'{number}) ' if len(usable) > 1 else '-  '}{each['name']}" for number, each in enumerate(usable, 1)]
    for each in candidates:
        if each in usable:
            continue
        why = (
            "not a name Proxmox gives one, so never used"
            if each.get("refused")
            else "offline"
            if each.get("inactive")
            else f"the token lacks {', '.join(each['lacks'])} on {path}{each['name']}"
        )
        lines.append(f"  -  {each['name']:<{width}}   {why}")
    return lines


def _public_keys(environ: Mapping[str, str]) -> list[tuple[str, str]]:
    """The public keys in ~/.ssh, each as its file's name and its key's line - a type OpenSSH names, its key in base64
    and its comment, made plain - in the files' order: the menu the SSH key's question offers. A file that is a link or
    no regular file (a FIFO is never waited on), cannot be read, is larger than a key is, or holds no key line is left
    out."""
    try:
        folder = paths.user_home(environ) / ".ssh"
        files = sorted(folder.glob("*.pub")) if folder.is_dir() else []
    except (paths.Refused, OSError):
        return []
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in files:
        try:
            data = _bounded(path, PUBLIC_KEY_MOST, links=False)
        except OSError:
            continue
        if len(data) > PUBLIC_KEY_MOST:
            continue
        first = data.decode("utf-8", errors="replace").strip().split("\n", 1)[0].strip()
        words = first.split()
        name = one_line(path.name, None)
        if len(words) >= 2 and words[0] in KNOWN_KEY_TYPES and KNOWN_KEY_RE.fullmatch(words[1]) and name not in seen:
            seen.add(name)
            found.append((name, one_line(first, None)))
    return found


# ---------------------------------------------------------------- the person, asked ----


def _question(lines: Sequence[str], label: str, colon: bool = True) -> str:
    """A question as runs/ask.py asks it: its lines under today's block, then its label, which ends it."""
    topic = said("{init}", {"init": _init()})
    return banner(list(lines), ASKED, topic=topic) + label + (":" if colon else "")


class _Asking:
    """The person at the run's terminal, each question one through runs/ask.py's ask alone, and a step of init's stage:
    begun as it is asked, ended done where answered or set, skipped where left. What was left is kept, by its label,
    for the run's last lines and NEXT STEPS."""

    def __init__(self, running: Context) -> None:
        self.running = running
        self.left: list[str] = []

    def step(self, key: str, outcome: str | None = None) -> None:
        if outcome is None:
            self.running.emit("step", None, key, stage=SETTINGS)
        else:
            self.running.emit("step", None, key, stage=SETTINGS, outcome=outcome)

    def passed(self, key: str, outcome: str = "done") -> None:
        """A step with no question: its value is set, or nothing can be asked of it."""
        self.step(key)
        self.step(key, outcome)

    def say(self, text: str) -> None:
        """A line said above the region, whole."""
        self.running.emit("report", None, text)

    async def ask(
        self, text: str, default: str | Secret, choices: Sequence[Choice | str] = (), hidden: bool = False
    ) -> str | Secret:
        from claude_vm.runs.ask import ask

        return await ask(self.running, text, default, choices, hidden)

    def _left(self, key: str, words: _Words, answered: bool) -> None:
        if not answered:
            self.left.append(words.label)
        self.step(key, "done" if answered else "skipped")

    async def line(self, key: str, words: _Words, *, keeps: str = "") -> str:
        """A typed answer held to its kind, "" where it was left: asked, and once more saying the rule where it does not
        fit, a second that does not fit said and left. keeps: the value Enter keeps, its words, "" for none: then
        nothing is left, the second asking says what Enter keeps, and a second answer that does not fit keeps it too,
        said so, as its caller then takes it ("" given back)."""
        self.step(key)
        answer = ""
        enter = f"Enter keeps {keeps}." if keeps else LATER
        for again in (False, True):
            lines = words.text if not again else (f"That is not {_rule(words.kind)}.", enter)
            typed = str(await self.ask(_question(lines, words.label), ""))
            if not typed.strip():
                break
            answer = answers.init_answer(typed, words.kind)
            if answer:
                break
            if again:
                self._said_left(words, keeps)
        self._left(key, words, bool(answer or keeps))
        return answer

    async def hidden(self, key: str, words: _Words) -> Secret | None:
        """A hidden answer held to its kind, a Secret, None where it was left: never echoed, said or kept in clear."""
        self.step(key)
        answer = None
        for again in (False, True):
            lines = words.text if not again else (f"That is not {_rule(words.kind)}.", LATER)
            nothing = Secret("")
            typed = await self.ask(_question(lines, words.label), nothing, hidden=True)
            if typed is nothing or not isinstance(typed, Secret):
                break
            answer = _kind_of(typed, words.kind)
            if answer is not None:
                break
            if again:
                self._said_left(words)
        self._left(key, words, answer is not None)
        return answer

    def _said_left(self, words: _Words, keeps: str = "") -> None:
        """What a second answer that does not fit leaves: the question, for the next run, or the value Enter keeps."""
        if keeps:
            self.say(f"That is not {_rule(words.kind)} either, so {words.label} keeps {keeps}.")
            return
        self.say(
            said(
                "That is not {rule} either, so {label} is left as it was: the next {init} asks again.",
                {"rule": _rule(words.kind), "label": words.label, "init": _init()},
            )
        )

    async def choice(self, key: str, words: _Words, choices: Sequence[Choice]) -> str:
        """One of choices, "" where it was left: a menu picked with the arrow keys where the terminal can draw one, else
        typed (runs/ask.py)."""
        self.step(key)
        picked = str(await self.ask(_question(words.text, words.label, colon=False), "", choices))
        self._left(key, words, bool(picked))
        return picked

    async def pick(self, key: str, what: str, candidates: Sequence[Mapping[str, Any]], text: Sequence[str]) -> str:
        """One of what the host lists that the token can use, "" where it was left: a yes or no for the only one, as
        today's, yes marked first, so that Enter at the menu takes the name the host reported and no or Esc leaves it
        (the coordinator's ruling on review 54's E5.md 7: three Enters that left all three had the fleet fall back to
        pve, local-lvm and vmbr0, wrong on a host whose names differ); a menu of several; today's rows above either.
        None that can be used asks nothing, and is left."""
        from claude_vm.runs.ask import Choice

        title = LISTED[what][2]
        usable = [each for each in candidates if _usable(each)]
        words = _Words(title, (*text, *_rows(what, candidates)))
        if not usable:
            self.passed(key, "skipped")
            self.left.append(title)
            return ""
        self.step(key)
        if len(usable) == 1:
            only = usable[0]["name"]
            # Yes is marked first, and is what Enter at the menu takes; no and Esc, which gives back the default, ""
            # (runs/ask.py), leave it. Typed, where no menu can be drawn, Enter gives back that default too, as its
            # prompt says, since ask gives a typed Enter and Esc at the menu the one default.
            answer = await self.ask(_question(words.text, f"Use {only}?", colon=False), "", _yes_no())
            picked = only if answer == YES else ""
        else:
            # A name is held to Proxmox's own form (init_candidates), and its type, as the host says it, is made plain.
            notes = [one_line(", ".join(part for part in (each["type"], each["free"]) if part), 60) for each in usable]
            listed = [Choice(each["name"], each["name"], note) for each, note in zip(usable, notes, strict=True)]
            picked = str(await self.ask(_question(words.text, title, colon=False), "", listed))
        self._left(key, words, bool(picked))
        return picked

    def trusting(self) -> certificate.Ask:
        """E3's question, whether to trust a certificate nothing on the host vouches for: a yes or no whose safe answer,
        and default, is no."""

        async def trust(question: certificate.Question) -> bool:
            prompt = question.prompt.removesuffix(" y or n")
            return await self.ask(_question(question.lines, prompt, colon=False), NO, _yes_no()) == YES

        return trust


def _yes_no() -> tuple[Choice, ...]:
    """A yes or no as a menu, yes first, as today's."""
    from claude_vm.runs.ask import Choice

    return (Choice(YES, "yes"), Choice(NO, "no"))


# ---------------------------------------------------------------- the writes ----


def _change(address: str, token_user: str, token_id: str, made: Sequence[hosts.Made]):
    """host.yml's change: the address and the token's user and ID, and what was made, by ID, beside what is recorded."""

    def change(raw: dict) -> None:
        raw["address"] = address
        raw["token_user"] = token_user
        raw["token_id"] = token_id
        if made:
            recorded = list(raw.get("made") or [])
            seen = {(entry.get("what"), str(entry.get("id"))) for entry in recorded if isinstance(entry, dict)}
            recorded += [
                {"what": each.what, "id": each.id, "by": each.by} for each in made if (each.what, each.id) not in seen
            ]
            raw["made"] = recorded

    return change


def _written(home: paths.Home, path: Path) -> str:
    """A file written, as a line names it: its path under the fleet's home, else whole."""
    try:
        return str(path.relative_to(home.root))
    except ValueError:
        return plain_text(str(path))


async def unattended(
    home: paths.Home,
    typed: Given,
    *,
    environ: Mapping[str, str],
    job: Job | None = None,
    root_ssh: certificate.RootSsh | None = None,
) -> list[str]:
    """The fleet's folder written from typed with nobody there, its caller holding the fleet's lock: the lines that say
    what was written. root_ssh reads the host's certificate files where given; else, with job, over root SSH as a child
    of its run; else root SSH is not tried. Refused, in one sentence, with nothing written, where a value given differs
    from the one set, the certificate is not settled, or the pool's mark does not make the fleet this one. See the
    module's docstring."""
    return await _run(home, typed, environ=environ, job=job, root_ssh=root_ssh, asking=None)


async def attended(
    home: paths.Home,
    typed: Given,
    *,
    running: Context,
    environ: Mapping[str, str],
    root_ssh: certificate.RootSsh | None = None,
) -> list[str]:
    """The fleet's folder written at the run's terminal, its caller holding the fleet's lock: each question whose value
    is unset asked through runs/ask.py, as the module's docstring says, every answer then a value given, as unattended
    takes one; each question a step of init's one stage (INIT). The lines that say what was written and what was left,
    which is also in NEXT STEPS. Refused as unattended is, with nothing written."""
    asking = _Asking(running)
    running.emit("stage", None, SETTINGS)
    lines = await _run(home, typed, environ=environ, job=running.job, root_ssh=root_ssh, asking=asking)
    if asking.left:
        running.emit(
            "next",
            None,
            said("{init}   asks again for {left}", {"init": _init(), "left": _listed_words(asking.left)}),
            matter="init",
        )
    running.emit("stage", None, SETTINGS, outcome="done")
    return lines


def _listed_words(words: Sequence[str]) -> str:
    return ", ".join(words[:-1]) + f" and {words[-1]}" if len(words) > 1 else "".join(words)


@dataclasses.dataclass(frozen=True)
class _Settled:
    """What a run settled of the host, before anything is written: what its certificate is held to, the token's user
    and ID, the fleet's ID, what the administrator made, by ID, where a secret was given or typed (the pool's mark read
    with it), and what was given and answered."""

    kept: certificate.Kept
    token_user: str
    token_id: str
    fleet_id: str
    made: tuple[hosts.Made, ...]
    typed: Given


async def _run(
    home: paths.Home,
    typed: Given,
    *,
    environ: Mapping[str, str],
    job: Job | None,
    root_ssh: certificate.RootSsh | None,
    asking: _Asking | None,
) -> list[str]:
    """unattended's steps, and attended's with asking: what is set read and every value given held to it, the host's
    part (its address, its token, its certificate, the pool's mark, and at a terminal its lists), at a terminal the
    fleet's own questions, then the writes, each through its one writer, and what the run says of them."""
    layout = hosts.Layout(home, environ)
    found = _read(home, layout, typed, environ)
    # Again under the fleet's lock, and for a caller that is not init's handler: before anything is sent or written.
    _held(home)
    name, fields = found.name, found.state.fields
    held = None if found.vault is None else found.vault.get(secrets.entry("token", host=name))
    differs = _differs(typed, found)
    if differs:
        never = said("{init} never writes over a value: edit the file to change it.", {"init": _init()})
        raise Refused(" ".join([*differs, never]))
    if typed.secret is not None and held is not None and not _same(held, typed.secret):
        raise Refused(
            said(
                "{what} differs from the token's secret the vault already holds for {name}: {init} never writes over a"
                " value; change it with {vault}",
                {
                    "what": typed.secret_from,
                    "name": name,
                    "init": _init(),
                    "vault": Line("vault", {"name": "token", "host": name}),
                },
            )
        )
    address = typed.address or (fields.address if fields is not None else "")
    if asking is not None:
        if address:
            asking.passed("host")
        else:
            address = await asking.line("host", ADDRESS)
    named = {"host": name} if typed.host else {}
    settled = None
    if address:
        settled = await _the_host(
            found,
            dataclasses.replace(typed, address=address),
            held,
            environ=environ,
            job=job,
            root_ssh=root_ssh,
            asking=asking,
        )
        typed = settled.typed
    elif asking is None:
        raise Refused(
            said(
                "the host {name} has no address yet: give it, as {init}; nothing was written",
                {"name": name, "init": _init(address=PLACEHOLDER, no_prompt=True, **named)},
            )
        )
    else:
        # The host's address left: so is everything that needs the host, which the next run asks.
        for key in ("token", "certificate", "secret", "node", "storage", "bridge"):
            asking.passed(key, "skipped")
    if asking is not None:
        typed = await _the_fleet(asking, typed, found, environ)
        asking.step("write")
    wrote = _write(home, layout, found, typed, settled, environ)
    if asking is not None:
        asking.step("write", "done")
    return _report(home, found, typed, settled, wrote, asking)


async def _the_host(
    found: _Set,
    typed: Given,
    held: Secret | None,
    *,
    environ: Mapping[str, str],
    job: Job | None,
    root_ssh: certificate.RootSsh | None,
    asking: _Asking | None,
) -> _Settled:
    """The host's part of a run, at typed.address: the token's ID, asked where nothing names one yet; the certificate,
    settled before any request that carries the token, a person asked where only one can vouch for it; the token's
    secret, asked where none is held or given; the pool's mark, read with a secret given or typed, which is the
    authority for the fleet's ID; and at a terminal the node, the storage and the bridge, from the host's lists."""
    name, state, fields = found.name, found.state, found.state.fields
    address = typed.address
    token_user = typed.token_user or (fields.token_user if fields is not None else TOKEN_USER)
    token_id = typed.token_id or (fields.token_id if fields is not None else TOKEN_ID)
    if asking is not None:
        if fields is None and held is None and typed.secret is None and not (typed.token_user or typed.token_id):
            # Enter keeps the engine's own, as a second answer that is not a token ID does: said, and written.
            answer = await asking.line("token", TOKEN, keeps=OWN_TOKEN)
            if answer:
                token_user, _, token_id = answer.partition("!")
                typed = dataclasses.replace(typed, token_user=token_user, token_id=token_id)
        else:
            asking.passed("token")
    own = {} if found.layers.host is None else found.layers.host.settings
    pool = typed.pool or own.get("proxmox_pool") or POOL
    tls_ca_file = found.values["tls_ca_file"].value or None

    # The certificate, settled before any request that carries the token.
    if fields is not None:
        host = certificate.Host.of(name, dataclasses.replace(fields, address=address), state.anchor, tls_ca_file)
    else:
        host = certificate.Host(name=name, address=address, tls_ca_file=tls_ca_file)
    reader = root_ssh
    if reader is None and job is not None:
        reader = _root_ssh(job, address, fields, found.values, environ)
    if asking is not None:
        asking.step("certificate")
    kept = await certificate.work_out(host, typed.certificate, reader, None if asking is None else asking.trusting())
    if isinstance(kept, certificate.Refusal):
        raise Refused(kept.said)
    if asking is not None:
        asking.step("certificate", "done")
        # The token's secret: given, held in the vault, or asked, hidden.
        if typed.secret is None and held is None:
            typed_secret = await asking.hidden("secret", SECRET)
            if typed_secret is not None:
                typed = dataclasses.replace(typed, secret=typed_secret, secret_from="the token's secret typed")
        else:
            asking.passed("secret")

    # The fleet's ID: the pool's mark, read with the secret given, which is the authority; else the fleet's own.
    full_id = f"{token_user}!{token_id}"
    url = f"https://{host.endpoint}"
    secret = typed.secret if typed.secret is not None else held
    made: tuple[hosts.Made, ...] = ()
    client = None
    unlisted = [setting for setting, *_ in LISTED.values() if own.get(setting) in (None, "")]
    if secret is not None and (typed.secret is not None or (asking is not None and unlisted)):
        client = _connect(url, kept, full_id, secret, found.values["network_proxy"].value, environ)
    try:
        if typed.secret is not None and client is not None:
            found_mark = await _mark(client, full_id, pool)
            rule = mark.may_change(found_mark, found.fleet or record.first(found_mark.fleet))
            if rule is not None:
                raise Refused(f"{rule.removeprefix('error: ')}; nothing was written")
            fleet_id = found_mark.fleet
            names = {"pool": pool, "user": token_user, "token": full_id}
            made = tuple(hosts.Made(what, name, BY_HAND) for what, name in names.items() if name == OWN_NAMES[what])
        else:
            fleet_id = found.fleet.fleet if found.fleet is not None else record.new_id()
        if asking is not None:
            typed = await _the_lists(asking, client, typed, own, address)
    finally:
        if client is not None:
            client.close()
    return _Settled(kept, token_user, token_id, fleet_id, made, typed)


async def _the_lists(
    asking: _Asking, client: Proxmox | None, typed: Given, own: Mapping[str, object], address: str
) -> Given:
    """The node, the storage and the bridge, each asked where the host's own settings.yml sets none and none was given:
    a menu of what the host lists to the token (none without a token's secret, and a storage or a bridge none without a
    node). Each answer is then a value given."""
    import asyncio

    from ..pve.answers import ProxmoxError
    from ..vendors.transport import TransportError

    node = str(own.get("proxmox_node") or "")
    given_now = {"node": "", "storage": typed.storage, "bridge": typed.bridge}
    for what in ("node", "storage", "bridge"):
        setting, word, title, _path = LISTED[what]
        if given_now[what] or own.get(setting) not in (None, ""):
            asking.passed(what)
            continue
        if client is None or (what != "node" and not node):
            asking.passed(what, "skipped")
            asking.left.append(title)
            continue
        try:
            candidates = await asyncio.to_thread(_listed, client, what, node)
        except (ProxmoxError, TransportError) as error:
            asking.passed(what, "skipped")
            asking.left.append(title)
            asking.say(f"{address} did not list its {word}s to the token: {one_line(str(error), 200)}")
            continue
        where = f"of {address}" if what == "node" else f"on {node}"
        text = (QUESTIONED[what].format(where=where), "What your token can see:" if what == "node" else SEEN_THERE)
        if not any(_usable(each) for each in candidates):
            names = ", ".join(each["name"] for each in candidates)
            why = f"{names} lack what it needs" if names else f"Proxmox lists a {word} only to a token that may see it"
            asking.say(f"{title} {where}: the token can use none there ({why})")
        picked = await asking.pick(what, what, candidates, text)
        if what == "node":
            node = picked
            typed = dataclasses.replace(typed, node=picked)
        elif what == "storage":
            typed = dataclasses.replace(typed, storage=picked)
        else:
            typed = dataclasses.replace(typed, bridge=picked)
    return typed


async def _the_fleet(asking: _Asking, typed: Given, found: _Set, environ: Mapping[str, str]) -> Given:
    """The fleet's own questions, each where its value is unset and none was given: the SSH key the machines get, whose
    repositories are cloned, which VPN the machines join, and Tailscale's auth key once Tailscale is chosen. Each answer
    is then a value given."""
    from claude_vm.runs.ask import Choice

    shared = found.layers.fleet.settings
    # The SSH key: vm_ssh_public_keys' default reads ~/.ssh/id_ed25519.pub at each run, as today's; only where that
    # gives none, and nothing sets the setting, is the person asked which of their other public keys.
    if "vm_ssh_public_keys" in shared or found.values["vm_ssh_public_keys"].value:
        asking.passed("key")
    else:
        keys = _public_keys(environ)
        if keys:
            # Each beside its type and its comment.
            notes = [one_line(" ".join(key.split()[:1] + key.split()[2:]), 60) for _file, key in keys]
            listed = [Choice(file, file, note) for (file, _key), note in zip(keys, notes, strict=True)]
            picked = await asking.choice("key", SSH_KEY, listed)
            if picked:
                typed = dataclasses.replace(typed, keys=(dict(keys)[picked],))
        else:
            asking.passed("key", "skipped")
            asking.left.append(SSH_KEY.label)
            asking.say(
                "the VMs would get no SSH key, and ~/.ssh has no public key: make one with ssh-keygen -t ed25519,"
                " which they then get"
            )
    if typed.github_user or shared.get("github_username") not in (None, ""):
        asking.passed("github")
    else:
        answer = await asking.line("github", GITHUB)
        if answer:
            typed = dataclasses.replace(typed, github_user=answer)
    vpn = shared.get("vpn")
    if typed.tailscale or vpn not in (None, ""):
        asking.passed("vpn")
    else:
        picked = await asking.choice("vpn", VPN, [Choice(value, value, note) for value, note in VPN_CHOICES])
        if picked:
            typed = dataclasses.replace(typed, tailscale="on" if picked == "tailscale" else "off")
    # Tailscale's auth key: only once Tailscale is chosen, where the vault holds none and the web login is not the only
    # way, hidden, and never from the command line.
    chosen = typed.tailscale == "on" if typed.tailscale else vpn == "tailscale"
    if not chosen:
        asking.passed("tailscale", "skipped")
    elif (found.vault is not None and AUTH_KEY in found.vault) or found.values["tailscale_auth_method"].value == "web":
        asking.passed("tailscale")
    else:
        auth_key = await asking.hidden("tailscale", TAILSCALE_KEY)
        if auth_key is not None:
            typed = dataclasses.replace(typed, auth_key=auth_key)
    return typed


# What a secret sealed in the vault is said to be.
SEALED = {"token": "the token's secret", AUTH_KEY: "Tailscale's auth key"}


def _write(
    home: paths.Home,
    layout: hosts.Layout,
    found: _Set,
    typed: Given,
    settled: _Settled | None,
    environ: Mapping[str, str],
) -> list[str]:
    """Every write of the run, each through its one writer, each only where nothing is set: the vault's key, fleet.json
    and the vault, where the host is settled or a secret goes into it; the secrets; host.yml and its anchor, the host's
    own settings and client.yml's current host, where the host is settled; and the fleet's own settings. What was
    written, as a line names each."""
    name, state, fields = found.name, found.state, found.state.fields
    held = None if found.vault is None else found.vault.get(secrets.entry("token", host=name))
    own = {} if found.layers.host is None else found.layers.host.settings
    shared = found.layers.fleet.settings
    seal: list[tuple[str, Secret, str | None]] = []
    if settled is not None and typed.secret is not None and held is None:
        seal.append(("token", typed.secret, name))
    if typed.auth_key is not None:
        seal.append((AUTH_KEY, typed.auth_key, None))
    if settled is not None:
        fleet_id = settled.fleet_id
    else:
        fleet_id = found.fleet.fleet if found.fleet is not None else record.new_id()
    wrote: list[str] = []
    key_file = found.values["vault_key_file"].value
    try:
        if settled is not None or seal:
            key = found.key
            if key is None:
                place = vaultkey.place(home, key_file, environ=environ)
                key = vaultkey.make_key(place)
                wrote.append(f"the vault's key ({_written(home, place)})")
            if found.fleet is None:
                record.write(home, record.first(fleet_id))
                wrote.append(_written(home, record.path(home)))
            if found.vault is None:
                sealed.create(home, key)
                wrote.append(f"the vault ({_written(home, sealed.path(home))})")
            for secret_name, value, owner in seal:
                sealed.put(home, key, secret_name, value, host=owner)
                wrote.append(f"{SEALED[secret_name]}, sealed in the vault as {secrets.entry(secret_name, host=owner)}")
        if settled is not None:
            kept = settled.kept
            if kept.anchor is None:
                anchor: Any = None if (fields is not None and fields.trust_kind is not None) else hosts.UNCHANGED
            else:
                anchor = kept.anchor if kept.changed else hosts.UNCHANGED
            change = _change(typed.address, settled.token_user, settled.token_id, settled.made)
            digest = hosts.write(layout, name, state.digest, change=change, anchor=anchor)
            if digest != state.digest:
                wrote.append(f"{hosts.HOSTS_REL}/{name}/{hosts.HOST_FILE}")
            digest = found.layers.host.digest if found.layers.host is not None else overrides.MISSING
            before = digest
            pool = typed.pool or own.get("proxmox_pool") or POOL
            for setting, value in zip(HOST_OWN, (typed.storage, typed.bridge, pool, typed.node), strict=True):
                if value and own.get(setting) in (None, ""):
                    digest = overrides.set(home, setting, value, name, digest, environ=environ)
            if digest != before:
                wrote.append(f"{hosts.HOSTS_REL}/{name}/{overrides.SETTINGS}")
        digest = before = found.layers.fleet.digest
        if typed.github_user and shared.get("github_username") in (None, ""):
            digest = overrides.set(home, "github_username", typed.github_user, None, digest, environ=environ)
        if typed.tailscale and shared.get("vpn") in (None, ""):
            vpn = "tailscale" if typed.tailscale == "on" else "none"
            digest = overrides.set(home, "vpn", vpn, None, digest, environ=environ)
        if typed.keys and "vm_ssh_public_keys" not in shared:
            digest = overrides.set(home, "vm_ssh_public_keys", list(typed.keys), None, digest, environ=environ)
        if digest != before:
            wrote.append(f"{paths.FLEET}/{overrides.SETTINGS}")
        if settled is not None and not found.layers.client.own.get("current_host"):
            overrides.set_current(home, name, found.layers.client.digest, environ=environ)
            wrote.append(f"{paths.CLIENT} (its current host, {name})")
    except (
        sealed.Refused,
        record.Refused,
        hosts.Refused,
        overrides.Refused,
        fleet_files.Unsafe,
        paths.Refused,
    ) as error:
        said_now = str(error).removeprefix("error: ")
        raise Refused(f"{said_now}: what was written before it stays, and init goes on from it") from None
    except OSError as error:
        why = error.strerror or error
        raise Refused(f"the fleet's folder could not be written ({why}): init goes on from here") from None
    return wrote


def _report(
    home: paths.Home,
    found: _Set,
    typed: Given,
    settled: _Settled | None,
    wrote: Sequence[str],
    asking: _Asking | None,
) -> list[str]:
    """What a run says once it has written: the certificate, the fleet, what was written, and what is left. The fleet's
    line is said wherever the host was settled with a secret given or typed, whose token read the pool's mark: made may
    record nothing then."""
    name = found.name
    own = {} if found.layers.host is None else found.layers.host.settings
    lines = [] if settled is None else [settled.kept.said]
    if settled is not None and settled.typed.secret is not None:
        lines.append(f"the fleet {settled.fleet_id}, as the pool's mark names it, which the token given reads")
    lines.append(
        f"wrote {', '.join(wrote)}" if wrote else "nothing to write: the fleet's folder holds every value given already"
    )
    if asking is None:
        lines.append("nothing was made on the Proxmox host: with nobody there init makes nothing on it")
    else:
        lines.append("nothing was made on the Proxmox host")
    unset = [
        setting
        for setting, value in (
            ("proxmox_node", typed.node),
            ("proxmox_storage", typed.storage),
            ("proxmox_bridge", typed.bridge),
        )
        if not value and own.get(setting) in (None, "")
    ]
    if settled is not None and unset and asking is None:
        lines.append(
            f"{', '.join(unset)} {'is' if len(unset) == 1 else 'are'} set for the host by init's checks on it (card E6,"
            f" not built yet): until then write {'it' if len(unset) == 1 else 'them'} in"
            f" {hosts.HOSTS_REL}/{name}/{overrides.SETTINGS}"
        )
    if asking is not None and asking.left:
        lines.append(
            said(
                "left for later: {left}; the next {init} asks again",
                {"left": _listed_words(asking.left), "init": _init()},
            )
        )
    return [*lines, *typed.notes]


# ---------------------------------------------------------------- the verb ----


def _refused(sentence: str) -> int:
    print(sentence, file=sys.stderr)
    return EXITS["refused"]


def _at_a_terminal() -> bool:
    """Whether a person is there to ask: standard input a terminal whose foreground this process is, as runs/ask.py's
    terminal() decides for each question, through that one function. Never isatty alone: a run in the background (aw
    init &) would draw its live view, whose change of the terminal's settings has the terminal stop it (SIGTTOU) before
    its first question; it is a run with nobody there."""
    from claude_vm.runs.ask import terminal

    return terminal() is not None


async def _asked(
    running: Context, home: paths.Home, typed: Given, environ: Mapping[str, str], verbosity: object
) -> int:
    """init at a terminal, its run's body: drawn live from its events (INIT), what attended says of its writes said
    above the region as one report, and a refusal said as the stage's failure, with nothing written. The exit
    status."""
    from claude_vm.runs import live

    counted = verbosity if isinstance(verbosity, int) and not isinstance(verbosity, bool) else 0
    live.Live(running.job, INIT, sys.stdout, verbosity=counted, cancelled=running.cancelled)
    try:
        lines = await attended(home, typed, running=running, environ=environ)
    except Refused as refused:
        running.emit("stage", None, SETTINGS, outcome="failed", why=refused.sentence.removeprefix("error: "))
        return EXITS["refused"]
    running.emit("report", None, "\n".join(lines))
    return EXITS["done"]


def init(run: Any, **values: object) -> int:
    """init's handler (words.py's row): the token's secret taken out of the environment first, the modes not built yet
    refused by name, the values held to their kinds, a home that is a link refused, and the fleet's folder written, as
    its run, under the fleet's lock: at a terminal with no --no-prompt asking what is unset (attended), drawn live,
    else with nobody there. The exit status."""
    # First of all: nothing started from here on holds the token's secret in its environment.
    variable = taken()
    for field, card in NOT_YET.items():
        if values.get(field) is True:
            mode = {"line": _init(**{field: True}), "card": card}
            return _refused(said("error: {line} is not built yet (card {card} builds it)", mode))
    asks = values.get("no_prompt") is not True and _at_a_terminal()
    environ = dict(os.environ)
    try:
        typed = given(values, getattr(run, "set", ()), cwd=os.getcwd(), environ=environ, variable=variable)
        home = paths.home(PROGRAM_ROOT, environ)
        _held(home)
    except Refused as refused:
        return _refused(refused.sentence)
    except paths.Refused as error:
        return _refused(f"error: {error}")

    from claude_vm import runs
    from claude_vm.runs import locks

    async def body(running: Context) -> int:
        if asks:
            return await _asked(running, home, typed, environ, getattr(run, "verbosity", 0))
        try:
            lines = await unattended(home, typed, environ=environ, job=running.job)
        except Refused as refused:
            return _refused(refused.sentence)
        for each in lines:
            print(each)
        return EXITS["done"]

    detached = getattr(run, "detached_run", False) is True
    draft = runs.Draft(
        paths.jobs_dir(home),
        "init",
        line("init", values, refused=True),
        typed.host or None,
        [],
        {"kind": "detach" if detached else "terminal"},
        True,
        values=values,
    )
    try:
        return runs.run(draft, body)
    except locks.Held as held:
        return _refused(held.sentence)
