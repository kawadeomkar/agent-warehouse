#!/usr/bin/python
# Copyright (c) agent-warehouse contributors
# MIT License
"""Read the parts of a Claude Code configuration directory just code carries to a VM beside the sessions."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: claude_config_info
short_description: Read what of a Claude Code configuration directory just code carries beside the sessions
version_added: "2.8.0"
description:
  - Lists, in a Claude Code configuration directory, what just code carries to a VM beside the sessions
    (docs/architecture/custom-code.md §7.6), by name, size and SHA-256: each auto memory folder, with the files it
    holds (none, where it was emptied), and the global instructions with their rules. Nothing of a file's content
    is returned but the values of the settings asked for, and the import paths C(CLAUDE.md) names.
  - Plain files only, never through a link, no name that starts with a dot or holds a control character, at
    most 4 folders deep. A memory folder past its bound is named in C(memory_over) and its files left out; C(rules/)
    past 1000 files or 16 MB is left out whole, and C(rules_over) says so.
  - A link where a file or folder would be carried, and a file or folder that could not be read, is named in
    C(unread), by its path under O(path): what is there is not carried, and never taken for gone. Where O(path) is
    not there at all, C(missing) says so and nothing is read.
  - Read-only. just code runs it on this machine (playbooks/tasks/claude_config_read.yml).
options:
  path:
    description: Claude Code's configuration directory, C(~/.claude) by default.
    type: path
    required: true
  memory:
    description: Return the auto memory folders under C(projects/) that hold a file.
    type: bool
    default: false
  instructions:
    description: Return C(CLAUDE.md) and the files under C(rules/), and the files C(CLAUDE.md) imports by C(@path).
    type: bool
    default: false
  settings:
    description:
      - The settings of C(settings.json) to return the values of, C(keys) at its top level and C(parts), a key to
        its parts read one by one. Of every other setting only its name is returned. Run under no_log.
    type: dict
  extensions:
    description:
      - Return the files of the user-level extensions - C(skills/) but the account's C(skills/synced/), C(agents/),
        C(commands/), C(output-styles/), C(workflows/), C(themes/) and C(keybindings.json) - by path, size and
        digest; all of them left out, and C(extensions_over), past 2000 files or 64 MB.
    type: bool
    default: false
  history:
    description:
      - Return the lines of C(history.jsonl) of these sessions, each as the object it holds, at most 8 MB of them:
        the prompts typed in them, which their transcripts hold too. Run under no_log.
    type: list
    elements: str
    default: []
  plugins:
    description:
      - Return the user-scope plugins enabled in O(path), from C(plugins/installed_plugins.json) and C(settings.json)'s
        C(enabledPlugins), and each marketplace's GitHub repository from C(plugins/known_marketplaces.json).
    type: bool
    default: false
  plugins_there:
    description:
      - Ask the first of O(claude) there is for the plugins installed for the user and the marketplaces it knows
        (C(claude plugin list --json), C(claude plugin marketplace list --json)). Run on the VM.
    type: bool
    default: false
  claude:
    description: The Claude Code executables O(plugins_there) may ask, the first one there is; never C(claude) on PATH.
    type: list
    elements: path
    default: []
  repo_paths:
    description:
      - Also return C(githubRepoPaths) from Claude Code's C(.claude.json), the one in O(path) or the one beside
        it, which Claude Code keeps in the home directory. Nothing else of that file is returned.
    type: bool
    default: false
author:
  - agent-warehouse contributors
"""

EXAMPLES = r"""
- name: Read this machine's auto memory
  claude_config_info:
    path: ~/.claude
    memory: true
    repo_paths: true
  register: found
"""

