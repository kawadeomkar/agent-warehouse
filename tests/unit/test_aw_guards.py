"""The new engine's tests and what keeps them off this machine (docs/architecture/testing.md §11.1).

Each test here is a rule a later change could break. Every test of a file tests/unit/test_aw_*.py is marked `engine`,
which `just test engine` runs alone, and runs with AGENT_WAREHOUSE_HOME in its own directory (conftest.py's aw_home). In
its own process, aw_kit's engine_guard keeps it as a run is kept, by a MonkeyPatch of its own that the test's
monkeypatch.undo() never reaches: its home for HOME and the XDG folders, failing stand-ins first on PATH, a list of
names for its environment, and loopback alone for its connections; a stand-in reached, a connection refused, or a PATH
of the test's own that finds this machine's programs ahead of the stand-ins fails it. A folder of the test's own
programs goes first only by first_on_path, held to the test's own directory, and a fixture of an engine file wider than
one test, which runs outside the guard, is named in WIDER_THAN_A_TEST with why. No test runs tools/aw but through
tests/unit/aw_kit.py, whose runs inherit that list of names, start with the stand-ins first on PATH, and fail their test
when one was reached; a test's env= never lifts one. A secret the engine holds is a `Secret`, never said, and only the
modules REVEALS names read its value.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import io
import json
import logging
import os
import pickle
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import aw_kit
import harness
import pytest
from aw_kit import AW, ENTRY, Offline, aw_at_a_terminal, environment, imports_of, run_aw, searched_for, stand_ins
from harness import PASSED_ON, REPO, repo_files

from claude_vm.fleet import paths
from claude_vm.secret import MARK, Secret

UNIT = REPO / "tests" / "unit"
# A 2.x test file, which tests/unit/conftest.py must leave unmarked: one that is cheap to collect.
UNMARKED = UNIT / "test_anchors.py"
SECRET = "unit-test-aw-secret-value"

# The modules that read a Secret's value, each with why: the one place it is sent on.
REVEALS: dict[str, str] = {
    "lib/claude_vm/vendors/transport.py": "a request's header or form value, a token's above all, read in _plain alone",
    "lib/claude_vm/ssh/runner.py": "a child's standard input, a password or a sign-in's code above all, read in _bytes"
    " alone and written to the child",
}

# The fixtures of the engine's test files whose scope is wider than one test, each with why it may be: pytest makes
# such a fixture before the guard of a test's own process holds and ends it after the guard lets go, so it runs with the
# developer's PATH, environment and home, and the network open (docs/architecture/testing.md §11.1).
WIDER_THAN_A_TEST: dict[str, str] = {
    "test_aw_fleet_paths.py::module_runs_environment": "harness._env of a directory of its own, what a module's run"
    " made before its tests is given: it starts nothing and reaches nothing",
    "test_aw_pve_agent.py::pki": "a certificate authority made in process with cryptography, for the fake Proxmox: it"
    " starts nothing and reaches nothing",
    "test_aw_pve_reads.py::pki": "a certificate authority made in process with cryptography, for the fake Proxmox: it"
    " starts nothing and reaches nothing",
    "test_aw_pve_reads.py::other_pki": "another such authority, of another subject, made the same way",
    "test_aw_pve_reads.py::misnamed_pki": "another such authority, whose node's certificate names another address than"
    " the fake's, made the same way",
    "test_aw_pve_writes.py::pki": "a certificate authority made in process with cryptography, for the fake Proxmox: it"
    " starts nothing and reaches nothing",
    "test_aw_transport.py::pki": "a certificate authority made in process with cryptography: it starts nothing and"
    " reaches nothing",
    "test_aw_transport.py::other_pki": "another such authority, made the same way",
    "test_aw_vendor_trust.py::pki": "a certificate authority made in process with cryptography: it starts nothing and"
    " reaches nothing",
    "test_aw_vendor_trust.py::other_pki": "another such authority, of another subject, made the same way",
    "test_aw_downloads.py::pki": "a certificate authority made in process with cryptography, for the fake download"
    " server's TLS: it starts nothing and reaches nothing",
    "test_aw_downloads.py::other_pki": "another such authority, of another subject, made the same way",
    "test_aw_downloads.py::signer": "an RSA key made in process with cryptography, the vendor's stand-in that signs a"
    " test's manifests and lists: it starts nothing and reaches nothing",
    "test_aw_downloads.py::stranger": "another such key, which is not the vendor's, made the same way",
    "test_aw_tailscale_api.py::pki": "a certificate authority made in process with cryptography, for the fake"
    " Tailscale's TLS: it starts nothing and reaches nothing",
    "test_aw_tailscale_api.py::other_pki": "another such authority, of another subject, made the same way",
}


def _engine_files() -> list[Path]:
    return [path for path in repo_files("tests/unit", untracked=True) if re.fullmatch(r"test_aw_\w+\.py", path.name)]


# ---------------------------------------------------------------- the marker and the home ----

# Loaded into a collection of its own (`pytest --collect-only -p`): each test it collected, the file it is in, whether
# it is marked engine, and which fixture gives it aw_home, at what scope.
PROBE = """
import json
import os


def pytest_collection_finish(session):
    seen = []
    for item in session.items:
        defs = item._fixtureinfo.name2fixturedefs.get("aw_home", ())
        seen.append(
            {
                "file": item.path.name,
                "engine": item.get_closest_marker("engine") is not None,
                "home": [defs[-1].func.__module__, defs[-1].scope] if defs else None,
            }
        )
    with open(os.environ["AW_PROBE"], "w") as out:
        json.dump(seen, out)
