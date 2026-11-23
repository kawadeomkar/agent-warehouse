"""A play's variables file (lib/claude_vm/guest/variables.py; docs/architecture/engine.md §16.10).

Every string of the file is written as {"__ansible_unsafe": s}, so that ansible-core, which loads an extra-variables
file as a trusted source, never reads one as a template. What holds it is Ansible itself, since the decoder that reads
the wrapper is ansible._internal's and no documentation promises it: Ansible's own loader, run in a Python of its own,
reads no string of a written file as trusted, and the same document unwrapped as trusted (the control); and through a
real ansible-playbook started by layer1.run on a stand-in machine, a wrapped `{{ 7 * 7 }}` reaches a task as that text,
an unwrapped one is templated (the control), with the untrusted-template switch at `error` the wrapped one used as a
template fails its task, and every scalar type comes back as it went. These are the tests that must pass before the
ansible-core pin moves (docs/architecture/toolchain.md).

What the writer refuses, before anything is written: a variable that names the play's connection (any `ansible_`
name: an extra variable outranks the inventory, where the SSH runner's options are), a key Ansible's decoder reads as
its own, anything that would make Ansible's JSON decoding fail and its loader read the text as YAML - where a wrapped
template is trusted, as a test shows - a type no variable holds, and a document that holds a secret the run opened,
anywhere, never said back. The file is 0600, whole, never written through a link; layer1.run holds the file it is
given to the same rules before its child starts, a connection's variable above all. The invariant with a test of its
own: a walk of every file written here finds no bare string leaf.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import stat
import struct
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from aw_machine import Machine, layer1_host, machine, target
from harness import REPO

from claude_vm.fleet import paths
from claude_vm.guest import layer1, variables
from claude_vm.runs import children, rundir
from claude_vm.runs import job as jobs
from claude_vm.secret import MARK, Secret
from claude_vm.ssh import hostkeys

WRAPPER = "__ansible_unsafe"
# Ansible's own variable for the untrusted-template switch, set here for each run, whatever the product's child is
# given: these tests hold the wrapping under each value.
SWITCH = "_ANSIBLE_TEMPLAR_UNTRUSTED_TEMPLATE_BEHAVIOR"
TEMPLATE = "{{ 7 * 7 }}"
RENDERED = "49"
# Every kind of value a role's variable holds, and the strings Ansible would read as templates among them.
EVERY_KIND = {
    "aw_text": TEMPLATE,
    "aw_statement": "{% if true %}rendered{% endif %}",
    "aw_comment": "{# hidden #}kept",
    "aw_lookup": "{{ lookup('pipe', 'id') }}",
    "aw_empty": "",
    "aw_quoted": 'a "quoted" line\nand a second, \\ and a tab\t',
    "aw_unicode": "naïve café, 日本, \U0001f600",
    "aw_integers": [0, 1, -1, 2**63 - 1, -(2**63)],
    "aw_floats": [1.5, -0.25, 1e300, 0.1],
    "aw_true": True,
    "aw_false": False,
    "aw_none": None,
    "aw_nested": {"list": [TEMPLATE, {"deeper": [True, None, 7, "x"]}], TEMPLATE: "a key that looks like one", "": []},
    "aw_mapping": {},
}


# ---------------------------------------------------------------- helpers ----


def _bare(value: object, where: str = "$") -> list[str]:
    """Where a walk of a written file's document finds a string leaf that is not wrapped: this test's own walk."""
    if isinstance(value, dict):
        if WRAPPER in value:
            return [] if set(value) == {WRAPPER} and isinstance(value[WRAPPER], str) else [where]
        return [found for key, item in value.items() for found in _bare(item, f"{where}.{key}")]
    if isinstance(value, list):
        return [found for at, item in enumerate(value) for found in _bare(item, f"{where}[{at}]")]
    return [where] if isinstance(value, str) else []


def _unwrapped(value: object) -> object:
    """A written file's document as the play's variables mean it: each wrapped string its text."""
    if isinstance(value, dict):
        if set(value) == {WRAPPER}:
            return value[WRAPPER]
        return {key: _unwrapped(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unwrapped(item) for item in value]
    return value


def _same(a: object, b: object) -> bool:
    """Equal with their types: True is not 1, nor 1.0 1."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _written(folder: Path) -> list[str]:
    return sorted(path.name for path in folder.iterdir())


