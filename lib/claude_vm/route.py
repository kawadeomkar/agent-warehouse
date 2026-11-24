"""How this machine reaches the VMs of one Proxmox host: straight to each VM's address, or through the host's own sshd
as a forward (docs/architecture/custom-code.md §7.17). The rule that picks one, the hop's ssh command, the refusal of
anything in it that is not one plain word, the proxy options a VM is given and the one ssh itself shows it would
take, and the hop's own ssh run under a hard time limit, its exit status and standard error read and nothing else.
Stdlib only; nothing is written but ssh's own socket, by ssh, and nothing removed but a socket file of the hop's that
nothing listens at.

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
import signal
import socket
import tempfile
import threading
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
LIMITS = {"check": 5, "stop": 5, "open": 20, "forward": 15, "config": 5, "resolve": 5}
# The hop's own connection: a dead one ends within 60 s rather than hanging its clients - a keepalive after 15 s of
# silence, the fourth unanswered one ends it; the keepalives are the open's, which makes it, and each forward's - and
# one kept lives 120 s past its last use.
KEEP = ("ServerAliveInterval=15", "ServerAliveCountMax=3")
PERSIST = 120

# ssh's options that take an argument, as its getopt string has them (OpenSSH 9 and 10): a word after one of these is
# its value, never an option, and the rest of a word after one is the value.
_SSH_VALUED = set("BbcDEeFIiJLlmOoPpQRSWw")


class HopRefused(ValueError):
    """A setting of the hop that is not one plain word, said by name: ``reason``, and ``way_out``, what to change."""

    def __init__(self, reason, way_out=""):
        super().__init__(f"{reason}: {way_out}" if way_out else reason)
        self.reason = reason
        self.way_out = way_out


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


def addresses_of(name, limit=None) -> list[str]:
    """``name``'s addresses as this machine resolves it now; none for a name that does not resolve, nor for one that
    gets no answer within ``limit`` seconds: a lookup cannot be ended, so it is left to finish in a thread of its own,
    which keeps nothing from ending."""
    found: list = []

    def resolve():
        try:
            found.extend(socket.getaddrinfo(str(name), None, proto=socket.IPPROTO_TCP))
        except (OSError, UnicodeError, ValueError):
            pass

    lookup = threading.Thread(target=resolve, name="route-resolve", daemon=True)
    lookup.start()
    lookup.join(limit or LIMITS["resolve"])
    if lookup.is_alive():
        return []
    return sorted({str(info[4][0]) for info in found})


def account(value) -> str:
    """``vm_ssh_jump``: one account on the Proxmox host, or "" for none. A user and a host, or a port, are refused by
    name: the hop is always the Proxmox host itself, at its own address and port."""
    value = "" if value is None else str(value)
    if not value:
        return ""
    # Each short enough to be read whole where a line is cut (just status's), the way out beside the reason.
    way_out = "set vm_ssh_jump on the host's entry to one account on that host, or vm_ssh_route: direct"
    if "@" in value or ":" in value:
        raise HopRefused(f"vm_ssh_jump {value!r} names more than an account", way_out)
    if not _USER.fullmatch(value):
        raise HopRefused(f"vm_ssh_jump {value!r} is not an account name", way_out)
    return value


# Each setting of the hop's, by the name it is set under on the Proxmox host's entry, for the way out a refusal names.
WAY_OUT = {
    "user": "ansible_user (or vm_ssh_jump)",
    "host": "ansible_host",
    "port": "ansible_port",
    "key": "ansible_ssh_private_key_file",
    "ssh": "ansible_ssh_executable",
    "control_dir": "ansible_control_path_dir",
}


def _refused(said: str, setting: str) -> HopRefused:
    return HopRefused(
        said, f"set {WAY_OUT[setting]} on the Proxmox host's entry to one that is, or set vm_ssh_route: direct"
    )


def _host(value) -> str:
    value = str(value or "")
    if "%" in value:  # an IPv6 zone, which ssh would expand as one of its tokens
        raise _refused(f"the Proxmox host's address {value!r} is not one host name or address", "host")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if not _NAME.fullmatch(value):
        raise _refused(f"the Proxmox host's address {value!r} is not one host name or address", "host")
    return value


def _path(value, what, setting) -> str:
    value = os.path.expanduser(str(value or ""))
    # A leading - is an option to ssh, and a leading = is expanded by zsh, the account's shell that runs the hop.
    if not _PATH.fullmatch(value) or value.startswith(("-", "=")):
        raise _refused(
            f"{what} {value!r} has a character other than letters, digits and _./+@,=- in it (a space, a % or a quote"
            " among them), or starts with - or =: it would reach a shell",
            setting,
        )
    return value


def hop_settings(
    ssh="ssh", user="", host="", port=None, key="", control_dir="~/.ansible/cp", name="", *, sized=True, pinned=False
) -> dict:
    """The hop's settings, each held: ``{ssh, user, host, port, key, socket, label}``. ``socket`` is its shared
    connection's path in ``control_dir``, named by a digest of the hop; ``label`` names it in the words a run says,
    with ``name``, the Proxmox host's inventory name. Raises HopRefused, naming the setting and the way out, for any
    value that is not one plain word, and for a socket past SOCKET_MOST bytes. The new engine's two, which today's
    callers pass neither of: ``sized`` false leaves a socket past SOCKET_MOST to the caller, which says it in its own
    words (socket_fits); ``pinned`` makes every line the hop's own ssh is run with pin ForwardX11=no and
    PermitLocalCommand=no first (_common), as the new engine pins them for a machine, kept as ``pinned`` in the
    settings, so that the hop's command a machine is given and the one it is held to are the same."""
    user = "" if user is None else str(user)
    if user and not _USER.fullmatch(user):
        raise _refused(f"the hop's user {user!r} is not an account name", "user")
    host = _host(host)
    if port in (None, ""):
        port = None
    else:
        try:
            port = int(str(port))
        except ValueError:
            port = 0
        if not 0 < port < 65536:
            raise _refused("the hop's port is not a port number", "port")
    key = _path(key, "the hop's key file", "key") if key else ""
    ssh = _path(ssh or "ssh", "the ssh program", "ssh")
    directory = _path(control_dir or "~/.ansible/cp", "the control directory", "control_dir").rstrip("/") or "/"
    digest = hashlib.sha256(f"{user} {host} {port or ''}".encode()).hexdigest()[:12]
    path = f"{directory}/awh-{digest}"
    if sized and not socket_fits(path):
        raise HopRefused(
            f"the hop's socket, {path}, is longer than {SOCKET_MOST} bytes, the most a socket's path may be here (macOS"
            " binds none longer)",
            "set ansible_control_path_dir to a shorter directory, or set vm_ssh_route: direct",
        )
    reached = f"{user}@{host}" if user else host
    label = f"{name} ({reached})" if name and name != reached else reached
    settings = {"ssh": ssh, "user": user, "host": host, "port": port, "key": key, "socket": path, "label": label}
    return {**settings, "pinned": True} if pinned else settings


