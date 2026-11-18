#!/usr/bin/python
# Copyright (c) agent-warehouse contributors
# MIT License
"""Write on the VM what just code carries of another machine's Claude Code beside the sessions."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: claude_config_carry
short_description: Write what just code carries of another machine's Claude Code beside the sessions
version_added: "2.8.0"
description:
  - Writes under O(dest), the VM user's C(~/.claude), each file of O(files) from O(mirror), where rsync left the
    other machine's, and each of O(generated), by one rule (docs/architecture/custom-code.md §7.6). An item is
    written where there is none, or where it is still what the last run wrote, as O(manifest) records by digest;
    one already as it would be is left; any other was changed on the VM, or was never just code's, and is left
    as it is and named. A file the manifest records under one of O(scopes) that is no longer asked for is removed
    while it is still as written.
  - O(appended) adds a line to a file as it is written, once, where the file does not hold it already.
  - O(history) appends to C(history.jsonl) under O(dest) each line its file does not hold already, by the session
    and time it records, in one write of whole lines, so a claude appending there at the same time interleaves
    whole lines. A file that is a link is left.
  - O(settings) sets keys of C(settings.json) under O(dest) by the same rule, each key - or each part of a key carried
    part by part, C(<key>.<part>) - an item of its own, its digest recorded in the manifest's C(settings) record.
    Every other key of the file is kept as it is; the file is replaced through one beside it, 0600, written as
    Claude Code writes it, two-space JSON. A file that is a link or no JSON object is left whole, and said.
  - Every path is relative to O(dest), under one of O(scopes), each of a form just code carries; no link is
    followed, on the way to a file or in its place. Files are 0600, folders made 0700, each written through a
    file beside it.
  - O(manifest) is written after each item, so a run that stops part way still records what it wrote, and an item
    is recorded only once it is written. An item whose write or removal fails is named in C(not_written) and the rest go
    on: a part that fails never stops the window. A file planned that rsync did not bring is never removed. Where
    O(mirror) or the manifest's folder is a link, nothing is written and C(refused) says so. Check mode writes
    nothing. Nothing of a file's content is returned.
options:
  dest:
    description: Claude Code's configuration directory here, C(~/.claude).
    type: path
    required: true
  mirror:
    description: The folder rsync copied the other machine's files into, at their paths under its C(~/.claude).
    type: path
    required: true
  manifest:
    description: The file that records the digest of each item this module wrote.
    type: path
    required: true
  scopes:
    description: The folders, as paths under O(dest) ending in C(/), whose files this run manages.
    type: list
    elements: str
    default: []
  files:
    description: The files to write, each from its path in O(mirror) to its path under O(dest).
    type: list
    elements: dict
    default: []
    suboptions:
      source:
        description: Its path in O(mirror).
        type: str
        required: true
      target:
        description: Its path under O(dest).
        type: str
        required: true
  generated:
    description: Files just code makes itself, each its path under O(dest) and its text.
    type: list
    elements: dict
    default: []
    suboptions:
      target:
        description: Its path under O(dest).
        type: str
        required: true
      content:
        description: Its text.
        type: str
        required: true
  appended:
    description: A line to add to a file of O(files) as it is written, by its path under O(dest).
    type: dict
    default: {}
  settings:
    description:
      - C(values), each settings item to carry to its value, and C(items), every item this run manages, so that one
        gone here is removed there while it is still as carried. Run under the task's no_log, never the option's:
        Ansible would blank each of its strings wherever it stands in the result, a setting's name among them.
    type: dict
  history:
    description: The prompt-history lines to append, each the object it holds. Run under the task's no_log.
    type: list
    elements: dict
    default: []
  prune:
    description: Remove from O(mirror) every file that is no source of O(files).
    type: bool
    default: true
author:
  - agent-warehouse contributors
"""

EXAMPLES = r"""
- name: Write the carried memory
  claude_config_carry:
    dest: /home/dev/.claude
    mirror: /home/dev/projects/.agent-warehouse/claude-config/mirror
    manifest: /home/dev/projects/.agent-warehouse/claude-config/manifest.json
    scopes: [projects/-home-dev-projects-demo/memory/]
    files:
      - source: projects/-Users-me-demo/memory/MEMORY.md
        target: projects/-home-dev-projects-demo/memory/MEMORY.md
"""

