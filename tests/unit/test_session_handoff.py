"""The pieces of just code's session handoff: lib/claude_vm/sessions.py, the modules in playbooks/library/ and
what they share, playbooks/module_utils/agent_warehouse.py.

Which session goes to which checkout on the VM, what a copy is made of, and
when an existing copy is left alone. Every transcript, ~/.claude and checkout
here is built for the test in its own directory, never the developer's; the
git checkouts are real ones, with origins that only look like GitHub's. The
window step that runs these (playbooks/tasks/session_read.yml and
session_write.yml) is run in test_code_session.py.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import time
from pathlib import Path

import pytest
from ansible.module_utils import agent_warehouse as shared
from conftest import run_main
from harness import _ran, _run, load_module

from claude_vm import sessions as filters

REPO = Path(__file__).resolve().parents[2]
info = load_module("playbooks/library/claude_sessions_info.py")
checkouts = load_module("playbooks/library/git_checkouts_info.py")
copy = load_module("playbooks/library/claude_sessions_copy.py")
fetch = load_module("playbooks/library/claude_sessions_fetch.py")

ID = "0a608ad8-1b2c-4d3e-8f90-123456789abc"
OTHER = "bc0e637b-2c3d-4e4f-9a01-23456789abcd"
HERE = "/Users/me/.claude"
MAC_MEMORY = f"{HERE}/projects/-Users-me-demo/memory/"
VM_MEMORY = "/home/dev/.claude/projects/-home-dev-projects-demo/memory/"
CHECKOUT = "/home/dev/projects/demo"
# What a tool printed, as a transcript holds it: it must never reach a run's output.
MARKER = "TRANSCRIPT-MARKER-6f1d2c"


def transcript(
    sid=ID,
    cwd="/Users/me/demo",
    *,
    version="2.1.308",
    branch="main",
    memory=MAC_MEMORY,
    worktree=None,
    conversation=True,
    partial=False,
    snapshot=True,
):
    """A transcript as Claude Code 2.1.308 writes one, compact JSON per line."""
    base = {"cwd": cwd, "sessionId": sid, "version": version, "gitBranch": branch, "userType": "external"}
    entries = []
    if snapshot:
        prompt = f"You have a persistent file-based memory at `{memory}`. This directory already exists."
        entries.append(
            {**base, "type": "attachment", "attachment": {"type": "prompt_snapshot", "systemPrompt": [prompt]}}
        )
    if conversation:
        entries.append({**base, "type": "user", "message": {"role": "user", "content": f"run the tests {MARKER}"}})
        entries.append(
            {**base, "type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}}
        )
    else:
        entries.append({**base, "type": "summary", "summary": "nothing"})
    if worktree is not None:
        entries.append({"type": "worktree-state", "worktreeSession": worktree, "sessionId": sid})
    text = "".join(json.dumps(entry, separators=(",", ":")) + "\n" for entry in entries)
    return (text + '{"type":"user","cwd":"/elsewh').encode() if partial else text.encode()


@pytest.fixture(autouse=True)
def _own_git_config(monkeypatch):
    # Never the developer's own, which changes what git says: status.showUntrackedFiles, a hook, an alias.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def lines(data):
    return [json.loads(line) for line in data.decode().splitlines()]


# ------------------------------------------------------------------------------------------ the filters ---
@pytest.mark.parametrize(
    ("path", "folder"),
    [
        ("/home/dev/projects/homeserver", "-home-dev-projects-homeserver"),
        ("/home/dev/projects/my_app.v2", "-home-dev-projects-my-app-v2"),
        ("/Users/Omkar/agent-warehouse", "-Users-Omkar-agent-warehouse"),
    ],
)
def test_the_folder_name_is_claude_code_s(path, folder):
    assert filters.claude_project_folder(path) == folder


def test_a_folder_name_claude_code_would_shorten_is_refused():
    with pytest.raises(ValueError, match="too long"):
        filters.claude_project_folder("/home/dev/projects/" + "x" * 190)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Octo/Demo.git",
        "https://github.com/octo/demo",
        "https://token@github.com/octo/demo/",
        "git@github.com:octo/demo.git",
        "ssh://git@github.com/octo/demo.git",
        "git@github-work:octo/demo",
        "/srv/git/octo/demo.git",
    ],
)
def test_an_origin_names_owner_and_repository_in_every_form(url):
    assert filters.git_repository(url) == "octo/demo"


@pytest.mark.parametrize("url", ["", "demo", "https://github.com/", "git@github.com:demo"])
def test_an_origin_with_no_owner_names_nothing(url):
    assert filters.git_repository(url) == ""


def test_the_dirs_asked_about_are_each_first_directory_and_its_worktree_s_checkout():
    sessions = [
        {"cwd": "/Users/me/demo"},
        {"cwd": "/Users/me/demo/.claude/worktrees/fix-x/src"},
        {"cwd": ""},
        {"cwd": "/Users/me/demo"},
        # A worktree of a checkout no session ran in: the checkout is asked about too.
        {"cwd": "/Users/me/other/.claude/worktrees/fix-y"},
    ]
    assert filters.claude_session_dirs(sessions) == [
        "/Users/me/demo",
        "/Users/me/demo/.claude/worktrees/fix-x/src",
        "/Users/me/other/.claude/worktrees/fix-y",
        "/Users/me/other",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Octo/Demo.git",
        "https://x-access-token@github.com/octo/demo/",
        "git@github.com:octo/demo.git",
        "github.com:octo/demo",
        "ssh://git@github.com/octo/demo.git",
        "ssh://git@ssh.github.com:443/octo/demo.git",
    ],
)
def test_on_the_vm_a_github_origin_names_its_repository(url):
    assert filters.github_repository(url) == "octo/demo"


@pytest.mark.parametrize(
    "url",
    [
        "/srv/git/octo/demo.git",  # a local path names folders, not a repository
        "file:///srv/git/octo/demo.git",
        "https://gitlab.com/octo/demo.git",  # another host's repository of the same name
        "git@github-work:octo/demo",  # an SSH alias: the VM's checkouts are just deploy's, which uses none
        "https://github.com.example.com/octo/demo",
        "https://github.com/octo/demo/tree/main",
        "https://github.com/../demo",
        "",
    ],
)
def test_on_the_vm_no_other_origin_names_a_repository(url):
    assert filters.github_repository(url) == ""


def test_a_vm_checkout_names_its_repository_only_by_a_github_origin():
    # The VM could otherwise claim any repository of this machine's by a path ending in its owner and name.
    here = [{"path": "/Users/me/demo", "exists": True, "origin": "git@github-work:octo/demo.git"}]
    claimed = [{"path": CHECKOUT, "name": "demo", "origin": "/srv/git/octo/demo.git"}]
    assert _plan([_session()], here, claimed)["sessions"] == []
    assert [item["checkout"] for item in _plan([_session()], here)["sessions"]] == [CHECKOUT]


def test_just_code_s_own_directory_on_the_vm_is_one_definition_for_code_and_destroy():
    assert filters.just_code_dir("/home/dev/projects/") == "/home/dev/projects/.agent-warehouse"
    assert filters.just_code_dir("/home/dev/projects", "/home/dev/workspaces/") == "/home/dev/workspaces"
    # By its 1.x name, which code.yml moves it from: the default's only, never a directory editor_files_dir names.
    assert filters.just_code_dir_1x("/home/dev/projects/") == "/home/dev/projects/.claude-on-proxmox"
    assert filters.just_code_dir_1x("/home/dev/projects", "/home/dev/workspaces/") == ""
    # code.yml and session_save.yml both read it through the filter, from the same setting: its name from 1.1, else
    # its name in 1.0.
    shared = "| just_code_dir(editor_files_dir | default(vscode_workspaces_dir | default('', true)))"
    for path in ("playbooks/code.yml", "playbooks/tasks/session_save.yml"):
        assert shared in (REPO / path).read_text(), path


def test_the_projects_directory_is_one_definition_for_every_reader():
    assert filters.projects_dir("", "/home/dev") == "/home/dev/projects"
    assert filters.projects_dir(None, "/home/dev/") == "/home/dev/projects"
    # vm_projects_dir as written, but for a trailing "/": configure.yml's alias line said "(opens /srv/code/)"
    # where just code and just status said /srv/code.
    assert filters.projects_dir("/srv/code/", "/home/dev") == "/srv/code"
    assert filters.projects_dir("/srv/code", "") == "/srv/code"
    assert filters.projects_dir("/", "/home/dev") == "/"
    # Each reader names it through the filter, and none adds /projects to a home of its own.
    # just status's probe holds its copy of the one line, which test_status_vms.py holds to this function.
    readers = (
        *(f"playbooks/{stem}.yml" for stem in ("configure", "code")),
        "playbooks/tasks/session_save.yml",
        "roles/claude_code/vars/main.yml",
        "roles/github_projects/tasks/main.yml",
    )
    for path in readers:
        text = (REPO / path).read_text()
        assert "| projects_dir(" in text and "~ '/projects'" not in text, path


def test_the_sessions_left_out_are_said_list_by_list_by_their_first_eight_characters():
    rows = [[" Kept: ", [ID, OTHER], "."], [" Not copied: ", [], "."], [" Open: ", [OTHER], "; why."]]
    assert filters.claude_sessions_listed(rows) == f" Kept: {ID[:8]}, {OTHER[:8]}. Open: {OTHER[:8]}; why."
    assert filters.claude_sessions_listed([[" Kept: ", [], "."]]) == ""


def _session(sid=ID, cwd="/Users/me/demo", **extra):
    return {
        "id": sid,
        "folder": "-Users-me-demo",
        "cwd": cwd,
        "version": "2.1.308",
        "branch": "main",
        "conversation": True,
        "open": 0,
        "tools": False,
        **extra,
    }


THERE = [{"path": CHECKOUT, "name": "demo", "origin": "https://github.com/octo/demo.git"}]
# This machine's checkout of the same repository.
HERE_DEMO = [{"path": "/Users/me/demo", "exists": True, "origin": "https://github.com/octo/demo.git"}]


def _plan(sessions, here, there=THERE, version="2.1.308", repo_paths=None, **kwargs):
    return filters.claude_sessions_plan(sessions, here, there, version, repo_paths or {}, "/home/dev/.claude", **kwargs)


def _placed(plan):
    """The sessions a plan copies, and those it leaves out for a reason just code says."""
    return plan["sessions"] + plan["newer"] + plan["ambiguous"] + plan["twice"] + plan["open"]


def test_a_session_goes_to_the_checkout_whose_origin_names_its_repository():
    here = [{"path": "/Users/me/demo", "exists": True, "origin": "git@github.com:Octo/Demo.git"}]
    plan = _plan([_session(tools=True)], here)
    assert plan["sessions"] == [{"id": ID, "folder": "-Users-me-demo", "checkout": CHECKOUT, "memory": VM_MEMORY}]
    assert plan["files"] == [f"-Users-me-demo/{ID}.jsonl", f"-Users-me-demo/{ID}/tool-results"]
    assert plan["repositories"] == ["octo/demo"]
    assert plan["over"] == [] and plan["bytes"] == 0 and plan["over_bytes"] == 0


THIRD = "f0e1d2c3-b4a5-4697-8a7b-6c5d4e3f2a1b"


def test_one_copy_sends_the_newest_sessions_that_fit_in_the_bound_and_says_the_rest():
    # Newest first, each kept while it fits: the 200-byte one does not, the older 50-byte one still does.
    sessions = [
        _session(ID, mtime=300.0, size=100),
        _session(OTHER, mtime=200.0, size=200),
        _session(THIRD, mtime=100.0, size=50),
    ]
    plan = _plan(sessions, HERE_DEMO, cap=160)
    assert [item["id"] for item in plan["sessions"]] == [ID, THIRD]
    assert plan["files"] == [f"-Users-me-demo/{ID}.jsonl", f"-Users-me-demo/{THIRD}.jsonl"]
    assert plan["over"] == [OTHER] and plan["bytes"] == 150 and plan["over_bytes"] == 200
    unbounded = _plan(sessions, HERE_DEMO)
    assert len(unbounded["sessions"]) == 3 and unbounded["over"] == [] and unbounded["bytes"] == 350


def test_the_session_asked_for_is_sent_whatever_the_bound_and_counts_first():
    sessions = [_session(ID, mtime=100.0, size=500), _session(OTHER, mtime=300.0, size=100)]
    plan = _plan(sessions, HERE_DEMO, wanted=ID, cap=200)
    assert [item["id"] for item in plan["sessions"]] == [ID] and plan["over"] == [OTHER]
    assert plan["bytes"] == 500 and plan["over_bytes"] == 100


def test_a_session_s_subagents_go_with_it_and_count_toward_the_bound_while_they_are_copied():
    sessions = [
        _session(ID, mtime=300.0, size=100, subagents=True, subagents_size=500),
        _session(OTHER, mtime=200.0, size=100, subagents=False, subagents_size=0),
    ]
    plan = _plan(sessions, HERE_DEMO)
    assert plan["files"] == [
        f"-Users-me-demo/{ID}.jsonl",
        f"-Users-me-demo/{ID}/subagents",
        f"-Users-me-demo/{OTHER}.jsonl",
    ]
    assert plan["bytes"] == 700
    # Its subagents' bytes are its own: with them the newest takes 600 of 650, and the older one stays here.
    bounded = _plan(sessions, HERE_DEMO, cap=650)
    assert bounded["bytes"] == 600 and bounded["over"] == [OTHER] and bounded["over_bytes"] == 100
    # Without them: neither sent nor counted.
    without = _plan(sessions, HERE_DEMO, cap=650, subagents=False)
    assert without["files"] == [f"-Users-me-demo/{ID}.jsonl", f"-Users-me-demo/{OTHER}.jsonl"]
    assert without["bytes"] == 200 and without["over"] == []


def _sent(sid, transcript, size, subagents=0):
    return {f"-Users-me-demo/{sid}.jsonl": {"transcript": transcript, "size": size, "subagents_size": subagents}}


def test_the_bound_counts_what_the_vm_s_mirror_lacks_of_each_session():
    # A long session copied before costs what it grew by: within a bound its whole size is past.
    grown = _session(ID, mtime=300.0, transcript=900, size=1000, subagents=True, subagents_size=500)
    plan = _plan([grown], HERE_DEMO, cap=200, mirrored=_sent(ID, 850, 950, 450))
    assert plan["over"] == [] and plan["bytes"] == 50 + 50
    # Never sent: the whole of it.
    plan = _plan([grown], HERE_DEMO, cap=200, mirrored={})
    assert plan["over"] == [ID] and plan["over_bytes"] == 1500
    # A transcript shorter here than there was written anew: it counts whole, its subagents by their growth.
    plan = _plan([grown], HERE_DEMO, cap=0, mirrored=_sent(ID, 950, 950, 500))
    assert plan["bytes"] == 1000
    # Already all there: nothing.
    assert _plan([grown], HERE_DEMO, mirrored=_sent(ID, 900, 1000, 500))["bytes"] == 0
    # Without subagents, theirs is not counted, here or there.
    assert _plan([grown], HERE_DEMO, mirrored=_sent(ID, 850, 950, 0), subagents=False)["bytes"] == 50


def test_what_the_vm_says_its_mirror_holds_counts_for_no_more_than_this_machine_holds():
    grown = _session(ID, mtime=300.0, transcript=900, size=1000, subagents=True, subagents_size=500)
    # A mirror made to look huge: its tool results and subagents count for what they are here at most, and its
    # transcript, shorter than this one, for what it says - never the transcript's growth here.
    plan = _plan([grown], HERE_DEMO, mirrored=_sent(ID, 0, 10**12, 10**12))
    assert plan["bytes"] == 900
    # A value that is no count counts for nothing, and ends nothing.
    for value in ("950", float("inf"), float("nan"), True, None, [1], -5):
        odd = {f"-Users-me-demo/{ID}.jsonl": {"transcript": value, "size": value, "subagents_size": value}}
        assert _plan([grown], HERE_DEMO, mirrored=odd)["bytes"] == 1500, value


def test_a_session_s_copy_in_the_mirror_stays_whatever_leaves_it_out_this_time():
    sessions = [
        _session(ID, mtime=300.0, size=100),
        _session(OTHER, mtime=200.0, size=500),
        _session(THIRD, version="9.9.9"),
        _session("a1b2c3d4-0000-4000-8000-000000000001", cwd="/Users/me/elsewhere"),
    ]
    plan = _plan(sessions, HERE_DEMO, cap=200, open_there=[ID])
    assert plan["open"] == [ID] and plan["over"] == [OTHER] and plan["newer"] == [THIRD]
    # Each of a repository the VM has, copied or not; never one of a repository it lacks.
    assert sorted(plan["mirror"]) == sorted(f"-Users-me-demo/{sid}" for sid in (ID, OTHER, THIRD))
    # Without Claude Code there, none is copied, and none leaves the mirror.
    assert sorted(_plan(sessions, HERE_DEMO, version="")["mirror"]) == sorted(plan["mirror"])


def test_a_mirror_entry_of_another_session_or_folder_counts_nothing_out():
    session = _session(ID, mtime=300.0, transcript=100, size=100)
    other = {f"-Users-me-other/{ID}.jsonl": {"transcript": 100, "size": 100}, **_sent(OTHER, 100, 100)}
    assert _plan([session], HERE_DEMO, mirrored=other)["bytes"] == 100


@pytest.mark.parametrize(("size", "cap"), [("256M", 256 * 1024**2), ("1G", 1024**3), ("2", 2 * 1024**3), ("0", 0)])
def test_the_bound_is_a_size_as_the_settings_write_one(size, cap):
    assert filters.claude_sessions_cap(size) == cap


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/someone-else/demo.git",  # another owner's repository of the same name
        "https://github.com/octo/demo-api.git",  # a name that only starts the same
        "",  # no origin
    ],
)
def test_another_repository_is_not_copied(origin):
    here = [{"path": "/Users/me/demo", "exists": True, "origin": origin}]
    assert _placed(_plan([_session()], here)) == []


def test_a_folder_named_like_a_checkout_of_another_repository_does_not_match():
    # The VM's demo folder holds someone else's demo: the name alone says nothing.
    here = HERE_DEMO
    there = [{"path": CHECKOUT, "name": "demo", "origin": "https://github.com/someone-else/demo.git"}]
    assert _plan([_session()], here, there)["sessions"] == []


def test_a_renamed_clone_matches_by_origin():
    here = [{"path": "/Users/me/discord-music-bot-2", "exists": True, "origin": "https://github.com/octo/demo"}]
    plan = _plan([_session(cwd="/Users/me/discord-music-bot-2")], here)
    assert [item["checkout"] for item in plan["sessions"]] == [CHECKOUT]


def test_a_gone_worktree_resolves_through_its_checkout():
    cwd = "/Users/me/demo/.claude/worktrees/fix-x"
    here = [
        {"path": cwd, "exists": False, "origin": ""},
        *HERE_DEMO,
    ]
    assert [item["id"] for item in _plan([_session(cwd=cwd)], here)["sessions"]] == [ID]


def test_a_gone_clone_resolves_through_github_repo_paths_and_nothing_else_does():
    here = [{"path": "/Users/me/old-demo", "exists": False, "origin": ""}]
    listed = {"Octo/Demo": ["/Users/me/old-demo"]}
    assert len(_plan([_session(cwd="/Users/me/old-demo")], here, repo_paths=listed)["sessions"]) == 1
    assert _placed(_plan([_session(cwd="/Users/me/old-demo")], here)) == []


def test_a_session_with_no_directory_or_no_message_is_not_copied():
    assert _placed(_plan([_session(cwd=""), _session(OTHER, conversation=False)], [])) == []


def test_a_session_newer_than_the_vm_s_claude_is_left_and_named():
    here = HERE_DEMO
    plan = _plan([_session(version="2.1.310"), _session(OTHER, version="2.1.9")], here, version="2.1.308")
    assert plan["newer"] == [ID] and [item["id"] for item in plan["sessions"]] == [OTHER]


def test_the_newest_claude_code_the_newer_sessions_need_is_named():
    sessions = [_session(ID, version="2.1.400"), _session(OTHER, version="2.1.350"), _session(THIRD, version="2.1.1")]
    plan = _plan(sessions, HERE_DEMO, version="2.1.308")
    assert sorted(plan["newer"]) == sorted([ID, OTHER]) and plan["newer_version"] == "2.1.400"
    assert _plan([_session(ID)], HERE_DEMO)["newer_version"] == ""


def test_without_claude_on_the_vm_nothing_is_copied():
    here = HERE_DEMO
    plan = _plan([_session()], here, version="")
    assert plan["sessions"] == [] and plan["claude"] is False


def test_two_checkouts_of_one_repository_take_the_one_named_after_it_or_neither():
    here = HERE_DEMO
    both = [*THERE, {"path": "/home/dev/projects/demo-copy", "name": "demo-copy", "origin": "git@github.com:octo/demo"}]
    assert [item["checkout"] for item in _plan([_session()], here, both)["sessions"]] == [CHECKOUT]
    neither = [{**item, "name": item["name"] + "-x", "path": item["path"] + "-x"} for item in both]
    plan = _plan([_session()], here, neither)
    assert plan["sessions"] == [] and plan["ambiguous"] == [
        {"id": ID, "repository": "octo/demo", "names": "demo-copy-x, demo-x"}
    ]


@pytest.mark.parametrize(
    ("sessions", "kwargs", "problem"),
    [
        ([], {}, f"There is no session {ID} on this machine"),
        ([_session(), {**_session(), "folder": "-Users-me-demo2"}], {}, "is in more than one folder here"),
        ([_session(conversation=False)], {}, "has no messages to continue"),
        ([_session(open=4242)], {}, "is open on this machine (claude, pid 4242)"),
        ([_session(cwd="/Users/me/elsewhere")], {}, "belongs to no repository alpha could have"),
        ([_session(version="2.2.0")], {}, "newer than alpha's 2.1.308"),
        ([_session(branch="HEAD")], {}, "a detached HEAD"),
        ([_session()], {"project": "other"}, "--project other on alpha is not a checkout of octo/demo; demo is."),
        ([_session()], {"version": ""}, "Claude Code is not installed on alpha yet"),
    ],
)
def test_session_is_refused_with_its_reason(sessions, kwargs, problem):
    version = kwargs.pop("version", "2.1.308")
    plan = _plan(sessions, HERE_DEMO, version=version, wanted=ID, vm="alpha", **kwargs)
    assert problem in plan["session"]["problem"]


def test_session_outside_the_window_is_planned_and_names_its_checkout():
    plan = _plan([_session(branch="feat/x")], HERE_DEMO, wanted=ID, project="demo", vm="alpha")
    assert plan["session"] == {
        "id": ID,
        "folder": "-Users-me-demo",
        "cwd": "/Users/me/demo",
        "dir": "/Users/me/demo",
        "how": "origin",
        "repository": "octo/demo",
        "branch": "feat/x",
        "checkout": CHECKOUT,
        "name": "demo",
        "problem": "",
    }
    assert [item["id"] for item in plan["sessions"]] == [ID]


def test_an_id_in_two_folders_here_is_left_out_and_named():
    # claude --resume <id> resumes from neither once both are on the VM.
    twice = {**_session(), "folder": "-Users-me-demo-src"}
    plan = _plan([_session(), twice, _session(OTHER)], HERE_DEMO)
    assert plan["twice"] == [ID] and [item["id"] for item in plan["sessions"]] == [OTHER]
    # --session's own is refused with its reason instead.
    assert "is in more than one folder here" in _plan([_session(), twice], HERE_DEMO, wanted=ID)["session"]["problem"]


def test_a_session_a_claude_on_the_vm_has_open_is_left_as_it_is():
    # Replaced under the claude holding it, the copy would take that claude's next lines onto other content.
    plan = _plan([_session(), _session(OTHER)], HERE_DEMO, open_there=[ID])
    assert plan["open"] == [ID] and [item["id"] for item in plan["sessions"]] == [OTHER]
    # --session's own stays planned: session_read.yml refuses it, saying where it is open.
    assert [item["id"] for item in _plan([_session()], HERE_DEMO, wanted=ID, open_there=[ID])["sessions"]] == [ID]


def test_a_vm_without_the_repository_is_named_with_the_run_that_clones_it():
    plan = _plan([_session()], HERE_DEMO, there=[], wanted=ID, vm="alpha")
    problem = plan["session"]["problem"]
    assert problem.startswith("alpha has no checkout of octo/demo. just deploy alpha --only configure")


# ------------------------------------------------------------------------ claude_sessions_info ---
def _claude_dir(tmp_path, files, *, old=()):
    """A ~/.claude with these transcripts: {(folder, id): bytes}; the ``old`` ones written 40 days ago."""
    root = tmp_path / ".claude"
    root.mkdir(parents=True, exist_ok=True)
    for (folder, sid), data in files.items():
        path = root / "projects" / folder / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if sid in old:
            then = time.time() - 40 * 86400
            os.utime(path, (then, then))
    return root


def test_a_transcript_is_summarized_and_nothing_it_says_is_kept(tmp_path):
    data = transcript(branch="feat/x", partial=True)
    root = _claude_dir(tmp_path, {("-Users-me-demo", ID): data})
    (root / "projects" / "-Users-me-demo" / ID / "tool-results").mkdir(parents=True)
    found = info.list_sessions(str(root), 30, [])
    assert found == [
        {
            "id": ID,
            "folder": "-Users-me-demo",
            "mtime": found[0]["mtime"],
            "open": 0,
            "transcript": len(data),
            "tools": True,
            "size": len(data),
            "subagents": False,
            "subagents_size": 0,
            "dir": True,
            "recorded": False,
            "copied": False,
            "cwd": "/Users/me/demo",
            "version": "2.1.308",
            "branch": "feat/x",
            "conversation": True,
        }
    ]
    assert MARKER not in json.dumps(found)


def test_a_session_s_size_is_its_transcript_and_tool_results_and_follows_no_link(tmp_path):
    data = transcript()
    root = _claude_dir(tmp_path, {("-Users-me-demo", ID): data})
    results = root / "projects" / "-Users-me-demo" / ID / "tool-results"
    (results / "deep").mkdir(parents=True)
    (results / "a.txt").write_bytes(b"x" * 1000)
    (results / "deep" / "b.txt").write_bytes(b"y" * 24)
    outside = tmp_path / "outside"
    outside.write_bytes(b"z" * 10_000)
    (results / "link").symlink_to(outside)
    (results / "folder-link").symlink_to(tmp_path, target_is_directory=True)
    (found,) = info.list_sessions(str(root), 30, [])
    assert found["size"] == len(data) + 1024


def test_a_session_s_subagents_are_found_and_sized_and_a_link_to_a_folder_is_none(tmp_path):
    root = _claude_dir(tmp_path, {("-Users-me-demo", ID): transcript(), ("-Users-me-demo", OTHER): transcript(OTHER)})
    agents = root / "projects" / "-Users-me-demo" / ID / "subagents"
    agents.mkdir(parents=True)
    (agents / "agent-a1.jsonl").write_bytes(b"x" * 300)
    (agents / "agent-a1.meta.json").write_bytes(b"{}" * 10)
    (root / "projects" / "-Users-me-demo" / OTHER).mkdir()
    (root / "projects" / "-Users-me-demo" / OTHER / "subagents").symlink_to(agents, target_is_directory=True)
    found = {item["id"]: item for item in info.list_sessions(str(root), 30, [])}
    assert found[ID]["subagents"] is True and found[ID]["subagents_size"] == 320
    assert found[ID]["size"] == len(transcript())
    assert found[OTHER]["subagents"] is False and found[OTHER]["subagents_size"] == 0


def test_the_mirror_says_what_it_holds_of_each_session_and_nothing_else(tmp_path):
    mirror = tmp_path / "mirror"
    (mirror / "-Users-me-demo" / ID / "tool-results").mkdir(parents=True)
    (mirror / "-Users-me-demo" / ID / "subagents").mkdir()
    (mirror / "-Users-me-demo" / f"{ID}.jsonl").write_bytes(b"x" * 100)
    (mirror / "-Users-me-demo" / ID / "tool-results" / "t.txt").write_bytes(b"y" * 20)
    (mirror / "-Users-me-demo" / ID / "subagents" / "agent-a.jsonl").write_bytes(b"z" * 7)
    (mirror / "-Users-me-demo" / "notes.txt").write_text("not a session\n")
    (mirror / "-Users-me-demo" / f"{OTHER}.jsonl").symlink_to(mirror / "-Users-me-demo" / f"{ID}.jsonl")
    assert info.mirrored(str(mirror)) == {
        f"-Users-me-demo/{ID}.jsonl": {"transcript": 100, "size": 120, "subagents_size": 7}
    }
    assert info.mirrored(str(tmp_path / "none")) == {}


def test_a_sparse_file_in_the_mirror_counts_the_bytes_it_holds(tmp_path):
    mirror = tmp_path / "mirror"
    (mirror / "-Users-me-demo" / ID / "tool-results").mkdir(parents=True)
    (mirror / "-Users-me-demo" / f"{ID}.jsonl").write_bytes(b"x" * 100)
    with open(mirror / "-Users-me-demo" / ID / "tool-results" / "t.txt", "wb") as handle:
        handle.truncate(1024**3)
    found = info.mirrored(str(mirror))[f"-Users-me-demo/{ID}.jsonl"]
    assert found["transcript"] == 100 and found["size"] < 1024**2


def test_the_newest_version_is_kept_whatever_its_place(tmp_path):
    data = transcript(version="2.1.310") + transcript(version="2.1.9")
    root = _claude_dir(tmp_path, {("-Users-me-demo", ID): data})
    assert info.list_sessions(str(root), 30, [])[0]["version"] == "2.1.310"


def _line_by_line(path):
    """What summarize said before transcripts were read in parts: each complete line in order, in one pass."""
    found = {"cwd": "", "version": "", "branch": "", "conversation": False}
    with open(path, "rb") as handle:
        for line in handle:
            if not line.endswith(b"\n"):
                break
            entry = shared.transcript_entry(line)
            cwd, version, branch = entry.get("cwd"), entry.get("version"), entry.get("gitBranch")
            if not found["cwd"] and isinstance(cwd, str):
                found["cwd"] = cwd
            if isinstance(version, str) and info.VERSION.fullmatch(version):
                if not found["version"] or shared.version_key(version) > shared.version_key(found["version"]):
                    found["version"] = version
            if isinstance(branch, str) and branch:
                found["branch"] = branch
            if entry.get("type") in ("user", "assistant"):
                found["conversation"] = True
    return found


def _varied_transcripts(directory):
    """Transcripts whose four values change part way, of lines of every length: an empty cwd first, versions equal by
    their numbers but not as text, a version that is no version, lines that are no object or no JSON, a branch that
    goes empty, a last line cut short, a file of one cut line, and an empty one."""
    directory.mkdir()
    paths = []
    for index in range(5):
        lines = [b'{"type":"summary","cwd":""}\n', b"[1]\n", b"not json\n"]
        for n in range(30 + 17 * index):
            entry = {"type": "user" if n % 11 == 7 + index else "progress", "pad": "x" * (n * 7 % 61)}
            if n % 6 == 2:
                entry["cwd"] = f"/Users/me/dir{index}-{n}"
            if n % 4 == 1:
                entry["version"] = ["2.1.30", "2.01.30", "2.1.9", "latest", f"2.1.{n}"][(n + index) % 5]
            if n % 5 == 3:
                entry["gitBranch"] = f"b{n}" if n % 3 else ""
            lines.append(json.dumps(entry, separators=(",", ":")).encode() + b"\n")
        if index % 2:
            lines.append(b'{"type":"assistant","cwd":"/cut","version":"9.9.9"')
        path = directory / f"{index}.jsonl"
        path.write_bytes(b"".join(lines))
        paths.append(str(path))
    for name, data in (("cut.jsonl", b'{"type":"user","cwd":"/x"'), ("empty.jsonl", b"")):
        (directory / name).write_bytes(data)
        paths.append(str(directory / name))
    return paths


@pytest.mark.parametrize("part", [1, 5, 64, 1000, 1 << 20])
def test_a_transcript_read_in_parts_says_what_it_says_read_line_by_line(tmp_path, monkeypatch, part):
    # One reader: the parts are cut and put together as several would, without forking pytest's own process.
    paths = _varied_transcripts(tmp_path / "t")
    monkeypatch.setattr(info, "PART", part)
    gone = str(tmp_path / "gone.jsonl")
    assert info.summaries([*paths, gone], readers=1) == [*(_line_by_line(path) for path in paths), None]
    assert [info.summarize(path) for path in paths] == [_line_by_line(path) for path in paths]
    with pytest.raises(OSError):
        info.summarize(gone)


def test_where_the_readers_cannot_be_had_this_process_reads_every_part_and_says_why(tmp_path, monkeypatch):
    paths = _varied_transcripts(tmp_path / "t")
    monkeypatch.setattr(info, "PART", 64)

    def refused(*_args, **_kwargs):
        raise OSError("no processes")

    monkeypatch.setattr(info, "ProcessPoolExecutor", refused)
    said = []
    assert info.summaries(paths, readers=4, said=said) == [_line_by_line(path) for path in paths]
    assert said == ["OSError"]


def test_ansible_s_run_of_the_module_reads_the_parts_with_processes_forked_from_it(tmp_path):
    # Ansible runs a module from a zip it built, as __main__: the readers must be forked from it, never started
    # afresh, or they could not find what they run. More than one part in all, so the module reads them together.
    size = info.PART + (1 << 20)
    root = tmp_path / ".claude"
    folder = root / "projects" / "-Users-me-demo"
    folder.mkdir(parents=True)
    expected = {}
    for index, sid in enumerate((ID, OTHER)):
        first = transcript(sid, cwd=f"/Users/me/first{index}", version="2.1.300", branch="start")
        filler = json.dumps({"type": "progress", "pad": "x" * 4000}).encode() + b"\n"
        last = transcript(sid, cwd="/Users/me/later", version=f"2.1.31{index}", branch=f"end{index}")
        (folder / f"{sid}.jsonl").write_bytes(first + filler * (size // len(filler) // 2) + last)
        expected[sid] = {"cwd": f"/Users/me/first{index}", "version": f"2.1.31{index}", "branch": f"end{index}"}
    playbook = tmp_path / "read.yml"
    playbook.write_text(
        "- hosts: localhost\n  connection: local\n  gather_facts: false\n  tasks:\n"
        f"    - name: Read the sessions\n      claude_sessions_info: {{path: {root}, days: 30}}\n"
    )
    report = _run(tmp_path, playbook, {}, timeout=300)
    (result,) = _ran(report, "Read the sessions")["localhost"]
    assert report["rc"] == 0 and not result.get("warnings"), result.get("warnings")
    assert {item["id"]: {key: item[key] for key in ("cwd", "version", "branch")} for item in result["sessions"]} == (
        expected
    )


def test_only_the_window_and_the_ids_asked_for_are_listed(tmp_path):
    root = _claude_dir(
        tmp_path,
        {("-Users-me-demo", ID): transcript(), ("-Users-me-demo", OTHER): transcript(OTHER), ("-x", "notes"): b"{}\n"},
        old=(OTHER,),
    )
    assert [item["id"] for item in info.list_sessions(str(root), 30, [])] == [ID]
    assert [item["id"] for item in info.list_sessions(str(root), 0, [OTHER])] == [OTHER]
    assert info.list_sessions(str(root), 0, []) == []
    # A name that is an ID and a newline is no session of Claude Code's: just destroy's save would refuse it.
    root = _claude_dir(tmp_path / "newline", {("-Users-me-demo", ID + "\n"): transcript()})
    assert info.list_sessions(str(root), 30, []) == []
    # A stub with no message is listed, and says so; the plan leaves it out.
    root = _claude_dir(tmp_path / "stub", {("-Users-me-demo", ID): transcript(conversation=False)})
    assert info.list_sessions(str(root), 30, [])[0]["conversation"] is False


def test_all_lists_every_session_whatever_its_age(tmp_path):
    root = _claude_dir(tmp_path, {("-x", ID): transcript(), ("-x", OTHER): transcript(OTHER)}, old=(ID, OTHER))
    assert info.list_sessions(str(root), 30, []) == []
    assert [item["id"] for item in info.list_sessions(str(root), 0, [], every=True)] == [ID, OTHER]


def test_the_manifest_tells_just_code_s_copies_still_as_written_from_the_rest(tmp_path):
    # Copied and untouched; copied and continued; copied, then moved to another folder by /cd; the VM's own.
    third = "c1d2e3f4-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
    own = "d4e5f6a7-8b9c-4d0e-9f1a-2b3c4d5e6f70"
    written = {sid: transcript(sid) for sid in (ID, OTHER, third)}
    root = _claude_dir(
        tmp_path,
        {
            ("-Users-me-demo", ID): written[ID],
            ("-Users-me-demo", OTHER): written[OTHER] + transcript(OTHER),
            ("-home-dev-projects-other", third): written[third],
            ("-home-dev-projects-demo", own): transcript(own),
        },
    )
    manifest = {f"-Users-me-demo/{sid}.jsonl": hashlib.sha256(data).hexdigest() for sid, data in written.items()}
    found = {
        item["id"]: (item["recorded"], item["copied"])
        for item in info.list_sessions(str(root), 30, [], manifest=manifest)
    }
    assert found == {ID: (True, True), OTHER: (True, False), third: (True, False), own: (False, False)}
    # A manifest that is not there, or not one, records nothing.
    assert info.read_manifest(str(tmp_path / "none.json")) == {}
    (tmp_path / "bad.json").write_text("[1]")
    assert info.read_manifest(str(tmp_path / "bad.json")) == {}


def test_memory_is_listed_by_the_folders_whose_memory_holds_a_file(tmp_path):
    root = _claude_dir(tmp_path, {})
    for folder, files in {"-a": ["MEMORY.md"], "-b": [], "-c": ["topics/x.md"]}.items():
        (root / "projects" / folder / "memory").mkdir(parents=True)
        for name in files:
            (root / "projects" / folder / "memory" / name).parent.mkdir(parents=True, exist_ok=True)
            (root / "projects" / folder / "memory" / name).write_text("- a note\n")
    (root / "projects" / "-d").mkdir()
    assert info.memory_folders(str(root)) == ["-a", "-c"]
    assert info.memory_folders(str(tmp_path / "none")) == []


@pytest.fixture
def a_claude():
    """A process whose argv is claude's, as `ps` and /proc show one: its pid."""
    process = subprocess.Popen(["claude", "600"], executable="/bin/sleep")
    yield process.pid
    process.kill()
    process.wait()


