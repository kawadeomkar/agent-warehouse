"""tools/comments, the comment rule's check, over checkouts each test writes (docs/development/comments.md).

A comment of the new engine's is a pointer to a section of the design, to a test or to an upstream issue, or a tool's
directive with its code; anything else is prose, and the prose and the docstrings past their cap are held to
tests/unit/comments.json's count of each file, which only goes down. A workaround's comment is refused in today's
product too, which takes nothing else. Each finding says the kind's own sentence, never the comment's text. The tests
run the tool as just lint and the commit hook do, from a checkout's top, and read what it prints.
"""

from __future__ import annotations

import json
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest
from harness import REPO
from test_aw_layout import NEW, _is_new
from test_docs import _sections

TOOL = REPO / "tools" / "comments"
ENGINE = "lib/claude_vm/engine/x.py"
TODAYS = "lib/claude_vm/routes.py"
PAGE = "# 16. The engine\n\n## 16.6 `ssh/`\n\n```\n## 99.1 a heading in a code block\n```\n"
TESTS = "def test_y():\n    pass\n\n\nclass TestZ:\n    def test_w(self):\n        pass\n"
PROSE = (
    "CMT003 prose in a comment of the new engine's. Fix: a rule goes into the test that holds it, its why in the"
    " test's docstring; a design reason into its § section, a `# §` pointer left here; a step the code shows,"
    " deleted."
)
WORKAROUND = "CMT001 a workaround's comment. Fix: fix the cause, or file a card and hold the case with a test."
SECTION = "a § pointer not of its form, or to a section no page of docs/architecture has"
TEST = "a test pointer not of its form, or to a test no file of tests/ holds"
UPSTREAM = "an upstream pointer not of the form owner/repo#N"
POINTED = (
    "Fix: point it at a § section of docs/architecture, a test of tests/ (tests/unit/test_x.py::test_y) or an upstream"
    " issue (owner/repo#N), or delete it."
)


def _tool():
    """tools/comments as a module, for what its tables are."""
    spec = spec_from_loader("comments_tool", SourceFileLoader("comments_tool", str(TOOL)))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checkout(root: Path, files: dict[str, str], counts: dict[str, int] | None = None) -> Path:
    """A checkout's top with one page of the design, one file of tests, the files given and, given, the counts."""
    for path, text in {"docs/architecture/engine.md": PAGE, "tests/unit/test_aw_x.py": TESTS, **files}.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    if counts is not None:
        (root / "tests/unit/comments.json").write_text(json.dumps(counts))
    return root


def _comments(root: Path, *paths: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(TOOL), *paths], cwd=root, capture_output=True, text=True, check=False
    )


def _found(root: Path, *paths: str) -> list[str]:
    result = _comments(root, *paths)
    assert result.returncode == (1 if result.stdout else 0), (result.returncode, result.stdout, result.stderr)
    assert result.stderr == ""
    return result.stdout.splitlines()


def test_each_kind_is_found_at_its_line_and_column(tmp_path: Path) -> None:
    text = (
        "import os\n"
        "# Why this is so.\n"
        "x = 1  # a hack\n"
        "# §16.99\n"
        "\n"
        "\n"
        "def f():\n"
        '    """One.\n\n    Two.\n    Three.\n    Four.\n    """\n'
    )
    root = _checkout(tmp_path, {ENGINE: text})
    assert _found(root, ENGINE) == [
        f"{ENGINE}: CMT003 and CMT004 count 2, tests/unit/comments.json allows 0. Fix: {PROSE.split('Fix: ', 1)[1]}",
        f"{ENGINE}:2:1: {PROSE}",
        f"{ENGINE}:3:8: {WORKAROUND}",
        f"{ENGINE}:4:1: CMT002 {SECTION}. {POINTED}",
        f"{ENGINE}:8:5: CMT004 a docstring of the new engine's past 5 lines. Fix: keep what it does, takes, returns"
        f" and raises, in 5 lines at most; for the rest, {PROSE.split('Fix: ', 1)[1]}",
    ]


def test_a_docstring_of_five_lines_is_within_the_cap(tmp_path: Path) -> None:
    text = 'def f():\n    """One.\n\n    Two.\n    Three.\n    """\n'
    root = _checkout(tmp_path, {ENGINE: text})
    assert _found(root, ENGINE) == []


