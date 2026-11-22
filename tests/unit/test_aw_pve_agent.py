"""A started VM's address from its guest agent (lib/claude_vm/pve/agent.py, and client.py's agent_interfaces;
docs/architecture/engine.md §16.5).

Every poll goes to the fake Proxmox (tests/molecule/fake_pve_api.py) through harness.fake_fleet, with the engine's pool
and its one token, granted as init grants them and held to the engine's table of privileges (enforce): the agent's call
is answered only where the token holds its row's privilege, so a token without it is Denied by the fake, as Proxmox
refuses it. A VM started just now has an agent that fails its first two polls, as the fake's does; what a VM's agent
reports is planted with the fake's agent-answer, as the VM's root would make it say.

The wait gives the VM's own address once its agent answers; ends at its deadline where none comes, and not later;
stops at once on a refused token, with one request made, on a VM that is not running, at its first poll or the
first after it stopped, on a VM with no guest agent configured, running or not, and on a certificate that is not the
host's, the agent's own connection pinned as the client's is; takes a connection refused or reset for "not yet"; holds
each poll to the longest one agent call runs, and a new client's first to the wait's deadline; and ends when the run's
cancelled is set, between polls or during one, a poll it left behind then ending within its bound and sending nothing
more once the client is closed. An address is taken only
on the VM's own network card and only where checks.guest_ipv4 takes it: 0.0.0.0, a multicast address, two addresses in
one, and an address with a newline and a ProxyCommand after it are each no address, and no shape an agent's answer
takes raises. The rules with a test of their own: the agent's calls go on connections of their own whose reads wait
AGENT_READ, shorter than every other read's; the words the client tells a stopped VM, and one with no agent, by are the
fake's, read as Proxmox's whole sentence; and no error of the wait says the token's secret where Proxmox's words echo
it.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
import logging
import ssl
import threading
import time
from pathlib import Path

import pytest
from harness import (
    ENGINE_POOL,
    ENGINE_ROLE,
    ENGINE_TOKEN,
    ENGINE_USER,
    FAKES,
    _pve,
    _pve_requests,
    _pve_vm,
    engine_grant,
    engine_token,
    fake_fleet,
)
from pki import proxmox_pki
from test_aw_transport import _Handler, _said, _Server, answer

from claude_vm.pve import agent, answers, pin, privileges
from claude_vm.pve import client as client_module
from claude_vm.pve.agent import DEADLINE, PAUSES, NoAddress, address, wait_for_address
from claude_vm.pve.client import (
    AGENT_READ,
    NO_AGENT,
    NOT_RUNNING,
    REFUSED_STATUS,
    Denied,
    NoAgent,
    NotRunning,
    Proxmox,
    Refused,
    Token,
    Unverified,
    _stopped,
)
from claude_vm.secret import MARK, Secret
from claude_vm.vendors.transport import Answer, TimedOut, Unreachable, Untrusted

# The module's certificate authority, which signs the fake's certificate: cheap enough to make again on each worker
# (tests/unit/conftest.py).
EACH_WORKER = {"pki"}
# A token's secret no error may say: low in entropy, so that gitleaks takes it for no key.
CANARY = "unit-test-pve-agent-canary-value"
# What the fake says where it refuses, as it says it: the constants at its top, which experiment 6 corrects (A21).
SAID = {
    target.id: node.value.value
    for node in ast.parse((FAKES / "fake_pve_api.py").read_text()).body
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
    for target in node.targets
    if isinstance(target, ast.Name)
}
# What the fake's agent reports for a VM of its own: the address of its eth0 (agent_interfaces).
ALPHA, DELTA = "192.0.2.60", "192.0.2.70"
OTHER_MAC = "02:42:9a:11:22:33"


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    return proxmox_pki(tmp_path_factory.mktemp("pki"), key_usage=True)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    """The pauses between polls made short: the fake's agent answers as soon as it is asked."""
    monkeypatch.setattr(agent, "PAUSES", (0.01, 0.02, 0.05))


@dataclasses.dataclass
class Fleet:
    """The fake at base, its state in directory, the engine's token, and the certificates it serves (pki)."""

    base: str
    directory: Path
    token: Token
    pki: dict

    def asked(self) -> list[dict]:
        """Every request the client sent the fake: those of its user agent, in order."""
        return [entry for entry in _pve_requests(self.directory) if entry["agent"] == pin.USER_AGENT]

    def polls(self, vmid: int) -> int:
        """How many times the client asked vmid's agent."""
        path = f"/api2/json/nodes/pve/qemu/{vmid}/agent/network-get-interfaces"
        return sum(entry["path"] == path for entry in self.asked())

    def client(self, token: Token | None = None) -> Proxmox:
        return Proxmox(self.base, pin.kept(self.pki["ca"]), token or self.token)

    def mac(self, vmid: int) -> str:
        """vmid's MAC, as a workflow reads it: its configuration's net0."""
        with self.client() as client:
            return client.config("pve", vmid).mac

    def plant(self, vmid: int, cards) -> None:
        """What vmid's agent reports of its network cards from now on, as its root makes it say."""
        _pve(self.base, "PUT", "fake/agent-answer", vmid=vmid, result=json.dumps(cards))


