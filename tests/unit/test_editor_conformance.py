"""The editors' plans in the core (lib/claude_vm/editors/): every family conforms to the seam, every family's real plans
pass the registry, each rule of the registry bites on a plan built to break it, a refusal leaves nothing else, and the
filter takes Ansible's own values (docs/architecture/custom-code.md §7.10). The registry runs its rules on every call;
this shows they hold and that they bite."""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import typing
from pathlib import Path

import pytest
import yaml
from harness import ANSIBLE_PLAYBOOK, ansible_config

from claude_vm.editors import model, registry
from claude_vm.editors.family import Family
from claude_vm.editors.model import FamilyName, OpenPlan, VmDirectory, VmFile
from claude_vm.refs import NextStep, VmRef

REPO = Path(__file__).resolve().parents[2]
ROWS = yaml.safe_load((REPO / "inventory/group_vars/all/defaults.yml").read_text())["claude_editors"]
HELPERS = sorted(path.name for path in (REPO / "playbooks/files").iterdir())
ROOT, FD = "/home/dev/projects", "/home/dev/projects/.claude-on-proxmox"
ZED, CODE = "/usr/local/bin/zeditor", "/usr/local/bin/code"
# ZFS's zed first on PATH, as on Ubuntu: the plan never runs it.
FOUND = {"which": ["/usr/sbin/zed", ZED, CODE], "versions": [[ZED, "Zed 0.210.4"]]}


def given(**over):
    return {
        "vm": {"name": "alpha", "vmid": 4013},
        "target": ROOT,
        "root": ROOT,
        "files_dir": FD,
        "terminal": True,
        "notes": True,
        "notes_file": f"{FD}/session/alpha-session.md",
        "notes_dir": f"{FD}/session",
        "task_written": True,
        "helpers": HELPERS,
        "probe": FOUND,
    } | over


WINDOW_CASES = [
    given(),
    given(target=f"{ROOT}/bot", project="bot"),
    given(files_dir="/home/dev/.just-code", task_written=False),
    given(session_id="0" * 8 + "-0000-0000-0000-" + "0" * 12, session_branch="main", session_commit="a" * 40),
    given(terminal=False, task_written=False),
]
REFUSED = [
    ("zed", given(probe={"which": ["/usr/sbin/zed"]})),
    ("vscode", given(probe={})),
    ("zed", given(target=f"{ROOT}/my repo", project="my repo")),
]
SLIPS = [
    {"helpers": ("vault.yml",)},
    {"helpers": ("claude-start.sh",)},  # without its directory
    {"launch": ("/bin/sh",)},
    {"opens": "/etc"},
    {"next_steps": (NextStep(vm=VmRef(name="alpha", vmid=4014), matter="editor", action=None),)},  # another VM
]
OUTSIDE = ["/etc/hosts", "../", "../repo/.zed/tasks.json", f"{FD}/../repo/x", "a/../../x", ""]
BAD_INPUTS = [
    {"files_dir": "/"},
    {"files_dir": ROOT},
    {"terminal": "false"},
    {"target": "/etc"},
    {"colour": "red"},
    {"vm": "alpha"},  # a bare name is no VmRef
]


def conforms(module: object, protocol: type) -> list[str]:
    """The members a type checker would refuse: missing, or another shape of parameters or return."""
    members = (
        typing.get_protocol_members(protocol)
        if hasattr(typing, "get_protocol_members")
        else protocol.__protocol_attrs__
    )

    def shape(fn, drop):
        params = list(inspect.signature(fn).parameters.values())[drop:]
        return [(p.name, p.kind) for p in params]

    return [
        m
        for m in sorted(members)
        if not callable(getattr(module, m, None)) or shape(getattr(protocol, m), 1) != shape(getattr(module, m), 0)
    ]


def test_every_family_conforms_and_every_row_parses():
    assert set(registry.FAMILIES) == set(typing.get_args(FamilyName))
    assert [name for name, module in registry.FAMILIES.items() if conforms(module, Family)] == []
    assert {row.family for row in model.editors_from(ROWS).values()} <= set(registry.FAMILIES)


@pytest.mark.parametrize("key", sorted(ROWS))
@pytest.mark.parametrize("case", WINDOW_CASES, ids=range(len(WINDOW_CASES)))
def test_every_family_s_plans_pass_the_core(key, case):
    plan = registry.editor_open_plan(key, ROWS, case)
    assert not plan["refusals"] and plan["launch"][0] in FOUND["which"][1:]
    assert plan["kind"] == "open_plan" and plan["next_steps"][0]["vm"] == case["vm"]
    # Every write tied to its switch: none without the terminal, or for Zed where no task is written.
    wrote = bool(plan["vm_writes"] or plan["helpers"])
    assert wrote == (case["terminal"] and (key == "vscode" or case["task_written"]))


