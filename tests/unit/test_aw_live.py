"""The live view of a run, drawn from its events in the run itself (lib/claude_vm/runs/live.py;
docs/architecture/engine.md §16.8).

At a pseudo-terminal of the test's own (harness._at_a_pseudo_terminal), each run here is a child process made by
runs.run as a verb makes one, whose body makes the live view and emits one of tests/unit/test_aw_run_view.py's canned
runs, a moment between its events: the frames it drew, each redraw one write, read back by tests/unit/vt.py with no line
wrapped at 80 or 100 columns, and none drawn once the run has ended; the terminal's echo off while it draws and back at
the end, however the run ended (a return, a Ctrl-C, a SIGTERM, a SIGHUP); a report said above the region and left there;
a failure said above, and the summary's head where it failed; the stopping line a Ctrl-C makes said above the region,
never inside it, and the footer saying the run is stopping; the summary, then NEXT STEPS once, after it, and the bell; a
SIGTERM or a SIGHUP at a random moment while events flow, which leaves no frame's remains and loses no line said above;
and a question asked with the region aside, the view found by the run's job. Piped, and with -v at a terminal, nothing
is drawn: a line as each step ends, or each event as a line; piped into a reader that leaves after the first line, the
run ends with its own status. In this process, with a clock of the test's own: what a run off a terminal prints is
exactly what the view gives after each event (what is said above, the lines of what ended), then the summary and NEXT
STEPS; with -v each event is its line; NEXT STEPS is said line for line either way; an event the model refuses is said
once on standard error and the run goes on, every listener after the view hearing it; a person's stop reaches the model
at the next event; a machine, whose events name it by its identity, is said by the name given for it; nothing is said
after the run's end; what an event says, and standard error's lines above the region, are said whole, a signal that
comes meanwhile delivered after; a job has one view, a second refused; and a stream that has gone is written no more, a
broken pipe pointed at /dev/null. The rule with a test of its own: nothing in runs/ writes an escape sequence or reads
/dev/tty itself, by a scan. The cases of tests/unit/test_live_commands.py about what a run draws each have their twin
here (TWINS), before K14 removes that file.

A key a test types at a run's terminal (an act) waits for a frame that shows what the run waits for, never for the
terminal's title alone. The region writes the title one write before the frame that says the same, and a Ctrl-C typed
between the two can take the frame with it: at an interrupt a terminal flushes what the run wrote and its reader has
not read (ISIG without NOFLSH), and macOS's pseudo-terminal keeps a write there until it is read, where Linux's has
handed it over at once. So the Ctrl-C's act is played again over the run's own writes, read one at a time as macOS
can hand them over, and a scan holds every act of this file to a frame.
"""

from __future__ import annotations

import ast
import contextlib
import errno
import io
import json
import os
import random
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import harness
import pytest
import test_aw_run_view as canned
from aw_kit import environment, stand_ins
from harness import REPO
from live_kit import FOOTER, HEADER, SYNC_ON, Ran, next_steps, piped
from vt import VT

from claude_vm.fleet import paths
from claude_vm.runs import job as jobs
from claude_vm.runs import live, model
from claude_vm.runs.stages import DEPLOY, Declared, Stage, Step
from claude_vm.runs.view import View
from claude_vm.text import banner

LIB = REPO / "lib"
RUNS = LIB / "claude_vm" / "runs"
SYNC_OFF = b"\x1b[?2026l"
STOPPING = "aw: stopping"
NEXT_LINE = "aw login alpha --only github"
# A machine's identity, the key of its every event (job.json's machines), beside the name a person chose.
IDENTITY = "agent-warehouse-00000000-0000-4000-8000-000000000001"

# The cases of tests/unit/test_live_commands.py, today's runs in parts, that are about what a run draws, each with its
# twin here, before K14 removes that file with tools/live. Its other cases are a run in parts (tools/live's state
# carried between parts, a part's child, its stdin and its log), which the new engine has no parts of: a run's children
# and its end are runs/attached.py's (tests/unit/test_aw_attached.py), its text log C12's.
TWINS = {
    "test_a_command_passed_through_is_exactly_what_it_printed_and_never_sees_the_switch": (
        "test_piped_a_run_says_a_line_as_each_step_ends_and_draws_nothing"
    ),
    "test_at_a_terminal_a_part_is_passed_through_without_a_title_or_with_a_v": (
        "test_with_a_v_a_run_at_a_terminal_is_each_event_as_a_line_and_draws_nothing"
    ),
    "test_parts_are_drawn_as_one_run_and_its_end_says_it_whatever_ended_it": (
        "test_a_failed_run_is_drawn_its_failure_said_above_and_its_end_says_where"
    ),
    "test_ctrl_c_ends_the_part_with_the_child_s_last_words_logged_and_the_run_said_stopped": (
        "test_a_ctrl_c_is_said_above_the_region_the_footer_says_stopping_and_the_run_is_said_stopped"
    ),
    "test_a_run_ended_from_outside_ends_its_child_and_gives_the_echo_back": (
        "test_a_run_ended_from_outside_gives_the_echo_back_and_says_it_stopped"
    ),
    "test_a_playbook_is_a_part_too_and_its_next_steps_wait_for_the_end": (
        "test_a_canned_run_is_drawn_frame_by_frame_one_write_a_redraw_and_ends_with_its_summary_then_next_steps"
    ),
    "test_a_part_that_passed_with_warnings_says_them_above": (
        "test_a_canned_run_is_drawn_frame_by_frame_one_write_a_redraw_and_ends_with_its_summary_then_next_steps"
    ),
    "test_a_run_ended_short_is_said_stopped": "test_a_run_that_ends_cancelled_is_said_stopped_where_it_stopped",
    "test_next_steps_says_each_record_line_for_line_as_the_stream_does": (
        "test_next_steps_is_said_line_for_line_drawn_and_streamed"
    ),
}

# ---------------------------------------------------------------- a run in this process ----