@pytest.fixture
def fleet(tmp_path, pki):
    """The fake with the engine's token held to its table, the template and alpha (running, its agent up) the pool's
    members; beta is running and not one, gamma stopped and not one."""
    certificate = (pki["cert"], pki["key"])
    for base in fake_fleet(tmp_path, engine=True, certificate=certificate, enforce=True):
        _pve(base, "PUT", "pools", poolid=ENGINE_POOL, vms="9000,9010")
        token_id, secret = engine_token(tmp_path)
        yield Fleet(base, tmp_path, Token(token_id, Secret(secret)), pki)


def _started(fleet: Fleet, vmid: int = 9020, name: str = "delta") -> str:
    """A VM started just now, its agent not asked yet, made a member of the pool as root makes one; its MAC."""
    _pve_vm(fleet.base, vmid, name, running=True, agent_polls=0)
    _pve(fleet.base, "PUT", "pools", poolid=ENGINE_POOL, vms=str(vmid))
    return fleet.mac(vmid)


def _wait(fleet: Fleet, vmid: int, mac: str, deadline: float = 30.0, token: Token | None = None) -> str:
    async def waited() -> str:
        with fleet.client(token) as client:
            return await wait_for_address(client, "pve", vmid, mac, deadline, asyncio.Event())

    return asyncio.run(waited())


def _card(mac: str, *addresses: str | tuple[str, str], name: str = "eth0") -> dict:
    """A network card as QEMU's agent reports one: each address IPv4 unless given as (address, type)."""
    entries = [(one, "ipv4") if isinstance(one, str) else one for one in addresses]
    return {
        "name": name,
        "hardware-address": mac,
        "ip-addresses": [{"ip-address": one, "ip-address-type": kind, "prefix": 24} for one, kind in entries],
    }


# ------------------------------------------------------------------------------------------------ the wait ----


def test_an_agent_that_fails_its_first_two_polls_gives_the_vm_s_own_address_at_the_third(fleet) -> None:
    mac = _started(fleet)
    # The fake's agent reports lo and a docker0 bridge too, each on a MAC of its own: never taken.
    assert _wait(fleet, 9020, mac) == DELTA
    assert fleet.polls(9020) == SAID["AGENT_READY_AFTER"] + 1 == 3


def test_an_agent_that_never_answers_is_ended_at_the_deadline_and_not_waited_for_longer(fleet) -> None:
    mac = _started(fleet)
    _pve(fleet.base, "PUT", "fake/agent-ready-after", polls=10**6)
    began = time.monotonic()
    with pytest.raises(NoAddress) as none:
        _wait(fleet, 9020, mac, deadline=1.0)
    took = time.monotonic() - began
    assert 1.0 <= took < 4.0, took
    assert (none.value.vmid, none.value.mac, none.value.seconds) == (9020, mac, 1.0)
    assert SAID["AGENT_NOT_RUNNING"] in str(none.value) and "within 1 s" in str(none.value)
    assert fleet.polls(9020) >= 2


def test_a_token_without_the_agent_s_privilege_is_denied_at_the_first_poll_with_one_request_made(fleet) -> None:
    # The role on the pool taken off, and one with everything the table needs there but the agent's privilege given.
    whom = {"users": ENGINE_USER, "tokens": f"{ENGINE_USER}!{ENGINE_TOKEN}"}
    _pve(fleet.base, "PUT", "access/acl", path=f"/pool/{ENGINE_POOL}", roles=ENGINE_ROLE, delete=1, **whom)
    held = privileges.needed("pool") - {"VM.GuestAgent.Audit"}
    engine_grant(fleet.base, f"/pool/{ENGINE_POOL}", ",".join(sorted(held)))
    mac = fleet.mac(9010)
    began = time.monotonic()
    with pytest.raises(Denied) as denied:
        _wait(fleet, 9010, mac, deadline=60.0)
    assert denied.value.status == 403 and time.monotonic() - began < 10
    assert fleet.polls(9010) == 1


