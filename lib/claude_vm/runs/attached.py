"""A run in the command's own process: a verb's body on one asyncio loop, and a stated end on Ctrl-C, a closed window
and a kill (docs/architecture/engine.md §16.8).

`run(job, body)` gives the body, a coroutine function, a `Context`, and ends the run as the ladder says:

- Ctrl-C, once: the run's `cancelled` is set, which a workflow checks between its steps; the tree under this process is
  remembered, each process with its start, before anything is signalled; each Ansible child's process group gets
  SIGINT; and a line says the run is stopping. A phase in `shield(reason)`, one that cannot be cancelled, is finished
  first, and the line says so. Once the body has ended and every child it started with it, what is left is ended at
  once; STOP_GRACE seconds after the Ctrl-C, whatever still runs is ended, and the body with it. The record says
  `cancelled`, and the process exits 130.
- Ctrl-C, twice: the tree is ended at once, from the signal's handler itself, so that a step stuck in the loop cannot
  hold it.
- SIGHUP or SIGTERM, a closed window or a kill: the tree is ended with that signal, the record says `cancelled` with
  the signal as its reason, and the process puts the signal back to its default and dies of it.
- An error the body did not handle: the tree is ended, the record says `failed`, and the error is raised again.
- A body that returns: what it still holds of its children is ended, and the record says `succeeded` for 0, `failed`
  for any other status, and `cancelled` for 130.
- SIGKILL: nothing is written, and the job reads `lost` once its lock is free (runs/job.py).

On every path but SIGKILL the terminal is put back as it was found, its echo with it; every child of the registry has
ended or been ended before the loop closes, since asyncio kills an `ansible-playbook` it still holds when its loop
closes and leaves the playbook's detached task workers running; the run's directory is removed; and the exit record is
written last. A signal ignored on the way in, as `nohup` or a shell's `&` leaves one, stays ignored. No child subreaper
and no systemd scope: a shared connection's master daemonises out of the tree, so no end here reaches it, where under
a subreaper one run's end would cut another run's connection.

The tree is the process table's (claude_vm.proc), read with the interrupts held off, and only to end it: nothing here
decides a job's state from it. Standard library only; asyncio, termios and claude_vm.proc are imported inside the
functions that use them.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_vm.runs import children, rundir
from claude_vm.runs import job as jobs
from claude_vm.schema.words import EXITS
from claude_vm.text import plain_text

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

# The lines a Ctrl-C says, on standard error.
STOPPING = "aw: stopping: what the run started has {grace} s to end, then it is ended; Ctrl-C again ends it now"
FINISHING = "aw: stopping once {reason} is finished: Ctrl-C again ends it now"
# Why a run ended as it did, in its record.
BY_CTRL_C = "Ctrl-C"
BY_CTRL_C_TWICE = "Ctrl-C twice"
DONE = "done"

Body = Callable[["Context"], Awaitable["int | None"]]


class Abandoned(KeyboardInterrupt):
    """The run ends at once - Ctrl-C twice, SIGHUP or SIGTERM - raised from the signal's handler once the tree is
    ended, wherever the run's thread is in the loop: a KeyboardInterrupt, the one kind asyncio lets out of its loop from
    a task or a callback alike. `run` takes it; a body never catches KeyboardInterrupt."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


class Context:
    """What a verb's body is given: its job (for the SSH runner and children.start), `cancelled`, `shield`, `emit`, the
    children the run still holds, and the run's directory."""

    def __init__(self, job: jobs.Job, directory: Path | None, ladder: _Ladder) -> None:
        import asyncio

        self.job = job
        self.directory = directory
        # Set by the first Ctrl-C: a workflow checks it between its steps, or waits on it.
        self.cancelled = asyncio.Event()
        self._ladder = ladder

    def emit(self, kind: str, machine: str | None, text: str, **data: object) -> jobs.Event:
        """One event of the run (runs/job.py)."""
        return self.job.emit(kind, machine, text, **data)

    def live(self, kind: str | None = None) -> list[children.Child]:
        """The children the run started that still run, of that kind or of every kind (runs/children.py)."""
        return children.live(self.job, kind)

    @contextlib.asynccontextmanager
    async def shield(self, reason: str) -> AsyncIterator[None]:
        """A phase that cannot be cancelled, such as a deletion already sent: a Ctrl-C that comes during it lets it
        finish first and says so, and the grace's end waits for it; only a second Ctrl-C or a signal from outside ends
        it. Never begun once the run is stopping: that raises asyncio.CancelledError."""
        if self.cancelled.is_set():
            import asyncio

            raise asyncio.CancelledError(f"the run is stopping: {plain_text(reason)} is not begun")
        ladder = self._ladder
        ladder.shields.append(reason)
        try:
            yield
        finally:
            ladder.shields.remove(reason)
            if ladder.overdue and not ladder.shields:
                ladder.grace_over()


