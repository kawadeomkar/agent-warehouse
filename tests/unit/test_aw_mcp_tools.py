"""The agents' server's tools over the program (lib/claude_vm/cli/mcp_backend.py; docs/architecture/engine.md §16.3).

test_mcp_tools.py's holds, carried against the one table of verbs and options (schema/words.py), where that test holds
today's server to the justfile's recipes and stays as it is until the cutover: every verb is a tool or has a reason in
the table; every option and word of a tool's verb has exactly one fate for it in the table's column agent, a property
of its schema, a value the server gives, or refused with the reason, and one added with no fate fails by name; every
verb whose run is a job has a tool that starts it detached; and no fate is written in the module, by a scan. Each word
a tool does not take is tried as an argument and refused before any child starts, with the table's reason; a value
with a space is one argument word, and a verb's word that starts with - is refused, while an option's value that does
is one word, --long=value. A schema is valid JSON Schema of exact types, each pattern the table's, anchored whole; its
descriptions are the table's sentences said; and a call's arguments are held by the one check before the printer.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from aw_kit import imports_of
from jsonschema import Draft202012Validator

from claude_vm.cli import mcp_backend
from claude_vm.cli.mcp_backend import Refused, Unfated, command, schema, tools
from claude_vm.schema import check, printer, words
from claude_vm.schema.patterns import PATTERNS
from claude_vm.schema.words import BEFORE, FATES, VERBS, Fate, Option, Words, fate

MODULE = "claude_vm.cli.mcp_backend"
SOURCE = Path(mcp_backend.__file__)
# The sixteen tools, by the verb each runs; None for the server's own reads of the state folder's jobs.
TOOLS = {
    "status": "status",
    "settings": "settings",
    "settings_set": "settings",
    "logs": "logs",
    "log": None,
    "hosts": "host",
    "deploy": "deploy",
    "login": "login",
    "code": "code",
    "jobs": None,
    "job": None,
    "cancel": "cancel",
    "destroy": "destroy",
    "init": "init",
    "install": "install",
    "release": "release",
}
# The verbs no tool runs.
NO_TOOL = {"help", "vault", "mcp"}
# What a call needs beyond nothing for the one check to take it, by tool: the verb's needed word, and the VMs destroy
# deletes, which its --confirm, given by the server, must name.
NEEDS = {"code": {"name": "alpha"}, "release": {"name": "alpha"}, "destroy": {"names": ["alpha"]}}
# The values the server works out for each call, by the field of each fate fixed with no value.
GIVES = {"attended": "1", "confirm": "alpha"}


def _table_tools() -> list[str]:
    return [tool for tool, verb in TOOLS.items() if verb is not None]


def _items(tool: str) -> dict[str, Option | Words]:
    return mcp_backend.items(VERBS[TOOLS[tool]])


def _fates(tool: str) -> dict[str, Fate]:
    """Every fate the table gives tool: its verb's words and options, --detach among them, and the words before any
    verb."""
    row = VERBS[TOOLS[tool]]
    own = {field: mcp_backend.fate_of(row, item, tool) for field, item in _items(tool).items()}
    return own | {option.field: fate(option.agent, tool) for option in BEFORE}


def _given(tool: str) -> dict[str, object]:
    return {field: GIVES[field] for field, one in _fates(tool).items() if one.kind == "fixed" and one.value is None}


def _run(tool: str, arguments: dict | None = None) -> list[str]:
    return command(tool, {**NEEDS.get(tool, {}), **(arguments or {})}, _given(tool))


def _refusal(tool: str, arguments: dict) -> str:
    with pytest.raises(Refused) as refused:
        command(tool, arguments, _given(tool))
    return str(refused.value)


def _sample(item: Option | Words) -> object:
    """A value of item's own type, which a tool that does not take it refuses before any check."""
    if isinstance(item, Words):
        return ["alpha"] if item.plural else "alpha"
    return {"flag": True, "counted": 1, "repeatable": ["vm_cores=4"]}.get(item.kind, "alpha")


