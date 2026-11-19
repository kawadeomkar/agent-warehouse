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
from claude_vm.text import and_list, one_line, plain_text, plural

# Where a send delivers, in the VM checkout's .git: never a branch of its own, which the apply alone moves. The apply's
# too (playbooks/module_utils/git_sync.py), on the VM: tests/unit/test_repo_sync.py holds the two equal.
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


# What the VM's path is held to before it reaches send-pack and rsync as <alias>:<path>: one plain absolute path, never
# put in an ssh:// URL, in which git decodes %.
_PLAIN_PATH = re.compile(r"/[A-Za-z0-9._/-]+")


def repo_sync_match(here, there, project, vm, target, elsewhere=False):
    """Whether the checkout just was started in (``here``, git_sync_info's) is the repository of ``project`` on ``vm``
    (``there``, its checkout at ``target``), by their origins: here any origin names one (git_repository), since a
    clone here may reach GitHub through an SSH alias of the user's own; on the VM only a GitHub one
    (github_repository), as just deploy clones it.

    Returns ``sync``, the repository (``owner/repo``), the source (here's top level), and ``said``: the one line why
    not, said only when just was started in another repository than this one (``elsewhere``) - from this checkout,
    with another project's name, every run would say it - but for a folder on the VM that is not one plain path, which
    is said whenever the two match.
    """
    here, there = here or {}, there or {}
    source = str(here.get("toplevel") or "")
    found = {"sync": False, "repository": "", "source": source, "said": ""}
    if not here.get("git"):
        return found
    mine = git_repository(here.get("origin"))
    theirs = github_repository(there.get("origin")) if there.get("git") and there.get("at_top") else ""
    if mine and mine == theirs:
        target = str(target or "")
        if not _PLAIN_PATH.fullmatch(target) or "/../" in f"{target}/" or "/./" in f"{target}/":
            return found | {
                "said": f"{project} on {vm} is not in one plain path of letters, digits, . _ - and /, so nothing of"
                f" {source} was carried there (repo_sync)."
            }
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


def repo_sync_plan(selected, match, cap=0, send_bytes=0, session_branch="", there=None):
    """What a sync carries, from ``selected`` (git_sync_info's read here, with select), once ``match`` said it may.

    ``cap`` is repo_sync_max_size in bytes (0: none), which bounds the git objects - ``send_bytes``, the estimate - and
    again the files: the changes not committed first, all or none, then the ignored files, an entry at a time. A new
    file the selection leaves stays here, said, and the rest of the changes go.
    ``session_branch`` is --session's, whose branch and the checkout are --session's to move. ``there`` is the VM's
    checkout as git_sync_info read it: whether it is shallow, which a branch it refuses is said with.

    Returns ``sync`` and ``why``; ``source`` and ``repository``; ``refs``, send-pack's lines, and ``send``, whether
    they go; ``branches``, ``tags`` and ``head`` as here; ``tree``, the checkout's move and the changes, with
    ``bring`` false for this machine's own reasons; ``files``, rsync's list, and ``file_bytes``; ``stamps``, each of
    those files' size and modification time, which the apply holds the copy that arrived to; ``ignored``, the ignored
    files among them; ``gone``, the ignored files a sync carried there that are gone here, which the apply may take
    away; ``unignored``, those here and no longer ignored, which are no longer the sync's there; ``upstreams``, the
    branch of origin each branch sent tracks here; and ``left``, each ``what`` and ``why``, what stays here.
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
        "stamps": {},
        "ignored": [],
        "gone": list(selected.get("gone") or []),
        "unignored": list(selected.get("unignored") or []),
        "upstreams": {},
        "included": list(selected.get("included") or []),
        "stashes": int(selected.get("stashes") or 0),
        "shallow": bool((there or {}).get("shallow")),
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
    if not plan["send"]:
        # Nothing arrives to create or move: the apply is handed none, and the tree stays.
        plan["branches"], plan["tags"] = {}, {}
    plan["upstreams"] = {
        name: str(branch)
        for name, branch in (selected.get("upstreams") or {}).items()
        if name in plan["branches"] and _REF_NAME.fullmatch(str(branch))
    }
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
    elif not plan["send"]:
        tree["why"] = "its branches were not sent"
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
    sent = set(plan["files"])
    stamps = {item["path"]: [int(item.get("size") or 0), int(item.get("mtime") or 0)] for item in files}
    plan["stamps"] = {path: stamp for path, stamp in stamps.items() if path in sent}
    for entry, count, size in over:
        left.append({"what": f"{entry} ({plural(count, 'file')}, {_size(size)})", "why": "past repo_sync_max_size"})
    for item in selected.get("left") or []:
        # A folder left whole without being listed counts no files; a repository inside one counts as one.
        counted = [plural(item["files"], "file")] if item.get("files", 1) > 1 else []
        counted += [plural(item["repositories"], "repository", "repositories")] if item.get("repositories") else []
        what = item["path"] + (f" ({', '.join(counted)})" if counted else "")
        left.append({"what": what or "a file whose name cannot be said", "why": item["why"]})
    return plan


def _vm_names(names):
    """Names the VM's apply returned, as plain text: they are the names it was handed, but the VM says them."""
    return [plain_text(name) for name in names or [] if isinstance(name, str)]


