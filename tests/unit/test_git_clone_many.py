"""Unit tests for roles/github_projects/library/git_clone_many.py, against repositories made on the spot."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import fail_main, run_main
from harness import load_module

git_clone_many = load_module("roles/github_projects/library/git_clone_many.py")


@pytest.fixture(autouse=True)
def _no_git_config(monkeypatch):
    # The developer's git config (an insteadOf, a hook) would change what git does.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _source(root: Path, name: str, commits: int = 1) -> dict:
    """A repository with commits on main, listed as github_repos lists one, at a file:// URL."""
    path = root / "sources" / name
    path.mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=path)
    for n in range(commits):
        (path / "file.txt").write_text(f"{n}\n")
        _git("add", "file.txt", cwd=path)
        _git("commit", "-q", "-m", f"commit {n}", cwd=path)
    return {
        "name": name,
        "full_name": f"octo/{name}",
        "clone_url": path.as_uri(),
        "ssh_url": "",
        "default_branch": "main",
    }


def _args(root: Path, repos: list[dict], **extra) -> dict:
    dest = root / "projects"
    dest.mkdir(exist_ok=True)
    return {"repos": repos, "dest_dir": str(dest), **extra}


def test_every_repository_is_cloned_whole_and_nothing_partial_is_left(tmp_path, patch_module):
    repos = [_source(tmp_path, name) for name in ("one", "two", "three")]

    result = run_main(git_clone_many.main, _args(tmp_path, repos, jobs=2))

    assert result["changed"] is True
    assert sorted(result["cloned"]) == ["octo/one", "octo/three", "octo/two"]
    assert result["not_cloned"] == [] and result["skipped_existing"] == [] and result["leftovers"] == []
    for name in ("one", "two", "three"):
        assert (tmp_path / "projects" / name / "file.txt").read_text() == "0\n"
    assert sorted(p.name for p in (tmp_path / "projects").iterdir()) == ["one", "three", "two"]


def test_a_repository_s_submodules_are_cloned_with_it(tmp_path, patch_module, monkeypatch):
    # Local paths as submodules are refused by default since git 2.38.
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    inner = _source(tmp_path, "inner")
    outer = _source(tmp_path, "outer")
    source = tmp_path / "sources" / "outer"
    _git("submodule", "add", "-q", inner["clone_url"], "vendored", cwd=source)
    _git("commit", "-q", "-m", "add a submodule", cwd=source)

    result = run_main(git_clone_many.main, _args(tmp_path, [outer]))

    assert result["cloned"] == ["octo/outer"]
    assert (tmp_path / "projects" / "outer" / "vendored" / "file.txt").read_text() == "0\n"


def test_a_submodule_that_cannot_be_fetched_is_said_and_leaves_the_checkout(tmp_path, patch_module, monkeypatch):
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    inner = _source(tmp_path, "inner")
    outer = _source(tmp_path, "outer")
    source = tmp_path / "sources" / "outer"
    _git("submodule", "add", "-q", inner["clone_url"], "vendored", cwd=source)
    _git("commit", "-q", "-m", "add a submodule", cwd=source)
    shutil.rmtree(tmp_path / "sources" / "inner")

    result = fail_main(git_clone_many.main, _args(tmp_path, [outer]))

    assert result["not_cloned"][0]["name"] == "octo/outer" and "its submodules" in result["not_cloned"][0]["said"]
    assert (tmp_path / "projects" / "outer" / "file.txt").is_file()
    # And the next run finds the checkout there, and goes on.
    assert run_main(git_clone_many.main, _args(tmp_path, [outer]))["skipped_existing"] == ["octo/outer"]


def test_one_that_fails_is_named_and_the_rest_are_cloned(tmp_path, patch_module):
    gone = {**_source(tmp_path, "gone"), "clone_url": (tmp_path / "nowhere").as_uri()}
    repos = [_source(tmp_path, "pub"), gone, _source(tmp_path, "other")]

    result = fail_main(git_clone_many.main, _args(tmp_path, repos))

    assert result["msg"] == "1 of 3 not cloned: octo/gone"
    assert [failure["name"] for failure in result["not_cloned"]] == ["octo/gone"]
    assert result["not_cloned"][0]["rc"] == 128 and result["not_cloned"][0]["said"]
    assert sorted(result["cloned"]) == ["octo/other", "octo/pub"]
    assert sorted(p.name for p in (tmp_path / "projects").iterdir()) == ["other", "pub"]


def test_a_partial_folder_an_earlier_run_left_is_removed_and_the_clone_made(tmp_path, patch_module):
    repos = [_source(tmp_path, "one")]
    leftover = tmp_path / "projects" / "one.aw-partial"
    (leftover / ".git").mkdir(parents=True)

    result = run_main(git_clone_many.main, _args(tmp_path, repos))

    assert result["leftovers"] == [str(leftover)]
    assert result["cloned"] == ["octo/one"]
    assert not leftover.exists()
    assert (tmp_path / "projects" / "one" / "file.txt").exists()