def socket_fits(path) -> bool:
    """Whether a shared connection's socket at ``path`` can be bound: at most SOCKET_MOST bytes."""
    return len(str(path).encode()) <= SOCKET_MOST


def _target(address) -> str:
    try:
        ip = ipaddress.IPv4Address(str(address))
    except ValueError:
        raise HopRefused(f"{address!r} is not one IPv4 address") from None
    return str(ip)


def _destination(hop: dict) -> str:
    return f"{hop['user']}@{hop['host']}" if hop["user"] else hop["host"]


def _common(hop: dict, policy: str, level: str = "INFO") -> list[str]:
    if policy not in ("yes", "accept-new"):
        raise ValueError(f"no host key policy {policy!r}")
    if level not in ("INFO", "VERBOSE"):
        raise ValueError(f"no log level {level!r}")
    return [
        hop["ssh"],
        # The new engine's hop (hop_settings' pinned): this computer's X display never handed to the host, no program
        # run here for a connection, and none of a person's forwards or tunnel opened, whatever their config says for
        # the host - a master the open makes, or a ProxyCommand's ssh that finds none live and becomes one, would run
        # their LocalCommand; and -W clears forwards only by default, so a ClearAllForwardings no of theirs would have
        # the ProxyCommand a machine is given, and the forward check, open each LocalForward, RemoteForward,
        # DynamicForward and Tunnel they give the host. The open carries its own ClearAllForwardings too, today's.
        *(
            ["-o", "ForwardX11=no", "-o", "PermitLocalCommand=no", "-o", "ClearAllForwardings=yes"]
            if hop.get("pinned")
            else []
        ),
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
        # What ssh says is read: never quieter for a LogLevel of the user's, where a refused forward and a closed port
        # would both be "stdio forwarding failed", or nothing at all. VERBOSE for the forward check alone, where ssh
        # says when its login is done (forward_verdict); never in a VM's ProxyCommand, whose words are in every VM's
        # failure.
        "-o",
        f"LogLevel={level}",
    ]


def _place(hop: dict) -> list[str]:
    return [*(["-p", str(hop["port"])] if hop["port"] else []), *(["-i", hop["key"]] if hop["key"] else [])]


def _shared(hop: dict, master: str = "auto") -> list[str]:
    # ControlPath and ControlPersist together, in every command that names the socket: Ansible scans each word of a
    # VM's command line for "controlpersist", else "controlpath", and adds the VM's own ControlPath only where it
    # found the first (ssh.py's _persistence_controls).
    return ["-o", f"ControlMaster={master}", "-o", f"ControlPath={hop['socket']}", "-o", f"ControlPersist={PERSIST}"]


def _keep() -> list[str]:
    return [word for option in KEEP for word in ("-o", option)]


