"""How a run reaches its host's machines (lib/claude_vm/engine/routes.py; docs/architecture/engine.md §16.4).

On a whole fleet folder (tests/unit/fleet_fixture.py) against the fake Proxmox, with the host's VMs and the host itself
stand-ins (tests/unit/aw_machine.py): the host the hop, at the fake's own address that host.yml names, its control
commands, its master and a forward through it played in the modes a test sets, and ssh -G answered by this computer's
own ssh given the test's own configuration. Nothing here reaches a real host. The hop's socket and the machines' are in
a short directory of the test's own, the person's home as the route reads it (the run's environment's HOME), since
pytest's own are too long for a socket's path.

Carried from tests/unit/test_vm_route.py, which stays as it is: each VM goes through the host after the product's own
options, its socket named with the hop, the login once at the run's start and one forward of its own to the first VM by
name; auto both ways, worked out at each run from where ssh dials the host; a host that cannot be the hop - its login
refused, forwarding prohibited, ssh failing to use it - stops the run before any VM is reached; a VM that says nothing
through the host is said and stops nothing; a VM that brings a jump of its own gets no hop, from its own options or the
person's ssh configuration, and a jump every VM is given puts the login off for good; a line that would lose the
product's options is refused before anything connects; a stale shared connection is made again. And the card's own:
B8's refusal of every route but direct is gone, host and auto now settling a hop; a configure through the stand-in hop
passes, layer 1's own ssh riding it, and with the hop refused stops before any connection to the VM; the route is this
computer's own for each host and never another's; only a Proxmox VM takes the hop; the connections' notes and the
route's line are said once; the shared connection is made sure of again and fails no machine. The rules with a test of
their own: every harness pins vm_ssh_route: direct, the new engine's fleet fixture among them; and no module of
engine/ but routes.py makes a Target, so every runner call there takes the arguments routes.py made.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import dataclasses
import ipaddress
import json
import os
import shutil
import tempfile
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
from aw_machine import LOOPBACK, Machine, hop, hop_modes, machine, ssh_stand_in, unrouted
from fleet_fixture import ROUTE, whole_fleet
from harness import REPO, fake_fleet, repo_files
from pki import proxmox_pki

from claude_vm import checks, route
from claude_vm.engine import fleet, routes
from claude_vm.fleet import hosts, overrides, paths
from claude_vm.guest import layer1
from claude_vm.hosts.model import Request
from claude_vm.runs import job as jobs
from claude_vm.runs import rundir
from claude_vm.ssh import connections, hostkeys, runner

ENGINE = REPO / "lib" / "claude_vm" / "engine"
ROUTES = ENGINE / "routes.py"
# Where ssh dials the host, as a test's own configuration sends it there: a tailnet's address, and a LAN's.
TAILNET = "100.101.102.103"
LAN = "198.51.100.4"
# The hop as a sentence names it: the host's name, and how ssh reaches it.
LABEL = f"pve (root@{LOOPBACK})"
PING = {"name": "ping", "ansible.builtin.ping": {}}


@pytest.fixture
def pki(tmp_path):
    return proxmox_pki(tmp_path / "pki", key_usage=True)


@pytest.fixture
def made_fleet(home, tmp_path, pki):
    """A whole fleet against the fake, its host pve at the fake's own address, its route pinned direct."""
    directory = tmp_path / "fake"
    directory.mkdir()
    for base in fake_fleet(directory, engine=True, certificate=(pki["cert"], pki["key"])):
        yield whole_fleet(home, base, directory, pki)


@pytest.fixture
def short() -> Iterator[Path]:
    """The person's home as the route reads it: short enough for the sockets' paths in its .ansible/cp."""
    directory = tempfile.mkdtemp(prefix="aw-", dir="/tmp")
    yield Path(directory)
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def made(home: Path) -> Iterator[jobs.Job]:
    """A job of this process's own with its run's directory, as the run that settles its route has; gone after."""
    job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True
    )
    rundir.make(job)
    yield job
    rundir.remove(job)
    jobs.drop(job)


@pytest.fixture
def heard(made: jobs.Job) -> list[jobs.Event]:
    """Every event the run emits."""
    found: list[jobs.Event] = []
    made.listen(found.append)
    return found


@pytest.fixture
def run(made: jobs.Job):
    """What a workflow's body is given that the route uses: its job, and its emit (runs/attached.py's Context)."""
    return types.SimpleNamespace(job=made, emit=made.emit)