@pytest.fixture
def made(home: Path) -> Iterator[jobs.Job]:
    """A job of this process's own with its run's directory, as the run that writes the variables file has."""
    job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True
    )
    rundir.make(job)
    yield job
    rundir.remove(job)
    jobs.drop(job)


def _vars(job: jobs.Job) -> Path:
    return rundir.path_for(job, "vars.json")


# ---------------------------------------------------------------- the wrapping ----


def test_wrap_puts_every_string_leaf_as_unsafe_and_keeps_every_other_value() -> None:
    assert variables.wrap("x") == {WRAPPER: "x"}
    assert variables.wrap({"a": ["b", 1, 2.5, True, None, ("c",)], "d": {}}) == {
        "a": [{WRAPPER: "b"}, 1, 2.5, True, None, [{WRAPPER: "c"}]],
        "d": {},
    }
    # Keys are names, never leaves: Ansible's decoder reads no key as a template's text, and the file keeps them.
    assert variables.wrap({TEMPLATE: TEMPLATE}) == {TEMPLATE: {WRAPPER: TEMPLATE}}
    assert _unwrapped(variables.wrap(EVERY_KIND)) == EVERY_KIND


def test_a_walk_of_every_file_written_finds_no_bare_string_leaf(made) -> None:
    documents = [
        EVERY_KIND,
        {"aw_one": "x"},
        {"aw_deep": [[["x", {"y": ["z"]}]]]},
        {"aw_keys": {"k": {"k": {"k": "v"}}}},
        {},
    ]
    for document in documents:
        written = json.loads(variables.write(_vars(made), document, secrets=()).read_text())
        assert _bare(written) == [], document
        assert _same(_unwrapped(written), document)
    # The walk finds one: a file written without the wrapping.
    plain = json.loads(json.dumps({"aw_deep": [[["x", {"y": [{WRAPPER: "z"}, "w"]}]]]}))
    assert _bare(plain) == ["$.aw_deep[0][0][0]", "$.aw_deep[0][0][1].y[1]"]


# ---------------------------------------------------------------- what write refuses ----


CONNECTION = [
    "ansible_ssh_common_args",
    "ansible_host_key_checking",
    "ansible_ssh_executable",
    "ansible_ssh_args",
    "ansible_user",
    "ansible_host",
    "ansible_become_exe",
    "ansible_python_interpreter",
]


@pytest.mark.parametrize("name", CONNECTION)
def test_write_refuses_a_variable_that_names_the_play_s_connection(made, name) -> None:
    folder = _vars(made).parent
    with pytest.raises(variables.Refused) as refused:
        variables.write(_vars(made), {"aw_name_ok": "x", name: "-o StrictHostKeyChecking=no"}, secrets=())
    assert str(refused.value).startswith(f"{name} names the play's connection"), refused.value
    assert _written(folder) == []


@pytest.mark.parametrize("name", ["Ansible", "1st", "a-b", "a b", "", "_private", "__ansible_unsafe", "a.b", "é"])
def test_write_refuses_a_name_that_is_no_role_variable_s(made, name) -> None:
    with pytest.raises(variables.Refused, match="named as a role variable is"):
        variables.write(_vars(made), {name: "x"}, secrets=())
    assert _written(_vars(made).parent) == []


def _nested(lists: int, leaf: object) -> object:
    """leaf inside lists lists, the outermost a variable's value: a variable's value is at depth 1, and a wrapped string
    one level below the list that holds it."""
    for _ in range(lists):
        leaf = [leaf]
    return leaf


