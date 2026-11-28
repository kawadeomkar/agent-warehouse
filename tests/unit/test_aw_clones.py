"""The account's repositories listed and cloned, layer 2's step clones (lib/claude_vm/guest/layer2/clones.py) and the
helper's verb clones (lib/claude_vm/guest/helper/clones.py; docs/architecture/engine.md §16.10).

Every machine is F30's stand-in (tests/unit/aw_machine.py), reached through B3's runner and the stand-in ssh, its gh
tests/molecule/fake_gh.sh; GitHub is tests/molecule/fake_github_api.py on loopback, its clone URLs local bare
repositories; GitHub's published SSH host keys a TLS fake of tests/molecule/fake_downloads.py's. The tokens are
test_github_auth.py's canned ones. test_github_repos.py, test_git_clone_many.py, test_vm_repos.py and
test_github_auth.py are unchanged and still hold today's role.

- The listing with whatever token the machine holds: none and no login, the public ones alone cloned and the rest
  named, and a second run that changes nothing; the store's token signing gh in, the private ones cloned; gh's own
  stored token listing them on the machine, and never on this computer; a private repository listed while gh is
  signed out skipped and named; with github_auth off, one cloned only where git has a credential (gh its helper by
  hand, or ssh), never keyed off github_auth alone.
- The git identity: the account's name and noreply address, a value of the settings over one set by hand, one set by
  hand kept key by key, none written with github_auth off, and an organisation said under NEXT STEPS.
- The machine's own list (test_vm_repos.py's and test_github_auth.py's): it replaces github_projects, a name GitHub
  does not list is named, a private name waits for a sign-in, a list with no name clones nothing, the exclude list wins,
  naming a fork or an archived repository asks for it; without one, forks and archived ones are skipped; a leading dot
  never cloned; nothing removes a checkout.
- The copy: filter_repos and today's module's choose the same repositories for the same listings, the listing's
  marks of a branchless repository included; LISTING's requests carry test_github_repos.py's cases (paging, a next
  link elsewhere, the token's own account, a token another's, a bad token, a token over plain http, a scheme).
- The verb (test_git_clone_many.py's cases): whole clones and nothing partial left, submodules, one failure named and
  the rest cloned, a partial folder and a link at its place, an existing folder kept, depth, a clone out of time, a
  name that is no folder name, ssh's URL, check mode, no folder, and jobs; the pull by a fast-forward alone, local
  changes and a diverged branch named and left; known_hosts written 0600, idempotent, a stale key replaced.
- The rest: the check says what would change and changes nothing; no token reaches an event, an argument or the run's
  folder, and it joins the redaction list before anything is read; each carried file is Python 3.10; the verb starts a
  program in git_run alone; the settings read are the rows, the role's options with its defaults, and what the run sets.
"""

from __future__ import annotations

import ast
import asyncio
import itertools
import json
import os
import shutil
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import certifi
import pytest
import yaml
from aw_machine import Machine, filed, machine, ssh_stand_in, target
from harness import FAKES, GIT_ENV, REPO, _bare_repos, _start, load_module
from pki import proxmox_pki
from test_aw_guest_helper import FLOOR, FLOOR_CHECK, _floor_python, _ran, _starting_functions
from test_github_auth import CANNED, NOREPLY, TOKENS, USERNAME

from claude_vm.fleet import passthrough, paths, registry, secrets
from claude_vm.guest import bundle
from claude_vm.guest.helper import clones as verb
from claude_vm.guest.layer2 import clones, github, step
from claude_vm.guest.layer2.clones import Clones
from claude_vm.guest.layer2.github import GitHubAuth
from claude_vm.runs import job as jobs
from claude_vm.secret import Secret

SOURCE = REPO / "lib" / "claude_vm" / "guest" / "layer2" / "clones.py"
VERB = REPO / "lib" / "claude_vm" / "guest" / "helper" / "clones.py"
ROLE = REPO / "roles" / "github_projects"
FAKE_API = FAKES / "fake_github_api.py"
REPOS = ["pub-one", "pub-two", "secret:private", "fresh:empty"]
KINDS = ["pub-one", "forked:fork", "old:archived"]
DOTTED = ["pub-one", ".claude", ".vscode"]
UNLISTED = " Private repositories are not listed until gh is signed in."
META_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMoleculeTestKeyDoNotUse0000000000000000000"
github_repos = load_module("roles/github_projects/library/github_repos.py", "github_repos_for_clones", register=False)


# ------------------------------------------------------------------ the kit ----


@pytest.fixture
def job(home: Path):
    """A job of this process's own, as a run that starts the runner's children has; dropped after the test."""
    made = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", None, ["alpha"], {"kind": "terminal"}, True
    )
    yield made
    jobs.drop(made)


@pytest.fixture
def api(tmp_path):
    """A maker of fake GitHub APIs, each listing specs for username molecule, its clone URLs bare repositories of the
    test's own; every one ended after the test."""
    servers: list[subprocess.Popen] = []

    def make(*specs: str) -> str:
        git_root = tmp_path / f"git-{len(servers)}"
        git_root.mkdir()
        _bare_repos(git_root, specs)
        server, port = _start(FAKE_API, USERNAME, str(git_root), *specs)
        servers.append(server)
        return f"http://127.0.0.1:{port}"

    yield make
    for server in servers:
        server.terminate()
        server.wait(timeout=10)


@pytest.fixture
def meta(tmp_path, monkeypatch):
    """GitHub's /meta over TLS, as the transport reads it: a fake of its own whose CA stands in for certifi's list."""
    pki = proxmox_pki(tmp_path / "pki", key_usage=True)
    stood_in = tmp_path / "certifi.pem"
    stood_in.write_text(pki["ca"])
    monkeypatch.setattr(certifi, "where", lambda: str(stood_in))
    served = tmp_path / "served"
    served.mkdir()
    (served / "meta").write_text(json.dumps({"ssh_keys": [META_KEY]}))
    server, port = _start(FAKES / "fake_downloads.py", str(served), "--tls", str(pki["cert"]), str(pki["key"]))
    yield f"https://127.0.0.1:{port}/meta"
    server.terminate()
    server.wait(timeout=10)


@pytest.fixture
def reach(tmp_path, engine_guard):
    """A maker of stand-in machines, each reached through the stand-in ssh, first on this test's PATH: gh holding
    stored where given, made git's helper by hand where helper, and identity set by hand in ~/.gitconfig."""
    root = tmp_path / "machines"
    made: list[Machine] = []
    engine_guard.first_on_path(ssh_stand_in(root, made))

    def make(
        name: str = "alpha", *, stored: str | None = None, helper: bool = False, identity: dict | None = None
    ) -> Machine:
        one = machine(root, name, gh={} if stored is None else {"fake-token": stored + "\n"})
        filed(one)
        if helper:
            for value in ("", f"!{one.bin / 'gh'} auth git-credential"):
                _git(one, "--add", "credential.https://github.com.helper", value)
        for key, value in (identity or {}).items():
            _git(one, key, value)
        made.append(one)
        ssh_stand_in(root, made)
        return one

    return make


