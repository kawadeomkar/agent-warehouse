#!/usr/bin/python
# Copyright (c) agent-warehouse contributors
# MIT License
"""Write the copies of another machine's Claude Code sessions that the VM resumes, from the files rsync brought."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: claude_sessions_copy
short_description: Write the copies of another machine's Claude Code sessions that this one resumes
version_added: "1.0.0"
description:
  - Reads each session from O(mirror), where rsync left this machine's copy of the other machine's
    C(projects/<folder>/<id>.jsonl), and writes it to C(<dest>/projects/<folder>/<id>.jsonl) with four
    edits and nothing else, its complete lines only, 0600 in a 0700 folder. Its C(tool-results/) folder
    comes along, and with O(subagents) its C(subagents/) folder.
  - The four edits. The memory folder named in each C(prompt_snapshot) attachment, which resume sends
    as the system prompt until the conversation is compacted, becomes O(sessions[].memory). The folder
    the other machine saved this session's large tool outputs in, as its tool results name it, becomes
    the copy's C(tool-results/) here, in user entries only - the tool results - so that a resumed session
    can read them; an assistant's entries, which may carry signed thinking, are never changed. When the
    last C(worktree-state) entry binds the session to a worktree, an entry clearing it is appended:
    Claude Code clears a binding to a worktree that is gone itself, but refuses to resume one whose path
    is there and is not that worktree, as where both machines have it. A C(relocated) entry naming
    O(sessions[].checkout) is appended last, so that the C(/resume) picker opens it there.
  - A copy keeps the other machine's file time while that is in the last 14 days, and is otherwise given
    the time it is written, renewed once that is 14 days old: Claude Code's sweep deletes a transcript
    here 30 days after its file time (cleanupPeriodDays), which would take a session asked for by ID,
    whatever its age, before it was resumed. Each file of its C(tool-results/) is given the transcript's
    time too, since the sweep takes those by their own age, and is compared by its size and digest, never
    its time - or, while what O(manifest) records it was made from still holds, as for the copy, not read.
  - The records Claude Code keeps of each request it sent (C(api-request-shape), C(api-request-blob),
    since 2.1.300) name the other machine's memory folder too, and stay as they are: a resume sends the
    C(prompt_snapshot), never them (2.1.308, held by the configure scenario).
  - A copy is replaced only while it is exactly what this module last wrote, as O(manifest) records by
    digest. One that differs was continued here, or was never this module's, and is left as it is.
  - Beside each digest, O(manifest) records the transcript's size and time, the copy's size, time and
    inode, and a digest of everything else the edits are made from, as they were when the copy was last
    found as it would be. While all of them are still so, the copy is that one, unchanged, and neither
    file is read again.
  - A copy that is gone while its session's ID is in another folder of C(<dest>/projects/) moved there,
    as C(/cd) moves a session it continues, and is left the same way: written again, the ID would be in
    two folders, and C(claude --resume <id>) from a third would find neither. But where that other copy
    is this module's own, still as written, the session moved on the other machine instead: it is written
    in its new folder, and the old copy removed.
  - With O(subagents), each file of the session's C(subagents/) goes with it while the session's copy is
    written or already what it would be. A subagent's transcript gets one edit, the memory folder of its
    recorded prompt and, in user entries, its session's tool results, as its session's does; never the
    entries appended to the session's own. Its other files are copied as they are. Each file is written
    where there is none, or where it is still what the manifest's C(subagents) record says the last run
    wrote, at its session's time; one that differs, a subagent continued on the VM, is left.
  - O(manifest) is written after each copy, so a run that stops part way still records what it wrote.
  - Nothing of a session's content is returned.
options:
  mirror:
    description: The folder rsync copied the other machine's C(projects/) folders into.
    type: path
    required: true
  dest:
    description: Claude Code's configuration directory here, C(~/.claude).
    type: path
    required: true
  manifest:
    description: The file that records the digest of each copy this module wrote, and what it was made from.
    type: path
    required: true
  here:
    description:
      - The other machine's Claude Code configuration directory, as its sessions name it. A copy whose
        recorded prompt still names a memory folder under it after the edit is not written.
    type: str
    default: ""
  sessions:
    description: The sessions to copy.
    type: list
    elements: dict
    required: true
    suboptions:
      id:
        description: The session's ID.
        type: str
        required: true
      folder:
        description: Its folder under C(projects/), kept as it is.
        type: str
        required: true
      checkout:
        description: The checkout here it continues in.
        type: str
        required: true
      memory:
        description: The memory folder here its recorded prompt is pointed at.
        type: str
        required: true
  prune:
    description: Remove from O(mirror) every session that is not in O(sessions).
    type: bool
    default: true
  subagents:
    description: Copy each session's C(subagents/) with it (claude_sessions_sync_subagents).
    type: bool
    default: false
author:
  - agent-warehouse contributors
"""