"""


def test_every_engine_test_is_marked_and_runs_in_its_own_home(tmp_path) -> None:
    # Collected as the suite collects them, by tests/unit/conftest.py's hook: every test_aw_*.py file on the branch,
    # those written before the marker included, and a 2.x file that must stay unmarked.
    files = _engine_files()
    assert UNIT / "test_aw_guards.py" in files and UNMARKED.exists(), files
    (tmp_path / "aw_probe.py").write_text(PROBE)
    env = {name: value for name, value in os.environ.items() if name in PASSED_ON or name.startswith("LC_")}
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-p", "aw_probe"]
        + ["-c", str(REPO / "pyproject.toml"), "--rootdir", str(REPO), *map(str, files), str(UNMARKED)],
        cwd=tmp_path,
        env={**env, "AW_PROBE": str(tmp_path / "seen.json")},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    seen = json.loads((tmp_path / "seen.json").read_text())
    assert {item["file"] for item in seen} == {path.name for path in files} | {UNMARKED.name}
    engine = [item for item in seen if item["file"] != UNMARKED.name]
    assert [item for item in engine if not item["engine"]] == [], "every test of a test_aw_*.py file is marked engine"
    assert [item for item in seen if item["file"] == UNMARKED.name and item["engine"]] == []
    # conftest.py's own aw_home, made for each test: never one a file defines over it, nor one shared by a module.
    homes = [item for item in engine if item["home"] != ["conftest", "function"]]
    assert homes == [], "use conftest.py's aw_home: never define one in a test file, nor give it another scope"


# The tests of today's suite that failed when the faults a short-set change can make were planted and the whole suite
# was run over them once, on a test machine (docs/architecture/testing.md §11.1): by fault, each test by its file and
# its name, every case of it. Each must be marked `tree` through conftest.py's TREE, so that just test --short runs it.
# Planted too, and failing nothing but the file map or a generated page: a new playbook, group_vars file, tool and
# scenario file, a new task file of a role, and a new engine test that tests/unit/durations.json does not name.
FOUND: dict[str, set[str]] = {
    "a pattern that ends in a dollar, held with match, in a new module": {
        "test_anchors.py::test_every_pattern_that_holds_a_value_ends_at_the_end_of_the_text",
        "test_claude_vm.py::test_a_vm_name_is_matched_whole_wherever_the_core_checks_one",
    },
    "a link to a page, and one to an anchor, that are not there": {
        "test_docs.py::test_the_site_builds_strictly",
        "test_docs.py::test_every_heading_reaches_its_built_page",
        "test_docs.py::test_every_page_renders_the_same_on_github_and_on_the_site",
        "test_docs.py::test_every_mermaid_fence_becomes_a_diagram",
    },
    "a page under docs/ that does not exist, named in a new module": {
        "test_docs.py::test_every_docs_page_the_repository_names_exists",
    },
    "a path that does not exist, in a code span of CLAUDE.md": {
        "test_docs.py::test_every_repository_path_in_a_code_span_is_a_file_or_one_git_ignores",
    },
    "a closing tag in a code span": {"test_docs.py::test_no_code_span_holds_a_closing_tag"},
    "a section cited with no link": {"test_docs.py::test_every_cross_page_section_reference_is_a_link"},
    "a page in no nav, and one at the top of docs/": {
        "test_docs.py::test_every_page_is_in_the_nav_and_every_nav_entry_is_a_page",
    },
    "a generated section edited by hand, and a new task file of a role": {
        "test_docs.py::test_generated_sections_are_up_to_date",
    },
    "a new file in tools/, tests/molecule/, filter_plugins/, playbooks/ and inventory/group_vars/all/": {
        "test_docs.py::test_the_file_map_names_every_file_of_the_folders_it_lists",
    },
    "an aw command line no verb takes, and a just one no recipe takes, on a page": {
        "test_commands.py::test_every_aw_command_named_is_one_the_table_accepts",
        "test_commands.py::test_every_just_command_named_is_one_the_justfile_accepts",
    },
    "a new core module that loads subprocess when imported": {
        "test_claude_vm.py::test_no_core_module_loads_ansible_yaml_asyncio_or_subprocess_when_imported",
    },
    "a second package in lib/": {"test_claude_vm.py::test_lib_holds_the_one_package_claude_vm"},
    "a filter plugin that loads the core when Ansible loads it": {
        "test_claude_vm.py::test_every_adapter_loads_no_core_module_and_each_filter_is_the_core_s_of_its_name",
    },
    "Proxmox's name for a machine's ID, VS Code and Zed, each named in a new module": {
        "test_ratchets.py::test_proxmox_is_read_outside_its_own_files_no_more_than_it_is_now",
        "test_ratchets.py::test_vs_code_is_named_outside_its_own_files_no_more_than_it_is_now",
        "test_ratchets.py::test_zed_is_named_outside_its_own_files_no_more_than_it_is_now",
    },
    "a new module that takes a machine's host key on first use (StrictHostKeyChecking=accept-new)": {
        "test_controller_writes.py::test_every_place_that_makes_a_row_s_write_is_one_of_its_sources",
    },
    "CLAUDE.md's words for one of the controller's writes changed": {
        "test_controller_writes.py::test_the_invariant_names_every_row",
    },
    "the security model's list of the controller's writes edited by hand": {
        "test_controller_writes.py::test_the_notes_and_the_security_model_read_the_list",
        "test_docs.py::test_generated_sections_are_up_to_date",
    },
    "a new module that names the live output's variable": {
        "test_deploy_live.py::test_no_other_test_or_scenario_draws_a_run",
    },
    "a fact read as a top-level variable, in a new file of the system layer's": {
        "test_gathered_facts.py::test_no_tracked_file_reads_a_fact_as_a_top_level_variable",
    },
    "a new engine test that names Ansible's settings file for a run of its own": {
        "test_gathered_facts.py::test_every_run_a_test_makes_leaves_facts_out_of_the_top_level",
    },
    "a new engine test that turns golden templates on": {
        "test_golden.py::test_golden_templates_stay_off_wherever_a_test_or_the_vagrantfile_runs_a_deploy",
    },
    "a new engine test named as a live test, test_aw_*_live.py": {
        "test_live_recipes.py::test_every_live_test_runs_a_real_playbook_only_as_its_own_tests_do",
    },
    "a new module of the MCP server's that reads the environment": {
        "test_mcp_tools.py::test_the_server_reads_nothing_of_its_environment_but_through_the_allow_list",
    },
    "a new engine test that names tools/status without the harness's status_root": {
        "test_status.py::test_every_status_run_in_a_test_writes_into_its_own_directory",
    },
    "a workflow whose checkout leaves its token in git's config": {
        "test_ci.py::test_no_checkout_leaves_the_workflow_s_token_in_git_s_config",
    },
    "EACH_WORKER written as a call, in a new engine test": {
        "test_shards.py::test_every_fixture_made_on_each_worker_is_a_module_fixture_of_its_own_module",
    },
    "a file the tracked-file guard refuses (.aw/, a page at the top of docs/), added by force": {
        "test_tracked_files.py::test_guard_passes_on_the_real_repository",
        "test_tracked_files.py::test_no_tracked_file_is_gitignored",
    },
    "a key file the pins do not hold": {"test_pins.py::test_every_vendored_signing_key_is_its_vendor_s"},
}
# A test file of today's that reads no tree, which conftest.py must leave unmarked: one that is cheap to collect.
UNTREED = UNIT / "test_vt.py"

# Loaded into a collection of its own, as PROBE is: each test's file, its name without its case, and its marks.
TREE_PROBE = """
import json
import os


