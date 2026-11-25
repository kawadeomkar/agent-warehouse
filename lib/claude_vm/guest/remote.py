"""A helper verb run on a machine: the helper's script sent on the machine's standard input over the SSH runner, and
what it answered read back bounded and typed (docs/architecture/engine.md §16.10).

`helper(job, target, verb, words, ...)` is the one way the engine sends a program to a machine. It sends guest/
bundle.py's script, always (nothing is installed on a machine), with the verb, its words and what it is asked inside
it, then `stdin`, the verb's own input, after the script's marked end; the command line is the machine's interpreter
given the bootstrap and nothing else, so no word of the verb's, nothing it is asked and nothing of its input is ever on
the argument list of the `ssh` child. It runs under the runner's limits (today's: a line of 16 MiB, 45 s in all, 20 s
to say anything), `limits.seconds` being the call's whole time, a second session's and the parse of the answer
included, and `LINES` lines at most where the limits set no bound on them.

As root (`as_root`, the default), through `sudo` by its path, with its messages in the C locale so that its words are
sudo's own: `sudo -n` first; and only where that is refused for want of a password, in sudo's words or sudo-rs's, and
the run's `Password` gives one, a second session with `sudo -S -p ''`, held (the runner's `hold`): the password's line
alone, which `sudo` reads to its newline before the program runs, then nothing more until the bootstrap's line says the
program runs, and only then the same input. So a wrong password costs one of sudo's attempts, never three, and no line
of the script is ever read by sudo as a password; and where sudo does not run the program with it, the `Password` is
marked refused and sent to no machine again in the run, which says so once, in that call's `NotRun`: one sent on
every read could lock an account where PAM counts the failures. Calls already under way may each have sent it once
before. Where sudo asks nothing, no password is sent, since it would reach the program's standard input. The password
is a `Secret`, read from it by the runner alone, in the one write of its line to the child's standard input, and put as
its mark (`Secret.struck`) wherever a machine says it back: in what it said on its standard error, in every line before
it is parsed, in every string after, and in why it was cut. Nothing else secret ever follows the helper on standard
input: a secret goes on the standard input of the one plain command that takes it, so `stdin` is bytes and never a
`Secret`.

What comes back is one of three. `Answer`: the verb ran (the bootstrap's line said so: what comes before it, a shell's
words at login, is dropped, as the bootstrap drops what comes before its mark) and ended by itself, with its exit
status, each line it printed after that parsed as JSON - NaN, Infinity and a nesting past `DEPTH_MOST` refused - and
every string in it, keys too, made one plain line (`text.one_line`), and what it said on its standard error as one
plain line. `NotRun`: the verb never started, no line of the bootstrap's: `sudo` refused it (`sudo` true) or the
machine's interpreter could not be run, with what was said. `NotAnswered`: no answer to take - the machine not reached
(ssh's own 255, or nothing said within its time: `reached` false), the verb out of its time (its answer's parse too),
ended with what the run started for it and the lines it printed before kept, parsed, where every one parses within
`KEPT_SECONDS`; more than it may send, in bytes or in lines, or a line that is not JSON: refused whole, nothing of it
kept.

Standard library only; asyncio is imported inside the coroutine that runs a verb.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from claude_vm import text
from claude_vm.guest import bundle
from claude_vm.secret import Secret
from claude_vm.ssh import runner

if TYPE_CHECKING:
    from asyncio.subprocess import Process

    from claude_vm.runs.job import Job

# The machine's sudo and env, by their paths: a sudo on the account's PATH (~/.local/bin is first there) would be handed
# the password and run as root whatever it is.
SUDO = "/usr/bin/sudo"
ENV = "/usr/bin/env"
# sudo's messages in the C locale: its refusal is read by its words, which a translation would change.
LOCALE = "LC_ALL=C"
# sudo's refusal under -n where it wants a password, a line of its standard error as each sudo a machine may run says
# it: sudo 1.9's (M-2, section 4: under a rule that asks one, and where the account has no rule at all, which only the
# password tells apart), and sudo-rs's, Ubuntu's sudo from 25.10, by either of the names it says itself by.
WANTS_A_PASSWORD = re.compile(r"(?:sudo|sudo-rs): (?:a password is required|interactive authentication is required)")
# The deepest a line's JSON may nest: a verb's answers are a few levels deep, and the walk that makes each string plain
# stops here, whatever the interpreter's own decoder takes.
DEPTH_MOST = 64
# The most lines an answer may hold where the caller's limits set no bound (Limits.lines): a verb sends a few, the probe
# three. Past it the answer is refused whole, and its parse, bounded so in lines as in bytes, ends within the call's
# time to within one line's.
LINES = 4096
# How long what a verb out of its time printed is parsed for, once its time is over: past it, nothing of it is kept.
KEPT_SECONDS = 1.0
# What the run says once, in the call where sudo did not run the program with the password.
NOT_TAKEN = "sudo did not run the program with the password, which is sent to no machine again in this run"


@dataclasses.dataclass(frozen=True)
class Answer:
    """The verb ran and ended by itself: its exit status, each line it printed after the bootstrap's, parsed, every
    string one plain line, and what it said on its standard error, one plain line."""

    status: int
    lines: tuple[object, ...]
    said: str


@dataclasses.dataclass(frozen=True)
class NotRun:
    """The verb never started: sudo refused it (sudo true: under -n, or with the one password sent), or the machine's
    interpreter could not be run. What was said, one plain line."""

    said: str
    sudo: bool


@dataclasses.dataclass(frozen=True)
class NotAnswered:
    """No answer to take: why, in a sentence; reached false where ssh could not reach the machine or it said nothing in
    its time. lines: what a verb out of its time printed before, parsed, where every line parses; () for every other."""

    why: str
    reached: bool
    lines: tuple[object, ...] = ()


Outcome = Answer | NotRun | NotAnswered


class Password:
    """The VM user's sudo password for one run: made once by the run and handed to each helper call of it. source, a
    function that gives a Secret or None where none is held, is called at the first call where sudo -n refused for want
    of one, and never again, what it gave kept; once sudo did not run the program with it (refused), it is sent to no
    machine again."""

    def __init__(self, source: Callable[[], Secret | None]) -> None:
        if not callable(source):
            raise runner.Refused("a sudo password is made of a function that gives it, a Secret, or None")
        self._source = source
        self._asked = False
        self._secret: Secret | None = None
        self._refused = False

    @property
    def refused(self) -> bool:
        """Whether sudo did not run the program with it, so that it is sent no more in the run."""
        return self._refused

    def secret(self) -> Secret | None:
        """The password to send: its source's, which is asked the first time alone; None where it holds none, or once
        sudo did not run the program with it. Refused where the source gives anything but a Secret or None."""
        if not self._asked:
            self._asked = True
            given = self._source()
            if given is not None and not isinstance(given, Secret):
                raise runner.Refused("a sudo password is a Secret")
            self._secret = given
        return None if self._refused else self._secret

    def held(self) -> Secret | None:
        """The password where its source was asked, refused or not, to strike from what a machine says; it never asks
        the source."""
        return self._secret

    def _refuse(self) -> None:
        self._refused = True


class _Garbled(ValueError):
    """A line of an answer that is not one JSON document of the shape a verb sends."""


class _Late(Exception):
    """The parse of an answer ran past the call's time."""


