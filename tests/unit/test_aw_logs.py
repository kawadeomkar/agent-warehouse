"""A run's plain-text log, its pruning, and `aw logs` (docs/architecture/engine.md §16.8).

What is held: a canned run's log, line for line, each machine said by its name; the log's name for each verb whose run
is a job, from a part its pattern holds and never a machine's name; the folder kept at 0700 and the log made new at
0600, never through a link; LOG_DIR empty leaving a job's folder and no log for a run at a terminal, and a detached
run's log in its job's folder; a run through runs.run writing its log and pruning first; a prune that deletes an old
log, an old ended job, a lost one with the directory its run left and a folder with no record, and keeps a live job of
any age, a link and whatever is not named as a log or a job; with LOG_RETENTION_DAYS=0 and every time set back a year,
the home's vault-key, client.yml and fleet/ byte for byte as they were, whatever a record names; `aw logs` listing
newest first and saying a lost job without changing its record; `aw logs <job>` printing its log and `--save` its path;
`--follow`, at a terminal (aw_kit's aw_at_a_terminal), printing each line as the job writes it and ending with the job,
its outcome and each machine's; a Ctrl-C there ending the follow with 130 and the job running on; an ID that is not
sixteen hex digits refused; emit handing every listener the event whatever one before it raised; the record keeping the
names given at create; and the loop's own reports going to the log. Every job another process holds is made by a Python
of its own (CHILD), never by the test's, which would hold its lock for the test's life. What `aw logs` imports is
tests/unit/test_aw_cli_imports.py's.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import types
from collections.abc import Iterator
from pathlib import Path

import harness
import pytest
from aw_kit import aw_at_a_terminal, run_aw
from harness import REPO

from claude_vm import runs
from claude_vm.fleet import paths
from claude_vm.runs import attached, prune, textlog
from claude_vm.runs import job as jobs
from claude_vm.schema.patterns import PATTERNS
from claude_vm.schema.words import EXITS, VERBS

# A machine by its identity, as a clone gives one, and the name a person chose.
IDENTITY = "agent-warehouse-4c4c4544-0042-3510-8051-b4c04f4e4b33"
OTHER = "agent-warehouse-4c4c4544-0042-3510-8051-b4c04f4e4b34"
YEAR = 366 * 86400
# A run in a Python of its own: it makes its job, with a text log in the folder it is given ("-" for no text log, ""
# for LOG_DIR empty), emits its first stage, says its ID, and then, unless it is to be lost, says each line it is sent
# ("say <text>") until "end" or the end of its input, and ends with its machine's end and its own.
CHILD = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from claude_vm.runs import job as jobs, textlog
jobs_dir, log_dir, then, identity = sys.argv[2:6]
made = jobs.create(
    Path(jobs_dir), "deploy", "aw deploy alpha", "pve", [identity], {"kind": "terminal"}, True,
    names={identity: "alpha"},
)
if log_dir != "-":
    textlog.start(made, textlog.Logs(Path(log_dir) if log_dir else None), "deploy", True)
made.emit("stage", identity, "provision")
print(made.id, flush=True)
if then == "lost":
    os._exit(0)
for line in sys.stdin:
    word, _, text = line.rstrip("\n").partition(" ")
    if word == "end":
        break
    made.emit("report", identity, text)
    print("said", flush=True)
made.emit("end", identity, "provisioned", outcome="succeeded")
made.emit("end", None, "deployed", outcome="succeeded", exit=0)
"""
# A whole run through runs.run, in a Python of its own: its text log in the folder it is given ("" for LOG_DIR empty),
# its body one stage of one machine; then the run's job's ID.
RUN = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from claude_vm import runs
from claude_vm.runs import textlog
jobs_dir, log_dir, identity = sys.argv[2:5]
seen = {}

async def body(ctx):
    seen["id"] = ctx.job.id
    ctx.job.emit("stage", identity, "provision", name="alpha")
    return 0