@pytest.fixture
def addresses(monkeypatch) -> None:
    """A name's addresses, as the resolver gives them for an address written as one, and none for any other: what
    route.py's rule reads under auto, with no lookup of the network's."""

    def literal(name, limit=None):
        try:
            return [str(ipaddress.ip_address(str(name)))]
        except ValueError:
            return []

    monkeypatch.setattr(route, "addresses_of", literal)


@pytest.fixture
def stand_ins(tmp_path, engine_guard):
    """The hop, pve, at the fake's own address, and two VMs, alpha and beta, behind it; the stand-in ssh first on this
    test's PATH."""
    root = tmp_path / "machines"
    pve = hop(root, address=LOOPBACK)
    alpha, beta = machine(root, "alpha"), machine(root, "beta")
    folder = ssh_stand_in(root, [pve, alpha, beta])
    engine_guard.first_on_path(folder)
    return types.SimpleNamespace(pve=pve, alpha=alpha, beta=beta, folder=folder)


def _set(name: str, value: object, host: str = "pve", machine_name: str | None = None) -> None:
    """One setting written into its home's file, as `aw settings --value` writes it."""
    where = paths.home(REPO)
    layers = overrides.read(where, host)
    row_home = {"vm_ssh_route": layers.client, "vm_ssh_options": layers.client}.get(name, layers.host)
    overrides.set(where, name, value, host, row_home.digest, machine=machine_name)


@contextlib.contextmanager
def _host(short: Path, name: str = "pve") -> Iterator[fleet.Host]:
    """The run's host, as a run reaches it and opens it, its environment's HOME the short one."""
    where = paths.home(REPO)
    request = Request(host=name) if name != "pve" else Request()
    with fleet.connect(where, hosts.reach(hosts.Layout(where), request)) as opened:
        yield dataclasses.replace(opened, environ={**opened.environ, "HOME": str(short)})


def _config(text: str, *homes: Path) -> None:
    """The person's own ~/.ssh/config, in each home: the test's own, where route.py's ssh -G reads it, and the run's,
    where a machine's is read."""
    for each in (Path(os.environ["HOME"]), *homes):
        (each / ".ssh").mkdir(exist_ok=True)
        (each / ".ssh" / "config").write_text(text)


def _vm(made_machine: Machine, kind: str = fleet.KIND) -> fleet.Target:
    """made_machine as the fleet reads a VM of the pool: its name, identity and address, the VM user's account."""
    return fleet.Target(
        kind=kind,
        name=made_machine.name,
        identity=made_machine.identity,
        host="pve",
        node="pve",
        vmid=9020 + len(made_machine.name),
        user=made_machine.account,
        address=made_machine.address,
    )


def _outcomes(made_machine: Machine) -> list[str]:
    return [call["outcome"] for call in made_machine.calls()]


def _reports(heard: list[jobs.Event]) -> list[str]:
    return [event.text for event in heard if event.kind == "report"]


def _proxy(target: runner.Target) -> str:
    """The ProxyCommand ssh takes for target: the first given."""
    return next((value for option, value in route.ssh_proxies(runner.options(target)) if option == "ProxyCommand"), "")


async def _settled(run, short: Path, name: str = "pve") -> routes.Route:
    with _host(short, name) as host:
        return await routes.settle(run, host)


# ---------------------------------------------------------------- direct, auto and host ----


def test_direct_settles_without_asking_ssh_and_gives_no_machine_the_hop(made_fleet, short, run, heard, stand_ins):
    _set("vm_ssh_options", {"ServerAliveInterval": "5"})

    async def body() -> None:
        found = await _settled(run, short)
        assert (found.settled["route"], found.through, found.label, found.proxy_jump) == ("direct", False, "", "")
        [alpha] = await found.targets([_vm(stand_ins.alpha)])
        assert alpha.hop is None and alpha.given == (("ServerAliveInterval", "5"),)
        assert alpha.control_dir == short / ".ansible" / "cp"
        assert await found.ready() is None

    asyncio.run(body())
    # No ssh -G, no login, nothing to the host or to a machine: the route is said only where it goes through the host.
    assert unrouted(stand_ins.folder) == [] and stand_ins.pve.calls() == [] and stand_ins.alpha.calls() == []
    assert _reports(heard) == []