EXAMPLES = r"""
- name: Write the copies
  claude_sessions_copy:
    mirror: /home/dev/projects/.agent-warehouse/claude-sessions/mirror
    dest: /home/dev/.claude
    manifest: /home/dev/projects/.agent-warehouse/claude-sessions/manifest.json
    sessions:
      - id: 0a608ad8-0c5e-4d8e-9a7e-2f6d1f0b9c11
        folder: -Users-me-homeserver
        checkout: /home/dev/projects/homeserver
        memory: /home/dev/.claude/projects/-home-dev-projects-homeserver/memory/
"""

RETURN = r"""
written:
  description: The sessions whose copy was written or replaced.
  returned: always
  type: list
  elements: str
unchanged:
  description: The sessions whose copy was already what it would be.
  returned: always
  type: list
  elements: str
held:
  description: Each session left as it is because its copy is in another folder here, to that folder.
  returned: always
  type: dict
continued:
  description: The sessions continued here since they were copied, and not changed on the other machine since.
  returned: always
  type: list
  elements: str
diverged:
  description: The sessions whose copy here differs from what was copied, and that changed on the other machine too.
  returned: always
  type: list
  elements: str
unreadable:
  description: The sessions not written, each with why.
  returned: always
  type: list
  elements: dict
subagents:
  description:
    - How many subagent files were written, left as the VM changed them, and not written because their
      recorded prompt names a memory folder in a form the edit does not know. All 0 without O(subagents).
  returned: always
  type: dict
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from stat import S_ISREG

from ansible.module_utils.agent_warehouse import (
    MANIFEST_FORMAT,
    SESSION_ID,
    file_digest,
    folders,
    manifest_key,
    read_manifest,
    transcript_entry,
)
from ansible.module_utils.basic import AnsibleModule

# How Claude Code's system prompt names the auto-memory folder (2.1.308): read from each entry, never computed,
# since the folder is Claude Code's own name for the directory the session was started in.
MEMORY_AT = re.compile(r"persistent file-based memory at `([^`\n]+)`")
# A copy keeps the other machine's file time while it is this recent; Claude Code sweeps a transcript
# 30 days after its file time.
FRESH = 14 * 86400
# The edits' version, part of what a copy is recorded as made from: raised whenever edit_transcript, or anything it
# calls, writes a copy otherwise, so that a copy an earlier version wrote is edited again rather than skipped as
# unchanged. tests/unit/test_session_handoff.py holds it to the edits' source.
EDITS = 2


def _escaped(text):
    """``text`` as it stands inside a JSON string in Claude Code's transcripts, which JSON.stringify writes."""
    return json.dumps(text, ensure_ascii=False)[1:-1].encode("utf-8")


def _line(entry):
    return json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def complete_lines(data):
    """``data`` up to its last newline: a line still being written is left for the next copy."""
    return data[: data.rfind(b"\n") + 1]


