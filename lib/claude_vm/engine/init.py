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
   never writes over a value.
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
   administrator made by hand from the printed commands: the pool, the user and the token (ruling B1, rule 5).

The names written where none is given are the engine's own (card E1; ruling B1, rule 1): the user `agent-warehouse@pve`,
the token `aw` and the pool `aw-fleet`; and the first host is `pve`, as today's. The modes built by other cards are
refused by name until they land: `--check` (E6), `--print-commands` (E7) and `--machines` (J14); and so are the
questions at a terminal (E5), where `--no-prompt` is not given. Every sentence that names a command names it through
the printer. Standard library, fleet/, pve/, ssh/, vendors/, schema/, runs/ and today's init.py and checks.py at import;
asyncio inside the functions that wait.
"""

from __future__ import annotations

import dataclasses
import os
import stat
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import init as answers
from ..checks import proxmox_name
from ..fleet import files as fleet_files
from ..fleet import hosts, mark, overrides, paths, record, sealed, secrets, vaultkey
from ..hosts import model
from ..schema.printer import line, said
from ..schema.words import EXITS, PLACEHOLDER, Line
from ..secret import Secret
from ..ssh import runner
from ..text import one_line, plain_text
from . import certificate

if TYPE_CHECKING:
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
# Who made what init records, where the secret was given: the administrator, by hand, from the printed commands.
BY_HAND = "administrator"
# The modes of init built by other cards, refused by name until each lands.
NOT_YET = {"check": "E6", "print_commands": "E7", "machines": "J14"}
# The settings each host sets for itself that init writes where given, in the order a sentence names them.
HOST_OWN = ("proxmox_storage", "proxmox_bridge", "proxmox_pool")


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
    gave it (secret_from: --token-secret-file and the file, or the variable), None where none was."""

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
        with open(path, "rb") as stream:
            data = stream.read(SECRET_MOST + 1)
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
    if pool and not (proxmox_name(pool, "pool") and pool[0].isalpha()):
        # A / makes a nested pool on VE 9.2, and Proxmox makes none whose name starts with anything but a letter.
        raise Refused(f"--pool ({pool}) is no name of a pool: letters, digits, _, . and -, from a letter")
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
    """Whether the secret given is the one the vault holds: the one place init reads a Secret's value, to compare it
    with another, never to send it on or say it (tests/unit/test_aw_guards.py's REVEALS)."""
    return held.reveal() == secret.reveal()


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
    """The read of the host's certificate files over root SSH, as a child of job's run: the host's account and port
    (host.yml's ssh_user and ssh_port), this computer's key for it and its ssh (client.yml's ssh_identity_file and
    ssh_executable). A target the runner refuses is root SSH that did not answer."""

    # The ssh program where client.yml names one; the runner's own default where it names none.
    named = values["ssh_executable"].value
    program = dict(ssh=named) if named else {}

    async def read() -> certificate.Read:
        try:
            target = runner.proxmox_host(
                address,
                fields.ssh_user if fields is not None else "root",
                paths.control_dir(environ),
                port=fields.ssh_port if fields is not None else 22,
                identity_file=values["ssh_identity_file"].value or None,
                **program,
            )
            return await certificate.read_over_root_ssh(job, target, environ)
        except (runner.Refused, paths.Refused) as error:
            raise certificate.NoRootSsh(one_line(str(error), 300)) from None

    return read


