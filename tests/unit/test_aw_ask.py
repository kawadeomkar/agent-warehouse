"""Questions at the terminal (lib/claude_vm/runs/ask.py, runs/live.py's aside, tui/choose.py's asked;
docs/architecture/engine.md §16.8).

At a pseudo-terminal of the test's own (harness._at_a_pseudo_terminal), each run here is a child process made by
runs.run as a verb makes one, whose body draws deploy's live view at a sign-in and asks its questions through runs.ask,
every event it emits written down as the run's text log will hear it: a yes or no picked with the arrow keys, the line
it leaves saying the answer; a line typed, its echo on; a hidden answer whose characters reach neither the screen, the
run's record nor its events (aw_kit.searched_for), given back as a Secret; Enter taking the default; keys typed before a
question was there never its answer; two lines pasted at a line or a hidden answer, the second never left in the
terminal's input once the run has ended; two questions asked at once, from two tasks, the second drawn only once the
first is answered; a report, a line on standard error and an event the view refuses, said while a question is up, shown
after it, never on its line, and so where a Ctrl-C or a SIGTERM cuts the question short, its line ended where it was
cut, what was typed on it kept there (`192^C`, the keys and the Ctrl-C in one write too) and never left in the
terminal's input; a choice whose words and note hold escapes and newlines drawn as plain text, nothing of the menu left
behind; a choice typed, and asked once more, where the terminal cannot draw the menu; one Ctrl-C at a line, a hidden
answer and the menu ending the run, cancelled, with the echo back; the run's summary and long NEXT STEPS said with
nobody reading, then keys typed unseen and a Ctrl-C, every line on the screen and the keys never left for the shell;
keys typed while a stopped run winds down never left either; from an interactive shell, a hidden answer and a line half
typed and then a Ctrl-Z (fg bringing the question back in its mode, its words written again), a Ctrl-\\ or a Ctrl-C,
never on the shell's line or in its history; however a run ends, the terminal's settings those it had before the run;
the region gone from the screen while a question is up, the title saying the run waits for you, and a frame drawn again
only half a second after the answer, none between two questions; each question a `waiting` event, an empty one once it
is answered, and a `step` event that says what was answered. With standard input on /dev/null, the default at once and a
line that says the question was not asked.

In this process: a question without a default cannot be built (the rule with a test of its own), with every other
refusal of a Question; a choice's words and note made one line of plain text, its value given back as given; nobody to
ask - a pipe, /dev/null, a terminal whose foreground the run is not - gives the default and never reads; two questions
asked at once taking turns at the terminal; the menu's keys cut from a read as choose.ask reads them one at a time, and
the menu asked through a read of the test's own, a sequence in parts, Esc, Ctrl-C and the terminal's size looked at
again, what is typed after its answer never left in the terminal's input; a question and the menu putting the terminal's
settings back through a signal that interrupts the change (EINTR, made again by tui/live_region.py's set_terminal), each
change TCSAFLUSH's; a line's mode keeping at a Ctrl-C what the terminal has not read (NOFLSH), a hidden answer's not,
and the settings put back exactly as found; a question's own words written while its mode is up (a line cut short ended
before the change back, the menu's words once the terminal is raw); a signal landing as a question ends delivered once
its line is ended and the settings are back; a question a shell puts in the background making no change of the settings
there; what the view says while a question is up, the line of an event it refuses among it, kept until it is answered,
or said before the summary where the run's end cuts the question short, and the bell of a question's wait rung only
after a quiet while; and by a scan, that ask.py reads the terminal only through the run's Context.typed.
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
import select
import signal
import subprocess
import sys
import termios
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import harness
import pytest
from aw_kit import environment, searched_for, stand_ins
from harness import REPO
from vt import VT

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
# A choice whose words and note came from a machine: an OSC (the title), an erase of the screen, a colour, a newline.
NODES = [Choice("pve", "pve\x1b]0;owned\x07 node\x1b[2J\nsecond", "lacks\x1b[45m Sys.Audit\nthere"), Choice("nuc")]
NODE = ("Which node?", "", NODES, False)
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
    "stderr": [ADDRESS],
    "line-paste": [ADDRESS],
    "hidden-paste": [KEY],
    "node": [NODE],
    "held-ctrl-c": [ADDRESS],
    "held-term": [ADDRESS],
}
# Ways that say lines while their question is up: a report, standard error's lines, or all of them.
HOLDING = ("report", "stderr", "held-ctrl-c", "held-term")
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
    (folder / "pid").write_text(str(os.getpid()))
    out = Kept(sys.stdout)
    atexit.register(lambda: (folder / "kept.json").write_text(json.dumps(out.writes)))
    ctx.job.listen(heard)
    live.Live(ctx.job, DEPLOY, out, cancelled=ctx.cancelled)
    ctx.emit("stage", "alpha", "sign-in")
    ctx.emit("step", "alpha", "claude", stage="sign-in")
    await asyncio.sleep(0.4)
    if way == "last":
        # NEXT STEPS longer than a terminal's line discipline holds, said at the run's end with the test reading
        # nothing from just before they are said (ending, then quiet) till its Ctrl-C has landed (interrupted): the
        # region's release, the settings' last change, waits for that, once said tells the test they are all written.
        from claude_vm.tui import live_region

        released = live_region.LiveRegion.release

        def release(self):
            (folder / "said").write_text("")
            while not (folder / "interrupted").exists():
                time.sleep(0.02)
            released(self)

        live_region.LiveRegion.release = release
        left = "\n".join(f"left to do {number:03}: a line of NEXT STEPS at the run's end" for number in range(1, 151))
        ctx.emit("next", "alpha", left, matter="init_settings")
        ctx.emit("step", "alpha", "claude", stage="sign-in", outcome="done")
        (folder / "ending").write_text("")
        while not (folder / "quiet").exists():
            await asyncio.sleep(0.02)
        return 0
    if way == "stop-typed":
        # Stopped by a Ctrl-C at work, the run winds down a while, and keys are typed meanwhile.
        await ctx.cancelled.wait()
        await asyncio.sleep(0.8)
        return 130
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
        if way in HOLDING:
            # Once the question is up, the run says a line on standard error, as the ladder or a warning would, and an
            # event the live view refuses (a stage deploy has not); and a report, as another machine's work would.
            up = asyncio.Event()
            ctx.job.listen(lambda event: event.kind == "waiting" and event.text and up.set())

            async def report():
                await up.wait()
                if way != "report":
                    print("aw: a line on standard error while a question is up", file=sys.stderr, flush=True)
                    ctx.emit("step", "alpha", "nope", stage="no-such-stage")
                if way != "stderr":
                    ctx.emit("report", "alpha", "alpha is up at 192.0.2.61")
                (folder / "reported").write_text("")

            reporting = asyncio.ensure_future(report())
        await asked(*question)
        if way in HOLDING:
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
# terminal echoes, how the run ended, how many bytes the run left in the terminal's input for whatever reads it next
# (the shell, after a run), and whether the terminal's settings are those it had before the run, as one line of JSON.
# The bytes left are counted as a shell's line editor reads them, with the line off: in the terminal's line mode
# FIONREAD counts whole lines alone, and a part of one (`192`, kept by a Ctrl-C) would go uncounted. A Ctrl-C typed
# reaches both: this one takes it with a handler that does nothing, never SIG_IGN, which the run would inherit (as
# tests/unit/test_aw_attached.py's).
WATCHES = r"""
import fcntl, json, signal, struct, subprocess, sys, termios
signal.signal(signal.SIGINT, lambda *_: None)
before = termios.tcgetattr(0)
run = subprocess.Popen(sys.argv[1:])
status = run.wait()
after = termios.tcgetattr(0)
raw = [*after[:3], after[3] & ~termios.ICANON, *after[4:]]
termios.tcsetattr(0, termios.TCSANOW, raw)
left = struct.unpack("i", fcntl.ioctl(0, termios.FIONREAD, struct.pack("i", 0)))[0]
termios.tcsetattr(0, termios.TCSANOW, after)
echo = bool(after[3] & termios.ECHO)
print(json.dumps({"echo": echo, "status": status, "left": left, "same": after == before}), flush=True)
"""
WATCHED = re.compile(rb'\{"echo": (true|false), "status": (-?\d+), "left": (\d+), "same": (true|false)\}')
# A key of a way's plan that is no key: SIGTERM sent to the run, by the pid it wrote down.
TERM = b"<SIGTERM>"
CHILD = [sys.executable, "-I", "-X", "utf8", "-c", RUN]
HINT = choose.HINT.encode()


class Ran:
    """A run at a pseudo-terminal: its output, how it ended and the terminal's echo once it had, the bytes it left in
    the terminal's input, its job's record, the events it emitted, its answers, and what its live view wrote, each with
    when."""

    def __init__(self, folder: Path, raw: bytes, watched: dict, left: int) -> None:
        self.raw, self.watched, self.left = raw, watched, left
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


def _at_a_terminal(
    home: Path,
    folder: Path,
    way: str,
    plan: Plan,
    *,
    term: str = "xterm-256color",
    then: Callable[[bytes, int], None] | None = None,
) -> Ran:
    """The run of way at a pseudo-terminal, typed at by its plan; then(output, master), where given, called beside it
    after each read and idle tick."""
    jobs_dir = paths.jobs_dir(paths.home(REPO))
    argv = [sys.executable, "-I", "-c", WATCHES, *CHILD, str(LIB), str(jobs_dir), str(folder), way]
    temporary = folder / "tmp"
    temporary.mkdir(exist_ok=True)
    env = environment(home, {"TERM": term, "PYTHONUTF8": "1", "TMPDIR": str(temporary)})
    left = list(plan)
    state = {"at": 0, "ahead": False}

    def act(chunk: bytes, output: bytes, master: int, process: subprocess.Popen) -> None:
        if then is not None:
            then(output, master)
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
        if way in ("report", "stderr", "held-ctrl-c", "held-term") and not (folder / "reported").is_file():
            # The answer typed only once the run has said its report, or its lines, while the question is up.
            return
        left.pop(0)
        state["at"] = found + len(wanted)
        for key in keys:
            if key == TERM:
                os.kill(int((folder / "pid").read_text()), signal.SIGTERM)
            else:
                os.write(master, key)
            time.sleep(0.15)

    rc, raw = harness._at_a_pseudo_terminal(argv, folder, env, size=(100, 30), act=act, timeout=90, poll=0.05)
    harness._fail_if_the_desktop_was_reached(stand_ins(home))
    found = WATCHED.search(raw)
    assert rc == 0 and found is not None, raw.decode(errors="replace")
    assert not left, f"no {left[0][0]!r} appeared:\n" + raw.decode(errors="replace")
    # However the run ended, the terminal's settings are those it had before it: its echo, its line, and NOFLSH.
    assert found.group(4) == b"true", raw.decode(errors="replace")
    return Ran(folder, raw, {"echo": found.group(1) == b"true", "status": int(found.group(2))}, int(found.group(3)))


def _done(ran: Ran) -> None:
    assert ran.watched == {"echo": True, "status": 0}, ran.text
    assert ran.record["outcome"] == "succeeded", ran.record


def _stopped(ran: Ran) -> None:
    # One Ctrl-C at the question ends the run there, cancelled, the terminal's echo back.
    assert ran.watched == {"echo": True, "status": 130}, ran.text
    assert (ran.record["outcome"], ran.record["exit"], ran.record["why"]) == ("cancelled", 130, "Ctrl-C"), ran.record
    assert "aw: stopping" in ran.text and "^Caw:" not in ran.text, ran.text
    assert ran.answers is None, ran.answers
    # What was typed before the Ctrl-C, kept by the terminal at the interrupt (NOFLSH), is discarded with the question:
    # never left for the shell.
    assert ran.left == 0, ran.left


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


def test_standard_errors_lines_while_a_question_is_up_are_shown_after_it_and_not_on_its_line(home, tmp_path) -> None:
    # A line on standard error (the ladder's, a warning) and the line of an event the view refuses, both said while the
    # question is up: kept with the view's lines, and said after the question's line, in the order they came.
    ran = _at_a_terminal(home, tmp_path, "stderr", [(b"Proxmox host address: ", [b"192.0.2.10\n"], False)])
    _done(ran)
    [(begun, ended)] = ran.asked()
    [refused] = [event for event in ran.events if event["kind"] == "step" and event["text"] == "nope"]
    assert begun < refused["at"] < ended, (begun, refused["at"], ended)
    raw = ran.raw
    answered = re.search(rb"Proxmox host address: 192\.0\.2\.10\r\n", raw)
    assert answered is not None, ran.text
    line = raw.find(b"aw: a line on standard error while a question is up\r\n")
    left_out = raw.find(b"aw: the live view left out an event it could not take (step alpha: nope)")
    assert answered.end() < line < left_out, ran.text


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
    ("way", "prompt", "kept"),
    [
        ("hidden-paste", b"Tailscale auth key: ", {"secret": hashlib.sha256(b"first-line").hexdigest()}),
        ("line-paste", b"Proxmox host address: ", {"value": "first-line"}),
    ],
    ids=["a hidden answer", "a line"],
)
def test_what_is_pasted_after_the_answers_line_is_never_left_for_the_shell(way, prompt, kept, home, tmp_path) -> None:
    # Two lines pasted at once: the first is the answer, and the second is discarded with the question, never left in
    # the terminal's input for whatever reads it once the run has ended - the shell, which would run it.
    ran = _at_a_terminal(home, tmp_path, way, [(prompt, [b"first-line\nsecond-line\n"], False)])
    _done(ran)
    assert ran.answers is not None and [{key: answer[key] for key in kept} for answer in ran.answers] == [kept]
    assert ran.left == 0, ran.left
    if way == "hidden-paste":
        assert "first-line" not in ran.text and "second-line" not in ran.text, ran.text


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


def test_a_ctrl_c_typed_with_what_was_typed_keeps_it_on_the_questions_line(home, tmp_path) -> None:
    # "192" and the Ctrl-C in one write: the echo of 192 is still the terminal's own, unread, when the interrupt lands,
    # and without NOFLSH Linux throws it away (as macOS throws away every echo the screen has not read yet), the line
    # left saying ^C alone. With it the line says what was typed, and the change back discards the 192 kept unread.
    ran = _at_a_terminal(home, tmp_path, "line-ctrl-c", [(b"Proxmox host address: ", [b"192\x03"], False)])
    _stopped(ran)
    screen = VT(100, 30)
    screen.feed(ran.raw)
    lines = screen.transcript()
    asked = [line for line in lines if line.startswith("Proxmox host address:")]
    assert asked == ["Proxmox host address: 192^C"], lines


def _first(lines: list[str], start: str) -> int:
    """Where the first of lines that starts with start is, its indent aside; -1 where none does."""
    return next((at for at, line in enumerate(lines) if line.lstrip().startswith(start)), -1)


@pytest.mark.parametrize(
    ("way", "end", "status", "record", "question"),
    [
        ("held-ctrl-c", b"\x03", 130, ("cancelled", 130, "Ctrl-C"), "Proxmox host address: 192^C"),
        ("held-term", TERM, -signal.SIGTERM, ("cancelled", 143, "SIGTERM"), "Proxmox host address: 192"),
    ],
    ids=["a Ctrl-C", "a SIGTERM"],
)
def test_a_question_cut_short_ends_its_line_before_what_was_kept_is_said(
    way, end, status, record, question, home, tmp_path
) -> None:
    # A line on standard error, an event the view refuses and a report, said while a question is up; then, part of an
    # answer typed, the run is stopped there: the question's line ends where it was cut, and each line kept, the
    # ladder's own after them, is said on a line of its own (the screen read back with tests/unit/vt.py).
    ran = _at_a_terminal(home, tmp_path, way, [(b"Proxmox host address: ", [b"192", end], False)])
    assert ran.watched == {"echo": True, "status": status}, ran.text
    assert (ran.record["outcome"], ran.record["exit"], ran.record["why"]) == record, ran.record
    assert ran.answers is None, ran.answers
    screen = VT(100, 30)
    screen.feed(ran.raw)
    lines = screen.transcript()
    asked = [at for at, line in enumerate(lines) if line.startswith("Proxmox host address:")]
    assert [lines[at] for at in asked] == [question], lines
    said = lines[asked[0] + 1 :]
    kept = [
        _first(said, "aw: a line on standard error while a question is up"),
        _first(said, "aw: the live view left out an event it could not take (step alpha: nope)"),
        _first(said, "alpha is up at 192.0.2.61"),
    ]
    assert -1 not in kept and kept == sorted(kept), said
    assert said[kept[0]] == "aw: a line on standard error while a question is up", said
    if way == "held-ctrl-c":
        assert _first(said, "aw: stopping") > kept[-1], said
    # What was typed, kept unread at the interrupt or not, is discarded with the question: never left for the shell.
    assert ran.left == 0, ran.left


# Each line of NEXT STEPS the "last" way's run says at its end.
LEFT_TO_DO = re.compile(rb"left to do (\d{3}): a line of NEXT STEPS at the run's end")


def test_a_ctrl_c_while_the_runs_last_words_are_unread_loses_none_and_leaves_nothing_typed(home, tmp_path) -> None:
    # The run's summary and 150 lines of NEXT STEPS said with nobody reading the terminal, then keys typed unseen and a
    # Ctrl-C, before the terminal has read any of it: at an interrupt a terminal throws away what it holds unread unless
    # NOFLSH, which the run keeps on till its last words are said - on Linux what it holds past its line discipline's
    # buffer, on macOS all it holds. Every line is on the screen; the keys typed are thrown away at the Ctrl-C, as the
    # terminal's own Ctrl-C would, never left for the shell; the settings are those found.
    typed: list[int] = []

    def last(output: bytes, master: int) -> None:
        if typed or not (tmp_path / "ending").is_file():
            return
        (tmp_path / "quiet").write_text("")
        waited = time.monotonic() + 10
        while not (tmp_path / "said").is_file() and time.monotonic() < waited:
            time.sleep(0.02)
        typed.append(os.write(master, b"typed-blind\n"))
        time.sleep(0.1)
        typed.append(os.write(master, b"\x03"))
        time.sleep(0.3)
        (tmp_path / "interrupted").write_text("")

    ran = _at_a_terminal(home, tmp_path, "last", [], then=last)
    assert typed and (tmp_path / "said").is_file(), ran.text
    _done(ran)
    said = [int(number) for number in LEFT_TO_DO.findall(ran.raw)]
    lost = sorted(set(range(1, 151)) - set(said))
    assert said == list(range(1, 151)), f"{len(lost)} lost, the first {lost[:5]}"
    assert ran.left == 0, ran.left


def test_keys_typed_while_a_stopped_run_winds_down_are_never_left_for_the_shell(home, tmp_path) -> None:
    # A Ctrl-C at work, then a line typed while the run winds down, the echo off: the settings put back at its end
    # throw away what was typed and not read.
    plan = [(b"sign in", [b"\x03"], False), (b"aw: stopping", [b"typed-later\n"], False)]
    ran = _at_a_terminal(home, tmp_path, "stop-typed", plan)
    _stopped(ran)


# What a person types at a question before a Ctrl-Z, a Ctrl-\ and a Ctrl-C, by the run's folder: a word of its own each.
HALF = {"stop": b"hush42x", "quit": b"quit42x", "interrupt": b"int42x"}
# The answer typed once fg has brought the question back.
AFRESH = b"fresh42x"
# What the shell says of each run: `echo "stopped=$?"` once one stops, `echo "ended=$?"` once one ends.
STATUSES = re.compile(rb"\b(stopped|ended)=(\d+)")


@pytest.mark.parametrize("hidden", [True, False], ids=["a hidden answer", "a line"])
def test_a_half_typed_answer_reaches_neither_the_shells_line_nor_its_history_at_ctrl_z_ctrl_backslash_or_ctrl_c(
    hidden, home, tmp_path
) -> None:
    # From an interactive shell, three runs each ask the question and the person types part of an answer: then a
    # Ctrl-Z, the shell's line run (an `echo` that what was left there would begin), and fg, which brings the question
    # back in its mode with its words written again, where the answer is typed afresh; a Ctrl-\, which ends the run as
    # a SIGTERM does, dying of SIGQUIT; a Ctrl-C, as C34 has it. What was typed before each key reaches neither the
    # shell's line, where the shell would run it, nor its history (review 59's H2: a secret in $HISTFILE after Ctrl-Z).
    asked = b"Tailscale auth key: " if hidden else b"Proxmox host address: "
    way = "hidden" if hidden else "line"
    jobs_dir = paths.jobs_dir(paths.home(REPO))
    (tmp_path / "run.py").write_text(RUN)
    for folder in HALF:
        (tmp_path / folder).mkdir()
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    given = {"TERM": "xterm-256color", "PYTHONUTF8": "1", "TMPDIR": str(temporary)}
    env = environment(home, {**given, "PY": sys.executable, "LIB": str(LIB), "JOBS": str(jobs_dir)})
    mode: list[int] = []

    def run(folder: str) -> bytes:
        return f'"$PY" -I -X utf8 run.py "$LIB" "$JOBS" {folder} {way}\n'.encode()

    def back_in_its_mode(master: int, output: bytes) -> None:
        mode.append(termios.tcgetattr(master)[3])
        os.write(master, AFRESH + b"\n")

    prompt = harness.SHELL_PROMPT
    ended = b'echo "ended=$?"\n'
    steps = [
        (prompt, b"ulimit -c 0\n"),
        (prompt, run("stop")),
        (asked, [HALF["stop"], b"\x1a"]),
        (b"Stopped", []),
        (prompt, b'echo "stopped=$?"\n'),
        (prompt, b"fg\n"),
        (asked, back_in_its_mode),
        (prompt, ended),
        (prompt, run("quit")),
        (asked, [HALF["quit"], b"\x1c"]),
        (prompt, ended),
        (prompt, run("interrupt")),
        (asked, [HALF["interrupt"], b"\x03"]),
        (prompt, ended),
        (prompt, b"exit\n"),
    ]
    output, history = harness._in_a_shell(tmp_path, env, steps)
    harness._fail_if_the_desktop_was_reached(stand_ins(home))
    said = output.decode(errors="replace")
    assert b"command not found" not in output, said
    for word in [*HALF.values(), AFRESH]:
        assert word not in history, history
    statuses = [(name.decode(), int(number)) for name, number in STATUSES.findall(output)]
    stop, quit = 128 + signal.SIGTSTP, 128 + signal.SIGQUIT
    assert statuses == [("stopped", stop), ("ended", 0), ("ended", quit), ("ended", 130)], said
    # Back in the question's mode once fg brought it back: its line and its signal keys, the echo and NOFLSH for a
    # line, neither for a hidden answer.
    (lflag,) = mode
    assert lflag & termios.ICANON and lflag & termios.ISIG, lflag
    assert bool(lflag & termios.ECHO) is not hidden and bool(lflag & termios.NOFLSH) is not hidden, lflag
    (answer,) = json.loads((tmp_path / "stop" / "answers.json").read_text())
    if hidden:
        assert answer["secret"] == hashlib.sha256(AFRESH).hexdigest(), answer
    else:
        assert answer["value"] == AFRESH.decode(), answer
    records = [job.read() for job in jobs.listed(jobs_dir)]
    ends = sorted((record["outcome"], record["exit"], record["why"]) for record in records)
    assert ends == [("cancelled", 130, "Ctrl-C"), ("cancelled", quit, "SIGQUIT"), ("succeeded", 0, "done")], ends


# How each run a shell stops at a question is then ended from the shell, by the run's folder: SIGTERM, then SIGHUP.
KILLS = {"term": b"kill", "hup": b"kill -HUP"}


@pytest.mark.parametrize("hidden", [True, False], ids=["a hidden answer", "a line"])
def test_a_question_stopped_and_then_killed_from_the_shell_ends_at_once_and_frees_its_lock(
    hidden, home, tmp_path
) -> None:
    # From an interactive shell, a run asks the question, a Ctrl-Z stops it, and the shell kills it (`kill %1`, then
    # `kill -HUP %1` for a second run): bash sends the signal and then SIGCONT, which in the question's hold would set
    # its mode again from the background and stop the run there (SIGTTOU) for good, its machine's lock held (review
    # 68's F1). Each run is gone within five seconds, dead of the signal (the shell's Terminated and Hangup, the
    # records' 143 and 129), and a third run of the same machine asks and ends as it should: the lock is free.
    asked = b"Tailscale auth key: " if hidden else b"Proxmox host address: "
    way = "hidden" if hidden else "line"
    jobs_dir = paths.jobs_dir(paths.home(REPO))
    (tmp_path / "run.py").write_text(RUN)
    for folder in [*KILLS, "after"]:
        (tmp_path / folder).mkdir()
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    given = {"TERM": "xterm-256color", "PYTHONUTF8": "1", "TMPDIR": str(temporary)}
    env = environment(home, {**given, "PY": sys.executable, "LIB": str(LIB), "JOBS": str(jobs_dir)})

    def run(folder: str) -> bytes:
        return f'"$PY" -I -X utf8 run.py "$LIB" "$JOBS" {folder} {way}\n'.encode()

    def killed(folder: str) -> bytes:
        # The signal, then up to five seconds for the run to be gone, said in words its line, which the shell echoes,
        # does not hold; one still there is killed. How it ended is its record's: the shell has reaped the job by then.
        alive = f'kill -0 "$(cat {folder}/pid)" 2>/dev/null'
        return (
            f"{KILLS[folder].decode()} %1; for i in $(seq 100); do {alive} || break; sleep 0.05; done; "
            f'if {alive}; then echo "stuck=$((1))"; kill -9 %1; else echo "gone=$((1))"; fi\n'
        ).encode()

    prompt = harness.SHELL_PROMPT
    steps = [(prompt, b"ulimit -c 0\n")]
    for folder in KILLS:
        steps += [(prompt, run(folder)), (asked, [HALF["stop"], b"\x1a"]), (b"Stopped", []), (prompt, killed(folder))]
    steps += [
        (prompt, run("after")),
        (asked, AFRESH + b"\n"),
        (prompt, b'echo "ended=$?"\n'),
        (prompt, b"exit\n"),
    ]
    output, history = harness._in_a_shell(tmp_path, env, steps)
    harness._fail_if_the_desktop_was_reached(stand_ins(home))
    said = output.decode(errors="replace")
    assert b"stuck=1" not in output and output.count(b"gone=1") == 2, said
    assert b"Terminated" in output and b"Hangup" in output, said
    assert b"command not found" not in output and HALF["stop"] not in history, said
    statuses = [(name.decode(), int(number)) for name, number in STATUSES.findall(output)]
    term, hup = 128 + signal.SIGTERM, 128 + signal.SIGHUP
    assert statuses == [("ended", 0)], said
    records = [job.read() for job in jobs.listed(jobs_dir)]
    ends = sorted((record["outcome"], record["exit"], record["why"]) for record in records)
    assert ends == [("cancelled", hup, "SIGHUP"), ("cancelled", term, "SIGTERM"), ("succeeded", 0, "done")], ends


# What a person types unseen under the region while the run's last words are said, before a Ctrl-Z.
BLIND = b"blind42x"


def test_keys_typed_unseen_and_a_ctrl_z_at_the_runs_last_words_reach_neither_the_shells_line_nor_its_history(
    home, tmp_path
) -> None:
    # From an interactive shell, the "last" way: keys typed unseen under the region while the summary and NEXT STEPS
    # are said, NOFLSH on, and then a Ctrl-Z, which the hold of the run's last words holds off till the settings are
    # back as found and the terminal's hold is given back, and then raises at its default. What was typed is thrown
    # away before that stop (review 68's F2), as the hold's own Ctrl-Z throws it away; fg ends the run as it should.
    folder = tmp_path / "last"
    folder.mkdir()
    jobs_dir = paths.jobs_dir(paths.home(REPO))
    (tmp_path / "run.py").write_text(RUN)
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    given = {"TERM": "xterm-256color", "PYTHONUTF8": "1", "TMPDIR": str(temporary)}
    env = environment(home, {**given, "PY": sys.executable, "LIB": str(LIB), "JOBS": str(jobs_dir)})

    def stopped_at_the_last_words(master: int, output: bytes) -> None:
        waited = time.monotonic() + 20
        while not (folder / "ending").is_file() and time.monotonic() < waited:
            time.sleep(0.02)
        (folder / "quiet").write_text("")
        while not (folder / "said").is_file() and time.monotonic() < waited:
            time.sleep(0.02)
        os.write(master, BLIND + b"\n")
        time.sleep(0.1)
        os.write(master, b"\x1a")
        time.sleep(0.3)
        (folder / "interrupted").write_text("")

    prompt = harness.SHELL_PROMPT
    steps = [
        (prompt, b'"$PY" -I -X utf8 run.py "$LIB" "$JOBS" last last\n'),
        (b"sign in", stopped_at_the_last_words),
        (b"Stopped", []),
        (prompt, b'echo "stopped=$?"\n'),
        (prompt, b"fg\n"),
        (prompt, b'echo "ended=$?"\n'),
        (prompt, b"exit\n"),
    ]
    output, history = harness._in_a_shell(tmp_path, env, steps)
    said = output.decode(errors="replace")
    assert (folder / "said").is_file(), said
    assert b"command not found" not in output and BLIND not in history, said
    statuses = [(name.decode(), int(number)) for name, number in STATUSES.findall(output)]
    assert statuses == [("stopped", 128 + signal.SIGTSTP), ("ended", 0)], said
    (record,) = [job.read() for job in jobs.listed(jobs_dir)]
    assert (record["outcome"], record["exit"]) == ("succeeded", 0), record


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


def test_a_choices_words_and_note_from_a_machine_reach_the_screen_as_plain_text(home, tmp_path) -> None:
    # Its words hold an OSC (the title), an erase of the screen and a newline, its note a colour and a newline: the
    # menu draws them plain, on one line each, moves over them and leaves its one line with nothing of itself behind.
    ran = _at_a_terminal(home, tmp_path, "node", [(HINT, [b"\x1b[B", b"\x1b[A", b"\r"], True)])
    _done(ran)
    assert ran.answers is not None and [answer["value"] for answer in ran.answers] == ["pve"], ran.answers
    screen = VT(100, 30)
    screen.feed(ran.raw)
    assert screen.unknown == set() and not [title for title in screen.titles if "owned" in title], screen.titles
    assert b"\x1b[45m" not in ran.raw and "lacks Sys.Audit there" in ran.text, ran.text
    lines = screen.transcript()
    asked = lines.index("Which node?")
    assert lines[asked + 1] == "Which node? pve node second", lines
    assert not [line for line in lines if choose.HINT in line or line.strip() in ("second", "there")], lines
    assert "Which node?: pve node second" in [event["text"] for event in ran.of("step")]


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


def test_a_choices_words_and_note_are_one_line_of_plain_text_and_its_value_is_given_back_as_given() -> None:
    # Words and a note from a machine, an OSC, an erase of the screen, a colour and a newline among them: what the
    # menu, the line it leaves, the typed choice and the step event say is plain, on one line.
    node = Choice("pve", "pve\x1b]0;owned\x07 node\x1b[2J\nsecond", "lacks\x1b[45m Sys.Audit\nthere")
    assert (node.value, node.text, node.note) == ("pve", "pve node second", "lacks Sys.Audit there")
    # Words that are nothing but escapes are the value's, made plain; the value itself is given back as it came.
    named = Choice("nuc\n", "\x1b[2J")
    assert (named.value, named.text, named.note) == ("nuc\n", "nuc", "")
    question = Question("Which node?", "", (node, named))
    menu = question.menu()
    assert [(each["text"], each["note"]) for each in menu["options"]] == [
        ("pve node second", "lacks Sys.Audit there"),
        ("nuc", ""),
    ]
    assert choose.collapsed(menu, "pve") == "Which node? pve node second"
    assert (question.said("pve"), question.picked("pve node second"), question.picked("nuc")) == (
        "pve node second",
        "pve",
        "nuc\n",
    )


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
    run's first Ctrl-C, a resize - gives it while TCSAFLUSH waits for the output to drain: each change's `when`, as
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
        assert tried == [termios.TCSAFLUSH] * 4, tried
        assert out.getvalue() == "Tailscale auth key: \n"
    finally:
        given.close()
        os.close(master)


@pytest.mark.parametrize("hidden", [False, True], ids=["a line", "a hidden answer"])
def test_a_questions_mode_keeps_what_the_terminal_has_not_read_at_a_ctrl_c_but_a_hidden_answers_and_goes_back(
    hidden, home, monkeypatch
) -> None:
    # The question's mode: its line and its signal keys, for a line the echo on and NOFLSH, for a hidden answer neither
    # (its echo off, there is nothing on the screen to keep, and its keys throw away what was typed themselves: review
    # 59's H2), nothing else of the terminal's changed; then the settings exactly as found, NOFLSH as it was, each
    # change TCSAFLUSH's.
    master, slave = pty.openpty()
    given = os.fdopen(slave, "r")
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", given)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(os, "tcgetpgrp", lambda fd: os.getpgrp())
    change = termios.tcsetattr
    changes: list[tuple[int, list]] = []

    def recorded(fd: int, when: int, settings: list) -> None:
        changes.append((when, [*settings[:6], list(settings[6])]))
        change(fd, when, settings)

    try:
        found = termios.tcgetattr(slave)
        assert not found[3] & termios.NOFLSH, found
        monkeypatch.setattr(termios, "tcsetattr", recorded)
        with _job() as made:
            context = _Typing(made, master, [b"192.0.2.1\n"])
            default = Secret("") if hidden else ""
            # The change back is a draining one with the echo still unread here: macOS waits for that read
            # (ttywait), so the master is read on a thread of its own while the question runs.
            reading = threading.Event()

            def echoes() -> None:
                while not reading.is_set():
                    if select.select([master], [], [], 0.05)[0]:
                        try:
                            os.read(master, 4096)
                        except OSError:
                            return

            reader = threading.Thread(target=echoes, daemon=True)
            reader.start()
            try:
                answer = asyncio.run(asking.ask(context, "Proxmox host address:", default, hidden=hidden))
            finally:
                reading.set()
                reader.join()
        assert (answer.reveal() if isinstance(answer, Secret) else answer) == "192.0.2.1"
        assert [when for when, _ in changes] == [termios.TCSAFLUSH] * 2, changes
        (_, mode), (_, back) = changes
        wanted = termios.ICANON | termios.ISIG
        assert mode[3] & wanted == wanted, mode
        assert bool(mode[3] & termios.ECHO) is not hidden and bool(mode[3] & termios.NOFLSH) is not hidden, mode
        assert mode[:3] + mode[4:] == found[:3] + found[4:]
        assert back == found and termios.tcgetattr(slave) == found
    finally:
        given.close()
        os.close(master)


class _Told(io.StringIO):
    """A question's standard output, each write noted in turn beside the terminal's changes."""

    def __init__(self, noted: list[tuple[str, object]]) -> None:
        super().__init__()
        self.noted = noted

    def write(self, text: str) -> int:
        self.noted.append(("write", text))
        return super().write(text)


def _noting(monkeypatch: pytest.MonkeyPatch, noted: list[tuple[str, object]]) -> None:
    """termios.tcsetattr noting each change, its settings' local modes, in the same list as _Told's writes."""
    made = termios.tcsetattr

    def noting(fd: int, when: int, settings: list) -> None:
        noted.append(("change", settings[3]))
        made(fd, when, settings)

    monkeypatch.setattr(termios, "tcsetattr", noting)


@pytest.mark.parametrize("asked", ["a line the input's end cuts short", "the menu"])
def test_a_questions_own_words_are_written_while_its_mode_keeps_them_at_a_ctrl_c(asked, home, monkeypatch) -> None:
    # What a question writes is written while its own mode is up: the line a line's read cut short leaves ended before
    # the settings go back, where NOFLSH keeps it at a Ctrl-C; the menu's words once the terminal is raw, where a
    # Ctrl-C is a key. Written before or after, a Ctrl-C the terminal has not read them by would throw them away.
    master, slave = pty.openpty()
    given = os.fdopen(slave, "r")
    noted: list[tuple[str, object]] = []
    out = _Told(noted)
    monkeypatch.setattr(sys, "stdin", given)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(os, "tcgetpgrp", lambda fd: os.getpgrp())
    try:
        found = termios.tcgetattr(slave)
        _noting(monkeypatch, noted)
        with _job() as made:
            if asked == "the menu":
                built = Question("Use Tailscale on the VMs?", "n", YES_NO)
                answer = asyncio.run(asking._menu(_Typing(made, master, [b"\r"]), built, slave, out))
                assert answer == "n"
                changes = [at for at, (kind, _) in enumerate(noted) if kind == "change"]
                words = next(at for at, (kind, text) in enumerate(noted) if "Use Tailscale on the VMs?" in str(text))
                assert changes[0] < words < changes[1], noted
                assert not noted[changes[0]][1] & termios.ICANON, noted
            else:
                # The end of the input (Ctrl-D) before any Enter: the read ends with the line unended on the screen.
                context = _Typing(made, master, [b"\x04"])
                assert asyncio.run(asking.ask(context, "Proxmox host address:", "")) == ""
                assert noted[-2:] == [("write", "\n"), ("change", found[3])], noted
                made_mode, _ = (text for kind, text in noted if kind == "change")
                assert made_mode & termios.NOFLSH, noted
        assert termios.tcgetattr(slave) == found
    finally:
        given.close()
        os.close(master)


def test_a_signal_that_lands_as_a_question_ends_is_delivered_once_its_line_is_ended_and_the_settings_are_back(
    home, monkeypatch
) -> None:
    # A hidden answer given, its line ended and the settings put back with the run's signals held off: a SIGTERM
    # whose handler raises (the ladder's), landing as the line is ended, is delivered once both are done.
    master, slave = pty.openpty()
    given = os.fdopen(slave, "r")

    class Landing(io.StringIO):
        def write(self, text: str) -> int:
            if text == "\n":
                signal.raise_signal(signal.SIGTERM)
            return super().write(text)

    def ends(signum: int, frame: object) -> None:
        raise SystemExit(128 + signum)

    out = Landing()
    monkeypatch.setattr(sys, "stdin", given)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(os, "tcgetpgrp", lambda fd: os.getpgrp())
    previous = signal.signal(signal.SIGTERM, ends)
    interrupt = signal.getsignal(signal.SIGINT)
    try:
        found = termios.tcgetattr(slave)
        with _job() as made, pytest.raises(SystemExit):
            context = _Typing(made, master, [HIDDEN.encode() + b"\n"])
            asyncio.run(asking.ask(context, "Tailscale auth key:", Secret(""), hidden=True))
        assert out.getvalue() == "Tailscale auth key: \n"
        assert termios.tcgetattr(slave) == found
        # The Ctrl-C's handler the question found, in place again: the hold of the terminal took it while it was up.
        assert signal.getsignal(signal.SIGINT) is interrupt
    finally:
        signal.signal(signal.SIGTERM, previous)
        given.close()
        os.close(master)


class _Sent(_Typing):
    """A run a shell puts in the background (Ctrl-Z, then bg) once its answer is typed: tcgetpgrp says another group
    has the terminal from then on."""

    def __init__(self, job: jobs.Job, master: int, keys: list[bytes], group: dict[str, int]) -> None:
        super().__init__(job, master, keys)
        self.group = group

    async def typed(self, fd: int, most: int = 4096) -> bytes:
        got = await super().typed(fd, most)
        self.group["of"] = os.getpgrp() + 1
        return got


@pytest.mark.parametrize("asked", ["a line", "the menu"])
def test_a_question_a_shell_puts_in_the_background_leaves_the_terminals_settings_to_the_shell(
    asked, home, monkeypatch
) -> None:
    # The question's mode made at once, the run the terminal's foreground; then a shell puts the run in the background,
    # where the settings are the shell's and a change of them would stop the run (SIGTTOU): none is made.
    master, slave = pty.openpty()
    given = os.fdopen(slave, "r")
    noted: list[tuple[str, object]] = []
    group = {"of": os.getpgrp()}
    monkeypatch.setattr(sys, "stdin", given)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(os, "tcgetpgrp", lambda fd: group["of"])
    made_here = termios.tcsetattr
    try:
        found = termios.tcgetattr(slave)
        _noting(monkeypatch, noted)
        with _job() as made:
            if asked == "the menu":
                built = Question("Use Tailscale on the VMs?", "n", YES_NO)
                context = _Sent(made, master, [b"\r"], group)
                assert asyncio.run(asking._menu(context, built, slave, io.StringIO())) == "n"
            else:
                context = _Sent(made, master, [b"192.0.2.1\n"], group)
                assert asyncio.run(asking.ask(context, "Proxmox host address:", "")) == "192.0.2.1"
        changes = [settings for kind, settings in noted if kind == "change"]
        assert len(changes) == 1 and changes[0] != found[3], noted
        assert termios.tcgetattr(slave) != found
    finally:
        made_here(slave, termios.TCSANOW, found)
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
    # Raw, then put back, each change TCSAFLUSH's (the keys typed before and after flushed with it): each tried again
    # after its EINTR.
    assert tried == [termios.TCSAFLUSH] * 4, tried


def test_what_is_typed_at_the_menu_after_its_answer_is_never_left_for_the_shell() -> None:
    # A paste, a second Enter: what came after the answer is discarded with the menu, never left in the terminal's
    # input once its settings are put back.
    master, slave = pty.openpty()
    given = [b"\x1b[A", b"\r"]

    async def read(seconds: float) -> bytes | None:
        got = given.pop(0)
        if not given:
            # Typed with the Enter, and in the terminal's input before the menu ends: the kernel hands a write to the
            # line discipline a moment later, and a change of the settings discards only what has reached it.
            os.write(master, b"second-line\n")
            assert select.select([slave], [], [], 5)[0]
        return got

    class In:
        def fileno(self) -> int:
            return slave

    try:
        menu = Question("Use Tailscale on the VMs?", "n", YES_NO).menu()
        got = asyncio.run(choose.asked(menu, read, In(), io.StringIO(), color=False))
        # Back in its line mode, the terminal would give that line to whatever reads it next.
        left = os.read(slave, 64) if select.select([slave], [], [], 0.5)[0] else b""
    finally:
        os.close(master)
        os.close(slave)
    assert (got, left) == ("y", b"")


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


class _Said(io.StringIO):
    """A stream that writes down each write, with its name, in one list shared with another: the order of what two
    streams said."""

    def __init__(self, name: str, writes: list[tuple[str, str]]) -> None:
        super().__init__()
        self.called, self.writes = name, writes

    def write(self, text: str) -> int:
        self.writes.append((self.called, text))
        return len(text)


def test_a_question_keeps_the_line_of_an_event_the_view_refuses_with_its_own_and_says_each_where_it_goes(
    home, monkeypatch
) -> None:
    # The refused event's line, said on standard error, is kept while the question is up like the view's report after
    # it, and both are said once it ends: in that order, each on its own stream.
    writes: list[tuple[str, str]] = []
    monkeypatch.setattr(sys, "stderr", _Said("stderr", writes))
    with _job() as made:
        view = live.Live(made, DEPLOY, _Said("stdout", writes), clock=Clock())
        made.emit("stage", "alpha", "sign-in")
        with view.aside():
            before = len(writes)
            made.emit("step", "alpha", "nope", stage="no-such-stage")
            made.emit("report", "alpha", "alpha is up at 192.0.2.61")
            assert len(writes) == before, writes[before:]
        after = writes[before:]
        refused = [at for at, (_, text) in enumerate(after) if text.startswith(live.REFUSED.split("(")[0])]
        reported = [at for at, (_, text) in enumerate(after) if "alpha is up at 192.0.2.61" in text]
        assert refused and reported and refused[0] < reported[0], after
        assert (after[refused[0]][0], after[reported[0]][0]) == ("stderr", "stdout"), after
        made.emit("end", None, "", outcome="succeeded", exit=0)


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
