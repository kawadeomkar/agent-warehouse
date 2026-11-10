"""What the project writes on this machine outside the checkout is one list: playbooks/vars/controller_writes.yml.

Every VM's session notes show it (code.yml loads it, vm_session.md.j2 renders it) and the security model's page
renders it (tools/docs.py), so the list is what the user is told. These tests hold it to the code. Each row's write
is found where it is made, by the pattern MADE_BY gives it, and every file that makes one must be a source of that row:
a new place that writes known_hosts, the managed SSH config or its Include line, the checkout's data folder, VS Code's
extension, an editor's window or a saved session, without its row naming it, fails here. CLAUDE.md's invariant names
every row.
"""

from __future__ import annotations

import re

import yaml
from harness import REPO, repo_texts

LIST = REPO / "playbooks" / "vars" / "controller_writes.yml"
DATA = yaml.safe_load(LIST.read_text())
WRITES = DATA["code_controller_writes"]
FIELDS = ("id", "path", "what", "writer", "when", "removed_by", "sources", "invariant")

# How each row's write looks where it is made.
MADE_BY = {
    "known_hosts": r"ansible\.builtin\.known_hosts:|StrictHostKeyChecking=accept-new",
    "ssh_config": r"(?:path|dest|creates): \"\{\{ claude_ssh_config_file \}\}\"",
    "ssh_include": r"line: \"Include \{\{ claude_ssh_config_file \}\}\"",
    "data": r"tools/env-run --data\)",
    "vscode_extension": r"--install-extension",
    "editor_records": r"argv: \"\{\{ code_plan\.launch \}\}\"",
    "mcp": r"mcp add --scope",
    "sessions_dir": r"(?m)^\s*claude_sessions_fetch:",
}
# A file that matches a row's pattern without writing on this machine, and why.
NOT_HERE = {
    "roles/github_projects/tasks/clone.yml": "git's ssh on the VM, which writes the VM's own known_hosts",
    "playbooks/status.yml": "a stat of the managed SSH config: just status reads it",
}


def _code() -> list[tuple[str, str]]:
    """The checkout's tracked files that run, by path: not its tests, its scenarios or its Markdown."""
    return [
        (str(path.relative_to(REPO)), text)
        for path, text in repo_texts(untracked=True)
        if not str(path.relative_to(REPO)).startswith(("tests/", "molecule/", "docs/"))
        and path.suffix != ".md"
        and path != LIST
    ]


def test_every_row_says_where_what_who_when_and_what_takes_it_out():
    assert [row["id"] for row in WRITES] == list(MADE_BY)
    for row in WRITES:
        assert set(row) - {"setting", "editor"} == set(FIELDS), row["id"]
        assert all(row[field] for field in FIELDS), row["id"]
        assert not re.search(r"/(?:Users|home)/", str(row)), "a path is said as ~/..., never a home's own"
    assert DATA["code_controller_also"]


def test_every_place_that_makes_a_row_s_write_is_one_of_its_sources():
    code = _code()
    for row in WRITES:
        made = {path for path, text in code if re.search(MADE_BY[row["id"]], text)} - set(NOT_HERE)
        assert made == set(row["sources"]), (row["id"], "name each place in its row's sources, or the row is stale")


def test_every_row_s_setting_is_a_setting():
    # The notes show the setting's value where it moves the row's file: a setting renamed would leave it unsaid.
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert {row["setting"] for row in WRITES if "setting" in row} <= set(defaults)


def test_the_invariant_names_every_row():
    text = " ".join((REPO / "CLAUDE.md").read_text().split())
    assert [row["id"] for row in WRITES if row["invariant"] not in text] == []


def test_the_notes_and_the_security_model_read_the_list():
    code = (REPO / "playbooks" / "code.yml").read_text()
    assert "    - vars/controller_writes.yml\n" in code.split("  vars_files:\n", 1)[1].split("  vars:\n", 1)[0]
    template = (REPO / "playbooks" / "templates" / "vm_session.md.j2").read_text()
    assert "{% for w in code_controller_writes if " in template
    page = (REPO / "docs" / "architecture" / "security-model.md").read_text()
    assert "<!-- BEGIN_GENERATED: controller-writes -->" in page
    assert all(row["path"].replace("|", "\\|") in page for row in WRITES)
