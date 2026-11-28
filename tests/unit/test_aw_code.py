"""`aw code`, one VM opened in the editor: lib/claude_vm/engine/code.py (card G17; docs/architecture/engine.md §16.4).

Without the fake: the commands code runs, each by name - on the VM, a read of a fixed list or a write in its own
directory, and here, the editor's own command lines - a new one failing by name, and the module's one call of each
runner; the helpers, today's lists (code.yml's and playbooks/vars/vscode.yml's) and playbooks/files/' files; the
folders a checkout is looked for in, the time zone, the editor's command found on PATH, the home and the folder's
refusals, said with what the projects directory holds; the editor and the managed file refused before anything is read;
and inside deploy, a window that failed said with the line that opens it later, or the deploy that starts the VM, and
the stage skipped, never failed.

On a whole fleet folder against the fake Proxmox, the VM made by G3's `aw deploy --only provision` and reached at F30's
stand-in machine (tests/unit/aw_machine.py), with stand-ins of the editors' commands first on PATH that record how they
were started, and never the developer's editor. Through `aw code` as a person runs it: Zed's window opened with the
alias and the folder (`aw code delta --editor zed`), its task file, its helper and code's directory written on the VM,
and every command the VM was sent one of the module's; VS Code's, the Remote - SSH extension installed where it lacks it
and never again, the person's settings read and never written; and each refusal before anything is written - a stopped
VM with the line that starts it, code's directory inside a checkout, a folder that is not there with the projects there,
the managed file off, --session (card G18), more than one name and a --project that is not one folder - with no write
on the VM, no alias and no editor started. At a terminal (test_code_live.py's cases, which stays as it is for today's
`just code`): the window drawn by its steps with its VM, and opened; and a VM that is not there, said under the step it
failed in.
"""

from __future__ import annotations

import ast
import asyncio
import os
import shlex
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from aw_kit import aw_at_a_terminal, run_aw
from aw_machine import SSH_FOLDER, Machine
from harness import REPO, _pve
from pki import proxmox_pki
from test_aw_create import FIRST, Made, _making, _ok

from claude_vm.editors import common, registry
from claude_vm.editors.vscode import HELPERS as FAMILY_HELPERS
from claude_vm.engine import aliases, code
from claude_vm.ssh import runner

CODE_PY = REPO / "lib" / "claude_vm" / "engine" / "code.py"
WINDOW = (200, 40)
# Zed's version line, as the real one says it.
ZED_SAYS = "Zed 1.26.2 - /Applications/Zed.app"
# An editor's command: each call one line of the log, its name and its words split by tabs, then what it answers.
STAND_IN = """#!/bin/sh
{{ printf '%s' "${{0##*/}}"; for word; do printf '\\t%s' "$word"; done; printf '\\n'; }} >> "{log}"
case "$1" in
  --version) echo "{version}" ;;
  --list-extensions) {extensions} ;;
esac
exit 0
"""


# ---------------------------------------------------------------- without the fake ----


def test_every_command_code_runs_is_one_of_its_named_reads_or_writes_and_a_new_one_fails_by_name() -> None:
    # On the VM: the home's read, the folder's, the checkouts' and a file's first line; the writes, a directory and a
    # file, each given its path as an argument. Here: the editor's version, VS Code's extensions and the install.
    assert set(code.READS) == {"home", "folder", "resolved", "checkouts", "first-line"}
    assert set(code.WRITES) == {"directory", "file"}
    assert code.READS["home"] == ("getent", "passwd")
    assert code.HERE == {
        "version": ("--version",),
        "extensions": ("--list-extensions",),
        "install": ("--install-extension", "ms-vscode-remote.remote-ssh"),
    }
    # Each script takes its values as arguments: none names a value of its own.
    for name, script in {**code.READS, **code.WRITES}.items():
        if isinstance(script, str):
            assert "{" not in script.replace("${f##*/}", ""), name


def _calls(tree: ast.AST, owner: str, name: str) -> list[str]:
    """The functions of tree that call owner.name, by name, one for each call."""
    found = []
    for function in ast.walk(tree):
        if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == name
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == owner
                ):
                    found.append(function.name)
    return found


