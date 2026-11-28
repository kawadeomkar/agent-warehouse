"""The product's stdout callback for layer 1's child: each event of the play as one line of JSON on the events pipe, and
nothing on the terminal (docs/architecture/engine.md §16.10).

Only the configuration layer 1 generates names it (lib/claude_vm/guest/layer1.py's CALLBACK and CALLBACKS): today's
ansible.cfg names its own folder, never this one. The pipe's write end is the descriptor AGENT_WAREHOUSE_EVENTS names,
which the run handed over and alone reads (lib/claude_vm/guest/events.py); it is made non-inheritable here at once, so
that no program a task starts holds it. A child started without one has no events to write, and refuses to start.

The events: a play's start (`play`), a task's (`task`), a result (`ok`, `changed`, `failed`, `skipped`, `unreachable`,
an item of a loop with `item`), a retry (`retry`), a diff (`diff`) and the recap (`stats`), with the task's name and its
part, the role it is in. A skip says whether a condition made it (`condition`: a false `when`, of the task or of each
item of its loop), by which F21's check tells a task it evaluated from one check mode skipped unrun. A result says
its message, and where it failed, was unreachable or the run was given -v, its output, return code and exception;
never more of a result than those, and never of a result Ansible censored for no_log: of one, only its kind, its task
and whether a condition skipped it, and none of its message, exception, warnings, item or diff, which
ansible-core 2.21 keeps in a censored result's `exception` and `warnings` and hands a callback whole. A diff is written
only in a run given --diff (F21's --check --diff), never for a task's own `diff: true` alone. A fault of a hook here is
written as an event of its own (`fault`, the hook and the error's type, no words of it), so that a result never
vanishes unseen, which Ansible's default of a warning for a failing callback would let happen.

A result's message, its item and its output are written as the values they are: a string as a string, and a mapping or
a list as one, never as JSON text inside a string, whose escaping (a quote, a backslash, a line break, a character
outside ASCII) would hide a secret in it from the run's strike by its exact text. Each string, a mapping's keys among
them, passes through `masked`, the one place F5a puts ansible-core 2.22's mask_secrets(): until then the identity. A
string longer than STRING_MOST keeps its end from the first line break inside it, never a part of a line, so that no
secret is cut in two before the run strikes it by its exact value; one with no line break there is left out, said by its
length. The run strikes every string of the structure of every secret it opened and makes it plain, and only then makes
the structure text (events.py): this module writes, and decides nothing of what a person reads.

Writing is under a lock, since Ansible calls a callback from its main thread and its results thread. A pipe whose
reader has gone (EPIPE) ends the writing, and the play goes on, unheard and without a traceback; any other error writing
is said once, as a warning in the child's own output.
"""

from __future__ import annotations

DOCUMENTATION = """
    name: aw_events
    type: stdout
    short_description: each event of the play as one line of JSON on agent-warehouse's events pipe
    description:
      - Writes each event of the play as one line of JSON to the descriptor AGENT_WAREHOUSE_EVENTS names, and nothing
        to the terminal. Only the configuration agent-warehouse's layer 1 generates names it.
    requirements:
      - set as stdout_callback in the configuration layer 1 generates
"""

import functools
import json
import os
import threading

from ansible import context
from ansible.plugins.callback import CallbackBase

# The variable that names the events pipe's descriptor: layer1.py's EVENTS_VARIABLE, which tests hold equal.
VARIABLE = "AGENT_WAREHOUSE_EVENTS"
# The kinds of event written, in the order a play meets them.
KINDS = ("play", "task", "ok", "changed", "failed", "skipped", "unreachable", "retry", "diff", "stats", "fault")
# The longest string written whole: beyond it, its end from a line break.
STRING_MOST = 256 * 1024
# The most warnings of one result written, and the most links of an error's chain followed.
WARNINGS_MOST = 20
CHAIN_MOST = 10


def masked(text):
    """text as it may leave this process: F5a passes it through ansible-core 2.22's mask_secrets(); until then, as it
    is."""
    return text


