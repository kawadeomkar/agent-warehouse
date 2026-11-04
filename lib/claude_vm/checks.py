"""The checks of what a VM, the Proxmox host or a person supplies before anything uses it: a guest agent's IPv4
address, and a name ssh would take for another host. Stdlib only, nothing read or written."""

from __future__ import annotations

import ipaddress
import re

# A name ssh could read as an IPv4 address, by its spelling alone: one to four
# parts between dots, each decimal, octal or 0x-hex, of any size, and anything
# after a space. That is everything inet_aton accepts - glibc's, which ssh on
# Linux resolves with, and macOS's, which also takes 4294967296 - and more, on
# every platform alike, rather than whatever this machine's own inet_aton says.
ADDRESS_LIKE_RE = re.compile(r"(?:0x[0-9a-f]*|[0-9]+)(?:\.(?:0x[0-9a-f]*|[0-9]+)){0,3}(?:\s.*)?", re.I | re.S)


def guest_ipv4(interfaces, mac=None):
    """Return the guest's IPv4 address from ``network-get-interfaces`` output.

    ``interfaces`` is what the QEMU guest agent reports. When ``mac`` is given,
    only the matching interface is considered, which keeps later bridges the
    guest may grow (docker0, podman, virbr0) from being picked up on a re-run.
    Loopback and link-local addresses are never returned, nor any a VM can
    have no NIC answer at: 0.0.0.0, which reaches this machine itself as a
    destination, multicast, broadcast and the reserved 240.0.0.0/4. Returns
    ``None`` when no address is available yet, so a wait loop can poll on it.

    The agent runs inside the guest, so whatever it reports is the VM's to
    choose, and the result becomes ``ansible_host`` and the ``HostName`` of an
    alias in this machine's ssh config. Only a string ``ipaddress`` parses as one
    IPv4 address is taken: "192.0.2.7" followed by a newline and a ProxyCommand
    would otherwise be a directive of its own in that file, run on this machine
    at the next ``ssh``. Anything else counts as no address yet.
    """
    if not interfaces:
        return None
    if not isinstance(interfaces, list):
        raise ValueError(f"guest_ipv4 expects a list, got {type(interfaces).__name__}")

    wanted = mac.lower() if mac else None
    for iface in interfaces:
        if not isinstance(iface, dict):
            continue
        if wanted and iface.get("hardware-address", "").lower() != wanted:
            continue
        if not wanted and iface.get("name") == "lo":
            continue
        for address in iface.get("ip-addresses") or []:
            if address.get("ip-address-type") != "ipv4":
                continue
            ip = _ipv4(address.get("ip-address"))
            if ip is None or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
                continue
            if ip.is_reserved:  # 240.0.0.0/4, 255.255.255.255 among them
                continue
            return str(ip)
    return None


def _ipv4(text):
    """The IPv4 address ``text`` is, or None for anything else (a newline, a space, a leading "-", a non-string)."""
    if not isinstance(text, str):
        return None
    try:
        return ipaddress.IPv4Address(text)
    except ValueError:
        return None


def _alias_refusal(name, hosts, chosen):
    """``(why, unnamed)``: why ``name`` cannot be an alias, ``""`` when it can, and whether only a dot stops it."""
    name = str(name)
    if ADDRESS_LIKE_RE.fullmatch(name):
        return "ssh reads it as an address (as it reads 3232235777 or 0x7f000001)", False
    if name in set(hosts or []):
        return "it names a host of the inventory, which ssh would then reach at the VM's address", False
    if "." in name and name not in set(chosen or []):
        return "it has a dot, so it could be a host's own name (github.com), and ssh would take it for that host", True
    return "", False


def ssh_alias_refusal(name, hosts=None, chosen=None):
    """Why ``name`` cannot be the alias of a VM in this machine's ssh config, or ``""`` when it can.

    The managed file is included at the top of ``~/.ssh/config``, so an alias wins
    over every other way ssh reaches that name: one named like a real host
    (``github.com``), an address, or a name this machine reaches a host of the
    inventory by (``hosts``: the Proxmox host's name and address) would send this
    machine's own connections to it - and where the VM's name comes from a shared
    Proxmox, someone else chose it. A name with a dot is an alias only when the
    person chose it (``chosen``: the names given, never a fleet-wide run's); an
    address or one of ``hosts`` never is.
    """
    return _alias_refusal(name, hosts, chosen)[0]


def ssh_alias_refusals(vms, hosts=None, chosen=None):
    """The entries of ``vms`` (``{name, ...}``) whose name cannot be an alias, in order.

    Each as ``{name, why, unnamed}``, ``unnamed`` true where only its dot stops it:
    named, it would get its alias, so a fleet-wide run says so rather than "rename".
    """
    if isinstance(vms, (str, dict)) or not hasattr(vms, "__iter__"):
        raise ValueError(f"ssh_alias_refusals expects a list, got {type(vms).__name__}")
    refused = []
    for vm in vms:
        name = vm.get("name", "") if isinstance(vm, dict) else ""
        why, unnamed = _alias_refusal(name, hosts, chosen)
        if why:
            refused.append({"name": str(name), "why": why, "unnamed": unnamed})
    return refused
