"""The live region every live output draws with: lib/claude_vm/tui/live_region.py, against a stand-in Display.

What is printed goes above the region and the region is drawn again below it; a Ctrl-C, a kill, a hangup, a Ctrl-Z or a
Ctrl-\\ between a write and its count is held off till the count is right, each delivered after in the order it came,
one ignored left ignored and one at its default still ending the process; a frame taller than the screen is cut and says
how much; a prompt - pause's prompt_until or a vars_prompt's do_var_prompt - has the screen to itself; the route sends
what it is given to the log alone; a screen too small gets no region; the echo is off while it draws, on a
pseudo-terminal of the test's own, a change of it that a signal interrupts (EINTR) made again, never taken for no
terminal; at a run's end NOFLSH kept till its last words are said, then the settings exactly as found; a Ctrl-C while it
draws throwing away what was typed unseen and handing the signal on; in the background no change of the settings made
and none of the shell's read as the ones to put back; a signal that lands while the settings go back delivered once they
are back, a Ctrl-Z coming back to them; a Ctrl-Z and a Ctrl-\\ at their default taken while it draws - the region
erased, none drawn till the run goes on, and the settings put back as found, what was typed unseen thrown away with
them, before the stop or the quit; after a stop the mode again - a handler of the program's own left in place, each
given back at the end, the one it took where it ends with the signals held off; from an interactive shell
(harness._in_a_shell), nothing typed unseen left on the shell's line or in its history at either; and the Display it
wraps is held to ansible-core's. Each live output's own tests run it for real: test_status_live.py and
test_deploy_live.py, at a pseudo-terminal (docs/architecture/custom-code.md §7.8).
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

import harness
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


@pytest.mark.parametrize(
    "signum",
    [signal.SIGTERM, signal.SIGHUP, signal.SIGTSTP, signal.SIGQUIT],
    ids=["SIGTERM", "SIGHUP", "SIGTSTP", "SIGQUIT"],
)
@pytest.mark.parametrize("step", ["redraw", "finish"])
def test_a_kill_or_a_hangup_between_a_write_and_its_count_leaves_the_count_right_too(step, signum):
    # A program that ends at once on SIGTERM or SIGHUP by raising from a handler of its own met the same gap as a
    # Ctrl-C: a frame left on the screen above the run's end, or lines said above it wiped by the next erase. A Ctrl-Z
    # and a Ctrl-\ are held off the same way, whoever takes them.
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


def _at_a_terminal_of_its_own(monkeypatch):
    """A pseudo-terminal of the test's own as the region's standard input: (master, slave, its file)."""
    master, slave = os.openpty()
    terminal = os.fdopen(slave, "r")
    monkeypatch.setattr(live.sys, "stdin", terminal)
    return master, slave, terminal


@pytest.mark.parametrize("noflsh", [False, True], ids=["found without NOFLSH", "found with NOFLSH"])
def test_at_a_runs_end_noflsh_is_kept_till_its_last_words_are_said_then_the_settings_go_back_as_found(
    monkeypatch, noflsh
):
    # finish(kept=True): the region gone and the echo back, but NOFLSH kept on, so that a Ctrl-C the terminal has not
    # read the run's summary and NEXT STEPS by throws none of them away; release(): the settings exactly as found, once.
    master, slave, terminal = _at_a_terminal_of_its_own(monkeypatch)
    try:
        given = termios.tcgetattr(slave)
        given[3] = given[3] | termios.NOFLSH if noflsh else given[3] & ~termios.NOFLSH
        termios.tcsetattr(slave, termios.TCSANOW, given)
        found = termios.tcgetattr(slave)
        region, _, _ = _region(frame=lambda cols, rows, glyph: ["region"])
        region.start()
        region.finish(kept=True)
        kept = termios.tcgetattr(slave)
        assert kept[3] == found[3] | termios.NOFLSH, (kept[3], found[3])
        assert kept[:3] + kept[4:] == found[:3] + found[4:]
        region.release()
        assert termios.tcgetattr(slave) == found
        # Nothing a second time: a change made since is left as it is.
        termios.tcsetattr(slave, termios.TCSANOW, kept)
        region.release()
        assert termios.tcgetattr(slave) == kept
    finally:
        terminal.close()
        os.close(master)


@pytest.mark.parametrize("handler", ["Python's", "a program's own"])
def test_a_ctrl_c_while_the_region_draws_throws_away_what_was_typed_unseen_and_hands_the_signal_on(
    monkeypatch, handler
):
    # NOFLSH keeps at a Ctrl-C what the terminal has not written; the kernel then keeps what was typed too, which its
    # own Ctrl-C threw away. The hold of the terminal throws it away, then hands the signal to the handler that was in
    # place (Python's raises KeyboardInterrupt), which is in place again once the region is gone.
    master, slave, terminal = _at_a_terminal_of_its_own(monkeypatch)
    heard = []
    own = signal.default_int_handler if handler == "Python's" else (lambda signum, frame: heard.append(signum))
    previous = signal.signal(signal.SIGINT, own)
    try:
        region, _, _ = _region(frame=lambda cols, rows, glyph: ["region"])
        region.start()
        os.write(master, b"typed unseen\n")
        assert select.select([slave], [], [], 5)[0], "the line never reached the terminal's input"
        if handler == "Python's":
            with pytest.raises(KeyboardInterrupt):
                signal.raise_signal(signal.SIGINT)
        else:
            signal.raise_signal(signal.SIGINT)
            assert heard == [signal.SIGINT]
        assert not select.select([slave], [], [], 0.3)[0], "what was typed is still there to read"
        region.finish()
        assert signal.getsignal(signal.SIGINT) is own
    finally:
        signal.signal(signal.SIGINT, previous)
        terminal.close()
        os.close(master)


def test_in_the_background_the_region_neither_changes_the_terminals_settings_nor_takes_the_shells_as_its_own(
    monkeypatch,
):
    # A shell put the run in the background (Ctrl-Z, then bg): the settings are the shell's, and a change of them would
    # stop the run (SIGTTOU) or change the shell's under it. The region changes nothing there, and reads none of them as
    # the settings to put back.
    master, slave, terminal = _at_a_terminal_of_its_own(monkeypatch)
    group = {"of": os.getpgrp()}
    monkeypatch.setattr(os, "tcgetpgrp", lambda fd: group["of"])
    made = termios.tcsetattr
    changes = []

    def recorded(fd, when, settings):
        changes.append((when, settings[3]))
        made(fd, when, settings)

    try:
        found = termios.tcgetattr(slave)
        region, display, _ = _region(frame=lambda cols, rows, glyph: ["region"])
        region.start()
        quiet = termios.tcgetattr(slave)
        assert not quiet[3] & termios.ECHO
        monkeypatch.setattr(live.termios, "tcsetattr", recorded)
        group["of"] = os.getpgrp() + 1
        display.prompt_until("Code:")
        region.finish()
        assert changes == [] and termios.tcgetattr(slave) == quiet
        # Started in the background, where a shell's line editor has its own settings up: none read as the run's,
        # so none put back once the run is the foreground again.
        made(slave, termios.TCSANOW, found)
        shells = [found[0], found[1], found[2], found[3] & ~(termios.ICANON | termios.ECHO), *found[4:]]
        made(slave, termios.TCSANOW, shells)
        later, _, _ = _region(frame=lambda cols, rows, glyph: ["region"])
        later.start()
        made(slave, termios.TCSANOW, found)
        group["of"] = os.getpgrp()
        later.finish()
        assert changes == [] and termios.tcgetattr(slave) == found
    finally:
        terminal.close()
        os.close(master)


@pytest.mark.parametrize(
    "signum", [signal.SIGINT, signal.SIGTERM, signal.SIGTSTP], ids=["a second Ctrl-C", "a SIGTERM", "a Ctrl-Z"]
)
def test_a_signal_that_lands_while_the_settings_go_back_is_delivered_once_they_are_back(monkeypatch, signum):
    # The change back is made with the signals a program ends or stops on held off: one whose handler raises, landing
    # in the middle of it, is delivered once it is made, never leaving the echo off for the shell; a Ctrl-Z, once the
    # run goes on, comes back to the settings put back, never to the mode they replaced.
    master, slave, terminal = _at_a_terminal_of_its_own(monkeypatch)
    made = termios.tcsetattr
    landed = []
    stops = []

    def landing(fd, when, settings):
        if settings[3] & termios.ECHO and not landed:
            landed.append(when)
            signal.raise_signal(signum)
        made(fd, when, settings)

    def ends(signum, frame):
        raise SystemExit(128 + signum)

    def stopped(number):
        # The stop, at the stop's default: the shell's line editor has its own settings up meanwhile.
        stops.append(termios.tcgetattr(slave))
        shells = termios.tcgetattr(slave)
        shells[3] &= ~(termios.ICANON | termios.ECHO)
        made(slave, termios.TCSANOW, shells)

    handlers = {signal.SIGINT: signal.default_int_handler, signal.SIGTERM: ends, signal.SIGTSTP: signal.SIG_DFL}
    previous = signal.signal(signum, handlers[signum])
    monkeypatch.setattr(live, "_at_default", stopped)
    try:
        found = termios.tcgetattr(slave)
        region, _, _ = _region(frame=lambda cols, rows, glyph: ["region"])
        region.start()
        monkeypatch.setattr(live.termios, "tcsetattr", landing)
        if signum == signal.SIGTSTP:
            # Taken, or at its default it would stop pytest.
            assert signal.getsignal(signum) == live._HOLD._stopped
            region.finish()
            assert stops == [found]
        else:
            with pytest.raises(KeyboardInterrupt if signum == signal.SIGINT else SystemExit):
                region.finish()
        assert landed and termios.tcgetattr(slave) == found
    finally:
        signal.signal(signum, previous)
        terminal.close()
        os.close(master)


# What the hold of the terminal takes from their default while the region draws, and the shell's own signal of a run
# put back in the foreground.
STOP_AND_QUIT = (signal.SIGTSTP, signal.SIGCONT, signal.SIGQUIT)


@pytest.mark.parametrize("signum", [signal.SIGTSTP, signal.SIGQUIT], ids=["a Ctrl-Z", "a Ctrl-\\"])
def test_a_ctrl_z_or_a_ctrl_backslash_while_the_region_draws_puts_the_terminal_back_as_found_before_its_default(
    monkeypatch, signum
):
    # Each at its default, taken while the region holds the terminal: the region erased and none drawn after, the
    # settings found put back and what was typed unseen thrown away with them, and then the default - here a stand-in,
    # the shell's settings up meanwhile. After a stop, once the run goes on: the mode again, and the region drawn.
    master, slave, terminal = _at_a_terminal_of_its_own(monkeypatch)
    defaults = {number: signal.signal(number, signal.SIG_DFL) for number in STOP_AND_QUIT}
    taken = []

    def at_default(number):
        region.redraw()
        taken.append((number, termios.tcgetattr(slave), bool(select.select([slave], [], [], 0)[0]), region._drawn))
        shells = termios.tcgetattr(slave)
        shells[3] &= ~(termios.ICANON | termios.ECHO)
        termios.tcsetattr(slave, termios.TCSANOW, shells)
        if number == signal.SIGQUIT:
            raise Ended(number)

    monkeypatch.setattr(live, "_at_default", at_default)
    try:
        found = termios.tcgetattr(slave)
        region, _, stream = _region(frame=lambda cols, rows, glyph: ["region"])
        region.start()
        quiet = termios.tcgetattr(slave)
        region.redraw()
        assert region._drawn == 1
        os.write(master, b"typed unseen\n")
        assert select.select([slave], [], [], 5)[0], "the line never reached the terminal's input"
        # Taken, or at its default it would stop or end pytest.
        own = {signal.SIGTSTP: live._HOLD._stopped, signal.SIGQUIT: live._HOLD._quit}
        assert signal.getsignal(signum) == own[signum]
        if signum == signal.SIGQUIT:
            with pytest.raises(Ended):
                signal.raise_signal(signum)
        else:
            signal.raise_signal(signum)
        assert taken == [(signum, found, False, 0)], taken
        assert stream.getvalue().endswith("\r\x1b[J"), stream.getvalue()
        if signum == signal.SIGTSTP:
            assert termios.tcgetattr(slave) == quiet
            region.redraw()
            assert region._drawn == 1
        region.finish()
        assert termios.tcgetattr(slave) == found
    finally:
        for number, handler in defaults.items():
            signal.signal(number, handler)
        terminal.close()
        os.close(master)


def test_the_hold_takes_a_ctrl_z_its_continue_and_a_ctrl_backslash_only_from_their_default_and_gives_them_back(
    monkeypatch,
):
    # A handler of the program's own - the new engine's ladder takes SIGQUIT as it takes SIGTERM - is left in place.
    master, slave, terminal = _at_a_terminal_of_its_own(monkeypatch)

    def own(signum, frame):
        pass

    previous = {number: signal.signal(number, signal.SIG_DFL) for number in STOP_AND_QUIT}
    signal.signal(signal.SIGQUIT, own)
    try:
        region, _, _ = _region(frame=lambda cols, rows, glyph: ["region"])
        region.start()
        held = {number: signal.getsignal(number) for number in STOP_AND_QUIT}
        assert held == {
            signal.SIGTSTP: live._HOLD._stopped,
            signal.SIGCONT: live._HOLD._continued,
            signal.SIGQUIT: own,
        }, held
        region.finish()
        back = {number: signal.getsignal(number) for number in STOP_AND_QUIT}
        assert back == {signal.SIGTSTP: signal.SIG_DFL, signal.SIGCONT: signal.SIG_DFL, signal.SIGQUIT: own}, back
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        terminal.close()
        os.close(master)


def test_a_hold_given_back_while_the_signals_are_held_off_puts_back_the_handlers_it_took(monkeypatch):
    # As a question's end gives it back (runs/ask.py's _line): the settings put back as found inside uninterrupted,
    # whose notes stand in place of the hold's handlers meanwhile. Once the signals are let through, each handler the
    # hold took is back - the program's own Ctrl-C, the defaults of the rest - never the hold's own.
    master, slave, terminal = _at_a_terminal_of_its_own(monkeypatch)

    def own(signum, frame):
        pass

    previous = {number: signal.signal(number, signal.SIG_DFL) for number in STOP_AND_QUIT}
    previous[signal.SIGINT] = signal.signal(signal.SIGINT, own)
    try:
        found = termios.tcgetattr(slave)
        mode = [*found[:3], found[3] & ~termios.ECHO, *found[4:]]
        live.change(slave, termios.TCSAFLUSH, mode)
        assert signal.getsignal(signal.SIGINT) == live._HOLD._interrupted
        with live.uninterrupted():
            live.put_back(slave, termios.TCSAFLUSH, found)
        handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, *STOP_AND_QUIT)}
        assert handlers == {
            signal.SIGINT: own,
            signal.SIGTSTP: signal.SIG_DFL,
            signal.SIGCONT: signal.SIG_DFL,
            signal.SIGQUIT: signal.SIG_DFL,
        }, handlers
        assert termios.tcgetattr(slave) == found
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        terminal.close()
        os.close(master)


# A region drawn, its spinner going, at the terminal of a shell's job that a signal ends.
DRAWS = r"""
import sys, threading, time
sys.path.insert(0, sys.argv[1])
from claude_vm.tui import live_region as live


