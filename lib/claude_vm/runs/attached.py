"""A run in the command's own process: a verb's body on one asyncio loop, and a stated end on Ctrl-C, a closed window
and a kill (docs/architecture/engine.md §16.8).

`run(job, body)` gives the body, a coroutine function, a `Context`, and ends the run as the ladder says:

- Ctrl-C, once: the run's `cancelled` and `stopping` are set, which a workflow checks between its steps and a blocking
  call in a thread is handed; the stop is recorded, so the job reads `cancelling`; the tree under this process is
  remembered, each process with its start, before anything is signalled; each Ansible child's process group gets
  SIGINT, but one started inside a shield, which gets it once that shield is done; and a line says the run is stopping.
  A phase in `shield(reason)`, one that cannot be cancelled, is finished first, and the line says so. A question up
  (`typed`) ends there: the first Ctrl-C at a prompt ends the run, never left waiting for an answer. Once the body has
  ended and every child it started with it, what is left is ended at once; STOP_GRACE seconds after the Ctrl-C,
  whatever still runs is ended, and the body with it. The record says `cancelled`, and the process exits 130.
- Ctrl-C, twice: the tree is ended at once, from the signal's handler itself, so that a step stuck in the loop cannot
  hold it.
- SIGHUP or SIGTERM, a closed window or a kill: the tree is ended with that signal, the record says `cancelled` with
  the signal as its reason, and the process puts the signal back to its default and dies of it. After a Ctrl-C, the
  signal is what the record says, and what ignores it is killed SETTLE seconds after it, not at the Ctrl-C's grace.
- An error the body did not handle: the tree is ended, the record says `failed`, and the error is raised again.
- A body that returns: what it still holds of its children is ended, and the record says `succeeded` for 0, `failed`
  for any other status, and `cancelled` for 130; but `declined` for one that said, by `Context.decline`, that the
  person's answer ended it, and then returned a status other than 0 and 130 (destroy's no, exit 1): a body says it
  declined, and an exit status alone never makes it.
- SIGKILL: nothing is written, and the job reads `lost` once its lock is free (runs/job.py).

While the run lives, a child the terminal stopped for asking a question there (runs/children.py's asking: SIGTTIN or
SIGTTOU, a passphrase, a host key to accept or a second factor asked by the `ssh` of a person's own ProxyJump, a
question of `git`'s or `sudo`'s; route.py's `ssh` too, in a group of its own) is said at once, in a line that names
its program, the leader of the group the terminal stopped, and, where that is `ssh`, what to do; and it is ended with
what is under it: a run answers no question at the terminal, and a child stopped there would hold its step to the
run's end. The step that waited on it sees it killed, its machine's failure. A program that made a process group of
its own under one is not seen: it is no child of the run's for waitid to read, and the run reads no process table for
it. Nothing the engine starts makes one, but a program of the person's own ssh configuration may (a ProxyCommand under
GNU `timeout` without --foreground): its step then waits in silence until its own bound ends it, or a Ctrl-C does, and
fails as a silence, not as a question. A child meant to outlive the run (children.outlive: the editor, a page's opener)
is never ended: it is left out of every tree the ladder remembers and ends.

On every path but SIGKILL every child of the registry has ended or been ended before the loop closes, since asyncio
kills an `ansible-playbook` it still holds when its loop closes and leaves the playbook's detached task workers running;
the body's asynchronous generators still suspended are closed, for SETTLE seconds at most; the run's directory is
removed; the exit record is written, which has the live view say the run's last words; and then the terminal is put
back as it was found, its echo with it, and for a run a Ctrl-C or the ladder stopped what was typed and not read is
thrown away (TCSAFLUSH), never left for the shell. Each Ctrl-C the ladder handles throws away what was typed and not
read yet, as the terminal's own Ctrl-C does where NOFLSH is off (tui/live_region.py's discard_typed). Then nothing
holds the process: the engine's blocking calls (asyncio.to_thread) run on daemon threads of the ladder's own, the
loop's default executor, which are never joined, and where one is still in a call the process ends at once (os._exit)
once its streams are flushed, an error first said as Python says one, but a line the printer would not write (its
Unwritten), said in one line as cli/main.py says it, never a traceback. A signal ignored on the way in, as `nohup` or a
shell's `&` leaves one, stays ignored. No child subreaper and no systemd scope: a shared connection's master daemonises
out of the tree, so no end here reaches it, where under a subreaper one run's end would cut another run's connection.

The tree is the process table's (claude_vm.proc), read with the interrupts held off, and only to end it: nothing here
decides a job's state from it. No thread blocks a signal: a process forked from a thread starts with that thread's
mask, which stays through exec, so a program a call in a thread starts (route.py's `ssh`, a vendor's tool) would never
take a Ctrl-C, a hangup or a kill. A signal the kernel hands another thread still runs its handler in the run's thread,
wherever that is; so while the table is read and what it leads to signalled, and while the run takes its outcome and
writes its record, the handler only takes note, and each signal noted is handled as it came once that is done
(`_held`): the hold is real whatever thread is alive, a body's own pool and asyncio's watcher on macOS among them. No
hold runs the loop, so no code of the body's runs held, and nothing it starts takes the interrupts blocked: the close's
loop, which ends the watch and closes the body's generators, runs unheld, where a second Ctrl-C ends the tree and a
SIGTERM or SIGHUP is what the run dies of, as anywhere else. While the ladder runs, Python's and asyncio's own messages
stay off the person's terminal: an end Python drops (an Abandoned raised in a fork's hook) and the loop's reports of
what the ladder ended or left (a task left pending, a callback a cut left half done), which the run's text log (card
C12) takes. An error of the ladder's own callbacks (a Ctrl-C's stop, the grace's end, the wake) is never one of them:
each says its own, in a line of aw's own. Standard library only; asyncio, termios and claude_vm.proc are imported
inside the functions that use them.
"""