def hop_argv(hop: dict, address, policy: str, port="%p", shared=True, level="INFO") -> list[str]:
    """The hop's ssh, forwarding to ``address``'s ``port``: ``%p`` inside a ProxyCommand, which ssh makes the VM's
    port. ``shared`` false makes the forward of a connection of its own (ControlPath=none); ``level`` is ssh's
    LogLevel, VERBOSE for the forward check alone."""
    if port != "%p" and not (str(port).isdigit() and 0 < int(port) < 65536):
        raise HopRefused(f"{port!r} is not a port number")
    sharing = _shared(hop) if shared else ["-o", "ControlPath=none"]
    return [
        *_common(hop, policy, level),
        *sharing,
        *_keep(),
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
    # Split as Ansible splits ssh's arguments (its ssh plugin's _split_ssh_args): each word stripped, a blank one
    # dropped.
    if isinstance(args, (list, tuple)):
        words = [str(word) for word in args]
    else:
        try:
            words = shlex.split(str(args or ""))
        except ValueError:
            words = str(args or "").split()
    return [word.strip() for word in words if word.strip()]


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
                # ssh takes a keyword in quotes, and after a leading "=" or blank.
                named = re.match(r"[\s=]*\"?([A-Za-z]+)\"?\s*(?:=\s*|\s+)(.*)", value, re.S)
                if named and named.group(1).lower() in ("proxyjump", "proxycommand"):
                    option = "ProxyJump" if named.group(1).lower() == "proxyjump" else "ProxyCommand"
                    found.append((option, named.group(2).strip()))
            break
    return found


def _none(option: str, value: str) -> bool:
    # ssh reads "none", in any case, as no ProxyCommand and as no ProxyJump.
    del option
    return value.lower() == "none"


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


# Every ssh this module has started and not yet waited for - _run's, and the ssh -G of route_settle and ssh_shown - so
# that a caller about to end at once (os._exit, which runs no finally) can end them first (end_all), and once it has,
# _OVER: none is started after. Each is started and added in one step under the lock end_all takes (_started), so that
# end_all never runs between the two. _LEADING, those of them that lead a process group of their own (process_group=0,
# the new engine's calls): each is ended with its group (_kill), and watched by the new engine's run for a stop at the
# terminal (leading).
_LIVE: set = set()
_LEADING: set = set()
_LIVE_LOCK = threading.Lock()
_OVER = threading.Event()


class NotStarted(OSError):
    """An ssh not started, since end_all has run: the run that would have waited for it is ending."""


def _kill(process) -> None:
    """``process`` killed, never once it has been reaped (as Popen.kill), with its process group where it leads one
    (_LEADING: started with process_group=0, the new engine's), so that what it started there - the program of a
    person's ``Match exec``, a ProxyCommand's - ends with it; by its pid alone where it leads none: today's callers
    start their ssh in their own process's group, which a group's end would signal. A leader that has exited and is
    not yet reaped is ended with its group too: an ``ssh -G`` whose ``Match exec`` put a program in the background
    exits and leaves it there. Decided by ``returncode``, set once it is reaped here, never by poll(), which would reap
    that leader and leave its group running; a zombie keeps its pid, so its group's number is no other's."""
    if process.returncode is not None:
        return
    if process in _LEADING:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def end_all() -> None:
    """End every ssh this process runs for the hop, ``ssh -G`` among them, whatever thread waits on it, each with its
    process group where it leads one (_kill), and start none after: for a caller that ends at once, before the threads'
    own ``finally`` could. One being started meanwhile is waited for and ended with the rest. Each is reaped by the
    thread that started it, or by init."""
    with _LIVE_LOCK:
        _OVER.set()
        live = list(_LIVE)
    for process in live:
        _kill(process)


def leading() -> list:
    """The ssh this module runs in a process group of its own (process_group=0, the new engine's calls), still running
    or not yet reaped: children of this process that a question at the terminal can stop, which the new engine's run
    watches as it watches its own children. Today's callers start none."""
    with _LIVE_LOCK:
        return [process for process in _LIVE if process in _LEADING]


def _started(argv: list[str], **popen):
    """``argv`` started with nothing on its standard input and Popen's ``popen``, and added to _LIVE in one step under
    the lock end_all takes: an end_all meanwhile waits for the start and ends it with the rest, and once one has run
    nothing is started (NotStarted). One started with ``process_group`` 0 leads a group of its own (_LEADING). OSError
    where it cannot be started. The caller waits for it, then discards it from _LIVE (_forget)."""
    import subprocess

    with _LIVE_LOCK:
        if _OVER.is_set():
            raise NotStarted(f"{argv[0]}: not started, the run ended")
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, **popen)
        _LIVE.add(process)
        if popen.get("process_group") == 0:
            _LEADING.add(process)
    return process


def _forget(process) -> None:
    with _LIVE_LOCK:
        _LIVE.discard(process)
        _LEADING.discard(process)


def _asked(argv: list[str], limit: float, *, process_group=None):
    """``argv``, an ``ssh -G``, run as ``subprocess.run`` runs it - its standard output read whole as text, a
    subprocess.CompletedProcess, and subprocess.TimeoutExpired once ``limit`` seconds have passed - but started as _run
    starts its ssh (_started), so that end_all ends it and starts none after (NotStarted): no ``ssh -G`` of a caller
    that ended at once goes on to run a person's ``Match exec``. Its standard error is read from a file, as _run reads
    its ssh's, its first 64 KiB, so that a program a ``Match exec`` put in the background, which keeps ssh's standard
    error (OpenSSH gives the command /dev/null for its standard input and output), never holds the answer back to the
    limit: the wait ends with ``ssh -G`` itself. Past its limit, or with its caller ended meanwhile, it is ended here
    and waited for. ``process_group`` is _run's: with 0, each of those ends its group too, the ``Match exec``'s program
    with it."""
    import subprocess

    with tempfile.TemporaryFile() as errors:
        process = _started(
            argv,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
            errors="replace",
            process_group=process_group,
        )
        encoding = process.stdout.encoding
        try:
            out, _ = process.communicate(timeout=limit)
        except BaseException:
            # Its limit passed, a Ctrl-C, any exception: ended here, with its group where it leads one, and waited for,
            # as subprocess.run does.
            _kill(process)
            process.wait()
            raise
        finally:
            process.stdout.close()
            _forget(process)
        # As subprocess.run gives a text stream: decoded as its standard output is, its newlines made "\n".
        err = _read(errors.fileno()).decode(encoding, "replace").replace("\r\n", "\n").replace("\r", "\n")
    return subprocess.CompletedProcess(argv, process.returncode, out, err)