def _unfated() -> list[str]:
    """The sentence of each tool whose schema meets an option with no fate for it."""
    found = []
    for tool in mcp_backend.runs():
        try:
            schema(tool)
        except Unfated as unfated:
            found.append(str(unfated))
    return found


def test_the_tools_are_the_tables_and_the_servers_own_reads() -> None:
    listed = tools()
    assert {tool.name: tool.verb for tool in listed} == TOOLS
    assert len(listed) == len(TOOLS) == 16
    assert mcp_backend.runs() == {tool: verb for tool, verb in TOOLS.items() if verb is not None}
    for tool in listed:
        assert tool.schema == schema(tool.name)
        assert tool.description.strip() and "{" not in tool.description
        if tool.verb is not None:
            assert tool.description == printer.said(VERBS[tool.verb].doc)


def test_every_verb_is_a_tool_or_has_a_reason_in_the_table_and_not_both() -> None:
    ran = set(mcp_backend.runs().values())
    for name, verb in VERBS.items():
        if isinstance(verb.tools, tuple):
            assert verb.tools and name in ran, name
        else:
            assert isinstance(verb.tools, str) and verb.tools.strip() and name not in ran, name
    assert {name for name, verb in VERBS.items() if isinstance(verb.tools, str)} == NO_TOOL
    listed = [tool for verb in VERBS.values() if isinstance(verb.tools, tuple) for tool in verb.tools]
    assert listed == list(mcp_backend.runs())


@pytest.mark.parametrize("tool", _table_tools())
def test_every_option_and_word_has_exactly_one_fate_for_the_tool(tool: str) -> None:
    row = VERBS[TOOLS[tool]]
    properties = schema(tool)["properties"]
    argv = _run(tool)
    for field, one in _fates(tool).items():
        item = _items(tool).get(field) or next(option for option in BEFORE if option.field == field)
        if isinstance(item.agent, dict):
            assert sorted(item.agent) == sorted(row.tools), (tool, field)
        assert one.kind in FATES, (tool, field)
        if one.kind == "property":
            assert field in properties, (tool, field)
            continue
        assert field not in properties, (tool, field)
        refusal = _refusal(tool, {**NEEDS.get(tool, {}), field: _sample(item)})
        assert refusal.startswith(f"error: the {tool} tool takes no {field}: "), refusal
        if one.why:
            assert printer.said(one.why) in refusal, (tool, field)
        if one.kind == "fixed":
            assert "the server gives it" in refusal, (tool, field)
            # Given to every call, by the table's value or the server's for this call.
            assert check.spelled(item) in argv, (tool, field, argv)
    # Each property is one of the verb's, or the server's wait.
    assert set(properties) - set(_fates(tool)) <= {mcp_backend.WAIT}


def test_an_option_added_to_the_table_with_no_fate_fails_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _unfated() == []
    deploy, settings = VERBS["deploy"], VERBS["settings"]
    fateless = Option("brand-new", None, "flag", None, "brand_new", "A word with no fate", {})
    one_tool_only = Option(
        "half-new", None, "flag", None, "half_new", "A word for one tool", {"settings": words.PROPERTY}
    )
    unknown = Option("odd-new", None, "flag", None, "odd_new", "A fate of no kind", Fate("perhaps"))
    monkeypatch.setitem(VERBS, "deploy", replace(deploy, options=(*deploy.options, fateless, unknown)))
    monkeypatch.setitem(VERBS, "settings", replace(settings, options=(*settings.options, one_tool_only)))
    assert _unfated() == [
        "deploy's --brand-new has no fate for the tool deploy in schema/words.py's column agent",
        "settings's --half-new has no fate for the tool settings_set in schema/words.py's column agent",
    ]
    monkeypatch.setitem(VERBS, "deploy", replace(deploy, options=(*deploy.options, unknown)))
    assert _unfated() == [
        "deploy's --odd-new has no fate for the tool deploy in schema/words.py's column agent",
        "settings's --half-new has no fate for the tool settings_set in schema/words.py's column agent",
    ]


