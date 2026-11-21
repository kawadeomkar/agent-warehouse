"""A started VM's address, from its guest agent: one IPv4 address on the VM's own network card, waited for
(docs/architecture/engine.md §16.5).

The agent runs inside the VM, so what it reports is the VM's to choose, and the address it gives becomes what every
connection to the VM dials. So address() reads it only on the network card whose MAC the VM's configuration gives -
a bridge the guest grows later (docker0) is never taken - and only through today's checks.guest_ipv4 as it stands,
which takes nothing but what ipaddress.IPv4Address parses as one address that a card can answer at: never 0.0.0.0,
multicast, broadcast or the reserved range, and never "192.0.2.7" with a newline and a ProxyCommand after it, which
would be a directive of its own in an SSH config. Of two good addresses on the card it takes the first, as today.

wait_for_address() polls the agent, as today's product polls network-get-interfaces, pausing longer each time up to
today's five seconds, until the card has such an address, within a deadline, and stops at once where waiting cannot
help: a token without the agent's privilege (Denied, at the first 401 or 403) and a VM that is not running
(NotRunning, which is never waited on). Anything else - the agent not answering yet, an answer with no such address,
an answer of another shape, a poll that ran out of time - is "not yet". It ends when the run's cancelled is set. Each
poll runs the blocking client in a thread, so the run's loop goes on meanwhile.

Standard library only; asyncio is imported by the coroutine that runs on it.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .. import checks, proxmox
from ..vendors.transport import TimedOut
from .answers import Interfaces, ProxmoxError
from .client import Proxmox, Refused

if TYPE_CHECKING:
    import asyncio

__all__ = ["NoAddress", "address", "wait_for_address"]

# How long a VM's address is waited for, where the workflow names no other: today's, 60 polls five seconds apart
# (roles/proxmox_vm's proxmox_vm_agent_retries and proxmox_vm_agent_delay).
DEADLINE = 300.0
# The pauses between polls, growing to the last, which is kept: today's five seconds once the agent is slow to come.
PAUSES = (1.0, 2.0, 5.0)


class NoAddress(ProxmoxError):
    """The deadline came and the VM's guest agent had given no IPv4 address on its own network card: what Proxmox
    said of the last poll, where it refused one."""

    def __init__(self, vmid: int, mac: str, seconds: float, said: str) -> None:
        self.vmid = vmid
        self.mac = mac
        self.seconds = seconds
        self.said = said
        why = f" ({said})" if said else ""
        super().__init__(
            f"VM {vmid}'s guest agent reported no IPv4 address on its network card {mac} within {seconds:g} s: the"
            f" template must carry the guest agent, and the VM's bridge a DHCP server{why}"
        )


def _seconds(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not value > 0:
        raise ValueError("a deadline is a number of seconds above 0")
    return float(value)


def _mac(mac: str) -> str:
    if not isinstance(mac, str) or not proxmox.MAC_RE.fullmatch(mac):
        raise ValueError("a VM's address is read on its own network card, by its MAC: six pairs of hex digits")
    return mac.lower()


def _addresses(given: Any) -> list[dict[str, Any]]:
    """Each address entry of given that is a mapping, with the two values guest_ipv4 reads."""
    if not isinstance(given, Sequence) or isinstance(given, str | bytes):
        return []
    return [
        {"ip-address": one.get("ip-address"), "ip-address-type": one.get("ip-address-type")}
        for one in given
        if isinstance(one, Mapping)
    ]


def address(interfaces: Interfaces | Sequence[Mapping[str, Any]], mac: str) -> str | None:
    """The VM's address from what its guest agent reported (agent_interfaces' Interfaces, or the cards as a list):
    the IPv4 address checks.guest_ipv4 takes on the card whose MAC is mac, the VM's own, as it stands; None where there
    is none yet. Every card whose MAC is no text, and every part of the answer of another shape, counts as nothing, so
    that no answer a VM chooses raises here."""
    wanted = _mac(mac)
    listed = interfaces.interfaces if isinstance(interfaces, Interfaces) else interfaces
    if not isinstance(listed, Sequence) or isinstance(listed, str | bytes):
        return None
    cards = [
        {"hardware-address": card["hardware-address"], "ip-addresses": _addresses(card.get("ip-addresses"))}
        for card in listed
        if isinstance(card, Mapping) and isinstance(card.get("hardware-address"), str)
    ]
    return checks.guest_ipv4(cards, wanted)


async def wait_for_address(
    client: Proxmox, node: str, vmid: int, mac: str, deadline: float, cancelled: asyncio.Event
) -> str:
    """The address of the started VM vmid on node, on its network card whose MAC is mac (VmConfig.mac), from its guest
    agent, polled within deadline seconds (DEADLINE is today's) with a pause between polls (PAUSES). Raises Denied at
    the first 401 or 403, NotRunning at once for a VM that is not running, NoAddress at the deadline, and
    asyncio.CancelledError once cancelled is set, the run stopping, between polls or during one."""
    import asyncio

    if not isinstance(client, Proxmox):
        raise TypeError("the address is asked of the Proxmox client")
    if not isinstance(cancelled, asyncio.Event):
        raise TypeError("cancelled is the run's asyncio.Event")
    wanted, seconds = _mac(mac), _seconds(deadline)
    end = time.monotonic() + seconds
    pauses = iter(PAUSES)
    said = ""
    while True:
        if cancelled.is_set():
            raise asyncio.CancelledError(f"the run is stopping: VM {vmid}'s address is no longer waited for")
        left = end - time.monotonic()
        if left <= 0:
            raise NoAddress(vmid, wanted, seconds, said)
        found, met = await _poll(client, node, vmid, left, cancelled)
        if found is not None:
            got = address(found, wanted)
            if got is not None:
                return got
            said = "the last poll: its agent answered, with no IPv4 address on that card"
        elif isinstance(met, Refused) or not said:
            # A poll that ran out of time keeps what an earlier one met: the deadline may have come during it.
            said = f"the last poll: {met}"
        pause = min(next(pauses, PAUSES[-1]), end - time.monotonic())
        if pause > 0:
            try:
                await asyncio.wait_for(cancelled.wait(), pause)
            except TimeoutError:
                pass


async def _poll(
    client: Proxmox, node: str, vmid: int, left: float, cancelled: asyncio.Event
) -> tuple[Interfaces | None, Refused | TimedOut | None]:
    """One poll of the agent, in a thread, held to left seconds: what it reported, and None; or None, and what the
    poll met where Proxmox refused it or it ran out of time. Denied, NotRunning and every other error pass as they are.
    Where cancelled is set first, asyncio.CancelledError: the thread ends by its own limits, and its answer is not
    read."""
    import asyncio

    poll = asyncio.ensure_future(asyncio.to_thread(client.agent_interfaces, node, vmid, left))
    stop = asyncio.ensure_future(cancelled.wait())
    try:
        await asyncio.wait({poll, stop}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop.cancel()
        if not poll.done():
            poll.cancel()
    if not poll.done():
        raise asyncio.CancelledError(f"the run is stopping: VM {vmid}'s address is no longer waited for")
    met: Refused | TimedOut | None = None
    try:
        return poll.result(), None
    except (Refused, TimedOut) as error:
        met = error
    return None, met