def _run(argv: list[str], limit: float, until: str = "", banner: bool = False, *, process_group=None) -> dict:
    """``argv`` run with nothing on its standard input and its standard output discarded - what a forward carries is
    the far end's to write - killed at ``limit`` seconds, or as soon as its standard error holds ``until``:
    ``{rc, said, killed, banner}``, ``said`` its standard error, ``rc`` None where it was killed. With ``banner``, its
    standard output is read as it comes and dropped, and only whether its first bytes are an SSH banner kept, as
    ``banner``: never more of what the far end wrote. A caller ended meanwhile (a Ctrl-C, any exception) ends it too,
    and waits for it: no ssh is left to run on by itself. Once end_all has run, nothing is started: ``rc`` 255, and
    ``said`` says so. ``process_group``, Popen's: the new engine's 0 starts it in a process group of its own, so that a
    terminal's Ctrl-C, which the engine takes as "finish the step", never reaches it, and each kill of it here, at its
    limit or when its caller ends, ends that group with it (_kill); None, today's callers', leaves it in this process's
    group, and a kill ends it alone."""
    import subprocess  # only where ssh is run: imported with the package, it would slow every run's start

    first = b""
    with tempfile.TemporaryFile() as errors:
        try:
            process = _started(
                argv,
                stdout=subprocess.PIPE if banner else subprocess.DEVNULL,
                stderr=errors,
                process_group=process_group,
            )
        except NotStarted as ended:
            return {"rc": 255, "said": str(ended), "killed": False, "banner": False}
        except OSError as error:
            return {"rc": 255, "said": f"{argv[0]}: {error.strerror or error}", "killed": False, "banner": False}
        if process.stdout is not None:
            os.set_blocking(process.stdout.fileno(), False)
        end = time.monotonic() + limit
        killed = False
        try:
            while process.poll() is None:
                first = _drained(process, first)
                if time.monotonic() >= end or (until and until.encode() in _read(errors.fileno())):
                    _kill(process)
                    killed = True
                    break
                time.sleep(0.05)
        finally:
            # A caller ended meanwhile: ended here too, with its group where it leads one (one killed above is ending).
            if not killed:
                _kill(process)
            process.wait()
            first = _drained(process, first)
            if process.stdout is not None:
                process.stdout.close()
            _forget(process)
        said = _read(errors.fileno()).decode(errors="replace").replace("\r", "")
    return {
        "rc": None if killed else process.returncode,
        "said": said.strip(),
        "killed": killed,
        "banner": first.startswith(b"SSH-"),
    }


def _drained(process, first: bytes) -> bytes:
    # What the far end wrote so far, read and dropped, so that it never waits on a full pipe: its first 4 bytes kept.
    if process.stdout is None:
        return first
    while True:
        try:
            chunk = os.read(process.stdout.fileno(), 65536)
        except (BlockingIOError, InterruptedError):
            return first
        except OSError:
            return first
        if not chunk:
            return first
        first = (first + chunk)[:4] if len(first) < 4 else first


def _read(fd: int) -> bytes:
    # pread, which moves no offset: ssh writes through the same open file.
    return os.pread(fd, 65536, 0)


def _check(hop: dict, policy: str, limit=None, *, process_group=None) -> dict:
    argv = [*_common(hop, policy), *_shared(hop), *_place(hop), "-O", "check", _destination(hop)]
    return _run(argv, limit or LIMITS["check"], process_group=process_group)


def hop_check(hop: dict, policy: str, limit=None, *, process_group=None) -> bool:
    """Whether this machine holds the hop's shared connection (``-O check``). Asks this machine's ssh process only: a
    connection whose far side has gone says it runs until its keepalives end it, 60 s at most. ``process_group`` is
    _run's."""
    return _check(hop, policy, limit, process_group=process_group)["rc"] == 0


def hop_stop(hop: dict, policy: str, limit=None, *, process_group=None) -> bool:
    """The shared connection told to take no new sessions (``-O stop``): the forwards it carries finish, and the next
    connection opens a new one at the same path. Never ``-O exit``, which would cut another run's tasks.
    ``process_group`` is _run's."""
    argv = [*_common(hop, policy), *_shared(hop), *_place(hop), "-O", "stop", _destination(hop)]
    return _run(argv, limit or LIMITS["stop"], process_group=process_group)["rc"] == 0


def hop_open_argv(hop: dict, policy: str) -> list[str]:
    """The open of the hop's shared connection: its master and nothing else (ControlMaster=yes, never a client of a
    connection another run opened meanwhile, which with ``-N`` would ask the host for a session: a shell on a full
    account), with the keepalives that end it once its far side is gone, and none of the user's forwards for that
    host."""
    return [
        *_common(hop, policy),
        *_shared(hop, master="yes"),
        *_keep(),
        *_place(hop),
        "-o",
        "ClearAllForwardings=yes",
        "-N",
        _destination(hop),
    ]