class Display:
    def __init__(self):
        self._lock = threading.RLock()

    def display(self, msg, **kwargs):
        pass

    def prompt_until(self, *args, **kwargs):
        pass

    def do_var_prompt(self, *args, **kwargs):
        pass


region = live.LiveRegion(Display(), frame=lambda cols, rows, glyph: ["a frame of the region"])
region.start()
region.spin()
while True:
    time.sleep(1)
"""
FRAME = b"a frame of the region"
# Keys typed unseen while the region draws: before its Ctrl-Z, and before its Ctrl-\.
BLIND = (b"blind42x", b"blind43x")
# The region's erase of its one line.
ERASE = b"\r\x1b[J"
STATUSES = re.compile(rb"\b(stopped|ended)=(\d+)")


def test_from_a_shell_a_ctrl_z_and_a_ctrl_backslash_while_the_region_draws_leave_nothing_typed_for_the_shell(
    tmp_path,
):
    # Keys typed unseen while the region draws, then a Ctrl-Z: the region erased before the stop, the keys on neither
    # the shell's line, where it would run them, nor in its history; fg, the echo off and NOFLSH on again and the region
    # drawn below the shell's lines; keys again, then a Ctrl-\, the region erased before the run dies of it, the keys
    # thrown away as at a Ctrl-C.
    (tmp_path / "draws.py").write_text(DRAWS)
    lib = str(Path(live.__file__).parents[2])
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "TERM": "xterm-256color",
        "PY": sys.executable,
        "LIB": lib,
    }
    mode = []

    def quiet_again(master, output):
        waited = time.monotonic() + 5
        while True:
            lflag = termios.tcgetattr(master)[3]
            if (not lflag & termios.ECHO and lflag & termios.NOFLSH) or time.monotonic() > waited:
                break
            time.sleep(0.05)
        mode.append(lflag)
        os.write(master, BLIND[1])
        time.sleep(0.2)
        os.write(master, b"\x1c")

    prompt = harness.SHELL_PROMPT
    steps = [
        (prompt, b"ulimit -c 0\n"),
        (prompt, b'"$PY" -I -X utf8 draws.py "$LIB"\n'),
        (FRAME, [BLIND[0], b"\x1a"]),
        (b"Stopped", []),
        (prompt, b'echo "stopped=$?"\n'),
        (prompt, b"fg\n"),
        (FRAME, quiet_again),
        (b"Quit", []),
        (prompt, b'echo "ended=$?"\n'),
        (prompt, b"exit\n"),
    ]
    output, history = harness._in_a_shell(tmp_path, env, steps, timeout=60)
    said = output.decode(errors="replace")
    assert b"command not found" not in output, said
    for word in BLIND:
        assert word not in history, history
    statuses = [(name.decode(), int(number)) for name, number in STATUSES.findall(output)]
    assert statuses == [("stopped", 128 + signal.SIGTSTP), ("ended", 128 + signal.SIGQUIT)], said
    for end in (b"Stopped", b"Quit"):
        at = output.find(end)
        assert output.rfind(ERASE, 0, at) > output.rfind(FRAME, 0, at), said
    (lflag,) = mode
    assert lflag & termios.ICANON and not lflag & termios.ECHO and lflag & termios.NOFLSH, lflag


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


class _Ended(Exception):
    """The run's end at once, as the ladder raises it (Abandoned)."""


