"""How this machine reaches the VMs of one Proxmox host: straight to each VM's address, or through the host's own sshd
as a forward (docs/architecture/custom-code.md §7.17). The rule that picks one, the hop's ssh command, the refusal of
anything in it that is not one plain word, the proxy options a VM is given, and the hop's own ssh run under a hard
time limit, its exit status and standard error read and nothing else. Stdlib only; nothing is written but ssh's own
socket, by ssh.

Every value that reaches the hop's command is held to one fixed set of characters, never quoted: ssh runs a
ProxyCommand through the account's shell after its own %-expansion, so a quote, a space, a % or a $ would be the
shell's or ssh's to read. The VM's address is the one value a VM supplies, and only one IPv4 address is taken.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import shlex
import socket
import tempfile
import time

ROUTES = ("auto", "direct", "host")
# Tailscale's addresses, IPv4's shared range (RFC 6598) and its ULA prefix: a host reached at one is reached on a
# tailnet, where the VMs' own network is reached only through a subnet router, if any.
TAILNET = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))

# What may reach the hop's command. A user and a host name as ssh takes them, never starting with "-", which ssh would
# read as an option; a port; a path, absolute or as the user wrote it, of these characters only.
_USER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,63}")
_NAME = re.compile(r"(?=.{1,253}\Z)[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,62})(?:\.[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,62}))*\.?")
_PATH = re.compile(r"[A-Za-z0-9_./+@,=-]{1,4096}")
# macOS keeps a socket's path to 103 bytes, and ssh adds 17 while it binds one (a "." and 16 random characters).
SOCKET_MOST = 86

# The hard limits, in seconds, on each ssh run for the hop itself: an open that loses a race to another never returns
# by itself, and a forward to an address that drops packets says nothing for minutes.
LIMITS = {"check": 5, "stop": 5, "open": 20, "forward": 15, "config": 5}
# The hop's own connection: a dead one ends within 45 s rather than hanging its clients, and one kept lives 120 s past
# its last use.
KEEP = ("ServerAliveInterval=15", "ServerAliveCountMax=3")
PERSIST = 120

# ssh's options that take an argument, as its getopt string has them (OpenSSH 9 and 10): a word after one of these is
# its value, never an option, and the rest of a word after one is the value.
_SSH_VALUED = set("BbcDEeFIiJLlmOoPpQRSWw")


class HopRefused(ValueError):
    """A setting of the hop that is not one plain word, said by name."""


def tailnet(address) -> bool:
    """Whether ``address`` is a tailnet one."""
    try:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError:
        return False
    return any(ip in network for network in TAILNET)


def route_rule(setting, addresses) -> str:
    """``host`` or ``direct``: the route ``setting`` names, or under ``auto``, ``host`` where every one of ``addresses``
    (what ssh dials for the Proxmox host) is a tailnet one and there is at least one. Never a fallback either way."""
    setting = str(setting or "auto")
    if setting not in ROUTES:
        raise ValueError(f"vm_ssh_route is one of {', '.join(ROUTES)}, not {setting!r}")
    if setting != "auto":
        return setting
    addresses = list(addresses or [])
    return "host" if addresses and all(tailnet(one) for one in addresses) else "direct"


def ssh_g_hostname(text, written) -> str:
    """Where ssh would dial for a host, from what ``ssh -G`` printed for it: its ``hostname`` line, else ``written``."""
    for line in str(text or "").splitlines():
        word, _, value = line.strip().partition(" ")
        if word.lower() == "hostname" and value.strip():
            return value.strip()
    return str(written)


def addresses_of(name) -> list[str]:
    """``name``'s addresses as this machine resolves it now; none for a name that does not resolve."""
    try:
        found = socket.getaddrinfo(str(name), None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError, ValueError):
        return []
    return sorted({str(info[4][0]) for info in found})


def account(value) -> str:
    """``vm_ssh_jump``: one account on the Proxmox host, or "" for none. A user and a host, or a port, are refused by
    name: the hop is always the Proxmox host itself, at its own address and port."""
    value = "" if value is None else str(value)
    if not value:
        return ""
    if "@" in value or ":" in value:
        raise HopRefused(
            f"vm_ssh_jump {value!r}: the hop is the Proxmox host itself; vm_ssh_jump names another account on it"
        )
    if not _USER.fullmatch(value):
        raise HopRefused(f"vm_ssh_jump {value!r} is not an account name")
    return value


