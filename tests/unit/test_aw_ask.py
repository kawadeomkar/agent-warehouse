"""Questions at the terminal (lib/claude_vm/runs/ask.py, runs/live.py's aside, tui/choose.py's asked;
docs/architecture/engine.md §16.8).

At a pseudo-terminal of the test's own (harness._at_a_pseudo_terminal), each run here is a child process made by
runs.run as a verb makes one, whose body draws deploy's live view at a sign-in and asks its questions through
runs.ask, every event it emits written down as the run's text log will hear it: a yes or no picked with the arrow
keys, the line it leaves saying the answer; a line typed, its echo on; a hidden answer whose characters reach neither
the screen, the run's record nor its events (aw_kit.searched_for), given back as a Secret; Enter taking the default;
keys typed before a question was there never its answer; two questions asked at once, from two tasks, the second
drawn only once the first is answered; a report said while a question is up shown after it, never on its line; a
choice typed, and asked once more, where the terminal cannot draw the menu; one Ctrl-C at a line, a hidden answer and
the menu ending the run, cancelled, with the echo back; the region gone from the screen while a question is up, the
title saying the run waits for you, and a frame drawn again only half a second after the answer, none between two
questions; each question a `waiting` event, an empty one once it is answered, and a `step` event that says what was
answered. With standard input on /dev/null, the default at once and a line that says the question was not asked.

In this process: a question without a default cannot be built (the rule with a test of its own), with every other
refusal of a Question; nobody to ask - a pipe, /dev/null, a terminal whose foreground the run is not - gives the default
and never reads; two questions asked at once taking turns at the terminal; the menu's keys cut from a read as
choose.ask reads them one at a time, and the menu asked through a
read of the test's own, a sequence in parts, Esc, Ctrl-C and the terminal's size looked at again; a question and the
menu putting the terminal's settings back through a signal that interrupts the change (EINTR, made again by
tui/live_region.py's set_terminal); what the view says while a question is up kept until it is answered, or said
before the summary where the run's end cuts the question short, and the bell of a question's wait rung only after a
quiet while; and by a scan, that ask.py reads the terminal only through the run's Context.typed.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import errno
import hashlib
import io
import json
import os
import pty
import re
import subprocess
import sys
import termios
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import harness
import pytest
from aw_kit import environment, searched_for, stand_ins
from harness import REPO

from claude_vm.fleet import paths
from claude_vm.runs import ask as asking
from claude_vm.runs import job as jobs
from claude_vm.runs import live
from claude_vm.runs.ask import Choice, Question
from claude_vm.runs.stages import DEPLOY
from claude_vm.secret import Secret
from claude_vm.tui import choose, live_region

LIB = REPO / "lib"
ASK = LIB / "claude_vm" / "runs" / "ask.py"
SYNC_ON = "\x1b[?2026h"
# What a person types as a hidden answer: low in entropy, as every fake secret here is.
HIDDEN = "hidden-answer-typed-here"
YES_NO = (Choice("y", "yes"), Choice("n", "no"))

# ---------------------------------------------------------------- a run at a pseudo-terminal ----

# A run, in a child process, as a verb makes one: argv is the lib folder, the jobs folder, the test's folder and the
# body's way. Its body hears every event the run emits (events.jsonl, with when: the run's text log's stand-in), draws
# deploy's live view on a standard output that keeps each write with when, begins alpha's sign-in, and asks the way's
# questions, keeping each answer (a hidden one by its digest alone) and when it asked and was answered; where the way
# types ahead, it waits for the test's keys first. Then it ends the sign-in and draws a while.
RUN = r"""
import asyncio, atexit, hashlib, io, json, os, sys, time
lib, jobs_dir, folder, way = sys.argv[1:5]
sys.path.insert(0, lib)
from pathlib import Path
from claude_vm import runs
from claude_vm.runs import live
from claude_vm.runs.ask import Choice, ask
from claude_vm.runs.stages import DEPLOY
from claude_vm.secret import Secret

folder = Path(folder)
YES_NO = [Choice("y", "yes"), Choice("n", "no")]
VPN = [Choice("none", "none", "no VPN"), Choice("tailscale", "Tailscale")]
ADDRESS = ("Proxmox host address:", "", (), False)
TAILSCALE = ("Use Tailscale on the VMs?", "n", YES_NO, False)
KEY = ("Tailscale auth key:", Secret(""), (), True)
WHICH = ("Which VPN do the VMs join?", "none", VPN, False)
USER = ("GitHub user:", "", (), False)
QUESTIONS = {
    "yes-no": [TAILSCALE],
    "menu-ctrl-c": [TAILSCALE],
    "line": [ADDRESS],
    "line-ctrl-c": [ADDRESS],
    "enter": [ADDRESS, TAILSCALE],
    "ahead": [ADDRESS],
    "hidden": [KEY],
    "hidden-ctrl-c": [KEY],
    "typed": [WHICH, WHICH],
    "null": [TAILSCALE, ADDRESS, KEY],
    "report": [ADDRESS],
}
# Ways whose questions are asked at once, each from a task of its own, as two machines' work would ask them.
AT_ONCE = {
    "both": [ADDRESS, USER],
}