def repo_sync_said(plan, vm, project, applied=None, sent=None):
    """The report's words for ``plan``: what was carried to ``project`` on ``vm`` - git_sync_apply's ``applied``, and
    ``sent``, send-pack's exit code and last words (``rc``, ``words``) and rsync's (``files_rc``, ``files_words``);
    what stays there as it is, and here, and why; what did not arrive, or could not be written; and the switch that
    turns it off. What the apply did, never what was planned: without ``applied`` nothing was."""
    plan = plan or {}
    if not plan.get("sync"):
        return str(plan.get("why") or "")
    head = plan.get("head") or {}
    on = f"on {head['branch']}" if head.get("branch") else "on a detached HEAD"
    at = f"{on} at {str(head.get('commit') or '')[:8]}"
    branches, tags = plan.get("branches") or {}, plan.get("tags") or {}
    applied = applied or {}
    done = applied.get("branches") or {}
    new, moved = len(done.get("created") or []), len(done.get("moved") or [])
    counted = [f"{new} new there"] if new else []
    counted += [f"{moved} moved"] if moved else []
    parts = [
        f"{plural(len(branches), 'branch', 'branches')}{' (' + ', '.join(counted) + ')' if counted else ''} and "
        f"{plural(len(tags), 'tag')}"
    ]
    if not plan.get("send") and plan.get("refs"):
        parts = ["no branch"]
    # A file is there once it was written or found as sent.
    files = applied.get("files") or {}
    there = _vm_names(files.get("written")) + _vm_names(files.get("unchanged"))
    if there:
        parts.append(f"{plural(len(there), 'ignored file')} ({_some(sorted(there))})")
    tree = applied.get("tree") or {}
    changes = int(tree.get("changes") or 0)
    if changes:
        parts.append(f"{plural(changes, 'change')} not committed")
    said = f"{project} on {vm} has {plan['source']}'s {and_list(parts)}"
    said += f", its checkout {at}." if tree.get("brought") else "."
    why = tree.get("why") or (plan.get("tree") or {}).get("why")
    if why and not tree.get("brought"):
        said += f" Its checkout stays as it is there: {plain_text(why)}."
    held = [f"{name} (commits of its own)" for name in _vm_names(done.get("own"))]
    held += [f"{name} (its upstream's commits, not fetched here)" for name in _vm_names(done.get("upstream"))]
    held += [f"{name} (a worktree there has it)" for name in _vm_names(done.get("in_use"))]
    held += [f"tag {name} (another object there)" for name in _vm_names((applied.get("tags") or {}).get("differ"))]
    held += [f"{name} (the VM's own there)" for name in _vm_names(files.get("left"))]
    if held:
        said += f" Left there as it is: {_some(held, 'more')}."
    ahead = _vm_names(done.get("ahead"))
    if ahead:
        said += f" Ahead there, by a pull or a commit: {_some(ahead)}."
    missing = _vm_names(done.get("missing"))
    if missing:
        said += f" Not there, its commit did not arrive: {_some(missing)}"
        said += (
            ", which a shallow clone refuses for a branch behind its own: git fetch --unshallow there takes it."
            if plan.get("shallow")
            else "."
        )
    sent = sent or {}
    if sent.get("rc") not in (None, 0) and branches and len(missing) == len(branches):
        # Nothing arrived: what git said, but for its lines about each ref, which the apply's words cover.
        words = [line for line in str(sent.get("words") or "").splitlines() if line and not line.startswith(" ")]
        said += f" git send-pack failed: {one_line(' '.join(words[-2:]), 200)}"
        said += "" if said.endswith(".") else "."
    said += _files_said(files, sent)
    if applied.get("error"):
        said += f" The sync stopped part way there: {one_line(applied['error'], 200)}."
    included = plan.get("included") or []
    if included:
        said += f" Carried because .worktreeinclude names them, credentials by their names: {_some(included)}."
    left = [f"{item['what']} ({item['why']})" for item in plan.get("left") or []]
    if plan.get("stashes"):
        left.append(plural(plan["stashes"], "stash", "stashes"))
    if left:
        said += f" Left here: {_some(left, 'more things')}."
    return said + " repo_sync: false turns this off."


