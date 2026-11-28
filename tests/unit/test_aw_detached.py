"""A detached run: the same program in a session of its own, which makes its own job, takes its own locks, writes its
own record and keeps its own limit (docs/architecture/engine.md §16.8, runs/detached.py).

What is held, against a stand-in of the program (STANDIN: tools/aw's own main, every verb's handler a stand-in run that
prints, emits and ends, through runs.run) started as a person starts aw: the record, the text log and the exit record
of a detached run; the starter returning only once the run has said it is under way, with what the run said before
said again; a refusal at the locks said by the starter, exit 1, no job's folder left, and the run proceeding once the
holder has ended; a run that ends before it is under way ending the starter with its status; a SIGKILL reading lost; a
limit of two seconds ending a run that sleeps, failed with the deadline as why, and a stop asked first staying its end;
the run outliving its starter and a closed pseudo-terminal; its log followed with logs <id> --follow; the two lines the
starter prints the printer's, parsed back to the same values; each of the seven verbs the table marks changes started
detached, and --detach on every other verb an unknown option, refused before anything runs; the caller's environment,
a proxy among it, and the program as started reaching the run; no word in time, or a Ctrl-C of the starter's, ending
the run with what it started, a process that left its group among it; start_detached's child unmarked, in a session of
its own, on /dev/null, its standard output and error the one pipe, and never handed a lock; and the limit's default
the setting's. Every child a test starts has its interrupts at their defaults (harness._default_signals), and every
run a test leaves is ended with its tree (claude_vm.proc) when the test ends.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import harness
import pytest
from aw_kit import environment, run_aw
from harness import REPO

from claude_vm import runs
from claude_vm.cli import parse
from claude_vm.fleet import paths, registry
from claude_vm.runs import children, detached
from claude_vm.runs import job as jobs
from claude_vm.schema import check as checks
from claude_vm.schema import printer
from claude_vm.schema.words import EXITS, VERBS

# The machine every stand-in run names, by its identity, and its name.
IDENTITY = "agent-warehouse-4c4c4544-0042-3510-8051-b4c04f4e4b33"
# A program that is tools/aw's own main with every verb's handler a stand-in run: it says what it was told to before its
# run, then makes its job through runs.run, its body printing a line, emitting a stage, a report of what it was given
# and its machine's end, after a sleep where it is told one. What it is told comes in its environment, which a detached
# run keeps from its starter.
STANDIN = r"""
import asyncio, dataclasses, os, sys, time
sys.path.insert(0, {lib!r})
from pathlib import Path
from claude_vm import runs
from claude_vm.cli import dispatch, main
from claude_vm.fleet import paths
from claude_vm.runs import textlog
from claude_vm.schema import printer, words

IDENTITY = {identity!r}
env = os.environ


def handler(verb):
    def standing_in(run, **values):
        Path(env["STANDIN_PIDS"], str(os.getpid())).write_text(verb)
        if env.get("STANDIN_REFUSE"):
            print(env["STANDIN_REFUSE"], file=sys.stderr)
            return 1
        if env.get("STANDIN_EXIT"):
            return int(env["STANDIN_EXIT"])
        if env.get("STANDIN_HALF"):
            sys.stderr.write(env["STANDIN_HALF"])
            sys.stderr.flush()
        if env.get("STANDIN_BEFORE"):
            print("standin: before its run", file=sys.stderr, flush=True)
            time.sleep(float(env["STANDIN_BEFORE"]))
        home = paths.home(Path(__file__))
        names = values.get("names") or ([values["name"]] if values.get("name") else [])
        name = names[0] if names else "alpha"

        async def body(ctx):
            print(f"standin {{verb}}: hello", flush=True)
            ctx.job.emit("stage", IDENTITY, "provision", name=name)
            said = f"program {{printer.program()}} proxy {{env.get('HTTPS_PROXY', '')}} attended {{run.attended}}"
            ctx.job.emit("report", IDENTITY, said)
            if env.get("STANDIN_SLEEP"):
                await asyncio.sleep(float(env["STANDIN_SLEEP"]))
            ctx.job.emit("end", IDENTITY, "provisioned", outcome="succeeded")
            return 0

        draft = runs.Draft(
            paths.jobs_dir(home), verb, f"standin {{verb}}", "pve", [IDENTITY], {{"kind": "terminal"}}, True,
            values=values, lock_machines=[("pve", each) for each in names], names={{IDENTITY: name}},
            logs=textlog.logs(home, run), detached_limit=float(env.get("STANDIN_LIMIT", runs.DETACHED_LIMIT)),
        )
        return runs.run(draft, body)

    return standing_in


