"""init with nobody there (lib/claude_vm/engine/init.py; docs/architecture/engine.md §16.4).

`aw init --no-prompt`, run as a person runs it (aw_kit.run_aw), against the fake Proxmox (harness.fake_fleet, with the
engine's pool, role, user and token, its requests held to the engine's table) serving a node's certificate of
tests/unit/pki.py's, with the fleet's home a folder of the test's own and a refusing ssh first on PATH
(fleet_fixture.refusing_ssh): every value given writes the folder, and the same again changes nothing; a value given
that differs from the one set fails, naming both, and writes nothing; the secret from a file and from the variable, and
never from the command line; a word after the options refused unsaid (H5's rule, end to end); the pool's mark's fleet ID
adopted, and a mark naming a control VM, another fleet, or none refused; no request carries the token before the
certificate is settled, and none changes anything on Proxmox. The home is short (short_home), so that root SSH, which a
run tries wherever the computer's certificates do not verify the host, starts its child, the refusing ssh, with the
host's shared socket, as from a person's home (from the kit's home, in pytest's long directory, it starts with none:
review 28, E4.md 2, and B27). A recording ssh in its place holds that the child's environment has neither
AGENT_WAREHOUSE_TOKEN_SECRET nor the secret, which init takes out of its own at its start (E4.md 1); and a home that is
a link is refused with nothing written through it, the same command finishing once the folder itself is there (E4.md 3).
The host's own certificates read over root SSH with nobody there are kept (the owner's answer of 2026-11-23 to review
20) by the same step run in this process, its root SSH a reader of the test's own, as
tests/unit/test_aw_engine_certificate.py has one: a run through tools/aw reaches the host's own target only once the
stand-in machine answers a host's first use (card F31). The invariant with a test of its own: after any run, no file
under the test's home holds the fake secret in clear, and the vault on disk is sealed. And after every run, host.yml's
made names only the engine's own default names (card E19, the owner's answer of 2026-11-23; and the roles and their
grants the offer makes, card E7), a pool, user or token of the person's own never among them. And
tests/unit/fleet_fixture.py, which gives other tests a whole fleet folder in one call.

At a terminal (card E5), `aw init` run at a pseudo-terminal of its own (aw_kit.aw_at_a_terminal, through
harness._at_a_pseudo_terminal), its answers typed as each question appears: every answer typed writes the files - the
certificate trusted at its yes or no, the token's secret hidden, the node, the storage and the bridge each picked from
the fake's lists with what the token lacks shown, the SSH key from ~/.ssh where id_ed25519.pub is not there, the GitHub
user and the VPN - and the same again asks nothing; an answer outside its kind is asked once more saying the rule, and
a second left, said; Enter (Esc at a menu) leaves each question for the next run, which NEXT STEPS says; a menu picked
with the arrow keys writes what the same choice typed does where no menu can be drawn; the VPN a menu, none first,
answered by its letter, never wrapped, erased once answered and said on one line, with no frame of the run's between two
questions; Tailscale's auth key asked only once Tailscale is chosen, given or set, and not where the vault holds one or
the web login is the only way, sealed in the vault and never on the screen, in the run's record or log, or in clear in
a file; the key given on the command line refused unsaid; a --tailscale the vpn setting contradicts refused naming
both before any question; one Ctrl-C at a question ending the run with nothing written; and with standard input from
/dev/null nothing asked. The questions are drawn live (the run's frames, its summary of the steps). A typed address is
asked first, then the token's ID, and an address nothing answers at writes nothing: the API's port is 8006 to an
address typed, where a test's fake never listens. The invariants with tests of their own: no code of the engine or the
fleet names /dev/tty or getpass, and every question init asks goes through runs/ask.py's ask.

The offer (card E7), run in this process at a terminal: a person of the test's own answers each question (_Person,
through _Asking's own steps), and the run's job reaches a stand-in Proxmox host of tests/unit/aw_machine.py's as root
through B3's runner (offer_target, as root_ssh stands in for the certificate's read: a run through tools/aw reaches
host.yml's address, where no stand-in machine is), the stand-in pveum, pvesh, hostname and id of
tests/molecule/fake_pve_cli.py in its bin. A y makes every object of E1's list, the vault holds the secret the script
printed, made records each object by this engine, and the same again asks nothing; n, Enter or Esc leave the host byte
for byte as it was, and the secret is then asked; the dry run's commands are in the question, nothing changed on the
host before the y; the plan says what the audit scope lets the token read; a bridge the node lists in place of the one
in effect is asked; a vault that cannot be written, and a secret printed that is no UUID (refused by the host's script,
or by the computer's own check), each have the token the run made deleted again; with the secret held the script runs
with --no-token and makes only what is missing; a token there whose secret is held nowhere is never offered (until card
E8); with nobody there the host is never reached; and a token of the person's own records the pool and the roles
alone. The invariants with tests of their own: the secret is in no question, no line said, no command line, no log of
the host's and no file in clear; nothing runs on Proxmox without a y, but the delete of the token the run made. And `aw
init --print-commands` through tools/aw prints the script the offer runs and writes nothing, and that script, run by
hand with --dry-run on a host with nothing yet, gives a command for each object of the list.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import contextlib
import dataclasses
import errno
import json
import os
import re
import select
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from aw_kit import aw_at_a_terminal, run_aw, searched_for
from aw_machine import Machine, filed, machine, ssh_stand_in, target, unrouted
from fleet_fixture import mark_pool, refusing_ssh, whole_fleet, with_port
from harness import (
    ENGINE_POOL,
    ENGINE_ROLE,
    ENGINE_TOKEN,
    ENGINE_USER,
    REPO,
    _executable,
    _in_raw_mode,
    _json_lines,
    _pve,
    _pve_cli,
    _pve_cli_calls,
    _pve_cli_writes,
    _pve_requests,
    engine_grant,
    engine_token,
    fake_fleet,
)
from pki import proxmox_pki
from test_aw_access_script import _found
from vt import VT

from claude_vm import route, trust
from claude_vm.engine import access, certificate
from claude_vm.engine import init as engine_init
from claude_vm.fleet import hosts, mark, overrides, paths, record, registry, sealed, vaultkey
from claude_vm.hosts.model import Request
from claude_vm.pve import pin
from claude_vm.pve import script as pve_script
from claude_vm.pve.client import Proxmox, Token
from claude_vm.runs import job as jobs
from claude_vm.schema import rules
from claude_vm.secret import Secret
from claude_vm.ssh import runner
from claude_vm.tui import choose

INIT = REPO / "lib" / "claude_vm" / "engine" / "init.py"
# The host's two files, as a sentence names them.
HOST_FILE = "fleet/hosts/pve/host.yml"
HOST_SETTINGS = "fleet/hosts/pve/settings.yml"
# Every value a first run is given beside the secret and the certificate.
EVERY = ("--storage", "local-lvm", "--bridge", "vmbr0", "--github-user", "octocat", "--tailscale", "off")
# A secret the fake did not make: low in entropy, so that gitleaks takes it for no key.
OTHER = "00000000-0000-4000-8000-000000000000"
# What init may record as made: the engine's own default names, by what each is, and nothing for a name the person
# gave (card E19, the owner's answer of 2026-11-23 on review 28's E4.md 4); and of what the offer made (card E7, review
# 55), a role of E1's three (engine/access.py's), and a grant of one of them to the engine's user or its token, on a
# path of Proxmox's form, which may name a pool, a storage, a bridge or a node the person gave.
DEFAULTS = {"pool": ENGINE_POOL, "user": ENGINE_USER, "token": f"{ENGINE_USER}!{ENGINE_TOKEN}"}
# A pool, a user and a token of the person's own, whose names are not the engine's.
OWN_POOL, OWN_USER, OWN_TOKEN = "dev-vms", "ops@pve", "tools"
# A recording ssh, for a run's PATH: each call's words and whole environment, a JSON line of calls.jsonl beside it; then
# refused as the refusing ssh refuses an address no machine has.
RECORDER = r"""
import json, os, sys
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls.jsonl"), "a") as out:
    out.write(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ)}) + "\n")
sys.stderr.write("ssh: connect to host port 22: No route to host\n")
sys.exit(255)
"""


@dataclasses.dataclass(frozen=True)
class Fake:
    """The fake Proxmox at base, its state in directory, its port, the certificates it serves, the engine's token's ID
    and secret, and the folder of a refusing ssh for a run's PATH."""

    base: str
    directory: Path
    port: int
    pki: dict
    token: str
    secret: str
    ssh: Path

    def calls(self) -> list[dict]:
        """Every request the fake was sent, in order."""
        return _pve_requests(self.directory)


@pytest.fixture
def pki(tmp_path):
    return proxmox_pki(tmp_path / "pki", key_usage=True)


@pytest.fixture
def short_home(monkeypatch) -> Iterator[Path]:
    """The fleet's home of this file's runs, and their HOME: a folder of the test's own under /tmp, short enough that
    the host's shared socket under it ($HOME/.ansible/cp/awh-<12 hex>, the runner's) fits route.SOCKET_MOST, so that
    root SSH starts its child with that socket, as from a person's home, and not with none, as from a home too long for
    it. AGENT_WAREHOUSE_HOME names it in this process too, so that the test's own reads and
    writes of the fleet are of the same folder. Removed with what is beside it once the test ends."""
    directory = Path(tempfile.mkdtemp(prefix="aw-", dir="/tmp"))
    home = directory / "h"
    home.mkdir()
    assert route.socket_fits(str(paths.control_dir({"HOME": str(home)}) / f"awh-{'0' * 12}")), home
    monkeypatch.setenv(paths.VARIABLE, str(home))
    yield home
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def fake(short_home, tmp_path, pki):
    directory = tmp_path / "fake"
    directory.mkdir()
    for base in fake_fleet(directory, engine=True, certificate=(pki["cert"], pki["key"]), enforce=True):
        token, secret = engine_token(directory)
        yield Fake(base, directory, int(base.rsplit(":", 1)[1]), pki, token, secret, refusing_ssh(short_home))


@pytest.fixture
def secret_file(fake, tmp_path) -> Path:
    """The token's secret in a file of the test's own, outside the fleet's home, readable by its owner alone."""
    path = tmp_path / "token-secret"
    path.write_text(fake.secret + "\n")
    os.chmod(path, 0o600)
    return path


def _home() -> paths.Home:
    return paths.home(REPO)


# The line the dispatch says first where the fleet's home is the one AGENT_WAREHOUSE_HOME names, as in every test.
DRIVES = "aw drives the fleet in "


def _init(fake: Fake, *words: str, env: dict | None = None, ssh: Path | None = None):
    """aw init run as a person runs it, with nobody there, from the short home, the fake's refusing ssh first on PATH
    (or ssh, a folder of the test's own): its standard error without the dispatch's line that names the fleet's
    home."""
    ran = run_aw("init", *words, home=_home().root, env=env, first_on_path=(ssh or fake.ssh,))
    ran.stderr = "".join(line for line in ran.stderr.splitlines(keepends=True) if not line.startswith(DRIVES))
    _only_the_engine_s_names()
    return ran


def _the_engine_s_own(each: hosts.Made) -> bool:
    """Whether an entry of made names one of the engine's own default names, by what it is (DEFAULTS): a role of E1's
    three, a grant whose role is one of them, for the engine's user or its token, on a path of Proxmox's form, else the
    pool, the user or the token of DEFAULTS."""
    if each.what == "role":
        return each.id in access.ROLES
    if each.what == "grant":
        words = each.id.split(" ")
        return (
            len(words) == 3
            and access.ACL_PATH.fullmatch(words[0]) is not None
            and words[1] in access.ROLES
            and words[2] in (DEFAULTS["user"], DEFAULTS["token"])
        )
    return DEFAULTS.get(each.what) == each.id


def _only_the_engine_s_names() -> None:
    """After every run of this file (_init, _unattended, _asked, _offered and the runs of their own): each entry of
    every host's made is one of the engine's own default names, by what it is (_the_engine_s_own), where the host has a
    host.yml."""
    layout = hosts.Layout(_home())
    for name in layout.folders():
        if (layout.hosts_dir / name / hosts.HOST_FILE).exists():
            made = layout.fields(name).made
            assert all(_the_engine_s_own(each) for each in made), made


def _sealed(secret: str) -> None:
    """The invariant: no file under the fleet's home holds the secret in clear, and the vault there is sealed."""
    where = _home()
    if where.root.exists() and any(where.root.rglob("*")):
        assert searched_for([secret], where.root) == []
    vault = sealed.path(where)
    if vault.exists():
        assert vault.read_bytes().startswith(sealed.ARMOR)


def _files() -> dict[str, bytes]:
    """Every file of the fleet's home but its state folder, by its path there: what a run changed, byte for byte."""
    where = _home()
    return {
        str(path.relative_to(where.root)): path.read_bytes()
        for path in sorted(where.root.rglob("*"))
        if path.is_file() and paths.STATE not in path.relative_to(where.root).parts
    }


def _given(fake: Fake, *words: str, secret: Path | None = None) -> list[str]:
    """init's options for a first run against the fake: the secret's file, the certificate's fingerprint, and words."""
    given = ["--no-prompt", "--certificate", trust.pem_fingerprint(fake.pki["node"]), *words]
    return [*given, "--token-secret-file", str(secret)] if secret is not None else given


def _first(fake: Fake, secret: Path, *words: str) -> str:
    """The pool marked with a fleet ID, as the administrator's commands leave it, host.yml given the fake's port, and a
    first run of init with words: the fleet's ID."""
    fleet = record.new_id()
    mark_pool(fake.base, fleet)
    with_port(fake.port)
    ran = _init(fake, *_given(fake, *words, secret=secret))
    assert ran.returncode == 0, ran
    _sealed(fake.secret)
    return fleet


def _reached() -> hosts.Reached:
    """The host as a run reaches it, lenient as settings and vault are: its node is init's checks' to set (card E6)."""
    return hosts.reach(hosts.Layout(_home()), Request(lenient=True))


def _untouched() -> bool:
    """Whether the fleet's home is as the test made it, empty: no fleet, no state folder, no job."""
    return not any(_home().root.iterdir())


def _opened() -> dict[str, Secret]:
    where = _home()
    key = vaultkey.open(where, key_file="", key_command=())
    found = sealed.read(where, key)
    assert found is not None
    return found


def _revealed(value: Secret) -> str:
    # The test reads the vault's value to compare it with the fake's: the engine's modules never do but REVEALS's.
    return value.reveal()


# ---------------------------------------------------------------- every value given writes the folder ----


def test_every_value_given_writes_the_folder_and_the_same_again_changes_nothing(fake, secret_file) -> None:
    words = EVERY
    before = len(fake.calls())
    fleet = _first(fake, secret_file, *words)
    where = _home()
    # The key, made where there was none, the account's alone; fleet.json, the mark's fleet ID; the vault, sealed to the
    # key, the secret in it under the host's token.
    key = vaultkey.place(where, "")
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert record.read(where) == record.first(fleet)
    assert {entry: _revealed(value) for entry, value in _opened().items()} == {"token@pve": fake.secret}
    # host.yml, with the node's certificate as its anchor held to its fingerprint, and what the administrator made by
    # hand: given no name of the person's own, the engine's three default names (card E19).
    fingerprint = trust.pem_fingerprint(fake.pki["node"])
    reached = _reached()
    assert (reached.host.name, reached.fields.address, reached.fields.api_port) == ("pve", "127.0.0.1", fake.port)
    assert (reached.fields.token_user, reached.fields.token_id) == (ENGINE_USER, ENGINE_TOKEN)
    assert (reached.fields.trust_kind, reached.fields.fingerprint) == ("node", fingerprint)
    assert trust.pem_fingerprint(reached.anchor) == fingerprint
    assert reached.fields.made == (
        hosts.Made("pool", ENGINE_POOL, "administrator"),
        hosts.Made("user", ENGINE_USER, "administrator"),
        hosts.Made("token", f"{ENGINE_USER}!{ENGINE_TOKEN}", "administrator"),
    )
    # The host's overrides, the fleet's and client.yml's current host.
    layers = overrides.read(where, "pve")
    assert dict(layers.host.settings) == {
        "proxmox_storage": "local-lvm",
        "proxmox_bridge": "vmbr0",
        "proxmox_pool": ENGINE_POOL,
    }
    assert dict(layers.fleet.settings) == {"github_username": "octocat", "vpn": "none"}
    assert layers.client.own.get("current_host") == "pve"
    # Nothing was made on Proxmox: the run's requests that carried the token were reads, after the probes, none of
    # which carried it.
    # (Every request of the test's own, the pool's mark set, carries the fake's token: those that carry none are the
    # probes'.)
    sent = fake.calls()[before:]
    client = [at for at, entry in enumerate(sent) if entry["agent"] == pin.USER_AGENT]
    probes = [at for at, entry in enumerate(sent) if not entry["token"]]
    assert client and probes and max(probes) < min(client), sent
    assert [(sent[at]["method"], sent[at]["path"], sent[at]["token"]) for at in client] == [
        ("GET", "/api2/json/version", ENGINE_TOKEN),
        ("GET", "/api2/json/pools", ENGINE_TOKEN),
    ]
    # The same again changes nothing, and says so.
    files = _files()
    again = _init(fake, *_given(fake, *words, secret=secret_file))
    assert again.returncode == 0, again
    assert "nothing to write" in again.stdout
    assert _files() == files
    _sealed(fake.secret)


