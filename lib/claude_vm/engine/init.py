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

The names written where none is given are the engine's own, engine/access.py's (card E1; ruling B1, rule 1): the user
`agent-warehouse@pve`, the token `aw` and the pool `aw-fleet`; and the first host is `pve`, as today's. The modes built
by other cards are refused by name until they land: `--machines` (J14).

**The checks** (card E6, engine/init_checks.py): once the run has written, at a terminal or with nobody there, init
checks the host as the fleet's files now open it and says one of today's `ok`, `!!`, `--` and `note` lines a check,
settling the node, the storage and the bridge where the host's own settings.yml sets none, with nobody there alone (at
a terminal the person's answers are the rule); it exits 1 exactly where a `!!` line was said. `aw init --check` runs
the same checks under the same lock, takes no value but `--host`, and writes nothing.

**The offer** (card E7), at a terminal alone, between the token's ID and its secret, once the certificate is settled:
the access script, engine/access.py's `as_script` rendered for the host's values and the fleet's ID (fleet.json's where
there is one), is run on the host as root over root SSH in batch mode, `/bin/sh -s` reading it from its standard input,
never a copy left there: its `--list` where no storage or bridge is given or set (the one in effect where the node lists
it, else the person asked), the node read first where settings.yml names none (`hostname -s`), then its `--dry-run`,
with `--no-token` where the vault holds the token's secret or one is given. Where something is missing, the plan (each
object of the list with its privileges, and whether it is there), the dry run's commands and what the script said are
shown, with today's question, "Set it up now? y or n", no first; and on y alone the script runs. Its standard output,
the token's secret, is held to a UUID and sealed in the vault before anything else runs, then read back; where it is
not there as printed, or the script printed none, or failed after it made the token, the token the run made is deleted
again, and init stops. What it made of the engine's own names (the pool, the user, the token, the roles and their
grants) is recorded in host.yml's made, with this engine its maker, from the commands it said it ran (its `+` lines).
No, Esc, Enter, root SSH that does not answer, a script that refuses, nothing missing, and a token there whose secret
is held nowhere here, in a pool nothing shows is this fleet's, leave the host as it was, said, and the secret is then
asked for. With nobody there no offer is made.