draft = runs.Draft(
    Path(jobs_dir), "deploy", "aw deploy alpha", "pve", [identity], {"kind": "terminal"}, True,
    values={"names": ["alpha"]}, lock_machines=[("pve", "alpha")], names={identity: "alpha"},
    logs=textlog.Logs(Path(log_dir) if log_dir else None, "0"),
)
status = runs.run(draft, body)
print(json.dumps({"status": status, "id": seen.get("id")}), flush=True)
"""


@pytest.fixture
def jobs_dir(home: Path) -> Path:
    """The jobs folder of the test's own fleet home, as fleet/paths.py names it."""
    return paths.jobs_dir(paths.home(REPO))


@pytest.fixture
def log_dir(home: Path) -> Path:
    """The run logs' folder of the test's own fleet home: its state folder's logs/, LOG_DIR's default there."""
    return paths.logs_dir(paths.home(REPO))


def _child(jobs_dir: Path, log_dir: Path | str, then: str = "ended", identity: str = IDENTITY) -> subprocess.Popen:
    """A run in a Python of its own (CHILD), its input a pipe where it is to run on; its ID its first line."""
    argv = [sys.executable, "-I", "-c", CHILD, str(REPO / "lib"), str(jobs_dir), str(log_dir), then, identity]
    stdin = subprocess.PIPE if then == "running" else subprocess.DEVNULL
    return subprocess.Popen(argv, stdin=stdin, stdout=subprocess.PIPE, text=True, start_new_session=True)


def _made(process: subprocess.Popen) -> str:
    said = process.stdout.readline().strip() if process.stdout else ""
    assert jobs._is_id(said), f"the run said no job's ID: {said!r}"
    return said


def _ran(jobs_dir: Path, log_dir: Path | str, then: str = "ended") -> str:
    """A run that has ended (or been lost), in a Python of its own: its job's ID."""
    process = _child(jobs_dir, log_dir, then)
    try:
        made = _made(process)
        assert process.wait(timeout=60) == 0
        return made
    finally:
        if process.poll() is None:
            harness._end(process)
        if process.stdout:
            process.stdout.close()


@pytest.fixture
def running(jobs_dir: Path, log_dir: Path) -> Iterator[tuple[subprocess.Popen, str]]:
    """A run that goes on until it is told to end, in a Python of its own, with its text log: the process and its ID."""
    process = _child(jobs_dir, log_dir, "running")
    try:
        yield process, _made(process)
    finally:
        if process.poll() is None:
            harness._end(process)
        for stream in (process.stdin, process.stdout):
            if stream:
                stream.close()


def _tell(process: subprocess.Popen, line: str) -> None:
    assert process.stdin is not None
    process.stdin.write(line + "\n")
    process.stdin.flush()


def _said(output: bytes) -> list[str]:
    """What a run at a terminal wrote, line by line."""
    return output.decode(errors="replace").replace("\r\n", "\n").splitlines()


def _back(path: Path, by: float = YEAR) -> None:
    """path's times set back by seconds, its own and never a link's target's."""
    found = os.lstat(path)
    os.utime(path, (found.st_atime - by, found.st_mtime - by), follow_symlinks=False)


def _rewrite(job: jobs.Job, **fields: object) -> None:
    """A record's fields set as a test needs them, written as it is on disk: never through the job's own writer."""
    job.record.write_text(json.dumps({**json.loads(job.record.read_text()), **fields}))


def _everything(folder: Path) -> dict[str, bytes | None]:
    """Every entry under folder, a file by its bytes, a link by its target, a folder as None."""
    found: dict[str, bytes | None] = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            found[str(path.relative_to(folder))] = os.readlink(path).encode()
        else:
            found[str(path.relative_to(folder))] = path.read_bytes() if path.is_file() else None
    return found


# ---------------------------------------------------------------- the text log ----


