"""A run of the new engine drawn as today's runs are (lib/claude_vm/runs/view.py; docs/architecture/engine.md §16.8).

Canned runs, as lists of the events a run emits - a deploy of one machine, of three, one failing in configure (alone
and in a fleet), one waiting for a sign-in, one a person stopped, and a run of one stage - each timed as the matching
run of tests/unit/test_run_view.py, today's, is timed: their frames at chosen moments, the lines for output that is no
terminal, what is said above the region and the summary, each compared row for row with what today's run draws where
the two runs have the same stages. What today's has and the new model has not - counts of tasks, bars, a percentage,
a window of tasks, a VM's ID on provision's row - is not compared; the lines where the two differ say why. No line of
a frame or a summary is wider than the screen at any width, and none wraps at 80 or 100 columns (tests/unit/vt.py); a
report is wrapped and never cut. The rules with a test of their own: a stage of every machine's work is said off a
terminal once every machine is through it; nothing in the view prints, reads a clock, a file or the terminal; and
tui/ imports nothing of runs/.

Review 32's rules (review/code/C9.md), each with a test: what the events said made one visible line, or a report's
and a failure's lines visible, before it is drawn; each wait said once off a terminal, with where, whose and what; the
name a person chose wherever a machine is said, never its identity; each report said above once, none skipped; a wait
said under its own machines alone; a failed quiet stage drawn; a machine's line of work drawn only while it is at that
stage; a machine that has ended drawn to come in a stage begun after its end; and a failed run's own why said above and
in the summary's head.
"""

from __future__ import annotations

import ast
import re
import unicodedata

import pytest
import test_run_view as todays
from harness import REPO
from test_claude_vm import _python
from vt import VT

from claude_vm.run import model as todays_model
from claude_vm.runs import job as jobs
from claude_vm.runs import model
from claude_vm.runs.stages import DEPLOY, Declared, Stage, Step
from claude_vm.runs.view import View
from claude_vm.tui import run as drawn
from claude_vm.tui import screen

VIEW = REPO / "lib" / "claude_vm" / "runs" / "view.py"
TUI = REPO / "lib" / "claude_vm" / "tui"
STARTED = todays.STARTED
LOG = todays.LOG
SGR = re.compile(r"\x1b\[[0-9;]*m")
PAGE = "https://claude.ai/oauth/authorize"
SIGN_IN = "Approve the sign-in in your browser"
SECTION = "template-pve"
# Machines as the job's record and layer 1's steps name them: by identity, the chosen name beside.
ALPHA = "agent-warehouse-4f1c0000-0000-4000-8000-000000000001"
BETA = "agent-warehouse-4f1c0000-0000-4000-8000-000000000002"
# A stage's row, as both draw one: its mark and its label, a time at the right edge but while to come.
ROW = re.compile(r" (\S) (\S.*)")
TIME = re.compile(r"(\d+:\d\d|\d+(?:\.\d)? s)\Z")


def _event(kind: str, machine: str | None, text: str, **data: object) -> tuple[str, str | None, str, dict]:
    return (kind, machine, text, data)


def _work(now: float, machine: str, text: str):
    """A line of a machine's work, as layer 1 emits each of its tasks: a step that names no stage."""
    return (now, _event("step", machine, text, status="task", part="common"))


# ---------------------------------------------------------------- the canned runs ----

# A deploy of one machine, to configure: timed as test_run_view.py's Script().to_configure() is, task by task.
ONE_TO_CONFIGURE = [
    (0.0, _event("stage", None, "template")),
    (0.7, _event("stage", None, "template", outcome="done")),
    (0.7, _event("stage", "alpha", "provision")),
    (61.1, _event("report", "alpha", "alpha is up at 192.0.2.61")),
    (61.1, _event("stage", "alpha", "provision", outcome="done")),
    (61.1, _event("stage", "alpha", "configure")),
    _work(81.2, "alpha", "Refresh apt cache"),
    _work(85.2, "alpha", "Install baseline packages"),
    _work(115.2, "alpha", "Set the time zone"),
    _work(115.7, "alpha", "Harden sshd"),
    _work(116.3, "alpha", "Mask each unit"),
    _work(117.0, "alpha", "Write the motd"),
]
# ... and on to its end, as C8's deploy of one machine: the aliases, a sign-in, the editor's first steps, NEXT STEPS.
ONE = [
    *ONE_TO_CONFIGURE,
    (117.0, _event("report", "alpha", "alpha: Claude Code 2.1.308")),
    (117.0, _event("stage", "alpha", "configure", outcome="done")),
    (117.0, _event("stage", "alpha", "aliases")),
    (117.4, _event("stage", "alpha", "aliases", outcome="done")),
    (117.4, _event("step", "alpha", "claude", stage="sign-in")),
    (130.0, _event("step", "alpha", "claude", stage="sign-in", outcome="done")),
    (130.0, _event("report", "alpha", "alpha: signed in as you@example.com")),
    (130.0, _event("stage", "alpha", "sign-in", outcome="done")),
    (131.0, _event("step", "alpha", "find", stage="editor")),
    (132.0, _event("step", "alpha", "find", stage="editor", outcome="done")),
    (132.0, _event("step", "alpha", "reach", stage="editor")),
    _work(133.0, "alpha", "Open the connection"),
    (135.0, _event("step", "alpha", "reach", stage="editor", outcome="done")),
    (135.0, _event("report", None, "Opening the editor")),
    (140.0, _event("next", "alpha", "aw login alpha --only github", matter="github")),
    (140.0, _event("report", "alpha", "aw login alpha --only github")),
    (141.0, _event("stage", "alpha", "editor", outcome="done")),
    (141.0, _event("end", "alpha", "ready", outcome="succeeded")),
    (141.0, _event("end", None, "", outcome="succeeded", exit=0)),
]
# The editor under way: a step running, with a line of work under it.
ONE_IN_THE_EDITOR = ONE[: ONE.index(_work(133.0, "alpha", "Open the connection")) + 1]

# A deploy of three machines, as C8's: provision for every machine, configure each on its own, the sign-ins in turn.
THREE_IN_CONFIGURE = [
    (1.0, _event("stage", None, "provision")),
    (20.0, _event("stage", "beta", "provision", outcome="done")),
    (25.0, _event("stage", None, "provision", outcome="done")),
    (26.0, _event("stage", "alpha", "configure")),
    (26.0, _event("stage", "gamma", "configure")),
    _work(26.5, "alpha", "Install baseline packages"),
    _work(26.8, "gamma", "Refresh apt cache"),
]
THREE = [
    *THREE_IN_CONFIGURE,
    (27.0, _event("stage", "beta", "configure")),
    (28.0, _event("waiting", "beta", "waiting for another run to leave the section", section=SECTION)),
    (40.0, _event("waiting", "beta", "", section=SECTION)),
    (60.0, _event("stage", "alpha", "configure", outcome="done")),
    (61.0, _event("stage", "beta", "configure", outcome="skipped")),
    (62.0, _event("stage", "gamma", "configure", outcome="done")),
    (63.0, _event("step", "alpha", "claude", stage="sign-in")),
    (70.0, _event("step", "alpha", "claude", stage="sign-in", outcome="done")),
    (70.0, _event("step", "beta", "claude", stage="sign-in")),
    (77.0, _event("step", "beta", "claude", stage="sign-in", outcome="done")),
    (77.0, _event("step", "gamma", "claude", stage="sign-in")),
    (84.0, _event("step", "gamma", "claude", stage="sign-in", outcome="done")),
    (84.0, _event("stage", None, "sign-in", outcome="done")),
    *((85.0, _event("end", machine, "ready", outcome="succeeded")) for machine in ("alpha", "beta", "gamma")),
    (85.0, _event("end", None, "", outcome="succeeded", exit=0)),
]
THREE_WAITING_ON_A_SECTION = THREE[: len(THREE_IN_CONFIGURE) + 2]