@pytest.mark.parametrize(
    "text",
    [
        'x = "# a hack, in a string"\n',
        'x = f"{1} # until, in an f-string"\n',
        "x = f'''\n# a race, in an f-string's lines\n'''\n",
        'def f():\n    """Its contract.\n\n    # a retry, in a docstring\'s text\n    """\n',
        "x = b'# sleep, in bytes'\n",
    ],
    ids=["string", "f-string", "f-string-lines", "docstring", "bytes"],
)
@pytest.mark.parametrize("path", [ENGINE, TODAYS])
def test_a_hash_in_a_string_is_no_comment(tmp_path: Path, text: str, path: str) -> None:
    root = _checkout(tmp_path, {path: text})
    assert _found(root, path) == []


@pytest.mark.parametrize(
    "comment",
    [
        "# §16.6",
        "# §16",
        "# test: tests/unit/test_aw_x.py::test_y",
        "# test: tests/unit/test_aw_x.py::TestZ",
        "# test: tests/unit/test_aw_x.py::TestZ::test_w",
        "# upstream: astral-sh/ruff#1234",
        "# noqa: E501",
        "# noqa: E501, F401",
        "# ruff: noqa: E501",
        "# type: ignore[attr-defined]",
        "# type: ignore[attr-defined, arg-type]",
        "# type: ignore[attr-defined]  # noqa: E501",
        "# pragma: no cover",
        "# fmt: off",
        "# fmt: on",
        "# fmt: skip",
    ],
)
def test_each_allowed_kind_is_let_through(tmp_path: Path, comment: str) -> None:
    root = _checkout(tmp_path, {ENGINE: f"import os\nx = 1  {comment}\n{comment}\n"})
    assert _found(root, ENGINE) == []


@pytest.mark.parametrize(
    "head", ["#!/usr/bin/env python3\n", "# -*- coding: utf-8 -*-\n", "#!/usr/bin/env python3\n# coding: utf-8\n"]
)
def test_a_shebang_and_a_coding_line_are_let_through_at_the_top(tmp_path: Path, head: str) -> None:
    root = _checkout(tmp_path, {ENGINE: f"{head}x = 1\n"})
    assert _found(root, ENGINE) == []


def test_a_shebang_or_a_coding_line_lower_down_is_prose(tmp_path: Path) -> None:
    root = _checkout(tmp_path, {ENGINE: "x = 1\ny = 2\n# coding: utf-8\n#!/bin/sh\n"}, {ENGINE: 2})
    assert _found(root, ENGINE) == []
    (root / "tests/unit/comments.json").write_text(json.dumps({ENGINE: 1}))
    assert [line.split(": ", 1)[1][:6] for line in _found(root, ENGINE)] == ["CMT003", "CMT003", "CMT003"]


@pytest.mark.parametrize(
    ("comment", "what"),
    [
        ("# §16.7", SECTION),
        ("# §99.1", SECTION),
        ("# § 16.6", SECTION),
        ("# §16.6 and §16.7", SECTION),
        ("# §16.6 says why", SECTION),
        ("# test: tests/unit/test_aw_x.py::test_y holds it", TEST),
        ("# test: tests/unit/test_aw_x.py::test_none", TEST),
        ("# test: tests/unit/test_aw_none.py::test_y", TEST),
        ("# test: tests/unit/test_aw_x.py", TEST),
        ("# test: tests/unit/test_aw_x.py::TestZ::test_y", TEST),
        ("# test: tests/unit/test_aw_x.py::test_y[a]", TEST),
        ("# test: tests/../tests/unit/test_aw_x.py::test_y", TEST),
        ("# test: lib/claude_vm/engine/x.py::f", TEST),
        ("# upstream: ruff#1234", UPSTREAM),
        ("# upstream: astral-sh/ruff", UPSTREAM),
        ("# upstream: https://github.com/astral-sh/ruff/issues/1", UPSTREAM),
        ("# upstream: astral-sh/ruff#0", UPSTREAM),
    ],
)
def test_a_pointer_to_nothing_is_refused(tmp_path: Path, comment: str, what: str) -> None:
    root = _checkout(tmp_path, {ENGINE: f"x = 1  {comment}\n"})
    assert _found(root, ENGINE) == [f"{ENGINE}:1:8: CMT002 {what}. {POINTED}"]


@pytest.mark.parametrize(
    "comment", ["# see §16.6", "# noqa: E501 - a long line", "# noqa", "# type: ignore", "# ruff: noqa"]
)
def test_a_pointer_or_a_directive_with_words_is_prose(tmp_path: Path, comment: str) -> None:
    root = _checkout(tmp_path, {ENGINE: f"x = 1  {comment}\n"}, {ENGINE: 1})
    assert _found(root, ENGINE) == []
    (root / "tests/unit/comments.json").write_text("{}")
    assert _found(root, ENGINE)[1] == f"{ENGINE}:1:8: {PROSE}"