def _git(made: Machine, *words: str) -> list[str]:
    result = subprocess.run(
        ["git", "config", "--file", str(made.home / ".gitconfig"), *words], capture_output=True, text=True, check=False
    )
    return result.stdout.splitlines()


def _settings(api_url: str, token: str | None = None, **over: object) -> dict[str, object]:
    return {
        "github_auth": True,
        "github_auth_method": "auto",
        "github_login_scopes": (),
        "vm_name": "alpha",
        "github_username": USERNAME,
        "github_projects": [],
        "github_projects_exclude": [],
        "github_projects_include_forks": False,
        "github_projects_include_archived": False,
        "github_projects_clone_protocol": "https",
        "git_user_name": "",
        "git_user_email": "",
        "vm_projects_dir": "",
        "tls_ca_file": "",
        "network_proxy": "none",
        "github_projects_api_url": api_url,
        **({} if token is None else {"github_token": Secret(token)}),
        **over,
    }


def _ctx(job: jobs.Job, made: Machine, settings) -> step.Context:
    return step.Context(
        job, target(made), made.identity, settings, dict(os.environ), threading.Event(), become_exe="sudo"
    )


def _steps(meta: str | None = None) -> list:
    return [GitHubAuth(), Clones(python="python3", meta=meta)]


def _run(job: jobs.Job, made: Machine, settings, meta: str | None = None) -> step.Ran:
    return asyncio.run(step.run_steps(_ctx(job, made, settings), _steps(meta)))


def _heard(job: jobs.Job) -> list[jobs.Event]:
    heard: list[jobs.Event] = []
    job.listen(heard.append)
    return heard


def _report(heard: list[jobs.Event]) -> str:
    """The step's report, the last said."""
    said = [event.text for event in heard if event.kind == "report" and "repositories selected for" in event.text]
    assert said, [event.text for event in heard if event.kind == "report"]
    return said[-1]


def _next(heard: list[jobs.Event], matter: str = "github_identity") -> str:
    return [event.text for event in heard if event.kind == "next" and event.data["matter"] == matter][-1]


def _there(made: Machine) -> list[str]:
    projects = made.home / "projects"
    return sorted(path.name for path in projects.iterdir()) if projects.is_dir() else []


def _clones(ran: step.Ran) -> bool:
    return dict(ran.changed)["clones"]


def _leaks(job: jobs.Job, heard: list[jobs.Event], *machines: Machine) -> list[str]:
    """Every canned token found in an event, a file of the run's folder, or a call of the stand-in ssh or sudo."""
    places = {"events": json.dumps([[event.text, dict(event.data)] for event in heard])}
    for path in sorted(job.folder.rglob("*")):
        if path.is_file():
            places[str(path)] = path.read_bytes().decode("utf-8", "replace")
    for made in machines:
        places[f"{made.name} ssh"] = json.dumps(made.calls())
        places[f"{made.name} sudo"] = json.dumps(made.sudo_calls())
    return [f"{token} in {where}" for where, said in places.items() for token in CANNED if token in said]


# ------------------------------------------------------------------ the listing, with whatever token there is ----


def test_no_token_and_no_login_clone_the_public_ones_name_the_rest_and_a_second_run_changes_nothing(job, reach, api):
    made, heard = reach(), _heard(job)
    settings = _settings(api(*REPOS))
    first = _run(job, made, settings)
    assert (first.outcome, _clones(first)) == ("succeeded", True), first
    assert _there(made) == ["pub-one", "pub-two"]
    assert (made.home / "projects" / "pub-one" / "README.md").read_text() == "# pub-one\n"
    folder = made.home / "projects"
    assert stat.S_IMODE(folder.stat().st_mode) == 0o755
    assert _report(heard) == f"alpha: 2 of 3 repositories selected for {folder} (skipped as empty: fresh).{UNLISTED}"
    # The identity: the account's, asked of GitHub with no token.
    assert (_git(made, "user.name"), _git(made, "user.email")) == (["Molecule Tester"], [NOREPLY])
    again = _run(job, made, settings)
    assert (again.outcome, _clones(again)) == ("succeeded", False), again
    assert _there(made) == ["pub-one", "pub-two"]


def test_the_store_s_token_signs_gh_in_and_the_private_ones_are_cloned(job, reach, api):
    made, heard = reach(), _heard(job)
    ran = _run(job, made, _settings(api(*REPOS), TOKENS["vault"]))
    assert ran.outcome == "succeeded", ran
    assert _there(made) == ["pub-one", "pub-two", "secret"]
    report = _report(heard)
    assert report.startswith("alpha: 3 of 4 repositories") and UNLISTED not in report, report
    assert _leaks(job, heard, made) == []


def test_gh_s_own_login_lists_the_private_ones_and_its_token_never_leaves_the_machine(job, reach, api):
    made, heard = reach(stored=TOKENS["oauth"]), _heard(job)
    ran = _run(job, made, _settings(api(*REPOS)))
    assert ran.outcome == "succeeded", ran
    assert _there(made) == ["pub-one", "pub-two", "secret"]
    assert _report(heard).startswith("alpha: 3 of 4 repositories")
    # gh's token is read on the machine and piped there: no call, event or file of this computer's holds it.
    assert _leaks(job, heard, made) == []
    assert any("gh auth token --hostname github.com" in " ".join(call["argv"]) for call in made.calls())


def test_a_private_repository_listed_while_gh_is_signed_out_is_skipped_and_named(job, reach, api):
    made, heard = reach(), _heard(job)
    ran = _run(job, made, _settings(api(*REPOS), TOKENS["vault"], github_auth=False))
    assert ran.outcome == "succeeded", ran
    report = _report(heard)
    assert report.startswith("alpha: 2 of 4 repositories"), report
    assert "private, skipped until gh is signed in: secret" in report
    assert _there(made) == ["pub-one", "pub-two"]
    # github_auth off: ~/.gitconfig is not written.
    assert not (made.home / ".gitconfig").exists()


@pytest.mark.parametrize(
    ("helper", "protocol", "cloned"),
    [(False, "https", False), (True, "https", True), (False, "ssh", True)],
    ids=["own-login", "own-helper", "ssh"],
)
def test_with_github_auth_off_a_private_one_is_cloned_only_where_git_has_a_credential(
    job, reach, api, meta, helper, protocol, cloned
):
    made, heard = reach(stored=TOKENS["oauth"], helper=helper), _heard(job)
    settings = _settings(api(*REPOS), github_auth=False, github_projects_clone_protocol=protocol)
    ran = _run(job, made, settings, meta)
    assert ran.outcome == "succeeded", ran
    report = _report(heard)
    assert ("secret" in _there(made)) is cloned
    if cloned:
        assert report.startswith("alpha: 3 of 4 repositories") and "skipped as git has no credential" not in report
    else:
        assert report.startswith("alpha: 2 of 4 repositories"), report
        assert "github_auth is false, and gh is not its credential helper): secret" in report
    # github_auth off: git's helper was read, never written.
    assert _git(made, "--get-all", "credential.https://github.com.helper") == (
        ["", f"!{made.bin / 'gh'} auth git-credential"] if helper else []
    )