class Kept(io.TextIOBase):
    def __init__(self, real):
        super().__init__()
        self.real, self.writes = real, []

    @property
    def encoding(self):
        return self.real.encoding

    def writable(self):
        return True

    def isatty(self):
        return self.real.isatty()

    def fileno(self):
        return self.real.fileno()

    def write(self, text):
        self.writes.append([time.monotonic(), text])
        return self.real.write(text)

    def flush(self):
        self.real.flush()


def heard(event):
    said = {"at": time.monotonic(), "kind": event.kind, "machine": event.machine, "text": event.text,
            "data": event.data}
    with (folder / "events.jsonl").open("a") as out:
        out.write(json.dumps(said) + "\n")


def kept(answer):
    if isinstance(answer, Secret):
        return {"secret": hashlib.sha256(answer.reveal().encode()).hexdigest()}
    return {"value": answer}


async def body(ctx):
    out = Kept(sys.stdout)
    atexit.register(lambda: (folder / "kept.json").write_text(json.dumps(out.writes)))
    ctx.job.listen(heard)
    live.Live(ctx.job, DEPLOY, out, cancelled=ctx.cancelled)
    ctx.emit("stage", "alpha", "sign-in")
    ctx.emit("step", "alpha", "claude", stage="sign-in")
    await asyncio.sleep(0.4)
    if way == "ahead":
        (folder / "ahead").write_text("")
        while not (folder / "typed").exists():
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)
    answers = []

    async def asked(text, default, choices, hidden):
        began = time.monotonic()
        answer = await ask(ctx, text, default, choices, hidden, machine="alpha")
        answers.append({**kept(answer), "default": answer is default, "took": time.monotonic() - began})
        (folder / "answers.json").write_text(json.dumps(answers))

    if way in AT_ONCE:
        await asyncio.gather(*(asked(*question) for question in AT_ONCE[way]))
    for question in QUESTIONS.get(way, ()):
        if way == "report":
            # Once the question is up, the run says a report, as another machine's work would.
            up = asyncio.Event()
            ctx.job.listen(lambda event: event.kind == "waiting" and event.text and up.set())

            async def report():
                await up.wait()
                ctx.emit("report", "alpha", "alpha is up at 192.0.2.61")
                (folder / "reported").write_text("")

            reporting = asyncio.ensure_future(report())
        await asked(*question)
        if way == "report":
            await reporting
    ctx.emit("step", "alpha", "claude", stage="sign-in", outcome="done")
    await asyncio.sleep(1.0)
    return 0


draft = runs.Draft(
    Path(jobs_dir),
    "deploy",
    "aw deploy alpha",
    "pve",
    ["alpha"],
    {"kind": "terminal"},
    True,
    values={"names": ["alpha"]},
    lock_machines=[("pve", "alpha")],
)
sys.exit(runs.run(draft, body))
"""

# At a pseudo-terminal: the run as a child in the terminal's foreground group, and once it has ended, whether the
# terminal echoes and how the run ended, as one line of JSON. A Ctrl-C typed reaches both: this one takes it with a
# handler that does nothing, never SIG_IGN, which the run would inherit (as tests/unit/test_aw_attached.py's).
WATCHES = r"""
import json, signal, subprocess, sys, termios
signal.signal(signal.SIGINT, lambda *_: None)
run = subprocess.Popen(sys.argv[1:])
status = run.wait()
print(json.dumps({"echo": bool(termios.tcgetattr(0)[3] & termios.ECHO), "status": status}), flush=True)
"""
WATCHED = re.compile(rb'\{"echo": (true|false), "status": (-?\d+)\}')
CHILD = [sys.executable, "-I", "-X", "utf8", "-c", RUN]
HINT = choose.HINT.encode()


class Ran:
    """A run at a pseudo-terminal: its output, how it ended and the terminal's echo once it had, its job's record, the
    events it emitted, its answers, and what its live view wrote, each with when."""

    def __init__(self, folder: Path, raw: bytes, watched: dict) -> None:
        self.raw, self.watched = raw, watched
        self.text = raw.decode(errors="replace")
        made = jobs.listed(paths.jobs_dir(paths.home(REPO)))
        assert len(made) == 1, made
        self.record = made[0].read()
        events = folder / "events.jsonl"
        self.events = [json.loads(line) for line in events.read_text().splitlines()] if events.is_file() else []
        answers = folder / "answers.json"
        self.answers = json.loads(answers.read_text()) if answers.is_file() else None
        writes = folder / "kept.json"
        self.writes = json.loads(writes.read_text()) if writes.is_file() else []

    def of(self, kind: str) -> list[dict]:
        return [event for event in self.events if event["kind"] == kind and event["machine"] == "alpha"]

    def asked(self) -> list[tuple[float, float]]:
        """When each question's wait began and ended."""
        waits = self.of("waiting")
        begun = [event["at"] for event in waits if event["text"]]
        ended = [event["at"] for event in waits if not event["text"]]
        assert len(begun) == len(ended), waits
        return list(zip(begun, ended, strict=True))

    def frames(self) -> list[float]:
        """When the region drew a frame of the run: a redraw with the sign-in's row in it."""
        return [at for at, text in self.writes if SYNC_ON in text and "sign in" in text]


