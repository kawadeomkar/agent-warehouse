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
import warnings
from pathlib import Path

import jinja2
import jinja2.lexer
import pytest
import yaml
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


# ------------------------------------------------------------------------ a Jinja literal's escapes ---
# Jinja decodes a string literal as Python's unicode-escape decodes bytes: '^[a-z]+\Z' keeps its \Z only with a warning
# that such sequences will not work in the future, and a known escape becomes its character ('\1' is U+0001). Ansible's
# own lexer escapes the backslashes of a literal inside {{ }} of a value it templates first, so there a literal reads as
# written: '^[a-z]+\Z', and '\1' a backreference. Jinja decodes every other: in a conditional (when, changed_when,
# failed_when, until, assert's that), which Ansible evaluates with that escaping off, in a {% %} block, and in a
# template file. There a backslash that starts no escape is written twice ('^[a-z]+\\Z'), as the YAML scalar hands it
# on: four times in a double-quoted one, which reads two as one first.
JINJA = jinja2.Environment(extensions=["jinja2.ext.do", "jinja2.ext.loopcontrols"])
# The keys whose value Ansible evaluates as a conditional, an expression with no {{ }} around it.
BARE = {"when", "changed_when", "failed_when", "until", "that"}
# A file of variables, whose keys are names and data: a "when" there is a variable's words, never a conditional.
DATA = ("defaults", "vars", "group_vars", "host_vars", "meta")


