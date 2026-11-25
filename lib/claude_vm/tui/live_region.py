"""A live region at the bottom of a terminal, for a stdout callback: drawn in place, with Display's output above it.

Every live output draws with it (docs/architecture/custom-code.md §7.8): `just status`'s report
(claude_vm.status.engine) and every live run (callback_plugins/agent_warehouse.py, `just
deploy` first). Imported as claude_vm.tui.live_region (docs/architecture/custom-code.md §7.10).

What it does, once started:

- Draws what its frame function returns at the bottom of the screen: one write per redraw, inside
  synchronized-output marks (CSI ?2026), the cursor moved up exactly the lines drawn, the region cut to the height,
  and ten times a second for a spinner. A frame's lines come already cut to the width, so none wraps. A screen
  under MIN_ROWS rows or MIN_COLS columns gets no region at all: a region as tall as the screen scrolls each redraw.
- Wraps Display.display: anything printed erases the region first and redraws it after, so it stays above for
  good. A route function, given the call's keyword arguments, may send it to the log only (log_only), which leaves
  the screen alone.
- Wraps Display.prompt_until and Display.do_var_prompt: a pause prints with display() and only then takes Display's
  lock to read the keys (ansible-core 2.21), and a vars_prompt reads with input(), so the region is erased before
  either and drawn again only once it returns, and the run has worked half a second more: questions one after
  another show no frame between them. The spinner takes the same lock, and draws nothing while a prompt is up. A
  program's own question steps the region aside the same way, inside aside().
- On a resize (SIGWINCH), which can wrap a frame worked out for the old size, pushes the screen into the
  scrollback and draws again from the top. The cursor is never hidden.
- Sets the terminal's title (OSC 0) when it changes, and rings the bell when asked.
- Turns the terminal's echo off while it draws, and back on for a prompt and at the end: an Enter typed during the
  run would move the cursor under the region, and the next redraw would leave a copy of it behind. Meanwhile a Ctrl-C
  keeps what the terminal has not read yet (NOFLSH), where it would throw away a line said above the region or part
  of a frame, and the keys typed unseen before it are thrown away all the same, as the terminal's own Ctrl-C threw
  them away (hold's Ctrl-C, before the handler that was in place). For a prompt and at the end the settings are put
  back exactly as found - at a run's end, where it asks (finish(kept=True)), with NOFLSH kept on until its last
  words are said, then all as found (release). A Ctrl-Z while the region or a question holds the terminal erases the
  region and puts the settings back as found, what was typed and not read thrown away with them, before the stop;
  once the run goes on (fg) its mode is made again, the region drawn below the shell's lines and a question's words
  written again; a Ctrl-\\ at its default does the same before the quit. A change of the terminal's settings is made
  only while this process is the terminal's foreground, SIGTTOU held off, so that a run put in the background (Ctrl-Z,
  then bg) is not stopped again by it, and leaves the shell's settings; a put-back is made with the signals of HELD
  held off till it is made (uninterrupted), and one that a signal interrupts is made again (set_terminal): the
  terminal is still there.
- Erases the region at exit, Ctrl-C included, and holds a Ctrl-C, a SIGTERM, a SIGHUP, a Ctrl-Z and a Ctrl-\\ off
  between a write and its count of the lines it drew (uninterrupted), each delivered once the count is right, in the
  order it came: a KeyboardInterrupt between the two left the count stale, and the erase that followed missed the
  region; a program that raises from its own SIGTERM or SIGHUP handler (the new engine's run, runs/attached.py) met
  the same gap, a frame's remains left above the run's last words or lines said above it wiped.

Forked workers inherit the wrapped methods: only the process that started the region draws. Without a frame
function it draws nothing and only routes, for a run whose output is not a terminal.
"""

from __future__ import annotations

import atexit
import contextlib
import errno
import os
import signal
import sys
import termios
import threading
import time
from contextlib import contextmanager

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SYNC_ON, SYNC_OFF = "\x1b[?2026h", "\x1b[?2026l"
FRAME_SECONDS = 0.1
# After a prompt, the region comes back only once the run has worked this long: questions one after another, just
# init's, show no frame between them.
SETTLE_SECONDS = 0.5
MIN_ROWS, MIN_COLS = 4, 20
# What a program ends or stops on, held off for a write to the terminal and its count (uninterrupted): a Ctrl-C, a
# kill and a hangup, which a program may end on by raising from a handler of its own; and a Ctrl-Z (SIGTSTP) and a
# Ctrl-\ (SIGQUIT), which the hold of the terminal (_Hold) takes, the region erased before the stop or the quit.
HELD = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGTSTP, signal.SIGQUIT)