RETURN = r"""
missing:
  description: Whether O(path) is not there at all, so that nothing of it was read.
  returned: always
  type: bool
unread:
  description:
    - The paths under O(path) of each link where a file or folder would be carried and each file or folder that
      could not be read, sorted. Never carried, and never taken for gone.
  returned: always
  type: list
  elements: str
rules_over:
  description: Whether C(rules/) was left out whole, past 1000 files or 16 MB.
  returned: always
  type: bool
memory:
  description: Each folder under C(projects/) that has a C(memory/) folder, to its files, sorted by path.
  returned: always
  type: dict
memory_over:
  description: The memory folders left out whole, past the bound of their files' number or bytes.
  returned: always
  type: list
  elements: str
instructions:
  description: C(CLAUDE.md) and each file under C(rules/), by path, size and digest; [] without O(instructions).
  returned: always
  type: list
  elements: dict
imports:
  description: The paths C(CLAUDE.md) imports by C(@path), at most 20; just code carries none of them.
  returned: always
  type: list
  elements: str
settings:
  description:
    - With O(settings), C(present), the names at the top level of C(settings.json); C(values), each setting asked for
      that is there, by its name (a part as C(<key>.<part>)), to its value; C(parts_left), the parts of the keys
      read part by part that were not asked for; and C(unreadable) where the file is not a JSON object. {} without.
  returned: always
  type: dict
extensions:
  description: With O(extensions), each file of the user-level extensions; [] without, or past the bound.
  returned: always
  type: list
  elements: dict
extensions_over:
  description: Whether the user-level extensions were left out whole, past 2000 files or 64 MB.
  returned: always
  type: bool
history:
  description: With O(history), the lines of those sessions, oldest first; [] without.
  returned: always
  type: list
  elements: dict
history_over:
  description: Whether lines of those sessions were left out, past the 8 MB a copy takes.
  returned: always
  type: bool
plugins:
  description:
    - With O(plugins), C(enabled), the IDs of the user-scope plugins enabled here, and C(marketplaces), each one's
      GitHub repository or "". With O(plugins_there), C(readable), C(claude) (the executable asked), C(installed)
      (the user-scope plugins there) and C(marketplaces) (the names it knows). {} without either.
  returned: always
  type: dict
repo_paths:
  description: githubRepoPaths, owner/repo to the local paths Claude Code has seen it in; {} without O(repo_paths).
  returned: always
  type: dict
"""

import json
import os
import re
import subprocess
from stat import S_ISDIR, S_ISLNK, S_ISREG

from ansible.module_utils.agent_warehouse import file_digest, folders, github_repo_paths
from ansible.module_utils.basic import AnsibleModule

# A name just code carries: no dot first (no .DS_Store, no editor's swap file), no control character, no "/".
NAME = re.compile(r"[^.\x00-\x1f\x7f/][^\x00-\x1f\x7f/]{0,254}")
# An import in CLAUDE.md, @ and a path, as Claude Code reads them: named in what just code says, never carried.
IMPORT = re.compile(r"(?<![\w`@])@(~?[\w./-]{1,200})")
# How deep below a carried folder a file may be.
DEPTH = 4
# A memory folder carried whole or not at all: Claude Code loads 200 lines or 25 KB of its MEMORY.md and reads the
# rest on demand, so a folder this large is no auto memory of Claude Code's own making.
MEMORY_FILES = 1000
MEMORY_BYTES = 16 * 1024 * 1024


def plain_files(root, depth=DEPTH, unread=None):
    """(path relative to ``root``, size, SHA-256) of each plain file under ``root``, sorted by path.

    No link is followed, a name ``NAME`` refuses is passed over with everything below it, and nothing deeper than
    ``depth`` folders is listed. A file that goes as it is read is passed over too. Each link, and each folder or file
    that could not be read, is added to ``unread`` by its path relative to ``root`` ("" for ``root`` itself): what is
    there is not carried, and never taken for gone.
    """
    found, unread = [], unread if unread is not None else []

    def walk(folder, rel, level):
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
        except FileNotFoundError:
            return
        except OSError:
            unread.append(rel)
            return
        for entry in entries:
            if not NAME.fullmatch(entry.name):
                continue
            path = f"{rel}/{entry.name}" if rel else entry.name
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except FileNotFoundError:
                continue
            except OSError:
                unread.append(path)
                continue
            if S_ISLNK(mode):
                unread.append(path)
            elif S_ISDIR(mode) and level < depth:
                walk(entry.path, path, level + 1)
            elif S_ISREG(mode):
                try:
                    size, digest = os.lstat(entry.path).st_size, file_digest(entry.path)
                except FileNotFoundError:
                    continue
                except OSError:
                    unread.append(path)
                    continue
                found.append({"path": path, "size": size, "digest": digest})

    walk(root, "", 0)
    return found


