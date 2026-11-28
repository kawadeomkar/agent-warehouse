"""The live region every live output draws with: lib/claude_vm/tui/live_region.py, against a stand-in Display.

What is printed goes above the region and the region is drawn again below it; a Ctrl-C, a kill or a hangup between a
write and its count is held off till the count is right, each delivered after in the order it came, one ignored left
ignored and one at its default still ending the process; a frame taller than the screen is cut
and says how much; a prompt - pause's prompt_until or a vars_prompt's do_var_prompt - has the screen to itself; the
route sends what it is given to the log alone; a screen too small gets no region; the echo is off while it draws,
on a pseudo-terminal of the test's own, a change of it that a signal interrupts (EINTR) made again, never taken for no
terminal; and the Display it wraps is held to ansible-core's. Each live output's own
tests run it for real: test_status_live.py and test_deploy_live.py, at a pseudo-terminal
(docs/architecture/custom-code.md §7.8).
"""

from __future__ import annotations

import errno
import inspect
import io
import os
import re
import select
import signal
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

import pytest

from claude_vm.tui import live_region as live


class Display:
    """What LiveRegion needs of Ansible's Display: display, prompt_until, and the lock."""

    def __init__(self, stream):
        self._lock = threading.RLock()
        self.stream = stream
        self.logged = []

    def display(self, msg, color=None, stderr=False, screen_only=False, log_only=False, newline=True, caplevel=None):
        if not log_only:
            self.stream.write(msg + ("\n" if newline else ""))
        if not screen_only:
            self.logged.append(msg)

    def prompt_until(self, msg, private=False, seconds=None, interrupt_input=None, complete_input=None):
        self.display(msg, newline=False)
        return b"typed"

    def do_var_prompt(self, varname, private=True, prompt=None, **kwargs):
        self.stream.write(f"{prompt}: ")
        return "typed"