@contextmanager
def uninterrupted():
    """HELD held off for what is inside, then each one that came delivered, in the order it came, by the handler that
    was in place before; the first error a handler raises (a KeyboardInterrupt) raised once every one is delivered.

    Held at Python's level, where a handler runs and raises: a handler of the hold's own takes note of each, and is
    swapped back before they are raised again (signal.raise_signal), so a default ends the process there and then. A
    signal mask would hold the signal in one thread only: the kernel hands it to another (Ansible's results thread, the
    spinner), and Python runs its handler in the main thread all the same. Only the main thread may set a handler, and
    only it is ever interrupted, so anywhere else nothing is held. A signal ignored (nohup's SIGHUP) stays ignored, and
    one whose handler no Python set is left as it is. Holds nest: one inside another hands what it held to the one
    around it."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    held = []
    swapped = {}

    def note(signum, _frame):
        held.append(signum)

    # A hold's own, which the terminal's hold (_Hold) never takes a signal from.
    note.holding = True
    try:
        for signum in HELD:
            if signal.getsignal(signum) not in (None, signal.SIG_IGN):
                swapped[signum] = signal.signal(signum, note)
        yield
    finally:
        raised = None
        for signum, handler in swapped.items():
            # Each handler put back whatever another one raises meanwhile (one put back before it, at a signal now);
            # the hold of the terminal's, given back meanwhile, as the one it took (_in_place_of).
            while True:
                try:
                    signal.signal(signum, _in_place_of(signum, handler))
                    break
                except BaseException as error:
                    raised = raised or error
        for signum in held:
            try:
                signal.raise_signal(signum)
            except BaseException as error:
                raised = raised or error
        if raised is not None:
            raise raised


def set_terminal(fd, when, settings):
    """termios.tcsetattr(fd, when, settings), made again where a signal interrupted it (EINTR), which says nothing of
    the terminal: TCSADRAIN waits for the output to drain, and a signal whose handler raises nothing - a program's first
    Ctrl-C, a hold's note (uninterrupted), a resize - ends the wait with EINTR, since Python sets its handlers without
    SA_RESTART, and termios, unlike os's calls, does not try again itself. A handler that raises goes through as itself,
    between two tries; any other error, as termios.error."""
    while True:
        try:
            termios.tcsetattr(fd, when, settings)
            return
        except termios.error as error:
            if not error.args or error.args[0] != errno.EINTR:
                raise


def foreground(fd) -> bool:
    """Whether this process is the terminal's foreground, where a change of its settings is its own to make: its group
    the terminal's, or a terminal that is not this process's controlling one (tcgetpgrp fails), whose changes stop
    nobody."""
    try:
        return os.tcgetpgrp(fd) == os.getpgrp()
    except OSError:
        return True


@contextmanager
def _unstopped():
    """SIGTTOU held off in this thread: a change of the terminal's settings, or a flush of its input, made while this
    process is no longer its foreground goes through, rather than stopping the process."""
    held = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTTOU})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, held)


def _made(fd, when, settings) -> bool:
    """settings made (set_terminal), only while this process is the terminal's foreground and with SIGTTOU held off;
    whether they were. A process the shell put in the background (Ctrl-Z, then bg) leaves the shell's settings, and
    is not stopped by a change of its own."""
    with _unstopped():
        if not foreground(fd):
            return False
        set_terminal(fd, when, settings)
        return True


class _Hold:
    """The terminal while this process holds it in a mode of its own (change): the settings found before the first
    change, the mode made last, and meanwhile the signals the terminal's keys send taken (_OWN), as the terminal's own
    keys would have it where its mode were not this process's:

    - A Ctrl-C (SIGINT), where a Python handler takes it, which then still does: the keys typed unseen under NOFLSH
      thrown away first, as the terminal's own Ctrl-C threw them away.
    - A Ctrl-Z (SIGTSTP), at its default: the settings found put back, what was typed and not read thrown away with them
      (TCSAFLUSH), as the key throws it away where NOFLSH is off; then the stop. Once the process goes on (SIGCONT, a
      shell's fg), the mode made last made again, where the shell put its own settings back at the stop - and a question
      (change's wait) continued in the background (bg) stops again there (SIGTTOU) until it is the foreground - and the
      words of what holds the terminal written again (back).
    - A Ctrl-\\ (SIGQUIT), at its default: the same, then the quit at its default. A handler of the program's own (the
      new engine's ladder, which ends the run on it as on a SIGTERM) is left as it is.

    Before a stop or a quit, what holds the terminal steps away (away: the region erased, none drawn till the process
    goes on). Given back once the settings are put back as found (put_back), each handler put back where it is still the
    hold's. A forked process's copy is no hold of its own: its handlers hand each signal on, or take its default."""

    def __init__(self):
        self.pid = 0
        self.fd = None
        self.found = None
        self.made = None  # (when, settings): the mode made last
        # What holds the terminal, told before a stop or a quit (away) and once the mode is made again after a stop
        # (back); and whether it waits for the terminal (change's wait: a question).
        self.away = self.back = None
        self.waits = False
        # Whether back is owed: a stop since the mode was last made.
        self.owed = False
        # signum: the handler in place before the hold's, kept while the hold's is in place - given back, or not, as a
        # handler of a hold (uninterrupted) put it back after the give back.
        self.taken = {}

    def ours(self) -> bool:
        return self.found is not None and self.pid == os.getpid()

    def take(self, fd):
        if self.ours() and self.fd == fd:
            return
        if self.ours():
            self.give_back()
        self.pid, self.fd, self.found, self.made = os.getpid(), fd, termios.tcgetattr(fd), None
        self.away = self.back = None
        self.waits = self.owed = False
        if threading.current_thread() is not threading.main_thread():
            return
        for signum, own in _OWN.items():
            handler, mine = signal.getsignal(signum), getattr(self, own)
            if handler == mine:
                continue
            if self._takes(signum, handler):
                self.taken[signum] = signal.signal(signum, mine)

    @staticmethod
    def _takes(signum, handler) -> bool:
        """Whether the hold takes signum from handler: a Ctrl-C from a Python handler, never a hold's note
        (uninterrupted) nor one that throws the keys typed away itself (DISCARDS: the new engine's ladder); a Ctrl-Z,
        the continue after it and a Ctrl-\\ only from their default, never from a handler of the program's own."""
        if signum != signal.SIGINT:
            return handler == signal.SIG_DFL
        return callable(handler) and not getattr(handler, "holding", False) and not getattr(handler, DISCARDS, False)

    def give_back(self):
        self.found = self.made = self.fd = None
        self.away = self.back = None
        self.waits = self.owed = False
        if threading.current_thread() is not threading.main_thread():
            return
        for signum, handler in list(self.taken.items()):
            if signal.getsignal(signum) == getattr(self, _OWN[signum]):
                signal.signal(signum, handler)
                del self.taken[signum]

    def _handed_on(self, signum, frame):
        """The signal to the handler that was in place before the hold's."""
        handler = self.taken.get(signum)
        if callable(handler):
            handler(signum, frame)
        elif signum == signal.SIGINT:
            signal.default_int_handler(signum, frame)

    def _interrupted(self, signum, frame):
        if self.ours():
            discard_typed(self.fd)
        self._handed_on(signum, frame)

    def _as_found(self):
        """Before a stop or a quit: what holds the terminal told (away), then the settings found put back, what was
        typed and not read thrown away with them (TCSAFLUSH), only while this process is the terminal's foreground."""
        if not self.ours():
            return
        if self.away is not None:
            with contextlib.suppress(Exception):
                self.away(True)
        with contextlib.suppress(OSError, termios.error):
            _made(self.fd, termios.TCSAFLUSH, self.found)

    def _stopped(self, signum, _frame):
        """A Ctrl-Z: the terminal as found, then the stop at SIGTSTP's default; once the process goes on, what stepped
        away told so (away(False)), foreground or not, and the mode again (_continued)."""
        away = self.away if self.ours() else None
        if self.ours():
            self._as_found()
            self.owed = True
        _at_default(signum)
        if away is not None:
            with contextlib.suppress(Exception):
                away(False)
        self._continued()

    def _continued(self, _signum=None, _frame=None):
        """The process goes on (SIGCONT): the mode made last made again where the terminal is no longer in it - the
        shell put its own settings back at the stop - only while this process is the terminal's foreground, but for a
        question (waits), which in the background stops there (SIGTTOU) till it is; then, after a stop, back told,
        once."""
        if not self.ours() or self.made is None:
            return
        when, settings = self.made
        try:
            if self.waits:
                # SIGTTOU let through, wherever the stop came: a question in the background stops here.
                held = signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTTOU})
                try:
                    if not foreground(self.fd) or termios.tcgetattr(self.fd) != list(settings):
                        set_terminal(self.fd, when, settings)
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, held)
            else:
                with _unstopped():
                    if not foreground(self.fd):
                        return
                    if termios.tcgetattr(self.fd) != list(settings):
                        set_terminal(self.fd, when, settings)
        except (OSError, termios.error):
            return
        if self.owed and self.ours():
            self.owed = False
            if self.back is not None:
                with contextlib.suppress(Exception):
                    self.back()

    def _quit(self, signum, _frame):
        """A Ctrl-\\: the terminal as found, then the quit at SIGQUIT's default."""
        self._as_found()
        _at_default(signum)


