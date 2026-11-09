"""lib/claude_vm/tui/choose.py, the menu just init asks a yes-or-no or a choice as, and the live engine's hook that
asks it (lib/claude_vm/ansible/engine.py: choosing).

What these hold: the answers travel in built-in types alone, so a main process that cannot import the project's code
still unpickles the prompt; each key moves or answers as it should; every frame fits short of the width, the window
keeps the answer marked in sight, and the hint says where it is in a long list; at a pseudo-terminal of its own the
menu answers down and Enter, a digit, a letter and Esc, raises at Ctrl-C, never hides the cursor, never wraps, is
erased once answered and leaves the terminal as it found it; the hook asks only a marked, shown question at a terminal
that can draw, and hands everything else to Ansible's own prompt; a hidden question never carries its answers. The
menu inside a real just init is test_init_live.py's.
"""

from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml
from ansible.errors import AnsiblePromptInterrupt
from ansible.executor.task_queue_manager import PromptSend
from harness import _at_a_pseudo_terminal, _in_raw_mode
from vt import VT

from claude_vm.ansible import engine
from claude_vm.tui import choose, screen

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "lib"
YES_NO = {
    "title": "Use Tailscale on the VMs?",
    "label": "Use Tailscale on the VMs? y or n",
    "options": [{"value": "y", "text": "yes", "note": ""}, {"value": "n", "text": "no", "note": ""}],
    "chosen": 0,
}
STORAGES = {
    "title": "Storage",
    "label": "Number, Enter to decide later",
    "options": [
        {"value": name, "text": name, "note": f"{index} GiB free on a pool whose name runs long"}
        for index, name in enumerate(["local-lvm", "nfs-fast", "nfs-slow", "zfs"], 1)
    ],
    "chosen": 0,
}


# ------------------------------------------------------------------- the data ---
def test_a_menu_rides_in_complete_input_and_comes_back_whole():
    complete = choose.marked(YES_NO)
    assert complete[:2] == [b"\r", b"\n"] and complete[2].startswith(choose.MARKER)
    assert choose.menu_of(complete) == YES_NO
    # Ansible compares one key at a time with the list: no key typed is ever the marked entry.
    assert all(bytes([byte]) not in complete[2:] for byte in range(256))


@pytest.mark.parametrize(
    "complete",
    [
        None,
        (b"\r", b"\n"),
        (b"c",),
        [b"\r", b"\n", choose.MARKER + b"not json"],
        [b"\r", b"\n", choose.MARKER + b'{"options": []}'],
        [b"\r", b"\n", choose.MARKER + b'{"options": [{"value": 1, "text": "x"}]}'],
    ],
)
def test_a_prompt_without_a_menu_whole_has_none(complete):
    assert choose.menu_of(complete) is None


def test_the_prompt_unpickles_where_the_project_s_code_cannot_be_imported():
    # As the worker sends it to the main process: a class of the project's there would kill Ansible's results thread.
    sent = pickle.dumps(PromptSend(worker_id=0, prompt="q", private=False, seconds=None, interrupt_input=None,
                                   complete_input=choose.marked(YES_NO)))  # fmt: skip
    code = (
        "import pickle, sys\n"
        "assert not any(path.endswith('/lib') for path in sys.path)\n"
        "sent = pickle.loads(sys.stdin.buffer.read())\n"
        "assert 'claude_vm' not in sys.modules\n"
        "print(sent.complete_input[2].startswith(b'\\x00agent-warehouse:choices\\x00'))\n"
    )
    ran = subprocess.run(
        [sys.executable, "-I", "-c", code], input=sent, capture_output=True, cwd="/", timeout=60, check=False
    )
    assert ran.returncode == 0, ran.stderr.decode()
    assert ran.stdout.decode().strip() == "True"


def test_what_is_said_above_the_menu_and_the_line_it_leaves():
    msg = "[init_ask | Ask for it]\nUSER INPUT REQUIRED\n  Tailscale ...\nUse Tailscale on the VMs? y or n:"
    assert choose.question(msg, YES_NO) == "[init_ask | Ask for it]\nUSER INPUT REQUIRED\n  Tailscale ..."
    assert choose.collapsed(YES_NO, "n") == "Use Tailscale on the VMs? no"
    assert choose.collapsed(STORAGES, "zfs") == "Storage: zfs"
    assert choose.collapsed(STORAGES, "") == "Storage: left for later"