def test_the_module_runs_a_command_on_the_vm_and_here_through_one_function_each() -> None:
    tree = ast.parse(CODE_PY.read_text())
    # Nested functions are walked by their outer one too: each call is counted once, under the innermost.
    assert set(_calls(tree, "runner", "run")) == {"on_machine"}
    assert set(_calls(tree, "children", "start")) == {"here"}
    assert set(_calls(tree, "children", "outlive")) == {"window"}
    for banned in ("subprocess", "pty"):
        assert banned not in {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for alias in node.names
        }


def test_the_steps_are_deploy_s_editor_stage_s_in_its_order_and_each_one_code_begins_is_declared() -> None:
    declared = [step.key for step in code.DECLARED.stages[0].steps]
    deploys = [step.key for step in code.DEPLOY.stage("editor").steps]
    assert declared == [key for key in deploys if key in code.BUILT] and set(declared) == set(code.BUILT)
    assert set(deploys) - set(declared) == {"read-sessions", "copy-sessions", "notes"}
    tree = ast.parse(CODE_PY.read_text())
    begun = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "step"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert begun == set(declared), begun


def test_the_helpers_are_today_s_lists_and_files() -> None:
    play = yaml.safe_load((REPO / "playbooks" / "code.yml").read_text())[0]
    vscode_vars = yaml.safe_load((REPO / "playbooks" / "vars" / "vscode.yml").read_text())
    assert list(code.SHARED_HELPERS) == play["vars"]["code_shared_helpers"]
    assert list(code.HELPER_NAMES) == play["vars"]["code_shared_helpers"] + vscode_vars["code_helpers"]
    assert tuple(vscode_vars["code_helpers"]) == FAMILY_HELPERS
    assert all((code.FILES / name).is_file() for name in code.HELPER_NAMES)


def test_the_folders_a_checkout_is_looked_for_in_are_those_below_the_projects_down_to_code_s_own() -> None:
    assert code.below("/home/dev/projects/.agent-warehouse", "/home/dev/projects") == [".agent-warehouse"]
    assert code.below("/home/dev/projects/a/b/", "/home/dev/projects/") == ["a", "b"]
    assert code.below("/srv/aw", "/home/dev/projects") == []
    assert code.below("/home/dev/projects-x/aw", "/home/dev/projects") == []


def test_the_time_zone_is_tz_then_localtime_s_zone_then_timezone_else_utc(tmp_path) -> None:
    zoneinfo = tmp_path / "usr" / "share" / "zoneinfo" / "Europe"
    zoneinfo.mkdir(parents=True)
    (zoneinfo / "Paris").write_text("")
    localtime, named = tmp_path / "localtime", tmp_path / "timezone"
    localtime.symlink_to(zoneinfo / "Paris")
    named.write_text("Asia/Tokyo\n")
    assert code.zone({"TZ": ":America/New_York"}, str(localtime), str(named)) == "America/New_York"
    assert code.zone({}, str(localtime), str(named)) == "Europe/Paris"
    assert code.zone({}, str(tmp_path / "none"), str(named)) == "Asia/Tokyo"
    assert code.zone({"TZ": "x;y"}, str(tmp_path / "none"), str(tmp_path / "none")) == "UTC"