# Each way's keys, typed in turn: at the first sight of the bytes wanted after the last, and for the menu, once the
# terminal is in its raw mode.
Plan = list[tuple[bytes, list[bytes], bool]]


def _at_a_terminal(home: Path, folder: Path, way: str, plan: Plan, *, term: str = "xterm-256color") -> Ran:
    jobs_dir = paths.jobs_dir(paths.home(REPO))
    argv = [sys.executable, "-I", "-c", WATCHES, *CHILD, str(LIB), str(jobs_dir), str(folder), way]
    temporary = folder / "tmp"
    temporary.mkdir(exist_ok=True)
    env = environment(home, {"TERM": term, "PYTHONUTF8": "1", "TMPDIR": str(temporary)})
    left = list(plan)
    state = {"at": 0, "ahead": False}

    def act(chunk: bytes, output: bytes, master: int, process: subprocess.Popen) -> None:
        if way == "ahead" and not state["ahead"] and (folder / "ahead").is_file():
            # Typed while the run is at work, before its question: never the question's answer.
            state["ahead"] = True
            os.write(master, b"stale\n")
            (folder / "typed").write_text("")
        if not left:
            return
        wanted, keys, raw = left[0]
        found = output.find(wanted, state["at"])
        if found < 0 or (raw and not harness._in_raw_mode(master)):
            return
        if way == "report" and not (folder / "reported").is_file():
            # The answer typed only once the run has said its report, while the question is up.
            return
        left.pop(0)
        state["at"] = found + len(wanted)
        for key in keys:
            os.write(master, key)
            time.sleep(0.15)

    rc, raw = harness._at_a_pseudo_terminal(argv, folder, env, size=(100, 30), act=act, timeout=90, poll=0.05)
    harness._fail_if_the_desktop_was_reached(stand_ins(home))
    found = WATCHED.search(raw)
    assert rc == 0 and found is not None, raw.decode(errors="replace")
    assert not left, f"no {left[0][0]!r} appeared:\n" + raw.decode(errors="replace")
    return Ran(folder, raw, {"echo": found.group(1) == b"true", "status": int(found.group(2))})


def _done(ran: Ran) -> None:
    assert ran.watched == {"echo": True, "status": 0}, ran.text
    assert ran.record["outcome"] == "succeeded", ran.record


def _stopped(ran: Ran) -> None:
    # One Ctrl-C at the question ends the run there, cancelled, the terminal's echo back.
    assert ran.watched == {"echo": True, "status": 130}, ran.text
    assert (ran.record["outcome"], ran.record["exit"], ran.record["why"]) == ("cancelled", 130, "Ctrl-C"), ran.record
    assert "aw: stopping" in ran.text and "^Caw:" not in ran.text, ran.text
    assert ran.answers is None, ran.answers


def test_a_yes_or_no_is_a_menu_picked_with_the_arrow_keys_the_region_out_of_the_way(home, tmp_path) -> None:
    ran = _at_a_terminal(home, tmp_path, "yes-no", [(HINT, [b"\x1b[A", b"\r"], True)])
    _done(ran)
    # The default, no, was marked first: up picked yes.
    assert ran.answers is not None and [answer["value"] for answer in ran.answers] == ["y"], ran.answers
    # The question above the menu, then the line the menu leaves, and nothing of the menu after it.
    text = ran.text
    assert text.index("Use Tailscale on the VMs?") < text.index("Use Tailscale on the VMs? yes"), text
    assert choose.HINT not in text[text.index("Use Tailscale on the VMs? yes") :], text
    # Its wait, ended once answered, and a line of alpha's work that says what was answered.
    assert [event["text"] for event in ran.of("waiting")] == ["Use Tailscale on the VMs?", ""]
    assert "Use Tailscale on the VMs?: yes" in [event["text"] for event in ran.of("step")]
    [(begun, ended)] = ran.asked()
    writes = ran.writes
    # The region erased before the question and nothing drawn while it was up, the title saying the run waits for you.
    before = [text for at, text in writes if at < begun]
    assert before and "\x1b[J" in before[-1] and SYNC_ON not in before[-1], before[-3:]
    up = [text for at, text in writes if begun <= at <= ended]
    assert not [text for text in up if SYNC_ON in text], up
    assert any("waiting for you" in text for text in up), up
    # Drawn again, but only half a second after the answer.
    after = [at for at in ran.frames() if at > ended]
    assert after and after[0] >= ended + live_region.SETTLE_SECONDS, (ended, after[:3])