class _Stopped(Exception):
    """A stop of the question's own (SIGTTOU at its default), stood in for."""


def test_a_question_the_shell_goes_on_with_in_the_background_lets_an_end_another_thread_took_land_first(monkeypatch):
    # A shell's kill of a run stopped at a question (kill %1): SIGTERM, then SIGCONT. The kernel may hand the SIGTERM to
    # a thread other than the main one, whose handler passes it on to Python only once that thread runs; here it lands
    # 50 ms after the SIGCONT, from a thread of the test's. The question goes on in the background: in the wait before
    # it stops there of its own (ENDING_GRACE), the run's handler of the end runs - the ladder's, which tells the hold
    # (live.ending) and raises - and neither a stop nor a change is made: a stop would keep the run for good.
    master, slave = os.openpty()
    found = termios.tcgetattr(slave)
    landed: list[int] = []
    made: list[tuple] = []
    stops: list[int] = []

    def stopped(signum):
        stops.append(signum)
        raise _Stopped

    def ended(signum, _frame):
        landed.append(signum)
        live.ending()
        if len(landed) == 1:
            raise _Ended

    def late():
        time.sleep(0.05)
        signal.pthread_kill(threading.get_ident(), signal.SIGTERM)

    previous = signal.signal(signal.SIGTERM, ended)
    try:
        mode = list(found)
        mode[3] &= ~termios.ECHO
        live.change(slave, termios.TCSAFLUSH, mode, wait=True)
        with monkeypatch.context() as patch:
            patch.setattr(os, "tcgetpgrp", lambda fd: os.getpgrp() + 1)
            patch.setattr(live, "set_terminal", lambda *args: made.append(args))
            patch.setattr(live, "_at_default", stopped)
            patch.setattr(live, "_parent_in_session", lambda: True)
            thread = threading.Thread(target=late)
            thread.start()
            try:
                with pytest.raises(_Ended):
                    live._HOLD._again(stop=True)
            finally:
                while thread.is_alive():
                    try:
                        thread.join()
                    except _Ended:
                        pass
        assert landed == [signal.SIGTERM] and made == [] and stops == [], (landed, made, stops)
    finally:
        signal.signal(signal.SIGTERM, previous)
        live.put_back(slave, termios.TCSANOW, found, last=True)
        os.close(master)
        os.close(slave)