def test_the_editor_s_command_is_found_on_path_as_which_finds_it(tmp_path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    for folder in (first, second):
        folder.mkdir()
        for name in ("zed", "zeditor"):
            (folder / name).write_text("#!/bin/sh\n")
            (folder / name).chmod(0o755)
    (second / "code").write_text("")  # not executable: no command
    path = {"PATH": f"{first}{os.pathsep}{second}"}
    assert code.which(["zeditor", "zed"], path, True) == [
        f"{first}/zeditor",
        f"{second}/zeditor",
        f"{first}/zed",
        f"{second}/zed",
    ]
    assert code.which(["zeditor", "zed"], path, False) == [f"{first}/zeditor", f"{first}/zed"]
    assert code.which(["code"], path, False) == []


def test_an_editor_not_on_path_is_refused_by_its_plan_and_the_alias_said_in_place() -> None:
    inputs = {"vm": {"name": "delta", "vmid": 9001}, "target": "/p", "root": "/p", "files_dir": "/p/.aw",
              "terminal": True, "notes": False, "probe": {"which": []}}  # fmt: skip
    for key in ("vscode", "zed"):
        [refusal] = registry.editor_open_plan(key, code.EDITORS, inputs)["refusals"]
        assert "The alias is in place either way: ssh delta." in refusal


def _settings(**given: object) -> dict[str, object]:
    return {"editor": "vscode", "claude_ssh_config": True, **given}


def test_an_editor_code_cannot_open_and_the_managed_file_off_are_refused_before_anything_is_read() -> None:
    assert code.before(code.Asked("delta", editor="zed"), _settings()) == "zed"
    assert code.before(code.Asked("delta"), _settings()) == "vscode"
    with pytest.raises(code.Failed) as failed:
        code.before(code.Asked("delta"), _settings(editor="emacs"))
    assert 'The editor setting is "emacs"' in failed.value.sentence and "vscode or zed" in failed.value.sentence
    with pytest.raises(code.Failed) as failed:
        code.before(code.Asked("delta"), _settings(claude_ssh_config=False))
    assert failed.value.sentence.startswith("claude_ssh_config is false") and failed.value.refused


def test_a_home_that_is_no_plain_absolute_path_is_refused() -> None:
    assert code.home_checked(["dev:x:1000:1000:dev:/home/dev:/bin/bash"], "dev", "delta") == "/home/dev"
    for line in ("dev:x:1000:1000:dev:/home/d\x1bv:/bin/bash", "dev:x:1:1::home:/bin/sh", "root:x:0:0::/root:/bin/sh"):
        with pytest.raises(code.Failed):
            code.home_checked([line], "dev", "delta")


class _Ctx:
    """A run's context as code's steps take it: each event kept."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str | None, str, dict]] = []
        self.job = SimpleNamespace(id="0" * 16)
        self.cancelled = asyncio.Event()

    def emit(self, kind: str, machine: str | None, text: str, **data: object) -> None:
        self.events.append((kind, machine, text, data))


def _host(**values: object) -> SimpleNamespace:
    settings = _settings(**values)
    return SimpleNamespace(
        name="pve",
        host_flag="",
        environ={},
        values={name: SimpleNamespace(value=value) for name, value in settings.items()},
    )


def _opening(asked: code.Asked, answers: dict[str, list[str]], **values: object) -> code.Opening:
    """An Opening whose reads on the VM answer from answers, by name, and record what they were given."""
    opening = code.Opening(_Ctx(), _host(**values), asked, "zed")  # type: ignore[arg-type]
    opening.asked_reads = []  # type: ignore[attr-defined]

    async def read(name: str, words, reach: str) -> runner.Done:
        opening.asked_reads.append((name, list(words)))  # type: ignore[attr-defined]
        return runner.Done(0, tuple(answers.get(name, ())), "")

    opening.read = read  # type: ignore[method-assign]
    return opening


def _folder(opening: code.Opening, home: str = "/home/dev") -> dict:
    return asyncio.run(opening.folder(home, "203.0.113.10"))


def test_the_folder_settled_and_code_s_directory_looked_for_in_a_checkout() -> None:
    opening = _opening(code.Asked("delta", "app"), {"folder": ["dir"]})
    settled = _folder(opening)
    assert (settled["root"], settled["target"], settled["files_dir"]) == (
        "/home/dev/projects",
        "/home/dev/projects/app",
        "/home/dev/projects/.agent-warehouse",
    )
    assert settled["task_written"] and settled["writes"]
    assert opening.asked_reads == [  # type: ignore[attr-defined]
        ("folder", ["/home/dev/projects/app", "/home/dev/projects"]),
        ("resolved", ["/home/dev/projects", *["/home/dev/projects/.agent-warehouse"] * 2]),
        ("checkouts", ["/home/dev/projects/.agent-warehouse"]),
    ]
    found = _opening(code.Asked("delta"), {"folder": ["dir"], "checkouts": ["/home/dev/projects/.agent-warehouse"]})
    with pytest.raises(code.Failed) as failed:
        _folder(found)
    assert "is inside the checkout /home/dev/projects/.agent-warehouse" in failed.value.sentence
    # The Claude terminal off and nothing else going into the directory: nothing is looked for.
    off = _opening(code.Asked("delta"), {"folder": ["dir"]}, editor_claude_terminal=False)
    assert not _folder(off)["writes"]
    assert [name for name, _ in off.asked_reads] == ["folder"]  # type: ignore[attr-defined]


def test_code_s_directory_that_is_a_link_or_resolves_outside_the_projects_is_refused() -> None:
    link = _opening(code.Asked("delta"), {"folder": ["dir"], "resolved": ["link\t/home/dev/projects/.agent-warehouse"]})
    with pytest.raises(code.Failed) as failed:
        _folder(link)
    assert "/home/dev/projects/.agent-warehouse on delta is a link" in failed.value.sentence
    assert [name for name, _ in link.asked_reads] == ["folder", "resolved"]  # type: ignore[attr-defined]
    answers = ["root\t/home/dev/projects", "real\t/srv/elsewhere"]
    outside = _opening(code.Asked("delta"), {"folder": ["dir"], "resolved": answers})
    with pytest.raises(code.Failed) as failed:
        _folder(outside)
    assert "resolves to /srv/elsewhere, outside the projects directory" in failed.value.sentence
    inside = ["root\t/data/projects", "real\t/data/projects/.agent-warehouse"]
    assert _folder(_opening(code.Asked("delta"), {"folder": ["dir"], "resolved": inside}))["writes"]


@pytest.mark.parametrize(
    ("answer", "head"),
    [(["absent"], None), (["there", "// mine"], "// mine"), (["there"], ""), (["unreadable"], "")],
)
def test_a_task_file_that_is_there_is_never_taken_for_absent_even_unreadable(answer, head) -> None:
    opening = _opening(code.Asked("delta"), {"first-line": answer})
    settled = {"files_dir": "/home/dev/projects/.agent-warehouse"}
    assert asyncio.run(opening.tasks_head(settled, "203.0.113.10")) == head


@pytest.mark.parametrize(
    ("values", "said"),
    [
        ({"vm_projects_dir": "projects"}, "vm_projects_dir is a path on the VM, and must be one plain absolute one"),
        ({"vm_projects_dir": "/srv/p\x1b"}, "vm_projects_dir is a path on the VM"),
        ({"editor_files_dir": "aw"}, "editor_files_dir is a path on the VM, and must be one plain absolute one"),
        ({"editor_files_dir": "/"}, "editor_files_dir is /, the whole of the VM"),
        ({"editor_files_dir": "/home/dev/projects/"}, "editor_files_dir is the projects directory"),
        ({"editor_files_dir": "/home/dev/projects/app"}, "is where code keeps its own files, not a project"),
    ],
)
def test_a_projects_or_code_s_directory_that_is_not_one_plain_absolute_path_of_its_own_is_refused(values, said):
    opening = _opening(code.Asked("delta", "app"), {"folder": ["dir"]}, **values)
    with pytest.raises(code.Failed) as failed:
        _folder(opening)
    assert said in failed.value.sentence and failed.value.refused
    assert opening.asked_reads == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("project", "answer", "said"),
    [
        (
            "app",
            ["absent", "listed", "App", "web"],
            "There is no project named app in /home/dev/projects on delta. Did"
            " you mean App? Folder names are case-sensitive.",
        ),
        ("api", ["absent", "listed", "App", "web"], "The projects there are: App, web."),
        ("app", ["file"], "/home/dev/projects/app on delta is a file, not a project folder."),
        ("app", ["absent", "listed"], "holds no projects yet. aw deploy delta --only configure clones"),
        ("", ["absent"], "/home/dev/projects does not exist on delta yet"),
    ],
)
def test_a_folder_that_is_not_there_is_said_with_what_the_projects_directory_holds(project, answer, said) -> None:
    with pytest.raises(code.Failed) as failed:
        _folder(_opening(code.Asked("delta", project), {"folder": answer}))
    assert said in failed.value.sentence, failed.value.sentence


def test_inside_deploy_a_window_that_failed_is_said_with_the_code_line_and_stops_nothing(monkeypatch) -> None:
    def failing(sentence: str, matter: str = code.MATTER):
        async def run(self) -> None:
            self.machine = "agent-warehouse-x"
            self.step("folder")
            raise code.Failed(sentence, matter=matter)

        return run

    for sentence, matter, line in (
        ("The folder is not there.", code.MATTER, "to open it later: aw code delta --editor zed"),
        (
            "delta is stopped.",
            code.STOPPED_MATTER,
            "To start it and finish the deploy: aw deploy delta --only provision --start",
        ),
    ):
        monkeypatch.setattr(code.Opening, "run", failing(sentence, matter))
        ctx = _Ctx()
        assert asyncio.run(code.window(ctx, _host(), code.Asked("delta", editor="zed"), in_deploy=True)) is False
        [said] = [text for kind, _, text, _ in ctx.events if kind == "report"]
        assert line in said, said
        stages = [data.get("outcome") for kind, _, _, data in ctx.events if kind == "stage"]
        assert stages == ["skipped"] and not [each for each in ctx.events if each[0] == "end"]
        steps = [(text, data.get("outcome")) for kind, _, text, data in ctx.events if kind == "step"]
        assert steps == [("folder", None), ("folder", "skipped")]
        # Outside deploy the same failure fails the stage and the VM, and is raised.
        ctx = _Ctx()
        with pytest.raises(code.Failed):
            asyncio.run(code.window(ctx, _host(), code.Asked("delta", editor="zed")))
        assert [data.get("outcome") for kind, _, _, data in ctx.events if kind in ("stage", "end")] == ["failed"] * 2
    # A refusal before the run, inside deploy: said, and False.
    ctx = _Ctx()
    assert asyncio.run(code.window(ctx, _host(claude_ssh_config=False), code.Asked("delta"), in_deploy=True)) is False
    assert [kind for kind, *_ in ctx.events] == ["report", "next"]


# ---------------------------------------------------------------- against the fake ----


@pytest.fixture
def pki(tmp_path):
    return proxmox_pki(tmp_path / "pki", key_usage=True)


@pytest.fixture
def made(home, tmp_path, pki) -> Iterator[Made]:
    yield from _making(home, tmp_path, pki, True)


# An editor's command no test may start: each name the editors list gives one, first on the PATH a run inherits, after
# the test's own and the kit's stand-ins, so that one the computer has is never reached; each records that it was
# started, and refuses.
TRAPPED = ("zeditor", "zed", "code")
TRAP = """#!/bin/sh
{{ printf '%s' "${{0##*/}}"; for word; do printf '\\t%s' "$word"; done; printf '\\n'; }} >> "{log}"
exit 1
"""


@pytest.fixture(autouse=True)
def no_editor_but_the_test_s(tmp_path_factory, monkeypatch) -> Iterator[None]:
    """Guard: no run of this file reaches an editor's command that is not the test's own stand-in (Editors)."""
    trap = tmp_path_factory.mktemp("trap")
    log = trap / "reached.log"
    for name in TRAPPED:
        (trap / name).write_text(TRAP.format(log=log))
        (trap / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{trap}{os.pathsep}{os.environ.get('PATH', os.defpath)}")
    yield
    assert not log.exists(), f"an editor not the test's own was started: {log.read_text()}"


class Editors:
    """Stand-ins of the editors' commands in a folder of the test's own: every name the editors list gives one - code,
    and zeditor and zed, which say they are Zed - so that the first of each name on PATH is the test's."""

    def __init__(self, folder: Path, *, extension: bool) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.folder, self.log = folder, folder / "started.log"
        listed = "echo ms-vscode-remote.remote-ssh" if extension else ":"
        for name, version in (("code", "1.99.0"), ("zeditor", ZED_SAYS), ("zed", ZED_SAYS)):
            path = folder / name
            path.write_text(STAND_IN.format(log=self.log, version=version, extensions=listed))
            path.chmod(0o755)

    def started(self) -> list[list[str]]:
        """How each was started, in order: its name, then its words."""
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return [line.split("\t") for line in lines]


def _ready(made: Made, *, projects: bool = True) -> Machine:
    """delta made by G3's provision at a stand-in machine, its projects directory there."""
    machine = made.stand_in("m0", FIRST)
    _ok(made.deploy("delta"))
    if projects:
        (machine.home / "projects").mkdir()
    return machine


def _code(made: Made, editors: Editors, *words: str):
    return run_aw("code", *words, home=made.home, first_on_path=(made.root / SSH_FOLDER, editors.folder), timeout=180)


def _sent(machine: Machine, since: int = 0) -> list[tuple[str, list[str]]]:
    """Each command the VM ran, by the module's name for it, and its arguments: a command none names fails here."""
    scripts = {script: name for name, script in {**code.READS, **code.WRITES}.items() if isinstance(script, str)}
    found = []
    for call in machine.calls()[since:]:
        if call["outcome"] != "ran":
            continue
        words = shlex.split(call["argv"][-1])
        if words[:2] == list(code.READS["home"]):
            found.append(("home", words[2:]))
        elif words[:2] == ["sh", "-c"] and words[2] in scripts and words[3] == "sh":
            found.append((scripts[words[2]], words[4:]))
        else:
            pytest.fail(f"the VM was sent a command code names no read or write for: {words}")
    return found


def _config(made: Made) -> str:
    managed = aliases.Files.of({"HOME": str(made.home)}).config
    return managed.read_text() if managed.exists() else ""


def _said(ran) -> str:
    return " ".join((ran.stdout + ran.stderr).split())


def test_zed_s_window_opens_with_the_alias_and_the_folder_and_writes_only_in_code_s_directory(made, tmp_path) -> None:
    machine = _ready(made)
    editors = Editors(tmp_path / "editors", extension=True)
    since = len(machine.calls())
    ran = _code(made, editors, "delta", "--editor", "zed")
    _ok(ran)
    root = machine.home / "projects"
    ours = root / ".agent-warehouse"
    # The window, by the alias and the folder; the first of Zed's commands asked its version first, and no other.
    assert editors.started() == [["zeditor", "--version"], ["zeditor", f"ssh://delta{root}"]]
    assert "\nHost delta\n" in _config(made) and f"HostName {machine.address}" in _config(made)
    # On the VM: code's directory, 0755; its helpers' and Zed's directories; the helper, as playbooks/files/ has it,
    # 0644; and the task file, with the marker.
    assert stat.S_IMODE(ours.stat().st_mode) == 0o755
    helper = ours / common.HELPERS / "claude-start.sh"
    assert helper.read_bytes() == (code.FILES / "claude-start.sh").read_bytes()
    assert stat.S_IMODE(helper.stat().st_mode) == 0o644
    tasks = ours / ".zed" / "tasks.json"
    assert tasks.read_text().startswith(common.MARKER + "\n") and str(helper) in tasks.read_text()
    assert sorted(path.name for path in root.iterdir()) == [".agent-warehouse"]
    sent = _sent(machine, since)
    reads = ["home", "folder", "resolved", "checkouts", "first-line"]
    # code's directory, then every path the plan writes looked at for a link before the first is written.
    writes = ["directory", "resolved"] + ["directory"] * 2 + ["file"] * 2
    assert [name for name, _ in sent] == reads + writes
    for name, words in sent:
        if name in code.WRITES:
            assert words[0].startswith(f"{ours}/") or words[0] == str(ours), (name, words)
    assert f"Zed is opening {root} on delta" in _said(ran)
    # Again: nothing changed, every file said unchanged, the window opened again.
    stamp = tasks.stat().st_mtime_ns
    _ok(_code(made, editors, "delta", "--editor", "zed"))
    assert tasks.stat().st_mtime_ns == stamp and editors.started()[-1] == ["zeditor", f"ssh://delta{root}"]


def test_vs_code_s_window_installs_its_extension_once_and_reads_the_settings_never_writing_them(made, tmp_path) -> None:
    machine = _ready(made)
    settings = made.home / ".config" / "Code" / "User" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"task.allowAutomaticTasks": "on"}\n')
    stamp = settings.stat().st_mtime_ns
    lacking = Editors(tmp_path / "lacking", extension=False)
    _ok(_code(made, lacking, "delta"))
    [listed, install, opened] = lacking.started()
    assert listed == ["code", "--list-extensions"]
    assert install == ["code", "--install-extension", "ms-vscode-remote.remote-ssh"]
    ours = machine.home / "projects" / ".agent-warehouse"
    assert opened[:3] == ["code", "--remote", "ssh-remote+delta"] and opened[3].startswith(f"{ours}/"), opened
    assert opened[3].endswith(".code-workspace") and Path(opened[3]).is_file()
    # VS Code's own helpers, which its workspace's tasks run; the shared one is the task file's of an editor that lists.
    assert sorted(path.name for path in (ours / common.HELPERS).iterdir()) == sorted(FAMILY_HELPERS)
    assert settings.stat().st_mtime_ns == stamp and settings.read_text() == '{"task.allowAutomaticTasks": "on"}\n'
    holding = Editors(tmp_path / "holding", extension=True)
    _ok(_code(made, holding, "delta"))
    assert [each[1] for each in holding.started()][:1] == ["--list-extensions"]
    assert "--install-extension" not in [word for each in holding.started() for word in each]