RETURN = r"""
written:
  description: The paths written or replaced.
  returned: always
  type: list
  elements: str
unchanged:
  description: How many were already what they would be.
  returned: always
  type: int
left:
  description: The paths left as they are, changed on the VM since the last run or never just code's.
  returned: always
  type: list
  elements: str
removed:
  description: The paths removed, gone on the other machine and still as written here.
  returned: always
  type: list
  elements: str
unreadable:
  description: The paths not written because rsync did not bring them.
  returned: always
  type: list
  elements: str
not_written:
  description: The paths whose write or removal failed, each left as it was.
  returned: always
  type: list
  elements: str
refused:
  description: Whether nothing was written because the mirror or the manifest's folder is a link.
  returned: always
  type: bool
history:
  description: How many prompt-history lines were appended.
  returned: always
  type: int
settings:
  description:
    - The settings items written, left as the VM changed them, and removed; C(unreadable) where C(settings.json) is a
      link or no JSON object, and nothing of it was changed. Names only, never a value.
  returned: always
  type: dict
"""

import hashlib
import json
import os
import re
import tempfile

from ansible.module_utils.agent_warehouse import file_digest
from ansible.module_utils.basic import AnsibleModule

# The manifest's format; one of another format reads as none.
FORMAT = 1
# The folders this module may manage, each a path under ~/.claude ending in "/": a project's auto memory folder,
# as Claude Code names the folder of a path.
SCOPES = (
    re.compile(r"projects/-[A-Za-z0-9-]{1,200}/memory/"),
    # The global instructions, a file of its own, and the folder of rules beside them.
    re.compile(r"CLAUDE\.md"),
    re.compile(r"rules/"),
    # The user-level extensions; skills/synced/, the account's own, is never written (_NEVER).
    re.compile(r"(?:skills|agents|commands|output-styles|workflows|themes)/"),
    re.compile(r"keybindings\.json"),
)
# Under a scope, never: Claude Code downloads the account's skills there at each sign-in.
_NEVER = ("skills/synced/",)
# One component of a path under a scope: no dot first, no control character.
_PART = re.compile(r"[^./\x00-\x1f\x7f][^/\x00-\x1f\x7f]{0,254}")
# How deep below its scope a file may be.
DEPTH = 5


def read_manifest(path, part="files"):
    """{path under dest: digest} of what the last run wrote, or {}; ``settings``, {item: digest} of the settings."""
    try:
        with open(path, encoding="utf-8") as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return {}
    files = found.get(part) if isinstance(found, dict) and found.get("format") == FORMAT else None
    return {str(key): str(value) for key, value in files.items()} if isinstance(files, dict) else {}


def scope_of(path, scopes):
    """The scope ``path`` is under, or "" when it is under none or not a path this module writes. A scope that does not
    end in "/" is one file, and only that path is under it."""
    if path.startswith(_NEVER):
        return ""
    for scope in scopes:
        if not scope.endswith("/"):
            if path == scope:
                return scope
            continue
        rest = path[len(scope) :] if path.startswith(scope) else ""
        parts = rest.split("/") if rest else []
        if parts and len(parts) <= DEPTH and all(_PART.fullmatch(part) for part in parts):
            return scope
    return ""


def classify(current, recorded, digest):
    """What to do with an item there: ``current`` its digest (None: there is none), ``recorded`` the manifest's.

    "write" where there is none or it is still what was last written, "unchanged" where it is what would be
    written, and "left" otherwise: changed there since, or never this module's.
    """
    if current is None or (current == recorded and current != digest):
        return "write"
    if current == digest:
        return "unchanged"
    return "left"


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _no_link_on_the_way(dest, path):
    """Whether no folder from ``dest`` down to ``path``'s own is a link: a write never goes through one."""
    folder = dest
    for part in path.split("/")[:-1]:
        folder = os.path.join(folder, part)
        if os.path.islink(folder):
            return False
    return True


def _current(path):
    """The digest of the plain file at ``path``; None when there is nothing there; "" for anything else there."""
    if not os.path.lexists(path):
        return None
    if os.path.islink(path) or not os.path.isfile(path):
        return ""
    return file_digest(path)


def _directory(path, created):
    """Make ``path`` and any parent missing, 0700 each; one that exists keeps its mode."""
    if os.path.isdir(path):
        return
    _directory(os.path.dirname(path), created)
    os.mkdir(path, 0o700)
    os.chmod(path, 0o700)
    created.append(path)


def _write(path, data, mode=0o600):
    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".agent-warehouse-")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _save_manifest(manifest, files, created, settings=None):
    _directory(os.path.dirname(manifest), created)
    record = {"format": FORMAT, "files": files, "settings": settings or {}}
    text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    _write(manifest, text.encode("utf-8"))


_MISSING = object()


def _get(data, item):
    """The value of settings ``item`` in ``data``, a key or a part as ``<key>.<part>``; _MISSING where none is."""
    key, _, part = item.partition(".")
    value = data.get(key, _MISSING)
    if not part:
        return value
    return value.get(part, _MISSING) if isinstance(value, dict) else _MISSING


