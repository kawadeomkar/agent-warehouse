#!/usr/bin/python
# Copyright (c) agent-warehouse contributors
# MIT License
"""Clone the repositories a VM does not have yet, several at once, each whole or not at all."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: git_clone_many
short_description: Clone repositories several at a time
version_added: "2.1.0"
description:
  - Clones each repository of O(repos) whose folder under O(dest_dir) does not exist yet, up to O(jobs) at a time.
  - A clone is made into C(<folder>.aw-partial) and renamed into place only once git has finished, so a folder
    named for a repository is always a whole checkout. A failed clone's partial folder is removed, and so is one an
    earlier run left behind when it was cut short.
  - A folder that exists is left as it is, whatever it holds.
  - Every repository is tried, and the module fails after the last one, naming each that failed.
options:
  repos:
    description:
      - The repositories, as M(github_repos) lists them. Each needs C(name), C(full_name), C(default_branch) and
        C(clone_url) or C(ssh_url), by O(protocol).
    type: list
    elements: dict
    required: true
  dest_dir:
    description: The folder each repository is cloned into, by its name.
    type: path
    required: true
  protocol:
    description: Which of each repository's URLs to clone.
    type: str
    choices: [https, ssh]
    default: https
  depth:
    description: Shallow clone depth. 0 clones the full history.
    type: int
    default: 0
  jobs:
    description: How many clones run at once.
    type: int
    default: 8
  timeout:
    description: Seconds one clone may take before it is ended and counted as failed. 0 waits as long as it takes.
    type: int
    default: 0
"""

RETURN = r"""
cloned:
  description: The full names of the repositories cloned, or that would be in check mode.
  type: list
  elements: str
  returned: always
skipped_existing:
  description: The full names of the repositories whose folder already existed.
  type: list
  elements: str
  returned: always
leftovers:
  description: The partial folders of an earlier run that were removed, or would be in check mode.
  type: list
  elements: str
  returned: always
not_cloned:
  description:
    - Each repository that could not be cloned, with git's exit status and the last line it printed. Not C(failed),
      which Ansible keeps for the task's own outcome.
  type: list
  elements: dict
  returned: always
"""

import os
import re
import shutil
import signal
import subprocess
from concurrent.futures import ThreadPoolExecutor

from ansible.module_utils.basic import AnsibleModule

PARTIAL = ".aw-partial"
# A folder name git and the projects folder can both take: no separator, no leading dot.
FOLDER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


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
    """Run a git command; None, or {rc, said} when it failed or ran out of time."""
    # A group of its own, so one that runs out of time is ended with every helper git started.
    process = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    )
    try:
        _, stderr = process.communicate(timeout=timeout or None)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        return {"rc": None, "said": f"took longer than {timeout} s"}
    if process.returncode != 0:
        return {"rc": process.returncode, "said": last_line(stderr.decode(errors="replace"))}
    return None


def clone(git, repo, depth, timeout):
    """Clone one repository into its partial folder, rename it into place, and fetch its submodules, as
    ansible.builtin.git does unless told not to; None, or what failed. A submodule that cannot be fetched is said and
    leaves the checkout, so the next run finds it there and goes on."""
    partial = repo["dest"] + PARTIAL
    argv = [git, "clone", "--quiet", "--branch", repo["branch"]]
    if depth > 0:
        argv += ["--depth", str(depth)]
    failure = git_run([*argv, "--", repo["url"], partial], timeout)
    if failure:
        if os.path.lexists(partial):
            remove(partial)
        return {"name": repo["name"], **failure}
    os.rename(partial, repo["dest"])
    if not os.path.exists(os.path.join(repo["dest"], ".gitmodules")):
        return None
    failure = git_run([git, "-C", repo["dest"], "submodule", "update", "--quiet", "--init", "--recursive"], timeout)
    if failure:
        return {"name": repo["name"], "rc": failure["rc"], "said": f"its submodules: {failure['said']}"}
    return None


def plan(repos, dest_dir, protocol):
    """Each repository as what to clone, or why it cannot be: (requests, refused)."""
    requests, refused = [], []
    for repo in repos:
        name, full_name = repo.get("name", ""), repo.get("full_name") or repo.get("name", "")
        url = repo.get("ssh_url" if protocol == "ssh" else "clone_url")
        if not FOLDER.fullmatch(name or ""):
            refused.append({"name": full_name, "rc": None, "said": "its name is not a folder name"})
        elif not url or not repo.get("default_branch"):
            refused.append({"name": full_name, "rc": None, "said": f"the listing gives no {protocol} URL or branch"})
        else:
            dest = os.path.join(dest_dir, name)
            requests.append({"name": full_name, "url": url, "dest": dest, "branch": repo["default_branch"]})
    return requests, refused


def run(module, git):
    params = module.params
    requests, not_cloned = plan(params["repos"], params["dest_dir"], params["protocol"])
    leftovers = [request["dest"] + PARTIAL for request in requests if os.path.lexists(request["dest"] + PARTIAL)]
    if not module.check_mode:
        for path in leftovers:
            remove(path)
    existing = [request for request in requests if os.path.lexists(request["dest"])]
    missing = [request for request in requests if not os.path.lexists(request["dest"])]
    cloned = []
    if missing and not module.check_mode:
        with ThreadPoolExecutor(max_workers=min(params["jobs"], len(missing))) as pool:
            outcomes = list(pool.map(lambda request: clone(git, request, params["depth"], params["timeout"]), missing))
        for request, outcome in zip(missing, outcomes, strict=True):
            if outcome:
                not_cloned.append(outcome)
            else:
                cloned.append(request["name"])
    elif missing:
        cloned = [request["name"] for request in missing]
    return {
        "changed": bool(cloned or leftovers),
        "cloned": cloned,
        "skipped_existing": [request["name"] for request in existing],
        "leftovers": leftovers,
        "not_cloned": not_cloned,
    }


def main():
    module = AnsibleModule(
        argument_spec=dict(
            repos=dict(type="list", elements="dict", required=True),
            dest_dir=dict(type="path", required=True),
            protocol=dict(type="str", choices=["https", "ssh"], default="https"),
            depth=dict(type="int", default=0),
            jobs=dict(type="int", default=8),
            timeout=dict(type="int", default=0),
        ),
        supports_check_mode=True,
    )
    if module.params["jobs"] < 1:
        module.fail_json(msg=f"jobs must be 1 or more, got {module.params['jobs']}")
    # In check mode on a VM the role has not set up yet, the folder is still to be made: everything would be cloned.
    if not os.path.isdir(module.params["dest_dir"]) and not module.check_mode:
        module.fail_json(msg=f"{module.params['dest_dir']} is not a folder")
    result = run(module, module.get_bin_path("git", required=True))
    if result["not_cloned"]:
        names = ", ".join(failure["name"] for failure in result["not_cloned"])
        count = len(result["not_cloned"])
        module.fail_json(msg=f"{count} of {len(module.params['repos'])} not cloned: {names}", **result)
    module.exit_json(**result)


if __name__ == "__main__":
    main()