def _refused_before_any_write(made: Made, machine: Machine, editors: Editors, *words: str) -> str:
    config, since = _config(made), len(machine.calls())
    ran = _code(made, editors, *words)
    _ok(ran, 1)
    assert [name for name, _ in _sent(machine, since) if name in code.WRITES] == []
    assert _config(made) == config and editors.started() == []
    return _said(ran)


@pytest.mark.parametrize("mode", [0o644, 0o000], ids=["readable", "unreadable"])
def test_a_task_file_of_someone_else_s_is_left_as_it_is_and_said(made, tmp_path, mode) -> None:
    machine = _ready(made)
    tasks = machine.home / "projects" / ".agent-warehouse" / ".zed" / "tasks.json"
    tasks.parent.mkdir(parents=True)
    tasks.write_text('[{"label": "mine"}]\n')
    tasks.chmod(mode)
    try:
        ran = _code(made, Editors(tmp_path / "e", extension=True), "delta", "--editor", "zed")
        _ok(ran)
        assert stat.S_IMODE(tasks.stat().st_mode) == mode
        tasks.chmod(0o644)
        assert tasks.read_text() == '[{"label": "mine"}]\n'
        assert f"{tasks.parent.parent}/.zed/tasks.json is not just code's, so it was left as it is" in _said(ran)
    finally:
        tasks.chmod(0o644)


