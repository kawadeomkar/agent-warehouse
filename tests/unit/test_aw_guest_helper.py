"""The helper, one script sent on a machine's standard input (lib/claude_vm/guest/bundle.py and guest/helper/;
docs/architecture/engine.md §16.10).

- Its sources: each parses as Python 3.9 and imports nothing but the standard library and the script's own modules;
  `sources()` reads today's three files of the probe where they are, whatever the working directory, and is the one
  function of lib/claude_vm/ outside status/ that names playbooks/module_utils (a scan, with its samples).
- The script: one text under 200 KB, itself Python 3.9. Run by `python3 -I -B -c <bootstrap>` against today's stand-in
  VMs (tests/unit/test_status_vms.py's), it prints byte for byte the document today's `reach.probe_script` prints for
  the same ask, and changes nothing there; sent through B3's runner to F30's stand-in machine, likewise. What comes
  before its mark is dropped unread. A verb it does not know, a word too many or an ask of another shape is one line
  and exit status 2; what cannot travel as JSON is refused before anything is made.
- The invariant with a test of its own: `ALLOWED` still bounds every program the bundled probe runs. Today's rule
  (test_status_vms.py's) is run over the copy the script carries, the dispatcher and the script's own lines start
  none, and the stand-ins' records of a run of the script hold only `ALLOWED`'s reads.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import pwd
import subprocess
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
from aw_machine import filed, machine, ssh_stand_in, target
from harness import GH_CALLS, REPO
from test_status_vms import CASES, EDITORS, _calls, _files, _vms

from claude_vm.fleet import paths
from claude_vm.guest import bundle
from claude_vm.runs import job as jobs
from claude_vm.ssh import runner
from claude_vm.status import reach

LIB = REPO / "lib" / "claude_vm"
UTILS = REPO / "playbooks" / "module_utils"
PROBE = "ansible.module_utils.status_probe"
# The bound the card sets on the script's size.
MOST = 200 * 1024
# A sudo password's line, as sudo -S leaves it in front of the script where it asks for none: the mark inside it, and
# carriage returns, which Python reads as line breaks, around a line that does not parse. Executed, Python's
# SyntaxError would say it back.
BEFORE = f"never said\r{bundle.MARK}\rx = (\r"
# What names a program a script's own lines or the dispatcher could start, by the dotted name a call is read as.
STARTERS = (
    "subprocess",
    "os.system",
    "os.popen",
    "os.exec",
    "os.spawn",
    "os.posix_spawn",
    "os.fork",
    "pty",
    "multiprocessing",
    "webbrowser",
    "Popen",
)


@pytest.fixture
def job(home: Path) -> Iterator[jobs.Job]:
    """A job of this process's own, as a run that starts the runner's children has; dropped after the test."""
    made = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "status", "status alpha", None, ["alpha"], {"kind": "terminal"}, True
    )
    yield made
    jobs.drop(made)


def _asked(home: Path, system: Path) -> dict:
    """What today's just status asks a VM's probe for, in status/settings.py's ASKED shape, for a stand-in whose home
    and system files are home and system: every read, but apt's simulation, which would run this computer's apt."""
    return {
        "user": pwd.getpwuid(os.getuid()).pw_name,
        "limit": 25,
        "system": {"root": str(system), "upgrades": False, "timeout": 5, "editors": EDITORS, "known": {}},
        "claude": {"unit": "claude-remote-control"},
        "github": {
            "marker": str(home / ".config" / "agent-warehouse" / "gh-login"),
            "marker_1x": str(home / ".config" / "claude-on-proxmox" / "gh-login"),
        },
        "work": {"dir": str(home / "projects")},
        "tailscale": {
            "wanted": True,
            "foreign": [str(home / "foreign" / "tailscaled")],
            "marker": str(home / "markers" / "tailscale-installed"),
            "marker_1x": "",
            "joined": str(home / "markers" / "tailscale-joined"),
            "joined_1x": "",
        },
    }


def _ran(argv: list[str], text: str) -> subprocess.CompletedProcess[bytes]:
    """argv run with text on its standard input, as a machine's python3 is sent a script."""
    return subprocess.run(argv, input=text.encode(), capture_output=True, timeout=120, check=False)