@pytest.mark.parametrize(("key", "case"), REFUSED, ids=["zfs alone", "no code", "a folder no URL carries"])
def test_a_refusal_leaves_nothing_but_itself(key, case):
    plan = registry.editor_open_plan(key, ROWS, case)
    assert plan["refusals"]
    assert not any(plan[name] for name in ("vm_writes", "helpers", "opens", "launch", "report", "next_steps"))


def test_zfs_s_zed_is_never_the_command_run():
    plan = registry.editor_open_plan("zed", ROWS, given())
    assert plan["launch"] == [ZED, f"ssh://alpha{ROOT}"]


def test_zed_s_task_file_is_marked_and_written_as_to_nice_json_writes_it():
    plan = registry.editor_open_plan("zed", ROWS, given())
    (tasks,) = [w for w in plan["vm_writes"] if w["kind"] == "file"]
    head, body = tasks["content"].split("\n", 1)
    assert head == "// Written by just code (claude-on-proxmox); edits here are overwritten."
    assert json.loads(body)[0]["args"] == [f"{FD}/helpers/claude-start.sh"]
    assert body == json.dumps(json.loads(body), indent=2, sort_keys=True, separators=(",", ": ")) + "\n"


def test_a_foreign_task_file_is_left_and_said():
    plan = registry.editor_open_plan("zed", ROWS, given(probe=FOUND | {"tasks_foreign": True}))
    assert [w["path"] for w in plan["vm_writes"]] == ["helpers"]
    assert any("is not just code's, so it was left as it is" in line for line in plan["report"])


@pytest.mark.parametrize("name", OUTSIDE)
def test_no_write_leaves_just_code_s_directory(name):
    with pytest.raises(ValueError):
        VmFile(kind="file", path=name, mode="0644", content="")
    with pytest.raises(ValueError):
        registry.inside(FD, name)


@pytest.mark.parametrize(
    "slip", SLIPS, ids=["a helper not listed", "no helpers directory", "a launch", "opens /etc", "another VM"]
)
def test_the_core_refuses_a_family_s_slip(slip):
    editor, inputs = model.editors_from(ROWS)["zed"], model.inputs_from(given())
    with pytest.raises(ValueError):
        registry.checked(editor, inputs, OpenPlan(editor="zed", **({"opens": ROOT} | slip)))


def test_a_plan_s_helpers_need_their_directory_and_pass_with_it():
    editor, inputs = model.editors_from(ROWS)["zed"], model.inputs_from(given())
    made = VmDirectory(kind="directory", path="helpers")
    plan = registry.checked(
        editor, inputs, OpenPlan(editor="zed", opens=ROOT, helpers=("claude-start.sh",), vm_writes=(made,))
    )
    assert plan.launch == (ZED, f"ssh://alpha{ROOT}")


@pytest.mark.parametrize("bad", BAD_INPUTS, ids=lambda bad: next(iter(bad)))
def test_inputs_and_rows_are_held_to_their_types(bad):
    with pytest.raises((ValueError, TypeError)):
        model.inputs_from(given(**bad))
    with pytest.raises(ValueError):
        model.editors_from({"emacs": ROWS["zed"] | {"family": "emacs"}})


def test_the_filter_takes_ansible_s_own_values(tmp_path):
    # Lazy and tagged values, as code.yml hands them; the plan kept as a fact, as the window step keeps none.
    inputs = given(terminal="{{ switch }}", target="{{ where }}", vm={"name": "alpha", "vmid": "{{ listed.vmid }}"})
    play = {
        "hosts": "localhost",
        "gather_facts": False,
        "vars": {"rows": ROWS, "inputs": inputs, "switch": True, "where": ROOT, "listed": {"vmid": 4013}},
        "tasks": [
            {"ansible.builtin.set_fact": {"kept": "{{ 'zed' | editor_open_plan(rows, inputs) }}"}},
            {"name": "Say", "ansible.builtin.debug": {"msg": "{{ [kept.kind] + kept.launch }}"}},
        ],
    }
    (tmp_path / "plan.yml").write_text(yaml.safe_dump([play]))
    (tmp_path / "hosts").write_text(f"localhost ansible_connection=local ansible_python_interpreter={sys.executable}\n")
    env = {
        "PATH": "/usr/bin:/bin",
        "ANSIBLE_CONFIG": ansible_config(tmp_path),
        "ANSIBLE_FILTER_PLUGINS": str(REPO / "filter_plugins"),
        "ANSIBLE_STDOUT_CALLBACK": "ansible.builtin.json",
        # The checkout's own collections, as every recipe exports them: the json callback is ansible.posix's.
        "ANSIBLE_COLLECTIONS_PATH": os.environ["ANSIBLE_COLLECTIONS_PATH"],
    }
    run = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", str(tmp_path / "hosts"), str(tmp_path / "plan.yml")],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    tasks = json.loads(run.stdout)["plays"][0]["tasks"]
    said = next(task for task in tasks if task["task"]["name"] == "Say")["hosts"]["localhost"]["msg"]
    assert said == ["open_plan", ZED, f"ssh://alpha{ROOT}"]
