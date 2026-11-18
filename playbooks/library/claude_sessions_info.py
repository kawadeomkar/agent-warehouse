#!/usr/bin/python
# Copyright (c) agent-warehouse contributors
# MIT License
"""Read the Claude Code sessions in a Claude Code configuration directory, never their content."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: claude_sessions_info
short_description: Read what the Claude Code sessions in a configuration directory say about themselves
version_added: "1.0.0"
description:
  - Lists the session transcripts under C(<path>/projects/<folder>/<id>.jsonl) and reads, from each,
    the first directory it ran in, the newest Claude Code version that wrote it, the branch it last ran
    on and whether it holds a message at all. Nothing of what was said is returned, only those values.
  - A line still being written, the last one without its newline, is not read.
  - Transcripts of more than 16 MiB in all are read in parts of that size by up to eight processes forked from
    this one, and what each part says put together in order: what is returned is the same.
  - Read-only. just code runs it on this machine and on the VM (playbooks/tasks/session_read.yml), and
    just destroy on the VM, for what of its sessions to save (playbooks/tasks/session_save.yml).
  - A session is open while C(sessions/) registers it to a running claude: a pid that now names another
    program is an entry a claude left behind, never an open session.
options:
  path:
    description: Claude Code's configuration directory, C(~/.claude) by default.
    type: path
    required: true
  days:
    description: The sessions written in the last this many days. C(0) lists none by age.
    type: int
    default: 0
  ids:
    description: These sessions too, whatever their age.
    type: list
    elements: str
    default: []
  all:
    description: Every session, whatever its age.
    type: bool
    default: false
  manifest:
    description:
      - The manifest just code's copies are recorded in on this machine (claude_sessions_copy). With it, each
        session says whether just code copied it here, and whether its copy is still as just code wrote it.
    type: path
  memory:
    description: Also return the folders under C(projects/) whose auto memory folder holds a file.
    type: bool
    default: false
  repo_paths:
    description:
      - Also return C(githubRepoPaths) from Claude Code's C(.claude.json), the one in O(path) or the one beside
        it, which Claude Code keeps in the home directory. Nothing else of that file is returned.
    type: bool
    default: false
  claude:
    description:
      - Also return the version of the first of these Claude Code executables there is. Only these: never
        C(claude) on PATH, which on a machine standing in for the VM in a test would be the developer's.
    type: list
    elements: path
    default: []
author:
  - agent-warehouse contributors
"""

EXAMPLES = r"""
- name: Read the sessions of the last 30 days
  claude_sessions_info:
    path: ~/.claude
    days: 30
  register: found
"""

RETURN = r"""
sessions:
  description: One item per transcript, sorted by folder and ID.
  returned: always
  type: list
  elements: dict
  contains:
    id:
      description: The session's ID, its file's name.
      type: str
    folder:
      description: The folder under C(projects/) it is filed in.
      type: str
    mtime:
      description: When it was last written, in seconds since the epoch.
      type: float
    cwd:
      description: The first directory recorded in it, or "".
      type: str
    version:
      description: The newest Claude Code version recorded in it, or "".
      type: str
    branch:
      description: The git branch its last entry that names one ran on, or "".
      type: str
    conversation:
      description: Whether it holds a user or assistant message; resume refuses a transcript without one.
      type: bool
    open:
      description: The pid of a running claude that C(sessions/) says has it open, or 0.
      type: int
    tools:
      description: Whether it has a C(tool-results/) folder, the large tool outputs it refers to.
      type: bool
    size:
      description: The bytes of the transcript and its C(tool-results/), what a copy of it sends; no link is followed.
      type: int
    subagents:
      description: Whether it has a C(subagents/) folder, the transcripts of the subagents it started.
      type: bool
    subagents_size:
      description: The bytes of its C(subagents/), which a copy sends while subagents are copied; no link is followed.
      type: int
    dir:
      description: Whether it has a folder of its own beside it, for its tool results, subagents and MCP tasks.
      type: bool
    recorded:
      description: Whether O(manifest) records a copy of it, in this folder or another it moved to; false without one.
      type: bool
    copied:
      description: Whether it is the copy O(manifest) records, still as just code wrote it; false without one.
      type: bool
memory:
  description: The folders whose auto memory folder holds a file, sorted; [] without O(memory).
  returned: always
  type: list
  elements: str
registered:
  description: Every session C(sessions/) registers to a running claude, whatever its transcript, its ID to the pid.
  returned: always
  type: dict
repo_paths:
  description: githubRepoPaths, owner/repo to the local paths Claude Code has seen it in; {} without O(repo_paths).
  returned: always
  type: dict
version:
  description: The version O(claude) reports, or "" when there is none or it did not answer.
  returned: always
  type: str
"""