def test_a_token_proxmox_does_not_take_is_denied_before_the_agent_is_asked(fleet) -> None:
    mac = fleet.mac(9010)
    with pytest.raises(Denied) as denied:
        _wait(fleet, 9010, mac, deadline=60.0, token=Token(fleet.token.id, Secret("not-the-secret")))
    assert denied.value.status == 401 and fleet.polls(9010) == 0


def test_a_stopped_vm_is_not_running_at_once_with_one_request_made(fleet) -> None:
    _pve(fleet.base, "PUT", "pools", poolid=ENGINE_POOL, vms="9012")
    mac = fleet.mac(9012)
    began = time.monotonic()
    with pytest.raises(NotRunning) as stopped:
        _wait(fleet, 9012, mac, deadline=60.0)
    assert stopped.value.vmid == 9012 and time.monotonic() - began < 10
    assert fleet.polls(9012) == 1


def test_a_vm_that_stops_while_it_is_waited_for_is_not_running_at_the_next_poll(fleet, monkeypatch) -> None:
    mac = _started(fleet)
    _pve(fleet.base, "PUT", "fake/agent-ready-after", polls=10**6)
    monkeypatch.setattr(agent, "PAUSES", (0.2,))

    async def waited() -> None:
        with fleet.client() as client:
            waiting = asyncio.ensure_future(wait_for_address(client, "pve", 9020, mac, 60.0, asyncio.Event()))
            while not fleet.polls(9020):
                await asyncio.sleep(0.02)
            await asyncio.to_thread(_pve, fleet.base, "POST", "nodes/pve/qemu/9020/status/stop")
            await waiting

    began = time.monotonic()
    with pytest.raises(NotRunning):
        asyncio.run(waited())
    assert time.monotonic() - began < 10


@pytest.mark.parametrize("running", [True, False], ids=["running", "stopped"])
def test_a_vm_with_no_guest_agent_configured_is_no_agent_at_once_with_one_request_made(fleet, running) -> None:
    """Proxmox says so before it asks whether the VM runs: no agent will answer, so waiting cannot help, and the
    sentence names the setting that turns one on."""
    _pve_vm(fleet.base, 9020, "delta", running=running, agent_polls=0, agent="enabled=0")
    _pve(fleet.base, "PUT", "pools", poolid=ENGINE_POOL, vms="9020")
    mac = fleet.mac(9020)
    began = time.monotonic()
    with pytest.raises(NoAgent) as none:
        _wait(fleet, 9020, mac, deadline=60.0)
    assert none.value.vmid == 9020 and time.monotonic() - began < 10
    assert "no QEMU guest agent configured" in str(none.value) and "agent setting" in str(none.value)
    assert "enabled=1" in str(none.value) and fleet.polls(9020) == 1


@pytest.mark.parametrize("before", [True, False], ids=["after-a-refused-poll", "alone"])
def test_a_poll_that_ran_out_of_time_keeps_what_an_earlier_poll_met(fleet, before) -> None:
    """The deadline may come during a poll: the poll's timeout then never hides what Proxmox said before it."""
    mac = fleet.mac(9010)
    asked = []

    def polled(node: str, vmid: int, deadline: float | None = None) -> answers.Interfaces:
        asked.append(deadline)
        if before and len(asked) == 1:
            raise Refused(500, SAID["AGENT_NOT_RUNNING"], "GET its agent")
        raise TimedOut("no answer within 10 s")

    async def waited() -> None:
        with fleet.client() as client:
            client.agent_interfaces = polled
            await wait_for_address(client, "pve", 9010, mac, 0.3, asyncio.Event())

    with pytest.raises(NoAddress) as none:
        asyncio.run(waited())
    assert len(asked) >= 2 and all(0 < left <= 0.3 for left in asked)
    if before:
        assert SAID["AGENT_NOT_RUNNING"] in none.value.said and "no answer" not in none.value.said
    else:
        assert none.value.said == "the last poll: no answer within 10 s"


# -------------------------------------------------------------------------------------- what an agent says ----

REFUSED = {
    "0.0.0.0": ["0.0.0.0"],
    "a multicast address": ["224.0.0.251"],
    "broadcast": ["255.255.255.255"],
    "two addresses in one": ["192.0.2.7 192.0.2.8"],
    "two addresses with a comma": ["192.0.2.7,192.0.2.8"],
    "an address with a newline and a ProxyCommand": ["192.0.2.7\n    ProxyCommand sh -c 'touch /tmp/owned'"],
    "an address and an option": ["192.0.2.7 -oProxyCommand=x"],
    "loopback and link-local": ["127.0.0.1", "169.254.1.1"],
    "IPv6 alone": [("2001:db8::7", "ipv6")],
    "an IPv4 address called IPv6": [("192.0.2.7", "ipv6")],
}


