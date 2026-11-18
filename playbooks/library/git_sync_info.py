#!/usr/bin/python
# Copyright (c) agent-warehouse contributors
# MIT License
"""Read a git checkout for a repository sync - what it is, and on request what it would carry - without changing it."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: git_sync_info
short_description: Read a git checkout for a repository sync without changing it
version_added: "2.8.0"
description:
  - Reports whether O(path) is in a git checkout, the checkout's top level, its C(origin) URL as configured, whether
    it is a shallow clone, the branch HEAD is on and its commit, and the commit of every ref it has.
  - With O(select), also what a sync would carry from it - every local branch and tag, what is under way (a merge, a
    rebase), the changes not committed against HEAD, and the files git does not track that may go, each file at any
    depth judged by git's own matcher - and what it would leave, each with its reason. A file that is this project's
    own secret or record (a vault, its password, an inventory, a run log) never goes, by its name or, for a vault or a
    private key, by its first bytes, and a wholly ignored or new folder that holds one is left whole. A credential by
    its name and a derived tree are left unless the checkout's C(.worktreeinclude) names them. A link, a repository
    inside the repository and a name with a control character are left. Of the changes not committed, a path
    C(repo_sync_exclude) names (O(exclude)) is left, and so is a vault decrypted in place and a tracked path that is a
    folder here (a submodule, a file made a folder).
  - With O(estimate), instead, how many bytes of git objects the refs given hold beyond the commits given that this
    checkout has, as C(git rev-list --objects --disk-usage) counts them.
  - With O(manifest), also the ignored files a repository sync carried into this checkout, as its manifest records them;
    and with O(select) and O(previous), which of those the other end named are gone here, and which are here and no
    longer ignored.
  - Every git command runs as C(git --no-optional-locks), so none refreshes the index under C(index.lock). Nothing is
    changed, and O(path) is taken as written: no C(~) or C($VAR) in it is expanded.
options:
  path:
    description: The directory to read, in the checkout or at its top.
    type: str
    required: true
  select:
    description: Also report what a sync would carry from the checkout, and what it would leave.
    type: bool
    default: false
  ignored:
    description: With O(select), look at the files git ignores too, not only the new ones.
    type: bool
    default: true
  exclude:
    description: With O(select), patterns in C(.gitignore) syntax whose files are left.
    type: list
    elements: str
    default: []
  manifest:
    description: A repository sync's manifest to read, outside the checkout.
    type: str
  previous:
    description:
      - With O(select), the files the other end says a sync carried there as ignored ones, each relative to the
        checkout: which of them nothing is at here, C(gone), and which are here and not ignored, C(unignored). Only
        ever those it named.
    type: list
    elements: str
    default: []
  estimate:
    description:
      - C(refs), the refs a send would carry, each C(refs/heads/<name>), C(refs/tags/<name>) or a full commit ID;
        and C(known), the commits the other end has, each a full commit ID, of which only those this checkout has
        count.
    type: dict
author:
  - agent-warehouse contributors
"""

EXAMPLES = r"""
- name: Read the checkout just was started in
  git_sync_info:
    path: /home/me/code/my-app
  register: here
"""

