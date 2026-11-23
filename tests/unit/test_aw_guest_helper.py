"""The helper, one script sent on a machine's standard input (lib/claude_vm/guest/bundle.py and guest/helper/;
docs/architecture/engine.md §16.10).

- Its sources: each parses as Python 3.10 and imports nothing but the standard library and the script's own modules;
  `sources()` reads today's three files of the probe where they are, whatever the working directory, and is the one
  function of lib/claude_vm/ outside status/ that names playbooks/module_utils (a scan, with its samples).
- The script: one text under 200 KB, itself Python 3.10, behind its mark's line with its length. Run by `python3 -I -B
  -c <bootstrap>` against today's stand-in VMs (tests/unit/test_status_vms.py's), it prints the bootstrap's first line,
  then byte for byte the document today's `reach.probe_script` prints for the same ask, and changes nothing there; sent
  through B3's runner to F30's stand-in machine, likewise. What comes before its mark's line is dropped and never
  executed; what comes after it is the verb's own standard input, read whole and never compiled. A verb it does not
  know, a word too many or an ask of another shape is one line and exit status 2; what cannot travel as JSON is refused
  before anything is made.
- Its floor, Python 3.10 (the owner's answer to review 25): the script run on a Python 3.10 - this computer's
  python3.10, or `python:3.10-slim` under docker where it is pulled (a lane), the test skipped and saying so where
  neither is - which parses every carried file with its own parser and finds every import in its own standard library;
  and ruff's rules that would write past 3.10 kept off the helper's folder.
- The invariant with a test of its own: the rule `just status` rests on holds over the whole bundle. At run time, the
  script started by a test-only bootstrap that first sets an audit hook (PEP 578) recording every program started,
  every file opened and every file-system write of the machine's Python: each program one of `ALLOWED`'s reads or
  git_checkouts.py's, each open without a flag that writes, no write. And statically, over each carried file: the
  functions that start a program named, with what each starts, those the probe never reaches among them; today's rule
  over the probe's own literal reads; git's reads by their subcommand; and every open a read.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import pwd
import shutil
import subprocess
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
import tomllib
from aw_machine import filed, machine, ssh_stand_in, target
from harness import REPO
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
# The bootstrap's first line, as a machine prints it.
STARTED = (bundle.STARTED + "\n").encode()
# Lines in front of the script, as sudo -S leaves a password's where it asks for none: the mark inside one, and carriage
# returns, which Python reads as line breaks, around a line that does not parse; the mark without its length, with
# one that is no number, behind a word; a line with no newline of its own; bytes that are no UTF-8. Executed, Python's
# SyntaxError would say one back.
BEFORE = (
    f"never said\r{bundle.MARK}\rx = (\r".encode(),
    f"{bundle.MARK}\nnever said = (\n".encode(),
    f"{bundle.MARK} 12x\nnever said = (\n".encode(),
    f"x{bundle.MARK} 12\nnever said = (\n".encode(),
    b"never said = (",
    b"never said \xff\xfe = (\n",
)
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
# The helper's floor: the oldest Python a machine's python3 may be (the owner's answer to review 25, F1.md 2), and where
# its interpreter is found where this computer has no python3.10 of its own: that image, already pulled, run with no
# network. On a lane: docker pull python:3.10-slim.
FLOOR = (3, 10)
IMAGE = "python:3.10-slim"
# ruff's rules that would write the helper's folder past FLOOR, which lib/**'s py313 would otherwise apply there.
PAST_THE_FLOOR = {"UP017", "B905"}

# Each function of a carried file that starts a program, by the module the script defines the file under, with what it
# starts: what the probe reaches, held at run time to ALLOWED's reads and git's (test_the_bundle_s_run_...), and what it
# never reaches, held there too, since none of it is a read either list names.
STARTING_FUNCTIONS = {
    (PROBE, "Commands.run_command"): "git's reads for git_checkouts.py, each of GIT_READS",
    (PROBE, "run"): "every read of ALLOWED but apt's, whose literal argument lists today's rule holds",
    (PROBE, "upgrades"): "apt-get's simulation, ALLOWED's first read",
    (PROBE, "probe"): "a fork of itself, as root alone, which becomes the machine's user for the user's reads:"
    " no program",
    ("ansible.module_utils.agent_warehouse", "process_argv"): "ps, one pid's argument list for the session modules:"
    " the probe never reaches it, and ps is no read of ALLOWED's",
}
# Functions of a carried file the probe never reaches, which start a program or run one through another, each with
# why: none of what they run is a read of ALLOWED's or GIT_READS', so a run that reached one would fail the audit.
NEVER_REACHED = {
    ("ansible.module_utils.agent_warehouse", "process_argv"): "ps, for running_claude",
    ("ansible.module_utils.agent_warehouse", "running_claude"): "whether a pid is a claude, for the session modules",
    ("ansible.module_utils.git_checkouts", "overwrites"): "git diff, for just code's fast-forward: the probe asks no"
    " target",
}
# git's reads as git_checkouts.py makes them for the probe, by subcommand, with the words a subcommand that also writes
# must be followed by (config, stash, worktree): test_status_work.py holds the work's. diff is overwrites', never the
# probe's.
GIT_READS: dict[str, tuple[str, ...] | None] = {
    "rev-parse": None,
    "config": ("--get", "--get-all"),
    "status": None,
    "symbolic-ref": None,
    "for-each-ref": None,
    "rev-list": None,
    "stash": ("list",),
    "worktree": ("list",),
}
NOT_THE_PROBES = {"diff": "overwrites', for just code's fast-forward"}
# The audit events the test's bootstrap records (PEP 578): each way a Python starts a program, opens a file, or writes
# to the file system.
STARTING = (
    "subprocess.Popen",
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
    "os.system",
    "os.fork",
    "os.forkpty",
    "os.startfile",
)
WRITING = (
    "os.remove",
    "os.rename",
    "os.rmdir",
    "os.mkdir",
    "os.symlink",
    "os.link",
    "os.chmod",
    "os.chown",
    "os.chflags",
    "os.truncate",
    "os.utime",
    "os.mkfifo",
    "os.mknod",
    "shutil.copyfile",
    "shutil.copymode",
    "shutil.copystat",
    "shutil.copytree",
    "shutil.move",
    "shutil.rmtree",
)
WATCHED = ("open", *STARTING, *WRITING)
# The flags of an open that may write.
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
# The properties the probe reads of Remote Control's units, as status/found.py and the probe name them.
UNIT_KEYS = ("LoadState", "ActiveState", "SubState", "NRestarts", "Result", "StateChangeTimestamp")


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


def _ran(argv: list[str], text: str | bytes, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    """argv run with text on its standard input, as a machine's python3 is sent a script; env, where given, its whole
    environment."""
    given = text.encode() if isinstance(text, str) else text
    return subprocess.run(argv, input=given, capture_output=True, timeout=120, check=False, env=env)


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


def _carried_probe() -> types.ModuleType:
    """The probe as the script carries it, made a module here: its imports are today's module_utils, which conftest.py
    puts on ansible.module_utils' path."""
    source = _carried(bundle.script(["probe"], {}))[PROBE]
    module = types.ModuleType("carried_status_probe")
    exec(compile(source, "carried_status_probe", "exec"), module.__dict__)
    return module