def _say(line: str) -> None:
    """line on standard error, where a terminal that has gone cannot fail the run's end."""
    with contextlib.suppress(Exception):
        print(line, file=sys.stderr, flush=True)


def _terminal() -> tuple[int, list] | None:
    """Standard input's terminal settings as the run found them, to put back at its end; None for no terminal."""
    import termios

    try:
        fd = sys.stdin.fileno()
        if os.isatty(fd):
            return fd, termios.tcgetattr(fd)
    except (AttributeError, ValueError, OSError, termios.error):
        pass
    return None


def _put_back(found: tuple[int, list] | None) -> None:
    """The terminal's settings as the run found them, the echo among them, where anything changed them: SIGTTOU held
    off, so that a run a shell put in the background is not stopped by its own end. A terminal that has gone, after a
    hangup, is left."""
    if found is None:
        return
    import termios

    fd, settings = found
    try:
        if termios.tcgetattr(fd) == settings:
            return
        held = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTTOU})
        try:
            termios.tcsetattr(fd, termios.TCSANOW, settings)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, held)
    except (OSError, termios.error):
        pass


def _die_of(signum: int) -> None:
    """The process ended by signum, at its default, as a shell expects to see a kill or a hangup end it."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()
    signal.signal(signum, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signum})
    os.kill(os.getpid(), signum)


class _Ladder:
    """One run's end, whichever way it comes."""

    def __init__(self, job: jobs.Job) -> None:
        import asyncio

        self.job = job
        self.loop = asyncio.new_event_loop()
        # Done when the run is to end at once: the grace over, Ctrl-C twice, SIGHUP or SIGTERM.
        self.now: asyncio.Future[None] = self.loop.create_future()
        self.ctx = Context(job, rundir.made(job), self)
        self.task: asyncio.Task | None = None
        self.interrupts = 0
        # The signal that ended the run at once while its body ran: SIGINT for a second Ctrl-C, SIGTERM or SIGHUP.
        self.cut: int | None = None
        # SIGTERM or SIGHUP, whenever it came: what the process dies of once its record is written.
        self.dies_of: int | None = None
        # Every process found under the run, with its start: a child that ends leaves its own under PID 1.
        self.remembered: dict[int, str] = {}
        self.deadline: float | None = None
        self.shields: list[str] = []
        # The grace ended inside a shield: the stop goes on once the shield is done.
        self.overdue = False
        # From the first moment of its close, a run's handler only takes note.
        self.closing = False
        # What could not read the process table, said at the end.
        self.blind: list[str] = []

    # ---------------------------------------------------------------- the handler, and what it starts ----

    def signalled(self, signum: int, _frame: object) -> None:
        """The handler of TAKEN, in the run's thread. A first Ctrl-C is the loop's to handle; a second, SIGHUP and
        SIGTERM end the tree here and, while the body runs, raise Abandoned, so that nothing stuck holds the end."""
        ending = self.closing or (self.task is not None and self.task.done())
        if signum == signal.SIGINT:
            self.interrupts += 1
            if self.interrupts == 1:
                if not ending:
                    self._soon(self.stop)
                return
            if self.interrupts > 2:
                return
            self.end_tree(signal.SIGKILL)
        else:
            if self.dies_of is not None:
                return
            self.dies_of = signum
            self.end_tree(signum)
        if ending:
            return
        if self.cut is None:
            self.cut = signum
        self._soon(self.wake)
        # Only out of the loop: anywhere else the run's own code is about to take note of the end.
        if self.loop.is_running():
            raise Abandoned(signum)

    def _soon(self, callback: Callable[[], None]) -> None:
        with contextlib.suppress(RuntimeError):  # a loop already closed
            self.loop.call_soon_threadsafe(callback)

    def remember(self) -> None:
        """The tree under this process added to what is remembered of it, read with the interrupts held off."""
        from claude_vm import proc

        held = signal.pthread_sigmask(signal.SIG_BLOCK, proc.HELD)
        try:
            self.remembered.update(proc.snapshot(os.getpid()))
        except proc.Blind as error:
            self.blind.append(str(error))
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, held)

    def end_tree(self, signum: int) -> None:
        """Everything under this process signalled, and what was remembered of it, never this process."""
        from claude_vm import proc

        self.remember()
        proc.end_under(os.getpid(), self.remembered, signum, blind=self.blind.append)

    def stop(self) -> None:
        """Ctrl-C, once: cancelled set, the tree remembered, SIGINT to each Ansible child's group, a line, the grace."""
        if self.ctx.cancelled.is_set() or self.cut is not None or self.task is None or self.task.done():
            return
        self.ctx.cancelled.set()
        self.remember()
        for child in children.live(self.job, children.ANSIBLE):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(child.pid, signal.SIGINT)
        if self.shields:
            _say(FINISHING.format(reason=plain_text(self.shields[0])))
        else:
            _say(STOPPING.format(grace=STOP_GRACE))
        self.deadline = self.loop.time() + STOP_GRACE
        self.loop.call_at(self.deadline, self.grace_over)

    def grace_over(self) -> None:
        """The grace's end: what is left is ended, and the body with it; inside a shield, once the shield is done."""
        if self.shields:
            self.overdue = True
            return
        self.overdue = False
        self.end_tree(signal.SIGKILL)
        self.wake()

    def wake(self) -> None:
        if not self.now.done():
            self.now.set_result(None)

    # ---------------------------------------------------------------- the run ----

    async def until_done(self) -> None:
        """The body's end: by itself, or cancelled once the run is to end at once."""
        import asyncio

        assert self.task is not None
        await asyncio.wait({self.task, self.now}, return_when=asyncio.FIRST_COMPLETED)
        if not self.task.done():
            self.task.cancel()
            await asyncio.wait({self.task}, timeout=SETTLE)

    async def _while_live(self, until: float) -> None:
        import asyncio

        while children.live(self.job) and self.loop.time() < until:
            await asyncio.sleep(POLL)

    async def settle(self) -> None:
        """What is left of the run's children ended, as the way it ended says, and none of the registry left running."""
        from claude_vm import proc

        raised = self._raised()
        if self.cut is None and self.ctx.cancelled.is_set():
            assert self.deadline is not None
            await self._while_live(self.deadline)
            self.end_tree(signal.SIGKILL)
        elif self.cut in OUTSIDE:
            # Each was given the signal that ended the run: what ignores it is killed after a moment.
            await self._while_live(self.loop.time() + SETTLE)
            self.end_tree(signal.SIGKILL)
        elif self.cut is None and raised is not None and not isinstance(raised, SystemExit):
            self.end_tree(signal.SIGKILL)
        for child in children.live(self.job):
            proc.end(child.pid, remembered=self.remembered, signum=signal.SIGKILL, blind=self.blind.append)
        await self._while_live(self.loop.time() + SETTLE)

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

    def _raised(self) -> BaseException | None:
        """What the body raised, read so that asyncio never says it went unread; None where it raised nothing."""
        task = self.task
        if task is None or not task.done() or task.cancelled():
            return None
        return task.exception()

    def outcome(self) -> tuple[str, int, str, BaseException | None]:
        """The run's outcome, its exit status, why, and the error to raise again."""
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
        return "failed", status, f"exit {status}", None

    def _close_loop(self) -> None:
        with contextlib.suppress(Exception):
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.close()

    def _end(self, outcome: str, status: int, why: str) -> None:
        """The run's directory removed, then its exit record written: the last thing a run does."""
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

    def run(self, body: Body) -> int:
        found = _terminal()
        # The body called inside its task: whatever it raises, even as it is called, is the run's to end on.
        self.task = self.loop.create_task(self._body(body))
        previous = {}
        error: BaseException | None = None
        try:
            for signum in TAKEN:
                # One ignored on the way in - nohup's SIGHUP, or SIGINT for a job a shell started with & - stays so.
                if signal.getsignal(signum) != signal.SIG_IGN:
                    previous[signum] = signal.signal(signum, self.signalled)
            self._drive(self.until_done)
            self._drive(self.settle)
        finally:
            self.closing = True
            held = signal.pthread_sigmask(signal.SIG_BLOCK, TAKEN)
            try:
                self._close_loop()
                outcome, status, why, error = self.outcome()
                _put_back(found)
                self._end(outcome, status, why)
            finally:
                # A signal held meanwhile reaches the handler, which takes note of what to die of; then the handlers the
                # run found are put back.
                signal.pthread_sigmask(signal.SIG_SETMASK, held)
                for signum, handler in previous.items():
                    # None: a handler no Python installed, which leaves its default to put back.
                    signal.signal(signum, signal.SIG_DFL if handler is None else handler)
        if self.dies_of is not None:
            _die_of(self.dies_of)
            return 128 + self.dies_of
        if error is not None:
            raise error
        return status


def run(job: jobs.Job, body: Body) -> int:
    """body run in this process, on one asyncio loop, with a Context for job, which this process made; ended as the
    ladder says, and its exit status: 0, the status the body returned, or 130 for a run a person cancelled. A run ended
    by SIGHUP or SIGTERM dies of it, and an error the body did not handle is raised again, each once the record says
    so. The run's thread is the main thread, the only one a signal's handler runs in."""
    if job._held_here() is None:
        raise RuntimeError(f"job {job.id} is not this process's: only the run that made a job runs it")
    return _Ladder(job).run(body)