def test_a_canned_run_s_log_is_written_line_for_line_each_machine_by_its_name(jobs_dir, log_dir) -> None:
    made = jobs.create(
        jobs_dir, "deploy", "aw deploy alpha", "pve", [IDENTITY], {"kind": "terminal"}, True, names={IDENTITY: "alpha"}
    )
    try:
        log = textlog.start(made, textlog.Logs(log_dir), "deploy", True)
        assert log is not None and made.read()["log"] == str(log.path)
        stand_in = jobs.stand_in()
        events = [
            made.emit("stage", IDENTITY, "provision"),
            made.emit("step", IDENTITY, "clone", state="done"),
            made.emit("report", IDENTITY, "line one\nline \x1b[31mtwo\x1b[0m"),
            made.emit("waiting", IDENTITY, "Approve the sign-in in your browser", page="https://claude.ai/x"),
            made.emit("stage", stand_in, "provision", name="beta"),
            made.emit("join", OTHER, stand_in),
            made.emit("end", OTHER, "made", outcome="succeeded"),
            made.emit("end", IDENTITY, "provisioned", outcome="succeeded"),
            made.emit("end", None, "deployed", outcome="succeeded", exit=0),
        ]
        at = [textlog._clock(event.time) for event in events]
        written = log.path.read_text().splitlines()
        assert written[0].endswith(f" aw: job {made.id}: aw deploy alpha"), written
        assert written[1:] == [
            f"{at[0]} stage alpha: provision",
            f"{at[1]} step alpha: clone · state=done",
            f"{at[2]} report alpha: line one",
            f"{at[2]} report alpha: line two",
            f"{at[3]} waiting alpha: Approve the sign-in in your browser · page=https://claude.ai/x",
            f"{at[4]} stage beta: provision",
            f"{at[5]} join beta: {OTHER}",
            f"{at[6]} end beta: made · outcome=succeeded",
            f"{at[7]} end alpha: provisioned · outcome=succeeded",
            f"{at[8]} end: deployed · outcome=succeeded · exit=0",
        ]
        assert stand_in not in log.path.read_text()
    finally:
        textlog.finish(made)
        jobs.drop(made)


@pytest.mark.parametrize("verb", sorted(name for name, verb in VERBS.items() if verb.changes))
def test_a_log_is_named_by_the_run_s_start_its_verb_and_a_part_its_pattern_holds(verb) -> None:
    started = time.mktime((2026, 10, 7, 20, 30, 15, 0, 0, -1))
    assert textlog.name(started, verb) == f"20261007-203015-{verb}.log"
    for part in ("configure", "ssh-config", "check", "github"):
        assert textlog.name(started, verb, part) == f"20261007-203015-{verb}-{part}.log"
    assert textlog.name(started, verb, None, 2) == f"20261007-203015-{verb}-2.log"
    # Never a machine's name, nor any word no pattern of a part holds.
    for part in ("alpha", "", "configure-alpha", "../x", "Configure"):
        with pytest.raises(ValueError, match="no part of a log's name"):
            textlog.name(started, verb, part)


@pytest.mark.parametrize("verb", ["status", "logs", "bogus"])
def test_a_verb_whose_run_is_no_job_has_no_log(verb) -> None:
    with pytest.raises(ValueError, match="no verb whose run is a job"):
        textlog.name(time.time(), verb)


def test_the_folder_is_kept_at_0700_and_each_log_made_new_at_0600_never_through_a_link(tmp_path) -> None:
    made = tmp_path / "a" / "logs"
    textlog._kept(made)
    assert made.stat().st_mode & 0o777 == 0o700
    folder = tmp_path / "open"
    folder.mkdir(mode=0o755)
    folder.chmod(0o755)
    textlog._kept(folder)
    assert folder.stat().st_mode & 0o777 == 0o700
    started = time.mktime((2026, 10, 7, 20, 30, 15, 0, 0, -1))
    target = tmp_path / "elsewhere"
    target.write_text("kept\n")
    (folder / "20261007-203015-deploy.log").symlink_to(target)
    first, held = textlog._open(folder, started, "deploy", None)
    os.close(held)
    second, held = textlog._open(folder, started, "deploy", None)
    os.close(held)
    # The link's name is taken: the next free names are used, and the link's target is never written.
    assert (first.name, second.name) == ("20261007-203015-deploy-2.log", "20261007-203015-deploy-3.log")
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in (first, second))
    assert target.read_text() == "kept\n"


