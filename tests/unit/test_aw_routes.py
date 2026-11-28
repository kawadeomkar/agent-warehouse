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
route's line are said once; the shared connection is made sure of again and fails no machine. And review 46's (G7.md):
a route that cannot be settled - from a home too long for the hop's socket, or an ssh -G for the host past its limit,
ended by a signal or exiting with an error - stops the run with nothing reached; every machine is held before the hop's
login and the forward; a forward's verdict that stopped the run stops every later targets, and one cut before its
verdict is made again; and the runner connects to no machine's Target the route did not mark, nor to one that lost its
hop. And review 56's (G29.md): a forward's verdict that stopped the run stops a targets that waited on it too; and the
runner connects to no machine's Target whose address, account, port, ssh program or options changed since the route
marked it, straight, by a jump of its own or through the hop. The rules with a test of their own: every fleet a test
makes pins vm_ssh_route: direct before anything reads it, each maker read back; and no module of the new engine but
routes.py and ssh/'s own makes a Target, or changes one's route after it was made, by any name a scan can see, so every
runner call takes the arguments routes.py made.
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

import fleet_fixture
import pytest
from aw_machine import LOOPBACK, Machine, hop, hop_modes, machine, ssh_stand_in, unrouted
from fleet_fixture import ROUTE, whole_fleet, with_port
from harness import REPO, fake_fleet, repo_files
from pki import proxmox_pki
from test_aw_layout import NEW_MODULES

from claude_vm import checks, route
from claude_vm.engine import fleet, routes
from claude_vm.fleet import hosts, overrides, paths
from claude_vm.guest import layer1
from claude_vm.hosts.model import Request
from claude_vm.runs import job as jobs
from claude_vm.runs import rundir
from claude_vm.schema.words import VERBS
from claude_vm.ssh import connections, hostkeys, runner

LIB = REPO / "lib" / "claude_vm"
ENGINE = LIB / "engine"
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


# Each road on which the route cannot be settled (review 46, G7.md 1 and 3): through the host from a home too long for
# the hop's socket; and under auto, an ssh -G for the host that says nothing within its limit, one ended by a signal,
# and one that exits with an error - a line of the person's configuration it refuses for the host alone, a ControlPath
# with a token ssh does not know, which the hop's own lines outrank with their own, so the hop itself would have worked.
# Each: the setting given, the stand-in's mode for ssh -G, a line for the person's configuration, and what the sentence
# says.
REFUSED_LINE = "  ControlPath /tmp/%Q\n"
UNSETTLED = {
    "a home too long for the hop's socket": ("host", "", "", "would be longer than"),
    "ssh -G past its limit": ("auto", "slow-G", "", "said nothing of where ssh dials for it within 0.5 s"),
    "ssh -G ended by a signal": ("auto", "killed-G", "", "was ended by a signal (9) before it said where ssh dials"),
    "ssh -G exiting with an error": ("auto", "", REFUSED_LINE, "exited 255 without saying where ssh dials"),
}


@pytest.mark.parametrize("road", UNSETTLED)
def test_a_route_that_cannot_be_settled_stops_the_run_and_nothing_is_reached(
    made_fleet, short, run, stand_ins, addresses, monkeypatch, tmp_path, road
):
    # Settled direct from the address as written, each would dial the VMs where the person's configuration sends the
    # host to a tailnet: settle stops the run, the sentence naming vm_ssh_route, and nothing reaches the host or a VM.
    setting, mode, line, said = UNSETTLED[road]
    _set(ROUTE, setting)
    home = short
    if road == "a home too long for the hop's socket":
        home = tmp_path / ("h" * 60)
        home.mkdir()
    _config(f"Host {LOOPBACK}\n  HostName {TAILNET}\n{line}", home)
    if mode == "slow-G":
        (stand_ins.folder / mode).write_text("30")
        monkeypatch.setitem(route.LIMITS, "config", 0.5)
    elif mode:
        (stand_ins.folder / mode).write_text("")

    async def body() -> None:
        found = await _settled(run, home)
        await found.targets([_vm(stand_ins.alpha), _vm(stand_ins.beta)])

    with pytest.raises(routes.Unusable) as refused:
        asyncio.run(body())
    sentence = refused.value.sentence
    assert said in sentence and "vm_ssh_route: direct" in sentence, sentence
    assert stand_ins.pve.calls() == stand_ins.alpha.calls() == stand_ins.beta.calls() == []
    # Under auto, the one ssh -G for the host, and none for a VM; under host, none at all.
    assert [call["outcome"] for call in unrouted(stand_ins.folder)] == (["-G"] if setting == "auto" else [])


