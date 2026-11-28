"""A question asked of the person at the run's terminal: a line, a menu, or a hidden answer, with the live region out
of the way; and with nobody there to ask, the question's safe answer (docs/architecture/engine.md §16.8).

`await ask(context, question, default, choices, hidden, machine=...)`, from a verb's body, on the run's loop
(runs/attached.py's Context). Every question has a default, the safe answer it takes where it is not answered, and one
without cannot be built (Question). What it gives back: the default itself where nothing was answered - Enter, the end
of the input, Esc at the menu, or no terminal - and otherwise the answer: the line typed, the value of the choice
picked, or, for a hidden answer, a Secret, whose default is a Secret too.

It asks only where the standard input the command was given is a terminal whose foreground this process is
(`terminal()`, which a verb that asks decides by too, before its run draws anything: engine/init.py). Anything else -
/dev/null, a pipe, a file, a run sent to the background, a detached run, an agent's job - is nobody to ask: the default
is the answer, and one line on standard error says the question was not asked and what it took (NOT_ASKED).
`--attended` changes nothing here: someone at the screen with no terminal is still not asked.

At the terminal, one question at a time (one asked while another is up, by two machines' work at once, waits till that
one has ended), in this order: the run's live view, found by the run's job (runs/live.py's Live.of), steps aside (its
aside(): the region erased and paused, what the view would say kept till the question ends, and the region drawn again
half a second after the answer), a `waiting` event says what the run waits for, keys typed before the question
was there are flushed, the question is written to standard output, and the answer is read. Every read is the run's
Context.typed, on the loop, never in a thread, from the standard input alone - never from the process's controlling
terminal opened by its name, and never through getpass: one Ctrl-C there ends the run (the owner's answer 4). Then an
empty `waiting` event ends that wait alone (runs/model.py), and a `step` event, a line of the machine's work, says what
was answered: the line, the choice's words, or of a hidden answer only that one was given. A hidden answer is in no
event, no record and no line on the screen.

- A line: read in the terminal's cooked mode, its echo on, Enter ending it, and a Ctrl-C keeping what the terminal has
  not read yet (NOFLSH): the question's line shows what was typed before it, `192^C`, never `^C` alone.
- A choice: tui/choose.py's menu where the terminal can draw it (choose.can_choose), picked with the arrow keys
  (choose.asked), its keys read raw, where Ctrl-C is a byte: the terminal's settings, its signal keys among them, are
  put back first, and then the run is sent SIGINT for it, as a Ctrl-C anywhere else. The line the menu leaves says the
  answer (choose.collapsed). Where it cannot be drawn, the choice is typed: a number, a value or its words; one that is
  none is asked once more, saying the choices, and a second is left at the default, said.
- A hidden answer: read in cooked mode with the echo off, and returned as a Secret. Without NOFLSH: with the echo
  off there is nothing typed on the screen to keep, and each signal key throws away what was typed, as it would.

The terminal's settings are put back as they were found once each question ends, however it ends, and each change of
them is TCSAFLUSH's, as getpass makes it: the keys typed before are flushed by the change that sets the question's mode,
and what was typed at the question and not read (the rest of a paste, a hidden answer's second line, the part of a line
a Ctrl-C kept) is discarded by the change back, never left in the terminal's input for the shell once the run has ended.
The change back is made with the run's signals held off (tui/live_region.py's put_back): a second Ctrl-C, a SIGTERM or a
SIGHUP meanwhile ends the run once it is made, never half way, and a run the ladder stopped puts the settings back once
more at its end with what is unread discarded (runs/attached.py). A change that a signal interrupts (the run's first
Ctrl-C, a resize) is made again (tui/live_region.py's set_terminal), never left undone; the change back is made only
while the run is the terminal's foreground, so that a run a shell put in the background is not stopped again by it. A
Ctrl-Z (SIGTSTP) at a line or a hidden answer puts the settings back as found, what was typed and not read thrown away
with them, before the run stops (tui/live_region.py's hold of the terminal); once it goes on (fg) the question's mode is
made again and its words written again, below the shell's lines, and a run continued in the background (bg) stops again
there (SIGTTOU, raised from Python by the hold's Ctrl-Z, never by the SIGCONT's handler: tui/live_region.py's
_once_foreground) till fg; but one killed while it is stopped (kill %1: SIGTERM, then SIGCONT), at a Ctrl-Z or after
bg, ends at once, the ladder's end telling the hold it waits no more (live_region.ending). A Ctrl-\\ (SIGQUIT) is the
ladder's, as a SIGTERM is: the run is cancelled, and the change back throws away what was typed. The edges: at the
menu, whose keys are raw, Ctrl-Z and Ctrl-\\ are keys it has no use for; and a key that lands between two questions,
with the settings as found, is the terminal's own, which throws away what was typed itself.

A choice's words and note are one line of plain text (Choice), as the question's own words are: a name from Proxmox or
a machine never reaches the terminal with its escapes. Standard library and the project's own modules only:
asyncio is imported inside the functions that use it.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import termios
import weakref
from collections.abc import Iterable
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING

from claude_vm.runs import live
from claude_vm.secret import Secret
from claude_vm.text import one_line, visible_text
from claude_vm.tui import choose, live_region

if TYPE_CHECKING:
    import asyncio

    from claude_vm.runs.attached import Context

# A question built without its safe answer.
NO_DEFAULT = "a question is built with its default, the safe answer it takes where nobody answers it"
# What is said, on standard error, of a question not asked.
NOT_ASKED = "aw: not asked, with no terminal to ask at: {question} - {answer}"
# What a step event says of a question not asked.
NOT_ASKED_STEP = "not asked, with no terminal: {answer}"
# What is said of an answer: none given, a hidden one given or not.
LEFT = "left for later"
GIVEN = "given"
NOT_GIVEN = "nothing given"
# A choice typed where the menu cannot be drawn: how it is asked, and what is said of one that is none of them.
TYPED_CHOICE = "A number or a name, Enter for {default}: "
NOT_ONE = "That is not one of them: {choices}."
NOT_ONE_EITHER = "That is not one of them either, so it takes {default}."

# Each run's turn at its terminal, by its Context: one question at a time, a second waiting till the first has ended.
_TURNS: weakref.WeakKeyDictionary[Context, asyncio.Lock] = weakref.WeakKeyDictionary()


@dataclass(frozen=True)
class Choice:
    """One answer of a choice: the value given back, the words shown (the value where none are given) and a note beside
    them. The words and the note are kept as one line of plain text (text.one_line): a name from Proxmox or a machine
    may hold an escape sequence or a newline, which the menu would write to the terminal as it is, and the line it
    leaves, the typed choice and the step event read the same words. The value is given back as it was given."""

    value: str
    text: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        for name in ("value", "text", "note"):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"a choice's {name} is a str")
        if not self.value:
            raise ValueError("a choice has a value to give back")
        object.__setattr__(self, "text", one_line(self.text, None) or one_line(self.value, None))
        object.__setattr__(self, "note", one_line(self.note, None))


@dataclass(frozen=True)
class Question:
    """A question, checked whole before anything is asked: its words, its default - the safe answer, which every
    question has - its choices (each a Choice, or a str that is its own value), and whether its answer is hidden. A
    visible answer's default is a str, and a choice's one of its values, or "" for none; a hidden answer's default is a
    Secret, and a hidden question has no choices, since a menu shows its answers."""

    text: str
    default: str | Secret
    choices: tuple[Choice, ...] = ()
    hidden: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not visible_text(self.text).strip():
            raise ValueError("a question has words to ask")
        if self.default is None:
            raise ValueError(NO_DEFAULT)
        if not isinstance(self.hidden, bool):
            raise TypeError("whether a question's answer is hidden is a bool")
        if isinstance(self.choices, (str, bytes)) or not isinstance(self.choices, Iterable):
            raise TypeError("a question's choices are a sequence of choices")
        choices = tuple(Choice(each) if isinstance(each, str) else each for each in self.choices)
        if not all(isinstance(each, Choice) for each in choices):
            raise TypeError("each of a question's choices is a Choice or a str")
        values = [each.value for each in choices]
        if len(set(values)) != len(values):
            raise ValueError("a question's choices each give back a value of their own")
        object.__setattr__(self, "choices", choices)
        if self.hidden:
            if choices:
                raise ValueError("a hidden question has no choices: a menu shows its answers")
            if not isinstance(self.default, Secret):
                raise TypeError("a hidden question's default is a Secret, as its answer is")
            return
        if not isinstance(self.default, str):
            raise TypeError("a question's default is a str, as its answer is")
        if choices and self.default and self.default not in values:
            raise ValueError("a choice's default is one of its values, or none")

    @property
    def asked(self) -> str:
        """What the question asks, on one line: its last line of words, without the colon that ends a prompt."""
        lines = [line for line in visible_text(self.text).split("\n") if line.strip()]
        return one_line(lines[-1], None).rstrip(":").rstrip()

    def said(self, answer: str | Secret) -> str:
        """What is said of an answer: the line, the choice's words, or of a hidden answer only whether one was given;
        nothing answered, left for later."""
        if self.hidden:
            return NOT_GIVEN if answer is self.default else GIVEN
        if answer == "":
            return LEFT
        for choice in self.choices:
            if choice.value == answer:
                return one_line(choice.text, None)
        return one_line(answer, None)

    def picked(self, typed: str) -> str | None:
        """The value a typed answer picks among the choices: by its number, its value or its words, in any case; None
        for none of them."""
        wanted = typed.strip().casefold()
        for number, choice in enumerate(self.choices, start=1):
            if wanted in (str(number), choice.value.casefold(), choice.text.casefold()):
                return choice.value
        return None

    def menu(self) -> dict:
        """The question as tui/choose.py's menu: its answers, the default marked first."""
        values = [choice.value for choice in self.choices]
        return {
            "title": self.asked,
            "label": "",
            "options": [{"value": each.value, "text": each.text, "note": each.note} for each in self.choices],
            "chosen": values.index(self.default) if self.default in values else 0,
        }