class Clock:
    """The view's clock, set by the test."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@contextlib.contextmanager
def _job(machines: tuple[str, ...] = ("alpha",)) -> Iterator[jobs.Job]:
    """A job of this process's own, in the test's fleet home, dropped at the end."""
    made = jobs.create(
        paths.jobs_dir(paths.home(REPO)),
        "deploy",
        " ".join(("aw deploy", *machines)),
        "pve",
        list(machines),
        {"kind": "terminal"},
        True,
    )
    try:
        yield made
    finally:
        jobs.drop(made)


class Said:
    """What a run in this process said: on its stream, on standard error, and what a listener after the view heard."""

    def __init__(self, out: str, errors: str, heard: list[jobs.Event], view: live.Live) -> None:
        self.out, self.errors, self.heard, self.view = out, errors, heard, view

    @property
    def lines(self) -> list[str]:
        return self.out.split("\n")


def _said(
    monkeypatch: pytest.MonkeyPatch,
    events,
    *,
    machines: tuple[str, ...] = ("alpha",),
    verbosity: int = 0,
    cancelled: Callable[[float], bool] | None = None,
    declared: Declared = DEPLOY,
    names: dict[str, str] | None = None,
) -> Said:
    """events emitted through a job of this process's, each at its time on the view's clock, its stream no terminal;
    cancelled(now), where given, says whether a person has stopped the run by then."""
    stream, errors = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)
    clock = Clock()

    class Stop:
        def is_set(self) -> bool:
            return cancelled is not None and cancelled(clock.now)

    heard: list[jobs.Event] = []
    with _job(machines) as made:
        view = live.Live(made, declared, stream, verbosity=verbosity, cancelled=Stop(), names=names, clock=clock)
        made.listen(heard.append)
        for now, (kind, machine, text, data) in events:
            clock.now = now
            made.emit(kind, machine, text, **data)
    return Said(stream.getvalue(), errors.getvalue(), heard, view)


def _expected_off_a_terminal(made_started: float, events, machines=("alpha",), cancel: float | None = None):
    """What the view gives for events off a terminal, each at its time: what is said above and the lines of what ended
    after each event, then a blank line, the summary and NEXT STEPS' block."""
    run = model.Run(DEPLOY, 0.0, list(machines))
    view = View(run, started_at=made_started)
    lines: list[str] = []
    now = 0.0
    for now, (kind, machine, text, data) in events:
        if cancel is not None and now >= cancel and run.stopping is None:
            run.cancel(now)
        run.feed(jobs.Event(now, kind, machine, text, data), now)
        lines += [plain for _, plain in view.above(live.WIDTH, False)]
        lines += view.ended_lines(now)
    lines += ["", *view.summary(now, live.WIDTH, False)]
    if run.next_steps():
        lines += banner(run.next_steps(), live.NEXT_STEPS).split("\n")
    return lines


def test_off_a_terminal_a_run_says_what_the_view_gives_after_each_event_then_its_summary_and_next_steps(
    home, monkeypatch
) -> None:
    said = _said(monkeypatch, canned.ONE)
    assert said.view.mode == live.LINES
    started = said.view.view.started_at
    assert said.lines == [*_expected_off_a_terminal(started, canned.ONE), ""]
    # Fed at the view's own clock, never the event's wall-clock time: the canned times are the model's.
    assert said.view.run.began == 0.0 and said.view.run.ended == 141.0
    assert said.errors == "" and SYNC_ON.decode() not in said.out and "\x1b" not in said.out
    # One line as each step and stage ends, the report above where it came, and NEXT STEPS after the summary.
    out = said.out
    assert re.search(r"^✓ provision +1:00$", out, re.M), out
    assert out.index("alpha is up at 192.0.2.61") < out.index("✓ provision")
    assert out.index("agent-warehouse  deploy alpha · done in") < out.index("##  NEXT STEPS")
    assert next_steps(out) == [NEXT_LINE]


def test_off_a_terminal_a_fleets_stage_is_said_once_every_machine_is_through_it(home, monkeypatch) -> None:
    machines = ("alpha", "beta", "gamma")
    said = _said(monkeypatch, canned.THREE, machines=machines)
    started = said.view.view.started_at
    assert said.lines == [*_expected_off_a_terminal(started, canned.THREE, machines), ""]
    # Its end said once (a wait in it is a line of its own, ⏸).
    assert len([line for line in said.lines if re.match(r"[✓–✗■] configure ", line)]) == 1, said.out


def test_with_a_v_each_event_is_its_line_and_nothing_is_drawn_but_next_steps_at_the_end(home, monkeypatch) -> None:
    said = _said(monkeypatch, canned.ONE, verbosity=1)
    assert said.view.mode == live.STREAM
    streamed = [live.streamed(event) for event in said.heard]
    assert said.lines == [*streamed, *banner([NEXT_LINE], live.NEXT_STEPS).split("\n"), ""]
    assert "stage alpha: provision · outcome=done" in streamed
    assert "report alpha: alpha is up at 192.0.2.61" in streamed
    assert streamed[-1] == "end · outcome=succeeded · exit=0"
    # No summary, no frame: as today any -v gives the stream.
    assert "agent-warehouse  deploy" not in said.out and "\x1b" not in said.out


def test_an_events_line_under_v_is_one_line_whatever_its_text_or_data() -> None:
    event = jobs.Event(0.0, "report", "alpha", "two\nlines\tand a tab", {"page": "https://x", "n": [1, {"a": 2}]})
    assert live.streamed(event) == 'report alpha: two lines and a tab · page=https://x · n=[1, {"a": 2}]'
    assert live.streamed(jobs.Event(0.0, "stage", None, "template", {})) == "stage: template"


def test_next_steps_is_said_line_for_line_drawn_and_streamed(home, monkeypatch) -> None:
    # just init's records, as test_live_commands.py's twin has them: bullets under the line that counts them, and a
    # paragraph longer than any terminal, whole.
    long = "Nothing is asked without a terminal: run it at one, or give the values on its command line. " * 3
    record = "Still to set:\n  the Proxmox host address\n" + long.strip()
    check = "2 problems: fix them, then run it again.\n  !!   the token was refused\n  !!   root SSH failed"
    events = [
        (0.0, canned._event("stage", "alpha", "provision")),
        (1.0, canned._event("next", None, record, matter="init_settings")),
        (1.0, canned._event("next", None, check, matter="init_check")),
        (2.0, canned._event("stage", "alpha", "provision", outcome="done")),
        (2.0, canned._event("end", None, "", outcome="succeeded", exit=0)),
    ]
    expected = [*record.split("\n"), *check.split("\n")]
    drawn = _said(monkeypatch, events)
    streamed = _said(monkeypatch, events, verbosity=2)
    assert next_steps(drawn.out) == next_steps(streamed.out) == expected


