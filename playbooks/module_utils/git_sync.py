# Copyright (c) agent-warehouse contributors
# MIT License
"""Carrying a git repository from this machine to its checkout on a VM: what playbooks/library/git_sync_info.py reads
at either end (docs/architecture/lifecycle.md §4.9).

Which files git does not track may go (select): the unit is the file, at any depth, since git lists a wholly ignored
folder as one entry, and a rule that read only that entry's name would carry a backup/ with a vault's password in it.
Every pattern is asked of git's own matcher (git ls-files --others --ignored -x), with letter case ignored, never of a
matcher written for this: .gitignore's syntax is not written again here, in the one place a miss sends a secret.

Every git command runs as git --no-optional-locks, as git_checkouts.py's do. Each function takes what runs a command,
an AnsibleModule, as ``module``. Standard library and git only, since it runs on the VM too.
"""

from __future__ import annotations

import os
import re

from ansible.module_utils.git_checkouts import COMMIT, GIT_ENV, USERINFO

# Tier 0, never, and nothing overrides it: what would hand a VM this project's own secrets or this machine's records,
# in .gitignore syntax. The patterns of tests/check_no_local_files.sh, less three - docs/, which is what a sync is
# asked for, and .env and *.log, which are tier 1 - and with all of .cache/, where the guard names six paths. Applied
# to every repository: the names are specific, and a fork or a copy of this project is covered without being
# recognised. tests/unit/test_repo_sync.py holds the two lists together over the same names.
NEVER = (
    ".vault_pass*",
    "**/group_vars/**/local*",
    "**/group_vars/**/.local*",
    "**/group_vars/**/vault*",
    "**/group_vars/**/.vault*",
    "**/group_vars/**/secret*",
    "**/group_vars/**/.secret*",
    "host_vars/",
    "hosts.y*ml",
    "hosts.json",
    "hosts.ini",
    "/hosts/",
    "inventory/**/hosts",
    "inventory/**/hosts.*",
    "inventory/**/*.ini",
    "inventory/**/*proxmox.y*ml",
    "local.y*ml",
    "local.json",
    "secret.y*ml",
    "secret.json",
    "secrets.y*ml",
    "secrets.json",
    "vault*.y*ml",
    "vault*.json",
    "proxmox-trust.pem",
    ".sessions/",
    ".logs/",
    ".cache/",
)
# Tier 1, left file by file unless the repository's .worktreeinclude names it - the file Claude Code reads to decide
# which ignored files a new worktree gets: credentials by their names, and derived trees, which are large and, built
# on macOS, mostly wrong on Linux.
CREDENTIALS = (
    ".env*",
    "*.pem",
    "*.key",
    "*.p12",
    "id_*",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials*",
    "secret*",
    "*.tfstate*",
    "*.tfvars",
    ".aws/",
    ".ssh/",
    ".gnupg/",
    ".kube/",
)
DERIVED = (
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    "*.pyc",
    "target/",
    "build/",
    "dist/",
    ".tox/",
    ".ansible/",
    ".molecule/",
    ".*_cache/",
    ".next/",
    ".gradle/",
    ".terraform/",
    ".vagrant/",
    ".DS_Store",
    "*.log",
)
INCLUDE_FILE = ".worktreeinclude"
# What a file starts with when it is an Ansible vault or a private key, whatever its name.
VAULT_START = b"$ANSIBLE_VAULT;"
KEY_START = b"-----BEGIN "
# What each reason a file is left is said as; the report names each.
WHY = {
    "never": "this project's own secrets and records, never carried",
    "secret": "it starts as a vault or a private key does",
    "tainted": "its folder holds a file that is never carried",
    "repository": "a repository inside the repository",
    "link": "a link, never followed",
    "name": "a control character, or no UTF-8, in its name",
    "credential": "a credential by its name, unless .worktreeinclude names it",
    "derived": "derived, unless .worktreeinclude names it",
    "excluded": "repo_sync_exclude",
    "decrypted": "committed as an Ansible vault, and decrypted here",
    "folder": "a folder here where git tracks a file or a submodule",
}
# The most entries a listing names as pathspecs: past it, git walks the whole tree, as it would without them.
MAX_PATHSPECS = 2000
# What a repository has under way here: the files git keeps while each is, by git rev-parse --git-path.
OPERATIONS = (
    ("MERGE_HEAD", "merge"),
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
    ("BISECT_LOG", "bisect"),
)


