"""The new engine's layout, and the rule between it and today's product (docs/architecture/engine.md §16.1).

The engine that replaces the playbooks at 3.0 is built beside them: ten subpackages of lib/claude_vm/ and tools/aw,
which runs them. The rule is one-way. Today's product never reaches the new engine, so nothing a person runs today
changes while it grows; and the new engine imports of today's lib/claude_vm/ only the modules REUSED names, each with
its reason, and those import in turn, all the way down, only REUSED's and REACHED's, so what it stands on is said in
one place. The cutover fills ALLOWED and then removes this file.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from aw_kit import run_aw
from harness import repo_files, repo_texts
from test_claude_vm import _core_modules

from claude_vm.cli.main import NOT_BUILT

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "lib"
AW = REPO / "tools" / "aw"

# The new engine's subpackages of lib/claude_vm/, and secret.py beside them.
NEW = ("schema", "cli", "engine", "pve", "ssh", "vendors", "runs", "fleet", "guest", "control")
NEW_MODULES = (*NEW, "secret")

# What the new engine imports of today's lib/claude_vm/: a module by its dotted name under claude_vm, or a package
# whole, each with why. A change that needs another adds it here, with its reason, in the same commit; never a module
# that imports Ansible when it is imported. Not here: records and refs (the table of verbs and the printer of a command
# take their place), ansible (today's engine of playbooks), and the rest of run - which the new engine's own code never
# imports, though REUSED's modules bring some of them in (REACHED).
REUSED = {
    "plugin": "tools/aw checks with plugin.own that the claude_vm imported is this checkout's",
    "store": "a file replaced atomically under a lock on its folder, for the fleet's files and the settings",
    "text": "what a machine printed made plain before a message quotes it",
    "trust": "which certificate makes the Proxmox API verify, for init's anchor and the client's pin",
    "proxmox": "a guest agent's output, a VM's tags, a refused token and a settings drift, read as today",
    "route": "how this machine reaches a host's VMs, and the hop's ssh under hard limits, for the SSH runner",
    "checks": "a guest agent's address, a name ssh would take for another host, a name Proxmox gives",
    "openpgp": "a vendor's detached signature checked here, for the verified downloads",
    "proc": "the processes under a run, and their end, a detached task worker's included",
    "golden": "a golden template's toolchain digest and key",
    "init": "what an answer to one of init's questions must look like, and its normal form",
    "wiring": "each role's argument_specs and the settings that reach it, for the role options passed through",
    "catalogue": "the choices a checkout makes once, the editor and the VPN, as rows of a list",
    "wire": "the one JSON form a reused record of today's leaves in",
    "claude_config": "what of this machine's Claude Code the verb code carries beside the sessions",
    "sessions": "which session the verb code carries to a machine, and which destroy saves",
    "vault": "today's vault read without Ansible, for the migration",
    "desktop": "the sign-ins' steps on this machine: its browser, and the run waiting for a sign-in",
    "run.next_steps": "NEXT STEPS by machine and matter, as today's runs say them",
    "status": "status's engine, its model and its lines, which aw status keeps",
    "tui": "every live output's region, its lines and the menu",
    "editors": "each editor's plan of a window",
    "hosts": "the kinds of host and where each host's files are",
    "mcp": "the agents' server, which moves onto aw",
    "vpn": "the VPNs' members",
}

# What REUSED's modules import in turn, all the way down, that REUSED does not name: a module the new engine loads
# through them and never imports itself, each with why. A change to today's code that brings in another adds it here,
# with its reason; never Ansible.
REACHED = {
    "records": "the checked records that catalogue, editors, hosts, status and vpn build theirs on, and refs on too",
    "refs": "a VM's name and reference and a NEXT STEPS line, which editors, status, vpn and run.model take from it",
    "run.model": "the run as a person reads it, which tui.run draws: reused only for the live drawing it serves",
    "run.recipes": "each drawn recipe's declaration, which tui.run and run.model read",
}

# Files of today's product that may name the new engine, each with why: empty until the cutover's changes fill it.
ALLOWED: dict[str, str] = {}

# The settings every ansible-playbook run of today's reads, where the engine's Ansible folder is never named: its
# child's own configuration names it.
TODAYS_CFG = REPO / "ansible.cfg"
# Today's product: what runs a playbook, a recipe or a tool, today's modules of lib/claude_vm/, TODAYS_CFG, and the
# Vagrantfile, which runs today's configure.yml.
TODAY = (
    "justfile",
    "deploy.yml",
    TODAYS_CFG.name,
    "Vagrantfile",
    "playbooks",
    "roles",
    "filter_plugins",
    "callback_plugins",
    "inventory",
    "tools",
)
NAMES_NEW = re.compile(
    rf"\bclaude_vm[./]({'|'.join(NEW_MODULES)})\b|\btools/aw(?![\w-])"
    rf"|^\s*from\s+claude_vm\s+import\s+[^\n]*\b({'|'.join(NEW_MODULES)})\b",
    re.M,
)


def _is_new(path: Path) -> bool:
    rel = path.relative_to(REPO)
    if rel.parts[:2] == ("lib", "claude_vm") and len(rel.parts) > 2:
        return rel.parts[2] in NEW or rel.parts[2] == "secret.py"
    return str(rel).startswith("tools/aw")


def _today_texts() -> list[tuple[Path, str]]:
    # Untracked files too, but for those git ignores: a file is held before its first commit.
    return [(path, text) for path, text in repo_texts(*TODAY, "lib/claude_vm", untracked=True) if not _is_new(path)]


def _is_python(path: Path, text: str) -> bool:
    return path.suffix == ".py" or (text.startswith("#!") and "python" in text.split("\n", 1)[0])


def _package(path: Path) -> str | None:
    """The package a file's relative imports start from, for a module of lib/; None for any other file."""
    if not path.is_relative_to(LIB):
        return None
    return ".".join(path.relative_to(LIB).with_suffix("").parts[:-1])