# ------------------------------------------------------------------ the git identity ----


@pytest.mark.parametrize(
    ("set_here", "by_hand", "expected"),
    [
        ({}, {}, (["Molecule Tester"], [NOREPLY])),
        (
            {"git_user_name": "Dev Eloper", "git_user_email": "dev@example.com"},
            {"user.name": "Hand Set", "user.email": "w@example.com"},
            (["Dev Eloper"], ["dev@example.com"]),
        ),
        ({}, {"user.name": "Hand Set", "user.email": "w@example.com"}, (["Hand Set"], ["w@example.com"])),
        ({}, {"user.email": "w@example.com"}, (["Molecule Tester"], ["w@example.com"])),
        ({}, {"user.name": "Hand Set"}, (["Hand Set"], [NOREPLY])),
    ],
    ids=["derived", "set-here", "by-hand", "email-by-hand", "name-by-hand"],
)
def test_the_identity_is_the_account_s_unless_set_here_and_one_set_by_hand_is_kept(
    job, reach, api, set_here, by_hand, expected
):
    made = reach(identity=by_hand)
    assert _run(job, made, _settings(api(*REPOS), **set_here)).outcome == "succeeded"
    assert (_git(made, "user.name"), _git(made, "user.email")) == expected


def test_an_organisation_gives_no_identity_and_says_so(job, reach, api):
    made, heard = reach(), _heard(job)
    settings = _settings(api(*REPOS), github_username="acme-org")
    ran = _run(job, made, settings)
    assert ran.outcome == "succeeded", ran
    left = _next(heard)
    assert left.startswith("alpha: acme-org is an organisation, so no git identity is derived from it"), left
    login = github.wanted(_ctx(job, made, settings)).login_command
    assert login.startswith("aw ") and left.endswith(f"whose identity that run derives: {login}"), left
    assert left in _report(heard)
    assert _git(made, "user.name") == [] and _git(made, "user.email") == []


# ------------------------------------------------------------------ the machine's own list ----


def _own(job, reach, api_url, names, token=None, **over):
    """The run on a machine with a list of its own: the report, and what is in its projects folder after."""
    made, heard = reach(), _heard(job)
    (made.home / "projects" / "from-before").mkdir(parents=True)
    ran = _run(job, made, _settings(api_url, token, vm_repos=tuple(names), **over))
    assert ran.outcome == "succeeded", ran
    return _report(heard), _there(made)


def test_a_machine_s_own_list_replaces_the_setting_and_removes_nothing(job, reach, api):
    report, there = _own(job, reach, api(*REPOS), ["pub-two"], github_projects=["pub-one"])
    assert there == ["from-before", "pub-two"]
    assert "own list" not in report


def test_a_name_github_does_not_list_is_named_and_the_run_goes_on(job, reach, api):
    report, there = _own(job, reach, api(*REPOS), ["pub-one", "nosuch"])
    assert there == ["from-before", "pub-one"]
    assert report.endswith(
        f"alpha: not cloned from its own list of repositories, as {USERNAME} has none listed by that name: nosuch."
        f"{UNLISTED}"
    ), report


def test_a_private_name_waits_for_a_sign_in_and_is_cloned_once_there_is_one(job, reach, api):
    url = api(*REPOS)
    report, there = _own(job, reach, url, ["secret"])
    assert there == ["from-before"] and "none listed by that name: secret." in report
    report, there = _own(job, lambda: reach("beta"), url, ["secret"], TOKENS["vault"], vm_name="beta")
    assert there == ["from-before", "secret"] and "own list" not in report


def test_a_list_with_no_name_left_clones_nothing_rather_than_the_setting_s(job, reach, api):
    assert _own(job, reach, api(*REPOS), [], github_projects=["pub-one"])[1] == ["from-before"]


def test_the_exclude_list_wins_over_a_machine_s_own_list(job, reach, api):
    there = _own(job, reach, api(*REPOS), ["pub-one", "pub-two"], github_projects_exclude=["pub-two"])[1]
    assert there == ["from-before", "pub-one"]


def test_naming_a_fork_or_an_archived_repository_asks_for_it(job, reach, api):
    assert _own(job, reach, api(*KINDS), ["forked", "old"])[1] == ["forked", "from-before", "old"]


def test_without_a_list_of_its_own_a_machine_skips_forks_and_archived_ones(job, reach, api):
    made = reach()
    assert _run(job, made, _settings(api(*KINDS))).outcome == "succeeded"
    assert _there(made) == ["pub-one"]


def test_a_repository_named_with_a_leading_dot_is_never_cloned_and_is_named(job, reach, api):
    made, heard = reach(), _heard(job)
    assert _run(job, made, _settings(api(*DOTTED))).outcome == "succeeded"
    report = _report(heard)
    assert report.startswith("alpha: 1 of 3 repositories"), report
    assert "named with a leading dot, which would make a folder of settings there: .claude, .vscode" in report
    assert _there(made) == ["pub-one"]


def test_the_projects_folder_follows_vm_projects_dir_and_is_made_for_the_account(job, reach, api):
    made = reach()
    folder = made.home / "work" / "code"
    assert _run(job, made, _settings(api(*REPOS), vm_projects_dir=str(folder))).outcome == "succeeded"
    assert sorted(path.name for path in folder.iterdir()) == ["pub-one", "pub-two"]


@pytest.mark.parametrize("own", ["changes", "branch"])
def test_update_existing_pulls_by_a_fast_forward_and_says_a_checkout_it_leaves(job, reach, api, tmp_path, own):
    made, heard = reach(), _heard(job)
    url = api(*REPOS)
    settings = _settings(url, github_projects_update_existing=True)
    assert _run(job, made, settings).outcome == "succeeded"
    # GitHub moves on: a commit pushed to pub-one and pub-two.
    git_root = tmp_path / "git-0"
    for name in ("pub-one", "pub-two"):
        work = tmp_path / "push" / name
        env = {**os.environ, **GIT_ENV}
        subprocess.run(["git", "clone", "-q", str(git_root / f"{name}.git"), str(work)], check=True, env=env)
        (work / "NEW.md").write_text("new\n")
        for argv in (["add", "NEW.md"], ["commit", "-q", "-m", "more"], ["push", "-q", "origin", "main"]):
            subprocess.run(["git", "-C", str(work), *argv], check=True, env=env, capture_output=True)
    mine = made.home / "projects" / "pub-two"
    if own == "branch":
        # A branch of the person's own: left on it, and the step goes on.
        subprocess.run(["git", "-C", str(mine), "checkout", "-q", "-b", "feat"], check=True, env=env)
        why = "it is not on main"
    else:
        (mine / "README.md").write_text("my work\n")
        why = "it has local changes, which a pull would put at risk"
    ran = _run(job, made, settings)
    assert (ran.outcome, _clones(ran)) == ("succeeded", True), ran
    assert _report(heard).endswith(f"alpha: not pulled, and left as they are: {USERNAME}/pub-two ({why})."), heard
    assert (made.home / "projects" / "pub-one" / "NEW.md").read_text() == "new\n"
    assert not (mine / "NEW.md").exists()
    if own == "changes":
        assert (mine / "README.md").read_text() == "my work\n"