def _no_number(word: str) -> float:
    raise _Garbled(f"{word} is not a number")


def _finite(word: str) -> float:
    value = float(word)
    return value if math.isfinite(value) else _no_number(word)


def loads(line: str) -> object:
    """One line's JSON, as today's status reads a VM's (status/reach.py's loads, held equal by
    tests/unit/test_aw_guest_remote.py): Python's parser takes NaN and makes 1e999 infinity, neither an answer."""
    return json.loads(line, parse_float=_finite, parse_constant=_no_number)


def _one_line(value: str, secret: Secret | None = None) -> str:
    """value one plain line (text.one_line, never cut), secret put as its mark before it is made plain and after, which
    may join what a machine sent apart."""
    if secret is None:
        return text.one_line(value, None)
    return secret.struck(text.one_line(secret.struck(value), None))


def plain(value: object, depth: int = 0, secret: Secret | None = None) -> object:
    """value with every string in it, a mapping's keys too, one plain line (text.one_line, never cut), secret put as its
    mark in each; _Garbled past DEPTH_MOST levels."""
    if depth > DEPTH_MOST:
        raise _Garbled(f"it nests deeper than {DEPTH_MOST} levels")
    if isinstance(value, str):
        return _one_line(value, secret)
    if isinstance(value, list):
        return [plain(item, depth + 1, secret) for item in value]
    if isinstance(value, dict):
        return {_one_line(key, secret): plain(item, depth + 1, secret) for key, item in value.items()}
    return value