# One machine failing in configure, timed as test_run_view.py's failed task: its last task fails at 117.0.
WHY = "Write the motd: permission denied"
FAILING_ALONE = [
    *ONE_TO_CONFIGURE,
    (117.0, _event("stage", "alpha", "configure", outcome="failed", why=WHY)),
    (117.0, _event("end", "alpha", WHY, outcome="failed")),
    (117.0, _event("end", None, "alpha failed", outcome="failed", exit=1)),
]
# A fleet with one machine failing in configure, as C8's: the other goes on, and the run fails at its end.
APT = "Install baseline packages: E: Unable to locate package nope"
FAILING_IN_A_FLEET = [
    (1.0, _event("stage", None, "provision")),
    (30.0, _event("stage", None, "provision", outcome="done")),
    (31.0, _event("stage", "alpha", "configure")),
    (31.0, _event("stage", "beta", "configure")),
    (50.0, _event("stage", "beta", "configure", outcome="failed", why=APT)),
    (50.0, _event("end", "beta", APT, outcome="failed")),
    (90.0, _event("stage", "alpha", "configure", outcome="done")),
    (90.0, _event("stage", "alpha", "aliases")),
    (91.0, _event("stage", "alpha", "aliases", outcome="done")),
    (92.0, _event("end", "alpha", "ready", outcome="succeeded")),
    (92.0, _event("end", None, "one machine failed", outcome="failed", exit=1)),
]

# One machine waiting for its sign-in, timed as test_run_view.py's wait for the browser: configure ends at 120.0, the
# sign-in begins, and its wait for you from 121.1 to 191.1.
TO_THE_SIGN_IN = [
    *ONE_TO_CONFIGURE,
    (120.0, _event("stage", "alpha", "configure", outcome="done")),
    (120.0, _event("step", "alpha", "claude", stage="sign-in")),
    (121.1, _event("waiting", "alpha", SIGN_IN, page=PAGE)),
]
WAITING = [
    *TO_THE_SIGN_IN,
    (191.1, _event("waiting", "alpha", "")),
    (191.2, _event("step", "alpha", "claude", stage="sign-in", outcome="done")),
    (191.2, _event("stage", "alpha", "sign-in", outcome="done")),
]

# One machine a person stopped in its sign-in: the Ctrl-C at 150.0 (the model's cancel), the run's end at 151.0.
CANCELLED_AT = 150.0
CANCELLED = [*TO_THE_SIGN_IN, (151.0, _event("end", None, "Ctrl-C", outcome="cancelled", exit=130))]

# A run of one stage, drawn as its steps: today's deploy --only configure, its roles declared as steps.
ONLY_CONFIGURE = Declared(
    "deploy",
    (Stage("configure", "configure", steps=(Step("common", "common"), Step("dev-tools", "dev_tools"))),),
)
CONFIGURE_ONLY = [
    (0.0, _event("step", "alpha", "common", stage="configure")),
    _work(0.0, "alpha", "Harden sshd"),
    _work(1.0, "alpha", "Write the motd"),
]


def _fed(events, machines=("alpha",), declared=DEPLOY, title=None, cancel=None, said=None) -> View:
    """A view of a run fed events in order, each at its time; with cancel, a person's stop at that time once the run
    has reached it; with said, the lines off a terminal after each event added to it."""
    run = model.Run(declared, 0.0, machines)
    view = View(run, started_at=STARTED, title=title, log=LOG)
    for now, (kind, machine, text, data) in events:
        if cancel is not None and now >= cancel and run.stopping is None:
            run.cancel(cancel)
        run.feed(jobs.Event(now, kind, machine, text, data), now)
        if said is not None:
            said += view.ended_lines(now)
    return view


def _plain(lines):
    return [SGR.sub("", line) for line in lines]


def _stage_rows(lines) -> dict[str, tuple[str, str | None, int | None]]:
    """Each stage's row of a frame or a summary, by its label: its mark, and but while it is to come, the time at its
    end and its width."""
    rows = {}
    for line in lines:
        found = ROW.match(line)
        if not found or line[1] == " ":
            continue
        mark, label = found.group(1), found.group(2).split("  ")[0]
        time = TIME.search(line)
        rows[label] = (mark, None, None) if mark == "○" else (mark, time.group() if time else None, len(line))
    return rows


def _marks(line: str) -> str:
    """The machines' marks a row says: alpha ✓ · beta ⠋."""
    found = re.search(r"\b(?:alpha|beta|gamma) \S(?: · (?:alpha|beta|gamma) \S)*", line)
    return found.group() if found else ""


# ---------------------------------------------------------------- the frame ----


def test_a_deploy_of_one_machine_is_framed_as_todays_row_for_row_where_their_stages_are_the_same() -> None:
    today = todays.Script().to_configure()
    now = today.now + 2.5
    theirs = today.run.frame(now, 100, 40, "⠋", color=False)
    view = _fed(ONE_TO_CONFIGURE)
    ours = view.frame(now, 100, 40, "⠋", color=False)
    # The header and the line under it, as today's.
    assert ours[:2] == theirs[:2] == [f"agent-warehouse  deploy alpha · started {theirs[0][-5:]}", ""]
    # Each stage both draw: its mark, its label, and its time at the same right edge; a stage to come by its label.
    ours_rows, their_rows = _stage_rows(ours), _stage_rows(theirs)
    assert ours_rows.keys() & their_rows.keys() == {"template", "provision", "configure", "sign in", "editor"}
    for label in ours_rows.keys() & their_rows.keys():
        assert ours_rows[label] == their_rows[label], label
    assert ours_rows["template"] == ("✓", "0.7 s", 99) and ours_rows["configure"] == ("⠋", "0:58", 99)
    # Deploy declares two stages more that today's playbook reached no play of: the aliases and the golden template.
    assert ours_rows.keys() - their_rows.keys() == {"SSH aliases", "golden template"}
    # Today's window of tasks: here, the machine's latest line of work, drawn as today's running task with its timer.
    work = [line for line in ours if line.startswith(" " * 9)]
    assert work == [line for line in theirs if line.startswith(" " * 9)][-1:]
    assert work[0].strip().startswith("⠋ Write the motd") and work[0].endswith("0:02")
    # The footer: the same clock, place and log, of the seven stages drawn where today's run drew five.
    assert ours[-2] == "" and ours[-1] == theirs[-1].replace("stage 3 of 5", "stage 3 of 7")
    assert ours[-1] == f"⠋ 1:59 · stage 3 of 7 · {LOG}"
    # The title: where the run is, with no percentage, since no task is counted.
    assert view.title_text(now) == today.run.title_text(today.now).rsplit(" ", 1)[0] == "deploy alpha · configure"


def test_colour_is_on_the_screen_only_when_asked_for() -> None:
    view = _fed(ONE_IN_THE_EDITOR)
    coloured = view.frame(134.0, 100, 40, "⠋", color=True)
    plain = view.frame(134.0, 100, 40, "⠋", color=False)
    assert any("\x1b[" in line for line in coloured) and not any("\x1b" in line for line in plain)
    assert _plain(coloured) == plain
    summary = _fed(ONE).summary(141.0, 100, color=True)
    assert any("\x1b[" in line for line in summary) and _plain(summary) == _fed(ONE).summary(141.0, 100, color=False)