def _set(data, item, value):
    key, _, part = item.partition(".")
    if not part:
        data[key] = value
        return
    if not isinstance(data.get(key), dict):
        data[key] = {}
    data[key][part] = value


def _unset(data, item):
    key, _, part = item.partition(".")
    if not part:
        data.pop(key, None)
        return
    if isinstance(data.get(key), dict):
        data[key].pop(part, None)
        if not data[key]:
            data.pop(key)


def _value_digest(value):
    # surrogatepass: a lone surrogate a JSON escape made is still a value to digest, never the module's end.
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _digest(text.encode("utf-8", "surrogatepass"))


def _history_key(line):
    return (str(line.get("sessionId")), json.dumps(line.get("timestamp"), sort_keys=True))


def carry_history(dest, lines, check):
    """Append to ``dest``/history.jsonl each of ``lines`` it does not hold, by session and time; return how many."""
    path = os.path.join(dest, "history.jsonl")
    if not lines or os.path.islink(path) or (os.path.lexists(path) and not os.path.isfile(path)):
        return 0
    sessions, held = {str(line.get("sessionId")) for line in lines}, set()
    if os.path.isfile(path):
        with open(path, "rb") as handle:
            for raw in handle:
                if b'"sessionId"' not in raw:
                    continue
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(line, dict) and str(line.get("sessionId")) in sessions:
                    held.add(_history_key(line))
    new, seen = [], set()
    for line in lines:
        key = _history_key(line)
        if key in held or key in seen:
            continue
        seen.add(key)
        # Escaped to ASCII, as JSON reads it back the same: a lone surrogate in a prompt could not be UTF-8.
        new.append(json.dumps(line, separators=(",", ":")) + "\n")
    if new and not check:
        _directory(dest, [])
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, "".join(new).encode("utf-8"))
        finally:
            os.close(fd)
    return len(new)


def carry_settings(dest, settings, recorded, check):
    """Set the settings items of ``settings`` (values, items) in ``dest``/settings.json by the rule; return (result,
    the manifest's settings record after it, changed)."""
    result = {"written": [], "left": [], "removed": [], "unreadable": False}
    copies, changed = dict(recorded), False
    values = settings.get("values") or {}
    items = [str(item) for item in settings.get("items") or []]
    path = os.path.join(dest, "settings.json")
    data = {}
    if os.path.lexists(path):
        try:
            if os.path.islink(path):
                raise ValueError("a link")
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except (OSError, ValueError):
            result["unreadable"] = True
            return result, copies, False
    for item in items:
        current = _get(data, item)
        current = None if current is _MISSING else _value_digest(current)
        if item in values:
            digest = _value_digest(values[item])
            action = classify(current, recorded.get(item), digest)
            if action == "left":
                result["left"].append(item)
                continue
            copies[item] = digest
            if action == "write":
                result["written"].append(item)
                _set(data, item, values[item])
                changed = True
        elif item in recorded:
            copies.pop(item)
            if current is not None and current == recorded[item]:
                result["removed"].append(item)
                _unset(data, item)
                changed = True
    if changed and not check:
        _directory(dest, [])
        # As the claude_code role writes it (to_nice_json(indent=2)), so its next configure finds it unchanged.
        _write(path, (json.dumps(data, indent=2, sort_keys=True, separators=(",", ": ")) + "\n").encode("ascii"))
    return result, copies, changed


def _with_line(data, line):
    """``data`` with ``line`` added as its last line, unless one of its lines is ``line`` already."""
    text = data.decode("utf-8", "replace")
    if line in text.splitlines():
        return data
    return data + (b"" if not data or data.endswith(b"\n") else b"\n") + line.encode("utf-8") + b"\n"


def _prune(mirror, keep, check):
    """Remove from ``mirror`` every file that is not in ``keep`` (paths relative to it); return whether any was."""
    removed = False
    for top, dirs, files in os.walk(mirror, topdown=False):
        for name in files:
            path = os.path.join(top, name)
            if os.path.relpath(path, mirror) in keep:
                continue
            removed = True
            if not check:
                os.unlink(path)
        for name in dirs:
            path = os.path.join(top, name)
            if os.path.islink(path):
                removed = True
                if not check:
                    os.unlink(path)
            elif not check and not os.listdir(path):
                os.rmdir(path)
    return removed