def test_a_question_in_the_background_is_stopped_by_the_holds_ctrl_z_and_never_by_the_handler_of_its_continue(
    monkeypatch,
):
    # Python runs a handler still due at the first line of another's: a shell's kill of a run stopped at its question
    # (kill %1: SIGTERM, then SIGCONT) runs the SIGCONT's handler before the SIGTERM's has done anything. In the
    # background that handler neither stops the run nor changes the terminal - a stop there kept the kill's handler,
    # and the end, from ever going on (review 68's F8) - and the hold's own Ctrl-Z is what stops the question again.
    master, slave = os.openpty()
    found = termios.tcgetattr(slave)
    made: list[tuple] = []
    stops: list[int] = []

    def stopped(signum):
        stops.append(signum)
        raise _Stopped

    try:
        mode = list(found)
        mode[3] &= ~termios.ECHO
        live.change(slave, termios.TCSAFLUSH, mode, wait=True)
        with monkeypatch.context() as patch:
            patch.setattr(os, "tcgetpgrp", lambda fd: os.getpgrp() + 1)
            patch.setattr(live, "set_terminal", lambda *args: made.append(args))
            patch.setattr(live, "_at_default", stopped)
            patch.setattr(live, "_parent_in_session", lambda: True)
            live._HOLD._continued(signal.SIGCONT, None)
            assert made == [] and stops == [], (made, stops)
            with pytest.raises(_Stopped):
                live._HOLD._again(stop=True)
        assert made == [] and stops == [signal.SIGTTOU], (made, stops)
    finally:
        live.put_back(slave, termios.TCSANOW, found, last=True)
        os.close(master)
        os.close(slave)


