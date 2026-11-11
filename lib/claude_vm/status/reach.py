"""How just status reaches a VM: one connection, the probe (playbooks/module_utils/status_probe.py) on its standard
input, its answer read a line at a time.

Over OpenSSH as Ansible reaches a VM - ansible.cfg's options with the host key held to what this machine knows
(status_ssh_args), BatchMode, the same ControlPersist - or, with discovery off, however the inventory says that host is
reached (docker, local, ssh with a port and a key of its own). Root through sudo by its full path (ansible_become_exe):
without a password first, and only where that is refused with the VM user's password, on standard input and never on
a command line; where sudo cannot be had at all, as the VM user, Tailscale said not to have been read.

What a VM sends is held to a size, a time and a shape, and is only ever data: its root chooses every byte.
"""

from __future__ import annotations

import json
import math
import os
import shlex
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
UTILS = REPO / "playbooks" / "module_utils"
SOURCES = ("agent_warehouse", "git_checkouts", "status_probe")
# What the probe's connection may take: a VM whose root sends more is cut off, and said as not read.
LINE_BYTES = 16 * 1024 * 1024
SECONDS = 45
CONNECT_SECONDS = 10
# How long a host may take to start the probe: a VM that takes a connection and then says nothing is not waited on.
START_SECONDS = 20
# The script's first line. sudo -S reads the password up to its newline and leaves the rest; a sudo that asks for none
# leaves the password there too, so what starts the probe looks for this mark and drops whatever comes before it,
# never handing the password to Python, whose syntax error would say the line.
MARK = "#!status-probe"
BOOTSTRAP = f"import sys; d = sys.stdin.read(); exec(d[d.index({MARK!r}):])"

_children = set()  # the connections still open
_children_lock = threading.Lock()
_over = threading.Event()  # set by end_all: no connection is made after it


def _no_number(text):
    raise ValueError(f"{text} is not a number")


def _finite(text):
    value = float(text)
    return value if math.isfinite(value) else _no_number(text)


def loads(text):
    """JSON from a VM, directly or through its guest agent. Python's parser takes NaN and makes 1e999 infinity, which
    the int() of a count then raises on: a VM's root would end the whole report with one line. Neither is an answer."""
    return json.loads(text, parse_float=_finite, parse_constant=_no_number)


def end_all() -> None:
    """Every connection still open ended, and none made after: a run that is over waits for none of them."""
    with _children_lock:
        _over.set()
        for process in _children:
            if process.poll() is None:
                process.kill()


def probe_script(asked: dict) -> str:
    """The probe as one script: each file of module_utils it needs defined in memory under its own name, then run."""
    sources = {name: (UTILS / f"{name}.py").read_text(encoding="utf-8") for name in SOURCES}
    return (
        f"{MARK}\n"
        "import json, sys, types\n"
        "def _module(name, source):\n"
        "    module = types.ModuleType(name); sys.modules[name] = module\n"
        "    exec(compile(source, name, 'exec'), module.__dict__); return module\n"
        "_module('ansible', ''); _module('ansible.module_utils', '')\n"
        f"SOURCES = json.loads({json.dumps(json.dumps(sources))})\n"
        f"for _name in {SOURCES!r}:\n"
        "    _last = _module('ansible.module_utils.' + _name, SOURCES[_name])\n"
        f"_last.main(json.loads({json.dumps(json.dumps(asked))}))\n"
    )


def _python(how: dict) -> list[str]:
    return [how["python"], "-I", "-B", "-c", BOOTSTRAP]


def _become(how: dict, password: bool) -> list[str]:
    if not how["become"]:
        return _python(how)
    if password:
        return [how["become_exe"], "-S", "-p", "", "-H", *_python(how)]
    return [how["become_exe"], "-n", "-H", *_python(how)]


