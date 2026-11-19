"""Carrying a local git repository to its checkout on a VM: the filters of filter_plugins/repo_sync.py.

playbooks/tasks/repo_read.yml reads both ends with git_sync_info and decides here, from what it read: whether to sync
at all - the checkout just was started in and the VM's checkout of the project must name the same repository by their
git origins, never by a folder's name - what to send and within which bound, what is left here and why, and the
report's words (docs/architecture/lifecycle.md §4.9). What only the VM can decide, after a send - a branch's row, the
tree's verdict, a file's - is git_sync_apply's, on the VM.

Nothing here reads a file: the module does, and only its values reach a filter. Nothing the VM reports chooses what is
sent: the refs are this machine's alone, and the VM's commits are only left out of an estimate.
"""

from __future__ import annotations

import re

from claude_vm.sessions import git_repository, github_repository
from claude_vm.text import and_list, plural

# Where a send delivers, in the VM checkout's .git: never a branch of its own, which the apply alone moves.
SENT = "refs/agent-warehouse/sent/"
# A ref's short name a send carries: what git's own names take, with nothing a line of send-pack's input or a word of a
# command could be read otherwise by.
_REF_NAME = re.compile(r"[^\x00-\x20\x7f~^:?*\[\\-][^\x00-\x20\x7f~^:?*\[\\]*")
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# How many names a report lists before it counts the rest.
_LISTED = 3


def _size(count):
    """Bytes as a person reads them: 512 B, 2.6 MB."""
    count = float(count or 0)
    for unit in ("B", "kB", "MB", "GB"):
        if count < 1000 or unit == "GB":
            return f"{int(count)} B" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1000
    return f"{count:.1f} GB"


def _some(names, word="more"):
    """The first few names and how many more: "a, b, c and 4 more"."""
    names = [str(name) for name in names]
    if len(names) <= _LISTED + 1:
        return and_list(names)
    return f"{', '.join(names[:_LISTED])} and {len(names) - _LISTED} {word}"


def repo_sync_match(here, there, project, vm, target, elsewhere=False):
    """Whether the checkout just was started in (``here``, git_sync_info's) is the repository of ``project`` on ``vm``
    (``there``, its checkout at ``target``), by their origins: here any origin names one (git_repository), since a
    clone here may reach GitHub through an SSH alias of the user's own; on the VM only a GitHub one
    (github_repository), as just deploy clones it.

    Returns ``sync``, the repository (``owner/repo``), the source (here's top level), and ``said``: the one line why
    not, said only when just was started in another repository than this one (``elsewhere``) - from this checkout,
    with another project's name, every run would say it.
    """
    here, there = here or {}, there or {}
    source = str(here.get("toplevel") or "")
    found = {"sync": False, "repository": "", "source": source, "said": ""}
    if not here.get("git"):
        return found
    mine = git_repository(here.get("origin"))
    theirs = github_repository(there.get("origin")) if there.get("git") and there.get("at_top") else ""
    if mine and mine == theirs:
        return found | {"sync": True, "repository": mine}
    if not elsewhere:
        return found
    if not mine:
        why = f"{source} has no origin to match it by"
    elif not there.get("git") or not there.get("at_top"):
        why = f"{target} on {vm} is not a git checkout"
    elif not theirs:
        why = f"{target} on {vm} has no GitHub origin"
    else:
        why = f"{source} is a checkout of {mine}, and {target} on {vm} of {theirs}"
    return found | {"said": f"{why}, so nothing of {source} was carried to {project} there (repo_sync)."}


def repo_sync_refs(selected):
    """send-pack's lines for ``selected`` (git_sync_info's read here, with select): every local branch and tag, each
    into the VM checkout's own refs of the sync's, and HEAD's commit where it is detached - this machine's refs alone,
    each named in full. A name git would not take, or that could be read as something else, is left out."""
    lines = []
    for kind, found in (("heads", selected.get("branches") or {}), ("tags", selected.get("tags") or {})):
        for name in sorted(found):
            if _REF_NAME.fullmatch(str(name)) and _COMMIT.fullmatch(str(found[name])):
                lines.append(f"refs/{kind}/{name}:{SENT}{kind}/{name}")
    head = selected.get("head") or {}
    if not head.get("branch") and _COMMIT.fullmatch(str(head.get("commit") or "")):
        lines.append(f"{head['commit']}:{SENT}HEAD")
    return lines