def pytest_collection_finish(session):
    seen = [
        {
            "file": item.path.name,
            "test": getattr(item, "originalname", item.name),
            "tree": item.get_closest_marker("tree") is not None,
            "engine": item.get_closest_marker("engine") is not None,
        }
        for item in session.items
    ]
    with open(os.environ["AW_PROBE"], "w") as out:
        json.dump(seen, out)
"""


def _tree() -> set[str]:
    """conftest.py's TREE, as its source holds it: a set literal."""
    module = ast.parse((UNIT / "conftest.py").read_text())
    (value,) = [
        node.value
        for node in module.body
        if isinstance(node, ast.Assign) and [getattr(target, "id", "") for target in node.targets] == ["TREE"]
    ]
    return ast.literal_eval(value)


def test_every_test_a_planted_fault_failed_is_marked_tree_as_tree_names_it_and_no_other(tmp_path) -> None:
    # Collected as the suite collects them, by tests/unit/conftest.py's hook: the files TREE and FOUND name, and one of
    # today's that TREE leaves out. An entry that names no test the suite collects (a test renamed, a file gone) leaves
    # what it stood for unmarked, and so does a hook that stops marking: each fails here.
    tree = _tree()
    found = {test for tests in FOUND.values() for test in tests}
    files = {entry.split("::")[0] for entry in tree | found}
    assert [name for name in files if name.startswith("test_aw_")] == [], "the engine's tests are marked engine"
    assert UNTREED.name not in files and not [entry for entry in tree if entry.count("::") > 1], sorted(tree)
    (tmp_path / "tree_probe.py").write_text(TREE_PROBE)
    env = {name: value for name, value in os.environ.items() if name in PASSED_ON or name.startswith("LC_")}
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-p", "tree_probe"]
        + ["-c", str(REPO / "pyproject.toml"), "--rootdir", str(REPO)]
        + [str(UNIT / name) for name in sorted(files)]
        + [str(UNTREED)],
        cwd=tmp_path,
        env={**env, "AW_PROBE": str(tmp_path / "seen.json")},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    seen = json.loads((tmp_path / "seen.json").read_text())
    named = {item["file"] for item in seen} | {f"{item['file']}::{item['test']}" for item in seen}
    assert sorted((tree | found) - named) == [], "each entry names a test the suite collects"
    marked = {f"{item['file']}::{item['test']}" for item in seen if item["tree"]}
    meant = {
        f"{item['file']}::{item['test']}" for item in seen if {item["file"], f"{item['file']}::{item['test']}"} & tree
    }
    assert marked == meant, sorted(marked ^ meant)
    assert sorted(found - marked) == [], (
        "a test a planted fault failed is not marked tree: name it in conftest.py's TREE"
    )
    assert [item for item in seen if item["file"] == UNTREED.name and item["tree"]] == []
    assert [item for item in seen if item["engine"]] == []


def _wider_fixtures(source: str) -> list[str]:
    """The fixtures source defines with a scope wider than one test's - module, class, package, session, or one a look
    cannot tell - by the name a test asks for them by."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or _callee(decorator) != "fixture":
                continue
            given = {keyword.arg: keyword.value for keyword in decorator.keywords}
            scope = given.get("scope")
            if scope is None or (isinstance(scope, ast.Constant) and scope.value == "function"):
                continue
            name = given.get("name")
            found.append(name.value if isinstance(name, ast.Constant) and isinstance(name.value, str) else node.name)
    return found


def test_no_fixture_of_an_engine_file_outlives_the_guard_unnamed() -> None:
    # A fixture of a wider scope is made before the guard of a test's own process holds and ended after it lets go:
    # each is named, with why it may run with the developer's PATH, environment, home and network.
    found = {f"{path.name}::{name}" for path in _engine_files() for name in _wider_fixtures(path.read_text())}
    assert found == set(WIDER_THAN_A_TEST), "name a fixture wider than a test in WIDER_THAN_A_TEST, with why"
    assert all(reason.strip() for reason in WIDER_THAN_A_TEST.values())


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ('@pytest.fixture(scope="module")\ndef made(): pass', ["made"]),
        ('@pytest.fixture(scope="session")\ndef made(): pass', ["made"]),
        ('@pytest.fixture(scope="class")\ndef made(): pass', ["made"]),
        ('@pytest.fixture(scope="package")\ndef made(): pass', ["made"]),
        ("@pytest.fixture(scope=SCOPE)\ndef made(): pass", ["made"]),
        ('@pytest.fixture(scope="module", name="asked")\ndef made(): pass', ["asked"]),
        ('from pytest import fixture\n@fixture(scope="module")\ndef made(): pass', ["made"]),
        ('@pytest.fixture(scope="module")\nasync def made(): pass', ["made"]),
        ('class T:\n    @pytest.fixture(scope="module")\n    def made(self): pass', ["made"]),
        ("@pytest.fixture\ndef made(): pass", []),
        ('@pytest.fixture(scope="function")\ndef made(): pass', []),
        ("@pytest.fixture(autouse=True)\ndef made(): pass", []),
        ('@pytest.mark.parametrize("scope", ["module"])\ndef test_made(scope): pass', []),
    ],
)
def test_the_scan_sees_each_fixture_wider_than_a_test(source, found) -> None:
    assert _wider_fixtures(source) == found


def test_the_home_is_the_tests_own_and_so_are_home_and_the_xdg_folders(home, aw_home, tmp_path) -> None:
    assert home == aw_home == tmp_path / "aw-home" and home.is_dir()
    for name in (paths.VARIABLE, "HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        assert Path(os.environ[name]).is_relative_to(home), name
    # The engine's own resolver, in this process, finds the fleet there.
    where = paths.home(REPO)
    assert where.root == home and paths.state(where).is_relative_to(home) and paths.key_file(where).is_relative_to(home)


# ---------------------------------------------------------------- an engine test's own process ----

# Two test files run by a pytest of their own under this suite's conftest.py, loaded as a plugin (-p conftest): an
# engine test's file, whose failures are the ones asserted, and a file of today's, which the guard must leave as it is.
CHILD_ENGINE = """
import os
import socket
import subprocess