def _parsed(lines: Sequence[str], until: float, secret: Secret | None = None) -> tuple[object, ...]:
    """Each line parsed and made plain, secret struck from each string; _Garbled at the first that is not JSON, _Late
    where time.monotonic() passes until before a line."""
    parsed = []
    for line in lines:
        if time.monotonic() > until:
            raise _Late
        try:
            parsed.append(plain(loads(line), 0, secret))
        except RecursionError:
            raise _Garbled(f"it nests deeper than {DEPTH_MOST} levels") from None
        except ValueError as error:
            raise _Garbled(f"it sent a line that is not JSON ({error})") from None
    return tuple(parsed)


def _started(line: str) -> bool:
    """Whether line is the bootstrap's, the whole of it."""
    return line == bundle.STARTED


def _after_started(lines: Sequence[str]) -> Sequence[str] | None:
    """lines after the first that is the bootstrap's, what came before it dropped; None where none is."""
    for at, line in enumerate(lines):
        if _started(line):
            return lines[at + 1 :]
    return None


def _kept(lines: Sequence[str], secret: Secret | None) -> tuple[object, ...]:
    """What a verb out of its time printed after the bootstrap's line, parsed within KEPT_SECONDS: () where it never
    started, a line does not parse, or the time is over first."""
    after = _after_started(lines)
    if after is None:
        return ()
    try:
        return _parsed(after, time.monotonic() + KEPT_SECONDS, secret)
    except (_Garbled, _Late):
        return ()


def _struck(result: runner.Result, secret: Secret | None) -> runner.Result:
    """result with secret put as its mark wherever the machine said it back: in what it said, in every line before it
    is parsed, and in why it was cut; result itself where no password is held."""
    if secret is None:
        return result
    struck: dict[str, object] = {"said": secret.struck(result.said)}
    if not isinstance(result, runner.Silent):
        struck["lines"] = tuple(secret.struck(line) for line in result.lines)
    if isinstance(result, runner.Cut):
        struck["why"] = secret.struck(result.why)
    return dataclasses.replace(result, **struck)


def command(python: str, become_exe: str | None, password: bool = False) -> list[str]:
    """The command line that runs the helper's script on a machine: python given the bootstrap, as root through
    become_exe in the C locale where it is given - -n, or with password -S -p '' - and nothing else."""
    run = bundle.command(python)
    if become_exe is None:
        return run
    sudo = [become_exe, "-S", "-p", "", "-H"] if password else [become_exe, "-n", "-H"]
    return [ENV, LOCALE, *sudo, *run]


def _wants_a_password(result: runner.Result) -> bool:
    """Whether sudo refused under -n for want of a password: no line of the bootstrap's, and sudo said so in its own
    words."""
    return (
        isinstance(result, runner.Done)
        and _after_started(result.lines) is None
        and any(WANTS_A_PASSWORD.fullmatch(line.strip()) for line in result.said.splitlines())
    )


def _outcome(
    result: runner.Result, limits: runner.Limits, sudo: bool, until: float, secret: Secret | None = None
) -> Outcome:
    """What a run of the script came back as, its answer parsed until time.monotonic() passes until, secret, the
    password where one is held, struck from every word of it."""
    result = _struck(result, secret)
    said = _one_line(result.said, secret)
    if isinstance(result, runner.Unreachable):
        return NotAnswered(said or "it was not reached (exit 255)", reached=False)
    if isinstance(result, runner.Silent):
        return NotAnswered(f"it took the connection and said nothing for {limits.silent:g} s: timed out", reached=False)
    if isinstance(result, runner.Cut):
        return NotAnswered(_one_line(result.why, secret), reached=True)
    if isinstance(result, runner.TimedOut):
        return NotAnswered(f"it took longer than {limits.seconds:g} s", reached=True, lines=_kept(result.lines, secret))
    assert isinstance(result, runner.Done)
    after = _after_started(result.lines)
    if after is None:
        why = f"it did not answer as the helper does: no line of the bootstrap's (exit {result.status})"
        return NotRun(said or why, sudo=sudo)
    try:
        return Answer(result.status, _parsed(after, until, secret), said)
    except _Garbled as garbled:
        return NotAnswered(str(garbled), reached=True)
    except _Late:
        return NotAnswered(f"it took longer than {limits.seconds:g} s", reached=True)