def _vm(tmp_path: Path, name: str) -> tuple[str, Path]:
    """One of today's stand-in VMs, made for the case name: its interpreter and its home."""
    hosts = _vms(tmp_path / "vms", {name: CASES[name]})
    return str(hosts[name]["ansible_python_interpreter"]), tmp_path / "vms" / name


# ---------------------------------------------------------------- its sources ----


def test_every_source_of_the_helper_is_python_3_10_and_imports_the_standard_library_alone() -> None:
    packed = bundle.sources()
    own = {*bundle.PACKAGES, *packed}
    assert list(packed) == [f"ansible.module_utils.{name}" for name in bundle.PROBE] + [bundle.DISPATCHER]
    for name, source in packed.items():
        ast.parse(source, feature_version=FLOOR)
        outside = [
            (level, module)
            for level, module in _imports(source)
            if level or (module not in own and module.split(".")[0] not in sys.stdlib_module_names)
        ]
        assert outside == [], (name, outside)
    # The helper's own files on disk, the package's __init__ too: none imports claude_vm, which is not on a machine.
    for path in sorted((LIB / "guest" / "helper").glob("*.py")):
        ast.parse(path.read_text(), feature_version=FLOOR)
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


def test_the_script_is_one_text_under_200_kb_behind_its_length_and_itself_python_3_10() -> None:
    text = bundle.script(["probe"], _asked(Path("/home/dev"), Path("/")))
    assert isinstance(text, str) and len(text.encode("utf-8")) < MOST, len(text.encode("utf-8"))
    # Its mark's line, after a newline, says the length of what follows it, to the byte.
    head, body = text.split("\n", 2)[1], text.split("\n", 2)[2]
    assert text.startswith("\n") and head == f"{bundle.MARK} {len(body.encode('utf-8'))}", head
    assert text.isascii() and bundle.frame(body) == text
    assert repr(bundle.LINE) in bundle.BOOTSTRAP and repr(bundle.STARTED) in bundle.BOOTSTRAP
    ast.parse(text, feature_version=FLOOR)
    ast.parse(bundle.BOOTSTRAP, feature_version=FLOOR)
    # Every source whole, as sources() read it, and nothing of the verb or the ask on the command line.
    assert list(_carried(text).items()) == list(bundle.sources().items())
    assert bundle.command() == ["/usr/bin/python3", "-I", "-B", "-c", bundle.BOOTSTRAP]
    assert "probe" not in bundle.BOOTSTRAP and "/home/dev" not in bundle.BOOTSTRAP


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_script_prints_today_s_document_on_today_s_stand_in_vms_and_changes_nothing(tmp_path, name) -> None:
    python, home = _vm(tmp_path, name)
    asked = _asked(home, home / "system")
    before = _files(tmp_path / "vms")
    new = _ran(bundle.command(python), bundle.script(["probe"], asked))
    assert (new.returncode, new.stderr) == (0, b""), new.stderr.decode()[-3000:]
    assert _files(tmp_path / "vms") == before
    old = _ran(_today(python), reach.probe_script(asked))
    assert (old.returncode, old.stderr) == (0, b""), old.stderr.decode()[-3000:]
    # The bootstrap's own line, then today's document, byte for byte.
    assert new.stdout == STARTED + old.stdout
    # A document of every read, none failed: two runs that failed alike would be equal too.
    lines = [json.loads(line) for line in new.stdout.decode().splitlines()]
    assert lines[:2] == [{"helper": "started"}, {"started": True}] and len(lines) == 3, lines
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