@pytest.mark.parametrize("tool", _table_tools())
def test_a_tool_whose_verb_s_run_is_a_job_starts_it_detached(tool: str) -> None:
    row = VERBS[TOOLS[tool]]
    argv = _run(tool, {mcp_backend.WAIT: 5} if row.changes else {})
    assert argv[0] == "aw"
    # Nothing travels before the verb but the words the server gives: no setting, no ANSIBLE_ARGS.
    verb_at = argv.index(row.name)
    assert argv[1:verb_at] == ["--attended", "1"], argv
    assert ("--detach" in argv) is row.changes, argv
    assert (mcp_backend.WAIT in schema(tool)["properties"]) is row.changes
    assert str(5) not in argv
    if not row.changes:
        assert _refusal(tool, {mcp_backend.WAIT: 5}) == f"error: the {tool} tool has no argument {mcp_backend.WAIT}"


def test_every_verb_whose_run_is_a_job_has_a_tool() -> None:
    for name, verb in VERBS.items():
        if verb.changes:
            assert isinstance(verb.tools, tuple) and verb.tools, name


def test_each_word_a_tool_does_not_take_is_refused_before_any_child_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    def started(*args, **kwargs):
        raise AssertionError("a child was started")

    for module, name in ((subprocess, "Popen"), (os, "fork"), (os, "posix_spawn"), (os, "execv"), (os, "execve")):
        monkeypatch.setattr(module, name, started)
    tried = 0
    for tool in _table_tools():
        for field, one in _fates(tool).items():
            if one.kind == "property":
                continue
            item = _items(tool).get(field) or next(option for option in BEFORE if option.field == field)
            _refusal(tool, {**NEEDS.get(tool, {}), field: _sample(item)})
            tried += 1
    # --set and -v on each verb that takes them, the four of hosts, init's and install's, --adopt and the rest.
    assert tried >= 40, tried
    assert _refusal("deploy", {"set": ["vm_cores=4"]}).endswith(printer.said(words.SET_REFUSED))
    assert _refusal("logs", {"job": "1", "follow": True}).endswith(printer.said(words.FOLLOW_REFUSED))
    assert _refusal("deploy", {"bogus": 1}) == "error: the deploy tool has no argument bogus"
    assert _refusal("deploy", {"Bad Name!": 1}) == "error: the deploy tool has no argument of that name"
    for own in ("log", "jobs", "job", "vault", "nothing"):
        with pytest.raises(Refused):
            command(own, {})


def test_a_value_with_a_space_is_one_word() -> None:
    argv = _run("settings_set", {"name": "editor", "value": "two words"})
    assert argv[argv.index("--value") + 1] == "two words"
    assert "two" not in argv and "words" not in argv
    argv = _run("init", {"github_user": "octocat", "address": "pve.lan"})
    assert argv[argv.index("--address") + 1] == "pve.lan"


@pytest.mark.parametrize(
    ("tool", "arguments", "field"),
    [
        ("deploy", {"names": ["alpha", "-x"]}, "names"),
        ("status", {"names": ["--only=vms"]}, "names"),
        ("code", {"name": "--project=x"}, "name"),
        ("login", {"names": ["-v"]}, "names"),
        ("cancel", {"job": "-1"}, "job"),
        ("logs", {"job": "--save"}, "job"),
        ("settings", {"name": "-all"}, "name"),
        ("destroy", {"names": ["-alpha"]}, "names"),
    ],
)
def test_a_verb_s_word_that_starts_with_a_dash_is_refused(tool: str, arguments: dict, field: str) -> None:
    sentence = f"error: the {tool} tool takes no {field} that starts with -, which aw would read as an option"
    with pytest.raises(Refused, match=re.escape(sentence)):
        command(tool, arguments, _given(tool))


