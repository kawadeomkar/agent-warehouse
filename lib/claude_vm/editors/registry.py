"""Every family's plan passes here, and leaves only as the core allows: a refusal alone; writes strictly inside just
code's directory; helpers by YAML's names, with their directory; NEXT STEPS of the inputs' VM alone; and a launch
of the registry's own making, over the command the row names. Anything else is a family's bug, a ValueError, which
fails the first task that reads the plan - the refusal's assert, before any write."""

from __future__ import annotations

import dataclasses
import posixpath
from collections.abc import Mapping
from typing import Any, Final, assert_never

from ..catalogue import pick
from ..wire import to_wire
from . import common, vscode, zed
from .family import Family
from .model import Editor, FamilyName, OpenInputs, OpenPlan, VmDirectory, VmFile, editors_from, inputs_from, vm_path

FAMILIES: Final[Mapping[FamilyName, Family]] = {"vscode": vscode, "zed": zed}  # this annotation is the check
_vscode: Family = vscode  # one line per family, so a type checker names the member that does not conform
_zed: Family = zed


def editor_open_plan(key: object, rows: object, inputs: object) -> dict[str, Any]:
    """The plan of the window of editor key, from claude_editors and code.yml's inputs, as the wire carries it."""
    editors, given = editors_from(rows), inputs_from(inputs)
    editor = pick(key, editors, "an editor just code opens")
    return to_wire(checked(editor, given, FAMILIES[editor.family].plan_open(editor, given)))


def editor_servers(rows: object) -> list[dict[str, Any]]:
    """Each editor's window as the status probe knows it, its editors setting: every row, in its order, each
    match as the wire carries it but for the envelope, which the module's arguments do not take."""
    bare = ("format_version", "kind")
    return [
        {"key": key} | {name: value for name, value in to_wire(row.server).items() if name not in bare}
        for key, row in editors_from(rows).items()
    ]


def checked(editor: Editor, inputs: OpenInputs, plan: OpenPlan) -> OpenPlan:
    if plan.refusals:  # a refusal writes, copies and launches nothing, and records nothing
        return OpenPlan(editor=editor.key, refusals=plan.refusals)
    files = {inside(inputs.files_dir, w.path) for w in plan.vm_writes if isinstance(w, VmFile)}
    dirs = {inside(inputs.files_dir, w.path) for w in plan.vm_writes if isinstance(w, VmDirectory)}
    if not set(plan.helpers) <= set(inputs.helpers):
        raise ValueError(f"{editor.key}'s plan copies {sorted(set(plan.helpers) - set(inputs.helpers))}: no helpers")
    for write in plan.vm_writes:
        # A plan's writes are its own: never the session copies' or the notes' folders, never over a helper.
        first = posixpath.normpath(write.path).split("/")[0]
        if first in common.KEPT or (isinstance(write, VmFile) and first == common.HELPERS):
            raise ValueError(f"{editor.key}'s plan writes {write.path}, which is just code's own")
    if plan.helpers and inside(inputs.files_dir, common.HELPERS) not in dirs:
        raise ValueError(f"{editor.key}'s plan copies helpers without making {common.HELPERS}/")
    opens = vm_path(plan.opens, f"{editor.key}'s plan's folder to open")
    if plan.launch or (opens != vm_path(inputs.target, "target") and opens not in files):
        raise ValueError(f"{editor.key}'s plan opens {plan.opens!r} or builds a command of its own")
    if any(step.vm != inputs.vm for step in plan.next_steps):  # by name and VM ID: the stat's key is the record's
        raise ValueError(f"{editor.key}'s plan records NEXT STEPS for a VM it does not open")
    cli = common.pick_cli(editor, inputs.probe)
    if not cli:
        raise ValueError(f"{editor.key}'s plan opens a window, but no command here is {editor.name}")
    return dataclasses.replace(plan, launch=launch(editor.family, cli, inputs.vm.name, plan.opens))


def inside(root: str, name: str) -> str:
    """root/name, strictly inside root (common.below, on the normalised join), and never in "/" itself."""
    root = vm_path(root, "just code's directory")
    path = posixpath.normpath(posixpath.join(root, name))
    if root == "/" or not common.below(path, root):
        raise ValueError(f"{name!r} is not inside {root}")
    return path


def launch(family: FamilyName, cli: str, vm: str, opens: str) -> tuple[str, ...]:
    """Each family's fixed shape of the command that opens its window over the alias."""
    match family:
        case "vscode":
            return (cli, "--remote", f"ssh-remote+{vm}", opens)
        case "zed":
            return (cli, f"ssh://{vm}{opens}")
        case _:
            assert_never(family)