def test_log_dir_empty_leaves_a_job_folder_and_no_log_for_a_run_at_a_terminal(jobs_dir, home) -> None:
    made = jobs.create(jobs_dir, "deploy", "aw deploy alpha", "pve", [], {"kind": "terminal"}, True)
    try:
        assert textlog.start(made, textlog.Logs(None), "deploy", True) is None
        made.emit("end", None, "deployed", outcome="succeeded", exit=0)
        assert made.record.is_file() and made.read()["log"] is None
        logs = list(home.rglob("*.log"))
        assert logs == [], logs
    finally:
        textlog.finish(made)
        jobs.drop(made)


def test_log_dir_empty_puts_a_detached_run_s_log_in_its_job_s_folder(jobs_dir) -> None:
    made = jobs.create(jobs_dir, "deploy", "aw deploy alpha", "pve", [], {"kind": "detach"}, False)
    try:
        log = textlog.start(made, textlog.Logs(None), "deploy", False)
        assert log is not None and log.path.parent == made.folder
        made.emit("end", None, "deployed", outcome="succeeded", exit=0)
        assert log.path.read_text().splitlines()[-1].endswith(" end: deployed · outcome=succeeded · exit=0")
    finally:
        textlog.finish(made)
        jobs.drop(made)


@pytest.mark.parametrize("logged", [True, False], ids=["LOG_DIR", "LOG_DIR empty"])
def test_a_run_prunes_first_then_writes_its_log_and_names_it_in_its_record(jobs_dir, log_dir, logged) -> None:
    old = jobs.Job(jobs_dir / _ran(jobs_dir, log_dir))
    _rewrite(old, ended=time.time() - YEAR)
    argv = [sys.executable, "-I", "-c", RUN, str(REPO / "lib"), str(jobs_dir), str(log_dir) if logged else "", IDENTITY]
    ran = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        preexec_fn=harness._default_signals,  # noqa: PLW1509 - only signal, safe beside xdist's thread
    )
    assert ran.returncode == 0, ran.stderr
    said = json.loads(ran.stdout.splitlines()[-1])
    made = jobs.find(jobs_dir, said["id"])
    assert said["status"] == 0 and made is not None and jobs.state(made) == "succeeded"
    if logged:
        assert not old.folder.exists(), "a run that writes a log prunes the jobs that ended longer ago"
        written = Path(made.read()["log"]).read_text().splitlines()
        assert Path(made.read()["log"]).parent == log_dir
        assert written[1].endswith(" stage alpha: provision") and " end: " in written[-1], written
    else:
        assert old.folder.exists(), "a run that writes no log prunes nothing"
        assert made.read()["log"] is None


# ---------------------------------------------------------------- the prune ----