@pytest.mark.skipif(sys.platform != "linux", reason="off Linux, a draining change with output unread waits for good")
def test_off_linux_the_drain_watch_notes_nothing(drainwatch, monkeypatch):
    # On macOS FIONREAD on a master counts what its slave has not read, never the slave's output: conftest.py's
    # drainwatch would fail a test that leaves what was typed unread there, and never see the output it is for (review
    # 68's F3). Here, on Linux, where a draining change returns at once, with the platform said to be macOS: a
    # TCSADRAIN with output unread at the master and nothing reading it is noted not at all.
    master, slave = os.openpty()
    try:
        os.write(slave, b"said and unread\n")
        # At the master once it can be read there: Linux hands what the slave writes on to the master a moment later.
        assert select.select([master], [], [], 5)[0], "nothing reached the master"
        with monkeypatch.context() as patch:
            patch.setattr(sys, "platform", "darwin")
            termios.tcsetattr(slave, termios.TCSADRAIN, termios.tcgetattr(slave))
        assert drainwatch == [], drainwatch
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.skipif(sys.platform != "linux", reason="the drain watch looks on Linux only")
def test_the_drain_watch_makes_the_echo_macos_makes_of_an_end_of_input_on_linux_too(drainwatch):
    # macOS echoes an end of input (Ctrl-D) typed in a canonical mode as ^D at the master, where Linux echoes nothing:
    # a draining change with that echo unread waits there for good (#310's hang in test_aw_ask.py, at a line the
    # input's end cuts short). On Linux the watch makes that echo too, so the change with it unread is noted; read at
    # the master, as an emulator reads it, it is nothing. Only as macOS makes it: in the mode of the moment it is typed.
    master, slave = os.openpty()
    try:
        os.write(master, b"\x04")
        assert os.read(slave, 16) == b""
        assert select.select([master], [], [], 5)[0], "no echo reached the master"
        termios.tcsetattr(slave, termios.TCSADRAIN, termios.tcgetattr(slave))
        assert len(drainwatch) == 1 and "an end of input's echo of macOS's" in drainwatch[0], drainwatch
        drainwatch.clear()
        echo = b""
        while select.select([master], [], [], 0.2)[0]:
            echo += os.read(master, 64)
        assert echo == b"^D\b\b", echo
        os.write(master, b"\x04")
        assert os.read(slave, 16) == b""
        echo = b""
        while select.select([master], [], [], 0.2)[0]:
            echo += os.read(master, 64)
        assert echo == b"^D\b\b", echo
        termios.tcsetattr(slave, termios.TCSADRAIN, termios.tcgetattr(slave))
        assert drainwatch == [], drainwatch
        # Typed while the terminal is raw, as at a menu, a Ctrl-D is a key, which macOS echoes not at all: nothing is
        # made, even once the canonical mode is back.
        canonical = termios.tcgetattr(slave)
        raw = list(canonical)
        raw[3] &= ~(termios.ICANON | termios.ECHO)
        termios.tcsetattr(slave, termios.TCSANOW, raw)
        os.write(master, b"\x04")
        assert os.read(slave, 16) == b"\x04"
        termios.tcsetattr(slave, termios.TCSANOW, canonical)
        assert not select.select([master], [], [], 0.2)[0], os.read(master, 64)
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.skipif(sys.platform != "linux", reason="the drain watch looks on Linux only")
def test_the_drain_watch_decides_the_echo_by_the_mode_a_ctrl_d_is_typed_in_never_the_one_after(drainwatch, monkeypatch):
    # macOS echoes a key by the mode of the moment it is typed. A program at a menu (raw) that reads a Ctrl-D as a key
    # and puts the canonical mode back has had nothing echoed, however soon it does so: the watch reads the mode before
    # what was typed reaches the master, never after (batch 27's ^D after test_aw_cli_rules.py's "Nothing was run.").
    # Here the program is a thread, and the watch's look at the master after the write is held a while, which gives it
    # the time to put its mode back first.
    master, slave = os.openpty()
    canonical = termios.tcgetattr(slave)
    raw = list(canonical)
    raw[3] &= ~(termios.ICANON | termios.ECHO)
    termios.tcsetattr(slave, termios.TCSANOW, raw)
    typed = threading.Event()
    stat = os.fstat

    def held(fd, *args, **kwargs):
        if fd == master and typed.is_set():
            time.sleep(0.3)
        return stat(fd, *args, **kwargs)

    def program() -> None:
        assert os.read(slave, 16) == b"\x04"
        termios.tcsetattr(slave, termios.TCSANOW, canonical)

    reader = threading.Thread(target=program)
    reader.start()
    try:
        monkeypatch.setattr(os, "fstat", held)
        # The first look (watched) before the write, then the write, then any look after it held.
        original = os.write
        monkeypatch.setattr(os, "write", lambda fd, data: (typed.set(), original(fd, data))[1])
        os.write(master, b"\x04")
        reader.join(5)
        monkeypatch.undo()
        assert not reader.is_alive()
        assert not select.select([master], [], [], 0.3)[0], os.read(master, 64)
    finally:
        monkeypatch.undo()
        reader.join(5)
        termios.tcsetattr(slave, termios.TCSANOW, canonical)
        os.close(master)
        os.close(slave)


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