def test_one_that_cannot_be_cloned_fails_the_step_by_name_and_the_rest_are_cloned(job, reach, api, tmp_path):
    made = reach()
    url = api(*REPOS)
    shutil.rmtree(tmp_path / "git-0" / "pub-two.git")
    ran = _run(job, made, _settings(url))
    assert ran.outcome == "failed", ran
    assert ran.why.startswith(f"1 of 2 repositories could not be cloned into {made.home / 'projects'} on alpha: ")
    assert f"{USERNAME}/pub-two (" in ran.why and ran.why.endswith("The rest were cloned.")
    assert _there(made) == ["pub-one"]


def test_over_ssh_github_s_published_keys_are_trusted_once(job, reach, api, meta):
    made = reach()
    settings = _settings(api(*REPOS), TOKENS["vault"], github_projects_clone_protocol="ssh")
    assert _run(job, made, settings, meta).outcome == "succeeded"
    known = made.home / ".ssh" / "known_hosts"
    assert known.read_text() == f"github.com {META_KEY}\n"
    assert stat.S_IMODE(known.stat().st_mode) == 0o600
    assert stat.S_IMODE(known.parent.stat().st_mode) == 0o700
    again = _run(job, made, settings, meta)
    assert (again.outcome, _clones(again)) == ("succeeded", False), again


def test_github_s_keys_are_read_over_https_alone(job, reach, api):
    made = reach()
    settings = _settings(api(*REPOS), github_projects_clone_protocol="ssh")
    ran = _run(job, made, settings, "http://127.0.0.1:1/meta")
    assert ran.outcome == "failed" and "over https alone" in ran.why, ran
    assert _there(made) == []


def test_a_listing_that_fails_says_why_and_clones_nothing(job, reach, api):
    made, heard = reach(), _heard(job)
    ran = _run(job, made, _settings(api(*REPOS), "bad-token", github_auth=False))
    assert ran.outcome == "failed", ran
    assert ran.why.startswith(f"The repositories of {USERNAME} could not be listed on alpha: GitHub API request to")
    assert "HTTP 401 Bad credentials" in ran.why
    assert _there(made) == [] and _leaks(job, heard, made) == []


# ------------------------------------------------------------------ the check, the token, the settings ----


def test_the_check_says_what_would_change_and_changes_nothing(job, reach, api):
    made = reach()
    ctx = _ctx(job, made, _settings(api(*REPOS), github_auth=False))
    (checked,) = asyncio.run(step.check_steps(ctx, [Clones(python="python3")]))
    assert checked.verdict == "would change", checked
    folder = made.home / "projects"
    assert checked.difference == step.Difference(
        f"the repositories in {folder}",
        "0 of the 2 selected checked out",
        f"{folder} made; cloned: {USERNAME}/pub-one, {USERNAME}/pub-two",
    )
    assert _there(made) == [] and not folder.exists()


def test_the_token_joins_the_run_s_redaction_list_before_anything_is_read(job, reach, api, monkeypatch):
    held, read = [], github.read
    token = Secret(TOKENS["vault"])

    async def reading(ctx):
        held.append(any(each is token for each in ctx.job.redacted))
        return await read(ctx)

    monkeypatch.setattr(github, "read", reading)
    settings = {**_settings(api(*REPOS), github_auth=False), "github_token": token}
    assert asyncio.run(step.run_steps(_ctx(job, reach(), settings), [Clones(python="python3")])).outcome == "succeeded"
    assert held and all(held), held


def test_the_settings_read_are_the_rows_the_role_s_options_and_what_the_run_sets():
    defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
    for name, default in clones.OPTIONS.items():
        option = passthrough.option(name)
        assert (option.role, option.refused) == ("github_projects", None), option
        assert defaults[name] == default, name
    every = {row.name for row in registry.rows()} | {
        option.name
        for option in passthrough.options()
        if option.through is None and option.refused != passthrough.ANY_VALUE
    }
    assert all(registry.row(name).name == name for name in clones.ROWS)
    named = {
        node.value
        for node in ast.walk(ast.parse(SOURCE.read_text()))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in every
    }
    assert named == {*clones.ROWS, *clones.OPTIONS, *(name for name in clones.RUN_SET if name in every)}
    assert Clones.part in clones.ROWS and secrets.row(github.SECRET) is not None
    assert registry.row("github_projects_clone_protocol").choices == clones.PROTOCOLS
    # The machine's own list is its record's, never a setting: the role's option is left out of the settings.
    assert passthrough.option("github_projects_vm_repos").refused is not None


# ------------------------------------------------------------------ the copy ----


def _listings():
    """Listings of every kind filter_repos tells apart: a fork, an archived one, never pushed, sized at 0 with and
    without a branch, names in both cases."""
    kinds = itertools.product((False, True), (False, True), (None, "2026-01-01T00:00:00Z"), (0, 12), (False, True))
    repos = []
    for index, (fork, archived, pushed, size, branchless) in enumerate(kinds):
        name = f"{'Repo' if index % 3 else 'repo'}-{index:02d}"
        repos.append(
            {
                "name": name,
                "full_name": f"{USERNAME}/{name}",
                "clone_url": f"https://github.com/{USERNAME}/{name}.git",
                "ssh_url": f"git@github.com:{USERNAME}/{name}.git",
                "default_branch": "main",
                "fork": fork,
                "archived": archived,
                "private": index % 2 == 0,
                "pushed_at": pushed,
                "size": size,
                "id": index,
                "branchless": branchless,
            }
        )
    return repos


def _listing_namespace() -> dict:
    namespace: dict = {"__name__": "listing"}
    exec(compile(clones.LISTING, "listing", "exec"), namespace)
    return namespace