def test_runs_ssh_by_name():
    subprocess.run(["ssh", "-V"], stdin=subprocess.DEVNULL, capture_output=True, check=False)


def test_reaches_past_loopback_and_swallows_the_refusal():
    try:
        socket.create_connection(("192.0.2.1", 9), timeout=1)
    except OSError:
        pass


def test_finds_the_sessions_variable_gone(aw_home):
    assert "GH_TOKEN" not in os.environ and os.environ["HOME"] == str(aw_home)
"""
CHILD_TODAY = """
import os


def test_keeps_the_sessions_variable_and_path():
    assert os.environ["GH_TOKEN"] == "made-up-session-value" and "BROWSER" not in os.environ
    assert not [entry for entry in os.environ["PATH"].split(os.pathsep) if entry.endswith((".refused", ".desktop"))]
"""


def _child_pytest(tmp_path: Path, files: dict[str, str], env: dict[str, str]) -> subprocess.CompletedProcess:
    """files, each a test file of tmp_path's, run by a pytest of its own under this suite's conftest.py, with env for
    its whole session: its status and what it said."""
    for name, source in files.items():
        (tmp_path / name).write_text(source)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "conftest", "-c", str(REPO / "pyproject.toml")]
        + ["--rootdir", str(tmp_path), *(str(tmp_path / name) for name in files)],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _said(output: str, kind: str) -> set[str]:
    """The tests the short summary says were of kind (ERROR, FAILED, PASSED), by name."""
    return set(re.findall(rf"(?m)^{kind} \S+::(\w+)", output))


def _session_env(engine_guard) -> dict[str, str]:
    """The session's environment, as a developer's shell would hand it: a token among it, this process's stand-ins
    not."""
    path = [entry for entry in os.environ["PATH"].split(os.pathsep) if Path(entry).parent != engine_guard.stand_ins]
    env = {name: value for name, value in os.environ.items() if name in PASSED_ON or name.startswith("LC_")}
    env |= {"PATH": os.pathsep.join(path), "GH_TOKEN": "made-up-session-value"}
    env["PYTHONPATH"] = os.pathsep.join([str(REPO / "tests" / "unit"), str(REPO / "lib")])
    return env


def test_an_engine_test_s_own_process_meets_the_stand_ins_and_its_own_environment(tmp_path, engine_guard) -> None:
    env = _session_env(engine_guard)
    ran = _child_pytest(tmp_path, {"test_aw_child.py": CHILD_ENGINE, "test_today_child.py": CHILD_TODAY}, env)
    said = ran.stdout + ran.stderr
    assert ran.returncode == 1, said
    # ssh by name met the refusing stand-in, and a connection past loopback failed its test though it was swallowed.
    assert _said(said, "ERROR") == {"test_runs_ssh_by_name", "test_reaches_past_loopback_and_swallows_the_refusal"}
    assert re.search(r"(?m)^E\s+ssh -V\s*$", said) and "tests/molecule/" in said, said
    # The session's own variable is gone from an engine test, and a test of today's keeps it, its PATH untouched: each
    # test's call passed, and two failed once they had ended.
    assert _said(said, "FAILED") == set() and re.search(r"\b4 passed, 2 errors\b", said), said


# An engine test's file whose tests take the stand-ins off their own PATH, each its own way, or keep the guard through
# what a test may do: its own monkeypatch undone, a folder of its own put first.
CHILD_PATH = """
import os
import shutil
import socket

import pytest


def test_sets_its_path_by_monkeypatch(monkeypatch):
    monkeypatch.setenv("PATH", os.defpath)


def test_sets_its_path_by_hand():
    os.environ["PATH"] = os.defpath


def test_undoes_its_own_monkeypatch(monkeypatch, aw_home, engine_guard):
    monkeypatch.setenv("AW_CHILD_OWN", "set")
    monkeypatch.undo()
    assert os.environ["HOME"] == os.environ["AGENT_WAREHOUSE_HOME"] == str(aw_home)
    assert os.path.dirname(os.path.dirname(shutil.which("ssh"))) == str(engine_guard.stand_ins)
    assert "GH_TOKEN" not in os.environ and os.environ["BROWSER"].startswith(str(engine_guard.stand_ins))
    with pytest.raises(OSError, match="tests/molecule/"):
        socket.getaddrinfo("example.com", 443)
    engine_guard.refused.clear()


def test_puts_a_folder_of_its_own_first(engine_guard, tmp_path):
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "ssh").write_text("#!/bin/sh\\n")
    (mine / "ssh").chmod(0o755)
    engine_guard.first_on_path(mine)
    assert shutil.which("ssh") == str(mine / "ssh")