@pytest.mark.parametrize(("dialled", "settled"), [(LAN, "direct"), (TAILNET, "host")])
def test_auto_is_worked_out_at_each_run_from_where_ssh_dials_the_host(
    made_fleet, short, run, stand_ins, addresses, dialled, settled
):
    # auto, the default once nothing pins it: the host as its own ssh -G resolves it, a HostName of the person's
    # among it.
    _set(ROUTE, "auto")

    async def body() -> list[str]:
        _config(f"Host {LOOPBACK}\n  HostName {LAN}\n", short)
        first = await _settled(run, short)
        _config(f"Host {LOOPBACK}\n  HostName {dialled}\n", short)
        again = await _settled(run, short)
        return [first.settled["route"], again.settled["route"]]

    # Nothing is kept from one run to the next.
    assert asyncio.run(body()) == ["direct", settled]
    login = ["-O check", "a master, opened", "-O check"]
    assert _outcomes(stand_ins.pve) == (login if settled == "host" else [])
    assert [call["outcome"] for call in unrouted(stand_ins.folder)] == ["-G", "-G"]


def test_through_the_host_each_vm_takes_the_hop_after_the_product_s_own_options(
    made_fleet, short, run, heard, stand_ins
):
    # B8's refusal of every route but direct is gone: host settles a hop, and a machine takes it.
    _set(ROUTE, "host")
    alpha, beta = stand_ins.alpha, stand_ins.beta

    async def body() -> tuple[routes.Route, tuple[runner.Target, ...]]:
        found = await _settled(run, short)
        # The login at the run's start, before any machine is asked about.
        assert _outcomes(stand_ins.pve) == ["-O check", "a master, opened", "-O check"]
        made = await found.targets([_vm(beta), _vm(alpha)])
        again = await found.targets([_vm(alpha)])
        assert again == made[1:]
        assert (await found.ready()).ok
        return found, made

    found, (beta_at, alpha_at) = asyncio.run(body())
    cp = str(short / ".ansible" / "cp")
    hop_ = route.hop_settings(user="root", host=LOOPBACK, port=22, control_dir=cp, pinned=True)
    for made_machine, at in ((alpha, alpha_at), (beta, beta_at)):
        assert _proxy(at) == " ".join(route.hop_argv(hop_, made_machine.address, "accept-new"))
        words = runner.options(at)
        # After the product's own, which ssh keeps since they come first; the machine's socket named with the hop.
        known = next(place for place, word in enumerate(words) if word.startswith("UserKnownHostsFile="))
        assert known < words.index(f"ProxyCommand={_proxy(at)}")
        assert at.hop.name == os.path.basename(hop_["socket"])
        name = checks.vm_control_name(made_machine.identity, made_machine.address, at.hop.name)
        assert f"ControlPath={short / '.ansible' / 'cp' / name}-%r-%p" in words
    assert found.label == LABEL and found.proxy_jump == f"root@{LOOPBACK}"
    # One forward of its own, to the first VM by name, and no forward or open more; then the connection made sure of.
    assert _outcomes(stand_ins.pve) == [
        "-O check",
        "a master, opened",
        "-O check",
        f"forward {alpha.address}:22 unshared",
        "-O check",
    ]
    # Each asked of ssh -G before anything connects: what the person's configuration gives it, read once a run, then at
    # each call what its whole line takes.
    assert [call["outcome"] for call in unrouted(stand_ins.folder)] == ["-G"] * 5
    assert alpha.calls() == beta.calls() == []
    assert _reports(heard) == [
        f"VMs are reached through {LABEL}. A VM that does not answer shows as a closed connection; aw status says what"
        " the host reports for it."
    ]


# ---------------------------------------------------------------- a hop that cannot be used ----


@pytest.mark.parametrize(
    ("setting", "login", "forward", "said"),
    [
        ("host", "refused", "answer", f"through {LABEL} (vm_ssh_route: host), and ssh could not log in there"),
        ("auto", "refused", "answer", "set vm_ssh_route: direct: under auto, a host reached on a tailnet is a hop."),
        ("host", "open", "prohibited", f"{LABEL} refused to forward to alpha at "),
        ("host", "open", "failed", f"ssh could not use {LABEL} as a hop to alpha at "),
    ],
)
def test_a_host_that_cannot_be_the_hop_stops_the_run_before_any_vm_is_reached(
    made_fleet, short, run, stand_ins, addresses, setting, login, forward, said
):
    # A refused login stops the run as it starts; forwarding the host prohibits, or ssh failing to use it, at the first
    # forward of its own, before anything connects to a VM.
    _set(ROUTE, setting)
    _config(f"Host {LOOPBACK}\n  HostName {TAILNET}\n", short)
    hop_modes(stand_ins.pve, login=login, forward=forward)

    async def body() -> str:
        with pytest.raises(routes.Unusable) as refused:
            found = await _settled(run, short)
            await found.targets([_vm(stand_ins.alpha), _vm(stand_ins.beta)])
        return refused.value.sentence

    sentence = asyncio.run(body())
    assert said in sentence, sentence
    if forward == "prohibited":
        assert "(jump, in the host's host.yml)" in sentence and "vm_ssh_jump" not in sentence
    assert stand_ins.alpha.calls() == stand_ins.beta.calls() == []


