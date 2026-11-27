"""gh's sign-in, layer 2's step github-auth (lib/claude_vm/guest/layer2/github.py; docs/architecture/engine.md §16.10).

Every machine is F30's stand-in (tests/unit/aw_machine.py), reached through B3's runner and the stand-in ssh, its gh
tests/molecule/fake_gh.sh, the login a file in the machine's home; the token a canned one of test_github_auth.py's,
never a real one. test_github_auth.py is unchanged and still holds today's role.

- Its cases of the sign-in, carried against the step: a token signs gh in once, again only when it changes, and marks
  the login with its digest at 0600; a login made by hand (a device flow's, a token pasted into gh) is not put over
  but under github_auth_method: token, and the token only lists there, said; a cleared token signs out only the marked
  login, by account, and its marker goes, while a pasted token, a device flow's, one from the environment and one that
  replaced the product's login stay; a token GitHub refuses fails under both methods, but one short of a scope under
  auto, which keeps listing, leaves gh signed out and names the scopes; a gh too old for --json is a named failure,
  quoted without control characters; gh is made git's helper once, only signed in; github_auth off touches nothing; a
  sign-in that follows in this run is said and leaves nothing to do; a second run changes nothing.
- Both directions converge: a machine is signed in and then signed out, with nothing of the token in the run's folder.
  Every event, file of the run and call of the stand-in ssh and gh is searched for every canned token, and the token
  joins the run's redaction list before the machine is read.
- The invariant with a test of its own: no sign-out is decided from a token's prefix.
- A method other than auto, web and token is refused before the machine is read, a GitHub App's token named; the
  check says what would change and changes nothing; the settings read are the rows and what the run sets.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path

import pytest
from aw_machine import Machine, filed, machine, ssh_stand_in, target
from harness import GH_CALLS, REPO
from test_github_auth import CANNED, CONTROL, ESCAPES, TOKENS, USERNAME

from claude_vm.fleet import passthrough, paths, registry, secrets
from claude_vm.guest.layer2 import github, step
from claude_vm.guest.layer2.github import GitHubAuth
from claude_vm.runs import job as jobs
from claude_vm.secret import Secret

SOURCE = REPO / "lib" / "claude_vm" / "guest" / "layer2" / "github.py"
LOGIN = "auth login --hostname github.com --git-protocol https --with-token --insecure-storage"
LOGOUT = f"auth logout --hostname github.com --user {USERNAME}"
SETUP = "auth setup-git --hostname github.com"
STEP = "github-auth"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@pytest.fixture
def job(home: Path):
    """A job of this process's own, as a run that starts the runner's children has; dropped after the test."""
    made = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", None, ["alpha"], {"kind": "terminal"}, True
    )
    yield made
    jobs.drop(made)


@pytest.fixture
def reach(tmp_path, engine_guard):
    """A maker of stand-in machines, each reached through the stand-in ssh, first on this test's PATH: gh holding
    stored (its source and its account's login where given), the product's marker of marked, a gh too old for --json
    where old (its words, "" for gh's own), gh made git's helper by hand where helper, and two accounts where two."""
    root = tmp_path / "machines"
    made: list[Machine] = []
    engine_guard.first_on_path(ssh_stand_in(root, made))

    def make(
        name: str = "alpha",
        *,
        stored: str | None = None,
        source: str | None = None,
        login: str | None = None,
        marked: str | None = None,
        old: str | None = None,
        helper: bool = False,
        two: bool = False,
    ) -> Machine:
        gh = {}
        if stored is not None:
            gh["fake-token"] = stored + "\n"
        if source is not None:
            gh["fake-source"] = source
        if login is not None:
            gh["fake-login"] = login
        if old is not None:
            gh["fake-old"] = old
        if two:
            gh["fake-two-accounts"] = ""
        one = machine(root, name, gh=gh)
        filed(one)
        if marked is not None:
            _marker(one).parent.mkdir(parents=True)
            _marker(one).write_text(_digest(marked) + "\n")
        if helper:
            for value in ("", f"!{one.bin / 'gh'} auth git-credential"):
                _git(one, "--add", "credential.https://github.com.helper", value)
        made.append(one)
        ssh_stand_in(root, made)
        return one

    return make


def _git(made: Machine, *words: str) -> list[str]:
    result = subprocess.run(
        ["git", "config", "--file", str(made.home / ".gitconfig"), *words], capture_output=True, text=True, check=False
    )
    return result.stdout.splitlines()


def _marker(made: Machine) -> Path:
    return made.home / github.MARKER


def _stored(made: Machine) -> str | None:
    path = made.home / ".config" / "gh" / "fake-token"
    return path.read_text().strip() if path.exists() else None


def _gh_calls(made: Machine) -> list[str]:
    path = made.home.joinpath(*GH_CALLS)
    return path.read_text().splitlines() if path.exists() else []


def _helpers(made: Machine) -> list[str]:
    return _git(made, "--get-all", "credential.https://github.com.helper")


def _gh_helper(made: Machine) -> bool:
    """Whether git's helpers for github.com are gh's two entries, as gh setup-git writes them: an empty one, then
    gh by its path."""
    found = _helpers(made)
    return len(found) == 2 and found[0] == "" and bool(github.GH_HELPER.match(found[1]))


def _settings(token: str | None = None, **over: object) -> dict[str, object]:
    return {
        "github_auth": True,
        "github_auth_method": "auto",
        "github_login_scopes": (),
        "vm_name": "alpha",
        **({} if token is None else {"github_token": Secret(token)}),
        **over,
    }


def _ctx(job: jobs.Job, made: Machine, settings) -> step.Context:
    return step.Context(
        job, target(made), made.identity, settings, dict(os.environ), threading.Event(), become_exe="sudo"
    )


def _run(job: jobs.Job, made: Machine, token: str | None = None, **over: object) -> step.Ran:
    return asyncio.run(step.run_steps(_ctx(job, made, _settings(token, **over)), [GitHubAuth()]))


def _heard(job: jobs.Job) -> list[jobs.Event]:
    heard: list[jobs.Event] = []
    job.listen(heard.append)
    return heard


def _said(heard: list[jobs.Event]) -> tuple[list[str], dict[str, str]]:
    """The reports said, and each NEXT STEPS matter's last line."""
    reports = [event.text for event in heard if event.kind == "report"]
    nexts = {event.data["matter"]: event.text for event in heard if event.kind == "next"}
    return reports, nexts


def _leaks(job: jobs.Job, heard: list[jobs.Event], *machines: Machine) -> list[str]:
    """Every canned token found in an event, a file of the run's folder, or a call of the stand-in ssh or gh."""
    places = {"events": json.dumps([[event.text, dict(event.data)] for event in heard])}
    for path in sorted(job.folder.rglob("*")):
        if path.is_file():
            places[str(path)] = path.read_bytes().decode("utf-8", "replace")
    for made in machines:
        places[f"{made.name} ssh"] = json.dumps(made.calls())
        places[f"{made.name} gh"] = "\n".join(_gh_calls(made))
        places[f"{made.name} sudo"] = json.dumps(made.sudo_calls())
    return [f"{token} in {where}" for where, said in places.items() for token in CANNED if token in said]


# ------------------------------------------------------------------ signing in ----


def test_a_token_signs_gh_in_once_marks_it_and_a_second_run_changes_nothing(job, reach):
    made = reach()
    heard = _heard(job)
    assert _run(job, made, TOKENS["vault"]) == step.Ran("succeeded", ((STEP, True),))
    assert _stored(made) == TOKENS["vault"]
    assert _marker(made).read_text() == _digest(TOKENS["vault"]) + "\n"
    assert oct(_marker(made).stat().st_mode & 0o777) == "0o600"
    assert oct(_marker(made).parent.stat().st_mode & 0o777) == "0o700"
    assert TOKENS["vault"] not in _marker(made).read_text()
    assert _gh_helper(made)
    reports, nexts = _said(heard)
    assert reports == [
        f"alpha: signed in to GitHub as {USERNAME} with the personal access token this project put there."
    ]
    assert nexts == {"github": ""}
    assert _run(job, made, TOKENS["vault"]) == step.Ran("succeeded", ((STEP, False),))
    calls = _gh_calls(made)
    assert calls.count(LOGIN) == 1 and calls.count(SETUP) == 1, calls
    assert _said(heard)[0][-1] == (
        f"alpha: already signed in to GitHub as {USERNAME} with the personal access token this project put there."
    )


def test_a_changed_token_signs_gh_in_again_and_marks_the_new_one(job, reach):
    made = reach(stored=TOKENS["old"], marked=TOKENS["old"])
    assert _run(job, made, TOKENS["vault"]).outcome == "succeeded"
    assert _stored(made) == TOKENS["vault"]
    assert _marker(made).read_text().strip() == _digest(TOKENS["vault"])
    assert _gh_calls(made).count(LOGIN) == 1


def test_a_marker_gone_missing_comes_back_without_a_second_sign_in(job, reach):
    made = reach()
    assert _run(job, made, TOKENS["vault"]).outcome == "succeeded"
    _marker(made).unlink()
    assert _run(job, made, TOKENS["vault"]) == step.Ran("succeeded", ((STEP, True),))
    assert _marker(made).read_text().strip() == _digest(TOKENS["vault"])
    assert _gh_calls(made).count(LOGIN) == 1


@pytest.mark.parametrize("hand", ["oauth", "old"], ids=["device-flow", "pasted-pat"])
def test_a_token_is_not_put_over_a_login_made_by_hand_and_only_lists_there(job, reach, hand):
    made = reach(stored=TOKENS[hand])
    heard = _heard(job)
    assert _run(job, made, TOKENS["vault"]).outcome == "succeeded"
    assert _stored(made) == TOKENS[hand] and not _marker(made).exists()
    assert LOGIN not in _gh_calls(made)
    reports, nexts = _said(heard)
    assert reports == [
        f"alpha: gh is signed in to GitHub as {USERNAME} by a login of your own, which this run leaves as it is, so"
        " github_token only lists the repositories there. To sign gh in with the token instead, set"
        " github_auth_method: token and run aw deploy alpha --tags github_projects"
    ]
    assert nexts == {"github": ""}


def test_the_token_method_signs_in_over_a_login_made_by_hand(job, reach):
    made = reach(stored=TOKENS["oauth"])
    assert _run(job, made, TOKENS["vault"], github_auth_method="token").outcome == "succeeded"
    assert _stored(made) == TOKENS["vault"]
    assert _marker(made).read_text().strip() == _digest(TOKENS["vault"])


# ------------------------------------------------------------------ signing out ----


def test_a_cleared_token_signs_out_only_the_marked_login_by_account_and_its_marker_goes(job, reach):
    # Two accounts on github.com: a logout without --user fails, as gh's does with no terminal to ask which.
    made = reach(stored=TOKENS["old"], marked=TOKENS["old"], two=True)
    heard = _heard(job)
    assert _run(job, made) == step.Ran("succeeded", ((STEP, True),))
    assert _gh_calls(made).count(LOGOUT) == 1
    assert _stored(made) is None and not _marker(made).exists()
    reports, nexts = _said(heard)
    line = (
        "alpha: not signed in to GitHub, so only public repositories are cloned and git push fails there. Run aw login"
        " alpha --only github to sign in by the device flow; that run clones the private repositories too."
    )
    assert reports == [line] and nexts == {"github": line}
    assert _run(job, made) == step.Ran("succeeded", ((STEP, False),))


@pytest.mark.parametrize(
    ("stored", "source", "marked"),
    [
        (TOKENS["old"], None, None),
        (TOKENS["oauth"], None, None),
        (TOKENS["env"], "GH_TOKEN", None),
        (TOKENS["oauth"], None, TOKENS["old"]),
    ],
    ids=["pasted-pat", "device-flow", "environment", "stale-marker"],
)
def test_a_cleared_token_leaves_every_login_the_product_did_not_make(job, reach, stored, source, marked):
    made = reach(stored=stored, source=source, marked=marked)
    assert _run(job, made).outcome == "succeeded"
    assert not any(call.startswith("auth logout") for call in _gh_calls(made))
    assert _stored(made) == stored
    # A marker that no longer matches the login is left where it was.
    assert (_marker(made).read_text().strip() if marked else None) == (_digest(marked) if marked else None)


def test_the_web_method_signs_out_the_product_s_login_though_a_token_is_set(job, reach):
    made = reach(stored=TOKENS["vault"], marked=TOKENS["vault"])
    assert _run(job, made, TOKENS["vault"], github_auth_method="web").outcome == "succeeded"
    assert _gh_calls(made).count(LOGOUT) == 1 and not _marker(made).exists()


def test_a_machine_converges_to_signed_in_and_back_with_nothing_of_the_token_in_the_run_s_folder(job, reach):
    made = reach()
    heard = _heard(job)
    assert _run(job, made, TOKENS["vault"]).outcome == "succeeded"
    assert _stored(made) == TOKENS["vault"]
    assert _run(job, made).outcome == "succeeded"
    assert _stored(made) is None and not _marker(made).exists()
    assert _gh_calls(made).count(LOGIN) == 1 and _gh_calls(made).count(LOGOUT) == 1
    assert _leaks(job, heard, made) == []


# ------------------------------------------------------------------ the invariant ----


def test_no_sign_out_is_decided_from_a_token_s_prefix(job, reach):
    # A token the person pasted that looks like the product's (ghp_, the vault's own length) is left; a marked login
    # whose token looks like a device flow's is signed out: the marker's digest decides, never the prefix.
    pasted = reach("pasted", stored=TOKENS["vault"].replace("vault", "hand0"))
    marked = reach("marked", stored=TOKENS["oauth"], marked=TOKENS["oauth"])
    for made in (pasted, marked):
        assert _run(job, made, vm_name=made.name).outcome == "succeeded"
    assert _stored(pasted) is not None and _stored(marked) is None
    # And decide reads no kind: every kind the prefix could tell gives the same acts.
    want = github.Wanted(True, "auto", None, "", (), False, "alpha", "dev", "aw login alpha", "aw deploy alpha")
    for found in (
        github.Found(True, account=USERNAME, state="success", stored="a" * 64, marker="a" * 64),
        github.Found(True, account=USERNAME, state="success", stored="a" * 64, marker="b" * 64),
        github.Found(True, account=USERNAME, state="success", stored="a" * 64),
    ):
        acts = {github.decide(dataclasses.replace(found, kind=kind), want) for kind in ("pat", "oauth", "other")}
        assert len(acts) == 1, acts


# ------------------------------------------------------------------ failures ----


@pytest.mark.parametrize("method", ["auto", "token"])
def test_a_token_github_refuses_fails_under_both_methods_and_says_why(job, reach, method):
    made = reach()
    ran = _run(job, made, "bad-token", github_auth_method=method)
    assert ran.outcome == "failed"
    assert "GitHub refused the token" in ran.why and "Bad credentials" in ran.why
    assert "aw login alpha --only github" in ran.why
    assert '(docs/guides/github.md, "Private repositories, and pushing from the VM")' in ran.why
    assert "bad-token" not in ran.why and not _marker(made).exists()


def test_a_token_short_of_a_scope_lists_under_auto_names_the_scopes_and_is_tried_again(job, reach):
    made = reach()
    heard = _heard(job)
    assert _run(job, made, TOKENS["repo_only"]) == step.Ran("succeeded", ((STEP, False),))
    assert _stored(made) is None and not _marker(made).exists()
    reports, nexts = _said(heard)
    assert len(reports) == 1 and nexts == {"github": reports[0]}
    assert "missing required scope 'read:org'" in reports[0] and "Add read:org" in reports[0]
    assert "aw deploy alpha --tags github_projects" in reports[0]
    assert reports[0].endswith("aw login alpha --only github")
    assert _run(job, made, TOKENS["repo_only"]).outcome == "succeeded"
    assert _gh_calls(made).count(LOGIN) == 2
    # Under token it fails: the method asked for the token's sign-in.
    ran = _run(job, made, TOKENS["repo_only"], github_auth_method="token")
    assert ran.outcome == "failed" and "missing required scope" in ran.why


@pytest.mark.parametrize("words", ["", ESCAPES + "unknown flag: --json\x1b[0m"], ids=["plain", "escapes"])
def test_a_gh_too_old_for_json_is_a_named_failure_quoted_without_control_characters(job, reach, words):
    made = reach(old=words)
    ran = _run(job, made)
    assert ran.outcome == "failed"
    assert "needs gh 2.81.0 or newer" in ran.why and "unknown flag: --json" in ran.why
    assert "dev_tools_install_gh" in ran.why
    assert not CONTROL.search(ran.why), repr(ran.why)
    assert "owned" not in ran.why and "b3duZWQ" not in ran.why and "[31m" not in ran.why


def test_the_token_method_with_no_token_fails_by_name(job, reach):
    ran = _run(job, reach(), github_auth_method="token")
    assert ran.outcome == "failed" and "github_auth_method is token, but no github_token is set" in ran.why


def test_a_method_for_a_github_app_is_refused_before_the_machine_is_read(job, reach):
    made = reach()
    ran = _run(job, made, TOKENS["vault"], github_auth_method="app")
    assert ran.outcome == "failed"
    assert "A GitHub App's hourly token is not taken without a control VM" in ran.why
    assert "fine-grained token" in ran.why
    assert made.calls() == []


# ------------------------------------------------------------------ the rest ----


def test_gh_becomes_git_s_helper_only_when_signed_in_and_a_helper_made_by_hand_stays(job, reach):
    signed_out = reach("out")
    by_hand = reach("hand", stored=TOKENS["oauth"], helper=True)
    for made in (signed_out, by_hand):
        assert _run(job, made, vm_name=made.name).outcome == "succeeded"
        assert SETUP not in _gh_calls(made)
    assert _helpers(signed_out) == []
    assert _helpers(by_hand) == ["", f"!{by_hand.bin / 'gh'} auth git-credential"]
    assert _gh_helper(by_hand)


def test_github_auth_off_touches_nothing(job, reach):
    made = reach()
    heard = _heard(job)
    assert _run(job, made, TOKENS["vault"], github_auth=False) == step.Ran("succeeded", ((STEP, False),))
    assert not any(call.startswith(("auth login", "auth logout", "auth setup-git")) for call in _gh_calls(made))
    assert not _marker(made).exists() and _helpers(made) == []
    assert _said(heard) == (
        ["alpha: not signed in to GitHub, and this project leaves gh alone there (github_auth: false)."],
        {"github": ""},
    )


def test_a_sign_in_that_follows_in_this_run_is_said_and_leaves_nothing_to_do(job, reach):
    heard = _heard(job)
    assert _run(job, reach(), github_projects_sign_in_follows=True).outcome == "succeeded"
    reports, nexts = _said(heard)
    assert reports == [
        "alpha: gh is not signed in to GitHub, so only public repositories are listed and cloned, and git push on the"
        " machine will fail. The deploy signs it in next, in this run, and the private repositories follow."
    ]
    assert nexts == {"github": ""}


def test_the_check_says_what_would_change_and_changes_nothing(job, reach):
    made = reach(stored=TOKENS["old"], marked=TOKENS["old"])
    ctx = _ctx(job, made, _settings(TOKENS["vault"]))
    (checked,) = asyncio.run(step.check_steps(ctx, [GitHubAuth()]))
    assert checked.verdict == "would change"
    assert checked.difference == step.Difference(
        "gh's login",
        f"signed in as {USERNAME} by this product's token",
        "gh signed in with github_token, and this product's marker of the login written; gh made git's credential"
        " helper where it is not",
    )
    assert _stored(made) == TOKENS["old"] and LOGIN not in _gh_calls(made)
    assert not any(token in repr(checked) for token in CANNED)


def test_the_token_joins_the_run_s_redaction_list_before_anything_is_read(job, reach, monkeypatch):
    held, read = [], github.read
    token = Secret(TOKENS["vault"])

    async def reading(ctx):
        held.append(any(each is token for each in ctx.job.redacted))
        return await read(ctx)

    monkeypatch.setattr(github, "read", reading)
    settings = {**_settings(), "github_token": token}
    assert asyncio.run(step.run_steps(_ctx(job, reach(), settings), [GitHubAuth()])).outcome == "succeeded"
    assert held and all(held), held


def test_no_token_reaches_an_event_an_argument_or_the_run_s_folder(job, reach):
    # Every case of this file's that hands gh a token, run once more: the token on gh's standard input alone.
    machines = [
        (reach("signin"), TOKENS["vault"], {}),
        (reach("changed", stored=TOKENS["old"], marked=TOKENS["old"]), TOKENS["vault"], {}),
        (reach("beside", stored=TOKENS["oauth"]), TOKENS["vault"], {}),
        (reach("short"), TOKENS["repo_only"], {}),
        (reach("refused"), "bad-token", {}),
        (reach("ours", stored=TOKENS["env"], marked=TOKENS["env"]), None, {}),
        (reach("env", stored=TOKENS["env"], source="GH_TOKEN"), TOKENS["vault"], {"github_auth_method": "token"}),
    ]
    heard = _heard(job)
    for made, token, over in machines:
        _run(job, made, token, vm_name=made.name, **over)
    assert _stored(machines[0][0]) == TOKENS["vault"]
    assert _leaks(job, heard, *(made for made, _, _ in machines)) == []


def test_the_settings_read_are_the_rows_the_secret_and_what_the_run_sets():
    every = {row.name for row in registry.rows()} | {
        option.name
        for option in passthrough.options()
        if option.through is None and option.refused != passthrough.ANY_VALUE
    }
    assert all(registry.row(name).name == name for name in github.ROWS)
    named = {
        node.value
        for node in ast.walk(ast.parse(SOURCE.read_text()))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in every
    }
    # The step's part is a row's name too, github_projects (the repositories cloned), and the tag its lines name.
    assert named - {GitHubAuth.part} == {*github.ROWS, *(name for name in github.RUN_SET if name in every)}
    assert passthrough.option("github_projects_sign_in_follows").refused == passthrough.SET_BY_A_RUN
    assert secrets.row(github.SECRET) is not None
    assert registry.row("github_auth_method").choices == github.METHODS