def _in_the_background_at_a_question(patch, made):
    """A pseudo-terminal whose mode a question made (live.change, wait), then this process no longer its foreground, and
    the change stood in for; the terminal and the settings found."""
    master, slave = os.openpty()
    found = termios.tcgetattr(slave)
    mode = list(found)
    mode[3] &= ~termios.ECHO
    live.change(slave, termios.TCSAFLUSH, mode, wait=True)
    patch.setattr(os, "tcgetpgrp", lambda fd: os.getpgrp() + 1)
    patch.setattr(live, "set_terminal", lambda *args: made.append(args))
    return master, slave, found


def _closed(master, slave, found):
    live.put_back(slave, termios.TCSANOW, found, last=True)
    os.close(master)
    os.close(slave)


def test_a_question_in_the_background_of_an_orphaned_group_is_never_stopped_and_says_eio(monkeypatch):
    # Linux throws a stop at SIGTTOU's default away in an orphaned process group (its shell gone): the question would
    # loop for good with its lock held (review 68's F11). With no parent in its session, no stop is raised, and the
    # change says EIO, as the kernel's own change says there.
    made: list[tuple] = []
    stops: list[int] = []

    def stopped(signum):
        stops.append(signum)
        raise _Stopped

    with monkeypatch.context() as patch:
        master, slave, found = _in_the_background_at_a_question(patch, made)
        try:
            patch.setattr(live, "_at_default", stopped)
            patch.setattr(os, "getppid", lambda: 1)
            patch.setattr(os, "getsid", lambda pid: 1 if pid == 1 else 4242)
            with pytest.raises(termios.error) as raised:
                live._once_foreground(slave, termios.TCSANOW, found, lambda: True)
            assert raised.value.args[0] == errno.EIO and made == [] and stops == [], (raised.value, made, stops)
        finally:
            patch.undo()
            _closed(master, slave, found)