def _today(python: str) -> list[str]:
    """How today's just status runs its probe on a VM: reach.py's own words."""
    return [python, "-I", "-B", "-c", reach.BOOTSTRAP]


def _carried(text: str) -> dict[str, str]:
    """The sources a script carries, by the name it defines each under, read back out of its text."""
    loops = [
        node
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.For) and isinstance(node.target, ast.Tuple) and isinstance(node.iter, ast.Call)
    ]
    assert len(loops) == 1, ast.dump(ast.parse(text))
    (literal,) = loops[0].iter.args
    return dict(json.loads(literal.value))


def _dotted(node: ast.AST) -> str:
    """A name or an attribute as its dotted text, '' for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else ""
    return ""


def _starts(source: str) -> list[str]:
    """What in source names a way to start a program: an import of one, or a name or attribute read as one."""
    found = []
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or "", *(f"{node.module}.{alias.name}" for alias in node.names)]
        elif isinstance(node, (ast.Name, ast.Attribute)):
            names = [_dotted(node)]
        found += [name for name in names if any(name == s or name.startswith((f"{s}.", s)) for s in STARTERS)]
    return found


def _imports(source: str) -> list[tuple[int, str]]:
    """Each import of source, by its level (0, absolute) and its module's whole name."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [(0, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append((node.level, node.module or ""))
    return found


def _only_allowed_reads(home: Path) -> dict[str, list[str]]:
    """What the stand-ins of the VM whose home is home recorded, by program: no call but a read the probe's ALLOWED
    lists, systemctl's with the unit it names."""
    allowed = {tuple(one) for one in _carried_probe().ALLOWED}
    logs = {
        "gh": home.joinpath(*GH_CALLS),
        "tailscale": home / ".tailscale-fake" / "calls.log",
        "systemctl": home / "systemctl.log",
    }
    called = {program: log.read_text().splitlines() if log.exists() else [] for program, log in logs.items()}
    for program in ("gh", "tailscale"):
        listed = {" ".join(argv[1:]) for argv in allowed if argv[0] == program}
        assert set(called[program]) <= listed, (program, called[program])
    units = [argv for argv in allowed if argv[0] == "systemctl"]
    for line in called["systemctl"]:
        words = line.split()
        assert any(words[:1] == [argv[1]] and words[2:] == list(argv[3:]) for argv in units), line
    return called


def _carried_probe() -> types.ModuleType:
    """The probe as the script carries it, made a module here: its imports are today's module_utils, which conftest.py
    puts on ansible.module_utils' path."""
    source = _carried(bundle.script(["probe"], {}))[PROBE]
    module = types.ModuleType("carried_status_probe")
    exec(compile(source, "carried_status_probe", "exec"), module.__dict__)
    return module


# ---------------------------------------------------------------- its sources ----


def test_every_source_of_the_helper_is_python_3_9_and_imports_the_standard_library_alone() -> None:
    packed = bundle.sources()
    own = {*bundle.PACKAGES, *packed}
    assert list(packed) == [f"ansible.module_utils.{name}" for name in bundle.PROBE] + [bundle.DISPATCHER]
    for name, source in packed.items():
        ast.parse(source, feature_version=(3, 9))
        outside = [
            (level, module)
            for level, module in _imports(source)
            if level or (module not in own and module.split(".")[0] not in sys.stdlib_module_names)
        ]
        assert outside == [], (name, outside)
    # The helper's own files on disk, the package's __init__ too: none imports claude_vm, which is not on a machine.
    for path in sorted((LIB / "guest" / "helper").glob("*.py")):
        ast.parse(path.read_text(), feature_version=(3, 9))
        assert [module for _, module in _imports(path.read_text()) if module.split(".")[0] == "claude_vm"] == []