def _host(value) -> str:
    value = str(value or "")
    if "%" in value:  # an IPv6 zone, which ssh would expand as one of its tokens
        raise HopRefused(f"the Proxmox host's address {value!r} is not one host name or address")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if not _NAME.fullmatch(value):
        raise HopRefused(f"the Proxmox host's address {value!r} is not one host name or address")
    return value


def _path(value, what) -> str:
    value = os.path.expanduser(str(value or ""))
    if not _PATH.fullmatch(value) or value.startswith("-"):
        raise HopRefused(
            f"{what} {value!r} has a character other than letters, digits and _./+@,=- in it (a space, a % or a quote"
            " among them): it would reach a shell"
        )
    return value


def hop_settings(ssh="ssh", user="", host="", port=None, key="", control_dir="~/.ansible/cp", name="") -> dict:
    """The hop's settings, each held: ``{ssh, user, host, port, key, socket, name, label}``. ``socket`` is its shared
    connection's path in ``control_dir``, named by a digest of the hop; ``name`` the Proxmox host's inventory name, for
    the words a run says. Raises HopRefused, naming the setting, for any value that is not one plain word."""
    user = "" if user is None else str(user)
    if user and not _USER.fullmatch(user):
        raise HopRefused(f"the hop's user {user!r} is not an account name")
    host = _host(host)
    if port in (None, ""):
        port = None
    else:
        try:
            port = int(str(port))
        except ValueError:
            port = 0
        if not 0 < port < 65536:
            raise HopRefused("the hop's port is not a port number")
    key = _path(key, "the hop's key file") if key else ""
    ssh = _path(ssh or "ssh", "the ssh program")
    directory = _path(control_dir or "~/.ansible/cp", "the control directory").rstrip("/") or "/"
    digest = hashlib.sha256(f"{user} {host} {port or ''}".encode()).hexdigest()[:12]
    path = f"{directory}/awh-{digest}"
    if len(path.encode()) > SOCKET_MOST:
        raise HopRefused(
            f"the hop's socket, {path}, is longer than {SOCKET_MOST} bytes, which macOS cannot bind: set"
            " ansible_control_path_dir to a shorter directory"
        )
    reached = f"{user}@{host}" if user else host
    label = f"{name} ({reached})" if name and name != reached else reached
    return {"ssh": ssh, "user": user, "host": host, "port": port, "key": key, "socket": path, "label": label}


def _target(address) -> str:
    try:
        ip = ipaddress.IPv4Address(str(address))
    except ValueError:
        raise HopRefused(f"{address!r} is not one IPv4 address") from None
    return str(ip)


def _destination(hop: dict) -> str:
    return f"{hop['user']}@{hop['host']}" if hop["user"] else hop["host"]


def _common(hop: dict, policy: str) -> list[str]:
    if policy not in ("yes", "accept-new"):
        raise ValueError(f"no host key policy {policy!r}")
    return [
        hop["ssh"],
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        f"StrictHostKeyChecking={policy}",
        "-o",
        "UpdateHostKeys=no",
        "-o",
        "ForwardAgent=no",
    ]


def _place(hop: dict) -> list[str]:
    return [*(["-p", str(hop["port"])] if hop["port"] else []), *(["-i", hop["key"]] if hop["key"] else [])]


def _shared(hop: dict) -> list[str]:
    # ControlPath and ControlPersist together, in every command that names the socket: Ansible scans each word of a
    # VM's command line for "controlpersist", else "controlpath", and adds the VM's own ControlPath only where it
    # found the first (ssh.py's _persistence_controls).
    return ["-o", "ControlMaster=auto", "-o", f"ControlPath={hop['socket']}", "-o", f"ControlPersist={PERSIST}"]


def hop_argv(hop: dict, address, policy: str, port="%p", shared=True) -> list[str]:
    """The hop's ssh, forwarding to ``address``'s ``port``: ``%p`` inside a ProxyCommand, which ssh makes the VM's
    port. ``shared`` false makes the forward of a connection of its own (ControlPath=none)."""
    if port != "%p" and not (str(port).isdigit() and 0 < int(port) < 65536):
        raise HopRefused(f"{port!r} is not a port number")
    keep = [word for option in KEEP for word in ("-o", option)]
    sharing = _shared(hop) if shared else ["-o", "ControlPath=none"]
    return [
        *_common(hop, policy),
        *sharing,
        *keep,
        *_place(hop),
        "-W",
        f"{_target(address)}:{port}",
        _destination(hop),
    ]