def test_a_running_stages_steps_are_drawn_under_it_with_the_work_of_the_step_under_way() -> None:
    lines = _fed(ONE_IN_THE_EDITOR).frame(134.0, 100, 40, "⠋", color=False)
    text = "\n".join(lines)
    assert re.search(r"^ ⠋ editor +0:03$", text, re.M), text
    assert re.search(r"^     ✓ find the VM +1\.0 s$", text, re.M), text
    assert re.search(r"^     ⠋ reach it +0:02$", text, re.M), text
    assert re.search(r"^         ⠋ Open the connection +0:01$", text, re.M), text
    assert re.search(r"^     ○ read the sessions$", text, re.M), text
    # Ended stages are whole, one to come by its label, and the sign-in's steps are not drawn while it is not running.
    assert re.search(r"^ ✓ sign in +13 s$", text, re.M) and " ○ golden template" in lines
    assert not any("Claude" in line for line in lines)
    assert lines[-1] == f"⠋ 2:14 · stage 6 of 7 · {LOG}"


def test_a_fleet_says_each_machines_mark_as_today_does_and_their_count_where_they_do_not_fit() -> None:
    # Today's: a step's row says each VM's state, a VM still to come in a play that is not serial among them.
    today = todays.Script()
    today.run.names = ["alpha", "beta", "gamma"]
    today.task("configure", "c1", "Refresh apt cache", "common", hosts=("alpha", "gamma"), outcome=None, took=0)
    theirs = next(line for line in today.run.frame(today.now, 100, 40, "⠋", color=False) if " common " in line)
    view = _fed(THREE_IN_CONFIGURE, ("alpha", "beta", "gamma"))
    lines = view.frame(27.0, 100, 40, "⠋", color=False)
    ours = next(line for line in lines if line.startswith(" ⠋ configure"))
    assert _marks(ours) == _marks(theirs) == "alpha ⠋ · beta ○ · gamma ⠋"
    assert ours.endswith("  0:01") and len(ours) == 99
    # The provision row: every machine through it; the host's template marks none.
    provision = next(line for line in lines if line.startswith(" ✓ provision"))
    assert _marks(provision) == "alpha ✓ · beta ✓ · gamma ✓" and provision.endswith("24 s")
    # Each machine's line of work, with its machine, as today's tasks say their VM in a fleet.
    work = [line for line in lines if line.startswith(" " * 9)]
    assert [line.strip().split("  ")[0] for line in work] == ["⠋ Install baseline packages", "⠋ Refresh apt cache"]
    assert work[0].endswith("alpha  0:00") and work[1].endswith("gamma  0:00")
    # Narrow, their count: as today's "1/9 VMs 1 failed".
    narrow = next(line for line in view.frame(27.0, 40, 40, "⠋", color=False) if line.startswith(" ⠋ configure"))
    assert "0/3 machines" in narrow and "alpha" not in narrow and len(narrow) <= 39


def test_a_wait_for_another_runs_section_is_a_line_of_work_and_no_wait_for_you() -> None:
    view = _fed(THREE_WAITING_ON_A_SECTION, ("alpha", "beta", "gamma"))
    lines = view.frame(30.0, 100, 40, "⠋", color=False)
    wait = next(line for line in lines if "another run" in line)
    assert wait.startswith("         ⏸ waiting for another run to leave the section") and wait.endswith("beta  0:02")
    assert lines[-1] == f"⠋ 0:30 · stage 3 of 7 · {LOG}"
    assert view.title_text(30.0) == "deploy alpha beta gamma · configure"


def test_a_wait_for_the_browser_is_said_on_its_stage_in_the_footer_and_the_title_as_todays() -> None:
    today = todays.Script().to_configure()
    today.run.result("c6", "alpha", "ok", today.now)
    for key in ("v1", "v2", "v3"):
        today.task("configure", key, f"Install {key}", "dev_tools")
    today.task("discover-login", "d2", "cluster_vms | List every VM in the cluster", outcome="skipped")
    today.task("login", "l1", "browser_login | Say the page", "claude_code", took=0.1)
    today.task("login", "l2", "browser_login | Wait for your browser", "claude_code", outcome=None, took=0)
    today.run.retry("l2", "alpha", today.now, 600)
    now = today.now + 65
    theirs = today.run.frame(now, 120, 40, "⠋", color=False)
    view = _fed(TO_THE_SIGN_IN)
    ours = view.frame(now, 120, 40, "⠋", color=False)
    # The stage's row: the step, what the run waits for, whose and since when - today's says the browser in its own
    # words, and how long it may still take, which the wait here does not carry.
    stage = next(line for line in ours if line.startswith(" ⏸ "))
    assert stage == f" ⏸ {'sign in':<13}Claude: {SIGN_IN} · alpha · 1:05"
    assert " ⏸ sign in      Claude: waiting for you in the browser · alpha · 1:05 of up to 10:00" in theirs
    # The step's row and the footer, as today's.
    for lines in (ours, theirs):
        assert re.search(r"^     ⏸ Claude ", "\n".join(lines), re.M)
    assert ours[-1] == theirs[-1].replace("stage 4 of 5", "stage 5 of 7")
    assert ours[-1] == f"⏸ 3:06 · stage 5 of 7 · waiting for you · {LOG}"
    assert view.title_text(now) == today.run.title_text(now) == "deploy alpha · waiting for you"


def test_a_failed_stage_is_marked_failed_as_todays() -> None:
    today = todays.Script().to_configure()
    today.run.result("c6", "alpha", "failed", today.now)
    theirs = today.run.frame(today.now, 100, 40, "⠋", color=False)
    ours = _fed(FAILING_ALONE[:-2]).frame(today.now, 100, 40, "⠋", color=False)
    assert _stage_rows(ours)["configure"][0] == _stage_rows(theirs)["configure"][0] == "✗"
    # In a fleet, the stage runs on while a machine is at it: the failed machine's mark says so.
    fleet = _fed(FAILING_IN_A_FLEET[:5], ("alpha", "beta")).frame(60.0, 100, 40, "⠋", color=False)
    assert _marks(next(line for line in fleet if " configure " in line)) == "alpha ⠋ · beta ✗"


def test_a_person_stopping_the_run_is_said_in_the_footer_and_ends_the_wait() -> None:
    view = _fed(TO_THE_SIGN_IN, cancel=CANCELLED_AT)
    view.run.cancel(CANCELLED_AT)
    lines = view.frame(150.5, 100, 40, "⠋", color=False)
    assert lines[-1] == f"⠋ 2:30 · stage 5 of 7 · stopping · {LOG}"
    assert any(line.startswith(" ⠋ sign in ") for line in lines) and not any("⏸" in line for line in lines)
    assert view.title_text(150.5) == "deploy alpha · sign in"


def test_a_run_of_one_stage_is_drawn_as_its_steps_as_todays() -> None:
    plays = [("c", "/r/playbooks/configure.yml:20", [("c1", "common"), ("c2", "common"), ("v1", "dev_tools")])]
    today = todays.Script(plays=plays, title="deploy --only configure")
    today.task("c", "c1", "Harden sshd", "common", outcome="changed")
    today.task("c", "c2", "Write the motd", "common", outcome=None, took=0)
    theirs = today.run.frame(today.now, 100, 30, "⠋", color=False)
    view = _fed(CONFIGURE_ONLY, declared=ONLY_CONFIGURE, title="deploy --only configure")
    ours = view.frame(1.0, 100, 30, "⠋", color=False)
    assert ours[0] == theirs[0] and not any(line.startswith(" ⠋ configure") for line in ours)
    common = next(line for line in ours if "common" in line)
    assert common[:21] == next(line for line in theirs if "common" in line)[:21] == " ⠋ common            "
    assert " ○ dev_tools" in ours and any(line.startswith(" ○ dev_tools ") for line in theirs)
    assert ours[-1] == theirs[-1] == f"⠋ 0:01 · step 1 of 2 · {LOG}"
    assert view.title_text(1.0) == today.run.title_text(today.now).rsplit(" ", 1)[0]
    assert view.title_text(1.0) == "deploy --only configure · common"
    view.run.feed(jobs.Event(2.0, "step", "alpha", "common", {"stage": "configure", "outcome": "done"}), 2.0)
    view.run.feed(jobs.Event(2.0, "end", None, "", {"outcome": "succeeded", "exit": 0}), 2.0)
    summary = view.summary(2.0, 100, color=False)
    assert summary[2].startswith("STEPS ") and summary[3].startswith(" ✓ common ")
    assert view.ended_lines(2.0) == [f"✓ {'configure / common':<28} {'':<36} 2.0 s"]


