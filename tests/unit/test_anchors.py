"""Every pattern that holds a value to a shape ends at the end of the text, never at a newline before it.

`$` matches at the end of the text and also just before a newline that ends it, so '^[a-z]+$' takes
"web\n": Ansible's match and search tests are re.match and re.search, and Python's match and search do
the same. A pattern here ends with \\Z instead (or is used with fullmatch), as tailscale_node_id_pattern
always has: a VM supplies some of these values - a folder for --session, Tailscale's sign-in page, what
gh printed - and the rest come from settings, the command line and an agent's MCP call. A pattern read
over the lines of a text (re.M, multiline=True, (?m)) is the exception, and so is lineinfile's regexp,
which is matched against each line with its newline.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from harness import repo_files

REPO = Path(__file__).resolve().parents[2]

# A match or search test, or the test's name handed to select, reject, selectattr or rejectattr: the pattern is
# the argument that follows.
TEST = re.compile(r"\bis\s+(?:not\s+)?(?:match|search|regex)\s*\(|['\"](?:match|search|regex)['\"]\s*,")
# A variable whose value is an anchored pattern, read by name in a test elsewhere: claude_ssh_word, *_pattern.
ASSIGNED = re.compile(r"""^\s*(?P<name>[a-z_][a-z0-9_]*):\s*(?P<q>['"])\^.*(?<!\\)\$(?P=q)\s*$""", re.M)
# A variable named for the pattern it holds, read with match, search or regex wherever it is used: its value, quoted.
NAMED = re.compile(r"""^\s*(?P<name>[a-z_][a-z0-9_]*_pattern):\s*(?P<q>['"])(?P<value>.*)(?P=q)\s*$""", re.M)
# lineinfile's regexp, matched against each line of a file with its newline: $ is what it needs.
LINE_BY_LINE = {"regexp", "init_hosts_line"}


def _pattern(text: str, start: int) -> tuple[str, str]:
    """The argument that starts at ``start``: its text, and its last string literal outside any parentheses of its
    own, which is where a pattern made of pieces ends."""
    depth, i, quote, literal, last = 0, start, None, [], ""
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and i + 1 < len(text):
                literal.append(text[i : i + 2])
                i += 2
                continue
            if ch == quote:
                quote = None
                if depth == 0:
                    last = "".join(literal)
            else:
                literal.append(ch)
        elif ch in "'\"":
            quote, literal = ch, []
        elif ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            break
        i += 1
    return text[start:i], last


def _ends_at_a_newline(pattern: str) -> bool:
    """Whether ``pattern`` ends with a $ that is no literal dollar: last, or last inside the groups that close it
    ("([;,]|$)", "(?:x|$)?"), where it ends the match just as well."""
    end = re.sub(r"(?:\)[?*+]?)+\Z", "", pattern)
    return end.endswith("$") and not end.endswith("\\$") and "(?m" not in pattern


def _yaml_findings(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    found = []
    for test in TEST.finditer(text):
        argument, last = _pattern(text, test.end())
        rest = text[test.end() : test.end() + len(argument) + 40]
        if _ends_at_a_newline(last) and "multiline=True" not in rest:
            found.append(f"{text.count(chr(10), 0, test.start()) + 1}: {argument.strip()[:100]}")
    for value in ASSIGNED.finditer(text):
        if value.group("name") not in LINE_BY_LINE:
            found.append(f"{text.count(chr(10), 0, value.start()) + 1}: {value.group(0).strip()[:100]}")
    for value in NAMED.finditer(text):
        if _ends_at_a_newline(value.group("value")) and not ASSIGNED.match(value.group(0)):
            found.append(f"{text.count(chr(10), 0, value.start()) + 1}: {value.group(0).strip()[:100]}")
    return found


def _multiline(call: ast.Call) -> bool:
    flags = call.args[1:2] + [keyword.value for keyword in call.keywords if keyword.arg == "flags"]
    return any(
        isinstance(node, ast.Attribute) and node.attr in ("M", "MULTILINE") for flag in flags for node in ast.walk(flag)
    )


def _python_findings(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []  # a shell script of tools/
    found = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
            and node.func.attr in ("compile", "match", "search")
            and node.args
        ):
            continue
        last = node.args[0]
        while isinstance(last, ast.BinOp):
            last = last.right
        if isinstance(last, ast.JoinedStr) and last.values:
            last = last.values[-1]
        if isinstance(last, ast.Constant) and isinstance(last.value, str):
            if _ends_at_a_newline(last.value) and not _multiline(node):
                found.append(f"{node.lineno}: {last.value[-60:]}")
    return sorted(found, key=lambda line: int(line.split(":")[0]))


def _scanned() -> list[Path]:
    """Every tracked file of the project's own code, its tests and Molecule's checks aside."""
    files = []
    for path in repo_files("*.yml", "*.j2", "*.yml.example", "*.py", "tools/*", untracked=True):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith(("tests/", "docs/", ".github/", "molecule/")) or "/molecule/" in rel:
            continue
        files.append(path)
    return files


def test_every_pattern_that_holds_a_value_ends_at_the_end_of_the_text():
    found = []
    for path in _scanned():
        rel = path.relative_to(REPO).as_posix()
        if path.suffix in (".yml", ".j2") or rel.endswith(".yml.example"):
            found += [f"{rel}:{line}" for line in _yaml_findings(path)]
        elif path.suffix == ".py" or (rel.startswith("tools/") and not path.suffix):
            found += [f"{rel}:{line}" for line in _python_findings(path)]
    assert found == [], "end these patterns with \\Z, not $:\n" + "\n".join(found)


def test_the_scan_sees_each_way_a_pattern_is_written(tmp_path):
    yml = tmp_path / "a.yml"
    yml.write_text(
        "a: \"{{ x is match('^[a-z]+$') }}\"\n"
        "b: \"{{ x | select('match', '^' ~ y ~ '-[0-9]+$') }}\"\n"
        "c: \"{{ x is search('(?m)^a$') }}\"\n"
        "d: \"{{ x is match((y | regex_replace('/+$', '')) ~ '/[a-z]+\\Z') }}\"\n"
        "e_pattern: '^https://example[.]com/$'\n"
        "regexp: '^# (BEGIN|END) x$'\n"
        "f: \"{{ x is search('^a$', multiline=True) }}\"\n"
        "g: \"{{ x is not match('^cost\\$') }}\"\n"
    )
    assert [line.split(":")[0] for line in _yaml_findings(yml)] == ["1", "2", "5"]
    py = tmp_path / "a.py"
    py.write_text(
        "import re\n"
        'A = re.compile(r"^[a-z]+$")\n'
        'B = re.compile(r"^a$", re.M)\n'
        're.match("^" + re.escape(x) + "-[0-9]+$", y)\n'
        'C = re.compile(r"^a\\Z")\n'
        'D = re.compile(r"^(?:x)"\n    r"(?:y)$", re.I)\n'
    )
    assert [line.split(":")[0] for line in _python_findings(py)] == ["2", "4", "6"]


def _literal(path: str, before: str) -> str:
    """The pattern written as the first string literal after ``before`` in ``path``, as Jinja reads it."""
    text = (REPO / path).read_text(encoding="utf-8")
    at = text.index(before) + len(before)
    quote = text[at:].lstrip()[0]
    start = text.index(quote, at) + 1
    return text[start : text.index(quote, start)]


@pytest.mark.parametrize(
    ("what", "pattern", "good"),
    [
        # The one a VM supplies: the folder its registry names for --session's session.
        ("--session's folder", _literal("playbooks/tasks/session_read.yml", "session.name | string is match("), "x"),
        (
            "Tailscale's sign-in page",
            _literal("roles/tailscale/vars/main.yml", "tailscale_web_page is match("),
            "https://login.tailscale.com/a/0123abcd",
        ),
        ("an alias's word", _literal("playbooks/tasks/ssh_config.yml", "claude_ssh_word:"), "alpha"),
        # A device's name, as the role holds it to the hostname it joined as.
        (
            "a device's name",
            "^"
            + re.escape("web")
            + _literal(
                "roles/tailscale/vars/main.yml",
                "tailscale_api_device_name is match('^' ~ (tailscale_hostname | lower | regex_escape) ~",
            ),
            "web-2",
        ),
    ],
)
def test_each_value_held_to_a_shape_refuses_a_trailing_newline(what, pattern, good):
    # Ansible's match test is re.match: the newline is what $ lets through.
    assert re.match(pattern, good), what
    assert not re.match(pattern, good + "\n"), what
    assert not re.match(pattern, good + "\nProxyCommand x"), what
