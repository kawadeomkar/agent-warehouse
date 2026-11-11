# Copyright (c) agent-warehouse contributors
# MIT License
"""Reading git checkouts without changing them: what playbooks/library/git_checkouts_info.py reports and what just
status's probe (status_probe.py) reads of each checkout's work.

Every git command runs as git --no-optional-locks, so none refreshes the index under index.lock, which could collide
with a git, an editor or a Claude working in the same checkout. Each function takes what runs a command - an
AnsibleModule, or the probe's stand-in for one - as ``module``. Standard library only.
"""

from __future__ import annotations

import os
import re

from ansible.module_utils.agent_warehouse import is_claude, own_processes

GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
# What precedes the host in a URL with a scheme: https://<token>@github.com/... names a credential.
USERINFO = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^/@]*@")
# fullmatch: an anchored match takes an ID with a newline at its end.
COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _git(module, path, *args):
    """git's output in ``path``, stripped, or None when it failed."""
    rc, out, _ = module.run_command(
        ["git", "--no-optional-locks", "-C", path, *args], environ_update=GIT_ENV, expand_user_and_vars=False
    )
    return out.strip() if rc == 0 else None


def claude_processes(proc="/proc"):
    """(pid, cwd) of every claude process of this user, from /proc; none where there is no /proc."""
    found = []
    for pid, argv, base in own_processes(proc):
        if not is_claude(argv):
            continue
        try:
            found.append((pid, os.readlink(os.path.join(base, "cwd"))))
        except OSError:
            continue
    return found


def _held(module, path):
    """Whether a branch, remote-tracking branch or tag holds HEAD's commit."""
    held = _git(
        module,
        path,
        "for-each-ref",
        "--count=1",
        "--contains",
        "HEAD",
        "--format=%(refname)",
        "refs/heads",
        "refs/remotes",
        "refs/tags",
    )
    return bool(held)


def overwrites(module, toplevel, target):
    """The paths of ``toplevel`` git does not track that moving its tree from HEAD to ``target`` would replace.

    Each path ``target`` adds where something is already there, and each folder on the way to one that
    is a file or a link here, unless HEAD tracks it and the move removes it: git replaces an ignored
    file without asking, and an untracked one would have made the tree unclean already. None when git
    cannot compare the two.
    """
    # strip() leaves the NULs -z ends each field with, and the first field is a status letter: the output is whole.
    out = _git(module, toplevel, "diff", "--name-status", "-z", "--no-renames", "HEAD", target, "--")
    if out is None:
        return None
    fields = out.split("\0")
    changes = list(zip(fields[0::2], fields[1::2], strict=False))
    added = [name for status, name in changes if status == "A"]
    removed = {name for status, name in changes if status == "D"}
    found = set()
    for name in added:
        parts = name.split("/")
        for depth in range(1, len(parts)):
            folder = "/".join(parts[:depth])
            here = os.path.join(toplevel, folder)
            if folder in removed:
                continue
            if os.path.islink(here) or (os.path.lexists(here) and not os.path.isdir(here)):
                found.add(folder)
                break
        else:
            here = os.path.join(toplevel, name)
            if os.path.islink(here) or (os.path.lexists(here) and not os.path.isdir(here)):
                found.add(name)
            elif os.path.isdir(here) and _untracked_under(here, name, removed):
                found.add(name)
    return sorted(found)


def _untracked_under(path, name, removed):
    """Whether the folder ``path`` (``name`` in the tree) holds a file the move does not remove."""
    for top, dirs, files in os.walk(path):
        for entry in files + [item for item in dirs if os.path.islink(os.path.join(top, item))]:
            relative = os.path.relpath(os.path.join(top, entry), path)
            if f"{name}/{relative}" not in removed:
                return True
    return False


def worktrees(module, toplevel):
    """The checkout's worktrees as git lists them, its main one first: path, branch and whether HEAD is detached."""
    out = _git(module, toplevel, "worktree", "list", "--porcelain")
    trees = []
    for block in (out or "").split("\n\n"):
        lines = block.splitlines()
        if not lines or not lines[0].startswith("worktree ") or "bare" in lines:
            continue
        branch = next(
            (line[len("branch refs/heads/") :] for line in lines if line.startswith("branch refs/heads/")), ""
        )
        trees.append({"path": lines[0][len("worktree ") :], "branch": branch, "detached": "detached" in lines})
    return trees