async def _mark(
    url: str,
    kept: certificate.Kept,
    token_id: str,
    secret: Secret,
    pool: str,
    proxy_setting: str,
    environ: Mapping[str, str],
) -> mark.Mark:
    """The mark in the pool's comment, read with the token given, through the connection the certificate's step made:
    Refused where Proxmox does not take the token for the pool, the pool is not there, or its comment carries no mark,
    or one that does not read."""
    import asyncio

    from ..pve.answers import ProxmoxError
    from ..pve.client import Denied, Proxmox, Token
    from ..vendors import proxy
    from ..vendors.transport import TransportError

    try:
        through = proxy.choose(proxy_setting, environ, url)
    except ValueError as error:
        raise Refused(str(error)) from None
    client = Proxmox(url, kept.pinned, Token(token_id, secret), through)
    try:
        found = await asyncio.to_thread(client.pool, pool)
    except Denied as error:
        raise Refused(
            f"{error}: {token_id} with the secret given does not read the pool {pool}, so nothing shows the fleet it"
            " belongs to, and nothing was written. Check the token's ID, its secret and its grant on the pool"
        ) from None
    except (ProxmoxError, TransportError) as error:
        raise Refused(f"{one_line(str(error), 300)}: nothing was written") from None
    finally:
        client.close()
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
    layout = hosts.Layout(home, environ)
    found = _read(home, layout, typed, environ)
    # Again under the fleet's lock, and for a caller that is not init's handler: before anything is sent or written.
    _held(home)
    name, state, fields = found.name, found.state, found.state.fields
    entry = secrets.entry("token", host=name)
    held = None if found.vault is None else found.vault.get(entry)
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
    named = {"host": name} if typed.host else {}
    if not address:
        raise Refused(
            said(
                "the host {name} has no address yet: give it, as {init}; nothing was written",
                {"name": name, "init": _init(address=PLACEHOLDER, no_prompt=True, **named)},
            )
        )
    token_user = typed.token_user or (fields.token_user if fields is not None else TOKEN_USER)
    token_id = typed.token_id or (fields.token_id if fields is not None else TOKEN_ID)
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
    kept = await certificate.work_out(host, typed.certificate, reader, None)
    if isinstance(kept, certificate.Refusal):
        raise Refused(kept.said)

    # The fleet's ID: the pool's mark, read with the secret given, which is the authority; else the fleet's own.
    made: list[hosts.Made] = []
    if typed.secret is not None:
        full_id = f"{token_user}!{token_id}"
        url = f"https://{host.endpoint}"
        found_mark = await _mark(url, kept, full_id, typed.secret, pool, found.values["network_proxy"].value, environ)
        rule = mark.may_change(found_mark, found.fleet or record.first(found_mark.fleet))
        if rule is not None:
            raise Refused(f"{rule.removeprefix('error: ')}; nothing was written")
        fleet_id = found_mark.fleet
        made = [
            hosts.Made("pool", pool, BY_HAND),
            hosts.Made("user", token_user, BY_HAND),
            hosts.Made("token", full_id, BY_HAND),
        ]
    else:
        fleet_id = found.fleet.fleet if found.fleet is not None else record.new_id()

    # The writes, each through its one writer.
    wrote: list[str] = []
    key_file = found.values["vault_key_file"].value
    try:
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
        if typed.secret is not None and held is None:
            sealed.put(home, key, "token", typed.secret, host=name)
            wrote.append(f"the token's secret, sealed in the vault as {entry}")
        if kept.anchor is None:
            anchor: Any = None if (fields is not None and fields.trust_kind is not None) else hosts.UNCHANGED
        else:
            anchor = kept.anchor if kept.changed else hosts.UNCHANGED
        digest = hosts.write(
            layout, name, state.digest, change=_change(address, token_user, token_id, made), anchor=anchor
        )
        if digest != state.digest:
            wrote.append(f"{hosts.HOSTS_REL}/{name}/{hosts.HOST_FILE}")
        digest = found.layers.host.digest if found.layers.host is not None else overrides.MISSING
        before = digest
        for setting, value in zip(HOST_OWN, (typed.storage, typed.bridge, pool), strict=True):
            if value and own.get(setting) in (None, ""):
                digest = overrides.set(home, setting, value, name, digest, environ=environ)
        if digest != before:
            wrote.append(f"{hosts.HOSTS_REL}/{name}/{overrides.SETTINGS}")
        shared = found.layers.fleet.settings
        digest = before = found.layers.fleet.digest
        if typed.github_user and shared.get("github_username") in (None, ""):
            digest = overrides.set(home, "github_username", typed.github_user, None, digest, environ=environ)
        if typed.tailscale and shared.get("vpn") in (None, ""):
            vpn = "tailscale" if typed.tailscale == "on" else "none"
            digest = overrides.set(home, "vpn", vpn, None, digest, environ=environ)
        if digest != before:
            wrote.append(f"{paths.FLEET}/{overrides.SETTINGS}")
        if not found.layers.client.own.get("current_host"):
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
    return _report(home, name, kept, fleet_id, made, wrote, typed, own)


def _report(
    home: paths.Home,
    name: str,
    kept: certificate.Kept,
    fleet_id: str,
    made: Sequence[hosts.Made],
    wrote: Sequence[str],
    typed: Given,
    own: Mapping[str, object],
) -> list[str]:
    """What a run says once it has written: the certificate, the fleet, what was written, and what is left."""
    lines = [kept.said]
    if made:
        lines.append(f"the fleet {fleet_id}, as the pool's mark names it, which the token given reads")
    lines.append(
        f"wrote {', '.join(wrote)}" if wrote else "nothing to write: the fleet's folder holds every value given already"
    )
    lines.append("nothing was made on the Proxmox host: with nobody there init makes nothing on it")
    unset = [
        setting
        for setting, value in (
            ("proxmox_node", ""),
            ("proxmox_storage", typed.storage),
            ("proxmox_bridge", typed.bridge),
        )
        if not value and own.get(setting) in (None, "")
    ]
    if unset:
        lines.append(
            f"{', '.join(unset)} {'is' if len(unset) == 1 else 'are'} set for the host by init's checks on it (card E6,"
            f" not built yet): until then write {'it' if len(unset) == 1 else 'them'} in"
            f" {hosts.HOSTS_REL}/{name}/{overrides.SETTINGS}"
        )
    return [*lines, *typed.notes]


# ---------------------------------------------------------------- the verb ----


def _refused(sentence: str) -> int:
    print(sentence, file=sys.stderr)
    return EXITS["refused"]


def _at_a_terminal() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def init(run: Any, **values: object) -> int:
    """init's handler (words.py's row): the token's secret taken out of the environment first, the modes not built yet
    refused by name, the values held to their kinds, a home that is a link refused, and the fleet's folder written with
    nobody there, as its run, under the fleet's lock. The exit status."""
    # First of all: nothing started from here on holds the token's secret in its environment.
    variable = taken()
    for field, card in NOT_YET.items():
        if values.get(field) is True:
            mode = {"line": _init(**{field: True}), "card": card}
            return _refused(said("error: {line} is not built yet (card {card} builds it)", mode))
    if values.get("no_prompt") is not True and _at_a_terminal():
        return _refused(
            said(
                "error: {init} at a terminal asks its questions, which are not built yet (card E5 builds them): give"
                " {unattended} and the values it needs",
                {"init": _init(), "unattended": _init(no_prompt=True)},
            )
        )
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