def hop_held(hop: dict, policy: str, limits=None, again: bool = True, *, process_group=None) -> dict:
    """Make sure of the hop's shared connection: ``{ok, opened, said}``. ``-O check`` first, never an open beside a
    live connection. Where the check found a socket file that nothing listens at ("Connection refused": a connection
    that ended without removing it), that file is removed, since a master never takes a path over; never where the
    check was ended at its limit. Then the open (hop_open_argv). One that meets a socket another run made meanwhile
    prints that it exists and would go on as a connection of its own, so it is ended then, or at its limit, and ``-O
    check`` decides once more. At worst the file removed was a connection another run opened in that instant: that one
    lives out its clients, a second is opened, and no session is started. An open that ended by itself, its master
    made, with no connection found at the path after it, had its path removed by such a caller: made sure of once
    more, ``again`` false that time. A failure always says why. ``process_group`` is _run's, for every ssh it
    runs."""
    limits = {**LIMITS, **(limits or {})}
    directory = os.path.dirname(hop["socket"])
    try:
        # Ansible's control directory, as Ansible makes it for a connection of its own: 0700, its owner's alone.
        os.makedirs(directory, mode=0o700, exist_ok=True)
    except OSError as error:
        return {"ok": False, "opened": False, "said": f"the control directory {directory}: {error.strerror or error}"}
    checked = _check(hop, policy, limits["check"], process_group=process_group)
    if checked["rc"] == 0:
        return {"ok": True, "opened": False, "said": ""}
    if not checked["killed"] and "Connection refused" in checked["said"]:
        try:
            os.unlink(hop["socket"])
        except FileNotFoundError:
            pass
        except OSError as error:
            return {"ok": False, "opened": False, "said": f"{hop['socket']}: {error.strerror or error}"}
    opened = _run(
        hop_open_argv(hop, policy),
        limits["open"],
        until="already exists, disabling multiplexing",
        process_group=process_group,
    )
    if hop_check(hop, policy, limits["check"], process_group=process_group):
        return {"ok": True, "opened": opened["rc"] == 0, "said": ""}
    if opened["rc"] == 0 and again:
        return hop_held(hop, policy, limits, again=False, process_group=process_group)
    said = (
        opened["said"]
        or (f"no answer within {limits['open']} s" if opened["killed"] else "")
        # Ended by a signal this module did not send - the new engine's run ends an ssh the terminal stopped as it
        # asked there - it made none.
        or (
            f"the open of {hop['socket']} was ended by a signal ({-opened['rc']}) before it made a connection there"
            if opened["rc"] is not None and opened["rc"] < 0
            else ""
        )
        or f"no connection answered at {hop['socket']} after ssh made one there (it exited {opened['rc']})"
    )
    return {"ok": False, "opened": False, "said": said}


# What ssh says at LogLevel=VERBOSE and not at INFO, by the start of its line: its login done ("Authenticated to
# <host> (...)", "(via proxy)" through a proxy of the user's, "using" left out by an older ssh, or one of several
# methods' partial success) and a session's totals. Taken out of a forward's words before anything reads them.
VERBOSE_ONLY = ("Authenticated to ", "Authenticated using ", "Transferred: ", "Bytes per second: ")


def forward_verdict(rc, said, killed, banner=False) -> dict:
    """``{usable, why, said, banner}`` for a forward that ended with ``rc`` (None where it was killed at its limit) and
    standard error ``said``, ssh's at LogLevel=VERBOSE, read in this order: ``prohibited``, the host refused to forward
    to it; ``answered``, the far end took the connection; ``refused``, the host tried and the far end did not take it;
    ``silent``, ended at its limit once ssh said its login to the host was done, no answer from the far end;
    ``stalled``, ended at its limit before that, the login to the host never done (a key that waits for a touch, a host
    that stops after its banner), so the far end was never asked; ``failed``, ssh failed to use the host as a hop. Only
    a refusal to forward, or ssh's own failure, makes the hop unusable: anything slower is the far end's doing, or a
    second login that waits beside a shared connection that works, and must not stop a run. ``said`` is as at INFO:
    VERBOSE_ONLY's lines taken out. ``banner``, from _run, is whether the far end's first bytes were an SSH banner."""
    lines = str(said or "").splitlines()
    logged_in = any(line.startswith("Authenticated to ") for line in lines)
    said = "\n".join(line for line in lines if not line.startswith(VERBOSE_ONLY)).strip()
    if "administratively prohibited" in said:
        why = "prohibited"
    elif rc == 0 and not killed:
        why = "answered"
    elif "connect failed:" in said:
        why = "refused"
    elif killed:
        why = "silent" if logged_in else "stalled"
    else:
        why = "failed"
    return {
        "usable": why in ("answered", "refused", "silent", "stalled"),
        "why": why,
        "said": said,
        "banner": bool(banner) and why == "answered",
    }


def hop_forward(hop: dict, address, policy: str, port=22, limit=None, *, process_group=None) -> dict:
    """One forward to ``address``'s ``port`` on a connection of its own, its standard input closed, ssh at
    LogLevel=VERBOSE: what it says of the hop (forward_verdict), from its exit status and standard error, and of the
    far end's words only whether they begin as an SSH server's do. ``process_group`` is _run's."""
    argv = hop_argv(hop, address, policy, port=port, shared=False, level="VERBOSE")
    ended = _run(argv, limit or LIMITS["forward"], banner=True, process_group=process_group)
    return forward_verdict(ended["rc"], ended["said"], ended["killed"], ended["banner"])