# What would make Ansible's JSON decoding fail, so that its loader read the text as YAML; and the keys its decoder reads
# as its own.
DECODING = [
    ("a lone surrogate", {"aw_x": "a\udc80b"}, "no text UTF-8 holds"),
    ("a lone surrogate in a key", {"aw_x": {"\ud800": 1}}, "no text UTF-8 holds"),
    ("an integer past 64 bits", {"aw_x": 2**63}, "one of 64 bits"),
    ("a negative integer past 64 bits", {"aw_x": -(2**63) - 1}, "one of 64 bits"),
    ("an integer of 5000 digits", {"aw_x": [10**4999]}, "one of 64 bits"),
    ("not a number", {"aw_x": math.nan}, "a finite one"),
    ("infinity", {"aw_x": [-math.inf]}, "a finite one"),
    ("a nesting too deep", {"aw_x": _nested(variables.DEPTH_MOST, 1)}, "no deeper than 32"),
    ("a string nested too deep", {"aw_x": _nested(variables.DEPTH_MOST, "x")}, "no deeper than 32"),
    ("the wrapper's key", {"aw_x": {WRAPPER: "x", "beside": "y"}}, "begins __ansible"),
    ("a vault's key", {"aw_x": {"__ansible_vault": "x"}}, "begins __ansible"),
    ("a serialized type's key", {"aw_x": [{"__ansible_type": "x"}]}, "begins __ansible"),
    ("a key of the decoder's own", {"aw_x": {"__ansible_future": 1}}, "begins __ansible"),
]


@pytest.mark.parametrize(("case", "document", "said"), DECODING, ids=[case for case, _, _ in DECODING])
def test_write_refuses_what_ansible_s_json_decoding_would_not_read(made, case, document, said) -> None:
    with pytest.raises(variables.Refused, match=said):
        variables.write(_vars(made), document, secrets=())
    assert _written(_vars(made).parent) == []


def test_write_takes_the_edges_it_refuses_past(made) -> None:
    deepest = variables.DEPTH_MOST - 1
    edges = {"aw_x": [2**63 - 1, -(2**63)], "aw_y": _nested(deepest, "x"), "aw_z": _nested(deepest, 1)}
    written = json.loads(variables.write(_vars(made), edges, secrets=()).read_text())
    assert _same(_unwrapped(written), edges)


class _Own(str):
    pass


@pytest.mark.parametrize(
    ("case", "value", "said"),
    [
        ("a set", {"x"}, "hold no set"),
        ("bytes", b"x", "hold no bytes"),
        ("a path", Path("/x"), "hold no PosixPath"),
        ("an object", object(), "hold no object"),
        ("a str of its own type", _Own("x"), "hold no _Own"),
        ("a Secret", Secret("a-secret-value"), "a Secret is never in a play's variables"),
        ("a key that is no string", {1: "x"}, "keys that are strings alone"),
    ],
)
def test_write_refuses_a_type_no_variable_holds(made, case, value, said) -> None:
    with pytest.raises(variables.Refused, match=said) as refused:
        variables.write(_vars(made), {"aw_x": [value]}, secrets=())
    assert "a-secret-value" not in str(refused.value)
    assert _written(_vars(made).parent) == []


def test_write_refuses_a_value_that_holds_itself(made) -> None:
    looped: list[object] = ["x"]
    looped.append(looped)
    with pytest.raises(variables.Refused, match="holds itself"):
        variables.write(_vars(made), {"aw_x": looped}, secrets=())
    # The same value twice, side by side, holds nothing of itself.
    shared = ["x"]
    assert variables.write(_vars(made), {"aw_x": [shared, shared]}, secrets=()).is_file()


def test_write_refuses_what_is_not_a_document_or_a_path(made) -> None:
    for document in (["aw_x"], "aw_x", None):
        with pytest.raises(variables.Refused, match="a mapping of names to values"):
            variables.write(_vars(made), document, secrets=())
    with pytest.raises(variables.Refused, match="an absolute one"):
        variables.write("vars.json", {}, secrets=())
    with pytest.raises(variables.Refused, match="is not a folder"):
        variables.write(_vars(made).parent / "absent" / "vars.json", {}, secrets=())
    assert _written(_vars(made).parent) == []


SECRET = "s3cr3t-token-value"
NUMBER = "424242424242"


