"""A helper verb run on a machine: the helper's script sent on the machine's standard input over the SSH runner, and
what it answered read back bounded and typed (docs/architecture/engine.md §16.10).

`helper(job, target, verb, words, ...)` is the one way the engine sends a program to a machine. It sends guest/
bundle.py's script, always (nothing is installed on a machine), with the verb, its words and what it is asked inside
it, then `stdin`, the verb's own input, after the script's marked end; the command line is the machine's interpreter
given the bootstrap and nothing else, so no word of the verb's, nothing it is asked and nothing of its input is ever on
the argument list of the `ssh` child. It runs under the runner's limits (today's: a line of 16 MiB, 45 s in all, 20 s
to say anything), `limits.seconds` being the call's whole time, a second session's included.

As root (`as_root`, the default), through `sudo` by its path, with its messages in the C locale so that its words are
sudo's own: `sudo -n` first; and only where that is refused for want of a password, and `password()` gives one, the
same input once more with `sudo -S -p ''` and the password's line in front, which `sudo` reads to its newline before
the program runs. One `-S`, never a second try: a wrong password costs sudo's three attempts, the lines after it read as
the next two, and may lock an account where PAM counts them; and where sudo asks nothing no password is sent, since it
would reach the program's standard input. The password is a `Secret`, read from it by the runner alone, in the one
write to the child's standard input. Nothing else secret ever follows the helper on standard input: a secret goes on
the standard input of the one plain command that takes it, so `stdin` is bytes and never a `Secret`.

What comes back is one of three. `Answer`: the verb ran (the bootstrap's first line said so) and ended by itself, with
its exit status, each line it printed after that parsed as JSON - NaN, Infinity and a nesting past `DEPTH_MOST`
refused - and every string in it, keys too, made one plain line (`text.one_line`), and what it said on its standard
error as one plain line. `NotRun`: the verb never started, `sudo` refused it (`sudo` true) or the machine's interpreter
could not be run, with what was said. `NotAnswered`: no answer to take - the machine not reached (ssh's own 255, or
nothing said within its time: `reached` false), the verb out of its time, ended with what the run started for it and
the lines it printed before kept, parsed, where every one parses; more than it may send, or a line that is not JSON,
or no first line of the bootstrap's: refused whole, nothing of it kept.

Standard library only; asyncio is imported inside the coroutine that runs a verb.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
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
# sudo's refusal under -n where it wants a password, a line of its standard error as sudo 1.9 says it (M-2, section 4:
# under a rule that asks one, and where the account has no rule at all, which only the password tells apart).
WANTS_A_PASSWORD = re.compile(r"sudo: a password is required")
# The deepest a line's JSON may nest: a verb's answers are a few levels deep, and the walk that makes each string plain
# stops here, whatever the interpreter's own decoder takes.
DEPTH_MOST = 64


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


class _Garbled(ValueError):
    """A line of an answer that is not one JSON document of the shape a verb sends."""


def _no_number(word: str) -> float:
    raise _Garbled(f"{word} is not a number")


def _finite(word: str) -> float:
    value = float(word)
    return value if math.isfinite(value) else _no_number(word)


def loads(line: str) -> object:
    """One line's JSON, as today's status reads a VM's (status/reach.py's loads, held equal by
    tests/unit/test_aw_guest_remote.py): Python's parser takes NaN and makes 1e999 infinity, neither an answer."""
    return json.loads(line, parse_float=_finite, parse_constant=_no_number)