@pytest.mark.parametrize(("forks", "archived"), list(itertools.product((False, True), repeat=2)))
def test_the_copy_chooses_what_today_s_module_chooses(forks, archived):
    repos = _listings()
    asked: list[str] = []

    def todays_no_branch(repo):
        asked.append(repo["name"])
        return repo["branchless"]

    today = github_repos.filter_repos(repos, forks, archived, todays_no_branch)
    todays_asked, asked[:] = list(asked), []
    assert today == clones.filter_repos(repos, forks, archived, todays_no_branch)
    assert asked == todays_asked
    assert clones.FIELDS == github_repos.FIELDS and clones.USER_FIELDS == github_repos.USER_FIELDS
    # The listing marks on the machine what the engine's filter then reads: branches asked of what today's asks.
    mark, marked_asked = _listing_namespace()["mark"], []

    def listing_no_branch(repo):
        marked_asked.append(repo["name"])
        return repo["branchless"]

    marked = [mark(repo, forks, archived, listing_no_branch) for repo in repos]
    assert clones.filter_repos(marked, forks, archived, clones.no_branch) == today
    assert sorted(marked_asked) == sorted(todays_asked)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("", None),
        ('<https://x/repos?page=2>; rel="next", <https://x/repos?page=5>; rel="last"', "https://x/repos?page=2"),
        ('<https://x/repos?page=1>; rel="prev", <https://x/repos?page=1>; rel="first"', None),
        ('<https://x/a?page=3>;rel="next"', "https://x/a?page=3"),
    ],
)
def test_the_listing_reads_a_next_link_as_today_s_module_does(header, expected):
    assert _listing_namespace()["parse_next_link"](header) == expected == github_repos.parse_next_link(header)


API = "https://api.github.com"


def _asking(answers: dict):
    """The listing's namespace with its one request stood in for by answers, each URL's (status, JSON, next link):
    the namespace and every (url, headers) asked."""
    namespace, asked = _listing_namespace(), []

    def get(url, headers):
        asked.append((url, dict(headers)))
        status, body, link = answers.get(url, (404, {"message": "Not Found"}, None))
        return status, json.dumps(body).encode(), link

    namespace["get"] = get
    return namespace, asked


def test_the_listing_follows_pages_and_never_a_next_page_elsewhere():
    page1, page2 = f"{API}/users/octo/repos?type=owner&per_page=100", f"{API}/users/octo/repos?page=2"
    namespace, asked = _asking(
        {page1: (200, [{"name": "one"}], f'<{page2}>; rel="next"'), page2: (200, [{"name": "three"}], None)}
    )
    headers = namespace["build_headers"]("")
    url, account = namespace["resolve_listing"](API, "octo", "", headers, 100, False)
    assert (url, account) == (page1, {})
    assert [repo["name"] for repo in namespace["fetch_all_pages"](url, headers, API)] == ["one", "three"]
    assert [one for one, _ in asked] == [page1, page2] and "Authorization" not in asked[0][1]
    for elsewhere in ("https://api.github.com.example/repos?page=2", "http://api.github.com/repos?page=2"):
        namespace, asked = _asking({page1: (200, [{"name": "one"}], f'<{elsewhere}>; rel="next"')})
        with pytest.raises(namespace["Refused"], match="not following it"):
            namespace["fetch_all_pages"](page1, headers, API)
        assert [one for one, _ in asked] == [page1]


def test_the_listing_takes_the_token_s_own_account_and_another_s_public_ones():
    me = {"login": "Octo", "name": "Octo Cat", "id": 583231, "email": None}
    namespace, asked = _asking({f"{API}/user": (200, me, None)})
    headers = namespace["build_headers"]("tok")
    assert headers["Authorization"] == "Bearer tok" and headers["User-Agent"] == clones.USER_AGENT
    url, account = namespace["resolve_listing"](API, "octo", "tok", headers, 100, False)
    assert url == f"{API}/user/repos?affiliation=owner&per_page=100" and account["login"] == "Octo"
    url, _ = namespace["resolve_listing"](API, "someone", "tok", headers, 100, False)
    assert url == f"{API}/users/someone/repos?type=owner&per_page=100"
    # A name is escaped in the URL; the account asked without a token only where an identity is derived.
    namespace, asked = _asking({f"{API}/users/a%2Fb": (200, {"login": "a/b", "type": "User"}, None)})
    url, account = namespace["resolve_listing"](API, "a/b", "", namespace["build_headers"](""), 100, True)
    assert url == f"{API}/users/a%2Fb/repos?type=owner&per_page=100" and account["login"] == "a/b"


def test_the_listing_refuses_a_bad_token_a_token_over_plain_http_and_another_scheme():
    namespace, _ = _asking({f"{API}/user": (401, {"message": "Bad credentials"}, None)})
    with pytest.raises(namespace["Refused"], match="HTTP 401 Bad credentials"):
        namespace["resolve_listing"](API, "octo", "bad-token", namespace["build_headers"]("bad-token"), 100, False)
    check = namespace["check_api_url"]
    with pytest.raises(namespace["Refused"], match="Refusing to send a token over plain HTTP to example.com"):
        check("http://example.com", "tok")
    for fine in ("http://127.0.0.1:8080", "http://localhost", "http://example.com"):
        check(fine, "tok" if "example" not in fine else "")
    with pytest.raises(namespace["Refused"], match="must be an http"):
        check("ftp://example.com", "")


def test_a_redirect_is_the_answer_and_the_token_never_reaches_where_it_points():
    seen: list[dict] = []

    class Elsewhere(BaseHTTPRequestHandler):
        def do_GET(self):  # http.server's name
            seen.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"login": "octo"}).encode())

        def log_message(self, *args):
            pass

    servers = [ThreadingHTTPServer(("127.0.0.1", 0), Elsewhere)]
    other = f"http://127.0.0.1:{servers[0].server_address[1]}"

    class Moved(Elsewhere):
        def do_GET(self):  # http.server's name
            self.send_response(302)
            self.send_header("Location", f"{other}/user")
            self.end_headers()

    servers.append(ThreadingHTTPServer(("127.0.0.1", 0), Moved))
    api = f"http://127.0.0.1:{servers[1].server_address[1]}"
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        namespace = _listing_namespace()
        headers = namespace["build_headers"]("tok")
        status, _body, _link = namespace["get"](f"{api}/user", headers)
        assert status == 302
        with pytest.raises(namespace["Refused"], match="HTTP 302"):
            namespace["resolve_listing"](api, "octo", "tok", headers, 100, False)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
    assert seen == []


def test_a_branches_answer_that_fails_leaves_the_repository_to_clone():
    namespace, _ = _asking({})
    repo = {"full_name": "octo/fresh", "size": 0, "pushed_at": "2026-01-01T00:00:00Z"}
    assert namespace["branchless"](API, repo, {}) is False


# ------------------------------------------------------------------ the verb ----


@pytest.fixture
def git_env(monkeypatch):
    """The test's git kept off the developer's config, and its prompts off."""
    for name, value in {**GIT_ENV, "GIT_TERMINAL_PROMPT": "0"}.items():
        monkeypatch.setenv(name, value)