"""


def test_a_path_of_the_test_s_own_fails_it_and_its_own_monkeypatch_undone_keeps_the_guard(
    tmp_path, engine_guard
) -> None:
    ran = _child_pytest(tmp_path, {"test_aw_child_path.py": CHILD_PATH}, _session_env(engine_guard))
    said = ran.stdout + ran.stderr
    assert ran.returncode == 1, said
    # Each PATH set fails its test as its call ends, before its monkeypatch is undone; one set by hand, which no
    # monkeypatch undoes, fails its teardown too. The guard is the guard's own: a test's monkeypatch undone leaves it.
    assert _said(said, "FAILED") == {"test_sets_its_path_by_monkeypatch", "test_sets_its_path_by_hand"}, said
    assert _said(said, "ERROR") == {"test_sets_its_path_by_hand"}, said
    assert "engine_guard.first_on_path(dir)" in said and "ssh at /" in said, said
    assert re.search(r"\b2 failed, 2 passed\b", said) and re.search(r"\b1 error\b", said), said


def test_a_folder_of_the_test_s_own_goes_first_in_its_own_process_and_the_stand_ins_stay(
    engine_guard, tmp_path
) -> None:
    mine = tmp_path / "mine"
    mine.mkdir()
    harness._executable(mine / "ssh", "#!/bin/sh\necho mine\n")
    engine_guard.first_on_path(mine)
    assert shutil.which("ssh") == str(mine / "ssh")
    for tool in aw_kit.STOOD_IN:
        if tool != "ssh":
            assert Path(shutil.which(tool)).parent.parent == engine_guard.stand_ins, tool
    engine_guard.held("now")


def test_a_path_that_finds_this_machine_s_programs_first_fails_the_guard(engine_guard, monkeypatch) -> None:
    monkeypatch.setenv("PATH", os.defpath)
    with pytest.raises(pytest.fail.Exception, match=r"ahead of the stand-ins \(.*ssh at /"):
        engine_guard.held("now")
    # As the test's call would end: its monkeypatch undone first, which leaves the guard's PATH.
    monkeypatch.undo()
    engine_guard.held("now")


@pytest.mark.parametrize("where", ["/usr/bin", "/bin", "relative/bin", "the test's parent", "a link to /usr/bin"])
def test_first_on_path_takes_only_a_folder_of_the_test_s_own(engine_guard, home, tmp_path, where) -> None:
    folder: str | Path = where
    if where == "the test's parent":
        folder = tmp_path.parent
    elif where == "a link to /usr/bin":
        folder = tmp_path / "linked"
        folder.symlink_to("/usr/bin")
    said = r"first_on_path takes a folder of the test's own directory"
    with pytest.raises(pytest.fail.Exception, match=said):
        engine_guard.first_on_path(folder)
    for run in (
        lambda: environment(home, first_on_path=(folder,)),
        lambda: run_aw("--version", home=home, first_on_path=(folder,)),
    ):
        with pytest.raises(pytest.fail.Exception, match=said):
            run()
    assert engine_guard.first == []


def test_path_home_is_the_test_s_own_without_asking_for_home(aw_home) -> None:
    assert Path.home() == aw_home and Path("~").expanduser() == aw_home and not aw_home.exists()
    for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        assert Path(os.environ[name]).is_relative_to(aw_home), name


def test_an_engine_test_s_own_process_keeps_the_names_listed_alone(engine_guard, aw_home) -> None:
    kit = {*aw_kit.home_variables(aw_home), "BROWSER", aw_kit.PYTEST_OWN}
    assert [name for name in os.environ if name not in PASSED_ON | kit and not name.startswith("LC_")] == []
    assert os.environ["BROWSER"] == str(engine_guard.stand_ins / ".desktop" / "open")


@pytest.mark.parametrize("tool", [*harness.DESKTOP_TOOLS, *harness.GUARDED_TOOLS, *harness.REFUSED_TOOLS])
def test_a_stand_in_is_first_on_the_test_s_own_path(engine_guard, tool) -> None:
    found = shutil.which(tool)
    assert found and Path(found).parent.parent == engine_guard.stand_ins, found


@pytest.mark.parametrize(
    "reach",
    [
        lambda: socket.getaddrinfo("example.com", 443),
        lambda: socket.create_connection(("example.com", 443), timeout=1),
        lambda: socket.create_connection(("192.0.2.1", 9), timeout=1),
        lambda: _connect(socket.AF_INET, ("192.0.2.1", 9)),
        lambda: _connect(socket.AF_INET, ("example.com", 80)),
        lambda: _connect(socket.AF_INET6, ("2001:db8::1", 9, 0, 0)),
        lambda: _connect(socket.AF_INET, ("192.0.2.1", 9), ex=True),
    ],
)
def test_an_engine_test_reaches_nothing_past_loopback(engine_guard, reach) -> None:
    with pytest.raises(Offline, match="tests/molecule/"):
        reach()
    assert len(engine_guard.refused) == 1 and "tests/molecule/" in engine_guard.refused[0]
    # Said, and so this test would fail once it ended: forgotten here, since the refusal is what it asserts.
    engine_guard.refused.clear()


def _connect(family: int, address: tuple, ex: bool = False) -> None:
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        (sock.connect_ex if ex else sock.connect)(address)


def test_an_engine_test_reaches_a_fake_on_loopback(engine_guard) -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        for host in ("127.0.0.1", "localhost"):
            with socket.create_connection((host, port), timeout=5):
                pass
        assert socket.getaddrinfo("localhost", port) and socket.getaddrinfo(None, port)
    assert engine_guard.refused == []


@pytest.mark.parametrize("host", ["::1", "::ffff:127.0.0.1", "127.0.0.2", "localhost", None, b"127.0.0.1"])
def test_loopback_is_what_the_guard_lets_through(host) -> None:
    assert aw_kit._loopback(host)


@pytest.mark.parametrize("host", ["example.com", "192.0.2.1", "0.0.0.0", "2001:db8::1", "<broadcast>", "localhost."])
def test_anything_else_is_not_loopback(host) -> None:
    assert not aw_kit._loopback(host)


# ---------------------------------------------------------------- tools/aw only through the kit ----

# What starts a program, or runs a file as one, by the name it is called by: subprocess's, os's, pty's, asyncio's,
# runpy's, and the suite's own launchers - the harness's ways to a terminal and its fakes' start, live_kit's runs.
STARTS = {
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
    "getoutput",
    "getstatusoutput",
    "system",
    "popen",
    "spawn",
    "posix_spawn",
    "posix_spawnp",
    "create_subprocess_exec",
    "create_subprocess_shell",
    "run_path",
    "_at_a_pseudo_terminal",
    "_run_at_a_terminal",
    "_start",
    "_serve",
    "at_a_terminal",
    "piped",
}
# os.execv and its kind, os.spawnv and its kind.
STARTS_LIKE = ("exec", "spawn")
# The kit's ways to the program, which a test calls: the launcher at the checkout's top runs tools/aw too.
KIT = {"run_aw", "aw_at_a_terminal", "imports_of", "run_launcher"}
# The kit's names of the program, and of the launcher that runs it.
KIT_NAMES = ("AW", "LAUNCHER")
# tools/aw in a string: alone, at the end of a path, or in a command line.
NAMES_AW = re.compile(r"(?<![\w-])tools/aw(?![\w.-])")


def _callee(node: ast.Call) -> str:
    return getattr(node.func, "attr", getattr(node.func, "id", ""))


def _parts(node: ast.AST) -> list[str | None]:
    """The parts of a path built with /, each string split at its own /: None for a part that is no string."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _parts(node.left) + _parts(node.right)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.split("/")
    return [None]