def test_a_question_whose_end_comes_at_its_first_stop_stops_no_more(monkeypatch):
    # The end told the hold (live.ending) while the question was stopped in the background: once it goes on, it is back
    # in the loop, and stops no more, nor changes the terminal (review 68's F13).
    made: list[tuple] = []
    stops: list[int] = []

    def stopped(signum):
        stops.append(signum)
        if len(stops) > 1:
            raise _Stopped
        live.ending()

    with monkeypatch.context() as patch:
        master, slave, found = _in_the_background_at_a_question(patch, made)
        try:
            patch.setattr(live, "_at_default", stopped)
            patch.setattr(live, "_parent_in_session", lambda: True)
            assert live._once_foreground(slave, termios.TCSANOW, found, live._HOLD._waiting) is False
            assert made == [] and stops == [signal.SIGTTOU], (made, stops)
        finally:
            patch.undo()
            _closed(master, slave, found)


def test_a_question_made_the_foreground_in_the_wait_before_its_stop_is_changed_and_never_stopped(monkeypatch):
    # fg in the wait before the stop (ENDING_GRACE): the change is made, and no stop raised (review 68's F12).
    made: list[tuple] = []
    stops: list[int] = []
    asked: list[bool] = []

    def stopped(signum):
        stops.append(signum)
        raise _Stopped

    def foreground(fd):
        asked.append(True)
        return len(asked) > 1

    with monkeypatch.context() as patch:
        master, slave, found = _in_the_background_at_a_question(patch, made)
        try:
            patch.setattr(live, "_at_default", stopped)
            patch.setattr(live, "_parent_in_session", lambda: True)
            patch.setattr(live, "foreground", foreground)
            assert live._once_foreground(slave, termios.TCSANOW, found, lambda: True) is True
            assert len(made) == 1 and stops == [], (made, stops)
        finally:
            patch.undo()
            _closed(master, slave, found)
