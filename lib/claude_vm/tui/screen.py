"""What every live output draws with: lines of styled text, cut and padded to a width, rendered in one palette.

A line is a list of segments, (text, style): a style is one of PALETTE's keys, or "" for plain. `just status`'s
report (tui/status.py) and every live run (tui/run.py's today, runs/view.py's the new engine's) are built as lines and
rendered here, so they cut, pad, colour and measure alike; live_region.py puts them on the screen. PENDING in a
segment's text marks where the spinner's glyph goes, for a value still being read. A run's marks (MARKS), a
summary's head cut and parts flowed (cut_head, flow), and a report's and a failure's lines said above the region
(report_lines, failure_lines) are here too, so both runs draw them one way, from no model of either.

A line is measured in characters: one each, which East Asian ambiguous-width characters (█ ● ○ – · …) are not in a
terminal set to draw them double (docs/architecture/custom-code.md §7.8).

Pure, nothing read or written. Imported as claude_vm.tui.screen (docs/architecture/custom-code.md §7.10).
"""

from __future__ import annotations

import re
import textwrap

from ..text import plural as _plural

# The palette, as ANSI codes: headings, bold, dim, and the colours of done, to do, failed, running and waiting.
PALETTE = {"h": "1;36", "b": "1", "d": "2", "g": "32", "y": "33", "r": "31", "c": "36", "m": "35"}
# Where a value still being read is drawn: the spinner's glyph in a live frame, "·" in a still one.
PENDING = "\x00"
SPARKS = "▁▂▃▄▅▆▇█"


def seg(text, style=""):
    return (str(text), style)


def plain(line):
    return "".join(text for text, _ in line)


def width(line):
    return len(plain(line))


def pad(line, cols):
    """line, padded with spaces to cols characters."""
    return line + [seg(" " * max(0, cols - width(line)))]


def cut(line, cols):
    """line cut to cols characters, ending in a dim "…" when cut: a live line that wrapped would break the count of
    lines drawn. cols None leaves it whole."""
    if cols is None or width(line) <= cols:
        return line
    out, used = [], 0
    for text, style in line:
        room = cols - 1 - used
        if room <= 0:
            break
        out.append((text[:room], style))
        used += len(text[:room])
    out.append(("…", "d"))
    return out


def right(left, tail, cols):
    """left, then tail aligned to the right edge at cols; without room for both, tail is dropped and left cut."""
    gap = cols - width(left) - width(tail)
    if tail and gap >= 1:
        return [*left, seg(" " * gap), *tail]
    return cut(left, cols)


def render(line, color, glyph=None):
    """line as text: in the palette's colours, or plain; PENDING as the glyph given, else "·"."""

    def piece(text, style):
        text = text.replace(PENDING, glyph or "·")
        if color and style and text:
            return f"\x1b[{PALETTE[style]}m{text}\x1b[0m"
        return text

    return "".join(piece(text, style) for text, style in line).rstrip()


def bar(fraction, cells=12, style=None):
    """A bar of cells: filled in style, or by how full - green, yellow from 80%, red from 90% - and dim the rest."""
    filled = max(0, min(cells, round(fraction * cells)))
    style = style or ("r" if fraction >= 0.9 else "y" if fraction >= 0.8 else "g")
    return [seg("█" * filled, style), seg("░" * (cells - filled), "d")]


def spark(values):
    """A sparkline of fractions, a space for each None."""
    out = []
    for value in values or []:
        out.append(" " if value is None else SPARKS[min(len(SPARKS) - 1, max(0, round(value * (len(SPARKS) - 1))))])
    return "".join(out)


def fill(text, cols):
    """text wrapped to cols, nothing cut: a word longer than a line, a URL, has a line of its own."""
    return textwrap.wrap(str(text), max(1, cols), break_long_words=False, break_on_hyphens=False) or [""]


plural = _plural  # a count and its word, as every report says one


def duration(seconds):
    """A duration as a report says it: 0.6 s, 33 s, 3:58. Its form follows the value as it is said, so 9.96 s is 10 s
    and 59.6 s is 1:00, never 10.0 s, a character wider than any other, nor 60 s."""
    if round(seconds, 1) < 10:
        return f"{seconds:.1f} s"
    if round(seconds) < 60:
        return f"{round(seconds)} s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}:{rest:02d}"


