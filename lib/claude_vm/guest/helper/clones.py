"""The helper's verb `clones`: the checkouts of one machine's projects folder, cloned where missing and pulled where
asked, run by the machine's own python3 as its account (docs/architecture/engine.md §16.10).

A program for the machine, as main.py is: Python 3.10 and the standard library alone, never claude_vm. It is today's
roles/github_projects/library/git_clone_many.py in plain code, with what the role did around it beside: GitHub's
published SSH host keys put in the account's known_hosts, and the checkouts that were there before pulled.

What it is asked, a JSON object: `dir`, the projects folder, absolute; `repos`, each repository as the engine selected
it (`name`, `full_name`, `clone_url`, `ssh_url`, `default_branch`); `url`, which of each one's URLs is cloned
(`clone_url` over https, `ssh_url` over ssh); `depth`, a shallow clone's depth, 0 the whole history; `jobs`, how many
clones run at once; `timeout`, the seconds one git command may take, 0 none; `pull`, whether the checkouts that exist
are pulled; `known_hosts`, the key lines (`<type> <base64>`) to trust for github.com, none over https; and `check`,
true to change nothing and say what would change.

- A clone is made into `<folder>.aw-partial` and renamed into place only once git has finished, so a folder named for a
  repository is always a whole checkout; a failed clone's partial folder is removed, and so is one an earlier run left
  behind. A folder that exists is left as it is, whatever it holds: nothing removes a checkout. Every repository is
  tried, and each that failed is named with git's last line.
- A name that is no folder name (a separator, a leading dot) is refused and nothing written for it.
- A pull, only where asked and only of a checkout that was there before this run, and only of the repository the folder
  is: no link, its own work tree's top (never one around it), its origin the repository's clone_url or ssh_url as its
  config holds it, its HEAD the default branch, and no local change to what git tracks; anything else is named and
  left. The default branch is fetched and moved on by a fast-forward alone, so that a commit of the person's own is
  never lost, and a branch that cannot fast-forward is named and left.
- known_hosts gains each key line for github.com it lacks, and loses a line of github.com's alone of a type the keys
  name with another key, which is replaced; the file is written whole and renamed into place, 0600, its folder 0700.

git runs with prompts off (GIT_TERMINAL_PROMPT=0), each command in a process group of its own, so that one past its
time is ended with every helper it started. It prints one JSON document: `folder` (whether the projects folder is
there), `known_hosts` (whether it changed, or would), `cloned`, `skipped_existing`, `leftovers`, `not_cloned` (each
with git's status and its last line), `pulled` and `not_pulled`; or `error`, why nothing was done.
"""

from __future__ import annotations

import json
import os
import re
import shutil

PARTIAL = ".aw-partial"
# A folder name git and the projects folder can both take: no separator, no leading dot.
FOLDER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
# The host whose keys known_hosts is given, and a key line as GitHub publishes one: its type, then its base64.
HOST = "github.com"
KEY = re.compile(r"[a-z0-9@.-]+ [A-Za-z0-9+/]+={0,3}")
KNOWN_HOSTS = "~/.ssh/known_hosts"
URLS = ("clone_url", "ssh_url")


class Refused(Exception):
    """What the verb was asked is not what it takes: why."""


def remove(path):
    """Remove a file, a link or a folder at path; a link is removed, never followed."""
    if os.path.islink(path) or not os.path.isdir(path):
        os.unlink(path)
    else:
        shutil.rmtree(path)