def _bounded(text):
    """text whole where it is short enough; else its end, from the first line break inside the STRING_MOST characters
    it ends with, after a line that says how much was left out; else that line alone."""
    if len(text) <= STRING_MOST:
        return text
    tail = text[-STRING_MOST:]
    at = tail.find("\n")
    if at < 0:
        return f"({len(text)} characters left out: too long, with no line break to cut at)"
    return f"({len(text) - STRING_MOST + at + 1} characters left out)\n{tail[at + 1 :]}"


def _text(value):
    """Ansible's own words of an error or a warning as one string: a string as it is, bytes decoded, anything else as
    JSON. Never a value of a result's, which is written as the value it is (_written)."""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return json.dumps(value, sort_keys=True, default=str)


def _key(key):
    """A mapping's key as JSON keeps one: a string masked, a number, a truth value or None as it is, anything else its
    text, masked."""
    if isinstance(key, str):
        return masked(key)
    if key is None or isinstance(key, (bool, int, float)):
        return key
    return masked(str(key))


def _written(value):
    """value as JSON keeps it, with every string masked and bounded, at any depth: a mapping's keys masked, bytes
    decoded, a tuple or a set a list, and anything else JSON has no form for its text."""
    if isinstance(value, str):
        return _bounded(masked(value))
    if isinstance(value, bytes):
        return _bounded(masked(value.decode("utf-8", "replace")))
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return {_key(key): _written(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_written(item) for item in value]
    return _bounded(masked(str(value)))


def _error_text(summary):
    """An error summary's words: each message of its chain, and a traceback where Ansible kept one; None for none."""
    event = getattr(summary, "event", None)
    parts = []
    for _ in range(CHAIN_MOST):
        if event is None:
            break
        for name in ("msg", "formatted_traceback"):
            said = getattr(event, name, None)
            if said:
                parts.append(_text(said))
        chain = getattr(event, "chain", None)
        event = getattr(chain, "event", None) if chain is not None and getattr(chain, "follow", True) else None
    return "\n".join(parts) or None


def _role(task):
    role = getattr(task, "_role", None)
    return role.get_name(include_role_fqcn=False) if role else None


def _about(task):
    """A task's name, without its role's prefix, and its part: the role it is in, or None."""
    part = _role(task)
    name = task.get_name().strip()
    if part and name.startswith(part + " : "):
        name = name[len(part) + 3 :]
    return {"task": name, "part": part}


def _by_condition(found):
    """Whether a skipped result was a false condition's: Ansible's false_condition or its skip_reason for one, set
    only where a `when` was false; a loop's whole result where every item's was. A skip of check mode (a module that
    does not support it, a command it would have run) is none."""
    if not isinstance(found, dict):
        return False
    if found.get("false_condition") is not None or found.get("skip_reason") == "Conditional result was False":
        return True
    items = found.get("results")
    return isinstance(items, list) and bool(items) and all(_by_condition(each) for each in items)


def _censored(result):
    """Whether Ansible censored result for no_log: its task's own, or its item's."""
    found = result.result
    return bool(result.task.no_log) or "censored" in found or bool(found.get("_ansible_no_log"))


def _hook(method):
    """A hook whose fault is written as an event, never raised into Ansible, which would only warn."""

    @functools.wraps(method)
    def hook(self, *args, **kwargs):
        try:
            method(self, *args, **kwargs)
        except Exception as error:
            self._write({"event": "fault", "hook": method.__name__, "error": type(error).__name__})

    return hook


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "aw_events"
    # Never one of the extra callbacks Ansible loads from a plugin folder by itself: the stdout one alone.
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self, display=None, options=None):
        super().__init__(display=display, options=options)
        given = os.environ.get(VARIABLE, "")
        if not given.isdigit():
            raise RuntimeError(f"{VARIABLE} names no descriptor: the events pipe is the run's, handed over by layer 1")
        self._fd = int(given)
        os.fstat(self._fd)
        os.set_inheritable(self._fd, False)
        self._lock = threading.Lock()
        self._diff = bool(context.CLIARGS.get("diff", False))

    # ---------------------------------------------------------------- the pipe ----

    def _write(self, event):
        # The event's own fields as they are; every value written as JSON keeps it, its strings masked.
        written = {field: _written(value) for field, value in event.items()}
        line = json.dumps(written, ensure_ascii=True, separators=(",", ":"), default=str) + "\n"
        data = memoryview(line.encode("ascii"))
        with self._lock:
            if self._fd is None:
                return
            try:
                while data:
                    data = data[os.write(self._fd, data) :]
            except BrokenPipeError:
                self._fd = None
            except OSError as error:
                self._fd = None
                self._display.warning(f"the events pipe could not be written ({error.strerror}): the play goes on")

    # ---------------------------------------------------------------- what a result says ----

    def _result(self, kind, result, item=False, ignored=None):
        found = result.result
        event = {"event": kind, **_about(result.task)}
        censored = _censored(result)
        if item:
            event["item"] = None if censored else self._get_item_label(found)
        if ignored is not None:
            event["ignored"] = bool(ignored)
        if kind == "skipped":
            # A yes or no alone, never the condition's words; said of a censored result too.
            event["condition"] = _by_condition(found)
        if censored:
            event["censored"] = True
            return event
        if found.get("msg") not in (None, ""):
            event["msg"] = found["msg"]
        if kind in ("failed", "unreachable") or self._display.verbosity > 0:
            for key, other in (("stdout", "module_stdout"), ("stderr", "module_stderr")):
                value = found.get(key) or found.get(other)
                if value:
                    event[key] = value
            if isinstance(found.get("rc"), int) and not isinstance(found.get("rc"), bool):
                event["rc"] = found["rc"]
            exception = _error_text(result.exception) if result.exception is not None else None
            if exception:
                event["exception"] = exception
        warnings = [_text(getattr(each.event, "msg", "")) for each in list(result.warnings)[:WARNINGS_MOST]]
        if [each for each in warnings if each]:
            event["warnings"] = [each for each in warnings if each]
        return event

    def _outcome(self, result, item=False):
        return self._result("changed" if result.result.get("changed", False) else "ok", result, item)

    # ---------------------------------------------------------------- the hooks ----

    @_hook
    def v2_playbook_on_play_start(self, play):
        self._write({"event": "play", "name": play.get_name().strip()})

    @_hook
    def v2_playbook_on_task_start(self, task, is_conditional):
        self._write({"event": "task", **_about(task), "handler": False})

    @_hook
    def v2_playbook_on_handler_task_start(self, task):
        self._write({"event": "task", **_about(task), "handler": True})

    @_hook
    def v2_runner_on_ok(self, result):
        self._write(self._outcome(result))

    @_hook
    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._write(self._result("failed", result, ignored=ignore_errors))

    @_hook
    def v2_runner_on_skipped(self, result):
        self._write(self._result("skipped", result))

    @_hook
    def v2_runner_on_unreachable(self, result):
        self._write(self._result("unreachable", result))

    @_hook
    def v2_runner_item_on_ok(self, result):
        self._write(self._outcome(result, item=True))

    @_hook
    def v2_runner_item_on_failed(self, result):
        self._write(self._result("failed", result, item=True))

    @_hook
    def v2_runner_item_on_skipped(self, result):
        self._write(self._result("skipped", result, item=True))

    @_hook
    def v2_runner_retry(self, result):
        found = result.result
        event = {"event": "retry", **_about(result.task)}
        for key in ("attempts", "retries"):
            if isinstance(found.get(key), int) and not isinstance(found.get(key), bool):
                event[key] = found[key]
        if _censored(result):
            event["censored"] = True
        elif found.get("msg") not in (None, ""):
            event["msg"] = found["msg"]
        self._write(event)

    @_hook
    def v2_on_file_diff(self, result):
        # Only a run given --diff, and never a task Ansible censored: a diff is a file's text as the machine holds it.
        if not self._diff or _censored(result):
            return
        found = result.result
        looped = found.get("results") if isinstance(found.get("results"), list) else None
        for each in looped if looped is not None else [found]:
            if not isinstance(each, dict) or each.get("censored") or each.get("_ansible_no_log"):
                continue
            if each.get("diff") and each.get("changed", False):
                said = self._get_diff(each["diff"])
                if said:
                    self._write({"event": "diff", **_about(result.task), "diff": said})

    @_hook
    def v2_playbook_on_stats(self, stats):
        hosts = {}
        for host in sorted(stats.processed):
            hosts[host] = {key: int(value) for key, value in stats.summarize(host).items()}
        self._write({"event": "stats", "hosts": hosts})