def test_a_line_is_typed_with_its_echo_on_and_said_as_answered(home, tmp_path) -> None:
    ran = _at_a_terminal(home, tmp_path, "line", [(b"Proxmox host address: ", [b"192.0.2.10\n"], False)])
    _done(ran)
    assert ran.answers is not None and [answer["value"] for answer in ran.answers] == ["192.0.2.10"]
    # Echoed as typed, after the question.
    assert re.search(r"Proxmox host address: 192\.0\.2\.10\r?\n", ran.text), ran.text
    assert [event["text"] for event in ran.of("waiting")] == ["Proxmox host address", ""]
    assert "Proxmox host address: 192.0.2.10" in [event["text"] for event in ran.of("step")]
    [(begun, ended)] = ran.asked()
    assert not [text for at, text in ran.writes if begun <= at <= ended and SYNC_ON in text]


def test_two_questions_asked_at_once_are_asked_one_after_the_other(home, tmp_path) -> None:
    # Two tasks of the run ask at once, as two machines' work would: the second drawn only once the first is answered.
    plan = [(b"Proxmox host address: ", [b"192.0.2.10\n"], False), (b"GitHub user: ", [b"octocat\n"], False)]
    ran = _at_a_terminal(home, tmp_path, "both", plan)
    _done(ran)
    assert ran.answers is not None
    assert [answer["value"] for answer in ran.answers] == ["192.0.2.10", "octocat"], ran.answers
    raw = ran.raw
    answered = re.search(rb"Proxmox host address: 192\.0\.2\.10\r\n", raw)
    assert answered is not None and raw.index(b"GitHub user: ") > answered.end(), ran.text
    assert [event["text"] for event in ran.of("waiting")] == ["Proxmox host address", "", "GitHub user", ""]
    [(_, first), (second, _)] = ran.asked()
    assert second >= first, (first, second)


def test_a_report_said_while_a_question_is_up_is_shown_after_it_and_not_on_its_line(home, tmp_path) -> None:
    ran = _at_a_terminal(home, tmp_path, "report", [(b"Proxmox host address: ", [b"192.0.2.10\n"], False)])
    _done(ran)
    [(begun, ended)] = ran.asked()
    [report] = [event for event in ran.events if event["kind"] == "report"]
    assert begun < report["at"] < ended, (begun, report["at"], ended)
    raw = ran.raw
    # The question's line whole, its answer echoed on it, and the report said only after it.
    answered = re.search(rb"Proxmox host address: 192\.0\.2\.10\r\n", raw)
    assert answered is not None, ran.text
    assert raw.find(b"alpha is up at 192.0.2.61") > answered.end(), ran.text


def test_enter_takes_each_questions_default_and_two_questions_show_no_frame_between_them(home, tmp_path) -> None:
    plan = [(b"Proxmox host address: ", [b"\n"], False), (HINT, [b"\r"], True)]
    ran = _at_a_terminal(home, tmp_path, "enter", plan)
    _done(ran)
    assert ran.answers is not None
    # The line's default given back itself; the menu's Enter picks the answer marked first, its default.
    assert [answer["value"] for answer in ran.answers] == ["", "n"] and ran.answers[0]["default"], ran.answers
    steps = [event["text"] for event in ran.of("step")]
    assert "Proxmox host address: left for later" in steps and "Use Tailscale on the VMs?: no" in steps, steps
    [(_, first), (second, _)] = ran.asked()
    assert second - first < live_region.SETTLE_SECONDS, (first, second)
    assert not [at for at in ran.frames() if first < at < second], ran.frames()


def test_keys_typed_before_the_question_are_not_its_answer(home, tmp_path) -> None:
    ran = _at_a_terminal(home, tmp_path, "ahead", [(b"Proxmox host address: ", [b"\n"], False)])
    _done(ran)
    assert ran.answers is not None and [answer["value"] for answer in ran.answers] == [""], ran.answers


def test_a_hidden_answer_reaches_neither_the_screen_the_record_nor_the_events_and_is_a_secret(home, tmp_path) -> None:
    ran = _at_a_terminal(home, tmp_path, "hidden", [(b"Tailscale auth key: ", [HIDDEN.encode() + b"\n"], False)])
    _done(ran)
    assert ran.answers is not None and ran.answers[0]["secret"] == hashlib.sha256(HIDDEN.encode()).hexdigest()
    assert not ran.answers[0]["default"]
    # The question, then a line of its own after it: the Enter was not echoed.
    assert re.search(r"Tailscale auth key: \r?\n", ran.text), ran.text
    assert [event["text"] for event in ran.of("waiting")] == ["Tailscale auth key", ""]
    assert "Tailscale auth key: given" in [event["text"] for event in ran.of("step")]
    screen = tmp_path / "screen.out"
    screen.write_bytes(ran.raw)
    jobs_dir = paths.jobs_dir(paths.home(REPO))
    assert searched_for([HIDDEN], screen, jobs_dir, tmp_path / "events.jsonl") == []