def test_the_probe_s_sources_are_today_s_files_read_where_they_are_whatever_the_working_directory(
    tmp_path, monkeypatch
) -> None:
    # The same three, in the same order, as today's bundler packs: reach.probe_script is held equal to this one.
    assert bundle.PROBE == reach.SOURCES
    monkeypatch.chdir(tmp_path)
    packed = bundle.sources()
    for name in bundle.PROBE:
        assert packed[f"ansible.module_utils.{name}"] == (UTILS / f"{name}.py").read_text(encoding="utf-8")
        assert packed[f"ansible.module_utils.{name}"] == (reach.UTILS / f"{name}.py").read_text(encoding="utf-8")
    assert packed[bundle.DISPATCHER] == (LIB / "guest" / "helper" / "main.py").read_text(encoding="utf-8")
    assert bundle.CHECKOUT == REPO.resolve()


def _module_utils_named(source: str) -> list[str]:
    """The function (or '' outside one) of each string source builds that names the folder module_utils as a part of a
    path - alone, as one of the parts a path is joined from, or within one - never a docstring or a string standing as
    a statement, and never a module's dotted name, ansible.module_utils."""
    found: list[str] = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Expr) and isinstance(node.value, (ast.Constant, ast.JoinedStr)):
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "module_utils" in node.value.replace(os.sep, "/").split("/"):
                found.append(function)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(ast.parse(source), "")
    return found


def test_sources_is_the_one_function_outside_status_that_names_playbooks_module_utils() -> None:
    naming: dict[str, set[str]] = {}
    for path in sorted(LIB.rglob("*.py")):
        where = path.relative_to(REPO).as_posix()
        if where.startswith("lib/claude_vm/status/"):
            continue
        for function in _module_utils_named(path.read_text()):
            naming.setdefault(where, set()).add(function)
    assert naming == {"lib/claude_vm/guest/bundle.py": {"sources"}}
    # status/ is today's: reach.py's bundler, which this one stands beside until the cutover.
    assert _module_utils_named((LIB / "status" / "reach.py").read_text()) == [""]


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ('UTILS = ROOT / "playbooks" / "module_utils"', [""]),
        ('def f():\n    return "playbooks/module_utils/status_probe.py"', ["f"]),
        ('def g(top):\n    return f"{top}/module_utils/x.py"', ["g"]),
        ('class C:\n    def h(self):\n        return Path("module_utils")', ["h"]),
        ('NAME = "ansible.module_utils.status_probe"', []),
        ('def i():\n    """Reads playbooks/module_utils."""', []),
        ('"playbooks/module_utils"', []),
    ],
)
def test_the_scan_sees_each_way_to_name_the_folder(source: str, found: list[str]) -> None:
    assert _module_utils_named(source) == found


# ---------------------------------------------------------------- the script ----