def test_an_option_s_value_that_starts_with_a_dash_is_one_word() -> None:
    argv = _run("settings_set", {"name": "editor", "value": "-1"})
    assert "--value=-1" in argv and "-1" not in argv


def test_each_schema_is_valid_of_exact_types_and_its_patterns_the_table_s_anchored() -> None:
    for tool in TOOLS:
        made = schema(tool)
        Draft202012Validator.check_schema(made)
        assert made["type"] == "object" and made["additionalProperties"] is False
        for field, prop in made["properties"].items():
            item = _items(tool).get(field) if TOOLS[tool] else None
            where = prop["items"] if prop["type"] == "array" else prop
            assert prop["type"] in ("string", "boolean", "array", "integer"), (tool, field)
            if item is not None and item.pattern:
                assert where["pattern"] == f"^(?:{PATTERNS[item.pattern].text})$", (tool, field)
            if "pattern" in where:
                re.compile(where["pattern"])
    deploy = Draft202012Validator(schema("deploy"))
    assert deploy.is_valid({"names": ["alpha"], "cores": "8", "check": True, "repos": "one,two"})
    for bad in ({"cores": 8}, {"check": "true"}, {"names": "alpha"}, {"cores": "08"}, {"repos": "a b"}, {"set": []}):
        assert not deploy.is_valid(bad), bad
    code = Draft202012Validator(schema("code"))
    assert code.is_valid({"name": "alpha"}) and not code.is_valid({}) and not code.is_valid({"name": ["alpha"]})
    assert not code.is_valid({"name": "alpha beta"})
    # The schema is advice to the client, whose pattern's $ is ECMA-262's, the end alone, where Python's re (and so this
    # validator) takes a newline before it: the call's gate is the one check, which holds the value whole, as full does.
    assert check.form_of(VERBS["code"].words) in _refusal("code", {"name": "alpha\n"})


@pytest.mark.parametrize("tool", _table_tools())
def test_each_description_is_the_table_s_sentence_said(tool: str) -> None:
    for field, prop in schema(tool)["properties"].items():
        if field == mcp_backend.WAIT:
            continue
        item = _items(tool)[field]
        said = printer.said(item.help)
        assert prop["description"] == (f"{said} {check.form_of(item)}" if item.pattern else said), (tool, field)
        assert "{" not in prop["description"] and "just " not in prop["description"], (tool, field)


def test_a_call_is_held_by_the_one_check_before_the_printer() -> None:
    # A value outside its pattern, of another type, or a rule between options broken: the check's sentence, never the
    # printer's words that the program is at fault.
    for tool, arguments in (
        ("deploy", {"names": ["alpha"], "cores": "0"}),
        ("deploy", {"names": ["alpha"], "cores": 8}),
        ("deploy", {"names": ["alpha"], "check": "yes"}),
        ("deploy", {"names": ["alpha"], "tags": "a,,b"}),
        ("code", {"name": ["alpha", "beta"]}),
        ("settings_set", {"name": "editor", "value": "zed", "reset": True}),
        ("status", {"names": ["alpha"], "only": "nothing"}),
    ):
        refusal = _refusal(tool, arguments)
        assert refusal.startswith("error: ") and printer.OWN_FAULT not in refusal, (tool, refusal)
        assert refusal == check.check(TOOLS[tool], {**arguments, **_fixed(tool)}), (tool, refusal)
    # One the check takes and the printer will not write: a character a terminal does not show as it is.
    refusal = _refusal("settings_set", {"name": "editor", "value": "a‮b"})
    assert "a terminal does not show" in refusal and printer.OWN_FAULT not in refusal


def _fixed(tool: str) -> dict[str, object]:
    """The values the server gives tool's verb itself, the words before any verb aside."""
    row = VERBS[TOOLS[tool]]
    out = {}
    for field, item in _items(tool).items():
        one = mcp_backend.fate_of(row, item, tool)
        if one.kind == "fixed":
            out[field] = GIVES[field] if one.value is None else one.value
    return out