def repo_sync_sources(selected):
    """What a send carries, each line's source: the refs the estimate counts."""
    return [line.split(":", 1)[0] for line in repo_sync_refs(selected)]


def _bounded(files, cap):
    """Which of ``files`` (git_sync_info's, each with its entry) fit in ``cap`` bytes (0: no bound): entries that are
    single files first, in path order, then folders by their total size, smallest first. An entry that does not fit is
    left whole. Returns (paths kept, bytes kept, [(entry, files, bytes)] left)."""
    entries = {}
    for item in files:
        entries.setdefault(item.get("entry") or item["path"], []).append(item)
    # Single files by their paths alone; folders by their total size, then their paths.
    sizes = {entry: sum(int(item.get("size") or 0) for item in entries[entry]) for entry in entries}
    order = sorted(entries, key=lambda entry: (entry.endswith("/"), sizes[entry] if entry.endswith("/") else 0, entry))
    kept, used, over = [], 0, []
    for entry in order:
        size = sum(int(item.get("size") or 0) for item in entries[entry])
        if cap and used + size > cap:
            over.append((entry, len(entries[entry]), size))
            continue
        used += size
        kept += sorted(item["path"] for item in entries[entry])
    return kept, used, over


def repo_sync_plan(selected, match, cap=0, send_bytes=0, session_branch=""):
    """What a sync carries, from ``selected`` (git_sync_info's read here, with select), once ``match`` said it may.

    ``cap`` is repo_sync_max_size in bytes (0: none), which bounds the git objects - ``send_bytes``, the estimate - and
    again the files: the changes not committed first, all or none, then the ignored files, an entry at a time. A new
    file the selection leaves stays here, said, and the rest of the changes go.
    ``session_branch`` is --session's, whose branch and the checkout are --session's to move.

    Returns ``sync`` and ``why``; ``source`` and ``repository``; ``refs``, send-pack's lines, and ``send``, whether
    they go; ``branches``, ``tags`` and ``head`` as here; ``tree``, the checkout's move and the changes, with
    ``bring`` false for this machine's own reasons; ``files``, rsync's list, and ``file_bytes``; ``ignored``, the
    ignored files among them; and ``left``, each ``what`` and ``why``, what stays here.
    """
    selected, match = selected or {}, match or {}
    plan = {
        "sync": bool(match.get("sync")),
        "why": str(match.get("said") or ""),
        "source": str(match.get("source") or ""),
        "repository": str(match.get("repository") or ""),
        "session_branch": str(session_branch or ""),
        "refs": [],
        "send": False,
        "send_bytes": int(send_bytes or 0),
        "branches": dict(selected.get("branches") or {}),
        "tags": dict(selected.get("tags") or {}),
        "head": dict(selected.get("head") or {"branch": "", "commit": ""}),
        "tree": {"bring": False, "why": "", "modified": [], "deleted": [], "new": [], "links": []},
        "files": [],
        "file_bytes": 0,
        "ignored": [],
        "included": list(selected.get("included") or []),
        "stashes": int(selected.get("stashes") or 0),
        "worktrees": int(selected.get("worktrees") or 0),
        "left": [],
    }
    if not plan["sync"]:
        return plan
    left = plan["left"]
    plan["refs"] = repo_sync_refs(selected)
    plan["send"] = bool(plan["refs"]) and not (cap and plan["send_bytes"] > cap)
    if plan["refs"] and not plan["send"]:
        left.append(
            {
                "what": plural(len(plan["branches"]), "branch", "branches"),
                "why": f"{_size(plan['send_bytes'])} of git objects to send, past repo_sync_max_size",
            }
        )
    changes = selected.get("changes") or {}
    files = list(selected.get("files") or [])
    tree_files = [item for item in files if item.get("kind") in ("change", "new")]
    tree_bytes = sum(int(item.get("size") or 0) for item in tree_files)
    tree = plan["tree"]
    tree.update(
        modified=list(changes.get("modified") or []),
        deleted=list(changes.get("deleted") or []),
        new=list(changes.get("new") or []),
        links=list(changes.get("links") or []),
    )
    if not plan["head"].get("commit"):
        tree["why"] = f"{plan['source']} has no commit yet"
    elif selected.get("operation"):
        tree["why"] = f"a {selected['operation']} is under way in {plan['source']}"
    elif plan["session_branch"]:
        tree["why"] = "--session brings the checkout to its session's commit"
    elif cap and tree_bytes > cap:
        tree["why"] = f"its changes not committed hold {_size(tree_bytes)}, past repo_sync_max_size"
    else:
        tree["bring"] = True
    if tree["bring"]:
        plan["files"] = sorted(item["path"] for item in tree_files)
        plan["file_bytes"] = tree_bytes
    else:
        tree.update(modified=[], deleted=[], new=[], links=[])
    ignored = [item for item in files if item.get("kind") == "ignored"]
    kept, used, over = _bounded(ignored, max(cap - plan["file_bytes"], 1) if cap else 0)
    plan["ignored"] = sorted(kept)
    plan["files"] = sorted(plan["files"] + kept)
    plan["file_bytes"] += used
    for entry, count, size in over:
        left.append({"what": f"{entry} ({plural(count, 'file')}, {_size(size)})", "why": "past repo_sync_max_size"})
    for item in selected.get("left") or []:
        # A folder left whole without being listed counts no files; a repository inside one counts as one.
        counted = [plural(item["files"], "file")] if item.get("files", 1) > 1 else []
        counted += [plural(item["repositories"], "repository", "repositories")] if item.get("repositories") else []
        what = item["path"] + (f" ({', '.join(counted)})" if counted else "")
        left.append({"what": what or "a file whose name cannot be said", "why": item["why"]})
    return plan