from __future__ import annotations

import contextlib
import itertools
import os
import signal
import sys
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_vm.runs import children, rundir
from claude_vm.runs import job as jobs
from claude_vm.schema import printer
from claude_vm.schema.words import EXITS
from claude_vm.text import plain_text
from claude_vm.tui import live_region

if TYPE_CHECKING:
    import asyncio

# How long a run stopped by Ctrl-C gives what it started to end by itself before what is left is ended: today's MCP
# jobs' STOP_GRACE (lib/claude_vm/mcp/jobs.py), which tests/unit/test_aw_attached.py holds equal.
STOP_GRACE = 30
# What ends a run from outside: a kill, and the terminal closing. With SIGINT, the signals the ladder takes.
OUTSIDE = (signal.SIGTERM, signal.SIGHUP)
TAKEN = (signal.SIGINT, *OUTSIDE)
# How long a step's own end is waited for: a body cancelled, a child signalled from outside or held at the run's end.
SETTLE = 5
# How often what is left of the run's children is looked at while the run waits for them.
POLL = 0.05
# The name each thread of the ladder's own carries, with its number.
THREADS = "aw-run"
# How much a question's read takes at once: a line in a terminal's cooked mode, a key or a few in raw mode.
TYPED = 4096

# The lines a Ctrl-C says, on standard error.
STOPPING = "aw: stopping: what the run started has {grace} s to end, then it is ended; Ctrl-C again ends it now"
FINISHING = "aw: stopping once {reason} is finished: Ctrl-C again ends it now"
# What is said where the run's work did not end once it was cancelled, and is left as it is.
LEFT = "aw: the run's work did not end within {settle} s of being stopped, and is left unfinished"
# How often the run looks for a child the terminal has stopped, and what it says of one.
ASKING_POLL = 0.2
# The program named is the leader of the group the terminal stopped, which is what the watch sees; what to do is said
# for `ssh` alone, whose questions are the person's own keys and jumps.
ASKED = (
    "aw: {child} stopped at the terminal to ask a question there (a passphrase, a host key to accept, a password),"
    " which a run never answers: it is ended, and its machine fails"
)
ASKED_BY_SSH = (
    ". Hold the key in an agent (ssh-add), give a ProxyJump of yours what it asks for in its own settings, or connect"
    " through it once by hand to accept its host key; then run it again"
)
# What a question is read from: a terminal or a pipe, which Linux's epoll can wait on. A regular file or /dev/null it
# refuses, where macOS's kqueue would take either and the read end at once.
UNASKABLE = "a question is read from a terminal or a pipe: descriptor {fd} is {what}, which nothing can be typed into"
# What the ladder says where one of its own callbacks failed.
OWN_ERROR = "aw: the run's own {what} failed: {error}"
# Why a run ended as it did, in its record.
BY_CTRL_C = "Ctrl-C"
BY_CTRL_C_TWICE = "Ctrl-C twice"
DONE = "done"
# Who stopped a run, in its record's stop.
BY_TERMINAL = "terminal"

Body = Callable[["Context"], Awaitable["int | None"]]


class Abandoned(KeyboardInterrupt):
    """The run ends at once - Ctrl-C twice, SIGHUP or SIGTERM - raised from the signal's handler once the tree is
    ended, wherever the run's thread is in the loop: a KeyboardInterrupt, the one kind asyncio lets out of its loop from
    a task or a callback alike. `run` takes it; a body never catches KeyboardInterrupt."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


class _Threads(ThreadPoolExecutor):
    """The loop's default executor, where asyncio.to_thread runs the engine's blocking calls: each call on a daemon
    thread of its own, never joined, so a call still running when the run has ended holds nothing; and each call made
    with no signal blocked, whatever its maker held, since a program it starts (route.py's `ssh`, a vendor's tool)
    inherits its thread's mask through exec and would take no Ctrl-C, hangup or kill. A signal the kernel hands such a
    thread runs its handler in the run's thread all the same (the ladder's `_held`). A ThreadPoolExecutor by kind alone,
    as asyncio's set_default_executor asks: none of its pool is used, and nothing of it is registered with the
    interpreter's exit, which joins a pool's threads."""

    def __init__(self) -> None:
        super().__init__(thread_name_prefix=THREADS)
        self._calling: set[threading.Thread] = set()
        self._guard = threading.Lock()
        self._numbers = itertools.count(1)
        self._shut = False

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        future: Future = Future()
        with self._guard:
            if self._shut:
                raise RuntimeError("cannot schedule new futures after shutdown")
            thread = threading.Thread(
                target=self._call, args=(future, fn, args, kwargs), name=f"{THREADS}-{next(self._numbers)}", daemon=True
            )
            self._calling.add(thread)
        try:
            thread.start()
        except BaseException:
            with self._guard:
                self._calling.discard(thread)
            raise
        return future

    def _call(self, future: Future, fn: Callable[..., Any], args: tuple, kwargs: dict) -> None:
        # A thread starts with its maker's mask, which a call made while the run's thread holds the interrupts off (its
        # close) would carry into every program it starts: none blocked, before anything of the call runs.
        signal.pthread_sigmask(signal.SIG_SETMASK, ())
        try:
            if future.set_running_or_notify_cancel():
                try:
                    result = fn(*args, **kwargs)
                except BaseException as error:
                    future.set_exception(error)
                else:
                    future.set_result(result)
        finally:
            with self._guard:
                self._calling.discard(threading.current_thread())

    def calling(self) -> int:
        """How many of its threads are still in a call."""
        with self._guard:
            return len(self._calling)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        """No call is taken from now on; none still running is waited for, whatever wait says."""
        with self._guard:
            self._shut = True


@dataclass(eq=False)
class _Up:
    """A shield up: why, and the run's children already running when it began, which it does not hold."""

    reason: str
    before: tuple[children.Child, ...]


