"""The quick commands import nothing heavy (docs/architecture/engine.md §16.3).

A quick command answers at once: help, a refusal, and the reads, logs, cancel, host, settings and status. QUICK names
each command line and what it may import beyond the standard library and claude_vm: nothing, a YAML reader, or a YAML
reader and pyrage, for a read that opens the vault, one file encrypted whole. A line runs as a person runs it, through
the launcher at the top of a checkout the test makes, with a fleet folder of the test's own (AGENT_WAREHOUSE_HOME), and
fails where the run loaded any of HEAVY - pydantic, pydantic_core, Ansible, Jinja2, the MCP SDK, or claude_vm.engine,
the workflows - or anything beyond the standard library and claude_vm that its line does not give it, pyrage among
them. A line whose verb has no handler yet is skipped by name, its card named: it runs once that card fills the
handler. A refusal is held to say its one sentence, so that what it loads once it is said is counted too.

What a run loaded is read from sys.modules at its exit, never from python's -X importtime, which lists no module loaded
through importlib.import_module, as the dispatch loads a verb's handler. A hook of the test's own writes it: the
checkout's PYTHON is a virtualenv the test makes, whose site-packages hold the suite's own packages, added as site adds
them, and one module, imported by a .pth line that site runs at the interpreter's start, -I and all, which notes what
the start loaded and writes that and sys.modules at exit (atexit), for the run of the checkout's tools/aw alone, never
for the launcher's own questions to the Python. What the start loaded is the interpreter's, not the command's: a heavy
module there fails the line all the same, and the rest of it is no import of the command's.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import NamedTuple

import pytest
from aw_kit import ENTRY, LAUNCHER, run_launcher
from harness import REPO

from claude_vm.fleet import paths, record
from claude_vm.schema.words import EXITS, VERBS, Verb


class Quick(NamedTuple):
    """A quick command line: the words after aw; what it may import beyond the standard library and claude_vm; and
    whether it is a refusal, which runs whatever the table's handlers, where any other line waits for its verb's."""

    words: tuple[str, ...]
    may: frozenset[str] = frozenset()
    refusal: bool = False


# The YAML reader, which parses the fleet's files: its hosts', its settings', the overrides'.
READER = "yaml"
# What opens the vault, one file encrypted whole: settings --all, to say which secrets are set; status, for the token.
VAULT = "pyrage"
# A job's ID, as logs lists one, and a host's name, each as its pattern takes it.
JOB = "3f9a0c1e7b2d4a58"
HOST = "lab2"

# Each quick command line, in the card's order (H11), and what it may import beyond the standard library and claude_vm.
QUICK: tuple[Quick, ...] = (
    # aw alone, which is help, and help of one verb.
    Quick(()),
    Quick(("help",)),
    Quick(("help", "deploy")),
    # The checkout aw runs from: argparse, which main.py imports at its top, is the standard library's.
    Quick(("--version",)),
    # A refusal: an option the verb has not got, a value outside its pattern, a verb the table has not got, a rule's
    # sentence of help's, a rule between options, and a habit's, a second name where one is taken.
    Quick(("deploy", "--core", "8"), refusal=True),
    Quick(("deploy", "--cores=0"), refusal=True),
    Quick(("bogus",), refusal=True),
    Quick(("help", "deploy", "login"), refusal=True),
    Quick(("deploy", "--only=provision", "--tags=common"), refusal=True),
    Quick(("code", "alpha", "beta"), refusal=True),
    # The jobs: a text file read, or tailed.
    Quick(("logs",)),
    Quick(("logs", JOB)),
    Quick(("logs", JOB, "--follow")),
    Quick(("cancel",)),
    Quick(("cancel", JOB)),
    # The hosts, each a folder of files a YAML reader parses.
    Quick(("host",), frozenset({READER})),
    Quick(("host", "--json", "--for", "alpha"), frozenset({READER})),
    Quick(("host", HOST), frozenset({READER})),
    # The settings: a group, a name, what changed; and every setting, which says which secrets the vault holds.
    Quick(("settings",), frozenset({READER})),
    Quick(("settings", "tailscale"), frozenset({READER})),
    Quick(("settings", "vm_cores"), frozenset({READER})),
    Quick(("settings", "--changed"), frozenset({READER})),
    Quick(("settings", "--all"), frozenset({READER, VAULT})),
    # status, which opens the vault for the fleet's one token.
    Quick(("status", "--only", "vms"), frozenset({READER, VAULT})),
    Quick(("status",), frozenset({READER, VAULT})),
)

# What no quick command loads, each a module and everything under it: pydantic and its core, Ansible, Jinja2, the MCP
# SDK, and the engine's workflows.
HEAVY = ("pydantic", "pydantic_core", "ansible", "jinja2", "mcp", "claude_vm.engine")
# The hook's module, which the virtualenv's .pth imports at the interpreter's start.
HOOK_MODULE = "aw_imports_hook"
HOOK = '''"""What a run of tools/aw loaded: what the interpreter's start loaded, now, and sys.modules at its exit."""
import atexit
import os
import sys