def hop_option(hop: dict, address, policy: str) -> str:
    """The VM's hop as words of its ssh_common_args: one ProxyCommand, quoted as one word for Ansible's split."""
    return "-o " + shlex.quote("ProxyCommand=" + " ".join(hop_argv(hop, address, policy)))


def hop_via(hop: dict) -> str:
    """The words a person's own ssh needs to reach a VM through the hop: ``-J user@host[:port]``."""
    return f"-J {jump_spec(hop)}"


def jump_spec(hop: dict) -> str:
    """The hop as a ProxyJump names it: ``user@host[:port]``, an IPv6 address in brackets."""
    host = hop["host"]
    if ":" in host:
        host = f"[{host}]"
    port = f":{hop['port']}" if hop["port"] and int(hop["port"]) != 22 else ""
    return f"{hop['user']}@{host}{port}" if hop["user"] else f"{host}{port}"


def _ssh_words(args) -> list[str]:
    if isinstance(args, (list, tuple)):
        return [str(word) for word in args]
    try:
        return shlex.split(str(args or ""))
    except ValueError:
        return str(args or "").split()


def ssh_proxies(args) -> list[tuple[str, str]]:
    """Every proxy option among ``args`` (a string as Ansible splits it, or a list), as ``(option, value)``: ``-J``,
    ``ProxyJump`` and ``ProxyCommand`` in any of ssh's spellings - ``-o Name=value``, ``-oName=value``, ``-o 'Name
    value'``, flags run together before ``-J`` or ``-o``."""
    words = _ssh_words(args)
    found = []
    at = 0
    while at < len(words):
        word = words[at]
        at += 1
        if not word.startswith("-") or word == "-" or word.startswith("--"):
            continue
        letters = word[1:]
        for place, letter in enumerate(letters):
            if letter not in _SSH_VALUED:
                continue
            value = letters[place + 1 :]
            if not value and at < len(words):
                value = words[at]
                at += 1
            if letter == "J":
                found.append(("-J", value))
            elif letter == "o":
                named = re.match(r"\s*([A-Za-z]+)\s*(?:=\s*|\s+)(.*)", value, re.S)
                if named and named.group(1).lower() in ("proxyjump", "proxycommand"):
                    option = "ProxyJump" if named.group(1).lower() == "proxyjump" else "ProxyCommand"
                    found.append((option, named.group(2).strip()))
            break
    return found


def _none(option: str, value: str) -> bool:
    # ssh reads "none" as no ProxyCommand only spelled so, and as no ProxyJump in any case.
    return value == "none" if option == "ProxyCommand" else value.lower() == "none"


def ssh_jump(*args_lists) -> str:
    """The first jump among each of ``args_lists``, as ``Option value``: a ``-J``, a ``ProxyJump`` or a
    ``ProxyCommand`` that is not ``none``; "" where there is none."""
    for args in args_lists:
        for option, value in ssh_proxies(args):
            if not _none(option, value):
                return f"{option} {value}" if option != "-J" else f"-J {value}"
    return ""


def ssh_proxy_any(args) -> str:
    """The first proxy option of any kind among ``args``, ``none`` included, as ``Option value``; "" for none."""
    for option, value in ssh_proxies(args):
        return f"{option} {value}" if option != "-J" else f"-J {value}"
    return ""


def vm_hop(route, address, given="", extra="") -> dict:
    """A VM's hop, from the run's route (``{route, hop, policy}``, the claude_vm_route fact) and what the VM is given:
    ``{option, name, via, jump}``. ``option`` is the words its ssh_common_args gains, ``name`` what its socket's name
    is made with, ``via`` the words a person's own ssh needs; all three "" where the route is direct, where the VM has
    no address, and where it is given a jump of its own (``given``, its common args; ``extra``, its
    ansible_ssh_extra_args), which ``jump`` then says."""
    none = {"option": "", "name": "", "via": "", "jump": ""}
    if not isinstance(route, dict) or route.get("route") != "host" or not address:
        return none
    jump = ssh_jump(given, extra)
    if jump:
        return {**none, "jump": jump}
    hop = route["hop"]
    return {
        "option": hop_option(hop, address, route["policy"]),
        "name": os.path.basename(hop["socket"]),
        "via": hop_via(hop),
        "jump": "",
    }