@pytest.mark.parametrize("where", ["checkout", "outside"])
def test_a_link_at_code_s_directory_is_refused_before_any_write(made, tmp_path, where) -> None:
    machine = _ready(made)
    root = machine.home / "projects"
    if where == "checkout":
        (root / "repo" / ".git").mkdir(parents=True)
        led = root / "repo" / "sub"
    else:
        led = tmp_path / "elsewhere"
    led.mkdir()
    (root / ".agent-warehouse").symlink_to(led)
    said = _refused_before_any_write(made, machine, Editors(tmp_path / "e", extension=True), "delta", "--editor", "zed")
    assert f"{root}/.agent-warehouse on delta is a link" in said
    assert list(led.iterdir()) == []


def _script(script: str, *words: str, stdin: str = "") -> str:
    """One of the module's scripts run here by sh, as the VM runs it: what it printed."""
    ran = subprocess.run(["sh", "-c", script, "sh", *words], input=stdin, capture_output=True, text=True, check=True)
    return ran.stdout


def test_the_directory_s_and_the_file_s_writes_refuse_a_link_and_write_nothing_through_it(tmp_path) -> None:
    tmp_path = tmp_path / "scripts"
    tmp_path.mkdir()
    led = tmp_path / "led"
    led.mkdir()
    (tmp_path / "dir").symlink_to(led)
    assert _script(code.WRITES["directory"], str(tmp_path / "dir"), "0755") == "link\n"
    assert list(led.iterdir()) == []
    theirs = tmp_path / "theirs.json"
    theirs.write_text("theirs\n")
    (tmp_path / "tasks.json").symlink_to(theirs)
    assert _script(code.WRITES["file"], str(tmp_path / "tasks.json"), "0644", stdin="ours\n") == "link\n"
    assert theirs.read_text() == "theirs\n" and (tmp_path / "tasks.json").is_symlink()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["dir", "led", "tasks.json", "theirs.json"]
    assert _script(code.WRITES["file"], str(tmp_path / "ours.json"), "0644", stdin="ours\n") == "written\n"


