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
program runs, and only then the same input; where it has not come `REFUSAL_GRACE` before the call's end, standard input
is closed, so that sudo says its own refusal in time. So a wrong password costs one of sudo's attempts, never three,
and no line of the script is ever read by sudo as a password; and where it was sent and sudo did not run the program
with it, however the session ended, the `Password` is marked refused and sent to no machine again in the run, which
says so once, in that call's outcome: one sent on every read could lock an account where PAM counts the failures. The
run's first send settles it: calls side by side wait for it, and send it only once sudo took it. Where sudo asks
nothing, no password is sent, since it would reach the program's standard input. The password is a `Secret`, read
from it by the runner alone, in the one write of its line to the child's standard input, and here once, at the run's
first ask, to make the forms a machine may say it back in (`_forms`: as sent; as the runner reads it, plain and
stripped; one plain line; JSON's escapes; a C string's and a shell's backslashes), each put as its mark
(`secret.struck_all`) wherever a machine says it back: in what it said on its standard error, and an end of that which
begins a form where the runner may have cut it there; in every line before it is parsed, in every string after, and in
why it was cut. Not struck: a number a machine spells another way, a password split across lines or written in
another escape. Nothing else secret ever follows the helper on standard input: a secret goes on the standard input of
the one plain command that takes it, so `stdin` is bytes and never a `Secret`.

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
from claude_vm.secret import Secret, struck_all, struck_end
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
# How long before the call's end the second session's standard input is closed where the bootstrap's line has not
# come: sudo, waiting on a second prompt after a wrong password, reads its end and says its own refusal within the
# call's time, so the password is marked refused whatever the time left.
REFUSAL_GRACE = 2.0
# Where what a machine said on its standard error comes within this many bytes of its bound (Limits.said), the runner
# may have cut it partway through the password: an end of it that begins one of the password's forms is struck too.
# What the runner keeps of a cut is the bound's bytes, less the control characters and the spaces at the ends it drops.
CUT_NEAR = 1024
# The characters bash's printf %q puts a backslash before in printable text (its sh_backslash_quote's table), beside
# a # first and a ~ first or after a : or =; and the two a C string's escape does.
SHELL_QUOTED = frozenset(" \t\n!\"$&'()*,;<>?[\\]^`{|}")
C_QUOTED = frozenset('\\"')


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
    of one, and never again, what it gave kept, with the forms a machine may say it back in; once sudo did not run the
    program with it (refused), it is sent to no machine again. The run's first send settles it: calls side by side wait
    for it, then send only where sudo took it (taken), never where it refused it."""

    def __init__(self, source: Callable[[], Secret | None]) -> None:
        if not callable(source):
            raise runner.Refused("a sudo password is made of a function that gives it, a Secret, or None")
        self._source = source
        self._asked = False
        self._secret: Secret | None = None
        self._forms: tuple[Secret, ...] = ()
        self._refused = False
        self._taken = False
        # The first send's, while it is under way: an asyncio future, done once it is settled.
        self._settling = None

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
            self._forms = () if given is None else _forms(given)
        return None if self._refused else self._secret

    def forms(self) -> tuple[Secret, ...]:
        """The password's forms where its source was asked, refused or not, to strike from what a machine says; () where
        it was not, or gave none. It never asks the source."""
        return self._forms

    async def _turn(self, until: float) -> bool | None:
        """Whether this call sends the password first, the run's first send (True: it settles it), or sends it as sudo
        took it already (False); None where sudo refused it meanwhile. A call while the first is under way waits for it;
        TimeoutError where time.monotonic() passes until first."""
        import asyncio

        while not self._refused:
            if self._taken:
                return False
            if self._settling is None:
                self._settling = asyncio.get_running_loop().create_future()
                return True
            await asyncio.wait_for(asyncio.shield(self._settling), until - time.monotonic())
        return None

    def _settled(self, taken: bool | None) -> None:
        """The first send's end: taken where sudo ran the program with it, refused where taken is False, and where it
        is None (nothing told, or the call ended first) neither, so that a call waiting sends first in its place."""
        if taken:
            self._taken = True
        elif taken is not None:
            self._refused = True
        if self._settling is not None:
            if not self._settling.done():
                self._settling.set_result(None)
            self._settling = None


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


def _backslashed(value: str, quoted: frozenset[str], shell: bool = False) -> str:
    """value with a backslash before each character of quoted, and as a shell's printf %q, before a # or a ~ first and
    a ~ after a : or =."""
    out = []
    for at, character in enumerate(value):
        first = at == 0 or value[at - 1] in ":="
        if character in quoted or (shell and ((at == 0 and character == "#") or (first and character == "~"))):
            out.append("\\")
        out.append(character)
    return "".join(out)


def _forms(secret: Secret) -> tuple[Secret, ...]:
    """The forms a machine may say secret back in, each a Secret: as sent; as the runner reads a line (text.plain_text)
    and its standard error (stripped too), and one plain line (text.one_line); JSON's escape, in ASCII and not; and
    backslashed, as a C string and a shell's printf %q write printable text. Made once, at the run's first ask: the one
    place this module reads a secret's value (tests/unit/test_aw_guards.py's REVEALS), each form wrapped at once."""
    value = secret.reveal()
    written = {
        value,
        json.dumps(value)[1:-1],
        json.dumps(value, ensure_ascii=False)[1:-1],
        _backslashed(value, C_QUOTED),
        _backslashed(value, SHELL_QUOTED, shell=True),
    }
    read: set[str] = set()
    for each in written:
        made_plain = text.plain_text(each)
        read |= {each, each.strip(), made_plain, made_plain.strip(), text.one_line(each, None)}
    return tuple(Secret(form) for form in sorted(read) if form)


def _one_line(value: str, forms: tuple[Secret, ...] = ()) -> str:
    """value one plain line (text.one_line, never cut), each of forms put as its mark before it is made plain and
    after, which may join what a machine sent apart."""
    if not forms:
        return text.one_line(value, None)
    return struck_all(text.one_line(struck_all(value, forms), None), forms)


def plain(value: object, depth: int = 0, forms: tuple[Secret, ...] = ()) -> object:
    """value with every string in it, a mapping's keys too, one plain line (text.one_line, never cut), each of forms
    put as its mark in each; _Garbled past DEPTH_MOST levels."""
    if depth > DEPTH_MOST:
        raise _Garbled(f"it nests deeper than {DEPTH_MOST} levels")
    if isinstance(value, str):
        return _one_line(value, forms)
    if isinstance(value, list):
        return [plain(item, depth + 1, forms) for item in value]
    if isinstance(value, dict):
        return {_one_line(key, forms): plain(item, depth + 1, forms) for key, item in value.items()}
    return value


def _parsed(lines: Sequence[str], until: float, forms: tuple[Secret, ...] = ()) -> tuple[object, ...]:
    """Each line parsed and made plain, each of forms struck from each string; _Garbled at the first that is not JSON,
    _Late where time.monotonic() passes until before a line."""
    parsed = []
    for line in lines:
        if time.monotonic() > until:
            raise _Late
        try:
            parsed.append(plain(loads(line), 0, forms))
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


def _kept(lines: Sequence[str], forms: tuple[Secret, ...]) -> tuple[object, ...]:
    """What a verb out of its time printed after the bootstrap's line, parsed within KEPT_SECONDS: () where it never
    started, a line does not parse, or the time is over first."""
    after = _after_started(lines)
    if after is None:
        return ()
    try:
        return _parsed(after, time.monotonic() + KEPT_SECONDS, forms)
    except (_Garbled, _Late):
        return ()


def _struck(result: runner.Result, forms: tuple[Secret, ...], bound: int) -> runner.Result:
    """result with each of forms put as its mark wherever the machine said it back: in what it said, and where that
    comes within CUT_NEAR bytes of bound, Limits.said, an end of it that begins a form; in every line before it is
    parsed; and in why it was cut. result itself where no password is held."""
    if not forms:
        return result
    said = struck_all(result.said, forms)
    if len(result.said.encode("utf-8")) >= bound - CUT_NEAR:
        said = struck_end(said, forms)
    if isinstance(result, runner.Silent):
        return runner.Silent(said)
    lines = tuple(struck_all(line, forms) for line in result.lines)
    if isinstance(result, runner.Cut):
        return runner.Cut(struck_all(result.why, forms), lines, said)
    if isinstance(result, runner.TimedOut):
        return runner.TimedOut(lines, said)
    if isinstance(result, runner.Unreachable):
        return runner.Unreachable(said, lines)
    assert isinstance(result, runner.Done)
    return runner.Done(result.status, lines, said)


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
    result: runner.Result, limits: runner.Limits, sudo: bool, until: float, forms: tuple[Secret, ...] = ()
) -> Outcome:
    """What a run of the script came back as, its answer parsed until time.monotonic() passes until, forms, the
    password's where one is held, struck from every word of it."""
    result = _struck(result, forms, limits.said)
    said = _one_line(result.said, forms)
    if isinstance(result, runner.Unreachable):
        return NotAnswered(said or "it was not reached (exit 255)", reached=False)
    if isinstance(result, runner.Silent):
        return NotAnswered(f"it took the connection and said nothing for {limits.silent:g} s: timed out", reached=False)
    if isinstance(result, runner.Cut):
        return NotAnswered(_one_line(result.why, forms), reached=True)
    if isinstance(result, runner.TimedOut):
        return NotAnswered(f"it took longer than {limits.seconds:g} s", reached=True, lines=_kept(result.lines, forms))
    assert isinstance(result, runner.Done)
    after = _after_started(result.lines)
    if after is None:
        why = f"it did not answer as the helper does: no line of the bootstrap's (exit {result.status})"
        return NotRun(said or why, sudo=sudo)
    try:
        return Answer(result.status, _parsed(after, until, forms), said)
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
    verb's input, only once the bootstrap's line says sudo ran the program with it, within the time to say anything and
    REFUSAL_GRACE before until; standard input closed, and its end read, all by until. password is marked taken once
    that line comes, and refused where it was sent and that line never came, however the session ended: its outcome
    then says so, once."""
    left = until - time.monotonic()
    wait = left - REFUSAL_GRACE if limits.silent is None else min(limits.silent, left - REFUSAL_GRACE)
    held = await runner.hold(job, target, words, env, started, left, dataclasses.replace(limits, seconds=left))
    given = let_in = False
    try:
        with contextlib.suppress(runner.Ended):
            await held.send(secret)
            given = True
            let_in = await _let_in(held, wait)
            if let_in:
                # sudo took it: calls side by side send it now, without waiting for this one's end.
                password._settled(True)
                await held.write(sent)
        await held.close()
        result = await held.result()
    finally:
        await held.end()
    outcome = _outcome(result, limits, True, until, password.forms())
    if not given:
        return outcome
    if not (let_in or _after_started(result.lines) is not None):
        password._settled(False)
        if isinstance(outcome, NotRun):
            return NotRun(f"{outcome.said} - {NOT_TAKEN}" if outcome.said else NOT_TAKEN, sudo=True)
        assert isinstance(outcome, NotAnswered)
        return NotAnswered(f"{outcome.why} - {NOT_TAKEN}", outcome.reached, outcome.lines)
    password._settled(True)
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
    outcome = _outcome(first, limits, as_root, until, () if password is None else password.forms())
    if not (as_root and password is not None and _wants_a_password(first)):
        return outcome
    if password.secret() is None:
        return outcome
    late = NotAnswered(f"it took longer than {limits.seconds:g} s", reached=True)
    try:
        first_send = await password._turn(until)
    except TimeoutError:
        return late
    try:
        # Refused by a call side by side while this one waited; and none sent with too little time for sudo to say.
        secret = password.secret()
        if first_send is None or secret is None:
            return outcome
        if until - time.monotonic() <= REFUSAL_GRACE:
            return late
        second = command(python, become, password=True)
        return await _with_password(job, target, second, script + stdin, password, secret, limits, until, env, started)
    finally:
        if first_send:
            password._settled(None)