class Screen(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture(autouse=True)
def _quiet_region(monkeypatch):
    """An input that is no terminal - under pytest -s it would be the developer's, whose echo the region turns off -
    no spinner between two looks at the screen, and SIGWINCH's handler put back."""
    monkeypatch.setattr(live.sys, "stdin", io.StringIO())
    monkeypatch.setattr(live, "FRAME_SECONDS", 3600)
    handler = signal.getsignal(signal.SIGWINCH)
    yield
    signal.signal(signal.SIGWINCH, handler)


def _region(frame=None, route=None):
    stream = Screen()
    display = Display(stream)
    region = live.LiveRegion(display, frame=frame, route=route, stream=stream)
    region._terminal_size = lambda: (40, 10)
    return region, display, stream


def test_a_region_is_drawn_only_at_a_terminal_that_takes_utf8():
    class Pipe(io.StringIO):
        encoding = "utf-8"

    class Terminal(Screen):
        encoding = "UTF-8"

    class Latin(Screen):
        encoding = "latin-1"

    assert live.can_draw(Terminal(), {"TERM": "xterm-256color"})
    assert not live.can_draw(Pipe(), {"TERM": "xterm-256color"})
    assert not live.can_draw(Terminal(), {"TERM": "dumb"})
    assert not live.can_draw(Terminal(), {})
    assert not live.can_draw(Latin(), {"TERM": "xterm"})


def test_what_is_printed_goes_above_the_region_which_is_drawn_again_below_it():
    region, display, stream = _region(frame=lambda cols, rows, glyph: ["one", "two"])
    region.start()
    region.redraw()
    try:
        drawn = stream.getvalue()
        assert drawn == f"{live.SYNC_ON}\rone\x1b[K\ntwo\x1b[K\x1b[J{live.SYNC_OFF}"
        stream.seek(0)
        stream.truncate()
        display.display("said")
        # Up one line, to the region's first, erased below; the words; the region again.
        assert stream.getvalue() == f"\x1b[1A\r\x1b[Jsaid\n{live.SYNC_ON}\rone\x1b[K\ntwo\x1b[K\x1b[J{live.SYNC_OFF}"
        assert display.logged == ["said"]
    finally:
        region.finish()
    assert stream.getvalue().endswith("\x1b[1A\r\x1b[J")


@pytest.mark.parametrize("step", ["redraw", "finish"])
def test_a_ctrl_c_between_a_write_and_its_count_leaves_the_count_right(step):
    # A Ctrl-C after a frame (or its erase) reached the terminal and before its lines were counted left the count
    # stale: the erase that followed missed the region, which stayed on the screen above the run's last words. Here
    # the signal comes at that very moment, from the write itself, with a second thread alive as Ansible's results
    # thread is: the kernel may hand the signal to it, which is why a signal mask in the main thread held nothing off.
    region, display, stream = _region(frame=lambda cols, rows, glyph: ["one", "two", "three"])
    region.start()
    if step == "finish":
        region.redraw()
    written = region._write

    def write_then_interrupt(text):
        written(text)
        os.kill(os.getpid(), signal.SIGINT)

    # Python's own handler, whatever started the suite: under nohup SIGINT is ignored.
    held = signal.signal(signal.SIGINT, signal.default_int_handler)
    idle = threading.Event()
    other = threading.Thread(target=idle.wait, daemon=True)
    other.start()
    region._write = write_then_interrupt
    try:
        with pytest.raises(KeyboardInterrupt):
            getattr(region, step)()
    finally:
        region._write = written
        idle.set()
        other.join()
        signal.signal(signal.SIGINT, held)
    assert region._drawn == (3 if step == "redraw" else 0)
    region.finish()


class Ended(BaseException):
    """What a program's own handler of a kill or a hangup raises to end at once, as the new engine's run does
    (lib/claude_vm/runs/attached.py's Abandoned)."""


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP], ids=["SIGTERM", "SIGHUP"])
@pytest.mark.parametrize("step", ["redraw", "finish"])
def test_a_kill_or_a_hangup_between_a_write_and_its_count_leaves_the_count_right_too(step, signum):
    # A program that ends at once on SIGTERM or SIGHUP by raising from a handler of its own met the same gap as a
    # Ctrl-C: a frame left on the screen above the run's end, or lines said above it wiped by the next erase.
    region, display, stream = _region(frame=lambda cols, rows, glyph: ["one", "two", "three"])
    region.start()
    if step == "finish":
        region.redraw()
    written = region._write

    def write_then_signal(text):
        written(text)
        os.kill(os.getpid(), signum)

    def ended(number, _frame):
        raise Ended(number)

    held = signal.signal(signum, ended)
    idle = threading.Event()
    other = threading.Thread(target=idle.wait, daemon=True)
    other.start()
    region._write = write_then_signal
    try:
        with pytest.raises(Ended):
            getattr(region, step)()
    finally:
        region._write = written
        idle.set()
        other.join()
        signal.signal(signum, held)
    assert region._drawn == (3 if step == "redraw" else 0)
    region.finish()


def test_the_signals_a_write_held_off_are_each_delivered_after_it_in_the_order_they_came():
    region, display, stream = _region(frame=lambda cols, rows, glyph: ["one", "two", "three"])
    region.start()
    written = region._write
    came = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
    heard = []

    def write_then_signal(text):
        written(text)
        for signum in came:
            signal.raise_signal(signum)

    def handler(signum, _frame):
        heard.append((signum, region._drawn))
        if signum == signal.SIGTERM:
            raise Ended(signum)

    held = {signum: signal.signal(signum, handler) for signum in came}
    region._write = write_then_signal
    try:
        with pytest.raises(Ended):
            region.redraw()
    finally:
        region._write = written
        for signum, previous in held.items():
            signal.signal(signum, previous)
    # Each once the count was right, in the order it came, the one after the SIGTERM's end too; that end raised last.
    assert heard == [(signal.SIGHUP, 3), (signal.SIGTERM, 3), (signal.SIGINT, 3)]
    region.finish()


