# Copyright (c) agent-warehouse contributors
# MIT License
"""What just status reads inside a VM, every read at once, in one run: changing nothing.

The engine (lib/claude_vm/status/engine.py) sends this file over ssh, with what it imports, and runs it with the
system's Python as root: the one way anything of just status runs on a VM. No Ansible there: standard library only,
and it imports only agent_warehouse.py and git_checkouts.py, which are sent with it.

It forks a child that becomes the VM user for the user's reads - the system's, Claude Code's, gh's and the checkouts' -
while the parent makes root's, Tailscale's. Each read is a thread of its own with a limit of its own, so one that
hangs or fails is said by name and the rest land. Connected as the VM user with no sudo to give (a run against a
stand-in, or a VM whose sudo refused), everything is read as that user, and what asks for root is left to the caller.

Nothing is changed, here or by what it runs: the programs are the fixed list of ALLOWED, each a read, and every file
is opened to read. tests/unit/test_status_vms.py holds both. Never claude itself, until a real VM shows that
claude auth status refreshes nothing, so a login is read as its file being there. gh's token never leaves the VM: only
which kind it is, and whether the role's marker holds its digest.

One JSON document a line on standard output: {"started": true} first, then, when apt's simulation is the only read
left, {"early": <the reads, upgrades_pending set>}, and last the whole of them.
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ansible.module_utils.agent_warehouse import folders, is_claude, own_processes, version_key
from ansible.module_utils.git_checkouts import read as read_checkout

APT_GET = "/usr/bin/apt-get"
INST = re.compile(r"^Inst (\S+) .*\(([^)]*)\)")
# sudo's own, for a child that has dropped root: never the user's PATH, but for what they installed for themselves.
SECURE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin"
# One entry of the system read's editors, with what each field holds unless given: the core's ProcessMatch
# (lib/claude_vm/editors/model.py), field for field, since this runs where the core cannot be imported.
EDITOR = {"argv0_contains": "", "argv0_name_prefix": "", "argv1": "", "argv_has": ()}
# systemd's properties of Remote Control's two units that the report reads.
UNIT_KEYS = ("LoadState", "ActiveState", "SubState", "NRestarts", "Result", "StateChangeTimestamp")
# Every program this runs, the whole of its arguments but what names one thing to read: reads, every one. Git's are
# git_checkouts.py's, each a read as tests/unit/test_status_work.py holds it.
ALLOWED = (
    (APT_GET, "-s", "-q", "-o", "Debug::NoLocking=1", "dist-upgrade"),
    ("systemctl", "show", None, "-p", ",".join(UNIT_KEYS)),
    ("gh", "auth", "status", "--hostname", "github.com", "--json", "hosts"),
    ("gh", "auth", "token", "--hostname", "github.com"),
    ("dpkg-query", "--show", "--showformat=${db:Status-Status}", "tailscale"),
    ("tailscale", "status", "--json"),
    ("tailscale", "debug", "prefs"),
)


class Commands:
    """What git_checkouts.py asks of AnsibleModule: run_command, and fail_json as an exception."""

    def run_command(self, args, environ_update=None, expand_user_and_vars=False):
        done = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            env={**os.environ, **(environ_update or {})},
            check=False,
        )
        return done.returncode, done.stdout, done.stderr

    def fail_json(self, msg="", **_):
        raise RuntimeError(msg)


def run(argv, env=None, timeout=20):
    """A command's result: rc, stdout and stderr, a program that is not there rc 127, one that ran out of time -1."""
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, errors="replace", env=env, timeout=timeout, check=False
        )
    except FileNotFoundError as error:
        return {"rc": 127, "stdout": "", "stderr": str(error)}
    except subprocess.TimeoutExpired:
        return {"rc": -1, "stdout": "", "stderr": f"it took longer than {timeout} s"}
    return {"rc": done.returncode, "stdout": done.stdout.strip(), "stderr": done.stderr.strip()}


def _file(path):
    """A file's first 64 KiB as text, or None: gone, or unreadable."""
    try:
        with open(os.path.expanduser(path), "rb") as handle:
            return handle.read(1 << 16).decode("utf-8", "replace").strip()
    except OSError:
        return None