@pytest.mark.parametrize(
    ("case", "document", "names"),
    [
        ("a value equal to it", {"aw_token": SECRET}, "aw_token"),
        ("a value that holds it", {"aw_line": f"--token={SECRET} --x"}, "aw_line"),
        ("in a list", {"aw_list": ["a", ["b", SECRET]]}, "aw_list"),
        ("in a mapping's key", {"aw_map": {SECRET: "x"}}, "aw_map"),
        ("a variable's name", {f"aw_{NUMBER}": "x"}, None),
        ("a number", {"aw_pin": int(NUMBER)}, None),
        ("a number in a list", {"aw_pins": [1, int(NUMBER)]}, None),
    ],
)
def test_write_refuses_a_document_that_holds_a_secret_the_run_opened(made, case, document, names) -> None:
    opened = [Secret("unused"), Secret(SECRET), Secret(NUMBER)]
    with pytest.raises(variables.Refused, match="a secret the run opened") as refused:
        variables.write(_vars(made), document, secrets=opened)
    said = str(refused.value)
    assert SECRET not in said and NUMBER not in said
    assert said.startswith(names) if names else "aw_" not in said, said
    assert _written(_vars(made).parent) == []


def test_the_secrets_are_secrets_and_an_empty_one_strikes_nothing(made) -> None:
    for given in ([SECRET], SECRET, Secret(SECRET), None):
        with pytest.raises(TypeError, match="Secret"):
            variables.write(_vars(made), {"aw_x": "x"}, secrets=given)
    written = variables.write(_vars(made), {"aw_x": "x", "aw_y": ""}, secrets=[Secret("")])
    assert json.loads(written.read_text()) == {"aw_x": {WRAPPER: "x"}, "aw_y": {WRAPPER: ""}}
    # A secret's mark is no secret: a document may hold the text the mark is.
    assert variables.write(_vars(made), {"aw_x": MARK}, secrets=[Secret(SECRET)]).is_file()


# ---------------------------------------------------------------- how it is written ----


def _under(mask: int, *words, **named) -> Path:
    """variables.write under the umask mask: the file's mode is set, never left to it."""
    previous = os.umask(mask)
    try:
        return variables.write(*words, **named)
    finally:
        os.umask(previous)


def test_the_file_is_written_whole_at_0600_and_never_through_a_link(made, tmp_path) -> None:
    where = _vars(made)
    outside = tmp_path / "outside.json"
    outside.write_text("theirs")
    outside.chmod(0o644)
    where.symlink_to(outside)
    written = _under(0, where, {"aw_x": "x"}, secrets=())
    assert written == where
    # The link replaced by a file of its own, the file it named untouched.
    assert not where.is_symlink() and stat.S_IMODE(where.lstat().st_mode) == 0o600
    assert (outside.read_text(), stat.S_IMODE(outside.stat().st_mode)) == ("theirs", 0o644)
    # Written whole, in JSON of plain ASCII, ending with a line's end, and nothing left beside it.
    content = where.read_bytes()
    assert content.isascii() and content.endswith(b"\n")
    assert json.loads(content) == {"aw_x": {WRAPPER: "x"}}
    assert _written(where.parent) == ["vars.json"]
    # A second write replaces it, at 0600 under a umask that would leave less.
    _under(0o277, where, {"aw_y": 1}, secrets=())
    assert json.loads(where.read_text()) == {"aw_y": 1}
    assert stat.S_IMODE(where.lstat().st_mode) == 0o600
    assert _written(where.parent) == ["vars.json"]


# ---------------------------------------------------------------- held ----


def _hand_made(folder: Path, text: str | bytes) -> Path:
    path = folder / "hand-made.json"
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text)
    return path