def test_an_open_session_is_one_a_running_claude_registered(tmp_path, a_claude):
    third = "c1d2e3f4-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
    files = {("-Users-me-demo", sid): transcript(sid) for sid in (ID, OTHER, third)}
    root = _claude_dir(tmp_path, files)
    sessions = root / "sessions"
    sessions.mkdir()
    (sessions / f"{a_claude}.json").write_text(json.dumps({"pid": a_claude, "sessionId": ID}))
    # A pid nothing runs as: the registry entry of a claude that exited without removing it.
    dead = subprocess.run(["sh", "-c", "echo $$"], capture_output=True, text=True, check=True).stdout.strip()
    (sessions / f"{dead}.json").write_text(json.dumps({"pid": int(dead), "sessionId": OTHER}))
    # A pid another program took since: this very pytest.
    (sessions / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": third}))
    (sessions / "3.json").write_text("not json")
    found = {item["id"]: item["open"] for item in info.list_sessions(str(root), 30, [])}
    assert found == {ID: a_claude, OTHER: 0, third: 0}
    # The registry alone, whatever transcripts there are.
    assert info.open_sessions(str(root)) == {ID: a_claude}


@pytest.mark.parametrize(
    ("argv", "claude"),
    [
        (["claude", "--resume", "x"], True),
        (["/home/dev/.local/bin/claude"], True),
        (["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"], True),
        # npm's bin, run through its node shebang.
        (["node", "/usr/bin/claude"], True),
        (["bash", "-l"], False),
        (["python3", "-m", "pytest"], False),
        # Only the program and its first argument: what it works on is not what it is.
        (["vim", "notes.md", "claude"], False),
    ],
)
def test_a_claude_process_is_told_by_its_argv(argv, claude):
    assert shared.is_claude(argv) is claude


def test_every_module_asks_the_one_rule_of_a_claude_process():
    # The shared file's: claude_sessions_info's open sessions, git_checkouts_info's busy checkouts and the status
    # probe's count, none with a rule of its own.
    for module in sorted((REPO / "playbooks" / "library").glob("*.py")):
        assert "@anthropic-ai/claude-code" not in module.read_text(), module.name


def test_a_claude_s_command_line_is_read_from_proc_where_there_is_one(tmp_path):
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "42").mkdir()
    (proc / "42" / "cmdline").write_bytes(b"/home/dev/.local/bin/claude\0--resume\0x\0")
    assert shared.process_argv(42, str(proc)) == ["/home/dev/.local/bin/claude", "--resume", "x"]
    assert shared.process_argv(43, str(proc)) == []


