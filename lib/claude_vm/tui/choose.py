"""A question with a few answers picked with the arrow keys: just init's yes-or-no and choice questions, asked as a
menu at the terminal while the live region steps aside (docs/architecture/custom-code.md §7.8).

The answers travel with the question from Ansible's worker to the main process in built-in types only: the action
playbooks/action_plugins/ask.py hands prompt_until a complete_input of Enter's two bytes and one entry more, MARKER
and the menu as JSON (marked). A class of the project's there would need the main process to import the project's
code to unpickle the prompt, and where it cannot, Ansible's results thread dies on the message and the worker waits
for its answer for good. Where nothing hooks the prompt, Ansible compares one key at a time with the list, which
never equals the marker: the typed answer still works.

The menu (ask) draws with screen.py's lines, palette and cut, as every live output does: each line cut short of the
terminal's width, so none wraps, the cursor never hidden, one write per redraw in synchronized output. It reads the
keys from the stdin it is given, never /dev/tty: up and down (CSI and SS3 forms), Home and End, a digit for the
answer it numbers, an answer's first letter, Enter to choose, Esc to decide later; the terminal's interrupt key
raises Interrupted, which the hook turns into what prompt_until raises, so pause's continue-or-abort follows. The
terminal's settings are restored whatever happens. Once chosen, the menu is erased; the hook says the answer on one
line (collapsed).

Stdlib only; imported as claude_vm.tui.choose (docs/architecture/custom-code.md §7.10).
"""

from __future__ import annotations

import json
import os
import select
import termios

from . import screen
from .live_region import SYNC_OFF, SYNC_ON, can_draw

# The mark of a prompt whose answers ride along: a NUL each side, so no key a person types is ever it.
MARKER = b"\x00agent-warehouse:choices\x00"
HINT = "↑ ↓ to move · Enter to choose · Esc to decide later"
# The keys of the CSI and SS3 sequences a terminal sends for the arrows, Home and End, in either cursor mode.
SEQUENCES = {
    b"\x1b[A": "up",
    b"\x1bOA": "up",
    b"\x1b[B": "down",
    b"\x1bOB": "down",
    b"\x1b[H": "home",
    b"\x1bOH": "home",
    b"\x1b[1~": "home",
    b"\x1b[7~": "home",
    b"\x1b[F": "end",
    b"\x1bOF": "end",
    b"\x1b[4~": "end",
    b"\x1b[8~": "end",
}
# How long after Esc the rest of a sequence may come before it is Esc alone.
SEQUENCE_SECONDS = 0.05
# How often, while nobody types, the terminal's size is read again: a resize redraws the menu for the new width.
IDLE_SECONDS = 0.25


class Interrupted(Exception):
    """The terminal's interrupt key, Ctrl-C, typed at the menu."""


# --- the menu as data ---------------------------------------------------------------------------------------------
def marked(menu):
    """prompt_until's complete_input for a menu: Enter's two bytes, then MARKER and the menu as JSON. menu is
    {"title", "label", "options": [{"value", "text", "note"}], "chosen"}: the title the collapsed line says, the
    prompt's last words, each answer - the value given back, its text and a note beside it - and the one marked
    first."""
    return [b"\r", b"\n", MARKER + json.dumps(menu, sort_keys=True).encode()]


def menu_of(complete_input):
    """The menu a prompt's complete_input carries, held to its shape, or None: a prompt like any other."""
    for entry in complete_input or ():
        if not isinstance(entry, bytes) or not entry.startswith(MARKER):
            continue
        try:
            menu = json.loads(entry[len(MARKER) :])
        except ValueError:
            return None
        options = menu.get("options") if isinstance(menu, dict) else None
        if not isinstance(options, list) or not options:
            return None
        if not all(isinstance(o, dict) and isinstance(o.get("value"), str) and o.get("text") for o in options):
            return None
        chosen = menu.get("chosen", 0)
        return {
            "title": str(menu.get("title") or ""),
            "label": str(menu.get("label") or ""),
            "options": [
                {"value": o["value"], "text": str(o["text"]), "note": str(o.get("note") or "")} for o in options
            ],
            "chosen": chosen if isinstance(chosen, int) and 0 <= chosen < len(options) else 0,
        }
    return None


def question(msg, menu):
    """What is said above the menu: the prompt's text without its last words and their colon, which the menu takes
    the place of."""
    text = str(msg)
    tail = f"{menu['label']}:"
    if menu["label"] and text.rstrip().endswith(tail):
        text = text.rstrip()[: -len(tail)]
    return text.rstrip("\n")


def collapsed(menu, value):
    """The one line the menu leaves: the title and the answer's text, or that it is left for later."""
    answer = next((o["text"] for o in menu["options"] if o["value"] == value), "") if value else ""
    said = answer or "left for later"
    title = menu["title"]
    if not title:
        return said
    return f"{title} {said}" if title.endswith("?") else f"{title}: {said}"


# --- the keys -----------------------------------------------------------------------------------------------------
def step(menu, chosen, key):
    """Where a key leaves the menu: ("move", index), ("choose", value), ("later", ""), or ("stay", chosen)."""
    options = menu["options"]
    last = len(options) - 1
    if key in ("\r", "\n"):
        return "choose", options[chosen]["value"]
    if key in ("esc", "\x04"):
        return "later", ""
    if key == "up":
        return "move", max(0, chosen - 1)
    if key == "down":
        return "move", min(last, chosen + 1)
    if key == "home":
        return "move", 0
    if key == "end":
        return "move", last
    if len(key) == 1 and key.isdigit() and 1 <= int(key) <= len(options):
        return "move", int(key) - 1
    if len(key) == 1 and key.isalpha():
        # The next answer after this one whose text starts with the letter, round again from the top.
        order = list(range(chosen + 1, len(options))) + list(range(0, chosen + 1))
        found = [i for i in order if options[i]["text"][:1].lower() == key.lower()]
        if found:
            return "move", found[0]
    return "stay", chosen