def test_a_run_that_ends_cancelled_is_said_stopped_where_it_stopped(home, monkeypatch) -> None:
    # A run whose body ended it short (130 returned, or a part a Ctrl-C stopped) is said stopped, and where.
    said = _said(monkeypatch, canned.CANCELLED)
    assert re.search(r"^agent-warehouse  deploy alpha · stopped after 2 min 31 s at Claude", said.out, re.M), said.out
    assert re.search(r"^ ■ sign in ", said.out, re.M), said.out


def test_a_persons_stop_reaches_the_model_at_the_next_event_and_ends_the_wait(home, monkeypatch) -> None:
    events = [*canned.TO_THE_SIGN_IN, (151.0, canned._event("report", "alpha", "stopping"))]
    said = _said(monkeypatch, events, cancelled=lambda now: now >= 150.0)
    run = said.view.run
    assert run.stopping == 151.0 and run.ended is None and run.waiting() == []
    # The footer says so while the run has not ended.
    assert said.view.view.frame(152.0, 100, 30, "⠋", False)[-1].endswith("· stopping")


def test_an_event_the_model_refuses_is_said_once_and_the_run_goes_on_every_listener_hearing_it(
    home, monkeypatch
) -> None:
    events = [
        (0.0, canned._event("stage", "alpha", "provision")),
        (1.0, canned._event("step", "alpha", "build", stage="configure")),
        (2.0, canned._event("stage", "alpha", "provision", outcome="done")),
        (2.0, canned._event("end", None, "", outcome="succeeded", exit=0)),
    ]
    said = _said(monkeypatch, events)
    refusal = said.errors.split("\n")
    assert refusal[1:] == [""] and refusal[0].startswith(
        "aw: the live view left out an event it could not take (step alpha: build): 'build' is no step of stage"
    ), said.errors
    # The listener after the view heard every event, the refused one among them, and the run went on to its end.
    assert [event.text for event in said.heard] == ["provision", "build", "provision", ""]
    assert "✓ provision" in said.out and "agent-warehouse  deploy alpha · done in" in said.out


def test_nothing_is_said_after_the_runs_end(home, monkeypatch) -> None:
    events = [
        (0.0, canned._event("stage", "alpha", "provision")),
        (1.0, canned._event("end", None, "", outcome="succeeded", exit=0)),
        (2.0, canned._event("report", "alpha", "after the end")),
    ]
    said = _said(monkeypatch, events)
    assert "after the end" not in said.out and said.errors == ""
    assert said.view.ended and said.heard[-1].text == "after the end"


def test_a_machine_is_said_by_the_name_given_for_its_identity_the_key_of_its_events(home, monkeypatch) -> None:
    # The job keeps the machine's identity alone, which every event names; its caller gives the name a person chose.
    events = [
        (0.0, canned._event("stage", IDENTITY, "provision")),
        (2.0, canned._event("report", IDENTITY, "up at 192.0.2.61")),
        (3.0, canned._event("stage", IDENTITY, "provision", outcome="done")),
        (3.0, canned._event("end", None, "", outcome="succeeded", exit=0)),
    ]
    said = _said(monkeypatch, events, machines=(IDENTITY,), names={IDENTITY: "alpha"})
    assert said.view.run.machines == [IDENTITY]
    assert "agent-warehouse  deploy alpha · done in 3 s" in said.out and IDENTITY not in said.out, said.out


def test_a_live_view_takes_a_declared_run_and_a_count_of_v(home) -> None:
    with _job() as made:
        with pytest.raises(TypeError, match="a Declared"):
            live.Live(made, object(), io.StringIO())  # type: ignore[arg-type]
        for verbosity in (-1, True, "1"):
            with pytest.raises(ValueError, match="count of -v"):
                live.Live(made, DEPLOY, io.StringIO(), verbosity=verbosity)  # type: ignore[arg-type]


# ---------------------------------------------------------------- a run at a terminal ----

# A run, in a child process, as a verb makes one: argv is the lib folder, the jobs folder, the test's folder, the body's
# way and the -v count. Its body makes the live view on a standard output that keeps each write, writes its pid, then
# emits the canned events (events.json: each a pause and an event), noting after each whether the terminal echoes;
# then ends its way: returns that status, holds until it is ended from outside, or waits for the Ctrl-C and returns.
RUN = r"""
import asyncio, atexit, io, json, os, sys, termios, time
lib, jobs_dir, folder, way, verbosity = sys.argv[1:6]
sys.path.insert(0, lib)
from pathlib import Path
from claude_vm import runs
from claude_vm.runs import live
from claude_vm.runs.stages import DEPLOY

folder = Path(folder)
EVENTS = json.loads((folder / "events.json").read_text())


class Kept(io.TextIOBase):
    def __init__(self, real):
        super().__init__()
        self.real, self.writes, self.times = real, [], []

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
        self.writes.append(text)
        self.times.append(time.monotonic())
        return self.real.write(text)

    def flush(self):
        self.real.flush()


def echoes():
    try:
        return bool(termios.tcgetattr(0)[3] & termios.ECHO)
    except termios.error:
        return None


async def body(ctx):
    out = Kept(sys.stdout)
    echo, asked = [], []
    atexit.register(
        lambda: (folder / "kept.json").write_text(
            json.dumps({"writes": out.writes, "times": out.times, "echo": echo, "asked": asked})
        )
    )
    live.Live(ctx.job, DEPLOY, out, verbosity=int(verbosity), cancelled=ctx.cancelled)
    (folder / "pid").write_text(str(os.getpid()))
    for pause, (kind, machine, text, data) in EVENTS:
        if kind == "await":
            # Till the test says to go on, by the file named.
            while not (folder / text).exists():
                await asyncio.sleep(0.01)
            continue
        await asyncio.sleep(pause)
        ctx.emit(kind, machine, text, **data)
        echo.append(echoes())
    if way == "flow":
        # At work with no pause: a report said above the region and a line of work each turn, till ended from outside.
        for turn in range(1, 100001):
            ctx.emit("report", "alpha", f"report {turn:05}")
            ctx.emit("step", "alpha", f"Task {turn:05}", status="task", part="common")
            await asyncio.sleep(0)
        await asyncio.sleep(600)
    if way == "aside":
        # A question, as runs/ask.py asks one: the run's view found by its job, the region aside while it is asked.
        view = live.Live.of(ctx.job)
        with view.aside():
            sys.stdout.write("Go on? ")
            sys.stdout.flush()
            asked.append(echoes())
            await asyncio.sleep(0.4)
            sys.stdout.write("yes\n")
            sys.stdout.flush()
        asked.extend([echoes(), time.monotonic()])
        await asyncio.sleep(1.0)
        return 0
    if way == "hold":
        await asyncio.sleep(600)
    if way == "stop":
        await ctx.cancelled.wait()
        await asyncio.sleep(0.6)
        return 130
    return int(way)


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
# The run's Python: isolated, as every run of the engine's is, and in UTF-8 whatever the locale (-I ignores PYTHONUTF8).
CHILD = [sys.executable, "-I", "-X", "utf8", "-c", RUN]
# A canned run's pauses: a moment between two events, and a longer one where the run would be at work, for the
# spinner's redraws between them.
PAUSE, AT_WORK = 0.03, 0.5


def _events(path: Path, events, at_work: tuple[str, ...] = ("Write the motd",)) -> None:
    """events as the run in the child emits them: each with the pause before it, a longer one after a line of work
    at_work names; the run's own end left out, which the ladder emits once the body has ended."""
    paused, before = [], ""
    for _, (kind, machine, text, data) in events:
        if kind == "end" and machine is None:
            continue
        paused.append([AT_WORK if before in at_work else PAUSE, [kind, machine, text, data]])
        before = text
    (path / "events.json").write_text(json.dumps(paused))