def git(module, top, *args, data=None):
    """git's exit code and its standard output as bytes, in ``top``."""
    rc, out, _ = module.run_command(
        ["git", "--no-optional-locks", "-C", top, *args],
        environ_update=GIT_ENV,
        expand_user_and_vars=False,
        encoding=None,
        data=data,
        binary_data=data is not None,
    )
    return rc, out


def text(module, top, *args):
    """git's output as text, stripped, or None when it failed."""
    rc, out = git(module, top, *args)
    return out.decode("utf-8", "replace").strip() if rc == 0 else None


def names(out):
    """The NUL-separated names of git's -z output, as bytes."""
    return [name for name in out.split(b"\0") if name]


def plain(name):
    """A name as text, or None for one with a control character or that is not UTF-8: it is said in a report, written
    to rsync's list one a line, and macOS's rsync has no --from0."""
    try:
        decoded = name.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return None if re.search(r"[\x00-\x1f\x7f]", decoded) else decoded


def listed(module, top, *args, which="--others", paths=None):
    """The files git does not track in ``top`` that ``args`` select, as bytes: a repository inside it (a worktree, a
    clone, a submodule not yet added) is one name ending in /, and git never looks inside. With ``which`` --cached,
    the index's files instead. With ``paths``, only under those names, each taken as it is spelled: git then never
    walks the rest of the tree."""
    if paths is not None and not paths:
        return set()
    within = ["--", *paths] if paths else []
    rc, out = git(
        module, top, "-c", "core.ignoreCase=true", "--literal-pathspecs", "ls-files", "-z", which, *args, *within
    )
    if rc != 0:
        module.fail_json(msg=f"git could not list the files of {top}.")
    return set(names(out))


def matching(module, top, patterns, which="--others", paths=None):
    """Which files git does not track in ``top`` (or with --cached, of its index) git's own matcher matches to
    ``patterns``, as bytes; with ``paths``, only under those."""
    if not patterns:
        return set()
    excludes = [f"--exclude={pattern}" for pattern in patterns]
    return listed(module, top, "--ignored", *excludes, which=which, paths=paths)


def secret_by_its_start(path):
    """Whether a file starts as an Ansible vault or a private key does, whatever its name."""
    try:
        with open(path, "rb") as handle:
            first = handle.read(256).split(b"\n", 1)[0]
    except OSError:
        return False
    return first.startswith(VAULT_START) or (first.startswith(KEY_START) and b"PRIVATE KEY" in first)


# A path a sync writes at, or names to the other end: relative, of plain components, never into .git.
FORBIDDEN_PARTS = ("", ".", "..", ".git")


def plain_path(path):
    """Whether ``path`` is one a sync writes at: relative, its parts plain, never into .git, no control character."""
    parts = path.split("/")
    return (
        bool(path)
        and not path.startswith("/")
        and not any(part in FORBIDDEN_PARTS for part in parts)
        and not any(ord(char) < 32 or ord(char) == 127 for char in path)
    )


def entry_index(entries):
    """What gives the entry git lists a name under: the outermost wholly ignored, or wholly new, folder of ``entries``
    that holds it - git lists a new folder and an ignored one inside it both, and the outer is the unit - or the name
    itself. By the name's own folders, one lookup each. The folders it knows are its ``folders``."""
    folders = frozenset(entry for entry in entries if entry.endswith(b"/"))

    def entry_of(name):
        end = name.find(b"/")
        while end != -1:
            if name[: end + 1] in folders:
                return name[: end + 1]
            end = name.find(b"/", end + 1)
        return name

    entry_of.folders = folders
    return entry_of


def holding(names, folders):
    """Which of ``folders`` (each ending in /) hold one of ``names``, at any depth: by each name's own folders."""
    found = set()
    for name in names:
        end = name.find(b"/")
        while end != -1:
            if name[: end + 1] in folders:
                found.add(name[: end + 1])
            end = name.find(b"/", end + 1)
    return found