def _run(argv: list[str], limit: float, until: str = "") -> dict:
    """``argv`` run with nothing on its standard input and its standard output discarded - what a forward carries is
    the far end's to write - killed at ``limit`` seconds, or as soon as its standard error holds ``until``:
    ``{rc, said, killed}``, ``said`` its standard error, ``rc`` None where it was killed."""
    import subprocess  # only where ssh is run: imported with the package, it would slow every run's start

    with tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors)
        except OSError as error:
            return {"rc": 255, "said": f"{argv[0]}: {error.strerror or error}", "killed": False}
        end = time.monotonic() + limit
        killed = False
        while process.poll() is None:
            if time.monotonic() >= end or (until and until.encode() in _read(errors.fileno())):
                process.kill()
                killed = True
                break
            time.sleep(0.05)
        process.wait()
        said = _read(errors.fileno()).decode(errors="replace").replace("\r", "")
    return {"rc": None if killed else process.returncode, "said": said.strip(), "killed": killed}


def _read(fd: int) -> bytes:
    # pread, which moves no offset: ssh writes through the same open file.
    return os.pread(fd, 65536, 0)


def hop_check(hop: dict, policy: str, limit=None) -> bool:
    """Whether this machine holds the hop's shared connection (``-O check``). Asks this machine's ssh process only: a
    connection whose far side has gone says it runs until its keepalives end it, 45 s at most."""
    argv = [*_common(hop, policy), *_shared(hop), *_place(hop), "-O", "check", _destination(hop)]
    return _run(argv, limit or LIMITS["check"])["rc"] == 0


def hop_stop(hop: dict, policy: str, limit=None) -> bool:
    """The shared connection told to take no new sessions (``-O stop``): the forwards it carries finish, and the next
    connection opens a new one at the same path. Never ``-O exit``, which would cut another run's tasks."""
    argv = [*_common(hop, policy), *_shared(hop), *_place(hop), "-O", "stop", _destination(hop)]
    return _run(argv, limit or LIMITS["stop"])["rc"] == 0


def hop_held(hop: dict, policy: str, limits=None) -> dict:
    """Make sure of the hop's shared connection: ``{ok, opened, said}``. ``-O check`` first, never an open beside a
    live connection - ``-N`` there starts a session on the host, a shell on a full account. Then an open, with none of
    the user's forwards for that host; an open that loses the race to another prints that the socket exists and waits
    for good, so it is ended then, or at its limit, and ``-O check`` decides once more."""
    limits = {**LIMITS, **(limits or {})}
    # Ansible's control directory, as Ansible makes it for a connection of its own: 0700, its owner's alone.
    os.makedirs(os.path.dirname(hop["socket"]), mode=0o700, exist_ok=True)
    if hop_check(hop, policy, limits["check"]):
        return {"ok": True, "opened": False, "said": ""}
    # None of the user's own forwards for that host come up with it.
    unforwarded = ["-o", "ClearAllForwardings=yes"]
    argv = [*_common(hop, policy), *_shared(hop), *_place(hop), *unforwarded, "-N", _destination(hop)]
    opened = _run(argv, limits["open"], until="already exists, disabling multiplexing")
    if hop_check(hop, policy, limits["check"]):
        return {"ok": True, "opened": opened["rc"] == 0, "said": ""}
    said = opened["said"] or (f"no answer within {limits['open']} s" if opened["killed"] else "")
    return {"ok": False, "opened": False, "said": said}


# What one unshared forward's ending says of the hop, read in this order from its exit status and standard error.
FORWARD = {
    "prohibited": "the host refused to forward to it",
    "answered": "the far end answered",
    "refused": "the host tried: the far end did not take it",
    "silent": "no answer from the far end",
    "failed": "ssh failed to use the host as a hop",
}


def forward_verdict(rc, said, killed) -> dict:
    """``{usable, why, said}`` for a forward that ended with ``rc`` (None where it was killed at its limit) and
    standard error ``said``. Only a refusal to forward, or ssh's own failure, makes the hop unusable: anything slower is
    the far end's doing, and one VM that drops packets must not stop a run."""
    said = str(said or "")
    if "administratively prohibited" in said:
        why = "prohibited"
    elif rc == 0 and not killed:
        why = "answered"
    elif "connect failed:" in said:
        why = "refused"
    elif killed:
        why = "silent"
    else:
        why = "failed"
    return {"usable": why in ("answered", "refused", "silent"), "why": why, "said": said}