def _under(prefix, paths):
    """``paths``, relative to a folder, as paths under ``prefix``: "" is the folder itself."""
    return [f"{prefix}/{path}" if path else prefix for path in paths]


def one_file(path, name, unread):
    """[(``name``, size, SHA-256)] of the plain file ``path``; [] where there is none, with ``name`` added to ``unread``
    where it is a link or could not be read."""
    if os.path.islink(path):
        unread.append(name)
        return []
    if not os.path.isfile(path):
        return []
    try:
        return [{"path": name, "size": os.lstat(path).st_size, "digest": file_digest(path)}]
    except FileNotFoundError:
        return []
    except OSError:
        unread.append(name)
        return []


def memory_folders(path, unread=None):
    """({folder: its memory/'s files}, [folders past the bound]) of the folders under ``path``/projects that have a
    memory/ folder: one emptied here is listed with none, so that the notes it held are removed on the VM too. What
    of one could not be read goes into ``unread`` (plain_files), by its path under ``path``; a memory/ folder that is a
    link or could not be read at all is left out whole, as one past the bound is."""
    found, over, unread = {}, [], unread if unread is not None else []
    for folder in folders(os.path.join(path, "projects")):
        memory = os.path.join(folder.path, "memory")
        if os.path.islink(memory):
            unread.append(f"projects/{folder.name}/memory")
            continue
        if not os.path.isdir(memory):
            continue
        missed = []
        files = plain_files(memory, unread=missed)
        unread += _under(f"projects/{folder.name}/memory", missed)
        if "" in missed:
            continue
        if len(files) > MEMORY_FILES or sum(item["size"] for item in files) > MEMORY_BYTES:
            over.append(folder.name)
            continue
        found[folder.name] = files
    return found, over


# The most of rules/ one copy takes: carried whole or not at all.
RULES_FILES = 1000
RULES_BYTES = 16 * 1024 * 1024


def instructions(path, unread=None):
    """(CLAUDE.md and the files under rules/, [the paths CLAUDE.md imports by @path], whether rules/ is past its
    bound and left out whole). What could not be read goes into ``unread`` (plain_files)."""
    unread = unread if unread is not None else []
    claude_md = os.path.join(path, "CLAUDE.md")
    found, imports = one_file(claude_md, "CLAUDE.md", unread), []
    if found:
        try:
            with open(claude_md, encoding="utf-8", errors="replace") as handle:
                imports = list(dict.fromkeys(IMPORT.findall(handle.read())))[:20]
        except OSError:
            imports = []
    rules, over, missed = os.path.join(path, "rules"), False, []
    if os.path.islink(rules):
        unread.append("rules")
    elif os.path.isdir(rules):
        files = plain_files(rules, unread=missed)
        unread += _under("rules", missed)
        if len(files) > RULES_FILES or sum(item["size"] for item in files) > RULES_BYTES:
            over = True
        else:
            found += [dict(item, path=f"rules/{item['path']}") for item in files]
    return found, imports, over


def settings(path, asked):
    """What of ``path``/settings.json ``asked`` names (keys, parts): the names at its top level, the values asked
    for, the parts of a key read part by part that were not asked for."""
    target = os.path.join(path, "settings.json")
    if not os.path.lexists(target):
        return {"present": [], "values": {}, "parts_left": [], "unreadable": False}
    try:
        if os.path.islink(target):
            raise ValueError("a link")
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (OSError, ValueError):
        return {"present": [], "values": {}, "parts_left": [], "unreadable": True}
    return settings_from(data, asked)