def _files_said(files, sent):
    """What of the files did not arrive, or was taken away, or could not be written: each said, with rsync's last words
    where it failed. What rsync and the VM printed is plain text."""
    said = ""
    if sent.get("files_rc") not in (None, 0):
        lines = [line for line in str(sent.get("files_words") or "").splitlines() if line.strip()]
        said += f" rsync could not send every file (exit {sent['files_rc']})"
        said += f": {one_line(lines[-1], 200)}." if lines else "."
    for key, words in (
        ("missing", "Not there, as they did not arrive as sent"),
        ("failed", "Not written there, as the VM would not let them be"),
        ("removed", "Taken away there, as they are gone here"),
    ):
        names = _vm_names(files.get(key))
        if names:
            said += f" {words}: {_some(sorted(names))}."
    return said


def repo_sync_next(plan, vm, project, applied=None, flag=""):
    """What NEXT STEPS keeps of a sync: "" while nothing the person must deal with stopped it, else the line - a branch
    with commits of its own there, one whose commits not here are its upstream's there, which this machine has not
    fetched, or a checkout with changes of its own - what to do, and the command that carries it again."""
    plan, applied = plan or {}, applied or {}
    if not plan.get("sync") or not applied:
        return ""
    done = applied.get("branches") or {}
    own, upstream = _vm_names(done.get("own")), _vm_names(done.get("upstream"))
    # By the apply's word for why, never its words.
    tree_own = not (applied.get("tree") or {}).get("brought") and (applied.get("tree") or {}).get("reason") == "own"
    if not own and not upstream and not tree_own:
        return ""
    held = [f"{_some(own)} there " + ("has commits of its own" if len(own) == 1 else "have commits of their own")]
    held = held if own else []
    if upstream:
        has = "has" if len(upstream) == 1 else "have"
        held.append(f"{_some(upstream)} there {has} commits of its upstream's that this machine has not fetched")
    held += ["its checkout there has changes of its own"] if tree_own else []
    many = len(own) + len(upstream) + tree_own > 1
    todo = ["pull those commits here"] if upstream else []
    todo += ["push or drop that work there"] if own or tree_own else []
    todo = and_list(todo)
    return (
        f"{project} on {vm}: {and_list(held)}, so the sync left {'them as they are' if many else 'it as it is'}."
        f" {todo[0].upper()}{todo[1:]}, then: just code {vm} --project {project}{flag}"
    )