def _decoded(raw: str, escaped: bool = False, warned: list[str] | None = None) -> str:
    """A string literal as written in an expression, quotes and all, as Ansible decodes it: with ``escaped``, its
    backslashes escaped first, as Ansible's lexer does inside {{ }} of a value it templates. Each warning the decoding
    gives goes into ``warned``."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        (token,) = JINJA.lexer.wrap(iter([(1, "string", raw.replace("\\", "\\\\") if escaped else raw)]))
    if warned is not None:
        warned += [str(warning.message) for warning in caught]
    return token.value


def _literals(source: str, escaping: bool) -> list[tuple[int, str, bool]]:
    """Each string literal of ``source`` as Jinja's lexer finds it: where it starts in ``source``, its text, and whether
    Ansible escapes it first - with ``escaping``, the rule of a value Ansible templates, one inside {{ }}."""
    found, at, inside = [], 0, False
    for _lineno, token, raw in JINJA.lexer.tokeniter(source, None):
        at = source.index(raw, at)
        if token == "variable_begin":
            inside = True
        elif token == "variable_end":
            inside = False
        elif token == "string":
            found.append((at, raw, escaping and inside))
        at += len(raw)
    return found


def _scalars(node: yaml.Node, key: str = ""):
    """Each scalar of a YAML document, in the order written, with the key it is the value of (a list's items take
    their list's)."""
    if isinstance(node, yaml.MappingNode):
        for name, value in node.value:
            yield from _scalars(name, "")
            yield from _scalars(value, name.value if isinstance(name, yaml.ScalarNode) else "")
    elif isinstance(node, yaml.SequenceNode):
        for value in node.value:
            yield from _scalars(value, key)
    elif isinstance(node, yaml.ScalarNode):
        yield node, key


def _expression(node: yaml.ScalarNode, key: str, data: bool) -> tuple[str, bool] | None:
    """What Ansible makes of a scalar: the text Jinja lexes, and whether Ansible's escaping inside {{ }} applies to it
    - for a value with {{ or {%, which Ansible templates; not for a bare conditional of a task or play file, which it
    evaluates in {{ }} - or None for one it does neither with, and one marked !unsafe."""
    value = node.value
    if node.tag == "!unsafe":
        return None
    if "{{" in value or "{%" in value:
        return value, True
    if key in BARE and not data:
        return "{{ " + value + " }}", False
    return None


def _escape_findings(path: Path, root: Path = REPO) -> list[str]:
    """Each Jinja string literal of ``path`` - a YAML file of Ansible's or a template file, under ``root`` - that warns
    as Ansible decodes it: "<line>: <the literal as Jinja is handed it>", the line its scalar starts at."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".j2":
        # The template module and lookup escape nothing: Jinja decodes every literal of a template file.
        sources = [(1, text, False)]
    else:
        data = any(part in DATA for part in path.relative_to(root).parts[:-1])
        sources = [
            (node.start_mark.line + 1, *made)
            for document in yaml.compose_all(text, Loader=yaml.SafeLoader)
            if document is not None
            for node, key in _scalars(document)
            if (made := _expression(node, key, data)) is not None
        ]
    found = []
    for line, source, escaping in sources:
        for _at, raw, escaped in _literals(source, escaping):
            warned: list[str] = []
            _decoded(raw, escaped, warned)
            if warned:
                found.append(f"{line}: {raw[:100]}")
    return found


# Where the files Ansible reads are: the roles (their Molecule scenarios too), the playbook scenarios and the files
# they share, the playbooks, the inventory.
READ_BY_ANSIBLE = ("roles/", "molecule/", "tests/molecule/", "playbooks/", "inventory/")


def _read_by_ansible() -> list[Path]:
    """Every file of the project's own that Ansible reads as YAML or as a template, a link but once, where it
    points."""
    return [
        path
        for path in repo_files("*.yml", "*.yaml", "*.j2", untracked=True)
        if path.relative_to(REPO).as_posix().startswith(READ_BY_ANSIBLE) and not path.is_symlink()
    ]


def test_no_jinja_literal_holds_an_escape_python_does_not_know():
    found = []
    for path in _read_by_ansible():
        found += [f"{path.relative_to(REPO).as_posix()}:{line}" for line in _escape_findings(path)]
    assert found == [], (
        "in a conditional, a {% %} block or a template file, write each backslash that starts no escape twice (four"
        " times in a double-quoted YAML scalar):\n" + "\n".join(found)
    )


def test_the_escape_scan_reads_a_literal_as_ansible_does(tmp_path):
    tasks = tmp_path / "tasks.yml"
    tasks.write_text(
        r"""- a: >-
    {{ x is match('^[a-z]+\Z') }}
- b: >-
    {% if x is match('^[a-z]+\Z') %}y{% endif %}
- c: "{{ x is match('^[a-z]+\\Z') }}"
- when: x is match('^[0-9]+\.[0-9]+')
- when: "x is match('^[0-9]+\\.[0-9]+')"
- when: "x is match('^[0-9]+\\\\.[0-9]+')"
- assert:
    that:
      - x is search('\s')
      - x is search('\\s')
- e_pattern: '^[a-z]+\Z'
- when: "{{ x is match('\\Z') }}"
- f: !unsafe "{% if '\\Z' %}{% endif %}"
"""
    )
    # Inside {{ }} a literal is escaped first, a pattern of its own is no literal, and what Ansible never templates is
    # nothing; a {% %} block's literal and a conditional's are decoded, a double-quoted scalar's after YAML read it.
    assert [line.split(":")[0] for line in _escape_findings(tasks, tmp_path)] == ["3", "6", "7", "11"]
    rows = tmp_path / "vars" / "main.yml"
    rows.parent.mkdir()
    rows.write_text(
        'rows:\n  - when: "`just code` runs, \\\\Z"\n'  # a double-quoted \\Z, which YAML reads as \Z
        "  - what: >-\n      {% if x is match('\\Z') %}{% endif %}\n"
    )
    # In a file of variables a "when" is words: only what holds {{ or {% is an expression there.
    assert [line.split(":")[0] for line in _escape_findings(rows, tmp_path)] == ["3"]
    template = tmp_path / "unit.j2"
    template.write_text("Exec={{ x | regex_replace('\\s+', ' ') }} \\s {{ y }}\n")
    assert _escape_findings(template, tmp_path) == ["1: '\\s+'"]


def test_a_literal_is_decoded_as_ansible_s_own_templar_decodes_it():
    # The rule the scan and _literal hold every file to, held to Ansible itself: an ansible-core that reads a literal
    # otherwise fails here, never in a run.
    from ansible.parsing.dataloader import DataLoader
    from ansible.template import Templar, trust_as_template

    templar = Templar(loader=DataLoader(), variables={"one": "a\\Z"})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # Inside {{ }}: written once, read as written; written twice, two backslashes.
        assert templar.template(trust_as_template("{{ 'a\\Z' }}")) == "a\\Z" == _decoded("'a\\Z'", escaped=True)
        assert templar.template(trust_as_template("{{ 'a\\\\Z' }}")) == "a\\\\Z" == _decoded("'a\\\\Z'", escaped=True)
        # A conditional's and a {% %} block's: written twice, read as one.
        assert templar.evaluate_conditional(trust_as_template("one == 'a\\\\Z'")) is True
        assert templar.template(trust_as_template("{% if one == 'a\\\\Z' %}yes{% endif %}")) == "yes"
        assert _decoded("'a\\\\Z'") == "a\\Z"
    assert [str(warning.message) for warning in caught if "escape" in str(warning.message)] == []
    # Written once there, it reads the same, with the warning the scan finds.
    warned: list[str] = []
    assert _decoded("'a\\Z'", warned=warned) == "a\\Z" and warned


def _literal(path: str, before: str) -> str:
    """The pattern written as the first string literal after ``before`` in ``path``, as Ansible reads it: YAML reads
    the scalar first; then a literal inside {{ }} of a value Ansible templates is read as written, and a conditional's
    decoded as Jinja's lexer decodes it; a scalar that is the pattern itself, outside any expression (a variable whose
    value is the pattern), is what YAML gives."""
    text = (REPO / path).read_text(encoding="utf-8")
    at = text.index(before) + len(before)
    for document in yaml.compose_all(text, Loader=yaml.SafeLoader):
        for node, key in _scalars(document):
            start, end = node.start_mark.index, node.end_mark.index
            if start < at < end:
                made = _expression(node, key, data=False)
                assert made, f"{before!r} in {path} is in no expression"
                source, escaping = made
                after = source.index(before) + len(before)
                _at, raw, escaped = next(one for one in _literals(source, escaping) if one[0] >= after)
                return _decoded(raw, escaped)
            if start >= at:
                return node.value
    raise AssertionError(f"no scalar after {before!r} in {path}")


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