# Moments of every canned run, each drawn at a time of its own.
MOMENTS = [
    (ONE_TO_CONFIGURE, ("alpha",), 119.5),
    (ONE_IN_THE_EDITOR, ("alpha",), 134.0),
    (THREE_IN_CONFIGURE, ("alpha", "beta", "gamma"), 27.0),
    (THREE_WAITING_ON_A_SECTION, ("alpha", "beta", "gamma"), 30.0),
    (TO_THE_SIGN_IN, ("alpha",), 186.1),
    (FAILING_IN_A_FLEET[:5], ("alpha", "beta"), 60.0),
]


@pytest.mark.parametrize("cols", [30, 40, 51, 52, 63, 64, 79, 80, 100, 200])
@pytest.mark.parametrize("rows", [6, 10, 14, 16, 24, 50])
def test_no_line_of_a_frame_is_wider_than_the_terminal_and_a_frame_folds_to_its_height(cols, rows) -> None:
    # Deploy draws seven stages, and the editor's ten steps under its row: folded, every moment fits 16 rows, where
    # today's five stages fit 14; below that the region cuts the frame to the screen (tui/live_region.py).
    for events, machines, now in MOMENTS:
        lines = _plain(_fed(events, machines).frame(now, cols, rows, "⠋", color=True))
        assert all(len(line) <= cols - 1 for line in lines), [line for line in lines if len(line) > cols - 1]
        if rows >= 16:
            assert len(lines) <= rows - 1, "\n".join(lines)


def test_a_folded_frame_says_what_is_to_come_on_one_line_as_todays() -> None:
    lines = _fed(ONE_IN_THE_EDITOR).frame(134.0, 100, 14, "⠋", color=False)
    then = next(line for line in lines if line.startswith("     ○ then "))
    assert then.startswith("     ○ then read the sessions, the folder, alias, the editor here,") and then.endswith("…")
    assert len(then) == 99 and " ○ then golden template" in lines
    folded = todays.Script().to_configure().run.frame(117.0, 100, 14, "⠋", color=False)
    assert "     ○ then dev_tools" in folded and " ○ then sign in, editor" in folded


@pytest.mark.parametrize("cols", [80, 100])
def test_no_line_drawn_wraps_at_80_or_100_columns(cols) -> None:
    for events, machines, now in MOMENTS:
        view = _fed(events, machines)
        terminal = VT(cols, 60)
        terminal.feed(b"\x1b[?2026h" + "\r\n".join(view.frame(now, cols, 60, "⠋")).encode() + b"\x1b[?2026l")
        assert terminal.frame_wraps == 0 and not terminal.unknown
    ended = ((ONE, ("alpha",)), (THREE, ("alpha", "beta", "gamma")), (FAILING_IN_A_FLEET, ("alpha", "beta")))
    for events, machines in ended:
        terminal = VT(cols, 60)
        summary = _fed(events, machines).summary(200.0, cols)
        terminal.feed(b"\x1b[?2026h" + "\r\n".join(summary).encode() + b"\x1b[?2026l")
        assert terminal.frame_wraps == 0 and not terminal.unknown


# ---------------------------------------------------------------- output that is no terminal ----


def test_off_a_terminal_a_line_says_each_step_and_stage_once_in_todays_columns() -> None:
    today = todays.Script()
    today.task("plan", "plan-say", "Report what this run will do", took=0.2)
    today.task("template", "t1", "Check for the template", "proxmox_template", took=0.4)
    today.task("register", "p0", "Register the VMs", hosts=("localhost",), took=0.3)
    theirs = today.run.ended_lines(today.now)
    said: list[str] = []
    view = _fed(ONE, said=said)
    # Today's line of a stage: its mark, its label in 28 columns, what it did in 36, and its time from column 68.
    assert said[0][:31] == theirs[1][:31] == f"✓ {'template':<28} "
    assert said[0].index("0.7 s") == theirs[1].index("0.6 s") == 68
    # Each step and stage once, in their order, as each ends: a stage with no tasks to count says nothing more than
    # its mark and time where it is done.
    assert said == [
        f"✓ {'template':<28} {'':<36} 0.7 s",
        f"✓ {'provision':<28} {'':<36} 1:00",
        f"✓ {'configure':<28} {'':<36} 56 s",
        f"✓ {'SSH aliases':<28} {'':<36} 0.4 s",
        f"✓ {'sign in / Claude':<28} {'':<36} 13 s",
        f"✓ {'sign in':<28} {'':<36} 13 s",
        f"✓ {'editor / find the VM':<28} {'':<36} 1.0 s",
        f"✓ {'editor / reach it':<28} {'':<36} 3.0 s",
        f"✓ {'editor':<28} {'':<36} 10 s",
    ]
    assert view.ended_lines(200.0) == []
    # Today's step of a stage, in the same columns.
    today.task("discover-login", "d2", "cluster_vms | List every VM in the cluster", outcome="skipped")
    today.task("login", "l1", "browser_login | Say the page", "claude_code")
    today.task("code", "o1", "Say what was opened", hosts=("localhost",))
    claude = next(line for line in today.run.ended_lines(today.now) if "Claude" in line)
    assert said[4][:31] == claude[:31] and said[4].index("13 s") == claude.index("2.0 s") == 68


def test_off_a_terminal_a_failure_and_a_fleets_marks_are_said_in_the_column_today_says_what_a_step_did() -> None:
    said: list[str] = []
    _fed(FAILING_IN_A_FLEET, ("alpha", "beta"), said=said)
    assert said == [
        f"✓ {'provision':<28} {'alpha ✓ · beta ✓':<36} 29 s",
        f"✗ {'configure':<28} {'alpha ✓ · beta ✗':<36} 59 s",
        f"✓ {'SSH aliases':<28} {'alpha ✓ · beta ○':<36} 1.0 s",
    ]
    alone: list[str] = []
    _fed(FAILING_ALONE, said=alone)
    assert alone[-1] == f"✗ {'configure':<28} {'failed':<36} 56 s"


def test_off_a_terminal_a_stage_of_each_machines_work_is_said_once_every_machine_is_through_it() -> None:
    # Machines that reach a stage one after another: the stage reads done between them, and is said at the last.
    said: list[str] = []
    events = [
        (1.0, _event("stage", "alpha", "configure")),
        (5.0, _event("stage", "alpha", "configure", outcome="done")),
        (6.0, _event("stage", "beta", "configure")),
        (9.0, _event("stage", "beta", "configure", outcome="done")),
    ]
    view = _fed(events[:2], ("alpha", "beta"), said=said)
    assert view.run.state("configure") == "done" and said == []
    for now, (kind, machine, text, data) in events[2:]:
        view.run.feed(jobs.Event(now, kind, machine, text, data), now)
        said += view.ended_lines(now)
    assert said == [f"✓ {'configure':<28} {'alpha ✓ · beta ✓':<36} 8.0 s"]
    # A machine that ended without reaching it does not hold it back; at the run's end, one never reached does not.
    other = _fed(events[:2] + [(6.0, _event("end", "beta", "gone", outcome="failed"))], ("alpha", "beta", "gamma"))
    assert other.ended_lines(6.0) == []
    other.run.feed(jobs.Event(7.0, "end", None, "", {"outcome": "failed", "exit": 1}), 7.0)
    assert other.ended_lines(7.0) == [f"✓ {'configure':<28} {'alpha ✓ · beta ○ · gamma ○':<36} 4.0 s"]