def test_the_engine_s_names_are_written_where_none_is_given_and_a_given_one_is_kept(fake, secret_file) -> None:
    # The fake's names are the engine's own (card E1; ruling B1, rule 1): init writes them where none is given.
    assert (access.USER, access.TOKEN, access.POOL) == (ENGINE_USER, ENGINE_TOKEN, ENGINE_POOL)
    _first(fake, secret_file)
    assert overrides.read(_home(), "pve").host.settings["proxmox_pool"] == ENGINE_POOL
    # The node, the storage and the bridge given none are said to be the checks' to set.
    ran = _init(fake, *_given(fake))
    assert "proxmox_node, proxmox_storage, proxmox_bridge are set for the host by init's checks" in ran.stdout


@pytest.mark.parametrize(
    ("words", "said"),
    [
        (("--address", "127.0.0.2"), f"--address 127.0.0.2, but {HOST_FILE} already sets 127.0.0.1."),
        (("--token-user", "other@pve"), f"--token-user other@pve, but {HOST_FILE} already sets {ENGINE_USER}."),
        (("--token-id", "other"), f"--token-id other, but {HOST_FILE} already sets aw."),
        (("--storage", "ceph"), f"--storage ceph, but {HOST_SETTINGS} already sets proxmox_storage: local-lvm."),
        (("--bridge", "vmbr1"), f"--bridge vmbr1, but {HOST_SETTINGS} already sets proxmox_bridge: vmbr0."),
        (("--pool", "dev-vms"), f"--pool dev-vms, but {HOST_SETTINGS} already sets proxmox_pool: aw-fleet."),
        (("--github-user", "other"), "--github-user other, but fleet/settings.yml already sets octocat."),
        (("--tailscale", "on"), "--tailscale on, but fleet/settings.yml already sets vpn: none."),
    ],
    ids=lambda value: value[0] if isinstance(value, tuple) else None,
)
def test_a_value_given_that_differs_from_the_one_set_fails_naming_both_and_writes_nothing(
    fake, secret_file, words, said
) -> None:
    _first(fake, secret_file, *EVERY)
    files, sent = _files(), len(fake.calls())
    ran = _init(fake, *_given(fake, *words, secret=secret_file))
    assert (ran.returncode, ran.stdout) == (1, ""), ran
    assert ran.stderr == f"error: {said} aw init never writes over a value: edit the file to change it.\n"
    assert _files() == files and len(fake.calls()) == sent
    _sealed(fake.secret)


@pytest.mark.parametrize("pool", ["aw-fleet ", "1fleet", "fleet/aw"], ids=["a space", "from a digit", "nested"])
def test_a_pool_that_is_no_pool_s_name_is_refused_where_it_is_written_and_where_the_host_s_file_is_read(
    fake, secret_file, pool
) -> None:
    # Review 55, 2, and the coordinator's word for the landing: proxmox_pool's row holds its value to the settings
    # table's one rule of a pool's name, which --pool is held to (access.POOL_NAME), so the writer refuses one Proxmox
    # makes no pool by, writing nothing; and one a person puts in the host's settings.yml by hand is refused where the
    # file is read, in one sentence naming the file and its line, never the value and never a traceback, before anything
    # is asked or sent, with nothing written.
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    held = f"proxmox_pool takes {registry.PROXMOX_POOL.said}"
    with pytest.raises(overrides.Refused, match=re.escape(f"{held}: nothing was written")):
        overrides.set(_home(), "proxmox_pool", pool, "pve", overrides.MISSING)
    settings = _home().root / HOST_SETTINGS
    assert not settings.exists()
    settings.write_text(f"proxmox_pool: {json.dumps(pool)}\n")
    files, sent = _files(), len(fake.calls())
    ran = _init(fake, *_given(fake, secret=secret_file))
    assert (ran.returncode, ran.stdout) == (1, ""), ran
    assert ran.stderr.startswith("error: ") and ran.stderr.count("\n") == 1, ran
    assert ran.stderr.endswith(f"{HOST_SETTINGS} line 1: {held}: the value there is not one\n"), ran
    assert json.dumps(pool) not in ran.stderr, ran
    assert _files() == files and len(fake.calls()) == sent


def test_a_pool_given_that_is_no_pool_s_name_is_refused_by_the_same_rule(fake, secret_file) -> None:
    # --pool's own pattern takes a name from a digit, which Proxmox makes no pool by: held to the same rule.
    ran = _init(fake, *_given(fake, "--pool", "1fleet", secret=secret_file))
    assert (ran.returncode, ran.stdout) == (1, ""), ran
    assert ran.stderr == f"error: --pool (1fleet) is no name of a pool: {access.POOL_RULE}\n", ran
    assert _untouched()


def test_a_secret_given_that_differs_from_the_vault_s_fails_unsaid_and_writes_nothing(fake, secret_file, tmp_path):
    _first(fake, secret_file)
    other = tmp_path / "other-secret"
    other.write_text(OTHER)
    files = _files()
    ran = _init(fake, *_given(fake, secret=other))
    assert (ran.returncode, ran.stdout) == (1, ""), ran
    assert ran.stderr == (
        f"error: the token's secret in {other} (--token-secret-file) differs from the token's secret the vault already"
        " holds for pve: aw init never writes over a value; change it with aw vault token --host pve\n"
    )
    assert OTHER not in ran.stderr and fake.secret not in ran.stderr and _files() == files
    _sealed(fake.secret)


# ---------------------------------------------------------------- the secret, given one way and never said ----


def test_the_secret_from_the_variable_writes_the_folder_as_the_file_does(fake) -> None:
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    ran = _init(fake, *_given(fake), env={engine_init.SECRET_VARIABLE: fake.secret.upper()})
    assert ran.returncode == 0, ran
    # Held to its kind as today's init holds it: lower case.
    assert {entry: _revealed(value) for entry, value in _opened().items()} == {"token@pve": fake.secret}
    assert fake.secret not in ran.stdout + ran.stderr
    _sealed(fake.secret)


def _recording_ssh(folder: Path) -> Path:
    """A folder of the test's own whose ssh is RECORDER, for first_on_path."""
    folder.mkdir()
    (folder / "ssh.py").write_text(RECORDER)
    _executable(folder / "ssh", f'#!/bin/sh\nexec "{sys.executable}" -I "{folder / "ssh.py"}" "$@"\n')
    return folder


def test_the_secret_from_the_variable_is_in_no_child_s_environment_root_ssh_s_among_them(fake) -> None:
    # Review 28 (E4.md 1): init takes AGENT_WAREHOUSE_TOKEN_SECRET out of its own environment at its very start, so the
    # child root SSH starts - with no certificate given, and with --certificate, which root SSH is tried before - holds
    # neither the variable nor the secret, in clear, upper case or base64, under any name.
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    recorder = _recording_ssh(_home().root.parent / "recording")
    env = {engine_init.SECRET_VARIABLE: fake.secret}
    refused = _init(fake, "--no-prompt", env=env, ssh=recorder)
    assert refused.returncode == 1 and "root SSH to 127.0.0.1 did not answer" in refused.stderr, refused
    done = _init(fake, *_given(fake), env=env, ssh=recorder)
    assert done.returncode == 0, done
    assert {entry: _revealed(value) for entry, value in _opened().items()} == {"token@pve": fake.secret}
    # The child started, once a run: root SSH's read of the host's certificate files, as root.
    calls = _json_lines(recorder / "calls.jsonl")
    assert len(calls) == 2, calls
    for call in calls:
        argv = call["argv"]
        assert argv[argv.index("-l") + 1] == "root" and certificate.HOST_FILES["ca"] in argv[-1], argv
    forms = (fake.secret, fake.secret.upper(), base64.b64encode(fake.secret.encode()).decode())
    held = [
        (at, name)
        for at, call in enumerate(calls)
        for name, value in call["env"].items()
        if name == engine_init.SECRET_VARIABLE or any(form in value for form in forms)
    ]
    assert held == [], held
    _sealed(fake.secret)


def test_the_secret_given_two_ways_or_not_as_a_secret_is_refused_unsaid_and_nothing_is_made(
    fake, secret_file, tmp_path
) -> None:
    both = _init(fake, *_given(fake, secret=secret_file), env={engine_init.SECRET_VARIABLE: fake.secret})
    assert both.returncode == 1 and both.stderr == (
        "error: the token's secret was given both by --token-secret-file and by AGENT_WAREHOUSE_TOKEN_SECRET: give it"
        " one way\n"
    )
    bad = tmp_path / "bad-secret"
    bad.write_text("not-a-secret-of-the-fake-made-for-a-test\n")
    ran = _init(fake, *_given(fake, secret=bad))
    assert ran.returncode == 1 and ran.stderr == (
        "error: --token-secret-file is not the 36-character secret pveum printed, like"
        " 01234567-89ab-cdef-0123-456789abcdef\n"
    )
    variable = _init(fake, *_given(fake), env={engine_init.SECRET_VARIABLE: "not-a-secret"})
    assert variable.returncode == 1 and "not-a-secret" not in variable.stderr
    folder = _init(fake, *_given(fake, secret=tmp_path))
    not_a_file = f"error: --token-secret-file names {tmp_path}, which is not a file\n"
    assert (folder.returncode, folder.stderr) == (1, not_a_file)
    # None of them made a job, a folder or a file: each was refused before the run.
    assert _untouched()


def test_a_secret_file_others_can_read_is_said(fake, secret_file) -> None:
    os.chmod(secret_file, 0o644)
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    ran = _init(fake, *_given(fake, secret=secret_file))
    assert ran.returncode == 0, ran
    assert f"note: {secret_file} can be read by others (mode 0644): chmod 600 it" in ran.stdout


@pytest.mark.parametrize("extra", ["word", OTHER], ids=["a word", "a secret"])
def test_a_word_after_the_options_is_refused_unsaid_and_nothing_is_made(fake, extra) -> None:
    # H5's rule, end to end: a word after init's options - a secret typed there above all - is never said back, and the
    # run makes nothing: no fleet, no state folder, no job.
    ran = _init(fake, "--no-prompt", "--address", "127.0.0.1", extra)
    assert (ran.returncode, ran.stdout) == (1, ""), ran
    assert ran.stderr == rules.RULES["init.1"].check({"extra": extra}) + "\n"
    assert extra not in ran.stderr
    assert _untouched()


# ---------------------------------------------------------------- the pool's mark ----


def test_the_mark_s_fleet_id_is_adopted_and_kept(fake, secret_file) -> None:
    fleet = _first(fake, secret_file)
    assert record.read(_home()).fleet == fleet
    # A run with no secret given keeps the fleet's ID, and reads no pool.
    sent = len(fake.calls())
    ran = _init(fake, *_given(fake))
    assert ran.returncode == 0, ran
    assert record.read(_home()).fleet == fleet
    assert not any(entry["token"] for entry in fake.calls()[sent:])


@pytest.mark.parametrize(
    ("comment", "said"),
    [
        (
            "agent-warehouse fleet={fleet} controller=6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b",
            "error: this fleet has a control VM (6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b): change it there, or retire it;"
            " nothing was written",
        ),
        (
            "a pool of the person's own",
            # Card E8: both ways it is taken are said, the by-hand road in the script's own form.
            "error: the pool aw-fleet on 127.0.0.1 carries no fleet's mark in its comment, and init takes a pool nobody"
            " marked only where it is named and a y says so: at a terminal, ",
        ),
        (
            "agent-warehouse fleet=not-an-id controller=none",
            "error: the pool's comment holds a mark that does not read as one",
        ),
    ],
    ids=["a control VM", "no mark", "unreadable"],
)
def test_a_pool_whose_mark_does_not_make_the_fleet_this_one_is_refused_and_nothing_written(
    fake, secret_file, comment, said
) -> None:
    _pve(fake.base, "PUT", "pools", poolid=ENGINE_POOL, comment=comment.format(fleet=record.new_id()))
    with_port(fake.port)
    files = _files()
    ran = _init(fake, *_given(fake, secret=secret_file))
    assert (ran.returncode, ran.stdout) == (1, ""), ran
    assert ran.stderr.startswith(said), ran.stderr
    if "nobody marked" in said:
        assert "--print-commands --pool aw-fleet | ssh root@127.0.0.1 sh -s -- --pool aw-fleet --take-unmarked" in (
            " ".join(ran.stderr.split())
        ), ran.stderr
        assert "init --pool aw-fleet shows its comment and its members and asks" in ran.stderr, ran.stderr
    assert _files() == files
    _sealed(fake.secret)


def test_a_mark_of_another_fleet_than_fleet_json_s_is_refused(fake, secret_file) -> None:
    fleet = _first(fake, secret_file)
    other = record.new_id()
    mark_pool(fake.base, other)
    files = _files()
    ran = _init(fake, *_given(fake, secret=secret_file))
    assert ran.returncode == 1 and ran.stderr.startswith(
        f"error: the pool's mark names another fleet, {other}, where this computer's folder is the fleet {fleet}'s"
    ), ran
    assert _files() == files


@pytest.mark.parametrize("which", ["secret", "token", "pool"])
def test_a_token_proxmox_does_not_take_for_the_pool_writes_nothing(fake, secret_file, tmp_path, which) -> None:
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    words = _given(fake, secret=secret_file)
    if which == "secret":
        other = tmp_path / "other-secret"
        other.write_text(OTHER)
        words = _given(fake, secret=other)
    elif which == "token":
        words = [*words, "--token-id", "nobody"]
        layout = hosts.Layout(_home())
        hosts.write(layout, "pve", layout.state("pve").digest, change=lambda raw: raw.update(token_id="nobody"))
    else:
        words = [*words, "--pool", "elsewhere"]
    files = _files()
    ran = _init(fake, *words)
    assert ran.returncode == 1 and ran.stdout == "", ran
    assert "nothing was written" in ran.stderr and fake.secret not in ran.stderr
    assert _files() == files
    _sealed(fake.secret)


# ---------------------------------------------------------------- what made records ----


@pytest.mark.parametrize(
    ("pool", "user", "token_id", "recorded"),
    [
        (OWN_POOL, ENGINE_USER, ENGINE_TOKEN, ("user", "token")),
        (ENGINE_POOL, OWN_USER, OWN_TOKEN, ("pool",)),
        (ENGINE_POOL, ENGINE_USER, OWN_TOKEN, ("pool", "user")),
        (OWN_POOL, OWN_USER, OWN_TOKEN, ()),
        # Review 55, E19.md 1: the person's own user with the token ID aw, whose full ID is not the engine's token; and
        # a default name in another case, which to Proxmox is another object.
        (ENGINE_POOL, OWN_USER, ENGINE_TOKEN, ("pool",)),
        ("AW-FLEET", ENGINE_USER, ENGINE_TOKEN, ("user", "token")),
    ],
    ids=["own-pool", "own-user-and-token", "own-token-id", "all-own", "own-user-token-aw", "pool-in-capitals"],
)
@pytest.mark.parametrize("source", ["given", "set"])
def test_made_records_only_the_engine_s_default_names_and_the_report_still_names_the_fleet(
    fake, tmp_path, pool, user, token_id, recorded, source
) -> None:
    # Card E19, the owner's answer of 2026-11-23 (review 28, E4.md 4): where the secret given reads the pool's mark,
    # made records the pool, the user and the token each only where its name is the engine's own default. A name the
    # person gave, or set in host.yml or the host's settings.yml, may be an object of theirs (a token made for other
    # tools, a pool they already had), which a cleanup by made would remove. Each object of the person's is made in
    # the fake as an administrator makes it, the engine's role granted the token and its user on the pool, and the
    # pool marked. source: the names given on init's command line too, or set in the host's files alone.
    full_id = f"{user}!{token_id}"
    if pool != ENGINE_POOL:
        _pve(fake.base, "POST", "pools", poolid=pool)
    if user != ENGINE_USER:
        _pve(fake.base, "POST", "access/users", userid=user)
    secret = fake.secret
    if full_id != fake.token:
        secret = _pve(fake.base, "POST", f"access/users/{user}/token/{token_id}", privsep=1)["data"]["value"]
    _pve(fake.base, "PUT", "access/acl", path=f"/pool/{pool}", roles=ENGINE_ROLE, users=user, tokens=full_id)
    fleet = record.new_id()
    mark_pool(fake.base, fleet, pool)
    # host.yml as a person writes it before init, with the API's port and the token's user and ID; the pool given on
    # the command line with them, or set in the host's settings.yml with no name given.
    layout = hosts.Layout(_home())
    fields = {"address": "127.0.0.1", "api_port": fake.port, "token_user": user, "token_id": token_id}
    hosts.write(layout, "pve", layout.state("pve").digest, change=lambda raw: raw.update(fields))
    secret_file = tmp_path / "own-secret"
    secret_file.write_text(secret + "\n")
    os.chmod(secret_file, 0o600)
    words = _given(fake, "--pool", pool, "--token-user", user, "--token-id", token_id, secret=secret_file)
    if source == "set":
        overrides.set(_home(), "proxmox_pool", pool, "pve", overrides.read(_home(), "pve").host.digest)
        words = _given(fake, secret=secret_file)
    named = f"the fleet {fleet}, as the pool's mark names it, which the token given reads"
    ran = _init(fake, *words)
    assert ran.returncode == 0, ran
    assert named in ran.stdout.splitlines(), ran.stdout
    assert record.read(_home()).fleet == fleet
    reached = _reached()
    assert reached.fields.made == tuple(hosts.Made(what, DEFAULTS[what], "administrator") for what in recorded)
    assert (reached.fields.token_user, reached.fields.token_id) == (user, token_id)
    assert overrides.read(_home(), "pve").host.settings["proxmox_pool"] == pool
    # The same again changes nothing, and names the fleet as before.
    files = _files()
    again = _init(fake, *words)
    assert again.returncode == 0, again
    assert named in again.stdout.splitlines() and "nothing to write" in again.stdout, again.stdout
    assert _files() == files
    _sealed(secret)


