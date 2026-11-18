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
  - Then the files git ignores that the sync carried, from O(mirror), where rsync brought them: each only once its
    copy there is what was sent - a plain file of the size and modification time O(stamps) gives, as rsync -a keeps
    them, so that a copy an earlier run left after an rsync that failed is never taken for this run's - and written
    where the checkout has no file at its path, or one still holding the bytes the last sync wrote, as O(manifest)
    records them; never at a path git tracks there, in HEAD or the index, never through a link; one the VM changed or
    made is left. A copy is removed only when the other machine says the file is gone (O(gone)) from the same folder
    it came from, and the copy is still the sync's; one only left out of this run stays, and stays the sync's; one the
    other machine no longer ignores (O(unignored)) stays, and is no longer the sync's.
    The manifest is written before any file, with what each path held and is about to hold, and again after, so that
    after a run that stopped part way a file matching either is still the sync's. Then a block of the checkout's
    C(.git/info/exclude), between two lines of the sync's, names each such file git there would not ignore without it,
    so that C(git status) says there what it says on the other machine; where git cannot say, the file is left as it
    is.
  - Nothing is deleted but what a send delivered and the other machine no longer has, under
    C(refs/agent-warehouse/sent/), the sync's own copies above, and what the mirror holds that was not sent; a record
    is never deleted, nor a branch or tag.
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
  mirror:
    description: The folder rsync brought the files into, outside the checkout.
    type: str
    default: ""
  ignored:
    description: The files git ignores that the sync carried, relative to the checkout, each in O(mirror).
    type: list
    elements: str
    default: []
  stamps:
    description: Each file sent into O(mirror) - the changes' and the ignored ones - and its C([size, mtime]) there.
    type: dict
    default: {}
  gone:
    description: The ignored files the sync carried before that the other machine no longer has.
    type: list
    elements: str
    default: []
  unignored:
    description: The ignored files the sync carried before that the other machine has and no longer ignores.
    type: list
    elements: str
    default: []
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
files:
  description:
    - The ignored files - C(written), C(unchanged), C(left) (the VM's own there, or a path git tracks there),
      C(missing) (not in the mirror as sent), C(removed) (the sync's own copies of files the other machine no longer
      has) and C(failed) (a write or a removal the VM refused).
  returned: always
  type: dict
excluded:
  description: The paths the sync's block of C(.git/info/exclude) names.
  returned: always
  type: list
  elements: str
error:
  description: What stopped the apply part way, a file the VM would not let it write, or "".
  returned: always
  type: str
"""

import hashlib
import json
import os
import re
import stat

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.git_checkouts import COMMIT, claude_processes, overwrites, read
from ansible.module_utils.git_sync import (
    EXCLUDE_BEGIN,
    EXCLUDE_END,
    RECORD,
    RECORD_DETACHED,
    SENT,
    branch_name,
    branch_row,
    exclude_text,
    file_verdict,
    git,
    names,
    operation,
    plain_path,
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


def _read_manifest(path):
    try:
        with open(path, encoding="utf-8") as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) and found.get("format") == MANIFEST_FORMAT else {}


def _within(top, path):
    """Whether every folder on the way from ``top`` to ``path`` (relative) is a real folder, never a link: what is
    there, a sync's write reaches through nothing."""
    folder = top
    for part in path.split("/")[:-1]:
        folder = os.path.join(folder, part)
        try:
            mode = os.lstat(folder).st_mode
        except FileNotFoundError:
            return True
        if not stat.S_ISDIR(mode):
            return False
    return True


def _digest(path):
    """The SHA-256 of the plain file at ``path``; "" when nothing is there; None for anything else."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return ""
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return None
        digest = hashlib.sha256()
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy(source, top, path):
    """The plain file ``source`` written at ``path`` in ``top``: its folders made, written beside and renamed into
    place, its mode's permission bits kept but never set-id."""
    target = os.path.join(top, path)
    folder = os.path.dirname(target)
    os.makedirs(folder, exist_ok=True)
    mode = stat.S_IMODE(os.lstat(source).st_mode) & 0o777
    temporary = os.path.join(folder, f".{os.path.basename(target)}.agent-warehouse-sync")
    with open(source, "rb") as reader:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as writer:
            for block in iter(lambda: reader.read(1 << 20), b""):
                writer.write(block)
    os.chmod(temporary, mode)
    os.replace(temporary, target)


def _tracked(module, top):
    """Every path git tracks in ``top``, in its index or its HEAD: listed whole, so that no list of paths is an
    argument."""
    found = set()
    for args in (("ls-files", "-z", "--cached"), ("ls-tree", "-z", "-r", "--name-only", "HEAD")):
        rc, out = git(module, top, *args)
        if rc == 0:
            found |= {name.decode("utf-8", "replace") for name in names(out)}
    return found


def _ours(entries, path):
    """The digests ``entries`` (the manifest's tree or ignored) record for ``path``: what the sync wrote there."""
    entry = entries.get(path) or {}
    return {entry.get("now"), entry.get("was")} - {None}


def _arrived(params, path):
    """The digest of ``path``'s copy in the mirror once it is what was sent - a plain file of the size and modification
    time the reader saw on the other machine (O(stamps)), which rsync -a keeps - or None. A copy an earlier run left is
    this run's only where nothing changed since: after an rsync that failed, never."""
    mirror, stamp = params["mirror"], (params["stamps"] or {}).get(path)
    if not mirror or not plain_path(path) or not _within(mirror, path) or not isinstance(stamp, (list, tuple)):
        return None
    try:
        size, mtime = (int(value) for value in stamp)
        found = os.lstat(os.path.join(mirror, path))
    except (OSError, TypeError, ValueError):
        return None
    if not stat.S_ISREG(found.st_mode) or found.st_size != size or int(found.st_mtime) != mtime:
        return None
    return _digest(os.path.join(mirror, path)) or None


def ignored_step(apply, params, manifest, result):
    """The files git ignores that the sync carried, by file_verdict, each once it arrived as sent; the sync's own
    copies of files gone from the folder they came from, removed. Returns the manifest's ignored entries as they are
    now."""
    module, top, mirror, source = apply.module, apply.top, params["mirror"], params["source"]
    said = result["files"]
    wanted = sorted({path for path in params["ignored"] or [] if plain_path(str(path))})
    gone = {str(path) for path in params["gone"] or []}
    unignored = {str(path) for path in params["unignored"] or []}
    entries = {
        key: value
        for key, value in (manifest.get("ignored") or {}).items()
        if isinstance(value, dict) and plain_path(str(key))
    }
    tracked = _tracked(module, top) if wanted or entries else set()
    plan = {}
    for path in wanted:
        sent = _arrived(params, path)
        if not sent:
            said["missing"].append(path)
            continue
        there = _digest(os.path.join(top, path)) if _within(top, path) else None
        entry = entries.get(path) or {}
        plan[path] = (file_verdict(there, path in tracked, _ours(entries, path), sent), sent, entry)
    # Before any file: what each path held by the last sync, and what it is about to hold.
    before = dict(entries)
    for path, (verdict, sent, entry) in plan.items():
        if verdict == "write":
            before[path] = {"now": sent, "was": entry.get("now", sent), "source": source}
    if before != entries:
        apply.changed |= _manifest(params["manifest"], {**manifest, "ignored": before})
    now = dict(before)
    for path, (verdict, sent, _) in sorted(plan.items()):
        if verdict == "write":
            try:
                _copy(os.path.join(mirror, path), top, path)
            except OSError:
                # Its entry keeps both digests: what is there is still the sync's.
                said["failed"].append(path)
                continue
            apply.changed = True
            said["written"].append(path)
            now[path] = {"now": sent, "source": source}
        elif verdict == "unchanged":
            said["unchanged"].append(path)
            now[path] = {"now": sent, "source": source}
        else:
            said["left"].append(path)
            if verdict == "tracked":
                now.pop(path, None)
    # Every other copy stays, and stays the sync's - one only left out of this run (repo_sync_ignored off, past the
    # bound, excluded) is still a file there - but one gone from the folder it came from, still as the sync wrote it,
    # which goes. A path git tracks there now, with nothing at it, or that the other machine no longer ignores, is no
    # longer the sync's: the file stays, and its line leaves the block.
    for path, entry in sorted(entries.items()):
        if path in plan:
            continue
        there = _digest(os.path.join(top, path)) if _within(top, path) else None
        if path in tracked or there == "" or path in unignored:
            now.pop(path, None)
            continue
        if path not in gone or entry.get("source") != source:
            continue
        if there and there in _ours(entries, path):
            try:
                os.unlink(os.path.join(top, path))
            except OSError:
                said["failed"].append(path)
                continue
            apply.changed = True
            said["removed"].append(path)
            now.pop(path, None)
    return now


def _block_lines(lines):
    """The line numbers, from 1 as git counts them, of the sync's block in an exclude file's ``lines``."""
    if EXCLUDE_BEGIN not in lines:
        return set()
    begin = lines.index(EXCLUDE_BEGIN) + 1
    end = next((index for index in range(begin, len(lines)) if lines[index] == EXCLUDE_END), len(lines))
    return set(range(begin + 1, end + 1))


def exclude_step(apply, entries, result):
    """The sync's block of the checkout's .git/info/exclude: each file it carried that git there would not ignore
    without the block - no pattern matches it, or only one of the block's - by git check-ignore -v, which names where a
    match comes from."""
    module, top = apply.module, apply.top
    path = text(module, top, "rev-parse", "--git-path", "info/exclude")
    if not path:
        return
    exclude = os.path.join(top, path)
    try:
        with open(exclude, encoding="utf-8") as handle:
            current = handle.read()
    except FileNotFoundError:
        current = ""
    except (OSError, UnicodeDecodeError):
        return
    block = _block_lines(current.splitlines())
    needed = []
    if entries:
        query = "\0".join(sorted(entries))
        rc, out = git(module, top, "check-ignore", "-v", "-z", "--non-matching", "--stdin", data=query)
        if rc not in (0, 1):
            # git could not say which need a line: the file stays as it is, never without the sync's block.
            return
        fields = out.split(b"\0")
        for index in range(0, len(fields) - 3, 4):
            source, line, _, name = (field.decode("utf-8", "replace") for field in fields[index : index + 4])
            ours = bool(source) and os.path.realpath(os.path.join(top, source)) == os.path.realpath(exclude)
            if not source or (ours and line.isdigit() and int(line) in block):
                needed.append(name)
    wanted = exclude_text(current, needed)
    result["excluded"] = sorted(needed)
    if wanted != current:
        os.makedirs(os.path.dirname(exclude), exist_ok=True)
        with open(exclude, "w", encoding="utf-8") as handle:
            handle.write(wanted)
        apply.changed = True


def prune_mirror(mirror, keep):
    """What the mirror holds that was not sent this run, removed: plain files and links alike, never followed."""
    if not mirror or not os.path.isdir(mirror):
        return
    for folder, dirs, files in os.walk(mirror, topdown=False):
        for name in files + [item for item in dirs if os.path.islink(os.path.join(folder, item))]:
            path = os.path.join(folder, name)
            if os.path.relpath(path, mirror) not in keep:
                os.unlink(path)
        if folder != mirror:
            try:
                os.rmdir(folder)
            except OSError:
                pass


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


def tree_step(apply, params, heads, records, arrived, result, state):
    """The checkout itself: moved on its branch, or switched to the one there, when nothing stops it. ``state`` is
    the manifest as read: the files the sync carried there as ignored ones."""
    module, top = apply.module, apply.top
    copies = state.get("ignored") or {}
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
    # A copy the sync carried as a file git ignores, which the commit now tracks: the sync's, and the commit brings
    # the file in its place.
    theirs = [
        path
        for path in in_the_way or []
        if _within(top, path) and _digest(os.path.join(top, path)) in _ours(copies, path)
    ]
    if theirs:
        try:
            for path in theirs:
                os.unlink(os.path.join(top, path))
                apply.changed = True
        except OSError as error:
            _stays(said, "write", f"the sync's own copy of a file could not be taken away: {error.strerror}")
            return ""
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
            "mirror": {"type": "str", "default": ""},
            "ignored": {"type": "list", "elements": "str", "default": []},
            "stamps": {"type": "dict", "default": {}},
            "gone": {"type": "list", "elements": "str", "default": []},
            "unignored": {"type": "list", "elements": "str", "default": []},
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
    apply = Apply(module, top)
    result = {
        "branches": {key: [] for key in ("created", "moved", "same", "ahead", "own", "upstream", "in_use", "missing")},
        "tags": {"created": [], "differ": []},
        "tree": {"brought": False, "why": "", "reason": "", "head": ""},
        "files": {key: [] for key in ("written", "unchanged", "left", "missing", "removed", "failed")},
        "excluded": [],
        "error": "",
    }
    try:
        apply_all(apply, params, branches, tags, result)
    except OSError as error:
        # A file the VM would not let it write - a folder of root's in the checkout, a full disk: said, never a
        # traceback. The manifest, written before each part, keeps what was written the sync's.
        result["error"] = f"{error.strerror or error}: {error.filename}" if error.filename else str(error)
    module.exit_json(changed=apply.changed, **result)


def apply_all(apply, params, branches, tags, result):
    """Every part, in order, into ``result``."""
    module, top = apply.module, apply.top
    session = str(params["session_branch"] or "")
    heads, records = refs(module, top, "refs/heads/"), refs(module, top, RECORD)
    sent = refs(module, top, f"{SENT}heads/")
    said = result["branches"]
    arrived = {name: sent.get(name) == commit and apply.has(commit) for name, commit in branches.items()}
    # What the sync last carried here as ignored files: read once, written as that part goes.
    found = _read_manifest(params["manifest"])
    state = {"format": MANIFEST_FORMAT, **{key: found[key] for key in ("ignored",) if isinstance(found.get(key), dict)}}
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
    brought = tree_step(apply, params, heads, records, arrived, result, state) if not session else ""
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
    # 5. The ignored files, then the block that keeps them out of git status there.
    entries = ignored_step(apply, params, state, result)
    exclude_step(apply, entries, result)
    # Everything this run sent stays in the mirror, a file the VM changed too, so that rsync sends it again only once
    # it changes here.
    prune_mirror(params["mirror"], {str(path) for path in params["stamps"] or {}} | set(params["ignored"] or []))
    content = {
        "format": MANIFEST_FORMAT,
        "source": params["source"],
        "head": text(module, top, "rev-parse", "--verify", "--quiet", "HEAD") or "",
        "ignored": entries,
    }
    result["tree"]["head"] = content["head"]
    if _manifest(params["manifest"], content):
        apply.changed = True


if __name__ == "__main__":
    main()