# ---------------------------------------------------------------- what is said above the region ----


def test_a_report_is_said_above_whole_wrapped_under_its_machine_and_never_cut_as_todays() -> None:
    url = "https://claude.ai/oauth/authorize?code=" + "x" * 120
    words = " ".join(f"word{n}" for n in range(40))
    view = _fed(
        [
            (1.0, _event("report", "alpha", f"alpha: {words} {url}\n  then the notes")),
            (2.0, _event("report", "beta", "beta is up at 192.0.2.62")),
            (3.0, _event("report", None, "Every alias is up to date.")),
        ],
        ("alpha", "beta"),
    )
    said = view.above(80, color=False)
    plain = [line for _, line in said]
    # As today's run says the same report.
    today = drawn.Run("deploy alpha beta", 0.0)
    first = today.report("alpha", [f"alpha: {words} {url}", "  then the notes"], ["", ""])
    expected = drawn.report_lines(first, 80, False)
    expected += drawn.report_lines(today.report("beta", ["beta is up at 192.0.2.62"], [""]), 80, False)
    expected += drawn.report_lines(today.report("localhost", ["Every alias is up to date."], [""]), 80, False)
    assert said == expected
    assert plain[0].startswith("  alpha  word0 word1") and "  beta is up at 192.0.2.62" in plain
    assert "  Every alias is up to date." in plain and "then the notes" in [line.strip() for line in plain]
    # Wrapped, nothing cut: every word in its order, the URL whole on a line of its own.
    rest = "then the notes beta is up at 192.0.2.62 Every alias is up to date."
    assert " ".join(plain).split() == ["alpha", *words.split(), url, *rest.split()]
    assert all(len(line) <= 79 for line in plain if url not in line) and url in [line.strip() for line in plain]
    # Each said once.
    assert view.above(80, color=False) == []


def test_a_failure_is_said_above_with_where_whose_and_why_in_todays_form() -> None:
    view = _fed(FAILING_IN_A_FLEET, ("alpha", "beta"))
    plain = [line for _, line in view.above(100, color=False)]
    assert plain == ["", f"✗ configure · beta: {APT} (19 s)", f"    the whole output: {LOG}", ""]
    # Today's failure, as the same function draws it.
    failure = drawn.Run("deploy", 0.0).failure("common", "alpha", "alpha", "Write the motd", 19.0, "failed", [], LOG)
    theirs = [line for _, line in drawn.failure_lines(failure, 100, False)]
    assert theirs[1] == "✗ common · alpha: Write the motd (19 s)"
    # A failure of several lines says the rest under it; one of the run's own, its machine when it has one.
    broke = _event("end", None, "it broke\nat the loop", outcome="failed", exit=1)
    alone = _fed([(1.0, _event("stage", None, "configure")), (4.0, broke)])
    assert [line for _, line in alone.above(100, color=False)] == [
        "",
        "✗ configure · alpha: it broke (4.0 s)",
        "    at the loop",
        f"    the whole output: {LOG}",
        "",
    ]


# ---------------------------------------------------------------- the summary ----


def test_the_summary_has_todays_head_stages_each_machines_words_and_totals() -> None:
    today = todays.Script().to_configure()
    today.run.result("c6", "alpha", "ok", today.now)
    today.run.note("alpha", "alpha: Claude Code 2.1.308")
    today.run.note("localhost", "alpha: signed in as you@example.com")
    today.run.note("localhost", "Opening the editor")
    today.run.end(today.now)
    theirs = today.run.summary(today.now, 100, color=False)
    ours = _fed(ONE).summary(141.0, 100, color=False)
    assert ours[0] == f"agent-warehouse  deploy alpha · done in 2 min 21 s · {ours[0][-5:]}"
    assert re.fullmatch(r"agent-warehouse  deploy alpha · done in 1 min 57 s · \d\d:\d\d", theirs[0])
    # The table: its heading and each stage both have, to its time, as today's - no TASKS or CHANGED, never counted.
    assert ours[1] == theirs[1] == "" and ours[2] == theirs[2][: len(ours[2])] == f"{'STAGES':<14}{'TIME':>6}"
    assert [line[:20] for line in ours[3:6]] == [line[:20] for line in theirs[3:6]]
    assert ours[3:9] == [
        f" ✓ {'template':<11}{'0.7 s':>6}",
        f" ✓ {'provision':<11}{'1:00':>6}",
        f" ✓ {'configure':<11}{'56 s':>6}",
        f" ✓ {'SSH aliases':<11}{'0.4 s':>6}",
        f" ✓ {'sign in':<11}{'13 s':>6}    Claude 13 s",
        f" ✓ {'editor':<11}{'10 s':>6}    find the VM 1.0 s · reach it 3.0 s",
    ]
    # Each machine's words, as today's - but its address and VM ID, which no event carries here - and NEXT STEPS'
    # line left to NEXT STEPS.
    text, their_text = "\n".join(ours), "\n".join(theirs)
    alpha = " alpha is up at 192.0.2.61\n Claude Code 2.1.308\n signed in as you@example.com\n"
    assert f"\nALPHA\n{alpha}\nTHIS MACHINE\n Opening the editor\n" in text
    assert f"\nALPHA  192.0.2.61 · VM 104 · new\n{alpha}\nTHIS MACHINE\n Opening the editor\n" in their_text
    assert "aw login" not in text
    # The totals: the failures and the log as today's, a count of machines where today's counted tasks.
    assert ours[-1] == f"1 machine · 0 failed · log {LOG}" and theirs[-1].endswith(f" · 0 failed · log {LOG}")


def test_the_summary_says_your_time_in_a_sign_in_as_todays() -> None:
    today = todays.Script().to_configure()
    today.run.result("c6", "alpha", "ok", today.now)
    for key in ("v1", "v2", "v3"):
        today.task("configure", key, f"Install {key}", "dev_tools")
    today.task("discover-login", "d2", "cluster_vms | List every VM in the cluster", outcome="skipped")
    today.task("login", "l1", "browser_login | Say the page", "claude_code", took=0.1)
    today.task("login", "l2", "browser_login | Wait for your browser", "claude_code", outcome=None, took=0)
    today.run.retry("l2", "alpha", today.now, 600)
    today.now += 70
    today.run.result("l2", "alpha", "changed", today.now)
    today.task("login", "l3", "Report", took=0.1)
    theirs = today.run.summary(today.now + 1, 100, color=False)
    ours = _fed(WAITING).summary(today.now + 1, 100, color=False)
    their_row = next(line for line in theirs if line.startswith(" ✓ sign in"))
    our_row = next(line for line in ours if line.startswith(" ✓ sign in"))
    assert our_row == f" ✓ {'sign in':<11}{'1:11':>6}    Claude 1:11 (1:10 yours)"
    assert our_row[:20] == their_row[:20] and their_row.endswith("Claude 1:11 (1:10 yours)")
    assert "1:10 of it yours" in ours[-1] and "1:10 of it yours" in "\n".join(theirs)