# ------------------------------------------------------------------- the keys ---
@pytest.mark.parametrize(
    ("sequence", "key"),
    [
        (b"\x1b[A", "up"),
        (b"\x1bOA", "up"),
        (b"\x1b[B", "down"),
        (b"\x1bOB", "down"),
        (b"\x1b[H", "home"),
        (b"\x1b[4~", "end"),
        (b"\x1b", "esc"),
        (b"\x1b[15~", ""),
        (b"n", "n"),
        (b"\r", "\r"),
    ],
)
def test_each_sequence_is_its_key(sequence, key):
    assert choose.key_of(sequence) == key


@pytest.mark.parametrize(
    ("chosen", "key", "then"),
    [
        (0, "down", ("move", 1)),
        (3, "down", ("move", 3)),
        (0, "up", ("move", 0)),
        (2, "home", ("move", 0)),
        (0, "end", ("move", 3)),
        (0, "3", ("move", 2)),
        (0, "9", ("stay", 0)),
        (0, "n", ("move", 1)),
        (1, "n", ("move", 2)),  # round again among those the letter starts
        (0, "q", ("stay", 0)),
        (2, "\r", ("choose", "nfs-slow")),
        (2, "esc", ("later", "")),
    ],
)
def test_each_key_moves_or_answers(chosen, key, then):
    assert choose.step(STORAGES, chosen, key) == then


# ------------------------------------------------------------------ the frame ---
@pytest.mark.parametrize(("cols", "rows"), [(100, 30), (40, 30), (24, 6)])
def test_every_line_fits_short_of_the_width_and_the_marked_answer_is_in_the_window(cols, rows):
    for chosen in range(len(STORAGES["options"])):
        lines = choose.frame(STORAGES, chosen, cols, rows)
        assert all(screen.width(line) <= cols - 1 for line in lines), lines
        shown = [screen.plain(line) for line in lines[:-1]]
        assert any(line.startswith("› " + STORAGES["options"][chosen]["text"]) for line in shown), shown
        assert len(lines) <= max(2, rows - 2)


def test_a_long_list_says_where_the_marked_answer_is():
    long = {**STORAGES, "options": [{"value": str(n), "text": f"item {n}", "note": ""} for n in range(1, 13)]}
    hint = screen.plain(choose.frame(long, 9, 80, 8)[-1])
    assert hint.endswith("10 of 12") and hint.startswith(choose.HINT)
    assert screen.plain(choose.frame(YES_NO, 0, 80, 30)[-1]) == choose.HINT


# ------------------------------------------------------- at a pseudo-terminal ---
ASK = """
import json, sys, termios
sys.path.insert(0, sys.argv[1])
from claude_vm.tui import choose
try:
    value = choose.ask(json.loads(sys.argv[2]), sys.stdin, sys.stdout)
except choose.Interrupted:
    value = "<interrupted>"
print("answer=" + json.dumps(value))
print("canonical=" + str(bool(termios.tcgetattr(0)[3] & termios.ICANON)))
"""


def _asked(menu, keys, cols=60, rows=12):
    screen_ = VT(cols, rows)
    typed = []

    def act(chunk, output, master, process):
        screen_.feed(chunk)
        # Once a frame is drawn and the terminal is the menu's: the hint may be cut at a narrow one.
        if not typed and choose.SYNC_OFF.encode() in output and _in_raw_mode(master):
            typed.append(True)
            for key in keys:
                os.write(master, key)
                time.sleep(0.15)

    rc, raw = _at_a_pseudo_terminal(
        [sys.executable, "-I", "-c", ASK, str(LIB), json.dumps(menu)],
        REPO,
        {"PATH": "/usr/bin:/bin", "TERM": "xterm-256color", "PYTHONUTF8": "1"},
        size=(cols, rows),
        act=act,
        timeout=60,
    )
    text = raw.decode(errors="replace")
    answer = json.loads(text.split("answer=", 1)[1].split("\n", 1)[0].strip())
    return rc, raw, answer, screen_, text