class Context:
    """What a verb's body is given: its job (for the SSH runner and children.start), `cancelled` and `stopping`,
    `shield`, `typed`, `emit`, `decline`, the children the run still holds, and the run's directory."""

    def __init__(self, job: jobs.Job, directory: Path | None, ladder: _Ladder) -> None:
        import asyncio

        self.job = job
        self.directory = directory
        # Set by the first Ctrl-C: a workflow checks it between its steps, or waits on it.
        self.cancelled = asyncio.Event()
        # Set with cancelled, and whenever the run is to end: what a blocking call in a thread is handed to stop on,
        # such as the Proxmox client's follow; never handed to one inside a shield, which finishes.
        self.stopping = threading.Event()
        self._ladder = ladder

    def emit(self, kind: str, machine: str | None, text: str, **data: object) -> jobs.Event:
        """One event of the run (runs/job.py)."""
        return self.job.emit(kind, machine, text, **data)

    def decline(self, why: str) -> None:
        """The person's answer ends the run, why said as its end: once the body returns a status other than 0 and 130
        (exit 1, as today's), the record says `declined`, never `failed`, and the view says it as the answer it is. A
        Ctrl-C, a signal or an error the body raises ends the run as it would have, and a body that goes on and returns
        0 succeeded."""
        if not isinstance(why, str):
            raise TypeError(f"a run declined says why in a str, not a {type(why).__name__}")
        self._ladder.declined = why

    def live(self, kind: str | None = None) -> list[children.Child]:
        """The children the run started that still run, of that kind or of every kind (runs/children.py)."""
        return children.live(self.job, kind)

    @contextlib.asynccontextmanager
    async def shield(self, reason: str) -> AsyncIterator[None]:
        """A phase that cannot be cancelled, such as a deletion already sent: a Ctrl-C that comes during it lets it
        finish first and says so, an Ansible child started inside it has the Ctrl-C's SIGINT only once it is done, and
        the grace's end waits for it; only a second Ctrl-C or a signal from outside ends it. Never begun once the run is
        stopping: that raises asyncio.CancelledError."""
        if self.cancelled.is_set():
            import asyncio

            raise asyncio.CancelledError(f"the run is stopping: {plain_text(reason)} is not begun")
        ladder = self._ladder
        up = _Up(reason, tuple(children.live(self.job)))
        ladder.shields.append(up)
        try:
            yield
        finally:
            ladder.shields.remove(up)
            ladder.shield_done()

    async def typed(self, fd: int = 0, most: int = TYPED) -> bytes:
        """What the person types on fd, read on the loop, never in a thread: the bytes there once fd can be read - a
        line in a terminal's cooked mode, a key in raw mode - or b"" at its end, where fd is a terminal or a pipe. Any
        other descriptor - a regular file, /dev/null, as standard input is in a detached run or under `< file` - is
        refused before anything waits, ValueError in a sentence of aw's own (UNASKABLE): Linux's epoll cannot wait on
        it, and macOS's kqueue would end the read at once, so the two systems would differ. A question is up while it
        waits: the first Ctrl-C ends the run there, as the owner's answer 4 says, the read raising
        asyncio.CancelledError and the run stopping, rather than waiting on for an answer. A prompt that reads the
        terminal raw, where Ctrl-C is a byte, sends this process SIGINT for it. Never begun once the run is stopping:
        asyncio.CancelledError. One question at a time on a descriptor: a second, while the first waits, is
        RuntimeError."""
        import asyncio

        if isinstance(fd, bool) or not isinstance(fd, int) or fd < 0:
            raise TypeError("a question is read from a descriptor, by its number")
        if isinstance(most, bool) or not isinstance(most, int) or most < 1:
            raise ValueError("a question's read takes at least one byte")
        if self.cancelled.is_set():
            raise asyncio.CancelledError("the run is stopping: no question is asked")
        what = _unaskable(fd)
        if what is not None:
            raise ValueError(UNASKABLE.format(fd=fd, what=what))
        reading = self._ladder.reading
        if fd in reading:
            raise RuntimeError(f"descriptor {fd} is already read for a question: one question at a time")
        loop = asyncio.get_running_loop()
        waiting: asyncio.Future[bytes] = loop.create_future()

        def readable() -> None:
            if waiting.done():
                return
            try:
                got = os.read(fd, most)
            except BlockingIOError:
                return
            except OSError as error:
                waiting.set_exception(error)
            else:
                waiting.set_result(got)

        loop.add_reader(fd, readable)
        reading[fd] = waiting
        try:
            return await waiting
        finally:
            reading.pop(fd, None)
            loop.remove_reader(fd)