@pytest.mark.parametrize(("forward", "said"), [("silent", "No answer from alpha at "), ("stalled", "The login to ")])
def test_a_vm_that_says_nothing_through_the_host_is_said_and_stops_nothing(
    made_fleet, short, run, heard, stand_ins, monkeypatch, forward, said
):
    _set(ROUTE, "host")
    hop_modes(stand_ins.pve, forward=forward)
    monkeypatch.setitem(route.LIMITS, "forward", 1)

    async def body() -> int:
        found = await _settled(run, short)
        return len(await found.targets([_vm(stand_ins.alpha)]))

    assert asyncio.run(body()) == 1
    [line] = [report for report in _reports(heard) if not report.startswith("VMs are reached")]
    assert line.startswith(said) and "within 1 s" in line, line


# ---------------------------------------------------------------- a jump of a machine's own ----


@pytest.mark.parametrize("where", ["options", "configuration"])
def test_a_vm_given_a_jump_of_its_own_gets_no_hop(made_fleet, short, run, heard, stand_ins, where):
    _set(ROUTE, "host")
    alpha, beta = stand_ins.alpha, stand_ins.beta
    if where == "options":
        _set("vm_ssh_options", {"ProxyJump": "jump.invalid"}, machine_name="alpha")
    else:
        _config(f"Host {alpha.address}\n  ProxyJump jump.invalid\n", short)

    async def body() -> tuple[runner.Target, ...]:
        found = await _settled(run, short)
        return await found.targets([_vm(alpha), _vm(beta)])

    alpha_at, beta_at = asyncio.run(body())
    assert alpha_at.hop is None and beta_at.hop is not None
    # The login at the start, and the forward to beta, the first by name that takes the hop.
    assert _outcomes(stand_ins.pve)[-1] == f"forward {beta.address}:22 unshared"
    jump = "ProxyJump jump.invalid" if where == "options" else routes.CONFIGURED
    assert f"alpha is reached by the jump it is given ({jump}), not through {LABEL}." in _reports(heard)


def test_a_jump_every_vm_is_given_puts_the_login_off_and_never_makes_it(made_fleet, short, run, stand_ins):
    _set(ROUTE, "host")
    _set("vm_ssh_options", {"ProxyJump": "jump.invalid"})

    async def body() -> tuple[runner.Target, ...]:
        found = await _settled(run, short)
        made = await found.targets([_vm(stand_ins.alpha), _vm(stand_ins.beta)])
        assert await found.ready() is None and found.proxy_jump == ""
        return made

    assert all(at.hop is None for at in asyncio.run(body()))
    assert stand_ins.pve.calls() == []


def test_a_line_that_would_lose_the_product_s_options_is_refused_before_anything_connects(
    made_fleet, short, run, stand_ins
):
    _set(ROUTE, "host")

    async def body() -> None:
        found = await _settled(run, short)
        # A person's options never outrank the product's: one that names what only the product sets is refused by
        # name, and a ProxyCommand none comes after the hop's, which ssh keeps.
        _set("vm_ssh_options", {"UserKnownHostsFile": "/dev/null"}, machine_name="alpha")
        with pytest.raises(routes.Refused, match="vm_ssh_options sets UserKnownHostsFile"):
            await found.targets([_vm(stand_ins.alpha)])
        _set("vm_ssh_options", {"ProxyCommand": "none"}, machine_name="beta")
        [beta] = await found.targets([_vm(stand_ins.beta)])
        assert beta.hop is not None and _proxy(beta).startswith("ssh ")
        # A Target the route did not make, its hop taken off: its own ssh would dial it from here, which held refuses.
        with pytest.raises(connections.Unheld, match="dialling it from here"):
            await found.connections.held(dataclasses.replace(beta, hop=None), found.settled)

    asyncio.run(body())
    assert stand_ins.alpha.calls() == stand_ins.beta.calls() == []