RETURN = r"""
repo:
  description: What was read.
  returned: always
  type: dict
  contains:
    git:
      description: Whether O(path) is in a git checkout.
      type: bool
    at_top:
      description: Whether O(path) is the checkout's top level, and not a folder inside it.
      type: bool
    toplevel:
      description: The checkout's top level, or "".
      type: str
    origin:
      description:
        - The URL of its C(origin) remote as configured, or "", without what precedes the host in a URL with a scheme,
          which can be a token.
      type: str
    shallow:
      description: Whether it is a shallow clone.
      type: bool
    head:
      description: C(branch), the branch HEAD is on ("" when detached), and C(commit), HEAD's commit ("" before any).
      type: dict
    tips:
      description: The commit of every ref, a tag's too, sorted.
      type: list
      elements: str
    operation:
      description: With O(select), what git has under way - merge, rebase, cherry-pick, revert or bisect - or "".
      type: str
    branches:
      description: With O(select), each local branch's commit, by its name.
      type: dict
    upstreams:
      description: With O(select), each local branch that tracks a branch of origin, and that branch's name there.
      type: dict
    tags:
      description: With O(select), each tag's object, by its name.
      type: dict
    stashes:
      description: With O(select), how many stashes it keeps.
      type: int
    worktrees:
      description: With O(select), how many worktrees it has besides this one.
      type: int
    changes:
      description:
        - With O(select), the changes not committed, against HEAD - C(modified), C(deleted), C(new) (untracked, or
          staged and never committed, each by the selection's names) and C(links), a link among them as C(path) and
          C(target). A file taken out of the index and kept is no deletion; a vault decrypted in place, a path
          O(exclude) names and a tracked path that is a folder here are left.
      type: dict
    files:
      description:
        - With O(select), what a sync may send as files, each C(path), C(kind) (change, new or ignored), C(size),
          C(mtime) (whole seconds) and C(entry), the file itself or the wholly ignored or new folder git lists it
          under.
      type: list
      elements: dict
    left:
      description:
        - With O(select), what it would leave, each C(path) - a file, or a folder whose every file is left for one
          reason and none carried - C(why), C(files), C(repositories) (the repositories inside such a folder, each
          one name) and C(size).
      type: list
      elements: dict
    included:
      description: With O(select), the files a C(.worktreeinclude) carries that are credentials by their names.
      type: list
      elements: str
    gone:
      description: With O(select), the files of O(previous) that nothing is at here.
      type: list
      elements: str
    unignored:
      description: With O(select), the files of O(previous) that are here and git here does not ignore.
      type: list
      elements: str
    carried:
      description: With O(manifest), the ignored files the sync carried there, as it records them.
      type: list
      elements: str
    bytes:
      description: With O(estimate), the bytes of the objects a send of the refs would carry.
      type: int
"""

import json
import os
import re
import stat

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.git_checkouts import COMMIT, worktrees
from ansible.module_utils.git_sync import (
    MAX_PATHSPECS,
    VAULT_START,
    WHY,
    checkout,
    entry_index,
    git,
    holding,
    matching,
    names,
    operation,
    plain,
    plain_path,
    refs,
    select,
)

# A ref the estimate may be given: a branch's or a tag's, held to what git's own names take, or a commit's ID.
SENT_REF = re.compile(r"refs/(?:heads|tags)/[^\x00-\x20\x7f~^:?*\[\\]+")


def changes(module, top, has_head):
    """The tracked changes not committed, against HEAD: (modified, deleted, staged-new), each a list of bytes."""
    if not has_head:
        return [], [], []
    rc, out = git(module, top, "diff", "--name-status", "-z", "--no-renames", "HEAD", "--")
    if rc != 0:
        module.fail_json(msg=f"git could not compare the tree with HEAD in {top}.")
    fields = names(out)
    modified, deleted, added = [], [], []
    for status, name in zip(fields[0::2], fields[1::2], strict=False):
        {b"D": deleted, b"A": added}.get(status[:1], modified).append(name)
    return modified, deleted, added


def collapse(left, entry_of, top, carrying=frozenset()):
    """What is left, a folder at a time where every file of an entry git lists as one is left for the same reason and
    none of its files goes (``carrying``, the folders that hold a file carried): else the folders git lists inside it,
    the same way, and only then file by file. A repository inside is one name, counted as one; a folder left whole
    without being listed (wholly derived) is one name, its files not counted."""
    by_entry = {}
    for name, why in left:
        by_entry.setdefault(entry_of(name), []).append((name, why))
    said = []
    for entry, items in sorted(by_entry.items()):
        reasons = {why for _, why in items}
        folder = entry.endswith(b"/")
        if folder and entry not in carrying and len(reasons) == 1 and plain(entry) is not None:
            inside = [name for name, _ in items if name != entry]
            files = [name for name in inside if not name.endswith(b"/")]
            size = sum(_stat(os.path.join(top.encode(), name))[0] for name in files)
            said.append(
                {
                    "path": plain(entry),
                    "why": WHY[reasons.pop()],
                    "files": len(files),
                    "repositories": len(inside) - len(files),
                    "size": size,
                }
            )
            continue
        inner = entry_index(item for item in entry_of.folders if item != entry and item.startswith(entry))
        inside = [(name, why) for name, why in items if name != entry]
        if folder and inner.folders and inside:
            # One level down: each name under a deeper folder git lists, never the entry itself again.
            said += collapse(inside, inner, top, carrying)
            items = [(name, why) for name, why in items if name == entry]
        for name, why in items:
            # A name that cannot be said is counted, never said.
            said.append(
                {
                    "path": plain(name) or "",
                    "why": WHY[why],
                    "files": 1,
                    "repositories": 0,
                    "size": _stat(os.path.join(top.encode(), name))[0],
                }
            )
    return said