class AtATerminal:
    """A run at a pseudo-terminal, read back by a VT: as live_kit's Ran (its screen, frames, transcript and the region
    gone), with how the run ended, the terminal's echo once it had, and what the run kept of its own writes."""

    def __init__(self, ran: Ran, watched: dict, kept: dict | None) -> None:
        self.ran, self.watched, self.kept = ran, watched, kept


def _at_a_terminal(
    home: Path,
    folder: Path,
    way: str,
    *,
    verbosity: int = 0,
    cols: int = 100,
    rows: int = 30,
    act: Callable[[bytes, VT, int, Path], None] | None = None,
) -> AtATerminal:
    jobs_dir = paths.jobs_dir(paths.home(REPO))
    run = [*CHILD, str(LIB), str(jobs_dir), str(folder), way, str(verbosity)]
    argv = [sys.executable, "-I", "-c", WATCHES, *run]
    temporary = folder / "tmp"
    temporary.mkdir(exist_ok=True)
    env = environment(home, {"TERM": "xterm-256color", "PYTHONUTF8": "1", "TMPDIR": str(temporary)})
    screen = VT(cols, rows)

    def drive(chunk: bytes, output: bytes, master: int, process) -> None:
        screen.feed(chunk)
        if act is not None:
            act(output, screen, master, folder)

    rc, raw = harness._at_a_pseudo_terminal(argv, folder, env, size=(cols, rows), act=drive, timeout=90, poll=0.05)
    harness._fail_if_the_desktop_was_reached(stand_ins(home))
    found = WATCHED.search(raw)
    assert rc == 0 and found is not None, raw.decode(errors="replace")
    watched = {"echo": found.group(1) == b"true", "status": int(found.group(2))}
    kept = folder / "kept.json"
    return AtATerminal(Ran(rc, raw, "", screen), watched, json.loads(kept.read_text()) if kept.is_file() else None)


def _redraws(writes: list[str]) -> list[str]:
    """The writes that drew the region."""
    return [write for write in writes if SYNC_ON.decode() in write or SYNC_OFF.decode() in write]


# ---------------------------------------------------------------- what a test types at, and when ----


def _newest_frame(output: bytes) -> str:
    """The newest frame drawn whole in what was read, its escapes left out: '' before the first, and while one is
    still being drawn, whose end a Ctrl-C typed now could take with it."""
    start = output.rfind(SYNC_ON)
    if start < 0 or output.find(SYNC_OFF, start) < 0:
        return ""
    return Ran(0, output[start:], "").frames[-1]


def _ctrl_c_once_waiting() -> tuple[Callable[[bytes, VT, int, Path], None], list[int]]:
    """An act that types Ctrl-C once, when the newest frame read says the run waits for you, and what it typed."""
    typed: list[int] = []

    def interrupt(output: bytes, screen: VT, master: int, folder: Path) -> None:
        if not typed and "waiting for you" in _newest_frame(output):
            typed.append(os.write(master, b"\x03"))

    return interrupt, typed


def _read_one_write_at_a_time(
    writes: list[str], act: Callable[[bytes, VT, int, Path], None], typed: list[int]
) -> bytes:
    """The run's own writes read as macOS's terminal can hand them over, each on its own, and the act told after each,
    as _at_a_terminal tells it after each read: what had been read when the act typed a key (what the run wrote after
    is what the terminal's flush at a Ctrl-C would take), or everything, where it never typed."""
    screen, read = VT(100, 30), b""
    keys = os.open(os.devnull, os.O_WRONLY)
    try:
        for write in writes:
            data = write.encode()
            screen.feed(data)
            read += data
            act(read, screen, keys, Path())
            if typed:
                break
    finally:
        os.close(keys)
    return read


def _the_wait_drawn(frames: list[str], said: str) -> int:
    """The last frame that says the run waits for you; that none did, said in words."""
    waiting = [index for index, frame in enumerate(frames) if "waiting for you" in frame]
    assert waiting, f"no frame read says the run waits for you, the frame the Ctrl-C waits for:\n{said}"
    return waiting[-1]