def settings_from(data, asked):
    """What of settings ``data``, a JSON object, ``asked`` names: as settings() returns it."""
    keys, parts = [str(key) for key in asked.get("keys") or []], asked.get("parts") or {}
    values, left = {}, []
    for key in keys:
        if key in data and key not in parts:
            values[key] = data[key]
    for key, wanted in parts.items():
        found = data.get(key)
        if not isinstance(found, dict):
            continue
        for part, value in found.items():
            if part in wanted:
                values[f"{key}.{part}"] = value
            else:
                left.append(f"{key}.{part}")
    return {
        "present": sorted(str(key) for key in data),
        "values": values,
        "parts_left": sorted(left),
        "unreadable": False,
    }


# The user-level extensions' folders, and the most of them a copy takes: carried whole or not at all.
EXTENSIONS = ("skills", "agents", "commands", "output-styles", "workflows", "themes")
EXTENSION_FILES = 2000
EXTENSION_BYTES = 64 * 1024 * 1024


def extensions(path, unread=None):
    """([the files of the user-level extensions], whether they are past the bound and left out whole). What could not
    be read goes into ``unread`` (plain_files)."""
    found, unread = [], unread if unread is not None else []
    for name in EXTENSIONS:
        folder = os.path.join(path, name)
        if os.path.islink(folder):
            unread.append(name)
            continue
        if not os.path.isdir(folder):
            continue
        missed = []
        for item in plain_files(folder, unread=missed):
            # The account's skills, which Claude Code downloads at each sign-in, there as here.
            if name == "skills" and item["path"].split("/")[0] == "synced":
                continue
            found.append(dict(item, path=f"{name}/{item['path']}"))
        unread += _under(name, [rel for rel in missed if not (name == "skills" and rel.split("/")[0] == "synced")])
    found += one_file(os.path.join(path, "keybindings.json"), "keybindings.json", unread)
    if len(found) > EXTENSION_FILES or sum(item["size"] for item in found) > EXTENSION_BYTES:
        return [], True
    return found, False


# The most one copy takes of the prompt history, in bytes of its lines.
HISTORY_BYTES = 8 * 1024 * 1024


def history(path, sessions):
    """([the lines of ``path``/history.jsonl of ``sessions``, as objects], whether some were left past the bound)."""
    wanted, found, size, over = {str(session) for session in sessions or []}, [], 0, False
    target = os.path.join(path, "history.jsonl")
    if not wanted or os.path.islink(target) or not os.path.isfile(target):
        return found, over
    try:
        with open(target, "rb") as handle:
            for raw in handle:
                if not raw.endswith(b"\n") or b'"sessionId"' not in raw:
                    continue
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(line, dict) or line.get("sessionId") not in wanted:
                    continue
                if size + len(raw) > HISTORY_BYTES:
                    over = True
                    continue
                size += len(raw)
                found.append(line)
    except OSError:
        return [], False
    return found, over


def _json_file(path):
    try:
        if os.path.islink(path):
            return None
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def plugins_here(path):
    """The user-scope plugins enabled in ``path``, and each marketplace's GitHub repository ("" for another source)."""
    installed = _json_file(os.path.join(path, "plugins", "installed_plugins.json"))
    installed = installed.get("plugins") if isinstance(installed, dict) else None
    settings_found = _json_file(os.path.join(path, "settings.json"))
    enabled = settings_found.get("enabledPlugins") if isinstance(settings_found, dict) else None
    enabled = enabled if isinstance(enabled, dict) else {}
    users = []
    for plugin, entries in (installed or {}).items() if isinstance(installed, dict) else []:
        entries = entries if isinstance(entries, list) else []
        scopes = [entry.get("scope") for entry in entries if isinstance(entry, dict)]
        if "user" in scopes and enabled.get(plugin) is True:
            users.append(str(plugin))
    known = _json_file(os.path.join(path, "plugins", "known_marketplaces.json"))
    markets = {}
    for name, entry in (known or {}).items() if isinstance(known, dict) else []:
        source = entry.get("source") if isinstance(entry, dict) else None
        github = isinstance(source, dict) and source.get("source") == "github" and isinstance(source.get("repo"), str)
        markets[str(name)] = source["repo"] if github else ""
    return {"enabled": sorted(users), "marketplaces": markets}