def test_a_prune_deletes_old_logs_and_ended_jobs_and_keeps_a_live_one_of_any_age(
    jobs_dir, log_dir, running, tmp_path
) -> None:
    process, live_id = running
    live = jobs.Job(jobs_dir / live_id)
    ended = jobs.Job(jobs_dir / _ran(jobs_dir, log_dir))
    lost = jobs.Job(jobs_dir / _ran(jobs_dir, "-", "lost"))
    assert jobs.state(lost) == "lost"
    left = tmp_path / f"aw-run-{lost.id}-abc123"
    (left / "inventory").mkdir(parents=True)
    _rewrite(lost, directory=str(left))
    _rewrite(ended, ended=time.time() - YEAR)
    _rewrite(live, started=time.time() - YEAR, ended=time.time() - YEAR)
    empty = jobs_dir / "0123456789abcdef"
    empty.mkdir()
    stranger = jobs_dir / "not-a-job"
    stranger.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = jobs_dir / "fedcba9876543210"
    linked.symlink_to(outside, target_is_directory=True)
    old_log, new_log, other = (log_dir / name for name in ("20251007-203015-deploy.log", "2026-1-deploy.log", "x.log"))
    target = tmp_path / "a-file"
    target.write_text("kept\n")
    link_log = log_dir / "20251007-203015-link.log"
    for path in (old_log, new_log, other):
        path.write_text("a run\n")
    link_log.symlink_to(target)
    for path in (old_log, other, link_log, empty, stranger, linked, live.folder, live.record, lost.record):
        _back(path)
    pruned = prune.prune(log_dir, jobs_dir, "0")
    assert pruned.said == []
    assert sorted(pruned.jobs) == sorted([ended.id, lost.id, empty.name])
    assert pruned.logs == [old_log] and pruned.directories == [left]
    assert not any(path.exists() for path in (ended.folder, lost.folder, empty, left, old_log))
    assert all(os.path.lexists(path) for path in (new_log, other, link_log, stranger, linked, outside))
    assert target.read_text() == "kept\n"
    # The live job, a year old by every time it has, is kept whole: its lock is held.
    assert jobs.state(live) == "running" and live.record.is_file()
    _tell(process, "end")
    assert process.wait(timeout=60) == 0


def test_a_retention_that_is_no_number_prunes_nothing_and_says_so(jobs_dir, log_dir) -> None:
    old_log = log_dir / "20251007-203015-deploy.log"
    log_dir.mkdir(parents=True)
    old_log.write_text("a run\n")
    _back(old_log)
    for retention in ("", "ten", "-1", "1.5", "１"):
        pruned = prune.prune(log_dir, jobs_dir, retention)
        assert pruned.said == ["warning: LOG_RETENTION_DAYS must be a number of days; nothing pruned"]
    assert old_log.exists()


def test_a_linked_log_dir_is_refused_before_anything_in_it_is_pruned_and_its_target_s_logs_are_kept(
    tmp_path, jobs_dir, capsys
) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    old_log = target / "20251007-203015-deploy.log"
    old_log.write_text("a run of last year\n")
    _back(old_log)
    link = tmp_path / "logs"
    link.symlink_to(target)
    # The prune alone passes over a LOG_DIR that is a link, and says so.
    pruned = prune.prune(link, jobs_dir, "0")
    assert (pruned.logs, pruned.said) == ([], [f"warning: LOG_DIR {link} is a link: no log is pruned through it"])
    assert old_log.read_text() == "a run of last year\n"
    # A run holds LOG_DIR to be a folder before it prunes: the link is refused first, so the prune never meets it.
    made = jobs.create(jobs_dir, "deploy", "aw deploy alpha", "pve", [], {"kind": "terminal"}, True)
    try:
        draft = types.SimpleNamespace(logs=textlog.Logs(link, "0"), attached=True, jobs_dir=jobs_dir, verb="deploy")
        runs._logged(made, draft)
        said = capsys.readouterr().err.splitlines()
        assert len(said) == 1 and said[0].startswith("warning: no text log for this run: error: "), said
        assert "is a link" in said[0] and made.read().get("log") is None
    finally:
        textlog.finish(made)
        jobs.drop(made)
    assert sorted(path.name for path in target.iterdir()) == [old_log.name]
    assert old_log.read_text() == "a run of last year\n"


def test_a_prune_reads_a_job_s_age_before_its_lock_and_never_tries_a_young_one_s(jobs_dir, monkeypatch) -> None:
    # A folder another run's create is making: its lock file there, no record yet, made this instant.
    young = jobs_dir / "3f9a0c1e7b2d4a58"
    young.mkdir(parents=True)
    (young / "lock").write_text("")
    old = jobs_dir / "3f9a0c1e7b2d4a59"
    old.mkdir()
    _back(old)
    tried: list[str] = []
    held = jobs.lock_held

    def lock_held(job: jobs.Job) -> bool:
        tried.append(job.id)
        return held(job)

    monkeypatch.setattr(jobs, "lock_held", lock_held)
    pruned = prune.prune(None, jobs_dir, "0")
    assert (tried, pruned.jobs) == ([old.name], [old.name])
    assert young.is_dir()


