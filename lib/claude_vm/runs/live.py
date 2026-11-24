"""The live view of a run: drawn from the events it emits, in the run's own process (docs/architecture/engine.md §16.8).

`Live(job, declared, stream)` listens to the job's events (runs/job.py's emit), feeds each to the run's model
(runs/model.py) at its own clock, `time.monotonic()`, never the event's wall-clock time, and says the run as
runs/view.py draws it, in one of three ways, decided once at its start:

- At a terminal that can take the region (tui/live_region.py's can_draw): the frame, drawn on the stream through the
  one live region every live output shares (tui/live_region.py's LiveRegion), redrawn after each event and ten times a
  second for the spinner, the terminal's title saying where the run is; what is said above the region after each event
  (each report whole, each failure); and while it draws, standard error said above the region too, line by line, so
  that a line the run says there (runs/attached.py's `stopping` at a Ctrl-C, a warning) never lands inside it.
- Where the stream is no terminal: what is said above, and a line as each step and stage ends (View.ended_lines).
- With any -v (cli/before.py's RunWords' verbosity): each event as one line, as it comes, and nothing drawn, as today
  any -v gives the stream.

At the run's own end (the `end` event with no machine, which runs/attached.py emits last), the region goes, the echo
comes back, and the summary is printed with NEXT STEPS after it, whole, in the block every run ends with (text.banner);
with -v, NEXT STEPS alone. A wait for you that begins rings the terminal's bell, and so does the end. Once the run's
`Context.cancelled` is set (C4's first Ctrl-C, which emits no event), the model is told at the next event or the next
frame (`Run.cancel`), so the footer says the run is stopping. An event the model refuses (an undeclared step, a
malformed end) is said in one line on standard error and left out, and the run goes on: the record has already kept
what it keeps of it, and the listeners after this one still hear it (review C8.md 6).

Nothing here writes an escape sequence or reads the terminal itself: the region, the palette and the title are tui/'s,
the terminal's echo the region's (tests/unit/test_aw_live.py holds every module of runs/ to that by a scan). The model's
machines are the job's own (`job.json`'s `machines`), by identity, the key every event of a machine names, each with the
name a person chose where the caller gives it (`names`): `job.json` keeps identities alone, and the view says a machine
by its name, which an event's `name` also tells the model. A Live is made in the run's thread, which alone may take
SIGWINCH for the region, and once a job: a second view of it is refused (RuntimeError), the first keeping it, and the
job hands the one view every event from then on.

What the view says in the run's thread - each event's lines, standard error's lines above the region - is said whole,
with a Ctrl-C, a SIGTERM and a SIGHUP held off meanwhile and delivered once it is said (tui/live_region.py's
uninterrupted, which holds each of the region's writes and its count too): the ladder (runs/attached.py) raises from
its handler wherever the run's thread is, and a frame drawn but not counted, or a line counted as said but not written,
would leave a frame's remains above the summary, wipe lines said above it, or lose one. A stream whose reader has gone
(`aw … | head -1`) is written no more; one whose pipe is broken is pointed at /dev/null, so that Python's flush at
the process's exit writes what it still holds there and the run ends with its own status, never Python's 120.

A question at the run's terminal finds the run's view by the job its Context carries, `Live.of(context.job)` (None for
a run with no view), and asks inside `with view.aside():`, which steps the region aside as it does for a prompt (erased
and paused, the echo on; after, the echo off and the region back half a second later).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import threading
import time
import weakref
from collections.abc import Callable, Mapping
from typing import IO, Protocol

from claude_vm.runs import job as jobs
from claude_vm.runs import model
from claude_vm.runs.stages import Declared
from claude_vm.runs.view import View
from claude_vm.text import banner, one_line
from claude_vm.tui import live_region, screen

# The three ways a run is said: the region, a line as each step ends, and each event as a line (any -v).
LIVE, LINES, STREAM = "live", "lines", "stream"
# What is printed above where the stream has no width of its own: today's runs' (ansible/parts.py's terminal_width).
WIDTH = 100
# NEXT STEPS' block: its title, and its colour, the palette's yellow (today's run/next_steps.py says bright yellow,
# which tui/screen.py's palette has not; a colour is the palette's, never one of this module's own).
NEXT_STEPS = "NEXT STEPS"
NEXT_STEPS_STYLE = "y"
# What is said of an event the model refused, on standard error.
REFUSED = "aw: the live view left out an event it could not take ({event}): {error}"
# What a second view of a job is refused with.
TWICE = "job {job} has its live view already: a run is drawn by one view, made once"

# The view of each job of this process's that has one, by the job's folder (as runs/job.py keys the jobs it holds), for
# as long as the job hands it its events.
_VIEWS: weakref.WeakValueDictionary[str, Live] = weakref.WeakValueDictionary()


def _key(job: jobs.Job) -> str:
    return os.path.realpath(job.folder)


class Cancelled(Protocol):
    """What says the run is stopping: the run's Context.cancelled (runs/attached.py)."""

    def is_set(self) -> bool: ...