def _left_behind(here, folders, found):
    """What a recorded prompt still naming the session's own memory folder here looks like, after the edit.

    The session's own: the folder of each memory path the prompt names, and ``folders``. Without any,
    every memory folder under ``here``. Another project's, which a prompt can quote, is left as text.
    """
    projects = here.rstrip("/") + "/projects/"
    own = set(folders)
    for path in found:
        named = re.match(re.escape(projects) + r"([^/]+)/memory", path, re.IGNORECASE)
        if named:
            own.add(named.group(1))
    which = b"|".join(re.escape(_escaped(name)) for name in sorted(own)) if own else rb'[^"/\\]+'
    return re.compile(re.escape(_escaped(projects)) + rb"(?:" + which + rb")/memory", re.IGNORECASE)


def edit_transcript(data, session, checkout, memory, here="", results="", folder="", own=True):
    """The copy of transcript ``data`` this machine resumes: (bytes, "") or (b"", why it is not written).

    ``data`` holds complete lines only. Every line comes out byte for byte but a prompt_snapshot
    attachment's, where only the memory folder's path changes, and a user entry naming this session's
    tool-results folder under ``here``, which becomes ``results``; two entries may be appended after them.
    ``folder`` is the session's folder here, whose memory folder the prompt must no longer name. ``own`` false
    is a subagent's transcript, which gets the edits but never the two entries: they are its session's.
    """
    out, bound = [], False
    memory_escaped = _escaped(memory)
    # Any folder of the other machine's: a session continued after /cd there names the old one.
    results_at = (
        re.compile(
            re.escape(_escaped(here.rstrip("/") + "/projects/"))
            + rb'[^"/\\]+/'
            + re.escape(session.encode())
            + rb"/tool-results/",
            re.IGNORECASE,
        )
        if here and results
        else None
    )
    results_escaped = _escaped(results.rstrip("/") + "/") if results else b""
    for line in data.splitlines(keepends=True):
        if (
            results_at
            and b"/tool-results/" in line
            and results_at.search(line)
            and transcript_entry(line).get("type") == "user"
        ):
            line = results_at.sub(lambda _: results_escaped, line)
        if b'"prompt_snapshot"' in line:
            entry = transcript_entry(line)
            attachment = entry.get("attachment")
            if entry.get("type") == "attachment" and isinstance(attachment, dict):
                if attachment.get("type") == "prompt_snapshot":
                    found = sorted({found for text in _strings(attachment) for found in MEMORY_AT.findall(text)})
                    for path in found:
                        if _escaped(path) not in line:
                            return b"", "its recorded prompt names its memory folder in a form just code cannot rewrite"
                        line = line.replace(_escaped(path), memory_escaped)
                    if here and _left_behind(here, [folder] if folder else [], found).search(line):
                        return b"", "its recorded prompt names a memory folder in a form just code does not know"
        if b'"worktree-state"' in line:
            entry = transcript_entry(line)
            if entry.get("type") == "worktree-state":
                bound = bool(entry.get("worktreeSession"))
        out.append(line)
    if not own:
        return b"".join(out), ""
    if bound:
        out.append(_line({"type": "worktree-state", "worktreeSession": None, "sessionId": session}))
    out.append(_line({"type": "relocated", "relocatedCwd": checkout, "sessionId": session}))
    return b"".join(out), ""


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def made_from(session, folder, checkout, memory, here, results):
    """A digest of what edit_transcript makes a copy from besides the transcript's bytes, and of the edits' version."""
    return _digest(json.dumps([EDITS, session, folder, checkout, memory, here, results]).encode("utf-8"))


def fingerprint(source, target, inputs):
    """What says a copy is still the one recorded: ``source``'s size and time, ``target``'s size, time and inode, and
    ``inputs``, made_from's; None where either file cannot be read, or the copy is not a plain file.

    The transcript's inode is left out: rsync may write it anew with the same bytes and time.
    """
    try:
        mirror, copy = os.stat(source), os.lstat(target)
    except OSError:
        return None
    if not S_ISREG(copy.st_mode):
        return None
    return {
        "mirror": [mirror.st_size, mirror.st_mtime_ns],
        "copy": [copy.st_size, copy.st_mtime_ns, copy.st_ino],
        "inputs": inputs,
    }