def test_the_head_says_failed_and_where_or_done_with_the_machines_that_failed_as_todays() -> None:
    today = todays.Script().to_configure()
    today.run.result("c6", "alpha", "failed", today.now)
    today.run.end(today.now)
    theirs = today.run.summary(today.now, 100, color=False, failed_hosts=["alpha"])
    ours = _fed(FAILING_ALONE).summary(today.now, 100, color=False)
    # Where: today's the role's step it failed in, here the stage, which declares no steps.
    assert theirs[0].startswith("agent-warehouse  deploy alpha · failed after 1 min 57 s at common · ")
    assert ours[0].startswith("agent-warehouse  deploy alpha · failed after 1 min 57 s at configure · ")
    rows = [next(line for line in lines if line.startswith(" ✗ configure ")) for lines in (ours, theirs)]
    assert rows[0] == f" ✗ {'configure':<11}{'56 s':>6}" == rows[1][:20]
    assert ours[-1] == f"1 machine · 1 failed · log {LOG}" and theirs[-1].endswith(f" · 1 failed · log {LOG}")
    # What it never reached, as today's.
    assert " not run  SSH aliases · sign in · editor · golden template" in ours
    assert " not run  sign in · editor" in theirs
    # A fleet where one failed and the others went on: done, with the one that failed, as today's says its VM.
    fleet = _fed(FAILING_IN_A_FLEET, ("alpha", "beta")).summary(92.0, 100, color=False)
    assert fleet[0] == "agent-warehouse  deploy alpha beta · done in 1 min 32 s · 1 machine failed: beta"
    assert fleet[2] == f"{'STAGES':<14}{'TIME':>6}    MACHINES"
    assert fleet[3:6] == [
        f" ✓ {'provision':<11}{'29 s':>6}    alpha ✓ · beta ✓",
        f" ✗ {'configure':<11}{'59 s':>6}    alpha ✓ · beta ✗",
        f" ✓ {'SSH aliases':<11}{'1.0 s':>6}    alpha ✓ · beta ○",
    ]


def test_a_stopped_run_says_where_it_stopped_as_todays() -> None:
    today = todays.Script().to_configure()
    today.run.result("c6", "alpha", "ok", today.now)
    for key in ("v1", "v2", "v3"):
        today.task("configure", key, f"Install {key}", "dev_tools")
    today.task("discover-login", "d2", "cluster_vms | List every VM in the cluster", outcome="skipped")
    today.task("login", "l1", "browser_login | Say the page", "claude_code", took=0.1)
    today.task("login", "l2", "browser_login | Wait for your browser", "claude_code", outcome=None, took=0)
    today.run.end(151.0, stopped=True)
    theirs = today.run.summary(151.0, 100, color=False)
    view = _fed(CANCELLED, cancel=CANCELLED_AT)
    ours = view.summary(151.0, 100, color=False)
    assert ours[0] == theirs[0]
    assert ours[0].startswith("agent-warehouse  deploy alpha · stopped after 2 min 31 s at Claude · ")
    assert view.title_text(151.0) == today.run.title_text(151.0) == "deploy alpha · stopped"
    row = next(line for line in ours if line.startswith(" ■ sign in"))
    assert row == f" ■ {'sign in':<11}{'31 s':>6}    Claude 31 s (29 s yours)"
    assert " not run  SSH aliases · editor · golden template" in ours


DECLINED = "Nothing was deleted: the deletion was not confirmed."
# destroy's no at a terminal, as engine/destroy.py's delete() says it: the check done, its answer, the run declined.
DESTROY = Declared("destroy", (Stage("check", "check", each_machine=False), Stage("delete", "delete")))
SAID_NO = [
    (0.0, _event("stage", None, "check")),
    (1.0, _event("stage", None, "check", outcome="done")),
    (1.0, _event("report", None, DECLINED)),
]


@pytest.mark.parametrize("outcome", ["declined", "failed"])
def test_a_run_the_person_declined_is_said_as_the_answer_never_as_a_failure_as_todays(outcome) -> None:
    # C38 (review 73's Later 1): today's head, `declined`, alone, with no ✗ and no failed count; exit 1 alone, a body
    # that fails with it, is still failed.
    today = todays._destroy()
    today.task("check", "k1", "Abort unless confirmed", hosts=("localhost",), outcome="declined")
    today.run.decline(DECLINED)
    today.run.end(today.now, failed=True)
    theirs = today.run.summary(today.now, 100, color=False, failed_hosts=["localhost"])
    ended = (1.0, _event("end", None, DECLINED, outcome=outcome, exit=1))
    view = _fed([*SAID_NO, ended], ("alpha", "beta"), declared=DESTROY, title="destroy alpha beta")
    above = [line for _, line in view.above(100, color=False)]
    ours = view.summary(1.0, 100, color=False)
    if outcome == "failed":
        assert ours[0].startswith("agent-warehouse  destroy alpha beta · failed after 1 s"), ours
        assert any("✗" in line for line in above) and "1 failed" in "\n".join(ours), (above, ours)
        assert view.title_text(1.0) == "destroy alpha beta · failed"
        return
    assert ours == theirs and ours[0].startswith("agent-warehouse  destroy alpha beta · declined · 1 s · "), ours
    assert view.title_text(1.0) == today.run.title_text(today.now) == "destroy alpha beta · declined"
    assert ["  " + DECLINED] == above and not any("✗" in line or "failed" in line for line in above + ours), (
        above,
        ours,
    )


def test_the_summary_of_a_fleet_marks_each_machine_and_leaves_out_the_host_s_work() -> None:
    ours = _fed(THREE, ("alpha", "beta", "gamma")).summary(85.0, 100, color=False)
    assert ours[0].startswith("agent-warehouse  deploy alpha beta gamma · done in 1 min 25 s · ")
    assert ours[3:6] == [
        f" ✓ {'provision':<11}{'24 s':>6}    alpha ✓ · beta ✓ · gamma ✓",
        f" ✓ {'configure':<11}{'36 s':>6}    alpha ✓ · beta – · gamma ✓",
        f" ✓ {'sign in':<11}{'21 s':>6}    alpha ✓ · beta ✓ · gamma ✓ · Claude 21 s",
    ]
    # As today's says each VM's outcome on a row: name and mark, joined.
    login = todays._login()
    login.task("sign", "l1", "Say the page", "claude_code", hosts=("alpha", "beta", "gamma"))
    login.run.end(login.now)
    claude = next(line for line in login.run.summary(login.now, 100, color=False) if " Claude " in line)
    assert _marks(claude) == "alpha ✓ · beta ✓ · gamma ✓" == _marks(ours[5])
    assert ours[-1] == f"3 machines · 0 failed · log {LOG}"


@pytest.mark.parametrize("cols", [30, 40, 52, 64, 80, 100, 200])
def test_no_line_of_a_summary_is_wider_than_the_terminal_and_the_head_loses_its_clock_first(cols) -> None:
    for events, machines in (
        (ONE, ("alpha",)),
        (THREE, ("alpha", "beta", "gamma")),
        (FAILING_IN_A_FLEET, ("alpha", "beta")),
        (CANCELLED, ("alpha",)),
    ):
        lines = _fed(events, machines).summary(200.0, cols, color=True)
        assert all(len(line) <= cols - 1 for line in _plain(lines)), _plain(lines)
    head = _plain(_fed(ONE).summary(141.0, 56, color=True))[0]
    assert head == "agent-warehouse  deploy alpha · done in 2 min 21 s"


# ---------------------------------------------------------------- review 32's rules ----


def _hidden(line: str) -> list[str]:
    """What a person cannot see in a drawn line, or that moves the terminal: a control character (a colour's escape
    aside) or a format character."""
    return [ch for ch in SGR.sub("", line) if unicodedata.category(ch) in ("Cc", "Cf")]


