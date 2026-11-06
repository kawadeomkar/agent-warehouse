"""The checks of what a VM, the Proxmox host or a person supplies before anything uses it: a guest agent's IPv4
address, a name ssh would take for another host, and a name Proxmox gives a node, a storage or a bridge. Stdlib
only, nothing read or written."""

from __future__ import annotations

import ipaddress
import json
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


def ssh_hostname(vm, domain=""):
    """Where a VM's alias points, its ``HostName``: ``<name>.<domain>`` where the network resolves VM names
    (``domain``, claude_ssh_host_domain), else the address its guest agent reported, ``""`` for none.

    The alias's block, the check that the user's ssh config reaches it, and the connection just code makes all
    read it here, so the editor and Ansible connect to the same place.
    """
    if not isinstance(vm, dict):
        raise ValueError(f"ssh_hostname expects a VM, got {type(vm).__name__}")
    if domain:
        return f"{vm['name']}.{domain}"
    return str(vm.get("address") or "")


def sshd_match_spec(ssh_connection, user):
    """What ``sshd -T -C`` evaluates its Match blocks for: ``user``'s sessions, coming from where the connection
    Ansible made comes from. ``ssh_connection`` is its ``SSH_CONNECTION`` (client address, client port, local address,
    local port); a container or a local run has none, and the loopback address stands in."""
    fields = str(ssh_connection or "").split()
    if len(fields) == 4:
        return f"user={user},host={fields[0]},addr={fields[0]},laddr={fields[2]},lport={fields[3]}"
    return f"user={user},host=127.0.0.1,addr=127.0.0.1"


_LABEL = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")

# What Proxmox itself allows as the name of a node (a host name's first label),
# a storage (pve-storage-id) and a bridge (a network interface's name). just
# init writes such a name into local.yml, which every later run reads as a
# template: a name the API or root SSH reported is held to these first, so
# one that holds anything else - {{ ... }} above all - is never offered or
# written, whoever answered for the host.
PROXMOX_NAMES = {
    "node": _LABEL,
    "storage": re.compile(r"(?i)[a-z][a-z0-9_.-]*[a-z0-9]"),
    "bridge": re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}"),
}


def proxmox_name(value, kind):
    """Return ``value`` when it is a name Proxmox gives a ``kind`` (node, storage, bridge), else ``""``."""
    if kind not in PROXMOX_NAMES:
        raise ValueError(f"proxmox_name: no kind {kind!r}; one of {', '.join(PROXMOX_NAMES)}")
    return value if isinstance(value, str) and PROXMOX_NAMES[kind].fullmatch(value) else ""


def proxmox_names(values, kind):
    """The items of ``values`` that are names Proxmox gives a ``kind``, in their order."""
    return [value for value in values or [] if proxmox_name(value, kind)]


def proxmox_names_refused(values, kind):
    """The items of ``values`` that are not, each as JSON, so whatever it holds is shown and never acted on."""
    return [json.dumps(str(value)) for value in values or [] if not proxmox_name(value, kind)]