def read_made_from(path):
    """What ``path``, a manifest, records each copy as made from, fingerprint's, by its key; {} for none."""
    try:
        with open(path, encoding="utf-8") as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return {}
    made = found.get("made_from") if isinstance(found, dict) and found.get("format") == MANIFEST_FORMAT else None
    return {str(key): value for key, value in made.items() if isinstance(value, dict)} if isinstance(made, dict) else {}


def _directory(path, created):
    """Make ``path`` and any parent missing, 0700 each; one that exists keeps its mode."""
    if os.path.isdir(path):
        return
    _directory(os.path.dirname(path), created)
    os.mkdir(path, 0o700)
    os.chmod(path, 0o700)
    created.append(path)


def _write(path, data, mtime):
    """Write ``data`` to ``path`` through a file beside it, 0600, with ``mtime``."""
    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".agent-warehouse-")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.chmod(temporary, 0o600)
        os.utime(temporary, (mtime, mtime))
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def copy_time(source, current, now):
    """The file time a copy is given: ``source``'s while recent, else ``current``'s while that is, else ``now``."""
    if now - source < FRESH:
        return source
    if current is not None and now - current < FRESH and current >= source:
        return current
    return now


def _save_manifest(manifest, copies, made, created, subagents=None):
    """Write ``copies``, each key's digest, ``made``, what each was made from, and ``subagents``, the digest of each
    subagent file written with its session. A key of its own beside the digests keeps the format: a reader of the
    digests alone, an earlier version's, passes over it, and an earlier version that writes the manifest again leaves
    it out, so that each copy is then read once more.

    ``made`` holds a session's copy by its key, and each file of its ``tool-results/`` by its own,
    ``<folder>/<id>/tool-results/<file>``: kept while that session's copy is recorded."""
    _directory(os.path.dirname(manifest), created)
    made = {key: value for key, value in made.items() if key in copies or _session_key(key) in copies}
    record = {"format": MANIFEST_FORMAT, "sessions": copies, "made_from": made, "subagents": subagents or {}}
    text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    _write(manifest, text.encode("utf-8"), time.time())


def _session_key(key):
    """The manifest key of the session a file of its ``tool-results/`` or ``subagents/`` belongs to."""
    return "/".join(key.split("/")[:2]) + ".jsonl"


def _made(made, key, source, target, inputs):
    """Record in ``made`` what ``target`` at ``key`` was made from as it now is, fingerprint's, or none where it cannot
    be had."""
    found = fingerprint(source, target, inputs)
    if found is None:
        made.pop(key, None)
    else:
        made[key] = found


def _plain_files(source):
    """(path relative to ``source``, path) of each plain file under it, sorted, no link followed."""
    found = []
    for top, dirs, files in os.walk(source):
        dirs[:] = sorted(name for name in dirs if not os.path.islink(os.path.join(top, name)))
        for name in sorted(files):
            path = os.path.join(top, name)
            if not os.path.islink(path) and os.path.isfile(path):
                found.append((os.path.relpath(path, source), path))
    return found


def _copy_subagents(source, target, prefix, edit, when, recorded, copies, check, created):
    """Each file of a session's ``subagents/`` from ``source`` in the mirror to ``target``, by the copy's rule.

    A transcript (``.jsonl``) is cut to its complete lines and given ``edit``; any other file goes as it is. Each
    is written where there is none or where it is still what ``recorded`` (the manifest's subagents record, keyed
    ``<prefix>/<file>``) says the last run wrote, at the time ``when``; one that differs, a subagent continued on
    the VM, is left. ``copies`` is updated with what is written or found as it would be. Returns (counts, changed).
    """
    counts, changed = {"written": 0, "left": 0, "refused": 0}, False
    for rel, path in _plain_files(source):
        key = f"{prefix}/{rel}"
        with open(path, "rb") as handle:
            data = handle.read()
        if rel.endswith(".jsonl"):
            data = complete_lines(data)
            if not data:
                continue
            data, why = edit(data)
            if why:
                counts["refused"] += 1
                continue
        digest, there = _digest(data), os.path.join(target, rel)
        current = file_digest(there) if os.path.isfile(there) and not os.path.islink(there) else None
        # A link or folder in its place is the VM's: left, as a copy that differs is.
        action = "diverged" if os.path.lexists(there) and current is None else None
        action = action or classify(current, recorded.get(key), digest)
        if action in ("continued", "diverged"):
            counts["left"] += 1
            continue
        copies[key] = digest
        if action == "write":
            counts["written"] += 1
            changed = True
            if not check:
                _directory(os.path.dirname(there), created)
                _write(there, data, when)
        elif int(os.stat(there).st_mtime) != int(when):
            changed = True
            if not check:
                os.utime(there, (when, when))
    return counts, changed