def test_only_a_proxmox_vm_takes_the_hop(made_fleet, short, run, stand_ins):
    _set(ROUTE, "host")

    async def body() -> list[object]:
        found = await _settled(run, short)
        return [at.hop for at in await found.targets([_vm(stand_ins.alpha, kind="machine"), _vm(stand_ins.beta)])]

    other, vm = asyncio.run(body())
    assert other is None and vm is not None


# ---------------------------------------------------------------- the shared connection ----


def test_a_stale_shared_connection_is_made_again_and_making_sure_fails_no_machine(made_fleet, short, run, stand_ins):
    _set(ROUTE, "host")
    cp = short / ".ansible" / "cp"
    socket = Path(route.hop_settings(user="root", host=LOOPBACK, port=22, control_dir=str(cp))["socket"])
    # The control directory as ssh's ControlPath and Ansible make it, its owner's alone, holding a socket file that
    # nothing listens at: a connection that ended without removing it.
    cp.mkdir(parents=True)
    cp.chmod(0o700)
    socket.write_text("stale")

    async def body() -> list[object]:
        found = await _settled(run, short)
        socket.unlink()
        opened = await found.ready()
        hop_modes(stand_ins.pve, login="refused")
        socket.unlink()
        gone = await found.ready()
        return [opened, gone]

    opened, gone = asyncio.run(body())
    assert (opened.ok, opened.opened) == (True, True)
    assert not gone.ok and "ssh could not log in there" in gone.said
    assert _outcomes(stand_ins.pve) == [
        "-O check",
        "a master, opened",
        "-O check",
        "-O check",
        "a master, opened",
        "-O check",
        "-O check",
        "refused: the login",
        "-O check",
    ]


def test_the_connections_notes_are_said_once_a_run(made_fleet, run, heard, stand_ins, tmp_path):
    # From a home whose control directory is too long for a socket's path: each connection logs in alone, said once.
    long_home = tmp_path / ("h" * 60)
    long_home.mkdir()

    async def body() -> tuple[runner.Target, ...]:
        found = await _settled(run, long_home)
        return await found.targets([_vm(stand_ins.alpha), _vm(stand_ins.beta)])

    assert all(not at.shared for at in asyncio.run(body()))
    [note] = _reports(heard)
    assert note.startswith(f"the control directory {long_home / '.ansible' / 'cp'} is too long"), note


@pytest.fixture
def nuc(home, tmp_path, made_fleet, pki):
    """A second host of the fleet, nuc, its own fake Proxmox, its route pinned direct as each host's is."""
    directory = tmp_path / "nuc"
    directory.mkdir()
    for base in fake_fleet(directory, engine=True, certificate=(pki["cert"], pki["key"])):
        yield whole_fleet(home, base, directory, pki, host="nuc", fleet=made_fleet.fleet)


def test_the_route_is_this_computer_s_own_for_each_host_and_never_another_s(made_fleet, nuc, short, run, stand_ins):
    # pve's through the host, in client.yml's map hosts under pve: nuc, which sets its own, never reads pve's.
    _set(ROUTE, "host")

    async def body() -> list[str]:
        return [(await _settled(run, short, name)).settled["route"] for name in ("pve", "nuc")]

    assert asyncio.run(body()) == ["host", "direct"]
    client = overrides.read(paths.home(REPO), "nuc").client
    assert {name: dict(own) for name, own in client.hosts.items()} == {"pve": {ROUTE: "host"}, "nuc": {ROUTE: "direct"}}
    assert ROUTE not in client.settings


# ---------------------------------------------------------------- a configure through the hop ----


