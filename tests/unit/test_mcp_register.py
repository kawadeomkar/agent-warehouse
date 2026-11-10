"""just mcp registers the MCP server with Claude Code, through a stand-in claude first on PATH that records what it was
asked and keeps the one entry it was given: no test runs the real claude, or reads or writes ~/.claude.json.

It runs a copy of the justfile and tools/ in a git repository of its own, so that the branch it is on is the test's to
choose, with BIN pointing at stand-ins as test_justfile.py's runs do.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from harness import GIT_ENV

REPO = Path(__file__).resolve().parents[2]
JUST = shutil.which("just")
VAULT = "vault.yml"


@pytest.fixture(scope="module")
def copy(tmp_path_factory) -> Path:
    """The justfile and tools/ in a git repository on main, one commit."""
    root = tmp_path_factory.mktemp("register") / "checkout"
    root.mkdir()
    shutil.copy2(REPO / "justfile", root / "justfile")
    shutil.copytree(REPO / "tools", root / "tools")
    env = {**os.environ, **GIT_ENV}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "copy"]):
        subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)
    return root


def _claude(bin_dir: Path, version: str = "2.1.319", keeps: bool = True) -> Path:
    """A claude that records each call - its arguments, one per line, and the folder it ran in - and keeps the entry
    `mcp add` gives it until `mcp remove`: `mcp get` finds it (unless keeps is false, an add another session's save
    lost)."""
    record = bin_dir.parent / "claude.args"
    entry = bin_dir.parent / "entry"
    claude = bin_dir / "claude"
    claude.write_text(
        "#!/bin/sh\n"
        f'{{ printf "cwd=%s\\n" "$(pwd -P)"; for arg in "$@"; do printf "%s\\n" "$arg"; done; echo --end--; }}'
        f' >> "{record}"\n'
        f'[ "$1" != --version ] || {{ echo "{version} (Claude Code)"; exit 0; }}\n'
        f'case "$2" in add) {"echo added > " + str(entry) if keeps else ":"} ;; remove) rm -f "{entry}" ;;'
        f' get) [ -f "{entry}" ] || exit 1 ;; esac\n'
    )
    claude.chmod(0o755)
    return record


def _calls(record: Path) -> list[list[str]]:
    text = record.read_text() if record.exists() else ""
    return [call.splitlines() for call in text.split("--end--\n")[:-1]]


def _setup(tmp_path: Path, copy: Path, *, vault_pass: bool = True, **claude) -> tuple[dict, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # A script, not a link: a link's Python, outside the virtualenv's folder, is the virtualenv's base, without its
    # packages.
    (bin_dir / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (bin_dir / "python").chmod(0o755)
    record = _claude(bin_dir, **claude)
    (tmp_path / VAULT).write_text("$ANSIBLE_VAULT;1.1;AES256\n3132\n")
    if vault_pass:
        (tmp_path / "vault_pass").write_text("x\n")
    settings = [
        f"BIN={bin_dir}",
        f"VAULT_FILE={tmp_path / VAULT}",
        f"VAULT_PASS={tmp_path / 'vault_pass'}",
    ]
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{Path(JUST).parent}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "HOME": str(tmp_path),
        "LANG": "en_US.UTF-8",
    }
    return {"settings": settings, "env": env, "copy": copy}, record


def _mcp(run: dict, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [JUST, "--justfile", str(run["copy"] / "justfile"), *run["settings"], "mcp", *args],
        cwd=cwd or run["copy"],
        env=run["env"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_it_registers_every_value_as_an_argument_reads_it_back_and_prints_the_rules(tmp_path, copy):
    run, record = _setup(tmp_path, copy)
    result = _mcp(run)
    assert result.returncode == 0, result.stderr
    calls = _calls(record)
    add = next(call for call in calls if call[1:3] == ["mcp", "add"])
    assert add[0] == f"cwd={copy.resolve()}"
    assert add[3:8] == ["--scope", "user", "--transport", "stdio", "agent-warehouse"]
    command = add[add.index("--") + 1 :]
    python = str(tmp_path / "bin" / "python")
    assert command[:3] == [python, "-I", str(copy / "tools" / "mcp")]
    assert command[3:] == [
        f"--checkout={copy}",
        f"--python={python}",
        f"--just={JUST}",
        f"--uv={shutil.which('uv', path=run['env']['PATH']) or 'uv'}",
        f"--path={run['env']['PATH']}",
        f"--home={tmp_path}",
    ]
    assert calls[-1][1:4] == ["mcp", "get", "agent-warehouse"]
    assert '"mcp__agent-warehouse__status"' in result.stdout and '"Bash(just destroy:*)"' in result.stdout


def test_xdg_data_home_is_recorded_where_it_is_set(tmp_path, copy):
    # tools/env-run finds the checkout's folder, and the collections Ansible loads, under it: a run takes it from the
    # registration, never from the session, whose project could set it.
    run, record = _setup(tmp_path, copy)
    run["env"]["XDG_DATA_HOME"] = str(tmp_path / "data")
    result = _mcp(run)
    assert result.returncode == 0, result.stderr
    add = next(call for call in _calls(record) if call[1:3] == ["mcp", "add"])
    assert add[-2:] == [f"--home={tmp_path}", f"--data-home={tmp_path / 'data'}"]


def test_claude_config_dir_is_recorded_where_it_is_set(tmp_path, copy):
    # The Claude Code whose user scope the server is registered in keeps its sessions there: the code tool's session is
    # looked for there, and just code copies them from there.
    run, record = _setup(tmp_path, copy)
    run["env"]["CLAUDE_CONFIG_DIR"] = str(tmp_path / "claude-work")
    result = _mcp(run)
    assert result.returncode == 0, result.stderr
    add = next(call for call in _calls(record) if call[1:3] == ["mcp", "add"])
    assert add[-1] == f"--claude-config-dir={tmp_path / 'claude-work'}"


def test_a_second_registration_replaces_the_first(tmp_path, copy):
    run, record = _setup(tmp_path, copy)
    assert _mcp(run).returncode == 0
    result = _mcp(run, "--attended=1", "--any-branch")
    assert result.returncode == 0, result.stderr
    calls = [call[1:3] for call in _calls(record) if call[1] == "mcp"]
    assert calls[-4:] == [["mcp", "get"], ["mcp", "remove"], ["mcp", "add"], ["mcp", "get"]]
    assert "in place of the one registered before" in result.stdout
    add = [call for call in _calls(record) if call[1:3] == ["mcp", "add"]][-1]
    assert add[-2:] == ["--attended=1", "--any-branch"]


def test_an_add_another_session_s_save_lost_is_said(tmp_path, copy):
    run, _ = _setup(tmp_path, copy, keeps=False)
    result = _mcp(run)
    assert result.returncode != 0 and "Run just mcp again" in result.stderr


@pytest.mark.parametrize("version", ["2.1.308", "1.0.0", "garbage"])
def test_a_claude_older_than_the_oldest_tried_is_refused(tmp_path, copy, version):
    run, record = _setup(tmp_path, copy, version=version)
    result = _mcp(run)
    assert result.returncode != 0 and "older than 2.1.309" in result.stderr
    assert not [call for call in _calls(record) if call[1:3] == ["mcp", "add"]]


def test_another_branch_or_a_tracked_change_is_refused_unless_any_branch(tmp_path, copy):
    run, record = _setup(tmp_path, copy)
    env = {**os.environ, **GIT_ENV}
    subprocess.run(["git", "checkout", "-q", "-b", "topic"], cwd=copy, env=env, check=True)
    try:
        result = _mcp(run)
        assert result.returncode != 0 and "is on topic" in result.stderr and "--any-branch" in result.stderr
        assert _mcp(run, "--any-branch").returncode == 0
    finally:
        subprocess.run(["git", "checkout", "-q", "main"], cwd=copy, env=env, check=True)
    tools_check = copy / "tools" / "check"
    text = tools_check.read_text()
    tools_check.write_text(text + "\n")
    try:
        result = _mcp(run)
        assert result.returncode != 0 and "with tracked changes" in result.stderr
    finally:
        tools_check.write_text(text)


def test_an_encrypted_vault_without_its_password_file_is_refused(tmp_path, copy):
    run, _ = _setup(tmp_path, copy, vault_pass=False)
    result = _mcp(run)
    assert result.returncode != 0 and "just init makes one" in result.stderr
    (tmp_path / "elsewhere").write_text("x\n")
    result = _mcp(run, f"--vault-password-file={tmp_path / 'elsewhere'}")
    assert result.returncode == 0, result.stderr


def test_show_prints_the_command_and_an_entry_for_claude_desktop_and_registers_nothing(tmp_path, copy):
    run, record = _setup(tmp_path, copy)
    result = _mcp(run, "--show")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("claude mcp add --scope user --transport stdio agent-warehouse -- ")
    desktop = json.loads(result.stdout[result.stdout.index("{") :])
    entry = desktop["mcpServers"]["agent-warehouse"]
    assert entry["command"] == str(tmp_path / "bin" / "python") and entry["args"][-1] == "--attended=1"
    assert not [call for call in _calls(record) if call[1] == "mcp"]


def test_remove_takes_it_out_and_project_dir_registers_it_for_that_repository_alone(tmp_path, copy):
    run, record = _setup(tmp_path, copy)
    project = tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    assert _mcp(run, f"--project-dir={project / 'sub'}").returncode == 0
    add = [call for call in _calls(record) if call[1:3] == ["mcp", "add"]][-1]
    assert add[0] == f"cwd={project.resolve()}" and add[3:5] == ["--scope", "local"]
    assert _mcp(run, "--remove", f"--project-dir={project}").returncode == 0
    remove = _calls(record)[-1]
    assert remove[1:] == ["mcp", "remove", "--scope", "local", "agent-warehouse"]
    assert remove[0] == f"cwd={project.resolve()}"


def test_a_word_after_the_options_or_remove_with_show_is_refused(tmp_path, copy):
    run, record = _setup(tmp_path, copy)
    assert "nothing after them" in _mcp(run, "test").stderr
    assert "one or the other" in _mcp(run, "--remove", "--show").stderr
    assert _calls(record) == []
