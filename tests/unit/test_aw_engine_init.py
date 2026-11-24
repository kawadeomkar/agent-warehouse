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
under the test's home holds the fake secret in clear, and the vault on disk is sealed. And tests/unit/fleet_fixture.py,
which gives other tests a whole fleet folder in one call.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import dataclasses
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from aw_kit import aw_at_a_terminal, run_aw, searched_for
from aw_machine import unrouted
from fleet_fixture import mark_pool, refusing_ssh, whole_fleet, with_port
from harness import (
    ENGINE_POOL,
    ENGINE_TOKEN,
    ENGINE_USER,
    REPO,
    _executable,
    _json_lines,
    _pve,
    _pve_requests,
    engine_token,
    fake_fleet,
)
from pki import proxmox_pki

from claude_vm import route, trust
from claude_vm.engine import certificate
from claude_vm.engine import init as engine_init
from claude_vm.fleet import hosts, mark, overrides, paths, record, sealed, vaultkey
from claude_vm.hosts.model import Request
from claude_vm.pve import pin
from claude_vm.pve.client import Proxmox, Token
from claude_vm.schema import rules
from claude_vm.secret import Secret

INIT = REPO / "lib" / "claude_vm" / "engine" / "init.py"
# The host's two files, as a sentence names them.
HOST_FILE = "fleet/hosts/pve/host.yml"
HOST_SETTINGS = "fleet/hosts/pve/settings.yml"
# Every value a first run is given beside the secret and the certificate.
EVERY = ("--storage", "local-lvm", "--bridge", "vmbr0", "--github-user", "octocat", "--tailscale", "off")
# A secret the fake did not make: low in entropy, so that gitleaks takes it for no key.
OTHER = "00000000-0000-4000-8000-000000000000"
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
    return ran


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
    # host.yml, with the node's certificate as its anchor held to its fingerprint, and what the administrator made.
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
    assert (engine_init.TOKEN_USER, engine_init.TOKEN_ID, engine_init.POOL) == (ENGINE_USER, ENGINE_TOKEN, ENGINE_POOL)
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
            "error: the pool aw-fleet carries no fleet's mark in its comment, so it is not shown to be the pool the"
            " administrator's commands made for this fleet: with nobody there no pool is taken over, and nothing was"
            " written",
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
    reached = _reached()
    assert reached.anchor is None and reached.fields.trust_kind is None


def _unattended(given: engine_init.Given, root_ssh=None) -> list[str]:
    """init with nobody there, run in this process, its root SSH root_ssh."""
    return asyncio.run(engine_init.unattended(_home(), given, environ=dict(os.environ), root_ssh=root_ssh))


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
        (("--print-commands",), "error: aw init --print-commands is not built yet (card E7 builds it)"),
        (("--machines",), "error: aw init --machines is not built yet (card J14 builds it)"),
    ],
    ids=["check", "print-commands", "machines"],
)
def test_a_mode_another_card_builds_is_refused_by_name_and_nothing_is_made(fake, words, said) -> None:
    ran = _init(fake, "--no-prompt", *words)
    assert (ran.returncode, ran.stdout, ran.stderr) == (1, "", said + "\n")
    assert _untouched()


def test_at_a_terminal_without_no_prompt_the_questions_are_card_e5_s(fake) -> None:
    rc, output = aw_at_a_terminal("init", "--address", "127.0.0.1", home=_home().root, first_on_path=(fake.ssh,))
    said = output.decode(errors="replace")
    assert rc == 1 and "(card E5 builds them): give aw init --no-prompt and the values it needs" in said, said
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
    reached = _reached()
    assert (reached.host.name, reached.host.problem) == (made.host, "")
    assert record.read(_home()).fleet == made.fleet
    secret = _opened()["token@pve"]
    # The client, pinned as the host keeps it, reads the pool with the vault's secret.
    pinned = pin.kept(reached.anchor, reached.fields.fingerprint)
    with Proxmox(f"https://127.0.0.1:{made.port}", pinned, Token(made.token, secret)) as client:
        assert mark.parse(client.pool(ENGINE_POOL).comment) == mark.Mark(made.fleet, mark.NONE)
    _sealed(made.secret)


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
    # The one file opened here is read: the secret's, in binary.
    opened = [node for node in called if isinstance(node.func, ast.Name) and node.func.id == "open"]
    assert [ast.literal_eval(node.args[1]) for node in opened] == ["rb"]
    # The secret's one way into the vault, and its one comparison with the one held there.
    assert names.count("put") == 1 and names.count("reveal") == 2