# ---------------------------------------------------------------- the certificate, settled first ----


def test_no_request_carries_the_token_before_the_certificate_is_settled(fake, secret_file) -> None:
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    sent = len(fake.calls())
    # A fingerprint that is not what the host presents, with nobody to ask and root SSH not answering: refused before
    # anything that carries the token.
    ran = _init(fake, "--no-prompt", "--certificate", ":".join(["AB"] * 32), "--token-secret-file", str(secret_file))
    assert ran.returncode == 1 and "nothing was kept, and nothing sent with the token" in ran.stderr, ran
    assert not any(entry["token"] for entry in fake.calls()[sent:])
    # None given: only a person or root SSH could vouch for it, and neither did.
    ran = _init(fake, "--no-prompt", "--token-secret-file", str(secret_file))
    assert ran.returncode == 1, ran
    assert "root SSH to 127.0.0.1 did not answer" in ran.stderr and "--certificate" in ran.stderr
    # Root SSH's child started, and was refused: the refusing ssh answered.
    assert unrouted(fake.ssh), ran.stderr
    assert not any(entry["token"] for entry in fake.calls()[sent:])
    # host.yml and the route to the host's VMs, which with_port wrote before init, as every fleet a test makes pins it.
    assert sorted(_files()) == ["client.yml", "fleet/hosts/pve/host.yml"]
    _sealed(fake.secret)


def test_a_host_the_computer_s_certificates_verify_keeps_no_anchor_and_reaches_no_ssh(fake, secret_file) -> None:
    where = _home()
    ca = where.root.parent / "ca.pem"
    ca.write_text(fake.pki["ca"])
    overrides.set(where, "tls_ca_file", str(ca), None, overrides.MISSING)
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    ran = run_aw("init", "--no-prompt", "--token-secret-file", str(secret_file), home=where.root)
    assert ran.returncode == 0, ran
    _only_the_engine_s_names()
    reached = _reached()
    assert reached.anchor is None and reached.fields.trust_kind is None


def _unattended(given: engine_init.Given, root_ssh=None) -> list[str]:
    """init with nobody there, run in this process, its root SSH root_ssh."""
    try:
        return asyncio.run(engine_init.unattended(_home(), given, environ=dict(os.environ), root_ssh=root_ssh))
    finally:
        _only_the_engine_s_names()


def _reader(ca: str = "", node: str = ""):
    """Root SSH that reads the host's CA and node certificates; called counts its calls."""

    async def read() -> certificate.Read:
        read.called += 1  # type: ignore[attr-defined]
        return certificate.Read(ca=ca, node=node)

    read.called = 0  # type: ignore[attr-defined]
    return read


def test_with_nobody_there_the_host_s_certificates_read_over_root_ssh_are_kept(fake) -> None:
    # The owner's answer of 2026-11-23 to review 20: root SSH may vouch for the certificate with nobody there.
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    read = _reader(fake.pki["ca"], fake.pki["node"])
    variable = engine_init.taken({engine_init.SECRET_VARIABLE: fake.secret})
    given = engine_init.given({}, cwd=str(REPO), environ={}, variable=variable)
    lines = _unattended(given, read)
    assert read.called == 1
    assert lines[0].startswith("Proxmox's certificate verifies against its CA")
    reached = _reached()
    assert reached.anchor == fake.pki["ca"] and (reached.fields.trust_kind, reached.fields.fingerprint) == ("ca", "")
    # The same again reads nothing over root SSH: what checking uses now verifies.
    assert _unattended(given, read)[1] == "the fleet " + record.read(_home()).fleet + (
        ", as the pool's mark names it, which the token given reads"
    )
    assert read.called == 1


def test_a_host_whose_anchor_a_hand_took_out_is_mended_by_init(fake, secret_file) -> None:
    # Review 20 (E3.md 5): the reader's refusal names init as the way back, and init writes the host again.
    _first(fake, secret_file)
    (hosts.Layout(_home()).hosts_dir / "pve" / hosts.ANCHOR).unlink()
    with pytest.raises(hosts.Refused, match="aw init --host pve keeps the host's anchor again"):
        _reached()
    ran = _init(fake, *_given(fake, "--host", "pve"))
    assert ran.returncode == 0, ran
    assert trust.pem_fingerprint(_reached().anchor) == trust.pem_fingerprint(fake.pki["node"])


# ---------------------------------------------------------------- what init refuses before it runs ----


@pytest.mark.parametrize(
    ("words", "said"),
    [
        (("--check",), "error: aw init --check is not built yet (card E6 builds it)"),
        (("--machines",), "error: aw init --machines is not built yet (card J14 builds it)"),
    ],
    ids=["check", "machines"],
)
def test_a_mode_another_card_builds_is_refused_by_name_and_nothing_is_made(fake, words, said) -> None:
    ran = _init(fake, "--no-prompt", *words)
    assert (ran.returncode, ran.stdout, ran.stderr) == (1, "", said + "\n")
    assert _untouched()


def test_a_further_host_is_card_e10_s(fake, secret_file) -> None:
    _first(fake, secret_file)
    files = _files()
    ran = _init(fake, "--no-prompt", "--host", "nuc", "--address", "127.0.0.1")
    assert ran.returncode == 1 and ran.stderr == (
        "error: nuc is no host of the fleet's (pve): a further host is set up by aw init --host nuc --address"
        " <address>, not built yet (card E10 builds it)\n"
    ), ran
    assert _files() == files


def test_no_vault_is_made_under_the_key_the_variable_names(fake, secret_file, tmp_path) -> None:
    # The owner's answer to review 23: a new vault is sealed to the fleet's own key file alone.
    named = tmp_path / "elsewhere-key"
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    files = _files()
    ran = _init(fake, *_given(fake, secret=secret_file), env={vaultkey.VARIABLE: str(named)})
    assert ran.returncode == 1 and ran.stderr.startswith(
        f"error: {vaultkey.VARIABLE} names {named}, and the fleet has no vault yet"
    ), ran
    assert _files() == files and not named.exists()


def test_a_fleet_with_a_vault_and_no_key_here_makes_no_key(fake, secret_file) -> None:
    _first(fake, secret_file)
    vaultkey.place(_home(), "").unlink()
    files = _files()
    ran = _init(fake, *_given(fake, secret=secret_file))
    assert ran.returncode == 1 and ran.stderr.startswith("error: nothing here opens"), ran
    assert _files() == files


# ---------------------------------------------------------------- a home that is a link ----


def _linked() -> tuple[Path, Path]:
    """The short home moved aside and a link to it put in its place: the link and the folder it names."""
    link = _home().root
    target = link.parent / "elsewhere"
    link.rename(target)
    link.symlink_to(target)
    return link, target


def _everything(folder: Path) -> dict[str, bytes | None]:
    """Every entry under folder, a file by its bytes and a folder as None: what a run wrote, folders and state
    included."""
    return {str(path.relative_to(folder)): path.read_bytes() if path.is_file() else None for path in folder.rglob("*")}


def _link_said(link: Path) -> str:
    return (
        f"error: {link} is a link, where a folder of the fleet's goes: nothing is written through it; put the folder"
        " itself there in its place\n"
    )


def test_a_home_that_is_a_link_is_refused_with_nothing_written_and_the_same_command_then_finishes(
    fake, secret_file
) -> None:
    # Review 28 (E4.md 3): refused before the run's job, its lock, the key, fleet.json or the vault is written
    # through it.
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    link, target = _linked()
    before, sent = _everything(target), len(fake.calls())
    words = _given(fake, secret=secret_file)
    ran = _init(fake, *words)
    assert (ran.returncode, ran.stdout, ran.stderr) == (1, "", _link_said(link)), ran
    assert _everything(target) == before and len(fake.calls()) == sent
    # The folder itself put in its place: the same command finishes.
    link.unlink()
    target.rename(link)
    again = _init(fake, *words)
    assert again.returncode == 0, again
    assert {entry: _revealed(value) for entry, value in _opened().items()} == {"token@pve": fake.secret}
    _sealed(fake.secret)


def test_init_s_run_refuses_a_home_that_is_a_link_before_anything_is_sent_or_written(fake, secret_file) -> None:
    # The run's own hold, for a caller that is not init's handler, and under the fleet's lock: its fleet/ a link too.
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    fingerprint = trust.pem_fingerprint(fake.pki["node"])
    given = engine_init.given(
        {"token_secret_file": str(secret_file), "certificate": fingerprint}, cwd=str(REPO), environ={}
    )
    link, target = _linked()
    before, sent = _everything(target), len(fake.calls())
    with pytest.raises(engine_init.Refused) as refused:
        _unattended(given)
    assert refused.value.sentence + "\n" == _link_said(link)
    assert _everything(target) == before and len(fake.calls()) == sent
    link.unlink()
    target.rename(link)
    fleet = paths.fleet_dir(_home())
    fleet.rename(link / "fleet-elsewhere")
    fleet.symlink_to(link / "fleet-elsewhere")
    before = _everything(link / "fleet-elsewhere")
    with pytest.raises(engine_init.Refused) as refused:
        _unattended(given)
    assert refused.value.sentence + "\n" == _link_said(fleet)
    assert _everything(link / "fleet-elsewhere") == before and len(fake.calls()) == sent


# ---------------------------------------------------------------- a run stopped part way ----


def test_a_run_stopped_after_any_write_goes_on_from_what_it_wrote(fake, secret_file) -> None:
    fleet = record.new_id()
    mark_pool(fake.base, fleet)
    with_port(fake.port)
    where = _home()
    # Stopped after the key, and again after fleet.json: each run finds what exists and goes on.
    vaultkey.make_key(vaultkey.place(where, ""))
    key = vaultkey.place(where, "").read_bytes()
    given = engine_init.given(
        {"token_secret_file": str(secret_file), "certificate": trust.pem_fingerprint(fake.pki["node"])},
        cwd=str(REPO),
        environ={},
    )
    record.write(where, record.first(fleet))
    _unattended(given)
    assert vaultkey.place(where, "").read_bytes() == key
    assert {entry: _revealed(value) for entry, value in _opened().items()} == {"token@pve": fake.secret}
    _sealed(fake.secret)


# ---------------------------------------------------------------- the fleet fixture ----


def test_the_fleet_fixture_gives_a_whole_fleet_folder_in_one_call(fake) -> None:
    made = whole_fleet(_home().root, fake.base, fake.directory, fake.pki)
    _only_the_engine_s_names()
    reached = _reached()
    assert (reached.host.name, reached.host.problem) == (made.host, "")
    assert record.read(_home()).fleet == made.fleet
    secret = _opened()["token@pve"]
    # The client, pinned as the host keeps it, reads the pool with the vault's secret.
    pinned = pin.kept(reached.anchor, reached.fields.fingerprint)
    with Proxmox(f"https://127.0.0.1:{made.port}", pinned, Token(made.token, secret)) as client:
        assert mark.parse(client.pool(ENGINE_POOL).comment) == mark.Mark(made.fleet, mark.NONE)
    _sealed(made.secret)


# ---------------------------------------------------------------- at a terminal: the questions (card E5) ----

# What the person sees of each question as runs/ask.py asks it: a line's prompt, a menu's last line of words above it
# (raw marks a menu: its keys are typed once the terminal is in its raw mode), and a choice typed where no menu can be
# drawn.
HOST_ASKED = b"Proxmox host address: "
SECRET_ASKED = b"API token secret: "
GITHUB_ASKED = b"GitHub username: "
KEY_ASKED = b"Tailscale auth key: "
TRUST_ASKED = b"Trust it?\r\n"
VPN_ASKED = b"\r\nVPN\r\n"
SSH_KEY_ASKED = b"\r\nSSH key\r\n"
TYPED_ASKED = b"A number or a name"
UP, DOWN, ENTER, ESC = b"\x1b[A", b"\x1b[B", b"\r", b"\x1b"
# Tailscale's auth key, as a person pastes one, and two public keys of their own that are not the default one: each low
# in entropy, so that gitleaks takes none for a key.
AUTH_KEY = "tskey-auth-kInit0CNTRL-TestOnly000000000000000"
PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyDoNotUse+/= dev@laptop"
OTHER_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOtherKeyDoNotUse+/= dev@elsewhere"
# A run's header, as its live region draws it (in bold: its words alone).
DRAWN = b"agent-warehouse"

Plan = list[tuple[bytes, list[bytes], bool]]


def _asked(fake: Fake, plan: Plan, *words: str, term: str = "xterm-256color") -> tuple[int, bytes]:
    """aw init at a pseudo-terminal of its own (harness._at_a_pseudo_terminal, through the kit), as a person runs it
    from the short home against the fake, the refusing ssh first on PATH: each step of plan typed in turn - the bytes
    awaited after the last step's, the keys typed then, one by one, and whether the terminal is first in its raw mode,
    as a menu puts it. Its status and every byte it wrote, once every step of plan was taken."""
    left = list(plan)
    at = [0]

    def act(chunk: bytes, output: bytes, master: int, process) -> None:
        if not left:
            return
        wanted, keys, raw = left[0]
        found = output.find(wanted, at[0])
        if found < 0 or (raw and not _in_raw_mode(master)):
            return
        left.pop(0)
        at[0] = found + len(wanted)
        for key in keys:
            os.write(master, key)
            time.sleep(0.15)

    try:
        rc, raw = aw_at_a_terminal(
            "init",
            *words,
            home=_home().root,
            env={"TERM": term},
            first_on_path=(fake.ssh,),
            size=(100, 30),
            act=act,
            timeout=60,
            poll=0.05,
        )
    finally:
        _only_the_engine_s_names()
    assert not left, f"no {left[0][0]!r} appeared:\n" + raw.decode(errors="replace")
    return rc, raw


def _screen(raw: bytes) -> VT:
    terminal = VT(100, 30)
    terminal.feed(raw)
    return terminal


def _read(raw: bytes) -> str:
    """What a person read at the terminal, each run of white space one space: a line said above the region is wrapped
    to its width, and a failure's indented."""
    return " ".join("\n".join(_screen(raw).transcript()).split())


def _grants(fake: Fake) -> None:
    """The engine's token granted what its table needs on the fake's node, its storage and its bridge, as init's grants
    give it (card E1), each its own role: each listed to the token, and one it can use. And on the fake's second node,
    pve2, which is offline: so that it is not one to use for being offline alone (review 54, E5.md 3)."""
    from claude_vm.pve import privileges

    listed = (
        ("node", "/nodes/pve"),
        ("node", "/nodes/pve2"),
        ("storage", "/storage/local-lvm"),
        ("bridge", "/sdn/zones/localnetwork/vmbr0"),
    )
    for kind, path in listed:
        engine_grant(fake.base, path, ",".join(sorted(privileges.needed(kind))))


def _public_key(name: str, line: str) -> None:
    """A public key in the person's ~/.ssh, which is the short home's in these runs."""
    folder = _home().root / ".ssh"
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(line + "\n")


def _no_secret_said(raw: bytes, tmp_path: Path, *secrets: str) -> None:
    """None of secrets is on the screen, in the fleet's home (the job's record and text log among it) or in clear in a
    file there, and the vault there is sealed."""
    screen = tmp_path / "screen.out"
    screen.write_bytes(raw)
    assert searched_for(list(secrets), screen, _home().root) == []


