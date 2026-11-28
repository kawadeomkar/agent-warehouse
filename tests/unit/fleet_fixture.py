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
test that reaches VMs through the host sets the route itself; and the host's node, which no option gives either, and
which init's checks (card E6) settle only where they can tell it. init ends with those checks, which against the fake,
no whole host (the role on the pool alone, no template of the engine's, no SSH key), say problems: init then exits 1,
exactly where they said a `!!` line, and the folder is written all the same. A second host's init (a further host's,
card E10) leaves the settings the host sets for itself to its checks, written once every check has passed, so where
they did not pass the storage, the bridge and the pool given are written here too, each where the host's settings.yml
sets none, as the node is. What it returns says where each thing is:
the fleet's home, the host, the fleet's ID, the fake's address and port, the token's ID and secret (the fake's, made
for the test), and the fingerprint kept.

The folder is the test's own: AGENT_WAREHOUSE_HOME (tests/unit/conftest.py's aw_home), so a test calls it with the kit's
`home` fixture made. Nothing here reaches a network but the fake on loopback, nor this computer's ssh, ~/.ssh or
~/.claude.
"""

from __future__ import annotations

import dataclasses
import os
import re
from collections.abc import Iterable
from pathlib import Path

from aw_kit import run_aw
from aw_machine import ssh_stand_in
from harness import ENGINE_POOL, ENGINE_TOKEN, ENGINE_USER, REPO, _pve, engine_token

from claude_vm import trust
from claude_vm.engine.init_checks import PREFIX
from claude_vm.engine.init_checks import PROBLEM as PROBLEM_STATE
from claude_vm.engine.record import Record
from claude_vm.fleet import hosts, mark, overrides, paths, record
from claude_vm.pve import record as block

# The host init sets up, unless a test names another; and its node, storage and bridge, the fake's.
HOST = "pve"
# The setting every fleet made here pins to direct, for each host: this computer's own, in client.yml's map hosts.
ROUTE = "vm_ssh_route"
NODE = "pve"
STORAGE = "local-lvm"
BRIDGE = "vmbr0"
# The prefix of a line of init's checks that says a problem for deploy.
PROBLEM = PREFIX[PROBLEM_STATE]


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


def recorded(
    base: str,
    vmids: Iterable[int],
    *,
    pool: str = ENGINE_POOL,
    node: str = NODE,
    down: Iterable[int] = (),
    **fields: object,
) -> dict[int, str]:
    """Each VM of vmids in the fake at base made a member of pool, its notes given the engine's record of it as a
    finished deploy leaves one - its kind, its SMBIOS UUID, its name, the stage finished and provisioned - with fields
    beside them (engine/record.py's Record): what the fleet's own VMs are to a reader of the pool. Each of down, a VM on
    a node that is down (whole_fleet's nameless 8001, on pve2), made a member as it is, since nothing of it can be read
    or written. Each recorded one's UUID, by its VMID."""
    uuids = {}
    for vmid in vmids:
        config = _pve(base, "GET", f"nodes/{node}/qemu/{vmid}/config")["data"]
        found = re.search(r"(?:^|,)uuid=([^,]+)", str(config.get("smbios1", "")))
        assert found, f"VM {vmid} of the fake has no SMBIOS UUID"
        made = Record(kind="vm", uuid=found[1], name=str(config["name"]), stage="finished", provisioned=True, **fields)
        notes = block.render(str(config.get("description", "")), made.fields())
        _pve(base, "PUT", f"nodes/{node}/qemu/{vmid}/config", description=notes)
        uuids[vmid] = found[1]
    listed = _pve(base, "GET", "pools", poolid=pool)["data"][0]["members"]
    joining = [vmid for vmid in [*uuids, *down] if vmid not in {member.get("vmid") for member in listed}]
    if joining:
        _pve(base, "PUT", "pools", poolid=pool, vms=",".join(str(vmid) for vmid in joining))
    return uuids


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
    assert ran.returncode == (1 if PROBLEM in ran.stdout else 0), ran
    where = paths.home(REPO)
    for setting, value in (
        ("proxmox_node", node),
        ("proxmox_storage", storage),
        ("proxmox_bridge", bridge),
        ("proxmox_pool", ENGINE_POOL),
    ):
        own = overrides.read(where, host).host
        if own is None or own.settings.get(setting) in (None, ""):
            digest = own.digest if own is not None else overrides.MISSING
            overrides.set(where, setting, value, host, digest)
    return Fleet(where.root, host, fleet, base, port, token, secret, fingerprint)