def test_a_given_value_the_table_does_not_ask_for_is_the_server_s_fault() -> None:
    with pytest.raises(TypeError):
        command("destroy", {"names": ["alpha"]}, {"attended": "1"})
    with pytest.raises(TypeError):
        command("status", {}, {"attended": "1", "confirm": "alpha"})
    argv = command("destroy", {"names": ["alpha"]}, {"attended": "", "confirm": "alpha"})
    assert argv[:2] == ["aw", "destroy"] and argv[argv.index("--confirm") + 1] == "alpha"


# The names the module may read from the table and never writes: each a fate, a reason or what makes one.
_FATE_MAKERS = {"fixed", "refused", "Fate"}


def _forbidden_strings() -> set[str]:
    """What a fate of the module's own would have to name: a verb, a tool of the table's, a kind of fate, a reason the
    table gives, or a word any tool does not take."""
    out = set(VERBS) | set(mcp_backend.runs()) | set(FATES)
    out |= {verb.tools for verb in VERBS.values() if isinstance(verb.tools, str)}
    for verb in VERBS.values():
        received = [verb.words] if verb.words is not None else []
        for item in [*received, *check.options_of(verb), *BEFORE]:
            agents = item.agent.values() if isinstance(item.agent, dict) else [item.agent]
            for one in agents:
                if one.why:
                    out.add(one.why)
                if one.kind != "property":
                    name = item.name if isinstance(item, Words) else item.long or item.short
                    out |= {item.field, str(name)}
    return out


def _fate_names() -> set[str]:
    """The table's names whose value is a fate, a fate by tool, or a reason."""
    reasons = _forbidden_strings()
    out = set()
    for name, value in vars(words).items():
        if isinstance(value, Fate) or (isinstance(value, dict) and any(isinstance(v, Fate) for v in value.values())):
            out.add(name)
        elif isinstance(value, str) and value in reasons:
            out.add(name)
    return out


def _written_fates(source: str) -> list[str]:
    """Each place in source where a fate is written rather than read from the table."""
    found: list[tuple[int, str]] = []
    forbidden, names = _forbidden_strings(), _fate_names()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) in _FATE_MAKERS:
            found.append((node.lineno, "makes a fate"))
        elif isinstance(node, ast.ImportFrom) and node.module == "claude_vm.schema.words":
            found += [(node.lineno, f"imports {alias.name}") for alias in node.names if alias.name in names]
        elif isinstance(node, ast.Attribute) and node.attr in names:
            found.append((node.lineno, f"reads {node.attr}"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in forbidden:
            found.append((node.lineno, f"names {node.value!r}"))
    return [f"{line}: {what}" for line, what in sorted(found)]


def test_no_fate_is_written_in_the_module() -> None:
    assert _written_fates(SOURCE.read_text()) == []
    # The scan sees each road a fate of the module's own would take.
    planted = (
        "from claude_vm.schema.words import JSON, refused\n"
        "LEFT = {'deploy': ('adopt',)}\n"
        "GIVEN = {'json': True}\n"
        "WHY = refused('a reason')\n"
        "from claude_vm.schema import words\n"
        "KIND = words.SET_REFUSED\n"
        "ALSO = 'refused'\n"
    )
    assert [line.split(":")[0] for line in _written_fates(planted)] == ["1", "2", "2", "3", "4", "6", "7"]


def test_the_module_loads_nothing_heavy_at_import(home) -> None:
    added = imports_of(module=MODULE, home=home) - imports_of(module="claude_vm", home=home)
    assert MODULE in added, sorted(added)
    tops = {name.split(".")[0] for name in added}
    assert sorted(tops - set(sys.stdlib_module_names) - {"claude_vm"}) == []
    assert sorted(name for name in added if name.startswith(("claude_vm.mcp", "claude_vm.engine"))) == []
    assert {"asyncio", "subprocess"} & added == set()