async def ask(
    context: Context,
    question: str,
    default: str | Secret,
    choices: Iterable[Choice | str] = (),
    hidden: bool = False,
    *,
    machine: str | None = None,
) -> str | Secret:
    """The person's answer to question, asked at the run's terminal as the module says, or its default where there is
    nobody to ask or nothing was answered; machine is the machine the question is about, None for the run's own. Never
    begun once the run is stopping: asyncio.CancelledError, as Context.typed."""
    import asyncio

    # The choices as given: the Question makes them a tuple of Choice, refusing a str for the whole of them.
    built = Question(question, default, choices, hidden)  # type: ignore[arg-type]
    if context.cancelled.is_set():
        raise asyncio.CancelledError("the run is stopping: no question is asked")
    fd = terminal()
    if fd is None:
        said = built.said(built.default)
        _say(NOT_ASKED.format(question=built.asked, answer=said))
        context.emit("step", machine, f"{built.asked}: {NOT_ASKED_STEP.format(answer=said)}")
        return built.default
    async with _turn(context):
        if context.cancelled.is_set():
            raise asyncio.CancelledError("the run is stopping: no question is asked")
        view = live.Live.of(context.job)
        with view.aside() if view is not None else contextlib.nullcontext():
            context.emit("waiting", machine, built.asked)
            try:
                answer = await _answered(context, built, fd, sys.stdout)
            finally:
                context.emit("waiting", machine, "")
        context.emit("step", machine, f"{built.asked}: {built.said(answer)}")
    return answer