def _two(parts: list[str | None]) -> bool:
    return any(parts[i : i + 2] == ["tools", "aw"] for i in range(len(parts) - 1))


def _names_aw(node: ast.AST, bound: set[str]) -> bool:
    """Whether node, or anything in it, names tools/aw: a string, a path built of its parts with / or as a call's
    arguments (Path("tools", "aw")), a name bound to one, or the kit's AW or LAUNCHER."""
    for inner in ast.walk(node):
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str) and NAMES_AW.search(inner.value):
            return True
        if isinstance(inner, ast.BinOp) and _two(_parts(inner)):
            return True
        if isinstance(inner, ast.Call) and _two([part for arg in inner.args for part in _parts(arg)]):
            return True
        if isinstance(inner, ast.Name) and inner.id in bound:
            return True
        if isinstance(inner, ast.Attribute) and inner.attr in KIT_NAMES and getattr(inner.value, "id", "") == "aw_kit":
            return True
    return False


def _bound(tree: ast.AST) -> set[str]:
    """The names a file binds to tools/aw's path, wherever: an assignment, a loop's target, a parameter's default, the
    kit's AW or LAUNCHER imported; and so on, through names bound to those."""
    bound: set[str] = set()
    while True:
        before = set(bound)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "aw_kit":
                bound |= {alias.asname or alias.name for alias in node.names if alias.name in KIT_NAMES}
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.For)):
                value = node.iter if isinstance(node, ast.For) else node.value
                targets = [node.target] if isinstance(node, (ast.AnnAssign, ast.For)) else node.targets
                if value is not None and _names_aw(value, bound):
                    bound |= {name.id for target in targets for name in ast.walk(target) if isinstance(name, ast.Name)}
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args = node.args
                positional = [*args.posonlyargs, *args.args]
                pairs = list(zip(positional[len(positional) - len(args.defaults) :], args.defaults, strict=True))
                pairs += [(arg, default) for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)]
                bound |= {arg.arg for arg, default in pairs if default is not None and _names_aw(default, bound)}
        if bound == before:
            return bound


def _runs_aw(source: str) -> list[int]:
    """The lines where source starts tools/aw other than through the kit: a call that starts a program, given
    anything that names it."""
    tree = ast.parse(source)
    bound = _bound(tree)
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (_callee(node) in STARTS or _callee(node).startswith(STARTS_LIKE))
        and _callee(node) not in KIT
        and any(_names_aw(arg, bound) for arg in [*node.args, *(keyword.value for keyword in node.keywords)])
    )


def test_no_test_runs_tools_aw_but_through_the_kit() -> None:
    # The kit's runs inherit a list of names and find the stand-ins first; a run of the test's own would inherit this
    # process's environment, the developer's ssh agent and keychain among it, and reach their real programs.
    files = [path for path in repo_files("tests", untracked=True) if path.suffix == ".py"]
    assert UNIT / "test_aw_layout.py" in files and UNIT / "aw_kit.py" in files, len(files)
    found = {
        str(path.relative_to(REPO)): lines
        for path in files
        if path.name != "aw_kit.py" and (lines := _runs_aw(path.read_text()))
    }
    assert found == {}, "run tools/aw through aw_kit's run_aw, aw_at_a_terminal or imports_of"


@pytest.mark.parametrize(
    ("source", "runs"),
    [
        ('subprocess.run([sys.executable, "-I", str(REPO / "tools" / "aw"), "help"])', True),
        ('AW = REPO / "tools" / "aw"\nsubprocess.run([sys.executable, str(AW), "status"])', True),
        ('def go(program=REPO / "tools" / "aw"):\n    return subprocess.Popen([sys.executable, program])', True),
        ('subprocess.check_output("python3 -I tools/aw deploy", shell=True)', True),
        ('os.execv(sys.executable, [sys.executable, "tools/aw"])', True),
        ('harness._at_a_pseudo_terminal([sys.executable, str(Path("tools", "aw"))], cwd, env)', True),
        ("from aw_kit import AW\nsubprocess.run([sys.executable, AW])", True),
        ("import aw_kit\nos.system(f'{sys.executable} {aw_kit.AW} help')", True),
        ('program = REPO / "tools/aw"\nwhere = program\nsubprocess.run([where])', True),
        ('for program in (AW_PATH, "tools/aw"):\n    subprocess.run([program])', True),
        ('runpy.run_path(str(REPO / "tools" / "aw"))', True),
        ('from aw_kit import LAUNCHER\nsubprocess.run([LAUNCHER, "status"])', True),
        ("import aw_kit\nsubprocess.Popen([str(aw_kit.LAUNCHER), 'help'])", True),
        ('run_launcher("status", home=home, launcher=checkout / "aw")', False),
        ('live_kit.at_a_terminal([sys.executable, "-I", str(REPO / "tools" / "aw"), "deploy"], root, env)', True),
        ('live_kit.piped([sys.executable, "-I", str(REPO / "tools" / "aw"), "status"], root, env)', True),
        ('harness._start(REPO / "tools" / "aw", "--version")', True),
        ('for base in harness._serve(REPO / "tools" / "aw"):\n    pass', True),
        ('harness._run_at_a_terminal(root, REPO / "tools/aw", {}, {})', True),
        ('run_aw("--version", home=home, cwd=REPO, program="tools/aw")', False),
        ('aw_kit.aw_at_a_terminal("help", home=home)', False),
        ('AW = REPO / "tools" / "aw"\nassert AW in new', False),
        ('subprocess.run(["git", "ls-files", "tools"])', False),
        ("pytest.mark.parametrize('source', [\"COMMAND = ['tools/aw', 'deploy']\"])", False),
        ('subprocess.run(["ls", "tools/awful"])', False),
    ],
)
def test_the_scan_for_a_run_of_tools_aw_sees_each_way(source, runs) -> None:
    assert bool(_runs_aw(source)) is runs


# ---------------------------------------------------------------- the kit's runs ----


def test_run_aw_runs_the_program_and_says_what_it_said(home) -> None:
    ran = run_aw("--version", home=home)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, f"aw from the checkout {REPO}\n", "")
    # Isolated, by its absolute path, from the home.
    assert ran.args[:3] == [sys.executable, "-I", str(AW)]
    # Text on its standard input, which today's aw reads none of.
    assert run_aw("--version", home=home, stdin="typed\n").returncode == 0


