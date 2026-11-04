"""lib/claude_vm, the controller's Python as one package: what it holds, what importing it costs, its records, and the
adapters' one way in (docs/architecture/custom-code.md §7.10)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from claude_vm import plugin, wire
from claude_vm.records import Checked, plain
from claude_vm.refs import Action, NextStep, VmRef, command_line

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "lib"
# Loaded only inside the function that needs one: each is milliseconds a client that is not Ansible would pay.
NEVER_AT_IMPORT = ("ansible", "yaml", "asyncio", "subprocess")
# What a module may load when imported all the same: the process table is what proc.py is for.
AT_IMPORT = {"claude_vm.proc": {"subprocess"}}


def _tracked(path: str) -> list[str]:
    run = subprocess.run(["git", "ls-files", "--", path], cwd=REPO, capture_output=True, text=True, check=True)
    return run.stdout.split()


def _core_modules() -> list[str]:
    names = []
    for path in sorted((LIB / "claude_vm").rglob("*.py")):
        parts = path.relative_to(LIB).with_suffix("").parts
        if "ansible" in parts[1:-1] or parts[1:2] == ("ansible",):
            continue  # the one subpackage that may import ansible
        names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


def test_lib_holds_the_one_package_claude_vm() -> None:
    # lib/ goes first on sys.path in every adapter's process: anything else there could shadow a module.
    assert _tracked("lib"), "lib/ holds nothing tracked"
    assert {Path(path).parts[1] for path in _tracked("lib")} == {"claude_vm"}


@pytest.mark.parametrize("name", _core_modules())
def test_no_core_module_loads_ansible_yaml_asyncio_or_subprocess_when_imported(name) -> None:
    # Each in an interpreter of its own, so one module's imports never hide another's.
    script = f"""
import importlib, json, sys
sys.path.insert(0, {str(LIB)!r})
importlib.import_module({name!r})
loaded = {{key.split(".")[0] for key in sys.modules}}
print(json.dumps(sorted(m for m in {NEVER_AT_IMPORT!r} if m in loaded)))
"""
    run = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, check=True)
    assert set(json.loads(run.stdout)) <= AT_IMPORT.get(name, set())


def test_importing_the_package_imports_no_module_of_it() -> None:
    script = (
        f"import sys; sys.path.insert(0, {str(LIB)!r}); import claude_vm; "
        "print(sorted(m for m in sys.modules if m.startswith('claude_vm')))"
    )
    run = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, check=True)
    assert run.stdout.strip() == "['claude_vm']"


@dataclass(frozen=True, slots=True, kw_only=True)
class _Sample(Checked):
    name: str
    count: int
    on: bool
    kind: Literal["a", "b"]
    names: tuple[str, ...]
    pair: tuple[str, int]
    either: int | None = None


GOOD = {"name": "x", "count": 1, "on": True, "kind": "a", "names": ("p", "q"), "pair": ("r", 2)}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", 1),
        ("count", True),  # True is no int
        ("count", "1"),
        ("on", 1),
        ("kind", "c"),
        ("names", ["p", "q"]),  # a list, not the tuple plain() makes
        ("names", ("p", 1)),
        ("pair", ("r",)),
        ("pair", ("r", "2")),
        ("either", "1"),
    ],
)
def test_a_record_refuses_a_field_its_annotation_cannot_hold(field, value) -> None:
    with pytest.raises(ValueError, match=rf"_Sample\.{field} cannot be"):
        _Sample(**(GOOD | {field: value}))


def test_a_record_takes_what_its_annotations_hold() -> None:
    assert _Sample(**GOOD).names == ("p", "q")
    assert _Sample(**GOOD, either=3).either == 3


def test_plain_copies_a_value_out_into_exact_types() -> None:
    class Tagged(str):
        pass

    copied = plain({Tagged("k"): [Tagged("v"), 1, True, None, {"n": (2,)}]}, "inputs")
    assert copied == {"k": ("v", 1, True, None, {"n": (2,)})}
    key, value = next(iter(copied.items()))
    assert type(key) is str and type(value[0]) is str


def test_plain_refuses_a_value_no_field_takes() -> None:
    with pytest.raises(ValueError, match=r"inputs\.vm\[0\] is a float"):
        plain({"vm": [1.5]}, "inputs")


@pytest.mark.parametrize(
    ("name", "vmid"), [("alpha", 99), ("alpha", 1_000_000_000), ("-alpha", 4013), ("al pha", 4013), ("", 4013)]
)
def test_a_vm_ref_refuses_a_name_just_would_not_take_or_an_id_proxmox_would_not_give(name, vmid) -> None:
    with pytest.raises(ValueError):
        VmRef(name=name, vmid=vmid)


def test_an_action_is_one_line_as_a_person_types_it() -> None:
    action = Action(recipe="code", args=("alpha", "--project", "my repo"))
    assert action.line == command_line(action) == "just code alpha --project 'my repo'"
    with pytest.raises(ValueError):
        Action(recipe="code", args=("alpha\n--x",))
    with pytest.raises(ValueError):
        Action(recipe="Code")


def test_to_wire_puts_format_version_and_kind_first_and_derived_values_after_the_fields() -> None:
    step = NextStep(vm=VmRef(name="alpha", vmid=4013), matter="editor", action=Action(recipe="code", args=("alpha",)))
    record = wire.to_wire(step)
    assert list(record)[:2] == ["format_version", "kind"]
    assert record == {
        "format_version": wire.FORMAT_VERSION,
        "kind": "next_step",
        "vm": {"name": "alpha", "vmid": 4013},
        "matter": "editor",
        "action": {"recipe": "code", "args": ["alpha"], "line": "just code alpha"},
    }
    assert wire.to_wire(NextStep(vm=step.vm, matter="vm", action=None))["action"] is None


def test_to_wire_refuses_what_is_no_record() -> None:
    with pytest.raises(TypeError, match="no record"):
        wire.to_wire(object())


def test_an_adapter_s_filter_imports_its_module_only_when_called() -> None:
    script = f"""