def last_line(text):
    """The last non-empty line of text, which is where git says what went wrong."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def git_run(argv, timeout):
    """Run a git command: (None, its output), or ({rc, said}, "") where it failed or ran out of time."""
    import signal
    import subprocess

    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    # A group of its own, so one that runs out of time is ended with every helper git started.
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=env,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout or None)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        return {"rc": None, "said": f"took longer than {timeout} s"}, ""
    if process.returncode != 0:
        return {"rc": process.returncode, "said": last_line(stderr.decode(errors="replace"))}, ""
    return None, stdout.decode(errors="replace")


def clone(git, repo, depth, timeout):
    """Clone one repository into its partial folder, rename it into place, and fetch its submodules, as
    ansible.builtin.git does unless told not to; None, or what failed. A submodule that cannot be fetched is said and
    leaves the checkout, so the next run finds it there and goes on."""
    partial = repo["dest"] + PARTIAL
    argv = [git, "clone", "--quiet", "--branch", repo["branch"]]
    if depth > 0:
        argv += ["--depth", str(depth)]
    failure, _ = git_run([*argv, "--", repo["url"], partial], timeout)
    if failure:
        if os.path.lexists(partial):
            remove(partial)
        return {"name": repo["name"], **failure}
    os.rename(partial, repo["dest"])
    return _submodules(git, repo, timeout)


def _submodules(git, repo, timeout):
    if not os.path.exists(os.path.join(repo["dest"], ".gitmodules")):
        return None
    failure, _ = git_run([git, "-C", repo["dest"], "submodule", "update", "--quiet", "--init", "--recursive"], timeout)
    if failure:
        return {"name": repo["name"], "rc": failure["rc"], "said": f"its submodules: {failure['said']}"}
    return None


def _left(name, said):
    return {"name": name, "rc": None, "said": said}, False


def pull(git, repo, depth, timeout):
    """A checkout that was there before brought to its default branch's tip by a fast-forward alone: (None or what
    stopped it, whether it moved). Only the repository the folder is itself, never one git finds around it or through a
    link, whose origin is the repository's own URL as its config holds it, and which is on the default branch."""
    dest, name = repo["dest"], repo["name"]
    if os.path.islink(dest):
        return _left(name, "its folder is a link")
    failure, top = git_run([git, "-C", dest, "rev-parse", "--show-toplevel"], timeout)
    if failure or os.path.realpath(top.strip()) != os.path.realpath(dest):
        return _left(name, "its folder is no checkout of its own")
    # As the config holds it: `remote get-url` would give it as an insteadOf rewrites it.
    failure, origin = git_run([git, "-C", dest, "config", "--get", "remote.origin.url"], timeout)
    if failure or origin.strip() not in repo["origins"]:
        return _left(name, "its origin is not this repository")
    failure, head = git_run([git, "-C", dest, "symbolic-ref", "-q", "HEAD"], timeout)
    if failure or head.strip() != f"refs/heads/{repo['branch']}":
        return _left(name, f"it is not on {repo['branch']}")
    failure, changes = git_run([git, "-C", dest, "status", "--porcelain", "--untracked-files=no"], timeout)
    if failure:
        return {"name": name, **failure}, False
    if changes.strip():
        return _left(name, "it has local changes, which a pull would put at risk")
    _, before = git_run([git, "-C", dest, "rev-parse", "HEAD"], timeout)
    fetch = [git, "-C", dest, "fetch", "--quiet", "origin", repo["branch"]]
    if depth > 0:
        fetch += ["--depth", str(depth)]
    failure, _ = git_run(fetch, timeout)
    if failure:
        return {"name": name, **failure}, False
    failure, _ = git_run([git, "-C", dest, "merge", "--ff-only", "--quiet", "FETCH_HEAD"], timeout)
    if failure:
        return {"name": name, "rc": failure["rc"], "said": f"not a fast-forward: {failure['said']}"}, False
    _, after = git_run([git, "-C", dest, "rev-parse", "HEAD"], timeout)
    moved = before.strip() != after.strip()
    if moved:
        failure = _submodules(git, repo, timeout)
        if failure:
            return failure, True
    return None, moved


def plan(repos, dest_dir, field):
    """Each repository as what to clone, or why it cannot be: (requests, refused)."""
    requests, refused = [], []
    for repo in repos:
        name, full_name = repo.get("name", ""), repo.get("full_name") or repo.get("name", "")
        url = repo.get(field)
        if not FOLDER.fullmatch(name or ""):
            refused.append({"name": full_name, "rc": None, "said": "its name is not a folder name"})
        elif not url or not repo.get("default_branch"):
            refused.append({"name": full_name, "rc": None, "said": f"the listing gives no {field} or branch"})
        else:
            dest = os.path.join(dest_dir, name)
            origins = [one for one in (repo.get("clone_url"), repo.get("ssh_url")) if one]
            request = {"name": full_name, "url": url, "dest": dest, "branch": repo["default_branch"]}
            requests.append({**request, "origins": origins})
    return requests, refused


