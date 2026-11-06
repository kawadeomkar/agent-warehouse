"""Text a VM printed, made safe before a message on this machine quotes it; filter_plugins/text.py's filters.

The VM's user controls what claude, gh and ssh print there, and ansible-core
2.21 writes a failure's [ERROR] line to the terminal and the run log raw, so an
escape sequence quoted in a message would retitle the terminal, recolour it, or
write the clipboard (OSC 52). Removing only the control characters left the
sequences' payload behind as text: a base64 clipboard write, a window title.

banner draws the block that sets a moment the run waits for the person apart
from the task output around it: in a pause or vars_prompt prompt, which
ansible-core prints as written, and in the stdout callback
(callback_plugins/claude_on_proxmox.py), so both look the same.

one_line keeps a value a VM chose to one line of a report: `just status`
(claude_vm.status.model) draws its report from them, and every live run
(claude_vm.run.model) its reports, and a newline in a directory's name
would otherwise start a line of its own - a forged item with a command to run.

plain_events is for the stdout callbacks: what a module returns as warnings,
deprecations or a failure's message is the VM's to choose, and Display prints
it as it is, so each callback prints those through plain_summary.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import re
import shutil
import unicodedata

# Each escape sequence whole: a string sequence (OSC, DCS, SOS, PM, APC) up to
# its terminator, or the end of its line when it has none; a CSI with its
# parameters and final byte; any other ESC sequence; and the 8-bit forms.
_SEQUENCES = re.compile(
    r"(?:\x1b[\]PX^_]|[\x9d\x90\x98\x9e\x9f])[^\x07\x1b\x9c\n]*(?:\x07|\x1b\\|\x9c)?"
    r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]?"
    r"|\x1b[ -/]*[0-~]?"
)
# What is left that a terminal acts on: every C0 and C1 control but tab and newline.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def plain_text(text):
    """Return ``text`` without escape sequences or control characters; tab and newline stay."""
    if text is None:
        return ""
    return _CONTROL.sub("", _SEQUENCES.sub("", str(text)))


# Unicode's format, line and paragraph separators - the bidi overrides and
# isolates, zero-width characters - which can make a line read in an order other
# than its own: visible_text makes each a space.
_FORMAT_CATEGORIES = frozenset({"Cf", "Zl", "Zp"})
_SPACES = re.compile(r" {2,}")


def visible_text(text):
    """Return ``text`` through plain_text, each Unicode format or separator character a space.

    Its lines, tabs and indents stay: what a report keeps line by line (every
    live run's, claude_vm.ansible.engine) reads in the order it was
    written, as one_line's does.
    """
    return "".join(" " if unicodedata.category(ch) in _FORMAT_CATEGORIES else ch for ch in plain_text(text))


def one_line(text, limit=120):
    """Return ``text`` as one line of plain text, at most ``limit`` characters, ending in "…" when cut.

    visible_text first, so whole escape sequences and every control character
    but tab and newline go - a carriage return among them, removed, not made a
    space - and each Unicode format or separator character is a space; then each
    newline and tab becomes one too, and runs of spaces one. None is "". What a
    VM printed is passed as it is: nothing needs cleaning first.
    """
    if text is None:
        return ""
    cleaned = "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in visible_text(text))
    cleaned = _SPACES.sub(" ", cleaned).strip()
    limit = max(int(limit), 1)
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


# The block's width: these columns, or the terminal's when it is narrower,
# never less than the narrowest that still reads as a block.
_WIDTH = 80
_NARROWEST = 20


def banner(body, title, host="", topic="", width=None):
    """Return ``body`` under a ``#`` block titled ``title``, ending in a newline.

    The title line names ``host`` and ``topic`` after the title where given.
    Body lines are never wrapped, so a URL or a command in one stays whole to
    copy: the terminal soft-wraps it. A list body is one line per item. The
    block ends in a newline because a prompt's last word must start a line of
    its own, and a "\n" inside the Jinja of a folded YAML scalar reaches the
    screen as a backslash and an n. Every part goes through plain_text.
    """
    if width is None:
        width = min(_WIDTH, shutil.get_terminal_size((_WIDTH, 24)).columns)
    rule = "#" * max(int(width), _NARROWEST)
    # plain_text gives "" for None, so a part that is missing or cleans to nothing is left out.
    parts = (plain_text(part).strip() for part in (title, host, topic))
    heading = "  -  ".join(part for part in parts if part)
    lines = body if isinstance(body, (list, tuple)) else [body]
    text = "\n".join(plain_text(line) for line in lines if line is not None).strip("\n")
    return f"{rule}\n##  {heading}\n{rule}\n" + (f"{text}\n" if text else "")


def plain_summary(summary):
    """Return ansible-core's warning, deprecation or error summary with every string in it through visible_text.

    A module's result chooses its warnings' and deprecations' text, and a failed
    one its message, which Display prints as [WARNING], [DEPRECATION WARNING]
    and [ERROR] lines, raw, on the terminal and in the run log: a VM's root can
    make any module on it return them. visible_text, as every live report has:
    no escape sequence, and no bidi override to make a line read in an order of
    its own. The summary is a frozen dataclass of events, chained events and
    plugin details, rebuilt here field by field; an enum is kept as it is (some
    are strings), and None stays None.
    """
    if isinstance(summary, enum.Enum):
        return summary
    if isinstance(summary, str):
        return visible_text(summary)
    if isinstance(summary, tuple):
        return tuple(plain_summary(item) for item in summary)
    if dataclasses.is_dataclass(summary) and not isinstance(summary, type):
        fields = dataclasses.fields(summary)
        return dataclasses.replace(
            summary, **{field.name: plain_summary(getattr(summary, field.name)) for field in fields if field.init}
        )
    return summary


class _PlainResult:
    """A callback's current task result whose warnings, deprecations and error read through plain_summary."""

    def __init__(self, result):
        self._result = result

    @property
    def warnings(self):
        return [plain_summary(warning) for warning in self._result.warnings]

    @property
    def deprecations(self):
        return [plain_summary(deprecation) for deprecation in self._result.deprecations]

    @property
    def exception(self):
        exception = self._result.exception
        return plain_summary(exception) if exception is not None else None

    def __getattr__(self, name):
        return getattr(self._result, name)


@contextlib.contextmanager
def plain_events(callback):
    """Inside, the callback's _handle_warnings and _handle_exception print the current result's events plain.

    ansible-core's CallbackBase reads them from _current_task_result, the
    result each hook is called for; the stdout callbacks wrap both methods in
    this, so every hook that prints a result's warnings or error does it plain.
    """
    current = getattr(callback, "_current_task_result", None)
    if current is None:
        yield
        return
    callback._current_task_result = _PlainResult(current)
    try:
        yield
    finally:
        callback._current_task_result = current


def plural(count, word, words=None):
    """A count and its word: 1 task, 2 tasks; 1 branch, 3 branches with words given."""
    return f"{count} {word if count == 1 else words or word + 's'}"