def _turn(context: Context) -> asyncio.Lock:
    """The run's turn at its terminal: a question asked while another is up (two machines' work at once) waits till
    that one has ended, rather than writing over it and reading its keys."""
    import asyncio

    turn = _TURNS.get(context)
    if turn is None:
        turn = _TURNS[context] = asyncio.Lock()
    return turn


def terminal() -> int | None:
    """Standard input's descriptor where it is a terminal whose foreground this process is; None for anything else.
    Whether there is anyone to ask: ask decides it for each question, and a verb that asks decides by it before its run
    draws anything, never by isatty alone, since the live region's change of the terminal's settings from a run in the
    background (aw init &) has the terminal stop the run (SIGTTOU) before its first question."""
    try:
        fd = sys.stdin.fileno()
        if os.isatty(fd) and os.tcgetpgrp(fd) == os.getpgrp():
            return fd
    except (AttributeError, ValueError, OSError):
        pass
    return None


def _say(line: str) -> None:
    """line on standard error, above the live region where it is drawn."""
    with contextlib.suppress(Exception):
        print(line, file=sys.stderr, flush=True)


def _write(out: IO[str], text: str) -> None:
    out.write(text)
    out.flush()


def _said_again(out: IO[str], text: str) -> None:
    """A question's words written again after a stop; a terminal that has gone is no reason to fail the question."""
    with contextlib.suppress(OSError, ValueError):
        _write(out, text)