@pytest.mark.parametrize(
    ("way", "plan"),
    [
        ("line-ctrl-c", [(b"Proxmox host address: ", [b"192.0", b"\x03"], False)]),
        ("hidden-ctrl-c", [(b"Tailscale auth key: ", [b"hidden-ans", b"\x03"], False)]),
        ("menu-ctrl-c", [(HINT, [b"\x03"], True)]),
    ],
    ids=["a line", "a hidden answer", "the menu"],
)
def test_one_ctrl_c_at_a_question_ends_the_run(way, plan, home, tmp_path) -> None:
    ran = _at_a_terminal(home, tmp_path, way, plan)
    _stopped(ran)
    assert "hidden-ans" not in ran.text
    # The wait it opened ended with it, and nothing was said answered.
    assert [event["text"] for event in ran.of("waiting")][-1] == ""
    assert [event for event in ran.of("step") if event["data"].get("stage") is None] == []


def test_where_the_menu_cannot_be_drawn_a_choice_is_typed_and_asked_once_more(home, tmp_path) -> None:
    prompt = b"A number or a name, Enter for none: "
    plan = [(prompt, [b"2\n"], False), (prompt, [b"wireguard\n"], False), (prompt, [b"vpn\n"], False)]
    ran = _at_a_terminal(home, tmp_path, "typed", plan, term="dumb")
    _done(ran)
    assert ran.answers is not None
    assert [(answer["value"], answer["default"]) for answer in ran.answers] == [("tailscale", False), ("none", True)]
    text = ran.text
    assert re.search(r"^  1  none  no VPN\r?$", text, re.M) and re.search(r"^  2  Tailscale\r?$", text, re.M), text
    assert "That is not one of them: none, Tailscale." in text, text
    assert "That is not one of them either, so it takes none." in text, text
    assert choose.HINT not in text and SYNC_ON not in text


def test_with_standard_input_on_dev_null_each_question_takes_its_default_at_once_and_says_so(home, tmp_path) -> None:
    jobs_dir = paths.jobs_dir(paths.home(REPO))
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    ended = subprocess.run(
        [*CHILD, str(LIB), str(jobs_dir), str(tmp_path), "null"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=environment(home, {"PYTHONUTF8": "1", "TMPDIR": str(temporary)}),
        timeout=60,
        preexec_fn=harness._default_signals,  # noqa: PLW1509 - only signal, safe beside xdist's thread
    )
    assert ended.returncode == 0, ended.stderr
    answers = json.loads((tmp_path / "answers.json").read_text())
    assert [answer["default"] for answer in answers] == [True, True, True], answers
    assert all(answer["took"] < 1 for answer in answers), answers
    said = ended.stderr.decode().splitlines()
    assert [line for line in said if line.startswith("aw: not asked")] == [
        "aw: not asked, with no terminal to ask at: Use Tailscale on the VMs? - no",
        "aw: not asked, with no terminal to ask at: Proxmox host address - left for later",
        "aw: not asked, with no terminal to ask at: Tailscale auth key - nothing given",
    ], said
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert not [event for event in events if event["kind"] == "waiting"], events
    steps = [event["text"] for event in events if event["kind"] == "step" and not event["data"].get("stage")]
    assert steps == [
        "Use Tailscale on the VMs?: not asked, with no terminal: no",
        "Proxmox host address: not asked, with no terminal: left for later",
        "Tailscale auth key: not asked, with no terminal: nothing given",
    ], steps


# ---------------------------------------------------------------- in this process ----


def test_a_question_without_a_default_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="built with its default, the safe answer"):
        Question("Proxmox host address:", None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="default"):
        asking.ask(object(), "Proxmox host address:")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("given", "error", "words"),
    [
        ({"text": "  \n "}, ValueError, "words to ask"),
        ({"text": Secret("x")}, ValueError, "words to ask"),
        ({"default": Secret("")}, TypeError, "default is a str"),
        ({"hidden": True}, TypeError, "default is a Secret"),
        ({"hidden": True, "default": Secret(""), "choices": ("a",)}, ValueError, "no choices"),
        ({"hidden": 1}, TypeError, "is a bool"),
        ({"choices": "yn"}, TypeError, "a sequence of choices"),
        ({"choices": ("y", "y")}, ValueError, "a value of their own"),
        ({"choices": (1,)}, TypeError, "a Choice or a str"),
        ({"choices": YES_NO, "default": "maybe"}, ValueError, "one of its values, or none"),
    ],
)
def test_a_question_is_held_to_its_form(given, error, words) -> None:
    form = {"text": "Use Tailscale on the VMs?", "default": "", "choices": (), "hidden": False, **given}
    with pytest.raises(error, match=words):
        Question(**form)


def test_a_choice_has_a_value_and_its_words_are_the_value_where_none_are_given() -> None:
    assert Choice("none").text == "none"
    with pytest.raises(ValueError, match="a value"):
        Choice("")
    with pytest.raises(TypeError, match="note is a str"):
        Choice("none", "none", None)  # type: ignore[arg-type]


