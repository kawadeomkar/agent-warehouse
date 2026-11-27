"""The deep diff: layer 1 in check mode with its diff, and each layer-2 step's own difference (lib/claude_vm/guest/
layer1.py's `check`, guest/events.py's `checked`, guest/layer2/step.py's `check_steps`; docs/architecture/engine.md
§16.10).

Layer 1's plays are real ansible-playbook runs on the pinned ansible-core against a stand-in machine
(tests/unit/aw_machine.py) over F3's test-only local connection, where the card's container stands: a file that
drifted is "would change" with its diff and nothing on the machine changes, one as wanted "would not change", a
command "not evaluated", a no_log task's diff absent, and a canned secret in a file's content struck from the diff
event and from the verdict. The forms Ansible quotes a secret in (JSON's escape of a mapping's diff, `repr`'s of a
value or a tuple key: review F5.md 10) are struck as its own text is. Layer 2's check answers from each step's probe
and difference on F30's stand-in with a settings.json that differs: the difference named, nothing written, no event
emitted; a step with no read of its own "would apply".
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from aw_machine import Machine, layer1_host, target
from harness import REPO
from test_aw_claude_settings import _ctx, _reached, _settings, _steps, _systemctl_calls, _units
from test_aw_layer1 import _alpha, _play

from claude_vm.fleet import paths
from claude_vm.guest import events, layer1
from claude_vm.guest.layer2 import step
from claude_vm.runs import job as jobs
from claude_vm.runs import rundir
from claude_vm.secret import MARK, Secret, struck_all

IDENTITY = "agent-warehouse-00000000-0000-4000-8000-00000000f021"
# A canned secret holding what JSON and repr escape: a quote, a backslash, a line break's escape and a character
# outside ASCII.
CANNED = 'f21-canned-"\\é-secret'
OK = {"event": "ok"}
# A condition that is false.
NO = "1 == 2"


@pytest.fixture
def made(home: Path):
    """A job of this process's own with its run's directory, as the run that starts layer 1 has; both gone after."""
    job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy --check alpha", "pve", ["alpha"], {"kind": "terminal"}, True
    )
    rundir.make(job)
    yield job
    rundir.remove(job)
    jobs.drop(job)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "machines"


def _check(job: jobs.Job, made_machine: Machine, play: Path, redacted=()) -> layer1.Checked:
    job.redact(*redacted)
    return asyncio.run(
        asyncio.wait_for(
            layer1.check(
                job,
                target(made_machine),
                play,
                name="alpha",
                host_flag="",
                env=dict(os.environ),
                test_connection=layer1_host(made_machine),
            ),
            180,
        )
    )


def _heard(job: jobs.Job) -> list[jobs.Event]:
    heard: list[jobs.Event] = []
    job.listen(heard.append)
    return heard


def _copy(name: str, dest: Path, content: str, **more: object) -> dict:
    return {"name": name, "ansible.builtin.copy": {"dest": str(dest), "content": content}, **more}


# ---------------------------------------------------------------------------------------------- layer 1 ---
def test_each_task_is_said_as_check_mode_makes_it_and_nothing_on_the_machine_changes(made, root, tmp_path):
    alpha = _alpha(root)
    home = alpha.home
    (home / "drifted.txt").write_text("as it drifted\n")
    (home / "kept.txt").write_text("as wanted\n")
    (home / "hidden.txt").write_text("hidden as it drifted\n")
    script = tmp_path / "script.sh"
    script.write_text(f"#!/bin/sh\ntouch {home / 'scripted'}\n")
    script.chmod(0o755)
    play = _play(
        tmp_path,
        _copy("drifted", home / "drifted.txt", "as wanted\n"),
        _copy("kept", home / "kept.txt", "as wanted\n"),
        {"name": "a command", "ansible.builtin.command": {"argv": ["touch", str(home / "touched")]}},
        _copy("hidden", home / "hidden.txt", "hidden as wanted\n", no_log=True),
        # Review 96's 2: check mode skips these unrun, whatever their module, and a false condition is evaluated.
        {"name": "a tempfile", "ansible.builtin.tempfile": {"path": str(home), "prefix": "f21-"}},
        {"name": "a script", "ansible.builtin.script": {"cmd": str(script)}},
        {"name": "a command not wanted", "ansible.builtin.command": {"argv": ["touch", str(home / "x")]}, "when": NO},
        {"name": "a loop not wanted", "ansible.builtin.command": {"argv": ["true"]}, "loop": [1, 2], "when": NO},
        {"name": "a command with creates", "ansible.builtin.command": {"cmd": "true", "creates": str(home / "none")}},
    )
    checked = _check(made, alpha, play)
    assert checked.ended.status == 0, checked.ended.said
    verdicts = {task.task: (task.verdict, task.diff) for task in checked.tasks}
    assert list(verdicts) == [
        "drifted",
        "kept",
        "a command",
        "hidden",
        "a tempfile",
        "a script",
        "a command not wanted",
        "a loop not wanted",
        "a command with creates",
    ]
    verdict, diff = verdicts["drifted"]
    assert verdict == "would change" and "-as it drifted" in diff and "+as wanted" in diff, diff
    assert verdicts["kept"] == ("would not change", "")
    assert verdicts["a command"] == ("not evaluated", "")
    # A no_log task's diff is absent: what it would change, said; never the file's text.
    assert verdicts["hidden"] == ("would change", "")
    assert verdicts["a tempfile"] == ("not evaluated", "") and verdicts["a script"] == ("not evaluated", "")
    assert verdicts["a command not wanted"] == ("would not change", "")
    assert verdicts["a loop not wanted"] == ("would not change", "")
    assert verdicts["a command with creates"][0] == "would change"
    assert (home / "drifted.txt").read_text() == "as it drifted\n"
    assert (home / "hidden.txt").read_text() == "hidden as it drifted\n"
    assert not (home / "touched").exists() and not (home / "scripted").exists()
    assert not list(home.glob("f21-*"))


def test_a_secret_in_a_file_s_content_is_struck_from_its_diff(made, root, tmp_path):
    alpha = _alpha(root)
    (alpha.home / "secret.txt").write_text("before\n")
    heard = _heard(made)
    play = _play(tmp_path, _copy("secret", alpha.home / "secret.txt", f"key={CANNED}\n"))
    checked = _check(made, alpha, play, redacted=[Secret(CANNED)])
    assert checked.ended.status == 0, checked.ended.said
    [task] = checked.tasks
    assert task.verdict == "would change" and f"+key={MARK}" in task.diff, task.diff
    [diff] = [event for event in heard if event.data.get("status") == "diff"]
    assert f"+key={MARK}" in diff.data["diff"]
    dumped = json.dumps([[event.text, dict(event.data)] for event in heard], ensure_ascii=False)
    for form in (CANNED, json.dumps(CANNED)[1:-1], repr(CANNED)[1:-1], "f21-canned"):
        assert form not in dumped and form not in task.diff
    assert (alpha.home / "secret.txt").read_text() == "before\n"


def test_a_secret_is_struck_in_the_forms_ansible_quotes_it_in():
    secret = Secret(CANNED)
    for form in (CANNED, json.dumps(CANNED)[1:-1], json.dumps(CANNED, ensure_ascii=False)[1:-1], repr(CANNED)[1:-1]):
        assert struck_all(f"a {form} b", [secret]) == f"a {MARK} b", form
    # A tuple key, as the callback makes it text, and a mapping's diff, as Ansible writes it through JSON.
    assert struck_all(str((CANNED, 1)), [secret]) == f"('{MARK}', 1)"
    assert struck_all(json.dumps({"pw": CANNED}, indent=4), [secret]) == json.dumps({"pw": MARK}, indent=4)


def test_a_mapping_s_diff_heard_holds_its_secret_in_no_form(made):
    reader = events.Heard(made, IDENTITY, "alpha", [Secret(CANNED)])
    heard = _heard(made)
    reader.hand(json.dumps({"event": "task", "task": "t", "part": None, "handler": False}))
    said = "--- before\n+++ after\n" + json.dumps({"before": {"pw": "old"}, "after": {"pw": CANNED}}, indent=4)
    reader.hand(json.dumps({"event": "diff", "task": "t", "part": None, "diff": said}))
    reader.hand(json.dumps({"event": "changed", "task": "t", "part": None}))
    dumped = json.dumps([dict(event.data) for event in heard], ensure_ascii=False)
    assert MARK in dumped and "f21-canned" not in dumped
    [task] = reader.checked
    assert task.verdict == "would change" and MARK in task.diff and "f21-canned" not in task.diff


@pytest.mark.parametrize(
    ("results", "verdict"),
    [
        ([{"event": "skipped", "condition": False}], "not evaluated"),
        ([{"event": "skipped"}], "not evaluated"),
        ([{"event": "skipped", "condition": True}], "would not change"),
        ([OK], "would not change"),
        ([{"event": "ok", "item": 1}, {"event": "skipped", "item": 2, "condition": False}, OK], "not evaluated"),
        ([{"event": "ok", "item": 1}, {"event": "skipped", "item": 2, "condition": True}, OK], "would not change"),
        ([{"event": "ok"}, {"event": "changed"}], "would change"),
        ([{"event": "failed"}], "not evaluated"),
        ([], "not evaluated"),
    ],
)
def test_check_mode_s_verdict_of_a_task(made, results, verdict):
    reader = events.Heard(made, IDENTITY, "alpha")
    reader.hand(json.dumps({"event": "task", "task": "t", "part": "common", "handler": False}))
    for result in results:
        reader.hand(json.dumps({"task": "t", "part": "common", **result}))
    assert reader.checked == (events.Checked("t", "common", verdict),)


# ---------------------------------------------------------------------------------------------- layer 2 ---
class _Unread:
    """A step with no read of its own."""

    name = "no-read"
    part = "claude_code"
    reads = False

    async def probe(self, ctx):
        raise AssertionError("a step with no read is never probed")

    async def apply(self, ctx, state):
        raise AssertionError("a check applies nothing")

    def difference(self, state):
        raise AssertionError("a step with no read has no difference")


class _Saying:
    """A step whose probe fails with what it read, a secret of the run's among it."""

    name = "saying"
    part = "claude_code"

    async def probe(self, ctx):
        raise step.Failed(f"the file holds {CANNED}, which is no JSON")

    async def apply(self, ctx, state):
        raise AssertionError("a check applies nothing")

    def difference(self, state):
        raise AssertionError("a probe that failed has no difference")