def test_inside_holds_an_entry_by_its_real_path_and_refuses_a_link_that_leads_out(tmp_path) -> None:
    folder = tmp_path / "logs"
    folder.mkdir()
    outside = tmp_path / "20261007-203015-deploy.log"
    outside.write_text("kept\n")
    (folder / "20261007-203015-deploy.log").write_text("a run\n")
    (folder / "20261007-203016-deploy.log").symlink_to(outside)
    assert prune._inside(folder / "20261007-203015-deploy.log", folder)
    assert not prune._inside(folder / "20261007-203016-deploy.log", folder)


def test_a_prune_never_reaches_the_key_file_client_yml_or_the_fleet_whatever_a_record_names(
    jobs_dir, log_dir, tmp_path
) -> None:
    where = paths.home(REPO)
    root = where.root
    (root / paths.FLEET / "hosts" / "lab").mkdir(parents=True)
    files = {
        where.key_file: b"the vault's key\n",
        root / paths.CLIENT: b"fleet_home: ~\n",
        root / paths.FLEET / "fleet.json": b'{"format": 1}\n',
        root / paths.FLEET / "hosts" / "lab" / "host.yml": b"address: 127.0.0.1\n",
    }
    for path, data in files.items():
        path.write_bytes(data)
    # Records that name what is no run's directory of theirs: the fleet's folder, the key file, a link to the fleet
    # in a run directory's name, and another job's run directory's name.
    named = [str(root / paths.FLEET), str(where.key_file), str(tmp_path / "aw-run-{id}-x"), str(root)]
    for each in named:
        job = jobs.Job(jobs_dir / _ran(jobs_dir, "-"))
        if "{id}" in each:
            Path(each.format(id=job.id)).symlink_to(root / paths.FLEET, target_is_directory=True)
        _rewrite(job, directory=each.format(id=job.id), ended=time.time() - YEAR)
    for path in [*root.rglob("*"), root]:
        _back(path)
    before = {name: data for name, data in _everything(root).items() if not name.startswith(paths.STATE)}
    # LOG_DIR the state folder's logs/, and LOG_DIR the home itself, where the key file and client.yml lie.
    for folder in (log_dir, root):
        prune.prune(folder, jobs_dir, "0")
    after = {name: data for name, data in _everything(root).items() if not name.startswith(paths.STATE)}
    assert after == before
    assert jobs.listed(jobs_dir) == []


# ------------------------------------------------------------- the listing ----


def test_logs_lists_the_jobs_newest_first_and_says_a_lost_one_without_changing_its_record(
    home, jobs_dir, log_dir, running
) -> None:
    process, live_id = running
    ended_id = _ran(jobs_dir, log_dir)
    lost_id = _ran(jobs_dir, log_dir, "lost")
    lost = jobs.Job(jobs_dir / lost_id)
    kept = lost.record.read_bytes()
    ran = run_aw("logs", home=home)
    assert ran.returncode == EXITS["done"] and ran.stderr == "", ran.stderr
    head, *rows = ran.stdout.splitlines()
    assert head.split() == ["JOB", "STATE", "STARTED", "LENGTH", "COMMAND", "LOG"]
    assert [row.split()[:2] for row in rows] == [[lost_id, "lost"], [ended_id, "succeeded"], [live_id, "running"]]
    for row, job_id in zip(rows, (lost_id, ended_id, live_id), strict=True):
        assert "aw deploy alpha" in row and row.endswith(jobs.Job(jobs_dir / job_id).read()["log"]), row
    assert lost.record.read_bytes() == kept and jobs.state(lost) == "lost"
    _tell(process, "end")
    assert process.wait(timeout=60) == 0