def _at_default(signum):
    """signum's default taken at once, in this thread - the stop, or the end - and the handler in place put back once
    the process goes on."""
    handler = signal.signal(signum, signal.SIG_DFL)
    try:
        held = signal.pthread_sigmask(signal.SIG_UNBLOCK, {signum})
        try:
            signal.raise_signal(signum)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, held)
    finally:
        signal.signal(signum, handler)


# The hold's own handler of each signal it takes, by its name.
_OWN = {
    signal.SIGINT: "_interrupted",
    signal.SIGTSTP: "_stopped",
    signal.SIGCONT: "_continued",
    signal.SIGQUIT: "_quit",
}
# Set true on a handler that throws the keys typed away itself at a Ctrl-C (discard_typed), which the hold then leaves
# in place.
DISCARDS = "discards_typed"
_HOLD = _Hold()


def _in_place_of(signum, handler):
    """The handler to put back for handler once a hold (uninterrupted) ends: the hold of the terminal's own, where the
    hold was given back meanwhile, which could not put back the one it took while the note was in its place, is that
    one; any other is itself."""
    own = _OWN.get(signum)
    if own is not None and handler == getattr(_HOLD, own) and not _HOLD.ours():
        return _HOLD.taken.pop(signum, handler)
    return handler


def change(fd, when, settings, *, wait=False, away=None, back=None) -> bool:
    """A mode of this process's own made (set_terminal): the terminal held (_Hold) from the first one, the settings
    found before it kept, and this mode as the one made last. Made only while this process is the terminal's
    foreground, SIGTTOU held off (whether it was is given back) - but with wait, at once, for a question that needs the
    terminal: a process in the background is stopped there (SIGTTOU) until it is the foreground again. What holds the
    terminal is told, where it gives them, before a stop or a quit (away(True)), once the process goes on after a stop
    (away(False)) and once its mode is back after one (back): in the main thread, from a signal's handler."""
    _HOLD.take(fd)
    _HOLD.made = (when, settings)
    _HOLD.away, _HOLD.back, _HOLD.waits = away, back, wait
    if wait:
        set_terminal(fd, when, settings)
        return True
    return _made(fd, when, settings)