def test_every_answer_typed_writes_the_files_and_the_same_again_asks_nothing(fake, tmp_path) -> None:
    # host.yml given the fake's port, as a person with a port of their own writes it: the address and the token's ID are
    # set, so neither is asked (the last test of this part asks them).
    fleet = record.new_id()
    mark_pool(fake.base, fleet)
    with_port(fake.port)
    _grants(fake)
    # A second storage the token may see but not allocate on: listed, with what the token lacks there, and not offered.
    _pve(fake.base, "PUT", "fake/storages", node="pve", storages="local-lvm:lvmthin,ceph:rbd")
    engine_grant(fake.base, "/storage/ceph", "Datastore.Audit")
    _public_key("laptop.pub", PUBLIC_KEY)
    plan: Plan = [
        # Nothing on the host vouches for its certificate: the person does, up to yes (no is marked first, the safe
        # answer).
        (TRUST_ASKED, [UP, ENTER], True),
        (SECRET_ASKED, [fake.secret.encode() + b"\n"], False),
        # The node, the storage and the bridge the host lists, each the only one the token can use: a yes or no, yes
        # marked first, which Enter takes.
        (b"Use pve?\r\n", [ENTER], True),
        (b"Use local-lvm?\r\n", [ENTER], True),
        (b"Use vmbr0?\r\n", [ENTER], True),
        # No ~/.ssh/id_ed25519.pub: which other key the VMs get.
        (SSH_KEY_ASKED, [ENTER], True),
        (GITHUB_ASKED, [b"octocat\n"], False),
        (VPN_ASKED, [ENTER], True),
    ]
    rc, raw = _asked(fake, plan)
    said = raw.decode(errors="replace")
    assert rc == 0, said
    where = _home()
    # Each question under today's block, in the report's order, the host's first.
    order = [said.index(asked.decode()) for asked, _, _ in plan]
    assert order == sorted(order) and said.count(engine_init.ASKED) == len(plan), said
    # The rows above each say what cannot be used and why: the second node is offline, though the token holds there all
    # its table asks, and the token lacks a privilege of its table on the second storage; neither is an answer of its
    # question, whose only one is asked about as a yes or no.
    assert re.search(r"-  pve2 +offline", said), said
    assert re.search(r"-  ceph +rbd +the token lacks Datastore.AllocateSpace on /storage/ceph", said), said
    assert record.read(where) == record.first(fleet)
    assert {entry: _revealed(value) for entry, value in _opened().items()} == {"token@pve": fake.secret}
    reached = _reached()
    assert (reached.fields.trust_kind, reached.fields.fingerprint) == ("node", trust.pem_fingerprint(fake.pki["node"]))
    layers = overrides.read(where, "pve")
    assert dict(layers.host.settings) == {
        "proxmox_storage": "local-lvm",
        "proxmox_bridge": "vmbr0",
        "proxmox_pool": ENGINE_POOL,
        "proxmox_node": "pve",
    }
    assert dict(layers.fleet.settings) == {
        "github_username": "octocat",
        "vpn": "none",
        "vm_ssh_public_keys": [PUBLIC_KEY],
    }
    assert layers.client.own.get("current_host") == "pve"
    # A whole host, as a run reaches it.
    assert hosts.reach(hosts.Layout(where), Request()).host.problem == ""
    # None chose Tailscale: its auth key was not asked.
    assert KEY_ASKED not in raw
    # The hidden answer reached neither the screen nor any file of the fleet's home but sealed in the vault.
    _no_secret_said(raw, tmp_path, fake.secret)
    _sealed(fake.secret)
    # Drawn live: the region's frames, a summary that says each step, done, and no NEXT STEPS, since nothing was left.
    screen = _screen(raw)
    transcript = "\n".join(screen.transcript())
    assert DRAWN in raw and "init · done in" in transcript, transcript
    for step in ("Proxmox host", "certificate", "secret", "node", "storage", "bridge", "SSH key", "GitHub", "VPN"):
        assert re.search(rf"^ ✓ {re.escape(step)} ", transcript, re.M), (step, transcript)
    assert "NEXT STEPS" not in transcript and screen.frame_wraps == 0
    # The same again asks nothing, and writes nothing.
    files = _files()
    rc, raw = _asked(fake, [])
    assert rc == 0, raw.decode(errors="replace")
    assert engine_init.ASKED.encode() not in raw and b"nothing to write" in raw
    assert _files() == files


# The node, the storage and the bridge, in the order they are asked, each as the fake's host lists it, the only one the
# token can use there: its setting, its name, and its title, as a line of what was left names it.
ONLY_ONE = (
    ("proxmox_node", "pve", "Node"),
    ("proxmox_storage", "local-lvm", "Storage"),
    ("proxmox_bridge", "vmbr0", "Bridge"),
)


def _only_one(fake: Fake, *before: tuple[str, str, str]) -> None:
    """The fake's host, its token granted the engine's privileges on each one it lists, with a key in ~/.ssh, and each
    of before set in the host's settings.yml, as a run before this one answered it, so that it is not asked."""
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    _grants(fake)
    _public_key("id_ed25519.pub", PUBLIC_KEY)
    digest = overrides.MISSING
    for setting, name, _ in before:
        digest = overrides.set(_home(), setting, name, "pve", digest)


@pytest.mark.parametrize("at", range(len(ONLY_ONE)), ids=[title.lower() for _, _, title in ONLY_ONE])
def test_enter_at_the_only_one_s_yes_or_no_takes_yes_and_writes_the_name_the_host_reported(fake, at) -> None:
    # The coordinator's ruling on review 54's E5.md 7: where the host reports one node, storage or bridge the token can
    # use, its yes or no marks yes first, and Enter takes it, writing the name the host reported: three Enters that
    # left all three had the fleet fall back to pve, local-lvm and vmbr0, wrong on a host whose names differ. Those
    # above it set, so that it is asked; Esc at each after it, which leaves those.
    _only_one(fake, *ONLY_ONE[:at])
    plan: Plan = [
        (TRUST_ASKED, [UP, ENTER], True),
        (SECRET_ASKED, [fake.secret.encode() + b"\n"], False),
        (f"Use {ONLY_ONE[at][1]}?\r\n".encode(), [ENTER], True),
        *((f"Use {name}?\r\n".encode(), [ESC], True) for _, name, _ in ONLY_ONE[at + 1 :]),
        (GITHUB_ASKED, [b"octocat\n"], False),
        (VPN_ASKED, [ENTER], True),
    ]
    rc, raw = _asked(fake, plan)
    said = raw.decode(errors="replace")
    assert rc == 0, said
    assert f"Use {ONLY_ONE[at][1]}? yes" in _read(raw), said
    own = overrides.read(_home(), "pve").host.settings
    assert dict(own) == {**{setting: name for setting, name, _ in ONLY_ONE[: at + 1]}, "proxmox_pool": ENGINE_POOL}, own
    left = [title for _, _, title in ONLY_ONE[at + 1 :]]
    if left:
        assert f"left for later: {' and '.join(left)}; the next aw init asks again" in _read(raw), said
    else:
        assert "left for later" not in _read(raw), said


def test_no_at_the_only_node_s_yes_or_no_writes_nothing_and_typed_enter_leaves_it_as_its_prompt_says(fake) -> None:
    # The ruling's other half: no, picked by its letter, leaves the only node, and the storage and the bridge, which
    # are listed on a node, with it; nothing of the three is written, and nothing above the question says Enter leaves
    # it, since Enter there takes yes. (Esc leaves it too: the cases above press it at the storage and the bridge.)
    _only_one(fake)
    plan: Plan = [
        (TRUST_ASKED, [UP, ENTER], True),
        (SECRET_ASKED, [fake.secret.encode() + b"\n"], False),
        (b"Use pve?\r\n", [b"n", ENTER], True),
        (GITHUB_ASKED, [b"octocat\n"], False),
        (VPN_ASKED, [ENTER], True),
    ]
    rc, raw = _asked(fake, plan)
    said = raw.decode(errors="replace")
    assert rc == 0, said
    asked = said[said.index("What your token can see:") : said.index("Use pve?")]
    assert engine_init.LATER not in asked, asked
    assert b"Use local-lvm?" not in raw and b"Use vmbr0?" not in raw, said
    assert "Use pve? no" in _read(raw), said
    assert dict(overrides.read(_home(), "pve").host.settings) == {"proxmox_pool": ENGINE_POOL}
    assert "left for later: Node, Storage and Bridge; the next aw init asks again" in _read(raw), said
    # Again, where the terminal cannot draw a menu (TERM=dumb): the same question typed, whose Enter gives back the
    # question's default, as Esc at the menu does (runs/ask.py gives the two one default), and so leaves the node, as
    # the typed prompt says.
    rc, raw = _asked(fake, [(b"Use pve?", [], False), (TYPED_ASKED, [b"\n"], False)], term="dumb")
    said = raw.decode(errors="replace")
    assert rc == 0, said
    assert b"Enter for left for later" in raw, said
    assert GITHUB_ASKED not in raw and TRUST_ASKED not in raw and SECRET_ASKED not in raw, said
    assert dict(overrides.read(_home(), "pve").host.settings) == {"proxmox_pool": ENGINE_POOL}
    assert "left for later: Node, Storage and Bridge" in _read(raw), said


def test_a_certificate_nothing_vouches_for_is_kept_only_on_a_yes_and_enter_is_no(fake) -> None:
    # E3's question, whose safe answer is no, marked first: Enter keeps nothing, sends nothing with the token, and the
    # run is refused with nothing written.
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    files, sent = _files(), len(fake.calls())
    rc, raw = _asked(fake, [(TRUST_ASKED, [ENTER], True)])
    said = _read(raw)
    assert rc == 1, said
    assert "was not trusted: nothing was kept, and nothing sent with the token" in said and SECRET_ASKED not in raw
    assert _files() == files and all(not entry["token"] for entry in fake.calls()[sent:])


def test_the_token_s_secret_typed_wrong_twice_is_asked_again_with_the_rule_left_and_never_shown(fake, tmp_path) -> None:
    # Review 54, E5.md 3: the token's secret, hidden, typed in a form that is not one, twice. Its second asking says the
    # rule alone and what Enter does; the second is left, said; neither answer is on the screen, in the run's record or
    # log, or in any file of the fleet's home, and the vault holds no secret.
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    _public_key("id_ed25519.pub", PUBLIC_KEY)
    wrong = ("0123-not-the-secret", "the-secret-typed-wrong-again")
    plan: Plan = [
        (TRUST_ASKED, [UP, ENTER], True),
        (SECRET_ASKED, [wrong[0].encode() + b"\n"], False),
        (SECRET_ASKED, [wrong[1].encode() + b"\n"], False),
        (GITHUB_ASKED, [b"\n"], False),
        (VPN_ASKED, [ESC], True),
    ]
    rc, raw = _asked(fake, plan)
    said = _read(raw)
    assert rc == 0, said
    first = raw.index(SECRET_ASKED) + len(SECRET_ASKED)
    again = raw[first : raw.index(SECRET_ASKED, first)].decode(errors="replace")
    assert "That is not the 36-character secret pveum printed" in again and engine_init.LATER in again, again
    assert "either, so API token secret is left as it was: the next aw init asks again" in said, said
    _no_secret_said(raw, tmp_path, *wrong)
    assert _opened() == {}


def test_an_answer_outside_its_kind_is_asked_once_more_saying_the_rule_and_a_second_is_left(fake, tmp_path) -> None:
    # ~/.ssh/id_ed25519.pub is there: the VMs get it by default, and nothing asks which key.
    _public_key("id_ed25519.pub", PUBLIC_KEY)
    wrong_key = b"tskey a b"
    plan: Plan = [
        (HOST_ASKED, [b"pve.lan; x\n"], False),
        (b"That is not an IP address or a host name", [], False),
        (HOST_ASKED, [b"1.2.3.4 ProxyCommand x\n"], False),
        (GITHUB_ASKED, [b"-octocat\n"], False),
        (b"That is not a GitHub username", [], False),
        (GITHUB_ASKED, [b"octocat\n"], False),
        (VPN_ASKED, [DOWN, ENTER], True),
        (KEY_ASKED, [wrong_key + b"\n"], False),
        (b"That is not a key as Tailscale printed it", [], False),
        (KEY_ASKED, [wrong_key + b"\n"], False),
    ]
    rc, raw = _asked(fake, plan)
    said = _read(raw)
    assert rc == 0, said
    # The address, wrong twice, and the key, wrong twice, are left, said; the user name, right the second time, is kept.
    assert "either, so Proxmox host address is left as it was: the next aw init asks again" in said, said
    assert "either, so Tailscale auth key is left as it was" in said, said
    assert wrong_key not in raw
    assert dict(overrides.read(_home(), None).fleet.settings) == {"github_username": "octocat", "vpn": "tailscale"}
    # With the host's address left, nothing of the host's is written, nor a vault with nothing to put in it.
    assert sorted(_files()) == [".ssh/id_ed25519.pub", "fleet/settings.yml"], _files()
    _no_secret_said(raw, tmp_path, wrong_key.decode())


def test_enter_leaves_each_question_for_the_next_run_and_writes_nothing(fake) -> None:
    _public_key("id_ed25519.pub", PUBLIC_KEY)
    files = _files()
    plan: Plan = [(HOST_ASKED, [b"\n"], False), (GITHUB_ASKED, [b"\n"], False), (VPN_ASKED, [ESC], True)]
    rc, raw = _asked(fake, plan)
    said = _read(raw)
    assert rc == 0, said
    assert _files() == files
    left = "Proxmox host address, GitHub username and VPN"
    assert f"left for later: {left}; the next aw init asks again" in said, said
    # NEXT STEPS keeps the line that asks again, once.
    assert said.count("NEXT STEPS") == 1 and f"aw init asks again for {left}" in said, said
    assert "VPN: left for later" in said and choose.HINT not in said, said


def test_a_menu_picked_with_the_arrow_keys_writes_what_a_typed_answer_would(fake) -> None:
    _public_key("id_ed25519.pub", PUBLIC_KEY)
    settings = paths.fleet_dir(_home()) / overrides.SETTINGS
    by_menu: Plan = [(HOST_ASKED, [b"\n"], False), (GITHUB_ASKED, [b"\n"], False), (VPN_ASKED, [DOWN, ENTER], True)]
    rc, raw = _asked(fake, [*by_menu, (KEY_ASKED, [b"\n"], False)])
    assert rc == 0, raw.decode(errors="replace")
    picked = settings.read_bytes()
    settings.unlink()
    # Where the terminal cannot draw a menu, the same choice typed, by its name.
    typed: Plan = [(HOST_ASKED, [b"\n"], False), (GITHUB_ASKED, [b"\n"], False), (TYPED_ASKED, [b"tailscale\n"], False)]
    rc, raw = _asked(fake, [*typed, (KEY_ASKED, [b"\n"], False)], term="dumb")
    assert rc == 0, raw.decode(errors="replace")
    assert choose.HINT.encode() not in raw
    assert settings.read_bytes() == picked and b"vpn: tailscale" in picked, picked


def test_the_vpn_is_a_menu_none_first_answered_by_its_letter_and_the_auth_key_after_it_is_sealed_unseen(
    fake, tmp_path
) -> None:
    _public_key("id_ed25519.pub", PUBLIC_KEY)
    plan: Plan = [
        (HOST_ASKED, [b"\n"], False),
        (GITHUB_ASKED, [b"\n"], False),
        (VPN_ASKED, [b"t", ENTER], True),
        (KEY_ASKED, [AUTH_KEY.encode() + b"\n"], False),
    ]
    rc, raw = _asked(fake, plan)
    screen = _screen(raw)
    transcript = "\n".join(screen.transcript())
    assert rc == 0, transcript
    # The menu: none first, its few words beside each answer; erased once answered, its answer said on one line; never
    # wrapped, and the region erased while it was up.
    menu = raw[raw.index(VPN_ASKED) :]
    assert menu.index(b"none") < menu.index(b"tailscale") and b"each VM joins your tailnet" in menu
    assert "VPN: tailscale" in transcript and choose.HINT not in transcript, transcript
    assert screen.frame_wraps == 0
    # The questions say first that a VPN routing the LAN needs none, and the key is asked only after Tailscale.
    said = raw.decode(errors="replace")
    assert "A VPN you already run that routes this LAN" in said and "choose none, and that is all" in said
    assert said.index("VPN: tailscale") < said.index(KEY_ASKED.decode())
    # No frame of the run's between the user name's answer and the VPN's question: they come one after the other.
    between = raw[raw.index(GITHUB_ASKED) : raw.index(VPN_ASKED)]
    assert DRAWN not in between, between
    where = _home()
    assert dict(overrides.read(where, None).fleet.settings) == {"vpn": "tailscale"}
    # The key went straight into the vault: never echoed, said, logged or written in clear.
    assert {entry: _revealed(value) for entry, value in _opened().items()} == {engine_init.AUTH_KEY: AUTH_KEY}
    _no_secret_said(raw, tmp_path, AUTH_KEY)
    _sealed(AUTH_KEY)
    # Both set: neither is asked again.
    rc, raw = _asked(fake, [(HOST_ASKED, [b"\n"], False), (GITHUB_ASKED, [b"\n"], False)])
    assert rc == 0, raw.decode(errors="replace")
    assert VPN_ASKED not in raw and KEY_ASKED not in raw