def _to_null(stream: IO[str]) -> None:
    """stream's descriptor pointed at /dev/null, once its reader has gone (a broken pipe): what the stream still holds
    goes there at Python's flush at exit, which then succeeds, as Python's documentation has it (the signal module's
    note on SIGPIPE). The descriptor keeps the flag it had: a child that inherits the run's standard output inherits
    /dev/null, and one that does not is never handed it (tests/unit/test_aw_children.py's scan holds that). A stream
    with no descriptor of its own is left as it is."""
    with contextlib.suppress(OSError, ValueError, AttributeError, io.UnsupportedOperation):
        fd = stream.fileno()
        null = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null, fd, inheritable=os.get_inheritable(fd))
        finally:
            os.close(null)


class Display:
    """What the live region asks of a display (tui/live_region.py), as status/engine.py's Display gives it: a lock, a
    display() that writes a line to the run's stream, or to standard error as the run found it, and the two prompt
    methods, which the region wraps to step aside. No question is asked here: a run's questions are runs/ask.py's
    (C11). Nothing goes to a log: the run's text log is a listener of its own (C12). A stream that has gone never fails
    the run: once a write or a flush there fails, nothing more is written there, and where its pipe is broken (a reader
    that left early, `| head -1`) its descriptor is pointed at /dev/null (_to_null)."""

    def __init__(self, stream: IO[str], errors: IO[str]) -> None:
        self._lock = threading.RLock()
        self._stream = stream
        self._errors = errors
        self._gone: list[IO[str]] = []

    def display(self, msg, color=None, stderr=False, screen_only=False, log_only=False, newline=True, caplevel=None):
        if log_only:
            return
        out = self._errors if stderr else self._stream
        if any(out is gone for gone in self._gone):
            return
        try:
            out.write(msg + ("\n" if newline else ""))
            out.flush()
        except (OSError, ValueError) as error:
            self._gone.append(out)
            if isinstance(error, BrokenPipeError):
                _to_null(out)

    def prompt_until(self, *args, **kwargs):
        raise RuntimeError("a run asks its questions through runs/ask.py, which steps the region aside")

    do_var_prompt = prompt_until


class _Above(io.TextIOBase):
    """Standard error while the region is drawn: each whole line written is said above the region, through the display
    the region wraps, which erases the region, writes the line to standard error as the run found it, and draws the
    region again, the interrupts held off till it is said (live_region.uninterrupted); a part of a line waits for its
    end, or for the region's (rest)."""

    def __init__(self, display: Display, real: IO[str]) -> None:
        super().__init__()
        self._display = display
        self._real = real
        self._partial = ""

    @property
    def encoding(self):  # type: ignore[override]
        return getattr(self._real, "encoding", "utf-8")

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return self._real.isatty()

    def fileno(self) -> int:
        return self._real.fileno()

    def write(self, text: str) -> int:
        with live_region.uninterrupted(), self._display._lock:
            lines = (self._partial + text).split("\n")
            self._partial = lines.pop()
            for line in lines:
                self._display.display(line, stderr=True)
        return len(text)

    def flush(self) -> None:
        with contextlib.suppress(OSError, ValueError):
            self._real.flush()

    def rest(self) -> str:
        """What was written after the last whole line, taken."""
        with self._display._lock:
            rest, self._partial = self._partial, ""
        return rest