def _cmd(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _source(root: Path, name: str, commits: int = 1) -> dict:
    """A repository with commits on main, as the engine hands the verb one, at a file:// URL."""
    path = root / "sources" / name
    path.mkdir(parents=True)
    _cmd("init", "-q", "-b", "main", cwd=path)
    for n in range(commits):
        (path / "file.txt").write_text(f"{n}\n")
        _cmd("add", "file.txt", cwd=path)
        _cmd("commit", "-q", "-m", f"commit {n}", cwd=path)
    return {
        "name": name,
        "full_name": f"octo/{name}",
        "clone_url": path.as_uri(),
        "ssh_url": "",
        "default_branch": "main",
    }


def _asked(root: Path, repos: list[dict], *, make: bool = True, **over) -> dict:
    folder = root / "projects"
    if make:
        folder.mkdir(exist_ok=True)
    return {
        "dir": str(folder),
        "repos": repos,
        "url": "clone_url",
        "depth": 0,
        "jobs": 2,
        "timeout": 120,
        "pull": False,
        "known_hosts": [],
        "check": False,
        **over,
    }


def _verb(asked: dict, git: str | None = None) -> dict:
    verb._held(asked)
    return verb.run(asked, git or shutil.which("git"))


def test_every_repository_is_cloned_whole_and_nothing_partial_is_left(tmp_path, git_env):
    done = _verb(_asked(tmp_path, [_source(tmp_path, name) for name in ("one", "two", "three")]))
    assert sorted(done["cloned"]) == ["octo/one", "octo/three", "octo/two"]
    assert done["not_cloned"] == [] and done["skipped_existing"] == [] and done["leftovers"] == []
    for name in ("one", "two", "three"):
        assert (tmp_path / "projects" / name / "file.txt").read_text() == "0\n"
    assert sorted(p.name for p in (tmp_path / "projects").iterdir()) == ["one", "three", "two"]


@pytest.mark.parametrize("fetched", [True, False], ids=["fetched", "gone"])
def test_a_repository_s_submodules_are_cloned_with_it_and_one_that_cannot_be_is_said(
    tmp_path, git_env, monkeypatch, fetched
):
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    inner, outer = _source(tmp_path, "inner"), _source(tmp_path, "outer")
    source = tmp_path / "sources" / "outer"
    _cmd("-c", "protocol.file.allow=always", "submodule", "add", "-q", inner["clone_url"], "vendored", cwd=source)
    _cmd("commit", "-q", "-m", "add a submodule", cwd=source)
    if not fetched:
        shutil.rmtree(tmp_path / "sources" / "inner")
    done = _verb(_asked(tmp_path, [outer]))
    if fetched:
        assert done["cloned"] == ["octo/outer"] and done["not_cloned"] == []
        assert (tmp_path / "projects" / "outer" / "vendored" / "file.txt").read_text() == "0\n"
    else:
        assert done["not_cloned"][0]["name"] == "octo/outer" and "its submodules" in done["not_cloned"][0]["said"]
        assert (tmp_path / "projects" / "outer" / "file.txt").is_file()
        assert _verb(_asked(tmp_path, [outer]))["skipped_existing"] == ["octo/outer"]


def test_one_that_fails_is_named_and_the_rest_are_cloned(tmp_path, git_env):
    gone = {**_source(tmp_path, "gone"), "clone_url": (tmp_path / "nowhere").as_uri()}
    done = _verb(_asked(tmp_path, [_source(tmp_path, "pub"), gone, _source(tmp_path, "other")]))
    assert [failure["name"] for failure in done["not_cloned"]] == ["octo/gone"]
    assert done["not_cloned"][0]["rc"] == 128 and done["not_cloned"][0]["said"]
    assert sorted(done["cloned"]) == ["octo/other", "octo/pub"]
    assert sorted(p.name for p in (tmp_path / "projects").iterdir()) == ["other", "pub"]


def test_a_partial_folder_an_earlier_run_left_is_removed_and_the_clone_made(tmp_path, git_env):
    leftover = tmp_path / "projects" / "one.aw-partial"
    (leftover / ".git").mkdir(parents=True)
    done = _verb(_asked(tmp_path, [_source(tmp_path, "one")]))
    assert done["leftovers"] == [str(leftover)] and done["cloned"] == ["octo/one"]
    assert not leftover.exists() and (tmp_path / "projects" / "one" / "file.txt").exists()


def test_a_link_at_the_partial_path_is_removed_never_followed(tmp_path, git_env):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("kept\n")
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "one.aw-partial").symlink_to(elsewhere)
    _verb(_asked(tmp_path, [_source(tmp_path, "one")]))
    assert (elsewhere / "keep.txt").read_text() == "kept\n"
    assert (tmp_path / "projects" / "one" / "file.txt").exists()


def test_a_folder_that_exists_is_left_as_it_is_and_nothing_to_clone_changes_nothing(tmp_path, git_env):
    mine = tmp_path / "projects" / "one"
    mine.mkdir(parents=True)
    (mine / "local.txt").write_text("my work\n")
    asked = _asked(tmp_path, [_source(tmp_path, "one"), _source(tmp_path, "two")])
    done = _verb(asked)
    assert done["skipped_existing"] == ["octo/one"] and done["cloned"] == ["octo/two"]
    assert sorted(p.name for p in mine.iterdir()) == ["local.txt"]
    again = _verb(asked)
    assert again["cloned"] == [] and again["skipped_existing"] == ["octo/one", "octo/two"]


@pytest.mark.parametrize(("depth", "count"), [(1, "1"), (0, "3")], ids=["shallow", "whole"])
def test_a_depth_makes_a_shallow_clone_and_none_the_whole_history(tmp_path, git_env, depth, count):
    _verb(_asked(tmp_path, [_source(tmp_path, "deep", commits=3)], depth=depth))
    assert _cmd("rev-list", "--count", "HEAD", cwd=tmp_path / "projects" / "deep").strip() == count


def test_a_clone_that_runs_out_of_time_is_ended_and_leaves_nothing(tmp_path, git_env):
    # A git that writes its partial folder and then hangs, as a stalled network clone does.
    slow = tmp_path / "bin" / "git"
    slow.parent.mkdir()
    slow.write_text('#!/bin/sh\nfor last; do :; done\nmkdir -p "$last"\nexec sleep 30\n')
    slow.chmod(0o755)
    done = _verb(_asked(tmp_path, [_source(tmp_path, "stuck")], timeout=1), str(slow))
    assert done["not_cloned"] == [{"name": "octo/stuck", "rc": None, "said": "took longer than 1 s"}]
    assert list((tmp_path / "projects").iterdir()) == []


@pytest.mark.parametrize("name", [".claude", "a/b", "..", ""])
def test_a_name_that_is_no_folder_name_is_refused_and_nothing_written(tmp_path, git_env, name):
    done = _verb(_asked(tmp_path, [{**_source(tmp_path, "fine"), "name": name, "full_name": f"octo/{name}"}]))
    assert done["not_cloned"][0]["said"] == "its name is not a folder name"
    assert list((tmp_path / "projects").iterdir()) == []