async def _answered(context: Context, built: Question, fd: int, out: IO[str]) -> str | Secret:
    """The answer at the terminal, or the default where nothing was answered."""
    if built.hidden:
        typed = await _line(context, fd, out, _prompt(built.text), echo=False)
        return Secret(typed) if typed else built.default
    if not built.choices:
        typed = (await _line(context, fd, out, _prompt(built.text), echo=True)).strip()
        return typed or built.default
    if choose.can_choose(sys.stdin, out):
        return await _menu(context, built, fd, out)
    return await _typed_choice(context, built, fd, out)


def _prompt(text: str) -> str:
    """A question's words as written before its answer: a space after them, where they end in none."""
    shown = visible_text(text)
    return shown if shown[-1:].isspace() else shown + " "


async def _line(context: Context, fd: int, out: IO[str], prompt: str, *, echo: bool) -> str:
    """A line read at the terminal in its cooked mode, with its signal keys, and the echo on or off: the keys typed
    before flushed, the prompt written, then each read the terminal gives until the line ends or the input does. The
    line, without its end; the settings put back as found however it ends, what was typed after the line and not read
    discarded with them, and the question's line ended wherever the person's Enter did not end it on the screen -
    however the read ends, a Ctrl-C or a SIGTERM included - so that what is said next, the view's lines kept while it
    was up among it, begins on a line of its own."""
    import asyncio

    found = termios.tcgetattr(fd)
    mode = list(found)
    # NOFLSH, for a line: a Ctrl-C keeps what the terminal has not read yet - the echo of what was typed, the prompt -
    # where it would throw it away and leave `^C` alone after the question's words; what was typed and not read is
    # discarded by the change back all the same. Never for a hidden answer: its echo is off, so there is nothing typed
    # on the screen to keep, and every signal key (a Ctrl-Z, a Ctrl-\) then throws away what was typed itself.
    on = termios.ICANON | termios.ISIG
    if echo:
        mode[3] = found[3] | on | termios.NOFLSH | termios.ECHO
    else:
        mode[3] = (found[3] | on) & ~(termios.ECHO | termios.ECHONL)
    loop = asyncio.get_running_loop()

    def again() -> None:
        # Once the run goes on after a Ctrl-Z (fg), its mode made again: the question's words written again, below the
        # shell's lines, on the loop.
        loop.call_soon_threadsafe(_said_again, out, prompt)

    # TCSAFLUSH, as getpass sets and puts back: the keys typed before are flushed in the same call that sets the mode,
    # so none typed between a flush and the change is taken; and what is still unread once the line is read - the rest
    # of a paste, the line after a token - is discarded with the change back, never left for the shell after the run.
    # Made at once: a run in the background waits for the terminal there (SIGTTOU), as the question needs it.
    live_region.change(fd, termios.TCSAFLUSH, mode, wait=True, back=again)
    got = b""
    try:
        _write(out, prompt)
        while not got.endswith(b"\n"):
            more = await context.typed(fd)
            if not more:
                break
            got += more
    finally:
        # The question's end with the run's signals held off till it is made (a second Ctrl-C, a SIGTERM, a SIGHUP
        # delivered after): never half made. A hidden answer's Enter, the end of the input, and a read cut short (a
        # Ctrl-C echoed as ^C, a SIGTERM) leave the cursor on the question's line, where the view's kept lines would
        # otherwise be said: the line ended first, in the question's mode, whose NOFLSH (a line's) keeps it at a
        # Ctrl-C. A terminal that has gone fails the write, which is never raised over the question's own end. Then the
        # settings as found (live_region.put_back: only while the run is the terminal's foreground).
        with live_region.uninterrupted():
            try:
                if not echo or not got.endswith(b"\n"):
                    with contextlib.suppress(OSError, ValueError):
                        _write(out, "\n")
            finally:
                live_region.put_back(fd, termios.TCSAFLUSH, found)
    return got.decode(errors="replace").rstrip("\r\n")