def test_the_registry_is_returned_whatever_the_transcripts(tmp_path, patch_module, a_claude):
    root = _claude_dir(tmp_path, {})
    (root / "sessions").mkdir()
    (root / "sessions" / f"{a_claude}.json").write_text(json.dumps({"pid": a_claude, "sessionId": ID}))
    result = run_main(info.main, {"path": str(root)})
    assert result["registered"] == {ID: a_claude} and result["sessions"] == []


def test_a_transcript_that_goes_as_it_is_listed_is_not_listed(tmp_path, monkeypatch):
    root = _claude_dir(tmp_path, {("-Users-me-demo", ID): transcript()})
    real = os.scandir

    class Gone:
        name, path = f"{OTHER}.jsonl", str(root / "projects" / "-Users-me-demo" / f"{OTHER}.jsonl")

        def is_file(self, follow_symlinks=True):
            return True

        def stat(self, follow_symlinks=True):
            raise FileNotFoundError(self.path)

    def scandir(path):
        entries = list(real(path))
        return entries + [Gone()] if str(path).endswith("-Users-me-demo") else entries

    monkeypatch.setattr(info.os, "scandir", scandir)
    assert [item["id"] for item in info.list_sessions(str(root), 30, [])] == [ID]


def test_github_repo_paths_come_from_the_claude_json_beside_the_directory_and_only_they(tmp_path):
    root = _claude_dir(tmp_path, {})
    (tmp_path / ".claude.json").write_text(
        json.dumps({"githubRepoPaths": {"octo/demo": ["/Users/me/demo", 3]}, "oauthAccount": {"emailAddress": "x"}})
    )
    assert info.repo_paths(str(root)) == {"octo/demo": ["/Users/me/demo"]}
    # CLAUDE_CONFIG_DIR keeps it inside the directory instead.
    (root / ".claude.json").write_text(json.dumps({"githubRepoPaths": {"octo/other": ["/x"]}}))
    assert info.repo_paths(str(root)) == {"octo/other": ["/x"]}
    # One there without the key is not the one Claude Code keeps it in: the next is read.
    (root / ".claude.json").write_text(json.dumps({"numStartups": 3}))
    assert info.repo_paths(str(root)) == {"octo/demo": ["/Users/me/demo"]}


def test_claude_s_version_comes_from_the_first_executable_there_is(tmp_path):
    claude = tmp_path / "claude"
    claude.write_text("#!/bin/sh\necho '2.1.308 (Claude Code)'\n")
    claude.chmod(0o755)
    assert info.claude_version([str(tmp_path / "missing"), str(claude)]) == "2.1.308"
    claude.write_text("#!/bin/sh\nexit 3\n")
    assert info.claude_version([str(claude)]) == ""