def test_ssh_takes_each_repository_s_ssh_url(tmp_path, git_env):
    repo = _source(tmp_path, "one")
    done = _verb(_asked(tmp_path, [{**repo, "clone_url": "", "ssh_url": repo["clone_url"]}], url="ssh_url"))
    assert done["cloned"] == ["octo/one"]


def test_check_mode_clones_nothing_and_removes_nothing_and_a_missing_folder_would_clone_everything(tmp_path, git_env):
    leftover = tmp_path / "projects" / "one.aw-partial"
    leftover.mkdir(parents=True)
    done = _verb(_asked(tmp_path, [_source(tmp_path, "one")], check=True))
    assert done["cloned"] == ["octo/one"] and done["leftovers"] == [str(leftover)]
    assert leftover.exists() and not (tmp_path / "projects" / "one").exists()
    elsewhere = _verb(_asked(tmp_path / "x", [_source(tmp_path, "two")], make=False, check=True))
    assert (elsewhere["folder"], elsewhere["cloned"]) == (False, ["octo/two"])


def test_no_projects_folder_outside_check_mode_and_fewer_than_one_job_are_refused(tmp_path, git_env, capsys):
    asked = _asked(tmp_path / "not-yet", [_source(tmp_path, "one")], make=False)
    assert _verb(asked) == {"error": f"{tmp_path / 'not-yet' / 'projects'} is not a folder"}
    verb.main({**asked, "jobs": 0})
    assert json.loads(capsys.readouterr().out) == {"error": "jobs is a whole number, 1 or more"}


def test_a_pull_moves_a_checkout_by_a_fast_forward_alone(tmp_path, git_env):
    repo = _source(tmp_path, "one")
    asked = _asked(tmp_path, [repo], pull=True)
    _verb(asked)
    source, checkout = tmp_path / "sources" / "one", tmp_path / "projects" / "one"
    assert _verb(asked)["pulled"] == []
    (source / "file.txt").write_text("1\n")
    _cmd("commit", "-q", "-am", "more", cwd=source)
    done = _verb(asked)
    assert (done["pulled"], done["not_pulled"]) == (["octo/one"], [])
    assert (checkout / "file.txt").read_text() == "1\n"
    # A commit of the person's own, and GitHub's moved on: not a fast-forward, named, and the commit kept.
    (checkout / "mine.txt").write_text("mine\n")
    _cmd("add", "mine.txt", cwd=checkout)
    _cmd("commit", "-q", "-m", "mine", cwd=checkout)
    (source / "file.txt").write_text("2\n")
    _cmd("commit", "-q", "-am", "even more", cwd=source)
    done = _verb(asked)
    (failure,) = done["left"]
    assert done["not_pulled"] == [] and done["pulled"] == []
    assert failure["name"] == "octo/one" and failure["said"].startswith("not a fast-forward"), failure
    assert (checkout / "mine.txt").read_text() == "mine\n"
    # Local changes to what git tracks: named, and left as they are.
    (checkout / "file.txt").write_text("edited\n")
    done = _verb(asked)
    said = "it has local changes, which a pull would put at risk"
    assert (done["left"], done["not_pulled"]) == ([{"name": "octo/one", "rc": None, "said": said}], [])
    assert (checkout / "file.txt").read_text() == "edited\n"


def _head(path: Path) -> str:
    return _cmd("rev-parse", "HEAD", cwd=path).strip()


@pytest.mark.parametrize("case", ["detached", "branch", "outer", "origin", "rewritten", "link"])
def test_a_pull_takes_only_the_repository_the_folder_is_on_its_default_branch(tmp_path, git_env, case):
    repo = _source(tmp_path, "one")
    source, projects = tmp_path / "sources" / "one", tmp_path / "projects"
    checkout = projects / "one"
    said = {
        "detached": "it is not on main",
        "branch": "it is not on main",
        "outer": "its folder is no checkout of its own",
        "origin": "its origin is not this repository",
        "rewritten": "its origin is not this repository",
        "link": "its folder is a link",
    }[case]
    if case == "outer":
        # The projects folder in another work tree, a folder of that name in it plain: the outer one is never pulled.
        _cmd("clone", "-q", repo["clone_url"], str(projects), cwd=tmp_path)
        checkout.mkdir()
        watched = projects
    elif case == "link":
        # A link at the checkout's place: what it points at, outside the projects folder, is never pulled.
        elsewhere = tmp_path / "elsewhere"
        _cmd("clone", "-q", repo["clone_url"], str(elsewhere), cwd=tmp_path)
        projects.mkdir()
        checkout.symlink_to(elsewhere)
        watched = elsewhere
    else:
        _verb(_asked(tmp_path, [repo]))
        watched = checkout
        if case == "detached":
            # A commit of the person's own on a detached HEAD: never left on no branch.
            _cmd("checkout", "-q", "--detach", cwd=checkout)
            (checkout / "mine.txt").write_text("mine\n")
            _cmd("add", "mine.txt", cwd=checkout)
            _cmd("commit", "-q", "-m", "mine", cwd=checkout)
        elif case == "branch":
            _cmd("checkout", "-q", "-b", "feat", cwd=checkout)
        else:
            # Another repository as its origin: by its URL as configured, never as an insteadOf rewrites it.
            other = _source(tmp_path, "other")
            _cmd("remote", "set-url", "origin", other["clone_url"], cwd=checkout)
            if case == "rewritten":
                _cmd("config", f"url.{repo['clone_url']}.insteadOf", other["clone_url"], cwd=checkout)
            source = tmp_path / "sources" / "other"
    before = _head(watched)
    (source / "file.txt").write_text("moved\n")
    _cmd("commit", "-q", "-am", "moved", cwd=source)
    if case == "rewritten":
        (tmp_path / "sources" / "one" / "file.txt").write_text("moved\n")
        _cmd("commit", "-q", "-am", "moved", cwd=tmp_path / "sources" / "one")
    done = _verb(_asked(tmp_path, [repo], pull=True))
    assert (done["pulled"], done["not_pulled"]) == ([], [])
    assert done["left"] == [{"name": "octo/one", "rc": None, "said": said}]
    assert _head(watched) == before
    if case == "detached":
        assert (checkout / "mine.txt").read_text() == "mine\n"
    if case == "branch":
        assert _cmd("symbolic-ref", "HEAD", cwd=checkout).strip() == "refs/heads/feat"