@pytest.mark.parametrize(
    ("case", "text", "said"),
    [
        ("YAML", "aw_x: !unsafe '{{ 7 * 7 }}'\n", "is not the JSON"),
        ("a list", "[]", "holds a mapping of names to values"),
        ("a bare string", '{"aw_x": "{{ 7 * 7 }}"}', "is wrapped, so that Ansible never reads it as a template"),
        ("a bare string, nested", '{"aw_x": [1, {"y": "z"}]}', "is wrapped"),
        ("a connection's variable", '{"ansible_ssh_common_args": {"__ansible_unsafe": "-o X=y"}}', "names the play's"),
        ("a wrapper with more", '{"aw_x": {"__ansible_unsafe": "x", "y": 1}}', "the string alone"),
        ("a wrapper of no string", '{"aw_x": {"__ansible_unsafe": 1}}', "the string alone"),
        ("a key twice", '{"aw_x": 1, "aw_x": 2}', "names a key twice"),
        ("not a number", '{"aw_x": NaN}', "no JSON number"),
        ("a lone surrogate", '{"aw_x": {"__ansible_unsafe": "\\udc80"}}', "no text UTF-8 holds"),
        ("no UTF-8", b'{"aw_x": {"__ansible_unsafe": "\xff"}}', "cannot be read"),
        ("nested too deep", '{"aw_x": ' + "[" * 40 + "1" + "]" * 40 + "}", "no deeper than 32"),
        ("nested past Python's limit", '{"aw_x": ' + "[" * 100000 + "]" * 100000 + "}", "is not the JSON"),
    ],
)
def test_held_refuses_a_file_write_would_not_have_made(tmp_path, case, text, said) -> None:
    with pytest.raises(variables.Refused, match=said):
        variables.held(_hand_made(tmp_path, text))


def test_held_takes_what_write_writes(made, tmp_path) -> None:
    written = variables.write(_vars(made), EVERY_KIND, secrets=())
    assert variables.held(written) == written
    with pytest.raises(variables.Refused, match="cannot be read"):
        variables.held(tmp_path / "absent.json")


# ---------------------------------------------------------------- Ansible's own loader ----

# Ansible's loader, run in a Python of its own as ansible-playbook runs it for `-e @<file>`: the file loaded as a
# trusted source, uncached (the loader caches a file without its trust), and again by its JSON path alone. It prints,
# for each, every string leaf with whether Ansible marks it trusted as a template, and the document.
LOADER = r"""
import json, sys
from ansible.parsing.dataloader import DataLoader
from ansible._internal._datatag._tags import TrustedAsTemplate

def leaves(value, where="$"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, f"{where}.{key}")
    elif isinstance(value, list):
        for at, item in enumerate(value):
            yield from leaves(item, f"{where}[{at}]")
    elif isinstance(value, str):
        yield where, TrustedAsTemplate.is_tagged_on(value)

found = {}
for how, only in (("loaded", False), ("json", True)):
    try:
        data = DataLoader().load_from_file(sys.argv[1], cache="none", trusted_as_template=True, json_only=only)
    except Exception as error:
        found[how] = {"error": type(error).__name__}
        continue
    found[how] = {"trusted": sorted(where for where, trusted in leaves(data) if trusted),
                  "untrusted": sorted(where for where, trusted in leaves(data) if not trusted),
                  "document": json.loads(json.dumps(data))}
print(json.dumps(found))
"""


def _loaded(path: Path) -> dict:
    shown = subprocess.run(
        [sys.executable, "-I", "-c", LOADER, str(path)],
        cwd=path.parent,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return json.loads(shown.stdout.splitlines()[-1])


def test_ansible_s_loader_reads_no_string_of_a_written_file_as_trusted(made, tmp_path) -> None:
    written = variables.write(_vars(made), EVERY_KIND, secrets=())
    found = _loaded(written)
    for how in ("loaded", "json"):
        assert found[how]["trusted"] == [], found[how]
        assert found[how]["untrusted"], found[how]
        assert _same(found[how]["document"], EVERY_KIND), found[how]["document"]
    # The control: the same document unwrapped, loaded the same way, is trusted as a template, string by string.
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps(EVERY_KIND))
    control = _loaded(plain)
    assert control["loaded"]["trusted"] == found["loaded"]["untrusted"]
    assert control["loaded"]["untrusted"] == []