for each in list(words.VERBS):
    words.VERBS[each] = dataclasses.replace(words.VERBS[each], handler=f"standin:{{each}}")
dispatch._load = lambda named: handler(named.partition(":")[2])
sys.exit(main.main([__file__, *sys.argv[1:]]))
"""
# The words each verb the table marks changes is started detached with.
CHANGING = {
    "deploy": ["alpha"],
    "login": ["alpha"],
    "code": ["alpha"],
    "destroy": ["alpha"],
    "release": ["pi"],
    "init": [],
    "install": [],
}
# What a process that leaves its group starts, as ansible-core's task worker does: a grandchild in a session of its own,
# both pids written to the file it is given, then a sleep that never says it is under way.
LEAVES_ITS_GROUP = r"""
import subprocess, sys, time
left = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True)
with open(sys.argv[1], "w") as pids:
    pids.write(f"{__import__('os').getpid()} {left.pid}")
time.sleep(120)
"""
# What a child of start_detached tells of itself, on its standard output.
PROBE = r"""
import json, os, sys
null, given, out, err = os.stat(os.devnull), os.fstat(0), os.fstat(1), os.fstat(2)
def is_open(fd):
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False
print(json.dumps({
    "session": os.getsid(0) == os.getpid(),
    "marker": os.environ.get("AGENT_WAREHOUSE_JOB"),
    "stdin": (given.st_rdev, given.st_ino) == (null.st_rdev, null.st_ino),
    "one pipe": (out.st_dev, out.st_ino) == (err.st_dev, err.st_ino),
    "open": [fd for fd in range(3, 256) if is_open(fd)],
}), flush=True)
"""


@dataclass
class Standin:
    """The stand-in program, the test's home and its jobs folder; and every process it started, to end."""

    program: Path
    home: Path
    jobs_dir: Path
    pids: Path
    started: list[subprocess.Popen] = field(default_factory=list)

    def env(self, **given: str) -> dict[str, str]:
        return environment(self.home, {"STANDIN_PIDS": str(self.pids), **given})

    def start(self, *words: str, **given: str) -> subprocess.Popen:
        """The program started with words, as a person starts aw, in a process group of its own."""
        process = subprocess.Popen(
            [sys.executable, "-I", str(self.program), *words],
            cwd=self.home,
            env=self.env(**given),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            process_group=0,
            preexec_fn=harness._default_signals,  # signal alone, safe beside xdist's thread
        )
        self.started.append(process)
        return process

    def run(self, *words: str, timeout: float = 120, **given: str) -> subprocess.CompletedProcess[str]:
        process = self.start(*words, **given)
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            harness._end(process)
            pytest.fail(f"the starter of {words} did not end within {timeout} seconds")
        return subprocess.CompletedProcess(process.args, process.returncode, out, err)

    def detached(self, *words: str, **given: str) -> tuple[subprocess.CompletedProcess[str], jobs.Job]:
        """words started detached, the starter's end and the job it said."""
        ended = self.run(*words, "--detach", **given)
        assert ended.returncode == 0, ended
        job = jobs.find(self.jobs_dir, ended.stdout.splitlines()[0])
        assert job is not None, ended
        return ended, job

    def handled(self) -> dict[int, str]:
        """Each process a stand-in handler ran in, by its pid, with its verb."""
        return {int(path.name): path.read_text() for path in self.pids.iterdir()}


@pytest.fixture
def standin(home: Path, tmp_path: Path) -> Iterator[Standin]:
    """The stand-in program, beside a fleet's files in the test's home; every run it left ended with its tree."""
    fleet = paths.home(REPO)
    paths.fleet_dir(fleet).mkdir(parents=True)
    program = tmp_path / "bin" / "aw"
    program.parent.mkdir()
    program.write_text(STANDIN.format(lib=str(REPO / "lib"), identity=IDENTITY))
    pids = tmp_path / "pids"
    pids.mkdir()
    made = Standin(program, home, paths.jobs_dir(fleet), pids)
    try:
        yield made
    finally:
        for process in made.started:
            if process.poll() is None:
                harness._end(process)
            for stream in (process.stdout, process.stderr):
                if stream:
                    stream.close()
        for pid in made.handled():
            _gone(pid)


def _gone(pid: int) -> None:
    """pid ended with everything under it, where it still runs (claude_vm.proc, never its group alone)."""
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, 0)
        harness._processes().end(pid, signum=signal.SIGKILL)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _until(done, within: float = 60, what: str = "it") -> None:
    end = time.monotonic() + within
    while not done():
        if time.monotonic() > end:
            pytest.fail(f"{what} did not come within {within} seconds")
        time.sleep(0.1)


