#!/usr/bin/python
# Copyright (c) agent-warehouse contributors
# MIT License
"""Bring a VM's checkout to what a repository sync sent it: its branches, its tags and its tree, never over its own."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: git_sync_apply
short_description: Bring a checkout to what a repository sync sent it, never over its own work
version_added: "2.8.0"
description:
  - The one writer inside the checkout of a repository sync. A send has delivered the other machine's branches and
    tags into this checkout's C(refs/agent-warehouse/sent/); this decides, from the checkout as it is now, what each
    becomes, and does it, in this order.
  - Every branch the checkout lacks is created at its commit, tracking the branch of origin the other machine's tracks
    (O(upstreams)) where the checkout has a remote origin, as a clone's has. Then the checkout itself, when O(tree)
    says it may be brought and nothing here stops it - no claude working in it, a clean tree, a HEAD a ref holds, no
    operation under way, nothing git does not track in the way - is moved on its branch, or switched to the branch
    there, by C(git merge --ff-only), C(git reset --keep) or C(git switch), never C(update-ref): moved under it, a tree
    nobody edited would show the move's changes as staged, in reverse. Then every other branch, by
    C(git branch --force), which refuses one checked out, being rebased or being bisected in any worktree. Then the
    tags the checkout lacks.
  - A branch moves only while the checkout added nothing to it - it is where the last sync left it, which the record
    in C(refs/agent-warehouse/sync/heads/) says and only this writes, or behind the commit sent; one that is ahead is
    counted, and one with commits of its own is left - said as its upstream's where every one of them is on its
    upstream there, which this machine has not fetched. A branch whose commit did not arrive is left, and said.
  - Nothing is deleted but what a send delivered and the other machine no longer has, under
    C(refs/agent-warehouse/sent/); a record is never deleted, nor a branch or tag.
options:
  checkout:
    description: The checkout's top level.
    type: str
    required: true
  manifest:
    description: The file the sync keeps what it did in, outside the checkout.
    type: str
    required: true
  source:
    description: The folder on the other machine the sync came from, which the manifest records.
    type: str
    required: true
  branches:
    description: Each branch the other machine has, and its commit.
    type: dict
    default: {}
  tags:
    description: Each tag the other machine has, and its object.
    type: dict
    default: {}
  head:
    description: C(branch), the branch checked out there ("" for a detached HEAD), and C(commit), its commit.
    type: dict
    default: {}
  session_branch:
    description: A branch another step moves in this run (--session's), left alone, with the tree.
    type: str
    default: ""
  tree:
    description: C(bring), whether the other machine's reasons let the tree be brought, and C(why) not.
    type: dict
    default: {}
  upstreams:
    description: Each branch of O(branches) that tracks a branch of origin on the other machine, and that branch.
    type: dict
    default: {}
author:
  - agent-warehouse contributors
"""

EXAMPLES = r"""
- name: Bring the VM's checkout to what was sent
  git_sync_apply:
    checkout: /home/dev/projects/demo
    manifest: /home/dev/projects/.agent-warehouse/repo-sync/demo/manifest.json
    source: /Users/me/code/demo
    branches: {main: 0123456789abcdef0123456789abcdef01234567}
    head: {branch: main, commit: 0123456789abcdef0123456789abcdef01234567}
    tree: {bring: true}
"""

RETURN = r"""
branches:
  description:
    - What became of each branch - C(created), C(moved), C(same), C(ahead) (the VM's is ahead, left), C(own) (it has
      commits of its own, left), C(upstream) (its commits not here are all its upstream's there, left), C(in_use) (a
      worktree there has it, left) and C(missing) (its commit did not arrive).
  returned: always
  type: dict
tags:
  description: C(created), and C(differ), a tag of the name the checkout has for another object, left.
  returned: always
  type: dict
tree:
  description:
    - C(brought), whether the checkout was brought to the other machine's, C(why) not and C(reason), a word for it
      (C(own) where the checkout has changes of its own), and C(head), its HEAD.
  returned: always
  type: dict
"""

import json
import os
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.git_checkouts import COMMIT, claude_processes, overwrites, read
from ansible.module_utils.git_sync import (
    RECORD,
    RECORD_DETACHED,
    SENT,
    branch_name,
    branch_row,
    git,
    operation,
    ref_name,
    refs,
    text,
)