def _stat(path):
    """A file's size and its modification time in whole seconds, as rsync -a keeps it: what the apply holds the copy
    that arrived to."""
    try:
        found = os.lstat(path)
    except OSError:
        return 0, 0
    return found.st_size, int(found.st_mtime)


def committed_vaults(module, top, candidates):
    """Which of ``candidates`` (names as bytes, each a plain file here that is no vault now) HEAD holds as an Ansible
    vault: a vault decrypted in place, whose change would carry its secrets in plain text. By each blob's first bytes,
    read only for blobs small enough to be a vault."""
    if not candidates:
        return set()
    query = b"".join(b"HEAD:" + name + b"\n" for name in candidates)
    rc, out = git(module, top, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)", data=query)
    lines = out.decode("ascii", "replace").splitlines() if rc == 0 else []
    small = {}
    for name, line in zip(candidates, lines, strict=False):
        fields = line.split(" ")
        if len(fields) == 3 and fields[1] == "blob" and fields[2].isdigit() and int(fields[2]) <= 1 << 20:
            small.setdefault(fields[0], []).append(name)
    if not small:
        return set()
    rc, out = git(module, top, "cat-file", "--batch", data="".join(f"{blob}\n" for blob in small).encode())
    found, rest = set(), out if rc == 0 else b""
    while rest:
        header, _, rest = rest.partition(b"\n")
        fields = header.split(b" ")
        if len(fields) != 3 or not fields[2].isdigit():
            break
        size = int(fields[2])
        if rest[:size].startswith(VAULT_START):
            found.update(small.get(fields[0].decode("ascii", "replace"), []))
        rest = rest[size + 1 :]
    return found


def _link(name, path, links, left):
    """A link, by its target's text: carried as that, or left where the text cannot be said."""
    target = os.readlink(path)
    if plain(target) is None:
        left.append((name, "name"))
    else:
        links.append({"path": plain(name), "target": plain(target)})