@pytest.mark.parametrize("reported", REFUSED.values(), ids=REFUSED.keys())
def test_an_answer_that_holds_no_one_address_a_card_answers_at_is_no_address(fleet, reported) -> None:
    mac = fleet.mac(9010)
    fleet.plant(9010, [_card(OTHER_MAC, "192.0.2.99", name="docker0"), _card(mac, *reported)])
    with pytest.raises(NoAddress) as none:
        _wait(fleet, 9010, mac, deadline=0.3)
    assert "its agent answered, with no IPv4 address on that card" in str(none.value)


def test_an_address_on_another_mac_is_ignored_and_the_vm_s_own_after_it_is_taken(fleet) -> None:
    mac = fleet.mac(9010)
    fleet.plant(9010, [_card(OTHER_MAC, "192.0.2.99"), _card(mac, ("fe80::1", "ipv6"))])
    with pytest.raises(NoAddress):
        _wait(fleet, 9010, mac, deadline=0.3)
    fleet.plant(9010, [_card(OTHER_MAC, "192.0.2.99"), _card(mac.upper(), "0.0.0.0", ALPHA)])
    assert _wait(fleet, 9010, mac) == ALPHA


SHAPES = {
    "no list": "eth0",
    "a mapping": {"eth0": ["192.0.2.7"]},
    "no cards": [],
    "a card that is no mapping": ["eth0", 7, None],
    "a MAC that is no text": [{"hardware-address": 7, "ip-addresses": []}],
    "addresses that are no list": [{"hardware-address": "{mac}", "ip-addresses": "192.0.2.7"}],
    "an address that is no mapping": [{"hardware-address": "{mac}", "ip-addresses": ["192.0.2.7", 7]}],
    "an address that is a list": [{"hardware-address": "{mac}", "ip-addresses": [{"ip-address": ["192.0.2.7"]}]}],
    "an address with no type": [{"hardware-address": "{mac}", "ip-addresses": [{"ip-address": "192.0.2.7"}]}],
}


@pytest.mark.parametrize("result", SHAPES.values(), ids=SHAPES.keys())
def test_no_shape_of_an_agent_s_answer_raises_and_none_is_an_address(fleet, result) -> None:
    mac = fleet.mac(9010)
    fleet.plant(9010, json.loads(json.dumps(result).replace("{mac}", mac)))
    with pytest.raises(NoAddress):
        _wait(fleet, 9010, mac, deadline=0.3)


def test_of_two_good_addresses_on_the_vm_s_card_the_first_is_taken_as_today() -> None:
    # The reading of the card's "two addresses": two in one value are refused above; two good ones on the VM's own
    # card, each on an entry of its own, are what checks.guest_ipv4 has always taken the first of.
    mac = "bc:24:11:00:23:32"
    assert address([_card(mac, "192.0.2.7", "192.0.2.8")], mac) == "192.0.2.7"


def test_an_answer_keeps_only_what_a_card_has_and_nothing_a_vm_nests_deeper() -> None:
    deep: list = []
    for _ in range(5000):
        deep = [deep]
    card = {**_card("BC:24:11:00:23:32", ALPHA), "statistics": deep, "speed": {"deep": deep}}
    card["ip-addresses"][0]["note"] = deep
    # Under the names a card has too: a value that is no plain one is not kept.
    nested = {"name": deep, "hardware-address": deep, "ip-addresses": [{"ip-address": deep, "prefix": {"n": deep}}]}
    found = answers.interfaces({"result": [card, "eth1", nested]}, "pve", 9010)
    assert len(found.interfaces) == 2
    kept, other = found.interfaces
    assert dict(other) == {"ip-addresses": ({},)}
    assert dict(kept) == {
        "name": "eth0",
        "hardware-address": "BC:24:11:00:23:32",
        "ip-addresses": ({"ip-address": ALPHA, "ip-address-type": "ipv4", "prefix": 24},),
    }
    assert address(found, "bc:24:11:00:23:32") == ALPHA
    assert answers.interfaces({"result": {"eth0": ALPHA}}, "pve", 9010).interfaces == ()
    with pytest.raises(answers.Malformed):
        answers.interfaces(["eth0"], "pve", 9010)


# ----------------------------------------------------------------------------------------- the run stopping ----


def test_a_wait_whose_run_is_already_stopping_asks_nothing(fleet) -> None:
    mac = fleet.mac(9010)

    async def waited() -> None:
        stopping = asyncio.Event()
        stopping.set()
        with fleet.client() as client:
            await wait_for_address(client, "pve", 9010, mac, 30.0, stopping)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(waited())
    assert fleet.polls(9010) == 0