def test_the_module_reports_and_changes_nothing(tmp_path, patch_module):
    root = _claude_dir(tmp_path, {("-Users-me-demo", ID): transcript()})
    result = run_main(info.main, {"path": str(root), "days": 30})
    assert result["changed"] is False and [item["id"] for item in result["sessions"]] == [ID]
    assert result["version"] == "" and result["repo_paths"] == {} and result["memory"] == []
    (root / "projects" / "-Users-me-demo" / "memory").mkdir()
    (root / "projects" / "-Users-me-demo" / "memory" / "MEMORY.md").write_text("- a note\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"format": 1, "sessions": {f"-Users-me-demo/{ID}.jsonl": "0" * 64}}))
    result = run_main(info.main, {"path": str(root), "all": True, "manifest": str(manifest), "memory": True})
    assert [(item["recorded"], item["copied"]) for item in result["sessions"]] == [(True, False)]
    assert result["memory"] == ["-Users-me-demo"]
    # A manifest of another format is read as claude_sessions_copy reads it: as none.
    manifest.write_text(json.dumps({"format": 2, "sessions": {f"-Users-me-demo/{ID}.jsonl": "0" * 64}}))
    result = run_main(info.main, {"path": str(root), "all": True, "manifest": str(manifest)})
    assert [(item["recorded"], item["copied"]) for item in result["sessions"]] == [(False, False)]


# ------------------------------------------------------------------ claude_sessions_saved ---
def _found(sid, folder, **fields):
    return {
        "id": sid,
        "folder": folder,
        "mtime": 0.0,
        "cwd": "/home/dev/projects/demo",
        "branch": "main",
        "conversation": True,
        "recorded": False,
        "copied": False,
        "dir": False,
        **fields,
    }


SAVED = "/Users/me/agent-warehouse/.sessions/alpha-20261023-153000"


def test_only_what_the_vm_alone_has_is_saved_with_the_command_that_continues_it():
    saved = filters.claude_sessions_saved(
        [
            _found(ID, "-home-dev-projects-demo", dir=True),
            _found(OTHER, "-Users-me-demo", recorded=True, cwd="/Users/me/demo"),
            _found("c1d2e3f4-5a6b-4c7d-8e9f-0a1b2c3d4e5f", "-Users-me-demo", recorded=True, copied=True),
            _found("d4e5f6a7-8b9c-4d0e-9f1a-2b3c4d5e6f70", "-home-dev-projects-demo", conversation=False),
        ],
        ["-home-dev-projects-demo"],
        SAVED,
    )
    assert saved["files"] == [
        f"-Users-me-demo/{OTHER}.jsonl",
        f"-home-dev-projects-demo/{ID}.jsonl",
        f"-home-dev-projects-demo/{ID}",
        "-home-dev-projects-demo/memory",
    ]
    assert (saved["started"], saved["continued"], saved["memory"]) == (1, 1, ["-home-dev-projects-demo"])
    started = saved["sessions"][1]
    assert started == {
        "id": ID,
        "folder": "-home-dev-projects-demo",
        "cwd": "/home/dev/projects/demo",
        "branch": "main",
        "version": "",
        "written": "1970-01-01T00:00:00+00:00",
        "started_on_the_vm": True,
        "transcript": f"{SAVED}/projects/-home-dev-projects-demo/{ID}.jsonl",
        "resume": f"claude --resume {SAVED}/projects/-home-dev-projects-demo/{ID}.jsonl",
    }


def test_the_command_that_continues_a_session_quotes_its_path():
    (item,) = filters.claude_sessions_saved([_found(ID, "-x")], [], "/Users/me/my repo/.sessions/a-1")["sessions"]
    assert item["resume"] == f"claude --resume '/Users/me/my repo/.sessions/a-1/projects/-x/{ID}.jsonl'"


def test_a_vm_with_nothing_of_its_own_saves_nothing():
    copied = _found(ID, "-Users-me-demo", recorded=True, copied=True)
    assert filters.claude_sessions_saved([copied], [], "/x") == {
        "files": [],
        "sessions": [],
        "started": 0,
        "continued": 0,
        "memory": [],
        "refused": 0,
        "newest": "",
    }


@pytest.mark.parametrize(
    ("folder", "sid"),
    [
        ("../../..", ID),
        ("-home-dev-x\nevil", ID),
        # A trailing newline too: `$` matches before one, so only a whole match keeps it out.
        ("-home-dev-x\n", ID),
        ("-home-dev-x", ID + "\n"),
        ("-home-dev x", ID),
        ("/etc", ID),
        ("-home-dev-x", "../../etc/passwd"),
        ("-home-dev-x", ID.upper()),
    ],
)
def test_what_the_vm_names_otherwise_than_claude_code_is_left_out_and_counted(folder, sid):
    # The VM reports these: never handed to tar, never a path here.
    saved = filters.claude_sessions_saved([_found(sid, folder, dir=True)], ["-home-dev-y", "../y"], "/x")
    assert saved["files"] == ["-home-dev-y/memory"] and saved["sessions"] == []
    assert saved["refused"] == 2 and saved["memory"] == ["-home-dev-y"]


def test_a_memory_folder_named_with_a_trailing_newline_is_left_out_and_counted():
    saved = filters.claude_sessions_saved([], ["-home-dev-y\n"], "/x")
    assert saved["files"] == [] and saved["memory"] == [] and saved["refused"] == 1


def test_each_saved_session_names_the_claude_code_that_wrote_it_and_the_newest_of_them():
    saved = filters.claude_sessions_saved(
        [_found(ID, "-x", version="2.1.310"), _found(OTHER, "-x", version="2.1.9; rm -rf /")], [], "/x"
    )
    assert [item["version"] for item in saved["sessions"]] == ["2.1.310", "2.1.9"]
    assert saved["newest"] == "2.1.310"
    assert filters.claude_sessions_saved([_found(ID, "-x")], [], "/x")["newest"] == ""


def test_a_time_the_vm_makes_up_is_no_failure():
    (item,) = filters.claude_sessions_saved([_found(ID, "-x", mtime=1e300)], [], "/x")["sessions"]
    assert item["written"] == ""


# --------------------------------------------------------------------------- git_checkouts_info ---
def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=True).stdout.strip()


def _repository(path, origin, branch="main"):
    path.mkdir(parents=True)
    _git(path, "init", "--quiet", "--initial-branch", branch)
    _git(path, "config", "user.email", "dev@example.com")
    _git(path, "config", "user.name", "Dev")
    (path / "README").write_text("hello\n")
    _git(path, "add", "README")
    _git(path, "commit", "--quiet", "-m", "first")
    if origin:
        _git(path, "remote", "add", "origin", origin)
    return path


def test_the_projects_on_the_vm_are_its_checkouts_with_their_origins(tmp_path, patch_module):
    projects = tmp_path / "projects"
    _repository(projects / "demo", "https://github.com/octo/demo.git")
    _repository(projects / "local", "")
    (projects / ".agent-warehouse").mkdir()
    (projects / "plain").mkdir()
    result = run_main(checkouts.main, {"under": str(projects)})
    assert [(item["name"], item["origin"]) for item in result["checkouts"]] == [
        ("demo", "https://github.com/octo/demo.git"),
        ("local", ""),
    ]


def test_a_directory_asked_about_is_reported_from_a_subfolder_or_gone(tmp_path, patch_module):
    repo = _repository(tmp_path / "demo", "git@github.com:octo/demo.git")
    (repo / "src").mkdir()
    result = run_main(checkouts.main, {"paths": [str(repo / "src"), str(tmp_path / "gone")]})
    first, gone = result["checkouts"]
    assert first["git"] and first["origin"] == "git@github.com:octo/demo.git"
    assert Path(first["toplevel"]).resolve() == repo.resolve()
    assert gone == {
        "path": str(tmp_path / "gone"),
        "name": "gone",
        "exists": False,
        "git": False,
        "toplevel": "",
        "origin": "",
    }


def test_status_and_branch_say_whether_the_work_is_committed_and_pushed(tmp_path, patch_module):
    bare = tmp_path / "remotes" / "octo" / "demo.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(bare)], check=True)
    repo = _repository(tmp_path / "demo", str(bare))
    _git(repo, "push", "--quiet", "-u", "origin", "main")
    ask = {"paths": [str(repo)], "status": True, "branch": "main"}
    item = run_main(checkouts.main, ask)["checkouts"][0]
    assert item["clean"] and item["head_branch"] == "main"
    assert item["branch_head"] == item["upstream_head"] == item["head"] and item["upstream_remote"] == "origin"
    assert item["upstream_branch"] == "main"
    (repo / "README").write_text("changed\n")
    assert run_main(checkouts.main, ask)["checkouts"][0]["clean"] is False
    _git(repo, "commit", "--quiet", "-am", "second")
    item = run_main(checkouts.main, ask)["checkouts"][0]
    assert item["clean"] and item["branch_head"] != item["upstream_head"]
    assert run_main(checkouts.main, {**ask, "branch": "gone"})["checkouts"][0]["branch_head"] == ""


@pytest.mark.parametrize("branch", ["--upload-pack=touch /tmp/x", "a..b", "-x"])
def test_a_branch_git_would_not_take_is_refused(tmp_path, patch_module, branch):
    from conftest import fail_main

    repo = _repository(tmp_path / "demo", "")
    assert "not a branch name" in fail_main(checkouts.main, {"paths": [str(repo)], "branch": branch})["msg"]


def test_busy_names_the_claude_processes_working_inside(tmp_path):
    proc = tmp_path / "proc"
    for pid, argv, cwd in (
        (11, b"claude\0", "/home/dev/projects/demo/src"),
        (12, b"bash\0", "/home/dev/projects/demo"),
    ):
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "cmdline").write_bytes(argv)
        (proc / str(pid) / "cwd").symlink_to(cwd)
    assert checkouts.claude_processes(str(proc)) == [(11, "/home/dev/projects/demo/src")]
    assert checkouts.claude_processes(str(tmp_path / "none")) == []


def test_the_origin_is_the_url_configured_never_one_insteadof_rewrote(tmp_path, patch_module):
    # A url.<base>.insteadOf says where git connects to; the repository is the URL the remote names.
    repo = _repository(tmp_path / "demo", "https://github.com/octo/demo.git")
    _git(repo, "config", f"url.{tmp_path}/bare.git.insteadOf", "https://github.com/octo/demo.git")
    assert _git(repo, "remote", "get-url", "origin") == f"{tmp_path}/bare.git"
    assert run_main(checkouts.main, {"paths": [str(repo)]})["checkouts"][0]["origin"] == (
        "https://github.com/octo/demo.git"
    )


def test_untracked_files_count_whatever_the_checkout_s_config_hides(tmp_path, patch_module):
    repo = _repository(tmp_path / "demo", "")
    _git(repo, "config", "status.showUntrackedFiles", "no")
    (repo / "notes.txt").write_text("not committed\n")
    assert _git(repo, "status", "--porcelain") == ""
    assert run_main(checkouts.main, {"paths": [str(repo)], "status": True})["checkouts"][0]["clean"] is False


@pytest.mark.parametrize(
    ("configured", "reported"),
    [
        ("https://x-access-token:ghp_canned@github.com/octo/demo.git", "https://github.com/octo/demo.git"),
        ("https://ghp_canned@github.com/octo/demo.git", "https://github.com/octo/demo.git"),
        ("ssh://git@github.com/octo/demo.git", "ssh://github.com/octo/demo.git"),
        ("git@github.com:octo/demo.git", "git@github.com:octo/demo.git"),
    ],
)
def test_no_credential_in_an_origin_is_reported(tmp_path, patch_module, configured, reported):
    # The module's result reaches -v output and the run's log.
    repo = _repository(tmp_path / "demo", configured)
    found = run_main(checkouts.main, {"paths": [str(repo)]})
    assert found["checkouts"][0]["origin"] == reported and "ghp_canned" not in json.dumps(found)
    assert filters.github_repository(reported) == "octo/demo"


def test_a_path_or_branch_holding_a_dollar_or_tilde_is_taken_as_written(tmp_path, patch_module):
    repo = _repository(tmp_path / "$HOME-demo", "")
    _git(repo, "branch", "x$HOME")
    found = run_main(checkouts.main, {"paths": [str(repo)], "branch": "x$HOME"})["checkouts"][0]
    assert found["exists"] and found["git"] and found["branch_head"] == _git(repo, "rev-parse", "HEAD")


def test_a_detached_head_is_kept_only_while_a_ref_holds_its_commit(tmp_path, patch_module):
    repo = _repository(tmp_path / "demo", "")
    ask = {"paths": [str(repo)], "status": True}
    assert run_main(checkouts.main, ask)["checkouts"][0]["head_kept"] is True
    _git(repo, "switch", "--quiet", "--detach")
    assert run_main(checkouts.main, ask)["checkouts"][0]["head_kept"] is True  # main holds it
    (repo / "x").write_text("x\n")
    _git(repo, "add", "x")
    _git(repo, "commit", "--quiet", "-m", "on no branch")
    item = run_main(checkouts.main, ask)["checkouts"][0]
    assert item["head_branch"] == "" and item["head_kept"] is False
    _git(repo, "tag", "kept")
    assert run_main(checkouts.main, ask)["checkouts"][0]["head_kept"] is True


def test_overwrites_names_what_git_does_not_track_that_a_move_would_replace(tmp_path, patch_module):
    repo = _repository(tmp_path / "demo", "")
    (repo / ".gitignore").write_text("build/\n*.log\nlinked\n")
    (repo / "tracked").write_text("a file\n")
    _git(repo, "add", ".gitignore", "tracked")
    _git(repo, "commit", "--quiet", "-m", "ignore")
    _git(repo, "switch", "--quiet", "--create", "next")
    (repo / "build").mkdir()
    (repo / "build" / "out.txt").write_text("built\n")
    (repo / "app.log").write_text("logged\n")
    (repo / "linked").mkdir()
    (repo / "linked" / "inner").write_text("x\n")
    # A tracked file that becomes a folder: git's own business, never named.
    _git(repo, "rm", "--quiet", "tracked")
    (repo / "tracked").mkdir()
    (repo / "tracked" / "inner").write_text("y\n")
    _git(repo, "add", "--force", "build/out.txt", "app.log", "linked/inner", "tracked/inner")
    _git(repo, "commit", "--quiet", "-m", "next")
    target = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "--quiet", "main")
    ask = {"paths": [str(repo)], "status": True, "target": target}
    assert run_main(checkouts.main, ask)["checkouts"][0]["overwrites"] == []
    (repo / "build").mkdir()
    (repo / "build" / "out.txt").write_text("mine\n")
    (repo / "app.log").write_text("mine\n")
    (repo / "linked").symlink_to(tmp_path)
    item = run_main(checkouts.main, ask)["checkouts"][0]
    # The tree reads clean all the same: every one of them is ignored.
    assert item["clean"] and item["overwrites"] == ["app.log", "build/out.txt", "linked"]


# A full ID with a newline after it: an anchored match took it.
@pytest.mark.parametrize("target", ["HEAD", "--output=/tmp/x", "abc123", "a" * 40 + "\n"])
def test_a_target_that_is_not_a_full_commit_id_is_refused(tmp_path, patch_module, target):
    from conftest import fail_main

    repo = _repository(tmp_path / "demo", "")
    assert "not a full commit ID" in fail_main(checkouts.main, {"paths": [str(repo)], "target": target})["msg"]


# ------------------------------------------------------------------------- claude_sessions_copy ---
def test_the_four_edits_and_nothing_else():
    data = transcript(worktree={"worktreePath": "/Users/me/demo/.claude/worktrees/fix-x"})
    edited, why = copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY, HERE)
    assert why == ""
    before, after = data.decode().splitlines(), edited.decode().splitlines()
    # The memory folder changes in the snapshot, and only there.
    assert MAC_MEMORY in before[0] and after[0] == before[0].replace(MAC_MEMORY, VM_MEMORY)
    assert after[1 : len(before)] == before[1:]
    # A cleared worktree binding, then where the session continues.
    assert [json.loads(line) for line in after[len(before) :]] == [
        {"type": "worktree-state", "worktreeSession": None, "sessionId": ID},
        {"type": "relocated", "relocatedCwd": CHECKOUT, "sessionId": ID},
    ]


def test_a_session_bound_to_no_worktree_gets_no_cleared_binding():
    # Never bound, or bound and then cleared by the session itself (ExitWorktree).
    cleared = json.dumps({"type": "worktree-state", "worktreeSession": None, "sessionId": ID}).encode() + b"\n"
    for data in (transcript(snapshot=False), transcript(worktree={"worktreePath": "/x"}, snapshot=False) + cleared):
        edited, _ = copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY)
        assert (
            edited
            == data
            + b'{"type":"relocated","relocatedCwd":"/home/dev/projects/demo","sessionId":"'
            + ID.encode()
            + b'"}\n'
        )