def test_what_a_question_asks_and_says_of_its_answers() -> None:
    banner = (
        "####\n##  USER INPUT REQUIRED\n####\nThe host's address, as this machine reaches it.\nProxmox host address:"
    )
    question = Question(banner, "")
    assert question.asked == "Proxmox host address"
    assert (question.said(""), question.said("192.0.2.10")) == ("left for later", "192.0.2.10")
    menu = Question("Use Tailscale on the VMs?", "n", YES_NO)
    assert (menu.said("n"), menu.said("")) == ("no", "left for later")
    assert [menu.picked(each) for each in ("1", "YES", "n", " no ", "3", "maybe")] == ["y", "y", "n", "n", None, None]
    assert menu.menu() == {
        "title": "Use Tailscale on the VMs?",
        "label": "",
        "options": [{"value": "y", "text": "yes", "note": ""}, {"value": "n", "text": "no", "note": ""}],
        "chosen": 1,
    }
    nothing = Secret("")
    hidden = Question("Tailscale auth key:", nothing, hidden=True)
    assert (hidden.said(nothing), hidden.said(Secret(HIDDEN))) == ("nothing given", "given")


class _Nobody:
    """What ask reads of a run's Context where it asks nobody: its job, its emit, and whether it is stopping. A read
    fails the test."""

    def __init__(self, job: jobs.Job) -> None:
        self.job = job
        self.cancelled = asyncio.Event()

    def emit(self, kind: str, machine: str | None, text: str, **data: object) -> jobs.Event:
        return self.job.emit(kind, machine, text, **data)

    async def typed(self, fd: int, most: int = 4096) -> bytes:
        pytest.fail("a question with nobody to ask read its standard input")


@contextlib.contextmanager
def _job(machines: tuple[str, ...] = ("alpha",)) -> Iterator[jobs.Job]:
    """A job of this process's own, in the test's fleet home, dropped at the end."""
    made = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "aw deploy alpha", "pve", list(machines), {"kind": "terminal"}, True
    )
    try:
        yield made
    finally:
        jobs.drop(made)


def _pipe_in() -> Iterator[io.TextIOBase]:
    read, write = os.pipe()
    os.close(write)
    with os.fdopen(read) as stdin:
        yield stdin


def _null_in() -> Iterator[io.TextIOBase]:
    with open(os.devnull) as stdin:
        yield stdin


def _background_in() -> Iterator[io.TextIOBase]:
    # A terminal of the test's own, whose foreground this process is not: tcgetpgrp says another group has it.
    master, slave = pty.openpty()
    try:
        with os.fdopen(slave) as stdin:
            yield stdin
    finally:
        os.close(master)


NOBODY: dict[str, Callable[[], Iterator[io.TextIOBase]]] = {
    "a pipe": _pipe_in,
    "/dev/null": _null_in,
    "a terminal in the background": _background_in,
}


@pytest.mark.parametrize("stdin", NOBODY)
def test_with_nobody_to_ask_a_question_takes_its_default_and_reads_nothing(stdin, home, monkeypatch) -> None:
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)
    monkeypatch.setattr(os, "tcgetpgrp", lambda fd: os.getpgrp() + 1)
    nothing = Secret("")
    heard: list[jobs.Event] = []
    with _job() as made, contextlib.contextmanager(NOBODY[stdin])() as given:
        monkeypatch.setattr(sys, "stdin", given)
        made.listen(heard.append)
        context = _Nobody(made)
        menu = asyncio.run(asking.ask(context, "Use Tailscale on the VMs?", "n", YES_NO, machine="alpha"))
        key = asyncio.run(asking.ask(context, "Tailscale auth key:", nothing, hidden=True))
    assert menu == "n" and key is nothing
    assert errors.getvalue().splitlines() == [
        "aw: not asked, with no terminal to ask at: Use Tailscale on the VMs? - no",
        "aw: not asked, with no terminal to ask at: Tailscale auth key - nothing given",
    ]
    assert [(event.kind, event.machine, event.text) for event in heard] == [
        ("step", "alpha", "Use Tailscale on the VMs?: not asked, with no terminal: no"),
        ("step", None, "Tailscale auth key: not asked, with no terminal: nothing given"),
    ]


def test_no_question_is_begun_once_the_run_is_stopping(home, monkeypatch) -> None:
    with _job() as made, open(os.devnull) as given:
        monkeypatch.setattr(sys, "stdin", given)
        context = _Nobody(made)
        context.cancelled.set()
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(asking.ask(context, "Proxmox host address:", ""))