import json
import multiprocessing
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from stat import S_ISREG

from ansible.module_utils.agent_warehouse import (
    SESSION_ID,
    file_digest,
    folders,
    manifest_key,
    read_manifest,
    running_claude,
    transcript_entry,
    version_key,
)
from ansible.module_utils.basic import AnsibleModule

VERSION = re.compile(r"\d+(?:\.\d+)+")
# A transcript is read in parts of this many bytes, each by one process where there is more than one to read: parsing
# every line is most of what this module costs, 5 s for a month of one person's sessions on one core.
PART = 16 << 20
# The most processes that read at once.
READERS = 8


def summary_of_part(job):
    """What the complete lines that start in one part of a transcript say: (cwd, version, branch, conversation).

    ``job`` is (path, start, end): the lines that start at ``start`` or after, and before ``end``. A line begun before
    ``start`` is the part before's, and one begun before ``end`` is this one's to its newline. None when the file cannot
    be read.
    """
    path, start, end = job
    cwd = version = branch = ""
    best, conversation = None, False
    try:
        with open(path, "rb") as handle:
            if start:
                handle.seek(start - 1)
                if handle.read(1) != b"\n":
                    handle.readline()
            while handle.tell() < end:
                line = handle.readline()
                if not line.endswith(b"\n"):
                    break
                entry = transcript_entry(line)
                found_cwd, found_version, found_branch = entry.get("cwd"), entry.get("version"), entry.get("gitBranch")
                if not cwd and isinstance(found_cwd, str):
                    cwd = found_cwd
                # The newest by its numbers, the first of equal ones; the newest found so far is not compared again.
                if isinstance(found_version, str) and found_version != version and VERSION.fullmatch(found_version):
                    key = version_key(found_version)
                    if best is None or key > best:
                        version, best = found_version, key
                if isinstance(found_branch, str) and found_branch:
                    branch = found_branch
                if not conversation and entry.get("type") in ("user", "assistant"):
                    conversation = True
    except OSError:
        return None
    return cwd, version, branch, conversation


def summary_of_parts(parts):
    """What a transcript says about itself from what each of its parts says, in the transcript's order."""
    found = {"cwd": "", "version": "", "branch": "", "conversation": False}
    best = None
    for cwd, version, branch, conversation in parts:
        if not found["cwd"] and cwd:
            found["cwd"] = cwd
        if version and (best is None or version_key(version) > best):
            found["version"], best = version, version_key(version)
        if branch:
            found["branch"] = branch
        found["conversation"] = found["conversation"] or conversation
    return found


def read_together(jobs, readers, said):
    """summary_of_part of each of ``jobs``, in order, by ``readers`` processes; None when they could not be had, and
    why appended to ``said``.

    Forked from this one, never started afresh: Ansible runs this module from a zip it built, which a new interpreter
    could not import it from, and Python 3.14 starts them by a forkserver on Linux unless told.
    """
    try:
        with ProcessPoolExecutor(readers, mp_context=multiprocessing.get_context("fork")) as pool:
            return list(pool.map(summary_of_part, jobs))
    except Exception as error:  # noqa: BLE001 - a pool that fails leaves the reading to this process
        said.append(type(error).__name__)
        return None