def test_a_wait_ends_between_polls_once_the_run_is_stopping(fleet, monkeypatch) -> None:
    mac = _started(fleet)
    _pve(fleet.base, "PUT", "fake/agent-ready-after", polls=10**6)
    monkeypatch.setattr(agent, "PAUSES", (30.0,))

    async def waited() -> float:
        stopping = asyncio.Event()
        with fleet.client() as client:
            waiting = asyncio.ensure_future(wait_for_address(client, "pve", 9020, mac, 300.0, stopping))
            while not fleet.polls(9020):
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.1)
            stopping.set()
            began = time.monotonic()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            return time.monotonic() - began

    assert asyncio.run(waited()) < 2
    assert fleet.polls(9020) == 1


def test_a_wait_ends_during_a_poll_once_the_run_is_stopping(fleet) -> None:
    mac = fleet.mac(9010)
    asked, release = threading.Event(), threading.Event()

    def held(node: str, vmid: int, deadline: float | None = None) -> answers.Interfaces:
        asked.set()
        release.wait(30)
        return answers.Interfaces(node, vmid, ())

    async def waited() -> float:
        stopping = asyncio.Event()
        with fleet.client() as client:
            client.agent_interfaces = held
            waiting = asyncio.ensure_future(wait_for_address(client, "pve", 9010, mac, 300.0, stopping))
            await asyncio.to_thread(asked.wait, 10)
            stopping.set()
            began = time.monotonic()
            try:
                with pytest.raises(asyncio.CancelledError):
                    await waiting
            finally:
                release.set()
            return time.monotonic() - began

    assert asyncio.run(waited()) < 2


# ------------------------------------------------------------------------- the connection, and its bounds ----


class _Once:
    """A transport that raises error at its first request and answers every later one with answer - or raises it
    again, where answer is None - and keeps the path of each."""

    def __init__(self, error: Exception, answer: Answer | None) -> None:
        self.error, self.answer = error, answer
        self.paths: list[str] = []

    def request(self, method: str, path: str, **kwargs) -> Answer:
        self.paths.append(path)
        if len(self.paths) == 1 or self.answer is None:
            raise self.error
        return self.answer

    def close(self) -> None:
        pass


MAC = "bc:24:11:00:23:32"
REPORTED = Answer(200, (), json.dumps({"data": {"result": [_card(MAC, "192.0.2.7")]}}).encode(), "OK")


def _standing_in(pki) -> Proxmox:
    """A client whose reads and agent calls are each a stand-in's, given after."""
    return Proxmox("https://127.0.0.1:9", pin.kept(pki["ca"]), Token(f"{ENGINE_USER}!{ENGINE_TOKEN}", Secret(CANARY)))


@pytest.mark.parametrize(
    "error",
    [
        Unreachable("the connection could not be made: Connection refused"),
        Unreachable("the connection was dropped: Connection reset by peer"),
    ],
    ids=["refused", "reset"],
)
def test_a_connection_refused_or_reset_is_not_yet_and_the_next_poll_gives_the_address(pki, error) -> None:
    """pveproxy restarting, or the computer's network coming back, seconds after a start: not yet, as a node's 595 is.
    The transport tried the GET three times first."""
    client = _standing_in(pki)
    client._transport, client._agent = _Answers(VERSION), (polls := _Once(error, REPORTED))
    assert asyncio.run(wait_for_address(client, "pve", 9010, MAC, 30.0, asyncio.Event())) == "192.0.2.7"
    assert polls.paths == [AGENT, AGENT]
    # Where every poll meets it, the deadline says so.
    client._agent = _Once(error, REPORTED)
    client._agent.answer = None
    with pytest.raises(NoAddress) as none:
        asyncio.run(wait_for_address(client, "pve", 9010, MAC, 0.2, asyncio.Event()))
    assert none.value.said == f"the last poll: {error}"


@pytest.mark.parametrize("where", ["from the transport", "from the call"])
def test_a_certificate_that_is_not_the_host_s_ends_the_wait_at_once(pki, where) -> None:
    """Untrusted is an Unreachable of its own: never taken for not yet, nor is the client's Unverified."""
    client = _standing_in(pki)
    untrusted = Untrusted("unable to get local issuer certificate", 20)
    client._transport, client._agent = _Answers(VERSION), (polls := _Once(untrusted, REPORTED))
    if where == "from the call":

        def refused(node: str, vmid: int, deadline: float | None = None) -> answers.Interfaces:
            polls.paths.append(AGENT)
            raise untrusted

        client.agent_interfaces = refused
    began = time.monotonic()
    with pytest.raises(Unverified if where == "from the transport" else Untrusted):
        asyncio.run(wait_for_address(client, "pve", 9010, MAC, 60.0, asyncio.Event()))
    assert time.monotonic() - began < 5 and polls.paths == [AGENT]