def _has_include(top):
    include = os.path.join(top, INCLUDE_FILE)
    return os.path.isfile(include) and not os.path.islink(include)


def _matched(module, top, exclude, which, paths=None):
    """Each list's matches among ``which``'s files, under ``paths``: (never, credentials, derived, excluded, named)."""
    named = set()
    if _has_include(top):
        named = listed(module, top, "--ignored", f"--exclude-from={INCLUDE_FILE}", which=which, paths=paths)
    return (
        matching(module, top, NEVER, which, paths),
        matching(module, top, CREDENTIALS, which, paths),
        matching(module, top, DERIVED, which, paths),
        matching(module, top, list(exclude or ()), which, paths),
        named,
    )


def wholly_derived(module, top, entries):
    """The folders of ``entries`` every file of which is derived (a node_modules/, a .venv/), as git's matcher says:
    left whole, so never listed file by file - but for one that is wholly of tier 0 too (a .logs/ of *.log files),
    whose files are said as never carried. None while a .worktreeinclude could name a file inside one."""
    folders = sorted(entry for entry in entries if entry.endswith(b"/"))
    if not folders or len(folders) > MAX_PATHSPECS or _has_include(top):
        return set()

    # --directory: a folder all of whose files match is one name, and git does not look inside.
    def whole(patterns):
        excludes = [f"--exclude={pattern}" for pattern in patterns]
        return listed(module, top, "--ignored", "--directory", *excludes, paths=folders)

    return set(folders) & whole(DERIVED) - whole(NEVER)


def select(module, top, ignored_too, exclude, staged=()):
    """Which files git does not track in ``top`` may go: {name: (verdict, why)}, by name as bytes, each verdict
    "carried", "left" or "never" and ``why`` a key of WHY ("" when carried); what gives the entry git lists each under
    (entry_index); the files of tier 1 a .worktreeinclude let through, which the report names every run; and which
    names are new rather than ignored.

    The new files (untracked, not ignored) are always looked at, and the ignored ones only with ``ignored_too``.
    ``staged`` are the files staged and never committed, new files too, whose names are asked of the index. In order:
    tier 0 (NEVER, and a file's first bytes), never; a repository inside, an ignored link and a name that cannot be
    said, left; tier 1 (CREDENTIALS, DERIVED) unless .worktreeinclude names the file, left; ``exclude``
    (repo_sync_exclude), left. A wholly ignored or wholly new folder that holds a file of tier 0 anywhere inside is
    left whole: it is most likely a copy of a checkout. A folder git lists as one entry whose every file is derived -
    a node_modules/ of 200,000 files - is left whole as one name, never listed file by file: nothing in it goes.
    """
    entries = listed(module, top, "--exclude-standard", "--directory")
    if ignored_too:
        entries |= listed(module, top, "--ignored", "--exclude-standard", "--directory")
    # Every file git does not track is under one of the entries: listed under those still to judge, and nowhere else.
    few = len(entries) <= MAX_PATHSPECS
    skipped = wholly_derived(module, top, entries) if few else set()
    within = sorted(entries - skipped) if few else None
    new = listed(module, top, "--exclude-standard", paths=within)
    ignored = listed(module, top, "--ignored", "--exclude-standard", paths=within) if ignored_too else set()
    never, credentials, derived, excluded, named = _matched(module, top, exclude, "--others", within)
    staged = set(staged)
    if staged:
        others = (never, credentials, derived, excluded, named)
        cached = _matched(module, top, exclude, "--cached")
        never, credentials, derived, excluded, named = (
            found | (more & staged) for found, more in zip(others, cached, strict=True)
        )
        new |= staged
    verdicts = {}
    for name in sorted(ignored | new):
        path = os.path.join(top.encode(), name)
        if name.endswith(b"/"):
            verdicts[name] = ("left", "repository")
        elif plain(name) is None:
            verdicts[name] = ("left", "name")
        elif name in never:
            verdicts[name] = ("never", "never")
        elif os.path.islink(path):
            # A new link travels as its target's text, with the changes; an ignored one never goes.
            verdicts[name] = ("carried", "") if name in new else ("left", "link")
        elif name in credentials and name not in named:
            verdicts[name] = ("left", "credential")
        elif name in derived and name not in named:
            verdicts[name] = ("left", "derived")
        elif name in excluded:
            verdicts[name] = ("left", "excluded")
        else:
            verdicts[name] = ("carried", "")
    entry_of = entry_index(entries)
    of = {name: entry_of(name) for name in verdicts}
    # A file's first bytes, in every entry that would carry something: only there can one taint the rest.
    carrying = {of[name] for name, (verdict, _) in verdicts.items() if verdict == "carried"}
    for name, (verdict, why) in list(verdicts.items()):
        if verdict != "never" and why not in ("repository", "name", "link") and of[name] in carrying:
            if secret_by_its_start(os.path.join(top.encode(), name)):
                verdicts[name] = ("never", "secret")
    tainted = {of[name] for name, (verdict, _) in verdicts.items() if verdict == "never"}
    for name, (verdict, _) in list(verdicts.items()):
        if verdict == "carried" and of[name].endswith(b"/") and of[name] in tainted:
            verdicts[name] = ("left", "tainted")
    for entry in skipped:
        verdicts[entry] = ("left", "derived")
    return verdicts, entry_of, named & credentials, new