_START = sorted(sys.modules)


def _write():
    if os.path.realpath(sys.argv[0]) != {program!r}:
        return
    loaded = sorted(sys.modules)
    import json

    with open(os.path.join({folder!r}, str(os.getpid()) + ".json"), "w") as out:
        json.dump({{"start": _START, "exit": loaded}}, out)


atexit.register(_write)
'''
# A fleet's ID, as fleet.json holds one.
FLEET = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
# The standard library's own data of sysconfig, a module whose name says the platform it was built for, which
# sys.stdlib_module_names leaves out.
SYSCONFIG_DATA = "_sysconfigdata_"


def _id(quick: Quick) -> str:
    return " ".join(quick.words) or "(no words)"


def _verb(words: tuple[str, ...]) -> Verb | None:
    """The verb a line runs: help for no words, none for a word the table has not got (--version, a refusal's)."""
    return VERBS["help"] if not words else VERBS.get(words[0])


def _top(name: str) -> str:
    return name.split(".")[0]


def heavy(loaded: frozenset[str]) -> list[str]:
    """The modules of loaded that HEAVY names, or that are under one it names."""
    return sorted(name for name in loaded if any(name == top or name.startswith(f"{top}.") for top in HEAVY))


def beyond(loaded: frozenset[str], start: frozenset[str]) -> set[str]:
    """The top-level packages of what a run loaded past its interpreter's start, beyond the standard library and
    claude_vm."""
    tops = {_top(name) for name in loaded - start} - set(sys.stdlib_module_names) - {"claude_vm"}
    return {top for top in tops if not top.startswith(SYSCONFIG_DATA)}


def _checkout(where: Path) -> Path:
    """A checkout the test makes: the launcher and the tools it runs, copied, this checkout's own program, and its
    lib/ by a link."""
    checkout = where / "checkout"
    (checkout / "tools").mkdir(parents=True)
    shutil.copy2(LAUNCHER, checkout / "aw")
    for tool in ("aw", "env-run"):
        shutil.copy2(REPO / "tools" / tool, checkout / "tools" / tool)
    (checkout / "lib").symlink_to(REPO / "lib")
    return checkout


def _python(where: Path, program: Path, records: Path) -> Path:
    """A virtualenv the test makes, and its python: its site-packages hold the suite's own packages, added as site adds
    them, and the hook, which writes into records what each run of program loaded."""
    venv = where / "venv"
    made = [sys.executable, "-m", "venv", "--without-pip", str(venv)]
    subprocess.run(made, check=True, timeout=120, capture_output=True)
    [site] = venv.glob("lib/python*/site-packages")
    suite = dict.fromkeys(sysconfig.get_path(kind) for kind in ("purelib", "platlib"))
    lines = [f"import site; site.addsitedir({path!r})" for path in suite] + [f"import {HOOK_MODULE}"]
    (site / f"{HOOK_MODULE}.pth").write_text("\n".join(lines) + "\n")
    (site / f"{HOOK_MODULE}.py").write_text(HOOK.format(program=str(program.resolve()), folder=str(records)))
    return venv / "bin" / "python"


def _fleet() -> None:
    """The test's own fleet, in the home AGENT_WAREHOUSE_HOME names: its folder, with the fleet's record."""
    record.write(paths.home(REPO), record.first(FLEET))


def modules_of(
    *words: str, home: Path, where: Path
) -> tuple[subprocess.CompletedProcess[str], frozenset[str], frozenset[str]]:
    """Run aw with words through the launcher of a checkout made in where, with the hook's python as PYTHON: the run,
    what its interpreters' start loaded, and every module loaded at its exit, for each process of the checkout's
    tools/aw the run made. A run that wrote none fails the test: nothing was read."""
    checkout = _checkout(where)
    records = where / "modules"
    records.mkdir()
    python = _python(where, checkout / "tools" / "aw", records)
    ran = run_launcher(*words, home=home, launcher=checkout / "aw", env={"PYTHON": str(python)})
    written = sorted(records.glob("*.json"))
    if not written:
        pytest.fail(f"the hook wrote nothing, so nothing was read of what the run loaded:\n{ran.stderr}")
    start: set[str] = set()
    loaded: set[str] = set()
    for each in written:
        document = json.loads(each.read_text())
        start |= set(document["start"])
        loaded |= set(document["exit"])
    return ran, frozenset(start), frozenset(loaded)


@pytest.mark.parametrize("quick", QUICK, ids=_id)
def test_a_quick_command_imports_nothing_heavy(home, tmp_path, quick: Quick) -> None:
    verb = _verb(quick.words)
    if not quick.refusal and verb is not None and verb.handler is None:
        pytest.skip(f"{verb.name} has no handler yet: card {verb.card} fills it, and this line runs then")
    _fleet()
    ran, start, loaded = modules_of(*quick.words, home=home, where=tmp_path)
    assert ENTRY in loaded, sorted(loaded)
    if quick.refusal:
        # Said, in one sentence, before the run's exit: what saying it loaded is counted.
        assert (ran.returncode, ran.stdout) == (EXITS["refused"], ""), ran.stderr
        assert ran.stderr.startswith("error: ") and len(ran.stderr.splitlines()) == 1, ran.stderr
    elif verb is None:
        assert ran.returncode == EXITS["done"] and ran.stdout.startswith("aw from the checkout "), ran.stderr
    else:
        # The verb's handler ran: what it loads is counted, never only what a refusal before it would.
        assert f"claude_vm.{verb.handler.partition(':')[0]}" in loaded, ran.stderr
    assert heavy(loaded) == []
    assert beyond(loaded, start) <= quick.may, sorted(beyond(loaded, start))
    assert VAULT not in {_top(name) for name in loaded} or VAULT in quick.may


def test_quick_runs_verbs_of_the_table_and_gives_the_vault_to_two_reads() -> None:
    # A line waits for its verb's row: a verb the table has not got would be skipped by nothing, and refused.
    assert all(quick.refusal or _verb(quick.words) is not None or quick.words == ("--version",) for quick in QUICK)
    verbs = {quick.words[0] for quick in QUICK if quick.words and not quick.refusal}
    assert verbs == {"help", "--version", "logs", "cancel", "host", "settings", "status"}
    assert all(quick.may <= {READER, VAULT} for quick in QUICK)
    vault = [quick.words for quick in QUICK if VAULT in quick.may]
    assert vault == [("settings", "--all"), ("status", "--only", "vms"), ("status",)]


def test_the_hook_lists_what_the_dispatch_loads_by_importlib_once_for_the_program_alone(home, tmp_path) -> None:
    # help.py is the dispatch's to import, by importlib, which -X importtime lists nothing of: the hook lists it.
    ran, start, loaded = modules_of("help", home=home, where=tmp_path)
    assert ran.returncode == EXITS["done"] and VERBS["help"].handler == "cli.help:main"
    assert {"claude_vm.cli.help", "claude_vm.cli.dispatch", ENTRY} <= loaded
    # The interpreter's start held the hook and nothing of the program's: it was in place before the program ran.
    assert HOOK_MODULE in start and [name for name in start if _top(name) == "claude_vm"] == []
    # The launcher asks the Python which interpreter it is, and tools/env-run whether it is a virtualenv's, each a
    # python -c, whose argv[0] is no program: the one record is the program's.
    assert len(list((tmp_path / "modules").glob("*.json"))) == 1


@pytest.mark.parametrize(
    ("loaded", "start", "found", "past"),
    [
        ({"json", "claude_vm.cli.main"}, set(), [], set()),
        ({"ansible.module_utils", "jinja2"}, set(), ["ansible.module_utils", "jinja2"], {"ansible", "jinja2"}),
        ({"claude_vm.engine.record", "claude_vm.cli"}, set(), ["claude_vm.engine.record"], set()),
        ({"mcp", "mcpx", "pydantic_core.core"}, set(), ["mcp", "pydantic_core.core"], {"mcp", "mcpx", "pydantic_core"}),
        ({"yaml", "_virtualenv"}, {"_virtualenv"}, [], {"yaml"}),
        ({"claude_vm.engineering", "_sysconfigdata__linux_x86_64-linux-gnu"}, set(), [], set()),
    ],
)
def test_heavy_and_beyond_read_a_module_by_its_name(loaded, start, found, past) -> None:
    # A module under one HEAVY names is heavy, and one whose name only begins as one does is not; what the
    # interpreter's start loaded is its own, and only past it is a package beyond the standard library and claude_vm
    # the command's.
    assert heavy(frozenset(loaded)) == found
    assert beyond(frozenset(loaded), frozenset(start)) == past