@pytest.mark.parametrize("why", ["--tailscale on", "vpn: tailscale", "the web login only"])
def test_the_auth_key_is_asked_once_tailscale_is_chosen_and_not_where_it_has_nothing_to_do(fake, why) -> None:
    _public_key("id_ed25519.pub", PUBLIC_KEY)
    where = _home()
    words = ("--tailscale", "on") if why == "--tailscale on" else ()
    if why != "--tailscale on":
        digest = overrides.set(where, "vpn", "tailscale", None, overrides.MISSING)
        if why == "the web login only":
            overrides.set(where, "tailscale_auth_method", "web", None, digest)
    asks_key = why != "the web login only"
    plan: Plan = [(HOST_ASKED, [b"\n"], False), (GITHUB_ASKED, [b"\n"], False)]
    rc, raw = _asked(fake, [*plan, *([(KEY_ASKED, [b"\n"], False)] if asks_key else [])], *words)
    said = raw.decode(errors="replace")
    assert rc == 0, said
    # The VPN is not asked: given, or set.
    assert VPN_ASKED not in raw and (KEY_ASKED in raw) is asks_key, said
    assert overrides.read(where, None).fleet.settings["vpn"] == "tailscale"


def test_tailscale_given_that_the_vpn_set_contradicts_fails_naming_both_before_any_question(fake) -> None:
    where = _home()
    overrides.set(where, "vpn", "none", None, overrides.MISSING)
    files = _files()
    rc, raw = _asked(fake, [], "--tailscale", "on")
    said = _read(raw)
    assert rc == 1, said
    assert "--tailscale on, but fleet/settings.yml already sets vpn: none. aw init never writes over a value" in said
    assert engine_init.ASKED.encode() not in raw and _files() == files


@pytest.mark.parametrize(
    "words", [("--tailscale-auth-key", AUTH_KEY), ("--tailscale", "on", AUTH_KEY)], ids=["an option", "a word"]
)
def test_the_auth_key_given_on_the_command_line_is_refused_unsaid_and_nothing_is_made(fake, words) -> None:
    ran = _init(fake, *words)
    assert ran.returncode != 0 and AUTH_KEY not in ran.stdout + ran.stderr, ran
    assert _untouched()


def test_ctrl_c_at_a_question_ends_the_run_and_writes_nothing(fake) -> None:
    # One Ctrl-C at the VPN's menu (the owner's answer 4): the run ends, cancelled, with what was answered unwritten.
    _public_key("id_ed25519.pub", PUBLIC_KEY)
    files = _files()
    plan: Plan = [(HOST_ASKED, [b"\n"], False), (GITHUB_ASKED, [b"octocat\n"], False), (VPN_ASKED, [b"\x03"], True)]
    rc, raw = _asked(fake, plan)
    said = _read(raw)
    assert rc == 130, said
    assert _files() == files
    assert choose.HINT not in said and "init · stopped after" in said, said
    [made] = jobs.listed(paths.jobs_dir(_home()))
    assert made.read()["outcome"] == "cancelled"


def test_with_standard_input_from_dev_null_nothing_is_asked(fake, secret_file) -> None:
    # No terminal, and no --no-prompt: the values given are written as with nobody there, and no question is asked,
    # nor said to be left.
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    ran = _init(fake, "--certificate", trust.pem_fingerprint(fake.pki["node"]), "--token-secret-file", str(secret_file))
    assert ran.returncode == 0, ran
    assert engine_init.ASKED not in ran.stdout + ran.stderr and "not asked" not in ran.stdout + ran.stderr, ran
    assert {entry: _revealed(value) for entry, value in _opened().items()} == {"token@pve": fake.secret}


def test_the_host_s_address_and_its_token_are_asked_first_and_a_host_nothing_answers_writes_nothing(fake) -> None:
    # No host.yml: the address, then the token's ID, before anything is sent. Nothing answers at the address typed
    # (the API's port is 8006, where nothing listens here: a test's fake is on a port of its own, which no question
    # asks), so the certificate is not settled, the run is refused, and nothing is written.
    _public_key("id_ed25519.pub", PUBLIC_KEY)
    files = _files()
    plan: Plan = [(HOST_ASKED, [b"127.0.0.1\n"], False), (b"API token ID: ", [b"deploy@pam!claude\n"], False)]
    rc, raw = _asked(fake, plan)
    said = raw.decode(errors="replace")
    assert rc == 1, said
    assert said.index("Enter keeps agent-warehouse@pve!aw.") < said.index("API token ID: ")
    assert "https://127.0.0.1:8006 presented no certificate" in _read(raw) and SECRET_ASKED not in raw, said
    assert _files() == files


def test_init_in_the_background_of_its_terminal_is_a_run_with_nobody_there_and_ends(fake) -> None:
    # Review 54, E5.md 2: aw init & at a terminal. Standard input is a terminal whose foreground the run is not, which
    # runs/ask.py's terminal() takes for nobody to ask, and init decides by it too: E4's run, its words unchanged,
    # nothing asked and nothing drawn, and the run ends, never stopped by the terminal (SIGTTOU) for its live view's
    # change of the terminal's settings.
    files = _files()
    try:
        rc, raw = aw_at_a_terminal(
            "init", home=_home().root, first_on_path=(fake.ssh,), size=(100, 30), timeout=60, background=True
        )
    finally:
        _only_the_engine_s_names()
    said = " ".join(raw.decode(errors="replace").split())
    assert rc == 1, said
    refused = "error: the host pve has no address yet: give it, as aw init --no-prompt --address <address>; nothing"
    assert refused in said, said
    assert engine_init.ASKED not in said and "not asked" not in said and DRAWN not in raw, said
    assert _files() == files


def test_a_fifo_or_a_link_in_ssh_is_never_read_and_only_a_key_file_of_its_own_is_offered(fake) -> None:
    # Review 54, E5.md 6: each ~/.ssh/*.pub is opened without waiting on a pipe and never through a link, then held to a
    # regular file by what was opened, before anything of it is read. A FIFO, and a link to a key outside ~/.ssh, each
    # named to sort before the one key file: the run neither waits on the one nor offers the other, and Enter at the
    # menu takes the only key it offers, which is written.
    folder = _home().root / ".ssh"
    folder.mkdir()
    os.mkfifo(folder / "a-fifo.pub")
    elsewhere = _home().root / "elsewhere.pub"
    elsewhere.write_text(OTHER_KEY + "\n")
    (folder / "a-link.pub").symlink_to(elsewhere)
    _public_key("laptop.pub", PUBLIC_KEY)
    plan: Plan = [
        (HOST_ASKED, [b"\n"], False),
        (SSH_KEY_ASKED, [ENTER], True),
        (GITHUB_ASKED, [b"\n"], False),
        (VPN_ASKED, [ESC], True),
    ]
    rc, raw = _asked(fake, plan)
    said = _read(raw)
    assert rc == 0, said
    assert "laptop.pub" in said and "a-fifo.pub" not in said and "a-link.pub" not in said, said
    assert overrides.read(_home(), None).fleet.settings["vm_ssh_public_keys"] == [PUBLIC_KEY]


# Where an address is reached with no host.yml to give its port: the API's own, 8006, here at a loopback address of its
# own, never 127.0.0.1's, at which the test above holds that nothing answers.
AT_8006 = ("127.0.0.2", 8006)


@contextlib.contextmanager
def _api_at_8006(fake: Fake) -> Iterator[str]:
    """The fake's API reached at 127.0.0.2:8006, the port an address is reached at where no host.yml gives one, through
    a forwarder of the test's own to the fake's port on 127.0.0.1: the address, the forwarder ended once the test is
    done with it. Skipped where this system has no 127.0.0.2 to listen at (macOS answers 127.0.0.1 alone of its
    loopback unless told)."""
    try:
        server = socket.create_server(AT_8006)
    except OSError as error:
        if error.errno == errno.EADDRNOTAVAIL:
            pytest.skip(f"this system has no {AT_8006[0]} to listen at for the API's port, {AT_8006[1]}")
        raise
    done = threading.Event()
    pumps: list[threading.Thread] = []

    def pump(near: socket.socket) -> None:
        # Both ways, until either end closes or the test is done.
        try:
            with near, socket.create_connection(("127.0.0.1", fake.port), timeout=10) as far:
                while not done.is_set():
                    ready, _, _ = select.select([near, far], [], [], 0.2)
                    for each in ready:
                        data = each.recv(65536)
                        if not data:
                            return
                        (far if each is near else near).sendall(data)
        except OSError:
            return

    def accept() -> None:
        while not done.is_set():
            ready, _, _ = select.select([server], [], [], 0.2)
            if ready:
                near, _ = server.accept()
                pumps.append(threading.Thread(target=pump, args=(near,), daemon=True))
                pumps[-1].start()

    acceptor = threading.Thread(target=accept, daemon=True)
    acceptor.start()
    try:
        yield AT_8006[0]
    finally:
        done.set()
        acceptor.join(5)
        for each in pumps:
            each.join(5)
        server.close()


def test_a_token_id_typed_wrong_twice_keeps_the_engine_s_own_says_so_and_host_yml_has_it(fake) -> None:
    # Review 54, E5.md 1. No host.yml, so the token's ID is asked, and Enter keeps the engine's own. Typed wrong twice,
    # the engine's own is kept too, and every word says so: the second asking says what Enter keeps, never that it
    # decides later; the run says the ID kept, never left for the next run, which NEXT STEPS does not name; and host.yml
    # has that ID, which the next run never asks. The address and the certificate given, the API reached at its own
    # port through the test's forwarder; the secret left, so nothing is read with the token.
    _public_key("id_ed25519.pub", PUBLIC_KEY)
    asked = b"API token ID: "
    plan: Plan = [
        (asked, [b"not a token\n"], False),
        (asked, [b"still-wrong\n"], False),
        (SECRET_ASKED, [b"\n"], False),
        (GITHUB_ASKED, [b"\n"], False),
        (VPN_ASKED, [ESC], True),
    ]
    fingerprint = trust.pem_fingerprint(fake.pki["node"])
    with _api_at_8006(fake) as address:
        rc, raw = _asked(fake, plan, "--address", address, "--certificate", fingerprint)
    said = _read(raw)
    assert rc == 0, said
    kept = engine_init.OWN_TOKEN
    first = raw.index(asked) + len(asked)
    again = raw[first : raw.index(asked, first)].decode(errors="replace")
    assert "That is not a token ID" in again and f"Enter keeps {kept}." in again, again
    assert engine_init.LATER not in again, again
    assert f"either, so API token ID keeps {kept}." in said and "API token ID is left" not in said, said
    left = re.search(r"left for later: ([^;]*);", said)
    assert left is not None and "API token secret" in left.group(1) and "API token ID" not in left.group(1), said
    assert "aw init asks again for API token secret" in said and "asks again for API token ID" not in said, said
    fields = hosts.Layout(_home()).state("pve").fields
    assert fields is not None, _files()
    assert (fields.address, f"{fields.token_user}!{fields.token_id}") == (address, kept)


def test_a_token_of_the_person_s_own_with_its_secret_typed_records_the_engine_s_pool_alone(fake, tmp_path) -> None:
    # Card E19 at a terminal (review 55, E19.md's "For the rebase onto E5"): where the secret is typed, the names are
    # compared as where it is given. host.yml, as a person writes it before init, names the API's port and their own
    # token, ops@pve!tools, made in the fake as an administrator makes it, the engine's role granted it and its user on
    # the engine's pool, which is marked; its secret is typed at the question, and the pool's mark read with it. The
    # fleet is named, and made records the pool alone, nothing of the person's user or token, which _asked's check
    # after the run holds too. No forwarder at the API's own port: its one address and port are E5's token ID case's,
    # which a second test would contend for on another worker.
    _public_key("id_ed25519.pub", PUBLIC_KEY)
    full_id = f"{OWN_USER}!{OWN_TOKEN}"
    _pve(fake.base, "POST", "access/users", userid=OWN_USER)
    secret = _pve(fake.base, "POST", f"access/users/{OWN_USER}/token/{OWN_TOKEN}", privsep=1)["data"]["value"]
    _pve(fake.base, "PUT", "access/acl", path=f"/pool/{ENGINE_POOL}", roles=ENGINE_ROLE, users=OWN_USER, tokens=full_id)
    fleet = record.new_id()
    mark_pool(fake.base, fleet)
    with_port(fake.port)
    layout = hosts.Layout(_home())
    own = {"token_user": OWN_USER, "token_id": OWN_TOKEN}
    hosts.write(layout, "pve", layout.state("pve").digest, change=lambda raw: raw.update(own))
    plan: Plan = [
        (SECRET_ASKED, [secret.encode() + b"\n"], False),
        (GITHUB_ASKED, [b"\n"], False),
        (VPN_ASKED, [ESC], True),
    ]
    rc, raw = _asked(fake, plan, "--certificate", trust.pem_fingerprint(fake.pki["node"]))
    said = _read(raw)
    assert rc == 0, said
    assert f"the fleet {fleet}, as the pool's mark names it" in said, said
    assert record.read(_home()).fleet == fleet
    fields = hosts.Layout(_home()).state("pve").fields
    assert fields is not None, _files()
    assert (fields.token_user, fields.token_id) == (OWN_USER, OWN_TOKEN)
    assert fields.made == (hosts.Made("pool", ENGINE_POOL, "administrator"),)
    assert {entry: _revealed(value) for entry, value in _opened().items()} == {"token@pve": secret}
    _no_secret_said(raw, tmp_path, secret)


# ---------------------------------------------------------------- the offer over root SSH (card E7) ----

# The offer's question, as its label ends its text, and the secret's.
SET_UP = engine_init.SET_UP
SECRET_LABEL = "API token secret:"
# The engine's own token, by its full ID, as host.yml's made names it.
ENGINE_FULL = f"{ENGINE_USER}!{ENGINE_TOKEN}"
# The commands root SSH runs on a host whose settings.yml names nothing yet, up to the question, all of them reads: the
# node's name, the script's lists of storages and bridges, the pool as root reads it (card E8), and the dry run.
POOL_READ = shlex.join(pve_script.pool_read(ENGINE_POOL))
READS = ["hostname -s", "/bin/sh -s -- --list", POOL_READ, "/bin/sh -s -- --dry-run"]


@pytest.fixture
def pve_host(engine_guard, fake, tmp_path) -> Machine:
    """The Proxmox host as a stand-in machine of tests/unit/aw_machine.py's, reached as root through B3's runner with
    the stand-in ssh first on this test's own PATH, as tests/unit/test_aw_engine_certificate.py's: its bin holds the
    stand-in pveum, pvesh, hostname and id (tests/molecule/fake_pve_cli.py) of a host with nothing of the engine's yet,
    whose next token's secret is the fake API's engine token's, so that the client the run opens after a y is the
    fake's own token. The script runs there as root SSH runs it, /bin/sh reading it from its standard input in the
    machine's home."""
    machines = tmp_path / "machines"
    host = machine(machines, "pve", account="root")
    filed(host)
    _pve_cli(host.bin, {"next_secret": fake.secret})
    engine_guard.first_on_path(ssh_stand_in(machines, [host]))
    return host


@pytest.fixture
def job(fake):
    """A run's job in the fleet's home, whose children the offer's commands are, as a run of init's own."""
    made = jobs.create(paths.jobs_dir(_home()), "init", "init", "pve", [], {"kind": "terminal"}, True)
    yield made
    jobs.drop(made)