def key_of(sequence):
    """A sequence read as a key: one of SEQUENCES', Esc alone, or the character typed."""
    if sequence in SEQUENCES:
        return SEQUENCES[sequence]
    if sequence == b"\x1b":
        return "esc"
    if sequence.startswith(b"\x1b"):
        return ""  # a sequence the menu has no use for: a function key, a mouse report
    return sequence.decode(errors="replace")


# --- the frame ----------------------------------------------------------------------------------------------------
def frame(menu, chosen, cols, rows):
    """The menu's lines, each cut short of cols so none wraps: a window of the answers around the one marked, as many
    as the terminal's rows leave room for, then the hint."""
    options = menu["options"]
    room = max(1, min(len(options), rows - 3))
    top = 0 if chosen < room else chosen - room + 1
    width = max(len(o["text"]) for o in options)
    lines = []
    for index in range(top, top + room):
        option = options[index]
        marker, style = ("› ", "c") if index == chosen else ("  ", "")
        line = [screen.seg(marker, "c"), screen.seg(f"{option['text']:<{width}}", "b" if style else "")]
        if option["note"]:
            line.append(screen.seg(f"  {option['note']}", "d"))
        lines.append(screen.cut(line, cols - 1))
    hint = HINT if room == len(options) else f"{HINT} · {chosen + 1} of {len(options)}"
    lines.append(screen.cut([screen.seg(hint, "d")], cols - 1))
    return lines


# --- at the terminal ----------------------------------------------------------------------------------------------
def can_choose(stdin, stdout, environ=None):
    """Whether a menu can be asked here: stdin a terminal of this process's foreground, and stdout one that can draw."""
    try:
        fd = stdin.fileno()
        foreground = os.isatty(fd) and os.getpgrp() == os.tcgetpgrp(fd)
    except (AttributeError, ValueError, OSError):
        return False
    return foreground and can_draw(stdout, environ)


def _keys(fd):
    """The next key's bytes: one byte, or an escape sequence whole; b"" at the end of the input; None when nobody
    typed for IDLE_SECONDS."""
    ready, _, _ = select.select([fd], [], [], IDLE_SECONDS)
    if not ready:
        return None
    first = os.read(fd, 1)
    if first != b"\x1b":
        return first
    sequence = first
    while select.select([fd], [], [], SEQUENCE_SECONDS)[0]:
        sequence += os.read(fd, 1)
        # A CSI ends at its final byte, an SS3 after the one byte that follows its O.
        if sequence[1:2] == b"O" and len(sequence) == 3:
            break
        if sequence[1:2] == b"[" and len(sequence) > 2 and 0x40 <= sequence[-1] <= 0x7E:
            break
        if sequence[1:2] not in (b"[", b"O"):
            break
    return sequence


def ask(menu, stdin, stdout, color=True):
    """Ask the menu at the terminal and return the chosen answer's value, or "" to decide later. Raises Interrupted
    at the terminal's interrupt key. The terminal is left as it was found, and the menu erased."""
    stdin_fd = stdin.fileno()
    settings = termios.tcgetattr(stdin_fd)
    interrupt = settings[6][termios.VINTR]
    interrupt = interrupt if isinstance(interrupt, bytes) else bytes([interrupt])
    quiet = list(settings)
    quiet[0] = settings[0] & ~(termios.ICRNL | termios.IXON)
    quiet[3] = settings[3] & ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
    quiet[6] = list(settings[6])
    quiet[6][termios.VMIN], quiet[6][termios.VTIME] = 1, 0
    drawn = 0

    def draw(chosen):
        nonlocal drawn
        try:
            cols, rows = os.get_terminal_size(stdout.fileno())
        except (OSError, ValueError):
            cols, rows = 80, 24
        lines = [screen.render(line, color) for line in frame(menu, chosen, cols, rows)]
        up = f"\x1b[{drawn - 1}A" if drawn > 1 else ""
        stdout.write(SYNC_ON + up + "\r" + "\x1b[K\n".join(lines) + "\x1b[K\x1b[J" + SYNC_OFF)
        stdout.flush()
        drawn = len(lines)
        return cols, rows

    # Keys typed before the question was there are not its answer.
    termios.tcflush(stdin_fd, termios.TCIFLUSH)
    termios.tcsetattr(stdin_fd, termios.TCSANOW, quiet)
    try:
        chosen = menu["chosen"]
        size = draw(chosen)
        while True:
            sequence = _keys(stdin_fd)
            if sequence is None:
                try:
                    now = tuple(os.get_terminal_size(stdout.fileno()))
                except (OSError, ValueError):
                    now = size
                if now != size:
                    size = draw(chosen)
                continue
            if sequence == b"":
                return ""
            if sequence == interrupt:
                raise Interrupted
            what, then = step(menu, chosen, key_of(sequence))
            if what == "move" and then != chosen:
                chosen = then
                size = draw(chosen)
            elif what in ("choose", "later"):
                return then
    finally:
        if drawn:
            stdout.write((f"\x1b[{drawn - 1}A" if drawn > 1 else "") + "\r\x1b[J")
            stdout.flush()
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, settings)