def route_settle(setting, hop: dict, limit=None, *, process_group=None, signalled_settles=True) -> dict:
    """The run's route: ``{route, addresses, dialled}``. Under ``auto``, where ssh would dial for the Proxmox host
    decides: its host as ``ssh -G`` resolves it - a HostName of the user's counts - or as written where that fails,
    then that name's addresses here, now. The ``ssh -G`` is started as _run starts its ssh, so that end_all ends it
    (_asked). ``process_group`` is _run's, for the ``ssh -G``. With ``signalled_settles`` false, the new engine's, an
    ``ssh -G`` that gave no answer settles nothing - one a signal ended, one past its limit (whose kill is a signal
    too), one not started since the run is ending, one that could not be run at all, the ssh program or the temporary
    file its standard error is read from not made, said with what could not be, and one that exited with an error, a
    line of the person's configuration it refused for the host, said in ssh's own words: HopRefused, naming
    vm_ssh_route, where today's callers settle from the address as written, which may be ``direct`` where the person's
    configuration sends the host to a tailnet."""
    import subprocess

    setting = str(setting or "auto")
    if setting != "auto":
        return {"route": route_rule(setting, []), "addresses": [], "dialled": ""}
    # The user's own ssh config is read here, as the hop's ssh reads it: its HostName for the host is where ssh dials.
    argv = [hop["ssh"], *_place(hop), "-G", _destination(hop)]
    limit = limit or LIMITS["config"]
    unanswered = ""
    way_out = "run it again, or set vm_ssh_route: direct or host"
    try:
        shown = _asked(argv, limit, process_group=process_group)
        text = shown.stdout if shown.returncode == 0 else ""
        if shown.returncode < 0:
            unanswered = f"was ended by a signal ({-shown.returncode}) before it said where ssh dials for it"
        elif shown.returncode != 0:
            # A line ssh refuses for the host alone - one the hop's own lines outrank, such as a ControlPath with a
            # token ssh does not know - leaves where it dials unsaid: the address as written is no answer.
            unanswered = (
                f"exited {shown.returncode} without saying where ssh dials for it"
                f" ({ssh_said(shown.stderr).rstrip('.') or 'it said nothing'})"
            )
            way_out = "mend what ssh says of your ssh configuration for the host, or set vm_ssh_route: direct or host"
    except subprocess.TimeoutExpired:
        # ssh -G prints its answer as it exits, and _asked waits for ssh alone (a Match exec's program has /dev/null
        # for its output, and its standard error is a file): past the limit, it said nothing.
        text, unanswered = "", f"said nothing of where ssh dials for it within {limit} s"
    except NotStarted:
        text, unanswered = "", "was not started, since the run is ending"
    except OSError as error:
        # The ssh program, or the temporary file _asked reads its standard error from (no temporary folder usable): to
        # today's callers an ssh that cannot be run, where nothing can be dialled anyway; to the new engine no answer.
        what = f"{error.strerror}: {error.filename}" if error.strerror and error.filename else error.strerror or error
        text, unanswered = "", f"could not be run ({what})"
    if unanswered and not signalled_settles:
        raise HopRefused(
            f"ssh -G for the Proxmox host {unanswered}, so the route under vm_ssh_route: auto is not settled", way_out
        )
    dialled = ssh_g_hostname(text, hop["host"])
    addresses = addresses_of(dialled)
    return {"route": route_rule("auto", addresses), "addresses": addresses, "dialled": dialled}


def ssh_shown(ssh, words, destination, limit=None, *, process_group=None) -> dict:
    """The proxy ssh itself would take for a connection to ``destination`` with ``words`` - a VM's whole line, as its
    connection applies it - asked of ssh (``-G``) with no config file read (``-F /dev/null``, ahead of the words): the
    line decides which proxy wins, and nothing of the user's is read or run, no ``Match exec``; a ``-F`` of the line's
    own, later, replaces it as it would for the connection. ``{ok, proxycommand, proxyjump, said}``: the two as ssh
    shows them, "" for none - read by name, since ssh shows ``proxyusefdpass`` too - and ``said``, ssh's words where
    it refused the line or could not be asked. Its ``ssh -G`` is started as _run starts its ssh, so that end_all ends
    it (_asked). ``process_group`` is _run's."""
    import subprocess

    argv = [str(ssh or "ssh"), "-G", "-F", "/dev/null", *[str(word) for word in words], str(destination)]
    shown = {"ok": False, "proxycommand": "", "proxyjump": "", "said": ""}
    try:
        ended = _asked(argv, limit or LIMITS["config"], process_group=process_group)
    except subprocess.TimeoutExpired:
        return {**shown, "said": f"{argv[0]} -G gave no answer within {limit or LIMITS['config']} s"}
    except NotStarted as unstarted:
        return {**shown, "said": str(unstarted)}
    except OSError as error:
        return {**shown, "said": f"{argv[0]}: {error.strerror or error}"}
    if ended.returncode != 0:
        return {**shown, "said": ended.stderr.strip() or f"{argv[0]} -G exited {ended.returncode}"}
    for line in ended.stdout.splitlines():
        word, _, value = line.strip().partition(" ")
        if word.lower() in ("proxycommand", "proxyjump"):
            shown[word.lower()] = value.strip()
    return {**shown, "ok": True}


def _hop_to(settled, proxycommand) -> str:
    """The address the hop's own command in ``proxycommand`` forwards to, word for word the hop's; "" for any other
    command."""
    found = re.search(r" -W (\S+):%p ", f" {proxycommand} ")
    if not found:
        return ""
    try:
        wanted = " ".join(hop_argv(settled["hop"], found.group(1), settled["policy"]))
    except (HopRefused, KeyError, TypeError, ValueError):
        return ""
    return found.group(1) if proxycommand == wanted else ""


