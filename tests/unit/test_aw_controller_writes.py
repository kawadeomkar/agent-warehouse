"""What the new engine writes on the person's computer outside the checkout is one list: lib/claude_vm/engine/
controller_writes.py (card G8; docs/architecture/engine.md §16.4), in the form of today's
playbooks/vars/controller_writes.yml, which tests/unit/test_controller_writes.py holds as it is until the cutover.

By today's test's rule: each row's write is found where the engine makes it, by the pattern MADE_BY gives it, and every
file of the new engine that makes it is one of the row's sources. And the rule today's cannot give the engine, whose
writes go through a few calls of Python: every file of the new engine that writes a file (WRITES: a file written,
replaced, created, appended to or made a folder of, a scratch file or folder, a link) is the source of a row, so that a
write to a place no row names fails here. A row whose write no card has built yet names that card and no source.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from harness import REPO, repo_files
from test_aw_layout import NEW_MODULES

from claude_vm.engine import controller_writes
from claude_vm.engine.controller_writes import ROWS
from claude_vm.fleet import paths, registry

FIELDS = ("id", "path", "what", "writer", "when", "removed_by")
# How each row's write looks where the engine makes it, where it has a spelling of its own: the Proxmox host's key taken
# at first use (the runner's policy for the host), a VM's key appended under its identity or named by an alias, the
# aliases' two writes, a run's text log opened new to append, the launcher's environment, and the two writes of other
# programs' command lines. A row with none here is found by WRITES alone.
MADE_BY = {
    "known_hosts": r"ACCEPT_NEW if [^\n]*is_host",
    "vm_known_hosts": r"os\.O_WRONLY \| os\.O_APPEND|UserKnownHostsFile \{",
    "logs": r"os\.O_EXCL \| os\.O_APPEND",
    "ssh_config": r"swap, files\.config",
    "ssh_include": r"swap, files\.user",
    "data": r"UV_PROJECT_ENVIRONMENT=",
    "vscode_extension": r"--install-extension",
    "mcp": r"\bmcp add\b",
}
# What writes a file, in the new engine's Python.
WRITES = re.compile(
    r"\.write_text\(|\.write_bytes\(|\bos\.replace\(|\bstore\.replace\(|\bos\.O_CREAT\b|\bos\.O_APPEND\b|\.mkdir\("
    r"|\bos\.makedirs\(|\btempfile\.mk|\bmkstemp\(|\bmkdtemp\(|\bos\.symlink\(|\.symlink_to\(|\bos\.rename\("
    r"|\bshutil\.(?:copy|move)|\bopen\([^)\n]*[\"'][wax]b?\+?[\"']"
)
# A file of the new engine that writes nothing on this computer though WRITES finds it, and why: none yet.
NOT_HERE: dict[str, str] = {}
# The launcher at the checkout's top, which is shell: MADE_BY reads it, WRITES does not.
LAUNCHER = REPO / "aw"
HELPER = "lib/claude_vm/guest/helper/"


def _engine() -> dict[str, str]:
    """The new engine's files, by their path in the checkout, with their text: every module of its subpackages (but the
    helper, which runs on a machine and writes there), secret.py, tools/aw and the launcher."""
    wanted = [where for name in NEW_MODULES for where in (f"lib/claude_vm/{name}", f"lib/claude_vm/{name}.py")]
    found = [path for path in repo_files(*wanted, untracked=True) if path.suffix == ".py"]
    texts = {str(path.relative_to(REPO)): path.read_text() for path in [*found, REPO / "tools" / "aw", LAUNCHER]}
    return {rel: text for rel, text in texts.items() if not rel.startswith(HELPER)}


def _writers(texts: dict[str, str]) -> set[str]:
    """The Python files among texts that write a file."""
    return {rel for rel, text in texts.items() if rel != "aw" and WRITES.search(text)}


def _made(texts: dict[str, str], pattern: str) -> set[str]:
    return {rel for rel, text in texts.items() if re.search(pattern, text)} - set(NOT_HERE)


def test_every_row_says_where_what_who_when_and_what_takes_it_out() -> None:
    ids = [row.id for row in ROWS]
    assert len(ids) == len(set(ids)), ids
    assert set(MADE_BY) <= set(ids)
    for row in ROWS:
        assert all(isinstance(getattr(row, field), str) and getattr(row, field) for field in FIELDS), row.id
        assert not re.search(r"/(?:Users|home)/", str(row)), "a path is said as ~/..., never a home's own"
        # A row is either built, with its sources, or names the card that builds it: never both, never neither.
        assert bool(row.sources) != bool(row.card), row.id
        assert all((REPO / source).is_file() for source in row.sources), row.id
        assert row.setting == "" or row.setting in {each.name for each in registry.rows()}, row.id
        assert row.where is None or row.where.__module__ == paths.__name__, row.id
    # Today's rows, each carried, and the report's own: the fleet's folder, the key file, the state folder's jobs, the
    # runs' text logs and the lock files, a run's directory and the shared control directory, which is Ansible's own.
    today = ["known_hosts", "vm_known_hosts", "ssh_config", "ssh_include", "data", "vscode_extension"]
    today += ["editor_records", "mcp", "sessions_dir"]
    assert ids[: len(today)] == today
    assert ids[len(today) :] == ["fleet", "vault_key", "jobs", "logs", "locks", "run_dir", "control_dir"]
    assert controller_writes.ALSO


def test_every_place_that_makes_a_row_s_write_is_one_of_its_sources() -> None:
    texts = _engine()
    by_id = {row.id: row for row in ROWS}
    for name, pattern in MADE_BY.items():
        made = _made(texts, pattern)
        assert made == set(by_id[name].sources), (name, "name each place in its row's sources, or the row is stale")


def test_every_file_of_the_engine_that_writes_is_the_source_of_a_row() -> None:
    texts = _engine()
    named = {source for row in ROWS for source in row.sources}
    assert _writers(texts) - named - set(NOT_HERE) == set(), "a write to a place no row names: give it its row"
    # And every source is a file of the new engine's.
    assert named <= set(texts), named - set(texts)


@pytest.mark.parametrize(
    "source",
    [
        'Path(where).write_text("x")',
        "where.write_bytes(b'x')",
        "os.replace(staged, where)",
        "store.replace(where, text)",
        "os.open(where, os.O_WRONLY | os.O_CREAT)",
        "os.open(where, os.O_WRONLY | os.O_APPEND)",
        "where.mkdir(parents=True)",
        "os.mkdir(where, 0o700)",
        "os.makedirs(where)",
        "tempfile.mkstemp(dir=where)",
        "tempfile.mkdtemp()",
        "os.symlink(a, b)",
        "where.symlink_to(a)",
        "os.rename(a, b)",
        "shutil.copy(a, b)",
        "shutil.move(a, b)",
        'open(where, "w")',
        "open(where, 'ab')",
        'open(where, mode="x")',
    ],
)
def test_the_scan_sees_each_way_to_write(source) -> None:
    assert _writers({"lib/claude_vm/engine/planted.py": source}) == {"lib/claude_vm/engine/planted.py"}


@pytest.mark.parametrize("source", ["open(where).read()", 'open(where, "r")', "where.read_text()", "os.path.exists(a)"])
def test_the_scan_takes_no_read_for_a_write(source) -> None:
    assert _writers({"lib/claude_vm/engine/planted.py": source}) == set()


def test_a_write_to_a_place_no_row_names_is_found() -> None:
    # A module of the engine that writes, with no row naming it, is what the scan exists for.
    texts = {**_engine(), "lib/claude_vm/engine/planted.py": "Path(where).write_text(text)\n"}
    named = {source for row in ROWS for source in row.sources}
    assert _writers(texts) - named == {"lib/claude_vm/engine/planted.py"}


def test_the_rows_name_the_aliases_files_as_the_aliases_write_them() -> None:
    from claude_vm.engine import aliases

    files = aliases.Files.of({"HOME": "/x"})
    by_id = {row.id: row for row in ROWS}
    assert f"`~/.ssh/{files.config.name}`" == by_id["ssh_config"].path
    assert f"`~/.ssh/{files.known_hosts.name}`" == by_id["vm_known_hosts"].path
    assert f"`~/.ssh/{files.user.name}`" in by_id["ssh_include"].path
    assert Path(paths.user_home({"HOME": "/x"})) == files.config.parent.parent