def put_back(fd, when, settings, *, last=False) -> bool:
    """settings put back, with the signals of HELD held off till they are (uninterrupted): a second Ctrl-C never leaves
    a change back half made. Only while this process is the terminal's foreground, SIGTTOU held off;
    whether they were made. A draining one (TCSADRAIN) of the settings the terminal has already is no change. The
    hold given back once the terminal is as found, or with last whatever it holds."""
    try:
        with uninterrupted():
            if _HOLD.ours() and _HOLD.fd == fd:
                # The mode made last before the change is made: a Ctrl-Z held off meanwhile, taken once the change is
                # made, comes back to these settings, never to the mode they replace. A mode put back is no one's to
                # step away from or to come back to with words.
                _HOLD.made = (when, settings)
                _HOLD.away = _HOLD.back = None
                _HOLD.waits = False
            if when != termios.TCSADRAIN or termios.tcgetattr(fd) != list(settings):
                return _made(fd, when, settings)
            return False
    finally:
        if _HOLD.ours() and _HOLD.fd == fd and (last or list(settings) == _HOLD.found):
            _HOLD.give_back()


def discard_typed(fd=None) -> bool:
    """What was typed and not read yet thrown away (TCIFLUSH), as the terminal's own Ctrl-C throws it away where NOFLSH
    is off: only while this process is the terminal's foreground, SIGTTOU held off; whether it was. Nothing for an
    input that is no terminal, or has gone."""
    try:
        fd = sys.stdin.fileno() if fd is None else fd
        if not os.isatty(fd):
            return False
        with _unstopped():
            if not foreground(fd):
                return False
            termios.tcflush(fd, termios.TCIFLUSH)
            return True
    except (OSError, ValueError, AttributeError, termios.error):
        return False