class _Said:
    """What a run of init in this process emits, as its live view draws it: each event's kind, text and fields."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []
        # Each shield the run holds now, by its reason, as the run's ladder holds them (runs/attached.py's shield).
        self.shields: list[str] = []

    def emit(self, kind: str, _target: object, text: str, **fields: object) -> None:
        self.events.append((kind, text, fields))

    @contextlib.asynccontextmanager
    async def shield(self, reason: str):
        self.shields.append(reason)
        try:
            yield
        finally:
            self.shields.remove(reason)


class _Person(engine_init._Asking):
    """The person at init's terminal, in this process: each question asked as init asks it, through _Asking's own
    steps, and answered by the first of answers whose label ends its text (a callable is given the text, and gives the
    answer), else its default, as Enter gives it back (and Esc at a menu). Every question asked is kept."""

    def __init__(self, answers: dict | None = None) -> None:
        super().__init__(_Said())  # type: ignore[arg-type]
        self.answers = dict(answers or {})
        self.asked: list[str] = []

    async def ask(self, text, default, choices=(), hidden=False):
        self.asked.append(text)
        for label, answer in self.answers.items():
            if text.endswith(label):
                return answer(text) if callable(answer) else answer
        return default

    def question(self, label: str) -> list[str]:
        """Every question asked whose label is label."""
        return [text for text in self.asked if text.endswith(label)]

    def said(self) -> str:
        """Every line said above the region, in order."""
        return "\n".join(text for kind, text, _ in self.running.events if kind == "report")


def _offered(fake: Fake, person: _Person, job, host: Machine, typed: engine_init.Given | None = None) -> list[str]:
    """init at a terminal, run in this process, the person answering: its job's root SSH reaches host for the offer,
    the certificate given as its fingerprint, as --certificate gives it (root SSH's read of the host's files is
    tests/unit/test_aw_engine_certificate.py's). What the run says once it has written."""
    try:
        return asyncio.run(_offering(fake, person, job, host, typed))
    finally:
        _only_the_engine_s_names()


def _offering(fake: Fake, person: _Person, job, host: Machine, typed: engine_init.Given | None = None):
    """_offered's run of init, not yet awaited."""
    given = typed or engine_init.Given(certificate=trust.pem_fingerprint(fake.pki["node"]))
    return engine_init._run(
        _home(),
        given,
        environ=dict(os.environ),
        job=job,
        root_ssh=_reader(),
        asking=person,
        offer_target=target(host),
    )


def _ran(host: Machine) -> list[str]:
    """Each command root SSH ran on host, as the runner hands it to ssh: one word, its last."""
    return [call["argv"][-1] for call in host.calls()]


def _state_of(host: Machine, **changes: object) -> dict:
    """The stand-in host's state, changed first where changes are given, as a hand changes the host."""
    path = host.bin / "pve-state.json"
    state = json.loads(path.read_text())
    if changes:
        state.update(changes)
        path.write_text(json.dumps(state))
    return state


def _engine_s(fleet: str, **changes: object) -> access.Host:
    """The Host the offer renders the script for on the stand-in host, its node pve and the values in effect."""
    return access.Host(**{"fleet": fleet, "node": "pve", "storage": "local-lvm", "bridges": ("vmbr0",), **changes})


def _nothing_of(secret: str, tmp_path: Path, person: _Person, host: Machine, *more: str) -> None:
    """The secret in no question, no line or event said, no command line root SSH ran and no pveum's or pvesh's words
    on the host (each log of them), no line of more, and no file of the fleet's home in clear, the vault sealed."""
    events = json.dumps(person.running.events, default=str)
    shown = tmp_path / "shown.txt"
    shown.write_text("\n".join([*person.asked, events, *more]))
    assert searched_for([secret], shown, host.directory / "calls.log", host.bin / "calls.log") == []
    _sealed(secret)


def test_a_y_at_the_offer_makes_every_object_and_seals_the_secret_the_script_printed(
    fake, pve_host, job, tmp_path
) -> None:
    # The host holds nothing of the engine's. The node is read over root SSH, settings.yml naming none, and the
    # storage and the bridge in effect are those the node lists, unasked; the dry run's commands are in the question,
    # and nothing is changed on the host before the y (the invariant: nothing runs on Proxmox without a y).
    with_port(fake.port)
    home = sorted(path.name for path in pve_host.home.iterdir())
    at_the_question: list[list[str]] = []

    def y(_text: str) -> str:
        at_the_question.append(_pve_cli_writes(pve_host.bin))
        return engine_init.YES

    person = _Person({SET_UP: y})
    lines = _offered(fake, person, job, pve_host)
    assert at_the_question == [[]]
    assert _ran(pve_host) == [*READS, "/bin/sh -s --"]
    fleet = record.read(_home()).fleet
    host = _engine_s(fleet)
    state = _state_of(pve_host)
    # Nothing of E1's list is missing now, and the pool carries the fleet's mark.
    assert access.plan(access.objects(host), _found(state), host_flag="").steps == ()
    assert mark.parse(state["pools"][ENGINE_POOL]["comment"]) == mark.Mark(fleet, mark.NONE)
    # The question showed each command the run then ran, as the dry run quotes it, and each object with what it holds.
    [question] = person.question(SET_UP)
    calls = _pve_cli_calls(pve_host.bin)
    writes = [each for each in calls if each[0] == "pveum" and each[1:3] != ["user", "permissions"]]
    assert len(writes) == len(access.objects(host)), writes
    for call in writes:
        assert f"\n  {shlex.join(call)}\n" in question, (call, question)
    assert f"the role {access.ROLE}, with " in question and f"the token {ENGINE_FULL}, with privilege" in question
    assert "the token's secret goes straight into the encrypted vault" in question
    # The secret the script printed, sealed in the vault before anything else, never asked for; no token deleted.
    assert {entry: _revealed(value) for entry, value in _opened().items()} == {"token@pve": fake.secret}
    assert person.question(SECRET_LABEL) == []
    assert not any("token delete" in each for each in _pve_cli_writes(pve_host.bin))
    # made records every object of the list, this engine its maker (its own names alone: _offered's check).
    made = _reached().fields.made
    assert sorted((each.what, each.id, each.by) for each in made) == sorted(
        (obj.what, obj.id, "engine") for obj in access.objects(host)
    )
    assert dict(overrides.read(_home(), "pve").host.settings) == {
        "proxmox_node": "pve",
        "proxmox_storage": "local-lvm",
        "proxmox_bridge": "vmbr0",
        "proxmox_pool": ENGINE_POOL,
    }
    assert any(line.startswith(f"the access script ran {len(writes)} commands") for line in lines), lines
    _nothing_of(fake.secret, tmp_path, person, pve_host, *lines)
    # No copy of the script left on the host.
    assert sorted(path.name for path in pve_host.home.iterdir()) == home
    # The same again: the vault holds the secret, so the dry run is --no-token, and nothing is missing: no question,
    # nothing changed on the host or in the fleet's folder.
    files, done = _files(), len(_pve_cli_writes(pve_host.bin))
    again = _Person({SET_UP: engine_init.YES})
    _offered(fake, again, job, pve_host)
    assert again.question(SET_UP) == [] and again.question(SECRET_LABEL) == []
    assert _ran(pve_host)[-1] == "/bin/sh -s -- --dry-run --no-token"
    assert len(_pve_cli_writes(pve_host.bin)) == done and _files() == files


@pytest.mark.parametrize("answer", [engine_init.NO, None], ids=["n", "enter-or-esc"])
def test_n_or_enter_at_the_offer_leaves_the_host_as_it_was_and_the_secret_is_then_asked(
    fake, pve_host, job, answer
) -> None:
    with_port(fake.port)
    before = (pve_host.bin / "pve-state.json").read_bytes()
    person = _Person({} if answer is None else {SET_UP: answer})
    _offered(fake, person, job, pve_host)
    assert len(person.question(SET_UP)) == 1
    # Only reads ran, and the host's state is byte for byte as it was.
    assert _ran(pve_host) == READS
    assert _pve_cli_writes(pve_host.bin) == [] and (pve_host.bin / "pve-state.json").read_bytes() == before
    assert "Nothing was made on 127.0.0.1: to set it up yourself, run " in person.said()
    assert " --print-commands | ssh root@127.0.0.1 sh -s -- --dry-run here" in person.said()
    assert len(person.question(SECRET_LABEL)) == 1
    assert _reached().fields.made == ()


@pytest.mark.parametrize("scope", [access.NODE_SCOPE, "pool"])
def test_the_plan_says_what_the_audit_scope_lets_the_token_read(fake, pve_host, job, scope) -> None:
    # O15, item 2, and the owner's answer of 2026-11-24 to review 58's finding 5: the /vms line under the node's scope
    # alone; under the pool's, what Sys.Audit on the node still reads.
    with_port(fake.port)
    if scope == "pool":
        layers = overrides.read(_home(), "pve")
        digest = layers.host.digest if layers.host is not None else overrides.MISSING
        overrides.set(_home(), "proxmox_audit_scope", "pool", "pve", digest)
    person = _Person({SET_UP: engine_init.NO})
    _offered(fake, person, job, pve_host)
    [question] = person.question(SET_UP)
    other = "pool" if scope == access.NODE_SCOPE else access.NODE_SCOPE
    assert engine_init.SCOPE_SAID[scope] in question and engine_init.SCOPE_SAID[other] not in question, question
    assert ("VM.Audit on /vms" in question) == (scope == access.NODE_SCOPE)


def test_a_bridge_the_node_lists_in_place_of_the_one_in_effect_is_asked_and_the_access_set_up_on_it(
    fake, pve_host, job
) -> None:
    with_port(fake.port)
    _state_of(pve_host, bridges=["vmbr1"])
    person = _Person({"Use vmbr1?": engine_init.YES, SET_UP: engine_init.YES})
    _offered(fake, person, job, pve_host)
    # The storage in effect is one the node lists: unasked.
    assert len(person.question("Use vmbr1?")) == 1 and person.question("Use local-lvm?") == []
    assert overrides.read(_home(), "pve").host.settings["proxmox_bridge"] == "vmbr1"
    host = _engine_s(record.read(_home()).fleet, bridges=("vmbr1",))
    assert access.plan(access.objects(host), _found(_state_of(pve_host)), host_flag="").steps == ()


def _vault_refuses(*_args, **_kwargs) -> None:
    raise OSError(errno.ENOSPC, "No space left on device")


def test_a_vault_that_cannot_be_written_has_the_token_the_run_made_deleted_again(
    fake, pve_host, job, monkeypatch, tmp_path
) -> None:
    # As the playbook's rescue: the token the run made is deleted while the vault is unwritten, so none is left there
    # whose secret is held nowhere. What it made before the token stays, recorded.
    with_port(fake.port)
    monkeypatch.setattr(sealed, "put", _vault_refuses)
    person = _Person({SET_UP: engine_init.YES})
    with pytest.raises(engine_init.Refused) as stopped:
        _offered(fake, person, job, pve_host)
    sentence = stopped.value.sentence
    assert "the vault could not be written (No space left on device)" in sentence, sentence
    assert f"the token {ENGINE_FULL} this run made was deleted on 127.0.0.1 again" in sentence, sentence
    assert _state_of(pve_host)["tokens"] == {}
    assert _pve_cli_writes(pve_host.bin)[-1] == f"pveum user token delete {ENGINE_USER} {ENGINE_TOKEN}"
    assert _ran(pve_host)[-1] == f"pveum user token delete {ENGINE_USER} {ENGINE_TOKEN}"
    made = {(each.what, each.id) for each in _reached().fields.made}
    assert {("pool", ENGINE_POOL), ("user", ENGINE_USER)} <= made and ("token", ENGINE_FULL) not in made, made
    assert not any(name.endswith(f" {ENGINE_FULL}") for what, name in made if what == "grant"), made
    vault = sealed.path(_home())
    assert not vault.exists() or "token@pve" not in _opened()
    _nothing_of(fake.secret, tmp_path, person, pve_host, sentence)


@pytest.mark.parametrize("where", ["host", "computer"])
def test_a_secret_printed_that_is_no_uuid_is_never_kept_and_its_token_is_deleted(
    fake, pve_host, job, monkeypatch, where
) -> None:
    # host: Proxmox answers a token whose value is no UUID, which the script refuses after the token was made; computer:
    # the script's line on standard output is held to a UUID again here (review 62), a host that printed another line
    # taken for one that printed none.
    with_port(fake.port)
    wrong = "not-a-secret-at-all"
    if where == "host":
        _state_of(pve_host, next_secret=wrong)
    else:
        real = engine_init._shell

        def shell(*args, **kwargs):
            run = real(*args, **kwargs)

            async def changed(command, stdin, seconds):
                result = await run(command, stdin, seconds)
                if list(command) == list(engine_init.SH) and isinstance(result, runner.Done):
                    return dataclasses.replace(result, lines=(wrong,))
                return result

            return changed

        monkeypatch.setattr(engine_init, "_shell", shell)
    person = _Person({SET_UP: engine_init.YES})
    with pytest.raises(engine_init.Refused) as stopped:
        _offered(fake, person, job, pve_host)
    sentence = stopped.value.sentence
    assert f"the token {ENGINE_FULL} this run made was deleted on 127.0.0.1 again" in sentence.lower(), sentence
    if where == "computer":
        assert "printed no secret of it" in sentence, sentence
    assert _state_of(pve_host)["tokens"] == {}
    assert _ran(pve_host)[-1] == f"pveum user token delete {ENGINE_USER} {ENGINE_TOKEN}"
    assert wrong not in sentence and wrong not in person.said()
    vault = sealed.path(_home())
    assert not vault.exists() or "token@pve" not in _opened()


def _pveum_first(host: Machine, words: str, then: str) -> None:
    """The stand-in host's pveum, where its words begin with words, running the shell lines then before (or in place
    of) Proxmox's own, which is pveum.real beside it: as a second hand on the host acts while the script runs."""
    real = host.bin / "pveum.real"
    (host.bin / "pveum").rename(real)
    wrapper = host.bin / "pveum"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'here=$(dirname "$0")\ncase "$*" in\n{shlex.quote(words)}*)\n{then}\n;;\nesac\nexec "$here/pveum.real" "$@"\n'
    )
    wrapper.chmod(0o755)


def test_a_run_cut_after_the_pool_keeps_the_fleet_s_id_and_the_next_init_makes_the_rest(fake, pve_host, job) -> None:
    # Review 72, 1 and 4: fleet.json is written at the y with the ID the script marks the pool with, so a run cut after
    # the pool finds the same ID the next time; made records what the cut run finished, never its last command said.
    with_port(fake.port)
    _state_of(pve_host, fail="pveum user add")
    person = _Person({SET_UP: engine_init.YES})
    with pytest.raises(engine_init.Refused) as stopped:
        _offered(fake, person, job, pve_host)
    sentence = stopped.value.sentence
    assert "The access script did not finish on 127.0.0.1" in sentence, sentence
    assert "makes only what is still missing" in sentence, sentence
    fleet = record.read(_home()).fleet
    state = _state_of(pve_host)
    assert mark.parse(state["pools"][ENGINE_POOL]["comment"]) == mark.Mark(fleet, mark.NONE)
    assert ENGINE_USER not in state["users"] and state["tokens"] == {}
    host = _engine_s(fleet)
    roles = {("role", obj.name) for obj in access.objects(host) if isinstance(obj, access.Role)}
    assert {(each.what, each.id) for each in _reached().fields.made} == {("pool", ENGINE_POOL), *roles}
    assert not any("token" in each for each in _pve_cli_writes(pve_host.bin))
    # The host as it was but the refusal: the same fleet goes on, and makes the rest.
    _state_of(pve_host, fail="")
    again = _Person({SET_UP: engine_init.YES})
    _offered(fake, again, job, pve_host)
    assert record.read(_home()).fleet == fleet
    assert access.plan(access.objects(host), _found(_state_of(pve_host)), host_flag="").steps == ()
    assert {entry: _revealed(value) for entry, value in _opened().items()} == {"token@pve": fake.secret}
    assert {("user", ENGINE_USER), ("token", ENGINE_FULL)} <= {(each.what, each.id) for each in _reached().fields.made}


@pytest.mark.parametrize("case", ["refused", "another-s-made-first"])
def test_a_token_add_proxmox_refuses_deletes_no_token_and_none_is_said_to_be_the_run_s(
    fake, pve_host, job, case
) -> None:
    # Review 72, 2: the script says the token's line before its add, so a run that ended by itself on that line had
    # the add refused. another-s-made-first: a second hand makes a token of that ID between the run's check and its
    # add, which the run must leave there.
    with_port(fake.port)
    if case == "refused":
        _state_of(pve_host, fail="user token add")
    else:
        _state_of(pve_host, next_secret=OTHER)
        _pveum_first(
            pve_host,
            "user token add ",
            '"$here/pveum.real" "$@" >/dev/null 2>&1\n'
            'echo "400 Parameter verification failed. token: already exists" >&2\nexit 255',
        )
    person = _Person({SET_UP: engine_init.YES})
    with pytest.raises(engine_init.Refused) as stopped:
        _offered(fake, person, job, pve_host)
    sentence = stopped.value.sentence
    assert f"Proxmox refused to make the token {ENGINE_FULL}: nothing of it was deleted" in sentence, sentence
    assert "this run made" not in sentence and "token delete" not in sentence, sentence
    assert not any("token delete" in each for each in _pve_cli_writes(pve_host.bin))
    assert _ran(pve_host)[-1] == "/bin/sh -s --"
    tokens = _state_of(pve_host)["tokens"]
    assert (ENGINE_FULL in tokens) == (case == "another-s-made-first"), tokens
    made = {(each.what, each.id) for each in _reached().fields.made}
    assert ("user", ENGINE_USER) in made and ("token", ENGINE_FULL) not in made, made
    vault = sealed.path(_home())
    assert not vault.exists() or "token@pve" not in _opened()


def test_the_connection_lost_after_the_token_s_line_says_its_delete_line_and_runs_nothing_more(
    fake, pve_host, job, monkeypatch, tmp_path
) -> None:
    # Review 72, 4 (E2's note, review 62): ssh's 255 after the token's line, the run's own lines up to it, the secret
    # printed after it: the token may be there, so its delete line is said; nothing more is run on the host.
    with_port(fake.port)
    real = engine_init._shell

    def shell(*args, **kwargs):
        run = real(*args, **kwargs)

        async def lost(command, stdin, seconds):
            result = await run(command, stdin, seconds)
            if list(command) == list(engine_init.SH) and isinstance(result, runner.Done):
                said = result.said.splitlines()
                at = next(n for n, each in enumerate(said) if each.startswith("+ pveum user token add "))
                return runner.Unreachable("\n".join(said[: at + 1]), result.lines)
            return result

        return lost

    monkeypatch.setattr(engine_init, "_shell", shell)
    person = _Person({SET_UP: engine_init.YES})
    with pytest.raises(engine_init.Refused) as stopped:
        _offered(fake, person, job, pve_host)
    sentence = stopped.value.sentence
    delete = f"pveum user token delete {ENGINE_USER} {ENGINE_TOKEN}"
    assert f"The token {ENGINE_FULL} may be on 127.0.0.1 with its secret unsaid: as root there, {delete}" in sentence
    assert _ran(pve_host)[-1] == "/bin/sh -s --"
    assert ENGINE_FULL in _state_of(pve_host)["tokens"]
    made = {(each.what, each.id) for each in _reached().fields.made}
    assert ("user", ENGINE_USER) in made and ("token", ENGINE_FULL) not in made, made
    vault = sealed.path(_home())
    assert not vault.exists() or "token@pve" not in _opened()
    _nothing_of(fake.secret, tmp_path, person, pve_host, sentence)