@pytest.mark.parametrize(
    "beside",
    [{"__ansible_vault": 1}, {WRAPPER: 1}, {"__ansible_type": "x"}],
    ids=["a vault's key", "a wrapper of no string", "a serialized type's key"],
)
def test_a_file_ansible_cannot_decode_as_json_is_read_as_yaml_where_a_wrapped_template_is_trusted(
    tmp_path, beside
) -> None:
    # What write refuses a key of the decoder's own for: Ansible's JSON decoding fails on one it cannot read, and its
    # loader then reads the very same text as YAML, where the wrapper is a plain mapping and the string in it a
    # template it trusts.
    path = tmp_path / "beside.json"
    path.write_text(json.dumps({"aw_x": {WRAPPER: TEMPLATE}, "aw_y": beside}))
    found = _loaded(path)
    assert found["json"] == {"error": "AnsibleJSONParserError"}, found
    assert f"$.aw_x.{WRAPPER}" in found["loaded"]["trusted"], found
    assert found["loaded"]["document"]["aw_x"] == {WRAPPER: TEMPLATE}


def test_a_lone_surrogate_fails_ansible_s_json_decoding_and_its_yaml_alike(tmp_path) -> None:
    # What write refuses a lone surrogate for: the play's variables would not load at all.
    path = tmp_path / "surrogate.json"
    path.write_text(json.dumps({"aw_x": {WRAPPER: TEMPLATE}, "aw_y": {WRAPPER: "\udc80"}}))
    found = _loaded(path)
    assert found["json"] == {"error": "AnsibleJSONParserError"}, found
    assert set(found["loaded"]) == {"error"}, found


# ---------------------------------------------------------------- through a real child ----


def _key(fill: bytes = b"\x01" * 32) -> hostkeys.Key:
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", len(fill)) + fill
    return hostkeys.Key("ssh-ed25519", base64.b64encode(blob).decode())


def _alpha(tmp_path: Path) -> Machine:
    """A stand-in machine with its key filed under its identity, as G3 files a VM's before its first configure."""
    alpha = machine(tmp_path / "machines", "alpha")
    hostkeys.file(target(alpha).known_hosts, alpha.identity, _key())
    return alpha


def _writes(alpha: Machine, name: str, expression: str) -> dict:
    """A task that writes what expression renders into a file of the machine's home."""
    return {
        "name": f"write {name}",
        "ansible.builtin.copy": {"content": expression, "dest": str(alpha.home / name), "mode": "0600"},
    }


def _converge(
    made: jobs.Job,
    alpha: Machine,
    folder: Path,
    given: Path,
    tasks: list[dict],
    switch: str,
    monkeypatch: pytest.MonkeyPatch,
) -> layer1.Ended:
    """tasks run on alpha by layer1.run's child, given as its variables file, with Ansible's untrusted-template
    switch at switch whatever the product's child is given."""
    built = layer1.environment
    monkeypatch.setattr(layer1, "environment", lambda *words, **named: {**built(*words, **named), SWITCH: switch})
    folder.mkdir(parents=True, exist_ok=True)
    play = folder / "play.yml"
    play.write_text(json.dumps([{"hosts": "all", "gather_facts": False, "tasks": tasks}]))
    return asyncio.run(
        layer1.run(
            made,
            target(alpha),
            play,
            given,
            name="alpha",
            host_flag="",
            env=dict(os.environ),
            test_connection=layer1_host(alpha),
        )
    )


def test_a_wrapped_template_reaches_a_task_as_its_text_and_an_unwrapped_one_is_templated(
    made, tmp_path, monkeypatch
) -> None:
    alpha = _alpha(tmp_path)
    written = variables.write(_vars(made), {"aw_text": TEMPLATE}, secrets=())
    ended = _converge(
        made, alpha, tmp_path / "wrapped", written, [_writes(alpha, "out.txt", "{{ aw_text }}")], "ignore", monkeypatch
    )
    assert ended.status == 0, ended.said
    assert (alpha.home / "out.txt").read_text() == TEMPLATE
    # The control: the same variable unwrapped, run's own check of the file set aside, is a template Ansible renders.
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps({"aw_text": TEMPLATE}))
    monkeypatch.setattr(layer1, "_variables", lambda path: path)
    ended = _converge(
        made, alpha, tmp_path / "plain", plain, [_writes(alpha, "plain.txt", "{{ aw_text }}")], "ignore", monkeypatch
    )
    assert ended.status == 0, ended.said
    assert (alpha.home / "plain.txt").read_text() == RENDERED


