"""Layer 1's events, read: each line the product's stdout callback writes on the events pipe made one `step` event of
the machine, handed to the run's own `emit` in the run's process, with every secret the run opened struck from it
(docs/architecture/engine.md §16.10).

The callback (guest/ansible/callback_plugins/aw_events.py) writes one JSON event a line; `Heard.read(stream)` reads them
to the pipe's end and hands each, as it comes, to `job.emit("step", machine, text, ...)` (runs/job.py): the job's text
log, the live view and every other listener the run registered hear it there, and no event is written to a file of its
own. machine is the machine's identity (`agent-warehouse-<its SMBIOS UUID>`), the one key of a machine in every event of
the run, as `job.json`'s `machines` and a machine's `end` (its `machine_outcomes`) have it; the name a person chose
rides beside it in every step, as `name`, for what is drawn. An event's data is what the callback wrote of a fixed set
of fields alone, each held to its type: `status` (the callback's kind: `play`, `task`, `ok`, `changed`, `failed`,
`skipped`, `unreachable`, `retry`, `diff`, `recap`, `fault`, or `unreadable` for a line that is none), `part` (the role
the task is in, which is its tag), `task`, and where it has one, the item, the message, the output, the return code, the
exception, the warnings, the diff, the attempts, and whether Ansible censored the result or the failure was ignored. Its
text is the task's name, the play's, or a sentence of the recap's counts or of what could not be read.

Every string the child wrote is made plain in three steps: every secret of the redaction list (`redacted`, the Secrets
the run opened, which the caller hands over) struck by its exact text where the child wrote it, the longest first
(secret.struck_all), then `text.plain_text`, then struck again, so that a secret an escape sequence split in two is
struck once it is whole; a code point no UTF-8 holds is replaced. A message, an item or an output the callback wrote as
a structure (a mapping or a list: a `debug` of one, a loop over them) stays one until each of its strings, a mapping's
keys among them, is so made, and only then is made one text, JSON's form of it, struck once more, since escaping a
quote or a backslash can make a secret's text: never escaped before the strike, which by exact text would not meet a
secret holding a quote, a backslash, a line break or a character outside ASCII. Then a string is cut to its field's
bound, its start for a message, its end for an output: cut after it is struck, so that no part of a secret is left of a
cut. The child's own bound is a string's end from a line break, never a part of a line, so that a secret on one line
reaches the strike whole. The strike meets a secret in the forms Ansible itself quotes one in too (secret.struck_all):
JSON's escape of it, as a mapping's diff is written, and `repr`'s, as an exception's text quotes a value or a key
that is a tuple is made text. What it does not promise: a secret in base64 or a URL's escapes, one split across two
strings, or one split by a character `plain_text` keeps (a zero-width space).

`checked` is what F21's check (layer1.check, a run in check mode with its diff) makes of the tasks heard, one `Checked`
a task in the order they started: `would change` where a result said changed, `would not change` where every result
said ok or skipped, and `not evaluated` for a `command`, `shell` or `raw` task that check mode skipped, as check mode
reports it, and for a task that failed, was unreachable or had no result; with the diffs heard of it, each struck as
every event's.

A line is read up to LINE_MOST bytes: a longer one is left out whole, said as an event that could not be read, and so is
a line that is no event (a write a signal cut short leaves one). The pipe's end, with or without a line the child did
not finish, ends the read. `counts` gives what a caller needs of the run: the failed, the unreachable and the changed,
from Ansible's own recap where the child wrote one, else counted from the results heard, an ignored failure and an item
apart (`recap` says which).

Standard library only; asyncio is imported inside `read`.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from claude_vm import checks
from claude_vm.runs import job as jobs
from claude_vm.secret import Secret, struck_all
from claude_vm.text import plain_text

if TYPE_CHECKING:
    from asyncio import StreamReader

# The kinds of event the callback writes, and the status each is said with: the recap as `recap`.
KINDS = ("play", "task", "ok", "changed", "failed", "skipped", "unreachable", "retry", "diff", "stats", "fault")
STATUS = {kind: "recap" if kind == "stats" else kind for kind in KINDS}
# What a line that is no event is said with.
UNREADABLE = "unreadable"
# The longest line read: a few strings at the callback's bound fit, and a longer line is left out whole.
LINE_MOST = 4 * 1024 * 1024
# Each field's bound, once struck and plain: a name, a message and an exception from their start, an output and a diff
# from their end.
NAME_MOST = 512
MESSAGE_MOST = 8 * 1024
OUTPUT_MOST = 32 * 1024
DIFF_MOST = 64 * 1024
# The most warnings an event keeps.
WARNINGS_MOST = 20
# How deep a structure the callback wrote is made text: below it, what is left is said as ELIDED.
DEPTH_MOST = 32
ELIDED = "…"
# The recap's counts, as Ansible's stats name them, in the order a sentence says them.
RECAP = ("ok", "changed", "unreachable", "failures", "skipped", "rescued", "ignored")
# The sentences of what could not be read, or written.
NOT_READ = "an event of the play could not be read, and is left out"
TOO_LONG = "an event of the play was longer than {most} bytes, and is left out"
NOT_WRITTEN = "an event of the play could not be written by its callback ({error} in {hook})"
# The strings each kind of result may carry: field, bound, whether its end is kept, and whether the callback writes it
# as the value it is, a structure among them (a result's own), or always a string (Ansible's own words, the diff).
TEXTS = (
    ("msg", "message", MESSAGE_MOST, False, True),
    ("stdout", "stdout", OUTPUT_MOST, True, True),
    ("stderr", "stderr", OUTPUT_MOST, True, True),
    ("exception", "exception", MESSAGE_MOST, False, False),
    ("diff", "diff", DIFF_MOST, True, False),
)


# What check mode makes of a task, and the modules it skips rather than evaluates.
VERDICTS = ("would change", "would not change", "not evaluated")
UNEVALUATED = frozenset({"command", "shell", "raw"})
ANSIBLE_OWN = ("ansible.builtin.", "ansible.legacy.")


@dataclasses.dataclass(frozen=True)
class Checked:
    """One task of a run in check mode: its name, its part, what check mode makes of it (one of VERDICTS), and the
    diffs heard of it, struck and joined ("" for none)."""

    task: str
    part: str | None
    verdict: str
    diff: str = ""


def _unevaluated(action: str) -> bool:
    for prefix in ANSIBLE_OWN:
        action = action.removeprefix(prefix)
    return action in UNEVALUATED


@dataclasses.dataclass(frozen=True)
class Counts:
    """What a caller needs of a layer-1 run: the hosts' failures, unreachable tasks and changes; recap says whether
    they are Ansible's own recap's (True) or counted from the results heard, the child having ended before its recap."""

    failed: int
    unreachable: int
    changed: int
    recap: bool


def _cut(text: str, most: int, end: bool) -> str:
    if len(text) <= most:
        return text
    return "…" + text[-(most - 1) :] if end else text[: most - 1] + "…"


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class Heard:
    """What one layer-1 child writes on its events pipe, handed to job's emit as `step` events of machine, its
    identity, with name, the name a person chose for it, beside it, every secret of redacted struck from each."""

    def __init__(self, job: jobs.Job, machine: str, name: str, redacted: Sequence[Secret] = ()) -> None:
        if not isinstance(job, jobs.Job):
            raise TypeError("the events go to a run's job, a jobs.Job")
        if not isinstance(machine, str) or not checks.HOST_KEY_ALIAS_RE.fullmatch(machine):
            raise TypeError("the events are a machine's, keyed by its identity, agent-warehouse-<its SMBIOS UUID>")
        if not isinstance(name, str) or not name:
            raise TypeError("the name a person chose for the machine rides beside its identity, a str")
        if isinstance(redacted, (str, bytes, Secret)) or not isinstance(redacted, Sequence):
            raise TypeError("the run's redaction list is a sequence of Secrets")
        if not all(isinstance(secret, Secret) for secret in redacted):
            raise TypeError("the run's redaction list holds Secrets alone: what the run opened, as it opened them")
        self._job = job
        self._machine = machine
        self._name = name
        self._redacted = tuple(redacted)
        self._counted = {"failed": 0, "unreachable": 0, "changed": 0}
        self._recap: dict[str, int] | None = None
        self._tasks: list[dict] = []

    # ---------------------------------------------------------------- what a string becomes ----

    def _plain(self, text: str) -> str:
        """text struck of every secret, the longest first, made plain, and struck again; a code point no UTF-8 holds
        replaced."""
        text = plain_text(struck_all(text, self._redacted))
        return struck_all(text, self._redacted).encode("utf-8", "replace").decode("utf-8")

    def clean(self, value: object, most: int = MESSAGE_MOST, end: bool = False) -> str:
        """value as an event may hold it: a string struck of every secret, made plain, struck again, then cut to most
        characters (its end where end is set); anything but a string is ""."""
        if not isinstance(value, str):
            return ""
        return _cut(self._plain(value), most, end)

    def _as_json(self, value: object, depth: int) -> str:
        """value, a structure JSON read, as JSON's text of it, each of its strings, a mapping's keys among them, made
        plain first (_plain); a part deeper than DEPTH_MOST said as ELIDED."""
        if isinstance(value, str):
            return json.dumps(self._plain(value), ensure_ascii=False)
        if value is None or isinstance(value, (bool, int, float)):
            return json.dumps(value)
        if depth >= DEPTH_MOST:
            return json.dumps(ELIDED, ensure_ascii=False)
        if isinstance(value, list):
            return "[" + ", ".join(self._as_json(each, depth + 1) for each in value) + "]"
        if isinstance(value, dict):
            # Made pair by pair, never as a mapping again: two keys struck to one mark keep both their values.
            pairs = (f"{self._as_json(key, depth)}: {self._as_json(each, depth + 1)}" for key, each in value.items())
            return "{" + ", ".join(pairs) + "}"
        return json.dumps(ELIDED, ensure_ascii=False)

    def rendered(self, value: object, most: int = MESSAGE_MOST, end: bool = False) -> str:
        """A value of a result's the callback wrote as the value it is: a string as clean makes it; a structure (a
        mapping, a list, a number) made text only once each of its strings is struck and plain, then struck once more,
        since escaping a quote or a backslash can make a secret's text, and cut as clean cuts."""
        if isinstance(value, str):
            return self.clean(value, most, end)
        return _cut(struck_all(self._as_json(value, 0), self._redacted), most, end)

    # ---------------------------------------------------------------- one line ----

    def _step(self, text: str, **data: object) -> None:
        self._job.emit("step", self._machine, text, name=self._name, **data)

    def unreadable(self, sentence: str = NOT_READ) -> None:
        self._step(sentence, status=UNREADABLE)

    def hand(self, line: bytes | str) -> None:
        """One line of the pipe, handed to emit as a step event: an event the callback wrote, or one that could not be
        read."""
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            self.unreadable()
            return
        if not isinstance(event, dict) or event.get("event") not in KINDS:
            self.unreadable()
            return
        kind = event["event"]
        if kind == "stats":
            self._recapped(event.get("hosts"))
            return
        if kind == "fault":
            hook, error = self.clean(event.get("hook"), NAME_MOST), self.clean(event.get("error"), NAME_MOST)
            self._step(NOT_WRITTEN.format(error=error or "an error", hook=hook or "a hook"), status=STATUS[kind])
            return
        if kind == "play":
            self._step(self.clean(event.get("name"), NAME_MOST), status=STATUS[kind])
            return
        task = self.clean(event.get("task"), NAME_MOST)
        data: dict[str, object] = {"status": STATUS[kind], "part": self.clean(event.get("part"), NAME_MOST) or None}
        data["task"] = task
        if kind == "task":
            data["handler"] = event.get("handler") is True
            action = self.clean(event.get("action"), NAME_MOST)
            self._tasks.append({"task": task, "part": data["part"], "action": action, "kinds": set(), "diffs": []})
            self._step(task, **data)
            return
        self._result(kind, event, data)
        self._tally(kind, task, data)
        self._step(task, **data)

    def _tally(self, kind: str, task: str, data: Mapping[str, object]) -> None:
        """A result kept for checked: of the task last started, where it is that task's."""
        if not self._tasks or self._tasks[-1]["task"] != task:
            return
        current = self._tasks[-1]
        if kind == "diff":
            if data.get("diff"):
                current["diffs"].append(data["diff"])
        elif kind != "retry":
            current["kinds"].add(kind)

    @property
    def checked(self) -> tuple[Checked, ...]:
        """Each task heard, as check mode makes it: see the module's docstring."""
        made = []
        for each in self._tasks:
            kinds = each["kinds"]
            if not kinds or kinds & {"failed", "unreachable"}:
                verdict = "not evaluated"
            elif _unevaluated(each["action"]) and kinds == {"skipped"}:
                verdict = "not evaluated"
            elif "changed" in kinds:
                verdict = "would change"
            else:
                verdict = "would not change"
            made.append(Checked(each["task"], each["part"], verdict, "\n".join(each["diffs"])))
        return tuple(made)

    def _result(self, kind: str, event: Mapping[str, object], data: dict[str, object]) -> None:
        """A result's fields, each held to its type, into data; and the counts kept of it."""
        item = "item" in event
        if item:
            # A censored item is written null; so is an item that is none.
            data["item"] = None if event["item"] is None else self.rendered(event["item"], NAME_MOST)
        for flag in ("censored", "ignored"):
            if event.get(flag) is True:
                data[flag] = True
        for given, field, most, end, valued in TEXTS:
            value = event.get(given)
            if value is None or value == "":
                continue
            if isinstance(value, str):
                data[field] = self.clean(value, most, end)
            elif valued:
                data[field] = self.rendered(value, most, end)
        for given in ("rc", "attempts", "retries"):
            count = event.get(given)
            if isinstance(count, int) and not isinstance(count, bool):
                data[given] = count
        warnings = event.get("warnings")
        if isinstance(warnings, list):
            kept = [self.clean(each) for each in warnings[:WARNINGS_MOST] if isinstance(each, str) and each]
            if kept:
                data["warnings"] = kept
        if item or kind not in ("failed", "unreachable", "changed"):
            return
        if kind == "failed" and data.get("ignored"):
            return
        self._counted[kind] += 1

    def _recapped(self, hosts: object) -> None:
        """The recap: Ansible's own counts, summed over its hosts (one), said as a step event."""
        summed = dict.fromkeys(RECAP, 0)
        if isinstance(hosts, Mapping):
            for counts in hosts.values():
                if isinstance(counts, Mapping):
                    for name in RECAP:
                        summed[name] += _count(counts.get(name)) or 0
        self._recap = summed
        counts = {("failed" if name == "failures" else name): summed[name] for name in RECAP}
        self._step(" ".join(f"{name}={count}" for name, count in counts.items()), status=STATUS["stats"], counts=counts)

    # ---------------------------------------------------------------- the pipe ----

    async def read(self, stream: StreamReader) -> Counts:
        """Every line of stream handed, as it comes, to the pipe's end; then the counts. stream's limit must be at
        least LINE_MOST: a longer line is left out whole, said once."""
        import asyncio

        skipping = False
        while True:
            try:
                line = await stream.readuntil(b"\n")
            except asyncio.IncompleteReadError:
                # The pipe's end; a line the child did not finish there is no event, and was never one to say.
                break
            except asyncio.LimitOverrunError as over:
                await stream.readexactly(over.consumed)
                if not skipping:
                    skipping = True
                    self.unreadable(TOO_LONG.format(most=LINE_MOST))
                continue
            if skipping:
                # The long line's own end.
                skipping = False
                continue
            self.hand(line)
        return self.counts

    @property
    def counts(self) -> Counts:
        if self._recap is not None:
            recap = self._recap
            return Counts(recap["failures"], recap["unreachable"], recap["changed"], True)
        counted = self._counted
        return Counts(counted["failed"], counted["unreachable"], counted["changed"], False)