def test_logs_with_no_job_says_so(home, jobs_dir) -> None:
    ran = run_aw("logs", home=home)
    assert (ran.returncode, ran.stdout, ran.stderr) == (EXITS["done"], f"No jobs in {jobs_dir}.\n", "")


def test_logs_of_a_job_prints_its_log_and_save_says_where_it_is(home, jobs_dir, log_dir) -> None:
    job = jobs.Job(jobs_dir / _ran(jobs_dir, log_dir))
    log = Path(job.read()["log"])
    ran = run_aw("logs", job.id, home=home)
    assert (ran.returncode, ran.stdout, ran.stderr) == (EXITS["done"], log.read_text(), "")
    assert ran.stdout.splitlines()[-1].endswith(" end: deployed · outcome=succeeded · exit=0")
    saved = run_aw("logs", job.id, "--save", home=home)
    assert saved.returncode == EXITS["done"] and str(log) in saved.stdout, saved


def test_logs_of_a_job_that_keeps_no_log_says_so(home, jobs_dir) -> None:
    job_id = _ran(jobs_dir, "")
    for words in (("logs", job_id), ("logs", job_id, "--save")):
        ran = run_aw(*words, home=home)
        assert ran.returncode == EXITS["refused"] and ran.stdout == "", ran
        assert ran.stderr == f"error: job {job_id} keeps no text log: its run had LOG_DIR empty\n"


def test_follow_prints_each_line_as_it_is_written_and_ends_with_the_job(home, jobs_dir, running) -> None:
    process, job_id = running
    # Each line the run is told to write must reach the follow's terminal while the job runs on, before the run is told
    # anything more: the follow is told nothing, and the run's next line waits for the last to be followed.
    steps = [
        (" stage alpha: provision", "say first"),
        (" report alpha: first", "say second"),
        (" report alpha: second", "end"),
    ]
    told: list[tuple[str, bool]] = []

    def act(_chunk: bytes, output: bytes, _master: int, follow: subprocess.Popen) -> None:
        if len(told) < len(steps) and steps[len(told)][0] in output.decode(errors="replace"):
            told.append((steps[len(told)][1], follow.poll() is None))
            _tell(process, told[-1][0])

    rc, output = aw_at_a_terminal("logs", job_id, "--follow", home=home, act=act, timeout=120)
    said = _said(output)
    assert told == [("say first", True), ("say second", True), ("end", True)], said
    assert said[0].endswith(f" aw: job {job_id}: aw deploy alpha"), said
    assert said[-2:] == [f"Job {job_id} succeeded, exit 0: deployed", "  alpha: succeeded at provision"], said
    assert rc == EXITS["done"], said
    assert process.wait(timeout=60) == 0


def test_ctrl_c_stops_the_follow_with_130_and_the_job_runs_on(home, jobs_dir, running) -> None:
    process, job_id = running
    typed: list[bytes] = []

    def act(_chunk: bytes, output: bytes, master: int, _follow: subprocess.Popen) -> None:
        # The follow waits for the log to grow, holding nothing and forking nothing: then a Ctrl-C at its terminal.
        if not typed and b" stage alpha: provision" in output:
            typed.append(b"\x03")
            os.write(master, typed[-1])

    rc, output = aw_at_a_terminal("logs", job_id, "--follow", home=home, act=act, timeout=120)
    said = output.decode(errors="replace")
    assert (rc, typed) == (EXITS["cancelled"], [b"\x03"]), said
    assert f"Stopped following job {job_id}: it goes on. Follow it again: aw logs {job_id} --follow" in said, said
    assert jobs.state(jobs.Job(jobs_dir / job_id)) == "running"
    _tell(process, "end")
    assert process.wait(timeout=60) == 0


