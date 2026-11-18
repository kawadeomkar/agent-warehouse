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
    holds (none, where it was emptied). Nothing of a file's content is returned.
  - Plain files only, never through a link, no name that starts with a dot or holds a control character, at
    most 4 folders deep. A memory folder past its bound is named in C(memory_over) and its files left out.
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
memory:
  description: Each folder under C(projects/) that has a C(memory/) folder, to its files, sorted by path.
  returned: always
  type: dict
memory_over:
  description: The memory folders left out whole, past the bound of their files' number or bytes.
  returned: always
  type: list
  elements: str
repo_paths:
  description: githubRepoPaths, owner/repo to the local paths Claude Code has seen it in; {} without O(repo_paths).
  returned: always
  type: dict
"""

import os
import re
from stat import S_ISDIR, S_ISREG

from ansible.module_utils.agent_warehouse import file_digest, folders, github_repo_paths
from ansible.module_utils.basic import AnsibleModule

# A name just code carries: no dot first (no .DS_Store, no editor's swap file), no control character, no "/".
NAME = re.compile(r"[^.\x00-\x1f\x7f/][^\x00-\x1f\x7f/]{0,254}")
# How deep below a carried folder a file may be.
DEPTH = 4
# A memory folder carried whole or not at all: Claude Code loads 200 lines or 25 KB of its MEMORY.md and reads the
# rest on demand, so a folder this large is no auto memory of Claude Code's own making.
MEMORY_FILES = 1000
MEMORY_BYTES = 16 * 1024 * 1024


def plain_files(root, depth=DEPTH):
    """(path relative to ``root``, size, SHA-256) of each plain file under ``root``, sorted by path.

    No link is followed, a name ``NAME`` refuses is passed over with everything below it, and nothing deeper than
    ``depth`` folders is listed. A file that goes as it is read is passed over too.
    """
    found = []

    def walk(folder, rel, level):
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
        except OSError:
            return
        for entry in entries:
            if not NAME.fullmatch(entry.name):
                continue
            path = f"{rel}/{entry.name}" if rel else entry.name
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError:
                continue
            if S_ISDIR(mode) and level < depth:
                walk(entry.path, path, level + 1)
            elif S_ISREG(mode):
                try:
                    size, digest = os.lstat(entry.path).st_size, file_digest(entry.path)
                except OSError:
                    continue
                found.append({"path": path, "size": size, "digest": digest})

    walk(root, "", 0)
    return found


def memory_folders(path):
    """({folder: its memory/'s files}, [folders past the bound]) of the folders under ``path``/projects that have a
    memory/ folder: one emptied here is listed with none, so that the notes it held are removed on the VM too."""
    found, over = {}, []
    for folder in folders(os.path.join(path, "projects")):
        memory = os.path.join(folder.path, "memory")
        if not os.path.isdir(memory) or os.path.islink(memory):
            continue
        files = plain_files(memory)
        if len(files) > MEMORY_FILES or sum(item["size"] for item in files) > MEMORY_BYTES:
            over.append(folder.name)
            continue
        found[folder.name] = files
    return found, over


def main():
    module = AnsibleModule(
        argument_spec={
            "path": {"type": "path", "required": True},
            "memory": {"type": "bool", "default": False},
            "repo_paths": {"type": "bool", "default": False},
        },
        supports_check_mode=True,
    )
    params = module.params
    memory, over = memory_folders(params["path"]) if params["memory"] else ({}, [])
    module.exit_json(
        changed=False,
        memory=memory,
        memory_over=over,
        repo_paths=github_repo_paths(params["path"]) if params["repo_paths"] else {},
    )


if __name__ == "__main__":
    main()