def work(module, toplevel):
    """What of a checkout's work is on no remote or not committed, as of its last fetch: nothing here fetches."""
    trees = []
    for tree in worktrees(module, toplevel):
        status = _git(
            module, tree["path"], "status", "--porcelain", "--untracked-files=normal", "--ignore-submodules=none"
        )
        own = 0
        if tree["detached"]:
            # Commits HEAD holds that no branch and no remote-tracking branch does: a switch would leave them.
            own = int(
                _git(module, tree["path"], "rev-list", "--count", "HEAD", "--not", "--remotes", "--branches") or 0
            )
        trees.append(
            {
                "path": tree["path"],
                "branch": tree["branch"],
                "dirty": None if status is None else status != "",
                "own_commits": own,
            }
        )
    unpushed = []
    for branch in (
        _git(module, toplevel, "for-each-ref", "--format=%(refname:short)", "refs/heads") or ""
    ).splitlines():
        count = _git(module, toplevel, "rev-list", "--count", f"refs/heads/{branch}", "--not", "--remotes")
        if count and int(count) > 0:
            unpushed.append({"branch": branch, "commits": int(count)})
    stashes = len((_git(module, toplevel, "stash", "list") or "").splitlines())
    return {"worktrees": trees, "unpushed": unpushed, "stashes": stashes}


def _inside(path, top):
    return path == top or path.startswith(top.rstrip("/") + "/")


def read(module, path, status, branch, processes, target="", with_work=False):
    item = {"path": path, "name": os.path.basename(path.rstrip("/")), "exists": os.path.isdir(path)}
    toplevel = _git(module, path, "rev-parse", "--show-toplevel") if item["exists"] else None
    item.update(git=toplevel is not None, toplevel=toplevel or "")
    # The first URL, which is the one git fetches from, as `git remote get-url` gives it but unrewritten.
    origin = (_git(module, path, "config", "--get-all", "remote.origin.url") or "") if toplevel else ""
    # Never with what precedes the host: a token there would reach the run's output and its log.
    item["origin"] = USERINFO.sub(r"\1", origin.splitlines()[0]) if origin else ""
    if status:
        # Whatever the checkout's own config says: status.showUntrackedFiles=no and a submodule's
        # ignore would hide what a move could lose.
        item["clean"] = (
            bool(toplevel)
            and _git(module, path, "status", "--porcelain", "--untracked-files=normal", "--ignore-submodules=none")
            == ""
        )
        item["head_branch"] = (
            (_git(module, path, "symbolic-ref", "--quiet", "--short", "HEAD") or "") if toplevel else ""
        )
        item["head"] = (
            (_git(module, path, "rev-parse", "--verify", "--quiet", "HEAD^{commit}") or "") if toplevel else ""
        )
        item["head_kept"] = bool(item["head_branch"]) or (bool(item["head"]) and _held(module, path))
    if branch:
        item["branch_head"] = upstream = ""
        item["upstream_remote"] = item["upstream_branch"] = item["upstream_head"] = ""
        if toplevel:
            item["branch_head"] = (
                _git(module, path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}") or ""
            )
            item["upstream_remote"] = _git(module, path, "config", "--get", f"branch.{branch}.remote") or ""
            merge = _git(module, path, "config", "--get", f"branch.{branch}.merge") or ""
            item["upstream_branch"] = merge[len("refs/heads/") :] if merge.startswith("refs/heads/") else ""
            if item["upstream_remote"] and item["upstream_branch"]:
                upstream = f"refs/remotes/{item['upstream_remote']}/{item['upstream_branch']}^{{commit}}"
                item["upstream_head"] = _git(module, path, "rev-parse", "--verify", "--quiet", upstream) or ""
    if target:
        found = overwrites(module, toplevel, target) if toplevel else None
        if found is None:
            module.fail_json(msg=f"git could not compare HEAD with {target} in {path}.")
        item["overwrites"] = found
    if with_work and toplevel:
        item["work"] = work(module, toplevel)
    if processes is not None:
        top = os.path.realpath(toplevel) if toplevel else ""
        item["busy"] = sorted(pid for pid, cwd in processes if top and _inside(cwd, top))
    return item