def test_a_secret_given_runs_with_no_token_and_records_the_user_and_the_token_as_the_administrator_s(
    fake, pve_host, job, monkeypatch
) -> None:
    # Review 72, 4 (decision 9): the user and the token made by hand, the secret given; the offer runs --no-token and
    # makes the rest, made recording what it made as the engine's and the user and the token as the administrator's.
    fleet = record.new_id()
    monkeypatch.setattr(record, "new_id", lambda: fleet)
    mark_pool(fake.base, fleet)
    with_port(fake.port)
    state = _state_of(pve_host)
    _state_of(
        pve_host,
        users={**state["users"], ENGINE_USER: {}},
        tokens={ENGINE_FULL: {"privsep": 1, "secret": fake.secret}},
    )
    given = engine_init.Given(
        certificate=trust.pem_fingerprint(fake.pki["node"]), secret=Secret(fake.secret), secret_from="the test's"
    )
    person = _Person({SET_UP: engine_init.YES})
    _offered(fake, person, job, pve_host, given)
    assert _ran(pve_host)[-2:] == ["/bin/sh -s -- --dry-run --no-token", "/bin/sh -s -- --no-token"]
    assert person.question(SECRET_LABEL) == []
    assert not any(each.startswith("pveum user token ") for each in _pve_cli_writes(pve_host.bin))
    assert record.read(_home()).fleet == fleet
    host = _engine_s(fleet)
    assert access.plan(access.objects(host), _found(_state_of(pve_host)), host_flag="").steps == ()
    by = {(each.what, each.id): each.by for each in _reached().fields.made}
    assert by[("user", ENGINE_USER)] == by[("token", ENGINE_FULL)] == "administrator", by
    assert by[("pool", ENGINE_POOL)] == "engine" and {
        value for (what, _), value in by.items() if what in ("role", "grant")
    } == {"engine"}, by
    assert {entry: _revealed(value) for entry, value in _opened().items()} == {"token@pve": fake.secret}


def test_a_run_ended_while_the_script_runs_said_the_token_s_delete_line_in_its_shield(
    fake, pve_host, job, tmp_path
) -> None:
    # Review 72, 3: the script, the vault's write, made's record and any deletion run in one shield (a Ctrl-C lets it
    # finish); a run ended inside it all the same (the grace's end, a second Ctrl-C, SIGHUP, SIGTERM: the body
    # cancelled, as the ladder cancels it) says the delete line of the token it may have made, its secret held nowhere.
    # The host's pveum waits at the token's first grant, the token made, until the run is ended.
    with_port(fake.port)
    held = pve_host.bin / "held"
    _pveum_first(
        pve_host,
        "acl modify ",
        'case "$*" in *" --tokens "*) : > "$here/held"; n=0\n'
        'while [ -e "$here/held" ] && [ "$n" -lt 600 ]; do sleep 0.1; n=$((n + 1)); done ;; esac',
    )
    person = _Person({SET_UP: engine_init.YES})
    seen: dict[str, object] = {}

    async def ended() -> None:
        run = asyncio.ensure_future(_offering(fake, person, job, pve_host))
        for _ in range(1200):
            if held.exists() or run.done():
                break
            await asyncio.sleep(0.05)
        seen["shields"] = list(person.running.shields)
        seen["token"] = ENGINE_FULL in _state_of(pve_host)["tokens"]
        run.cancel()
        try:
            await run
        except asyncio.CancelledError:
            seen["ended by"] = "cancel"

    try:
        asyncio.run(ended())
    finally:
        held.unlink(missing_ok=True)
        _only_the_engine_s_names()
    assert seen == {
        "shields": ["setting up the API access on 127.0.0.1"],
        "token": True,
        "ended by": "cancel",
    }, seen
    assert person.running.shields == []
    delete = f"pveum user token delete {ENGINE_USER} {ENGINE_TOKEN}"
    assert (
        f"The run was ended while the access was set up on 127.0.0.1: the token {ENGINE_FULL} may be there with its"
        f" secret held nowhere. As root there, {delete} deletes it."
    ) in person.said(), person.said()
    assert not any("token delete" in each for each in _pve_cli_writes(pve_host.bin))
    vault = sealed.path(_home())
    assert not vault.exists() or "token@pve" not in _opened()
    _nothing_of(fake.secret, tmp_path, person, pve_host)


def test_with_the_secret_held_the_offer_runs_with_no_token_and_makes_only_what_is_missing(fake, pve_host, job) -> None:
    with_port(fake.port)
    _offered(fake, _Person({SET_UP: engine_init.YES}), job, pve_host)
    made, vault = _reached().fields.made, sealed.path(_home()).read_bytes()
    # One grant to the token taken out on the host by hand.
    host = _engine_s(record.read(_home()).fleet)
    grant = next(obj for obj in access.objects(host) if isinstance(obj, access.Grant) and obj.subject == ENGINE_FULL)
    state = _state_of(pve_host)
    taken = (grant.path, grant.role, ENGINE_FULL)
    acls = [acl for acl in state["acls"] if (acl["path"], acl["roleid"], acl["ugid"]) != taken]
    assert len(acls) == len(state["acls"]) - 1
    _state_of(pve_host, acls=acls)
    done = len(_pve_cli_writes(pve_host.bin))
    person = _Person({SET_UP: engine_init.YES})
    _offered(fake, person, job, pve_host)
    [question] = person.question(SET_UP)
    assert "the token's secret goes straight into the encrypted vault" not in question
    assert _ran(pve_host)[-2:] == ["/bin/sh -s -- --dry-run --no-token", "/bin/sh -s -- --no-token"]
    assert _pve_cli_writes(pve_host.bin)[done:] == [
        f"pveum acl modify {grant.path} --tokens {ENGINE_FULL} --roles {grant.role}"
    ]
    assert person.question(SECRET_LABEL) == []
    assert sealed.path(_home()).read_bytes() == vault and _reached().fields.made == made


def test_a_token_there_whose_secret_is_held_nowhere_here_is_never_offered(fake, pve_host, job) -> None:
    # Review 58: in a pool nothing shows is this fleet's (none there here), one of the engine's ID may be another's.
    with_port(fake.port)
    state = _state_of(pve_host)
    users = {**state["users"], ENGINE_USER: {}}
    _state_of(pve_host, users=users, tokens={ENGINE_FULL: {"privsep": 1, "secret": OTHER}})
    before = (pve_host.bin / "pve-state.json").read_bytes()
    person = _Person({SET_UP: engine_init.YES})
    _offered(fake, person, job, pve_host)
    assert person.question(SET_UP) == []
    assert f"The token {ENGINE_FULL} is on 127.0.0.1 already, and its secret is held nowhere here" in person.said()
    assert _ran(pve_host) == READS
    assert (pve_host.bin / "pve-state.json").read_bytes() == before
    assert len(person.question(SECRET_LABEL)) == 1


def test_with_nobody_there_no_offer_is_made_and_the_host_is_never_reached(fake, pve_host, job) -> None:
    mark_pool(fake.base, record.new_id())
    with_port(fake.port)
    given = engine_init.Given(
        certificate=trust.pem_fingerprint(fake.pki["node"]), secret=Secret(fake.secret), secret_from="the test's"
    )
    try:
        asyncio.run(
            engine_init._run(
                _home(),
                given,
                environ=dict(os.environ),
                job=job,
                root_ssh=_reader(),
                asking=None,
                offer_target=target(pve_host),
            )
        )
    finally:
        _only_the_engine_s_names()
    assert pve_host.calls() == [] and not (pve_host.bin / "calls.log").exists()


def test_a_token_of_the_person_s_own_made_by_the_offer_records_the_engine_s_pool_and_roles_alone(
    fake, pve_host, job
) -> None:
    # Card E19 on the offer (review 55): host.yml names the person's own token, ops@pve!tools, which the offer makes
    # with its user and grants; made records the pool and the roles, the engine's own names, never the person's user,
    # token or a grant for either.
    with_port(fake.port)
    layout = hosts.Layout(_home())
    own = {"token_user": OWN_USER, "token_id": OWN_TOKEN}
    hosts.write(layout, "pve", layout.state("pve").digest, change=lambda raw: raw.update(own))
    _offered(fake, _Person({SET_UP: engine_init.YES}), job, pve_host)
    host = _engine_s(record.read(_home()).fleet, user=OWN_USER, token=OWN_TOKEN)
    roles = {("role", obj.name) for obj in access.objects(host) if isinstance(obj, access.Role)}
    assert {(each.what, each.id) for each in _reached().fields.made} == {("pool", ENGINE_POOL), *roles}
    assert f"{OWN_USER}!{OWN_TOKEN}" in _state_of(pve_host)["tokens"]
    assert _revealed(_opened()["token@pve"]) == fake.secret


@pytest.mark.parametrize("case", ["y", "y-and-the-vault-refuses", "n"])
def test_proxmox_is_changed_only_by_the_script_after_a_y_and_by_deleting_the_token_the_run_made(
    fake, pve_host, job, monkeypatch, case
) -> None:
    # The invariant, carried from tests/unit/test_init_settings.py's: no pveum that changes the host runs before the
    # y; after it, each is a command the question showed, or the delete of the token the run made.
    with_port(fake.port)
    if case == "y-and-the-vault-refuses":
        monkeypatch.setattr(sealed, "put", _vault_refuses)
    at_the_question: list[int] = []

    def answered(_text: str) -> str:
        at_the_question.append(len(_pve_cli_writes(pve_host.bin)))
        return engine_init.NO if case == "n" else engine_init.YES

    person = _Person({SET_UP: answered})
    with contextlib.suppress(engine_init.Refused):
        _offered(fake, person, job, pve_host)
    assert at_the_question == [0]
    [question] = person.question(SET_UP)
    shown = {line.strip() for line in question.splitlines() if line.startswith("  pveum ")}
    deleted = f"pveum user token delete {ENGINE_USER} {ENGINE_TOKEN}"
    calls = _pve_cli_calls(pve_host.bin)
    writes = [shlex.join(each) for each in calls if each[0] == "pveum" and each[1:3] != ["user", "permissions"]]
    assert all(each in shown or each == deleted for each in writes), (writes, shown)
    assert (deleted in writes) == (case == "y-and-the-vault-refuses")
    assert bool(writes) == (case != "n")


def _jobs() -> list[str]:
    folder = paths.jobs_dir(_home())
    return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []


def test_print_commands_prints_the_script_the_offer_runs_and_writes_nothing(fake, secret_file, tmp_path) -> None:
    fleet = _first(fake, secret_file, *EVERY)
    files, made, sent = _files(), _jobs(), len(fake.calls())
    ran = _init(fake, "--print-commands")
    assert ran.returncode == 0, ran
    host = _engine_s(fleet)
    assert ran.stdout == access.as_script(host, "", host_flag="")
    said_now = " ".join(ran.stderr.split())
    assert "--print-commands | ssh root@127.0.0.1 sh -s -- --dry-run to see each command" in said_now, said_now
    # No request, no job, no file written.
    assert (len(fake.calls()), _jobs(), _files()) == (sent, made, files)
    # Run by hand with --dry-run on a host that holds nothing yet: a command for each object of E1's list.
    pve = _pve_cli(tmp_path / "by-hand")
    dry = subprocess.run(
        ["/bin/sh", "-s", "--", "--dry-run"],
        input=ran.stdout,
        env={"PATH": os.pathsep.join([str(pve), "/usr/bin", "/bin"]), "LC_ALL": "C"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert dry.returncode == 0, dry.stderr
    assert len(dry.stdout.splitlines()) == len(access.objects(host)), dry.stdout
    # The script makes the token: the secret's file has no place beside it.
    ran = _init(fake, "--print-commands", "--token-secret-file", str(secret_file))
    assert ran.returncode == 1 and "it takes no --token-secret-file" in ran.stderr, ran
    assert _files() == files


def test_print_commands_with_no_fleet_json_writes_the_id_it_renders_and_a_second_print_agrees(
    fake, secret_file
) -> None:
    # Card E8, the coordinator's ruling on review 72's Later medium: its one write, said, so that two prints and the
    # offer render one ID and a by-hand run again after a partial one is never refused as another fleet's.
    fleet = _first(fake, secret_file, *EVERY)
    name = str(record.path(_home()).relative_to(_home().root))
    record.path(_home()).unlink()
    files = _files()
    first = _init(fake, "--print-commands")
    assert first.returncode == 0, first
    written = record.read(_home()).fleet
    assert written != fleet
    assert first.stdout == access.as_script(_engine_s(written), "", host_flag="")
    assert f"Wrote {name}: the fleet {written}, whose ID this script marks the pool with" in (
        " ".join(first.stderr.split())
    ), first.stderr
    second = _init(fake, "--print-commands")
    assert (second.returncode, second.stdout) == (0, first.stdout), second
    assert "Wrote" not in second.stderr
    assert set(_files()) == {*files, name}
    assert {each: data for each, data in _files().items() if each != name} == files


# ---------------------------------------------------------------- the pool's mark on the offer (card E8) ----

# The questions card E8 adds: which of two things is meant, and the other pool's name and token.
WHICH, AGAIN = engine_init.WHICH, engine_init.AGAIN
POOL_LABEL, TOKEN_LABEL = "Pool:", "API token ID:"
NOTE = "the person's own pool"
CONTROLLER = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"


def _another_fleet_s(host: Machine, fleet: str, controller: str = mark.NONE) -> bytes:
    """The stand-in host set up by another fleet's script: its pool and its token marked as that fleet's, the token
    the engine's own ID, its secret held by that fleet alone. The state's bytes, as they are now."""
    line = mark.Mark(fleet, controller).line
    state = _state_of(host)
    _state_of(
        host,
        pools={ENGINE_POOL: {"comment": line}},
        users={**state["users"], ENGINE_USER: {}},
        tokens={ENGINE_FULL: {"privsep": 1, "secret": OTHER, "comment": line}},
    )
    return (host.bin / "pve-state.json").read_bytes()


@pytest.mark.parametrize("answer", [engine_init.YES, engine_init.NO])
def test_an_unmarked_pool_named_is_shown_and_taken_on_a_y_alone_its_comment_kept(fake, pve_host, job, answer) -> None:
    # The safety rule (R2 C-1): a pool nobody marked is taken only where the person named it and said y to the
    # question that shows its comment and its members; the mark is added at the comment's end, the rest kept.
    with_port(fake.port)
    _state_of(
        pve_host,
        pools={ENGINE_POOL: {"comment": NOTE}},
        guests=[{"vmid": 150, "pool": ENGINE_POOL, "name": "keep-me"}],
    )
    before = (pve_host.bin / "pve-state.json").read_bytes()
    person = _Person({SET_UP: answer})
    given = engine_init.Given(certificate=trust.pem_fingerprint(fake.pki["node"]), pool=ENGINE_POOL)
    _offered(fake, person, job, pve_host, given)
    [question] = person.question(SET_UP)
    assert f"comment: {NOTE}" in question and "qemu/150 keep-me" in question, question
    taking = ["/bin/sh -s -- --dry-run --pool aw-fleet --take-unmarked"]
    if answer != engine_init.YES:
        assert _ran(pve_host) == [*READS[:-1], *taking]
        assert (pve_host.bin / "pve-state.json").read_bytes() == before
        return
    assert _ran(pve_host) == [*READS[:-1], *taking, "/bin/sh -s -- --pool aw-fleet --take-unmarked"]
    fleet = record.read(_home()).fleet
    comment = _state_of(pve_host)["pools"][ENGINE_POOL]["comment"]
    assert comment == f"{NOTE} {mark.Mark(fleet, mark.NONE).line}"
    assert _state_of(pve_host)["guests"] == [{"vmid": 150, "pool": ENGINE_POOL, "name": "keep-me"}]
    # Review 72: a pool taken is not one the engine made, so made never records it.
    assert ("pool", ENGINE_POOL) not in {(each.what, each.id) for each in _reached().fields.made}
    assert access.plan(access.objects(_engine_s(fleet)), _found(_state_of(pve_host)), host_flag="").steps == ()


def test_an_unmarked_pool_not_named_is_never_taken_and_both_ways_are_said(fake, pve_host, job) -> None:
    with_port(fake.port)
    _state_of(pve_host, pools={ENGINE_POOL: {"comment": NOTE}})
    before = (pve_host.bin / "pve-state.json").read_bytes()
    person = _Person({SET_UP: engine_init.YES, WHICH: engine_init.TAKE})
    _offered(fake, person, job, pve_host)
    assert person.question(SET_UP) == [] and person.question(WHICH) == []
    assert _ran(pve_host) == READS[:-1]
    assert (pve_host.bin / "pve-state.json").read_bytes() == before
    said_now = " ".join(person.said().split())
    assert "The pool aw-fleet on 127.0.0.1 carries no fleet's mark in its comment" in said_now, said_now
    assert "init --pool aw-fleet shows its comment and its members and asks" in said_now, said_now
    assert "--print-commands --pool aw-fleet | ssh root@127.0.0.1 sh -s -- --pool aw-fleet --take-unmarked" in said_now
    assert len(person.question(SECRET_LABEL)) == 1


@pytest.mark.parametrize("answer", [engine_init.TAKE, "", None], ids=["take", "enter", "esc"])
def test_a_pool_another_fleet_marked_is_taken_over_only_when_chosen(fake, pve_host, job, tmp_path, answer) -> None:
    # The safety rule: a marked pool is never taken over unasked. Taken over, the token is made again, so that the
    # other copy of its secret stops working, and fleet.json takes the mark's ID.
    with_port(fake.port)
    other = record.new_id()
    before = _another_fleet_s(pve_host, other)
    person = _Person({WHICH: answer, SET_UP: engine_init.YES} if answer is not None else {SET_UP: engine_init.YES})
    _offered(fake, person, job, pve_host)
    [which] = person.question(WHICH)
    assert f"carries the mark of the fleet {other}" in which and "init does neither of these unasked" in which
    assert f"the token {ENGINE_FULL} is made again" in which and "use another pool" in which
    if answer != engine_init.TAKE:
        assert person.question(SET_UP) == []
        assert _ran(pve_host) == READS[:-1]
        assert (pve_host.bin / "pve-state.json").read_bytes() == before
        assert "Neither was chosen, so nothing was made on 127.0.0.1." in person.said()
        assert len(person.question(SECRET_LABEL)) == 1
        return
    [question] = person.question(SET_UP)
    assert "there, its secret made again: every other copy of it stops working" in question, question
    assert _ran(pve_host) == [*READS[:-1], "/bin/sh -s -- --dry-run --regenerate", "/bin/sh -s -- --regenerate"]
    state = _state_of(pve_host)
    assert state["tokens"][ENGINE_FULL]["secret"] == fake.secret != OTHER
    assert state["pools"][ENGINE_POOL]["comment"] == mark.Mark(other, mark.NONE).line
    assert record.read(_home()).fleet == other
    assert access.plan(access.objects(_engine_s(other)), _found(state), host_flag="").steps == ()
    assert {entry: _revealed(value) for entry, value in _opened().items()} == {"token@pve": fake.secret}
    assert person.question(SECRET_LABEL) == []
    _nothing_of(fake.secret, tmp_path, person, pve_host)


def test_another_pool_makes_a_fleet_of_this_computer_s_own_beside_the_other_with_its_own_token(
    fake, pve_host, job
) -> None:
    # Two fleets never share a token (E1's point 1, ruled 2026-11-24): the second's is asked, and the user is shared.
    with_port(fake.port)
    other = record.new_id()
    _another_fleet_s(pve_host, other)
    person = _Person(
        {
            WHICH: engine_init.ANOTHER,
            POOL_LABEL: "dev-vms",
            TOKEN_LABEL: f"{ENGINE_USER}!second",
            SET_UP: engine_init.YES,
        }
    )
    _offered(fake, person, job, pve_host)
    assert len(person.question(POOL_LABEL)) == 1 and len(person.question(TOKEN_LABEL)) == 1
    pooled = shlex.join(pve_script.pool_read("dev-vms"))
    assert _ran(pve_host) == [*READS[:-1], pooled, "/bin/sh -s -- --dry-run", "/bin/sh -s --"]
    fleet = record.read(_home()).fleet
    assert fleet != other
    state = _state_of(pve_host)
    assert state["pools"] == {
        ENGINE_POOL: {"comment": mark.Mark(other, mark.NONE).line},
        "dev-vms": {"comment": mark.Mark(fleet, mark.NONE).line},
    }
    # The other fleet's token is as it was; this fleet's is its own, marked as this fleet's.
    assert state["tokens"][ENGINE_FULL] == {"privsep": 1, "secret": OTHER, "comment": mark.Mark(other, mark.NONE).line}
    assert state["tokens"][f"{ENGINE_USER}!second"]["comment"] == mark.Mark(fleet, mark.NONE).line
    fields = _reached().fields
    assert (fields.token_user, fields.token_id) == (ENGINE_USER, "second")
    assert overrides.read(_home(), "pve").host.settings["proxmox_pool"] == "dev-vms"


def test_this_fleet_s_own_pool_with_its_token_there_and_no_secret_here_asks_before_the_token_is_made_again(
    fake, pve_host, job
) -> None:
    # A second computer with the fleet's folder and not its vault: the token is this fleet's, and its secret is held
    # nowhere here, so the take-over is asked, never done unasked.
    with_port(fake.port)
    fleet = record.new_id()
    record.write(_home(), record.first(fleet))
    before = _another_fleet_s(pve_host, fleet)
    nothing = _Person({SET_UP: engine_init.YES})
    _offered(fake, nothing, job, pve_host)
    assert len(nothing.question(AGAIN)) == 1 and nothing.question(SET_UP) == []
    assert (pve_host.bin / "pve-state.json").read_bytes() == before
    # Review 72: the mark's ID is fleet.json's, so it is never offered as a take-over of what is this fleet's own.
    assert nothing.question(WHICH) == []
    person = _Person({AGAIN: engine_init.TAKE, SET_UP: engine_init.YES})
    _offered(fake, person, job, pve_host)
    [which] = person.question(AGAIN)
    assert "carries this fleet's own mark" in which and "take the fleet over" not in which, which
    assert _ran(pve_host)[-3:] == [POOL_READ, "/bin/sh -s -- --dry-run --regenerate", "/bin/sh -s -- --regenerate"]
    assert _state_of(pve_host)["tokens"][ENGINE_FULL]["secret"] == fake.secret
    assert record.read(_home()).fleet == fleet


def test_a_token_made_again_is_never_deleted_where_the_vault_cannot_be_written(
    fake, pve_host, job, monkeypatch
) -> None:
    # Review 72's note owed: the take-over made the token's secret again and never made the token, so the rescue leaves
    # it, with its grants; deleting it would leave the fleet with none. The next run asks to make its secret again.
    with_port(fake.port)
    other = record.new_id()
    _another_fleet_s(pve_host, other)
    monkeypatch.setattr(sealed, "put", _vault_refuses)
    person = _Person({WHICH: engine_init.TAKE, SET_UP: engine_init.YES})
    with pytest.raises(engine_init.Refused) as stopped:
        _offered(fake, person, job, pve_host)
    sentence = stopped.value.sentence
    assert "the vault could not be written (No space left on device)" in sentence, sentence
    assert f"the token {ENGINE_FULL} was there before this run, which made its secret again" in sentence, sentence
    assert not any("token delete" in each for each in _pve_cli_writes(pve_host.bin))
    assert _state_of(pve_host)["tokens"][ENGINE_FULL]["secret"] == fake.secret
    assert ("token", ENGINE_FULL) not in {(each.what, each.id) for each in _reached().fields.made}


def test_a_token_a_take_over_adds_is_deleted_where_the_vault_cannot_be_written(
    fake, pve_host, job, monkeypatch, tmp_path
) -> None:
    # Review 76, 1 (its probe P1): the other fleet's token is gone, so --regenerate adds it; that token is the run's
    # own, deleted as E7 deletes one, and only a token the dry run made again (token modify) is spared.
    with_port(fake.port)
    _another_fleet_s(pve_host, record.new_id())
    _state_of(pve_host, tokens={})
    monkeypatch.setattr(sealed, "put", _vault_refuses)
    person = _Person({WHICH: engine_init.TAKE, SET_UP: engine_init.YES})
    with pytest.raises(engine_init.Refused) as stopped:
        _offered(fake, person, job, pve_host)
    sentence = stopped.value.sentence
    [question] = person.question(SET_UP)
    assert f"pveum user token add {ENGINE_USER} {ENGINE_TOKEN}" in question, question
    assert f"the token {ENGINE_FULL} this run made was deleted on 127.0.0.1 again" in sentence, sentence
    assert "was there before this run" not in sentence
    assert _state_of(pve_host)["tokens"] == {}
    assert _ran(pve_host)[-1] == f"pveum user token delete {ENGINE_USER} {ENGINE_TOKEN}"
    assert ("token", ENGINE_FULL) not in {(each.what, each.id) for each in _reached().fields.made}
    _nothing_of(fake.secret, tmp_path, person, pve_host, sentence)


@pytest.mark.parametrize("race", ["deleted after a modify", "made after an add"])
def test_the_rescue_reads_the_run_s_own_token_line_where_the_host_changed_while_the_question_waited(
    fake, pve_host, job, monkeypatch, race
) -> None:
    # Review 76b, 1b (its probes Q1 and Q2): the script decides again at the y. A token deleted after a dry run that
    # made its secret again is added by the run, the run's own, and deleted; one made with the fleet's mark after a dry
    # run that added it has its secret made again by the run, was there before, and is never deleted.
    with_port(fake.port)
    other = record.new_id()
    _another_fleet_s(pve_host, other)
    if race == "made after an add":
        _state_of(pve_host, tokens={})
    line = mark.Mark(other, mark.NONE).line

    def y(_text: str) -> str:
        there = {ENGINE_FULL: {"privsep": 1, "secret": OTHER, "comment": line}}
        _state_of(pve_host, tokens={} if race == "deleted after a modify" else there)
        return engine_init.YES

    monkeypatch.setattr(sealed, "put", _vault_refuses)
    person = _Person({WHICH: engine_init.TAKE, SET_UP: y})
    with pytest.raises(engine_init.Refused) as stopped:
        _offered(fake, person, job, pve_host)
    sentence = stopped.value.sentence
    [question] = person.question(SET_UP)
    deleted = f"pveum user token delete {ENGINE_USER} {ENGINE_TOKEN}"
    if race == "deleted after a modify":
        assert f"pveum user token modify {ENGINE_USER} {ENGINE_TOKEN} --regenerate" in question, question
        assert f"the token {ENGINE_FULL} this run made was deleted on 127.0.0.1 again" in sentence, sentence
        assert _state_of(pve_host)["tokens"] == {} and _ran(pve_host)[-1] == deleted
    else:
        assert f"pveum user token add {ENGINE_USER} {ENGINE_TOKEN}" in question, question
        assert f"the token {ENGINE_FULL} was there before this run, which made its secret again" in sentence, sentence
        assert _state_of(pve_host)["tokens"][ENGINE_FULL]["secret"] == fake.secret
        assert not any("token delete" in each for each in _pve_cli_writes(pve_host.bin))
    assert ("token", ENGINE_FULL) not in {(each.what, each.id) for each in _reached().fields.made}


@pytest.mark.parametrize("pool_set", [False, True], ids=["no pool set", "proxmox_pool set"])
def test_a_take_over_is_never_offered_where_fleet_json_names_another_fleet(fake, pve_host, job, pool_set) -> None:
    # Review 76, 2 (its probe P2): fleet.json names fleet B and the pool carries fleet A's mark: no take-over, which
    # would leave fleet.json B's against A's mark; another pool alone, or, with the pool set, nothing asked and the
    # mark's rule said.
    with_port(fake.port)
    mine, theirs = record.new_id(), record.new_id()
    record.write(_home(), record.first(mine))
    if pool_set:
        overrides.set(_home(), "proxmox_pool", ENGINE_POOL, "pve", overrides.read(_home(), "pve").host.digest)
    before = _another_fleet_s(pve_host, theirs)
    person = _Person({SET_UP: engine_init.YES})
    _offered(fake, person, job, pve_host)
    assert person.question(AGAIN) == [] and person.question(SET_UP) == []
    if pool_set:
        assert person.question(WHICH) == []
        assert f"the pool's mark names another fleet, {theirs}, where this computer's folder is the fleet {mine}'s" in (
            " ".join(person.said().split())
        ), person.said()
    else:
        [which] = person.question(WHICH)
        assert "take the fleet over" not in which and "use another pool" in which, which
    assert (pve_host.bin / "pve-state.json").read_bytes() == before
    assert record.read(_home()).fleet == mine


def test_a_pool_whose_mark_names_a_control_vm_is_refused_and_nothing_asked(fake, pve_host, job) -> None:
    # This fleet's mark, naming a control VM (D2): no question, the script's refusal said.
    with_port(fake.port)
    fleet = record.new_id()
    record.write(_home(), record.first(fleet))
    before = _another_fleet_s(pve_host, fleet, CONTROLLER)
    person = _Person({WHICH: engine_init.TAKE, SET_UP: engine_init.YES})
    _offered(fake, person, job, pve_host)
    assert person.question(WHICH) == [] and person.question(SET_UP) == []
    assert f"this fleet has a control VM ({CONTROLLER}): change it there, or retire it" in person.said()
    assert (pve_host.bin / "pve-state.json").read_bytes() == before


# ---------------------------------------------------------------- the rules held by scans ----


# What writes, moves or removes a file, by the name a call ends in, or as it is spelled: none of them is init's own.
WRITES = frozenset({"write_text", "write_bytes", "unlink", "rename", "rmtree", "remove", "chmod", "mkdir", "makedirs"})
MOVES = frozenset({"os.replace", "store.replace", "shutil.move", "shutil.copy", "shutil.copyfile"})


def test_init_writes_nothing_but_through_the_fleet_s_writers_and_puts_the_secret_in_the_vault_alone() -> None:
    tree = ast.parse(INIT.read_text())
    called = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    names = [getattr(node.func, "attr", getattr(node.func, "id", "")) for node in called]
    spelled = {ast.unparse(node.func) for node in called}
    # No file is written, moved or removed here: each is its one writer's (fleet/'s, and the key's, vaultkey.make_key).
    assert WRITES.isdisjoint(names) and MOVES.isdisjoint(spelled), (WRITES & set(names), MOVES & spelled)
    # The one open here reads, in binary, a bounded part of a file: the secret's, or a public key's (card E5).
    opened = [node for node in called if isinstance(node.func, ast.Name) and node.func.id == "open"]
    assert [ast.literal_eval(node.args[1]) for node in opened] == ["rb"]
    # A secret's one way into the vault, the token's comparison with the one held there, and a hidden answer held to its
    # kind (card E5): never sent on, written or said.
    assert names.count("put") == 1 and names.count("reveal") == 3


# The new engine's folders no question may read a terminal of its own in (card E5): the engine's and the fleet's.
ASKING_FOLDERS = ("lib/claude_vm/engine", "lib/claude_vm/fleet")
# What reads a terminal, or the standard input, by itself: never init's, whose every read is runs/ask.py's.
OWN_READS = frozenset({"input", "os.read", "sys.stdin.read", "sys.stdin.readline", "sys.stdin.buffer.read"})
TERMINAL_MODULES = frozenset({"getpass", "termios", "tty", "pty", "readline", "curses", "claude_vm.tui.choose"})


def test_no_code_of_the_engine_or_the_fleet_names_dev_tty_or_getpass() -> None:
    found = [
        str(path.relative_to(REPO))
        for folder in ASKING_FOLDERS
        for path in sorted((REPO / folder).rglob("*.py"))
        if "/dev/tty" in path.read_text() or "getpass" in path.read_text()
    ]
    assert found == []


def test_every_question_init_asks_goes_through_runs_ask() -> None:
    tree = ast.parse(INIT.read_text())
    imported = [
        (node.module or "", alias.name, node.level)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ]
    modules = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    modules |= {module.lstrip(".") for module, _, _ in imported}
    # ask is runs/ask.py's, imported by its whole name, and called in one place: _Asking.ask, which every question uses.
    assert [(module, level) for module, name, level in imported if name == "ask"] == [("claude_vm.runs.ask", 0)]
    # Whether anyone is there to ask is runs/ask.py's terminal(), which ask decides each question by: one function,
    # never a second copy of it here (review 54, E5.md 2), nor isatty alone.
    assert [(module, level) for module, name, level in imported if name == "terminal"] == [("claude_vm.runs.ask", 0)]
    named = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not {"isatty", "tcgetpgrp", "getpgrp"} & named, named
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    asking = [node for node in calls if isinstance(node.func, ast.Name) and node.func.id == "ask"]
    assert len(asking) == 1, [ast.unparse(node) for node in asking]
    method = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "_Asking")
    [ask] = [node for node in method.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "ask"]
    assert asking[0] in list(ast.walk(ask))
    # Nothing here reads the terminal or the standard input itself, or imports what does.
    assert OWN_READS.isdisjoint({ast.unparse(node.func) for node in calls})
    assert TERMINAL_MODULES.isdisjoint(modules) and not {"tui", "claude_vm.tui"} & modules, modules