def _exists(path):
    """Whether a file is there, not following a link: a link to nothing is there."""
    return os.path.lexists(os.path.expanduser(path))


def _at_once(reads, limit):
    """{name: read} each in a thread of its own, joined for the limit: {name: its answer}, one that hung or failed as
    {"failed": true, "msg": why}. Daemon threads, so one that hangs leaves nothing to wait for at exit."""
    boxes = {}
    started = time.monotonic()

    def work(read, box):
        try:
            box["value"] = read()
        except Exception as error:  # noqa: BLE001 - any failure is the read's own, said by name
            box["value"] = {"failed": True, "msg": str(error) or type(error).__name__}

    threads = []
    for name, read in reads.items():
        boxes[name] = {}
        thread = threading.Thread(target=work, args=(read, boxes[name]), daemon=True)
        thread.start()
        threads.append((name, thread))
    for name, thread in threads:
        thread.join(max(0.1, limit - (time.monotonic() - started)))
        if thread.is_alive() or "value" not in boxes[name]:
            boxes[name]["value"] = {"failed": True, "msg": f"it took longer than {limit} s"}
    return {name: box["value"] for name, box in boxes.items()}


# ------------------------------------------------------------------------------------------------ the system ----


def _read(root, path):
    with open(os.path.join(root, path.lstrip("/")), encoding="utf-8", errors="replace") as handle:
        return handle.read()


def os_release(root):
    for name in ("etc/os-release", "usr/lib/os-release"):
        try:
            text = _read(root, name)
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def kernels(root):
    running = _read(root, "proc/sys/kernel/osrelease").strip()
    try:
        names = os.listdir(os.path.join(root, "boot"))
    except OSError:
        names = []
    installed = [name[len("vmlinuz-") :] for name in names if name.startswith("vmlinuz-")]
    newest = max(installed, key=version_key) if installed else ""
    return {"kernel": running, "kernel_newest": newest}


def reboot(root):
    required = os.path.exists(os.path.join(root, "run/reboot-required"))
    packages = []
    try:
        for line in _read(root, "run/reboot-required.pkgs").splitlines():
            if line.strip() and line.strip() not in packages:
                packages.append(line.strip())
    except OSError:
        pass
    return {"reboot_required": required, "reboot_packages": packages}


def load_memory_uptime(root):
    load = [float(value) for value in _read(root, "proc/loadavg").split()[:3]]
    memory = {}
    for line in _read(root, "proc/meminfo").splitlines():
        key, _, value = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            memory["total" if key == "MemTotal" else "available"] = int(value.split()[0]) * 1024
    uptime = int(float(_read(root, "proc/uptime").split()[0]))
    return {"load": load, "memory": memory, "uptime": uptime}