def _ended(job: jobs.Job, within: float = 60) -> dict:
    """The record of job once its run has ended, its lock free."""
    _until(lambda: not jobs.lock_held(job), within, f"the end of job {job.id}")
    return job.read()


def _said(stderr: str) -> list[str]:
    """What a starter said on standard error but the dispatch's line of which fleet it drives, the test's own home."""
    return [line for line in stderr.splitlines() if not line.startswith("aw drives the fleet in ")]


def _log(record: dict) -> str:
    return Path(record["log"]).read_text()


def _listed(standin: Standin) -> list[str]:
    return sorted(each.name for each in standin.jobs_dir.iterdir()) if standin.jobs_dir.exists() else []


# ---------------------------------------------------------------- the run ----


def test_a_detached_run_writes_its_record_its_text_log_and_its_exit_record(standin) -> None:
    ended, job = standin.detached("deploy", "alpha")
    record = _ended(job)
    assert jobs.state(job) == "succeeded"
    assert record["starter"] == {"kind": "detach"} and record["attached"] is False
    assert record["outcome"] == "succeeded" and record["exit"] == 0 and record["why"] == "done"
    assert record["machine_outcomes"][IDENTITY]["outcome"] == "succeeded"
    # Its own limit, the setting's default, by the computer's clock.
    assert record["deadline"] == pytest.approx(record["started"] + runs.DETACHED_LIMIT, abs=60)
    log = _log(record)
    assert Path(record["log"]).parent == paths.logs_dir(paths.home(REPO))
    # What the run printed, and what it emitted, are in its log: its output was pointed there once it was under way.
    assert "standin deploy: hello" in log, log
    assert f"job {job.id}: standin deploy" in log and " report alpha: program aw" in log, log
    assert " end: done" in log, log
    # Nothing of the run reached the starter once it was under way.
    assert "hello" not in ended.stdout + ended.stderr


def test_the_starter_returns_only_once_the_run_is_under_way_and_says_what_it_said_before(standin) -> None:
    began = time.monotonic()
    ended, job = standin.detached("deploy", "alpha", STANDIN_BEFORE="2", STANDIN_SLEEP="30")
    assert time.monotonic() - began >= 2
    assert _said(ended.stderr) == ["standin: before its run"], ended.stderr
    # Under way: its locks held, its log open, its record its own.
    assert jobs.state(job) == "running"
    record = job.read()
    assert Path(record["log"]).is_file() and record["locks"], record


def test_half_a_line_said_before_the_run_is_under_way_is_said_whole_and_never_hides_its_word(standin) -> None:
    # Review 99's finding 1: text with no newline on the shared pipe, then `started <id>`, read as one line, left the
    # starter waiting for a run already under way, and then killing it.
    ended, job = standin.detached("deploy", "alpha", STANDIN_HALF="standin: no newline", STANDIN_SLEEP="8")
    assert _said(ended.stderr) == ["standin: no newline"], ended.stderr
    assert jobs.state(job) == "running"
    assert _ended(job)["outcome"] == "succeeded"


def test_a_run_ended_by_a_signal_is_said_by_its_signal() -> None:
    said = (detached._how(3), detached._how(-9), detached._how(-15))
    assert said == ("exit 3", "killed by SIGKILL", "killed by SIGTERM")


def test_a_run_another_run_holds_the_machine_of_is_refused_by_the_starter_and_proceeds_once_it_has_ended(
    standin,
) -> None:
    _, first = standin.detached("deploy", "alpha", STANDIN_SLEEP="4")
    refused = standin.run("deploy", "alpha", "--detach")
    assert refused.returncode == EXITS["refused"] and refused.stdout == "", refused
    assert "alpha" in refused.stderr and first.id in refused.stderr, refused.stderr
    # A refusal leaves no job's folder: the holder's alone is there.
    assert _listed(standin) == [first.id]
    assert _ended(first)["outcome"] == "succeeded"
    _, second = standin.detached("deploy", "alpha")
    assert _ended(second)["outcome"] == "succeeded"


