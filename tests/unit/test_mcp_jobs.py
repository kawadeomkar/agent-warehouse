"""The MCP server's runs: a recipe that changes something runs as a job that outlives the call, the server and the
session (lib/claude_vm/mcp/jobs.py), which the call follows (runs.py).

As test_mcp_server.py, the server runs a copy of this checkout through the real just, its BIN stand-ins; here
ansible-playbook does what the test's behaviour file says - print lines, a browser block, wait for a file - and records
what it was given. The run lock is the copy's own, in its .cache.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from harness import GIT_ENV
from test_mcp_server import SERVER, _args, _copy, _env, _just, _text

from claude_vm.mcp import cancels, jobs, runs, server
from claude_vm.mcp.config import Config
from claude_vm.mcp.environment import run_env

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
    block = "\n".join(f'echo "{line}"' for line in BLOCK)
    _behave(world, f"trap 'echo interrupted; exit 130' INT\necho started\n{block}\necho\nwhile :; do sleep 0.2; done\n")
    _, (waiting,) = _talk(world, [("deploy", {"names": ["alpha"], "wait_seconds": 30})])
    assert "waiting for the user in the browser" in _text(waiting)
    job_id = _job_id(waiting)
    time.sleep(1)
    # The first server is gone with its connection; the job is not.
    found = jobs.find(world["checkout"], job_id)
    assert found.state() == "running"
    _, (listed, stopped) = _talk(world, [("jobs", {}), ("cancel", {"id": job_id})])
    assert job_id in _text(listed) and "running" in _text(listed)
    # The stop's result: what the run printed as it stopped, never the browser block an earlier call returned.
    text = _text(stopped)
    assert "interrupted" in text and found.state() == "stopped", text
    assert "waiting for the user" not in text and BLOCK[3] not in text and "started" not in text


def test_a_stop_before_the_run_has_a_pid_is_sent_once_it_has_one(tmp_path):
    # The supervisor loads the server before it starts the run: a person's Esc in that second found no pid to signal.
    job = jobs.Job(tmp_path / "job")
    job.folder.mkdir()
    job.output.touch()
    job.write({"id": "job", "argv": ["sleep", "60"], "started": time.time()})
    jobs.stop(job, "user-cancel")
    assert "pid" not in job.read()
    subprocess.run(
        [sys.executable, "-I", str(SERVER), f"--supervise={job.folder}"], cwd=tmp_path, timeout=30, check=True
    )
    assert job.state() == "stopped" and job.read()["exit"] != 0


def test_the_server_s_and_the_supervisor_s_writes_are_never_lost_to_each_other(tmp_path):
    job = jobs.Job(tmp_path / "job")
    job.folder.mkdir()
    job.write({})
    writer = (
        "import sys; from pathlib import Path; from claude_vm.mcp.jobs import Job\n"
        "job = Job(Path(sys.argv[1]))\n"
        "for i in range(100): job.update(**{f'{sys.argv[2]}{i}': i})\n"
    )
    env = {**os.environ, "PYTHONPATH": str(Path(jobs.__file__).resolve().parents[2])}
    writers = [subprocess.Popen([sys.executable, "-c", writer, str(job.folder), name], env=env) for name in "ab"]
    assert [each.wait(timeout=60) for each in writers] == [0, 0]
    assert set(job.read()) == {f"{name}{i}" for name in "ab" for i in range(100)}


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
        # Taken as a terminal's run takes it, in another zone than the server's.
        lock = co / ".cache" / "run.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        check = [str(co / "tools" / "check"), "lock", str(lock), str(holder.pid), "deploy beta"]
        subprocess.run(check, env={**os.environ, "TZ": "XYZ-5:30"}, check=True)
        _behave(world, "echo ran\n")
        _, (busy,) = _talk(world, [("login", {"names": ["alpha"]})])
        assert busy.is_error and "just deploy beta is running" in _text(busy)
    finally:
        holder.kill()
        holder.wait()


def test_a_deploy_that_would_create_a_vm_needs_its_names(world):
    before = len(jobs.listed(world["checkout"]))
    _, results = _talk(world, [("deploy", {}), ("deploy", {"only": "provision"}), ("deploy", {"start": True})])
    for result in results:
        assert result.is_error and "none were: name them" in _text(result)
    assert len(jobs.listed(world["checkout"])) == before


def test_the_stages_that_create_nothing_still_run_without_names(world):
    _behave(world, 'echo "PLAY RECAP all ok"\n')
    arguments = ({"only": "configure"}, {"only": "ssh-config"}, {"only": "template"}, {"check": True})
    _, results = _talk(world, [("deploy", given) for given in arguments])
    for result in results:
        assert not result.is_error, _text(result)


def test_install_reads_no_vault_so_one_it_cannot_open_does_not_stop_it(world):
    uv = world["root"] / "uv"
    synced = world["root"] / "uv-synced"
    uv.write_text(f'#!/bin/sh\necho "$@" >> "{synced}"\n')
    uv.chmod(0o755)
    args = [*(arg for arg in _args(world) if not arg.startswith("--uv=")), f"--uv={uv}"]
    password = world["checkout"] / ".vault_pass"
    password.rename(password.with_suffix(".aside"))
    try:
        _, (result,) = _talk(world, [("install", {})], args=args)
    finally:
        password.with_suffix(".aside").rename(password)
    assert not result.is_error, _text(result)
    assert synced.read_text().startswith("sync ")


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
    # The HOME just mcp recorded, never the session's.
    config = Config(checkout=tmp_path, python="", just="", uv="", path="", home=str(tmp_path))
    environ = {"CLAUDE_PROJECT_DIR": project, "HOME": "/nonexistent/evil-home"}
    assert "no conversation of this project's" in server.session_refusal(config, environ, session)
    transcript = folder / f"{session}.jsonl"
    transcript.write_text("{}\n")
    assert server.session_refusal(config, environ, session) == ""
    os.utime(transcript, (time.time() - 120, time.time() - 120))
    assert "last minute" in server.session_refusal(config, environ, session)
    assert server.session_refusal(config, environ, "") == ""
    assert "not a Claude Code session ID" in server.session_refusal(config, environ, "../../etc/passwd")
    # Under the CLAUDE_CONFIG_DIR just mcp recorded: its Claude Code keeps sessions there, and just code reads them.
    work = replace(config, claude_config_dir=str(tmp_path / "claude-work"))
    assert "no conversation of this project's" in server.session_refusal(work, environ, session)
    (tmp_path / "claude-work" / "projects" / folder.name).mkdir(parents=True)
    (tmp_path / "claude-work" / "projects" / folder.name / f"{session}.jsonl").write_text("{}\n")
    assert server.session_refusal(work, environ, session) == ""


def test_a_run_s_claude_config_dir_is_the_recorded_one_or_none():
    config = Config(checkout=Path("/x"), python="", just="", uv="", path="/usr/bin", home="/home/dev")
    session = {"CLAUDE_CONFIG_DIR": "/nonexistent/evil-claude", "HOME": "/nonexistent/evil-home"}
    assert "CLAUDE_CONFIG_DIR" not in run_env(config, session) and run_env(config, session)["HOME"] == "/home/dev"
    work = replace(config, claude_config_dir="/home/dev/.claude-work")
    assert run_env(work, session)["CLAUDE_CONFIG_DIR"] == "/home/dev/.claude-work"


def test_a_stop_remembers_the_run_s_tree_before_it_signals_it(tmp_path, monkeypatch):
    # A run that ends at the SIGINT leaves what left its group - ansible-core's task worker - under PID 1, where the end
    # STOP_GRACE later could not find it from the run: it is read first, and handed to that end.
    marker = tmp_path / "worker"
    script = (
        "import subprocess, sys, time\n"
        "worker = subprocess.Popen(['sleep', '60'], start_new_session=True)\n"
        "open(sys.argv[1], 'w').write(str(worker.pid))\n"
        "time.sleep(60)\n"
    )
    run = subprocess.Popen([sys.executable, "-c", script, str(marker)], process_group=0)
    worker = 0
    try:
        deadline = time.monotonic() + 20
        while not (marker.exists() and marker.read_text().isdigit()):
            assert time.monotonic() < deadline, "the worker's pid not written"
            time.sleep(0.1)
        worker = int(marker.read_text())
        later: list[list[str]] = []
        recorder = SimpleNamespace(
            run=subprocess.run, DEVNULL=subprocess.DEVNULL, Popen=lambda argv, **_: later.append(argv)
        )
        monkeypatch.setattr(jobs, "subprocess", recorder)
        jobs.interrupt({"pid": run.pid, "since": jobs.started(run.pid)})
        monkeypatch.undo()
        (argv,) = later
        assert worker in {int(each) for each in json.loads(argv[-1])}
        assert run.wait(timeout=20) != 0
    finally:
        run.kill()
        run.wait()
        if worker:
            with contextlib.suppress(ProcessLookupError):
                os.kill(worker, signal.SIGKILL)