def upgrades(timeout):
    """What `apt-get -s dist-upgrade` would install: a simulation, which takes no lock and changes nothing."""
    result = subprocess.run(
        [APT_GET, "-s", "-q", "-o", "Debug::NoLocking=1", "dist-upgrade"],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        env={"LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        check=False,
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else f"exit {result.returncode}")
    found = [match.group(2) for match in map(INST.match, result.stdout.splitlines()) if match]
    return {"upgrades": len(found), "security": sum(1 for pocket in found if "-security" in pocket)}


def apt_key():
    """What the simulation's answer depends on, as a digest of names, sizes and times: dpkg's status, the package lists
    and apt's own configuration. Metadata only, a few milliseconds where the simulation takes seconds, so an answer
    known for this key need not be worked out again."""
    digest = hashlib.sha256()
    try:
        info = os.stat("/var/lib/dpkg/status")
        digest.update(f"dpkg {info.st_size} {info.st_mtime_ns}\n".encode())
    except OSError:
        digest.update(b"dpkg -\n")
    for top in ("/var/lib/apt/lists", "/etc/apt"):
        for folder, names, files in os.walk(top):
            names.sort()
            for name in sorted(files):
                path = os.path.join(folder, name)
                try:
                    info = os.lstat(path)
                except OSError:
                    continue
                digest.update(f"{path} {info.st_size} {info.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def window(argv, match):
    """Whether argv is one of an editor's windows, as its entry of the editors says: every field given holds, and the
    program is named, since a match on the arguments alone would count any of the user's programs."""
    program = argv[0]
    return bool(match["argv0_contains"] or match["argv0_name_prefix"]) and (
        match["argv0_contains"] in program
        and os.path.basename(program).startswith(match["argv0_name_prefix"])
        and (not match["argv1"] or argv[1:2] == [match["argv1"]])
        and all(arg in argv for arg in match["argv_has"])
    )


def processes(root, editors=()):
    """This user's claude processes, and each editor's windows as the editors know them, from /proc, by the editor's
    key in the editors list (claude_editors); editor_windows is every editor's together, and vscode_windows stays as
    1.0 has it."""
    claude = 0
    by = {match["key"]: 0 for match in editors}
    for _, argv, _ in own_processes(os.path.join(root, "proc")):
        if is_claude(argv):
            claude += 1
        else:
            key = next((match["key"] for match in editors if window(argv, match)), None)
            if key is not None:
                by[key] += 1
    return {
        "claude": claude,
        "vscode_windows": by.get("vscode", 0),
        "editor_windows": sum(by.values()),
        "editor_windows_by": by,
    }


def run_all(reads, timeout):
    """{name: (read, its limit or None)}, each a thread of its own, joined for its own limit: what they found merged,
    and what each that hung or failed said."""
    results, unread = {}, {}
    threads = []
    for name, (read, limit) in reads.items():
        box = {}

        def work(read=read, box=box):
            try:
                box["value"] = read()
            except Exception as error:  # noqa: BLE001 - any failure is the read's own, said by name
                box["error"] = str(error) or type(error).__name__

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        threads.append((name, thread, box, limit or timeout))
    for name, thread, box, limit in threads:
        thread.join(limit)
        if thread.is_alive():
            unread[name] = f"it took longer than {limit} s"
        elif "error" in box:
            unread[name] = box["error"]
        else:
            results.update(box["value"])
    return results, unread


def system(asked, with_upgrades=True):
    """The OS, the kernels, a reboot waiting, the load, memory and uptime, and the user's own processes; apt's upgrades
    with ``with_upgrades``."""
    root, timeout = asked.get("root", "/"), int(asked.get("timeout", 5))
    editors = [{**EDITOR, **one} for one in asked.get("editors") or []]
    reads = {
        "os": (lambda: {"os": os_release(root)}, None),
        "kernel": (lambda: kernels(root), None),
        "reboot": (lambda: reboot(root), None),
        "load": (lambda: load_memory_uptime(root), None),
        "processes": (lambda: processes(root, editors), None),
    }
    if with_upgrades and asked.get("upgrades", True):
        reads["upgrades"] = (lambda: upgrades(timeout * 4), timeout * 4 + 1)
    found = {
        "os": "",
        "kernel": "",
        "kernel_newest": "",
        "reboot_required": False,
        "reboot_packages": [],
        "load": [],
        "memory": {},
        "uptime": 0,
        "upgrades": None,
        "security": None,
        "claude": 0,
        "vscode_windows": 0,
        "editor_windows": 0,
        "editor_windows_by": {},
    }
    results, unread = run_all(reads, timeout)
    return {**found, **results, "unread": unread}


# --------------------------------------------------------------------------------------- the user's other reads ----


def _unit(name):
    """systemd's word on a unit: the properties the report reads, those with a value; {} where systemctl failed."""
    said = run(["systemctl", "show", name, "-p", ",".join(UNIT_KEYS)])
    if said["rc"] != 0:
        return {}
    lines = (line.partition("=") for line in said["stdout"].splitlines())
    return {key: value for key, _, value in lines if key in UNIT_KEYS and value}


def claude(asked, home):
    """What Claude Code is on this VM: where the installer's link points, read and never followed into, whether a login
    is stored, and Remote Control's two units."""
    binary = os.path.join(home, ".local/bin/claude")
    try:
        target = os.readlink(binary)
    except OSError:
        target = ""
    return {
        "installed": _exists(binary),
        "target": target,
        "login": _exists(os.path.join(home, ".claude/.credentials.json")),
        "service": _unit(f"{asked['unit']}.service"),
        "path": _unit(f"{asked['unit']}.path"),
    }


def github(asked, home, env):
    """gh's login, as gh says it: never its token, only the kind of token it holds and whether the role's marker holds
    its digest."""
    if not shutil.which("gh", path=(env or os.environ).get("PATH")):
        return {"found": False}
    status = run(["gh", "auth", "status", "--hostname", "github.com", "--json", "hosts"], env=env)
    found = {"found": True, "status": status, "kind": "", "marked": False}
    try:
        hosts = json.loads(status["stdout"]).get("hosts", {}) if status["rc"] == 0 else {}
    except (AttributeError, ValueError):
        hosts = {}
    active = next((one for one in hosts.get("github.com", []) if isinstance(one, dict) and one.get("active")), {})
    if active.get("state") == "success":
        said = run(["gh", "auth", "token", "--hostname", "github.com"], env=env)
        token = said["stdout"].strip() if said["rc"] == 0 else ""
        digest = _file(os.path.join(home, asked["marker"]))
        if digest is None and asked.get("marker_1x"):
            digest = _file(os.path.join(home, asked["marker_1x"]))
        found["kind"] = (
            "pat" if token.startswith(("ghp_", "github_pat_")) else "oauth" if token.startswith("gho_") else "other"
        )
        found["marked"] = bool(token and digest and hashlib.sha256(token.encode()).hexdigest() == digest)
    return found


def work(asked, home):
    """Every checkout directly in the projects directory, and what of its work is on no remote or not committed."""
    under = (str(asked.get("dir") or "") or f"{home.rstrip('/')}/projects").rstrip("/") or "/"
    commands = Commands()
    paths = [folder.path for folder in folders(under)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        found = list(pool.map(lambda path: read_checkout(commands, path, False, "", None, "", True), paths))
    return {
        "checkouts": [
            item
            for item in found
            if item["git"] and os.path.realpath(item["toplevel"]) == os.path.realpath(item["path"])
        ]
    }


def user_reads(asked, home, early=None):
    """The user's reads, each at once. Apt's simulation goes beside the rest: skipped where the package state's key is
    the one its answer is known for, and ``early`` told everything else as soon as only it is left."""
    env = None
    if os.environ.get("STATUS_PROBE_DROPPED"):
        env = {**os.environ, "PATH": f"{home}/.local/bin:{SECURE_PATH}"}
    asked_system = asked.get("system")
    apart = bool(asked_system and asked_system.get("upgrades", True))
    reads = {}
    if asked_system:
        reads["system"] = lambda: system(asked_system, with_upgrades=not apart)
    if "claude" in asked:
        reads["claude"] = lambda: claude(asked["claude"], home)
    if "github" in asked:
        reads["github"] = lambda: github(asked["github"], home, env)
    if "work" in asked:
        reads["work"] = lambda: work(asked["work"], home)
    box, simulation, key, known = {}, None, "", False
    if apart:
        key = apt_key()
        hit = asked_system.get("known") or {}
        known = bool(hit.get("key") == key)
        if known:
            box["value"] = {"upgrades": hit.get("upgrades"), "security": hit.get("security")}
        else:
            limit = int(asked_system.get("timeout", 5)) * 4

            def simulate():
                try:
                    box["value"] = upgrades(limit)
                except Exception as error:  # noqa: BLE001 - said as the read's own, as run_all does
                    box["error"] = str(error) or type(error).__name__

            simulation = threading.Thread(target=simulate, daemon=True)
            simulation.start()
    found = _at_once(reads, int(asked.get("limit", 25)))
    read_system = found.get("system")
    if apart and isinstance(read_system, dict) and not read_system.get("failed"):
        if simulation is not None:
            if early:
                early({**found, "system": {**read_system, "upgrades_pending": True}})
            limit = int(asked_system.get("timeout", 5)) * 4 + 1
            simulation.join(limit)
            if simulation.is_alive():
                read_system["unread"]["upgrades"] = f"it took longer than {limit} s"
            elif "error" in box:
                read_system["unread"]["upgrades"] = box["error"]
        read_system.update(box.get("value") or {})
        read_system["upgrades_key"] = key
    return found


# ------------------------------------------------------------------------------------------------ root's reads ----


def tailscale(asked):
    """What is on the VM and whose, and, where the report says anything of it, its state: read once, never waited on.
    Root's: the role's markers are."""
    package = run(["dpkg-query", "--show", "--showformat=${db:Status-Status}", "tailscale"])["stdout"]
    found = {
        "package": package,
        "foreign": any(_exists(path) for path in asked["foreign"]),
        "marker": _exists(asked["marker"]) or bool(asked["marker_1x"] and _exists(asked["marker_1x"])),
    }
    joined = _file(asked["joined"])
    if joined is None and asked["joined_1x"]:
        joined = _file(asked["joined_1x"])
    found["joined"] = joined or ""
    if package == "installed" and (asked["wanted"] or found["marker"]):
        status = run(["tailscale", "status", "--json"])
        found["status"] = status
        try:
            backend = json.loads(status["stdout"]).get("BackendState") if status["rc"] == 0 else ""
        except (AttributeError, ValueError):
            backend = ""
        if backend == "Running":
            found["prefs"] = run(["tailscale", "debug", "prefs"])
    return found


def root_reads(asked):
    return _at_once(
        {"tailscale": lambda: tailscale(asked["tailscale"])} if "tailscale" in asked else {},
        int(asked.get("limit", 25)),
    )


# ------------------------------------------------------------------------------------------------- the whole ----


def _line(stream, value):
    stream.write(json.dumps(value) + "\n")
    stream.flush()


def _as_the_user(entry, write_end, asked):
    """In the child: become the VM user, make the user's reads, one line each to the parent, and end."""
    out = os.fdopen(write_end, "w")
    try:
        os.setgroups(os.getgrouplist(entry.pw_name, entry.pw_gid))
        os.setgid(entry.pw_gid)
        os.setuid(entry.pw_uid)
        os.environ.update(HOME=entry.pw_dir, USER=entry.pw_name, LOGNAME=entry.pw_name, STATUS_PROBE_DROPPED="1")
        try:
            os.chdir(entry.pw_dir)
        except OSError:
            pass
        _line(out, user_reads(asked, entry.pw_dir, early=lambda part: _line(out, {"early": part})))
    except Exception as error:  # noqa: BLE001 - said as every user read's failure
        _line(out, {"failed": True, "msg": str(error) or type(error).__name__})
    out.close()


def probe(asked, say=None):
    """Every read asked for, the user's as the user and root's as root, at once. ``say``, given, is handed the reads in
    the moment only apt's simulation is still running."""
    root = os.geteuid() == 0
    entry = pwd.getpwnam(asked["user"]) if root else None
    if root and entry.pw_uid != 0:
        # Before any thread starts: the child becomes the user, and writes its reads to a pipe, one document a line.
        read_end, write_end = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_end)
            _as_the_user(entry, write_end, asked)
            os._exit(0)
        os.close(write_end)
        box = {}
        thread = threading.Thread(target=lambda: box.update(root=root_reads(asked)), daemon=True)
        thread.start()
        user = {"failed": True, "msg": "the user's reads said nothing"}
        with os.fdopen(read_end) as lines:
            for text in lines:
                said = json.loads(text)
                if "early" in said:
                    thread.join()
                    if say:
                        say({"user": said["early"], "root": box.get("root", {})})
                else:
                    user = said
        os.waitpid(pid, 0)
        thread.join()
        return {"user": user, "root": box.get("root", {"failed": True, "msg": "root's reads did not end"})}
    # Connected as the VM user, or as root where the VM user is root: everything is read as this process.
    home = entry.pw_dir if entry else os.path.expanduser("~")
    box = {}
    thread = threading.Thread(target=lambda: box.update(root=root_reads(asked)), daemon=True)
    thread.start()

    def early(part):
        thread.join()
        if say:
            say({"user": part, "root": box.get("root", {})})

    user = user_reads(asked, home, early=early)
    thread.join()
    return {"user": user, "root": box.get("root", {"failed": True, "msg": "root's reads did not end"})}


def main(asked):
    """The probe as the engine runs it: a JSON document a line on standard output."""
    _line(sys.stdout, {"started": True})
    _line(sys.stdout, probe(asked, say=lambda part: _line(sys.stdout, {"early": part})))