def test_a_run_that_ends_before_it_is_under_way_ends_the_starter_with_its_status_and_what_it_said(standin) -> None:
    sentence = "error: the stand-in refuses this run"
    refused = standin.run("deploy", "alpha", "--detach", STANDIN_REFUSE=sentence)
    assert (refused.returncode, refused.stdout, _said(refused.stderr)) == (EXITS["refused"], "", [sentence]), refused
    silent = standin.run("deploy", "alpha", "--detach", STANDIN_EXIT="3")
    assert (silent.returncode, _said(silent.stderr)) == (3, [detached.SILENT.format(how="exit 3")]), silent
    assert _listed(standin) == []


def test_a_run_killed_with_sigkill_reads_lost(standin) -> None:
    _, job = standin.detached("deploy", "alpha", STANDIN_SLEEP="60")
    os.kill(job.read()["pid"], signal.SIGKILL)
    record = _ended(job)
    assert jobs.state(job) == "lost" and "outcome" not in record, record


def test_a_limit_of_two_seconds_ends_a_run_that_sleeps_failed_with_the_deadline_as_why(standin) -> None:
    began = time.monotonic()
    _, job = standin.detached("deploy", "alpha", STANDIN_LIMIT="2", STANDIN_SLEEP="60")
    record = _ended(job)
    assert time.monotonic() - began < 30
    assert jobs.state(job) == "failed" and record["exit"] == EXITS["failed"], record
    assert record["why"] == "the deadline: run_detached_limit's 2 s came", record
    assert record["deadline"] == pytest.approx(record["started"] + 2, abs=20)
    assert record["machine_outcomes"][IDENTITY]["outcome"] == "failed"
    assert "the run's limit of 2 s (run_detached_limit) came" in _log(record)


def test_a_stop_asked_before_the_limit_stays_the_run_s_end_its_grace_cut_short_there(standin) -> None:
    began = time.monotonic()
    _, job = standin.detached("deploy", "alpha", STANDIN_LIMIT="3", STANDIN_SLEEP="60")
    # A cancel's SIGINT, while the run waits on its sleep and nothing forks.
    os.kill(job.read()["pid"], signal.SIGINT)
    record = _ended(job)
    assert time.monotonic() - began < 25, "the grace of 30 seconds was not cut short at the limit"
    assert (record["outcome"], record["why"]) == ("cancelled", "Ctrl-C"), record


def test_the_run_outlives_its_starter_and_a_closed_pseudo_terminal(standin) -> None:
    status, said = harness._at_a_pseudo_terminal(
        [sys.executable, "-I", str(standin.program), "deploy", "alpha", "--detach"],
        standin.home,
        standin.env(STANDIN_SLEEP="6"),
        timeout=120,
    )
    assert status == 0, said
    job_id = re.search(rb"^([0-9a-f]{16})\r?$", said, re.M)
    assert job_id is not None, said
    job = jobs.find(standin.jobs_dir, job_id.group(1).decode())
    assert job is not None
    # The starter has ended and its terminal is closed; the run goes on, and ends as it would have.
    assert jobs.state(job) == "running"
    assert _ended(job)["outcome"] == "succeeded"


def test_a_detached_run_s_log_is_followed_with_logs_follow(standin) -> None:
    _, job = standin.detached("deploy", "alpha", STANDIN_SLEEP="3")
    followed = run_aw("logs", job.id, "--follow", home=standin.home, timeout=120)
    assert followed.returncode == 0, followed
    assert "standin deploy: hello" in followed.stdout and "succeeded" in followed.stdout, followed.stdout


def test_the_two_lines_the_starter_prints_are_the_printer_s_and_parse_back_to_the_same_values(standin) -> None:
    ended, job = standin.detached("deploy", "alpha")
    follow, cancel = {"job": job.id, "follow": True}, {"job": job.id}
    assert ended.stdout.splitlines() == [
        job.id,
        printer.line("logs", follow, refused=False),
        printer.line("cancel", cancel, refused=False),
    ]
    for line, verb, values in zip(ended.stdout.splitlines()[1:], ("logs", "cancel"), (follow, cancel), strict=True):
        read = parse.read(shlex.split(line)[1:])
        assert (read.verb, read.values) == (verb, values)


@pytest.mark.parametrize("verb", sorted(CHANGING))
def test_each_verb_whose_run_is_a_job_runs_detached(standin, verb) -> None:
    assert set(CHANGING) == {each.name for each in VERBS.values() if each.changes}
    _, job = standin.detached(verb, *CHANGING[verb])
    record = _ended(job)
    assert (record["verb"], record["starter"], record["attached"]) == (verb, {"kind": "detach"}, False)
    assert record["outcome"] == "succeeded", record
    # The handler ran in the detached run alone, never in its starter.
    assert list(standin.handled().values()) == [verb]