def repo_sync_said(plan, vm, project, applied=None):
    """The report's words for ``plan``: what was carried to ``project`` on ``vm`` (git_sync_apply's ``applied``), or,
    without it, what would be; what stays here, and why; and the switch that turns it off."""
    plan = plan or {}
    if not plan.get("sync"):
        return str(plan.get("why") or "")
    branches, tags, head = plan.get("branches") or {}, plan.get("tags") or {}, plan.get("head") or {}
    on = f"on {head['branch']}" if head.get("branch") else "on a detached HEAD"
    parts = [
        f"{plural(len(branches), 'branch', 'branches')} and {plural(len(tags), 'tag')}, {on} at "
        f"{str(head.get('commit') or '')[:8]}"
    ]
    if not plan.get("send") and plan.get("refs"):
        parts[0] = "no branch"
    ignored = plan.get("ignored") or []
    if ignored:
        parts.append(f"{plural(len(ignored), 'ignored file')} ({_some(ignored)})")
    tree = plan.get("tree") or {}
    changed = len(tree.get("modified") or []) + len(tree.get("deleted") or []) + len(tree.get("new") or [])
    changed += len(tree.get("links") or [])
    if tree.get("bring") and changed:
        parts.append(f"{plural(changed, 'change')} not committed")
    if applied is None:
        said = f"Read only, nothing sent: {project} on {vm} would get {plan['source']}'s {and_list(parts)}."
    else:
        said = f"{project} on {vm} has {plan['source']}'s {and_list(parts)}."
    if plan.get("file_bytes"):
        said += f" {_size(plan['file_bytes'])} of files."
    if not tree.get("bring") and tree.get("why"):
        said += f" Its tree stays as it is there: {tree['why']}."
    included = plan.get("included") or []
    if included:
        said += f" Carried because .worktreeinclude names them, credentials by their names: {_some(included)}."
    left = [f"{item['what']} ({item['why']})" for item in plan.get("left") or []]
    if plan.get("stashes"):
        left.append(plural(plan["stashes"], "stash", "stashes"))
    if left:
        said += f" Left here: {_some(left, 'more things')}."
    return said + " repo_sync: false turns this off."