def can_draw(stream=None, environ=None) -> bool:
    """Whether a stream is a terminal that can take the region: one, not dumb, and UTF-8 for the glyphs."""
    stream = stream or sys.stdout
    environ = os.environ if environ is None else environ
    try:
        tty = stream.isatty()
    except (AttributeError, ValueError):
        tty = False
    encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
    return tty and environ.get("TERM", "") not in ("", "dumb") and encoding.startswith("utf8")


class LiveRegion:
    """The region. frame(cols, rows, glyph) returns its lines; route(kwargs) returns display()'s keyword arguments;
    more says the lines a frame taller than the screen leaves out, {} their number."""

    def __init__(self, display, frame=None, route=None, stream=None, more="⋮ {} more lines", on_prompt=None):
        self._display = display
        self._frame = frame
        self._route = route
        self._on_prompt = on_prompt  # told True as a prompt takes the screen, False once it is answered
        self._more = more
        self._stream = stream or sys.stdout
        self._pid = os.getpid()
        self._drawn = 0
        self._size = None
        self._resized = False
        self._tick = 0
        self._paused = False
        self._stepped = False  # away for a stop, till the run goes on
        self._started = False
        self._stop = threading.Event()
        self._thread = None
        self._title = None
        self._echo = None  # the terminal's settings before the echo went off, while it is off
        self._kept = None  # the same, while finish(kept=True) has left NOFLSH on for release() to put back
        self._settle = 0.0  # until when, after a prompt, nothing is drawn
        self.prompting = False

    # --- the screen -------------------------------------------------------------------------------------------
    def _write(self, text):
        self._stream.write(text)
        self._stream.flush()

    # The signals of HELD held off for one write to the terminal and the count of the lines it drew.
    _uninterrupted = staticmethod(uninterrupted)

    def _erase(self):
        if self._drawn:
            with self._uninterrupted():
                self._write((f"\x1b[{self._drawn - 1}A" if self._drawn > 1 else "") + "\r\x1b[J")
                self._drawn = 0

    def _terminal_size(self):
        return os.get_terminal_size(self._stream.fileno())

    def redraw(self):
        """Draw the frame again, in place. A no-op while stopped, paused for a prompt, settling after one, away for a
        Ctrl-Z till the run goes on, or without a frame."""
        if not self._frame or self._stop.is_set() or self._paused or self._stepped or not self._started:
            return
        if time.monotonic() < self._settle:
            return
        with self._display._lock:
            # Looked at again under the lock: a stop's erase (_away) may have come between.
            if self._stepped:
                return
            try:
                cols, rows = self._terminal_size()
            except (OSError, ValueError, AttributeError):
                return
            if self._resized or (self._size and self._size != (cols, rows)):
                # Everything on screen goes up into the scrollback, where nothing is lost, and the region is drawn
                # again from the top of an empty screen: no count of what was drawn before the resize holds.
                self._resized = False
                with self._uninterrupted():
                    self._write("\n" * rows + f"\x1b[{rows - 1}A\r")
                    self._drawn = 0
            self._size = (cols, rows)
            lines = self._frame(cols, rows, FRAMES[self._tick % len(FRAMES)])
            if rows < MIN_ROWS or cols < MIN_COLS:
                lines = []
            limit = rows - 1
            # A frame taller than the screen keeps its top and its last line; a screen too small has none at all -
            # a terminal that reports 0 rows, a pseudo-terminal nobody gave a size, among them.
            if lines and len(lines) > limit:
                lines = lines[: limit - 2] + [self._more.format(len(lines) - limit + 1)[: cols - 1], lines[-1]]
            with self._uninterrupted():
                up = f"\x1b[{self._drawn - 1}A" if self._drawn > 1 else ""
                self._write(SYNC_ON + up + "\r" + "\x1b[K\n".join(lines) + "\x1b[K\x1b[J" + SYNC_OFF)
                self._drawn = len(lines)

    def title(self, text):
        """The terminal's title, written when it changes."""
        if self._frame and text != self._title and os.getpid() == self._pid:
            self._title = text
            with self._display._lock:
                self._write(f"\x1b]0;{text}\x07")

    def bell(self):
        if self._frame and os.getpid() == self._pid:
            with self._display._lock:
                self._write("\a")

    def _quiet(self, on, kept=False):
        """The terminal's echo off and its output kept at an interrupt (on=True), or both back exactly as found - or,
        with kept, all but NOFLSH, which release() puts back after the run's last words. NOFLSH: at a Ctrl-C (a quit,
        a suspend) the terminal otherwise throws away what was written and not yet read by it - a line said above the
        region, a part of a frame - which macOS keeps in its queue until it is read and Linux for a moment. The
        settings to put back are kept before the change is made, so that an interrupt inside it leaves them to put
        back. Each change only while this process is the terminal's foreground (change, put_back): in the background
        the settings are the shell's, never read as the ones to put back. Nothing for an input that is no terminal; a
        change a signal interrupted is made again (set_terminal), never taken for no terminal, which would drop the
        settings to put back and leave the echo off for a prompt."""
        try:
            fd = sys.stdin.fileno()
            if on and self._echo is None and os.isatty(fd) and foreground(fd):
                settings = termios.tcgetattr(fd)
                quiet = [*settings[:3], (settings[3] & ~termios.ECHO) | termios.NOFLSH, *settings[4:]]
                self._echo = settings
                change(fd, termios.TCSADRAIN, quiet, away=self._away)
            elif not on and self._echo is not None:
                found = self._echo
                back = found
                if kept and not found[3] & termios.NOFLSH:
                    back = [*found[:3], found[3] | termios.NOFLSH, *found[4:]]
                    self._kept = found
                self._echo = None
                put_back(fd, termios.TCSADRAIN, back)
        except (OSError, ValueError, AttributeError, termios.error):
            self._echo = None

    def _away(self, going):
        """Before a stop (Ctrl-Z) or a quit (Ctrl-\\) while the region holds the terminal (going): the region erased,
        so that the shell's lines take its place, and none drawn till the run goes on (not going), then drawn below
        them. In the main thread, from the signal's handler: Display's lock waited for a second at most."""
        self._stepped = going
        lock = self._display._lock
        if going and lock.acquire(timeout=1):
            try:
                self._erase()
            finally:
                lock.release()

    @contextmanager
    def aside(self):
        """The region out of a question's way while what is inside asks it: erased and paused, the echo on and on_prompt
        told True; after, on_prompt told False, the echo off again while the region still draws, and nothing drawn until
        the run has worked SETTLE_SECONDS more, so questions one after another show no frame between them. What the
        wrapped prompt methods do, and a program's own question (runs/live.py's Live.aside). In a forked worker,
        nothing: only the process that started the region draws."""
        if os.getpid() != self._pid:
            yield
            return
        # The question prints through display() or its own writes, and goes to the screen whatever the route says.
        with self._display._lock:
            self._erase()
            self._paused = True
            self.prompting = True
            self._quiet(False)
        if self._on_prompt is not None:
            self._on_prompt(True)
        try:
            yield
        finally:
            if self._on_prompt is not None:
                self._on_prompt(False)
            with self._display._lock:
                self._paused = False
                self.prompting = False
                if self._frame and not self._stop.is_set():
                    self._quiet(True)
                self._settle = time.monotonic() + SETTLE_SECONDS

    # --- the wrappers -----------------------------------------------------------------------------------------
    def _wrap(self):
        display = self._display
        original_display = display.display

        def wrapped_display(
            msg, color=None, stderr=False, screen_only=False, log_only=False, newline=True, caplevel=None
        ):
            kwargs = {"color": color, "stderr": stderr, "screen_only": screen_only, "log_only": log_only,
                      "newline": newline, "caplevel": caplevel}  # fmt: skip
            # A forked worker's display goes over the queue to this process, which draws.
            if os.getpid() != self._pid:
                return original_display(msg, **kwargs)
            # Routed even once the region is finished: what prints after it, a recap, follows the same rule.
            if self._route and not self.prompting:
                kwargs = self._route(kwargs)
            # Under the lock either way, so every write to the log takes the locks in one order.
            if kwargs.get("log_only") or not self._frame or self._stop.is_set():
                with display._lock:
                    return original_display(msg, **kwargs)
            with display._lock:
                self._erase()
                original_display(msg, **kwargs)
                self.redraw()

        def stepping_aside(original):
            def wrapped_prompt(*args, **kwargs):
                # The prompt prints through display() or input(), with the region aside.
                with self.aside():
                    return original(*args, **kwargs)

            return wrapped_prompt

        display.display = wrapped_display
        display.prompt_until = stepping_aside(display.prompt_until)
        display.do_var_prompt = stepping_aside(display.do_var_prompt)

    def _spin(self):
        while not self._stop.wait(FRAME_SECONDS):
            with self._display._lock:
                self._tick += 1
                self.redraw()

    # --- the life of it ---------------------------------------------------------------------------------------
    def start(self):
        """Wrap Display, and with a frame, take the terminal - SIGWINCH, the echo, an erase at exit - from the main
        thread, which alone may set a signal handler. The region is drawn by redraw(), and by spin()'s ticks."""
        if self._started:
            return
        self._started = True
        self._wrap()
        if self._frame:
            signal.signal(signal.SIGWINCH, lambda *_: setattr(self, "_resized", True))
            atexit.register(self._at_exit)
            self._quiet(True)

    def spin(self):
        """Redraw ten times a second, for the spinner, until finish()."""
        if self._frame and self._started and self._thread is None:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()

    def finish(self, kept=False):
        """Erase the region and draw no more; Display's output goes on as it would without the region. The terminal's
        settings back as found - but with kept, NOFLSH kept on, so that a Ctrl-C the terminal has not read the run's
        last words by (its summary, NEXT STEPS) throws none of them away: release() then puts it back."""
        if self._started and not self._stop.is_set() and os.getpid() == self._pid:
            with self._display._lock:
                self._erase()
                self._stop.set()
                self._quiet(False, kept=kept)

    def release(self):
        """The terminal's settings exactly as found once the run's last words are said, after finish(kept=True): its
        last change of them, a draining one, which where the terminal waits for its reader (macOS) lets NOFLSH go only
        once those words are read. Nothing otherwise, and nothing a second time."""
        found, self._kept = self._kept, None
        if found is not None and os.getpid() == self._pid:
            with contextlib.suppress(OSError, ValueError, AttributeError, termios.error):
                put_back(sys.stdin.fileno(), termios.TCSADRAIN, found)

    def _at_exit(self):
        """At exit, whatever ended the run: the region erased and the settings exactly as found."""
        self.finish()
        self.release()


