"""The checks of what a VM, the Proxmox host or a person supplies before anything uses it: a guest agent's IPv4
address, a name ssh would take for another host, and a name Proxmox gives a node, a storage or a bridge. Stdlib
only, nothing read or written."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import shlex

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


# The name a VM's SSH host key is filed under (ssh_host_key_alias in proxmox.py): this project's, and the VM's SMBIOS
# UUID, which Proxmox holds and the VM cannot change.
HOST_KEY_ALIAS_RE = re.compile(r"agent-warehouse-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def vm_ssh_word(path):
    """``path`` as one word of an ssh option's value: in double quotes, which ssh takes as one file where it would take
    a space as the end of one (UserKnownHostsFile lists files). A path with a double quote, a newline or a NUL in it is
    no path ssh could be given, and is refused."""
    path = str(path or "")
    if not path or any(ch in path for ch in '"\n\r\0'):
        raise ValueError("an ssh option needs a path with no double quote or line break in it")
    return f'"{path}"'


def vm_ssh_args(alias, known_hosts, forward_agent=False):
    """The ssh options every connection to a VM is made with, as ssh_common_args takes them - for ssh, sftp and scp
    alike: its host key looked up, and a new one filed, in the project's own known_hosts file, under ``alias`` - the
    VM's identity, never the address it is reached at, which the VM reports - and agent forwarding off unless
    ``forward_agent``, whatever the user's own ssh config says for every host. A VM with no alias (a config with no
    SMBIOS UUID) is filed by its address, in that file still. ssh keeps the first value given for an option, so
    ansible.cfg's ssh_args, given before these, stand."""
    alias = "" if alias is None else str(alias)
    if alias and not HOST_KEY_ALIAS_RE.fullmatch(alias):
        raise ValueError(f"{alias!r} is not a VM's host key alias")
    options = [f"UserKnownHostsFile={vm_ssh_word(known_hosts)}", "CheckHostIP=no", "UpdateHostKeys=no"]
    options += [f"HostKeyAlias={alias}"] if alias else []
    options += [] if forward_agent else ["ForwardAgent=no"]
    return " ".join(f"-o {shlex.quote(option)}" for option in options)


def vm_control_name(alias, address):
    """What names a VM's shared-connection socket, before ssh's own -%r-%p (its user and port, which a session over an
    open master never asks again): a digest of its identity and the address it is reached at, so that a VM reporting
    another's address never rides that one's open connection, on which no host key is checked again. Short, since a
    socket's path is at most 103 characters on macOS, ssh adds 17 while it makes one, and the directory holds the
    account's name: ssh's %C alone, 40, left a long account name too little room."""
    alias = "" if alias is None else str(alias)
    if alias and not HOST_KEY_ALIAS_RE.fullmatch(alias):
        raise ValueError(f"{alias!r} is not a VM's host key alias")
    return f"aw-{hashlib.sha256(f'{alias} {address}'.encode()).hexdigest()[:12]}"


def vm_ssh_unheld(vms, hostvars, known_hosts):
    """The names of ``vms`` (records with a name, or names) whose connection, as ``hostvars`` has it, would not hold
    the host key to the project's file: no ``UserKnownHostsFile`` of it among their ssh_common_args, or another
    identity than their own. A host var or an extra var of the same name outranks the publisher's."""
    wanted = f"UserKnownHostsFile={vm_ssh_word(known_hosts)}"
    unheld = []
    for vm in vms or []:
        name = str(vm.get("name") if isinstance(vm, dict) else vm)
        host = (hostvars or {}).get(name) or {}
        try:
            words = shlex.split(str(host.get("ansible_ssh_common_args") or ""))
        except ValueError:
            words = []
        options = [words[at + 1] for at, word in enumerate(words[:-1]) if word == "-o"]
        alias = str(host.get("claude_vm_host_key_alias") or "")
        aliases = [option.split("=", 1)[1] for option in options if option.startswith("HostKeyAlias=")]
        if wanted not in options or (alias and aliases[:1] != [alias]):
            unheld.append(name)
    return unheld


def known_hosts_due(vms, lines, kept):
    """Of ``vms`` ({name, address, key_alias, created}), each whose host keys are still to be carried over: an identity
    and an address, no key under that identity in the project's file (``kept``, ``ssh-keygen -F`` of it, looped over
    the same records), and no mark that its carry-over was made among the file's ``lines``."""
    marked = {line.split()[-1] for line in lines or [] if str(line).startswith("# agent-warehouse carried ")}
    held = {
        str(result["claude_host_keys_vm"].get("key_alias"))
        for result in kept or []
        if isinstance(result, dict) and result.get("rc") == 0 and isinstance(result.get("claude_host_keys_vm"), dict)
    }
    due = []
    for vm in vms or []:
        if not isinstance(vm, dict):
            continue
        alias, address = str(vm.get("key_alias") or ""), str(vm.get("address") or "")
        if alias and address and HOST_KEY_ALIAS_RE.fullmatch(alias) and alias not in marked | held:
            due.append({**vm, "created": bool(vm.get("created"))})
    return due


# The key types a known_hosts line may carry, as OpenSSH names them; and its key, base64.
KNOWN_KEY_TYPES = frozenset(
    {
        "ssh-ed25519",
        "ssh-rsa",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "sk-ssh-ed25519@openssh.com",
        "sk-ecdsa-sha2-nistp256@openssh.com",
    }
)
KNOWN_KEY_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}")


def ssh_args_split(text):
    """ssh arguments as Ansible splits ssh_common_args and ssh_extra_args, for an ssh the project runs itself: the
    forward of Claude's sign-in reaches the VM as every run does (vm_ssh_args among them)."""
    return shlex.split(str(text or ""))


def known_hosts_keys(results, loop_var):
    """The keys ``ssh-keygen -F`` found in each looped result, as [{name, key_alias, key}]: the VM the result's
    ``loop_var`` holds ({name, key_alias}), and ``key`` the line's "<type> <base64>" with its host field left off, so
    that it can be filed under the VM's identity. Only a plain key line is taken: a comment, a @cert-authority or
    @revoked marker, an unknown type or a key that is not base64 is left out."""
    keys = []
    for result in results or []:
        if not isinstance(result, dict) or result.get("rc") != 0 or result.get("skipped"):
            continue
        vm = result.get(loop_var)
        if not isinstance(vm, dict) or not HOST_KEY_ALIAS_RE.fullmatch(str(vm.get("key_alias") or "")):
            continue
        for line in str(result.get("stdout") or "").splitlines():
            fields = line.split()
            if len(fields) < 3 or fields[0].startswith(("#", "@")):
                continue
            if fields[1] in KNOWN_KEY_TYPES and KNOWN_KEY_RE.fullmatch(fields[2]):
                keys.append(
                    {"name": str(vm.get("name") or ""), "key_alias": vm["key_alias"], "key": f"{fields[1]} {fields[2]}"}
                )
    return keys


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