@pytest.mark.parametrize("login", ["made at the start", "put off"])
def test_every_machine_is_held_before_the_hop_s_login_and_the_forward(
    made_fleet, short, run, stand_ins, monkeypatch, login
):
    # held, then the login, then the forward (review 46, G7.md 4): a machine whose own ssh would not go where the route
    # says stops the run before the hop is logged in to, where every machine's options put the login off, and before
    # the forward to the machine beside it that would take the hop. What held refuses is held's own tests' (a line ssh
    # refuses, planted here for beta alone): this holds the order.
    _set(ROUTE, "host")
    alpha, beta = stand_ins.alpha, stand_ins.beta
    if login == "put off":
        _set("vm_ssh_options", {"ProxyJump": "jump.invalid"})
        _set("vm_ssh_options", {"ServerAliveInterval": "5"}, machine_name="alpha")
    real = route.ssh_shown

    def shown(ssh, words, destination, *args, **kwargs):
        if destination == beta.address:
            return {"ok": False, "proxycommand": "", "proxyjump": "", "said": "planted: a line ssh refuses"}
        return real(ssh, words, destination, *args, **kwargs)

    monkeypatch.setattr(route, "ssh_shown", shown)

    async def body() -> routes.Refused:
        found = await _settled(run, short)
        with pytest.raises(routes.Refused) as refused:
            await found.targets([_vm(alpha), _vm(beta)])
        return refused.value

    refused = asyncio.run(body())
    assert type(refused) is routes.Refused and "ssh refused its options: planted" in refused.sentence, refused
    # No forward, and no login but the one made at the run's start.
    at_the_start = ["-O check", "a master, opened", "-O check"]
    assert _outcomes(stand_ins.pve) == (at_the_start if login == "made at the start" else [])
    assert alpha.calls() == beta.calls() == []


def test_a_forward_that_stopped_the_run_stops_every_later_targets(made_fleet, short, run, stand_ins):
    # Unusable is the run's stop, even where a step caught it for one machine: a later targets raises it again, and
    # hands out no hop through a host that refuses to forward (review 46, G7.md 5).
    _set(ROUTE, "host")
    hop_modes(stand_ins.pve, forward="prohibited")
    alpha, beta = stand_ins.alpha, stand_ins.beta

    async def body() -> list[str]:
        found = await _settled(run, short)
        sentences = []
        for machines in ([alpha], [alpha, beta], [beta]):
            with pytest.raises(routes.Unusable) as refused:
                await found.targets([_vm(made_machine) for made_machine in machines])
            sentences.append(refused.value.sentence)
        return sentences

    first, *again = asyncio.run(body())
    assert first.startswith(f"{LABEL} refused to forward to alpha at ") and again == [first, first], again
    assert [outcome for outcome in _outcomes(stand_ins.pve) if outcome.startswith("forward")] == [
        f"forward {alpha.address}:22 unshared"
    ]


def test_two_targets_at_once_after_a_refused_forward_both_stop_the_run(made_fleet, short, run, stand_ins):
    # Two targets of one step at once (a TaskGroup over the machines): the one that waited on the lock while the
    # other's forward was refused hands out no hop either, and raises the same stop (review 56, G29.md 3).
    _set(ROUTE, "host")
    hop_modes(stand_ins.pve, forward="prohibited")
    alpha, beta = stand_ins.alpha, stand_ins.beta

    async def body() -> list[object]:
        found = await _settled(run, short)
        return await asyncio.gather(found.targets([_vm(alpha)]), found.targets([_vm(beta)]), return_exceptions=True)

    first, second = asyncio.run(body())
    assert type(first) is routes.Unusable and type(second) is routes.Unusable, (first, second)
    assert first.sentence == second.sentence and f"{LABEL} refused to forward to " in first.sentence
    assert len([outcome for outcome in _outcomes(stand_ins.pve) if outcome.startswith("forward")]) == 1
    assert alpha.calls() == beta.calls() == []