def _exists(module: str) -> bool:
    where = LIB / module.replace(".", "/")
    return where.with_suffix(".py").is_file() or (where / "__init__.py").is_file()


def _imported(tree: ast.AST, package: str | None, *, every: bool = False) -> list[str]:
    """Each module an import statement brings in, by its absolute name, at the top or inside a function: for `from a
    import b`, a.b where it is a module or a package, a where b is a name a holds - or with every, both, whatever is on
    disk. And a module named whole to import_module or __import__."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if package is None:
                    continue
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1] + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            for alias in node.names:
                module = f"{base}.{alias.name}"
                found += [base, module] if every else [module if _exists(module) else base]
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name in ("import_module", "__import__") and isinstance(node.args[0].value, str):
                found.append(node.args[0].value)
    return found


def _within(module: str, names: tuple[str, ...] | dict[str, str]) -> bool:
    """Whether module is claude_vm.<one of names>, or inside one of them."""
    parts = module.split(".")
    return parts[0] == "claude_vm" and any(".".join(parts[1:end]) in names for end in range(2, len(parts) + 1))


def _names_the_new_engine(path: Path, text: str) -> bool:
    """Whether a file of today's names tools/aw or imports a module of the new engine: a Python file read as a syntax
    tree (its imports, relative ones included; its strings; a filter's module handed to plugin.filters), any other as
    text."""
    if not _is_python(path, text):
        return bool(NAMES_NEW.search(text))
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return bool(NAMES_NEW.search(text))
    if any(_within(module, NEW_MODULES) for module in _imported(tree, _package(path), every=True)):
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and NAMES_NEW.search(node.value):
            return True
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", getattr(node.func, "id", "")) == "filters"
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
            and str(node.args[1].value).split(".")[0] in NEW_MODULES
        ):
            return True
    return False


def _outside_reused(path: Path, text: str) -> list[str]:
    """The modules of today's claude_vm a file of the new engine imports that REUSED does not name."""
    return [
        module
        for module in _imported(ast.parse(text), _package(path))
        if module.split(".")[0] == "claude_vm"
        and module != "claude_vm"
        and not _within(module, NEW_MODULES)
        and not _within(module, REUSED)
    ]


def _module_files(module: str) -> list[Path]:
    """What importing module runs: its own file, and the __init__.py of each package it is in."""
    parts = module.split(".")
    where = LIB.joinpath(*parts)
    own = where.with_suffix(".py") if where.with_suffix(".py").is_file() else where / "__init__.py"
    return [own, *(LIB.joinpath(*parts[:end], "__init__.py") for end in range(1, len(parts)))]


def _reused_modules() -> list[str]:
    """Every module of today's claude_vm that REUSED names: a module itself, or each module of a package whole."""
    found = []
    for name in REUSED:
        where = LIB / "claude_vm" / name.replace(".", "/")
        if where.is_dir():
            found += [".".join(file.relative_to(LIB).with_suffix("").parts) for file in sorted(where.rglob("*.py"))]
        else:
            found.append(f"claude_vm.{name}")
    return [module.removesuffix(".__init__") for module in found]


def _reached(starts: list[str]) -> dict[str, str]:
    """Each module of today's claude_vm that starts are or import in turn, all the way down, at the top of a file or
    inside a function, with the module that first brought it in (a start, itself). Not the new engine's modules, which
    its own scan reads; nor a package for its __init__.py alone, whose imports are followed all the same."""
    reached = {module: module for module in starts}
    queue = list(starts)
    while queue:
        module = queue.pop()
        for path in _module_files(module):
            for found in _imported(ast.parse(path.read_text()), _package(path)):
                if found.split(".")[0] == "claude_vm" and found != "claude_vm" and not _within(found, NEW_MODULES):
                    if found not in reached:
                        reached[found] = module
                        queue.append(found)
    return reached


def _unnamed(starts: list[str], reached_names: dict[str, str]) -> dict[str, str]:
    """What starts bring in, all the way down, that neither REUSED nor reached_names names, with what brought it in."""
    return {
        module: by
        for module, by in _reached(starts).items()
        if not _within(module, REUSED) and not _within(module, reached_names)
    }


# ---------------------------------------------------------------- the layout ----


@pytest.mark.parametrize("name", NEW)
def test_each_subpackage_is_a_package_that_says_what_it_holds_and_imports(name) -> None:
    init = LIB / "claude_vm" / name / "__init__.py"
    doc = ast.get_docstring(ast.parse(init.read_text())) or ""
    assert "import" in doc and "tests/unit/test_aw_layout.py" in doc, init


def test_the_import_cost_test_walks_every_new_subpackage() -> None:
    # test_claude_vm.py's parametrised test imports each in a fresh python -I and fails on ansible, yaml, asyncio or
    # subprocess loaded at import.
    assert {f"claude_vm.{name}" for name in NEW} <= set(_core_modules())


def test_aw_version_names_this_checkout(tmp_path, home) -> None:
    # From another working directory, by its absolute path, and from the checkout by a relative one: the line names
    # the checkout tools/aw is in, never the directory it was started from.
    for cwd, program in ((tmp_path, AW), (REPO, "tools/aw")):
        result = run_aw("--version", home=home, cwd=cwd, program=program)
        assert (result.returncode, result.stdout, result.stderr) == (0, f"aw from the checkout {REPO}\n", ""), cwd


@pytest.mark.parametrize("words", [(), ("deploy", "alpha"), ("--version", "--version"), ("help",), ("hunter2",)])
def test_aw_refuses_anything_else_in_one_sentence_that_repeats_nothing(tmp_path, home, words) -> None:
    result = run_aw(*words, home=home, cwd=tmp_path)
    assert (result.returncode, result.stdout, result.stderr) == (1, "", NOT_BUILT + "\n")
    assert "just" in NOT_BUILT and "not built" in NOT_BUILT


# ---------------------------------------------------------------- the rule between the two engines ----


def test_nothing_of_today_names_tools_aw_or_imports_the_new_engine() -> None:
    texts = _today_texts()
    read = {path for path, _ in texts}
    assert len(texts) > 300 and {REPO / "justfile", TODAYS_CFG, REPO / "Vagrantfile"} <= read, len(texts)
    found = sorted(str(path.relative_to(REPO)) for path, text in texts if _names_the_new_engine(path, text))
    assert [path for path in found if path not in ALLOWED] == [], "today's product must not reach the new engine"
    assert [path for path in ALLOWED if path not in found] == [], "it no longer does: take it out of ALLOWED"


def test_the_new_engine_imports_of_today_only_what_reused_names() -> None:
    new = [path for path in repo_files("lib/claude_vm", "tools", untracked=True) if _is_new(path)]
    assert AW in new and LIB / "claude_vm" / "cli" / "main.py" in new
    outside = {
        str(path.relative_to(REPO)): modules
        for path in new
        if _is_python(path, text := path.read_text()) and (modules := _outside_reused(path, text))
    }
    assert outside == {}, "add each to REUSED with its reason, or import it no more"


def test_what_reused_imports_in_turn_is_reused_or_reached() -> None:
    # REUSED names packages whole, and the engine may import any module of one: each is followed, all the way down.
    starts = _reused_modules()
    assert "claude_vm.status.engine" in starts and "claude_vm.run.next_steps" in starts, len(starts)
    reached = _reached(starts)
    assert _unnamed(starts, REACHED) == {}, "add each to REACHED with its reason, or stop REUSED's module importing it"
    gone = [name for name in REACHED if not any(_within(module, (name,)) for module in reached)]
    assert gone == [], "no module of REUSED brings it in any more: take it out of REACHED"
    # Never Ansible, by any road: today's claude_vm.ansible imports it when imported.
    assert not [module for module in reached if _within(module, ("ansible",))]


def test_reused_and_reached_name_modules_of_today_and_never_ansible() -> None:
    for names in (REUSED, REACHED):
        assert [name for name in names if not _exists(f"claude_vm.{name}")] == []
        assert [name for name in names if name.split(".")[0] in ("ansible", *NEW_MODULES)] == []
        assert all(reason.strip() for reason in names.values())
    assert set(REUSED).isdisjoint(REACHED)


@pytest.mark.parametrize(
    ("source", "where", "names"),
    [
        ("from ..engine import deploy", "lib/claude_vm/status/engine.py", True),
        ("from .. import cli", "lib/claude_vm/run/model.py", True),
        ("import claude_vm.runs.record", "playbooks/library/x.py", True),
        ("from claude_vm import secret", "filter_plugins/x.py", True),
        ("importlib.import_module('claude_vm.fleet')", "callback_plugins/x.py", True),
        ("plugin.filters(LIB, 'pve.client', ['x'])", "filter_plugins/x.py", True),
        ("COMMAND = ['tools/aw', 'deploy']", "lib/claude_vm/mcp/x.py", True),
        ("callback_plugins = callback_plugins:lib/claude_vm/guest/ansible", TODAYS_CFG.name, True),
        ('ansible.playbook_command = "python3 tools/aw deploy"', "Vagrantfile", True),
        ("callback_plugins = callback_plugins", TODAYS_CFG.name, False),
        ("from ..status import engine", "lib/claude_vm/ansible/x.py", False),
        ("from claude_vm.ansible import engine", "tools/x.py", False),
        ("from claude_vm import plugin  # not tools/aw", "tools/x.py", False),
    ],
)
def test_the_scan_of_today_sees_each_way_in(source, where, names) -> None:
    assert _names_the_new_engine(REPO / where, source) is names


@pytest.mark.parametrize(
    ("source", "outside"),
    [
        ("from claude_vm import plugin, store", []),
        ("from claude_vm.run import next_steps", []),
        ("from ..status import engine", []),
        ("from . import main\nfrom ..fleet import paths", []),
        ("from claude_vm.run import model", ["claude_vm.run.model"]),
        ("from claude_vm import records", ["claude_vm.records"]),
        ("def f():\n    import claude_vm.ansible.engine", ["claude_vm.ansible.engine"]),
        ("importlib.import_module('claude_vm.refs')", ["claude_vm.refs"]),
    ],
)
def test_the_scan_of_the_new_engine_sees_each_import(source, outside) -> None:
    assert _outside_reused(LIB / "claude_vm" / "cli" / "x.py", source) == outside


def test_an_import_of_a_reused_module_is_followed_into_what_it_imports() -> None:
    # vpn is reused whole, so the new engine may import vpn.model; vpn.model brings records and refs in with it, which
    # pass only by REACHED's reason for each.
    assert _outside_reused(LIB / "claude_vm" / "engine" / "x.py", "from claude_vm.vpn import model") == []
    assert _unnamed(["claude_vm.vpn.model"], REACHED) == {}
    without = {name: why for name, why in REACHED.items() if name not in ("records", "refs")}
    assert _unnamed(["claude_vm.vpn.model"], without) == {
        "claude_vm.records": "claude_vm.vpn.model",
        "claude_vm.refs": "claude_vm.vpn.model",
    }
    # Through today's relative imports, all the way down: tui.run brings run.model in, which brings refs, which brings
    # records.
    reached = {"claude_vm.run.model", "claude_vm.run.recipes", "claude_vm.refs", "claude_vm.records"}
    assert set(_unnamed(["claude_vm.tui.run"], {})) == reached