@pytest.mark.parametrize("cols", [80, 100])
def test_a_canned_run_is_drawn_frame_by_frame_one_write_a_redraw_and_ends_with_its_summary_then_next_steps(
    cols, home, tmp_path
) -> None:
    _events(tmp_path, canned.ONE)
    seen = _at_a_terminal(home, tmp_path, "0", cols=cols)
    ran, kept = seen.ran, seen.kept
    assert seen.watched == {"echo": True, "status": 0}, ran.text
    # Drawn, never wrapped, nothing unknown written, and nothing of the region left once the run ended.
    ran.assert_drawn_and_gone()
    assert ran.screen.frame_wraps == 0
    # Each redraw one write, whole: its synchronized output's mark at its start, the end mark at its end, one of each.
    assert kept is not None
    redraws = _redraws(kept["writes"])
    on, off = SYNC_ON.decode(), SYNC_OFF.decode()
    assert len(redraws) >= len(json.loads((tmp_path / "events.json").read_text()))
    assert all(each.startswith(on) and each.endswith(off) for each in redraws), redraws
    assert all(each.count(on) == each.count(off) == 1 for each in redraws)
    # The region gone before the summary: nothing drawn after the run's end (its head coloured, so read by its words).
    head = "agent-warehouse  deploy alpha · done in"
    writes = kept["writes"]
    summary = next(index for index, each in enumerate(writes) if head.split("  ", 1)[1] in each)
    assert max(index for index, each in enumerate(writes) if on in each) < summary
    # The echo off while it drew, after every event, and back at the end.
    assert kept["echo"] and set(kept["echo"]) == {False}
    # Frame by frame: the header, configure at work under its row, the footer's stage of the seven.
    frames = ran.frames
    assert any("agent-warehouse  deploy alpha · started" in frame for frame in frames)
    at_work = [frame for frame in frames if "Write the motd" in frame]
    assert at_work and re.search(r"^ \S configure ", at_work[-1], re.M), at_work[-1]
    assert re.search(r"^\S \d+:\d\d · stage 3 of 7", at_work[-1], re.M), at_work[-1]
    assert all(len(line) < cols for frame in frames for line in frame.split("\n")), frames
    # A report said above the region and left there: above the summary, once the run has ended.
    screen = ran.transcript
    assert screen.count("alpha is up at 192.0.2.61") >= 1 and screen.index("alpha is up at") < screen.index(head)
    # The summary once, then NEXT STEPS once, after it; the title says how the run ended.
    assert screen.count(head) == 1 and screen.count("##  NEXT STEPS") == 1, screen
    assert screen.index(head) < screen.index("##  NEXT STEPS")
    assert next_steps(screen) == [NEXT_LINE]
    assert re.search(r"^ ✓ provision ", screen.split(head, 1)[1], re.M), screen
    assert ran.screen.titles[-1] == "deploy alpha · done"
    assert ran.screen.bells >= 1


def test_a_failed_run_is_drawn_its_failure_said_above_and_its_end_says_where(home, tmp_path) -> None:
    _events(tmp_path, canned.FAILING_ALONE)
    seen = _at_a_terminal(home, tmp_path, "1")
    ran = seen.ran
    assert seen.watched == {"echo": True, "status": 1}, ran.text
    ran.assert_drawn_and_gone()
    screen = ran.transcript
    head = re.search(r"^agent-warehouse  deploy alpha · failed after \d+ s at configure", screen, re.M)
    assert head is not None, screen
    # The failure above, for good, before the summary: where, whose and why.
    above = screen[: head.start()]
    assert re.search(r"^✗ configure\b.*alpha", above, re.M) and "Write the motd: permission denied" in above, above
    assert re.search(r"^ ✗ configure ", screen[head.start() :], re.M), screen
    assert ran.screen.titles[-1] == "deploy alpha · failed"


def test_a_ctrl_c_is_said_above_the_region_the_footer_says_stopping_and_the_run_is_said_stopped(home, tmp_path) -> None:
    _events(tmp_path, canned.TO_THE_SIGN_IN)
    # Once a frame shows the run waits for you, and once only.
    interrupt, typed = _ctrl_c_once_waiting()
    seen = _at_a_terminal(home, tmp_path, "stop", act=interrupt)
    ran, kept = seen.ran, seen.kept
    assert typed and seen.watched == {"echo": True, "status": 130}, ran.text
    # The race, made to happen: the same act over the run's own writes, read one at a time. Where it types, the frame
    # this test looks for below has been read, so the terminal's flush at the Ctrl-C cannot take it.
    assert kept is not None
    replayed, typed_again = _ctrl_c_once_waiting()
    read = _read_one_write_at_a_time(kept["writes"], replayed, typed_again)
    assert typed_again, read.decode(errors="replace")
    _the_wait_drawn(Ran(0, read, "").frames, read.decode(errors="replace"))
    ran.assert_drawn_and_gone()
    lines = ran.transcript.split("\n")
    # The ladder's line, on standard error, said above the region on a line of its own, and never inside a frame.
    stopping = [line for line in lines if STOPPING in line]
    assert len(stopping) == 1 and stopping[0].startswith(STOPPING), ran.transcript
    assert not any(STOPPING in frame for frame in ran.frames)
    # A frame after it says the run is stopping, and the wait for you ended.
    after = ran.frames[_the_wait_drawn(ran.frames, ran.transcript) + 1 :]
    assert any(re.search(r"· stopping", frame) for frame in after), after
    head = next(line for line in lines if line.startswith("agent-warehouse  deploy alpha · "))
    assert re.match(r"agent-warehouse  deploy alpha · stopped after \d+ s at Claude", head), head
    assert lines.index(stopping[0]) < lines.index(head)
    assert ran.screen.titles[-1] == "deploy alpha · stopped"


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP], ids=["SIGTERM", "SIGHUP"])
def test_a_run_ended_from_outside_gives_the_echo_back_and_says_it_stopped(signum, home, tmp_path) -> None:
    _events(tmp_path, canned.ONE_TO_CONFIGURE)
    sent: list[int] = []

    def end_it(output: bytes, screen: VT, master: int, folder: Path) -> None:
        # Once the last event is drawn and the run holds.
        pid = folder / "pid"
        if not sent and pid.is_file() and "Write the motd" in "\n".join(screen.lines):
            sent.append(int(pid.read_text()))
            os.kill(sent[0], signum)

    seen = _at_a_terminal(home, tmp_path, "hold", act=end_it)
    ran = seen.ran
    assert sent and seen.watched == {"echo": True, "status": -signum}, ran.text
    ran.assert_drawn_and_gone()
    assert re.search(r"^agent-warehouse  deploy alpha · stopped after \d+ s at configure", ran.transcript, re.M)
    assert ran.screen.titles[-1] == "deploy alpha · stopped"


# A report the flowing run says above the region, under its machine, and the same in the summary's last ten.
FLOWED = re.compile(r"^  alpha  report (\d{5})$", re.M)
SUMMED = re.compile(r"^ report (\d{5})$", re.M)
# How many runs of each signal at a random moment, and the latest moment after the flow is seen, in seconds.
TRIES, LATEST = 8, 0.4