def carry(
    dest,
    mirror,
    manifest,
    scopes,
    files=(),
    generated=(),
    appended=None,
    prune=True,
    check=False,
    settings=None,
    history=(),
):
    """Write every item, and with ``settings`` the settings; return the result and whether anything changed."""
    appended = appended or {}
    scopes = [scope for scope in scopes if any(form.fullmatch(scope) for form in SCOPES)]
    recorded, recorded_settings = read_manifest(manifest), read_manifest(manifest, "settings")
    copies, saved = dict(recorded), dict(recorded)
    settings_copies = dict(recorded_settings)
    result = {
        "written": [],
        "unchanged": 0,
        "left": [],
        "removed": [],
        "unreadable": [],
        "not_written": [],
        "refused": False,
        "settings": {"written": [], "left": [], "removed": [], "unreadable": False},
        "history": 0,
    }
    created, changed = [], False
    # just code's own folders on the VM, which only the VM could have made links: then nothing is read or written.
    if os.path.islink(mirror) or os.path.islink(os.path.dirname(manifest)):
        result["refused"] = True
        return result, False
    wanted, planned = {}, set()
    for item in files:
        target = str(item["target"])
        if not scope_of(target, scopes):
            continue
        planned.add(target)
        source = os.path.join(mirror, str(item["source"]))
        if os.path.islink(source) or not os.path.isfile(source) or ".." in str(item["source"]).split("/"):
            result["unreadable"].append(target)
            continue
        with open(source, "rb") as handle:
            wanted[target] = handle.read()
    for item in generated:
        target = str(item["target"])
        if scope_of(target, scopes):
            wanted[target] = str(item["content"]).encode("utf-8")
    for target, line in appended.items():
        if target in wanted:
            wanted[target] = _with_line(wanted[target], str(line))
    try:
        for target in sorted(wanted):
            data, path = wanted[target], os.path.join(dest, target)
            if not _no_link_on_the_way(dest, target):
                result["left"].append(target)
                continue
            current, digest = _current(path), _digest(wanted[target])
            action = "left" if current == "" else classify(current, recorded.get(target), digest)
            if action == "left":
                result["left"].append(target)
                continue
            if action == "unchanged":
                result["unchanged"] += 1
            else:
                if not check:
                    try:
                        _directory(os.path.dirname(path), created)
                        _write(path, data)
                    except OSError:
                        result["not_written"].append(target)
                        continue
                result["written"].append(target)
                changed = True
            # Recorded once written, never before: a record of a write that failed would leave the file there read
            # as changed on the VM for good.
            copies[target] = digest
            if copies != saved and not check:
                _save_manifest(manifest, copies, created, settings_copies)
                saved = dict(copies)
        # Gone here: removed while still as written. A file planned that rsync did not bring is not gone here.
        for target in sorted(recorded):
            if target in wanted or target in planned or not scope_of(target, scopes):
                continue
            path = os.path.join(dest, target)
            if _no_link_on_the_way(dest, target) and _current(path) == recorded[target]:
                if not check:
                    try:
                        os.unlink(path)
                    except OSError:
                        result["not_written"].append(target)
                        continue
                result["removed"].append(target)
                changed = True
            copies.pop(target, None)
        if settings is not None:
            try:
                result["settings"], settings_copies, moved = carry_settings(dest, settings, recorded_settings, check)
            except (OSError, ValueError):
                result["not_written"].append("settings.json")
                moved = False
            changed = moved or changed
        try:
            result["history"] = carry_history(dest, list(history or []), check)
        except (OSError, ValueError):
            result["not_written"].append("history.jsonl")
        changed = changed or result["history"] > 0
    finally:
        if (copies != saved or settings_copies != recorded_settings) and not check:
            _save_manifest(manifest, copies, created, settings_copies)
    if copies != recorded or settings_copies != recorded_settings:
        changed = True
    if prune and os.path.isdir(mirror):
        keep = {os.path.normpath(str(item["source"])) for item in files}
        changed = _prune(mirror, keep, check) or changed
    return result, changed or bool(created)


def main():
    module = AnsibleModule(
        argument_spec={
            "dest": {"type": "path", "required": True},
            "mirror": {"type": "path", "required": True},
            "manifest": {"type": "path", "required": True},
            "scopes": {"type": "list", "elements": "str", "default": []},
            "files": {
                "type": "list",
                "elements": "dict",
                "default": [],
                "options": {"source": {"type": "str", "required": True}, "target": {"type": "str", "required": True}},
            },
            "generated": {
                "type": "list",
                "elements": "dict",
                "default": [],
                "options": {"target": {"type": "str", "required": True}, "content": {"type": "str", "required": True}},
            },
            "appended": {"type": "dict", "default": {}},
            "settings": {"type": "dict"},
            "history": {"type": "list", "elements": "dict", "default": []},
            "prune": {"type": "bool", "default": True},
        },
        supports_check_mode=True,
    )
    params = module.params
    result, changed = carry(
        params["dest"],
        params["mirror"],
        params["manifest"],
        params["scopes"],
        params["files"],
        params["generated"],
        params["appended"],
        params["prune"],
        module.check_mode,
        params["settings"],
        params["history"],
    )
    module.exit_json(changed=changed, **result)


if __name__ == "__main__":
    main()