def plain(value: object, depth: int = 0) -> object:
    """value with every string in it, a mapping's keys too, one plain line (text.one_line, never cut); _Garbled past
    DEPTH_MOST levels."""
    if depth > DEPTH_MOST:
        raise _Garbled(f"it nests deeper than {DEPTH_MOST} levels")
    if isinstance(value, str):
        return text.one_line(value, None)
    if isinstance(value, list):
        return [plain(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {text.one_line(key, None): plain(item, depth + 1) for key, item in value.items()}
    return value


def _parsed(lines: Sequence[str]) -> tuple[object, ...]:
    """Each line parsed and made plain; _Garbled at the first that is not JSON."""
    parsed = []
    for line in lines:
        try:
            parsed.append(plain(loads(line)))
        except RecursionError:
            raise _Garbled(f"it nests deeper than {DEPTH_MOST} levels") from None
        except ValueError as error:
            raise _Garbled(f"it sent a line that is not JSON ({error})") from None
    return tuple(parsed)


def _kept(lines: Sequence[str]) -> tuple[object, ...]:
    """What a verb out of its time printed after the bootstrap's line, parsed: () where it never started or a line does
    not parse."""
    if not lines or lines[0] != bundle.STARTED:
        return ()
    try:
        return _parsed(lines[1:])
    except _Garbled:
        return ()


def command(python: str, become_exe: str | None, password: bool = False) -> list[str]:
    """The command line that runs the helper's script on a machine: python given the bootstrap, as root through
    become_exe in the C locale where it is given - -n, or with password -S -p '' - and nothing else."""
    run = bundle.command(python)
    if become_exe is None:
        return run
    sudo = [become_exe, "-S", "-p", "", "-H"] if password else [become_exe, "-n", "-H"]
    return [ENV, LOCALE, *sudo, *run]


def _wants_a_password(result: runner.Result) -> bool:
    """Whether sudo refused under -n for want of a password: nothing started, and sudo said so in its own words."""
    return (
        isinstance(result, runner.Done)
        and not result.lines
        and any(WANTS_A_PASSWORD.fullmatch(line.strip()) for line in result.said.splitlines())
    )


def _outcome(result: runner.Result, limits: runner.Limits, sudo: bool) -> Outcome:
    """What a run of the script came back as."""
    said = text.one_line(result.said, None)
    if isinstance(result, runner.Unreachable):
        return NotAnswered(said or "it was not reached (exit 255)", reached=False)
    if isinstance(result, runner.Silent):
        return NotAnswered(f"it took the connection and said nothing for {limits.silent:g} s: timed out", reached=False)
    if isinstance(result, runner.Cut):
        return NotAnswered(result.why, reached=True)
    if isinstance(result, runner.TimedOut):
        return NotAnswered(f"it took longer than {limits.seconds:g} s", reached=True, lines=_kept(result.lines))
    assert isinstance(result, runner.Done)
    if not result.lines:
        return NotRun(said or f"exit {result.status}", sudo=sudo)
    if result.lines[0] != bundle.STARTED:
        return NotAnswered("it did not answer as the helper does: its first line is not the bootstrap's", reached=True)
    try:
        return Answer(result.status, _parsed(result.lines[1:]), said)
    except _Garbled as garbled:
        return NotAnswered(str(garbled), reached=True)


async def helper(
    job: Job,
    target: runner.Target,
    verb: str,
    words: Sequence[str] = (),
    *,
    asked: object = None,
    stdin: bytes = b"",
    as_root: bool = True,
    password: Callable[[], Secret | None] | None = None,
    limits: runner.Limits = runner.LIMITS,
    env: Mapping[str, str],
    python: str = bundle.PYTHON,
    become_exe: str = SUDO,
    started: Callable[[Process], None] | None = None,
) -> Outcome:
    """verb run with its words on target by the helper's script, a child of job's run under limits, as root unless
    as_root is false: asked, what the verb is given ({} unless given), inside the script; stdin, the verb's own input,
    after it. password is called once at most, and only where sudo -n refused for want of a password; started(process)
    for each ssh started. Refused (runner's) where a value is not one the call takes, before anything starts."""
    import asyncio

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
    try:
        script = bundle.script([verb, *words], {} if asked is None else asked).encode("utf-8")
    except ValueError as error:
        raise runner.Refused(str(error)) from None
    become = become_exe if as_root else None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limits.seconds
    first = await runner.run(job, target, command(python, become), [script, stdin], limits, env, started)
    outcome = _outcome(first, limits, as_root)
    if not (as_root and password is not None and _wants_a_password(first)):
        return outcome
    secret = password()
    if secret is None:
        return outcome
    if not isinstance(secret, Secret):
        raise runner.Refused("a sudo password is a Secret")
    left = deadline - loop.time()
    if left <= 0:
        return NotAnswered(f"it took longer than {limits.seconds:g} s", reached=True)
    given = dataclasses.replace(limits, seconds=left)
    second = await runner.run(
        job, target, command(python, become, password=True), [secret, "\n", script, stdin], given, env, started
    )
    return _outcome(second, limits, True)