def _unaskable(fd: int) -> str | None:
    """What fd is, where a question cannot be read from it; None for a terminal, a pipe or a socket, which every loop
    can wait on."""
    import stat

    found = os.fstat(fd)
    if stat.S_ISFIFO(found.st_mode) or stat.S_ISSOCK(found.st_mode) or os.isatty(fd):
        return None
    if stat.S_ISREG(found.st_mode):
        return "a regular file"
    null = os.stat(os.devnull)
    if (found.st_dev, found.st_ino) == (null.st_dev, null.st_ino):
        return os.devnull
    if stat.S_ISDIR(found.st_mode):
        return "a directory"
    return "a device that is no terminal"


def _say(line: str) -> None:
    """line on standard error, where a terminal that has gone cannot fail the run's end."""
    with contextlib.suppress(Exception):
        print(line, file=sys.stderr, flush=True)


def asked_line(program: str) -> str:
    """The line said of a child the terminal stopped to ask a question there: program, the leader of the group it
    stopped, and what to do where that is ssh, by its program's name, which is the SSH runner's kind's (children.SSH):
    never for a child of that kind that runs another program."""
    said = ASKED.format(child=plain_text(program))
    return said + ASKED_BY_SSH if program == children.SSH else said


def _terminal() -> tuple[int, list] | None:
    """Standard input's terminal settings as the run found them, to put back at its end; None for no terminal, and for
    a run started in the background, where the settings are the shell's own of the moment, never the run's to put
    back."""
    import termios

    try:
        fd = sys.stdin.fileno()
        if os.isatty(fd) and live_region.foreground(fd):
            return fd, termios.tcgetattr(fd)
    except (AttributeError, ValueError, OSError, termios.error):
        pass
    return None


def _put_back(found: tuple[int, list] | None, discard: bool) -> None:
    """The terminal's settings as the run found them, the echo among them, after the run's last words: where anything
    changed them, drained (TCSADRAIN), so that the terminal has read those words first; and for a run a Ctrl-C or the
    ladder stopped (discard), flushed (TCSAFLUSH) whatever they are, so that the keys typed meanwhile and not read are
    thrown away, never left for the shell. Only while the run is the terminal's foreground, SIGTTOU held off, so that a
    run a shell put in the background leaves the shell's settings and is not stopped by its own end; and with the run's
    signals held off till it is made (live_region.put_back). A terminal that has gone, after a hangup, is left."""
    if found is None:
        return
    import termios

    fd, settings = found
    with contextlib.suppress(OSError, ValueError, termios.error):
        live_region.put_back(fd, termios.TCSAFLUSH if discard else termios.TCSADRAIN, settings, last=True)


def _flush() -> None:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()


def _die_of(signum: int) -> None:
    """The process ended by signum, at its default, as a shell expects to see a kill or a hangup end it."""
    _flush()
    signal.signal(signum, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signum})
    os.kill(os.getpid(), signum)


def _joined_at_exit() -> bool:
    """Whether a thread Python would wait for at its exit still runs: any but this one that is no daemon."""
    me = threading.current_thread()
    return any(each is not me and not each.daemon and each.is_alive() for each in threading.enumerate())


def _end_now(status: int, error: BaseException | None) -> None:
    """The process ended at once with status, a thread still in a call left as it is: an error the body did not
    handle is said first, as Python says one it ends on, and the status is then 1. A line the printer would not write
    (its Unwritten), which cli/main.py never sees from here, is said as main says it: one line, the code's own fault
    (schema/printer.py's own_fault), never a traceback, exit 1."""
    if error is not None:
        with contextlib.suppress(Exception):
            if isinstance(error, printer.Unwritten):
                print(printer.own_fault(error), file=sys.stderr)
            else:
                sys.excepthook(type(error), error, error.__traceback__)
        status = 1
    _flush()
    os._exit(status)


def _unsaid(_loop: asyncio.AbstractEventLoop, _context: dict[str, Any]) -> None:
    """The loop's exception handler: the loop's own reports of what the ladder ended or left - a task destroyed
    pending, a callback a cut left half done (asyncio's transport of a child whose start a second Ctrl-C cut), a body's
    own callback - kept off the person's terminal while the run is on; the run's text log (card C12) takes them. No
    error of the ladder's own callbacks reaches it: each says its own (_Ladder._own)."""


def _dropping(previous: Callable[[Any], object]) -> Callable[[Any], object]:
    """sys.unraisablehook while the ladder runs: an Abandoned that Python drops where it cannot raise one (a fork's
    hook) is dropped unsaid, the run's end being taken by its wake; anything else goes to the hook there was."""

    def hook(unraisable: Any) -> object:
        if isinstance(getattr(unraisable, "exc_value", None), Abandoned):
            return None
        return previous(unraisable)

    return hook


