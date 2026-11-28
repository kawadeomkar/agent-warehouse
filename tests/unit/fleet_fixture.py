"""A whole fleet folder for a test of the new engine, in one call: made by `aw init` with nobody there (card E4), as a
person's scripted first run makes it, against the fake Proxmox (tests/molecule/fake_pve_api.py) that a test started with
harness.fake_fleet(..., engine=True) and a certificate of tests/unit/pki.py's.

`whole_fleet(fake, pki)` does what the administrator's printed commands leave a host with - the engine's pool, role,
user and token, which fake_fleet made, and the pool's mark with a fleet ID the host made - then runs `aw init
--no-prompt` through the kit (aw_kit.run_aw) with the token's secret from a file of the test's own, `--certificate`
the node's certificate's fingerprint, the storage and the bridge, and a refusing ssh first on PATH, so that root SSH,
which init tries for the host's certificates where nothing else vouches for them, never answers and never reaches this
computer's ssh. Two things are written before and after it, each through its one writer: host.yml's `api_port`, the
fake's port, which no option of init's gives (a person with a port of their own writes it so), and with it the route
to the host's VMs pinned to `vm_ssh_route: direct` (`pinned`), before init or anything else reads it, as every fleet a
test makes pins it: under auto a run would ask this computer's ssh where it dials the host (engine/routes.py), and a
test that reaches VMs through the host sets the route itself; and the host's node, which init's checks settle on the
host (card E6) and which no option gives either. What it returns says where each thing is: the fleet's home, the host,
the fleet's ID, the fake's address and port, the token's ID and secret (the fake's, made for the test), and the
fingerprint kept.

The folder is the test's own: AGENT_WAREHOUSE_HOME (tests/unit/conftest.py's aw_home), so a test calls it with the kit's
`home` fixture made. Nothing here reaches a network but the fake on loopback, nor this computer's ssh, ~/.ssh or
~/.claude.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

from aw_kit import run_aw
from aw_machine import ssh_stand_in
from harness import ENGINE_POOL, ENGINE_TOKEN, ENGINE_USER, REPO, _pve, engine_token

from claude_vm import trust
from claude_vm.fleet import hosts, mark, overrides, paths, record

# The host init sets up, unless a test names another; and its node, storage and bridge, the fake's.
HOST = "pve"
# The setting every fleet made here pins to direct, for each host: this computer's own, in client.yml's map hosts.
ROUTE = "vm_ssh_route"
NODE = "pve"
STORAGE = "local-lvm"
BRIDGE = "vmbr0"


@dataclasses.dataclass(frozen=True)
class Fleet:
    """A fleet folder whole_fleet made: its home, its host's name, the fleet's ID (the pool's mark's), the fake's
    address (base) and port, the token's ID and its secret (the fake's, a test's own), and the SHA-256 fingerprint of
    the node's certificate the host keeps as its anchor."""

    home: Path
    host: str
    fleet: str
    base: str
    port: int
    token: str
    secret: str
    fingerprint: str


def mark_pool(base: str, fleet: str, pool: str = ENGINE_POOL, controller: str = mark.NONE) -> None:
    """The pool's comment in the fake at base given the fleet's mark, as the administrator's script writes it."""
    _pve(base, "PUT", "pools", poolid=pool, comment=mark.Mark(fleet, controller).line)


def pinned(host: str = HOST) -> None:
    """This computer's route to host's VMs pinned to vm_ssh_route: direct, in client.yml's map hosts, through its one
    writer: what every fleet a test makes does before anything reads the route (tests/unit/test_aw_routes.py holds
    each maker to it)."""
    where = paths.home(REPO)
    overrides.set(where, ROUTE, "direct", host, overrides.read(where, None).client.digest)


def with_port(port: int, host: str = HOST, address: str = "127.0.0.1") -> None:
    """host.yml of a host not set up yet, written through its one writer with the fake's port, the engine's token's
    user and ID and address: what a person writes for an API on a port of its own, before init; and the route to its
    VMs pinned (pinned), before init reads anything."""
    layout = hosts.Layout(paths.home(REPO))
    fields = {"address": address, "api_port": port, "token_user": ENGINE_USER, "token_id": ENGINE_TOKEN}
    hosts.write(layout, host, layout.state(host).digest, change=lambda raw: raw.update(fields))
    pinned(host)


def refusing_ssh(home: Path) -> Path:
    """A folder of the test's own whose ssh answers no address (tests/unit/aw_machine.py's stand-in with no machine):
    first on a run's PATH, root SSH to the host never answers, and this computer's ssh is never reached."""
    return ssh_stand_in(home.parent / "no-machines", [])


def whole_fleet(
    home: Path,
    base: str,
    directory: Path,
    pki: dict,
    *,
    host: str = HOST,
    node: str = NODE,
    storage: str = STORAGE,
    bridge: str = BRIDGE,
    fleet: str | None = None,
) -> Fleet:
    """A whole fleet folder at home, made by aw init with nobody there against the fake at base, whose state is in
    directory and which serves pki's node certificate (harness.fake_fleet(directory, engine=True,
    certificate=(pki["cert"], pki["key"]))): see the module's docstring. fleet is the fleet's ID the pool's mark is
    given, a new one unless given: a second host of the fleet at home, called host, is given the fleet's own."""
    port = int(base.rsplit(":", 1)[1])
    fleet = fleet or record.new_id()
    mark_pool(base, fleet)
    with_port(port, host)
    token, secret = engine_token(directory)
    secret_file = home.parent / "token-secret"
    secret_file.write_text(secret + "\n")
    os.chmod(secret_file, 0o600)
    fingerprint = trust.pem_fingerprint(pki["node"])
    ran = run_aw(
        "init",
        "--no-prompt",
        "--host",
        host,
        "--token-secret-file",
        str(secret_file),
        "--certificate",
        fingerprint,
        "--storage",
        storage,
        "--bridge",
        bridge,
        home=home,
        first_on_path=(refusing_ssh(home),),
    )
    assert ran.returncode == 0, ran
    where = paths.home(REPO)
    overrides.set(where, "proxmox_node", node, host, overrides.read(where, host).host.digest)
    return Fleet(where.root, host, fleet, base, port, token, secret, fingerprint)