def test_the_records_of_requests_sent_keep_the_memory_folder_they_name():
    # A resume sends the snapshot, never these (the configure scenario holds that against the real claude).
    shape = {"type": "api-request-shape", "sessionId": ID, "shapeHash": "ab12", "shape": {"system": [MAC_MEMORY]}}
    record = json.dumps(shape, separators=(",", ":")).encode() + b"\n"
    data = transcript() + record
    edited, why = copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY, HERE)
    assert why == "" and record in edited and edited.count(MAC_MEMORY.encode()) == 1


def test_a_snapshot_without_a_memory_folder_is_copied_as_it_is():
    # Auto memory off: the recorded prompt names none, and nothing needs rewriting.
    data = transcript(snapshot=False)
    edited, why = copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY, HERE)
    assert why == "" and edited.startswith(data)


def test_a_memory_folder_named_in_a_form_just_code_does_not_know_is_not_copied():
    # A Claude Code that words the prompt otherwise: the folder of this machine's would be sent as it is.
    data = transcript().replace(b"persistent file-based memory at", b"memory, kept at")
    edited, why = copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY, HERE)
    assert edited == b"" and "a form just code does not know" in why


VM_RESULTS = f"/home/dev/.claude/projects/-Users-me-demo/{ID}/tool-results"


def test_tool_results_are_named_where_the_copy_keeps_them_in_the_tool_results_alone():
    # As Claude Code names a large output it saved: in the tool result, in any folder of this machine's (a
    # session continued after /cd names the old one), and in what the assistant then asked for.
    saved = f"{HERE}/projects/-Users-me-old/{ID}/tool-results/toolu_1.txt"
    other = f"{HERE}/projects/-Users-me-demo/{OTHER}/tool-results/toolu_2.txt"
    result = {
        "type": "user",
        "sessionId": ID,
        "message": {"role": "user", "content": [{"type": "tool_result", "content": f"Output saved to: {saved}"}]},
        "toolUseResult": {"stdout": f"{saved} and {other}"},
    }
    asked = {
        "type": "assistant",
        "sessionId": ID,
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": f"read {saved}", "signature": "c2lnbmVk"},
                {"type": "tool_use", "name": "Read", "input": {"file_path": saved}},
            ],
        },
    }
    lines_in = [json.dumps(entry, separators=(",", ":")).encode() + b"\n" for entry in (result, asked)]
    data = transcript() + b"".join(lines_in)
    edited, why = copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY, HERE, VM_RESULTS)
    assert why == ""
    out = edited.splitlines(keepends=True)
    tool_result, assistant = out[-3], out[-2]
    assert tool_result == lines_in[0].replace(saved.encode(), f"{VM_RESULTS}/toolu_1.txt".encode())
    # Another session's saved output is that session's; the assistant's entry, signed thinking and all, is as it was.
    assert other.encode() in tool_result and assistant == lines_in[1]
    # Without the other machine's directory, no tool result is rewritten.
    assert lines_in[0] in copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY, "", VM_RESULTS)[0]


def test_another_project_s_memory_folder_a_prompt_quotes_is_left_as_text():
    # A CLAUDE.md or MEMORY.md in the prompt can name another project's memory folder; only the session's
    # own must be rewritten.
    quoted = f"{HERE}/projects/-Users-me-elsewhere/memory/notes.md"
    data = transcript().replace(b"This directory already exists.", f"See {quoted}.".encode())
    edited, why = copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY, HERE, folder="-Users-me-demo")
    assert why == "" and quoted.encode() in edited and MAC_MEMORY.encode() not in edited
    # The session's own, named otherwise than the rewrite knows, is still refused.
    other_form = transcript().replace(b"persistent file-based memory at", b"memory, kept at")
    edited, why = copy.edit_transcript(other_form, ID, CHECKOUT, VM_MEMORY, HERE, folder="-Users-me-demo")
    assert edited == b"" and "a form just code does not know" in why


def test_only_complete_lines_are_copied():
    assert copy.complete_lines(transcript(partial=True)) == transcript()
    assert copy.complete_lines(b'{"type":"us') == b""


@pytest.mark.parametrize(
    ("current", "recorded", "digest", "action"),
    [
        (None, None, "new", "write"),  # no copy yet
        ("old", "old", "new", "write"),  # the last copy, untouched: replaced
        ("new", "old", "new", "unchanged"),
        ("new", None, "new", "unchanged"),
        ("vm", "old", "old", "continued"),  # continued on the VM; nothing new here
        ("vm", "old", "new", "diverged"),  # continued on both
        ("vm", None, "new", "diverged"),  # never this module's
    ],
)
def test_a_copy_is_replaced_only_while_it_is_what_was_last_written(current, recorded, digest, action):
    assert copy.classify(current, recorded, digest) == action


@pytest.mark.parametrize(
    ("recorded", "digest", "action"),
    [("old", "old", "continued"), ("old", "new", "diverged"), (None, "new", "diverged")],
)
def test_a_copy_that_moved_to_another_folder_counts_as_changed_on_the_vm(recorded, digest, action):
    assert copy.classify(None, recorded, digest, moved=True) == action


def _mirror(tmp_path, sessions):
    """The mirror rsync leaves: {(folder, id): bytes}, written an hour ago, with a tool result and a subagent each."""
    mirror = tmp_path / "mirror"
    then = time.time() - 3600
    for (folder, sid), data in sessions.items():
        path = mirror / folder / f"{sid}.jsonl"
        (mirror / folder / sid / "tool-results").mkdir(parents=True)
        (mirror / folder / sid / "subagents").mkdir()
        (mirror / folder / sid / "tool-results" / "toolu_1.txt").write_text("output\n")
        (mirror / folder / sid / "subagents" / "agent-1.jsonl").write_text("{}\n")
        path.write_bytes(data)
        os.utime(path, (then, then))
    return mirror, then


def _copy(tmp_path, mirror, sessions, check=False, subagents=False, keep=()):
    return copy.copy_sessions(
        str(mirror),
        str(tmp_path / "vm" / ".claude"),
        str(tmp_path / "ws" / "manifest.json"),
        [{"id": sid, "folder": folder, "checkout": CHECKOUT, "memory": VM_MEMORY} for folder, sid in sessions],
        here=HERE,
        check=check,
        subagents=subagents,
        keep=keep,
    )


def test_copies_are_written_with_their_time_and_modes_and_a_second_run_changes_nothing(tmp_path):
    keys = [("-Users-me-demo", ID), ("-Users-me-demo--claude-worktrees-x", OTHER)]
    mirror, then = _mirror(tmp_path, {keys[0]: transcript(partial=True), keys[1]: transcript(OTHER)})
    result, changed = _copy(tmp_path, mirror, keys)
    assert changed and sorted(result["written"]) == sorted([ID, OTHER])
    target = tmp_path / "vm" / ".claude" / "projects" / "-Users-me-demo"
    copied = target / f"{ID}.jsonl"
    assert copied.read_bytes() == copy.edit_transcript(transcript(), ID, CHECKOUT, VM_MEMORY)[0]
    assert int(copied.stat().st_mtime) == int(then)
    assert stat.S_IMODE(copied.stat().st_mode) == 0o600 and stat.S_IMODE(target.stat().st_mode) == 0o700
    assert (target / ID / "tool-results" / "toolu_1.txt").read_text() == "output\n"
    # The tool result's time is the transcript's, never the time it was written on the other machine.
    assert int((target / ID / "tool-results" / "toolu_1.txt").stat().st_mtime) == int(then)
    assert not (target / ID / "subagents").exists()
    manifest = json.loads((tmp_path / "ws" / "manifest.json").read_text())
    assert sorted(manifest["sessions"]) == sorted(f"{folder}/{sid}.jsonl" for folder, sid in keys)
    assert stat.S_IMODE((tmp_path / "ws" / "manifest.json").stat().st_mode) == 0o600
    # Again, with nothing new: nothing written, nothing changed.
    again, changed = _copy(tmp_path, mirror, keys)
    assert not changed and sorted(again["unchanged"]) == sorted([ID, OTHER])


def _counted(monkeypatch):
    """How many times the copy module edits a transcript and digests a file, from here on."""
    calls = {"edits": 0, "digests": 0}
    edit, digest = copy.edit_transcript, copy.file_digest

    def counted_edit(*args, **kwargs):
        calls["edits"] += 1
        return edit(*args, **kwargs)

    def counted_digest(*args, **kwargs):
        calls["digests"] += 1
        return digest(*args, **kwargs)

    monkeypatch.setattr(copy, "edit_transcript", counted_edit)
    monkeypatch.setattr(copy, "file_digest", counted_digest)
    return calls


def test_a_copy_unchanged_since_it_was_written_is_not_read_again(tmp_path, monkeypatch):
    keys = [("-Users-me-demo", ID), ("-Users-me-other", OTHER)]
    mirror, _ = _mirror(tmp_path, {keys[0]: transcript(), keys[1]: transcript(OTHER)})
    _copy(tmp_path, mirror, keys)
    manifest = tmp_path / "ws" / "manifest.json"
    recorded = json.loads(manifest.read_text())
    # Each copy by its key, and each file of its tool results by its own.
    sessions = sorted(recorded["sessions"])
    assert sorted(key for key in recorded["made_from"] if key.count("/") == 1) == sessions
    assert all(key in sessions or "/".join(key.split("/")[:2]) + ".jsonl" in sessions for key in recorded["made_from"])
    # The digests are still all an earlier version reads, and it reads them as before.
    assert shared.read_manifest(str(manifest)) == recorded["sessions"]
    calls = _counted(monkeypatch)
    result, changed = _copy(tmp_path, mirror, keys)
    assert not changed and sorted(result["unchanged"]) == sorted([ID, OTHER])
    assert calls == {"edits": 0, "digests": 0}
    assert json.loads(manifest.read_text()) == recorded


@pytest.mark.parametrize("what", ["checkout", "edits"])
def test_a_copy_is_made_again_when_what_it_is_made_from_changes(tmp_path, monkeypatch, what):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    _copy(tmp_path, mirror, [key])
    calls = _counted(monkeypatch)
    if what == "checkout":
        # The VM's checkout moved: the copy's last entry names the new one.
        result, changed = copy.copy_sessions(
            str(mirror),
            str(tmp_path / "vm" / ".claude"),
            str(tmp_path / "ws" / "manifest.json"),
            [{"id": ID, "folder": key[0], "checkout": "/home/dev/projects/moved", "memory": VM_MEMORY}],
            here=HERE,
        )
        copied = tmp_path / "vm" / ".claude" / "projects" / key[0] / f"{ID}.jsonl"
        assert (
            result["written"] == [ID] and lines(copied.read_bytes())[-1]["relocatedCwd"] == "/home/dev/projects/moved"
        )
    else:
        # The edits themselves changed: each copy is edited again, and found as it would be.
        monkeypatch.setattr(copy, "EDITS", copy.EDITS + 1)
        result, changed = _copy(tmp_path, mirror, [key])
        assert result["unchanged"] == [ID]
    assert changed and calls["edits"] == 1