def test_with_the_switch_at_error_a_wrapped_template_used_as_one_fails_its_task(made, tmp_path, monkeypatch) -> None:
    alpha = _alpha(tmp_path)
    written = variables.write(_vars(made), {"aw_text": TEMPLATE, "aw_plain": "no template"}, secrets=())
    used = {
        "name": "use it",
        "block": [_writes(alpha, "plain.txt", "{{ aw_plain }}"), _writes(alpha, "out.txt", "{{ aw_text }}")],
        "rescue": [_writes(alpha, "reason.txt", "{{ ansible_failed_result.msg }}")],
    }
    ended = _converge(made, alpha, tmp_path / "error", written, [used], "error", monkeypatch)
    # Rescued: the task that used it as a template failed, and nothing was rendered; a wrapped string that is no
    # template went through.
    assert ended.status == 0, ended.said
    assert not (alpha.home / "out.txt").exists()
    assert (alpha.home / "plain.txt").read_text() == "no template"
    assert "Encountered untrusted template or expression" in (alpha.home / "reason.txt").read_text()


def test_every_scalar_type_round_trips_through_a_real_child(made, tmp_path, monkeypatch) -> None:
    alpha = _alpha(tmp_path)
    written = variables.write(_vars(made), EVERY_KIND, secrets=())
    every = "{{ {" + ", ".join(f"'{name}': {name}" for name in EVERY_KIND) + "} | to_json }}"
    ended = _converge(
        made, alpha, tmp_path / "every", written, [_writes(alpha, "every.json", every)], "ignore", monkeypatch
    )
    assert ended.status == 0, ended.said
    assert _same(json.loads((alpha.home / "every.json").read_text()), EVERY_KIND)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ansible_ssh_common_args", {WRAPPER: "-o StrictHostKeyChecking=yes -o BatchMode=yes"}),
        ("ansible_host_key_checking", False),
        ("ansible_ssh_executable", {WRAPPER: "/bin/false"}),
    ],
)
def test_run_refuses_a_variables_file_with_a_connection_s_variable_before_the_child_starts(
    made, tmp_path, name, value
) -> None:
    alpha = _alpha(tmp_path)
    given = tmp_path / "given.json"
    given.write_text(json.dumps({"aw_text": {WRAPPER: "x"}, name: value}))
    play = tmp_path / "play.yml"
    play.write_text(json.dumps([{"hosts": "all", "gather_facts": False, "tasks": [{"ansible.builtin.ping": {}}]}]))
    with pytest.raises(layer1.Refused) as refused:
        asyncio.run(
            layer1.run(
                made,
                target(alpha),
                play,
                given,
                name="alpha",
                host_flag="",
                env=dict(os.environ),
                test_connection=layer1_host(alpha),
            )
        )
    assert str(refused.value).startswith(f"{name} names the play's connection"), refused.value
    directory = rundir.made(made)
    assert directory is not None
    assert _written(directory) == [] and children.live(made) == []


@pytest.mark.parametrize(
    ("case", "text", "said"),
    [
        ("a bare string", '{"aw_text": "{{ 7 * 7 }}"}', "is wrapped, so that Ansible never reads it as a template"),
        ("YAML", "aw_text: x\n", "is not the JSON"),
    ],
)
def test_run_refuses_a_variables_file_write_would_not_have_made(made, tmp_path, case, text, said) -> None:
    alpha = _alpha(tmp_path)
    given = _hand_made(tmp_path, text)
    play = tmp_path / "play.yml"
    play.write_text("[]")
    with pytest.raises(layer1.Refused, match=said):
        asyncio.run(layer1.run(made, target(alpha), play, given, name="alpha", host_flag="", env=dict(os.environ)))
    directory = rundir.made(made)
    assert directory is not None
    assert _written(directory) == [] and children.live(made) == []