def _shown_proxy(shown: dict) -> str:
    if shown["proxyjump"]:
        return f"ProxyJump {shown['proxyjump']}"
    if shown["proxycommand"]:
        return f"ProxyCommand {shown['proxycommand']}"
    return ""


def vm_route_refusal(settled, address, takes_hop, shown, alone=None, transfers=None) -> str:
    """Why a VM's connection would not go where the route says, from what ssh showed of its whole line (ssh_shown);
    "" where it would. One that takes the hop must show the hop's command to ``address``, word for word, and nothing
    else; one given a jump of its own, some proxy - and on ``alone`` too, what ssh shows of its common and extra args
    alone, the line Claude's sign-in forward and destroy's fetch are given, and on each of ``transfers``, by the
    variable whose words end the line (``{sftp_extra_args: shown, scp_extra_args: shown}``), the lines sftp and scp
    are given, asked only where the jump is not in what every line starts with. Never neither: nothing is dialled
    from here."""
    from .text import one_line

    if not shown["ok"]:
        return f"ssh refused its options: {_plain(shown['said'])}"
    taken = _shown_proxy(shown)
    if not takes_hop:
        if not taken:
            return "it is given a jump of its own, and ssh would take none, dialling it from here"
        if alone is not None and not alone["ok"]:
            return f"ssh refused its common and extra args alone: {_plain(alone['said'])}"
        if alone is not None and not _shown_proxy(alone):
            return (
                "it is given a jump of its own, and ssh would take none on its common and extra args alone, which"
                " Claude's sign-in forward and destroy's fetch are given"
            )
        for variable, line in (transfers or {}).items():
            if not line["ok"] or not _shown_proxy(line):
                return (
                    f"its jump is not in ansible_ssh_common_args, and ssh would take none on the line {variable} ends,"
                    " dialling its file transfers from here"
                )
        return ""
    try:
        wanted = " ".join(hop_argv(settled["hop"], address, settled["policy"]))
    except HopRefused:
        return f"its ansible_host, {one_line(str(address), 80)}, is not the address it was published with"
    if shown["proxycommand"] == wanted and not shown["proxyjump"]:
        return ""
    # The hop's own command, to the address it was published with, where its ansible_host was outranked.
    published = "" if shown["proxyjump"] else _hop_to(settled, shown["proxycommand"])
    if published and published != address:
        return (
            f"its ansible_host, {one_line(str(address), 80)}, is not the address it was published with, {published},"
            " which the hop forwards to"
        )
    if taken:
        return f"ssh would take {one_line(taken, 160)} for it, not the hop"
    return "ssh would take no proxy for it, dialling it from here, not through the hop"


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


def unheld_detail(settled, address, takes_hop, line) -> str:
    """Where in a VM's line the proxy option is that ssh would take before the hop, or beside it, by the project's
    reading of ssh's arguments: words for a person, beside what ssh itself showed. ``line`` holds the ssh plugin's
    ``ssh_args``, ``ssh_common_args`` and ``ssh_extra_args`` for the VM; "" where none is found there."""
    before = ssh_proxy_any(line.get("ssh_args"))
    if before:
        return f"ssh_args holds {before}, which ssh would take before {'the hop' if takes_hop else 'its own'}"
    if not takes_hop:
        return ""
    beside = ssh_proxy_any(line.get("ssh_extra_args"))
    if beside:
        return f"ssh_extra_args holds {beside} beside the hop"
    try:
        wanted = ("ProxyCommand", " ".join(hop_argv(settled["hop"], address, settled["policy"])))
    except HopRefused:
        return ""
    first = ssh_proxies(line.get("ssh_common_args"))[:1]
    # The project's own common args, the hop to the address the VM was published with: its address was outranked,
    # not they, which the refusal says.
    if first and first[0][0] == "ProxyCommand" and _hop_to(settled, first[0][1]):
        return ""
    if first != [wanted]:
        return "ansible_ssh_common_args is set where it outranks the project's"
    return ""


def unheld_said(settled, unheld) -> str:
    """Why the VMs ``unheld`` (``{name, why, asked}``) are refused before anything connects to them, and the ways out:
    for a VM whose ssh was asked, a jump of its own in the one variable ssh, sftp and scp all get; for one whose ssh
    could not be asked at all (``asked`` false), the program."""
    each = "; ".join(f"{vm['name']}: {vm['why']}" for vm in unheld)
    ways = []
    if any(vm.get("asked", True) for vm in unheld):
        ways.append(
            "set a jump of your own as ansible_ssh_common_args, which ssh, sftp and scp all get, where the VM is given"
            " it - with an empty value on the Proxmox host's own entry where it is set for every host"
        )
    if not all(vm.get("asked", True) for vm in unheld):
        ways.append(
            "set ansible_ssh_executable to an OpenSSH ssh, which is asked with -G which proxy a VM's line takes"
        )
    ways_out = "; or ".join(ways)
    return (
        f"VMs are reached through {settled['hop']['label']}, and {each}. {ways_out[:1].upper()}{ways_out[1:]}, or set"
        " vm_ssh_route: direct."
    )


