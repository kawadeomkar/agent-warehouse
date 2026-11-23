"""Layer 1's events (lib/claude_vm/guest/ansible/callback_plugins/aw_events.py, lib/claude_vm/guest/events.py;
docs/architecture/engine.md §16.10).

Every play here is a real ansible-playbook on the pinned ansible-core, against a stand-in machine
(tests/unit/aw_machine.py) over F3's test-only local connection: started by layer1.run, whose events reach the job's
emit, or, where a test reads the pipe as the child wrote it, started by the test as layer 1 starts it, with the pipe's
read end the test's own. The job's text log is card C12's, a listener of emit that is not written yet: here a listener
of the test's own stands for it, writing each event's text and strings a line each, as a text log would.

What is held: a play of one ping hands its events to emit, writes its lines in the text log and prints nothing on the
terminal; a no_log task that fails leaves its message and its exception text in none of the pipe, emit and the text log,
where the same task without no_log has both; a secret of the run's redaction list, printed by a task, named in a task's
name and split by an escape sequence, is in no event, no text log, no record and nothing the child printed; a control
sequence reaches the text log as plain text; a diff is written only in a run given --diff, and never for a no_log task;
the pipe closed by its reader ends the writing and the play goes on without a traceback; a listener that fails ends the
child; and the reader's own rules - a line that is no event, one too long, a line cut at the pipe's end, the counts, the
fields kept - on lines of the test's own.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import select
import signal
import subprocess
import time
from pathlib import Path

import pytest
from aw_kit import searched_for
from aw_machine import Machine, layer1_host, target
from harness import REPO
from test_aw_layer1 import _alpha, _play, _still

from claude_vm import proc
from claude_vm.fleet import paths
from claude_vm.guest import events, layer1
from claude_vm.runs import children, rundir
from claude_vm.runs import job as jobs
from claude_vm.secret import MARK, Secret

CALLBACK_FILE = layer1.CALLBACKS / f"{layer1.CALLBACK}.py"
# The canned values of these tests: one of the run's redaction list, one a no_log task fails with, and one a task
# without no_log says, which must be there for the others' absence to mean anything.
REDACTED = "f5-canned-value-struck"
CENSORED = "f5-canned-value-hidden"
VISIBLE = "f5-plain-value-said"
# How long a play here may take.
PLAY_MOST = 120
# An action plugin beside a play, which raises with what its task says: a failure with an exception, as a module that
# breaks gives one.
RAISES = """from ansible.plugins.action import ActionBase