import sys
sys.path.insert(0, {str(LIB)!r})
from claude_vm import plugin
made = plugin.filters({str(LIB)!r}, "wire", ["to_wire"])
before = "claude_vm.wire" in sys.modules
from claude_vm.refs import VmRef
made["to_wire"](VmRef(name="alpha", vmid=4013))
print(before, "claude_vm.wire" in sys.modules, made["to_wire"].__name__)
"""
    run = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, check=True)
    assert run.stdout.split() == ["False", "True", "to_wire"]


def test_an_adapter_refuses_another_checkout_s_claude_vm(tmp_path) -> None:
    other = tmp_path / "lib"
    other.mkdir()
    with pytest.raises(ImportError, match="not this checkout's"):
        plugin.filters(str(other), "wire", ["to_wire"])["to_wire"](VmRef(name="alpha", vmid=4013))


def test_an_adapter_knows_its_own_lib_by_file_not_by_spelling(tmp_path) -> None:
    link = tmp_path / "lib"
    os.symlink(LIB, link)
    plugin.own(str(link))  # the same directory, spelled another way


ADAPTERS = sorted((REPO / "filter_plugins").glob("*.py"))


@pytest.mark.parametrize("path", ADAPTERS, ids=lambda path: path.name)
def test_every_adapter_loads_no_core_module_and_each_filter_is_the_core_s_of_its_name(path) -> None:
    # Every worker executes every file in filter_plugins/ at its first filter lookup: an adapter loads nothing of the
    # core there, and each filter it names calls the core's function of that name when it is called.
    script = f"""
import importlib, importlib.util, inspect, json, sys
spec = importlib.util.spec_from_file_location("adapter", {str(path)!r})
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
# After filters() too, which a worker calls at its first lookup: the plugin module it uses, and nothing of the core.
filters = adapter.FilterModule().filters()
loaded = sorted(m for m in sys.modules if m.split(".")[0] == "claude_vm" and m not in ("claude_vm", "claude_vm.plugin"))
found = {{}}
for name, call in filters.items():
    where = inspect.getclosurevars(call).nonlocals
    core = getattr(importlib.import_module("claude_vm." + where["module"]), where["name"], None)
    found[name] = [call.__name__, where["name"], callable(core)]
print(json.dumps({{"loaded": loaded, "found": found}}))
"""
    run = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, check=True)
    said = json.loads(run.stdout)
    assert said["loaded"] == []
    assert said["found"] and all(made == [name, name, True] for name, made in said["found"].items()), said["found"]