@pytest.mark.parametrize(
    ("keys", "answer"),
    [
        ([b"\x1b[B", b"\r"], "n"),
        ([b"\x1bOB", b"\x1bOA", b"\r"], "y"),
        ([b"n", b"\r"], "n"),
        ([b"2", b"\r"], "n"),
        ([b"\x1b"], ""),
        ([b"\x03"], "<interrupted>"),
    ],
)
def test_the_menu_answers_at_a_terminal_and_leaves_it_as_it_found_it(keys, answer):
    rc, raw, got, vt, text = _asked(YES_NO, keys)
    assert rc == 0, text
    assert got == answer, text
    assert "canonical=True" in text, text
    assert b"\x1b[?25l" not in raw, "the cursor is never hidden"
    assert vt.frame_wraps == 0
    # Erased once answered: no hint left on the screen or above it.
    assert not [line for line in vt.transcript() if choose.HINT in line], vt.transcript()


def test_a_long_note_at_a_narrow_terminal_is_cut_never_wrapped():
    rc, _, got, vt, text = _asked(STORAGES, [b"\x1b[F", b"\r"], cols=30, rows=6)
    assert rc == 0 and got == "zfs", text
    assert vt.frame_wraps == 0 and vt.unknown == set(), vt.unknown


# ------------------------------------------------------------------- the hook ---
class _Display:
    def __init__(self):
        self._lock = threading.RLock()
        self.said = []

    def display(self, msg, **_):
        assert self._lock._is_owned()  # noqa: SLF001 - said under the lock prompt_until reads under
        self.said.append(msg)


def _original(calls):
    def prompt_until(msg, private=False, seconds=None, interrupt_input=None, complete_input=None):
        calls.append((msg, private, seconds, interrupt_input, complete_input))
        return b"typed"

    return prompt_until


@pytest.mark.parametrize(
    ("kwargs", "drawable"),
    [
        ({"complete_input": None}, True),
        ({"complete_input": choose.marked(YES_NO), "private": True}, True),
        ({"complete_input": choose.marked(YES_NO), "seconds": 3}, True),
        ({"complete_input": choose.marked(YES_NO)}, False),
    ],
)
def test_the_hook_hands_anything_but_a_shown_menu_at_a_terminal_to_ansible(monkeypatch, kwargs, drawable):
    monkeypatch.setattr(choose, "can_choose", lambda *_: drawable)
    monkeypatch.setattr(choose, "ask", lambda *_, **__: pytest.fail("the menu was asked"))
    calls = []
    prompt = engine.choosing(_Display(), _original(calls))
    assert prompt("Use it? y or n:", **kwargs) == b"typed"
    assert len(calls) == 1 and calls[0][0] == "Use it? y or n:"


def test_the_hook_asks_a_menu_says_the_question_and_the_answer_and_turns_ctrl_c_into_pause_s(monkeypatch):
    shown = _Display()
    monkeypatch.setattr(choose, "can_choose", lambda *_: True)
    monkeypatch.setattr(choose, "ask", lambda menu, *_, **__: "n")
    prompt = engine.choosing(shown, _original([]))
    assert prompt("[Ask]\nText\nUse Tailscale on the VMs? y or n:", complete_input=choose.marked(YES_NO)) == b"n"
    assert shown.said == ["[Ask]\nText", "Use Tailscale on the VMs? no"]

    def interrupted(*_, **__):
        raise choose.Interrupted

    monkeypatch.setattr(choose, "ask", interrupted)
    with pytest.raises(AnsiblePromptInterrupt):
        prompt("Use Tailscale on the VMs? y or n:", complete_input=choose.marked(YES_NO))


# ------------------------------------------------------------------ statically ---
def test_a_hidden_question_never_carries_its_answers():
    tasks = yaml.safe_load((REPO / "playbooks" / "tasks" / "init_ask.yml").read_text())
    asking = [task for task in tasks if "ask" in task]
    assert [task["name"] for task in asking] == [
        "init_ask | Ask for {{ init_ask_label }}",
        "init_ask | Ask once more, saying the rule",
    ]
    for task in asking:
        assert task["ask"]["menu"].startswith("{{ {} if init_ask_hidden | default(false)"), task["name"]
        assert task["ask"]["echo"] == "{{ not (init_ask_hidden | default(false)) }}", task["name"]
    # And the action, whatever it is handed: no menu without echo.
    action = (REPO / "playbooks" / "action_plugins" / "ask.py").read_text()
    assert 'complete = choose.marked(menu) if echo and menu.get("options") else None' in action