def test_a_forward_cut_before_its_verdict_is_made_again_by_the_next_targets(made_fleet, short, run, stand_ins):
    # A cancellation or a refusal that cuts the forward leaves no verdict: the next targets makes it.
    _set(ROUTE, "host")
    alpha = stand_ins.alpha
    asked: list[str] = []

    async def body() -> runner.Target:
        found = await _settled(run, short)
        real = found.connections.forward

        async def cut(target: runner.Target, settled) -> dict:
            asked.append(target.address)
            if len(asked) == 1:
                raise runner.Refused("planted: the forward was cut before its verdict")
            return await real(target, settled)

        found.connections.forward = cut
        with pytest.raises(runner.Refused, match="planted"):
            await found.targets([_vm(alpha)])
        [at] = await found.targets([_vm(alpha)])
        return at

    at = asyncio.run(body())
    assert asked == [alpha.address, alpha.address] and at.hop is not None
    assert [outcome for outcome in _outcomes(stand_ins.pve) if outcome.startswith("forward")] == [
        f"forward {alpha.address}:22 unshared"
    ]


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
        # A Target the route did not make, its hop taken off and its mark forged as direct, with what it would have
        # been decided from (which the scan below refuses in the engine's code): its own ssh would dial it from here,
        # which held refuses.
        bare = dataclasses.replace(beta, hop=None, routed_as="", routed_from=())
        forged = dataclasses.replace(bare, routed_as="direct", routed_from=runner.decided_from(bare))
        with pytest.raises(connections.Unheld, match="dialling it from here"):
            await found.connections.held(forged, found.settled)

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


def test_the_route_is_this_computer_s_own_for_each_host_and_never_another_s(
    made_fleet, nuc, short, run, stand_ins, addresses
):
    # pve's through the host, in client.yml's map hosts under pve: nuc, its own pin taken out, reads the default, auto,
    # never pve's - worked out at the run from where ssh dials nuc, which no configuration sends to a tailnet, it
    # settles direct, and an inherited host would settle a hop.
    _set(ROUTE, "host")
    where = paths.home(REPO)
    overrides.unset(where, ROUTE, "nuc", overrides.read(where, "nuc").client.digest)
    assert overrides.resolve(where, "nuc")[ROUTE] == overrides.Value(ROUTE, "auto", overrides.DEFAULT)

    async def body() -> list[tuple[object, object]]:
        settled = [(await _settled(run, short, name)).settled for name in ("pve", "nuc")]
        return [(each["route"], each["setting"]) for each in settled]

    assert asyncio.run(body()) == [("host", "host"), ("direct", "auto")]
    client = overrides.read(where, "nuc").client
    assert {name: dict(own) for name, own in client.hosts.items()} == {"pve": {ROUTE: "host"}}
    assert ROUTE not in client.settings


# ---------------------------------------------------------------- a configure through the hop ----


