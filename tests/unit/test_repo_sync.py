"""The pieces of the repository sync: lib/claude_vm/repo_sync.py, the modules git_sync_info and git_sync_apply in
playbooks/library/, and what they share, playbooks/module_utils/git_sync.py.

Which files may go, what a send carries and within which bound, and what is said. Every repository here is built for
the test in its own directory with git's config of the developer's switched off; nothing reads the developer's
checkouts. The window step that runs these (playbooks/tasks/repo_read.yml) is run in test_code_session.py.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from ansible.module_utils import git_sync as shared
from conftest import fail_main, run_main
from harness import load_module

from claude_vm import repo_sync as core

REPO = Path(__file__).resolve().parents[2]
info = load_module("playbooks/library/git_sync_info.py")

A = "a" * 40
B = "b" * 40
C = "c" * 40


@pytest.fixture(autouse=True)
def _own_git_config(monkeypatch):
    # Never the developer's own, which changes what git says and does: an init.defaultBranch, a hook, an excludesFile.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _git(path, *args, data=None):
    return subprocess.run(
        ["git", "-C", str(path), *args], input=data, capture_output=True, text=True, check=True
    ).stdout.strip()


def _repository(path, origin="git@github.com:octo/demo.git", ignore=""):
    path.mkdir(parents=True)
    _git(path, "init", "--quiet", "--initial-branch", "main")
    _git(path, "config", "user.email", "dev@example.com")
    _git(path, "config", "user.name", "Dev")
    (path / ".gitignore").write_text(ignore)
    (path / "README").write_text("hello\n")
    _git(path, "add", "-A")
    _git(path, "commit", "--quiet", "-m", "first")
    if origin:
        _git(path, "remote", "add", "origin", origin)
    return path


def _write(root, name, content="x\n"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return path


def _read(path, **params):
    return run_main(info.main, {"path": str(path), **params})["repo"]


# ------------------------------------------------------------------------------------------- the match ---
HERE = {"git": True, "toplevel": "/Users/me/code/demo", "origin": "git@github.com:Octo/demo.git"}
THERE = {"git": True, "at_top": True, "toplevel": "/home/dev/projects/demo", "origin": "https://github.com/octo/demo"}


def _match(here=HERE, there=THERE, elsewhere=True):
    return core.repo_sync_match(here, there, "demo", "alpha", "/home/dev/projects/demo", elsewhere)


def test_one_repository_by_its_origins_is_synced_whatever_the_folders_are_called():
    found = _match()
    assert found == {"sync": True, "repository": "octo/demo", "source": "/Users/me/code/demo", "said": ""}
    # Through an SSH alias of the user's own here: the host is not compared.
    assert _match({**HERE, "origin": "gh-work:octo/demo.git"})["sync"]


@pytest.mark.parametrize(
    ("here", "there", "said"),
    [
        pytest.param(
            {**HERE, "origin": "git@github.com:octo/other.git"},
            THERE,
            "/Users/me/code/demo is a checkout of octo/other, and /home/dev/projects/demo on alpha of octo/demo",
            id="another-repository",
        ),
        pytest.param({**HERE, "origin": ""}, THERE, "/Users/me/code/demo has no origin to match it by", id="no-origin"),
        pytest.param(HERE, {**THERE, "at_top": False}, "on alpha is not a git checkout", id="inside-another"),
        pytest.param(HERE, {"git": False}, "on alpha is not a git checkout", id="no-checkout"),
        # On the VM only a GitHub origin names a repository, as just deploy clones it.
        pytest.param(HERE, {**THERE, "origin": "/srv/demo.git"}, "on alpha has no GitHub origin", id="not-github"),
    ],
)
def test_another_repository_is_never_synced_and_said_once_started_elsewhere(here, there, said):
    found = _match(here, there)
    assert not found["sync"]
    assert said in found["said"]
    assert found["said"].endswith("so nothing of /Users/me/code/demo was carried to demo there (repo_sync).")
    # Started in this checkout, with another project's name, as every run is: nothing to say.
    assert _match(here, there, elsewhere=False)["said"] == ""


def test_no_checkout_here_is_no_sync_and_nothing_said():
    assert _match({"git": False}) == {"sync": False, "repository": "", "source": "", "said": ""}


# -------------------------------------------------------------------------------------------- the refs ---
def test_a_send_carries_this_machine_s_branches_and_tags_each_named_in_full_into_the_sync_s_own_refs():
    selected = {"branches": {"main": A, "feat/x": B}, "tags": {"v1": C}, "head": {"branch": "main", "commit": A}}
    assert core.repo_sync_refs(selected) == [
        "refs/heads/feat/x:refs/agent-warehouse/sent/heads/feat/x",
        "refs/heads/main:refs/agent-warehouse/sent/heads/main",
        "refs/tags/v1:refs/agent-warehouse/sent/tags/v1",
    ]
    assert core.repo_sync_sources(selected) == ["refs/heads/feat/x", "refs/heads/main", "refs/tags/v1"]


def test_a_detached_head_goes_by_its_commit():
    selected = {"branches": {"main": A}, "head": {"branch": "", "commit": B}}
    assert core.repo_sync_refs(selected)[-1] == f"{B}:refs/agent-warehouse/sent/HEAD"


@pytest.mark.parametrize(
    "name", ["x\nrefs/stash:refs/agent-warehouse/sent/heads/y", "-x", "a b", "a:b", "x~1", "a\x7fb", ""]
)
def test_a_name_that_could_be_read_as_something_else_is_left_out(name):
    # Never a second line of send-pack's input, an option, or a source that names another object.
    assert core.repo_sync_refs({"branches": {name: A, "main": B}}) == [
        "refs/heads/main:refs/agent-warehouse/sent/heads/main"
    ]


def test_a_branch_whose_commit_is_no_commit_id_is_left_out():
    assert core.repo_sync_refs({"branches": {"main": "HEAD", "x": A + "\n"}}) == []


# -------------------------------------------------------------------------------------------- the plan ---
MATCH = {"sync": True, "repository": "octo/demo", "source": "/Users/me/code/demo", "said": ""}


def _selected(**extra):
    return {
        "branches": {"main": A},
        "tags": {},
        "head": {"branch": "main", "commit": A},
        "operation": "",
        "changes": {"modified": [], "deleted": [], "new": [], "links": []},
        "files": [],
        "left": [],
        "included": [],
        "stashes": 0,
        "worktrees": 0,
    } | extra


def _file(path, size, kind="ignored", entry=None):
    return {"path": path, "kind": kind, "size": size, "entry": entry or path}


def test_no_match_plans_nothing_and_says_its_one_line():
    plan = core.repo_sync_plan(_selected(), {"sync": False, "said": "why"})
    assert (plan["sync"], plan["refs"], plan["files"], plan["why"]) == (False, [], [], "why")
    assert core.repo_sync_said(plan, "alpha", "demo") == "why"


def test_branches_past_the_bound_are_not_sent_and_said_with_their_size():
    plan = core.repo_sync_plan(_selected(), MATCH, cap=1000, send_bytes=3_000_000)
    assert not plan["send"] and plan["refs"]
    assert {"what": "1 branch", "why": "3.0 MB of git objects to send, past repo_sync_max_size"} in plan["left"]
    assert core.repo_sync_plan(_selected(), MATCH, cap=0, send_bytes=3_000_000)["send"]


def test_ignored_files_go_single_files_first_then_folders_smallest_first_until_the_bound():
    files = [
        _file("notes.md", 10),
        _file("big/a", 50, entry="big/"),
        _file("big/b", 50, entry="big/"),
        _file("small/a", 30, entry="small/"),
        _file("z.md", 20),
    ]
    plan = core.repo_sync_plan(_selected(files=files), MATCH, cap=40)
    assert plan["ignored"] == ["notes.md", "z.md"]
    plan = core.repo_sync_plan(_selected(files=files), MATCH, cap=60)
    assert plan["ignored"] == ["notes.md", "small/a", "z.md"] and plan["file_bytes"] == 60
    assert {"what": "big/ (2 files, 100 B)", "why": "past repo_sync_max_size"} in plan["left"]
    assert core.repo_sync_plan(_selected(files=files), MATCH)["file_bytes"] == 160


def test_the_changes_go_first_all_or_none():
    changes = {"modified": ["a.py"], "deleted": ["gone.py"], "new": ["b.py"], "links": []}
    files = [_file("a.py", 40, "change"), _file("b.py", 40, "new"), _file("doc.md", 10)]
    plan = core.repo_sync_plan(_selected(changes=changes, files=files), MATCH, cap=100)
    assert plan["tree"]["bring"] and plan["files"] == ["a.py", "b.py", "doc.md"]
    plan = core.repo_sync_plan(_selected(changes=changes, files=files), MATCH, cap=50)
    assert not plan["tree"]["bring"] and "past repo_sync_max_size" in plan["tree"]["why"]
    # The ignored files then have the bound to themselves.
    assert plan["files"] == ["doc.md"] and plan["tree"]["deleted"] == []


@pytest.mark.parametrize(
    ("extra", "why"),
    [
        pytest.param({"operation": "rebase"}, "a rebase is under way in /Users/me/code/demo", id="rebase"),
        pytest.param({"head": {"branch": "", "commit": ""}}, "has no commit yet", id="no-commit"),
    ],
)
def test_the_tree_stays_for_this_machine_s_own_reasons(extra, why):
    plan = core.repo_sync_plan(_selected(**extra), MATCH)
    assert not plan["tree"]["bring"] and why in plan["tree"]["why"]


def test_a_new_file_the_selection_leaves_stays_here_said_and_the_rest_of_the_changes_go():
    # The reader leaves credentials.json out of the changes and says why; what it does carry goes.
    changes = {"modified": ["a.py"], "deleted": [], "new": ["b.py"], "links": []}
    left = [{"path": "credentials.json", "why": shared.WHY["credential"], "files": 1, "size": 3}]
    files = [_file("a.py", 4, "change"), _file("b.py", 4, "new")]
    plan = core.repo_sync_plan(_selected(changes=changes, files=files, left=left), MATCH)
    assert plan["tree"]["bring"] and plan["tree"]["new"] == ["b.py"] and plan["files"] == ["a.py", "b.py"]
    applied = {"branches": {"same": ["main"]}, "tree": {"brought": True, "changes": 2}}
    said = core.repo_sync_said(plan, "alpha", "demo", applied)
    assert "2 changes not committed" in said and "Left here: credentials.json (a credential by its name" in said


def test_with_session_the_tree_is_session_s_to_move():
    plan = core.repo_sync_plan(_selected(), MATCH, session_branch="feat/x")
    assert not plan["tree"]["bring"] and plan["session_branch"] == "feat/x"


def test_what_was_carried_and_what_is_left_is_said_with_the_switch_that_turns_it_off():
    left = [{"path": ".env", "why": shared.WHY["credential"], "files": 1, "size": 9}]
    left.append({"path": "node_modules/", "why": shared.WHY["derived"], "files": 1203, "size": 9})
    left.append({"path": ".claude/worktrees/", "why": shared.WHY["repository"], "files": 0, "repositories": 25})
    selected = _selected(files=[_file("docs/plan-a.md", 10)], left=left, stashes=1, tags={"v1": C})
    applied = {"branches": {"same": ["main"]}, "tree": {"brought": True}, "files": {"written": ["docs/plan-a.md"]}}
    said = core.repo_sync_said(core.repo_sync_plan(selected, MATCH), "alpha", "demo", applied)
    assert said.startswith(
        "demo on alpha has /Users/me/code/demo's 1 branch and 1 tag and 1 ignored file (docs/plan-a.md), its checkout"
        " on main at aaaaaaaa."
    ), said
    assert "Left here: .env (a credential by its name" in said and "node_modules/ (1203 files) (derived" in said
    # A repository inside counts as one, never as a file.
    assert ".claude/worktrees/ (25 repositories) (a repository inside the repository)" in said
    assert "1 stash" in said and said.endswith(" repo_sync: false turns this off.")
    # The words are of what the apply did: with nothing applied, nothing was carried.
    assert core.repo_sync_said(core.repo_sync_plan(selected, MATCH), "alpha", "demo").startswith(
        "demo on alpha has /Users/me/code/demo's 1 branch and 1 tag."
    )


def test_single_files_go_in_path_order_whatever_their_sizes():
    # The bound takes the entries that are single files by their paths, as §7.18 says, and only then folders.
    files = [_file("a-big.md", 35), _file("notes.md", 10), _file("z.md", 20)]
    plan = core.repo_sync_plan(_selected(files=files), MATCH, cap=40)
    assert plan["ignored"] == ["a-big.md"] and plan["file_bytes"] == 35


def test_the_core_and_the_apply_name_one_place_a_send_delivers():
    assert core.SENT == shared.SENT


# --------------------------------------------------------------------------------------- the selection ---
def _selection_world(root):
    """The plan's made-up checkout (checks/fixes.sh, C1): 22 files git does not track, 4 to carry, 10 to leave, 8
    never to go."""
    top = _repository(
        root / "sel",
        ignore="docs/plan-*.md\nbackup/\nscratch/\nkeys/\ndeep/\nodd/\n.env\n.logs/\n.venv\n.claude/worktrees/\n",
    )
    _write(top, "docs/guide.md")
    _write(top, "sub/keep.txt")
    _git(top, "add", "-A")
    _git(top, "commit", "--quiet", "-m", "c1")
    _write(top, "docs/plan-a.md", "p\n")
    _write(top, "backup/.vault_pass", "pw\n")
    _write(top, "backup/inventory/group_vars/all/vault.yml", "v\n")
    _write(top, "backup/readme.md", "r\n")
    _write(top, "scratch/ok.md")
    _write(top, "scratch/.env.local")
    _write(top, "scratch/node_modules/x.js")
    _write(top, "keys/notes.txt", "$ANSIBLE_VAULT;1.1;AES256\n6162\n")
    _write(top, "keys/readme.txt")
    # A key's header, put together here so that no scanner of this file takes the test for a key.
    _write(top, "deep/a/b/server.txt", "-----BEGIN OPENSSH " + "PRIVATE KEY-----\nb3Blbg==\n")
    _write(top, "deep/a/ok.txt")
    _write(top, "odd/.Vault_Pass")
    _write(top, ".env")
    _write(top, ".logs/run.log")
    _write(top, "sub/.logs/x.log")
    _write(top, "newdir/code.py")
    _write(top, "newdir2/credentials.json")
    _write(top, "newdir3/.vault_pass")
    _write(top, "newdir3/a.py")
    (top / "newlink").symlink_to("docs/guide.md")
    (top / ".venv").symlink_to("/nonexistent/pyenv")
    _git(top, "worktree", "add", "--quiet", ".claude/worktrees/w1", "-b", "w1")
    return top


class _Module:
    """What runs git for the shared code, as an AnsibleModule would: run_command and fail_json."""

    def run_command(self, args, environ_update=None, expand_user_and_vars=True, encoding="utf-8", data=None, **_):
        process = subprocess.run(
            args,
            input=data.encode() if isinstance(data, str) else data,
            capture_output=True,
            env={**os.environ, **(environ_update or {})},
            check=False,
        )
        return process.returncode, process.stdout, process.stderr

    def fail_json(self, **kwargs):
        raise AssertionError(kwargs)


def test_every_file_is_judged_on_its_own_at_any_depth_by_git_s_matcher(tmp_path):
    top = _selection_world(tmp_path)
    verdicts, *_ = shared.select(_Module(), str(top), True, [])
    said = {name.decode(): verdict for name, (verdict, _) in verdicts.items()}
    assert {name for name, verdict in said.items() if verdict == "carried"} == {
        "docs/plan-a.md",
        "newdir/code.py",
        "newlink",
        "scratch/ok.md",
    }
    assert {name for name, verdict in said.items() if verdict == "never"} == {
        ".logs/run.log",
        "backup/.vault_pass",
        "backup/inventory/group_vars/all/vault.yml",
        "deep/a/b/server.txt",
        "keys/notes.txt",
        "newdir3/.vault_pass",
        "odd/.Vault_Pass",
        "sub/.logs/x.log",
    }
    assert {name for name, verdict in said.items() if verdict == "left"} == {
        ".claude/worktrees/w1/",
        ".env",
        ".venv",
        "backup/readme.md",
        "deep/a/ok.txt",
        "keys/readme.txt",
        "newdir2/credentials.json",
        "newdir3/a.py",
        "scratch/.env.local",
        "scratch/node_modules/x.js",
    }


def test_worktreeinclude_opts_a_credential_or_a_derived_file_in_and_never_tier_0(tmp_path):
    top = _selection_world(tmp_path)
    _write(top, ".worktreeinclude", ".env\nscratch/node_modules/\nbackup/.vault_pass\n")
    verdicts, _, included, _ = shared.select(_Module(), str(top), True, [])
    said = {name.decode(): verdict for name, (verdict, _) in verdicts.items()}
    assert said[".env"] == said["scratch/node_modules/x.js"] == "carried"
    assert said["backup/.vault_pass"] == "never"
    # Named in the report every run.
    assert {name.decode() for name in included} == {".env"}


def test_repo_sync_exclude_leaves_what_it_names(tmp_path):
    top = _selection_world(tmp_path)
    verdicts, *_ = shared.select(_Module(), str(top), True, ["docs/plan-*"])
    assert verdicts[b"docs/plan-a.md"] == ("left", "excluded")


def test_without_the_ignored_files_only_the_new_ones_are_judged(tmp_path):
    top = _selection_world(tmp_path)
    verdicts, *_ = shared.select(_Module(), str(top), False, [])
    assert b"docs/plan-a.md" not in verdicts and b"newdir/code.py" in verdicts


def test_never_is_the_tracked_file_guard_less_docs_env_and_logs_with_all_of_cache(tmp_path):
    # tests/check_no_local_files.sh stops a commit of these; the sync must never carry them either, from any depth.
    names = [
        ".vault_pass", ".vault_pass.bak", "inventory/hosts.yml", "inventory/prod/hosts.yml", "inventory/prod/hosts",
        "inventory/x.ini", "inventory/prod/proxmox.yml", "inventory/group_vars/all/vault.yml",
        "inventory/group_vars/all/local.yml", "inventory/group_vars/all/.local.yml.swp",
        "inventory/group_vars/all/secret-x", "playbooks/group_vars/all/vault.yml.bak", "host_vars/pve.yml",
        "sub/host_vars/x.yml", "local.yml", "sub/local.json", "secrets.yaml", "vault-prod.yml", "hosts/nuc/hosts.yml",
        "hosts/current", "inventory/proxmox-trust.pem", ".sessions/a/b.jsonl", "sub/.sessions/x", ".logs/run.txt",
        "sub/.logs/x.txt", ".cache/status/x.json", ".cache/live/s", ".cache/run.lock", ".cache/mcp/jobs/1/out",
        ".cache/init/c.pem", ".cache/golden/k", "hosts.json", "sub/hosts.ini", ".env", "sub/.env.local", "run.log",
        "docs/plan-x.md", "src/local_settings.py", "locale.yml", "inventory/group_vars/all/defaults.yml",
        ".cache/other/file",
    ]  # fmt: skip
    top = _repository(tmp_path / "names")
    for name in names:
        _write(top, name)
    never = {name.decode() for name in shared.matching(_Module(), str(top), shared.NEVER)}
    guard = subprocess.run(
        ["bash", str(REPO / "tests" / "check_no_local_files.sh"), "--stdin"],
        input="\n".join(names) + "\n",
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    refused = {name for name in guard if not name.startswith("::")}
    # The three the plan takes out (docs/, which is what a sync is for; .env and *.log, which are tier 1), and the one
    # it adds: the whole of .cache/.
    assert refused - never == {".env", "sub/.env.local", "run.log", "docs/plan-x.md"}
    assert never - refused == {".cache/other/file"}


def test_the_guard_s_patterns_each_have_a_counterpart_on_the_never_list():
    # A pattern added to tests/check_no_local_files.sh is added here: each of its names is matched above, and this
    # holds the count, so a new one fails until both lists know it.
    text = (REPO / "tests" / "check_no_local_files.sh").read_text()
    patterns = text.split("patterns=(", 1)[1].split("\n)", 1)[0]
    rules = [line.strip() for line in patterns.splitlines() if line.strip().startswith("'")]
    assert len(rules) == 21, f"{len(rules)} patterns: give NEVER in playbooks/module_utils/git_sync.py the new one too"


# ------------------------------------------------------------------------------------------ the module ---
def test_a_checkout_is_read_by_its_top_level_origin_and_head(tmp_path, patch_module):
    top = _repository(tmp_path / "demo", "https://tok3n@github.com/octo/demo.git")
    (top / "src").mkdir()
    found = _read(top / "src")
    assert found["git"] and not found["at_top"] and Path(found["toplevel"]).resolve() == top.resolve()
    # Never what precedes the host, which can be a token.
    assert found["origin"] == "https://github.com/octo/demo.git"
    assert found["head"] == {"branch": "main", "commit": _git(top, "rev-parse", "HEAD")} and not found["shallow"]
    assert found["tips"] == [_git(top, "rev-parse", "HEAD")]
    assert _read(top)["at_top"]
    assert _read(tmp_path / "gone") == {
        "git": False,
        "at_top": False,
        "toplevel": "",
        "origin": "",
        "shallow": False,
        "head": {"branch": "", "commit": ""},
        "tips": [],
    }


def test_the_selection_says_branches_tags_changes_and_what_it_leaves(tmp_path, patch_module):
    top = _repository(tmp_path / "demo", ignore="notes/\n.env\nnode_modules/\n")
    _git(top, "branch", "feat/x")
    _git(top, "tag", "-a", "v1", "-m", "v1")
    (top / "README").write_text("changed\n")
    _write(top, "new.py")
    _write(top, "staged.py")
    _git(top, "add", "staged.py")
    _write(top, "notes/plan.md", "plan\n")
    _write(top, ".env", "TOKEN=x\n")
    for index in range(3):
        _write(top, f"node_modules/m{index}.js")
    (top / "link").symlink_to("README")
    found = _read(top, select=True)
    commit = _git(top, "rev-parse", "HEAD")
    assert found["branches"] == {"feat/x": commit, "main": commit}
    assert list(found["tags"]) == ["v1"] and found["operation"] == "" and found["worktrees"] == 0
    assert found["changes"] == {
        "modified": ["README"],
        "deleted": [],
        "new": ["new.py", "staged.py"],
        "links": [{"path": "link", "target": "README"}],
    }
    assert {(item["path"], item["kind"]) for item in found["files"]} == {
        ("README", "change"),
        ("new.py", "new"),
        ("staged.py", "new"),
        ("notes/plan.md", "ignored"),
    }
    plan_md = [item for item in found["files"] if item["path"] == "notes/plan.md"][0]
    assert plan_md["entry"] == "notes/" and plan_md["mtime"] == int((top / "notes" / "plan.md").stat().st_mtime)
    # node_modules/, wholly derived, is one name, never listed file by file: its files are not counted.
    left = {item["path"]: (item["why"], item["files"]) for item in found["left"]}
    assert left == {".env": (shared.WHY["credential"], 1), "node_modules/": (shared.WHY["derived"], 0)}


def test_a_file_staged_and_never_committed_is_held_to_the_same_names(tmp_path, patch_module):
    top = _repository(tmp_path / "demo")
    _write(top, "credentials.json", "{}\n")
    _write(top, "vault.yml", "v\n")
    _git(top, "add", "-A")
    found = _read(top, select=True)
    assert found["changes"]["new"] == []
    assert {item["path"] for item in found["left"]} == {"credentials.json", "vault.yml"}
    assert not [item for item in found["files"] if item["path"] in ("credentials.json", "vault.yml")]


def test_an_operation_under_way_is_said(tmp_path, patch_module):
    top = _repository(tmp_path / "demo")
    _git(top, "switch", "--quiet", "-c", "other")
    (top / "README").write_text("other\n")
    _git(top, "commit", "--quiet", "-am", "other")
    _git(top, "switch", "--quiet", "main")
    (top / "README").write_text("main\n")
    _git(top, "commit", "--quiet", "-am", "main")
    subprocess.run(["git", "-C", str(top), "merge", "--quiet", "other"], capture_output=True, check=False)
    assert _read(top, select=True)["operation"] == "merge"


def test_a_file_is_listed_under_the_outermost_folder_git_lists_for_it():
    # git lists a new folder and an ignored one inside it both: the outer is the unit, whatever order they come in.
    for entries in ([b"a/", b"a/b/", b"c"], [b"a/b/", b"c", b"a/"]):
        entry_of = shared.entry_index(set(entries))
        assert entry_of(b"a/b/x.txt") == b"a/" and entry_of(b"a/y") == b"a/" and entry_of(b"c") == b"c"
        assert entry_of(b"d/e") == b"d/e" and entry_of(b"a/") == b"a/"


def test_a_wholly_derived_folder_is_one_name_never_listed_file_by_file(tmp_path):
    top = _repository(tmp_path / "demo", ignore="node_modules/\nbuild/\nplans/\n")
    _write(top, "app/main.py")
    _git(top, "add", "-A")
    _git(top, "commit", "--quiet", "-m", "app")
    for index in range(50):
        _write(top, f"node_modules/m{index}/index.js")
    _write(top, "node_modules/.vault_pass", "pw\n")
    _write(top, "app/build/out.js")
    _write(top, "plans/plan.md")
    _write(top, "plans/node_modules/x.js")
    verdicts, entry_of, *_ = shared.select(_Module(), str(top), True, [])
    said = {name.decode(): verdict for name, verdict in verdicts.items()}
    # Each wholly derived folder is one name, left; inside another folder, a derived one is judged with it, file by
    # file.
    assert said["node_modules/"] == said["app/build/"] == ("left", "derived")
    assert not [name for name in said if name.startswith(("node_modules/", "app/build/")) and not name.endswith("/")]
    assert said["plans/plan.md"] == ("carried", "") and said["plans/node_modules/x.js"] == ("left", "derived")
    # A .worktreeinclude could name a file inside one: then every file is listed and judged - here, left all the same,
    # beside a vault's password.
    _write(top, ".worktreeinclude", "node_modules/m1/\n")
    verdicts, *_ = shared.select(_Module(), str(top), True, [])
    said = {name.decode(): verdict for name, verdict in verdicts.items()}
    assert said["node_modules/.vault_pass"] == ("never", "never") and "node_modules/" not in said
    assert said["node_modules/m1/index.js"] == ("left", "tainted") and said["node_modules/m2/index.js"][1] == "derived"


def test_a_vault_decrypted_in_place_is_left_and_the_rest_of_the_changes_go(tmp_path, patch_module):
    # Tracked, so no name of tier 0 is asked of it; committed as a vault, its plain text here is the vault's secrets.
    top = _repository(tmp_path / "demo")
    # Two copies of one vault are one blob: each is held back.
    for name in ("group_vars/all/vault.yml", "group_vars/copy/vault.yml"):
        _write(top, name, "$ANSIBLE_VAULT;1.1;AES256\n6162\n")
    _write(top, "files/other.vault", "$ANSIBLE_VAULT;1.1;AES256\n6364\n")
    _git(top, "add", "-A")
    _git(top, "commit", "--quiet", "-m", "vaults")
    for name in ("group_vars/all/vault.yml", "group_vars/copy/vault.yml"):
        _write(top, name, "token: hunter2\n")
    _write(top, "files/other.vault", "$ANSIBLE_VAULT;1.1;AES256\n6566\n")
    (top / "README").write_text("changed\n")
    found = _read(top, select=True)
    assert found["changes"]["modified"] == ["README", "files/other.vault"], found["changes"]
    left = {item["path"]: item["why"] for item in found["left"]}
    decrypted = shared.WHY["decrypted"]
    assert left == {"group_vars/all/vault.yml": decrypted, "group_vars/copy/vault.yml": decrypted}
    assert "group_vars/all/vault.yml" not in [item["path"] for item in found["files"]]


def test_a_file_taken_out_of_the_index_and_kept_is_no_deletion(tmp_path, patch_module):
    top = _repository(tmp_path / "demo")
    _write(top, "kept.py", "kept\n")
    _write(top, "gone.py")
    _git(top, "add", "-A")
    _git(top, "commit", "--quiet", "-m", "two")
    _git(top, "rm", "--quiet", "--cached", "kept.py")
    (top / "gone.py").unlink()
    changes = _read(top, select=True)["changes"]
    assert changes["deleted"] == ["gone.py"] and changes["new"] == ["kept.py"], changes


def test_the_estimate_counts_what_the_refs_hold_beyond_the_commits_the_other_end_has(tmp_path, patch_module):
    top = _repository(tmp_path / "demo")
    base = _git(top, "rev-parse", "HEAD")
    _git(top, "switch", "--quiet", "-c", "big")
    (top / "big.bin").write_bytes(os.urandom(200_000))
    _git(top, "add", "-A")
    _git(top, "commit", "--quiet", "-m", "big")
    whole = _read(top, estimate={"refs": ["refs/heads/big"], "known": []})["bytes"]
    beyond = _read(top, estimate={"refs": ["refs/heads/big"], "known": [base]})["bytes"]
    assert whole > beyond > 190_000
    # A commit this machine lacks, or no commit ID at all, is left out rather than failing the count.
    known = [base, "f" * 40, "HEAD", "--all"]
    assert _read(top, estimate={"refs": ["refs/heads/big"], "known": known})["bytes"] == beyond
    assert _read(top, estimate={"refs": [], "known": [base]})["bytes"] == 0


@pytest.mark.parametrize("ref", ["--all", "refs/stash", "HEAD", "refs/heads/x y", "refs/heads/x\nrefs/stash"])
def test_the_estimate_refuses_a_ref_no_send_would_carry(tmp_path, patch_module, ref):
    top = _repository(tmp_path / "demo")
    failed = fail_main(info.main, {"path": str(top), "estimate": {"refs": [ref], "known": []}})
    assert "is not a branch's, a tag's or a commit's name" in failed["msg"]


# ------------------------------------------------------------------------------------- the send and the apply ---
apply = load_module("playbooks/library/git_sync_apply.py")
IDENTITY = ["-c", "user.name=Dev", "-c", "user.email=dev@example.com"]


def _commit(path, name, content=None):
    (path / name).write_text(content if content is not None else name + "\n")
    _git(path, "add", name)
    _git(path, *IDENTITY, "commit", "--quiet", "-m", name)
    return _git(path, "rev-parse", "HEAD")


def _pair(root, depth=None):
    """An origin, this machine's clone of it ("here") and the VM's ("vm"), both on main."""
    origin = root / "origin.git"
    origin.mkdir(parents=True)
    _git(origin, "init", "--quiet", "--bare", "--initial-branch", "main")
    seed = root / "seed"
    seed.mkdir()
    _git(seed, "init", "--quiet", "--initial-branch", "main")
    _commit(seed, "README")
    _commit(seed, "second")
    _git(seed, "push", "--quiet", str(origin), "main")
    _git(root, "clone", "--quiet", str(origin), "here")
    clone = ["clone", "--quiet", *(["--depth", str(depth), f"file://{origin}"] if depth else [str(origin)]), "vm"]
    _git(root, *clone)
    for path in (root / "here", root / "vm"):
        _git(path, "config", "user.name", "Dev")
        _git(path, "config", "user.email", "dev@example.com")
    return root / "here", root / "vm"


def _send(here, vm, lines):
    """The task's own send-pack, to a local path: (its exit code, its last words)."""
    process = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(here), "send-pack", "--force", "--stdin",
         "--receive-pack=git-receive-pack", str(vm)],
        input="\n".join(lines),
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"},
        check=False,
    )  # fmt: skip
    return process.returncode, process.stderr.strip()