def _color_on() -> bool:
    return not os.environ.get("NO_COLOR")


def streamed(event: jobs.Event) -> str:
    """An event as one line, for a run with any -v: its kind and machine, its text, and what it carries, each value on
    the same line (text.one_line, never cut)."""
    head = event.kind if event.machine is None else f"{event.kind} {event.machine}"
    said = [f"{head}: {one_line(event.text, None)}" if event.text else head]
    for name, value in event.data.items():
        shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        said.append(f"{name}={one_line(shown, None)}")
    return " · ".join(said)


class Live:
    """The live view of one run: made in the run's thread once its job exists, once for the job (a second is
    RuntimeError), it hears every event the job emits from then on and ends with the run's own end. stream is where the
    run is said (standard output by default); verbosity the run's -v count; cancelled the run's Context.cancelled; names
    the name a person chose for each of the job's machines, by identity; title, host and check what the view's header
    says (runs/view.py); clock the view's own, time.monotonic by default, never the events' wall-clock time."""

    @staticmethod
    def of(job: jobs.Job) -> Live | None:
        """The view drawing job's run, the one a question at the run's terminal steps aside (aside()); None where the
        run has none."""
        return _VIEWS.get(_key(job))

    def __init__(
        self,
        job: jobs.Job,
        declared: Declared,
        stream: IO[str] | None = None,
        *,
        verbosity: int = 0,
        cancelled: Cancelled | None = None,
        names: Mapping[str, str] | None = None,
        title: str | None = None,
        host: str = "",
        check: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(declared, Declared):
            raise TypeError("a live view draws a run of a Declared")
        if isinstance(verbosity, bool) or not isinstance(verbosity, int) or verbosity < 0:
            raise ValueError("a run's verbosity is a count of -v, 0 or more")
        # Before anything is taken: a second view would save the terminal's settings with the first's echo off and put
        # those back last, and leave its standard error the first's writer.
        key = _key(job)
        if key in _VIEWS:
            raise RuntimeError(TWICE.format(job=job.id))
        record = job.read()
        machines = record.get("machines") or []
        started = record.get("started")
        self.job = job
        self._stream = stream if stream is not None else sys.stdout
        self._errors = sys.stderr
        self._clock = clock
        self._cancelled = cancelled
        named = dict(names or {})
        self.run = model.Run(declared, clock(), {str(machine): named.get(str(machine)) for machine in machines})
        self.view = View(
            self.run,
            started_at=float(started) if isinstance(started, (int, float)) else time.time(),
            title=title,
            log=str(record.get("log") or ""),
            host=host,
            check=check,
        )
        self.mode = STREAM if verbosity else LIVE if live_region.can_draw(self._stream) else LINES
        self.display = Display(self._stream, self._errors)
        # The region, on the stream given (tui/live_region.py's Output would draw on sys.stdout): with no frame, where
        # the run is not drawn, it only wraps the display, which then prints as it is.
        drawn = self.mode == LIVE
        self.color = drawn and _color_on()
        self.region = live_region.LiveRegion(self.display, frame=self._frame if drawn else None, stream=self._stream)
        self.ended = False
        self._above: _Above | None = None
        job.listen(self._heard)
        # The job's view from the moment it listens: one that fails before has taken nothing, and may be made again.
        _VIEWS[key] = self
        self.region.start()
        if drawn:
            self._above = _Above(self.display, self._errors)
            sys.stderr = self._above
            self.region.redraw()
            self.region.spin()

    # --- what it reads ------------------------------------------------------------------------------------------
    def _width(self) -> int:
        """The stream's width, for what is said above the region and the summary: WIDTH where it is no terminal."""
        try:
            return os.get_terminal_size(self._stream.fileno()).columns
        except (OSError, ValueError, AttributeError, io.UnsupportedOperation):
            return WIDTH

    def _look(self, now: float) -> None:
        """The model told once that a person stopped the run, as soon as this view sees it."""
        cancelled = self._cancelled
        if cancelled is not None and cancelled.is_set() and self.run.stopping is None and self.run.ended is None:
            self.run.cancel(now)

    def _frame(self, cols: int, rows: int, glyph: str) -> list[str]:
        """The region's lines, as the region asks for them, under the display's lock: the view's frame now, and the
        terminal's title."""
        now = self._clock()
        self._look(now)
        self.region.title(self.view.title_text(now))
        return self.view.frame(now, cols, rows, glyph, self.color)

    def _print(self, lines: list[tuple[str, str]]) -> None:
        """Lines of the run's own, (coloured, plain) each, above the region where it is drawn: coloured where the
        palette is on."""
        for coloured, plain in lines:
            self.display.display(coloured if self.color else plain)

    # --- each event ---------------------------------------------------------------------------------------------
    def _heard(self, event: jobs.Event) -> None:
        """One event of the run, as the job hands it: fed to the model, and said as this view says a run, whole: a
        signal that comes meanwhile is delivered once it is said (live_region.uninterrupted), so that no line the view
        has counted as said is lost to the ladder's end at once."""
        if self.ended:
            return
        with live_region.uninterrupted(), self.display._lock:
            now = self._clock()
            self._look(now)
            if self.mode == STREAM:
                self._print([(streamed(event), streamed(event))])
            waited = self.run.waiting_for_you()
            try:
                self.run.feed(event, now)
            except (ValueError, TypeError) as error:
                said = f"{event.kind} {event.machine or 'the run'}: {event.text}"
                refused = REFUSED.format(event=one_line(said, None), error=one_line(str(error), None))
                self.display.display(refused, stderr=True)
            if self.mode != STREAM:
                self._print(self.view.above(self._width(), self.color))
            ending = event.kind == "end" and event.machine is None
            if self.mode == LINES:
                self._print([(line, line) for line in self.view.ended_lines(now)])
            elif self.mode == LIVE and not ending:
                self.region.redraw()
            if not waited and self.run.waiting_for_you():
                self.region.bell()
            if ending:
                self._end(now)

    # --- a question -------------------------------------------------------------------------------------------
    def aside(self) -> contextlib.AbstractContextManager[None]:
        """The region out of the way of a question asked inside, as the region steps aside for a prompt
        (tui/live_region.py's LiveRegion.aside): erased and paused, the echo on; after, the echo off again and the
        region drawn once the run has worked half a second more. Where the run is not drawn, nothing moves. A question
        finds the view by its Context's job: Live.of(context.job)."""
        return self.region.aside()

    # --- the end ------------------------------------------------------------------------------------------------
    def close(self) -> None:
        """The region gone, the echo back and standard error the run's own again; what was written to it after its
        last whole line written there now. Nothing more is drawn: the run's end does it, and a second call does
        nothing."""
        self.region.finish()
        above, self._above = self._above, None
        if above is None:
            return
        if sys.stderr is above:
            sys.stderr = self._errors
        rest = above.rest()
        if rest:
            self.display.display(rest, stderr=True, newline=False)

    def _end(self, now: float) -> None:
        """The run's end: the title, the region gone, the summary and NEXT STEPS after it, and the bell; with -v, NEXT
        STEPS alone."""
        self.ended = True
        if self.mode == LIVE:
            self.region.title(self.view.title_text(now))
        self.close()
        if self.mode != STREAM:
            width = self._width()
            summary = self.view.summary(now, width, self.color)
            plain = self.view.summary(now, width, False)
            self._print([("", ""), *zip(summary, plain, strict=True)])
        steps = self.run.next_steps()
        if steps:
            block = banner(steps, NEXT_STEPS).split("\n")
            self._print([(screen.render([(line, NEXT_STEPS_STYLE)], self.color), line) for line in block])
        self.region.bell()