@pytest.mark.parametrize("attempt", range(TRIES))
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP], ids=["SIGTERM", "SIGHUP"])
def test_a_kill_or_a_hangup_while_events_flow_leaves_no_frame_and_loses_no_line(
    signum, attempt, home, tmp_path
) -> None:
    # The ladder raises from its handler wherever the run's thread is (runs/attached.py's Abandoned): a frame drawn and
    # not counted was left above the summary, an erase not counted wiped lines said above it, a line the view counted
    # as said and did not write was lost. The signal comes at a moment of its own while the run says a report and a
    # line of work each turn with no pause, with the terminal's reader behind, as a person's often is.
    _events(tmp_path, canned.ONE_TO_CONFIGURE[:6])
    delay = random.uniform(0.0, LATEST)
    state: dict[str, float] = {}

    def end_it(output: bytes, screen: VT, master: int, folder: Path) -> None:
        if "sent" in state:
            return
        if "flowing" not in state:
            if b"report 00001" in output:
                state["flowing"] = time.monotonic()
            return
        if time.monotonic() >= state["flowing"] + delay:
            state["sent"] = 1.0
            os.kill(int((folder / "pid").read_text()), signum)

    seen = _at_a_terminal(home, tmp_path, "flow", act=end_it)
    ran = seen.ran
    lines = ran.screen.transcript()
    tail = "\n".join(lines[-60:])
    assert "sent" in state and seen.watched == {"echo": True, "status": -signum}, tail
    assert ran.screen.unknown == set() and ran.screen.pending == b"" and ran.screen.frame_wraps == 0, tail
    # No frame's header or footer anywhere, above the summary or in it.
    left = [(number, line) for number, line in enumerate(lines) if FOOTER.match(line) or HEADER.match(line)]
    assert left == [], (left, tail)
    screen = ran.transcript
    head = re.search(r"^agent-warehouse  deploy alpha · stopped after .+ at configure", screen, re.M)
    assert head is not None, tail
    # Every report said above, once and in order, none wiped; the last of them the last the view took, which the
    # summary's last ten end with: none counted as said and lost.
    said = [int(number) for number in FLOWED.findall(screen[: head.start()])]
    assert said and said == list(range(1, len(said) + 1)), (said, tail)
    summed = [int(number) for number in SUMMED.findall(screen[head.start() :])]
    assert summed and summed[-1] == said[-1], (said[-3:], summed, tail)


def test_a_question_has_the_region_aside_found_by_the_runs_job(home, tmp_path) -> None:
    # As runs/ask.py asks one: Live.of(context.job), and the question inside its aside().
    _events(tmp_path, canned.ONE_TO_CONFIGURE)
    seen = _at_a_terminal(home, tmp_path, "aside")
    ran, kept = seen.ran, seen.kept
    assert seen.watched == {"echo": True, "status": 0}, ran.text
    ran.assert_drawn_and_gone()
    assert kept is not None
    during, after, ended = kept["asked"]
    # The echo on while it is asked, and off again after it, while the region draws.
    assert (during, after) == (True, False), kept["asked"]
    # The region erased before the question, nothing drawn while it is up, and drawn again only half a second on.
    raw = ran.raw
    asked, answered = raw.index(b"Go on? "), raw.index(b"yes\r\n")
    assert raw[:asked].endswith(b"\r\x1b[J"), raw[asked - 40 : asked]
    assert SYNC_ON not in raw[asked:answered] and SYNC_ON in raw[answered:], raw[asked:answered]
    again = [then for write, then in zip(kept["writes"], kept["times"], strict=True) if SYNC_ON.decode() in write]
    later = [then for then in again if then > ended]
    assert later and later[0] - ended >= live.live_region.SETTLE_SECONDS - 0.05, (later[:1], ended)
    assert "Go on? yes" in ran.transcript.split("\n")


def test_piped_into_a_reader_that_leaves_early_a_run_ends_with_its_own_status(home, tmp_path) -> None:
    # aw deploy … | head -1: once head has its line and has gone, every write after it fails; the run still ends 0,
    # and Python says nothing at its exit's flush (BrokenPipeError, and the status 120 it gives).
    events = [*canned.ONE[:2], (0.7, ("await", None, "go", {})), *canned.ONE[2:]]
    _events(tmp_path, events)
    jobs_dir = paths.jobs_dir(paths.home(REPO))
    argv = [*CHILD, str(LIB), str(jobs_dir), str(tmp_path), "0", "0"]
    env = environment(home, {"TERM": "xterm-256color", "PYTHONUTF8": "1"})
    run = subprocess.Popen(
        argv,
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        process_group=0,
    )
    try:
        assert run.stdout is not None
        head = subprocess.run(["/usr/bin/head", "-1"], stdin=run.stdout, capture_output=True, timeout=60, check=False)
        run.stdout.close()
        (tmp_path / "go").write_text("")
        _, errors = run.communicate(timeout=60)
    except BaseException:
        harness._end(run)
        raise
    harness._fail_if_the_desktop_was_reached(stand_ins(home))
    assert head.stdout.decode().startswith("✓ template"), head.stdout
    assert (run.returncode, errors.decode()) == (0, "")


def test_a_job_has_one_live_view_and_a_second_is_refused_before_it_takes_anything(home, monkeypatch) -> None:
    # A second view would save the terminal's settings with the first's echo off and put those back after the run, and
    # leave standard error the first's writer: refused, by the job's folder, whatever Job reads it.
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)
    with _job() as made, _job() as other:
        # A view that could not be made took nothing, and is no view of the job's, even while its error is kept.
        with pytest.raises(TypeError, match="a name, a str") as refused:
            live.Live(made, DEPLOY, io.StringIO(), names={"alpha": 5})  # type: ignore[dict-item]
        assert live.Live.of(made) is None and made._listeners == [], refused
        first = live.Live(made, DEPLOY, io.StringIO())
        listening = list(made._listeners)
        for again in (made, jobs.Job(made.folder)):
            with pytest.raises(RuntimeError, match=f"job {made.id} has its live view already"):
                live.Live(again, DEPLOY, io.StringIO())
        assert made._listeners == listening and sys.stderr is errors
        assert live.Live.of(made) is first and live.Live.of(jobs.Job(made.folder)) is first
        assert live.Live.of(other) is None


class Ended(BaseException):
    """What the ladder's handler of SIGTERM and SIGHUP raises wherever the run's thread is (runs/attached.py's
    Abandoned), here from a handler of the test's own."""


@contextlib.contextmanager
def _ending_on(signum: int) -> Iterator[None]:
    """signum's handler raising Ended, as the ladder's does, then the one before put back."""

    def ended(number: int, _frame: object) -> None:
        raise Ended(number)

    held = signal.signal(signum, ended)
    try:
        yield
    finally:
        signal.signal(signum, held)