@pytest.mark.parametrize(
    ("change", "seen"),
    [
        ("time", "continued"),  # rewritten in place to its own size: its time is new
        ("file", "continued"),  # another file of its size put in its place, the time put back: its inode is new
        # Rewritten in place to its size with its time put back by hand: taken for the copy written, and so left as
        # it is all the same - never written over, which is all a copy continued on the VM asks.
        ("bytes alone", "unchanged"),
    ],
)
def test_a_copy_changed_on_the_vm_is_told_by_its_size_time_or_file_and_never_written_over(tmp_path, change, seen):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    _copy(tmp_path, mirror, [key])
    copied = tmp_path / "vm" / ".claude" / "projects" / key[0] / f"{ID}.jsonl"
    before = copied.stat()
    other = copied.read_bytes().replace(b"run the tests", b"RUN THE TESTS")
    if change == "file":
        beside = copied.with_name("beside")
        beside.write_bytes(other)
        os.utime(beside, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(beside, copied)
    else:
        copied.write_bytes(other)
        if change == "bytes alone":
            os.utime(copied, ns=(before.st_atime_ns, before.st_mtime_ns))
    result, _ = _copy(tmp_path, mirror, [key])
    assert result[seen] == [ID] and copied.read_bytes() == other


def test_a_manifest_an_earlier_version_wrote_reads_each_copy_once_more(tmp_path, monkeypatch):
    keys = [("-Users-me-demo", ID), ("-Users-me-other", OTHER)]
    mirror, _ = _mirror(tmp_path, {keys[0]: transcript(), keys[1]: transcript(OTHER)})
    _copy(tmp_path, mirror, keys)
    manifest = tmp_path / "ws" / "manifest.json"
    recorded = json.loads(manifest.read_text())
    manifest.write_text(json.dumps({"format": 1, "sessions": recorded["sessions"]}))
    calls = _counted(monkeypatch)
    result, changed = _copy(tmp_path, mirror, keys)
    assert changed and sorted(result["unchanged"]) == sorted([ID, OTHER]) and calls["edits"] == 2
    assert json.loads(manifest.read_text()) == recorded
    _, changed = _copy(tmp_path, mirror, keys)
    assert not changed and calls["edits"] == 2


# The source of what makes a copy's bytes from a transcript's, for each EDITS: a change to any of it must raise
# claude_sessions_copy's EDITS, or a copy an earlier version wrote would be skipped as unchanged, never edited again.
EDITS_SOURCE = {
    1: "2615ac647ce6d5489278686ab679d8702fff33e06f23a5f7663831145d95ba54",
    # A subagent's transcript, edited as its session's but never given the session's own two entries (own=False).
    2: "fd85547a80da162f66910905add1d1128bc10e64a4041e9ab7b9abfde8957c78",
}


def test_the_edits_version_is_raised_with_the_edits():
    import inspect

    parts = [
        inspect.getsource(function)
        for function in (
            copy.complete_lines,
            copy.edit_transcript,
            copy._escaped,
            copy._line,
            copy._strings,
            copy._left_behind,
            shared.transcript_entry,
        )
    ]
    digest = hashlib.sha256("\n".join([*parts, copy.MEMORY_AT.pattern]).encode()).hexdigest()
    recorded = EDITS_SOURCE.get(copy.EDITS)
    assert recorded == digest, (
        f"record {digest} for EDITS {copy.EDITS} in EDITS_SOURCE here"
        if recorded is None
        else f"the edits changed: raise EDITS in playbooks/library/claude_sessions_copy.py to {copy.EDITS + 1}, and "
        f"record {digest} for it in EDITS_SOURCE here"
    )


def test_a_copy_continued_on_the_vm_is_left_and_one_that_grew_here_too_is_named(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    _copy(tmp_path, mirror, [key])
    copied = tmp_path / "vm" / ".claude" / "projects" / "-Users-me-demo" / f"{ID}.jsonl"
    continued = copied.read_bytes() + b'{"type":"user","message":{"content":"on the VM"}}\n'
    copied.write_bytes(continued)
    result, changed = _copy(tmp_path, mirror, [key])
    assert result["continued"] == [ID] and not changed and copied.read_bytes() == continued
    # The session grew here as well: both left, and said apart.
    (mirror / "-Users-me-demo" / f"{ID}.jsonl").write_bytes(transcript() + transcript())
    result, _ = _copy(tmp_path, mirror, [key])
    assert result["diverged"] == [ID] and copied.read_bytes() == continued


def test_a_copy_moved_to_another_folder_on_the_vm_is_never_written_again(tmp_path):
    # /cd in the resumed session moves its transcript to the new directory's folder.
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    _copy(tmp_path, mirror, [key])
    projects = tmp_path / "vm" / ".claude" / "projects"
    manifest = (tmp_path / "ws" / "manifest.json").read_bytes()
    (projects / "-home-dev-projects-other").mkdir()
    moved = projects / "-home-dev-projects-other" / f"{ID}.jsonl"
    (projects / "-Users-me-demo" / f"{ID}.jsonl").rename(moved)
    result, changed = _copy(tmp_path, mirror, [key])
    assert result["continued"] == [ID] and not changed
    assert not (projects / "-Users-me-demo" / f"{ID}.jsonl").exists()
    assert (tmp_path / "ws" / "manifest.json").read_bytes() == manifest
    # The session grew here as well: still one folder, and said apart.
    (mirror / "-Users-me-demo" / f"{ID}.jsonl").write_bytes(transcript() + transcript())
    result, _ = _copy(tmp_path, mirror, [key])
    assert result["diverged"] == [ID] and not (projects / "-Users-me-demo" / f"{ID}.jsonl").exists()


def test_a_copy_still_as_written_is_replaced_when_the_session_grows_here(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    _copy(tmp_path, mirror, [key])
    (mirror / "-Users-me-demo" / f"{ID}.jsonl").write_bytes(transcript() + transcript())
    result, changed = _copy(tmp_path, mirror, [key])
    assert result["written"] == [ID] and changed


def test_a_session_moved_to_another_folder_here_takes_its_copy_along(tmp_path):
    # /cd on this machine: the session's folder changed here, and the VM's copy in the old one is still as written.
    old, new = ("-Users-me-demo", ID), ("-Users-me-demo-src", ID)
    mirror, _ = _mirror(tmp_path, {old: transcript()})
    _copy(tmp_path, mirror, [old])
    os.renames(mirror / old[0] / f"{ID}.jsonl", mirror / new[0] / f"{ID}.jsonl")
    os.renames(mirror / old[0] / ID, mirror / new[0] / ID)
    result, changed = _copy(tmp_path, mirror, [new])
    projects = tmp_path / "vm" / ".claude" / "projects"
    assert changed and result["written"] == [ID] and result["held"] == {}
    assert (projects / new[0] / f"{ID}.jsonl").exists() and not (projects / old[0]).exists()
    assert sorted(json.loads((tmp_path / "ws" / "manifest.json").read_text())["sessions"]) == [f"{new[0]}/{ID}.jsonl"]
    again, changed = _copy(tmp_path, mirror, [new])
    assert not changed and again["unchanged"] == [ID]


def test_a_session_moved_here_whose_old_copy_was_continued_on_the_vm_is_left_and_its_folder_named(tmp_path):
    old, new = ("-Users-me-demo", ID), ("-Users-me-demo-src", ID)
    mirror, _ = _mirror(tmp_path, {old: transcript()})
    _copy(tmp_path, mirror, [old])
    projects = tmp_path / "vm" / ".claude" / "projects"
    continued = projects / old[0] / f"{ID}.jsonl"
    continued.write_bytes(continued.read_bytes() + b'{"type":"user","message":{"content":"on the VM"}}\n')
    os.renames(mirror / old[0] / f"{ID}.jsonl", mirror / new[0] / f"{ID}.jsonl")
    result, _ = _copy(tmp_path, mirror, [new])
    assert result["diverged"] == [ID] and result["held"] == {ID: old[0]}
    assert continued.exists() and not (projects / new[0] / f"{ID}.jsonl").exists()


def test_each_copy_is_recorded_as_it_is_written_so_a_run_that_stops_records_what_it_wrote(tmp_path, monkeypatch):
    keys = [("-Users-me-demo", ID), ("-Users-me-other", OTHER)]
    mirror, _ = _mirror(tmp_path, {keys[0]: transcript(), keys[1]: transcript(OTHER)})
    # The second copy's write fails - a disk that fills - after the first was written.
    write = copy._write

    def full(path, data, mtime):
        if path.endswith(f"{OTHER}.jsonl"):
            raise OSError(28, "No space left on device")
        write(path, data, mtime)

    monkeypatch.setattr(copy, "_write", full)
    with pytest.raises(OSError):
        _copy(tmp_path, mirror, keys)
    recorded = json.loads((tmp_path / "ws" / "manifest.json").read_text())["sessions"]
    assert sorted(recorded) == [f"{keys[0][0]}/{ID}.jsonl"]
    # The session then grows here: the copy is the module's own, and replaced, never "diverged".
    monkeypatch.setattr(copy, "_write", write)
    (mirror / keys[0][0] / f"{ID}.jsonl").write_bytes(transcript() + transcript())
    result, _ = _copy(tmp_path, mirror, keys)
    assert sorted(result["written"]) == sorted([ID, OTHER])


def test_a_file_the_vm_put_where_a_copy_s_folder_goes_is_named_and_the_rest_goes_on(tmp_path):
    keys = [("-Users-me-demo", ID), ("-Users-me-other", OTHER)]
    mirror, _ = _mirror(tmp_path, {keys[0]: transcript(), keys[1]: transcript(OTHER)})
    (tmp_path / "vm" / ".claude" / "projects").mkdir(parents=True)
    (tmp_path / "vm" / ".claude" / "projects" / keys[0][0]).write_text("in the way\n")
    result, _ = _copy(tmp_path, mirror, keys)
    assert result["written"] == [OTHER]
    assert result["unreadable"] == [{"id": ID, "why": "something else is in its folder's place there"}]
    assert (tmp_path / "vm" / ".claude" / "projects" / keys[0][0]).read_text() == "in the way\n"


@pytest.mark.parametrize(
    ("age", "current", "kept"),
    [
        (3600, None, "source"),  # recent: the other machine's time
        (40 * 86400, None, "now"),  # older than the sweep's 30 days: the time it is written
        (40 * 86400, 86400, "current"),  # given that a day ago: kept
        (40 * 86400, 20 * 86400, "now"),  # given that 20 days ago: renewed
    ],
)
def test_a_copy_keeps_the_other_machine_s_time_only_while_the_sweep_would_spare_it(age, current, kept):
    now = 2_000_000_000.0
    source, there = now - age, None if current is None else now - current
    assert copy.copy_time(source, there, now) == {"source": source, "now": now, "current": there}[kept]


def test_an_old_session_s_copy_is_given_the_time_it_is_written_and_kept_on_the_next_run(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    then = time.time() - 40 * 86400
    os.utime(mirror / key[0] / f"{ID}.jsonl", (then, then))
    _copy(tmp_path, mirror, [key])
    copied = tmp_path / "vm" / ".claude" / "projects" / key[0] / f"{ID}.jsonl"
    assert time.time() - copied.stat().st_mtime < 60
    _, changed = _copy(tmp_path, mirror, [key])
    assert not changed


def test_an_old_session_s_tool_results_are_kept_from_the_sweep_with_their_transcript(tmp_path):
    # Claude Code sweeps tool-results/ by the files' own age: rsync brings the other machine's time, which a copy
    # of a session older than 30 days must not keep, or its tool results go before its transcript does.
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    then = time.time() - 40 * 86400
    for path in (mirror / key[0] / f"{ID}.jsonl", mirror / key[0] / ID / "tool-results" / "toolu_1.txt"):
        os.utime(path, (then, then))
    _copy(tmp_path, mirror, [key])
    target = tmp_path / "vm" / ".claude" / "projects" / key[0]
    saved = target / ID / "tool-results" / "toolu_1.txt"
    assert int(saved.stat().st_mtime) == int((target / f"{ID}.jsonl").stat().st_mtime)
    assert time.time() - saved.stat().st_mtime < 60
    _, changed = _copy(tmp_path, mirror, [key])
    assert not changed


def test_a_tool_result_is_told_by_its_bytes_never_by_its_time(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, then = _mirror(tmp_path, {key: transcript()})
    _copy(tmp_path, mirror, [key])
    saved = tmp_path / "vm" / ".claude" / "projects" / key[0] / ID / "tool-results" / "toolu_1.txt"
    # Another output of the same size, with the time the copy there already has: written all the same.
    source = mirror / key[0] / ID / "tool-results" / "toolu_1.txt"
    source.write_text("OUTPUT\n")
    os.utime(source, (then, then))
    _, changed = _copy(tmp_path, mirror, [key])
    assert changed and saved.read_text() == "OUTPUT\n" and int(saved.stat().st_mtime) == int(then)
    # A copy whose time alone moved is given its transcript's again, and not rewritten.
    os.utime(saved, (then - 999, then - 999))
    _, changed = _copy(tmp_path, mirror, [key])
    assert changed and int(saved.stat().st_mtime) == int(then)
    assert not _copy(tmp_path, mirror, [key])[1]


AGENT = "agent-a9f3c2e1b0d4"


def _with_subagent(mirror, key, data=None):
    """The session's subagents/ in the mirror: one subagent's transcript, as Claude Code writes one, and its meta."""
    agents = mirror / key[0] / key[1] / "subagents"
    (agents / "agent-1.jsonl").unlink()
    (agents / f"{AGENT}.jsonl").write_bytes(transcript() if data is None else data)
    (agents / f"{AGENT}.meta.json").write_text('{"agentType":"general-purpose"}\n')
    return agents


def test_a_session_s_subagents_go_with_its_copy_with_the_memory_edit_and_none_of_its_entries(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, then = _mirror(tmp_path, {key: transcript()})
    _with_subagent(mirror, key, transcript(partial=True))
    result, changed = _copy(tmp_path, mirror, [key], subagents=True)
    assert changed and result["subagents"] == {"written": 2, "left": 0, "refused": 0}
    agents = tmp_path / "vm" / ".claude" / "projects" / key[0] / ID / "subagents"
    copied = agents / f"{AGENT}.jsonl"
    # Its complete lines, its prompt pointed at the VM's memory folder, and no relocated or worktree entry.
    edited, _ = copy.edit_transcript(transcript(), ID, CHECKOUT, VM_MEMORY, HERE, own=False)
    assert copied.read_bytes() == edited and MAC_MEMORY.encode() not in edited
    assert [entry["type"] for entry in lines(edited)] == [entry["type"] for entry in lines(transcript())]
    assert (agents / f"{AGENT}.meta.json").read_text() == '{"agentType":"general-purpose"}\n'
    assert int(copied.stat().st_mtime) == int(then) and stat.S_IMODE(copied.stat().st_mode) == 0o600
    recorded = json.loads((tmp_path / "ws" / "manifest.json").read_text())["subagents"]
    assert sorted(recorded) == [f"{key[0]}/{ID}/subagents/{AGENT}.jsonl", f"{key[0]}/{ID}/subagents/{AGENT}.meta.json"]
    # The sessions record is what it was: a reader of it alone sees only sessions.
    assert copy.read_manifest(str(tmp_path / "ws" / "manifest.json")) == {
        f"{key[0]}/{ID}.jsonl": copy.file_digest(str(tmp_path / "vm" / ".claude" / "projects" / key[0] / f"{ID}.jsonl"))
    }
    again, changed = _copy(tmp_path, mirror, [key], subagents=True)
    assert not changed and again["subagents"] == {"written": 0, "left": 0, "refused": 0}


def test_without_subagents_none_is_copied(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    _with_subagent(mirror, key)
    result, _ = _copy(tmp_path, mirror, [key])
    assert result["subagents"] == {"written": 0, "left": 0, "refused": 0}
    assert not (tmp_path / "vm" / ".claude" / "projects" / key[0] / ID / "subagents").exists()


def test_a_subagent_continued_on_the_vm_is_left_and_one_still_as_written_is_replaced(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    source = _with_subagent(mirror, key)
    _copy(tmp_path, mirror, [key], subagents=True)
    copied = tmp_path / "vm" / ".claude" / "projects" / key[0] / ID / "subagents" / f"{AGENT}.jsonl"
    # It grew here: still as written there, so replaced.
    (source / f"{AGENT}.jsonl").write_bytes(transcript() + transcript())
    result, changed = _copy(tmp_path, mirror, [key], subagents=True)
    assert changed and result["subagents"]["written"] == 1 and copied.read_bytes().count(b"prompt_snapshot") == 2
    # Continued on the VM, by SendMessage after a resume: left, and counted.
    continued = copied.read_bytes() + b'{"type":"user","message":{"content":"on the VM"}}\n'
    copied.write_bytes(continued)
    (source / f"{AGENT}.jsonl").write_bytes(transcript() * 3)
    result, _ = _copy(tmp_path, mirror, [key], subagents=True)
    assert result["subagents"] == {"written": 0, "left": 1, "refused": 0} and copied.read_bytes() == continued


def test_a_subagent_naming_its_memory_folder_in_a_form_just_code_does_not_know_is_not_copied(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    _with_subagent(mirror, key, transcript().replace(b"persistent file-based memory at", b"memory, kept at"))
    result, _ = _copy(tmp_path, mirror, [key], subagents=True)
    assert result["subagents"] == {"written": 1, "left": 0, "refused": 1}
    agents = tmp_path / "vm" / ".claude" / "projects" / key[0] / ID / "subagents"
    assert not (agents / f"{AGENT}.jsonl").exists() and (agents / f"{AGENT}.meta.json").exists()


def test_a_subagent_link_in_the_mirror_or_in_its_place_there_is_never_followed(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    source = _with_subagent(mirror, key)
    outside = tmp_path / "outside.txt"
    outside.write_text("not a subagent's\n")
    (source / "agent-link.jsonl").symlink_to(outside)
    agents = tmp_path / "vm" / ".claude" / "projects" / key[0] / ID / "subagents"
    agents.mkdir(parents=True)
    (agents / f"{AGENT}.meta.json").symlink_to(outside)
    result, _ = _copy(tmp_path, mirror, [key], subagents=True)
    assert result["subagents"] == {"written": 1, "left": 1, "refused": 0}
    assert not (agents / "agent-link.jsonl").exists() and outside.read_text() == "not a subagent's\n"


def test_a_session_moved_here_takes_its_subagents_along_and_leaves_none_behind(tmp_path):
    old, new = ("-Users-me-demo", ID), ("-Users-me-demo-src", ID)
    mirror, _ = _mirror(tmp_path, {old: transcript()})
    _with_subagent(mirror, old)
    _copy(tmp_path, mirror, [old], subagents=True)
    os.renames(mirror / old[0] / f"{ID}.jsonl", mirror / new[0] / f"{ID}.jsonl")
    os.renames(mirror / old[0] / ID, mirror / new[0] / ID)
    result, _ = _copy(tmp_path, mirror, [new], subagents=True)
    projects = tmp_path / "vm" / ".claude" / "projects"
    assert result["written"] == [ID] and not (projects / old[0]).exists()
    assert (projects / new[0] / ID / "subagents" / f"{AGENT}.jsonl").exists()
    recorded = json.loads((tmp_path / "ws" / "manifest.json").read_text())["subagents"]
    assert sorted(recorded) == [f"{new[0]}/{ID}/subagents/{AGENT}.jsonl", f"{new[0]}/{ID}/subagents/{AGENT}.meta.json"]


def test_a_subagent_file_whose_write_fails_is_left_and_not_recorded(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    source = _with_subagent(mirror, key)
    (tmp_path / "vm").mkdir()
    (tmp_path / "vm" / "agents").mkdir(0o500)
    recorded, seen = {}, {}
    try:
        if os.access(tmp_path / "vm" / "agents", os.W_OK):
            pytest.skip("a folder mode 0500 is still written here (root)")
        # The folder may not be written: each write fails, is left, and nothing says it was written.
        counts, changed = copy._copy_subagents(
            str(source),
            str(tmp_path / "vm" / "agents"),
            "p",
            lambda data: (data, ""),
            time.time(),
            {},
            recorded,
            False,
            [],
            seen,
            "",
            str(tmp_path / "vm"),
        )
    finally:
        (tmp_path / "vm" / "agents").chmod(0o700)
    assert counts == {"written": 0, "left": 2, "refused": 0} and not changed and recorded == {} and seen == {}


@pytest.mark.parametrize("part", ["subagents", "tool-results"])
def test_a_file_the_vm_put_where_a_session_s_folder_goes_is_left_and_the_rest_goes_on(tmp_path, part):
    keys = [("-Users-me-demo", ID), ("-Users-me-other", OTHER)]
    mirror, _ = _mirror(tmp_path, {keys[0]: transcript(), keys[1]: transcript(OTHER)})
    _copy(tmp_path, mirror, keys, subagents=True)
    there = tmp_path / "vm" / ".claude" / "projects" / keys[0][0] / ID / part
    shutil.rmtree(there)
    there.write_text("the VM's\n")
    (mirror / keys[1][0] / OTHER / part / "new.txt").write_text("new\n")
    result, _ = _copy(tmp_path, mirror, keys, subagents=True)
    assert there.read_text() == "the VM's\n" and sorted(result["unchanged"]) == sorted([ID, OTHER])
    assert (tmp_path / "vm" / ".claude" / "projects" / keys[1][0] / OTHER / part / "new.txt").exists()


def test_a_session_left_out_for_now_keeps_its_copy_in_the_mirror(tmp_path):
    keys = [("-Users-me-demo", ID), ("-Users-me-other", OTHER)]
    mirror, _ = _mirror(tmp_path, {keys[0]: transcript(), keys[1]: transcript(OTHER)})
    _, changed = _copy(tmp_path, mirror, keys[:1], keep=[f"{keys[1][0]}/{OTHER}"])
    assert (mirror / keys[1][0] / f"{OTHER}.jsonl").exists() and (mirror / keys[1][0] / OTHER).is_dir()
    _copy(tmp_path, mirror, keys[:1])
    assert not (mirror / keys[1][0]).exists()


@pytest.mark.parametrize("what", ["mirror", "manifest"])
def test_a_link_for_the_mirror_or_no_folder_for_the_manifest_is_refused_and_nothing_is_pruned(tmp_path, what):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "src").mkdir(parents=True)
    (elsewhere / "src" / "main.py").write_text("print('mine')\n")
    if what == "mirror":
        os.rename(mirror, tmp_path / "real-mirror")
        mirror.symlink_to(elsewhere, target_is_directory=True)
    else:
        (tmp_path / "ws").write_text("in the way\n")
    result, changed = _copy(tmp_path, mirror, [key])
    assert result["refused"] and not changed and not (tmp_path / "vm").exists()
    assert (elsewhere / "src" / "main.py").read_text() == "print('mine')\n"


def test_a_file_unchanged_at_both_ends_since_it_was_copied_is_not_read_again(tmp_path, monkeypatch):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    _with_subagent(mirror, key)
    _copy(tmp_path, mirror, [key], subagents=True)
    read = []
    monkeypatch.setattr(copy, "file_digest", lambda path: read.append(path) or reader_digest(path))
    result, changed = _copy(tmp_path, mirror, [key], subagents=True)
    assert not changed and result["unchanged"] == [ID]
    # The transcript itself is compared, as ever; its tool results and subagents, by their sizes and times alone.
    assert all(path.endswith(f"{ID}.jsonl") for path in read), read
    # One changed in the mirror is read and written again.
    (mirror / key[0] / ID / "tool-results" / "toolu_1.txt").write_text("output, longer\n")
    _, changed = _copy(tmp_path, mirror, [key], subagents=True)
    there = tmp_path / "vm" / ".claude" / "projects" / key[0] / ID / "tool-results" / "toolu_1.txt"
    assert changed and there.read_text() == "output, longer\n"


def reader_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_check_mode_writes_no_subagent_either(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    _with_subagent(mirror, key)
    result, changed = _copy(tmp_path, mirror, [key], check=True, subagents=True)
    assert changed and result["subagents"]["written"] == 2
    assert not (tmp_path / "vm").exists() and not (tmp_path / "ws").exists()


def test_check_mode_writes_nothing(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    result, changed = _copy(tmp_path, mirror, [key], check=True)
    assert changed and result["written"] == [ID]
    assert not (tmp_path / "vm").exists() and not (tmp_path / "ws").exists()


def test_sessions_that_left_the_selection_leave_the_mirror(tmp_path):
    keys = [("-Users-me-demo", ID), ("-Users-me-other", OTHER)]
    mirror, _ = _mirror(tmp_path, {keys[0]: transcript(), keys[1]: transcript(OTHER)})
    _, changed = _copy(tmp_path, mirror, keys[:1])
    assert changed
    assert sorted(path.name for path in mirror.iterdir()) == ["-Users-me-demo"]


def test_the_module_takes_only_a_session_s_folder_and_id(tmp_path, patch_module):
    from conftest import fail_main

    args = {
        "mirror": str(tmp_path / "mirror"),
        "dest": str(tmp_path / ".claude"),
        "manifest": str(tmp_path / "manifest.json"),
        "sessions": [{"id": ID, "folder": "../../etc", "checkout": CHECKOUT, "memory": VM_MEMORY}],
    }
    assert "is not a session's folder and ID" in fail_main(copy.main, args)["msg"]


# ------------------------------------------------------------------------ claude_sessions_fetch ---
FOLDER = "-home-dev-projects-demo"
ASKED = [f"{FOLDER}/{ID}.jsonl", f"{FOLDER}/{ID}", f"{FOLDER}/memory"]


def _member(name, kind=tarfile.REGTYPE, data=b"", **fields):
    info = tarfile.TarInfo(name)
    info.type, info.size, info.mtime = kind, len(data), 1_700_000_000
    for key, value in fields.items():
        setattr(info, key, value)
    return info, io.BytesIO(data) if kind in (tarfile.REGTYPE, tarfile.AREGTYPE) else None


def _archive(*members):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for info, data in members:
            archive.addfile(info, data)
    out.seek(0)
    return out


def _tree(path):
    return sorted(str(item.relative_to(path)) for item in path.rglob("*"))


def test_only_plain_files_and_folders_at_the_names_asked_for_are_unpacked(tmp_path):
    # Whatever the VM sends: nothing lands outside the folder, and nothing there is a link.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "target").write_text("mine\n")
    dest = tmp_path / "dest"
    dest.mkdir(mode=0o700)
    stream = _archive(
        _member(f"./{FOLDER}/{ID}.jsonl", data=b'{"type":"user"}\n'),
        _member(f"./{FOLDER}/{ID}", tarfile.DIRTYPE),
        _member(f"./{FOLDER}/{ID}/subagents/agent-1.jsonl", data=b"{}\n"),
        _member(f"./{FOLDER}/memory/MEMORY.md", data=b"- a note\n"),
        _member(f"{tmp_path}/outside/abs", data=b"x"),
        _member(f"./{FOLDER}/../../outside/up", data=b"x"),
        _member(f"./{FOLDER}/{ID}/link", tarfile.SYMTYPE, linkname=str(outside / "target")),
        _member(f"./{FOLDER}/{ID}/hard", tarfile.LNKTYPE, linkname=f"./{FOLDER}/{ID}.jsonl"),
        _member(f"./{FOLDER}/memory/pipe", tarfile.FIFOTYPE),
        _member(f"./{FOLDER}/memory/null", tarfile.CHRTYPE, devmajor=1, devminor=3),
        _member(f"./{FOLDER}/{OTHER}.jsonl", data=b"not asked for\n"),
        _member(f"./{FOLDER}/{ID}.jsonl/inside", data=b"x"),
        _member(f"./{FOLDER}/{ID}.jsonl", data=b"twice\n"),
    )
    files, left_out, count, arrived = fetch.unpack(stream, str(dest), ASKED)
    assert files == 3 and arrived == {f"{FOLDER}/{ID}.jsonl", f"{FOLDER}/{ID}"} and count == len(left_out) == 9
    assert _tree(dest) == [
        FOLDER,
        f"{FOLDER}/{ID}",
        f"{FOLDER}/{ID}.jsonl",
        f"{FOLDER}/{ID}/subagents",
        f"{FOLDER}/{ID}/subagents/agent-1.jsonl",
        f"{FOLDER}/memory",
        f"{FOLDER}/memory/MEMORY.md",
    ]
    assert (dest / FOLDER / f"{ID}.jsonl").read_bytes() == b'{"type":"user"}\n'
    assert not any(path.is_symlink() for path in dest.rglob("*"))
    assert _tree(outside) == ["target"] and (outside / "target").read_text() == "mine\n"
    assert [item["why"] for item in left_out] == [
        "not a path inside the folder",
        "not a path inside the folder",
        "a link",
        "a link",
        "not a plain file or folder",
        "not a plain file or folder",
        "not asked for",
        "not asked for",
        "named twice, or a folder of that name came first",
    ]
    for path in dest.rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600), path
    assert int((dest / FOLDER / f"{ID}.jsonl").stat().st_mtime) == 1_700_000_000


def test_a_transcript_asked_for_must_arrive_as_a_file_and_a_folder_as_a_folder(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    stream = _archive(_member(f"./{FOLDER}/{ID}.jsonl", tarfile.DIRTYPE), _member(f"./{FOLDER}/memory", data=b"x"))
    _, left_out, _, arrived = fetch.unpack(stream, str(dest), ASKED)
    assert arrived == set() and _tree(dest) == []
    assert [item["why"] for item in left_out] == [
        "a folder where a transcript is asked for",
        "a file where a folder is asked for",
    ]


NOT_CLAUDE_S = "not named as Claude Code names its own"


@pytest.mark.parametrize(
    ("name", "why"),
    [
        # A .git a git run inside the saved folder would read, its hooks and fsmonitor included.
        (f"./{FOLDER}/memory/.git/config", NOT_CLAUDE_S),
        (f"./{FOLDER}/memory/.hidden", NOT_CLAUDE_S),
        (f"./{FOLDER}/.{ID}.jsonl", NOT_CLAUDE_S),
        (f"./{FOLDER}/{ID}/subagents/agent\x1b[2J.jsonl", NOT_CLAUDE_S),
        (f"./{FOLDER}/{ID}/subagents/agent\x7f.jsonl", NOT_CLAUDE_S),
        (f"./{FOLDER}/{ID}/" + "a" * 256, NOT_CLAUDE_S),
        (f"./{FOLDER}/{ID}/" + "/".join(["d"] * 15) + "/f", "deeper than a session's folder goes"),
        # A bare repository needs no dot: HEAD beside objects/ and refs/ is one to git, whose config a git run in the
        # folder reads. In any case, since a file system that ignores it takes head for HEAD.
        (f"./{FOLDER}/memory/HEAD", "named as git names a repository's own"),
        (f"./{FOLDER}/memory/sub/head", "named as git names a repository's own"),
    ],
)
def test_a_name_claude_code_never_writes_is_left_out(tmp_path, name, why):
    dest = tmp_path / "dest"
    dest.mkdir()
    stream = _archive(_member(f"./{FOLDER}/memory/MEMORY.md", data=b"- a note\n"), _member(name, data=b"x"))
    files, left_out, count, _ = fetch.unpack(stream, str(dest), ASKED)
    assert files == 1 and count == 1
    # Said as the module keeps it: cut to NAME_SAID, a character that is not printable as ?.
    said = "".join(ch if ch.isprintable() else "?" for ch in name[: fetch.NAME_SAID])
    assert left_out == [{"name": said, "why": why}]
    assert _tree(dest) == [FOLDER, f"{FOLDER}/memory", f"{FOLDER}/memory/MEMORY.md"]


def test_a_name_as_deep_and_as_long_as_claude_code_s_own_may_be_is_written(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    deep = f"./{FOLDER}/{ID}/" + "/".join(["d"] * 13) + "/" + "a" * 255
    files, left_out, _, _ = fetch.unpack(_archive(_member(deep, data=b"x")), str(dest), ASKED)
    assert files == 1 and left_out == []


def test_only_the_first_50_entries_left_out_are_named_and_every_one_counted(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    stream = _archive(*(_member(f"./{FOLDER}/{OTHER}-{n}.jsonl", data=b"x") for n in range(120)))
    files, left_out, count, _ = fetch.unpack(stream, str(dest), ASKED)
    assert files == 0 and count == 120 and len(left_out) == fetch.LISTED == 50
    assert left_out[-1] == {"name": f"./{FOLDER}/{OTHER}-49.jsonl", "why": "not asked for"}


def test_a_name_left_out_is_said_by_its_start_as_plain_text(tmp_path):
    # Each of the 50 names kept goes into the module's result: a megabyte each would be 50 of them.
    dest = tmp_path / "dest"
    dest.mkdir()
    long = f"./{FOLDER}/{OTHER}-" + "x\x1b" * 400 + ".jsonl"
    _, left_out, count, _ = fetch.unpack(_archive(_member(long, data=b"x")), str(dest), ASKED)
    assert count == 1 and len(left_out[0]["name"]) == fetch.NAME_SAID and "\x1b" not in left_out[0]["name"]


def test_a_path_longer_than_this_machine_takes_is_left_out_not_the_save_failed(tmp_path, monkeypatch):
    import errno

    def too_long(*args):
        raise OSError(errno.ENAMETOOLONG, "File name too long")

    monkeypatch.setattr(fetch, "_write", too_long)
    dest = tmp_path / "dest"
    dest.mkdir()
    files, left_out, count, _ = fetch.unpack(_archive(_member(f"./{FOLDER}/memory/a.md", data=b"x")), str(dest), ASKED)
    assert files == 0 and count == 1 and left_out[0]["why"] == "a path longer than this machine takes"


def test_a_global_header_stops_the_save_whatever_its_size(tmp_path):
    # Its records go on every entry after it, a copy each: a few thousand entries after one of a megabyte is gigabytes.
    info, data = _member(f"./{FOLDER}/memory/MEMORY.md", data=b"x")
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT, pax_headers={"comment": "x" * 100}) as archive:
        archive.addfile(info, data)
    out.seek(0)
    with pytest.raises(fetch.Stopped, match="^the VM sent a global header"):
        fetch.unpack(out, str(tmp_path), ASKED)


def test_no_entry_read_is_kept_once_it_is_unpacked(tmp_path, monkeypatch):
    # tarfile keeps every entry it reads in its members: one each of a few hundred thousand.
    kept = []
    original = tarfile.TarFile.next

    def counting(self):
        kept.append(len(self.members))
        return original(self)

    monkeypatch.setattr(tarfile.TarFile, "next", counting)
    stream = _archive(*(_member(f"./{FOLDER}/memory/{n}.md", data=b"x") for n in range(50)))
    files, _, _, _ = fetch.unpack(stream, str(tmp_path), ASKED)
    assert files == 50 and max(kept) <= 1


def test_a_vm_that_sends_nothing_is_ended(tmp_path):
    # One block and then silence would otherwise hold just destroy, and the run lock, until Ctrl-C.
    import time

    class Process:
        killed = False

        def kill(self):
            self.killed = True

    process, stream = Process(), fetch._Counted(io.BytesIO(b""), 1)
    with fetch._Watch(process, stream, idle=1, whole=60) as watch:
        for _ in range(40):
            if process.killed:
                break
            time.sleep(0.1)
    assert process.killed and watch.why == "the VM sent nothing for 1 s"


def test_more_of_files_than_the_cap_stops_the_save_before_the_file_that_passes_it_is_written(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    stream = _archive(
        _member(f"./{FOLDER}/memory/a.md", data=b"x" * 600), _member(f"./{FOLDER}/memory/b.md", data=b"y" * 600)
    )
    with pytest.raises(fetch.Stopped, match="^the VM sent more than the 1000 bytes of files destroy_sessions_max_size"):
        fetch.unpack(stream, str(dest), ASKED, most=1000)
    assert _tree(dest) == [FOLDER, f"{FOLDER}/memory", f"{FOLDER}/memory/a.md"]


def test_a_file_longer_than_the_room_left_is_cut_off_and_stops_the_save(tmp_path):
    with pytest.raises(fetch.Stopped):
        fetch._write(str(tmp_path), "f", io.BytesIO(b"x" * 100), 0, 99)
    assert (tmp_path / "f").stat().st_size == 0


def test_a_stream_that_goes_on_past_its_bound_stops_the_save():
    stream = fetch._Counted(io.BytesIO(b"x" * 2048), 1024)
    assert stream.read(1024) == b"x" * 1024
    with pytest.raises(fetch.Stopped, match="^the VM sent more than 1K in all$"):
        stream.read(1)


def test_more_entries_than_a_save_may_hold_stops_it(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "MOST_ENTRIES", 5)
    dest = tmp_path / "dest"
    dest.mkdir()
    stream = _archive(*(_member(f"./{FOLDER}/memory/{n}.md", data=b"x") for n in range(6)))
    with pytest.raises(fetch.Stopped, match="^the VM sent more than 5 entries$"):
        fetch.unpack(stream, str(dest), ASKED)


def test_a_save_takes_up_to_200000_entries():
    assert fetch.MOST_ENTRIES == 200_000 and fetch.MOST_BYTES == 4 << 30 and fetch.MOST_HEADER == 1 << 20


class _Grudging(io.BytesIO):
    """A stream that fails the test when more than ``most`` bytes of it are read."""

    def __init__(self, data, most):
        super().__init__(data)
        self.most, self.taken = most, 0

    def read(self, size=-1):
        data = super().read(size)
        self.taken += len(data)
        assert self.taken <= self.most, f"read {self.taken} bytes of a header it should have refused"
        return data


@pytest.mark.parametrize(
    ("kind", "fmt"),
    [
        (tarfile.XHDTYPE, tarfile.USTAR_FORMAT),
        (tarfile.GNUTYPE_LONGNAME, tarfile.GNU_FORMAT),
        (tarfile.GNUTYPE_LONGLINK, tarfile.GNU_FORMAT),
    ],
)
def test_a_header_larger_than_a_name_needs_is_refused_before_it_is_read(tmp_path, kind, fmt):
    # tarfile reads a pax header or a GNU long name whole into memory before the entry it is for.
    header = tarfile.TarInfo("././@LongLink")
    header.type, header.size = kind, 2 << 20
    data = header.tobuf(fmt) + b"\0" * (2 << 20)
    stream = _Grudging(data, 64 << 10)
    with pytest.raises(fetch.Stopped, match="^the VM sent a header of 2M, more than the 1M one needs$"):
        fetch.unpack(stream, str(tmp_path), ASKED)


def test_a_header_of_exactly_the_bound_is_read(tmp_path):
    # One record of a pax header that is exactly MOST_HEADER long: "<length> comment=<value>\n", its length its own.
    info, data = _member(f"./{FOLDER}/memory/MEMORY.md", data=b"x")
    info.pax_headers = {"comment": "x" * (fetch.MOST_HEADER - 17)}
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as archive:
        archive.addfile(info, data)
    out.seek(0)
    files, _, _, _ = fetch.unpack(out, str(tmp_path), ASKED)
    assert files == 1


def test_a_header_a_name_fits_in_is_read():
    # The check is in tarfile's own hook, which a Python could move; a long name must still arrive.
    out = io.BytesIO()
    name = f"./{FOLDER}/{ID}/" + "/".join(["a" * 200] * 3)
    with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as archive:
        archive.addfile(*_member(name, data=b"x"))
    out.seek(0)
    with tarfile.open(fileobj=out, mode="r|", tarinfo=fetch._Header) as archive:
        assert [member.name for member in archive] == [name]
    assert "_proc_member" in vars(tarfile.TarInfo) and "_proc_gnusparse_10" in vars(tarfile.TarInfo)


def _sparse_gnu():
    info = tarfile.TarInfo(f"./{FOLDER}/memory/MEMORY.md")
    info.type = tarfile.GNUTYPE_SPARSE
    return info.tobuf(tarfile.GNU_FORMAT) + b"\0" * 1024


def _sparse_pax(headers):
    info, data = _member(f"./{FOLDER}/memory/MEMORY.md", data=b"x" * 10)
    info.pax_headers = headers
    return _archive((info, data)).getvalue()


@pytest.mark.parametrize(
    "archive",
    [
        _sparse_gnu(),
        _sparse_pax({"GNU.sparse.map": "0,10", "GNU.sparse.realsize": "10"}),
        _sparse_pax({"GNU.sparse.size": "10", "GNU.sparse.offset": "0", "GNU.sparse.numbytes": "10"}),
        _sparse_pax({"GNU.sparse.major": "1", "GNU.sparse.minor": "0", "GNU.sparse.realsize": "10"}),
    ],
    ids=["gnu", "pax-0.1", "pax-0.0", "pax-1.0"],
)
def test_a_sparse_file_stops_the_save(tmp_path, archive):
    # The VM's tar makes one only with --sparse; its map can be as long as the VM makes it.
    with pytest.raises(fetch.Stopped, match="^the VM sent a sparse file"):
        fetch.unpack(io.BytesIO(archive), str(tmp_path), ASKED)


def test_a_save_past_its_cap_fails_saying_where_what_arrived_is(tmp_path):
    projects = _vm_projects(tmp_path)
    dest = tmp_path / "a" / "projects"
    with pytest.raises(
        RuntimeError, match=f"^the VM sent more than the 10 bytes of files .*; what arrived before is in {dest}$"
    ):
        fetch.fetch(str(dest), str(projects), ASKED, most=10)


@pytest.mark.parametrize(
    ("size", "cap"),
    [
        ("4G", 4 << 30),
        ("4", 4 << 30),
        (4, 4 << 30),
        ("500M", 500 << 20),
        ("1.5T", 3 << 39),
        ("64k", 64 << 10),
        ("8GiB", 8 << 30),
        (" 2 G ", 2 << 30),
        ("0", 0),
        # B alone is bytes, never the gigabytes a bare number is.
        ("500B", 500),
        ("4096b", 4096),
        (False, 0),
        ("lots", 0),
        ("4G4", 0),
        ("-1G", 0),
        ("", 0),
        (None, 0),
    ],
)
def test_the_cap_is_a_size_as_the_settings_write_one(size, cap):
    assert filters.claude_sessions_cap(size) == cap


def test_the_fetch_module_refuses_a_cap_of_nothing(tmp_path, patch_module):
    from conftest import fail_main

    args = {"dest": str(tmp_path / "a" / "projects"), "root": "/home/dev/.claude/projects", "names": ASKED}
    assert "max_bytes must be a number of bytes above 0" in fail_main(fetch.main, {**args, "max_bytes": 0})["msg"]
    assert not (tmp_path / "a").exists()


def _vm_projects(tmp_path):
    projects = tmp_path / "vm" / ".claude" / "projects"
    (projects / FOLDER / ID / "subagents").mkdir(parents=True)
    (projects / FOLDER / f"{ID}.jsonl").write_bytes(transcript())
    (projects / FOLDER / ID / "subagents" / "agent-1.jsonl").write_text("{}\n")
    (projects / FOLDER / "memory").mkdir()
    (projects / FOLDER / "memory" / "MEMORY.md").write_text("- a note\n")
    return projects


def test_a_link_the_vm_holds_is_left_there_and_what_it_points_at_here_is_untouched(tmp_path):
    projects = _vm_projects(tmp_path)
    secret = tmp_path / "secret"
    secret.write_text("mine\n")
    secret.chmod(0o644)
    (projects / FOLDER / ID / "subagents" / "link").symlink_to(secret)
    (projects / FOLDER / "memory" / "home").symlink_to(tmp_path)
    dest = tmp_path / "saved" / "alpha-1" / "projects"
    result = fetch.fetch(str(dest), str(projects), ASKED)
    assert result["files"] == 3 and sorted(item["why"] for item in result["left_out"]) == ["a link", "a link"]
    assert not any(path.is_symlink() for path in dest.rglob("*"))
    assert stat.S_IMODE(secret.stat().st_mode) == 0o644 and secret.read_text() == "mine\n"
    assert stat.S_IMODE(dest.parent.stat().st_mode) == 0o700


def test_a_name_that_did_not_arrive_or_a_folder_already_there_fails_the_fetch(tmp_path):
    projects = _vm_projects(tmp_path)
    with pytest.raises(RuntimeError, match=f"{FOLDER}/{OTHER}.jsonl did not arrive"):
        fetch.fetch(str(tmp_path / "a" / "projects"), str(projects), [*ASKED, f"{FOLDER}/{OTHER}.jsonl"])
    with pytest.raises(FileExistsError):
        fetch.fetch(str(tmp_path / "a" / "projects"), str(projects), ASKED)


@pytest.mark.parametrize(
    "name",
    [
        "../etc/passwd",
        f"{FOLDER}/{ID}/x",
        "-x/notes.txt",
        f"a b/{ID}.jsonl",
        "/etc/x",
        f"{FOLDER}/{ID}\n",
        f"{FOLDER}/memory\n",
    ],
)
def test_only_a_transcript_a_session_s_folder_or_a_memory_folder_is_asked_for(tmp_path, name):
    with pytest.raises(RuntimeError, match="is not a Claude Code transcript"):
        fetch.fetch(str(tmp_path / "a" / "projects"), "/home/dev/.claude/projects", [name])
    assert not (tmp_path / "a").exists()


def _stand_in_ssh(tmp_path, monkeypatch, body):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "ssh").write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{tmp_path}/ssh-argv"\n{body}\n')
    (bin_dir / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_over_ssh_as_ansible_reaches_the_vm_never_asking(tmp_path, monkeypatch):
    projects = _vm_projects(tmp_path)
    # The stand-in runs the command it is handed, as the VM's shell would.
    _stand_in_ssh(tmp_path, monkeypatch, 'for last; do :; done\nexec sh -c "$last"')
    reach = {"user": "dev", "host": "192.0.2.10", "port": "2222", "private_key_file": "/k", "args": "-o Foo='a b'"}
    result = fetch.fetch(str(tmp_path / "a" / "projects"), str(projects), ASKED, reach)
    assert result["files"] == 3
    argv = (tmp_path / "ssh-argv").read_text().splitlines()
    assert argv[:8] == [
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
    ]
    assert argv[9:] == ["-p", "2222", "-i", "/k", "-o", "Foo=a b", "dev@192.0.2.10", f"tar -cf - -C {projects} -T -"]


def test_an_archive_that_ends_early_or_an_ssh_that_fails_fails_the_fetch(tmp_path, monkeypatch):
    projects = _vm_projects(tmp_path)
    reach = {"user": "dev", "host": "192.0.2.10"}
    _stand_in_ssh(tmp_path, monkeypatch, f'tar -cf - -C "{projects}" "./{FOLDER}" | head -c 700')
    with pytest.raises(RuntimeError, match="not a whole archive"):
        fetch.fetch(str(tmp_path / "a" / "projects"), str(projects), ASKED, reach)
    _stand_in_ssh(tmp_path, monkeypatch, 'echo "Permission denied (publickey)." >&2\nexit 255')
    with pytest.raises(RuntimeError, match=r"Permission denied \(publickey\)"):
        fetch.fetch(str(tmp_path / "b" / "projects"), str(projects), ASKED, reach)


# The VM's tar is GNU tar, which sends what it can find and exits 2; this machine's may be bsdtar, which exits 1.
def test_gnu_tar_s_2_names_the_session_that_did_not_arrive_or_fails_the_fetch_as_tar_s(tmp_path, monkeypatch):
    projects = _vm_projects(tmp_path)
    reach = {"user": "dev", "host": "192.0.2.10"}
    sent = f'tar -cf - -C "{projects}" ' + " ".join(f'"./{name}"' for name in ASKED)
    gone = f"tar: ./{FOLDER}/{OTHER}.jsonl: Cannot stat: No such file or directory"
    _stand_in_ssh(tmp_path, monkeypatch, f'{sent}\necho "{gone}" >&2\nexit 2')
    with pytest.raises(RuntimeError, match=f"^{FOLDER}/{OTHER}.jsonl did not arrive: tar: "):
        fetch.fetch(str(tmp_path / "a" / "projects"), str(projects), [*ASKED, f"{FOLDER}/{OTHER}.jsonl"], reach)
    _stand_in_ssh(tmp_path, monkeypatch, f'{sent}\necho "tar: x: Cannot open: Permission denied" >&2\nexit 2')
    with pytest.raises(RuntimeError, match="^tar exited 2: tar: x: Cannot open"):
        fetch.fetch(str(tmp_path / "b" / "projects"), str(projects), ASKED, reach)


@pytest.mark.parametrize(
    "reach",
    [
        {"host": "-oProxyCommand=x"},
        {"host": "a b"},
        {"host": "h", "user": "-l"},
        # A word with a newline after it: an anchored match took it.
        {"host": "h\n"},
        {"host": "h", "user": "dev\n"},
    ],
)
def test_a_host_or_user_ssh_could_read_as_an_option_is_refused(reach):
    with pytest.raises(ValueError, match="is not a user and a host"):
        fetch.ssh_argv(reach, "true")


def test_the_fetch_module_in_check_mode_writes_nothing(tmp_path, patch_module):
    from conftest import fail_main

    args = {"dest": str(tmp_path / "a" / "projects"), "root": "/home/dev/.claude/projects", "names": ASKED}
    assert run_main(fetch.main, {**args, "_ansible_check_mode": True})["changed"] is True
    assert not (tmp_path / "a").exists()
    assert "could not be brought here" in fail_main(fetch.main, {**args, "names": ["../x"]})["msg"]