def _remove_copy(dest, folder, session, subagents=None):
    """Remove this module's copy of ``session`` in ``folder``: its transcript, its tool results, the subagent files
    ``subagents`` (the manifest's record, updated) says it wrote that are still as written, what is left empty."""
    base = os.path.join(dest, "projects", folder)
    os.unlink(os.path.join(base, f"{session}.jsonl"))
    tools = os.path.join(base, session, "tool-results")
    if os.path.isdir(tools) and not os.path.islink(tools):
        shutil.rmtree(tools)
    agents, prefix = os.path.join(base, session, "subagents"), f"{folder}/{session}/subagents/"
    for key in [key for key in (subagents or {}) if key.startswith(prefix)]:
        path = os.path.join(agents, key[len(prefix) :])
        if os.path.isfile(path) and not os.path.islink(path) and file_digest(path) == subagents[key]:
            os.unlink(path)
        subagents.pop(key)
    if os.path.isdir(agents) and not os.path.islink(agents):
        for top, _dirs, _files in os.walk(agents, topdown=False):
            try:
                os.rmdir(top)
            except OSError:
                pass
    for path in (os.path.join(base, session), base):
        try:
            os.rmdir(path)
        except OSError:
            break


def classify(current, recorded, digest, moved=False):
    """What to do with a copy here: ``current`` its digest (None: there is none), ``recorded`` the manifest's.

    "write" where there is none or it is still what was last written, "unchanged" where it is what
    would be written, and otherwise "continued" - changed here, not on the other machine since - or
    "diverged". ``moved``: there is none because the session moved to another folder here, which
    counts as changed here.
    """
    if moved:
        return "continued" if recorded == digest else "diverged"
    if current is None or (current == recorded and current != digest):
        return "write"
    if current == digest:
        return "unchanged"
    return "continued" if recorded == digest else "diverged"


def _folders_holding(dest, sessions):
    """{id: [folder, ...]}: the folders of ``dest``/projects that hold a transcript of each session ID asked about."""
    wanted, found = {session["id"] for session in sessions}, {}
    for folder in folders(os.path.join(dest, "projects")):
        try:
            names = {entry.name for entry in os.scandir(folder.path)}
        except OSError:
            continue
        for sid in wanted:
            if f"{sid}.jsonl" in names:
                found.setdefault(sid, []).append(folder.name)
    return found


def _same_file(here, there):
    """Whether ``there`` is a plain file holding the bytes of the plain file ``here``."""
    if os.path.islink(there) or not os.path.isfile(there):
        return False
    return os.path.getsize(there) == os.path.getsize(here) and file_digest(there) == file_digest(here)


def _tool_results(source, target, when, check, created, made, prefix):
    """Copy what ``source`` holds and ``target`` lacks or holds otherwise, each file at the time ``when``.

    ``when`` is the session's transcript's time there: Claude Code sweeps tool results by their own age, so a
    file keeping the other machine's time would be deleted before its transcript. Files are compared by size and
    digest, since their time here is never the other machine's - unless ``made`` (keyed ``<prefix>/<file>``) holds
    what it was made from as both still are (fingerprint's), from a run that found them the same. Returns how many
    files were written or retimed.
    """
    changed = 0
    for top, dirs, files in os.walk(source):
        dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(top, name))]
        for name in files:
            here = os.path.join(top, name)
            if os.path.islink(here) or not os.path.isfile(here):
                continue
            rel = os.path.relpath(here, source)
            key, there = f"{prefix}/{rel}", os.path.join(target, rel)
            known = made.get(key) is not None and fingerprint(here, there, "") == made[key]
            if known or _same_file(here, there):
                if int(os.stat(there).st_mtime) != int(when):
                    changed += 1
                    if not check:
                        os.utime(there, (when, when))
                if not check:
                    _made(made, key, here, there, "")
                continue
            changed += 1
            if not check:
                _directory(os.path.dirname(there), created)
                with open(here, "rb") as handle:
                    _write(there, handle.read(), when)
                _made(made, key, here, there, "")
    return changed