@pytest.mark.parametrize(
    "word", ["workaround", "hack", "for now", "temporary", "until", "flaky", "race", "retry", "sleep", "UNTIL"]
)
def test_each_word_of_a_workaround_is_found(tmp_path: Path, word: str) -> None:
    root = _checkout(tmp_path, {TODAYS: f"x = 1  # kept so {word} it is fixed\n"})
    assert _found(root, TODAYS) == [f"{TODAYS}:1:8: {WORKAROUND}"]


@pytest.mark.parametrize("word", ["hacks", "races", "retrying", "sleeper", "trace", "untiled", "temporarily"])
def test_a_word_that_only_holds_a_workaround_s_is_not_one(tmp_path: Path, word: str) -> None:
    root = _checkout(tmp_path, {TODAYS: f"x = 1  # the {word} of it\n"})
    assert _found(root, TODAYS) == []


def test_the_findings_are_sorted_by_path_and_line_and_the_same_twice(tmp_path: Path) -> None:
    second = "lib/claude_vm/cli/y.py"
    root = _checkout(
        tmp_path,
        {ENGINE: "x = 1\n# b\n# a\n", second: "# c\nx = 1  # a hack\n"},
        {ENGINE: 1, second: 0},
    )
    found = _found(root, ENGINE, second)
    assert [line.split(": ", 1)[0] for line in found] == [
        f"{second}",
        f"{second}:1:1",
        f"{second}:2:8",
        f"{ENGINE}",
        f"{ENGINE}:2:1",
        f"{ENGINE}:3:1",
    ]
    assert _found(root, second, ENGINE) == found


def test_no_finding_quotes_the_comment(tmp_path: Path) -> None:
    root = _checkout(
        tmp_path,
        {ENGINE: '# zanzibar\nx = 1  # a zanzibar hack\n# §16.77 zanzibar\ndef f():\n    """zanzibar\n\n\n\n\n"""\n'},
    )
    found = _found(root, ENGINE)
    assert len(found) == 5 and not any("zanzibar" in line for line in found)


def test_a_file_above_its_count_fails_naming_both_numbers_and_each_finding(tmp_path: Path) -> None:
    root = _checkout(tmp_path, {ENGINE: "# a\n# b\n"}, {ENGINE: 1})
    assert _found(root, ENGINE) == [
        f"{ENGINE}: CMT003 and CMT004 count 2, tests/unit/comments.json allows 1. Fix: {PROSE.split('Fix: ', 1)[1]}",
        f"{ENGINE}:1:1: {PROSE}",
        f"{ENGINE}:2:1: {PROSE}",
    ]


def test_a_file_at_its_count_says_nothing(tmp_path: Path) -> None:
    root = _checkout(tmp_path, {ENGINE: "# a\n# b\n"}, {ENGINE: 2})
    assert _found(root, ENGINE) == []


def test_a_file_below_its_count_fails_until_the_count_is_lowered(tmp_path: Path) -> None:
    root = _checkout(tmp_path, {ENGINE: "# a\n"}, {ENGINE: 3})
    assert _found(root, ENGINE) == [
        f"{ENGINE}: CMT003 and CMT004 count 1, tests/unit/comments.json allows 3. Fix: lower its count in"
        " tests/unit/comments.json to 1, in this commit."
    ]


def test_a_new_file_is_held_to_0(tmp_path: Path) -> None:
    root = _checkout(tmp_path, {ENGINE: "# a\n"}, {"lib/claude_vm/cli/other.py": 4})
    assert _found(root, ENGINE)[0] == (
        f"{ENGINE}: CMT003 and CMT004 count 1, tests/unit/comments.json allows 0. Fix: {PROSE.split('Fix: ', 1)[1]}"
    )
    (root / "tests/unit/comments.json").unlink()
    assert _found(root, ENGINE)[0].startswith(f"{ENGINE}: CMT003 and CMT004 count 1")


def test_a_workaround_in_the_new_engine_is_never_counted(tmp_path: Path) -> None:
    root = _checkout(tmp_path, {ENGINE: "# a hack\n"}, {ENGINE: 1})
    assert _found(root, ENGINE) == [
        f"{ENGINE}: CMT003 and CMT004 count 0, tests/unit/comments.json allows 1. Fix: lower its count in"
        " tests/unit/comments.json to 0, in this commit.",
        f"{ENGINE}:1:1: {WORKAROUND}",
    ]