@pytest.mark.parametrize("verb", sorted(each.name for each in VERBS.values() if not each.changes))
def test_detach_on_a_verb_whose_run_is_no_job_is_refused_before_anything_runs(standin, verb) -> None:
    refused = standin.run(verb, "--detach")
    assert (refused.returncode, refused.stderr) == (EXITS["refused"], checks.no_option(verb, "--detach") + "\n")
    assert standin.handled() == {} and _listed(standin) == []


def test_the_run_keeps_its_starter_s_environment_a_proxy_and_the_program_as_started_among_it(standin) -> None:
    proxy = "http://127.0.0.1:9"
    ended, job = standin.detached("deploy", "alpha", HTTPS_PROXY=proxy, AGENT_WAREHOUSE_STARTED_AS="./aw")
    assert ended.stdout.splitlines()[1].startswith("./aw logs "), ended.stdout
    assert f" report alpha: program ./aw proxy {proxy} attended False" in _log(_ended(job))


def test_attended_given_to_the_starter_reaches_the_run(standin) -> None:
    _, job = standin.detached("--attended=1", "deploy", "alpha")
    assert " attended True" in _log(_ended(job))


# ---------------------------------------------------------------- the starter's own ends ----


def test_a_run_not_under_way_in_time_is_ended_with_a_process_that_left_its_group(tmp_path) -> None:
    pids = tmp_path / "pids"
    out, err = io.StringIO(), io.StringIO()
    status = detached.detach(
        [sys.executable, "-I", "-c", LEAVES_ITS_GROUP, str(pids)], dict(os.environ), within=4, out=out, err=err
    )
    assert (status, out.getvalue()) == (EXITS["refused"], "")
    assert err.getvalue() == detached.LATE.format(within=4) + "\n"
    run, left = map(int, pids.read_text().split())
    try:
        _until(lambda: not _alive(run) and not _alive(left), 20, "the end of the run and of what left its group")
    finally:
        _gone(left)


def test_a_ctrl_c_of_the_starter_ends_a_run_not_yet_under_way(standin) -> None:
    starter = standin.start("deploy", "alpha", "--detach", STANDIN_BEFORE="60")
    assert starter.stderr is not None
    # Each line the starter says, till the run's own: the starter's wait is bounded (detached.STARTING), so this is.
    for _ in range(4):
        said = starter.stderr.readline()
        if not said or said.startswith("standin: "):
            break
    assert said == "standin: before its run\n"
    [run] = standin.handled()
    starter.send_signal(signal.SIGINT)
    assert starter.wait(timeout=60) == EXITS["cancelled"]
    assert starter.stderr.read() == detached.STOPPED + "\n"
    _until(lambda: not _alive(run), 20, "the end of the run")
    assert _listed(standin) == []


# ---------------------------------------------------------------- start_detached ----


def test_start_detached_s_child_is_unmarked_in_a_session_of_its_own_on_dev_null_with_one_pipe_for_both() -> None:
    reading, writing = os.pipe()
    try:
        try:
            process = children.start_detached(
                [sys.executable, "-I", "-c", PROBE], {**os.environ, children.MARKER: "0123456789abcdef"}, writing
            )
        finally:
            os.close(writing)
        said = b""
        while chunk := os.read(reading, 65536):
            said += chunk
    finally:
        os.close(reading)
    assert process.wait(timeout=60) == 0
    assert json.loads(said) == {"session": True, "marker": None, "stdin": True, "one pipe": True, "open": []}


def test_start_detached_never_hands_a_lock(tmp_path) -> None:
    made = jobs.create(tmp_path / "jobs", "deploy", "x", None, [], {"kind": "terminal"}, True)
    try:
        [held] = [fd for fd, name in jobs.locks_held().items() if made.id in name]
        with pytest.raises(children.LockRefused, match="never handed a lock"):
            children.start_detached([sys.executable, "-I", "-c", "pass"], dict(os.environ), held)
    finally:
        jobs.drop(made)


def test_the_limit_is_the_setting_s_minutes_and_its_default_the_setting_s() -> None:
    assert runs.DETACHED_LIMIT == registry.row("run_detached_limit").default * 60
    assert runs.Draft(Path("."), "deploy", "", None, [], {"kind": "terminal"}, True, values={}).detached_limit == (
        runs.DETACHED_LIMIT
    )
    assert (detached.limit_of(0), detached.limit_of(5)) == (0, 300)
    for wrong in (-1, True, "5", 1.5):
        with pytest.raises(ValueError, match="whole number of minutes"):
            detached.limit_of(wrong)