def test_a_link_at_the_partial_path_is_removed_never_followed(tmp_path, patch_module):
    repos = [_source(tmp_path, "one")]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("kept\n")
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "one.aw-partial").symlink_to(elsewhere)

    run_main(git_clone_many.main, _args(tmp_path, repos))

    assert (elsewhere / "keep.txt").read_text() == "kept\n"
    assert (tmp_path / "projects" / "one" / "file.txt").exists()


def test_a_folder_that_exists_is_left_as_it_is(tmp_path, patch_module):
    repos = [_source(tmp_path, "one"), _source(tmp_path, "two")]
    mine = tmp_path / "projects" / "one"
    mine.mkdir(parents=True)
    (mine / "local.txt").write_text("my work\n")

    result = run_main(git_clone_many.main, _args(tmp_path, repos))

    assert result["skipped_existing"] == ["octo/one"] and result["cloned"] == ["octo/two"]
    assert sorted(p.name for p in mine.iterdir()) == ["local.txt"]


def test_nothing_to_clone_changes_nothing(tmp_path, patch_module):
    repos = [_source(tmp_path, "one")]
    args = _args(tmp_path, repos)
    run_main(git_clone_many.main, args)

    again = run_main(git_clone_many.main, args)

    assert again["changed"] is False and again["cloned"] == [] and again["skipped_existing"] == ["octo/one"]


def test_a_depth_makes_a_shallow_clone(tmp_path, patch_module):
    repos = [_source(tmp_path, "deep", commits=3)]

    run_main(git_clone_many.main, _args(tmp_path, repos, depth=1))

    assert _git("rev-list", "--count", "HEAD", cwd=tmp_path / "projects" / "deep").strip() == "1"


def test_no_depth_clones_the_whole_history(tmp_path, patch_module):
    repos = [_source(tmp_path, "deep", commits=3)]

    run_main(git_clone_many.main, _args(tmp_path, repos))

    assert _git("rev-list", "--count", "HEAD", cwd=tmp_path / "projects" / "deep").strip() == "3"


def test_a_clone_that_runs_out_of_time_is_ended_and_leaves_nothing(tmp_path, patch_module, monkeypatch):
    # A git that writes its partial folder and then hangs, as a stalled network clone does.
    slow = tmp_path / "bin" / "git"
    slow.parent.mkdir()
    slow.write_text('#!/bin/sh\nfor last; do :; done\nmkdir -p "$last"\nexec sleep 30\n')
    slow.chmod(0o755)
    monkeypatch.setattr(git_clone_many.AnsibleModule, "get_bin_path", lambda self, name, required=False: str(slow))
    repos = [_source(tmp_path, "stuck")]

    result = fail_main(git_clone_many.main, _args(tmp_path, repos, timeout=1))

    assert result["not_cloned"] == [{"name": "octo/stuck", "rc": None, "said": "took longer than 1 s"}]
    assert list((tmp_path / "projects").iterdir()) == []


@pytest.mark.parametrize("name", [".claude", "a/b", "..", ""])
def test_a_name_that_is_no_folder_name_is_refused_and_nothing_written(tmp_path, patch_module, name):
    repos = [{**_source(tmp_path, "fine"), "name": name, "full_name": f"octo/{name}"}]

    result = fail_main(git_clone_many.main, _args(tmp_path, repos))

    assert result["not_cloned"][0]["said"] == "its name is not a folder name"
    assert list((tmp_path / "projects").iterdir()) == []


def test_ssh_takes_each_repository_s_ssh_url(tmp_path, patch_module):
    repo = _source(tmp_path, "one")
    repos = [{**repo, "clone_url": "", "ssh_url": repo["clone_url"]}]

    result = run_main(git_clone_many.main, _args(tmp_path, repos, protocol="ssh"))

    assert result["cloned"] == ["octo/one"]


def test_check_mode_clones_nothing_and_removes_nothing(tmp_path, patch_module):
    repos = [_source(tmp_path, "one")]
    leftover = tmp_path / "projects" / "one.aw-partial"
    leftover.mkdir(parents=True)

    result = run_main(git_clone_many.main, {**_args(tmp_path, repos), "_ansible_check_mode": True})

    assert result["changed"] is True and result["cloned"] == ["octo/one"] and result["leftovers"] == [str(leftover)]
    assert leftover.exists() and not (tmp_path / "projects" / "one").exists()


def test_check_mode_on_a_vm_with_no_projects_folder_yet_would_clone_everything(tmp_path, patch_module):
    repos = [_source(tmp_path, "one")]
    args = {"repos": repos, "dest_dir": str(tmp_path / "not-yet"), "_ansible_check_mode": True}

    assert run_main(git_clone_many.main, args)["cloned"] == ["octo/one"]


def test_no_projects_folder_outside_check_mode_is_refused(tmp_path, patch_module):
    args = {"repos": [_source(tmp_path, "one")], "dest_dir": str(tmp_path / "not-yet")}

    assert fail_main(git_clone_many.main, args)["msg"] == f"{tmp_path / 'not-yet'} is not a folder"


def test_fewer_than_one_job_is_refused(tmp_path, patch_module):
    assert fail_main(git_clone_many.main, _args(tmp_path, [], jobs=0))["msg"] == "jobs must be 1 or more, got 0"