def summaries(paths, readers=None, said=None):
    """summarize of each of ``paths``, in order: None for one that cannot be read.

    Each transcript is cut into parts of PART bytes, the last read to the end of the file as it is then. With more
    than one part in all, and more than one CPU, the parts are read by several processes at once; ``readers`` says how
    many, whatever the bytes. Where they cannot be had, this process reads every part, and ``said`` gains why.
    """
    jobs, owners, total = [], [], 0
    for index, path in enumerate(paths):
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        total += size
        for start in range(0, max(size, 1), PART):
            jobs.append((path, start, start + PART if start + PART < size else sys.maxsize))
            owners.append(index)
    if readers is None:
        readers = min(os.cpu_count() or 1, READERS) if total > PART else 1
    found = read_together(jobs, readers, [] if said is None else said) if readers > 1 and len(jobs) > 1 else None
    if found is None:
        found = [summary_of_part(job) for job in jobs]
    parts = [[] for _ in paths]
    for index, part in zip(owners, found, strict=True):
        parts[index].append(part)
    return [None if any(part is None for part in each) else summary_of_parts(each) for each in parts]


def summarize(path):
    """What the transcript at ``path`` says about itself: cwd, version, branch, conversation.

    Every complete line is parsed, and only those four values are kept.
    """
    (found,) = summaries([path], readers=1)
    if found is None:
        raise OSError(f"{path} cannot be read")
    return found


def open_sessions(path, proc="/proc"):
    """Session ID to the pid of a running claude that has it open, from Claude Code's sessions/ registry.

    Each file is named for its claude's pid, so a claude that starts under a pid a stale entry names
    replaces that entry: only a pid another program took since can be stale, and that one is checked.
    """
    found = {}
    try:
        names = os.listdir(os.path.join(path, "sessions"))
    except OSError:
        return found
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(path, "sessions", name), encoding="utf-8") as handle:
                entry = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        pid, session = entry.get("pid"), entry.get("sessionId")
        if isinstance(pid, int) and pid > 0 and isinstance(session, str) and running_claude(pid, proc):
            found[session] = pid
    return found


def memory_folders(path):
    """The folders under ``path``/projects whose memory/ holds at least one file, at any depth."""
    found = []
    for folder in folders(os.path.join(path, "projects")):
        memory = os.path.join(folder.path, "memory")
        if os.path.isdir(memory) and not os.path.islink(memory) and any(files for _, _, files in os.walk(memory)):
            found.append(folder.name)
    return found


def list_sessions(path, days, ids, now=None, every=False, manifest=None, opened=None, said=None):
    """Every transcript under ``path``/projects written in the last ``days`` days, named in ``ids``, or with ``every``.

    With ``manifest`` - just code's record of its copies here, read_manifest's - each session also says
    whether it was copied here and whether its copy is still as written. ``opened`` is open_sessions'. ``said`` gains
    why the transcripts were read by this process alone, where they were to be read together.
    """
    now = time.time() if now is None else now
    projects, wanted, listed, sessions = os.path.join(path, "projects"), set(ids), [], []
    opened = open_sessions(path) if opened is None else opened
    copied_ids = {os.path.basename(key)[: -len(".jsonl")] for key in manifest or {}}
    for folder in folders(projects):
        try:
            files = sorted(os.scandir(folder.path), key=lambda entry: entry.name)
        except OSError:
            continue
        for item in files:
            session = item.name[: -len(".jsonl")] if item.name.endswith(".jsonl") else ""
            try:
                if not SESSION_ID.fullmatch(session) or not item.is_file(follow_symlinks=False):
                    continue
                # A transcript Claude Code's sweep, or the user, removes as this lists them is not listed.
                stat = item.stat(follow_symlinks=False)
            except OSError:
                continue
            if not every and session not in wanted and not (days > 0 and now - stat.st_mtime <= days * 86400):
                continue
            listed.append((folder, session, item.path, stat))
    # Every transcript at once, so that their parts can be read together.
    found = summaries([item[2] for item in listed], said=said)
    for (folder, session, transcript, stat), summary in zip(listed, found, strict=True):
        if summary is None:
            continue
        try:
            recorded = (manifest or {}).get(manifest_key(folder.name, session))
            copied = recorded is not None and file_digest(transcript) == recorded
        except OSError:
            continue
        sessions.append(
            {
                "id": session,
                "folder": folder.name,
                "mtime": stat.st_mtime,
                "open": opened.get(session, 0),
                "tools": os.path.isdir(os.path.join(folder.path, session, "tool-results")),
                "size": stat.st_size + tree_size(os.path.join(folder.path, session, "tool-results")),
                "subagents": _real_dir(os.path.join(folder.path, session, "subagents")),
                "subagents_size": tree_size(os.path.join(folder.path, session, "subagents")),
                "dir": os.path.isdir(os.path.join(folder.path, session)),
                "recorded": session in copied_ids,
                "copied": copied,
                **summary,
            }
        )
    return sessions