async def _let_in(held: runner.Held, wait: float) -> bool:
    """Whether the bootstrap's line came within wait seconds of the password's: sudo ran the program with it. Each line
    before it is a shell's at login, read and dropped."""
    import asyncio

    lines = held.lines()
    until = time.monotonic() + wait
    try:
        while (left := until - time.monotonic()) > 0:
            if _started(await asyncio.wait_for(anext(lines), left)):
                return True
    except (TimeoutError, StopAsyncIteration):
        pass
    finally:
        await lines.aclose()
    return False


async def _with_password(
    job: Job,
    target: runner.Target,
    words: list[str],
    sent: bytes,
    password: Password,
    secret: Secret,
    limits: runner.Limits,
    until: float,
    env: Mapping[str, str],
    started: Callable[[Process], None] | None,
) -> Outcome:
    """The second session, held: words, sudo -S's, started; the password's line alone, then sent, the script and the
    verb's input, only once the bootstrap's line says sudo ran the program with it, within the time to say anything;
    standard input closed, and its end read, all by until. password is marked refused where no line of the bootstrap's
    came at all."""
    left = until - time.monotonic()
    wait = left if limits.silent is None else min(limits.silent, left)
    held = await runner.hold(job, target, words, env, started, left, dataclasses.replace(limits, seconds=left))
    let_in = False
    try:
        with contextlib.suppress(runner.Ended):
            await held.send(secret)
            let_in = await _let_in(held, wait)
            if let_in:
                await held.write(sent)
        await held.close()
        result = await held.result()
    finally:
        await held.end()
    outcome = _outcome(result, limits, True, until, secret)
    if isinstance(outcome, NotRun):
        password._refuse()
        return NotRun(f"{outcome.said} - {NOT_TAKEN}" if outcome.said else NOT_TAKEN, sudo=True)
    if not let_in and isinstance(result, runner.Done):
        return NotAnswered(
            f"sudo took longer than {wait:g} s to run the program with the password: nothing was sent to it",
            reached=True,
        )
    return outcome


async def helper(
    job: Job,
    target: runner.Target,
    verb: str,
    words: Sequence[str] = (),
    *,
    asked: object = None,
    stdin: bytes = b"",
    as_root: bool = True,
    password: Password | None = None,
    limits: runner.Limits = runner.LIMITS,
    env: Mapping[str, str],
    python: str = bundle.PYTHON,
    become_exe: str = SUDO,
    started: Callable[[Process], None] | None = None,
) -> Outcome:
    """verb run with its words on target by the helper's script, a child of job's run under limits, as root unless
    as_root is false: asked, what the verb is given ({} unless given), inside the script; stdin, the verb's own input,
    after it. password, the run's, is asked for its secret only where sudo -n refused for want of one, and sends it once
    at most; started(process) for each ssh started. Refused (runner's) where a value is not one the call takes, before
    anything starts."""
    if isinstance(stdin, Secret) or not isinstance(stdin, bytes):
        raise runner.Refused(
            "a verb's standard input is bytes, never a Secret: a secret goes on the standard input of the one plain"
            " command that takes it"
        )
    if not isinstance(verb, str) or isinstance(words, (str, bytes)) or not isinstance(words, Sequence):
        raise runner.Refused("a helper verb is a word, and its words a list of them")
    if not isinstance(as_root, bool):
        raise runner.Refused("whether a verb runs as root is True or False")
    if not isinstance(limits, runner.Limits):
        raise runner.Refused("limits is a Limits")
    if password is not None and not isinstance(password, Password):
        raise runner.Refused("a sudo password is the run's Password (remote.Password), made once for the run")
    if limits.lines is None:
        limits = dataclasses.replace(limits, lines=LINES)
    try:
        script = bundle.script([verb, *words], {} if asked is None else asked).encode("utf-8")
    except ValueError as error:
        raise runner.Refused(str(error)) from None
    become = become_exe if as_root else None
    until = time.monotonic() + limits.seconds
    first = await runner.run(job, target, command(python, become), [script, stdin], limits, env, started)
    outcome = _outcome(first, limits, as_root, until, None if password is None else password.held())
    if not (as_root and password is not None and _wants_a_password(first)):
        return outcome
    secret = password.secret()
    if secret is None:
        return outcome
    if until - time.monotonic() <= 0:
        return NotAnswered(f"it took longer than {limits.seconds:g} s", reached=True)
    second = command(python, become, password=True)
    return await _with_password(job, target, second, script + stdin, password, secret, limits, until, env, started)