@pytest.mark.parametrize("inner", ["helpers", ".zed"])
def test_a_link_inside_code_s_directory_stops_every_write_of_the_window(made, tmp_path, inner) -> None:
    machine = _ready(made)
    root = machine.home / "projects"
    ours = root / ".agent-warehouse"
    ours.mkdir()
    if inner == "helpers":
        (root / "repo" / ".git").mkdir(parents=True)
        led = root / "repo" / "sub"
    else:
        led = tmp_path / "elsewhere"
    led.mkdir()
    (ours / inner).symlink_to(led)
    editors = Editors(tmp_path / "e", extension=True)
    since = len(machine.calls())
    ran = _code(made, editors, "delta", "--editor", "zed")
    _ok(ran, 2)
    assert f"{ours}/{inner} on delta is a link inside code's own directory" in _said(ran)
    assert list(led.iterdir()) == [] and sorted(path.name for path in ours.iterdir()) == [inner]
    assert [name for name, _ in _sent(machine, since) if name == "file"] == []
    assert [each for each in editors.started() if each[1] != "--version"] == []


def test_a_stopped_vm_is_refused_with_the_line_that_starts_it_and_never_reached(made, tmp_path) -> None:
    machine = _ready(made)
    _pve(made.base, "POST", f"nodes/pve/qemu/{FIRST}/status/stop")
    since = len(machine.calls())
    said = _refused_before_any_write(made, machine, Editors(tmp_path / "e", extension=True), "delta", "--editor", "zed")
    assert "delta is stopped, so there is nothing to connect to: aw deploy delta --only provision --start" in said
    assert "run this again, aw code delta --editor zed." in said and len(machine.calls()) == since