def _real_dir(path):
    """Whether ``path`` is a folder and no link to one."""
    return os.path.isdir(path) and not os.path.islink(path)


def tree_size(path):
    """The bytes of the plain files under ``path``, no link followed; 0 where there is no such folder, and a file that
    goes as it is read counted as nothing."""
    total = 0
    if os.path.islink(path):
        return total
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                stat = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            if S_ISREG(stat.st_mode):
                total += stat.st_size
    return total


def repo_paths(path):
    """githubRepoPaths from the .claude.json Claude Code keeps in ``path``, or beside it in the home directory."""
    for candidate in (
        os.path.join(path, ".claude.json"),
        os.path.join(os.path.dirname(path.rstrip("/")), ".claude.json"),
    ):
        try:
            with open(candidate, encoding="utf-8") as handle:
                listed = json.load(handle).get("githubRepoPaths")
        except (OSError, ValueError, AttributeError):
            continue
        # One without the key is not the one Claude Code keeps it in: the next is asked.
        if isinstance(listed, dict):
            return {
                str(repo): [str(item) for item in paths if isinstance(item, str)]
                for repo, paths in listed.items()
                if isinstance(paths, list)
            }
    return {}


def claude_version(candidates):
    """The version the first Claude Code there is reports, or ""."""
    found = next((path for path in candidates if os.path.isfile(path) and os.access(path, os.X_OK)), None)
    if not found:
        return ""
    try:
        answer = subprocess.run(
            [found, "--version"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    version = VERSION.search(answer.stdout or "")
    return version.group(0) if answer.returncode == 0 and version else ""


def main():
    module = AnsibleModule(
        argument_spec={
            "path": {"type": "path", "required": True},
            "days": {"type": "int", "default": 0},
            "ids": {"type": "list", "elements": "str", "default": []},
            "all": {"type": "bool", "default": False},
            "manifest": {"type": "path"},
            "memory": {"type": "bool", "default": False},
            "repo_paths": {"type": "bool", "default": False},
            "claude": {"type": "list", "elements": "path", "default": []},
        },
        supports_check_mode=True,
    )
    params = module.params
    manifest = read_manifest(params["manifest"]) if params["manifest"] else None
    opened, said = open_sessions(params["path"]), []
    sessions = list_sessions(
        params["path"], params["days"], params["ids"], every=params["all"], manifest=manifest, opened=opened, said=said
    )
    for why in said:
        module.warn(
            f"The transcripts were read by one process: the processes to read them together could not be had ({why})."
        )
    module.exit_json(
        changed=False,
        sessions=sessions,
        memory=memory_folders(params["path"]) if params["memory"] else [],
        registered=opened,
        repo_paths=repo_paths(params["path"]) if params["repo_paths"] else {},
        version=claude_version(params["claude"]) if params["claude"] else "",
    )


if __name__ == "__main__":
    main()