@pytest.mark.parametrize("before", BEFORE, ids=range(len(BEFORE)))
def test_what_comes_before_the_mark_s_line_is_dropped_never_executed(tmp_path, before) -> None:
    python, home = _vm(tmp_path, "ready")
    text = bundle.script(["probe"], _asked(home, home / "system")).encode()
    plain = _ran(bundle.command(python), text)
    behind = _ran(bundle.command(python), before + text)
    assert (plain.returncode, behind.returncode, behind.stderr) == (0, 0, b""), behind.stderr.decode()[-3000:]
    assert behind.stdout == plain.stdout
    assert b"never said" not in behind.stdout + behind.stderr


# A verb's body for the bootstrap alone (frame): what follows the script on standard input, written back whole.
ECHO = "import sys\nsys.stdout.flush()\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n"
# What a verb may be handed after the script: any bytes, a NUL, a line that is the mark's, and a line that is no Python.
AFTER = b"\x00binary\xff\n" + f"\n{bundle.MARK} 5\nnever said = (\n".encode() + b"no newline at its end"


def test_what_follows_the_script_is_the_verb_s_own_standard_input_read_whole() -> None:
    ran = _ran(bundle.command(sys.executable), bundle.frame(ECHO).encode() + AFTER)
    assert (ran.returncode, ran.stderr) == (0, b""), ran.stderr
    assert ran.stdout == STARTED + AFTER


def test_a_line_after_the_script_that_is_no_python_is_never_compiled_or_said() -> None:
    # The dispatcher's refusal, so that no probe runs: the line after the script is never Python's to say back.
    trailing = b"\nnever said carried file line = (\n"
    ran = _ran(bundle.command(sys.executable), bundle.script(["nosuch"], {}).encode() + trailing)
    assert (ran.returncode, ran.stdout) == (2, STARTED), ran
    assert ran.stderr.decode() == 'helper: no verb "nosuch": the verbs are probe\n'