def test_a_signal_ignored_stays_ignored_through_a_write():
    # As nohup leaves SIGHUP: nothing of the region's takes it in the meantime, and it ends nothing after.
    region, display, stream = _region(frame=lambda cols, rows, glyph: ["one"])
    region.start()
    written = region._write
    during = []

    def write_then_hang_up(text):
        written(text)
        during.append(signal.getsignal(signal.SIGHUP))
        signal.raise_signal(signal.SIGHUP)

    held = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    region._write = write_then_hang_up
    try:
        region.redraw()
    finally:
        region._write = written
        signal.signal(signal.SIGHUP, held)
    assert during == [signal.SIG_IGN] and region._drawn == 1
    region.finish()


# A region in a process of its own whose SIGTERM is at its default, as today's runs leave it: the kill comes during a
# write, and the process lives to finish that write and count it, then dies of the kill before anything more.
AT_ITS_DEFAULT = r"""
import io, os, signal, sys, threading
sys.path.insert(0, sys.argv[1])
from claude_vm.tui import live_region as live

signal.signal(signal.SIGTERM, signal.SIG_DFL)


class Screen(io.StringIO):
    def isatty(self):
        return True


class Display:
    def __init__(self):
        self._lock = threading.RLock()

    def display(self, msg, **kwargs):
        pass

    def prompt_until(self, *args, **kwargs):
        pass

    def do_var_prompt(self, *args, **kwargs):
        pass


region = live.LiveRegion(Display(), frame=lambda cols, rows, glyph: ["one"], stream=Screen())
region._terminal_size = lambda: (40, 10)
region.start()
written = region._write


def write_then_kill(text):
    written(text)
    os.kill(os.getpid(), signal.SIGTERM)
    os.write(2, b"the write ended\n")


region._write = write_then_kill
region.redraw()
os.write(2, b"the redraw returned\n")
"""