async def _menu(context: Context, built: Question, fd: int, out: IO[str]) -> str:
    """A choice picked from tui/choose.py's menu, its keys read on the loop: a read left waiting while no key comes, so
    that none is lost between two looks at the terminal's size, and ended with the menu."""
    import asyncio

    menu = built.menu()
    pending: asyncio.Future[bytes] | None = None

    async def read(seconds: float) -> bytes | None:
        nonlocal pending
        if pending is None:
            pending = asyncio.ensure_future(context.typed(fd))
        done, _ = await asyncio.wait({pending}, timeout=seconds)
        if not done:
            return None
        finished, pending = pending, None
        return finished.result()

    try:
        # The question's words written once the menu's raw mode is set, where a Ctrl-C is a key and throws nothing
        # the terminal has not read away.
        above = visible_text(built.text).rstrip("\n") + "\n"
        value = await choose.asked(menu, read, sys.stdin, out, color=not os.environ.get("NO_COLOR"), above=above)
    except choose.Interrupted:
        # Ctrl-C read as a byte, the terminal already put back: the run's own Ctrl-C, as anywhere else in it.
        os.kill(os.getpid(), signal.SIGINT)
        await context.cancelled.wait()
        raise asyncio.CancelledError("the run was stopped at its question") from None
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.wait({pending})
    answer = value or built.default
    _write(out, choose.collapsed(menu, answer) + "\n")
    return answer


async def _typed_choice(context: Context, built: Question, fd: int, out: IO[str]) -> str:
    """A choice typed where the menu cannot be drawn: the question, its choices numbered, and a number, a value or its
    words read; one that is none of them asked once more, and a second left at the default, said."""
    listed = "\n".join(
        f"  {number}  {visible_text(each.text)}" + (f"  {visible_text(each.note)}" if each.note else "")
        for number, each in enumerate(built.choices, start=1)
    )
    default = built.said(built.default)
    prompt = visible_text(built.text).rstrip("\n") + "\n" + listed + "\n" + TYPED_CHOICE.format(default=default)
    for again in (False, True):
        typed = (await _line(context, fd, out, prompt, echo=True)).strip()
        if not typed:
            return built.default
        picked = built.picked(typed)
        if picked is not None:
            return picked
        if again:
            _write(out, NOT_ONE_EITHER.format(default=default) + "\n")
            return built.default
        choices = ", ".join(visible_text(each.text) for each in built.choices)
        prompt = NOT_ONE.format(choices=choices) + "\n" + TYPED_CHOICE.format(default=default)
    return built.default
