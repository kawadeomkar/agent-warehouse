#!/usr/bin/python
# Copyright (c) agent-warehouse contributors
# MIT License
"""Read git checkouts - their origin, and on request their state - without taking git's locks."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: git_checkouts_info
short_description: Read git checkouts without changing them
version_added: "1.0.0"
description:
  - Reports, for each directory asked about, whether it is in a git checkout, the checkout's top level and its
    C(origin) URL as configured; with O(status), whether its tree is clean, what HEAD is and whether a ref
    holds its commit; with O(branch), that branch's commit and its upstream's; with O(target), what in the
    tree a move to that commit would overwrite; with O(busy), the claude processes working inside it; with
    O(work), what of its work is on no remote or not committed - its worktrees, each dirty or not and the commits
    only a detached HEAD holds, its branches' commits on no remote, and its stashes - as of its last fetch.
  - Every git command runs as C(git --no-optional-locks), so none refreshes the index under C(index.lock), which
    could collide with a git, an editor or a Claude working in the same checkout. Nothing is changed.
  - Paths and the branch are taken as written: no C(~) or C($VAR) in them is expanded, since a directory a
    transcript names, or a branch, can hold either.
options:
  paths:
    description: The directories to report on, each as it is; one that does not exist is reported as such.
    type: list
    elements: str
    default: []
  under:
    description: Report on every git checkout directly inside this directory instead of on O(paths).
    type: str
  status:
    description: Also report whether each tree is clean, the branch HEAD is on and HEAD's commit.
    type: bool
    default: false
  branch:
    description:
      - Also report this branch's commit, the remote and branch it tracks, and the commit its upstream is at.
      - Refused unless C(git check-ref-format --branch) accepts it and it does not start with "-".
    type: str
  target:
    description:
      - Also report the paths a move of the tree from HEAD to this commit, a full hex ID, would overwrite or
        remove that git does not track - an ignored file among them, which C(git status) does not show and
        git overwrites without asking.
    type: str
  busy:
    description: Also report the pids of this user's claude processes whose directory is inside each checkout (Linux).
    type: bool
    default: false
  work:
    description:
      - Also report, for each checkout, its worktrees (C(git worktree list), wherever they are), whether each holds
        changes not committed, untracked files included, and the commits only a detached HEAD holds; each local
        branch's commits on no remote; and how many stashes it keeps. Nothing is fetched first, so "no remote" is
        as of the checkout's last fetch.
    type: bool
    default: false
author:
  - agent-warehouse contributors
"""

EXAMPLES = r"""
- name: Read the origin of every project on the VM
  git_checkouts_info:
    under: /home/dev/projects
  register: found
"""

RETURN = r"""
checkouts:
  description: One item per directory, in O(paths)' order or O(under)'s checkouts by name.
  returned: always
  type: list
  elements: dict
  contains:
    path:
      description: The directory as asked about, or found under O(under).
      type: str
    name:
      description: Its last component.
      type: str
    exists:
      description: Whether it is a directory.
      type: bool
    git:
      description: Whether it is inside a git checkout.
      type: bool
    toplevel:
      description: The checkout's top level, or "".
      type: str
    origin:
      description:
        - The URL of its C(origin) remote as configured, or "": never rewritten by a C(url.<base>.insteadOf),
          which names where git connects to, not the repository.
        - Without what precedes the host in a URL with a scheme, which can be a token.
      type: str
    clean:
      description: With O(status), whether C(git status --porcelain) prints nothing.
      type: bool
    head_branch:
      description: With O(status), the branch HEAD is on, or "" when it is detached.
      type: str
    head:
      description: With O(status), HEAD's commit, or "".
      type: str
    head_kept:
      description:
        - With O(status), whether HEAD is on a branch or a branch, remote-tracking branch or tag holds its commit:
          false for a detached HEAD whose commits a switch would leave to garbage collection.
      type: bool
    branch_head:
      description: With O(branch), that branch's commit here, or "" when there is no such branch.
      type: str
    upstream_remote:
      description: With O(branch), the remote it tracks, or "".
      type: str
    upstream_branch:
      description: With O(branch), the branch on that remote it tracks, or "".
      type: str
    upstream_head:
      description: With O(branch), the commit its upstream is at here, or "".
      type: str
    overwrites:
      description: With O(target), the paths git does not track that the move would overwrite or remove, sorted.
      type: list
      elements: str
    busy:
      description: With O(busy), the pids of this user's claude processes working inside it.
      type: list
      elements: int
    work:
      description:
        - With O(work), C(worktrees) - each C(path), C(branch) ("" when detached), C(dirty) (null when it could not be
          read) and C(own_commits), the commits only its detached HEAD holds - C(unpushed), each local branch with
          commits on no remote and how many, and C(stashes).
      type: dict
"""

import os

from ansible.module_utils.agent_warehouse import folders
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.git_checkouts import COMMIT, GIT_ENV, claude_processes, read


def main():
    module = AnsibleModule(
        argument_spec={
            # str, not path: a path option expands ~ and $VAR, and a directory a transcript names is as written.
            "paths": {"type": "list", "elements": "str", "default": []},
            "under": {"type": "str"},
            "status": {"type": "bool", "default": False},
            "branch": {"type": "str"},
            "target": {"type": "str"},
            "busy": {"type": "bool", "default": False},
            "work": {"type": "bool", "default": False},
        },
        mutually_exclusive=[["paths", "under"]],
        supports_check_mode=True,
    )
    params = module.params
    branch = params["branch"] or ""
    if branch:
        rc, _, _ = module.run_command(
            ["git", "check-ref-format", "--branch", branch], environ_update=GIT_ENV, expand_user_and_vars=False
        )
        if rc != 0 or branch.startswith("-"):
            module.fail_json(msg=f"{branch!r} is not a branch name git accepts.")
    target = params["target"] or ""
    if target and not COMMIT.fullmatch(target):
        module.fail_json(msg=f"{target!r} is not a full commit ID.")
    processes = claude_processes() if params["busy"] else None
    paths = params["paths"]
    if params["under"]:
        paths = [folder.path for folder in folders(params["under"])]
    found = [read(module, path, params["status"], branch, processes, target, params["work"]) for path in paths]
    if params["under"]:
        # A checkout's own top level, compared resolved: git names it by its real path.
        found = [
            item
            for item in found
            if item["git"] and os.path.realpath(item["toplevel"]) == os.path.realpath(item["path"])
        ]
    module.exit_json(changed=False, checkouts=found)


if __name__ == "__main__":
    main()