def test_a_configure_through_a_stand_in_hop_passes_and_with_the_hop_refused_stops_before_the_vm(
    made_fleet, short, run, made, stand_ins, tmp_path, installed
):
    if not Path(layer1.PYTHON).is_file():
        pytest.skip(f"this machine has no {layer1.PYTHON}, the machine's interpreter the inventory names")
    _set(ROUTE, "host")
    alpha = stand_ins.alpha
    hostkeys.file(Path(runner.KNOWN_HOSTS).expanduser(), alpha.identity, hostkeys.parse(alpha.key))
    play = tmp_path / "play.yml"
    play.write_text(json.dumps([{"hosts": "all", "gather_facts": False, "tasks": [PING]}]))

    async def body() -> layer1.Ended:
        found = await _settled(run, short)
        [at] = await found.targets([_vm(alpha)])
        await found.ready()
        return await layer1.run(made, at, play, name="alpha", host_flag="", env=dict(os.environ))

    hop_modes(stand_ins.pve, login="refused")
    with pytest.raises(routes.Unusable, match="ssh could not log in there"):
        asyncio.run(body())
    assert alpha.calls() == []
    hop_modes(stand_ins.pve, login="open")
    ended = asyncio.run(body())
    assert ended.status == 0, ended.said
    # Layer 1's own ssh rode the hop's shared connection to the VM's address, and nothing dialled it from here.
    calls = [call for call in alpha.calls() if call["outcome"] == "ran"]
    assert calls and all(any(word.startswith("ProxyCommand=") for word in call["argv"]) for call in calls), calls
    assert f"forward {alpha.address}:22" in _outcomes(stand_ins.pve)


# ---------------------------------------------------------------- the rules ----


def test_every_harness_pins_the_route_direct(made_fleet, short):
    # auto would ask this computer's ssh, through the developer's own configuration, where it dials a test's host:
    # the new engine's fleet fixture pins direct for each host it makes, in client.yml's map hosts, as today's
    # harnesses pin it (tests/unit/test_vm_route.py holds those).
    where = paths.home(REPO)
    assert overrides.resolve(where, made_fleet.host)[ROUTE] == overrides.Value(ROUTE, "direct", overrides.CLIENT)
    with _host(short) as host:
        assert host.values[ROUTE].value == "direct"


# What makes a runner's Target: only routes.py of engine/ calls them, or names the connections that give one.
MAKERS = frozenset({"machine", "proxmox_host", "Target", "Hop"})


def _makes_a_target(source: str) -> list[str]:
    """Each place source makes a runner's Target, or takes what gives one: a call of runner.machine, proxmox_host,
    Target or Hop, by the module or by a name imported from it; an import of ssh/connections.py; a call of target_for
    or of Connections. What no scan sees: a name built at run time (getattr), and a dataclasses.replace of a Target
    handed in."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if module.endswith("runner") and alias.name in MAKERS:
                    found.append(f"line {node.lineno}: from {module} import {alias.name}")
                if module.endswith("connections") or (module.endswith("ssh") and alias.name == "connections"):
                    found.append(f"line {node.lineno}: imports connections")
        elif isinstance(node, ast.Import):
            found += [f"line {node.lineno}: imports connections" for a in node.names if a.name.endswith("connections")]
        elif isinstance(node, ast.Call):
            func = node.func
            owner = getattr(func.value, "id", "") if isinstance(func, ast.Attribute) else ""
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if (owner == "runner" and name in MAKERS) or name in ("target_for", "Connections"):
                found.append(f"line {node.lineno}: {owner + '.' if owner else ''}{name}")
    return found


def test_no_module_of_engine_but_routes_makes_a_target() -> None:
    files = [path for path in repo_files("lib/claude_vm/engine", untracked=True) if path.suffix == ".py"]
    assert ROUTES in files and ENGINE / "init.py" in files, files
    found = {path.name: where for path in files if path != ROUTES and (where := _makes_a_target(path.read_text()))}
    assert found == {}, "a machine's SSH arguments are routes.py's alone: take a Target from routes.targets"
    assert _makes_a_target(ROUTES.read_text())


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ('runner.machine(identity, address, "dev", control)', True),
        ('runner.proxmox_host(address, "root", control)', True),
        ("runner.Target(identity=None, address=a, account='root', control_dir=c)", True),
        ("runner.Hop(command, name)", True),
        ("from ..ssh.runner import machine", True),
        ("from claude_vm.ssh.runner import proxmox_host as host", True),
        ("from ..ssh import connections", True),
        ("from claude_vm.ssh.connections import Connections", True),
        ("import claude_vm.ssh.connections", True),
        ("await made.target_for(job, base, settled)", True),
        ("Connections(control, env)", True),
        ("await runner.run(job, target, command, None, limits, env)", False),
        ("from ..ssh import runner", False),
        ("target: runner.Target = at", False),
        ("dataclasses.replace(target, address=address)", False),
    ],
)
def test_the_target_scan_sees_each_way(source, found) -> None:
    assert bool(_makes_a_target(source)) is found