def test_the_whole_checkout_is_what_git_lists_and_a_count_of_a_file_gone_fails(tmp_path: Path) -> None:
    root = _checkout(
        tmp_path,
        {ENGINE: "# a\n", "lib/claude_vm/cli/ignored.py": "# a hack\n", ".gitignore": "ignored.py\n"},
        {ENGINE: 1, "lib/claude_vm/cli/gone.py": 2},
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    assert _found(root) == [
        "lib/claude_vm/cli/gone.py: CMT003 and CMT004 count 0, tests/unit/comments.json allows 2. Fix: lower its"
        " count in tests/unit/comments.json to 0, in this commit."
    ]
    assert _found(root, ENGINE) == []


@pytest.mark.parametrize(
    "path",
    [
        "inventory/group_vars/all/local.yml.example",
        "inventory/group_vars/all/vault.yml.example",
        "roles/common/defaults/main.yml",
        "roles/common/vars/main.yml",
        "roles/common/meta/argument_specs.yml",
    ],
)
def test_the_files_the_wiki_is_generated_from_are_exempt(tmp_path: Path, path: str) -> None:
    text = "#!/usr/bin/env python3\n# kept for now\n"
    root = _checkout(tmp_path, {path: text, "tools/other": text})
    assert _found(root, path) == []
    assert _found(root, "tools/other") == [f"tools/other:2:1: {WORKAROUND}"]


def test_today_s_product_is_held_to_cmt001_alone(tmp_path: Path) -> None:
    text = '"""One.\n\n\n\n\n"""\n# Why this is so.\nx = 1  # §16.99\ny = 2  # a workaround\n'
    files = {path: text for path in (TODAYS, "playbooks/library/m.py", "filter_plugins/f.py", "tools/x.py")}
    root = _checkout(tmp_path, files)
    for path in files:
        assert _found(root, path) == [f"{path}:9:8: {WORKAROUND}"], path


@pytest.mark.parametrize(
    "path", ["tests/unit/test_aw_y.py", "tests/molecule/fake.py", "molecule/system/x.py", "docs/x.py", "x.py"]
)
def test_a_file_outside_the_code_is_not_read(tmp_path: Path, path: str) -> None:
    root = _checkout(tmp_path, {path: "# a hack\n# prose\n"})
    assert _found(root, path) == []


def test_a_file_that_is_not_python_is_not_read(tmp_path: Path) -> None:
    root = _checkout(tmp_path, {"lib/claude_vm/engine/x.yml": "# a hack\n", "tools/x.sh": "#!/bin/sh\n# a hack\n"})
    assert _found(root, "lib/claude_vm/engine/x.yml", "tools/x.sh") == []


def test_a_file_that_does_not_parse_is_said(tmp_path: Path) -> None:
    root = _checkout(tmp_path, {ENGINE: "def f(:\n"})
    assert _found(root, ENGINE) == [f"{ENGINE}: not read as Python. Fix: what ruff says of it."]


def test_it_runs_from_a_checkout_s_top_and_reads_its_files_alone(tmp_path: Path) -> None:
    root = _checkout(tmp_path / "checkout", {ENGINE: "x = 1\n"})
    below = _comments(root / "lib", "claude_vm/engine/x.py")
    assert (below.returncode, below.stdout) == (2, "")
    assert below.stderr == "error: tools/comments runs from a checkout's top, which has docs/architecture\n"
    (tmp_path / "elsewhere.py").write_text("# a hack\n")
    outside = _comments(root, str(tmp_path / "elsewhere.py"))
    assert (outside.returncode, outside.stdout) == (2, "")
    assert outside.stderr == f"error: not in this checkout: {(tmp_path / 'elsewhere.py').resolve()}\n"


def test_the_new_engine_is_the_layout_s_and_tools_comments() -> None:
    tool = _tool()
    assert tool.NEW == NEW
    for path in REPO.glob("lib/claude_vm/**/*.py"):
        assert tool.is_new(str(path.relative_to(REPO))) == _is_new(path), path
    assert tool.is_new("tools/aw") and tool.is_new("tools/aw-mcp") and tool.is_new("tools/comments")
    assert not tool.is_new("tools/live")


def test_its_sections_are_the_ones_the_wiki_s_check_reads() -> None:
    assert _tool().sections(REPO) == set(_sections())


def test_the_counts_are_of_the_new_engine_s_files_each_above_0_in_order() -> None:
    counts = json.loads((REPO / "tests/unit/comments.json").read_text())
    tool = _tool()
    assert list(counts) == sorted(counts)
    assert all(tool.is_new(path) and isinstance(n, int) and n > 0 for path, n in counts.items())