def test_what_the_events_said_is_drawn_as_one_visible_line_and_reports_and_failures_visible() -> None:
    # C9.md 1: a line break, a tab and a right-to-left override in a wait's words, a line of work, a report, a failure.
    hostile = "first line\nsecond\tline \u202edesserp ton\u202c end"
    events = [
        (1.0, _event("stage", None, "configure")),
        (1.0, _event("stage", "beta", "configure")),
        _work(2.0, "alpha", hostile),
        (3.0, _event("waiting", "beta", "waiting for another run\nto leave\tthe section", section=SECTION)),
        (3.5, _event("waiting", "alpha", "Answer\nthe question \u202e[y/N]")),
        (4.0, _event("report", "alpha", "\n\nalpha: one\ttab \u202eflipped\n  two  \n\n")),
    ]
    view = _fed(events, ("alpha", "beta"))
    for cols in (80, 100):
        frame = view.frame(5.0, cols, 40, "⠋", color=True)
        assert not [line for line in frame if _hidden(line)], frame
        terminal = VT(cols, 60)
        terminal.feed(b"\x1b[?2026h" + "\r\n".join(frame).encode() + b"\x1b[?2026l")
        # One row on the screen for each line the region counts, so it draws up exactly the lines drawn.
        assert terminal.r == len(frame) - 1 and terminal.frame_wraps == 0
    plain = _plain(view.frame(5.0, 100, 40, "⠋", color=False))
    assert any(line.strip().startswith("⠋ first line second line desserp ton end") for line in plain), plain
    assert any("Answer the question [y/N] · alpha" in line for line in plain), plain
    # A report: each line visible, the tab expanded as today's engine expands it, the blank first and last lines gone.
    said = [line for _, line in view.above(100, color=False)]
    assert said == ["  alpha  one  tab  flipped", "           two"]
    # A failure's why, as its lines.
    failed = {"outcome": "failed", "why": "\tapt \u202efailed\n\tE: x"}
    view.run.feed(jobs.Event(6.0, "stage", "beta", "configure", failed), 6.0)
    said = [line for _, line in view.above(100, color=False)]
    assert said[1:3] == ["✗ configure · beta: apt  failed (5.0 s)", "        E: x"]


def test_off_a_terminal_each_wait_is_said_once_with_where_whose_and_what() -> None:
    # C9.md 2: the run waits for you in its sign-in; nothing else said it there.
    said: list[str] = []
    view = _fed(TO_THE_SIGN_IN, said=said)
    assert said[-2:] == [
        f"✓ {'configure':<28} {'':<36} 59 s",
        f"⏸ {'sign in / Claude':<28} waiting for you · alpha: {SIGN_IN}",
    ]
    assert view.ended_lines(186.1) == []
    # A short section's wait, another run's, is said too, in its own words; the run's own wait names no machine.
    fleet: list[str] = []
    _fed(THREE_WAITING_ON_A_SECTION, ("alpha", "beta", "gamma"), said=fleet)
    assert fleet == [
        f"✓ {'provision':<28} {'alpha ✓ · beta ✓ · gamma ✓':<36} 24 s",
        f"⏸ {'configure':<28} beta: waiting for another run to leave the section",
    ]
    host = _fed(
        [(1.0, _event("stage", None, "template")), (2.0, _event("waiting", None, "the template", section=SECTION))]
    )
    assert host.ended_lines(3.0) == [f"⏸ {'template':<28} the template"]
    # A wait said again of its machine, a new one, is said again; in one call, in the order things happened.
    again = _fed(TO_THE_SIGN_IN)
    again.ended_lines(122.0)
    for now, kind, text, data in (
        (130.0, "waiting", "Type the code GitHub shows", {}),
        (131.0, "waiting", "", {}),
        (131.0, "step", "claude", {"stage": "sign-in", "outcome": "done"}),
    ):
        again.run.feed(jobs.Event(now, kind, "alpha", text, data), now)
    assert again.ended_lines(132.0) == [
        f"⏸ {'sign in / Claude':<28} waiting for you · alpha: Type the code GitHub shows",
        f"✓ {'sign in / Claude':<28} {'':<36} 11 s",
    ]


def test_a_machine_is_said_by_the_name_a_person_chose_never_its_identity() -> None:
    # C9.md 3: events keyed by identity, as job.json and layer 1 have them, the name beside.
    events = [
        (1.0, _event("stage", None, "provision")),
        (2.0, _event("report", ALPHA, "alpha is up at 192.0.2.61")),
        (3.0, _event("stage", None, "provision", outcome="done")),
        (3.0, _event("stage", ALPHA, "configure")),
        (3.0, _event("stage", BETA, "configure")),
        (4.0, _event("step", ALPHA, "Install baseline packages", status="task", part="common", name="alpha")),
        (5.0, _event("stage", BETA, "configure", outcome="failed", why="apt failed")),
        (5.0, _event("end", BETA, "apt failed", outcome="failed")),
    ]
    view = _fed(events, {ALPHA: "alpha", BETA: "beta"})
    frame = _plain(view.frame(6.0, 100, 40, "⠋", color=True))
    said = [line for _, line in view.above(100, color=False)]
    ended = [*view.ended_lines(6.0)]
    view.run.feed(jobs.Event(7.0, "stage", ALPHA, "configure", {"outcome": "done"}), 7.0)
    view.run.feed(jobs.Event(7.0, "end", ALPHA, "ready", {"outcome": "succeeded"}), 7.0)
    view.run.feed(jobs.Event(7.0, "end", None, "exit 1", {"outcome": "failed", "exit": 1}), 7.0)
    summary = view.summary(7.0, 100, color=False)
    ended += view.ended_lines(7.0)
    everything = [*frame, *said, *ended, *summary, view.title, view.title_text(7.0)]
    assert not [line for line in everything if "agent-warehouse-4f1c" in line], everything
    assert (
        view.title == "deploy alpha beta"
        and _marks(next(line for line in frame if " configure " in line)) == "alpha ⠋ · beta ✗"
    )
    assert any(line.endswith("alpha  0:02") for line in frame) and "  alpha is up at 192.0.2.61" in said
    assert "✗ configure · beta: apt failed (2.0 s)" in said
    assert summary[0].startswith("agent-warehouse  deploy alpha beta · done in 7 s · 1 machine failed: beta")
    assert "ALPHA" in summary and summary[-1].startswith("2 machines · 1 failed")


def test_above_says_each_report_once_and_skips_none_however_often_it_is_asked() -> None:
    # C9.md 4: one report a call, then two at once, then none.
    view = _fed([], ("alpha", "beta"))
    heard = []
    for now, batch in ((1.0, ["one"]), (2.0, ["two", "three"]), (3.0, []), (4.0, ["four"])):
        for text in batch:
            view.run.feed(jobs.Event(now, "report", None, text, {}), now)
        heard += [line.strip() for _, line in view.above(100, color=False)]
    assert heard == ["one", "two", "three", "four"]


def test_a_wait_for_you_is_said_under_its_own_machines_alone() -> None:
    # C9.md 4: two machines at one stage, one waiting for you.
    events = [(1.0, _event("stage", None, "configure")), (2.0, _event("waiting", "alpha", "Answer the question"))]
    frame = _fed(events, ("alpha", "beta")).frame(3.0, 100, 40, "⠋", color=False)
    row = next(line for line in frame if line.startswith(" ⏸ configure"))
    assert row == f" ⏸ {'configure':<13}Answer the question · alpha · 0:01"