@pytest.mark.parametrize(
    "given",
    [
        b"",
        b"print('never said')\n",
        f"\n{bundle.MARK} 999\nprint('never said')\n".encode(),
        f"\n{bundle.MARK} 0\n".encode(),
        f"\n{bundle.MARK}\nprint('never said')\n".encode(),
    ],
    ids=["nothing", "no mark", "shorter than its length", "empty", "no length"],
)
def test_no_whole_script_is_one_line_and_exit_2_with_nothing_executed(given: bytes) -> None:
    ran = _ran(bundle.command(sys.executable), given)
    assert (ran.returncode, ran.stdout) == (2, STARTED), ran
    assert ran.stderr.decode() == "helper: no whole script on standard input\n"


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
    assert (ran.returncode, ran.stdout, ran.stderr.decode()) == (2, STARTED, f"{said}\n")


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
    assert isinstance(old, runner.Done) and new.lines == (bundle.STARTED, *old.lines), (new, old)
    document = json.loads(new.lines[-1])
    assert document["user"]["claude"]["login"] is True and document["user"]["github"]["kind"] == "pat", document
    # The verb and what it was asked travel inside the script: the line ssh was given holds the bootstrap alone.
    first = alpha.calls()[0]
    assert first["argv"] == runner.argv(target(alpha), bundle.command(python))[1:]
    assert not [word for word in first["argv"] if "probe" in word or str(alpha.system) in word], first["argv"]


# ---------------------------------------------------------------- the floor, run ----


def _floor_python(empty: Path) -> tuple[list[str], dict[str, str] | None]:
    """The words that start a Python of FLOOR, the rest of a command line after them, its standard input passed
    through, and the environment they are run with: IMAGE's under docker with no network, whose machine has nothing of
    this computer's; or this computer's python3.10, with a PATH of empty alone, so that the probe finds none of this
    computer's programs, nor the test's stand-ins. The test skipped, saying how to give it one, where neither is."""
    docker = shutil.which("docker")
    if docker and subprocess.run([docker, "image", "inspect", IMAGE], capture_output=True, timeout=60).returncode == 0:
        return [docker, "run", "--rm", "-i", "--network", "none", IMAGE, "python3"], None
    found = shutil.which("python3.10")
    if found:
        said = subprocess.run([found, "-I", "-c", "import sys; print(sys.version_info[:2])"], capture_output=True)
        if said.stdout.decode().strip() == str(FLOOR):
            empty.mkdir()
            return [found], {"PATH": str(empty)}
    pytest.skip(f"no Python {FLOOR[0]}.{FLOOR[1]} here: docker with {IMAGE} pulled (a lane), or python3.10 on PATH")


# Run on the floor's Python, the sources on its standard input: each compiled by that Python's own parser, and each
# module it imports, anywhere in it, either its standard library's or one the script defines. Its findings, as JSON.
FLOOR_CHECK = """
import ast, json, sys
sources = json.load(sys.stdin)
own = {"ansible", "ansible.module_utils", *sources}
found = []
for name, source in sources.items():
    tree = compile(source, name, "exec", ast.PyCF_ONLY_AST)
    compile(source, name, "exec")
    for node in ast.walk(tree):
        names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
        if isinstance(node, ast.ImportFrom):
            names = [("." * node.level) + (node.module or "")]
        for module in names:
            if module not in own and module.split(".")[0] not in sys.stdlib_module_names:
                found.append([name, module])
print(json.dumps({"version": list(sys.version_info[:2]), "outside": found}))
"""


def test_the_helper_runs_on_python_3_10(tmp_path) -> None:
    floor, env = _floor_python(tmp_path / "empty")
    # Every carried file parses and compiles on the floor, and imports its standard library alone.
    checked = _ran([*floor, "-I", "-B", "-c", FLOOR_CHECK], json.dumps(bundle.sources()), env)
    assert (checked.returncode, checked.stderr) == (0, b""), checked.stderr.decode()[-3000:]
    assert json.loads(checked.stdout) == {"version": list(FLOOR), "outside": []}
    on_floor = [*floor, *bundle.command("python3")[1:]]
    # The probe, as root on a machine with nothing of the product's, its reads of what is there: one whole document.
    asked = {**_asked(Path("/nonexistent"), Path("/")), "user": "root"}
    probed = _ran(on_floor, bundle.script(["probe"], asked), env)
    assert (probed.returncode, probed.stderr) == (0, b""), probed.stderr.decode()[-3000:]
    lines = [json.loads(line) for line in probed.stdout.decode().splitlines()]
    assert lines[:2] == [{"helper": "started"}, {"started": True}] and len(lines) == 3, lines
    assert set(lines[-1]["user"]) == {"system", "claude", "github", "work"} and set(lines[-1]["root"]) == {"tailscale"}
    # What follows the script is the verb's, whole; a verb it does not know is refused in one line.
    echoed = _ran(on_floor, bundle.frame(ECHO).encode() + AFTER, env)
    assert (echoed.returncode, echoed.stdout, echoed.stderr) == (0, STARTED + AFTER, b"")
    refused = _ran(on_floor, bundle.script(["nosuch"], {}).encode() + b"\nnever said = (\n", env)
    assert (refused.returncode, refused.stdout) == (2, STARTED)
    assert refused.stderr.decode() == 'helper: no verb "nosuch": the verbs are probe\n'