# The git commands this writes with, each by its first words, and nothing else: never push, fetch, rebase, stash,
# clean, gc or reset --hard. update-ref only under refs/agent-warehouse/, and config only a new branch's upstream.
WRITES = (
    ("branch", "--force"),
    ("branch",),
    ("switch", "--detach"),
    ("switch",),
    ("merge", "--ff-only"),
    ("reset", "--keep"),
    ("tag",),
    ("update-ref",),
    ("config", "--local"),
)
MANIFEST_FORMAT = 1


class Apply:
    def __init__(self, module, top):
        self.module = module
        self.top = top
        self.changed = False

    def write(self, *args):
        """One of WRITES, in the checkout: (whether it succeeded, git's last words)."""
        if not any(tuple(args[: len(verb)]) == verb for verb in WRITES):
            self.module.fail_json(msg=f"git {args[0]} is not one of the writes a sync makes.")
        if args[0] == "update-ref" and not any(str(arg).startswith("refs/agent-warehouse/") for arg in args[1:3]):
            self.module.fail_json(msg="update-ref writes only under refs/agent-warehouse/.")
        if args[0] == "config" and not (len(args) == 4 and re.fullmatch(r"branch\..+\.(remote|merge)", str(args[2]))):
            self.module.fail_json(msg="config writes only a branch's upstream.")
        rc, _, err = self.module.run_command(
            ["git", "--no-optional-locks", "-C", self.top, *args],
            environ_update={"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
            expand_user_and_vars=False,
        )
        if rc == 0:
            self.changed = True
        lines = [line for line in (err or "").splitlines() if line.strip()]
        return rc == 0, (lines[-1] if lines else "")

    def commit_of(self, ref):
        return text(self.module, self.top, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}") or ""

    def has(self, commit):
        return git(self.module, self.top, "cat-file", "-e", f"{commit}^{{commit}}")[0] == 0

    def ancestor(self, older, newer):
        return git(self.module, self.top, "merge-base", "--is-ancestor", older, newer)[0] == 0

    def row(self, there, record, sent):
        if not there or there == sent or (record and there == record):
            return branch_row(there, record, sent)
        return branch_row(there, record, sent, self.ancestor(there, sent), self.ancestor(sent, there))

    def upstream_holds(self, branch, there):
        """Whether every commit of ``branch`` at ``there`` is on its upstream there: commits the VM fetched, never its
        own work."""
        upstream = self.commit_of(f"{branch}@{{upstream}}")
        return bool(upstream) and self.ancestor(there, upstream)

    def track(self, branch, upstream):
        """A branch the sync made tracks origin's ``upstream``, as the other machine's does: by its configuration, which
        needs no fetch of it first."""
        if not text(self.module, self.top, "config", "--get", "remote.origin.url"):
            return
        if self.write("config", "--local", f"branch.{branch}.remote", "origin")[0]:
            self.write("config", "--local", f"branch.{branch}.merge", f"refs/heads/{upstream}")

    def record(self, branch, commit, records):
        if records.get(branch) != commit:
            self.write("update-ref", f"{RECORD}{branch}", commit)
            records[branch] = commit


def _manifest(path, content):
    """Write the manifest where it differs, 0600: whether it was written."""
    try:
        with open(path, encoding="utf-8") as handle:
            if json.load(handle) == content:
                return False
    except (OSError, ValueError):
        pass
    temporary = f"{path}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(content, handle, indent=1, sort_keys=True)
    os.replace(temporary, path)
    return True


def _stays(said, reason, why):
    """The checkout left as it is: ``reason``, a word the report decides by, and ``why``, the words it says."""
    said.update(reason=reason, why=why)


def tree_step(apply, params, heads, records, arrived, result):
    """The checkout itself: moved on its branch, or switched to the one there, when nothing stops it."""
    module, top = apply.module, apply.top
    head, tree = params["head"] or {}, params["tree"] or {}
    want_branch, want = str(head.get("branch") or ""), str(head.get("commit") or "")
    now = read(module, top, True, "", claude_processes())
    said = result["tree"]
    if not tree.get("bring"):
        _stays(said, "here", str(tree.get("why") or ""))
        return ""
    if now["busy"]:
        _stays(said, "busy", f"claude is running in it (pid {', '.join(map(str, now['busy']))})")
        return ""
    if not now["clean"]:
        _stays(said, "own", "it has changes of its own")
        return ""
    # A HEAD no branch, remote-tracking branch or tag holds is the VM's own, but for one the sync brought it to.
    detached_here = not now["head_branch"] and now["head"] and now["head"] == apply.commit_of(RECORD_DETACHED)
    if not now["head_kept"] and not detached_here:
        _stays(said, "detached", f"its HEAD is detached at {now['head'][:12]}, which no ref holds")
        return ""
    under_way = operation(module, top)
    if under_way:
        _stays(said, "operation", f"a {under_way} is under way there")
        return ""
    if want_branch and not arrived.get(want_branch):
        _stays(said, "missing", f"{want_branch}'s commit did not arrive")
        return ""
    if not want_branch and not (COMMIT.fullmatch(want) and apply.commit_of(f"{SENT}HEAD") == want):
        _stays(said, "missing", "the commit checked out here did not arrive")
        return ""
    row = apply.row(heads.get(want_branch, ""), records.get(want_branch, ""), want) if want_branch else ""
    if row in ("ahead", "own"):
        if row == "own" and apply.upstream_holds(want_branch, heads.get(want_branch, "")):
            _stays(said, "upstream", f"{want_branch} there has commits of its upstream's this machine has not fetched")
        elif row == "own":
            _stays(said, "commits", f"{want_branch} there has commits of its own")
        else:
            _stays(said, "ahead", f"{want_branch} there is ahead")
        return ""
    on_it = bool(want_branch) and now["head_branch"] == want_branch
    if (on_it and row == "same") or (not want_branch and not now["head_branch"] and now["head"] == want):
        said.update(brought=True, head=want)
        _hold_detached(apply, want_branch, want)
        return want_branch
    in_the_way = overwrites(module, top, want)
    if in_the_way is None or in_the_way:
        _stays(
            said,
            "in_the_way",
            f"moving it would overwrite what git does not track there: {', '.join(in_the_way[:5])}"
            if in_the_way
            else "git could not compare it with the commit",
        )
        return ""
    if not want_branch:
        done, words = apply.write("switch", "--quiet", "--detach", want)
    elif on_it:
        verb = ("reset", "--keep") if row == "move" else ("merge", "--ff-only")
        done, words = apply.write(*verb, "--quiet", want)
    else:
        done, words = True, ""
        if row in ("move", "forward"):
            done, words = apply.write("branch", "--force", want_branch, want)
        if done:
            done, words = apply.write("switch", "--quiet", want_branch)
    if not done:
        _stays(said, "git", f"git said: {words}" if words else "git could not move it")
        return ""
    _hold_detached(apply, want_branch, want)
    said.update(brought=True, head=want)
    return want_branch


def _hold_detached(apply, branch, commit):
    """A checkout brought to a detached HEAD: the commit held by the sync's own ref, which tells the next run it is
    the sync's to move."""
    if not branch and apply.commit_of(RECORD_DETACHED) != commit:
        apply.write("update-ref", RECORD_DETACHED, commit)


def main():
    module = AnsibleModule(
        argument_spec={
            # str, not path: a path option expands ~ and $VAR.
            "checkout": {"type": "str", "required": True},
            "manifest": {"type": "str", "required": True},
            "source": {"type": "str", "required": True},
            "branches": {"type": "dict", "default": {}},
            "tags": {"type": "dict", "default": {}},
            "head": {"type": "dict", "default": {}},
            "session_branch": {"type": "str", "default": ""},
            "tree": {"type": "dict", "default": {}},
            "upstreams": {"type": "dict", "default": {}},
        },
        supports_check_mode=False,
    )
    params = module.params
    top = params["checkout"]
    if text(module, top, "rev-parse", "--show-toplevel") is None:
        module.fail_json(msg=f"{top} is not a git checkout.")
    branches = {
        str(name): str(commit)
        for name, commit in (params["branches"] or {}).items()
        if COMMIT.fullmatch(str(commit)) and branch_name(str(name))
    }
    tags = {
        str(name): str(target)
        for name, target in (params["tags"] or {}).items()
        if COMMIT.fullmatch(str(target)) and ref_name(f"refs/tags/{name}")
    }
    session = str(params["session_branch"] or "")
    apply = Apply(module, top)
    heads, records = refs(module, top, "refs/heads/"), refs(module, top, RECORD)
    sent = refs(module, top, f"{SENT}heads/")
    result = {
        "branches": {key: [] for key in ("created", "moved", "same", "ahead", "own", "upstream", "in_use", "missing")},
        "tags": {"created": [], "differ": []},
        "tree": {"brought": False, "why": "", "reason": "", "head": ""},
    }
    said = result["branches"]
    arrived = {name: sent.get(name) == commit and apply.has(commit) for name, commit in branches.items()}
    upstreams = {
        str(name): str(branch)
        for name, branch in (params["upstreams"] or {}).items()
        if str(name) in branches and branch_name(str(branch))
    }
    # 1. Every branch the checkout lacks, at its commit: git branch fails where one exists. It tracks what the other
    # machine's does on origin, which git pull and git push there then use.
    for name, commit in sorted(branches.items()):
        if name == session:
            continue
        if not arrived[name]:
            said["missing"].append(name)
        elif name not in heads:
            done, _ = apply.write("branch", "--no-track", name, commit)
            if done:
                said["created"].append(name)
                heads[name] = commit
                apply.record(name, commit, records)
                if name in upstreams:
                    apply.track(name, upstreams[name])
    # 2. The checkout: after the creations, so the branch there exists; before the other moves, so that a branch the
    # checkout was on until now is free to move in the same run.
    if session:
        _stays(result["tree"], "session", "--session moves it")
    brought = tree_step(apply, params, heads, records, arrived, result) if not session else ""
    if brought:
        apply.record(brought, branches[brought], records)
        if brought not in said["created"]:
            said["same" if heads.get(brought) == branches[brought] else "moved"].append(brought)
    heads = refs(module, top, "refs/heads/")
    checked_out = text(module, top, "symbolic-ref", "--quiet", "--short", "HEAD") or ""
    # 3. Every other branch, by its row.
    for name, commit in sorted(branches.items()):
        if name in (session, brought) or name in said["created"] or not arrived[name]:
            continue
        row = apply.row(heads.get(name, ""), records.get(name, ""), commit)
        if row == "same":
            said["same"].append(name)
            apply.record(name, commit, records)
        elif row == "own" and apply.upstream_holds(name, heads[name]):
            said["upstream"].append(name)
        elif row in ("ahead", "own"):
            said[row].append(name)
        elif name == checked_out:
            # The checked-out branch moves only with its tree, which stayed.
            said["in_use"].append(name)
        else:
            done, _ = apply.write("branch", "--force", name, commit)
            if done:
                said["moved"].append(name)
                apply.record(name, commit, records)
            else:
                said["in_use"].append(name)
    # 4. The tags the checkout lacks; one it has for another object is left.
    there_tags, sent_tags = refs(module, top, "refs/tags/"), refs(module, top, f"{SENT}tags/")
    for name, target in sorted(tags.items()):
        if sent_tags.get(name) != target:
            continue
        if name not in there_tags:
            done, _ = apply.write("tag", name, target)
            if done:
                result["tags"]["created"].append(name)
        elif there_tags[name] != target:
            result["tags"]["differ"].append(name)
    # What a send delivered that the other machine no longer has, and nothing else.
    for kind, given in (("heads", branches), ("tags", tags)):
        for name in refs(module, top, f"{SENT}{kind}/"):
            if name not in given:
                apply.write("update-ref", "-d", f"{SENT}{kind}/{name}")
    if (params["head"] or {}).get("branch") and apply.commit_of(f"{SENT}HEAD"):
        apply.write("update-ref", "-d", f"{SENT}HEAD")
    content = {
        "format": MANIFEST_FORMAT,
        "source": params["source"],
        "head": text(module, top, "rev-parse", "--verify", "--quiet", "HEAD") or "",
    }
    result["tree"]["head"] = content["head"]
    if _manifest(params["manifest"], content):
        apply.changed = True
    module.exit_json(changed=apply.changed, **result)


if __name__ == "__main__":
    main()