def test_code_s_directory_inside_a_checkout_is_refused_before_the_alias_and_any_write(made, tmp_path) -> None:
    machine = _ready(made)
    ours = machine.home / "projects" / ".agent-warehouse"
    (ours / ".git").mkdir(parents=True)
    said = _refused_before_any_write(made, machine, Editors(tmp_path / "e", extension=True), "delta", "--editor", "zed")
    assert f"is inside the checkout {ours}" in said
    assert sorted(path.name for path in ours.iterdir()) == [".git"]


def test_a_folder_that_is_not_there_is_refused_with_the_projects_there(made, tmp_path) -> None:
    machine = _ready(made)
    (machine.home / "projects" / "App").mkdir()
    if (machine.home / "projects" / "app").exists():
        pytest.skip("this file system folds case (macOS's default), so the stand-in's app is its App, a folder there")
    editors = Editors(tmp_path / "e", extension=True)
    said = _refused_before_any_write(made, machine, editors, "delta", "--project", "app", "--editor", "zed")
    assert "There is no project named app in" in said and "Did you mean App?" in said


def test_the_managed_file_off_session_many_names_and_a_project_not_one_folder_are_refused(made, tmp_path) -> None:
    machine = _ready(made)
    editors = Editors(tmp_path / "e", extension=True)
    since = len(machine.calls())
    said = _refused_before_any_write(made, machine, editors, "delta", "--set", "claude_ssh_config=false")
    assert "claude_ssh_config is false, so this project keeps no SSH alias for the VM" in said
    session = "0f1e2d3c-4b5a-4968-8776-a5b4c3d2e1f0"
    said = _refused_before_any_write(made, machine, editors, "delta", "--session", session)
    assert "is not built yet (card G18 builds it)" in said
    assert "code opens one VM at a time" in _refused_before_any_write(made, machine, editors, "delta", "echo")
    assert "--project names one project folder" in _refused_before_any_write(
        made, machine, editors, "delta", "--project=.."
    )
    assert len(machine.calls()) == since


def test_at_a_terminal_the_window_is_drawn_by_its_steps_with_its_vm_and_opens(made, tmp_path) -> None:
    machine = _ready(made)
    editors = Editors(tmp_path / "e", extension=True)
    path = (made.root / SSH_FOLDER, editors.folder)
    rc, output = aw_at_a_terminal("code", "delta", "--editor", "zed", home=made.home, first_on_path=path, size=WINDOW,
                                  timeout=180)  # fmt: skip
    text = output.decode(errors="replace")
    assert rc == 0 and "the live view left out an event" not in text, text
    for label in ("find the VM", "reach it", "the folder", "alias", "the editor here", "the directory", "window"):
        assert label in text, label
    assert "delta" in text and editors.started()[-1] == ["zeditor", f"ssh://delta{machine.home / 'projects'}"]
    # A VM that is not there: said under the step it failed in, and nothing started.
    rc, output = aw_at_a_terminal("code", "gamma", "--editor", "zed", home=made.home, first_on_path=path, size=WINDOW,
                                  timeout=180)  # fmt: skip
    text = output.decode(errors="replace")
    assert rc == 1 and "find the VM" in text and "gamma" in text, text
    assert "the live view left out an event" not in text, text
    assert len(editors.started()) == 2