def _prune(mirror, keep, check):
    """Remove from ``mirror`` every session that is not in ``keep``; return whether anything was."""
    removed = False
    for folder in folders(mirror):
        for entry in list(os.scandir(folder.path)):
            session = entry.name.removesuffix(".jsonl")
            if (folder.name, session) in keep:
                continue
            removed = True
            if check:
                continue
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)
        if not check and not os.listdir(folder.path):
            os.rmdir(folder.path)
    return removed


def copy_sessions(mirror, dest, manifest, sessions, here="", prune=True, check=False, now=None, subagents=False):
    """Write every session's copy, and with ``subagents`` its subagents'; return the result and whether anything
    changed."""
    now = time.time() if now is None else now
    recorded, recorded_made = read_manifest(manifest), read_made_from(manifest)
    recorded_agents = read_manifest(manifest, "subagents")
    copies, saved = dict(recorded), dict(recorded)
    made, saved_made = dict(recorded_made), dict(recorded_made)
    agents, agents_saved = dict(recorded_agents), dict(recorded_agents)
    result = {
        "written": [],
        "held": {},
        "unchanged": [],
        "continued": [],
        "diverged": [],
        "unreadable": [],
        "subagents": {"written": 0, "left": 0, "refused": 0},
    }
    created, changed = [], False
    holding = _folders_holding(dest, sessions)
    try:
        for session in sessions:
            sid, folder = session["id"], session["folder"]
            key = manifest_key(folder, sid)
            source, target = os.path.join(mirror, key), os.path.join(dest, "projects", key)
            results = os.path.join(dest, "projects", folder, sid, "tool-results")
            inputs = made_from(sid, folder, session["checkout"], session["memory"], here, results)
            others, mac_move = [], False
            known = recorded.get(key) is not None and recorded_made.get(key) is not None
            if known and fingerprint(source, target, inputs) == recorded_made[key]:
                # Neither the transcript nor the copy changed since the copy was written or found as it would be, nor
                # anything else it is made from: it still is, and neither is read.
                action, digest, mtime = "unchanged", recorded[key], os.stat(source).st_mtime
            else:
                try:
                    with open(source, "rb") as handle:
                        data = complete_lines(handle.read())
                    mtime = os.stat(source).st_mtime
                except OSError:
                    result["unreadable"].append({"id": sid, "why": "rsync did not bring it"})
                    continue
                edited, why = (
                    edit_transcript(data, sid, session["checkout"], session["memory"], here, results, folder)
                    if data
                    else (b"", "")
                )
                if not edited:
                    result["unreadable"].append({"id": sid, "why": why or "it holds no complete line"})
                    continue
                digest = _digest(edited)
                current = file_digest(target) if os.path.isfile(target) and not os.path.islink(target) else None
                if os.path.lexists(target) and current is None:
                    result["diverged"].append(sid)
                    continue
                others = [other for other in holding.get(sid, []) if other != folder] if current is None else []
                # The one other folder holding it, with the very copy this module wrote there: the session moved on
                # the other machine (/cd there), not here.
                old = manifest_key(others[0], sid) if len(others) == 1 else ""
                old_path = os.path.join(dest, "projects", old)
                mac_move = (
                    bool(old)
                    and recorded.get(old) is not None
                    and os.path.isfile(old_path)
                    and not os.path.islink(old_path)
                    and file_digest(old_path) == recorded[old]
                )
                if others and not mac_move:
                    result["held"][sid] = others[0]
                action = classify(current, recorded.get(key), digest, bool(others) and not mac_move)
            if action in ("continued", "diverged"):
                result[action].append(sid)
                continue
            if action == "write":
                result["written"].append(sid)
                changed = True
                kept = copy_time(mtime, None, now)
                if not check:
                    _directory(os.path.dirname(target), created)
                    _write(target, edited, kept)
                # Recorded once written, never before: a record of a copy that is not there would make the
                # one that is read as continued here.
                copies[key] = digest
                if mac_move:
                    copies.pop(old, None)
                    made.pop(old, None)
                    if not check:
                        _remove_copy(dest, others[0], sid, agents)
            else:
                result["unchanged"].append(sid)
                copies[key] = digest
                there = os.stat(target).st_mtime
                kept = copy_time(mtime, there, now)
                if int(there) != int(kept):
                    changed = True
                    if not check:
                        os.utime(target, (kept, kept))
            # Taken after the copy's last write and time, from the files as they now are: the copy is the digest
            # recorded, and the transcript, edited, is that too.
            if not check:
                now_made = fingerprint(source, target, inputs)
                if now_made is None:
                    made.pop(key, None)
                else:
                    made[key] = now_made
            tools = os.path.join(mirror, folder, sid, "tool-results")
            if os.path.isdir(tools):
                changed = (
                    _tool_results(tools, results, kept, check, created, made, f"{folder}/{sid}/tool-results") > 0
                    or changed
                )
            source = os.path.join(mirror, folder, sid, "subagents")
            if subagents and os.path.isdir(source) and not os.path.islink(source):
                counts, moved = _copy_subagents(
                    source,
                    os.path.join(dest, "projects", folder, sid, "subagents"),
                    f"{folder}/{sid}/subagents",
                    lambda data, sid=sid, session=session, results=results, folder=folder: edit_transcript(
                        data, sid, session["checkout"], session["memory"], here, results, folder, own=False
                    ),
                    kept,
                    recorded_agents,
                    agents,
                    check,
                    created,
                )
                changed = moved or changed
                for name, count in counts.items():
                    result["subagents"][name] += count
            # After each copy, so that a run stopped part way still records what it wrote: a copy written
            # and not recorded would read as continued here once the session changed on the other machine.
            if (copies, made, agents) != (saved, saved_made, agents_saved) and not check:
                _save_manifest(manifest, copies, made, created, agents)
                saved, saved_made, agents_saved = dict(copies), dict(made), dict(agents)
    finally:
        if (copies, made, agents) != (saved, saved_made, agents_saved) and not check:
            _save_manifest(manifest, copies, made, created, agents)
    if copies != recorded or made != recorded_made or agents != recorded_agents:
        changed = True
    if prune:
        changed = _prune(mirror, {(item["folder"], item["id"]) for item in sessions}, check) or changed
    return result, changed or bool(created)