def read_selection(module, top, found, ignored_too, exclude):
    """What a sync would carry from the checkout at ``top``, and what it would leave, into ``found``."""
    found.update(
        operation=operation(module, top),
        branches=refs(module, top, "refs/heads/"),
        upstreams=upstreams(module, top),
        tags=refs(module, top, "refs/tags/"),
        stashes=len(git(module, top, "stash", "list")[1].splitlines()),
        worktrees=max(len(worktrees(module, top)) - 1, 0),
    )
    modified, deleted, added = changes(module, top, bool(found["head"]["commit"]))
    # New: untracked and not ignored, or staged and never committed. The rest of what the selection judged is ignored.
    verdicts, entry_of, included, new_names = select(module, top, ignored_too, exclude, staged=added)
    files, left, links, new = [], [], [], []
    carried_names = [name for name, (verdict, _) in verdicts.items() if verdict == "carried"]
    found["ignored_here"] = {
        plain(name) for name in verdicts if name not in new_names and not name.endswith(b"/") and plain(name)
    }
    for name, (verdict, why) in sorted(verdicts.items()):
        path = os.path.join(top.encode(), name)
        entry = plain(entry_of(name))
        if verdict != "carried":
            left.append((name, why))
        elif os.path.islink(path):
            _link(name, path, links, left)
        else:
            size, mtime = _stat(path)
            kind = "new" if name in new_names else "ignored"
            if kind == "new":
                new.append(plain(name))
            files.append({"path": plain(name), "kind": kind, "size": size, "mtime": mtime, "entry": entry})
    # The owner's own patterns hold for the changes too: a tracked path repo_sync_exclude names goes in no way, a
    # deletion included.
    tracked = modified + deleted
    excluded = matching(module, top, exclude, "--cached", tracked if len(tracked) <= MAX_PATHSPECS else None)
    for name in [name for name in tracked if name in excluded]:
        left.append((name, "excluded"))
    modified = [name for name in modified if name not in excluded]
    deleted = [name for name in deleted if name not in excluded]
    changed, plainly = [], []
    for name in modified:
        path = os.path.join(top.encode(), name)
        if plain(name) is None:
            left.append((name, "name"))
        elif os.path.islink(path):
            _link(name, path, links, left)
        elif os.path.isfile(path):
            plainly.append(name)
        elif os.path.isdir(path):
            # A submodule moved, or a file made a folder: the VM cannot be brought to it by files, and the rest goes.
            left.append((name, "folder"))
        else:
            # A file removed without git rm's knowing: gone from the tree, as deleted.
            deleted.append(name)
    # A vault decrypted in place is a change like any other to git: left, or its secrets would arrive in plain text.
    now_plain = [name for name in plainly if not _starts(os.path.join(top.encode(), name), VAULT_START)]
    decrypted = committed_vaults(module, top, now_plain)
    for name in plainly:
        if name in decrypted:
            left.append((name, "decrypted"))
            continue
        size, mtime = _stat(os.path.join(top.encode(), name))
        changed.append(plain(name))
        files.append({"path": plain(name), "kind": "change", "size": size, "mtime": mtime, "entry": plain(name)})
    # Taken out of the index and kept (git rm --cached): no deletion, but a file git does not track, judged as one. A
    # folder where git has a file: left, as a submodule's is.
    for name in deleted:
        path = os.path.join(top.encode(), name)
        if os.path.isdir(path) and not os.path.islink(path):
            left.append((name, "folder"))
    deleted = [name for name in deleted if not _a_file(os.path.join(top.encode(), name))]
    deleted = [name for name in deleted if not os.path.isdir(os.path.join(top.encode(), name))]
    found.update(
        changes={
            "modified": changed,
            "deleted": sorted(plain(name) for name in deleted if plain(name) is not None),
            "new": sorted(new),
            "links": sorted(links, key=lambda link: link["path"]),
        },
        files=files,
        left=collapse(left, entry_of, top, holding(carried_names, entry_of.folders)),
        included=sorted(plain(name) for name in included if plain(name) is not None),
    )


def upstreams(module, top):
    """{branch: branch of origin it tracks} for each local branch whose upstream is origin's: what the VM's branch
    tracks once the sync makes it, as here."""
    fields = "%(refname)%00%(upstream:remotename)%00%(upstream:remoteref)"
    rc, out = git(module, top, "for-each-ref", f"--format={fields}", "refs/heads/")
    found = {}
    for line in out.decode("utf-8", "replace").splitlines() if rc == 0 else ():
        ref, remote, merge = (line.split("\0") + ["", ""])[:3]
        if remote == "origin" and ref.startswith("refs/heads/") and merge.startswith("refs/heads/"):
            found[ref[len("refs/heads/") :]] = merge[len("refs/heads/") :]
    return found


def _starts(path, start):
    try:
        with open(path, "rb") as handle:
            return handle.read(len(start)) == start
    except OSError:
        return False


def _a_file(path):
    """Whether a plain file or a link is at ``path``: a folder there is not one."""
    return os.path.islink(path) or os.path.isfile(path)


def carried(path):
    """The ignored files a sync's manifest at ``path`` records, held to plain paths; none where there is no manifest."""
    try:
        with open(path, encoding="utf-8") as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return []
    ignored = found.get("ignored") if isinstance(found, dict) else None
    return sorted(key for key in ignored if plain_path(key)) if isinstance(ignored, dict) else []