@pytest.fixture
def job(home: Path):
    made = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy --check alpha", None, ["alpha"], {"kind": "terminal"}, True
    )
    yield made
    jobs.drop(made)


def test_layer_2_answers_from_each_step_s_probe_and_difference_and_writes_nothing(tmp_path, engine_guard, job):
    alpha = _reached(tmp_path, engine_guard, login=True)
    on_disk = alpha.home / ".claude" / "settings.json"
    on_disk.write_text('{"theme": "light"}\n')
    on_disk.chmod(0o600)
    heard = _heard(job)
    ctx = _ctx(job, alpha, _settings(claude_code_settings={"theme": "dark"}))
    checked = asyncio.run(step.check_steps(ctx, [*_steps(alpha), _Unread()]))
    assert [(each.name, each.verdict) for each in checked] == [
        ("claude-settings", "would change"),
        ("remote-control", "would change"),
        ("no-read", "would apply"),
    ]
    assert checked[0].difference == step.Difference(
        "~/.claude/settings.json", "present", "autoUpdatesChannel set, theme changed"
    )
    assert "claude-remote-control.service installed" in checked[1].difference.wanted
    assert on_disk.read_text() == '{"theme": "light"}\n' and list(_units(alpha).iterdir()) == []
    assert not (alpha.home / ".claude.json").exists() and heard == []
    assert not [call for call in _systemctl_calls(alpha) if call["root"]]
    # A probe that cannot go on is not evaluated, with why.
    on_disk.write_text("{not json")
    checked = asyncio.run(step.check_steps(ctx, _steps(alpha)))
    assert checked[0].verdict == "not evaluated" and "is not a JSON object" in checked[0].why
    assert on_disk.read_text() == "{not json"
    # Review 96's 5: why is struck of the run's secrets, as every event is.
    job.redact(Secret(CANNED))
    [said] = asyncio.run(step.check_steps(ctx, [_Saying()]))
    assert said.why == f"the file holds {MARK}, which is no JSON"