def main():
    module = AnsibleModule(
        argument_spec={
            "mirror": {"type": "path", "required": True},
            "dest": {"type": "path", "required": True},
            "manifest": {"type": "path", "required": True},
            "here": {"type": "str", "default": ""},
            "sessions": {
                "type": "list",
                "elements": "dict",
                "required": True,
                "options": {
                    "id": {"type": "str", "required": True},
                    "folder": {"type": "str", "required": True},
                    "checkout": {"type": "str", "required": True},
                    "memory": {"type": "str", "required": True},
                },
            },
            "prune": {"type": "bool", "default": True},
            "subagents": {"type": "bool", "default": False},
        },
        supports_check_mode=True,
    )
    params = module.params
    for session in params["sessions"]:
        if "/" in session["folder"] or session["folder"] in ("", ".", "..") or not SESSION_ID.fullmatch(session["id"]):
            module.fail_json(msg=f"{session['folder']}/{session['id']} is not a session's folder and ID.")
    result, changed = copy_sessions(
        params["mirror"],
        params["dest"],
        params["manifest"],
        params["sessions"],
        here=params["here"],
        prune=params["prune"],
        check=module.check_mode,
        subagents=params["subagents"],
    )
    module.exit_json(changed=changed, **result)


if __name__ == "__main__":
    main()