@pytest.mark.parametrize("commits", [1, 3])
def test_a_shallow_checkout_is_pulled_once_github_moves(tmp_path, git_env, commits):
    repo = _source(tmp_path, "one", commits=3)
    asked = _asked(tmp_path, [repo], depth=1, pull=True)
    _verb(asked)
    source, checkout = tmp_path / "sources" / "one", tmp_path / "projects" / "one"
    for n in range(commits):
        (source / "file.txt").write_text(f"moved {n}\n")
        _cmd("commit", "-q", "-am", f"moved {n}", cwd=source)
    done = _verb(asked)
    assert (done["pulled"], done["not_pulled"], done["left"]) == (["octo/one"], [], [])
    assert (checkout / "file.txt").read_text() == f"moved {commits - 1}\n"


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/octo/one",
        "https://github.com/Octo/One.git",
        "https://github.com/octo/one.git/",
        "ssh://git@github.com/octo/one.git",
        "git@github.com:Octo/one",
    ],
)
def test_the_repository_s_own_origin_is_pulled_in_any_spelling_of_its_url(tmp_path, git_env, monkeypatch, origin):
    # Nothing leaves this computer: the spelling, as the checkout's config holds it, fetches from the source here.
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    monkeypatch.setenv("GIT_SSH_COMMAND", "/bin/false")
    repo = _source(tmp_path, "one")
    _verb(_asked(tmp_path, [repo]))
    source, checkout = tmp_path / "sources" / "one", tmp_path / "projects" / "one"
    _cmd("remote", "set-url", "origin", origin, cwd=checkout)
    _cmd("config", f"url.{repo['clone_url']}.insteadOf", origin, cwd=checkout)
    (source / "file.txt").write_text("moved\n")
    _cmd("commit", "-q", "-am", "moved", cwd=source)
    github = {**repo, "clone_url": "https://github.com/octo/one.git", "ssh_url": "git@github.com:octo/one.git"}
    done = _verb(_asked(tmp_path, [github], pull=True))
    assert (done["pulled"], done["not_pulled"], done["left"]) == (["octo/one"], [], [])
    assert (checkout / "file.txt").read_text() == "moved\n"


def test_a_repository_s_url_is_one_key_whatever_its_spelling():
    key = ("github.com", "octo/one")
    for url in (
        "https://github.com/octo/one.git",
        "https://github.com/Octo/One",
        "http://GitHub.com/octo/one/",
        "ssh://git@github.com:22/octo/one.git",
        "git@github.com:octo/one.git",
        "github.com:octo/one",
    ):
        assert verb.repository(url) == key, url
    for other in ("https://github.com/octo/two.git", "git@example.com:octo/one.git", "https://github.com/one/octo"):
        assert verb.repository(other) != key, other
    assert [verb.repository(none) for none in ("", "/srv/one", "https://github.com/", "nonsense")] == [None] * 4


def test_a_pull_runs_no_checkout(tmp_path, git_env):
    # The branch checked out is the default, held before: fetched and moved on, never switched to.
    repo = _source(tmp_path, "one")
    _verb(_asked(tmp_path, [repo]))
    source = tmp_path / "sources" / "one"
    (source / "file.txt").write_text("1\n")
    _cmd("commit", "-q", "-am", "more", cwd=source)
    log = tmp_path / "git.log"
    recording = tmp_path / "bin" / "git"
    recording.parent.mkdir()
    recording.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexec "{shutil.which("git")}" "$@"\n')
    recording.chmod(0o755)
    done = _verb(_asked(tmp_path, [repo], pull=True), str(recording))
    assert done["pulled"] == ["octo/one"]
    commands = [line.split()[2] for line in log.read_text().splitlines()]
    assert commands == ["rev-parse", "config", "symbolic-ref", "status", "rev-parse", "fetch", "merge", "rev-parse"]


def test_known_hosts_gains_github_s_keys_once_and_a_stale_one_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    known = tmp_path / ".ssh" / "known_hosts"
    assert verb.known_hosts([META_KEY], True) is True and not known.exists()
    assert verb.known_hosts([META_KEY], False) is True
    assert known.read_text() == f"github.com {META_KEY}\n" and stat.S_IMODE(known.stat().st_mode) == 0o600
    assert verb.known_hosts([META_KEY], False) is False
    known.write_text(
        "example.org ssh-ed25519 AAAAother\ngithub.com ssh-ed25519 AAAAstale\ngithub.com ssh-rsa AAAAkept\n"
    )
    assert verb.known_hosts([META_KEY], False) is True
    assert known.read_text() == (
        f"example.org ssh-ed25519 AAAAother\ngithub.com ssh-rsa AAAAkept\ngithub.com {META_KEY}\n"
    )
    # A link at the partial file's place is removed, never written through.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("kept\n")
    (tmp_path / ".ssh" / "known_hosts.aw-partial").symlink_to(elsewhere)
    known.write_text("")
    assert verb.known_hosts([META_KEY], False) is True and elsewhere.read_text() == "kept\n"


def test_each_carried_file_is_python_3_10_and_the_verb_starts_a_program_in_git_run_alone(tmp_path, monkeypatch):
    packed = bundle.sources("clones")
    assert list(packed) == ["clones", bundle.DISPATCHER] and packed["clones"] == VERB.read_text()
    # The probe's script carries no writer, and the verb's none of the probe.
    assert "clones" not in bundle.sources() and not any(name.startswith("ansible") for name in packed)
    assert _starting_functions(packed["clones"]) == {"git_run"}
    floor, env = _floor_python(tmp_path / "empty")
    checked = _ran([*floor, "-I", "-B", "-c", FLOOR_CHECK], json.dumps({**packed, "listing": clones.LISTING}), env)
    assert (checked.returncode, checked.stderr) == (0, b""), checked.stderr.decode()[-3000:]
    assert json.loads(checked.stdout) == {"version": list(FLOOR), "outside": []}
    # The verb run there, on the floor, which has no git: it says so, in its one document.
    on_floor = [*floor, *bundle.command("python3")[1:]]
    asked = {**_asked(Path("/nonexistent"), [], make=False), "check": True}
    ran = _ran(on_floor, bundle.script(["clones"], asked), env)
    assert ran.returncode == 0, ran.stderr.decode()[-3000:]
    assert json.loads(ran.stdout.decode().splitlines()[-1]) == {"error": "git is not installed on the machine"}
    # A verb whose module the script does not carry is refused: the probe asked of a script that packs the verb's.
    sources = bundle.sources
    monkeypatch.setattr(bundle, "sources", lambda verb="probe": sources("clones"))
    refused = _ran(on_floor, bundle.script(["probe"], {}), env)
    assert refused.returncode == 2 and refused.stderr.decode() == "helper: probe is not carried by this script\n"


def test_no_token_reaches_an_event_an_argument_or_the_run_s_folder(job, reach, api):
    url = api(*REPOS)
    machines = [
        (reach("vault"), TOKENS["vault"], {}),
        (reach("stored", stored=TOKENS["oauth"]), None, {}),
        (reach("off", stored=TOKENS["env"]), TOKENS["vault"], {"github_auth": False}),
        (reach("refused"), "bad-token", {"github_auth": False}),
    ]
    heard = _heard(job)
    for made, token, over in machines:
        _run(job, made, _settings(url, token, vm_name=made.name, **over))
    assert _there(machines[0][0]) == ["pub-one", "pub-two", "secret"]
    assert _leaks(job, heard, *(made for made, _, _ in machines)) == []