def test_the_script_is_one_text_under_200_kb_and_itself_python_3_9() -> None:
    text = bundle.script(["probe"], _asked(Path("/home/dev"), Path("/")))
    assert isinstance(text, str) and len(text.encode("utf-8")) < MOST, len(text.encode("utf-8"))
    assert text.startswith(bundle.START) and repr(bundle.START) in bundle.BOOTSTRAP
    ast.parse(text, feature_version=(3, 9))
    ast.parse(bundle.BOOTSTRAP, feature_version=(3, 9))
    # Every source whole, as sources() read it, and nothing of the verb or the ask on the command line.
    assert list(_carried(text).items()) == list(bundle.sources().items())
    assert bundle.command() == ["/usr/bin/python3", "-I", "-B", "-c", bundle.BOOTSTRAP]
    assert "probe" not in bundle.BOOTSTRAP and "/home/dev" not in bundle.BOOTSTRAP


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_script_prints_today_s_document_on_today_s_stand_in_vms_and_changes_nothing(tmp_path, name) -> None:
    hosts = _vms(tmp_path / "vms", {name: CASES[name]})
    home = tmp_path / "vms" / name
    python = str(hosts[name]["ansible_python_interpreter"])
    asked = _asked(home, home / "system")
    before = _files(tmp_path / "vms")
    new = _ran(bundle.command(python), bundle.script(["probe"], asked))
    assert (new.returncode, new.stderr) == (0, b""), new.stderr.decode()[-3000:]
    assert _files(tmp_path / "vms") == before
    called = _only_allowed_reads(home)
    # Each read was made: gh's and systemctl's on every VM, Tailscale's where its package is there and wanted.
    assert "auth status --hostname github.com --json hosts" in called["gh"] and len(called["systemctl"]) == 2, called
    assert "status --json" in called["tailscale"], called
    old = _ran(_today(python), reach.probe_script(asked))
    assert (old.returncode, old.stderr) == (0, b""), old.stderr.decode()[-3000:]
    assert new.stdout == old.stdout
    # A document of every read, none failed: two runs that failed alike would be equal too.
    lines = [json.loads(line) for line in new.stdout.decode().splitlines()]
    assert lines[0] == {"started": True} and len(lines) == 2, lines
    user, root = lines[-1]["user"], lines[-1]["root"]
    assert set(user) == {"system", "claude", "github", "work"} and set(root) == {"tailscale"}, lines[-1]
    assert not [part for part in (*user.values(), *root.values()) if part.get("failed")], lines[-1]
    assert user["system"]["os"] == "Ubuntu 24.04.3 LTS" and user["system"]["unread"] == {}, user["system"]
    expected = {
        "ready": (True, "pat", f"nodekey:{CASES['ready']['marker']}", 0),
        "fresh": (False, "", "", 1),
        "down": (True, "", "", 0),
    }[name]
    found = (user["claude"]["login"], user["github"].get("kind", ""), root["tailscale"]["joined"])
    assert (*found, len(user["work"]["checkouts"])) == expected, lines[-1]
    assert user["system"]["reboot_required"] is (name == "down")


def test_what_comes_before_the_mark_is_dropped_unread(tmp_path) -> None:
    hosts = _vms(tmp_path / "vms", {"ready": CASES["ready"]})
    home = tmp_path / "vms" / "ready"
    python = str(hosts["ready"]["ansible_python_interpreter"])
    text = bundle.script(["probe"], _asked(home, home / "system"))
    plain = _ran(bundle.command(python), text)
    behind = _ran(bundle.command(python), f"{BEFORE}\n{text}")
    assert (plain.returncode, behind.returncode, behind.stderr) == (0, 0, b""), behind.stderr.decode()[-3000:]
    assert behind.stdout == plain.stdout
    assert b"never said" not in behind.stdout + behind.stderr


@pytest.mark.parametrize(
    ("entry", "asked", "said"),
    [
        (["nosuch"], {}, 'helper: no verb "nosuch": the verbs are probe'),
        (["bad\nverb"], {}, 'helper: no verb "bad\\nverb": the verbs are probe'),
        (["probe", "more"], {}, "helper: probe takes no words"),
        (["probe"], ["user", "dev"], "helper: probe is asked for a JSON object"),
        (["probe"], None, "helper: probe is asked for a JSON object"),
    ],
)
def test_a_verb_it_does_not_take_is_one_line_and_exit_2_with_nothing_run(entry, asked, said) -> None:
    ran = _ran(bundle.command(sys.executable), bundle.script(entry, asked))
    assert (ran.returncode, ran.stdout, ran.stderr.decode()) == (2, b"", f"{said}\n")


@pytest.mark.parametrize(
    ("entry", "asked"),
    [
        ("probe", {}),
        ([], {}),
        (["probe", 1], {}),
        (("probe", b"x"), {}),
        (["probe"], {"limit": float("nan")}),
        (["probe"], {"editors": {"a", "b"}}),
    ],
)
def test_what_cannot_travel_as_a_verb_and_json_is_refused_before_anything_is_made(entry, asked) -> None:
    with pytest.raises(ValueError, match=r"\Athe helper's entry|\Aeach word|\Awhat the helper is asked is JSON"):
        bundle.script(entry, asked)


def _over_ssh(job: jobs.Job, at: runner.Target, command: list[str], text: str) -> runner.Result:
    return asyncio.run(runner.run(job, at, command, text, runner.LIMITS, dict(os.environ)))