def test_run_aw_never_reads_this_terminal_or_runs_another_program(home, tmp_path) -> None:
    with pytest.raises(AssertionError, match="never reads this terminal"):
        run_aw("--version", home=home, stdin=None)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "aw").write_text("print('another')\n")
    with pytest.raises(AssertionError, match="is not tools/aw"):
        run_aw("--version", home=home, cwd=tmp_path, program="tools/aw")


def test_a_run_inherits_the_names_listed_and_the_home_s_alone(home, monkeypatch) -> None:
    # What would point the program at the developer's own: their ssh agent, a token, a config, a module path.
    for name in ("SSH_AUTH_SOCK", "GH_TOKEN", "GIT_CONFIG_GLOBAL", "ANSIBLE_HOME", "PYTHONPATH", "CLAUDE_CONFIG_DIR"):
        monkeypatch.setenv(name, "/developer/own")
    env = environment(home)
    kit = {paths.VARIABLE, "HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "BROWSER"}
    assert [name for name in env if name not in PASSED_ON | kit and not name.startswith("LC_")] == []
    assert env[paths.VARIABLE] == env["HOME"] == str(home)
    assert all(Path(env[name]).is_relative_to(home) for name in kit - {"BROWSER"})


@pytest.mark.parametrize("tool", [*harness.DESKTOP_TOOLS, *harness.GUARDED_TOOLS, *harness.REFUSED_TOOLS])
def test_a_stand_in_is_first_on_path_and_a_run_that_reached_it_fails_its_test(home, tool) -> None:
    env = environment(home)
    found = shutil.which(tool, path=env["PATH"])
    assert found and Path(found).parent.parent == stand_ins(home), found
    reached = subprocess.run([found, "--asked"], env=env, stdin=subprocess.DEVNULL, capture_output=True, check=False)
    assert reached.returncode != 0
    with pytest.raises(pytest.fail.Exception, match=rf"(?m)^{re.escape(tool)} --asked$"):
        harness._fail_if_the_desktop_was_reached(stand_ins(home))
    # And the next run of the kit's fails the test for it.
    with pytest.raises(pytest.fail.Exception):
        run_aw("--version", home=home)
    # As the guard of this test's own process would once it ended: forgotten here, since the call is what it asserts.
    _forget_the_calls(stand_ins(home))


def _forget_the_calls(root: Path) -> None:
    for folder in (".desktop", ".refused"):
        (root / folder / "calls.log").unlink(missing_ok=True)


@pytest.mark.parametrize("name", ["PATH", "HOME", "BROWSER", paths.VARIABLE, "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR"])
def test_a_test_s_env_never_lifts_a_guard(home, name) -> None:
    lifted = {name: "/elsewhere"}
    for run in (
        lambda: run_aw("--version", home=home, env=lifted),
        lambda: aw_at_a_terminal("--version", home=home, env=lifted),
        lambda: imports_of("--version", home=home, env=lifted),
    ):
        with pytest.raises(pytest.fail.Exception, match=rf"never sets {name}\b.*first_on_path=\(dir, \.\.\.\)"):
            run()
    # Any other variable is the test's to set.
    assert environment(home, {"AW_TEST_OWN": "yes"})["AW_TEST_OWN"] == "yes"


def test_first_on_path_goes_ahead_of_the_stand_ins_and_keeps_them(home, tmp_path) -> None:
    mine = tmp_path / "mine"
    mine.mkdir()
    harness._executable(mine / "ssh", "#!/bin/sh\necho mine\n")
    env = environment(home, first_on_path=(mine,))
    assert shutil.which("ssh", path=env["PATH"]) == str(mine / "ssh")
    for tool in [*harness.DESKTOP_TOOLS, *harness.GUARDED_TOOLS, *harness.REFUSED_TOOLS]:
        if tool != "ssh":
            assert Path(shutil.which(tool, path=env["PATH"])).parent.parent == stand_ins(home), tool
    assert run_aw("--version", home=home, first_on_path=(mine,)).returncode == 0
    rc, _ = aw_at_a_terminal("--version", home=home, first_on_path=(mine,), timeout=60)
    assert rc == 0 and ENTRY in imports_of("--version", home=home, first_on_path=(str(mine),))


def test_the_browser_is_a_stand_in_too(home) -> None:
    assert environment(home)["BROWSER"] == str(stand_ins(home) / ".desktop" / "open")


def test_aw_at_a_terminal_runs_the_program_at_a_terminal_of_its_own(home) -> None:
    rc, output = aw_at_a_terminal("--version", home=home, timeout=60)
    assert rc == 0 and f"aw from the checkout {REPO}" in output.decode(), output


def test_imports_of_lists_what_a_run_loaded(home) -> None:
    loaded = imports_of("--version", home=home)
    assert ENTRY in loaded and "claude_vm.fleet" not in loaded
    assert not {name for name in loaded if name.split(".")[0] in ("ansible", "yaml", "asyncio", "subprocess")}


def test_imports_of_lists_what_importing_one_module_loaded(home) -> None:
    loaded = imports_of(module="claude_vm.fleet.paths", home=home)
    assert {"claude_vm", "claude_vm.fleet", "claude_vm.fleet.paths", "dataclasses"} <= loaded, sorted(loaded)
    assert ENTRY not in loaded and "claude_vm.secret" not in loaded


@pytest.mark.parametrize(
    ("module", "words", "said"),
    [
        pytest.param("claude_vm.fleet; import os", (), "by its dotted name", id="code-after-the-name"),
        pytest.param("claude_vm..fleet", (), "by its dotted name", id="an-empty-part"),
        pytest.param("claude_vm.fleet\n", (), "by its dotted name", id="a-newline"),
        pytest.param("", (), "by its dotted name", id="empty"),
        pytest.param("claude_vm.fleet", ("--version",), "by its dotted name", id="words-beside-it"),
        pytest.param(
            "claude_vm.no_such_module", (), "importing claude_vm.no_such_module failed", id="an-import-that-fails"
        ),
    ],
)
def test_imports_of_fails_a_module_it_cannot_list(home, module: str, words: tuple[str, ...], said: str) -> None:
    # The guard's own sentence: without the guard, a name that is no dotted name still fails, for another reason, once
    # it has run as code.
    with pytest.raises(pytest.fail.Exception) as refused:
        imports_of(*words, module=module, home=home)
    assert said in str(refused.value)


def test_imports_of_runs_nothing_of_a_name_it_refuses(home, tmp_path) -> None:
    made = tmp_path / "made-by-the-name"
    with pytest.raises(pytest.fail.Exception, match="by its dotted name"):
        imports_of(module=f"os; open({str(made)!r}, 'w').close()", home=home)
    assert not made.exists()


# ---------------------------------------------------------------- the canary search ----


def test_the_search_finds_a_secret_in_a_file_or_a_folder(tmp_path) -> None:
    (tmp_path / "log").write_text(f"token={SECRET}\n")
    (tmp_path / "folder" / "deep").mkdir(parents=True)
    (tmp_path / "folder" / "deep" / "state.json").write_text('{"ok": true}\n')
    assert searched_for([SECRET], tmp_path / "log") == [(tmp_path / "log", SECRET)]
    assert searched_for([SECRET, b"other"], tmp_path / "folder") == []
    wrapped = Secret(SECRET)
    assert searched_for([wrapped], tmp_path) == [(tmp_path / "log", wrapped)]


def test_the_search_fails_when_it_searched_nothing(tmp_path) -> None:
    (tmp_path / "empty").write_text("")
    (tmp_path / "folder").mkdir()
    (tmp_path / "log").write_text("something\n")
    for secrets, where in (
        ([SECRET], [tmp_path / "missing"]),
        ([SECRET], [tmp_path / "log", tmp_path / "empty"]),
        ([SECRET], [tmp_path / "folder"]),
        ([], [tmp_path / "log"]),
        ([""], [tmp_path / "log"]),
        ([SECRET], []),
    ):
        with pytest.raises(pytest.fail.Exception):
            searched_for(secrets, *where)


# ---------------------------------------------------------------- Secret ----


def test_a_secret_is_never_said() -> None:
    secret = Secret(SECRET)
    shown = [
        repr(secret),
        str(secret),
        f"{secret}",
        f"{secret!r}",
        f"{secret!s}",
        f"{secret:>40}",
        "{}".format(secret),  # noqa: UP032 - format is one way a secret is shown
        "%s %r" % (secret, secret),  # noqa: UP031 - the old way is one way a secret is shown
        json.dumps({"token": secret}, default=str),
        repr([secret, {"token": secret}]),
    ]
    stream = io.StringIO()
    logger = logging.getLogger("aw-guards")
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    try:
        logger.warning("token %s", secret)
    finally:
        logger.removeHandler(handler)
    shown.append(stream.getvalue())
    assert [text for text in shown if SECRET in text] == []
    assert all(MARK in text for text in shown), shown


def test_a_secret_refuses_json_and_pickle_and_copies_as_itself() -> None:
    secret = Secret(SECRET)
    with pytest.raises(TypeError):
        json.dumps({"token": secret})
    for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
        with pytest.raises(TypeError):
            pickle.dumps(secret, protocol=protocol)
    assert copy.copy(secret) is secret and copy.deepcopy(secret) is secret

    @dataclasses.dataclass
    class Login:
        name: str
        token: Secret

    held = dataclasses.asdict(Login("dev", secret))
    assert held["token"] is secret
    with pytest.raises(TypeError):
        json.dumps(held)


def test_a_secret_strikes_itself_from_a_text_and_says_nothing_else() -> None:
    secret = Secret(SECRET)
    assert secret.struck(f"echoed {SECRET} and {SECRET}!") == f"echoed {MARK} and {MARK}!"
    assert secret.struck("nothing of it") == "nothing of it" and secret.struck("") == ""
    assert Secret("").struck("anything") == "anything"
    for value in (SECRET.encode(), None, Secret(SECRET)):
        with pytest.raises(TypeError) as refused:
            secret.struck(value)
        assert SECRET not in str(refused.value)


def test_a_secret_holds_a_string_and_reveal_alone_reads_it() -> None:
    assert Secret(SECRET).reveal() == SECRET
    for value in (SECRET.encode(), 42, None, Secret(SECRET)):
        with pytest.raises(TypeError):
            Secret(value)
    assert not hasattr(Secret(SECRET), "__dict__")


def _reveals(source: str) -> list[int]:
    """The lines where source reads a Secret's value: reveal reached as an attribute, called or kept to call later
    (token.reveal, partial(token.reveal)), a call of reveal by its name, or reveal named to getattr or methodcaller."""
    return sorted(
        {
            node.lineno
            for node in ast.walk(ast.parse(source))
            if (isinstance(node, ast.Attribute) and node.attr == "reveal")
            or (isinstance(node, ast.Call) and _callee(node) == "reveal")
            or (
                isinstance(node, ast.Call)
                and _callee(node) in ("getattr", "methodcaller")
                and any(getattr(arg, "value", None) == "reveal" for arg in node.args[:2])
            )
        }
    )


def _product_python() -> list[Path]:
    """The product's Python: lib/ and tools/, a file without .py known by its first line."""
    found = []
    for path in repo_files("lib", "tools", untracked=True):
        if path.suffix == ".py" or (path.suffix == "" and path.read_bytes()[:64].startswith(b"#!/usr/bin/env python")):
            found.append(path)
    return found


def test_only_the_modules_reveals_names_read_a_secret_s_value() -> None:
    files = _product_python()
    assert AW in files and REPO / "lib" / "claude_vm" / "secret.py" in files, len(files)
    found = sorted(str(path.relative_to(REPO)) for path in files if _reveals(path.read_text()))
    assert found == sorted(REVEALS), "a module that sends a secret on is added to REVEALS, with why"
    assert all(reason.strip() for reason in REVEALS.values())


@pytest.mark.parametrize(
    ("source", "reads"),
    [
        ("token.reveal()", True),
        ("headers = {'Authorization': f'PVEAPIToken={secret.reveal()}'}", True),
        ("getattr(secret, 'reveal')()", True),
        ("send = token.reveal\nsend()", True),
        ("functools.partial(token.reveal)", True),
        ("operator.methodcaller('reveal')(token)", True),
        ("def reveal(self):\n    return self.value", False),
        # A word of VS Code's tasks, which lib/claude_vm/editors/vscode.py writes.
        ("presentation = {'reveal': 'always'}", False),
        ("Secret(value)", False),
    ],
)
def test_the_scan_for_reveal_sees_each_way(source, reads) -> None:
    assert bool(_reveals(source)) is reads