def test_a_kill_at_its_default_still_ends_the_process_once_the_write_is_counted():
    lib = str(Path(live.__file__).parents[2])
    ran = subprocess.run(
        [sys.executable, "-I", "-c", AT_ITS_DEFAULT, lib],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert (ran.returncode, ran.stderr) == (-signal.SIGTERM, b"the write ended\n"), ran


def test_a_frame_taller_than_the_screen_is_cut_and_says_how_much():
    region, _, stream = _region(frame=lambda cols, rows, glyph: [f"line {n}" for n in range(20)])
    region.start()
    region.redraw()
    region.finish()
    drawn = stream.getvalue().split(live.SYNC_ON, 1)[1].split(live.SYNC_OFF, 1)[0]
    lines = drawn.replace("\x1b[K", "").replace("\x1b[J", "").lstrip("\r").split("\n")
    assert lines == [*(f"line {n}" for n in range(7)), "⋮ 12 more lines", "line 19"]


def test_a_prompt_has_the_screen_to_itself_and_the_region_comes_back_after():
    region, display, stream = _region(frame=lambda cols, rows, glyph: ["region"])
    seen = []

    def prompt_until(msg, **kwargs):
        seen.append((stream.getvalue(), region.prompting))
        region.redraw()  # the spinner's: nothing while the prompt is up
        seen.append(stream.getvalue())
        return b"typed"

    display.prompt_until = prompt_until
    region.start()
    region.redraw()
    try:
        assert display.prompt_until("Code:") == b"typed"
        # Back only once the run has worked half a second: the next question, at once, finds nothing drawn.
        region.redraw()
        settled = stream.getvalue()
        time.sleep(live.SETTLE_SECONDS)
        region.redraw()
    finally:
        region.finish()
    (before, prompting), during = seen
    assert prompting and before.endswith("\r\x1b[J") and during == before and settled == before
    assert stream.getvalue()[len(before) :].startswith(f"{live.SYNC_ON}\rregion")


def test_the_route_sends_to_the_log_alone_and_never_a_prompt():
    region, display, stream = _region(frame=None, route=lambda kwargs: {**kwargs, "log_only": True})
    region.start()
    region.redraw()
    display.display("task output")
    assert stream.getvalue() == "" and display.logged == ["task output"]
    assert display.prompt_until("Code:") == b"typed"
    assert stream.getvalue() == "Code:"
    region.finish()
    display.display("after the end")
    assert display.logged[-1] == "after the end" and "after the end" not in stream.getvalue()


# 0 by 0: a pseudo-terminal nobody gave a size, as script(1) makes one without a terminal of its own.
@pytest.mark.parametrize("size", [(40, 3), (40, 1), (0, 0)])
def test_a_region_on_a_screen_too_small_for_one_draws_nothing(size):
    region, _, stream = _region(frame=lambda cols, rows, glyph: ["one", "two"])
    region._terminal_size = lambda: size
    region.start()
    region.redraw()
    region.finish()
    assert "one" not in stream.getvalue()


def test_a_region_steps_aside_for_a_vars_prompt_too():
    region, display, stream = _region(frame=lambda cols, rows, glyph: ["region"])
    seen = []
    display.do_var_prompt = lambda *args, **kwargs: seen.append((stream.getvalue(), region.prompting)) or "typed"
    region.start()
    region.redraw()
    try:
        assert display.do_var_prompt("confirm_destroy", prompt="Delete these VMs?") == "typed"
    finally:
        region.finish()
    before, prompting = seen[0]
    assert prompting and before.endswith("\r\x1b[J")


def test_the_region_turns_the_echo_off_while_it_draws_and_on_for_a_prompt_and_at_the_end(monkeypatch):
    # A pseudo-terminal of the test's own, never the developer's.
    master, slave = os.openpty()
    terminal = os.fdopen(slave, "r")
    monkeypatch.setattr(live.sys, "stdin", terminal)
    echoes = lambda: bool(termios.tcgetattr(slave)[3] & termios.ECHO)  # noqa: E731
    try:
        assert echoes()
        region, display, _ = _region(frame=lambda cols, rows, glyph: ["region"])
        during = []
        display.prompt_until = lambda *args, **kwargs: during.append(echoes()) or b"typed"
        region.start()
        region.redraw()
        assert not echoes()
        display.prompt_until("Code:")
        assert during == [True] and not echoes()
        region.finish()
        assert echoes()
    finally:
        terminal.close()
        os.close(master)


def test_a_change_of_the_echo_a_signal_interrupts_is_made_again_never_taken_for_no_terminal(monkeypatch):
    # TCSADRAIN waits for the output to drain, and a signal whose handler raises nothing - a first Ctrl-C, a hold's
    # note, a resize - ends the wait with EINTR, as termios.error gives it: the terminal is still there, and the echo
    # still the region's to put back. Here every change of the settings meets one EINTR first.
    master, slave = os.openpty()
    terminal = os.fdopen(slave, "r")
    monkeypatch.setattr(live.sys, "stdin", terminal)
    echoes = lambda: bool(termios.tcgetattr(slave)[3] & termios.ECHO)  # noqa: E731
    made = termios.tcsetattr
    tried = []

    def interrupted(fd, when, settings):
        tried.append(when)
        if len(tried) % 2:
            raise termios.error(errno.EINTR, "Interrupted system call")
        made(fd, when, settings)

    monkeypatch.setattr(live.termios, "tcsetattr", interrupted)
    try:
        region, display, _ = _region(frame=lambda cols, rows, glyph: ["region"])
        during = []
        display.prompt_until = lambda *args, **kwargs: during.append(echoes()) or b"typed"
        region.start()
        assert not echoes()
        # A program's own question, and a prompt through the wrapper: the echo on while each is up, off after.
        with region.aside():
            during.append(echoes())
        display.prompt_until("Code:")
        assert during == [True, True] and not echoes()
        region.finish()
        assert echoes()
        # Each change tried twice, the EINTR and then the change: none left undone.
        assert tried == [termios.TCSADRAIN] * 12, tried
    finally:
        terminal.close()
        os.close(master)


@pytest.mark.parametrize("kept", [False, True], ids=["found without NOFLSH", "found with NOFLSH"])
def test_while_it_draws_the_terminal_keeps_its_output_at_an_interrupt_and_its_settings_go_back_as_found(
    monkeypatch, kept
):
    # NOFLSH beside the echo off while the region draws: at a Ctrl-C the terminal keeps what it has not read yet. For a
    # prompt and at the end, the settings exactly as found, NOFLSH as it was.
    master, slave = os.openpty()
    terminal = os.fdopen(slave, "r")
    monkeypatch.setattr(live.sys, "stdin", terminal)
    try:
        given = termios.tcgetattr(slave)
        given[3] = given[3] | termios.NOFLSH if kept else given[3] & ~termios.NOFLSH
        termios.tcsetattr(slave, termios.TCSANOW, given)
        found = termios.tcgetattr(slave)
        region, display, _ = _region(frame=lambda cols, rows, glyph: ["region"])
        during = []
        display.prompt_until = lambda *args, **kwargs: during.append(termios.tcgetattr(slave)) or b"typed"
        region.start()
        region.redraw()
        drawing = termios.tcgetattr(slave)
        assert drawing[3] == (found[3] & ~termios.ECHO) | termios.NOFLSH
        assert drawing[:3] + drawing[4:] == found[:3] + found[4:]
        display.prompt_until("Code:")
        assert during == [found] and termios.tcgetattr(slave) == drawing
        region.finish()
        assert termios.tcgetattr(slave) == found
    finally:
        terminal.close()
        os.close(master)


# How many lines the case below says above the region before its Ctrl-C, and how long it waits for them, reading
# nothing: a writer that waits for the terminal's reader, once the terminal holds as much unread as it takes, has said
# fewer, which wait unread the same.
LINES_SAID, SAYING = 300, 1.5


def test_a_ctrl_c_before_the_terminal_reads_the_lines_said_above_the_region_loses_none_of_them(monkeypatch):
    # 300 lines said above the region with nobody reading the terminal yet, and then a Ctrl-C typed: at an interrupt a
    # terminal throws away what it holds unread unless NOFLSH - Linux what it holds past its line discipline's buffer,
    # macOS all it holds. Every line is read after, once and in order. No process has this terminal for its own, so
    # the Ctrl-C signals nobody: the throwing away is the terminal's alone.
    master, slave = os.openpty()
    terminal = os.fdopen(slave, "r")
    screen = os.fdopen(os.dup(slave), "w", encoding="utf-8")
    monkeypatch.setattr(live.sys, "stdin", terminal)
    interrupted = []
    handler = signal.signal(signal.SIGINT, lambda *_: interrupted.append(True))
    display = Display(screen)
    region = live.LiveRegion(display, frame=lambda cols, rows, glyph: ["region one", "region two"], stream=screen)
    region._terminal_size = lambda: (80, 24)
    read = bytearray()

    def drain(quiet):
        # What the terminal holds, until it has held nothing for quiet seconds.
        while select.select([master], [], [], quiet)[0]:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                return
            if not chunk:
                return
            read.extend(chunk)

    def say():
        for number in range(1, LINES_SAID + 1):
            display.display(f"line {number:05}")

    try:
        found = termios.tcgetattr(slave)
        region.start()
        region.redraw()
        writer = threading.Thread(target=say, daemon=True)
        writer.start()
        writer.join(SAYING)
        os.write(master, b"\x03")
        while writer.is_alive():
            drain(0.1)
        writer.join()
        # finish() makes a draining change of the settings with the erase still unread here: macOS waits for that
        # read (ttywait), so the master is read on while it runs.
        ender = threading.Thread(target=region.finish, daemon=True)
        ender.start()
        while ender.is_alive():
            drain(0.1)
        ender.join()
        drain(0.3)
        assert termios.tcgetattr(slave) == found
    finally:
        signal.signal(signal.SIGINT, handler)
        screen.close()
        terminal.close()
        os.close(master)
    said = [int(number) for number in re.findall(rb"line (\d{5})\r\n", bytes(read))]
    lost = sorted(set(range(1, LINES_SAID + 1)) - set(said))
    assert said == list(range(1, LINES_SAID + 1)), f"{len(lost)} lost, the first {lost[:5]}"
    assert interrupted == []


def test_a_change_of_the_settings_that_fails_otherwise_goes_through():
    # Only EINTR is tried again: a descriptor that is no terminal is termios.error as it was.
    master, slave = os.openpty()
    read, write = os.pipe()
    try:
        settings = termios.tcgetattr(slave)
        with pytest.raises(termios.error) as failed:
            live.set_terminal(read, termios.TCSANOW, settings)
        assert failed.value.args[0] == errno.ENOTTY
    finally:
        for fd in (master, slave, read, write):
            os.close(fd)


def test_a_title_is_written_when_it_changes_and_a_bell_when_asked():
    region, _, stream = _region(frame=lambda cols, rows, glyph: [])
    region.title("deploy alpha")
    region.title("deploy alpha")
    region.bell()
    assert stream.getvalue() == "\x1b]0;deploy alpha\x07\x07"
    quiet, _, quiet_stream = _region(frame=None)
    quiet.title("deploy alpha")
    quiet.bell()
    assert quiet_stream.getvalue() == ""


def test_the_display_the_region_wraps_is_the_one_it_was_built_against():
    # The region replaces display and prompt_until and takes _lock (live_region.py): a prompt prints through
    # display before it locks to read the keys, which is why the region can step aside first.
    from ansible.utils.display import Display

    parameters = list(inspect.signature(Display.display).parameters)
    assert parameters == ["self", "msg", "color", "stderr", "screen_only", "log_only", "newline", "caplevel"]
    source = inspect.getsource(Display.prompt_until)
    assert "self._final_q.send_prompt(" in source, "a worker's prompt is the main process's to print"
    assert source.index("self.display(msg)") < source.index("with self._lock:")
    # A vars_prompt reads with input(), through the prompt its do_var_prompt looks up each time.
    assert "do_prompt = self.prompt" in inspect.getsource(Display.do_var_prompt)
    assert "input(msg)" in inspect.getsource(Display.prompt)
    assert isinstance(Display()._lock, type(threading.RLock()))


def _output(**kwargs):
    stream = Screen()
    display = Display(stream)
    out = live.Output(display, live=False, **kwargs)
    return out, display, stream


def test_an_outputs_own_words_go_coloured_to_the_screen_and_plain_to_the_log():
    out, display, stream = _output(color=True, to_log=True)
    out.start()
    # Without a region there is no colour: a pipe gets the plain words, once, on the screen and in the log.
    assert out.color is False
    out.print([("\x1b[1msaid\x1b[0m", "said")])
    assert stream.getvalue() == "said\n" and display.logged == ["said"]
    out.finish()
    coloured = live.Output(Display(Screen()), frame=lambda cols, rows, glyph: [], live=True, color=True)
    assert coloured.color is True and coloured.region._frame is not None


def test_with_to_log_only_the_outputs_own_words_and_stderr_reach_the_screen():
    out, display, stream = _output(to_log=True)
    out.start()
    display.display("a task's line")
    display.display("a warning", stderr=True)
    out.print([("the run's own", "the run's own")])
    with out.say():
        display.display("said inside")
    out.finish()
    assert stream.getvalue() == "a warning\nthe run's own\nsaid inside\n"
    assert display.logged == ["a task's line", "a warning", "the run's own", "said inside"]


def test_without_to_log_everything_reaches_the_screen():
    out, display, stream = _output()
    out.start()
    display.display("a line")
    out.finish()
    assert stream.getvalue() == "a line\n" and display.logged == ["a line"]