def _interrupted(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """termios.tcsetattr meeting one EINTR before each change it makes, as a signal whose handler raises nothing - the
    run's first Ctrl-C, a resize - gives it while TCSADRAIN waits for the output to drain: each change's `when`, as
    tried."""
    made = termios.tcsetattr
    tried: list[int] = []

    def interrupted(fd: int, when: int, settings: list) -> None:
        tried.append(when)
        if len(tried) % 2:
            raise termios.error(errno.EINTR, "Interrupted system call")
        made(fd, when, settings)

    monkeypatch.setattr(termios, "tcsetattr", interrupted)
    return tried


class _Typing(_Nobody):
    """A run's Context at a terminal of the test's own: what the test types reaches it once the question has asked,
    after the keys typed before are flushed."""

    def __init__(self, job: jobs.Job, master: int, keys: list[bytes]) -> None:
        super().__init__(job)
        self.master, self.keys = master, list(keys)

    async def typed(self, fd: int, most: int = 4096) -> bytes:
        if self.keys:
            os.write(self.master, self.keys.pop(0))
        return os.read(fd, most)


class _Slow(_Typing):
    """The same, each read waiting a while first, so that another task of the run's may ask meanwhile."""

    async def typed(self, fd: int, most: int = 4096) -> bytes:
        await asyncio.sleep(0.05)
        return await super().typed(fd, most)


def test_two_questions_asked_at_once_take_turns_at_the_terminal(home, monkeypatch) -> None:
    # Two machines' work, each asking at once: the second waits till the first has ended, never writing over it.
    master, slave = pty.openpty()
    given = os.fdopen(slave, "r")
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", given)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(os, "tcgetpgrp", lambda fd: os.getpgrp())
    heard: list[jobs.Event] = []

    async def both(context: _Typing) -> list[str | Secret]:
        return await asyncio.gather(
            asking.ask(context, "alpha's key:", Secret(""), hidden=True, machine="alpha"),
            asking.ask(context, "beta's key:", Secret(""), hidden=True, machine="beta"),
        )

    try:
        with _job(("alpha", "beta")) as made:
            made.listen(heard.append)
            answers = asyncio.run(both(_Slow(made, master, [b"first\n", b"second\n"])))
    finally:
        given.close()
        os.close(master)
    assert [answer.reveal() for answer in answers if isinstance(answer, Secret)] == ["first", "second"]
    assert out.getvalue() == "alpha's key: \nbeta's key: \n"
    waits = [(event.machine, event.text) for event in heard if event.kind == "waiting"]
    assert waits == [("alpha", "alpha's key"), ("alpha", ""), ("beta", "beta's key"), ("beta", "")], waits


def test_a_question_puts_the_terminal_back_through_a_signal_that_interrupts_the_change(home, monkeypatch) -> None:
    # A hidden answer turns the echo off; a signal meanwhile is no reason to leave it off, or to fail the question.
    master, slave = pty.openpty()
    given = os.fdopen(slave, "r")
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", given)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(os, "tcgetpgrp", lambda fd: os.getpgrp())
    try:
        found = termios.tcgetattr(slave)
        tried = _interrupted(monkeypatch)
        with _job() as made:
            context = _Typing(made, master, [HIDDEN.encode() + b"\n"])
            key = asyncio.run(asking.ask(context, "Tailscale auth key:", Secret(""), hidden=True))
        assert isinstance(key, Secret) and key.reveal() == HIDDEN
        assert termios.tcgetattr(slave) == found
        assert tried == [termios.TCSANOW, termios.TCSANOW, termios.TCSADRAIN, termios.TCSADRAIN], tried
        assert out.getvalue() == "Tailscale auth key: \n"
    finally:
        given.close()
        os.close(master)


# Keys as a read gives them, and the keys choose._keys reads of them one at a time.
CUT = [
    (b"\x1b[B\r", [b"\x1b[B", b"\r"], b""),
    (b"\x1bOB\x1bOA\r", [b"\x1bOB", b"\x1bOA", b"\r"], b""),
    (b"n2\r", [b"n", b"2", b"\r"], b""),
    (b"\x1b[1~x", [b"\x1b[1~", b"x"], b""),
    (b"\x1bx\x03", [b"\x1bx", b"\x03"], b""),
    (b"a\x1b", [b"a"], b"\x1b"),
    (b"\x1b[", [], b"\x1b["),
    (b"\x1b[12", [], b"\x1b[12"),
    (b"\x1bO", [], b"\x1bO"),
    (b"\x1bO1x", [b"\x1bO1", b"x"], b""),
]


@pytest.mark.parametrize(("typed", "keys", "left"), CUT)
def test_the_menus_keys_are_cut_from_a_read_as_choose_reads_them_one_at_a_time(typed, keys, left) -> None:
    assert choose.split(typed) == (keys, left)
    # choose._keys, given the same bytes on a pipe: each key whole, and a sequence nothing ended as it stands.
    read, write = os.pipe()
    try:
        os.write(write, typed)
        one_at_a_time = []
        while (key := choose._keys(read)) is not None:
            one_at_a_time.append(key)
    finally:
        os.close(read)
        os.close(write)
    assert one_at_a_time == keys + ([left] if left else [])


def _menu_with(reads: list[bytes | None], cols: list[tuple[int, int]] | None = None) -> tuple[object, list, bool]:
    """choose.asked at a terminal of the test's own, its keys the reads given in turn: what it gave back (or raised),
    the timeouts it read with, and whether the terminal was left as it was found."""
    master, slave = pty.openpty()
    given = list(reads)
    asked: list[float] = []

    async def read(seconds: float) -> bytes | None:
        asked.append(seconds)
        return given.pop(0) if given else b""

    class In:
        def fileno(self) -> int:
            return slave

    found = termios.tcgetattr(slave)
    try:
        out = io.StringIO()
        menu = Question("Use Tailscale on the VMs?", "n", YES_NO).menu()
        try:
            got: object = asyncio.run(choose.asked(menu, read, In(), out, color=False))
        except choose.Interrupted as error:
            got = error
        return got, asked, termios.tcgetattr(slave) == found
    finally:
        os.close(master)
        os.close(slave)


def test_the_menu_takes_its_keys_as_a_read_gives_them() -> None:
    # A sequence in parts, read again within SEQUENCE_SECONDS, moves as one key: down from yes is no.
    got, asked, kept = _menu_with([b"\x1b[A", b"\x1b[", b"B", b"\r"])
    assert (got, kept) == ("n", True)
    assert asked == [choose.IDLE_SECONDS, choose.IDLE_SECONDS, choose.SEQUENCE_SECONDS, choose.IDLE_SECONDS]
    # Esc that nothing ends in time is Esc alone: decided later; and the terminal's size looked at again meanwhile.
    got, asked, kept = _menu_with([None, b"\x1b", None])
    assert (got, kept) == ("", True) and asked == [choose.IDLE_SECONDS, choose.IDLE_SECONDS, choose.SEQUENCE_SECONDS]
    # The end of the input decides later too.
    assert _menu_with([b""])[0] == ""
    # Ctrl-C read as a byte among others: Interrupted, the terminal put back.
    got, _, kept = _menu_with([b"\x1b[B\x03"])
    assert isinstance(got, choose.Interrupted) and kept


def test_the_menu_puts_the_terminal_back_through_a_signal_that_interrupts_the_change(monkeypatch) -> None:
    tried = _interrupted(monkeypatch)
    got, _, kept = _menu_with([b"\x1b[A", b"\r"])
    assert (got, kept) == ("y", True)
    # Raw, then put back: each change tried again after its EINTR.
    assert tried == [termios.TCSANOW, termios.TCSANOW, termios.TCSADRAIN, termios.TCSADRAIN], tried


class Clock:
    """The view's clock, set by the test."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_question_keeps_what_the_view_says_until_it_is_answered_and_rings_only_after_a_quiet_while(
    home, monkeypatch
) -> None:
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    clock = Clock()
    bells: list[float] = []
    with _job() as made:
        view = live.Live(made, DEPLOY, stream, clock=clock)
        assert live.Live.of(made) is view and view.mode == live.LINES
        monkeypatch.setattr(view.region, "bell", lambda: bells.append(clock.now))
        made.emit("stage", "alpha", "sign-in")
        with view.aside():
            clock.now = 5.0
            made.emit("waiting", "alpha", "Proxmox host address")
            made.emit("report", "alpha", "alpha is up at 192.0.2.61")
            assert "alpha is up" not in stream.getvalue() and "Proxmox host address" not in stream.getvalue()
            made.emit("waiting", "alpha", "")
        # Said once the question is answered, in the order it came.
        said = stream.getvalue()
        assert said.index("Proxmox host address") < said.index("alpha is up at 192.0.2.61"), said
        clock.now = 12.0
        with view.aside():
            made.emit("waiting", "alpha", "Use Tailscale on the VMs?")
            made.emit("waiting", "alpha", "")
        clock.now = 30.0
        with view.aside():
            made.emit("waiting", "alpha", "Tailscale auth key")
            made.emit("waiting", "alpha", "")
        # A wait that is no question rings at once.
        clock.now = 31.0
        made.emit("waiting", "alpha", "Approve the sign-in in your browser")
        made.emit("waiting", "alpha", "")
        # Five seconds after the view began, seven after an answer (twelve after the start): no bell; eighteen after
        # one: the bell.
        assert bells == [30.0, 31.0]
        made.emit("end", None, "", outcome="succeeded", exit=0)
        assert live.Live.of(made) is view and view.ended


def test_what_a_question_kept_is_said_before_the_summary_where_the_runs_end_cuts_it_short(home, monkeypatch) -> None:
    # A second Ctrl-C ends the run where the question still waits: the run's end comes inside the question's aside.
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    with _job() as made:
        view = live.Live(made, DEPLOY, stream, clock=Clock())
        made.emit("stage", "alpha", "sign-in")
        with view.aside():
            made.emit("waiting", "alpha", "Proxmox host address")
            made.emit("report", "alpha", "alpha is up at 192.0.2.61")
            made.emit("end", None, "", outcome="cancelled", exit=130)
            # Said at the end itself: the question's aside may never end, the process ending first.
            said = stream.getvalue()
            assert view.ended
    assert "alpha is up at 192.0.2.61" in said and "STAGES" in said, said
    assert said.index("alpha is up at 192.0.2.61") < said.index("STAGES"), said
    assert stream.getvalue() == said


def test_ask_reads_the_terminal_only_through_the_runs_typed() -> None:
    tree = ast.parse(ASK.read_text())
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not imported & {"getpass", "tty", "select", "selectors", "readline"}, imported
    called = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert not called & {"os.read", "input", "open", "os.open", "sys.stdin.read", "sys.stdin.readline"}, called
    assert "context.typed" in called