@pytest.mark.parametrize("kept", ["another CA", "another fingerprint"])
def test_the_agent_s_connection_is_pinned_as_the_client_s_is(fleet, tmp_path, kept) -> None:
    """The agent's calls carry the token on a transport of their own, held to the same pin: where it refuses the fake's
    certificate nothing goes. The client's other transport is a stand-in that answers the version, so that the agent's
    connection alone meets the fake."""
    if kept == "another CA":
        pinned = pin.kept(proxmox_pki(tmp_path / "other-pki", key_usage=True, name="Another Test CA")["ca"])
    else:
        pinned = pin.kept(fleet.pki["node"], ":".join(["AB"] * 32))
    with Proxmox(fleet.base, pinned, fleet.token) as client:
        client._transport = _Answers(VERSION)
        with pytest.raises(Unverified):
            client.agent_interfaces("pve", 9010)
    assert fleet.polls(9010) == 0 and fleet.asked() == []


def test_each_poll_is_held_to_the_longest_one_agent_call_usefully_runs(fleet) -> None:
    mac = fleet.mac(9010)
    asked = []

    def polled(node: str, vmid: int, deadline: float | None = None) -> answers.Interfaces:
        asked.append(deadline)
        if len(asked) == 2:
            raise Denied(403, "GET its agent")
        raise TimedOut("no answer within 10 s")

    async def waited() -> float:
        with fleet.client() as client:
            client.agent_interfaces = polled
            with pytest.raises(Denied):
                await wait_for_address(client, "pve", 9010, mac, 300.0, asyncio.Event())
            return client.agent_call_most

    most = asyncio.run(waited())
    assert most == AGENT_READ + pin.CONNECT == 15.0
    assert asked == [most, most]


class _Silent:
    """A transport that answers nothing: each request waits what its call's deadline gives, or five seconds where it
    gives none (the transport's own is 120), then is the transport's TimedOut."""

    def __init__(self) -> None:
        self.deadlines: list[float | None] = []

    def request(self, method: str, path: str, deadline: float | None = None, **kwargs) -> Answer:
        self.deadlines.append(deadline)
        time.sleep(deadline if deadline is not None else 5.0)
        raise TimedOut("no answer within 30 s")

    def close(self) -> None:
        pass


def test_a_new_client_s_first_poll_is_held_to_the_wait_s_deadline_its_version_included(pki) -> None:
    client = _standing_in(pki)
    client._transport, client._agent = (reads := _Silent()), _Silent()
    began = time.monotonic()
    with pytest.raises(NoAddress):
        asyncio.run(wait_for_address(client, "pve", 9010, MAC, 0.5, asyncio.Event()))
    took = time.monotonic() - began
    assert took < 1.5, took
    assert reads.deadlines and all(left is not None and 0 < left <= 0.5 for left in reads.deadlines)


def _holding(pki, release: threading.Event) -> _Server:
    """A TLS server on loopback with the fake's node certificate, as the fake serves it: GET /version answered as
    Proxmox VE 9 answers it, and every other request held, nothing said, until release is set; .asked, each request."""

    def behave(handler, asked, nth) -> None:
        if asked.path.endswith("/version"):
            return answer(200, VERSION.body)(handler, asked, nth)
        release.wait(60)
        handler.close_connection = True
        return None

    server = _Server(("127.0.0.1", 0), _Handler)
    server.asked, server.lock, server.behave = [], threading.Lock(), behave
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pki["cert"], pki["key"])
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return server


