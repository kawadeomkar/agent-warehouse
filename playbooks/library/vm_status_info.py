#!/usr/bin/python
# Copyright (c) claude-on-proxmox contributors
# MIT License
"""Read what a VM is running and what waits on it - its OS, kernel, load, memory, upgrades - changing nothing."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: vm_status_info
short_description: Read a VM's OS, kernel, load, memory, waiting upgrades and the user's own processes
version_added: "1.0.0"
description:
  - For C(just status) (playbooks/status.yml). Reads the OS and the running kernel, the newest kernel installed,
    whether a reboot is waiting and for which packages, the load, memory and uptime, the upgrades apt would make
    and how many are security ones, and how many claude processes and windows of each editor the user has open.
  - Every read is fixed here, and none changes anything. The upgrades come from C(apt-get -s), a simulation that
    takes no lock and reads the lists as the last C(apt update) left them; nothing here refreshes them. Each read
    runs in a thread of its own with its own time limit, and one that runs out is said as such rather than holding
    the others.
  - Programs are run by absolute path, since the user's PATH over SSH starts with directories the user owns.
options:
  root:
    description: The directory the files read are under, C(/) on a VM; a fixture tree in the unit tests.
    type: str
    default: /
  upgrades:
    description: Whether to ask apt what an upgrade would install.
    type: bool
    default: true
  timeout:
    description: Seconds each read may take; apt's simulation gets four times as long.
    type: int
    default: 5
  editors:
    description:
      - How each editor's windows are known, one entry per editor of the editors list (claude_editors), in its
        order, as the core gives them (the editor_servers filter). A process of the user's is one window of the
        first entry every given field of which holds; an entry that names no program counts none.
    type: list
    elements: dict
    default: []
    suboptions:
      key:
        description: The editor's key in the editors list, which editor_windows_by counts it under.
        type: str
        required: true
      argv0_contains:
        description: Text the program's path contains.
        type: str
        default: ""
      argv0_name_prefix:
        description: The start of the program's own name.
        type: str
        default: ""
      argv1:
        description: Its first argument, whole.
        type: str
        default: ""
      argv_has:
        description: Arguments it has, anywhere.
        type: list
        elements: str
        default: []
author:
  - claude-on-proxmox contributors
"""

EXAMPLES = r"""
- name: Read the VM's system
  vm_status_info:
  register: found
"""

RETURN = r"""
os:
  description: C(PRETTY_NAME) from os-release, or "".
  returned: always
  type: str
kernel:
  description: The kernel running, from C(/proc/sys/kernel/osrelease).
  returned: always
  type: str
kernel_newest:
  description: The newest kernel in C(/boot), by version, or "".
  returned: always
  type: str
reboot_required:
  description: Whether C(/run/reboot-required) exists, as Ubuntu's update-notifier leaves it.
  returned: always
  type: bool
reboot_packages:
  description: The packages C(/run/reboot-required.pkgs) names, each once.
  returned: always
  type: list
  elements: str
load:
  description: The load over 1, 5 and 15 minutes, or an empty list.
  returned: always
  type: list
  elements: float
memory:
  description: C(total) and C(available) in bytes, from C(/proc/meminfo).
  returned: always
  type: dict
uptime:
  description: Seconds since boot, or 0.
  returned: always
  type: int
upgrades:
  description: The packages an upgrade would install, or null when not asked or not read.
  returned: always
  type: int
security:
  description: How many of them come from a C(-security) pocket, or null.
  returned: always
  type: int
claude:
  description: How many claude processes the user runs.
  returned: always
  type: int
vscode_windows:
  description: As 1.0 has it, editor_windows_by's count under the key vscode, or 0.
  returned: always
  type: int
editor_windows:
  description: How many windows of any editor this project opens the user has, every editor's together.
  returned: always
  type: int
editor_windows_by:
  description: How many windows of each editor the user has, by its key in the editors list (claude_editors).
  returned: always
  type: dict
unread:
  description: Each read that failed or ran out of time, by name, with why.
  returned: always
  type: dict
"""

import os
import re
import subprocess
import threading

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.claude_on_proxmox import is_claude, own_processes, version_key

APT_GET = "/usr/bin/apt-get"
INST = re.compile(r"^Inst (\S+) .*\(([^)]*)\)")
# One entry of the editors argument: the core's ProcessMatch (lib/claude_vm/editors/model.py), field for field.
EDITOR = {
    "key": {"type": "str", "required": True},
    "argv0_contains": {"type": "str", "default": ""},
    "argv0_name_prefix": {"type": "str", "default": ""},
    "argv1": {"type": "str", "default": ""},
    "argv_has": {"type": "list", "elements": "str", "default": []},
}


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
        timeout=timeout,
        env={"LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        check=False,
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else f"exit {result.returncode}")
    found = [match.group(2) for match in map(INST.match, result.stdout.splitlines()) if match]
    return {"upgrades": len(found), "security": sum(1 for pocket in found if "-security" in pocket)}


def window(argv, match):
    """Whether argv is one of an editor's windows, as its entry of the editors argument says: every field given
    holds, and the program is named, since a match on the arguments alone would count any of the user's programs."""
    program = argv[0]
    return bool(match["argv0_contains"] or match["argv0_name_prefix"]) and (
        match["argv0_contains"] in program
        and os.path.basename(program).startswith(match["argv0_name_prefix"])
        and (not match["argv1"] or argv[1:2] == [match["argv1"]])
        and all(arg in argv for arg in match["argv_has"])
    )


def processes(root, editors=()):
    """This user's claude processes, and each editor's windows as the editors argument knows them, from /proc, by the
    editor's key in the editors list (claude_editors); editor_windows is every editor's together, and vscode_windows
    stays as 1.0 has it."""
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
    """Each read in a thread of its own, joined for its own limit: one that hangs is said, the rest still land."""
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


def main():
    module = AnsibleModule(
        argument_spec={
            "root": {"type": "str", "default": "/"},
            "upgrades": {"type": "bool", "default": True},
            "timeout": {"type": "int", "default": 5},
            "editors": {"type": "list", "elements": "dict", "default": [], "options": EDITOR},
        },
        supports_check_mode=True,
    )
    root, timeout = module.params["root"], module.params["timeout"]
    reads = {
        "os": (lambda: {"os": os_release(root)}, None),
        "kernel": (lambda: kernels(root), None),
        "reboot": (lambda: reboot(root), None),
        "load": (lambda: load_memory_uptime(root), None),
        "processes": (lambda: processes(root, module.params["editors"]), None),
    }
    if module.params["upgrades"]:
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
    found.update(results)
    module.exit_json(changed=False, unread=unread, **found)


if __name__ == "__main__":
    main()