# What ssh says, over the hop's shared connection, of a connection that ended before the VM's sshd spoke: a VM that
# did not answer through the host, whose own words ssh never had. Never a refusal the VM's sshd made ("Received
# disconnect"), nor status's own line for a VM that took the connection and said nothing. Read by just status and by
# just destroy alike (hop_unanswered).
HOP_UNANSWERED = (
    "connection closed by unknown port 65535",
    "connection timed out during banner exchange",
    "connection to unknown port 65535 timed out",
    "stdio forwarding request failed",
    "kex_exchange_identification",
)


def hop_unanswered(said) -> bool:
    """Whether ssh's words for a VM reached through the hop say the connection ended before the VM's sshd spoke."""
    low = str(said or "").lower()
    return "received disconnect" not in low and any(words in low for words in HOP_UNANSWERED)


def ssh_said(said, limit=400) -> str:
    """What ssh said, as one line of at most ``limit`` characters: its last lines, where the most is said, and never
    its banner of @s - a changed host key's warning is 400 characters before "Host key for ... has changed"."""
    from .text import one_line

    lines = [line for line in str(said or "").splitlines() if line.strip() and not line.lstrip().startswith("@")]
    kept: list[str] = []
    for line in reversed(lines):
        if kept and len(" ".join([line, *kept])) > limit:
            break
        kept.insert(0, line)
    return one_line(" ".join(kept), limit)


def _plain(said) -> str:
    return ssh_said(said).rstrip(".") or "no reason given"


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
        f" `{hop_login_line(hop)}` here shows which.{_direct_tail(settled)}"
    )


def hop_login_line(hop: dict) -> str:
    """The ssh a person runs to try the hop's own login: its port and key, as the hop's ssh has them."""
    return shlex.join(["ssh", *_place(hop), _destination(hop)])


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
    if why == "stalled":
        return (
            f"The login to {hop['label']} for a forward of its own to {vm} did not finish within {LIMITS['forward']} s,"
            f" so forwarding there was not checked: `{hop_login_line(hop)}` here shows why (a key that waits for a"
            " touch or an approval, or a host that stops after its banner)."
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


def vm_kept_said(settled, name, address, verdict, held=None, again=None) -> str:
    """Why ``just destroy`` keeps a VM that was unreachable through the hop, where the road is still what fails: from a
    forward of its own to it (forward_verdict) - the host refused to forward, ssh could not use it, or its login did
    not finish - and, for a VM that answered that forward, from the making sure of the shared connection that followed
    (``held``, the result of making it again) and the reach after it (``again``): a connection that could not be made
    again, or a reach that ended again before the VM's sshd spoke (hop_unanswered). "" where the VM is at fault itself
    - it refused the host's connection or said nothing, or its sshd spoke and refused this run (a login, a host key),
    or its connection worked and a module failed - and it goes, as under direct."""
    why = verdict.get("why") if isinstance(verdict, dict) else None
    if not isinstance(settled, dict) or settled.get("route") != "host":
        return ""
    if why in ("prohibited", "failed", "stalled"):
        said = forward_said(settled, name, address, verdict).rstrip(".")
        return said[:1].lower() + said[1:] if why == "stalled" else said
    if why != "answered":
        return ""
    label = settled["hop"]["label"]
    if isinstance(held, dict) and held and not held.get("skipped") and not held.get("ok", False):
        return (
            f"it answered a forward of its own through {label}, and the connection through the host could not be made"
            f" again: {_plain(held.get('said') or held.get('msg'))}"
        )
    if isinstance(again, dict) and again.get("unreachable") and hop_unanswered(again.get("msg")):
        return f"it answered a forward of its own through {label}, but not this run's connection, made again"
    return ""


def vm_gone_said(settled, via, verdict=None) -> str:
    """What the report of a VM deleted though it did not answer adds: through the hop, what the host's forward to it
    showed - the VM's own fault - and never `just status`, which cannot read a VM that is gone; under direct, the
    setting that would go through the host; nothing for a VM that brings its own jump, nor for one the host was not
    asked about (``verdict`` a skipped result: its connection worked, and what failed was on the VM)."""
    if not isinstance(settled, dict) or settled.get("route") != "host":
        return vm_unanswered(settled, via, "")
    if not via or (isinstance(verdict, dict) and verdict.get("skipped")):
        return ""
    label = settled["hop"]["label"]
    why = verdict.get("why") if isinstance(verdict, dict) else None
    if why == "refused":
        reason = re.search(r"connect failed: ([^\n]*)", str(verdict.get("said") or ""))
        refused = f" ({_plain(reason.group(1))})" if reason else ""
        return f"It did not answer through {label}: the host's connection to it was refused{refused}."
    if why == "silent":
        return f"It did not answer through {label}: it said nothing to the host within {LIMITS['forward']} s."
    if why == "answered":
        return (
            f"It answered a forward of its own through {label}, so the fault is the VM's own: this run's connection to"
            " it, made again, reached it and failed there, as it would under vm_ssh_route: direct."
        )
    return f"It did not answer through {label}."


def hop_key_offered(text, key) -> bool:
    """Whether ``ssh -G`` (``text``) for the hop lists ``key`` among the identity files it would offer, each expanded
    as ssh expands ~; one with a % token in it is taken as not known."""
    offered = set()
    for line in str(text or "").splitlines():
        word, _, value = line.strip().partition(" ")
        if word.lower() == "identityfile" and value.strip() and "%" not in value:
            offered.add(os.path.expanduser(value.strip()))
    return os.path.expanduser(str(key or "")) in offered