class _Ladder:
    """One run's end, whichever way it comes."""

    def __init__(self, job: jobs.Job) -> None:
        import asyncio

        self.job = job
        self.threads = _Threads()
        self.loop = asyncio.new_event_loop()
        try:
            self.loop.set_default_executor(self.threads)
            self.loop.set_exception_handler(_unsaid)
            # Done when the run is to end at once: the grace over, Ctrl-C twice, SIGHUP or SIGTERM.
            self.now: asyncio.Future[None] = self.loop.create_future()
            self.ctx = Context(job, rundir.made(job), self)
        except BaseException:
            self.loop.close()
            raise
        self.task: asyncio.Task | None = None
        self.interrupts = 0
        # Why the person's answer ended the run, where the body said so (Context.decline).
        self.declined: str | None = None
        # The signal that ended the run at once while its body ran: SIGINT for a second Ctrl-C, SIGTERM or SIGHUP.
        self.cut: int | None = None
        # SIGTERM or SIGHUP, whenever it came, and when: what the process dies of once its record is written.
        self.dies_of: int | None = None
        self.dies_at: float | None = None
        # Every process found under the run, with its start: a child that ends leaves its own under PID 1.
        self.remembered: dict[int, str] = {}
        self.deadline: float | None = None
        self.shields: list[_Up] = []
        # The grace ended inside a shield: the stop goes on once the shield is done.
        self.overdue = False
        # The Ansible children that have had the first Ctrl-C's SIGINT.
        self.interrupted: set[children.Child] = set()
        # The questions up, each read's future by its descriptor.
        self.reading: dict[int, asyncio.Future[bytes]] = {}
        # From the first moment of its close, a run's handler begins no stop and raises no end at once: it ends the
        # tree, and takes note of what to die of.
        self.closing = False
        # While the table is read and what it leads to signalled, or the record written (_held): how deep, and each
        # signal whose handler ran meanwhile, to be handled as it came once nothing holds them.
        self.holding = 0
        self.noted: list[int] = []
        # What could not read the process table, or record the stop, said at the end.
        self.blind: list[str] = []
        # The children the terminal stopped that were said and ended, and the task that looks for them.
        self.asked: set[children.Child] = set()
        self.watching: asyncio.Task | None = None

    # ---------------------------------------------------------------- the handler, and what it starts ----

    def signalled(self, signum: int, _frame: object) -> None:
        """The handler of TAKEN, in the run's thread. A first Ctrl-C is the loop's to handle; a second, SIGHUP and
        SIGTERM end the tree here and, while the body runs, raise Abandoned, so that nothing stuck holds the end. While
        the interrupts are held (_held) it only takes note, whatever thread the kernel handed the signal to."""
        if self.holding:
            self.noted.append(signum)
            return
        ending = self.closing or (self.task is not None and self.task.done())
        if signum == signal.SIGINT:
            # What was typed and not read yet thrown away, as the terminal's own Ctrl-C throws it away: a live region
            # or a question keeps what the terminal has not written at a Ctrl-C (NOFLSH), and the kernel then keeps
            # what was typed too.
            live_region.discard_typed()
            self.interrupts += 1
            if self.interrupts == 1:
                if not ending:
                    self._soon(self.stop, "stop at a Ctrl-C")
                return
            if self.interrupts > 2:
                return
            self.end_tree(signal.SIGKILL)
        else:
            if self.dies_of is not None:
                return
            self.dies_of = signum
            self.dies_at = self.loop.time()
            self.end_tree(signum)
        if ending:
            return
        if self.cut is None:
            self.cut = signum
        self._soon(self.wake, "end at once")
        # Only out of the loop: anywhere else the run's own code is about to take note of the end.
        if self.loop.is_running():
            raise Abandoned(signum)

    # The hold of the terminal (tui/live_region.py) leaves this handler in place: it throws the keys typed away itself.
    setattr(signalled, live_region.DISCARDS, True)

    def _soon(self, callback: Callable[[], None], what: str) -> None:
        with contextlib.suppress(RuntimeError):  # a loop already closed
            self.loop.call_soon_threadsafe(self._own, callback, what)

    def _own(self, callback: Callable[[], None], what: str) -> None:
        """callback, one of the ladder's own, as the loop runs it: an error of its own said at once in a line of aw's
        own, what it is named by what, never left to the loop's handler, which says nothing it is handed (_unsaid). An
        end at once (Abandoned) goes through, as it would from the loop."""
        try:
            callback()
        except Exception as error:
            _say(OWN_ERROR.format(what=what, error=plain_text(f"{type(error).__name__}: {error}")))

    @contextlib.contextmanager
    def _held(self) -> Iterator[None]:
        """The interrupts held off, in the run's thread, while the table is read and what it leads to signalled, or the
        run's outcome taken and its record written: blocked in this thread, and the handler, which a signal the kernel
        handed another thread still runs here, only taking note meanwhile, so that no end begins inside another half
        way. Once the outermost hold ends, each signal noted is handled as it came. Never around the loop: a body's code
        run inside a hold, and what it started, would take the interrupts blocked."""
        held = signal.pthread_sigmask(signal.SIG_BLOCK, TAKEN)
        self.holding += 1
        try:
            yield
        finally:
            try:
                # One blocked meanwhile reaches the handler here, which still only takes note.
                signal.pthread_sigmask(signal.SIG_SETMASK, held)
            finally:
                self.holding -= 1
            self._take_noted()

    def _take_noted(self) -> None:
        """Each signal noted while the interrupts were held, handled now, in the order it came, once nothing holds them;
        an end at once that one of them raises (Abandoned) raised once every one is handled."""
        ending: Abandoned | None = None
        while self.noted and not self.holding:
            try:
                self.signalled(self.noted.pop(0), None)
            except Abandoned as raised:
                ending = ending or raised
        if ending is not None:
            raise ending

    def remember(self) -> None:
        """The tree under this process added to what is remembered of it, read with the interrupts held off (_held).
        Never a shared master's open, nor what is under it (children.opening): the master it leaves keeps its
        ProxyCommand in the open's group, and no run ends a master; nor a child meant to outlive the run
        (children.outliving), nor what is under it."""
        from claude_vm import proc

        with self._held():
            try:
                leaving = [*children.opening(), *children.outliving()]
                self.remembered.update(proc.snapshot(os.getpid(), leaving=leaving))
            except proc.Blind as error:
                self.blind.append(str(error))

    def end_tree(self, signum: int) -> None:
        """Everything under this process signalled, and what was remembered of it, never this process, nor a child
        meant to outlive the run (children.outliving) or what is under it; the interrupts held off throughout."""
        from claude_vm import proc

        with self._held():
            self.remember()
            proc.end_under(os.getpid(), self.remembered, signum, blind=self.blind.append, leaving=children.outliving())

    def _shielded(self, child: children.Child) -> bool:
        """Whether child began inside a shield still up."""
        return any(child not in up.before for up in self.shields)

    def _due(self) -> list[children.Child]:
        """The Ansible children owed the first Ctrl-C's SIGINT now: those that have not had it, but one started inside
        a shield still up."""
        return [
            child
            for child in children.live(self.job, children.ANSIBLE)
            if child not in self.interrupted and not self._shielded(child)
        ]

    def _interrupt(self, due: list[children.Child]) -> None:
        for child in due:
            self.interrupted.add(child)
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(child.pid, signal.SIGINT)

    def _record_stop(self) -> None:
        """The stop in the job's record, so it reads `cancelling` through the grace; a stop already recorded (a
        cancel's) is kept."""
        stop = {"by": BY_TERMINAL, "at": time.time()}
        try:
            self.job._change(lambda record: record if record.get("stop") else {**record, "stop": stop})
        except Exception as error:
            self.blind.append(f"the stop could not be recorded in job {self.job.id}'s record: {plain_text(str(error))}")

    def stop(self) -> None:
        """Ctrl-C, once: cancelled and stopping set, the tree remembered, the stop recorded, SIGINT to each Ansible
        child's group that is not inside a shield, a line, the grace; and a question up ended."""
        if self.ctx.cancelled.is_set() or self.cut is not None or self.task is None or self.task.done():
            return
        self.ctx.cancelled.set()
        self.ctx.stopping.set()
        self.remember()
        self._record_stop()
        self._interrupt(self._due())
        # At a question the line begins on a line of its own, below what the person was typing.
        at = "\n" if self.reading else ""
        if self.shields:
            _say(at + FINISHING.format(reason=plain_text(self.shields[0].reason)))
        else:
            _say(at + STOPPING.format(grace=STOP_GRACE))
        self.deadline = self.loop.time() + STOP_GRACE
        self.loop.call_at(self.deadline, self._own, self.grace_over, "end of the grace")
        for waiting in list(self.reading.values()):
            # The first Ctrl-C at a prompt ends the run there (the owner's answer 4): no answer is waited for.
            waiting.cancel("the run was stopped at its question")

    def shield_done(self) -> None:
        """A shield ended: the Ansible children it held have the Ctrl-C's SIGINT now, and an overdue grace ends."""
        if self.ctx.cancelled.is_set() and self.cut is None:
            due = self._due()
            if due:
                self.remember()
                self._interrupt(due)
        if self.overdue and not self.shields:
            self.grace_over()

    def grace_over(self) -> None:
        """The grace's end: what is left is ended, and the body with it; inside a shield, once the shield is done."""
        if self.shields:
            self.overdue = True
            return
        self.overdue = False
        self.end_tree(signal.SIGKILL)
        self.wake()

    def wake(self) -> None:
        self.ctx.stopping.set()
        if not self.now.done():
            self.now.set_result(None)

    async def watch(self) -> None:
        """For as long as the loop runs: each child the terminal has stopped for asking a question there
        (children.asking: the registry's, and route.py's `ssh` in a group of its own), said at once and ended, its tree
        and its group. A process that made a group of its own under one, which the terminal stops alone, is not looked
        for: it is no child of this process's for waitid to read, and the watch reads no process table. Once only for
        each; one the loop's look cannot read is said at the end, and the look ends."""
        import asyncio

        from claude_vm import proc

        while True:
            try:
                for child, _signum in children.asking(self.job):
                    if child in self.asked:
                        continue
                    self.asked.add(child)
                    _say(asked_line(child.program))
                    with self._held():
                        proc.end(child.pid, signum=signal.SIGKILL, blind=self.blind.append)
            except Exception as error:
                self.blind.append(f"a child stopped at the terminal could not be looked for: {plain_text(str(error))}")
                return
            await asyncio.sleep(ASKING_POLL)

    def _unwatch(self) -> None:
        """The look for a child the terminal stopped ended, before the loop closes."""
        import asyncio

        watching = self.watching
        if watching is None or watching.done():
            return
        watching.cancel()
        with contextlib.suppress(Exception):
            self.loop.run_until_complete(asyncio.wait({watching}, timeout=SETTLE))

    # ---------------------------------------------------------------- the run ----

    async def until_done(self) -> None:
        """The body's end: by itself, or cancelled once the run is to end at once."""
        import asyncio

        assert self.task is not None
        await asyncio.wait({self.task, self.now}, return_when=asyncio.FIRST_COMPLETED)
        if not self.task.done():
            self.task.cancel()
            await asyncio.wait({self.task}, timeout=SETTLE)

    async def _while_live(self, until: Callable[[], float]) -> None:
        import asyncio

        while children.live(self.job) and self.loop.time() < until():
            await asyncio.sleep(POLL)

    def _by(self, deadline: float) -> float:
        """deadline, or SETTLE seconds after a SIGTERM or SIGHUP that came since the Ctrl-C, whichever is sooner."""
        return deadline if self.dies_at is None else min(deadline, self.dies_at + SETTLE)

    async def settle(self) -> None:
        """What is left of the run's children ended, as the way it ended says, and none of the registry left running."""
        from claude_vm import proc

        raised = self._raised()
        if self.cut is None and self.ctx.cancelled.is_set():
            deadline = self.deadline if self.deadline is not None else self.loop.time()
            await self._while_live(lambda: self._by(deadline))
            self.end_tree(signal.SIGKILL)
        elif self.cut in OUTSIDE:
            # Each was given the signal that ended the run: what ignores it is killed after a moment.
            moment = self.loop.time() + SETTLE
            await self._while_live(lambda: moment)
            self.end_tree(signal.SIGKILL)
        elif self.cut is None and raised is not None and not isinstance(raised, SystemExit):
            self.end_tree(signal.SIGKILL)
        with self._held():
            for child in children.live(self.job):
                proc.end(child.pid, remembered=self.remembered, signum=signal.SIGKILL, blind=self.blind.append)
        moment = self.loop.time() + SETTLE
        await self._while_live(lambda: moment)

    def _drive(self, make: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """make()'s coroutine run on the loop to its end. An Abandoned from a handler, or the body's own
        KeyboardInterrupt or SystemExit, leaves the loop wherever it was raised: the loop goes on, the coroutine made
        again where it was the one that raised."""
        task = self.loop.create_task(make())
        while True:
            try:
                self.loop.run_until_complete(task)
                return
            except (KeyboardInterrupt, SystemExit):
                if task.done():
                    task = self.loop.create_task(make())

    def _left(self) -> None:
        """The body's work still pending once settled - one that would not take its cancel - said in a line of aw's
        own. asyncio's own report of it, once it is destroyed pending, goes to the loop's handler, which says nothing
        (_unsaid)."""
        if self.task is not None and not self.task.done():
            _say(LEFT.format(settle=SETTLE))

    def _raised(self) -> BaseException | None:
        """What the body raised, read so that asyncio never says it went unread; None where it raised nothing."""
        task = self.task
        if task is None or not task.done() or task.cancelled():
            return None
        return task.exception()

    def _outcome(self) -> tuple[str, int, str, BaseException | None]:
        cancelled = EXITS["cancelled"]
        raised = self._raised()
        if self.cut == signal.SIGINT:
            return "cancelled", cancelled, BY_CTRL_C_TWICE, None
        if self.cut is not None:
            return "cancelled", 128 + self.cut, signal.Signals(self.cut).name, None
        if self.ctx.cancelled.is_set():
            return "cancelled", cancelled, BY_CTRL_C, None
        task = self.task
        if task is None or not task.done() or task.cancelled():
            return "failed", 1, "the run's work ended unfinished", None
        if isinstance(raised, KeyboardInterrupt):
            return "cancelled", cancelled, BY_CTRL_C, None
        status: object
        if isinstance(raised, SystemExit):
            status = raised.code if isinstance(raised.code, int) else 0 if raised.code is None else 1
        elif raised is not None:
            return "failed", 1, f"an error the run did not handle: {type(raised).__name__}: {raised}", raised
        else:
            status = task.result()
        if status is None:
            status = 0
        if not isinstance(status, int) or isinstance(status, bool):
            mistake = TypeError(f"a run's body returns its exit status, an int, not a {type(status).__name__}")
            return "failed", 1, f"an error the run did not handle: {mistake}", mistake
        if status == 0:
            return "succeeded", 0, DONE, None
        if status == cancelled:
            return "cancelled", cancelled, BY_CTRL_C, None
        if self.declined is not None:
            return "declined", status, self.declined, None
        return "failed", status, f"exit {status}", None

    def outcome(self) -> tuple[str, int, str, BaseException | None]:
        """The run's outcome, its exit status, why, and the error to raise again. A SIGTERM or SIGHUP after a Ctrl-C,
        which the process dies of, is what the record says over the Ctrl-C."""
        found = self._outcome()
        if self.dies_of is not None and found[0] == "cancelled" and found[2] in (BY_CTRL_C, BY_CTRL_C_TWICE):
            return "cancelled", 128 + self.dies_of, signal.Signals(self.dies_of).name, None
        return found

    def _close_loop(self) -> None:
        """The loop closed, once the body's asynchronous generators still suspended are closed, for SETTLE seconds at
        most: one whose `finally` does not return is left as it is, as a body's work that will not take its cancel is,
        and asyncio's report of it goes to the loop's handler (_unsaid)."""
        import asyncio

        with contextlib.suppress(Exception):
            closing = self.loop.create_task(self.loop.shutdown_asyncgens())
            self.loop.run_until_complete(asyncio.wait({closing}, timeout=SETTLE))
        self.loop.close()

    def _end(self, outcome: str, status: int, why: str) -> None:
        """The run's directory removed, then its exit record written: the last thing a run does, but for putting the
        terminal's settings back (_put_back) once what the record's end said is on the screen."""
        try:
            if not rundir.remove(self.job):
                _say(f"aw: the run's directory could not be removed whole (job {self.job.id})")
        except (OSError, ValueError, RuntimeError) as error:
            _say(f"aw: the run's directory was left (job {self.job.id}): {plain_text(str(error))}")
        try:
            self.job.emit("end", None, why, outcome=outcome, exit=status)
        except Exception as error:
            _say(f"aw: the end of the run could not be written into job {self.job.id}'s record: {error}")
        for said in self.blind:
            _say(f"aw: {said}")

    async def _body(self, body: Body) -> int | None:
        return await body(self.ctx)

    def run(self, body: Body, mask: set[signal.Signals] | None) -> int:
        found = _terminal()
        # The body called inside its task: whatever it raises, even as it is called, is the run's to end on.
        self.task = self.loop.create_task(self._body(body))
        self.watching = self.loop.create_task(self.watch())
        previous = {}
        error: BaseException | None = None
        unraisable = sys.unraisablehook
        sys.unraisablehook = _dropping(unraisable)
        try:
            for signum in TAKEN:
                # One ignored on the way in - nohup's SIGHUP, or SIGINT for a job a shell started with & - stays so.
                if signal.getsignal(signum) != signal.SIG_IGN:
                    previous[signum] = signal.signal(signum, self.signalled)
            if mask is not None:
                # runs.run held the signals off from before the job was made: one that came meanwhile is the
                # handler's now, and from here every one is.
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            self._drive(self.until_done)
            self._drive(self.settle)
            self._left()
        finally:
            self.closing = True
            try:
                # A call in a thread still waiting stops at its next look.
                self.ctx.stopping.set()
                # The loop's last runs, unheld: what of the body's runs there (a task left pending, a generator's
                # close) runs with no signal blocked, as everything it starts does, and a signal meanwhile is the
                # handler's at once, which from here only ends the tree and takes note of what to die of (closing).
                self._unwatch()
                self._close_loop()
                # The record held as a read of the table is: a signal meanwhile, in whichever thread, is noted, then
                # handled once the record is written, which takes note of what to die of.
                with self._held():
                    outcome, status, why, error = self.outcome()
                    try:
                        self._end(outcome, status, why)
                    finally:
                        # After the run's last words, which its end said (the summary, NEXT STEPS).
                        _put_back(found, outcome == "cancelled" or self.interrupts > 0)
            finally:
                # Then the handlers the run found are put back.
                for signum, handler in previous.items():
                    # None: a handler no Python installed, which leaves its default to put back.
                    signal.signal(signum, signal.SIG_DFL if handler is None else handler)
                sys.unraisablehook = unraisable
        if self.dies_of is not None:
            _die_of(self.dies_of)
            return 128 + self.dies_of
        if self.threads.calling() or _joined_at_exit():
            # Python would wait for the thread at its exit: up to a call's whole deadline, or for good.
            _end_now(status, error)
        if error is not None:
            raise error
        return status


def _never_began(job: jobs.Job, error: BaseException) -> None:
    """A run whose ladder could not be built: its directory removed and its job dropped, as a run that never began,
    with what the drop left said beside the error."""
    with contextlib.suppress(Exception):
        rundir.remove(job)
    left = jobs.drop(job)
    if left is not None:
        error.add_note(left)


def run(job: jobs.Job, body: Body, *, mask: set[signal.Signals] | None = None) -> int:
    """body run in this process, on one asyncio loop, with a Context for job, which this process made; ended as the
    ladder says, and its exit status: 0, the status the body returned, or 130 for a run a person cancelled. A run ended
    by SIGHUP or SIGTERM dies of it, and an error the body did not handle is raised again, each once the record says
    so; where a call is still running in a thread then, the process ends at once with the status, the error said. A
    ladder that cannot be built leaves no job and no directory. The run's thread is the main thread, the only one a
    signal's handler runs in. mask, where it is given, is the signal mask put back once the ladder's handlers are set:
    runs.run's, which held TAKEN off from before the job was made, so that a signal in between is the ladder's to
    take."""
    if job._held_here() is None:
        raise RuntimeError(f"job {job.id} is not this process's: only the run that made a job runs it")
    try:
        ladder = _Ladder(job)
    except BaseException as error:
        _never_began(job, error)
        raise
    return ladder.run(body, mask)