def test_a_poll_a_stopping_run_left_ends_within_its_bound_and_sends_nothing_once_the_client_is_closed(
    pki, monkeypatch
) -> None:
    """The run stops during a poll the host holds, and leaves its client: the poll's thread, not read, ends at its
    first error, within the longest one agent call runs, and its GET, which carries the token, is never sent again on
    a new connection. asyncio.run returns once that thread has ended. The agent's read waits a second here."""
    monkeypatch.setattr(client_module, "AGENT_READ", 1.0)
    release = threading.Event()
    server = _holding(pki, release)
    polled = f"/api2/json/{AGENT}"
    token = Token(f"{ENGINE_USER}!{ENGINE_TOKEN}", Secret(CANARY))

    async def waited() -> tuple[float, float]:
        stopping = asyncio.Event()
        with Proxmox(f"https://127.0.0.1:{server.server_address[1]}", pin.kept(pki["ca"]), token) as client:
            waiting = asyncio.ensure_future(wait_for_address(client, "pve", 9010, MAC, 300.0, stopping))
            while not [one for one in server.asked if one.path == polled]:
                await asyncio.sleep(0.02)
            stopping.set()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            most = client.agent_call_most
        return time.monotonic(), most

    try:
        closed, most = asyncio.run(waited())
        ended = time.monotonic()
        assert most == 1.0 + pin.CONNECT and ended - closed < most, ended - closed
        agent_asked = [one for one in server.asked if one.path == polled]
    finally:
        release.set()
        server.shutdown()
        server.server_close()
    assert len(agent_asked) == 1 and agent_asked[0].headers["authorization"].startswith("PVEAPIToken=")


# --------------------------------------------------------------------------- held before anything is sent ----

GIVEN = {
    "no MAC": {"mac": ""},
    "a MAC of five parts": {"mac": "bc:24:11:00:23"},
    "a MAC and a newline": {"mac": "bc:24:11:00:23:32\n"},
    "a MAC that is no text": {"mac": None},
    "a deadline of 0": {"deadline": 0},
    "a deadline below 0": {"deadline": -1},
    "a deadline that is a boolean": {"deadline": True},
    "a deadline that is text": {"deadline": "5"},
    "a node with a path in it": {"node": "pve/../x"},
    "a VMID below Proxmox's": {"vmid": 99},
    "a VMID that is text": {"vmid": "9010"},
    "cancelled that is no asyncio.Event": {"cancelled": threading.Event()},
    "no client": {"client": None},
}


@pytest.mark.parametrize("given", GIVEN.values(), ids=GIVEN.keys())
def test_a_wait_that_is_not_one_the_engine_asks_is_refused_before_anything_is_sent(fleet, given) -> None:
    async def waited() -> None:
        with fleet.client() as client:
            asked = {
                "client": client,
                "node": "pve",
                "vmid": 9010,
                "mac": "bc:24:11:00:23:32",
                "deadline": 30.0,
                "cancelled": asyncio.Event(),
                **given,
            }
            await wait_for_address(**asked)

    with pytest.raises((ValueError, TypeError)):
        asyncio.run(waited())
    assert fleet.asked() == []


def test_the_agent_s_call_is_held_before_anything_is_sent(fleet) -> None:
    with fleet.client() as client:
        for args in (("pve/../x", 9010), ("pve", 99), ("pve", 9010, 0), ("pve", 9010, "5")):
            with pytest.raises(ValueError):
                client.agent_interfaces(*args)
        with pytest.raises(ValueError):
            address([], "not a MAC")
    assert fleet.asked() == []


# ------------------------------------------------------------------------------------------------- the rules ----


class _Answers:
    """A transport that answers each request with the next of answers, the last again once they run out, and keeps
    the path of each."""

    def __init__(self, *given: Answer) -> None:
        self.given = list(given)
        self.paths: list[str] = []

    def request(self, method: str, path: str, **kwargs) -> Answer:
        self.paths.append(path)
        return self.given.pop(0) if len(self.given) > 1 else self.given[0]

    def close(self) -> None:
        pass


VERSION = Answer(200, (), b'{"data": {"version": "9.2.21", "release": "9.2"}}', "OK")
AGENT = "nodes/pve/qemu/9010/agent/network-get-interfaces"


def _echo(words: str) -> Answer:
    """A refusal whose status line's words are words."""
    return Answer(500, (), b'{"data": null}', words)


def test_the_agent_s_calls_go_on_connections_of_their_own_whose_reads_wait_less(pki, monkeypatch) -> None:
    made = []
    real = pin.transport

    def recorded(url, pinned, proxy=None, read=30.0):
        made.append(read)
        return real(url, pinned, proxy, read)

    monkeypatch.setattr(pin, "transport", recorded)
    client = Proxmox("https://127.0.0.1:9", pin.kept(pki["ca"]), Token(f"{ENGINE_USER}!{ENGINE_TOKEN}", Secret(CANARY)))
    assert made == [30.0, AGENT_READ] and AGENT_READ < 30.0
    reads, polls = _Answers(VERSION), _Answers(Answer(200, (), b'{"data": {"result": []}}', "OK"))
    client._transport, client._agent = reads, polls
    assert client.agent_interfaces("pve", 9010).interfaces == ()
    assert (reads.paths, polls.paths) == (["version"], [AGENT])