class Signalling(io.StringIO):
    """A stream that sends this process signum as the first text holding `at` is written to it."""

    def __init__(self, at: str, signum: int) -> None:
        super().__init__()
        self.at, self.signum, self.sent = at, signum, False

    def write(self, text: str) -> int:
        written = super().write(text)
        if self.at in text and not self.sent:
            self.sent = True
            signal.raise_signal(self.signum)
        return written


def test_what_an_event_says_is_said_whole_and_a_signal_meanwhile_is_delivered_after(home, monkeypatch) -> None:
    # A report of two lines, the signal coming as its first is written: both said, then the end at once.
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    stream = Signalling("first of two", signal.SIGTERM)
    with _job() as made, _ending_on(signal.SIGTERM):
        live.Live(made, DEPLOY, stream)
        made.emit("stage", "alpha", "provision")
        with pytest.raises(Ended):
            made.emit("report", "alpha", "first of two\nsecond of two")
    assert stream.sent and "second of two" in stream.getvalue(), stream.getvalue()


def test_standard_errors_lines_are_said_above_whole_and_a_signal_meanwhile_is_delivered_after() -> None:
    errors = Signalling("one", signal.SIGHUP)
    display = live.Display(io.StringIO(), errors)
    # Each whole line handed to the display, as the view hands them while no question is up (Live._complained).
    above = live._Above(display, errors, lambda lines: [display.display(line, stderr=True) for line, _ in lines])
    with _ending_on(signal.SIGHUP), pytest.raises(Ended):
        above.write("one\ntwo\n")
    assert errors.getvalue() == "one\ntwo\n"


class Gone(io.StringIO):
    """A stream whose terminal has gone: each write fails."""

    def __init__(self) -> None:
        super().__init__()
        self.tries = 0

    def write(self, text: str) -> int:
        self.tries += 1
        raise OSError(errno.EIO, "Input/output error")


def test_once_a_write_to_a_stream_fails_nothing_more_is_written_there() -> None:
    gone, errors = Gone(), io.StringIO()
    display = live.Display(gone, errors)
    for line in ("one", "two", "three"):
        display.display(line)
    display.display("on standard error", stderr=True)
    assert gone.tries == 1 and errors.getvalue() == "on standard error\n"


@pytest.mark.parametrize("inheritable", [False, True], ids=["kept-from-children", "inherited"])
def test_a_stream_whose_pipe_is_broken_is_pointed_at_dev_null_so_its_last_flush_succeeds(inheritable) -> None:
    # As inheritable as it was: a child that inherits the run's standard output inherits /dev/null, and one that does
    # not is never handed it.
    read, write = os.pipe()
    os.close(read)
    os.set_inheritable(write, inheritable)
    stream = os.fdopen(write, "w", encoding="utf-8")
    try:
        live.Display(stream, io.StringIO()).display("a line nobody reads")
        null, found = os.stat(os.devnull), os.fstat(write)
        assert (found.st_dev, found.st_ino) == (null.st_dev, null.st_ino)
        assert os.get_inheritable(write) is inheritable
        # What it still held goes there at the flush Python makes at its exit.
        stream.write("and the rest")
        stream.flush()
    finally:
        stream.close()


def test_piped_a_run_says_a_line_as_each_step_ends_and_draws_nothing(home, tmp_path) -> None:
    _events(tmp_path, canned.ONE)
    jobs_dir = paths.jobs_dir(paths.home(REPO))
    argv = [*CHILD, str(LIB), str(jobs_dir), str(tmp_path), "0", "0"]
    ran = piped(argv, tmp_path, environment(home, {"TERM": "xterm-256color", "PYTHONUTF8": "1"}), timeout=90)
    harness._fail_if_the_desktop_was_reached(stand_ins(home))
    assert ran.rc == 0, ran.text
    assert SYNC_ON not in ran.raw and "\x1b" not in ran.text, ran.text
    order = ["✓ template", "alpha is up at 192.0.2.61", "✓ provision", "✓ configure", "agent-warehouse  deploy alpha"]
    found = [ran.text.find(said) for said in order]
    assert -1 not in found and found == sorted(found), ran.text
    assert re.search(r"^✓ provision +\S", ran.text, re.M) and next_steps(ran.text) == [NEXT_LINE]


def test_with_a_v_a_run_at_a_terminal_is_each_event_as_a_line_and_draws_nothing(home, tmp_path) -> None:
    _events(tmp_path, canned.ONE)
    seen = _at_a_terminal(home, tmp_path, "0", verbosity=1)
    ran = seen.ran
    assert seen.watched == {"echo": True, "status": 0}, ran.text
    assert SYNC_ON not in ran.raw and not ran.frames
    screen = ran.transcript
    assert re.search(r"^stage alpha: provision · outcome=done$", screen, re.M), screen
    assert re.search(r"^report alpha: alpha is up at 192\.0\.2\.61$", screen, re.M), screen
    assert re.search(r"^end: done · outcome=succeeded · exit=0$", screen, re.M), screen
    assert "agent-warehouse  deploy" not in screen and next_steps(screen) == [NEXT_LINE]


# ---------------------------------------------------------------- the rules ----

# What a terminal takes as the start of an escape sequence: ESC, and the 8-bit introducers (CSI, OSC, DCS, SOS, PM,
# APC).
INTRODUCERS = ("\x1b", "\x9b", "\x9d", "\x90", "\x98", "\x9e", "\x9f")