def known_hosts(keys, check):
    """The account's known_hosts given each key line for github.com: whether it changed, or would."""
    path = os.path.expanduser(KNOWN_HOSTS)
    lines = []
    if os.path.lexists(path):
        with open(path, "rb") as held:
            lines = held.read().decode("utf-8", "replace").splitlines()
    by_type = {key.split(" ")[0]: key for key in keys}
    kept, changed = [], False
    for line in lines:
        fields = line.split()
        other = len(fields) >= 3 and fields[0] == HOST and fields[1] in by_type
        if other and " ".join(fields[1:3]) != by_type[fields[1]]:
            changed = True  # another key of that type for github.com: replaced below
            continue
        kept.append(line)
    for key in keys:
        if not any(line.split()[:3] == [HOST, *key.split(" ")] for line in kept):
            kept.append(f"{HOST} {key}")
            changed = True
    if changed and not check:
        folder = os.path.dirname(path)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        partial = path + PARTIAL
        if os.path.lexists(partial):
            remove(partial)  # an earlier run's, or a link: removed, never followed
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as written:
            written.write("".join(f"{line}\n" for line in kept))
        os.chmod(partial, 0o600)
        os.replace(partial, path)
    return changed


def _held(asked):
    """asked held to what the verb takes; Refused naming what is not."""
    if not isinstance(asked.get("dir"), str) or not os.path.isabs(asked["dir"]):
        raise Refused("dir is the projects folder, an absolute path")
    if asked.get("url") not in URLS:
        raise Refused(f"url is one of {', '.join(URLS)}")
    for name, least in (("depth", 0), ("jobs", 1), ("timeout", 0)):
        value = asked.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < least:
            raise Refused(f"{name} is a whole number, {least} or more")
    for name in ("pull", "check"):
        if not isinstance(asked.get(name), bool):
            raise Refused(f"{name} is true or false")
    repos = asked.get("repos")
    if not isinstance(repos, list) or not all(isinstance(repo, dict) for repo in repos):
        raise Refused("repos is a list of repositories, each an object")
    keys = asked.get("known_hosts")
    if not isinstance(keys, list) or not all(isinstance(key, str) and KEY.fullmatch(key) for key in keys):
        raise Refused("known_hosts is a list of key lines, each its type and its base64")


def run(asked, git):
    """What the verb does, or would where asked["check"]: its document."""
    folder, check = asked["dir"], asked["check"]
    there = os.path.isdir(folder)
    if not there and not check:
        return {"error": f"{folder} is not a folder"}
    requests, not_cloned = plan(asked["repos"], folder, asked["url"])
    changed_hosts = known_hosts(asked["known_hosts"], check) if asked["known_hosts"] else False
    leftovers = [request["dest"] + PARTIAL for request in requests if os.path.lexists(request["dest"] + PARTIAL)]
    if not check:
        for path in leftovers:
            remove(path)
    existing = [request for request in requests if os.path.lexists(request["dest"])]
    missing = [request for request in requests if not os.path.lexists(request["dest"])]
    cloned, pulled, not_pulled = [], [], []
    if missing and not check:
        from concurrent.futures import ThreadPoolExecutor

        depth, timeout = asked["depth"], asked["timeout"]
        with ThreadPoolExecutor(max_workers=min(asked["jobs"], len(missing))) as pool:
            outcomes = list(pool.map(lambda request: clone(git, request, depth, timeout), missing))
        for request, outcome in zip(missing, outcomes):
            if outcome:
                not_cloned.append(outcome)
            else:
                cloned.append(request["name"])
    elif missing:
        cloned = [request["name"] for request in missing]
    if asked["pull"] and not check:
        for request in existing:
            failure, moved = pull(git, request, asked["depth"], asked["timeout"])
            if failure:
                not_pulled.append(failure)
            if moved:
                pulled.append(request["name"])
    return {
        "folder": there,
        "known_hosts": changed_hosts,
        "cloned": cloned,
        "skipped_existing": [request["name"] for request in existing],
        "leftovers": leftovers,
        "not_cloned": not_cloned,
        "pulled": pulled,
        "not_pulled": not_pulled,
    }


def main(asked):
    """The verb, run with asked: one JSON document on standard output."""
    try:
        _held(asked)
    except Refused as refused:
        print(json.dumps({"error": str(refused)}), flush=True)
        return
    git = shutil.which("git")
    if git is None:
        print(json.dumps({"error": "git is not installed on the machine"}), flush=True)
        return
    print(json.dumps(run(asked, git)), flush=True)