@pytest.mark.parametrize("word", ["3f9a0c1e", "3F9A0C1E7B2D4A58", "3f9a0c1e7b2d4a581", "3f9a0c1e7b2d4a5g", "../x"])
def test_logs_refuses_an_id_that_is_not_sixteen_hex_digits(home, word) -> None:
    for words in (("logs", word), ("logs", word, "--follow")):
        ran = run_aw(*words, home=home)
        assert ran.returncode == EXITS["refused"] and ran.stdout == "", ran
        assert ran.stderr.startswith("error: ") and len(ran.stderr.splitlines()) == 1, ran.stderr
        # Refused by the ID's pattern, as the line is read: never looked for among the jobs.
        assert PATTERNS["job_id"].words in ran.stderr and "there is no job" not in ran.stderr, ran.stderr


def test_logs_refuses_a_job_there_is_not_and_follow_or_save_without_one(home, jobs_dir) -> None:
    ran = run_aw("logs", "3f9a0c1e7b2d4a58", home=home)
    assert ran.returncode == EXITS["refused"], ran
    assert ran.stderr == f"error: there is no job 3f9a0c1e7b2d4a58 in {jobs_dir}: aw logs lists the jobs\n"
    for flag in ("--follow", "--save"):
        ran = run_aw("logs", flag, home=home)
        assert ran.returncode == EXITS["refused"], ran
        assert ran.stderr == f"error: {flag} is for one job: give its ID, as aw logs lists the jobs\n"


# ---------------------------------------------------------------- what the log takes from the run ----


def test_every_listener_hears_an_event_whatever_one_before_it_raised(jobs_dir, log_dir) -> None:
    made = jobs.create(jobs_dir, "deploy", "aw deploy alpha", "pve", [], {"kind": "terminal"}, True)
    heard = []
    try:

        def view(event: jobs.Event) -> None:
            raise OSError("the terminal has gone")

        made.listen(view)
        log = textlog.start(made, textlog.Logs(log_dir), "deploy", True)
        assert log is not None
        made.listen(heard.append)
        with pytest.raises(OSError, match="the terminal has gone"):
            made.emit("end", None, "deployed", outcome="succeeded", exit=0)
        assert [event.kind for event in heard] == ["end"]
        assert log.path.read_text().splitlines()[-1].endswith(" end: deployed · outcome=succeeded · exit=0")
        assert made.read()["outcome"] == "succeeded"
    finally:
        textlog.finish(made)
        jobs.drop(made)


def test_the_record_keeps_the_names_given_with_its_machines(jobs_dir) -> None:
    made = jobs.create(jobs_dir, "deploy", "aw", "pve", [IDENTITY], {"kind": "terminal"}, True, names={IDENTITY: "a"})
    try:
        assert made.read()["names"] == {IDENTITY: "a"}
    finally:
        jobs.drop(made)
    with pytest.raises(ValueError, match="names are its machines' own"):
        jobs.create(jobs_dir, "deploy", "aw", "pve", [IDENTITY], {"kind": "terminal"}, True, names={OTHER: "b"})
    for names in ({IDENTITY: 1}, {IDENTITY: ""}):
        with pytest.raises(TypeError, match="a job's names are"):
            jobs.create(jobs_dir, "deploy", "aw", "pve", [IDENTITY], {"kind": "terminal"}, True, names=names)
    assert jobs.listed(jobs_dir) == []


def test_the_loop_s_own_report_goes_to_the_run_s_text_log(jobs_dir, log_dir) -> None:
    made = jobs.create(jobs_dir, "deploy", "aw deploy alpha", "pve", [], {"kind": "terminal"}, True)
    try:
        log = textlog.start(made, textlog.Logs(log_dir), "deploy", True)
        assert log is not None
        attached._unsaid(made)(None, {"message": "Task was destroyed but it is pending!", "exception": KeyError("x")})
        said = " aw: Task was destroyed but it is pending!: KeyError('x')"
        assert log.path.read_text().splitlines()[-1].endswith(said)
    finally:
        textlog.finish(made)
        jobs.drop(made)