def clock(seconds):
    """A running timer: 0:07, 12:30."""
    minutes, rest = divmod(int(max(0, seconds)), 60)
    return f"{minutes}:{rest:02d}"


# How a state is marked - a VM's in a step's row and in the summary, a stage's or a step's, a check's, a run's end -
# and in what style; running's mark is the spinner's glyph.
MARKS = {
    "running": ("", "c"),
    "waiting": ("⏸", "y"),
    "done": ("✓", "g"),
    "skipped": ("–", "d"),
    "failed": ("✗", "r"),
    "unreachable": ("✗", "r"),
    "left": ("–", "d"),
    "to come": ("○", "d"),
    "stopped": ("■", "y"),
}

# How many lines of what a failed task said its failure draws: twice what today's run keeps of them, run/model.py's
# FAILURE_LINES, which tests/unit/test_aw_run_view.py holds this to.
FAILURE_SAID = 40


def first_sentence(text):
    """text up to the end of its first sentence, or whole when it has one."""
    found = re.match(r"(.+?[.!?])(\s+\S)", text)
    return found.group(1) if found else text


def cut_head(line, limit):
    """A summary's head cut from its right, the clock first, never into its outcome."""
    if width(line) > limit and re.fullmatch(r" · \d\d:\d\d", line[-1][0]):
        line = line[:-1]
    return cut(line, limit)


def flow(head, parts, limit, style=""):
    """head, then parts joined by " · ", wrapped under the column head ends at."""
    lines, line = [], list(head)
    column = width(head)
    for index, part in enumerate(parts):
        piece = part if index == 0 or width(line) == column else " · " + part
        if width(line) + len(piece) > limit and width(line) > column:
            lines.append(line)
            line = [(" " * column, ""), (part, style)]
        else:
            line.append((piece, style))
    if line and not parts:
        line[-1] = (line[-1][0].rstrip(), line[-1][1])
    lines.append(line)
    return lines


# --- what a run says above the region, from its records --------------------------------------------------------
def wrapped(head, lines, cols, color, style="", styles=None):
    """head, then lines wrapped under where head ends, whole - never cut: (coloured, plain) pairs to print. A line
    keeps its own indent, its wrapped pieces under where its words start; styles, one for each line, colour each its
    own way."""
    column = width(head)
    room = max(20, cols - 1 - column)
    out = []
    first = True
    for number, line in enumerate(lines):
        style_ = (styles[number] if styles else "") or style
        indent = len(line) - len(line.lstrip(" "))
        pieces = fill(line.strip(), max(10, room - indent)) if line.strip() else [""]
        for piece in pieces:
            lead = head if first else [(" " * column, "")]
            first = False
            runs = [*lead, (" " * indent + piece, style_)]
            out.append((render(runs, color), render(runs, False)))
    return out


def report_lines(report, cols, color):
    """A report, whole: under its VM, which leads unless the report names it itself."""
    who = [(report["host"], "b"), ("  ", "")] if report["named"] else []
    return wrapped([("  ", ""), *who], report["lines"], cols, color, styles=report["styles"])


def _block(where, who, task, took, how, said, cols, color):
    out = [("", "")]
    out += wrapped([("✗ ", "r")], [f"{where} · {who}: {task}{took}{how}"], cols, color, "r")
    out += wrapped([("    ", "")], [line for line in said if line.strip()][:FAILURE_SAID], cols, color)
    return out


def failure_lines(failure, cols, color):
    """A failure, for good above the region: the step, the VM, the task, what it said, and where the log has it; a
    raise of earlier failures, each cause so, then its own words."""
    if "causes" in failure:
        out = []
        for cause in failure["causes"]:
            out += _block(cause["where"], cause["who"], cause["name"], "", "", cause["said"], cols, color)
        if failure["own"]:
            out += wrapped([("    ", "")], failure["own"], cols, color)
    else:
        took = f" ({duration(failure['seconds'])})" if failure["seconds"] is not None else ""
        how = "" if failure["how"] == "failed" else f" · {failure['how']}"
        out = _block(failure["where"], failure["who"], failure["task"], took, how, failure["said"], cols, color)
    if failure["log"]:
        out += wrapped([("    ", "")], [f"the whole output: {failure['log']}"], cols, color, "d")
    return [*out, ("", "")]
