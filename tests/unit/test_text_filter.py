"""plain_text: what a VM printed, safe to quote in a message on this machine. visible_text: that, with no
Unicode format character to reorder a line. one_line, uncut: how a message quotes it. full_stop, retry_line and
and_list: the words messages are built with. banner: the block that sets a moment the run waits for the person
apart."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_vm import text as _module

REPO = Path(__file__).resolve().parents[2]
plain_text = _module.plain_text
visible_text = _module.visible_text
one_line = _module.one_line
full_stop = _module.full_stop
retry_line = _module.retry_line
and_list = _module.and_list
banner = _module.banner
plain_summary = _module.plain_summary


@pytest.mark.parametrize(
    ("raw", "plain"),
    [
        ("Login failed: invalid code", "Login failed: invalid code"),
        ("Opening browser to sign in…\nLogin successful.\n", "Opening browser to sign in…\nLogin successful.\n"),
        ("a\tb", "a\tb"),
        # A clipboard write, a title and a colour: gone whole, payload included.
        ("\x1b]52;c;b3duZWQ=\x07\x1b]0;retitled\x07\x1b[31mError\x1b[0m", "Error"),
        ("\x1b]0;title\x1b\\text", "text"),
        ("\x1b]8;;https://example.com\x1b\\link\x1b]8;;\x1b\\", "link"),
        ("\x1bPq#0;2;0;0;0\x1b\\after", "after"),
        ("\x1b[2J\x1b[1;1Htop", "top"),
        ("\x1b7saved\x1b8\x1bc", "saved"),
        # The 8-bit forms.
        ("\x9b31mred\x9b0m", "red"),
        ("\x9d0;title\x9cbody", "body"),
        # An unterminated string sequence ends at its line, not the text.
        ("\x1b]0;no end\nLogin failed", "\nLogin failed"),
        # Any other control character.
        ("bell\x07 nul\x00 del\x7f back\x08", "bell nul del back"),
        (None, ""),
        (42, "42"),
    ],
)
def test_plain_text(raw, plain):
    assert plain_text(raw) == plain


def test_nothing_a_terminal_acts_on_is_left():
    every = "".join(chr(c) for c in range(0x00, 0xA0)) + "x"
    left = plain_text(every)
    assert all(ch in "\t\n" or 0x20 <= ord(ch) < 0x7F for ch in left), repr(left)


def test_visible_text_keeps_lines_and_indents_and_leaves_no_format_character():
    # A bidi override that would read "login.exe" backwards, zero-width characters, a line separator.
    raw = "  account \u202eexe.nigol\u202c ok\n\tnext\u200b line\u2028end\x1b[31m"
    assert visible_text(raw) == "  account  exe.nigol  ok\n\tnext  line end"
    every = "".join(chr(c) for c in range(0x2000, 0x2070)) + "\ufeff\u061c\u00ad"
    assert all(_module.unicodedata.category(ch) not in ("Cf", "Zl", "Zp") for ch in visible_text(every))
    # one_line is the same text on one line.
    assert one_line(raw) == "account exe.nigol ok next line end"


def test_one_line_without_a_limit_cleans_as_it_does_with_one_and_never_cuts():
    # How a playbook or a role quotes what a VM or a tool printed in a message of its own.
    said = "fatal: \x1b[31mrepository\x1b[0m\n\tnot   found " + "x" * 500
    assert one_line(said, None) == "fatal: repository not found " + "x" * 500
    assert one_line(said) == one_line(said, None)[:119] + "…"
    assert one_line(None, None) == ""


@pytest.mark.parametrize(
    ("reason", "sentence"),
    [
        # A module's message often has no stop of its own; one that has gets no second.
        ("One or more items failed", "One or more items failed."),
        ("Connection refused.", "Connection refused."),
        ("Is it running?", "Is it running?"),
        ("Login failed!", "Login failed!"),
        ("exit code 1", "exit code 1."),
        ("", "."),
        (None, "."),
    ],
)
def test_full_stop_ends_the_sentence_once(reason, sentence):
    assert full_stop(reason) == sentence


def test_a_retry_line_ends_the_reason_s_sentence_and_the_line_with_the_command():
    # No stop after the command, which would be pasted along with it.
    assert retry_line("One or more items failed", "just deploy alpha --tags github_projects") == (
        "One or more items failed. Retry it with: just deploy alpha --tags github_projects"
    )
    assert retry_line("Connection refused.", "just login alpha --only claude") == (
        "Connection refused. Retry it with: just login alpha --only claude"
    )


@pytest.mark.parametrize(
    ("items", "said"),
    [
        ([], ""),
        (None, ""),
        (["alpha"], "alpha"),
        (["alpha", "beta"], "alpha and beta"),
        (["Claude", "GitHub", "Tailscale"], "Claude, GitHub and Tailscale"),
        (("a", "b", "c", "d"), "a, b, c and d"),
        ([1, 2], "1 and 2"),
    ],
)
def test_and_list_says_a_list_as_words_do(items, said):
    assert and_list(items) == said


def test_a_banner_is_a_titled_block_that_ends_in_a_newline():
    block = banner(
        "Paste the code the sign-in page shows.", "USER INPUT REQUIRED", host="alpha", topic="Claude Code sign-in"
    )
    assert block == (
        "#" * 80 + "\n"
        "##  USER INPUT REQUIRED  -  alpha  -  Claude Code sign-in\n" + "#" * 80 + "\n"
        "Paste the code the sign-in page shows.\n"
    )


def test_a_banner_without_a_host_or_topic_names_the_title_alone():
    assert banner("x", "NEXT STEPS", width=30).splitlines()[1] == "##  NEXT STEPS"


@pytest.mark.parametrize("missing", [None, "", "  ", "\x1b[0m", "\x07 \x1b]0;t\x07"])
def test_a_host_or_topic_that_is_missing_or_cleans_to_nothing_is_left_out(missing):
    # No "  -  " with nothing after it.
    assert banner("x", "T", host=missing, topic="sign-in", width=30).splitlines()[1] == "##  T  -  sign-in"
    assert banner("x", "T", host="alpha", topic=missing, width=30).splitlines()[1] == "##  T  -  alpha"


def test_a_banner_never_wraps_its_body():
    # A URL or a command stays whole to copy; the terminal soft-wraps it.
    url = "https://claude.com/cai/oauth/authorize?" + "a" * 200
    block = banner(f"sign in at {url}", "ACTION REQUIRED IN YOUR BROWSER", width=40)
    assert f"sign in at {url}" in block.splitlines()


def test_a_list_is_a_line_per_item_and_nothing_is_an_empty_body():
    assert banner(["one", "two"], "NEXT STEPS", width=20).splitlines()[3:] == ["one", "two"]
    assert banner("", "X", width=20) == "#" * 20 + "\n##  X\n" + "#" * 20 + "\n"
    assert banner(None, "X", width=20) == banner("", "X", width=20)


@pytest.mark.parametrize(("width", "rule"), [(40, 40), (120, 120), (5, 20)])
def test_a_banner_is_as_wide_as_asked_but_never_narrower_than_twenty(width, rule):
    assert banner("x", "T", width=width).splitlines()[0] == "#" * rule


@pytest.mark.parametrize(("columns", "rule"), [("200", 80), ("60", 60)])
def test_a_banner_is_eighty_columns_or_the_terminal_when_it_is_narrower(monkeypatch, columns, rule):
    monkeypatch.setenv("COLUMNS", columns)
    assert banner("x", "T").splitlines()[0] == "#" * rule


def test_a_banner_lets_no_control_character_through():
    block = banner("\x1b]52;c;b3duZWQ=\x07body\x1b[31m", "TITLE\x07", host="al\x1b[2Jpha", width=20)
    assert block == "#" * 20 + "\n##  TITLE  -  alpha\n" + "#" * 20 + "\nbody\n"


def test_a_banners_frame_is_ascii():
    lines = banner("…é", "USER INPUT REQUIRED", host="alpha", topic="GitHub sign-in").splitlines()
    assert all(line.isascii() for line in lines[:3])


def test_a_modules_warnings_and_failure_are_made_plain_and_read_in_their_own_order():
    # What Display prints as [WARNING] and [ERROR] lines: a VM's root chooses it.
    from ansible._internal._task import UnifiedTaskResult

    said = UnifiedTaskResult.from_module_result_dict(
        {"warnings": ["warned \x1b]0;retitled\x07\u202etxt.exe"], "failed": True, "msg": "failed \x9b31m\u2066x"}
    )
    (warning,) = [plain_summary(each) for each in said.warnings]
    assert warning.event.msg == "warned  txt.exe", warning
    assert plain_summary(said.exception).event.msg == "failed  x"
    assert plain_summary(None) is None