def test_a_configure_through_a_stand_in_hop_passes_and_with_the_hop_refused_stops_before_the_vm(
    made_fleet, short, run, made, stand_ins, tmp_path
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


# ---------------------------------------------------------------- the route's mark ----


def test_the_runner_connects_to_no_target_the_route_did_not_mark_nor_one_that_lost_its_hop(
    made_fleet, short, run, made, stand_ins
):
    # Under host, a Target of the route's with its hop taken off - a dataclasses.replace a later step could make - is a
    # valid machine's Target that would dial the VM at its address: refused as it is made, and again where a command
    # line is made of one changed by other means; and a machine's Target the route did not make at all, unmarked, at
    # each of the runner's command lines (review 46, G7.md 2). Nothing reaches the VM.
    _set(ROUTE, "host")
    alpha = stand_ins.alpha

    async def body() -> runner.Target:
        found = await _settled(run, short)
        [at] = await found.targets([_vm(alpha)])
        return at

    at = asyncio.run(body())
    assert at.routed_as == "hop" and at.hop is not None
    with pytest.raises(runner.Refused, match="has lost its hop, and would be dialled from here"):
        dataclasses.replace(at, hop=None)
    changed = dataclasses.replace(at)
    object.__setattr__(changed, "hop", None)
    with pytest.raises(runner.Refused, match="has lost its hop: nothing was started"):
        runner.argv(changed, ["true"])
    unmarked = runner.machine(alpha.identity, alpha.address, alpha.account, at.control_dir)
    for line in (
        lambda: runner.argv(unmarked, ["true"]),
        lambda: runner.forward_argv(unmarked, ["8080:127.0.0.1:8080"], 5),
        lambda: runner.ansible_vars(unmarked),
        lambda: asyncio.run(runner.run(made, unmarked, ["true"], None, runner.LIMITS, dict(os.environ))),
    ):
        with pytest.raises(runner.Refused, match="this one is unmarked, so nothing was started"):
            line()
    assert alpha.calls() == []


# Each field the route decided a machine's mark from, changed as a later step could change it, with the words the
# runner names it by: alpha's address moved to beta's, which the route reaches through the hop where it goes through
# the host; another account, port and ssh program; and its options with a jump's ProxyJump none, which takes a jump the
# person's own configuration gives out of its line.
MOVES = {
    "address": "address",
    "account": "account",
    "port": "port",
    "ssh": "ssh program",
    "given": "vm_ssh_options",
}


@pytest.mark.parametrize("mark", ["direct", "own", "hop"])
def test_the_runner_connects_to_no_target_changed_since_the_route_marked_it(
    made_fleet, short, run, made, stand_ins, mark
):
    # The mark is bound to what the route decided it from (review 56, G29.md 2 and 7): alpha's Target, marked straight
    # at it (direct), by the jump the person's own configuration gives its address (own) or through the hop (hop), with
    # a field changed - by a replace, refused as it is made, or by other means, refused by each of the runner's lines -
    # reaches nothing. Under host, its address moved to beta's dialled beta from here, where the route gives beta the
    # hop, before the mark was bound.
    alpha, beta = stand_ins.alpha, stand_ins.beta
    if mark != "direct":
        _set(ROUTE, "host")
    if mark == "own":
        _config(f"Host {alpha.address}\n  ProxyJump jump.invalid\n", short)

    async def body() -> tuple[runner.Target, ...]:
        found = await _settled(run, short)
        return await found.targets([_vm(alpha), _vm(beta)])

    at, beta_at = asyncio.run(body())
    assert (at.routed_as, at.routed_from) == (mark, runner.decided_from(at))
    assert (beta_at.hop is None) is (mark == "direct")
    values = {
        "address": beta.address,
        "account": "other",
        "port": 2222,
        "ssh": "ssh-other",
        "given": (("ProxyJump", "none"),),
    }
    lines = (
        lambda target: runner.argv(target, ["true"]),
        lambda target: runner.forward_argv(target, ["8080:127.0.0.1:8080"], 5),
        runner.ansible_vars,
        lambda target: asyncio.run(runner.run(made, target, ["true"], None, runner.LIMITS, dict(os.environ))),
    )
    for name, said in MOVES.items():
        moved = f"its {said} changed since, so the mark is no longer the route's: nothing was started"
        # Through the hop, another address is refused first by the hop's own -W, which names the machine's address.
        first = "another address than the machine's own" if (mark, name) == ("hop", "address") else moved
        with pytest.raises(runner.Refused, match=first):
            dataclasses.replace(at, **{name: values[name]})
        changed = dataclasses.replace(at)
        object.__setattr__(changed, name, values[name])
        for line in lines:
            with pytest.raises(runner.Refused, match=moved):
                line(changed)
    # A first use's known-hosts file decides no road, and goes with the mark (connections.first_use).
    first_use = dataclasses.replace(at, known_hosts=short / "scratch", first_use=True)
    assert (first_use.routed_as, first_use.routed_from) == (at.routed_as, at.routed_from)
    assert runner.argv(first_use, ["true"])[-1] == "true"
    assert alpha.calls() == beta.calls() == []


# ---------------------------------------------------------------- the rules ----


def test_every_harness_pins_the_route_direct(made_fleet, short):
    # auto would ask this computer's ssh, through the developer's own configuration, where it dials a test's host:
    # the new engine's fleet fixture pins direct for each host it makes, in client.yml's map hosts, as today's
    # harnesses pin it (tests/unit/test_vm_route.py holds those).
    where = paths.home(REPO)
    assert overrides.resolve(where, made_fleet.host)[ROUTE] == overrides.Value(ROUTE, "direct", overrides.CLIENT)
    with _host(short) as host:
        assert host.values[ROUTE].value == "direct"


def test_every_fleet_a_test_makes_pins_the_route_before_anything_reads_it(home, tmp_path, pki, monkeypatch):
    # Each maker of a fleet the new engine's tests have, read back as tests/unit/test_vm_route.py reads today's
    # harnesses (review 46, G7.md 6), each in a home of its own: whole_fleet's before aw init, which reads the fleet
    # first, and with_port, which every init test writes its host with; H34's fleet of the quick commands; the host the
    # certificate's tests keep; and the settings the template's tests read.
    import test_aw_cli_imports
    import test_aw_engine_certificate
    import test_aw_engine_template

    pinned = overrides.Value(ROUTE, "direct", overrides.CLIENT)

    def route_of(host: str) -> overrides.Value:
        return overrides.resolve(paths.home(REPO), host)[ROUTE]

    def own_home(name: str) -> None:
        (tmp_path / name).mkdir()
        monkeypatch.setenv(paths.VARIABLE, str(tmp_path / name))

    at_init: list[overrides.Value] = []
    real = fleet_fixture.run_aw

    def init(*words: str, **kwargs):
        at_init.append(route_of(words[words.index("--host") + 1]))
        return real(*words, **kwargs)

    monkeypatch.setattr(fleet_fixture, "run_aw", init)
    directory = tmp_path / "fake"
    directory.mkdir()
    for base in fake_fleet(directory, engine=True, certificate=(pki["cert"], pki["key"])):
        made_one = whole_fleet(home, base, directory, pki)
    assert at_init == [pinned] and route_of(made_one.host) == pinned
    with_port(8006, "nuc")
    assert route_of("nuc") == pinned
    own_home("quick")
    with test_aw_cli_imports._fleet(("status",)):
        assert {name: route_of(name) for name in test_aw_cli_imports.HOSTS} == dict.fromkeys(
            test_aw_cli_imports.HOSTS, pinned
        )
    own_home("certificate")
    test_aw_engine_certificate._keep(types.SimpleNamespace(anchor=None), "lab")
    assert route_of("lab") == pinned
    own_home("template")
    assert test_aw_engine_template._settings()[ROUTE] == "direct" and route_of("lab") == pinned


# The scan: where a road to a machine's Target is taken. RUNNER defines it; CONNECTIONS marks it with its route.
RUNNER = "claude_vm.ssh.runner"
CONNECTIONS = "claude_vm.ssh.connections"
# What makes a Target, the Proxmox host's or a machine's, or a machine's hop: the runner's, by any name.
MAKERS = frozenset({"machine", "proxmox_host", "Target", "Hop"})
# What gives a machine's Target its route, called on any object: the run's connections, and target_for, which marks it.
GIVERS = frozenset({"target_for", "Connections"})
# A Target's fields a replace may change only in ssh/connections.py: the route's - its hop, the person's options, the
# mark and what it was decided from - and a first use's. A replace that names none of them changes no route: address is
# left out, since other records of the engine's have one (a fleet's Target, a host's fields), and the runner refuses a
# Target whose address, account, port or ssh program changed since the route marked it (runner.Target.routed_from), by
# whatever spelling (test_the_runner_connects_to_no_target_changed_since_the_route_marked_it).
CHANGES = frozenset({"hop", "given", "routed_as", "routed_from", "first_use", "known_hosts"})
# The route's mark and what it was decided from, which only connections.target_for sets.
MARKS = frozenset({"routed_as", "routed_from"})
# Each road a module of the new engine's takes as its own, by its file under lib/claude_vm/, the road and the function
# it is in ("*": anywhere in it), with why. ssh/runner.py, which defines the Target, is not read.
OWN_ROADS = {
    ("ssh/connections.py", "maker", "target_for"): "the hop's ProxyCommand it gives a machine, a runner.Hop",
    ("ssh/connections.py", "replace", "target_for"): "the person's options, the hop and the route's mark it gives",
    ("ssh/connections.py", "replace", "first_use"): "a machine's Target made a first use, its mark carried",
    ("ssh/connections.py", "mark", "target_for"): "the one place a machine's Target is marked with its route",
    ("engine/routes.py", "maker", "host_target"): "the Proxmox host's own Target, for the hop and for root SSH",
    ("engine/routes.py", "maker", "targets"): "each machine's Target, unmarked, for target_for to give it its route",
    ("engine/routes.py", "connections", "*"): "the run's connections, which settle the route and give each Target",
    ("cli/dispatch.py", "import", "*"): "a verb's handler, by the module the table of verbs names, cli/ or engine/",
}


def _new_engine_files() -> list[Path]:
    """The new engine's Python: every module of its subpackages, secret.py, and tools/aw."""
    wanted = [where for name in NEW_MODULES for where in (f"lib/claude_vm/{name}", f"lib/claude_vm/{name}.py")]
    return [*(path for path in repo_files(*wanted, untracked=True) if path.suffix == ".py"), REPO / "tools" / "aw"]


def _package(path: Path) -> tuple[str, ...]:
    """The package a module of lib/ is in, which its relative imports start from: ("claude_vm", "engine") for
    engine/routes.py and for engine/__init__.py; none for tools/aw."""
    if not path.is_relative_to(REPO / "lib"):
        return ()
    return path.relative_to(REPO / "lib").parts[:-1]


def _bound(tree: ast.AST, package: tuple[str, ...]) -> dict[str, str]:
    """Each name a module's imports bind, with the dotted name it stands for, its relative imports read from package;
    runner and connections, where nothing binds them, stand for the runner and the connections, as in this file's
    cases."""
    names = {"runner": RUNNER, "connections": CONNECTIONS}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # import a.b binds a; import a.b as c binds c to a.b.
                head = alias.name.split(".")[0]
                names[alias.asname or head] = alias.name if alias.asname else head
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join([*package[: len(package) - node.level + 1], *([node.module] if node.module else [])])
            for alias in node.names:
                names[alias.asname or alias.name] = f"{base}.{alias.name}" if base else alias.name
    return names


def _imported(node: ast.Import | ast.ImportFrom, package: tuple[str, ...]) -> list[str]:
    """The dotted names an import statement brings in."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    base = node.module or ""
    if node.level:
        base = ".".join([*package[: len(package) - node.level + 1], *([node.module] if node.module else [])])
    return [f"{base}.{alias.name}" if base else alias.name for alias in node.names]


def _dotted(node: ast.AST, names: dict[str, str]) -> str:
    """The dotted name an attribute or a name stands for, its head read through the imports; "" for anything else."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    return ".".join([names.get(node.id, node.id), *reversed(parts)])


def _typed(tree: ast.AST) -> set[int]:
    """The nodes of tree that only name a type: annotations, and isinstance's and issubclass's second argument."""
    roots: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            each = node.args
            given = [*each.posonlyargs, *each.args, *each.kwonlyargs, each.vararg, each.kwarg]
            roots += [arg.annotation for arg in given if arg is not None and arg.annotation is not None]
            roots += [node.returns] if node.returns is not None else []
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
        elif isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("isinstance", "issubclass"):
            roots += node.args[1:2]
    return {id(inner) for root in roots for inner in ast.walk(root)}


def _within(tree: ast.AST) -> dict[int, str | None]:
    """Each node of tree, by its id, with the name of the innermost function it is in: None for the module's own."""
    found: dict[int, str | None] = {}

    def visit(node: ast.AST, function: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            found[id(child)] = inner
            visit(child, inner)

    visit(tree, None)
    return found


def _roads(source: str, package: tuple[str, ...] = ("claude_vm", "engine")) -> list[tuple[str, str | None, str]]:
    """Each road source takes to a machine's Target - (what, the function it is in, where) - by any name a scan can see:

    - maker: the runner's machine, proxmox_host or Hop named at all (a call, a bound name, partial's argument), or its
      Target but as a type, by the module under any name (an alias, its whole name, an attribute chain) or imported;
    - runner: the runner module handed on as a value (getattr's argument, a name bound to it);
    - connections: ssh/connections.py named or imported at all, or target_for or Connections called on anything;
    - class: a Target made from another's class, type(target)(...) or target.__class__(...);
    - import: importlib, or __import__, which import a module by a name built at run time;
    - replace: dataclasses.replace, copy.replace or __replace__ under any name, changing a field of CHANGES, or given a
      mapping spread into it;
    - mark: the route's mark, or what it was decided from, set - given as a keyword, assigned, or named in a string
      (setattr, a mapping's key).

    Only the outermost of an attribute chain is read. What none of them sees: a name built from pieces at run time."""
    tree = ast.parse(source)
    names, typed, within = _bound(tree, package), _typed(tree), _within(tree)
    inner = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    found: list[tuple[str, str | None, str]] = []

    def take(what: str, node: ast.AST, said: str) -> None:
        found.append((what, within.get(id(node)), f"line {getattr(node, 'lineno', 0)}: {said}"))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for dotted in _imported(node, package):
                if dotted == CONNECTIONS or dotted.startswith(f"{CONNECTIONS}."):
                    take("connections", node, f"imports {dotted}")
                elif dotted.rpartition(".")[0] == RUNNER and dotted.rpartition(".")[2] in MAKERS:
                    take("maker", node, f"imports {dotted}")
                elif dotted.split(".")[0] == "importlib":
                    take("import", node, f"imports {dotted}")
        elif isinstance(node, (ast.Name, ast.Attribute)) and id(node) not in inner:
            dotted = _dotted(node, names)
            if dotted in {f"{RUNNER}.{name}" for name in MAKERS - {"Target"}}:
                take("maker", node, dotted)
            elif dotted == f"{RUNNER}.Target" and id(node) not in typed:
                take("maker", node, dotted)
            elif dotted == RUNNER:
                take("runner", node, "the runner module handed on")
            elif dotted == CONNECTIONS or dotted.startswith(f"{CONNECTIONS}."):
                take("connections", node, dotted)
            if isinstance(node, ast.Attribute) and node.attr in MARKS and isinstance(node.ctx, ast.Store):
                take("mark", node, f"{node.attr} assigned")
        elif isinstance(node, ast.Constant) and node.value in MARKS:
            take("mark", node, f"{node.value} named in a string")
        elif isinstance(node, ast.Call):
            func = node.func
            called = _dotted(func, names)
            last = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if last in GIVERS:
                take("connections", node, f"{last} called")
            if isinstance(func, ast.Call) and _dotted(func.func, names) == "type" or last == "__class__":
                take("class", node, "a Target made from another's class")
            if called in ("importlib.import_module", "__import__"):
                take("import", node, called)
            changes = {keyword.arg for keyword in node.keywords}
            if called in ("dataclasses.replace", "copy.replace") or last == "__replace__":
                if None in changes or changes & CHANGES:
                    take("replace", node, f"{called or last} of {sorted(changes - {None}) or 'a mapping spread'}")
            for mark in sorted(changes & MARKS):
                take("mark", node, f"{mark} given")
    return found


def test_no_module_of_the_new_engine_but_routes_and_ssh_s_own_makes_or_changes_a_target() -> None:
    # Held where the connection is made too (the route's mark, above): a scan sees what a slip would write, and the
    # mark what it cannot. Every road of OWN_ROADS is still taken: one no module takes any more goes out of it.
    files = _new_engine_files()
    assert {ROUTES, LIB / "ssh" / "connections.py", ENGINE / "init.py", LIB / "guest" / "layer1.py"} <= set(files)
    found: dict[str, list[str]] = {}
    taken = set()
    for path in files:
        rel = path.relative_to(LIB).as_posix() if path.is_relative_to(LIB) else path.relative_to(REPO).as_posix()
        if rel == "ssh/runner.py":
            continue
        for what, function, where in _roads(path.read_text(), _package(path)):
            own = next((key for key in ((rel, what, function), (rel, what, "*")) if key in OWN_ROADS), None)
            if own is None:
                found.setdefault(rel, []).append(f"{where} ({what})")
            else:
                taken.add(own)
    assert found == {}, "a machine's Target is routes.py's alone, marked by its connections: take one from targets"
    assert taken == set(OWN_ROADS), set(OWN_ROADS) - taken
    # The one module that imports by a name at run time loads a verb's handler, which the table of verbs names in cli/,
    # engine/ or runs/ (logs' reader of jobs, C12), never ssh/.
    assert {verb.handler.split(".")[0] for verb in VERBS.values() if verb.handler} <= {"cli", "engine", "runs"}


# A road, planted in a module of engine/, and the road the scan sees it as; then what it does not take for one.
SEEN = [
    ('runner.machine(identity, address, "dev", control)', "maker"),
    ('runner.proxmox_host(address, "root", control)', "maker"),
    ("runner.Target(identity=None, address=a, account='root', control_dir=c)", "maker"),
    ("runner.Hop(command, name)", "maker"),
    ("from ..ssh.runner import machine", "maker"),
    ("from claude_vm.ssh.runner import proxmox_host as host", "maker"),
    # The runner under another name: an alias, its whole name, an attribute chain, a bound name, partial.
    ('from ..ssh import runner as r\nr.machine(identity, address, "dev", control)', "maker"),
    ("import claude_vm.ssh.runner as r\nr.Target(identity=i, address=a, account='dev', control_dir=c)", "maker"),
    ("import claude_vm.ssh.runner\nclaude_vm.ssh.runner.machine(i, a, 'dev', c)", "maker"),
    ("from .. import ssh\nssh.runner.machine(identity, address, 'dev', control)", "maker"),
    ("from ..ssh import runner as r\nmake = r.machine\nmake(identity, address, 'dev', control)", "maker"),
    ("import functools\nfunctools.partial(runner.machine, identity)(address, 'dev', control)", "maker"),
    ("from ..ssh import runner as r\nmake = r.Target", "maker"),
    ("from ..ssh import runner as r\ngetattr(r, 'machine')(identity, address, 'dev', control)", "runner"),
    # A Target made from another's class.
    ("type(target)(identity=i, address=a, account='dev', control_dir=c)", "class"),
    ("target.__class__(identity=i, address=a, account='dev', control_dir=c)", "class"),
    # A module imported by its name at run time.
    ("import importlib\nimportlib.import_module('claude_vm.ssh.connections')", "import"),
    ("from importlib import import_module", "import"),
    ("__import__('claude_vm.ssh.connections')", "import"),
    # A Target changed after the route made it, by any spelling of replace.
    ("dataclasses.replace(target, hop=None)", "replace"),
    ("import dataclasses as dc\ndc.replace(target, given=())", "replace"),
    ("from dataclasses import replace as changed\nchanged(target, hop=None)", "replace"),
    ("import copy\ncopy.replace(target, hop=None)", "replace"),
    ("target.__replace__(given=())", "replace"),
    ("dataclasses.replace(target, **changes)", "replace"),
    ("dataclasses.replace(target, routed_from=())", "replace"),
    # The route's mark, or what it was decided from, set anywhere but where the route gives it.
    ("dataclasses.replace(target, hop=None, routed_as='direct')", "mark"),
    ("object.__setattr__(target, 'routed_as', 'direct')", "mark"),
    ("target.routed_as = 'direct'", "mark"),
    ("runner.machine(i, a, 'dev', c, routed_as='direct')", "mark"),
    ("dataclasses.replace(target, address=a, routed_from=runner.decided_from(moved))", "mark"),
    ("object.__setattr__(target, 'routed_from', ())", "mark"),
    ("target.routed_from = ()", "mark"),
    # The connections.
    ("from ..ssh import connections", "connections"),
    ("from . import connections", "connections"),
    ("from claude_vm.ssh.connections import Connections", "connections"),
    ("import claude_vm.ssh.connections", "connections"),
    ("from .. import ssh\nssh.connections.first_use(job, target)", "connections"),
    ("await made.target_for(job, base, settled)", "connections"),
    ("Connections(control, env)", "connections"),
]
UNSEEN = [
    "from ..ssh import runner\nawait runner.run(job, target, command, None, limits, env)",
    "from ..ssh import runner",
    "from ..ssh import runner\ntarget: runner.Target = at",
    "from ..ssh import runner\nisinstance(target, runner.Target)",
    "from ..ssh import runner\ndef reach(target: runner.Target) -> runner.Target | None: ...",
    # Seen by the runner instead, which refuses a Target whose address changed since the route marked it.
    "dataclasses.replace(target, address=address)",
    "dataclasses.replace(limits, seconds=left)",
    "dataclasses.replace(target)",
    "await found.connections.held(target, found.settled)",
    "said = target.routed_as",
    "said = runner.decided_from(target) == target.routed_from",
]


@pytest.mark.parametrize(("source", "what"), SEEN)
def test_the_target_scan_sees_each_way(source, what) -> None:
    # "from . import connections" is planted in ssh/, beside it; every other, in engine/.
    package = ("claude_vm", "ssh") if source == "from . import connections" else ("claude_vm", "engine")
    assert what in {road for road, _, _ in _roads(source, package)}, _roads(source, package)


@pytest.mark.parametrize("source", UNSEEN)
def test_the_target_scan_takes_no_other_way_for_one(source) -> None:
    assert _roads(source) == []