def _talks_to_the_terminal(source: str) -> list[str]:
    """Where source writes an escape sequence or reads the terminal itself: a string or bytes holding an escape's
    introducer or naming /dev/tty, a chr() of an introducer, a call of ctermid."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            text = node.value if isinstance(node.value, str) else node.value.decode("latin-1")
            if any(introducer in text for introducer in INTRODUCERS):
                found.append(f"line {node.lineno}: an escape sequence")
            if "/dev/tty" in text:
                found.append(f"line {node.lineno}: /dev/tty")
        elif isinstance(node, ast.Call):
            called = ast.unparse(node.func)
            first = node.args[0] if node.args else None
            if called.split(".")[-1] == "ctermid":
                found.append(f"line {node.lineno}: ctermid")
            if called == "chr" and isinstance(first, ast.Constant) and first.value in map(ord, INTRODUCERS):
                found.append(f"line {node.lineno}: chr of an escape")
    return found


def test_nothing_in_runs_writes_an_escape_sequence_or_reads_the_terminal_itself() -> None:
    modules = sorted(RUNS.glob("*.py"))
    assert RUNS / "live.py" in modules and len(modules) >= 10, modules
    for path in modules:
        assert _talks_to_the_terminal(path.read_text()) == [], path


@pytest.mark.parametrize(
    "source",
    [
        'print("\\x1b[2J")',
        'out.write(b"\\x1b]0;title\\x07")',
        "SAY = '\\033[1m'",
        'write(f"{up}\\N{ESCAPE}[K")',
        "csi = '\\x9b'",
        'open("/dev/tty")',
        "os.ctermid()",
        "chr(27)",
        "chr(0x9b)",
    ],
)
def test_the_scan_finds_each_way_to_the_terminal(source) -> None:
    assert _talks_to_the_terminal(source), source


# What a frame is read through here: the newest one drawn whole, or every one.
FRAME_READERS = {"_newest_frame", "frames"}


def _acts_at_the_title_alone(source: str) -> list[str]:
    """Where an act given as act= reads the screen's titles and no frame (FRAME_READERS) anywhere in it, by line: an
    act is a function of the file's, by its name (every one of that name), or a lambda; one of any other form, which
    this scan cannot read, is said too."""
    tree = ast.parse(source)
    functions: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.setdefault(node.name, []).append(node)
    found = []
    for node in ast.walk(tree):
        for keyword in node.keywords if isinstance(node, ast.Call) else []:
            if keyword.arg != "act" or (isinstance(keyword.value, ast.Constant) and keyword.value.value is None):
                continue
            value = keyword.value
            acts = [value] if isinstance(value, ast.Lambda) else functions.get(getattr(value, "id", ""), [])
            if not acts:
                found.append(f"line {node.lineno}: an act this scan cannot read, {ast.unparse(value)}")
            for act in acts:
                read = {sub.attr for sub in ast.walk(act) if isinstance(sub, ast.Attribute)}
                read |= {sub.id for sub in ast.walk(act) if isinstance(sub, ast.Name)}
                if "titles" in read and not read & FRAME_READERS:
                    found.append(f"line {act.lineno}: an act at the title alone")
    return found


def test_no_act_types_at_the_title_alone() -> None:
    source = Path(__file__).read_text()
    assert _acts_at_the_title_alone(source) == []
    # The acts of this file the scan reads: the Ctrl-C's, the kills', and the reader's own that tells each.
    given = {
        ast.unparse(keyword.value)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "act"
    }
    assert {"interrupt", "end_it", "drive"} <= given, given


@pytest.mark.parametrize(
    "source",
    [
        "def interrupt(output, screen, master, folder):\n"
        "    if screen.titles and screen.titles[-1].endswith('waiting for you'):\n"
        "        os.write(master, b'x')\n"
        "run(act=interrupt)",
        "run(act=lambda output, screen, master, folder: 'waiting' in screen.titles[-1])",
        "def act(output, screen, master, folder):\n"
        "    def deeper():\n"
        "        return screen.titles\n"
        "    deeper()\n"
        "run(home, act=act)",
        "def end_it(output, screen, master, folder):\n    pass\n"
        "def end_it(output, screen, master, folder):\n    return screen.titles[-1]\n"
        "run(act=end_it)",
        "run(act=made.interrupt)",
        "run(act=elsewhere)",
    ],
)
def test_the_scan_finds_each_act_at_the_title_alone(source) -> None:
    assert _acts_at_the_title_alone(source), source


@pytest.mark.parametrize(
    "source",
    [
        "def interrupt(output, screen, master, folder):\n"
        "    if screen.titles and 'waiting for you' in _newest_frame(output):\n"
        "        os.write(master, b'x')\n"
        "run(act=interrupt)",
        "run(act=lambda output, screen, master, folder: len(ran.frames))",
        "def end_it(output, screen, master, folder):\n    return 'motd' in '\\n'.join(screen.lines)\nrun(act=end_it)",
        "run(argv, act=None)",
    ],
)
def test_the_scan_passes_an_act_that_reads_a_frame_or_no_title(source) -> None:
    assert _acts_at_the_title_alone(source) == [], source


@pytest.mark.parametrize(
    ("output", "newest"),
    [
        (b"", ""),
        (b"\x1b]0;deploy alpha \xc2\xb7 waiting for you\x07", ""),
        (SYNC_ON + b"one\x1b[K\n" + SYNC_OFF, "one\n"),
        (SYNC_ON + b"one" + SYNC_OFF + b"\x07" + SYNC_ON + b"two" + SYNC_OFF, "two"),
        # The newest still being drawn: none whole yet, its end still to come.
        (SYNC_ON + b"one" + SYNC_OFF + SYNC_ON + b"waiting for", ""),
    ],
    ids=["nothing", "a title alone", "one frame", "the newest of two", "one still drawn"],
)
def test_the_newest_frame_is_the_last_drawn_whole_and_none_while_one_is_drawn(output, newest) -> None:
    assert _newest_frame(output) == newest


def test_each_drawing_case_of_todays_parts_has_its_twin_here() -> None:
    here = {name for name in globals() if name.startswith("test_")}
    assert set(TWINS.values()) <= here, set(TWINS.values()) - here
    todays = REPO / "tests" / "unit" / "test_live_commands.py"
    # Until K14 removes it with today's parts, each case named is one of its own.
    if todays.is_file():
        tree = ast.parse(todays.read_text())
        names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        assert set(TWINS) <= names, set(TWINS) - names


def test_a_run_of_one_stage_is_drawn_as_its_steps_off_a_terminal_too(home, monkeypatch) -> None:
    only = Declared("deploy", (Stage("configure", "configure", steps=(Step("common", "common"),)),))
    events = [
        (0.0, canned._event("step", "alpha", "common", stage="configure")),
        (3.0, canned._event("step", "alpha", "common", stage="configure", outcome="done")),
        (3.0, canned._event("end", None, "", outcome="succeeded", exit=0)),
    ]
    said = _said(monkeypatch, events, declared=only)
    assert re.search(r"^✓ configure / common +3\.0 s$", said.out, re.M), said.out
    assert "agent-warehouse  deploy alpha · done in 3 s" in said.out
    assert re.search(r"^ ✓ common +3\.0 s$", said.out, re.M), said.out