def plugins_there(candidates):
    """What the first Claude Code of ``candidates`` there is says it has: the user-scope plugins, the marketplaces."""
    found = next((path for path in candidates if os.path.isfile(path) and os.access(path, os.X_OK)), None)
    result = {"readable": False, "claude": found or "", "installed": [], "marketplaces": []}
    if not found:
        return result
    answers = []
    for argv in ([found, "plugin", "list", "--json"], [found, "plugin", "marketplace", "list", "--json"]):
        try:
            answer = subprocess.run(
                argv, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, check=False
            )
            answers.append(json.loads(answer.stdout) if answer.returncode == 0 else None)
        except (OSError, subprocess.SubprocessError, ValueError):
            answers.append(None)
    plugins, markets = answers
    if not isinstance(plugins, list) or not isinstance(markets, list):
        return result
    result["installed"] = sorted(
        str(item["id"]) for item in plugins if isinstance(item, dict) and item.get("scope") == "user" and "id" in item
    )
    result["marketplaces"] = sorted(str(item["name"]) for item in markets if isinstance(item, dict) and "name" in item)
    result["readable"] = True
    return result


def main():
    module = AnsibleModule(
        argument_spec={
            "path": {"type": "path", "required": True},
            "memory": {"type": "bool", "default": False},
            "instructions": {"type": "bool", "default": False},
            "settings": {"type": "dict"},
            "extensions": {"type": "bool", "default": False},
            "history": {"type": "list", "elements": "str", "default": []},
            "plugins": {"type": "bool", "default": False},
            "plugins_there": {"type": "bool", "default": False},
            "claude": {"type": "list", "elements": "path", "default": []},
            "repo_paths": {"type": "bool", "default": False},
        },
        supports_check_mode=True,
    )
    params = module.params
    # A directory that is not there at all is no Claude Code emptied: nothing of it is read, and nothing carried
    # before reads as gone. The VM's own reading (plugins_there) asks its claude, whatever its directory.
    missing, unread = not params["plugins_there"] and not os.path.isdir(params["path"]), []
    reading = not missing
    memory, over = memory_folders(params["path"], unread) if params["memory"] and reading else ({}, [])
    found, imports, over_rules = (
        instructions(params["path"], unread) if params["instructions"] and reading else ([], [], False)
    )
    lines, over_history = history(params["path"], params["history"]) if reading else ([], False)
    extended, over_extensions = extensions(params["path"], unread) if params["extensions"] and reading else ([], False)
    asked = params["settings"]
    module.exit_json(
        changed=False,
        missing=missing,
        unread=sorted(set(unread)),
        memory=memory,
        memory_over=over,
        instructions=found,
        imports=imports,
        rules_over=over_rules,
        settings=(
            {"present": [], "values": {}, "parts_left": [], "unreadable": True}
            if asked and missing
            else settings(params["path"], asked)
            if asked
            else {}
        ),
        history=lines,
        history_over=over_history,
        extensions=extended,
        extensions_over=over_extensions,
        plugins=(
            plugins_there(params["claude"])
            if params["plugins_there"]
            else plugins_here(params["path"])
            if params["plugins"]
            else {}
        ),
        repo_paths=github_repo_paths(params["path"]) if params["repo_paths"] else {},
    )


if __name__ == "__main__":
    main()