def test_the_words_the_client_tells_a_stopped_vm_by_are_the_fake_s() -> None:
    assert REFUSED_STATUS == SAID["AGENT_REFUSED_STATUS"] == 500
    found = NOT_RUNNING.search(SAID["VM_NOT_RUNNING"].format(vmid=9012))
    assert found and found.group(1) == "9012"
    assert _stopped(SAID["VM_NOT_RUNNING"].format(vmid=9012), 9012)
    assert not _stopped(SAID["VM_NOT_RUNNING"].format(vmid=9012), 901)
    assert not _stopped(SAID["VM_NOT_RUNNING"].format(vmid=90120), 9012)
    assert not _stopped(SAID["AGENT_NOT_RUNNING"], 9012)
    assert NO_AGENT.fullmatch(SAID["NO_AGENT"]) and not _stopped(SAID["NO_AGENT"], 9012)
    assert not NO_AGENT.search(SAID["AGENT_NOT_RUNNING"]) and not NO_AGENT.search(SAID["VM_NOT_RUNNING"])
    # Read whole, never found within Proxmox's words: what follows "failed - " is where a VM's could be.
    within = "VM 9012 qga command 'guest-network-get-interfaces' failed - got wrong command id 'VM 9012 is not running'"
    assert not _stopped(within, 9012) and _stopped(f" {SAID['VM_NOT_RUNNING'].format(vmid=9012)}\n", 9012)


def test_the_agent_s_call_is_one_row_of_the_table_on_the_vm() -> None:
    rows = privileges.for_request("GET", f"/{AGENT}")
    assert [(call.name, call.privilege, at) for call, at in rows] == [
        ("agent_interfaces", "VM.GuestAgent.Audit", "/vms/9010")
    ]
    assert "VM.GuestAgent.Audit" in privileges.needed("pool")


def test_today_s_wait_is_the_default_deadline_and_the_pauses_grow_to_today_s() -> None:
    # roles/proxmox_vm polls 60 times, 5 s apart.
    assert DEADLINE == 300.0 and PAUSES[-1] == 5.0 and list(PAUSES) == sorted(PAUSES)


ECHOES = {
    "the secret": f"QEMU guest agent is not running: {CANARY}",
    "the secret cut by a control character": f"{CANARY[:7]}\x07{CANARY[7:]} refused",
    "the secret where the words are cut": "x" * 180 + CANARY,
}


@pytest.mark.parametrize("echo", ECHOES.values(), ids=ECHOES.keys())
def test_no_error_of_the_wait_says_the_token_s_secret_even_where_proxmox_s_words_echo_it(
    pki, caplog, capsys, echo
) -> None:
    caplog.set_level(logging.DEBUG)
    raised = []
    # Proxmox's sentence with more after it is no stopped VM, nor one with no agent: a refusal, said at the deadline.
    for given, kind in (
        (_echo(echo), NoAddress),
        (_echo(f"VM 9010 is not running {echo}"), NoAddress),
        (_echo(f"No QEMU guest agent configured {echo}"), NoAddress),
    ):

        async def waited(given: Answer = given) -> None:
            token = Token(f"{ENGINE_USER}!{ENGINE_TOKEN}", Secret(CANARY))
            with Proxmox("https://127.0.0.1:9", pin.kept(pki["ca"]), token) as client:
                client._transport, client._agent = _Answers(VERSION), _Answers(given)
                await wait_for_address(client, "pve", 9010, "bc:24:11:00:23:32", 0.2, asyncio.Event())

        with pytest.raises(kind) as error:
            asyncio.run(waited())
        raised.append(error.value)
    parts = {CANARY[at : at + 10] for at in range(len(CANARY) - 9)}
    for error in raised:
        said = _said(error, caplog, capsys, frames=False) + [getattr(error, "said", "")]
        assert not [text for text in said if any(part in text for part in parts)], said
        assert error.__cause__ is None
    assert isinstance(raised[0], NoAddress) and "500" in raised[0].said
    if echo == ECHOES["the secret"]:
        assert MARK in raised[0].said


def test_a_refused_poll_is_refused_and_says_proxmox_s_words_struck(pki) -> None:
    token = Token(f"{ENGINE_USER}!{ENGINE_TOKEN}", Secret(CANARY))
    with Proxmox("https://127.0.0.1:9", pin.kept(pki["ca"]), token) as client:
        client._transport, client._agent = _Answers(VERSION), _Answers(_echo(f"got timeout {CANARY}"))
        with pytest.raises(Refused) as refused:
            client.agent_interfaces("pve", 9010)
    assert refused.value.status == 500 and CANARY not in str(refused.value) and MARK in refused.value.reason