def test_ruff_keeps_the_rules_that_write_past_the_floor_off_the_helper_s_folder() -> None:
    lint = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["ruff"]["lint"]
    assert PAST_THE_FLOOR <= set(lint["per-file-ignores"]["lib/claude_vm/guest/helper/**"])
    assert {"B", "UP"} <= set(lint["select"])


# ---------------------------------------------------------------- the rule status rests on, over the whole bundle ----


def _audited(record: Path) -> str:
    """BOOTSTRAP behind a first part of the test's own: an audit hook (PEP 578) that writes each event of WATCHED, with
    what it names, as a JSON line to record, opened before the hook is set."""
    return (
        "import json, os, sys; "
        f"_fd = os.open({str(record)!r}, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600); "
        f"_watched = frozenset({list(WATCHED)!r}); "
        "sys.addaudithook(lambda event, args: event in _watched"
        " and os.write(_fd, (json.dumps([event, args], default=repr) + chr(10)).encode())); "
        f"{bundle.BOOTSTRAP}"
    )


def _a_read(argv: list) -> bool:
    """Whether argv is one of ALLOWED's reads (a None there any word) or one of git's reads as git_checkouts.py makes
    them."""
    allowed = {tuple(one) for one in _carried_probe().ALLOWED}
    sized = [one for one in allowed if len(one) == len(argv)]
    if any(all(w is None or w == a for w, a in zip(one, argv, strict=True)) for one in sized):
        return True
    if argv[:3] != ["git", "--no-optional-locks", "-C"] or len(argv) < 5 or argv[4] not in GIT_READS:
        return False
    followed = GIT_READS[argv[4]]
    return followed is None or (len(argv) > 5 and argv[5] in followed)


def _events(record: Path) -> list[list]:
    """What the audit hook wrote, an [event, arguments] pair a line."""
    return [json.loads(line) for line in record.read_text().splitlines()]


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_bundle_s_run_starts_only_allowed_reads_and_opens_every_file_to_read(tmp_path, name) -> None:
    python, home = _vm(tmp_path, name)
    record = tmp_path / "audit.log"
    before = _files(tmp_path / "vms")
    ran = _ran([python, "-I", "-B", "-c", _audited(record)], bundle.script(["probe"], _asked(home, home / "system")))
    assert (ran.returncode, ran.stderr) == (0, b""), ran.stderr.decode()[-3000:]
    assert _files(tmp_path / "vms") == before
    events = _events(record)
    programs = [list(args[1]) for event, args in events if event == "subprocess.Popen"]
    # The hook saw the run: gh's read on every VM, and the files of the VM's system read.
    assert ["gh", "auth", "status", "--hostname", "github.com", "--json", "hosts"] in programs, programs
    opened = [str(args[0]) for event, args in events if event == "open"]
    assert any(path.startswith(str(home / "system")) for path in opened), opened
    assert [argv for argv in programs if not _a_read(argv)] == []
    if name == "fresh":
        assert any(argv[:3] == ["git", "--no-optional-locks", "-C"] for argv in programs), programs
    writes = [
        (event, args)
        for event, args in events
        if event == "open" and (not isinstance(args[2], int) or args[2] & WRITE_FLAGS)
    ]
    assert writes == []
    others = [(event, args) for event, args in events if event not in ("open", "subprocess.Popen")]
    assert [one for one in others if one[0] != "os.posix_spawn" or not _a_read(list(one[1][1]))] == []


@pytest.mark.parametrize(
    ("argv", "read"),
    [
        (["gh", "auth", "token", "--hostname", "github.com"], True),
        (["systemctl", "show", "claude-remote-control.service", "-p", ",".join(UNIT_KEYS)], True),
        (["systemctl", "show", "claude-remote-control.service", "-p", "ActiveState"], False),
        (["systemctl", "start", "claude-remote-control.service"], False),
        (["tailscale", "up"], False),
        (["ps", "-o", "args=", "-p", "1"], False),
        (["git", "--no-optional-locks", "-C", "/p", "status", "--porcelain"], True),
        (["git", "--no-optional-locks", "-C", "/p", "config", "--get-all", "remote.origin.url"], True),
        (["git", "--no-optional-locks", "-C", "/p", "config", "user.name", "x"], False),
        (["git", "--no-optional-locks", "-C", "/p", "stash", "list"], True),
        (["git", "--no-optional-locks", "-C", "/p", "stash", "pop"], False),
        (["git", "--no-optional-locks", "-C", "/p", "worktree", "add", "x"], False),
        (["git", "--no-optional-locks", "-C", "/p", "diff", "--name-status"], False),
        (["git", "-C", "/p", "status"], False),
    ],
)
def test_a_read_is_one_of_allowed_s_or_git_s(argv: list[str], read: bool) -> None:
    assert _a_read(argv) is read


