# Copyright (c) agent-warehouse contributors
# MIT License
"""What the modules in playbooks/library/ share: a session's ID, just code's manifest, a transcript's lines, the
folders in a folder, a version's order, the user's own processes, and which of them is claude.

Each module imports it as ansible.module_utils.agent_warehouse, and Ansible sends it along with the module, to the
VM or to this machine: a playbook's module_utils folder is on the path its modules are built from, as its library
folder is (docs/architecture/custom-code.md §7.10). Standard library only, and no AnsibleModule: each function reads
and hands back data, and the module decides what to do with it.

playbooks/files/claude-start.sh keeps a copy of alive, is_claude, process_argv and running_claude, since it runs as
plain python3 -I and not as a module; tests/unit/test_code_session.py holds the copy equal to these.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess

# A session's ID: what just code accepts as --session, and what Claude Code names a transcript. Matched whole with
# fullmatch: an anchored match takes a name with a newline at its end, and a VM names its own files.
SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# The format of claude_sessions_copy's manifest; one of another format reads as none.
MANIFEST_FORMAT = 1


def manifest_key(folder, session):
    """The manifest's name for the copy of ``session`` in ``folder``: its transcript's path under projects/."""
    return f"{folder}/{session}.jsonl"


def read_manifest(path):
    """The copies just code recorded in ``path``: ``<folder>/<id>.jsonl`` to the digest it wrote, or {}."""
    try:
        with open(path, encoding="utf-8") as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return {}
    copies = found.get("sessions") if isinstance(found, dict) and found.get("format") == MANIFEST_FORMAT else None
    return {str(key): str(value) for key, value in copies.items()} if isinstance(copies, dict) else {}


def file_digest(path):
    """The SHA-256 of the file at ``path``, as the manifest records a copy's."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def transcript_entry(line):
    """One line of a transcript as the object it holds, or {} for a line that is not a JSON object."""
    try:
        entry = json.loads(line)
    except ValueError:
        return {}
    return entry if isinstance(entry, dict) else {}


def folders(path):
    """The folders directly in ``path``, sorted by name, never one reached through a link; [] when it cannot be read."""
    try:
        return sorted(
            (entry for entry in os.scandir(path) if entry.is_dir(follow_symlinks=False)), key=lambda entry: entry.name
        )
    except OSError:
        return []


def version_key(text):
    """The numbers in ``text``, in order: 2.1.310 sorts after 2.1.39, and the kernel 6.8.0-87 after 6.8.0-9."""
    return tuple(int(part) for part in re.findall(r"\d+", text))


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        return False
    return True


# The one rule of every module that asks, just status's count and just code's open and busy checks alike: the program
# or its first argument named claude, or either of them Claude Code's npm package, so that npm's bin run through its
# node shebang, ["node", "/usr/bin/claude"], is claude to all of them.
def is_claude(argv):
    """Whether a process's argv is Claude Code's: the native binary, or the npm package under node."""
    names = [os.path.basename(arg) for arg in argv[:2]]
    return "claude" in names or any("@anthropic-ai/claude-code" in arg for arg in argv[:2])


def process_argv(pid, proc="/proc"):
    """The argv of process ``pid``, from /proc where there is one, else ps; None when it cannot be read."""
    if os.path.isdir(os.path.join(proc, "self")):
        try:
            with open(os.path.join(proc, str(pid), "cmdline"), "rb") as handle:
                return [part.decode("utf-8", "replace") for part in handle.read().split(b"\0") if part]
        except FileNotFoundError:
            return []
        except OSError:
            return None
    try:
        found = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return found.stdout.split()


def running_claude(pid, proc="/proc"):
    """Whether ``pid`` is a running claude. A pid reused by another program since is not; one unreadable is."""
    if not alive(pid):
        return False
    argv = process_argv(pid, proc)
    return argv is None or is_claude(argv)


def own_processes(proc="/proc"):
    """(pid, argv, its folder in ``proc``) of each process of this user's that has an argv; none without ``proc``.

    /proc alone, never ps: a process that cannot be read, or that ends as it is read, is passed over.
    """
    try:
        names = os.listdir(proc)
    except OSError:
        return
    uid = os.getuid()
    for name in names:
        if not name.isdigit():
            continue
        base = os.path.join(proc, name)
        try:
            if os.stat(base).st_uid != uid:
                continue
            with open(os.path.join(base, "cmdline"), "rb") as handle:
                argv = [part.decode("utf-8", "replace") for part in handle.read().split(b"\0") if part]
        except OSError:
            continue
        if argv:
            yield int(name), argv, base