def operation(module, top):
    """What git has under way in ``top``'s worktree - a merge, a rebase, a cherry-pick, a revert, a bisect - or ""."""
    for marker, what in OPERATIONS:
        path = text(module, top, "rev-parse", "--git-path", marker)
        if path and os.path.lexists(os.path.join(top, path)):
            return what
    return ""


def refs(module, top, prefix):
    """{name: object} of the refs under ``prefix`` (refs/heads/), each name without it."""
    rc, out = git(module, top, "for-each-ref", "--format=%(refname)%00%(objectname)", prefix)
    found = {}
    for line in out.decode("utf-8", "replace").splitlines() if rc == 0 else ():
        ref, _, object_id = line.partition("\0")
        if ref.startswith(prefix) and COMMIT.fullmatch(object_id):
            found[ref[len(prefix) :]] = object_id
    return found


def head(module, top):
    """The branch HEAD is on ("" when detached or unborn) and its commit ("" when unborn)."""
    return {
        "branch": text(module, top, "symbolic-ref", "--quiet", "--short", "HEAD") or "",
        "commit": text(module, top, "rev-parse", "--verify", "--quiet", "HEAD^{commit}") or "",
    }


def checkout(module, path):
    """What any sync reads of a checkout: whether ``path`` is in one, and is its top level (``at_top``: a project's
    folder on the VM inside some other repository is no checkout of its own), its top level, origin and whether it is
    shallow, HEAD, and the commit of every ref, which a send leaves out of what it carries."""
    found = {"git": False, "at_top": False, "toplevel": "", "origin": "", "shallow": False}
    found.update(head={"branch": "", "commit": ""}, tips=[])
    if not os.path.isdir(path):
        return found
    toplevel = text(module, path, "rev-parse", "--show-toplevel")
    if not toplevel:
        return found
    origin = text(module, path, "config", "--get-all", "remote.origin.url") or ""
    found.update(
        git=True,
        at_top=os.path.realpath(path) == os.path.realpath(toplevel),
        toplevel=toplevel,
        # The first URL, the one git fetches from, as configured: never with what precedes the host, which can be a
        # token, and never as an insteadOf rewrites it, which names where git connects to, not the repository.
        origin=USERINFO.sub(r"\1", origin.splitlines()[0]) if origin else "",
        shallow=text(module, path, "rev-parse", "--is-shallow-repository") == "true",
        head=head(module, toplevel),
    )
    rc, out = git(module, toplevel, "for-each-ref", "--format=%(objectname) %(*objectname)")
    tips = set()
    for line in out.decode("utf-8", "replace").splitlines() if rc == 0 else ():
        tips.update(word for word in line.split() if COMMIT.fullmatch(word))
    found["tips"] = sorted(tips)
    return found