def hop_forward(hop: dict, address, policy: str, port=22, limit=None) -> dict:
    """One forward to ``address``'s ``port`` on a connection of its own, its standard input closed: what it says of
    the hop (forward_verdict), from its exit status and standard error alone."""
    ended = _run(hop_argv(hop, address, policy, port=port, shared=False), limit or LIMITS["forward"])
    return forward_verdict(ended["rc"], ended["said"], ended["killed"])


def route_settle(setting, hop: dict, limit=None) -> dict:
    """The run's route: ``{route, addresses, dialled}``. Under ``auto``, where ssh would dial for the Proxmox host
    decides: its host as ``ssh -G`` resolves it - a HostName of the user's counts - or as written where that fails,
    then that name's addresses here, now."""
    import subprocess

    setting = str(setting or "auto")
    if setting != "auto":
        return {"route": route_rule(setting, []), "addresses": [], "dialled": ""}
    argv = [hop["ssh"], *_place(hop), "-G", _destination(hop)]
    try:
        shown = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=limit or LIMITS["config"],
        )
        text = shown.stdout if shown.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        text = ""
    dialled = ssh_g_hostname(text, hop["host"])
    addresses = addresses_of(dialled)
    return {"route": route_rule("auto", addresses), "addresses": addresses, "dialled": dialled}


def _given(hostvars, name, given_all, extra_all) -> tuple[str, str]:
    """What a VM is given, its common args and its extra args: a VM published already in its group, as its groups and
    -e give it (the common args the publisher keeps as claude_vm_ssh_given first); any other, what every host is."""
    if hostvars is not None and name in hostvars:
        host = hostvars[name]
        given = host.get("claude_vm_ssh_given", host.get("ansible_ssh_common_args", given_all))
        return str(given or ""), str(host.get("ansible_ssh_extra_args", extra_all) or "")
    return str(given_all or ""), str(extra_all or "")


def vm_route_vms(vms, hostvars=None, given_all="", extra_all="") -> list[dict]:
    """Of ``vms`` (``{name, address}``, or a name, whose address is the proxmox_vm_address provision.yml's play left on
    it), each with an address, by name: ``{name, address, jump}``, ``jump`` the jump the VM is given (ssh_jump of its
    common and extra args), "" where it goes through the hop."""
    found = []
    for vm in vms or []:
        if isinstance(vm, str) and hostvars is not None and vm in hostvars:
            vm = {"name": vm, "address": hostvars[vm].get("proxmox_vm_address") or ""}
        if not isinstance(vm, dict) or not vm.get("address"):
            continue
        name = str(vm.get("name") or "")
        given, extra = _given(hostvars, name, given_all, extra_all)
        found.append({"name": name, "address": str(vm["address"]), "jump": ssh_jump(given, extra)})
    return sorted(found, key=lambda vm: vm["name"])


def vm_route_unheld(names, hostvars, settled, ssh_args_configured="", extra_configured="") -> list[dict]:
    """Of the VMs ``names`` just published, each whose connection, as ``hostvars`` has it, would not go where the route
    says, ``{name, why}``: through the host, every VM with an address carries the hop first among its proxy options,
    or brings a jump of its own; never neither. One that carries the hop has no other proxy option where ssh would
    read it first: its ssh_args, ansible.cfg's or the environment's where it has none of its own, nor ssh_extra_args
    from ansible.cfg or the environment where it has no ansible_ssh_extra_args. A host var or -e that outranks the
    publisher's value is found here, before anything connects."""
    if not isinstance(settled, dict) or settled.get("route") != "host":
        return []
    unheld = []
    for name in names or []:
        host = hostvars[str(name)]
        address = str(host.get("ansible_host") or "")
        if not address or str(host.get("ansible_connection") or "ssh").rsplit(".", 1)[-1] != "ssh":
            continue
        common = str(host.get("ansible_ssh_common_args") or "")
        extra = host.get("ansible_ssh_extra_args")
        if not host.get("claude_vm_ssh_via"):
            if not ssh_jump(common, extra or ""):
                unheld.append({"name": str(name), "why": "neither the hop nor a jump of its own"})
            continue
        wanted = ("ProxyCommand", " ".join(hop_argv(settled["hop"], address, settled["policy"])))
        proxies = ssh_proxies(common)
        if proxies[:1] != [wanted]:
            unheld.append({"name": str(name), "why": "ansible_ssh_common_args is set where it outranks the project's"})
            continue
        before = ssh_proxy_any(host.get("ansible_ssh_args", ssh_args_configured))
        after = ssh_jump(extra) if extra is not None else ssh_proxy_any(extra_configured)
        if before:
            unheld.append({"name": str(name), "why": f"ssh_args holds {before}, which ssh would take before the hop"})
        elif after:
            where = "ansible_ssh_extra_args" if extra is not None else "ssh_extra_args (ansible.cfg or the environment)"
            unheld.append({"name": str(name), "why": f"{where} holds {after} beside the hop"})
    return unheld