def _nothing_at(top, path):
    """Whether nothing is at ``path`` (relative) in ``top``: a link on the way counts as something, since what it
    reaches is not the checkout's to say."""
    folder = top
    parts = path.split("/")
    for index, part in enumerate(parts):
        folder = os.path.join(folder, part)
        try:
            mode = os.lstat(folder).st_mode
        except (FileNotFoundError, NotADirectoryError):
            return True
        except OSError:
            return False
        if index < len(parts) - 1 and not stat.S_ISDIR(mode):
            return not stat.S_ISLNK(mode)
    return False


def gone(top, previous):
    """Which of ``previous``, as plain paths, nothing is at here: a copy there of a file that is gone may go."""
    return sorted({str(path) for path in previous or [] if plain_path(str(path)) and _nothing_at(top, str(path))})


def unignored(top, previous, ignored):
    """Which of ``previous``, as plain paths, are here and not among ``ignored`` (the files git ignores here, as the
    selection listed them): tracked now, or no longer named by an ignore file, so the VM's copy is no ignored file of
    the sync's. Asked of the reader's own listing, never of a command: the names are the VM's."""
    return sorted(
        {
            str(path)
            for path in previous or []
            if plain_path(str(path)) and not _nothing_at(top, str(path)) and str(path) not in ignored
        }
    )


def estimate(module, top, given):
    """The bytes of git objects ``given``'s refs hold beyond its known commits that this checkout has."""
    sent = [str(ref) for ref in given.get("refs") or []]
    for ref in sent:
        if not (SENT_REF.fullmatch(ref) or COMMIT.fullmatch(ref)):
            module.fail_json(msg=f"{ref!r} is not a branch's, a tag's or a commit's name to estimate a send by.")
    known = [str(commit) for commit in given.get("known") or [] if COMMIT.fullmatch(str(commit))]
    if not sent:
        return 0
    have = []
    if known:
        rc, out = git(module, top, "cat-file", "--batch-check=%(objectname) %(objecttype)", data="\n".join(known))
        for line in out.decode("ascii", "replace").splitlines() if rc == 0 else ():
            object_id, _, kind = line.partition(" ")
            if kind in ("commit", "tag") and COMMIT.fullmatch(object_id):
                have.append(object_id)
    # Each known commit as ^<id>: git before 2.42 refuses an option, --not among them, in --stdin mode.
    lines = "\n".join([*sent, *(f"^{commit}" for commit in have)])
    rc, out = git(module, top, "rev-list", "--objects", "--disk-usage", "--stdin", data=lines)
    if rc != 0:
        module.fail_json(msg=f"git could not count what a send from {top} would carry.")
    return int(out.decode("ascii", "replace").strip() or 0)


def main():
    module = AnsibleModule(
        argument_spec={
            # str, not path: a path option expands ~ and $VAR, and a folder's name is free text.
            "path": {"type": "str", "required": True},
            "select": {"type": "bool", "default": False},
            "ignored": {"type": "bool", "default": True},
            "exclude": {"type": "list", "elements": "str", "default": []},
            "manifest": {"type": "str"},
            "previous": {"type": "list", "elements": "str", "default": []},
            "estimate": {"type": "dict"},
        },
        supports_check_mode=True,
    )
    params = module.params
    found = checkout(module, params["path"])
    if params["manifest"]:
        found["carried"] = carried(params["manifest"])
    if found["git"] and params["select"]:
        read_selection(module, found["toplevel"], found, params["ignored"], params["exclude"])
        found["gone"] = gone(found["toplevel"], params["previous"])
        # Without the ignored files listed, none can be told no longer ignored.
        ignored = found.pop("ignored_here")
        found["unignored"] = unignored(found["toplevel"], params["previous"], ignored) if params["ignored"] else []
    if found["git"] and params["estimate"] is not None:
        found["bytes"] = estimate(module, found["toplevel"], params["estimate"])
    module.exit_json(changed=False, repo=found)


if __name__ == "__main__":
    main()
