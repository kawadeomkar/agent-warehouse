"""A run an agent started, kept on disk so that it outlives the call, the server and the session that started it.

A deploy takes minutes, and its sign-ins wait in the person's browser; Claude Code moves a call into the background
after two minutes and ends its server with the session. So a run tool starts a job: a supervisor in a session of its
own (tools/mcp --supervise) runs the recipe and writes .cache/mcp/jobs/<id>/ - job.json, what the job is and how it
ended, and output.txt, what the run printed, made plain - and any server, of this session or a later one, follows it
there. A job is running while its supervisor's pid still runs the process of the start it recorded, never by pid alone.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from claude_vm import proc
from claude_vm.desktop import PS_ENV
from claude_vm.text import visible_text

ID_HEX = 16
# How long a person's stop waits for the run to end as a Ctrl-C ends it, before the whole tree is ended.
STOP_GRACE = 30


def jobs_dir(checkout: Path) -> Path:
    return checkout / ".cache" / "mcp" / "jobs"


def started(pid: int) -> str:
    """A process's start as ps says it, or "" when there is none: with its pid, what tells it from a later one. Read in
    C and UTC, its spaces collapsed, as tools/check lock writes it: in the reader's locale and zone, or with the padding
    macOS's ps adds, one process's start would read as two (desktop.PS_ENV). ps gets nothing else of the environment,
    which the server reads only through environment.py; it is found on the default path."""
    out = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, check=False, env=PS_ENV
    )
    return " ".join(out.stdout.split())


@dataclass
class Job:
    folder: Path

    @property
    def id(self) -> str:
        return self.folder.name

    @property
    def output(self) -> Path:
        return self.folder / "output.txt"

    def read(self) -> dict:
        try:
            return json.loads((self.folder / "job.json").read_text())
        except (OSError, ValueError):
            return {}

    def write(self, record: dict) -> None:
        # A scratch file of this process's own: two writers never replace each other's.
        scratch = self.folder / f"job.json.{os.getpid()}.tmp"
        scratch.write_text(json.dumps(record))
        os.replace(scratch, self.folder / "job.json")

    def update(self, **fields) -> dict:
        """fields written into the record, and the record as written. Under a lock: the server and the supervisor both
        write it, and a write between another's read and its write would be lost."""
        with open(self.folder / "job.lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            record = self.read() | fields
            self.write(record)
        return record

    def state(self) -> str:
        """running, ok, failed, stopped, or gone: a supervisor that ended without saying how the run did."""
        record = self.read()
        if "exit" in record:
            return "stopped" if record.get("stopped") else "ok" if record["exit"] == 0 else "failed"
        pid, since = record.get("supervisor"), record.get("supervisor_since")
        if pid and since and started(pid) == since:
            return "running"
        return "gone"

    def text(self, since: int = 0) -> tuple[str, int]:
        """What the run printed from byte since on, and where that ends."""
        try:
            with open(self.output, "rb") as stream:
                stream.seek(since)
                data = stream.read()
        except OSError:
            return "", since
        # Only whole lines: the rest is read next time.
        cut = data.rfind(b"\n") + 1
        return data[:cut].decode(errors="replace"), since + cut


def find(checkout: Path, job_id: str) -> Job | None:
    if len(job_id) != ID_HEX or not all(c in "0123456789abcdef" for c in job_id):
        return None
    folder = jobs_dir(checkout) / job_id
    return Job(folder) if (folder / "job.json").is_file() else None


def listed(checkout: Path) -> list[Job]:
    """Every job, newest first."""
    folder = jobs_dir(checkout)
    found = [Job(path) for path in folder.iterdir() if (path / "job.json").is_file()] if folder.is_dir() else []
    return sorted(found, key=lambda job: job.read().get("started", 0), reverse=True)


def prune(checkout: Path, days: int) -> None:
    """Jobs ended more than days ago, as the run logs are."""
    limit = time.time() - days * 86400
    for job in listed(checkout):
        record = job.read()
        if record.get("ended", time.time()) < limit:
            shutil.rmtree(job.folder, ignore_errors=True)


def start(checkout: Path, python: str, argv: list[str], env: dict[str, str], tool: str, title: str) -> Job:
    """A new job running argv, its supervisor started in a session of its own with the signals at their defaults."""
    folder = jobs_dir(checkout)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder.parent, 0o700)
    job = Job(folder / secrets.token_hex(ID_HEX // 2))
    job.folder.mkdir(mode=0o700)
    job.output.touch(mode=0o600)
    job.write({"id": job.id, "tool": tool, "title": title, "argv": argv, "started": time.time()})
    supervisor = subprocess.Popen(
        [python, "-I", str(checkout / "tools" / "mcp"), f"--supervise={job.folder}"],
        cwd=checkout,
        env={**env, "AGENT_WAREHOUSE_JOB": job.id},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        preexec_fn=default_signals,
    )
    job.update(supervisor=supervisor.pid, supervisor_since=started(supervisor.pid))
    return job


def lock_holder(checkout: Path) -> str:
    """The run holding the checkout's run lock (tools/check lock's record), said, or nothing when it is free."""
    try:
        pid, since, title, job = ((checkout / ".cache" / "run.lock").read_text().split("\n") + [""] * 4)[:4]
    except OSError:
        return ""
    if not (pid.isdigit() and since and started(int(pid)) == since):
        return ""
    return f"just {title} is running (pid {pid}, started {since} UTC{f', MCP job {job}' if job else ''})"


def default_signals() -> None:
    """In a child, before it runs: the signals a person's Ctrl-C and a closing terminal send, at their defaults,
    whatever the parent was started with - a parent that ignored them would pass the ignore down."""
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(number, signal.SIG_DFL)


def supervise(folder: Path) -> int:
    """tools/mcp --supervise: run the job's argv in a process group of its own, append each line it prints to
    output.txt made plain, and say how it ended. A stop is a SIGINT to that group (stop())."""
    job = Job(folder)
    record = job.read()
    # The run's group is the run's: a Ctrl-C sent to it never ends this.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    run = subprocess.Popen(
        record["argv"],
        cwd=os.getcwd(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        process_group=0,
        preexec_fn=default_signals,
    )
    record = job.update(pid=run.pid, since=started(run.pid))
    # A stop that came before the run had a pid had nothing to signal, and left it to this (stop()).
    if record.get("stopped"):
        interrupt(record)
    with open(job.output, "a", encoding="utf-8") as out:
        assert run.stdout is not None
        for raw in run.stdout:
            out.write(visible_text(raw.decode(errors="replace")).rstrip("\n") + "\n")
            out.flush()
    code = run.wait()
    job.update(exit=code, ended=time.time())
    return 0


def stop(job: Job, reason: str) -> None:
    """A person's stop, recorded, and sent to the run when it has a pid; before then, the supervisor sends it once it
    has one. Both write the record under its lock, so exactly one of the two finds both the stop and the pid."""
    interrupt(job.update(stopped=reason))


def interrupt(record: dict) -> None:
    """SIGINT to the record's run's group, as a terminal's Ctrl-C, while it is the process of its start; the whole tree
    ended after STOP_GRACE if it is still there - ansible-core's task workers leave the group (custom-code.md §7.10) -
    read before the SIGINT, since a run that ends at it leaves them under PID 1, where nothing leads to them."""
    pid, since = record.get("pid"), record.get("since")
    if not pid or started(pid) != since:
        return
    try:
        remembered = proc.snapshot(pid)
    except proc.Blind:
        remembered = {}
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGINT)
    subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            _END_LATER,
            str(Path(__file__).resolve().parents[2]),
            str(pid),
            since,
            json.dumps(remembered),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


# Run detached by interrupt(): after STOP_GRACE, the run's whole tree, read before anything is signalled, with what
# was under it at the stop, while its root is still the process of its start; once it has ended, what it left of the
# tree remembered, each still the process of its start, never by the root's pid.
_END_LATER = f"""
import json, signal, sys, time
sys.path.insert(0, sys.argv[1])
from claude_vm import proc
from claude_vm.mcp.jobs import started
pid, since = int(sys.argv[2]), sys.argv[3]
remembered = {{int(each): start for each, start in json.loads(sys.argv[4]).items()}}
for signum, wait in ((signal.SIGTERM, {STOP_GRACE}), (signal.SIGKILL, 5)):
    time.sleep(wait)
    if started(pid) == since:
        proc.end(pid, remembered=remembered, signum=signum)
    else:
        proc.end_left(remembered, signum=signum)
"""