def _plain(said) -> str:
    from .text import one_line

    return one_line(said, 400).rstrip(".") or "no reason given"


def _direct_tail(settled) -> str:
    if settled.get("setting") != "auto":
        return ""
    return (
        " Where a VPN routes the VMs' network, or a jump of your own reaches them, set vm_ssh_route: direct:"
        " under auto, a host reached on a tailnet is a hop."
    )


def route_said(settled) -> str:
    """The run's one line about its route, said once: "" where it is direct."""
    if not isinstance(settled, dict) or settled.get("route") != "host":
        return ""
    return (
        f"VMs are reached through {settled['hop']['label']}. A VM that does not answer shows as a closed connection;"
        " `just status` says what the host reports for it."
    )


def login_said(settled, said) -> str:
    """Why the hop's login failed, and the ways out."""
    hop = settled["hop"]
    return (
        f"VMs are reached through {hop['label']} (vm_ssh_route: {settled.get('setting', 'auto')}), and ssh could not"
        f" log in there: {_plain(said)}. Its key may be missing from the agent and from the host's entry"
        " (ansible_ssh_private_key_file), the host's key may have changed, or the login may be refused:"
        f" `ssh {_destination(hop)}` here shows which.{_direct_tail(settled)}"
    )


def forward_said(settled, name, address, verdict) -> str:
    """What one forward said of the hop: why it cannot be used, or that the VM said nothing; "" where it answered."""
    hop = settled["hop"]
    why = verdict.get("why")
    vm = f"{name} at {address}" if name else str(address)
    if why == "prohibited":
        return (
            f"{hop['label']} refused to forward to {vm}: {_plain(verdict.get('said'))}. Its sshd forbids it -"
            " AllowTcpForwarding, PermitOpen, DisableForwarding, or restrict on the key - or Tailscale's own SSH"
            " server answers there and its policy does: each gives the same reply. Allow forwarding to the VMs there,"
            f" or name an account that may (vm_ssh_jump).{_direct_tail(settled)}"
        )
    if why == "failed":
        said = _plain(verdict.get("said"))
        return f"ssh could not use {hop['label']} as a hop to {vm}: {said}.{_direct_tail(settled)}"
    if why == "silent":
        return (
            f"No answer from {vm} through {hop['label']} within {LIMITS['forward']} s: it fails alone when it is first"
            " reached."
        )
    return ""


def vm_unanswered(settled, via, name, host_flag="") -> str:
    """What a VM's first contact that failed adds: through the host, that it did not answer there and where to look;
    under direct, the setting that would go through it; nothing for a VM that brings its own jump, nor for a run that
    settled no route (VMs of the inventory's own, which no publisher adds)."""
    if not isinstance(settled, dict) or settled.get("route") not in ("host", "direct"):
        return ""
    if settled.get("route") == "host":
        if not via:
            return ""
        return (
            f"It did not answer through {settled['hop']['label']}: `just status {name}{host_flag}` says what the host"
            " reports for it."
        )
    return (
        "Where this machine reaches the Proxmox host but not its VMs' network, vm_ssh_route: host carries every"
        " connection through the host."
    )


def hop_key_offered(text, key) -> bool:
    """Whether ``ssh -G`` (``text``) for the hop lists ``key`` among the identity files it would offer, each expanded
    as ssh expands ~; one with a % token in it is taken as not known."""
    offered = set()
    for line in str(text or "").splitlines():
        word, _, value = line.strip().partition(" ")
        if word.lower() == "identityfile" and value.strip() and "%" not in value:
            offered.add(os.path.expanduser(value.strip()))
    return os.path.expanduser(str(key or "")) in offered