def _sync(here, vm, manifest, *, send=True, session_branch="", cap=0):
    """One whole sync, as the window step runs it: read here, plan, send, apply."""
    selected = _read(here, select=True)
    there = _read(vm)
    match = {"sync": True, "repository": "octo/demo", "source": str(here), "said": ""}
    plan = core.repo_sync_plan(selected, match, cap=cap, session_branch=session_branch, there=there)
    sent = _send(here, vm, plan["refs"]) if send and plan["send"] else (None, "")
    applied = run_main(
        apply.main,
        {
            "checkout": str(vm),
            "manifest": str(manifest),
            "source": str(here),
            "branches": plan["branches"],
            "tags": plan["tags"],
            "head": plan["head"],
            "session_branch": plan["session_branch"],
            "tree": plan["tree"],
            "upstreams": plan["upstreams"],
        },
    )
    return plan, sent, applied


def _branches(path):
    out = _git(path, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads")
    return dict(line.split(" ") for line in out.splitlines())


def _on(path):
    return _git(path, "symbolic-ref", "--quiet", "--short", "HEAD")


@pytest.mark.parametrize(
    ("there", "record", "behind", "ahead", "row"),
    [
        ("", "", False, False, "create"),
        (A, "", False, False, "same"),
        (B, B, True, True, "move"),
        (B, C, True, False, "forward"),
        (B, C, False, True, "ahead"),
        (B, C, False, False, "own"),
    ],
)
def test_a_branch_s_row_is_the_first_that_fits(there, record, behind, ahead, row):
    # The VM's branch; where the last sync left it; whether it is behind the commit sent (A), or ahead of it.
    assert shared.branch_row(there, record, A, behind, ahead) == row


def test_a_first_sync_creates_what_the_vm_lacks_and_switches_its_checkout(tmp_path, patch_module):
    here, vm = _pair(tmp_path)
    _git(here, "switch", "--quiet", "-c", "feat/x")
    feat = _commit(here, "feat")
    _git(here, "branch", "never-pushed")
    _git(here, "tag", "v1")
    plan, sent, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert sent[0] == 0, sent
    assert applied["branches"]["created"] == ["feat/x", "never-pushed"] and applied["branches"]["same"] == ["main"]
    assert applied["tags"]["created"] == ["v1"] and applied["tree"]["brought"]
    assert (_on(vm), _git(vm, "rev-parse", "HEAD")) == ("feat/x", feat)
    assert _git(vm, "status", "--porcelain") == ""
    # Where the last sync left each branch: the apply's own record, apart from what the send delivered.
    records = _git(vm, "for-each-ref", "--format=%(refname)", "refs/agent-warehouse/sync/heads").splitlines()
    assert records == [f"refs/agent-warehouse/sync/heads/{name}" for name in ("feat/x", "main", "never-pushed")]
    # A second run changes nothing: send-pack sends nothing, and the apply writes nothing.
    _, sent, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert sent == (0, "Everything up-to-date") and applied["changed"] is False
    assert applied["branches"]["same"] == ["feat/x", "main", "never-pushed"]


def test_send_pack_writes_nothing_here_and_runs_no_hook_of_this_machine_s(tmp_path, patch_module):
    here, vm = _pair(tmp_path)
    hook = here / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    _git(here, "config", f"url.{tmp_path / 'elsewhere'}.pushInsteadOf", str(vm))
    _commit(here, "work")
    before = sorted((str(path), path.stat().st_mtime_ns) for path in (here / ".git").rglob("*") if path.is_file())
    _, sent, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert sent[0] == 0, sent
    assert applied["branches"]["moved"] == ["main"]
    after = sorted((str(path), path.stat().st_mtime_ns) for path in (here / ".git").rglob("*") if path.is_file())
    assert after == before


def test_an_amended_branch_and_one_reset_back_follow_while_the_vm_added_nothing(tmp_path, patch_module):
    here, vm = _pair(tmp_path)
    _git(here, "branch", "side")
    _commit(here, "work")
    _sync(here, vm, tmp_path / "manifest.json")
    # Amended here: the VM's branch is where the last sync left it, and moves aside - its checkout by reset --keep,
    # so that its status stays empty.
    (here / "work").write_text("amended\n")
    _git(here, *IDENTITY, "commit", "--quiet", "-a", "--amend", "-m", "work, amended")
    amended = _git(here, "rev-parse", "HEAD")
    _git(here, "switch", "--quiet", "side")
    _commit(here, "side-work")
    _git(here, "switch", "--quiet", "main")
    _, _, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert sorted(applied["branches"]["moved"]) == ["main", "side"]
    assert _git(vm, "rev-parse", "main") == amended and _git(vm, "status", "--porcelain") == ""
    # Reset back here: the VM's branch is ahead of the commit sent, and where the last sync left it: it follows.
    _git(here, "reset", "--quiet", "--hard", "HEAD~1")
    _, _, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert applied["branches"]["moved"] == ["main"]
    assert _git(vm, "rev-parse", "main") == _git(here, "rev-parse", "main")


def test_a_branch_the_vm_is_ahead_on_or_committed_to_aside_is_left(tmp_path, patch_module):
    here, vm = _pair(tmp_path)
    for name in ("pulled", "aside"):
        _git(here, "branch", name)
    _sync(here, vm, tmp_path / "manifest.json")
    # On the VM: one branch gets a commit (ahead), the other a commit while here amends it (its own).
    for name in ("pulled", "aside"):
        _git(vm, "switch", "--quiet", name)
        _commit(vm, f"vm-{name}")
    _git(vm, "switch", "--quiet", "main")
    ahead, own = _git(vm, "rev-parse", "pulled"), _git(vm, "rev-parse", "aside")
    _git(here, "switch", "--quiet", "aside")
    _git(here, *IDENTITY, "commit", "--quiet", "--amend", "-m", "amended here")
    _git(here, "switch", "--quiet", "main")
    _, _, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert applied["branches"]["ahead"] == ["pulled"] and applied["branches"]["own"] == ["aside"]
    assert (_git(vm, "rev-parse", "pulled"), _git(vm, "rev-parse", "aside")) == (ahead, own)
    said = core.repo_sync_said(core.repo_sync_plan(_selected(), MATCH), "alpha", "demo", applied)
    assert "Left there as it is: aside (commits of its own)." in said
    assert "Ahead there, by a pull or a commit: pulled." in said
    line = core.repo_sync_next(core.repo_sync_plan(_selected(), MATCH), "alpha", "demo", applied, " --host nuc")
    assert line.startswith("demo on alpha: aside there has commits of its own, so the sync left it as it is.")
    assert line.endswith("just code alpha --project demo --host nuc")


def test_the_vm_on_main_with_feat_here_ends_on_feat_with_main_moved_in_the_same_run(tmp_path, patch_module):
    # Before the switch, main is the VM's checkout's and in use; after it, free: moved in the same run.
    here, vm = _pair(tmp_path)
    _sync(here, vm, tmp_path / "manifest.json")
    main = _commit(here, "more")
    _git(here, "switch", "--quiet", "-c", "feat")
    feat = _commit(here, "feat")
    _, _, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert (_on(vm), _git(vm, "rev-parse", "HEAD"), _git(vm, "rev-parse", "main")) == ("feat", feat, main)
    assert applied["branches"]["created"] == ["feat"] and applied["branches"]["moved"] == ["main"]


def test_a_branch_in_use_by_a_worktree_on_the_vm_is_never_moved_under_it(tmp_path, patch_module):
    # Checked out in another worktree, or being bisected there, where git worktree list calls it only "detached":
    # git branch --force refuses both, where update-ref would have moved them.
    here, vm = _pair(tmp_path)
    for index in range(3):
        _commit(here, f"more-{index}")
    for name in ("wt", "bis"):
        _git(here, "branch", name)
    _sync(here, vm, tmp_path / "manifest.json")
    _git(vm, "worktree", "add", "--quiet", str(tmp_path / "vm-wt"), "wt")
    _git(vm, "worktree", "add", "--quiet", str(tmp_path / "vm-bis"), "bis")
    _git(tmp_path / "vm-bis", "bisect", "start")
    _git(tmp_path / "vm-bis", "bisect", "bad")
    _git(tmp_path / "vm-bis", "bisect", "good", "HEAD~3")
    for name in ("wt", "bis"):
        _git(here, "branch", "--force", name, "main~1")
    before = _branches(vm)
    _, _, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert sorted(applied["branches"]["in_use"]) == ["bis", "wt"], applied
    assert {name: _branches(vm)[name] for name in ("wt", "bis")} == {name: before[name] for name in ("wt", "bis")}


def test_a_vm_checkout_with_an_edit_of_its_own_keeps_its_tree_and_its_branch(tmp_path, patch_module):
    here, vm = _pair(tmp_path)
    _sync(here, vm, tmp_path / "manifest.json")
    (vm / "README").write_text("edited on the VM\n")
    _commit(here, "more")
    _, _, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert not applied["tree"]["brought"] and applied["tree"]["why"] == "it has changes of its own"
    assert applied["branches"]["in_use"] == ["main"] and (vm / "README").read_text() == "edited on the VM\n"
    assert core.repo_sync_next(core.repo_sync_plan(_selected(), MATCH), "alpha", "demo", applied).startswith(
        "demo on alpha: its checkout there has changes of its own"
    )


def test_a_move_that_would_overwrite_what_git_does_not_track_there_is_refused(tmp_path, patch_module):
    here, vm = _pair(tmp_path)
    _sync(here, vm, tmp_path / "manifest.json")
    (vm / ".git" / "info" / "exclude").write_text("notes.txt\n")
    (vm / "notes.txt").write_text("the VM's own, ignored\n")
    _commit(here, "notes.txt", "tracked here\n")
    _, _, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert not applied["tree"]["brought"] and "notes.txt" in applied["tree"]["why"]
    assert (vm / "notes.txt").read_text() == "the VM's own, ignored\n"


def test_session_s_branch_and_the_tree_are_left_to_session(tmp_path, patch_module):
    here, vm = _pair(tmp_path)
    _git(here, "switch", "--quiet", "-c", "feat/x")
    _commit(here, "work")
    _, _, applied = _sync(here, vm, tmp_path / "manifest.json", session_branch="feat/x")
    assert "feat/x" not in _branches(vm) and _on(vm) == "main"
    assert not applied["tree"]["brought"] and "--session" in applied["tree"]["why"]


def test_a_branch_whose_commit_did_not_arrive_is_left_and_said(tmp_path, patch_module):
    here, vm = _pair(tmp_path)
    _git(here, "branch", "never-sent")
    _, _, applied = _sync(here, vm, tmp_path / "manifest.json", send=False)
    assert "never-sent" in applied["branches"]["missing"] and "never-sent" not in _branches(vm)


def test_a_detached_head_the_sync_brought_follows_once_this_machine_is_back_on_a_branch(tmp_path, patch_module):
    # Only the sync's own ref holds that commit there, which no branch, remote-tracking branch or tag does.
    here, vm = _pair(tmp_path)
    _git(here, "switch", "--quiet", "--detach", "HEAD")
    detached = _commit(here, "detached-work")
    _, _, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert applied["tree"]["brought"] and _git(vm, "rev-parse", "HEAD") == detached, applied
    _git(here, "switch", "--quiet", "main")
    _, _, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert applied["tree"]["brought"] and _on(vm) == "main", applied
    # One the VM detached at a commit of its own is still its own.
    _git(vm, "switch", "--quiet", "--detach", "HEAD")
    _commit(vm, "vm-detached")
    _, _, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert not applied["tree"]["brought"] and applied["tree"]["reason"] == "detached", applied


def test_a_shallow_clone_takes_a_branch_from_its_tip_and_refuses_one_from_behind_without_failing(
    tmp_path, patch_module
):
    here, vm = _pair(tmp_path, depth=1)
    _git(here, "switch", "--quiet", "-c", "ontip")
    _commit(here, "tip")
    _git(here, "switch", "--quiet", "-c", "behind", "main~1")
    (here / "big.bin").write_bytes(os.urandom(5000))
    _git(here, "add", "big.bin")
    _git(here, *IDENTITY, "commit", "--quiet", "-m", "behind")
    _git(here, "switch", "--quiet", "main")
    plan, sent, applied = _sync(here, vm, tmp_path / "manifest.json")
    # Exit 1 from git 2.56, 255 from 2.43, whose send-pack also fails its own walk: never what is read.
    assert sent[0] != 0 and plan["shallow"], sent
    assert "ontip" in applied["branches"]["created"] and "behind" in applied["branches"]["missing"]
    assert _git(vm, "rev-parse", "--is-shallow-repository") == "true"
    said = core.repo_sync_said(plan, "alpha", "demo", applied, {"rc": sent[0], "words": sent[1]})
    assert "git send-pack failed" not in said, said
    assert "Not there, its commit did not arrive: behind, which a shallow clone refuses" in said
    assert "git fetch --unshallow there takes it." in said


def test_a_run_that_sent_and_died_before_its_apply_is_finished_by_the_next(tmp_path, patch_module):
    # Were what was sent also the record, the amended branch would read as commits of its own, for good.
    here, vm = _pair(tmp_path)
    _sync(here, vm, tmp_path / "manifest.json")
    _git(here, *IDENTITY, "commit", "--quiet", "--amend", "-m", "amended")
    plan = core.repo_sync_plan(_read(here, select=True), MATCH | {"source": str(here)})
    assert _send(here, vm, plan["refs"])[0] == 0
    _, _, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert applied["branches"]["moved"] == ["main"] and _git(vm, "rev-parse", "main") == _git(here, "rev-parse", "main")


def test_a_branch_deleted_here_stays_there_and_only_its_sent_ref_goes(tmp_path, patch_module):
    here, vm = _pair(tmp_path)
    _git(here, "branch", "gone")
    _sync(here, vm, tmp_path / "manifest.json")
    _git(here, "branch", "-D", "gone")
    _sync(here, vm, tmp_path / "manifest.json")
    assert "gone" in _branches(vm)
    refs = _git(vm, "for-each-ref", "--format=%(refname)", "refs/agent-warehouse").splitlines()
    assert "refs/agent-warehouse/sent/heads/gone" not in refs and "refs/agent-warehouse/sync/heads/gone" in refs


def test_a_tag_the_vm_has_for_another_object_is_left(tmp_path, patch_module):
    here, vm = _pair(tmp_path)
    _git(vm, "tag", "v1", "HEAD~1")
    _git(here, "tag", "v1")
    _, _, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert applied["tags"]["differ"] == ["v1"] and _git(vm, "rev-parse", "v1") != _git(here, "rev-parse", "v1")


def test_a_branch_name_or_commit_git_would_not_take_is_never_handed_to_git(tmp_path, patch_module):
    here, vm = _pair(tmp_path)
    head = _git(here, "rev-parse", "HEAD")
    applied = run_main(
        apply.main,
        {
            "checkout": str(vm),
            "manifest": str(tmp_path / "manifest.json"),
            "source": str(here),
            "branches": {"-D": head, "a b": head, "ok": "HEAD", "x..y": head},
            "head": {"branch": "main", "commit": head},
            "tree": {"bring": False, "why": "test"},
        },
    )
    assert all(not names for key, names in applied["branches"].items() if key != "missing"), applied
    assert set(_branches(vm)) == {"main"}


# The git commands the apply only reads with.
APPLY_READS = {"rev-parse", "cat-file", "merge-base", "symbolic-ref", "for-each-ref", "config"}


def test_the_apply_writes_with_its_fixed_verbs_alone():
    # Never push, fetch, rebase, stash, clean, gc or reset --hard; update-ref only under refs/agent-warehouse/.
    source = (REPO / "playbooks" / "library" / "git_sync_apply.py").read_text()
    for word in ('"push"', '"fetch"', '"rebase"', '"stash"', '"gc"', '"--hard"'):
        assert word not in source, word
    reads = re.findall(r"\b(?:git|text)\(\s*(?:self\.)?module,\s*(?:self\.)?top,\s*\"([a-z-]+)\"", source)
    assert reads and set(reads) <= APPLY_READS, reads
    assert {verb[0] for verb in apply.WRITES} == {"branch", "switch", "merge", "reset", "tag", "update-ref", "config"}
    # config only a branch's upstream, and no process per name to check it: git's rules, in the module.
    assert ("config", "--local") in apply.WRITES and "check-ref-format" not in source
    assert ("reset", "--keep") in apply.WRITES and ("merge", "--ff-only") in apply.WRITES
    calls = re.findall(r"apply\.write\(\s*\"([a-z-]+)\"(?:,\s*\"(--[a-z-]+)\")?", source)
    assert calls and all((verb,) in apply.WRITES or (verb, flag) in apply.WRITES for verb, flag in calls), calls


def test_next_steps_keeps_a_checkout_by_the_apply_s_word_for_why_never_by_its_words():
    plan = core.repo_sync_plan(_selected(), MATCH)
    applied = {"branches": {}, "tree": {"brought": False, "reason": "own", "why": "words that may change"}}
    assert "its checkout there has changes of its own" in core.repo_sync_next(plan, "alpha", "demo", applied)
    applied["tree"] = {"brought": False, "reason": "busy", "why": "it has changes of its own"}
    assert core.repo_sync_next(plan, "alpha", "demo", applied) == ""


# ----------------------------------------------------------------------------- the live check's and V1-V9 ---
def test_a_branch_whose_commits_not_here_are_its_upstream_s_is_said_so_with_a_pull_here(tmp_path, patch_module):
    # Live, 2026-11-18: GitHub's main moved on and the VM's clone has it; here main has a commit of its own and never
    # fetched. Leaving it is right; the line says to pull here, not to push or drop work there that nobody made.
    here, vm = _pair(tmp_path)
    _git(tmp_path, "clone", "--quiet", str(tmp_path / "origin.git"), "other")
    _commit(tmp_path / "other", "theirs")
    _git(tmp_path / "other", "push", "--quiet", "origin", "main")
    _git(vm, "pull", "--quiet", "--ff-only")
    _git(here, "branch", "side")
    _sync(here, vm, tmp_path / "manifest.json")
    _git(vm, "switch", "--quiet", "side")
    _commit(vm, "vm-side")
    _git(vm, "switch", "--quiet", "main")
    _commit(here, "mine")
    _git(here, "switch", "--quiet", "side")
    _git(here, *IDENTITY, "commit", "--quiet", "--amend", "-m", "amended here")
    _git(here, "switch", "--quiet", "main")
    vm_main = _git(vm, "rev-parse", "main")
    plan, _, applied = _sync(here, vm, tmp_path / "manifest.json")
    assert applied["branches"]["upstream"] == ["main"] and applied["branches"]["own"] == ["side"], applied
    assert applied["tree"]["reason"] == "upstream" and _git(vm, "rev-parse", "main") == vm_main
    said = core.repo_sync_said(plan, "alpha", "demo", applied)
    assert "Left there as it is: side (commits of its own) and main (its upstream's commits, not fetched here)." in said
    line = core.repo_sync_next(plan, "alpha", "demo", applied, " --editor zed")
    assert line == (
        "demo on alpha: side there has commits of its own and main there has commits of its upstream's that this"
        " machine has not fetched, so the sync left them as they are. Pull those commits here and push or drop that"
        " work there, then: just code alpha --project demo --editor zed"
    ), line


@pytest.mark.parametrize(
    ("done", "line"),
    [
        (
            {"upstream": ["main"]},
            "demo on alpha: main there has commits of its upstream's that this machine has not fetched, so the sync"
            " left it as it is. Pull those commits here, then: just code alpha --project demo",
        ),
        (
            {"own": ["a", "b"]},
            "demo on alpha: a and b there have commits of their own, so the sync left them as they are. Push or drop"
            " that work there, then: just code alpha --project demo",
        ),
    ],
    ids=["one branch behind its upstream", "two branches of their own"],
)
def test_next_steps_says_what_to_do_for_each_and_more_than_one_as_they_are(done, line):
    applied = {"branches": done, "tree": {"brought": True}}
    assert core.repo_sync_next(core.repo_sync_plan(_selected(), MATCH), "alpha", "demo", applied) == line


def test_a_folder_with_a_file_carried_is_never_said_left_whole(tmp_path, patch_module):
    # Live, 2026-11-18: ".claude/ (25 files) (a repository inside the repository)", while .claude/settings.local.json
    # went. The worktrees are said by the folder that holds them alone, each counted as a repository.
    top = _repository(tmp_path / "demo", ignore=".claude/settings.local.json\n")
    (top / ".git" / "info" / "exclude").write_text("**/.claude/worktrees/\n")
    _write(top, ".claude/settings.local.json", "{}\n")
    for name in ("one", "two"):
        _git(tmp_path, "init", "--quiet", str(top / ".claude" / "worktrees" / name))
    found = _read(top, select=True)
    assert [item["path"] for item in found["files"]] == [".claude/settings.local.json"], found["files"]
    assert found["left"] == [
        {
            "path": ".claude/worktrees/",
            "why": shared.WHY["repository"],
            "files": 0,
            "repositories": 2,
            "size": 0,
        }
    ], found["left"]
    plan = core.repo_sync_plan(found, MATCH)
    assert {"what": ".claude/worktrees/ (2 repositories)", "why": shared.WHY["repository"]} in plan["left"]


def test_the_estimate_hands_git_each_known_commit_as_a_caret_line(tmp_path, patch_module, monkeypatch):
    # git before 2.42 refuses --not in --stdin mode: each commit the other end has is a ^<id> line instead.
    top = _repository(tmp_path / "demo")
    base = _git(top, "rev-parse", "HEAD")
    asked = []
    real = info.git

    def recording(module, path, *args, data=None):
        if "rev-list" in args:
            asked.append(data)
        return real(module, path, *args, data=data)

    monkeypatch.setattr(info, "git", recording)
    _read(top, estimate={"refs": ["refs/heads/main"], "known": [base]})
    assert asked == [f"refs/heads/main\n^{base}"], asked


def test_a_submodule_moved_or_a_file_made_a_folder_is_left_and_the_rest_of_the_changes_go(tmp_path, patch_module):
    top = _repository(tmp_path / "demo")
    sub = top / "sub"
    identity = ["-c", "user.name=Dev", "-c", "user.email=dev@example.com"]
    _git(tmp_path, "init", "--quiet", "--initial-branch", "main", str(sub))
    _git(sub, *identity, "commit", "--quiet", "--allow-empty", "-m", "inside")
    (top / "file").write_text("a file\n")
    _git(top, "add", "sub", "file")
    _git(top, "commit", "--quiet", "-m", "a repository inside, and a file")
    _git(sub, *identity, "commit", "--quiet", "--allow-empty", "-m", "moved")
    (top / "file").unlink()
    _write(top, "file/now.md")
    (top / "README").write_text("changed\n")
    found = _read(top, select=True)
    assert found["changes"]["modified"] == ["README"] and found["changes"]["deleted"] == [], found["changes"]
    left = {item["path"]: item["why"] for item in found["left"]}
    assert left == {"sub": shared.WHY["folder"], "file": shared.WHY["folder"]}, found["left"]


def test_the_owner_s_patterns_hold_for_the_changes_to_tracked_files_too(tmp_path, patch_module):
    # repo_sync_exclude: "never carried from any repository" - a change to a tracked file it names, and its deletion,
    # included. The built-in names are not asked of tracked files: an edited .env.example goes.
    top = _repository(tmp_path / "demo")
    for name in ("secrets.yml", "old-secrets.yml", ".env.example"):
        (top / name).write_text("committed\n")
    _git(top, "add", "-A")
    _git(top, "commit", "--quiet", "-m", "tracked")
    (top / "secrets.yml").write_text("filled in here\n")
    (top / "old-secrets.yml").unlink()
    (top / ".env.example").write_text("TOKEN=\n")
    (top / "README").write_text("changed\n")
    found = _read(top, select=True, exclude=["*secrets.yml"])
    assert found["changes"]["modified"] == [".env.example", "README"] and found["changes"]["deleted"] == []
    left = {item["path"]: item["why"] for item in found["left"]}
    assert left == {"old-secrets.yml": shared.WHY["excluded"], "secrets.yml": shared.WHY["excluded"]}, left
    assert all(item["path"] != "secrets.yml" for item in found["files"])


def test_a_new_branch_tracks_the_branch_of_origin_it_tracks_here(tmp_path, patch_module):
    # git pull and git push on the VM then work for it, as here; a branch that tracks nothing here tracks nothing there.
    here, vm = _pair(tmp_path)
    _git(here, "switch", "--quiet", "-c", "feat/x")
    _commit(here, "x")
    _git(here, "push", "--quiet", "--set-upstream", "origin", "feat/x")
    _git(here, "branch", "local-only")
    _git(here, "switch", "--quiet", "main")
    _sync(here, vm, tmp_path / "manifest.json")
    assert _git(vm, "config", "--get", "branch.feat/x.remote") == "origin"
    assert _git(vm, "config", "--get", "branch.feat/x.merge") == "refs/heads/feat/x"
    assert subprocess.run(["git", "-C", str(vm), "config", "--get", "branch.local-only.remote"], check=False).returncode
    # Without an origin there, nothing is set.
    _git(vm, "remote", "remove", "origin")
    _git(here, "switch", "--quiet", "-c", "feat/y")
    _git(here, "push", "--quiet", "--set-upstream", "origin", "feat/y")
    _git(here, "switch", "--quiet", "main")
    _sync(here, vm, tmp_path / "manifest.json")
    assert "feat/y" in _branches(vm)
    assert subprocess.run(["git", "-C", str(vm), "config", "--get", "branch.feat/y.remote"], check=False).returncode


def test_ref_names_are_held_to_git_s_own_rules_with_no_process_per_name():
    # The apply judged each branch and tag with its own git check-ref-format: seconds for thousands of tags. Its rules
    # in the module agree with git's over names made to break each one.
    names = ["main", "feat/x", "a.b", "v1.0", "x.lock", "x.lock/y", "a..b", ".a", "a/.b", "a/", "/a", "a//b", "a.",
             "@", "a@{b", "a@b", "a b", "a~b", "a^b", "a:b", "a?b", "a*b", "a[b", "a\\b", "a\x7fb", "a\tb", "é", "-x",
             "a/b.lock/c", "@{", "{", "}", "a/./b", "a/../b", "HEAD"]  # fmt: skip
    for name in names:
        ref = f"refs/heads/{name}"
        git_says = subprocess.run(["git", "check-ref-format", ref], capture_output=True, check=False).returncode == 0
        assert shared.ref_name(ref) == git_says, name
    assert not shared.branch_name("HEAD") and not shared.branch_name("-x") and shared.branch_name("feat/x")


def test_next_steps_names_the_editor_as_every_other_just_code_line_does():
    # V7: the line is given code_editor_flag, which holds --editor and the host's flag, as code.yml's own lines are.
    text = (REPO / "playbooks" / "tasks" / "repo_write.yml").read_text()
    task = text.split("- name: repo_write | Keep what is left to do for the end of the run", 1)[1].split("\n- ", 1)[0]
    assert "code_editor_flag" in task and "claude_host_flag" not in task, task