**The pool's mark** (card E8). Before the dry run the pool is read over root SSH (pve/script.py's `pool_read`), which
decides only what is asked; the script decides again. A pool whose comment carries no mark is taken only where it is
named (`--pool`, or the host's `proxmox_pool`): the question then shows its comment and its members, and its y runs
the script with `--pool <name> --take-unmarked`, the mark added at the comment's end and the rest kept. By default it is
refused, naming both ways, the by-hand road in that same form (`_take_unmarked`), as is a pool with no mark that a
secret given reads with nobody there. Where no token's secret is held or given and the pool's comment carries a mark
with `controller=none`, another fleet's, or this fleet's with its token there, init asks which of two things is meant
(WHICH) and does neither unasked: taking the fleet over (where fleet.json names no fleet or the mark's) renders the
script with the mark's ID and runs it with `--regenerate`, so that the token is made again and every other copy of its
secret stops working, and fleet.json keeps the mark's ID; using another pool (where settings.yml names none) takes its
name and, where no token was named, the token's full ID, two fleets never sharing one, and starts a fleet of this
computer's own there. Where the mark's ID is fleet.json's, the first is asked as making this fleet's own token's
secret again (AGAIN), never as a take-over (review 72). A token whose secret the run made again (`token modify`)
is never deleted where the vault cannot be written (`_rescue`): it was not this run's, and deleting it would leave the
fleet with none; one a take-over adds, the token gone, is the run's own and deleted (review 76). A mark
that names a control VM is refused by the script with fleet/mark.py's sentence, and neither is offered; Enter or Esc
does neither. `--print-commands` prints the same script on standard output, and on standard error how to run it,
reading the fleet's files alone; its one write, with no fleet.json, is the fleet's ID it renders, there (card E8), so
that two prints and the offer render one ID.

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

import contextlib
import dataclasses
import errno
import json
import os
import shlex
import stat
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, MutableMapping, Sequence
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
from . import access, certificate, init_checks, report, routes

if TYPE_CHECKING:
    from ..pve.client import Proxmox
    from ..runs.ask import Choice
    from ..runs.attached import Context
    from ..runs.job import Job

# The tree of the program that runs, whose fleet's home init writes: this module's own checkout or release, which
# tools/aw holds to the one it runs from (plugin.own), as cli/main.py's checkout works it out from tools/aw's path.
PROGRAM_ROOT = Path(__file__).resolve().parents[3]
# The names init writes for a host where none is given are the engine's own, engine/access.py's, their one home (card
# E1; ruling B1, rule 1): access.USER, access.TOKEN and access.POOL. The first host's name is today's first host's.
FIRST_HOST = "pve"
# Where the token's secret is given when no file is: the environment, never the command line; the most of a file of
# it read, more than a secret is.
SECRET_VARIABLE = "AGENT_WAREHOUSE_TOKEN_SECRET"
SECRET_MOST = 4096
# Who made what init records: the administrator, by hand, from the printed script, where the secret was given; this
# engine, through the offer, of what the script made after a y (card E7). Only the engine's own default names are
# recorded (card E19, the owner's answer of 2026-11-23): the pool, the token's user and the token by its full ID, each
# where its name equals OWN_NAMES's, the roles of access.ROLES, and a grant of one of those roles to that user or that
# token (_own_name); nothing for a name the person gave or host.yml sets, which may name an object of the person's own.
BY_HAND = "administrator"
BY_ENGINE = "engine"
OWN_NAMES = {"pool": access.POOL, "user": access.USER, "token": f"{access.USER}!{access.TOKEN}"}
# The modes of init built by other cards, refused by name until each lands.
NOT_YET = {"machines": "J14"}
# What init --check refuses beside it: every value init writes, and the other modes.
CHECK_REFUSES = (
    "address", "token_user", "token_id", "token_secret_file", "github_user", "storage", "bridge", "tailscale",
    "certificate", "pool", "print_commands", "machines",
)  # fmt: skip
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
                Step("access", "API access"),
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
OWN_TOKEN = OWN_NAMES["token"]
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
        token = f"{token_user or access.USER}!{token_id or access.TOKEN}"
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


async def _mark(client: Proxmox, token_id: str, pool: str, address: str, host_flag: str) -> mark.Mark:
    """The mark in the pool's comment, read with the token given, through client: Refused where Proxmox does not take
    the token for the pool, the pool is not there, or its comment carries no mark, or one that does not read. A pool
    with no mark is refused naming the two ways it is taken (card E8): named at a terminal, on a y, or by hand with the
    script's --pool and --take-unmarked, on address, for the host host_flag names."""
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
        raise Refused(f"{_take_unmarked(pool, address, host_flag)}; nothing was written")
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
        """A line said above the region, whole: init's settings, which leaves NEXT STEPS nothing until the run's
        last lines say what was left."""
        report.say(self.running, None, "init_settings", text, "")

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

    async def typed(self, words: _Words, held: Callable[[str], str], rule: str) -> str:
        """A typed answer asked within another question's step (card E8's, in the offer's): held by held, which gives
        its plain form or "", asked once more saying rule where it does not fit; "" where it was left or a second does
        not fit. Nothing is left for the next run: the offer says what it did."""
        for again in (False, True):
            lines = words.text if not again else (f"That is not {rule}.", words.text[-1])
            answer = str(await self.ask(_question(lines, words.label), "")).strip()
            if not answer:
                return ""
            if kept := held(answer):
                return kept
        return ""

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


def _sealed_in(
    home: paths.Home,
    found: _Set,
    seal: Sequence[tuple[str, Secret, str | None]],
    fleet_id: str,
    environ: Mapping[str, str],
) -> tuple[Secret, list[str]]:
    """The vault's writes, each where nothing is there yet: the vault's key, fleet.json and the vault, then each secret
    of seal (its name, its value, the host it is of) put in it, the one way a secret goes into it. The key, and what
    was written. The writers' own refusals, and OSError, as they raise them."""
    wrote: list[str] = []
    key = found.key
    if key is None:
        place = vaultkey.place(home, found.values["vault_key_file"].value, environ=environ)
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
    return key, wrote


# What a writer refuses, which init says with what was written before it.
WRITERS_REFUSE = (
    sealed.Refused,
    record.Refused,
    hosts.Refused,
    overrides.Refused,
    fleet_files.Unsafe,
    paths.Refused,
)


# ---------------------------------------------------------------- the offer over root SSH (card E7) ----

# What runs one command on the host as root over root SSH, as a child of the run: its words, its standard input, and
# the seconds it may take; the runner's result (_shell).
Shell = Callable[[Sequence[str], str, float], Awaitable[runner.Result]]
# The access script, read by the host's shell from its standard input, never a copy of it left there: its options
# after these words.
SH = ("/bin/sh", "-s", "--")
# The host's own short name, which Proxmox names its node by, read where the host's settings.yml names no node: the
# script is rendered for one node, as today's script took the node from hostname -s.
NODE_COMMAND = ("hostname", "-s")
# How long the script may take: a read (the node, --list, --dry-run), and the run, which makes each object with a pveum
# of its own.
READ_SECONDS = runner.SECONDS
RUN_SECONDS = 600.0
# The question, today's.
SET_UP = "Set it up now?"
# A dry run's own lines that say what the question says itself, left out of it: the script says them to a person who
# runs it by hand.
DRY_RUN_HEADS = ("The commands this would run", "the token's line prints JSON")
# The script's line where nothing is missing.
NOTHING_TO_DO = "Nothing to do"
# The script's own words that it made the token, in a run that then stopped (pve/script.py's make_token and fail).
TOKEN_MADE = ("after the token was made", "made the token and printed no secret")
# Card E8: the question asked of a pool another fleet's mark names, or of this fleet's pool whose token is there with
# its secret held nowhere here, where no token's secret is held or given; its two answers.
WHICH = "Take the fleet over, or use another pool?"
# The same two, of this fleet's own pool (its mark's ID fleet.json's), its token there with its secret held nowhere
# here: a run of this computer's cut before the secret was kept, or another computer of the fleet. Never a take-over.
AGAIN = "Make the token's secret again, or use another pool?"
TAKE, ANOTHER = "take", "another"
# The most of an unmarked pool's members the question names.
MEMBERS_SAID = 10
# init's own line, as a sentence names it.
NEXT = {"init": Line("init")}
# What proxmox_audit_scope lets the token read, a line of the plan of its own (O15, item 2; the owner's answer of
# 2026-11-24 to review 58's finding 5).
SCOPE_SAID = {
    access.NODE_SCOPE: (
        "VM.Audit on /vms, read only, lets status show the node's other VMs, as today; proxmox_audit_scope: pool"
        " keeps the token to the pool"
    ),
    "pool": (
        "proxmox_audit_scope is pool: the token's writes and its reads of single VMs stay in the pool, while"
        " Sys.Audit on the node, for status's capacity, also reads the node's report, every guest's configuration"
    ),
}


class _NotOffered(Exception):
    """The offer is not made: why, the sentence said; the host is left as it was."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


@dataclasses.dataclass(frozen=True)
class _Offered:
    """What the offer did after a y: what the script made, by ID, this engine its maker, of the engine's own names
    alone (_own_name); each command it ran, as it said it; the token's secret it printed, None where it made no token,
    sealed in the vault before anything else ran; what was written then; and the fleet's ID the script was rendered
    with, the pool's mark's where the fleet was taken over (card E8)."""

    made: tuple[hosts.Made, ...]
    ran: tuple[str, ...]
    secret: Secret | None
    wrote: tuple[str, ...]
    fleet: str


def _shell(
    job: Job,
    address: str,
    fields: hosts.HostFile | None,
    values: Mapping[str, overrides.Value],
    environ: Mapping[str, str],
    target: runner.Target | None = None,
) -> Shell:
    """A command run on the host as root over root SSH, as a child of job's run, with environ, which holds no secret
    (taken): at the host's own Target as routes.host_target makes it (_root_ssh's), or at target where a caller gives
    one, and with no bound on how long it says nothing, since the script prints only at its end. A target the runner
    refuses is root SSH that did not answer."""

    async def run(command: Sequence[str], stdin: str, seconds: float) -> runner.Result:
        try:
            reached = target or routes.host_target(
                address,
                fields.ssh_user if fields is not None else "root",
                fields.ssh_port if fields is not None else 22,
                values,
                environ,
            )
            return await runner.run(job, reached, command, stdin, runner.Limits(seconds=seconds, silent=None), environ)
        except (runner.Refused, paths.Refused) as error:
            return runner.Unreachable(one_line(str(error), 300))

    return run


def _why(result: runner.Result) -> str:
    """Why a command over root SSH gave nothing to use, in plain words."""
    if isinstance(result, runner.Done):
        return one_line(f"it ended with status {result.status}: {result.said}", 600)
    if isinstance(result, runner.Cut):
        return one_line(f"it sent more than was asked: {result.why}", 300)
    if isinstance(result, runner.Silent):
        return "it said nothing in time"
    if isinstance(result, runner.TimedOut):
        return "it did not end in time"
    return one_line(result.said or "it could not be reached", 300)


def _said_lines(result: runner.Result) -> list[str]:
    """What the script said on its standard error, a line each, plain, none empty."""
    return [one_line(each, None) for each in getattr(result, "said", "").splitlines() if each.strip()]


def _by_hand(address: str, host_flag: str) -> str:
    """How the person sets the access up by hand: the script printed, piped to the host's root."""
    return said(
        "to set it up yourself, run {print_commands} | ssh root@{address} sh -s -- --dry-run here to see each command"
        " it would run there, then the same without --dry-run",
        {"print_commands": Line("init", {"print_commands": True}, host_flag=host_flag), "address": address},
    )


def _object_of(words: Sequence[str]) -> tuple[str, str, bool] | None:
    """The object one of the script's commands makes or changes, as host.yml's made names it: what it is, its ID, and
    whether the command makes it (True), or changes one that is there (False: a pool taken, the fleet's mark added to
    its comment; a role given what it lacks; the token's secret made again on a take-over, card E8). None for a command
    that is none of the list's."""
    match list(words):
        case ["pveum", "pool", "add", name, *_]:
            return ("pool", name, True)
        case ["pveum", "pool", "modify", name, *_]:
            return ("pool", name, False)
        case ["pveum", "role", "add", name, *_]:
            return ("role", name, True)
        case ["pveum", "role", "modify", name, *_]:
            return ("role", name, False)
        case ["pveum", "user", "add", name, *_]:
            return ("user", name, True)
        case ["pveum", "user", "token", "add", user, name, *_]:
            return ("token", f"{user}!{name}", True)
        case ["pveum", "user", "token", "modify", user, name, *_]:
            return ("token", f"{user}!{name}", False)
        case ["pveum", "acl", "modify", path, "--users" | "--tokens", subject, "--roles", role]:
            return ("grant", access.Grant(path, role, subject).id, True)
    return None


def _own_name(what: str, name: str) -> bool:
    """Whether made may record an object by this name: one of the engine's own default names (card E19): the pool
    aw-fleet, the user agent-warehouse@pve, its token aw by its full ID, a role of access.ROLES, and a grant of one of
    those roles to that user or that token. A grant's own name is its role and whom it is for; its path may name a pool,
    a storage, a bridge or a node the person gave, which the grant's removal leaves as it is."""
    if what == "role":
        return name in access.ROLES
    if what == "grant":
        path, _, rest = name.partition(" ")
        role, _, subject = rest.partition(" ")
        own = (OWN_NAMES["user"], OWN_NAMES["token"])
        return role in access.ROLES and subject in own and access.ACL_PATH.fullmatch(path) is not None
    return OWN_NAMES.get(what) == name


def _script(
    found: _Set,
    *,
    fleet_id: str,
    user: str,
    token: str,
    node: str,
    storage: str,
    bridge: str,
    pool: str,
    host_flag: str,
) -> tuple[access.Host, str]:
    """The access script rendered for the host (engine/access.py's as_script), and the Host it was rendered for: the
    host's settings as they are resolved, with the node, the storage, the bridge and the pool this run gives, the
    fleet's ID (fleet.json's where there is one), the token's user and ID, and a person's own template by the name
    proxmox_template_custom gives, which the script looks up on the host with the bridges of its network cards, as on
    the road by hand (card E1's point 2: the lookup is the script's, never a second reader here). Refused, saying why,
    where a value is none Proxmox makes an object by."""
    settings = {name: value.value for name, value in found.values.items()}
    settings.update(proxmox_node=node, proxmox_storage=storage, proxmox_bridge=bridge, proxmox_pool=pool)
    custom = settings.get("proxmox_template_custom") or ""
    try:
        host = access.Host.of(settings, fleet=fleet_id, user=user, token=token)
        return host, access.as_script(host, str(custom), host_flag=host_flag)
    except access.Refused as error:
        raise Refused(f"the access script cannot be rendered for the host {found.name}: {error}") from None


def _described(obj: access.Object) -> str:
    """One object of the list as the plan says it, with its privileges."""
    if isinstance(obj, access.Pool):
        return f"the pool {obj.name}, its comment the fleet's mark"
    if isinstance(obj, access.Role):
        return f"the role {obj.name}, with {', '.join(obj.privileges)}"
    if isinstance(obj, access.User):
        return f"the user {obj.name}, with no password"
    if isinstance(obj, access.Token):
        return f"the token {obj.id}, with privilege separation"
    return f"{obj.role} on {obj.path} for {obj.subject}"


def _plan(listed: Sequence[access.Object], commands: Sequence[Sequence[str]], scope: str) -> list[str]:
    """E1's list rendered for people, in the order the script makes it: each object with its privileges, and what the
    dry run says of it - there already, missing and made, or changed (a role given what it lacks, a pool taken, its
    mark added); then what the script makes beyond the list, a person's own template's role and grants, which it looked
    up on the host; then what proxmox_audit_scope lets the token read, a line of its own (SCOPE_SAID)."""
    changes: dict[tuple[str, str], str] = {}
    for words in commands:
        named = _object_of(words)
        if named is None:
            continue
        what, name, made = named
        if made:
            changes[(what, name)] = "missing, made"
        elif what == "role":
            changes[(what, name)] = f"lacks {words[-1]}, given it"
        elif what == "token":
            changes[(what, name)] = "there, its secret made again: every other copy of it stops working"
        else:
            changes[(what, name)] = "unmarked, taken: the fleet's mark added to its comment"
    lines = [f"  {_described(obj)}: {changes.pop((obj.what, obj.id), 'there')}" for obj in listed]
    lines += [f"  the {what} {name}: {state}" for (what, name), state in changes.items()]
    return [*lines, f"  {SCOPE_SAID[scope]}"]


def _token_line(words: Sequence[str], full_id: str) -> bool:
    """Whether one of the script's commands makes the token, or makes its secret again (card E8)."""
    named = _object_of(words)
    return named is not None and named[:2] == ("token", full_id)


def _take_unmarked(pool: str, address: str, host_flag: str) -> str:
    """The two ways a pool whose comment carries no mark is taken (card E8; R2 C-1): named, at a terminal, on a y to the
    question that shows its comment and its members; or by hand, the script run there with --pool and --take-unmarked,
    which an administrator confirms by running."""
    return said(
        "the pool {pool} on {address} carries no fleet's mark in its comment, and init takes a pool nobody marked only"
        " where it is named and a y says so: at a terminal, {named} shows its comment and its members and asks; or, to"
        " set it up yourself, run {print_commands} | ssh root@{address} sh -s -- --pool {pool} --take-unmarked",
        {
            "pool": pool,
            "address": address,
            "named": Line("init", {"pool": pool}, host_flag=host_flag),
            "print_commands": Line("init", {"print_commands": True, "pool": pool}, host_flag=host_flag),
        },
    )


async def _pool_there(shell: Shell, pool: str) -> Any:
    """The pool as root reads it over root SSH (pve/script.py's pool_read), its comment and its members, read as the
    API's answer is (pve/answers.py's pool); None where it is not there or could not be read: the script then decides
    on its own, as it does of every pool."""
    from ..pve import answers as read
    from ..pve import script

    result = await shell(script.pool_read(pool), "", READ_SECONDS)
    if not (isinstance(result, runner.Done) and result.status == 0):
        return None
    try:
        return read.pool(json.loads("\n".join(result.lines)), pool)
    except (ValueError, read.ProxmoxError):
        return None


def _unmarked_lines(there: Any, address: str) -> list[str]:
    """What the offer's question shows of a pool whose comment carries no mark, which a y takes: its comment and its
    members, each plain."""
    members = [one_line(" ".join(part for part in (each.id, each.name) if part), 80) for each in there.members]
    more = len(members) - MEMBERS_SAID
    listed = ", ".join(members[:MEMBERS_SAID]) + (f", and {more} more" if more > 0 else "")
    return [
        f"The pool {there.poolid} is on {address} already, and its comment carries no fleet's mark: a y makes it this"
        " fleet's, its comment kept and the fleet's mark added at its end.",
        f"  its comment: {one_line(there.comment, access.SAID_MOST) or '(none)'}",
        f"  its members: {listed or '(none)'}",
    ]


async def _which(
    asking: _Asking,
    *,
    pool: str,
    address: str,
    found: mark.Mark,
    full_id: str,
    take: bool,
    another: bool,
    refused: str,
    own: bool,
) -> str:
    """Card E8: which of two things is meant, of a pool another fleet's mark names (or this fleet's, its token there
    with its secret held nowhere here), where no token's secret is held or given: TAKE, the fleet taken over, its ID the
    mark's and its token made again (--regenerate), so that every other copy of its secret stops working; ANOTHER, a
    fleet of this computer's own in another pool; "" for neither, at Enter or Esc. take: where fleet.json names no
    fleet or the mark's; another: where the host's settings.yml names no pool. Neither of them: _NotOffered with
    refused, mark.may_change's sentence. own: the mark's ID is fleet.json's (review 72), so the first is asked as making
    this fleet's own token's secret again (AGAIN), never as a take-over."""
    from claude_vm.runs.ask import Choice

    if not (take or another):
        raise _NotOffered(f"{refused.removeprefix('error: ')}; nothing was made on {address}")
    if own:
        head = (
            f"The pool {pool} on {address} carries this fleet's own mark, and its token {full_id} is there with its"
            " secret held nowhere here: a run of this computer's cut before the secret was kept, or another computer"
            " of the fleet's. init does neither of these unasked:"
        )
        taking = (
            f"  make the token's secret again: the token {full_id} is made again, its secret new, so that every other"
            " copy of it stops working;"
        )
    else:
        head = (
            f"The pool {pool} on {address} carries the mark of the fleet {found.fleet}, and no secret of a token of it"
            " is held here. init does neither of these unasked:"
        )
        taking = (
            f"  take the fleet over: this computer drives the fleet {found.fleet}, and the token {full_id} is made"
            " again, its secret new, so that every other copy of it stops working;"
        )
    other = "  use another pool: a fleet of this computer's own, in a pool you name, beside that one."
    lines = [
        head,
        *([taking] if take else []),
        *([other] if another else []),
        "Enter, or Esc, does neither, and nothing on the host changes.",
    ]
    choices = [
        *([Choice(TAKE, "make its secret again" if own else "take the fleet over")] if take else []),
        *([Choice(ANOTHER, "use another pool")] if another else []),
    ]
    return str(await asking.ask(_question(lines, AGAIN if own else WHICH, colon=False), "", choices))


async def _another(asking: _Asking, pool: str, full_id: str, token_named: bool) -> tuple[str, str]:
    """Card E8's second answer: the pool for a fleet of this computer's own, a name Proxmox makes a pool by and not
    pool, and, where no token was named (given, or host.yml's other than the engine's own), the token's full ID, Enter
    keeping full_id: two fleets never share a token, and the other pool's fleet has its own. _NotOffered where no pool
    was named."""
    rule = access.POOL_RULE
    name = await asking.typed(
        _Words(
            "Pool",
            (
                f"The pool for this fleet's VMs, beside {pool}: one that is not there is made, and one whose comment",
                f"carries no mark is shown and taken on a y. {rule[:1].upper()}{rule[1:]}. Enter leaves it.",
            ),
        ),
        lambda typed: typed if _pool_name(typed) and typed != pool else "",
        f"a pool's name other than {pool}",
    )
    if not name:
        raise _NotOffered("no other pool was named, so init makes nothing there")
    if token_named:
        return name, full_id
    token = await asking.typed(
        _Words(
            "API token ID",
            (
                "The API token this fleet uses there, as user@realm!name: two fleets never share a token, and the",
                f"fleet of {pool} has its own. Enter keeps {full_id}.",
            ),
        ),
        lambda typed: answers.init_answer(typed, "token_id"),
        _rule("token_id"),
    )
    return name, token or full_id


async def _node(shell: Shell, address: str, host_flag: str) -> str:
    """The node the host names itself, over root SSH: _NotOffered where root SSH did not answer, or not with a name
    Proxmox gives a node."""
    result = await shell(list(NODE_COMMAND), "", READ_SECONDS)
    if not (isinstance(result, runner.Done) and result.status == 0):
        raise _NotOffered(_unanswered(address, result, host_flag))
    name = result.lines[0].strip() if len(result.lines) == 1 else ""
    if not proxmox_name(name, "node"):
        raise _NotOffered(
            f"{address} gives no node's name over root SSH ({one_line(name, 60) or 'nothing'}), so init makes nothing"
            f" there: {_by_hand(address, host_flag)}"
        )
    return name


def _unanswered(address: str, result: runner.Result, host_flag: str) -> str:
    return (
        f"root SSH to {address} did not answer as asked ({_why(result)}), so init makes nothing there:"
        f" {_by_hand(address, host_flag)}"
    )


async def _picked(asking: _Asking, what: str, names: Sequence[str], node: str) -> str:
    """The storage or the bridge to set the access up on, of those the node lists as root sees them: a yes or no for
    the only one, yes marked first, as E5's menus; a menu of several. "" where it was left."""
    from claude_vm.runs.ask import Choice

    title = LISTED[what][2]
    text = (QUESTIONED[what].format(where=f"on {node}"), f"{node} lists, as root sees it:")
    rows = [f"  {'-  ' if len(names) == 1 else f'{number}) '}{name}" for number, name in enumerate(names, 1)]
    if len(names) == 1:
        answer = await asking.ask(_question([*text, *rows], f"Use {names[0]}?", colon=False), "", _yes_no())
        return names[0] if answer == YES else ""
    choices = [Choice(name, name, "") for name in names]
    return str(await asking.ask(_question([*text, *rows], title, colon=False), "", choices))


async def _rescue(shell: Shell, user: str, token: str, address: str, again: bool = False) -> str:
    """The token this run made deleted on the host, its secret held nowhere: the vault could not be written, or the
    script printed no secret, or failed after it made the token. Besides the script's run after a y, the one change
    init makes on Proxmox; never where the vault holds the secret. The sentence that says it was, or the line that
    deletes it where it could not be. again: the run's own token line made the token's secret again (`token modify
    --regenerate`, card E8), so the run never made it and it is never deleted (review 72): it stays with its grants,
    its secret held nowhere, as the next init finds it and asks to make the secret again; deleting it would leave the
    fleet with no token at all. A token a take-over adds, where it was gone, is the run's own, and deleted (review
    76)."""
    if again:
        return said(
            "the token {token} was there before this run, which made its secret again, and it is never deleted: it is"
            " on {address} with its secret held nowhere, and the next {init} asks to make its secret again",
            {"token": f"{user}!{token}", "address": address, "init": _init()},
        )
    words = ["pveum", "user", "token", "delete", user, token]
    result = await shell(words, "", READ_SECONDS)
    if isinstance(result, runner.Done) and result.status == 0:
        return f"the token {user}!{token} this run made was deleted on {address} again, so none is left unheld"
    return (
        f"the token {user}!{token} this run made could not be deleted on {address} ({_why(result)}): as root there,"
        f" {shlex.join(words)} deletes it"
    )


def _keep(home: paths.Home, found: _Set, secret: Secret, fleet_id: str, environ: Mapping[str, str]) -> list[str]:
    """The token's secret the script printed, sealed in the vault at once, before anything else runs: the vault's key,
    fleet.json and the vault where there are none, then the secret under the host's token; then read back, and held to
    be the one printed. What was written. Refused where any of it fails, or the vault does not hold it: its caller then
    deletes the token."""
    name = found.name
    try:
        key, wrote = _sealed_in(home, found, [("token", secret, name)], fleet_id, environ)
        held = (sealed.read(home, key) or {}).get(secrets.entry("token", host=name))
    except WRITERS_REFUSE as error:
        raise Refused(f"the vault could not be written ({str(error).removeprefix('error: ')})") from None
    except OSError as error:
        raise Refused(f"the vault could not be written ({error.strerror or error})") from None
    if held is None or not _same(held, secret):
        raise Refused("the vault does not hold the token's secret as it was written")
    return wrote


def _record(
    home: paths.Home,
    found: _Set,
    address: str,
    token_user: str,
    token_id: str,
    made: Sequence[hosts.Made],
    environ: Mapping[str, str],
) -> list[str]:
    """host.yml's address, the token's user and ID, and what the script made, written at once after it ran, so that a
    run stopped after it leaves made recording each object: through hosts.write, its anchor as it was (the run's own
    writes it after the questions). What was written. Refused where it cannot be written."""
    layout = hosts.Layout(home, environ)
    try:
        state = layout.state(found.name)
        change = _change(address, token_user, token_id, made)
        digest = hosts.write(layout, found.name, state.digest, change=change, anchor=hosts.UNCHANGED)
    except WRITERS_REFUSE as error:
        raise Refused(f"{str(error).removeprefix('error: ')}: what was written before it stays") from None
    except OSError as error:
        raise Refused(f"host.yml could not be written ({error.strerror or error})") from None
    return [f"{hosts.HOSTS_REL}/{found.name}/{hosts.HOST_FILE}"] if digest != state.digest else []


def _but_token(made: Sequence[tuple[str, str, bool]], full_id: str) -> list[tuple[str, str, bool]]:
    """What the script made but the token full_id and its grants: what stays once the token was deleted again."""
    return [named for named in made if named[0] != "token" and not named[1].endswith(f" {full_id}")]


@contextlib.asynccontextmanager
async def _said_if_ended(asking: _Asking, sentence: str, held: Sequence[bool]) -> AsyncIterator[None]:
    """The y's phase, whose end by a second Ctrl-C, the grace's end, SIGHUP or SIGTERM says sentence (the token's
    delete line) where held[0] is still false then: a token the run may have made, its secret held nowhere."""
    import asyncio

    try:
        yield
    except (asyncio.CancelledError, KeyboardInterrupt):
        if not held[0]:
            asking.say(sentence)
        raise


async def _offer(
    asking: _Asking,
    found: _Set,
    typed: Given,
    held: Secret | None,
    *,
    home: paths.Home,
    environ: Mapping[str, str],
    shell: Shell,
    token_user: str,
    token_id: str,
    pool: str,
    fleet_id: str,
) -> tuple[Given, _Offered | None]:
    """The offer (card E7), at a terminal, between the token's ID and its secret, the certificate settled: the access
    script rendered for the host's values (_script), its node read over root SSH where settings.yml names none; where
    no storage or bridge is given or set, the script's --list, and one in effect that the node lists, else the person
    asked which; then the script's --dry-run, with --no-token where the vault holds the token's secret or one is given.
    Where something is missing, the plan (E1's list rendered for people), the commands and what the script said are
    shown, and the person asked "Set it up now? y or n", no first; on y alone, fleet.json is written where there is
    none, with the ID the pool is marked with, then the script is run as root over root SSH, /bin/sh reading it from its
    standard input, in a shield with what follows it (a run ended inside it anyway says the token's delete line where
    the token may be there unheld). Its one line on standard output, the token's secret, held to a UUID and
    sealed in the vault at once (_keep), then host.yml's record of what it made (_record); where the vault cannot be
    written, or no secret was printed, the token the run made (its add said, then a later command, the run's end or
    the script's words that it made it) is deleted again (_rescue), and init stops. The given
    values with what the offer settled: the storage and the bridge asked, and after a y the node, the storage and the
    bridge the access was set up on, and the secret, and the pool and the token another pool was named with (card E8);
    and what the offer did, None where it ran nothing. Before the dry run the pool is read as root sees it, and a pool
    nobody marked, or a fleet's pool with no token's secret held or given, is asked about as the module's docstring
    says (card E8). Root SSH that does not answer, a script that refuses, nothing missing, a token there whose secret
    is held nowhere here in a pool nothing shows is this fleet's, no, Esc and Enter: each leaves the host as it was,
    said, and the secret is then asked for."""
    address = typed.address
    own = {} if found.layers.host is None else found.layers.host.settings
    full_id = f"{token_user}!{token_id}"
    no_token = ["--no-token"] if typed.secret is not None or held is not None else []
    # Whether the token was named, given or by host.yml, which the offer never changes: else a fleet of its own in
    # another pool asks for one (card E8), the engine's own ID there being likely the other fleet's.
    token_named = bool(typed.token_user or typed.token_id) or full_id != OWN_TOKEN
    host_flag = typed.host
    asking.step("access")
    try:
        node = str(own.get("proxmox_node") or "") or await _node(shell, address, host_flag)
        chosen = {
            "storage": typed.storage or str(own.get("proxmox_storage") or ""),
            "bridge": typed.bridge or str(own.get("proxmox_bridge") or ""),
        }

        def render(storage: str, bridge: str) -> tuple[access.Host, str]:
            return _script(
                found,
                fleet_id=fleet_id,
                user=token_user,
                token=token_id,
                node=node,
                storage=storage,
                bridge=bridge,
                pool=pool,
                host_flag=host_flag,
            )

        if not all(chosen.values()):
            in_effect = {what: str(found.values[LISTED[what][0]].value) for what in chosen}
            listed = render(**{what: chosen[what] or in_effect[what] for what in chosen})[1]
            listing = await shell([*SH, "--list"], listed, READ_SECONDS)
            if not (isinstance(listing, runner.Done) and listing.status == 0):
                raise _NotOffered(_unanswered(address, listing, host_flag))
            there: dict[str, list[str]] = {"storage": [], "bridge": []}
            for each in listing.lines:
                what, _, name = each.partition(" ")
                if what in there and proxmox_name(name, what) and name not in there[what]:
                    there[what].append(name)
            for what in chosen:
                if chosen[what]:
                    continue
                if in_effect[what] in there[what]:
                    # The one in effect, and on the node: as today's offer settles it, unasked.
                    chosen[what] = in_effect[what]
                    continue
                if not there[what]:
                    raise _NotOffered(f"{node} lists no {what} root can use, so init makes nothing on {address}")
                picked = await _picked(asking, what, there[what], node)
                if not picked:
                    raise _NotOffered(f"no {what} was chosen, so init makes nothing on {address}")
                chosen[what] = picked
                if what == "storage":
                    typed = dataclasses.replace(typed, storage=picked)
                else:
                    typed = dataclasses.replace(typed, bridge=picked)
        # Card E8: the pool as root reads it decides which question is asked; the script decides again. pending: the
        # dry run found this fleet's own token there, its secret held nowhere here.
        again: list[str] = []
        pending = False
        while True:
            full_id = f"{token_user}!{token_id}"
            extra: list[str] = []
            shown: list[str] = []
            pooled = await _pool_there(shell, pool)
            try:
                found_mark = None if pooled is None else mark.parse(pooled.comment)
            except mark.Refused:
                # One that does not read: the script refuses it, in fleet/mark.py's words.
                pooled = found_mark = None
            own_mark = found_mark == mark.Mark(fleet_id, mark.NONE)
            unheld = not no_token and found_mark is not None and found_mark.controller == mark.NONE
            if pooled is not None and found_mark is None:
                # A pool nobody marked: taken only where it is named, on a y to the question that shows it.
                if not (typed.pool or own.get("proxmox_pool")):
                    sentence = _take_unmarked(pool, address, host_flag)
                    raise _NotOffered(f"{sentence[:1].upper()}{sentence[1:]}. Nothing was made on {address}.")
                extra = ["--pool", pool, "--take-unmarked"]
                shown = _unmarked_lines(pooled, address)
            elif unheld and not again and (pending or not own_mark):
                # A fleet's pool, and no token's secret held or given: which of two things is meant, neither unasked.
                assert found_mark is not None
                answer = await _which(
                    asking,
                    pool=pool,
                    address=address,
                    found=found_mark,
                    full_id=full_id,
                    take=found.fleet is None or found.fleet.fleet == found_mark.fleet,
                    another=not own.get("proxmox_pool"),
                    refused=str(mark.may_change(found_mark, found.fleet or record.first(fleet_id))),
                    own=own_mark,
                )
                if answer == ANOTHER:
                    pool, full_id = await _another(asking, pool, full_id, token_named)
                    token_user, _, token_id = full_id.partition("!")
                    typed = dataclasses.replace(typed, pool=pool, token_user=token_user, token_id=token_id)
                    pending = False
                    continue
                if answer != TAKE:
                    raise _NotOffered(f"Neither was chosen, so nothing was made on {address}.")
                fleet_id, again = found_mark.fleet, ["--regenerate"]
            host, script = render(chosen["storage"], chosen["bridge"])
            dry = await shell([*SH, "--dry-run", *extra, *again, *no_token], script, READ_SECONDS)
            if isinstance(dry, runner.Done) and dry.status != 0:
                # The script refused, in its own words: a pool of another fleet's, a token made without privilege
                # separation or another fleet's, a storage the node does not have, the token not there under
                # --no-token.
                raise _NotOffered(
                    f"the access script refused on {address}, and nothing was changed: {' '.join(_said_lines(dry))}"
                )
            if not isinstance(dry, runner.Done):
                raise _NotOffered(_unanswered(address, dry, host_flag))
            try:
                commands = [shlex.split(each) for each in dry.lines]
            except ValueError:
                raise _NotOffered(_unanswered(address, dry, host_flag)) from None
            sayings = [each for each in _said_lines(dry) if not each.startswith(DRY_RUN_HEADS)]
            if no_token or any(_token_line(each, full_id) for each in commands):
                break
            if unheld and own_mark and not again and not pending:
                pending = True
                continue
            raise _NotOffered(
                "\n".join(
                    [
                        f"The token {full_id} is on {address} already, and its secret is held nowhere here: init makes"
                        " nothing there while it is, since nothing shows it is this fleet's. Type its secret at the"
                        " next question, or delete it as the script says, and run init again:",
                        *(f"  {each}" for each in sayings),
                    ]
                )
            )
        if not commands:
            said_now = [each for each in sayings if not each.startswith(NOTHING_TO_DO)]
            if said_now:
                asking.say("\n".join([f"The API access is set up on {address} already:", *said_now]))
            asking.step("access", "done")
            return typed, None
    except _NotOffered as why:
        asking.say(why.sentence)
        asking.step("access", "skipped")
        return typed, None

    appended = any((named := _object_of(each)) is not None and named[0] == "role" and not named[2] for each in commands)
    lines = [
        said(
            "{init} can set up the API access the engine uses on {address}, as root over SSH.",
            {"init": _init(), "address": address},
        ),
        *shown,
        "What it uses there, each object with its privileges, and what is there already:",
        *_plan(access.objects(host), commands, host.audit_scope),
        "It would run there, adding only what is missing:",
        *(f"  {each}" for each in dry.lines),
        *(["The script says:", *(f"  {each}" for each in sayings)] if sayings else []),
        *(["What is added to a role reaches every user it is granted to, wherever it is granted."] if appended else []),
        "Nothing else on the host changes"
        + ("." if no_token else ", and the token's secret goes straight into the encrypted vault."),
        f"n, or Enter, {_by_hand(address, host_flag).replace('to set it up yourself', 'to do it yourself', 1)}.",
    ]
    answer = await asking.ask(_question(lines, SET_UP, colon=False), NO, _yes_no())
    if answer != YES:
        asking.say(f"Nothing was made on {address}: {_by_hand(address, host_flag)}")
        asking.step("access", "skipped")
        return typed, None

    # The y. fleet.json first, with the ID the script marks the pool with, so that a run cut after it finds the same ID
    # (review 72, 1). Then the script, the vault's write of the secret it printed, made's record and any rescue, as one
    # phase a Ctrl-C lets finish (review 72, 3), nothing run between the script's end and the vault's write.
    early: list[str] = []
    if found.fleet is None:
        try:
            record.write(home, record.first(fleet_id))
        except WRITERS_REFUSE as error:
            raise Refused(f"{str(error).removeprefix('error: ')}: nothing was run on {address}") from None
        except OSError as error:
            wrong = error.strerror or error
            raise Refused(f"fleet.json could not be written ({wrong}): nothing was run on {address}") from None
        early.append(_written(home, record.path(home)))
        found = dataclasses.replace(found, fleet=record.first(fleet_id))
    # Whether no token of the run's own is held nowhere: where the run is ended before it is, the delete line said.
    held = [bool(no_token)]
    delete = shlex.join(["pveum", "user", "token", "delete", token_user, token_id])
    ended = (
        f"The run was ended while the access was set up on {address}: the token {full_id} may be there with its secret"
        f" held nowhere. As root there, {delete} deletes it."
    )
    # Card E8: a token whose secret is made again (token modify) was there before, and is never deleted (review 72's
    # note owed); one a take-over adds (--regenerate, the token gone) is the run's own, deleted as E7 deletes it
    # (review 76, 1). Before the run, the dry run's line is the best known, for the ended sentence alone; the rescues
    # read the run's own line (review 76b, 1b), since the script decides again at the y.
    regenerating = any(_object_of(each) == ("token", full_id, False) for each in commands)
    if regenerating:
        ended = said(
            "The run was ended while the access was set up on {address}: the token {token} may have its secret made"
            " again, held nowhere; the next {init} asks to make it again.",
            {"address": address, "token": full_id, "init": _init()},
        )
    async with asking.running.shield(f"setting up the API access on {address}"), _said_if_ended(asking, ended, held):
        result = await shell([*SH, *extra, *again, *no_token], script, RUN_SECONDS)
        ran = [each[2:] for each in _said_lines(result) if each.startswith("+ ")]
        words = []
        for each in ran:
            try:
                words.append(shlex.split(each))
            except ValueError:
                words.append([])
        finished = isinstance(result, runner.Done) and result.status == 0
        # Of a run that did not finish, the last command said may not have run, or not to its end.
        completed = words if finished else words[:-1]
        made_now = [named for each in completed if (named := _object_of(each)) is not None and named[2]]
        # The token's line said, and whether the run made it (review 72, 2): the dry run found no token of that ID,
        # so one the run made is its own, its secret held nowhere unless the vault takes it. The line is said before
        # the add runs: made where a later command was said after it, the run finished, or the script says it made
        # it; last in a run that ended by itself, none of that said, Proxmox refused the add, and nothing is its own.
        # Card E8: the take-over's line, which makes the token's secret again, is read as the add is.
        said_at = [number for number, each in enumerate(words) if _token_line(each, full_id)]
        token_said = bool(said_at)
        # The script's own refusals alone (review 72b, 7): a `+` line may quote a pool's comment a take keeps.
        told = "\n".join(each for each in _said_lines(result) if each.startswith("error: "))
        later = token_said and said_at[-1] < len(words) - 1
        token_made = token_said and (finished or later or any(part in told for part in TOKEN_MADE))
        # The run's own token line: token modify made the secret again of a token there before (card E8).
        remade = token_said and (_object_of(words[said_at[-1]]) or ("", "", True))[2] is False
        if isinstance(result, runner.Done) and not token_made:
            held[0] = True
        secret: Secret | None = None
        wrote = list(early)
        stop = ""
        if not finished:
            stop = f"The access script did not finish on {address} ({_why(result)})."
            if token_made and isinstance(result, runner.Done):
                # The script ended by itself after it made the token (a later command refused, or no secret
                # printed): the token deleted, as the playbook's rescue deletes it while the vault is unwritten.
                rescued = await _rescue(shell, token_user, token_id, address, remade)
                held[0] = True
                stop += f" {rescued[:1].upper()}{rescued[1:]}."
                made_now = _but_token(made_now, full_id)
            elif token_said and isinstance(result, runner.Done):
                # Proxmox refused the token's add: none of the run's own to delete, and one of that ID is not its.
                stop += f" Proxmox refused to make the token {full_id}: nothing of it was deleted."
            elif token_said:
                # The connection was lost after the token's line: it may be there, its secret unsaid. Never run again.
                stop += f" The token {full_id} may be on {address} with its secret unsaid: as root there, {delete}"
                stop += " deletes it."
            stop += said(" What it made before that stays; the next {init} makes only what is still missing", NEXT)
        elif token_made:
            value = answers.init_answer(result.lines[0], "uuid") if len(result.lines) == 1 else ""
            if not value:
                sentence = await _rescue(shell, token_user, token_id, address, remade)
                held[0] = True
                stop = f"The access script made the token {full_id} on {address} and printed no secret of it:"
                stop += f" {sentence}."
                made_now = _but_token(made_now, full_id)
            else:
                secret = Secret(value)
                try:
                    wrote += _keep(home, found, secret, fleet_id, environ)
                    held[0] = True
                except Refused as refused:
                    sentence = await _rescue(shell, token_user, token_id, address, remade)
                    held[0] = True
                    stop = f"{refused.sentence.removeprefix('error: ')}: {sentence}."
                    made_now = _but_token(made_now, full_id)
                    secret = None
        if ran:
            asking.say("\n".join([f"Ran on {address}, as root:", *(f"+ {each}" for each in ran)]))
        made = tuple(hosts.Made(what, name, BY_ENGINE) for what, name, _ in made_now if _own_name(what, name))
        try:
            wrote += _record(home, found, address, token_user, token_id, made, environ) if made else []
        except Refused as refused:
            if stop:
                stop += f" Its record in made could not be written: {refused.sentence.removeprefix('error: ')}"
            else:
                raise
        if stop:
            raise Refused(stop)
    asking.step("access", "done")
    typed = dataclasses.replace(typed, node=node, storage=chosen["storage"], bridge=chosen["bridge"])
    if secret is not None:
        typed = dataclasses.replace(typed, secret=secret, secret_from="the token's secret the access script printed")
    return typed, _Offered(made, tuple(ran), secret, tuple(wrote), fleet_id)


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
    which is also in NEXT STEPS: said as one report once the stage is done, with the line that asks again for what was
    left. Refused as unattended is, with nothing written."""
    asking = _Asking(running)
    running.emit("stage", None, SETTINGS)
    lines = await _run(home, typed, environ=environ, job=running.job, root_ssh=root_ssh, asking=asking)
    running.emit("stage", None, SETTINGS, outcome="done")
    again = ""
    if asking.left:
        again = said("{init}   asks again for {left}", {"init": _init(), "left": _listed_words(asking.left)})
    report.say(running, None, "init_settings", "\n".join(lines), again)
    return lines


def _listed_words(words: Sequence[str]) -> str:
    return ", ".join(words[:-1]) + f" and {words[-1]}" if len(words) > 1 else "".join(words)


@dataclasses.dataclass(frozen=True)
class _Settled:
    """What a run settled of the host, before anything but the offer's is written: what its certificate is held to,
    the token's user and ID, the fleet's ID, what was made, by ID - by this engine, through the offer, and by the
    administrator where a secret was given or typed (the pool's mark read with it) - what was given and answered, and
    what the offer did, None where it ran nothing."""

    kept: certificate.Kept
    token_user: str
    token_id: str
    fleet_id: str
    made: tuple[hosts.Made, ...]
    typed: Given
    offered: _Offered | None = None


async def _run(
    home: paths.Home,
    typed: Given,
    *,
    environ: Mapping[str, str],
    job: Job | None,
    root_ssh: certificate.RootSsh | None,
    asking: _Asking | None,
    offer_target: runner.Target | None = None,
) -> list[str]:
    """unattended's steps, and attended's with asking: what is set read and every value given held to it, the host's
    part (its address, its token, its certificate, at a terminal the offer, the pool's mark, and at a terminal its
    lists), at a terminal the fleet's own questions, then the writes, each through its one writer, and what the run
    says of them. What the offer wrote at once, the vault and host.yml, is read again before the writes, which then find
    it set. offer_target: where the offer reaches root SSH, the host's own Target where None."""
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
            home=home,
            environ=environ,
            job=job,
            root_ssh=root_ssh,
            asking=asking,
            offer_target=offer_target,
        )
        typed = settled.typed
        if settled.offered is not None:
            found = _read(home, layout, typed, environ)
    elif asking is None:
        raise Refused(
            said(
                "the host {name} has no address yet: give it, as {init}; nothing was written",
                {"name": name, "init": _init(address=PLACEHOLDER, no_prompt=True, **named)},
            )
        )
    else:
        # The host's address left: so is everything that needs the host, which the next run asks.
        for key in ("token", "certificate", "access", "secret", "node", "storage", "bridge"):
            asking.passed(key, "skipped")
    if asking is not None:
        typed = await _the_fleet(asking, typed, found, environ)
        asking.step("write")
    early = settled.offered.wrote if settled is not None and settled.offered is not None else ()
    wrote = [*early, *_write(home, layout, found, typed, settled, environ)]
    if asking is not None:
        asking.step("write", "done")
    return _report(home, found, typed, settled, wrote, asking)


async def _the_host(
    found: _Set,
    typed: Given,
    held: Secret | None,
    *,
    home: paths.Home,
    environ: Mapping[str, str],
    job: Job | None,
    root_ssh: certificate.RootSsh | None,
    asking: _Asking | None,
    offer_target: runner.Target | None = None,
) -> _Settled:
    """The host's part of a run, at typed.address: the token's ID, asked where nothing names one yet; the certificate,
    settled before any request that carries the token, a person asked where only one can vouch for it; at a terminal,
    the offer to set the access up over root SSH (_offer); the token's secret, asked where none is held, given or
    printed by the offer's script; the pool's mark, read with a secret given or typed, which is the authority for the
    fleet's ID, the offer's script having made or checked the mark itself; and at a terminal the node, the storage and
    the bridge, from the host's lists."""
    name, state, fields = found.name, found.state, found.state.fields
    address = typed.address
    token_user = typed.token_user or (fields.token_user if fields is not None else access.USER)
    token_id = typed.token_id or (fields.token_id if fields is not None else access.TOKEN)
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
    pool = typed.pool or own.get("proxmox_pool") or access.POOL
    tls_ca_file = found.values["tls_ca_file"].value or None
    # The fleet's own ID, or a new fleet's: the script is rendered with it (E4's builder: fleet.json's where there is
    # one), and it is written unless the pool's mark, read with a secret given or typed, names the fleet.
    fleet_id = found.fleet.fleet if found.fleet is not None else record.new_id()

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
    offered = None
    if asking is not None:
        asking.step("certificate", "done")
        if job is None:
            asking.passed("access", "skipped")
        else:
            shell = _shell(job, address, fields, found.values, environ, offer_target)
            typed, offered = await _offer(
                asking,
                found,
                typed,
                held,
                home=home,
                environ=environ,
                shell=shell,
                token_user=token_user,
                token_id=token_id,
                pool=pool,
                fleet_id=fleet_id,
            )
            # Card E8: another pool named, with its token, or the fleet taken over, its ID the pool's mark's.
            token_user, token_id = typed.token_user or token_user, typed.token_id or token_id
            pool = typed.pool or pool
            if offered is not None:
                fleet_id = offered.fleet
        # The token's secret: given, held in the vault, printed by the offer's script, or asked, hidden.
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
    made: tuple[hosts.Made, ...] = () if offered is None else offered.made
    printed = offered is not None and offered.secret is not None
    client = None
    unlisted = [setting for setting, *_ in LISTED.values() if own.get(setting) in (None, "")]
    if secret is not None and (typed.secret is not None or (asking is not None and unlisted)):
        client = _connect(url, kept, full_id, secret, found.values["network_proxy"].value, environ)
    try:
        if typed.secret is not None and client is not None and not printed:
            found_mark = await _mark(client, full_id, pool, address, typed.host)
            rule = mark.may_change(found_mark, found.fleet or record.first(found_mark.fleet))
            if rule is not None:
                raise Refused(f"{rule.removeprefix('error: ')}; nothing was written")
            fleet_id = found_mark.fleet
            # What the administrator made by hand, where the offer did not make it now.
            names = {"pool": pool, "user": token_user, "token": full_id}
            by_engine = {(each.what, each.id) for each in made}
            made += tuple(
                hosts.Made(what, name, BY_HAND)
                for what, name in names.items()
                if _own_name(what, name) and (what, name) not in by_engine
            )
        if asking is not None:
            typed = await _the_lists(asking, client, typed, own, address)
    finally:
        if client is not None:
            client.close()
    return _Settled(kept, token_user, token_id, fleet_id, made, typed, offered)


async def _the_lists(
    asking: _Asking, client: Proxmox | None, typed: Given, own: Mapping[str, object], address: str
) -> Given:
    """The node, the storage and the bridge, each asked where the host's own settings.yml sets none and none was given:
    a menu of what the host lists to the token (none without a token's secret, and a storage or a bridge none without a
    node). Each answer is then a value given."""
    import asyncio

    from ..pve.answers import ProxmoxError
    from ..vendors.transport import TransportError

    node = str(own.get("proxmox_node") or typed.node)
    given_now = {"node": typed.node, "storage": typed.storage, "bridge": typed.bridge}
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
    try:
        if settled is not None or seal:
            wrote += _sealed_in(home, found, seal, fleet_id, environ)[1]
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
            pool = typed.pool or own.get("proxmox_pool") or access.POOL
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
    except WRITERS_REFUSE as error:
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
    """What a run says once it has written: the certificate, the fleet, what was written, what the offer made on the
    Proxmox host, and what is left. The fleet's line is said wherever the host was settled with a secret given or typed,
    whose token read the pool's mark (made may record nothing then), or printed by the offer's script, which made or
    found the mark."""
    name = found.name
    lines = [] if settled is None else [settled.kept.said]
    offered = None if settled is None else settled.offered
    if offered is not None and offered.secret is not None:
        lines.append(f"the fleet {settled.fleet_id}, as the pool's mark names it, which the access script put there")
    elif settled is not None and settled.typed.secret is not None:
        lines.append(f"the fleet {settled.fleet_id}, as the pool's mark names it, which the token given reads")
    lines.append(
        f"wrote {', '.join(wrote)}" if wrote else "nothing to write: the fleet's folder holds every value given already"
    )
    if asking is None:
        lines.append("nothing was made on the Proxmox host: with nobody there init makes nothing on it")
    elif offered is not None and offered.ran:
        lines.append(
            f"the access script ran {len(offered.ran)} command{'s' if len(offered.ran) > 1 else ''} on the Proxmox"
            f" host after your y, each said above: what it made of the engine's own names is recorded in"
            f" {hosts.HOSTS_REL}/{name}/{hosts.HOST_FILE}'s made"
        )
    else:
        lines.append("nothing was made on the Proxmox host")
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


# The one sentence init --print-commands says on standard error, beside the script on standard output.
PRINTED = (
    "The access script for {address}, rendered for its values: run it there as root, {print_commands} | ssh"
    " root@{address} sh -s -- --dry-run to see each command it would run, then the same without --dry-run, which"
    " prints the token's secret last, for {secret_file} to read from a file of your own"
)


# What --print-commands says of its one write (card E8).
WROTE_ID = (
    "Wrote {file}: the fleet {fleet}, whose ID this script marks the pool with, so that the next print and {init}"
    " render the same ID."
)


def print_commands(run: Any, values: Mapping[str, object]) -> int:
    """init --print-commands (card E7; V2-9): the access script (engine/access.py's as_script), rendered for this host's
    values as the offer renders it (_script), on standard output, and on standard error the one sentence that says how
    to run it on the host as root (PRINTED). The fleet's files are read - host.yml, the settings, fleet.json - never the
    vault; no run, job or lock is made, and no request is sent. Its one write (card E8, the coordinator's ruling on
    review 72's Later medium): with no fleet.json, the fleet's ID it renders is written there, through record.py's
    writer, and said (WROTE_ID), so that a second print and the offer render the same ID and a by-hand run again is
    never refused as another fleet's; no other file is written, the script's above all. A value given that differs
    from the one set is refused, as init refuses it. The exit status."""
    environ = dict(os.environ)
    try:
        if _text(values, "token_secret_file"):
            raise Refused("--print-commands prints the script, which makes the token: it takes no --token-secret-file")
        typed = given(values, getattr(run, "set", ()), cwd=os.getcwd(), environ=environ)
        home = paths.home(PROGRAM_ROOT, environ)
        _held(home)
        layout = hosts.Layout(home, environ)
        name = _host_name(layout, typed.host)
        try:
            state = layout.state(name)
            layers = overrides.read(home, name, environ=environ)
            fleet = record.read(home)
            run_now = _run_values(typed.settings, [], environ)
            resolved = overrides.resolve(home, name, run_now, environ=environ)
        except (hosts.Refused, overrides.Refused, paths.Refused, record.Refused) as error:
            raise Refused(str(error)) from None
        found = _Set(name, state, layers, fleet, resolved, run_now, None, None)
        differs = _differs(typed, found)
        if differs:
            never = said("{init} never writes over a value: edit the file to change it.", {"init": _init()})
            raise Refused(" ".join([*differs, never]))
        fields = state.fields
        fleet_id = fleet.fleet if fleet is not None else record.new_id()
        _host, text = _script(
            found,
            fleet_id=fleet_id,
            user=typed.token_user or (fields.token_user if fields is not None else access.USER),
            token=typed.token_id or (fields.token_id if fields is not None else access.TOKEN),
            node=str(resolved["proxmox_node"].value),
            storage=typed.storage or str(resolved["proxmox_storage"].value),
            bridge=typed.bridge or str(resolved["proxmox_bridge"].value),
            pool=typed.pool or str(resolved["proxmox_pool"].value or access.POOL),
            host_flag=typed.host,
        )
        wrote = ""
        if fleet is None:
            try:
                record.write(home, record.first(fleet_id))
            except WRITERS_REFUSE as error:
                raise Refused(f"{str(error).removeprefix('error: ')}: nothing was printed") from None
            except OSError as error:
                wrong = error.strerror or error
                raise Refused(f"fleet.json could not be written ({wrong}): nothing was printed") from None
            wrote = said(WROTE_ID, {"file": _written(home, record.path(home)), "fleet": fleet_id, "init": _init()})
    except Refused as refused:
        return _refused(refused.sentence)
    except paths.Refused as error:
        return _refused(f"error: {error}")
    address = typed.address or (fields.address if fields is not None else "<host>")
    sys.stdout.write(text)
    sys.stdout.flush()
    sentence = said(
        PRINTED,
        {
            "address": address,
            "print_commands": Line("init", {"print_commands": True}, host_flag=typed.host),
            "secret_file": Line("init", {"token_secret_file": PLACEHOLDER}, host_flag=typed.host),
        },
    )
    print(sentence, file=sys.stderr)
    if wrote:
        print(wrote, file=sys.stderr)
    return EXITS["done"]


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
    above the region as one report (attended's own), and a refusal said as the stage's failure, with nothing written;
    then the checks (card E6), their lines one report of matter init_check, its NEXT STEPS line what is left of them.
    The exit status: the checks' where attended finished."""
    from claude_vm.runs import live

    counted = verbosity if isinstance(verbosity, int) and not isinstance(verbosity, bool) else 0
    live.Live(running.job, INIT, sys.stdout, verbosity=counted, cancelled=running.cancelled)
    try:
        await attended(home, typed, running=running, environ=environ)
    except Refused as refused:
        running.emit("stage", None, SETTINGS, outcome="failed", why=refused.sentence.removeprefix("error: "))
        return EXITS["refused"]
    # At a terminal the person's answers are init's writing rule: the checks settle nothing they left for later.
    checked, flag = await init_checks.check(home, typed.host, environ=environ, job=running.job, write=False)
    report.say(running, None, "init_check", "\n".join(each.line for each in checked.checks), checked.next_line(flag))
    return checked.exit


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
    if values.get("print_commands") is True and values.get("check") is not True:
        return print_commands(run, values)
    checking = values.get("check") is True
    given_too = [field for field in CHECK_REFUSES if checking and values.get(field) not in (None, False, "", ())]
    if given_too:
        flags = ", ".join(f"--{field.replace('_', '-')}" for field in given_too)
        sentence = "error: {line} runs the checks alone and writes nothing: it takes no {flags}"
        return _refused(said(sentence, {"line": _init(check=True), "flags": flags}))
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
        if asks and not checking:
            return await _asked(running, home, typed, environ, getattr(run, "verbosity", 0))
        lines = []
        if not checking:
            try:
                lines = await unattended(home, typed, environ=environ, job=running.job)
            except Refused as refused:
                return _refused(refused.sentence)
        checked, flag = await init_checks.check(home, typed.host, environ=environ, job=running.job, write=not checking)
        for each in [*lines, *checked.lines(flag)]:
            print(each)
        return checked.exit

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