class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        raise RuntimeError(self._task.args["say"])
"""


@pytest.fixture
def made(home: Path):
    """A job of this process's own with its run's directory, as the run that starts layer 1 has; both gone after."""
    job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True
    )
    rundir.make(job)
    yield job
    rundir.remove(job)
    jobs.drop(job)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "machines"


def _heard(job: jobs.Job, log: Path) -> list[jobs.Event]:
    """Every event the job's run emits from now on, recorded; and each written to log as a text log would write it,
    a line for its text and one for each line of its strings."""
    heard: list[jobs.Event] = []

    def text_log(event: jobs.Event) -> None:
        lines = [f"{event.machine} {event.data.get('status')} {event.data.get('part') or '-'}: {event.text}"]
        for key in ("item", "message", "stdout", "stderr", "exception", "diff"):
            value = event.data.get(key)
            if isinstance(value, str):
                lines += [f"  {key}: {line}" for line in value.splitlines()]
        lines += [f"  warning: {warning}" for warning in event.data.get("warnings") or []]
        with log.open("a", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n")

    job.listen(heard.append)
    job.listen(text_log)
    return heard


def _dumped(heard: list[jobs.Event], where: Path) -> Path:
    """Every event heard, written whole as JSON, for a search of every field."""
    where.write_text(json.dumps([[event.kind, event.machine, event.text, dict(event.data)] for event in heard]))
    return where


def _statuses(heard: list[jobs.Event]) -> list[str]:
    return [str(event.data.get("status")) for event in heard]


def _of(heard: list[jobs.Event], task: str) -> list[jobs.Event]:
    return [event for event in heard if event.data.get("task") == task]


def _run(job: jobs.Job, made_machine: Machine, play: Path, **given) -> layer1.Ended:
    given.setdefault("env", dict(os.environ))
    return asyncio.run(
        asyncio.wait_for(
            layer1.run(
                job,
                target(made_machine),
                play,
                name="alpha",
                host_flag="",
                test_connection=layer1_host(made_machine),
                **given,
            ),
            PLAY_MOST,
        )
    )


def _raises_beside(folder: Path) -> None:
    (folder / "action_plugins").mkdir(parents=True, exist_ok=True)
    (folder / "action_plugins" / "aw_raise.py").write_text(RAISES)


def _command(name: str, made_machine: Machine, script: str, **more) -> dict:
    return {
        "name": name,
        "ansible.builtin.command": {"argv": ["/bin/sh", "-c", script], "chdir": str(made_machine.home)},
        **more,
    }


# ---------------------------------------------------------------- the pipe as the child wrote it ----


class Raw:
    """ansible-playbook started by the test as layer 1 starts it - its configuration, inventory, environment and
    words - with the events pipe's read end the test's own."""

    def __init__(self, job: jobs.Job, made_machine: Machine, play: Path, folder: Path, *, events_pipe: bool = True):
        directory = rundir.made(job)
        assert directory is not None
        config = directory / layer1.CONFIG
        config.write_text(layer1.configuration_text(directory))
        inventory = directory / "inventory.yml"
        document = layer1.inventory_document(target(made_machine), "alpha", layer1_host(made_machine))
        inventory.write_text(json.dumps(document))
        self.reading, writing = os.pipe()
        env = layer1.environment(dict(os.environ), config, writing)
        if not events_pipe:
            del env[layer1.EVENTS_VARIABLE]
        self.printed = folder / "printed.txt"
        with self.printed.open("wb") as out:
            self.child = subprocess.Popen(
                layer1.argv(inventory, play),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                pass_fds=(writing,),
                start_new_session=True,
            )
        os.close(writing)
        self.data = b""

    def read(self, until=None) -> bool:
        """The pipe read until its end (True) or until(data) holds (False), within PLAY_MOST seconds."""
        deadline = time.monotonic() + PLAY_MOST
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.reading], [], [], 0.2)
            if ready:
                chunk = os.read(self.reading, 65536)
                if not chunk:
                    return True
                self.data += chunk
                if until is not None and until(self.data):
                    return False
        pytest.fail(f"the pipe did not end within {PLAY_MOST} s: {self.data[-2000:]!r}")

    def close(self) -> None:
        if self.reading >= 0:
            os.close(self.reading)
            self.reading = -1

    def ended(self) -> int:
        try:
            return self.child.wait(PLAY_MOST)
        finally:
            self.close()
            if self.child.poll() is None:
                proc.end(self.child.pid)
                self.child.wait()

    def lines(self) -> list[dict]:
        return [json.loads(line) for line in self.data.splitlines() if line]


# ---------------------------------------------------------------- what the card is done by ----


def test_a_ping_through_layer1_hands_its_events_to_emit_writes_the_text_log_and_prints_nothing(
    made, root, tmp_path, capfd
) -> None:
    alpha = _alpha(root)
    log = tmp_path / "text.log"
    heard = _heard(made, log)
    ended = _run(made, alpha, _play(tmp_path, {"name": "ping", "ansible.builtin.ping": {}}))
    assert ended.status == 0, ended.said
    assert ended.counts == events.Counts(failed=0, unreachable=0, changed=0, recap=True)
    # Each a step of the machine, by the name a person chose, as it came: the play, the task, its result, the recap.
    assert _statuses(heard) == ["play", "task", "ok", "recap"]
    assert {(event.kind, event.machine) for event in heard} == {("step", "alpha")}
    assert [event.text for event in heard[1:3]] == ["ping", "ping"]
    assert heard[2].data == {"status": "ok", "part": None, "task": "ping"}
    assert heard[3].data["counts"] == {
        "ok": 1,
        "changed": 0,
        "unreachable": 0,
        "failed": 0,
        "skipped": 0,
        "rescued": 0,
        "ignored": 0,
    }
    assert log.read_text().splitlines() == [
        "alpha play -: all",
        "alpha task -: ping",
        "alpha ok -: ping",
        "alpha recap -: ok=1 changed=0 unreachable=0 failed=0 skipped=0 rescued=0 ignored=0",
    ]
    # Nothing on this process's terminal, and nothing of Ansible's own output for a play that went as it should.
    out, err = capfd.readouterr()
    assert out + err == ""
    assert "PLAY" not in ended.said and "ok=1" not in ended.said, ended.said
    # A step keeps nothing in the record.
    assert "step" not in made.record.read_text() and children.live(made) == []


def test_a_role_s_task_says_its_part_and_its_name_without_the_role(made, root, tmp_path) -> None:
    alpha = _alpha(root)
    role = tmp_path / "roles" / "probe_role" / "tasks"
    role.mkdir(parents=True)
    (role / "main.yml").write_text(json.dumps([{"name": "say it", "ansible.builtin.debug": {"msg": VISIBLE}}]))
    play = tmp_path / "play.yml"
    play.write_text(json.dumps([{"hosts": "all", "gather_facts": False, "roles": ["probe_role"]}]))
    heard = _heard(made, tmp_path / "text.log")
    assert _run(made, alpha, play).status == 0
    [said] = [event for event in heard if event.data.get("status") == "ok"]
    assert (said.text, said.data["part"], said.data["task"], said.data["message"]) == (
        "say it",
        "probe_role",
        "say it",
        VISIBLE,
    )


# ---------------------------------------------------------------- what is never written ----


def _failing_play(folder: Path, made_machine: Machine) -> Path:
    """A play whose no_log tasks fail with CENSORED in their message, output and exception, and whose last task fails
    the same way without no_log, with VISIBLE: every failure ignored, so that the play goes on to its recap."""
    _raises_beside(folder)
    return _play(
        folder,
        {"name": "raises hidden", "aw_raise": {"say": CENSORED}, "no_log": True, "ignore_errors": True},
        _command(
            "prints hidden",
            made_machine,
            f"echo {CENSORED}; echo {CENSORED} >&2; exit 3",
            no_log=True,
            ignore_errors=True,
        ),
        {
            "name": "loops hidden",
            "ansible.builtin.fail": {"msg": "{{ item }}"},
            "loop": [CENSORED],
            "no_log": True,
            "ignore_errors": True,
        },
        {"name": "raises said", "aw_raise": {"say": VISIBLE}, "ignore_errors": True},
    )


def test_a_no_log_task_that_fails_writes_no_message_or_exception_on_the_pipe(made, root, tmp_path) -> None:
    alpha = _alpha(root)
    raw = Raw(made, alpha, _failing_play(tmp_path, alpha), tmp_path)
    assert raw.read() is True
    assert raw.ended() == 0, raw.printed.read_text()
    assert CENSORED.encode() not in raw.data
    lines = raw.lines()
    hidden = [line for line in lines if line["event"] == "failed" and line["task"].endswith("hidden")]
    # Each censored failure, the loop's item and the loop itself: its kind, its task and its part alone, and whether
    # it was ignored, which Ansible says of a task and never of an item.
    assert len(hidden) == 4, lines
    for line in hidden:
        if "item" in line:
            assert line == {"event": "failed", "task": "loops hidden", "part": None, "item": None, "censored": True}
        else:
            assert set(line) == {"event", "task", "part", "ignored", "censored"}, line
    # The same failure without no_log says its message and its exception: the absence above is the censoring's.
    [said] = [line for line in lines if line["event"] == "failed" and line["task"] == "raises said"]
    assert VISIBLE in said["msg"] and VISIBLE in said["exception"], said
    assert CENSORED not in raw.printed.read_text()


def test_a_no_log_task_that_fails_reaches_neither_emit_nor_the_text_log(made, root, tmp_path) -> None:
    alpha = _alpha(root)
    log = tmp_path / "text.log"
    heard = _heard(made, log)
    ended = _run(made, alpha, _failing_play(tmp_path, alpha))
    assert ended.status == 0, ended.said
    # Ignored failures: Ansible's recap counts none, and the command that failed as changed, as command says it.
    assert ended.counts == events.Counts(failed=0, unreachable=0, changed=1, recap=True)
    hidden = [event for event in heard if event.data.get("status") == "failed" and event.text.endswith("hidden")]
    assert len(hidden) == 4 and all(event.data.get("censored") is True for event in hidden), hidden
    assert not [event for event in hidden if {"message", "exception", "stdout", "stderr", "warnings"} & set(event.data)]
    [said] = [event for event in heard if event.data.get("status") == "failed" and event.text == "raises said"]
    assert VISIBLE in said.data["message"] and VISIBLE in said.data["exception"]
    assert said.data["ignored"] is True
    dumped = _dumped(heard, tmp_path / "events.json")
    assert searched_for([CENSORED], dumped, log, made.folder) == []
    assert CENSORED not in ended.said


def test_a_secret_of_the_redaction_list_printed_by_a_task_is_struck_from_every_event(made, root, tmp_path) -> None:
    alpha = _alpha(root)
    split = f"{REDACTED[:5]}\x1b[0m{REDACTED[5:]}"
    play = _play(
        tmp_path,
        {"name": f"names {REDACTED}", "ansible.builtin.debug": {"msg": f"says {REDACTED}"}},
        _command("prints it", alpha, f"echo {REDACTED}; echo {REDACTED} >&2"),
        {"name": "prints it split", "ansible.builtin.debug": {"msg": split}},
        _command("fails with it", alpha, f"echo {REDACTED}; exit 4", ignore_errors=True),
        {"name": "loops on it", "ansible.builtin.debug": {"msg": "{{ item }}"}, "loop": [REDACTED]},
    )
    log = tmp_path / "text.log"
    heard = _heard(made, log)
    # At -v, so that each result says its output too.
    ended = _run(made, alpha, play, verbosity=1, redacted=[Secret(REDACTED)])
    assert ended.status == 0, ended.said
    dumped = _dumped(heard, tmp_path / "events.json")
    assert searched_for([REDACTED], dumped, log, made.folder) == []
    assert REDACTED not in ended.said
    # It was there to strike: in a task's name, a message, an output, an item and the message split by an escape.
    struck = [event for event in heard if MARK in json.dumps([event.text, dict(event.data)])]
    assert {event.data.get("task") for event in struck} >= {
        f"names {MARK}",
        "prints it",
        "prints it split",
        "fails with it",
        "loops on it",
    }
    [split_said] = [event for event in _of(heard, "prints it split") if event.data.get("status") == "ok"]
    assert split_said.data["message"] == MARK
    [printed] = [event for event in _of(heard, "prints it") if event.data.get("status") == "changed"]
    assert (printed.data["stdout"], printed.data["stderr"]) == (MARK, MARK)


def test_a_task_that_prints_a_control_sequence_reaches_the_text_log_as_plain_text(made, root, tmp_path) -> None:
    alpha = _alpha(root)
    sequences = "\x1b]0;retitled\x07plain \x1b[31mred\x1b[0m and \x1b]52;c;Y2xpcA==\x07clipboard\x9b2J"
    play = _play(
        tmp_path,
        {"name": "says escapes", "ansible.builtin.debug": {"msg": sequences}},
        _command(
            "prints escapes",
            alpha,
            r"printf '\033]0;retitled\007plain \033[31mred\033[0m\n' >&2; exit 2",
            ignore_errors=True,
        ),
    )
    log = tmp_path / "text.log"
    heard = _heard(made, log)
    assert _run(made, alpha, play).status == 0
    written = log.read_bytes()
    assert not [byte for byte in written if byte < 0x20 and byte not in b"\t\n"], written
    assert "\x9b".encode() not in written and b"retitled" not in written and b"Y2xpcA" not in written
    text = written.decode()
    assert "message: plain red and clipboard" in text and "stderr: plain red" in text, text
    [said] = [event for event in heard if event.data.get("status") == "ok"]
    assert said.data["message"] == "plain red and clipboard"


def test_what_the_child_printed_is_struck_and_plain_too(made, root, tmp_path) -> None:
    # A play Ansible refuses before it starts quotes its line, a secret of the run's among it: its own words, which no
    # event carries.
    alpha = _alpha(root)
    play = _play(tmp_path, {"name": "unknown", REDACTED: {}})
    heard = _heard(made, tmp_path / "text.log")
    ended = _run(made, alpha, play, redacted=[Secret(REDACTED)])
    assert ended.status != 0 and heard == []
    assert REDACTED not in ended.said and MARK in ended.said, ended.said
    assert ended.counts == events.Counts(failed=0, unreachable=0, changed=0, recap=False)


def test_the_end_of_what_the_child_printed_starts_at_a_line_never_inside_a_secret(made, tmp_path) -> None:
    # The window of the last MOST bytes begins five bytes into a line that holds the secret: that line is left out
    # whole, never handed on with the secret's end alone, which no strike by its exact text would find.
    first = f"{REDACTED}\n".encode()
    body = b"y" * (layer1.MOST + 5 - len(first) - 1) + b"\n"
    printed = tmp_path / "printed"
    printed.write_bytes(first + body)
    with printed.open("rb") as stream:
        said = layer1._said(stream, events.Heard(made, "alpha", [Secret(REDACTED)]))
    assert REDACTED[5:] not in said and set(said) == {"y"}, said[:80]
    # Short of the bound, all of it, struck.
    printed.write_bytes(first + b"z\n")
    with printed.open("rb") as stream:
        assert layer1._said(stream, events.Heard(made, "alpha", [Secret(REDACTED)])) == f"{MARK}\nz"


# ---------------------------------------------------------------- the diff ----


def _diffing_play(folder: Path, made_machine: Machine, *, keyword: bool = False) -> Path:
    shown = {
        "name": "shown",
        "ansible.builtin.lineinfile": {"path": str(made_machine.home / "shown.txt"), "line": VISIBLE, "create": True},
    }
    if keyword:
        shown["diff"] = True
    hidden = {
        "name": "hidden",
        "ansible.builtin.lineinfile": {"path": str(made_machine.home / "hidden.txt"), "line": CENSORED, "create": True},
        "no_log": True,
    }
    return _play(folder, shown, hidden)


def test_a_diff_is_written_in_a_run_given_diff_and_never_for_a_no_log_task(made, root, tmp_path) -> None:
    alpha = _alpha(root)
    log = tmp_path / "text.log"
    heard = _heard(made, log)
    ended = _run(made, alpha, _diffing_play(tmp_path, alpha), check=True, diff=True)
    assert ended.status == 0, ended.said
    diffs = [event for event in heard if event.data.get("status") == "diff"]
    assert [event.text for event in diffs] == ["shown"]
    assert f"+{VISIBLE}" in diffs[0].data["diff"]
    assert searched_for([CENSORED], _dumped(heard, tmp_path / "events.json"), log) == []
    # Check mode: nothing was written on the machine.
    assert not (alpha.home / "shown.txt").exists()


def test_a_task_s_own_diff_keyword_writes_no_diff_in_a_run_not_given_diff(made, root, tmp_path) -> None:
    alpha = _alpha(root)
    heard = _heard(made, tmp_path / "text.log")
    ended = _run(made, alpha, _diffing_play(tmp_path, alpha, keyword=True), check=True)
    assert ended.status == 0, ended.said
    assert "diff" not in _statuses(heard) and not [event for event in heard if "diff" in event.data]
    assert [event.data.get("status") for event in _of(heard, "shown")] == ["task", "changed"]


# ---------------------------------------------------------------- the pipe's end ----


@pytest.mark.parametrize("when", ["before the play", "in the play"])
def test_the_pipe_closed_by_its_reader_ends_the_writing_and_the_play_goes_on_without_a_traceback(
    made, root, tmp_path, when
) -> None:
    alpha = _alpha(root)
    play = _play(
        tmp_path,
        {"name": "ping", "ansible.builtin.ping": {}},
        _command("wait", alpha, "n=0; while [ ! -e go ] && [ $n -lt 600 ]; do sleep 0.1; n=$((n+1)); done; test -e go"),
        _command("after", alpha, "touch after"),
        {"name": "says after", "ansible.builtin.debug": {"msg": "after"}},
    )
    if when == "before the play":
        (alpha.home / "go").touch()
    raw = Raw(made, alpha, play, tmp_path)
    if when == "before the play":
        raw.close()
    else:
        assert raw.read(until=lambda data: b'"task":"wait"' in data) is False
        raw.close()
        (alpha.home / "go").touch()
    assert raw.ended() == 0, raw.printed.read_text()
    # The play went on to its end, and said nothing of the pipe: no traceback, no failing callback.
    assert (alpha.home / "after").exists()
    printed = raw.printed.read_text()
    assert "Traceback" not in printed and "callback" not in printed.lower() and "pipe" not in printed, printed


def test_a_listener_that_fails_ends_the_child_with_its_tree_and_its_error_is_raised(made, root, tmp_path) -> None:
    alpha = _alpha(root)
    play = _play(tmp_path, _command("wait", alpha, "touch started && exec sleep 600"))
    held: dict[str, object] = {}
    remembered: dict[int, str] = {}

    class Broken(Exception):
        pass

    def breaks(event: jobs.Event) -> None:
        if event.data.get("status") != "task":
            return
        # Once the task's command runs, so that the child has a tree to end: remembered, then the fault.
        deadline = time.monotonic() + PLAY_MOST
        while not (alpha.home / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        process = held["process"]
        remembered.update(proc.snapshot(process.pid))
        raise Broken("a listener's own fault")

    made.listen(breaks)
    # Its command sleeps 600 s and the call is given PLAY_MOST: a child left to run would time the call out.
    with pytest.raises(Broken):
        _run(made, alpha, play, started=lambda process: held.setdefault("process", process))
    assert len(remembered) >= 2, remembered
    assert _still(remembered) == [] and children.live(made) == []
    process = held["process"]
    assert process.returncode is not None


# ---------------------------------------------------------------- the callback, by itself ----


def _callback_module():
    spec = importlib.util.spec_from_file_location("aw_events_under_test", CALLBACK_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_callback_and_the_reader_agree_on_the_variable_and_the_kinds() -> None:
    callback = _callback_module()
    assert callback.VARIABLE == layer1.EVENTS_VARIABLE
    assert callback.KINDS == events.KINDS
    assert CALLBACK_FILE.is_file() and callback.CallbackModule.CALLBACK_NAME == layer1.CALLBACK


def test_the_callback_refuses_to_start_without_its_pipe(made, root, tmp_path) -> None:
    alpha = _alpha(root)
    raw = Raw(made, alpha, _play(tmp_path, {"name": "ping", "ansible.builtin.ping": {}}), tmp_path, events_pipe=False)
    assert raw.read() is True and raw.data == b""
    assert raw.ended() != 0
    assert f"{layer1.EVENTS_VARIABLE} names no descriptor" in raw.printed.read_text()


class _Named:
    def __init__(self, name: str) -> None:
        self.name = name

    def get_name(self) -> str:
        return self.name


def _in_process(monkeypatch):
    """The callback made in this process, writing on a pipe of the test's own: the module, the callback, the read
    end."""
    callback = _callback_module()
    reading, writing = os.pipe()
    monkeypatch.setenv(callback.VARIABLE, str(writing))
    return callback, callback.CallbackModule(), reading, writing


def _drained(reading: int) -> list[dict]:
    os.set_blocking(reading, False)
    data = b""
    try:
        while chunk := os.read(reading, 65536):
            data += chunk
    except BlockingIOError:
        pass
    return [json.loads(line) for line in data.splitlines() if line]


def test_every_string_the_callback_writes_passes_through_masked(monkeypatch) -> None:
    # F5a puts mask_secrets() there: until then the identity, and every string goes through it.
    callback, made_callback, reading, writing = _in_process(monkeypatch)
    try:
        assert callback.masked(VISIBLE) == VISIBLE
        monkeypatch.setattr(callback, "masked", lambda text: text.upper())
        made_callback.v2_playbook_on_play_start(_Named(VISIBLE))
        assert _drained(reading) == [{"event": "PLAY", "name": VISIBLE.upper()}]
    finally:
        os.close(reading)
        os.close(writing)


def test_a_fault_of_a_hook_is_written_as_an_event_and_a_closed_pipe_ends_the_writing(monkeypatch) -> None:
    _, made_callback, reading, writing = _in_process(monkeypatch)
    try:
        # A result that is none: the hook's fault, its type alone, never raised into Ansible.
        made_callback.v2_runner_on_ok(object())
        assert _drained(reading) == [{"event": "fault", "hook": "v2_runner_on_ok", "error": "AttributeError"}]
        os.close(reading)
        reading = -1
        previous = signal.signal(signal.SIGPIPE, signal.SIG_IGN)
        try:
            made_callback.v2_playbook_on_play_start(_Named("after the close"))
            made_callback.v2_playbook_on_play_start(_Named("and after that"))
        finally:
            signal.signal(signal.SIGPIPE, previous)
        assert made_callback._fd is None
    finally:
        for left in (reading, writing):
            if left >= 0:
                os.close(left)


def test_a_string_past_the_callback_s_bound_keeps_its_end_from_a_line_break_never_a_part_of_a_line() -> None:
    callback = _callback_module()
    most = callback.STRING_MOST
    lines = [f"{index:08d}-{'x' * 50}" for index in range(most // 40)]
    long = "\n".join(lines)
    bounded = callback._bounded(long)
    note, _, kept = bounded.partition("\n")
    assert note == f"({len(long) - len(kept)} characters left out)"
    assert len(kept) <= most and long.endswith(kept) and kept.split("\n")[0] in lines
    assert (
        callback._bounded("y" * (most + 1))
        == f"({most + 1} characters left out: too long, with no line break to cut at)"
    )
    assert callback._bounded("z" * most) == "z" * most


# ---------------------------------------------------------------- the reader, by itself ----


def _line(**event) -> bytes:
    return json.dumps(event).encode() + b"\n"


def test_a_line_that_is_no_event_is_said_as_one_that_could_not_be_read(made) -> None:
    heard: list[jobs.Event] = []
    made.listen(heard.append)
    reader = events.Heard(made, "alpha")
    for line in (b"{not json\n", b"[1, 2]\n", _line(event="exploded"), b"[" * 100000 + b"\n"):
        reader.hand(line)
    assert [(event.text, event.data) for event in heard] == [(events.NOT_READ, {"status": "unreadable"})] * 4


def test_the_reader_keeps_only_its_fields_each_held_to_its_type(made) -> None:
    heard: list[jobs.Event] = []
    made.listen(heard.append)
    reader = events.Heard(made, "alpha")
    reader.hand(
        _line(
            event="failed",
            task="t",
            part=7,
            msg=["not", "a", "string"],
            stdout="out",
            rc="3",
            attempts=True,
            ignored="yes",
            beside=VISIBLE,
            warnings=["w", 5, ""],
        )
    )
    [event] = heard
    assert event.data == {"status": "failed", "part": None, "task": "t", "stdout": "out", "warnings": ["w"]}


def test_a_line_longer_than_the_bound_is_left_out_whole_and_the_read_goes_on(made) -> None:
    heard: list[jobs.Event] = []
    made.listen(heard.append)

    async def scenario(chunks: list[bytes]) -> events.Counts:
        stream = asyncio.StreamReader(limit=events.LINE_MOST)
        reader = events.Heard(made, "alpha")
        reading = asyncio.ensure_future(reader.read(stream))
        for chunk in chunks:
            stream.feed_data(chunk)
            for _ in range(5):
                await asyncio.sleep(0)
        stream.feed_eof()
        return await reading

    long = b'{"event": "play", "name": "' + b"x" * (events.LINE_MOST + 10)
    # Fed whole, its end in the buffer; and fed in two, its end after the bound was passed.
    for chunks in (
        [long + b'"}\n' + _line(event="play", name="next")],
        [long, b'"}\n' + _line(event="play", name="next")],
    ):
        heard.clear()
        asyncio.run(scenario(chunks))
        assert [(event.text, event.data["status"]) for event in heard] == [
            (events.TOO_LONG.format(most=events.LINE_MOST), "unreadable"),
            ("next", "play"),
        ]
    # A line the child did not finish at the pipe's end is no event.
    heard.clear()
    asyncio.run(scenario([_line(event="play", name="whole"), b'{"event": "play", "na']))
    assert [event.text for event in heard] == ["whole"]


def test_the_counts_are_the_recap_s_where_there_is_one_else_counted_from_the_results(made) -> None:
    reader = events.Heard(made, "alpha")
    for event in (
        {"event": "failed", "task": "a"},
        {"event": "failed", "task": "b", "ignored": True},
        {"event": "changed", "task": "c"},
        {"event": "changed", "task": "d"},
        {"event": "changed", "task": "e", "item": "one"},
        {"event": "unreachable", "task": "f"},
        {"event": "ok", "task": "g"},
    ):
        reader.hand(_line(**event))
    assert reader.counts == events.Counts(failed=1, unreachable=1, changed=2, recap=False)
    reader.hand(
        _line(event="stats", hosts={"agent-warehouse-x": {"ok": 4, "changed": 3, "failures": 2, "unreachable": 0}})
    )
    assert reader.counts == events.Counts(failed=2, unreachable=0, changed=3, recap=True)


def test_a_secret_is_struck_before_and_after_the_text_is_made_plain_and_before_it_is_cut(made) -> None:
    secret = Secret(REDACTED)
    reader = events.Heard(made, "alpha", [secret])
    assert reader.clean(f"a {REDACTED} b") == f"a {MARK} b"
    assert reader.clean(f"{REDACTED[:3]}\x1b[1m{REDACTED[3:]}") == MARK
    # Cut after the strike: the secret straddles where the cut falls, and no part of it is left there, its mark whole.
    cut = reader.clean("w" * (events.MESSAGE_MOST - 10) + REDACTED + "w" * 20)
    assert REDACTED[:4] not in cut and MARK in cut and cut.endswith("…") and len(cut) == events.MESSAGE_MOST
    tail = reader.clean("w" * 20 + REDACTED + "w" * (events.OUTPUT_MOST - 10), events.OUTPUT_MOST, end=True)
    assert REDACTED[-4:] not in tail and MARK in tail and tail.startswith("…") and len(tail) == events.OUTPUT_MOST
    # A code point no UTF-8 holds is replaced, never handed on.
    assert reader.clean("a\udcffb") == "a?b"


@pytest.mark.parametrize("redacted", [[REDACTED], "one", [Secret(REDACTED), b"two"], Secret(REDACTED)])
def test_the_redaction_list_holds_secrets_alone(made, redacted) -> None:
    with pytest.raises(TypeError):
        events.Heard(made, "alpha", redacted)