def _qualified(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """Each function of tree by its dotted name within the file (Class.method), with its node."""
    found: list[tuple[str, ast.AST]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((f"{prefix}{child.name}", child))
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")

    visit(tree, "")
    return found


def _starting_functions(source: str) -> set[str]:
    """The innermost function of source (Class.method, '' outside one) around each call that starts a program."""
    found: set[str] = set()

    def visit(node: ast.AST, function: str, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, f"{prefix}{child.name}", f"{prefix}{child.name}.")
            elif isinstance(child, ast.ClassDef):
                visit(child, function, f"{prefix}{child.name}.")
            else:
                if isinstance(child, ast.Call) and _starts(ast.unparse(child.func)):
                    found.add(function)
                visit(child, function, prefix)

    visit(ast.parse(source), "", "")
    return found


def test_each_carried_file_starts_a_program_only_where_it_is_named() -> None:
    carried = _carried(bundle.script(["probe"], {}))
    starting = {(module, function) for module, source in carried.items() for function in _starting_functions(source)}
    assert starting == set(STARTING_FUNCTIONS)
    defined = {(module, name) for module, source in carried.items() for name, _ in _qualified(ast.parse(source))}
    assert set(NEVER_REACHED) <= defined
    # The probe names none of what it never reaches, and asks git_checkouts.read no target, which overwrites is for.
    probe = carried[PROBE]
    assert [name for _, name in NEVER_REACHED if name in probe] == []
    assert 'read_checkout(commands, path, False, "", None, "", True)' in probe


def test_today_s_rule_holds_the_probe_s_literal_reads_and_git_s_by_subcommand() -> None:
    carried = _carried(bundle.script(["probe"], {}))
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
    # git's: each subcommand git_checkouts.py writes out, with the word after it where the subcommand also writes.
    git = ast.parse(carried["ansible.module_utils.git_checkouts"])
    subcommands = {}
    for node in ast.walk(git):
        if isinstance(node, ast.Call) and _dotted(node.func) == "_git" and len(node.args) > 2:
            sub = node.args[2]
            assert isinstance(sub, ast.Constant), ast.unparse(node)
            after = node.args[3].value if len(node.args) > 3 and isinstance(node.args[3], ast.Constant) else None
            subcommands.setdefault(sub.value, set()).add(after)
    assert set(subcommands) == set(GIT_READS) | set(NOT_THE_PROBES), subcommands
    for sub, followed in GIT_READS.items():
        assert followed is None or subcommands[sub] <= set(followed), (sub, subcommands[sub])
    # The dispatcher and the script's own lines start nothing.
    assert _starts(carried[bundle.DISPATCHER]) == []
    assert _starts(bundle.script(["probe"], {})) == []


def test_each_carried_file_opens_every_file_to_read() -> None:
    for module, source in _carried(bundle.script(["probe"], {})).items():
        for node in _calls(source, "open"):
            if getattr(node.func, "attr", "") == "fdopen":
                continue
            assert _dotted(node.func) == "open", (module, ast.unparse(node))
            modes = [arg.value for arg in node.args[1:2] if isinstance(arg, ast.Constant)] + [
                k.value.value for k in node.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)
            ]
            assert len(node.args) <= 2 and all(mode in ("r", "rb") for mode in modes), (module, ast.unparse(node))
            assert not [k.arg for k in node.keywords if k.arg == "mode" and not isinstance(k.value, ast.Constant)]
        writing = [
            ast.unparse(node)
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Attribute)
            and _dotted(node)
            in {
                f"os.{name}"
                for name in (
                    "remove",
                    "unlink",
                    "rename",
                    "replace",
                    "mkdir",
                    "makedirs",
                    "rmdir",
                    "chmod",
                    "chown",
                    "truncate",
                    "utime",
                )
            }
        ]
        assert writing == [], (module, writing)


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