def test_the_script_sent_through_the_runner_prints_today_s_document(tmp_path, engine_guard, job) -> None:
    root = tmp_path / "machines"
    alpha = machine(root, "alpha", **CASES["ready"])
    filed(alpha)
    engine_guard.first_on_path(ssh_stand_in(root, [alpha]))
    asked = _asked(alpha.home, alpha.system)
    # The machine's own interpreter as today's stand-ins have it: this checkout's Python, its HOME the machine's home.
    python = str(alpha.home / "python")
    new = _over_ssh(job, target(alpha), bundle.command(python), bundle.script(["probe"], asked))
    old = _over_ssh(job, target(alpha), _today(python), reach.probe_script(asked))
    assert isinstance(new, runner.Done) and (new.status, new.said) == (0, ""), new
    assert isinstance(old, runner.Done) and new.lines == old.lines, (new, old)
    document = json.loads(new.lines[-1])
    assert document["user"]["claude"]["login"] is True and document["user"]["github"]["kind"] == "pat", document
    # The verb and what it was asked travel inside the script: the line ssh was given holds the bootstrap alone.
    first = alpha.calls()[0]
    assert first["argv"] == runner.argv(target(alpha), bundle.command(python))[1:]
    assert not [word for word in first["argv"] if "probe" in word or str(alpha.system) in word], first["argv"]


# ---------------------------------------------------------------- ALLOWED bounds what it runs ----


def test_allowed_bounds_every_program_the_bundled_probe_runs() -> None:
    carried = _carried(text := bundle.script(["probe"], {}))
    source = carried[PROBE]
    # The copy is today's file, which test_status_vms.py and test_status_work.py hold; and today's rule, run over it.
    assert source == (UTILS / "status_probe.py").read_text(encoding="utf-8")
    probe = _carried_probe()
    allowed = {tuple(one) for one in probe.ALLOWED}
    literal, built = [], []
    for node in _calls(source, "run"):
        argv = node.args[0] if node.args else None
        if isinstance(argv, ast.List) and all(isinstance(element, ast.Constant) for element in argv.elts):
            literal.append(tuple(ast.literal_eval(argv)))
        elif argv is not None:
            built.append(ast.unparse(argv))
    assert literal and set(literal) <= allowed, set(literal) - allowed
    assert ("systemctl", "show", None, "-p", ",".join(probe.UNIT_KEYS)) in allowed
    assert set(built) == {
        "[APT_GET, '-s', '-q', '-o', 'Debug::NoLocking=1', 'dist-upgrade']",
        "['systemctl', 'show', name, '-p', ','.join(UNIT_KEYS)]",
        "args",
        "argv",
    }, built
    for argv in allowed:
        assert not {"up", "down", "set", "login", "logout", "start", "stop", "restart", "install", "remove"} & set(
            argv
        ), argv
    assert source.count("subprocess.run(") == 3 and "Popen" not in source and "os.system" not in source
    assert "process_argv" not in source and "running_claude" not in source
    # The utilities are today's files; what the dispatcher and the script's own lines add starts nothing.
    for name in ("agent_warehouse", "git_checkouts"):
        assert carried[f"ansible.module_utils.{name}"] == (UTILS / f"{name}.py").read_text(encoding="utf-8")
    assert _starts(carried[bundle.DISPATCHER]) == []
    # The script's own lines, the sources being strings there.
    assert _starts(text) == [] and "import json, sys, types" in text


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("import subprocess", ["subprocess"]),
        ("from subprocess import run", ["subprocess", "subprocess.run"]),
        ("os.system('x')", ["os.system"]),
        ("os.execvp('x', [])", ["os.execvp"]),
        ("import pty", ["pty"]),
        ("x = Popen", ["Popen"]),
        ("sys.modules['m'].main({})", []),
        ("exec(compile(s, 'n', 'exec'))", []),
    ],
)
def test_the_scan_sees_each_way_to_start_a_program(source: str, found: list[str]) -> None:
    assert sorted(set(_starts(source))) == sorted(found)