class Output:
    """What every live output does around its region, whatever it draws: whether to draw at all, the region, and its
    own words printed coloured to the screen and plain to the log.

    live says whether there is a region (can_draw(), unless the caller decided: a run piped is drawn as lines);
    color whether the screen gets the palette (the caller's ANSIBLE_NOCOLOR). With to_log, everything Display prints
    goes to the log alone - the stream a drawn run replaces - but what is printed inside say(), and anything on
    stderr, which still reaches the screen."""

    def __init__(
        self, display, frame=None, live=None, color=False, to_log=False, more="⋮ {} more lines", on_prompt=None
    ):
        self.display = display
        self.live = can_draw() if live is None else live
        self.color = bool(color) and self.live
        self._speaking = threading.local()
        self.region = LiveRegion(
            display,
            frame=frame if self.live else None,
            route=self._route if to_log else None,
            more=more,
            on_prompt=on_prompt,
        )

    # --- the region's life, as LiveRegion's --------------------------------------------------------------------
    def start(self):
        self.region.start()

    def redraw(self):
        self.region.redraw()

    def spin(self):
        self.region.spin()

    def finish(self):
        self.region.finish()

    def title(self, text):
        self.region.title(text)

    def bell(self):
        self.region.bell()

    # --- the output's own words --------------------------------------------------------------------------------
    @contextlib.contextmanager
    def say(self):
        """What is printed inside reaches the screen, where the route would send it to the log alone."""
        previous = getattr(self._speaking, "on", False)
        self._speaking.on = True
        try:
            yield
        finally:
            self._speaking.on = previous

    def _route(self, kwargs):
        if getattr(self._speaking, "on", False) or kwargs.get("stderr"):
            return kwargs
        return {**kwargs, "log_only": True}

    def print(self, lines):
        """Lines of the output's own, (coloured, plain) each: the colour to the screen, the plain text to the log."""
        with self.say():
            for coloured, plain in lines:
                if self.color:
                    self.display.display(coloured, screen_only=True)
                    self.display.display(plain, log_only=True)
                else:
                    self.display.display(plain)