def command(how: dict, as_root: bool = True, password: bool = False) -> list[str]:
    """The program that runs the probe on this host. ``as_root`` false runs it as whoever the connection is."""
    argv = _become(how, password) if as_root else _python(how)
    kind = how["connection"].rsplit(".", 1)[-1]
    if kind == "local":
        return argv
    if kind == "docker":
        user = ["-u", how["user"]] if how.get("user") else []
        return ["docker", "exec", "-i", *user, how["host"], *argv]
    if kind != "ssh":
        raise ValueError(f"just status cannot reach a host over the {how['connection']} connection")
    directory = os.path.expanduser(how["control_dir"])
    os.makedirs(directory, mode=0o700, exist_ok=True)
    options = shlex.split(how["ssh_args"])
    options += ["-o", f"ControlPath={directory}/%C", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={CONNECT_SECONDS}"]
    if how.get("port"):
        options += ["-p", str(how["port"])]
    if how.get("key"):
        options += ["-i", how["key"]]
    if how.get("user"):
        options += ["-l", how["user"]]
    return [how["ssh_executable"], *options, how["host"], shlex.join(argv)]


def _attempt(argv: list[str], stdin: str, early) -> dict:
    """One connection: what it said, as {"started", "final", "rc", "errors"}."""
    import subprocess  # only where a connection is made: imported with the package, it would slow every run's start

    if _over.is_set():
        raise OSError("the run ended before it was reached")
    process = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace"
    )
    with _children_lock:
        _children.add(process)
        if _over.is_set():
            # end_all ran while it started: ended here, since end_all could not see it.
            process.kill()
    timer = threading.Timer(SECONDS, process.kill)
    timer.start()
    silent = threading.Event()

    def said_nothing():
        silent.set()
        process.kill()

    start_timer = threading.Timer(START_SECONDS, said_nothing)
    start_timer.start()
    errors: list[str] = []
    drain = threading.Thread(target=lambda: errors.append(process.stderr.read(1 << 16)), daemon=True)
    drain.start()

    def send():
        try:
            process.stdin.write(stdin)
            process.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass  # it ended before reading: what it said says why

    threading.Thread(target=send, daemon=True).start()
    started, final, last_early, cut = False, None, None, False
    for text in iter(lambda: process.stdout.readline(LINE_BYTES), ""):
        if not text.endswith("\n"):
            cut = True
            process.kill()
            break
        try:
            said = loads(text)
        except (RecursionError, ValueError):
            continue
        if not isinstance(said, dict):
            continue
        if said.get("started") is True:
            started = True
            start_timer.cancel()
        elif isinstance(said.get("early"), dict):
            last_early = said["early"]
            early(last_early)
        else:
            final = said
    process.wait()
    timer.cancel()
    start_timer.cancel()
    drain.join(1)
    with _children_lock:
        _children.discard(process)
    return {
        "started": started,
        "final": final,
        "early": last_early,
        "silent": silent.is_set(),
        "rc": process.returncode,
        "errors": (errors[0] if errors else "").strip(),
        "cut": cut,
    }


def read(how: dict, asked: dict, early, password) -> dict:
    """What this host's probe found: {"user": ..., "root": ...}, or {"unreachable": True, "msg": ssh's words}, or
    {"failed": True, "msg": why}. ``early`` is told the reads but apt's the moment only that is left; ``password`` is
    called for the VM user's password only where sudo asks for one."""
    script = probe_script(asked)
    is_ssh = how["connection"].rsplit(".", 1)[-1] == "ssh"
    try:
        done = _attempt(command(how), script, early)
    except (OSError, ValueError) as error:
        return {"failed": True, "msg": str(error)}
    if done["silent"]:
        said = f"it took the connection and said nothing for {START_SECONDS} s: timed out"
        return {"unreachable": True, "msg": said} if is_ssh else {"failed": True, "msg": said}
    if is_ssh and done["rc"] == 255:
        return {"unreachable": True, "msg": done["errors"]}
    if how["become"] and not done["started"] and done["rc"] != -9:
        # sudo said no before Python started: with the password, then as the VM user alone, Tailscale's reads left out.
        refused = done["errors"]
        secret = password()
        if secret:
            done = _attempt(command(how, password=True), f"{secret}\n{script}", early)
        if not done["started"]:
            alone = _attempt(
                command(how, as_root=False), probe_script({k: v for k, v in asked.items() if k != "tailscale"}), early
            )
            if alone["final"] is not None:
                return {**alone["final"], "root": {"failed": True, "msg": f"sudo refused: {refused}"}}
            done = alone
    if done["final"] is not None:
        return done["final"]
    why = _why(done)
    if done["early"] is not None:
        # All but apt's simulation had landed: the rest of what it read stands.
        kept = done["early"]
        system = kept["user"].get("system") if isinstance(kept.get("user"), dict) else None
        if isinstance(system, dict):
            system.pop("upgrades_pending", None)
            unread = system.get("unread")
            system["unread"] = {**(unread if isinstance(unread, dict) else {}), "upgrades": why}
        return kept
    return {"failed": True, "msg": why}


def _why(done: dict) -> str:
    if done["cut"]:
        return "it sent more than it may"
    if done["rc"] == -9:
        return f"it took longer than {SECONDS} s"
    return done["errors"] or f"exit {done['rc']}"