def test_a_failed_quiet_stage_is_drawn_in_the_frame_the_lines_and_the_summary() -> None:
    # C9.md 4: deploy's finish is quiet, drawn once something in it failed.
    events = [
        (1.0, _event("stage", "alpha", "configure")),
        (2.0, _event("stage", "alpha", "configure", outcome="done")),
        (2.0, _event("stage", None, "finish")),
    ]
    said: list[str] = []
    view = _fed(events, said=said)
    assert not any("finish" in line for line in view.frame(2.5, 100, 40, "⠋", color=False))
    view.run.feed(jobs.Event(3.0, "stage", None, "finish", {"outcome": "failed", "why": "a sign-in failed"}), 3.0)
    said += view.ended_lines(3.0)
    frame = view.frame(3.0, 100, 40, "⠋", color=False)
    assert any(line.startswith(" ✗ finish ") for line in frame)
    assert said[-1] == f"✗ {'finish':<28} {'failed':<36} 1.0 s"
    view.run.feed(jobs.Event(3.0, "end", None, "a sign-in failed", {"outcome": "failed", "exit": 1}), 3.0)
    assert any(line.startswith(" ✗ finish ") for line in view.summary(3.0, 100, color=False))


def test_a_machines_line_of_work_is_drawn_only_while_it_is_at_that_stage() -> None:
    # C9.md 4: alpha has left configure; beta is still at it.
    events = [
        (1.0, _event("stage", "alpha", "configure")),
        (1.0, _event("stage", "beta", "configure")),
        _work(2.0, "alpha", "Write the motd"),
        _work(2.5, "beta", "Refresh apt cache"),
        (3.0, _event("stage", "alpha", "configure", outcome="done")),
    ]
    frame = _fed(events, ("alpha", "beta")).frame(4.0, 100, 40, "⠋", color=False)
    work = [line.strip() for line in frame if line.startswith(" " * 9)]
    assert len(work) == 1 and work[0].startswith("⠋ Refresh apt cache") and work[0].endswith("beta  0:01")


def test_a_machine_that_has_ended_is_drawn_to_come_in_a_stage_begun_after_its_end() -> None:
    # C9.md 7, with C8's fix: beta failed in configure; the aliases are then written for every machine, beta's never.
    events = [
        *FAILING_IN_A_FLEET[:7],
        (90.0, _event("stage", None, "aliases")),
    ]
    said: list[str] = []
    view = _fed(events, ("alpha", "beta"), said=said)
    frame = view.frame(90.5, 100, 40, "⠋", color=False)
    assert _marks(next(line for line in frame if " SSH aliases " in line)) == "alpha ⠋ · beta ○"
    for now, event in ((91.0, _event("stage", None, "aliases", outcome="done")), *FAILING_IN_A_FLEET[-2:]):
        view.run.feed(jobs.Event(now, *event[:3], event[3]), now)
        said += view.ended_lines(now)
    assert said[-1] == f"✓ {'SSH aliases':<28} {'alpha ✓ · beta ○':<36} 1.0 s"
    summary = view.summary(92.0, 100, color=False)
    assert f" ✓ {'SSH aliases':<11}{'1.0 s':>6}    alpha ✓ · beta ○" in summary


def test_a_failed_run_says_its_own_why_above_and_in_the_summarys_head() -> None:
    # The coordinator's ruling on C8.md 7: what a failed run left open is stopped, and its why is said.
    why = "an error the run did not handle: RuntimeError: the loop broke"
    events = [
        (1.0, _event("stage", "alpha", "configure")),
        (1.0, _event("stage", "beta", "configure")),
        (2.0, _event("stage", "alpha", "configure", outcome="failed", why="apt failed")),
        (2.0, _event("end", "alpha", "apt failed", outcome="failed")),
        (3.0, _event("end", None, why, outcome="failed", exit=1)),
    ]
    view = _fed(events, ("alpha", "beta"))
    said = [line for _, line in view.above(120, color=False)]
    assert "✗ configure · alpha: apt failed (1.0 s)" in said and f"✗ configure · this machine: {why} (3.0 s)" in said
    summary = view.summary(3.0, 160, color=False)
    assert summary[0] == f"agent-warehouse  deploy alpha beta · failed after 3 s at configure · {why}"
    assert f" ✗ {'configure':<11}{'2.0 s':>6}    alpha ✗ · beta ■" in summary
    # Narrow, the why goes first, never the outcome.
    assert view.summary(3.0, 60, color=False)[0].startswith("agent-warehouse  deploy alpha beta · failed after 3 s")


# ---------------------------------------------------------------- the rules ----


def test_nothing_in_the_view_prints_reads_a_clock_a_file_or_the_terminal_and_tui_imports_nothing_of_runs() -> None:
    tree = ast.parse(VIEW.read_text())
    imported = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported.isdisjoint({"os", "sys", "io", "pathlib", "termios", "tty", "subprocess", "asyncio", "shutil"})
    called = {
        (node.func.value.id if isinstance(node.func.value, ast.Name) else "", node.func.attr)
        if isinstance(node.func, ast.Attribute)
        else ("", node.func.id)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert called.isdisjoint({("", "print"), ("", "open"), ("", "input"), ("time", "time"), ("time", "monotonic")})
    assert {("time", "localtime"), ("time", "strftime")} <= called, "a time given, said as a clock"
    assert "\\x1b" not in VIEW.read_text() and "\x1b" not in VIEW.read_text(), "only tui/screen.py writes a colour"
    # The drawing of today's names nothing of the new engine's: runs/ imports tui/, never the reverse.
    for path in TUI.glob("*.py"):
        found = {
            node.module if isinstance(node, ast.ImportFrom) else alias.name
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not {name for name in found if name and "runs" in name.split(".")}, path


def test_the_view_loads_nothing_of_today_s_run_model() -> None:
    # The view draws with tui/screen.py alone: an import of it in a fresh interpreter loads neither today's drawing of
    # a run (tui/run.py) nor today's model under it (run/model.py, run/recipes.py). Of run/, only next_steps, which
    # runs/model.py reuses for NEXT STEPS.
    loaded = _python(
        f"import sys; sys.path.insert(0, {str(REPO / 'lib')!r}); import claude_vm.runs.view; "
        "print('\\n'.join(sorted(sys.modules)))"
    ).split()
    assert "claude_vm.runs.view" in loaded and "claude_vm.tui.screen" in loaded, loaded
    assert "claude_vm.tui.run" not in loaded
    assert {name for name in loaded if name.split(".")[:2] == ["claude_vm", "run"]} == {
        "claude_vm.run",
        "claude_vm.run.next_steps",
    }


def test_a_failure_draws_twice_the_lines_today_s_run_keeps_of_what_it_said() -> None:
    # tui/screen.py imports no model, so the lines a failure draws are its own number, held here to today's.
    assert screen.FAILURE_SAID == 2 * todays_model.FAILURE_LINES
    said = [f"line {number}" for number in range(screen.FAILURE_SAID + 5)]
    failure = {"where": "common", "who": "alpha", "task": "Write the motd", "seconds": None, "how": "failed"}
    drawn_lines = [line for _, line in screen.failure_lines({**failure, "said": said, "log": ""}, 100, False)]
    assert [line.strip() for line in drawn_lines if line.strip().startswith("line ")] == said[: screen.FAILURE_SAID]


def test_a_view_draws_a_runs_model_and_nothing_else() -> None:
    with pytest.raises(TypeError, match="runs.model.Run"):
        View(drawn.Run("deploy alpha", 0.0), started_at=STARTED)
    assert _fed([], ("alpha", "beta")).title == "deploy alpha beta"
    assert _fed([], title="deploy --check alpha").title == "deploy --check alpha"
