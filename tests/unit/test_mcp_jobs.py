"""The MCP server's runs: a recipe that changes something runs as a job that outlives the call, the server and the
session (lib/claude_vm/mcp/jobs.py), which the call follows (runs.py).

As test_mcp_server.py, the server runs a copy of this checkout through the real just, its BIN stand-ins; here
ansible-playbook does what the test's behaviour file says - print lines, a browser block, wait for a file - and records
what it was given. The run lock is the copy's own, in its .cache.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from harness import GIT_ENV
from test_mcp_server import SERVER, _args, _copy, _env, _just, _text

from claude_vm.mcp import cancels, jobs, runs, server

BLOCK = [
    "#" * 80,
    "##  ACTION REQUIRED IN YOUR BROWSER  -  alpha  -  Claude",
    "#" * 80,
    "Approve the sign-in in your browser: https://claude.ai/oauth/authorize?code=abc",
]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("jobs")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (bin_dir / "python").chmod(0o755)
    (bin_dir / "ansible-galaxy").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "ansible-galaxy").chmod(0o755)
    record = root / "argv"
    playbook = bin_dir / "ansible-playbook"
    playbook.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{record}"\n'
        f'printf "%s\\n" "${{AGENT_WAREHOUSE_JOB:-}}" > "{root}/job"\n'
        f'. "{root}/behaviour"\n'
    )
    playbook.chmod(0o755)
    home = root / "home"
    home.mkdir()
    return {
        "root": root,
        "checkout": _copy(root),
        "bin": bin_dir,
        "just": _just(root, bin_dir),
        "home": home,
        "path": f"{Path(shutil.which('just')).parent}{os.pathsep}/usr/bin{os.pathsep}/bin",
    }


def _behave(world: dict, script: str) -> None:
    (world["root"] / "behaviour").write_text(script)


async def _calls(args, env, steps, client_name="claude-code", client_version="2.1.319"):
    """One client, one connection: each step is (tool, arguments) or a callable run between calls."""
    from mcp import Client, StdioServerParameters
    from mcp_types import Implementation

    params = StdioServerParameters(command=sys.executable, args=["-I", str(SERVER), *args], env=env)
    results = []
    info = Implementation(name=client_name, version=client_version)
    async with Client(params, client_info=info) as client:
        listed = await client.list_tools()
        for step in steps:
            if callable(step):
                step(results)
            else:
                results.append(await client.call_tool(*step))
    return {tool.name: tool for tool in listed.tools}, results


def _talk(world, steps, extra_env=None, args=None, **client):
    env = _env(world, extra_env)
    return asyncio.run(asyncio.wait_for(_calls(args or _args(world), env, steps, **client), 180))


def _job_id(result) -> str:
    return _text(result).split(", job ")[1].split()[0].strip(",")


def test_a_run_is_a_job_its_lines_its_result_and_attended_for_an_interactive_session(world):
    _behave(world, 'echo "TASK one"\necho "PLAY RECAP all ok"\n')
    _, (result,) = _talk(world, [("deploy", {"names": ["alpha"]})], {"CLAUDE_CODE_ENTRYPOINT": "cli"})
    text = _text(result)
    assert not result.is_error, text
    assert ": ok in " in text and "PLAY RECAP all ok" in text and text.startswith("on main at ")
    argv = (world["root"] / "argv").read_text().splitlines()
    # ATTENDED=1: the pages open and the sign-ins run, and nothing asks.
    assert '{"claude_vm_login_terminal":false}' in argv and "deploy_sign_in" not in " ".join(argv)
    job = jobs.find(world["checkout"], _job_id(result))
    assert job is not None and job.state() == "ok"
    assert (world["root"] / "job").read_text().strip() == job.id
    assert oct(job.folder.stat().st_mode & 0o777) == "0o700"


@pytest.mark.parametrize(
    ("env", "args", "attended"),
    [
        ({"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}, {}, False),
        ({"CLAUDE_CODE_ENTRYPOINT": "cli", "SSH_CONNECTION": "192.0.2.1 22 192.0.2.2 22"}, {}, False),
        ({"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}, {"attended": "1"}, True),
        ({"CLAUDE_CODE_ENTRYPOINT": "cli"}, {"attended": "0"}, False),
    ],
)
def test_attended_is_an_interactive_session_here_unless_just_mcp_said(world, env, args, attended):
    _behave(world, "echo done\n")
    _, (result,) = _talk(world, [("login", {"names": ["alpha"]})], env, args=_args(world, **args))
    assert not result.is_error, _text(result)
    argv = " ".join((world["root"] / "argv").read_text().splitlines())
    assert ("deploy_sign_in" not in argv) == attended, argv


def test_the_call_returns_at_a_wait_for_the_user_and_job_follows_the_run_to_its_end(world):
    release = world["root"] / "release"
    release.unlink(missing_ok=True)
    block = "\n".join(f'echo "{line}"' for line in BLOCK)
    _behave(world, f'echo "TASK before"\n{block}\necho\nwhile [ ! -e "{release}" ]; do sleep 0.2; done\necho "after"\n')

    def go_on(results):
        release.touch()

    tools, results = _talk(world, [("deploy", {"names": ["alpha"]}), go_on])
    waiting = _text(results[0])
    assert "waiting for the user in the browser" in waiting and BLOCK[3] in waiting
    since = int(waiting.split("since=")[1].split(")")[0])
    job_id = _job_id(results[0])
    _, (ended,) = _talk(world, [("job", {"id": job_id, "since": since})])
    text = _text(ended)
    assert ": ok in " in text and "after" in text
    # The block was the earlier call's: not said again.
    assert BLOCK[3] not in text


def test_a_job_outlives_its_server_and_cancel_stops_it_as_ctrl_c(world):
    _behave(world, "trap 'echo interrupted; exit 130' INT\necho started\nwhile :; do sleep 0.2; done\n")
    _, (running,) = _talk(world, [("deploy", {"names": ["alpha"], "wait_seconds": 2})])
    assert "still running" in _text(running)
    job_id = _text(running).split("job ")[1].split()[0]
    time.sleep(1)
    # The first server is gone with its connection; the job is not.
    found = jobs.find(world["checkout"], job_id)
    assert found.state() == "running"
    _, (listed, stopped) = _talk(world, [("jobs", {}), ("cancel", {"id": job_id})])
    assert job_id in _text(listed) and "running" in _text(listed)
    assert "interrupted" in _text(stopped) and found.state() == "stopped"


def test_a_branch_or_a_held_lock_is_refused_before_anything_runs(world):
    co = world["checkout"]
    env = {**os.environ, **GIT_ENV}
    subprocess.run(["git", "checkout", "-q", "-b", "topic"], cwd=co, env=env, check=True)
    try:
        _, (refused,) = _talk(world, [("deploy", {"names": ["alpha"]})])
    finally:
        subprocess.run(["git", "checkout", "-q", "main"], cwd=co, env=env, check=True)
    assert refused.is_error and "just mcp --any-branch" in _text(refused)
    holder = subprocess.Popen(["sleep", "60"])
    try:
        since = jobs.started(holder.pid)
        lock = co / ".cache" / "run.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(f"{holder.pid}\n{since}\ndeploy beta\n\n")
        _behave(world, "echo ran\n")
        _, (busy,) = _talk(world, [("login", {"names": ["alpha"]})])
        assert busy.is_error and "just deploy beta is running" in _text(busy)
    finally:
        holder.kill()
        holder.wait()


def test_install_asks_the_user_in_claude_code_s_own_prompt_and_runs_for_no_other_client(world):
    tools, results = _talk(world, [("install", {})], client_name="claude-ai", client_version="1.0")
    assert tools["install"].meta["anthropic/requiresUserInteraction"] is True
    assert results[0].is_error and "Ask the user to run it at a terminal: just install" in _text(results[0])
    _, (old,) = _talk(world, [("install", {})], client_version="2.1.308")
    assert old.is_error and "this client is claude-code 2.1.308" in _text(old)


def test_a_person_s_stop_is_esc_or_x_in_tasks_and_nothing_else():
    cancels.REASONS.clear()
    for request, reason in (
        (7, "AbortError: user-cancel"),
        ("8", "AbortError: The operation was aborted."),
        (9, "SdkError: Request timed out"),
    ):
        params = {"requestId": request, "reason": reason}
        cancels.note(json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": params}).encode())
    cancels.note(b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"notifications/cancelled"}}')
    assert cancels.reason("7") in cancels.STOPS and cancels.reason("8") in cancels.STOPS
    assert cancels.reason("9") not in cancels.STOPS and cancels.reason("10") == ""


def test_a_browser_block_is_found_whole():
    lines = ["TASK x", *BLOCK, "Waiting 300 s", "", "TASK y"]
    start, end = runs.browser_block(lines)
    assert lines[start:end] == [*BLOCK, "Waiting 300 s"]
    assert runs.browser_block(["TASK x", "nothing"]) is None


def test_a_session_must_be_a_transcript_of_the_calling_project_written_just_now(tmp_path):
    project = "/Users/somebody/code/x.y"
    folder = tmp_path / ".claude" / "projects" / "-Users-somebody-code-x-y"
    folder.mkdir(parents=True)
    session = "0a608ad8-1b2c-4d3e-8f90-123456789abc"
    environ = {"CLAUDE_PROJECT_DIR": project, "HOME": str(tmp_path)}
    assert "no conversation of this project's" in server.session_refusal(environ, session)
    transcript = folder / f"{session}.jsonl"
    transcript.write_text("{}\n")
    assert server.session_refusal(environ, session) == ""
    os.utime(transcript, (time.time() - 120, time.time() - 120))
    assert "last minute" in server.session_refusal(environ, session)
    assert server.session_refusal(environ, "") == ""
    assert "not a Claude Code session ID" in server.session_refusal(environ, "../../etc/passwd")
