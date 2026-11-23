"""A run as a person reads it, from a verb's declared stages and its events (lib/claude_vm/runs/model.py and
runs/stages.py; docs/architecture/engine.md §16.8).

Canned lists of events, as a run emits them, and what the model holds after each: a deploy of one machine, of three,
one with a machine failing in configure, one waiting for a sign-in, and one a person cancelled. NEXT STEPS is said once
and in today's order (run/next_steps.py's). The rules with a test of their own: an event of a stage the declaration has
not, of a step its stage does not declare, or after the run's end is refused with nothing kept; a wait is ended by its
own machine and section alone, and at the cancel; a step that names no stage (layer 1's events) is a line of work and
begins or ends nothing; deploy's stages are today's labels; and the model imports nothing of its drawing, nor today's
model, and reads nothing. One run is fed by a real job's emit, as the live view will be.
"""

from __future__ import annotations

import ast
import copy

import pytest
from aw_kit import imports_of
from harness import REPO

from claude_vm.fleet import paths
from claude_vm.run import next_steps as todays_next_steps
from claude_vm.run import recipes
from claude_vm.runs import job as jobs
from claude_vm.runs import model
from claude_vm.runs.stages import DEPLOY, Declared, Stage, Step

RUNS = REPO / "lib" / "claude_vm" / "runs"
PAGE = "https://claude.ai/oauth/authorize"
SIGN_IN = "Approve the sign-in in your browser"
SECTION = "template-pve"


def _event(kind: str, machine: str | None, text: str, **data: object) -> jobs.Event:
    """An event as runs/job.py's emit hands it to a listener; the model reads no time of the event's own."""
    return jobs.Event(0.0, kind, machine, text, data)


def _states(run: model.Run) -> dict[str, str]:
    """Each stage that has begun, with its state."""
    return {key: run.state(key) for key in run.stages if run.state(key) != "to come"}


def _held(run: model.Run) -> dict[str, object]:
    """Everything the model holds that a drawer reads, as plain values: what a refused event must leave as it was."""
    parts = {
        (key, step): {
            machine: (part.began, part.ended, part.outcome, part.why) for machine, part in found.parts.items()
        }
        for key, progress in run.stages.items()
        for step, found in [(None, progress), *progress.steps.items()]
    }
    return copy.deepcopy(
        {
            "parts": parts,
            "machines": list(run.machines),
            "waits": [vars(wait) for wait in run.waits],
            "reports": list(run.reports),
            "failures": list(run.failures),
            "outcomes": dict(run.outcomes),
            "latest": dict(run.latest),
            "next": run.next_records(),
            "end": (run.stopping, run.ended, run.outcome, run.exit, run.why),
        }
    )


def _fed(run: model.Run, script) -> None:
    """Each (now, event, stages' states, checks) fed in order, the states and checks asserted after each event."""
    for number, (now, event, expected, checks) in enumerate(script, 1):
        run.feed(event, now)
        where = f"after event {number}, {event.kind} {event.machine} {event.text!r} {dict(event.data)}"
        assert _states(run) == expected, where
        for check, value in (checks or {}).items():
            if check == "at":
                assert run.running_stage() == value, where
            elif check == "waiting":
                assert [(wait.machine, wait.text) for wait in run.waiting()] == value, where
            elif check == "you":
                assert run.waiting_for_you() is value, where
            else:
                stage, _, step = check.partition("/")
                assert run.marks(stage, step or None) == value, f"{where}: {check}"


# ---------------------------------------------------------------- canned runs ----


def test_a_deploy_of_one_machine_reads_stage_by_stage_as_each_event_says() -> None:
    run = model.Run(DEPLOY, 0.0, ["alpha"])
    assert _states(run) == {} and run.running_stage() is None and run.machines == ["alpha"]
    configured = {"template": "done", "provision": "done", "configure": "done"}
    done_to_aliases = configured | {"aliases": "done"}
    _fed(
        run,
        [
            # The template is the host's: no machine is marked in it.
            (1.0, _event("stage", None, "template"), {"template": "running"}, {"at": "template", "template": {}}),
            (3.0, _event("stage", None, "template", outcome="done"), {"template": "done"}, {"at": None}),
            (
                3.0,
                _event("stage", "alpha", "provision"),
                {"template": "done", "provision": "running"},
                {"at": "provision", "provision": {"alpha": "running"}},
            ),
            (
                40.0,
                _event("report", "alpha", "alpha is up at 192.0.2.61"),
                {"template": "done", "provision": "running"},
                {},
            ),
            (
                40.0,
                _event("stage", "alpha", "provision", outcome="done"),
                {"template": "done", "provision": "done"},
                {"at": None, "provision": {"alpha": "done"}},
            ),
            (
                41.0,
                _event("stage", "alpha", "configure"),
                {"template": "done", "provision": "done", "configure": "running"},
                {},
            ),
            (100.0, _event("stage", "alpha", "configure", outcome="done"), configured, {}),
            (100.0, _event("stage", "alpha", "aliases"), configured | {"aliases": "running"}, {"at": "aliases"}),
            (101.0, _event("stage", "alpha", "aliases", outcome="done"), done_to_aliases, {}),
            # A step's start begins its stage.
            (
                101.0,
                _event("step", "alpha", "claude", stage="sign-in"),
                done_to_aliases | {"sign-in": "running"},
                {"at": "sign-in", "sign-in/claude": {"alpha": "running"}, "sign-in/github": {"alpha": "to come"}},
            ),
            (
                130.0,
                _event("step", "alpha", "claude", stage="sign-in", outcome="done"),
                done_to_aliases | {"sign-in": "running"},
                {"sign-in/claude": {"alpha": "done"}, "sign-in": {"alpha": "running"}},
            ),
            (
                130.0,
                _event("stage", "alpha", "sign-in", outcome="done"),
                done_to_aliases | {"sign-in": "done"},
                {"sign-in/github": {"alpha": "to come"}},
            ),
            (131.0, _event("stage", "alpha", "editor"), done_to_aliases | {"sign-in": "done", "editor": "running"}, {}),
            (
                140.0,
                _event("next", "alpha", "aw login alpha --only github", matter="github"),
                done_to_aliases | {"sign-in": "done", "editor": "running"},
                {},
            ),
            (
                140.0,
                _event("stage", "alpha", "editor", outcome="done"),
                done_to_aliases | {"sign-in": "done", "editor": "done"},
                {},
            ),
            (
                141.0,
                _event("end", "alpha", "ready", outcome="succeeded"),
                done_to_aliases | {"sign-in": "done", "editor": "done"},
                {},
            ),
            (
                141.0,
                _event("end", None, "", outcome="succeeded", exit=0),
                done_to_aliases | {"sign-in": "done", "editor": "done"},
                {"at": None},
            ),
        ],
    )
    assert run.ended == 141.0 and (run.outcome, run.exit) == ("succeeded", 0)
    assert run.outcomes == {"alpha": model.MachineEnd("succeeded", "editor", "ready", 141.0)}
    assert [(report.machine, report.text) for report in run.reports] == [("alpha", "alpha is up at 192.0.2.61")]
    assert run.failures == [] and run.waits == []
    assert run.next_steps() == ["aw login alpha --only github"]
    # Elapsed times: the run's, a stage's, a step's, a machine's.
    assert run.seconds(200.0) == 141.0
    assert run.seconds(200.0, "provision") == 37.0 and run.seconds(200.0, "provision", machine="alpha") == 37.0
    assert run.seconds(200.0, "sign-in", "claude") == 29.0 and run.seconds(200.0, "golden") == 0.0
    # Nothing is taken after the run's end.
    with pytest.raises(ValueError, match="the run has ended"):
        run.feed(_event("report", "alpha", "late"), 150.0)


def test_a_deploy_of_three_machines_marks_each_and_reads_every_machines_part_where_it_has_none_of_its_own() -> None:
    run = model.Run(DEPLOY, 0.0, ["alpha", "beta", "gamma"])
    each = {"alpha": "running", "beta": "running", "gamma": "running"}
    _fed(
        run,
        [
            # Begun for every machine: each reads it.
            (1.0, _event("stage", None, "provision"), {"provision": "running"}, {"provision": each}),
            (
                20.0,
                _event("stage", "beta", "provision", outcome="done"),
                {"provision": "running"},
                {"provision": each | {"beta": "done"}},
            ),
            (
                25.0,
                _event("stage", None, "provision", outcome="done"),
                {"provision": "done"},
                {"provision": {"alpha": "done", "beta": "done", "gamma": "done"}},
            ),
            # Each machine on its own: alpha and gamma configure side by side, beta not yet.
            (26.0, _event("stage", "alpha", "configure"), {"provision": "done", "configure": "running"}, {}),
            (
                26.0,
                _event("stage", "gamma", "configure"),
                {"provision": "done", "configure": "running"},
                {"configure": {"alpha": "running", "beta": "to come", "gamma": "running"}},
            ),
            (27.0, _event("stage", "beta", "configure"), {"provision": "done", "configure": "running"}, {}),
            (
                60.0,
                _event("stage", "alpha", "configure", outcome="done"),
                {"provision": "done", "configure": "running"},
                {},
            ),
            (
                61.0,
                _event("stage", "beta", "configure", outcome="skipped"),
                {"provision": "done", "configure": "running"},
                {},
            ),
            (
                62.0,
                _event("stage", "gamma", "configure", outcome="done"),
                {"provision": "done", "configure": "done"},
                {"configure": {"alpha": "done", "beta": "skipped", "gamma": "done"}},
            ),
            # The sign-ins one machine after another: whose turn it is, and who is to come.
            (
                63.0,
                _event("step", "alpha", "claude", stage="sign-in"),
                {"provision": "done", "configure": "done", "sign-in": "running"},
                {"sign-in/claude": {"alpha": "running", "beta": "to come", "gamma": "to come"}},
            ),
            (
                70.0,
                _event("step", "alpha", "claude", stage="sign-in", outcome="done"),
                {"provision": "done", "configure": "done", "sign-in": "running"},
                {},
            ),
            (
                70.0,
                _event("step", "beta", "claude", stage="sign-in"),
                {"provision": "done", "configure": "done", "sign-in": "running"},
                {"sign-in/claude": {"alpha": "done", "beta": "running", "gamma": "to come"}},
            ),
        ],
    )
    assert run.machines == ["alpha", "beta", "gamma"]
    assert run.seconds(100.0, "configure") == 36.0 and run.seconds(100.0, "configure", machine="beta") == 34.0
    # A stage's end for every machine ends every part still open, its steps' among them, and begins no step: gamma,
    # which never began the step, is still to come in it, though the stage has ended for it.
    run.feed(_event("stage", None, "sign-in", outcome="failed", why="the run stopped asking"), 80.0)
    assert run.marks("sign-in", "claude") == {"alpha": "done", "beta": "failed", "gamma": "to come"}
    assert run.marks("sign-in") == {"alpha": "failed", "beta": "failed", "gamma": "failed"}
    assert run.state("sign-in", "github") == "to come" and run.state("sign-in") == "failed"
    assert [(f.stage, f.step, f.machine, f.why) for f in run.failures] == [
        ("sign-in", None, None, "the run stopped asking")
    ]


def test_a_machine_that_fails_in_configure_is_said_once_and_the_others_go_on() -> None:
    run = model.Run(DEPLOY, 0.0, ["alpha", "beta"])
    for now, event in (
        (1.0, _event("stage", None, "provision")),
        (30.0, _event("stage", None, "provision", outcome="done")),
        (31.0, _event("stage", "alpha", "configure")),
        (31.0, _event("stage", "beta", "configure")),
    ):
        run.feed(event, now)
    why = "Install baseline packages: E: Unable to locate package nope"
    run.feed(_event("stage", "beta", "configure", outcome="failed", why=why), 50.0)
    assert run.marks("configure") == {"alpha": "running", "beta": "failed"}
    assert run.state("configure") == "running" and run.running_stage() == "configure"
    assert run.failures == [model.Failure("configure", None, "beta", why, 19.0, 50.0)]
    # Its machine's end says nothing more, and leaves the others as they are.
    run.feed(_event("end", "beta", why, outcome="failed"), 50.0)
    assert len(run.failures) == 1 and run.outcomes["beta"] == model.MachineEnd("failed", "configure", why, 50.0)
    assert run.marks("configure") == {"alpha": "running", "beta": "failed"}
    run.feed(_event("stage", "alpha", "configure", outcome="done"), 90.0)
    run.feed(_event("stage", "alpha", "aliases"), 90.0)
    run.feed(_event("stage", "alpha", "aliases", outcome="done"), 91.0)
    assert _states(run) == {"provision": "done", "configure": "failed", "aliases": "done"}
    assert run.marks("aliases") == {"alpha": "done", "beta": "to come"}
    run.feed(_event("end", "alpha", "ready", outcome="succeeded"), 92.0)
    run.feed(_event("end", None, "one machine failed", outcome="failed", exit=1), 92.0)
    # The run's failure was its machine's: nothing is said twice.
    assert run.failures == [model.Failure("configure", None, "beta", why, 19.0, 50.0)]
    assert (run.outcome, run.exit, run.why) == ("failed", 1, "one machine failed")


def test_a_failure_is_said_once_a_steps_and_not_again_by_its_stage() -> None:
    declared = Declared("configure", (Stage("configure", "configure", steps=(Step("common", "common"),)),))
    run = model.Run(declared, 0.0, ["alpha"])
    run.feed(_event("step", "alpha", "common", stage="configure"), 1.0)
    run.feed(_event("step", "alpha", "common", stage="configure", outcome="failed", why="apt failed"), 5.0)
    run.feed(_event("stage", "alpha", "configure", outcome="failed", why="apt failed"), 5.0)
    assert run.failures == [model.Failure("configure", "common", "alpha", "apt failed", 4.0, 5.0)]
    # A machine that fails with nothing of its own failed is said, once; and so is a run that fails with nothing failed.
    other = model.Run(declared, 0.0)
    other.feed(_event("stage", None, "configure"), 1.0)
    other.feed(_event("end", "beta", "its address never came", outcome="failed"), 3.0)
    other.feed(_event("end", "beta", "its address never came", outcome="failed"), 3.0)
    assert other.failures == [model.Failure("configure", None, "beta", "its address never came", 3.0, 3.0)]
    third = model.Run(declared, 0.0)
    third.feed(_event("stage", None, "configure"), 1.0)
    third.feed(_event("end", None, "RuntimeError: the loop broke", outcome="failed", exit=1), 4.0)
    assert third.failures == [model.Failure("configure", None, None, "RuntimeError: the loop broke", 4.0, 4.0)]
    assert third.state("configure") == "failed"


def test_a_run_waiting_for_a_sign_in_waits_for_you_there_until_its_wait_ends() -> None:
    run = model.Run(DEPLOY, 0.0, ["alpha", "beta"])
    begun = {"sign-in": "running"}
    _fed(
        run,
        [
            (10.0, _event("step", "alpha", "claude", stage="sign-in"), begun, {"you": False}),
            (
                11.0,
                _event("waiting", "alpha", SIGN_IN, page=PAGE),
                {"sign-in": "waiting"},
                {
                    "you": True,
                    "waiting": [("alpha", SIGN_IN)],
                    "sign-in/claude": {"alpha": "waiting", "beta": "to come"},
                },
            ),
            # Another run's section, which a machine waits for: no wait for you.
            (
                12.0,
                _event("waiting", "beta", "waiting for another run to leave the section", section=SECTION),
                {"sign-in": "waiting"},
                {"waiting": [("alpha", SIGN_IN), ("beta", "waiting for another run to leave the section")]},
            ),
            (
                76.0,
                _event("waiting", "alpha", ""),
                begun,
                {"you": False, "waiting": [("beta", "waiting for another run to leave the section")]},
            ),
            (76.0, _event("waiting", "beta", "", section=SECTION), begun, {"waiting": []}),
            (
                80.0,
                _event("step", "alpha", "claude", stage="sign-in", outcome="done"),
                begun,
                {"sign-in/claude": {"alpha": "done", "beta": "to come"}},
            ),
        ],
    )
    wait = run.waits[0]
    assert (wait.machine, wait.section, wait.stage, wait.step, wait.since, wait.until) == (
        "alpha",
        None,
        "sign-in",
        "claude",
        11.0,
        76.0,
    )
    assert wait.data == {"page": PAGE} and wait.yours and not run.waits[1].yours
    # The person's time: the sign-in's wait alone, never another run's section.
    assert run.waited(100.0) == 65.0 == run.waited(100.0, "sign-in", "claude") and run.waited(100.0, "configure") == 0.0


def test_a_cancelled_run_ends_its_waits_at_the_cancel_and_what_is_open_reads_stopped_at_its_end() -> None:
    run = model.Run(DEPLOY, 0.0, ["alpha"])
    run.feed(_event("stage", "alpha", "configure"), 1.0)
    run.feed(_event("stage", "alpha", "configure", outcome="done"), 9.0)
    run.feed(_event("step", "alpha", "claude", stage="sign-in"), 10.0)
    run.feed(_event("waiting", "alpha", SIGN_IN, page=PAGE), 11.0)
    run.feed(_event("waiting", None, "waiting for another run to leave the section", section=SECTION), 12.0)
    assert run.waiting_for_you() and run.state("sign-in") == "waiting"
    # A Ctrl-C: nobody is asked any more, though the run goes on through its grace.
    run.cancel(20.0)
    assert run.stopping == 20.0 and run.waiting() == [] and not run.waiting_for_you()
    assert [wait.until for wait in run.waits] == [20.0, 20.0]
    assert run.state("sign-in") == "running" and run.marks("sign-in", "claude") == {"alpha": "running"}
    run.cancel(25.0)
    assert run.stopping == 20.0
    run.feed(_event("end", None, "Ctrl-C", outcome="cancelled", exit=130), 30.0)
    assert _states(run) == {"configure": "done", "sign-in": "stopped"}
    assert run.marks("sign-in", "claude") == {"alpha": "stopped"} and run.failures == []
    assert (run.outcome, run.exit, run.why) == ("cancelled", 130, "Ctrl-C")
    run.cancel(31.0)
    assert run.stopping == 20.0


def test_a_machines_end_ends_what_of_it_is_open_and_its_waits_and_says_the_stage_it_reached() -> None:
    run = model.Run(DEPLOY, 0.0)
    run.feed(_event("stage", None, "template"), 0.5)
    run.feed(_event("stage", None, "configure"), 1.0)
    run.feed(_event("waiting", "alpha", "waiting for another run to leave the section", section=SECTION), 2.0)
    run.feed(_event("waiting", "beta", SIGN_IN), 2.0)
    run.feed(_event("end", "alpha", "the machine went away", outcome="cancelled"), 3.0)
    # Its stage is every machine's last, and the host's template is no machine's to end; beta still waits for you.
    assert run.outcomes["alpha"] == model.MachineEnd("cancelled", "configure", "the machine went away", 3.0)
    assert run.marks("configure") == {"alpha": "stopped", "beta": "waiting"}
    assert run.state("template") == "running" and run.progress("template").parts.keys() == {None}
    assert [(wait.machine, wait.until) for wait in run.waits] == [("alpha", 3.0), ("beta", None)]
    run.feed(_event("end", "beta", "", outcome="succeeded", stage="configure"), 4.0)
    assert run.marks("configure") == {"alpha": "stopped", "beta": "done"} and run.waiting() == []


# ---------------------------------------------------------------- the rules ----


def test_a_wait_is_ended_by_its_own_machine_and_section_alone() -> None:
    # C6.md 8 and C7.md 6: the job's record keeps one wait, which a clear of another machine's or of no section wipes;
    # the model keeps each machine's and each section's apart.
    run = model.Run(DEPLOY, 0.0, ["a", "b"])
    run.feed(_event("waiting", "a", "waiting for another run to leave the section", section=SECTION), 1.0)
    run.feed(_event("waiting", "b", SIGN_IN), 1.0)
    run.feed(_event("waiting", "a", SIGN_IN), 1.0)
    for machine, section in (("b", SECTION), ("a", "nextid-pve"), (None, None), (None, SECTION)):
        run.feed(_event("waiting", machine, "", **({"section": section} if section else {})), 2.0)
    assert [(wait.machine, wait.section) for wait in run.waiting()] == [("a", SECTION), ("b", None), ("a", None)]
    run.feed(_event("waiting", "a", "", section=SECTION), 3.0)
    assert [(wait.machine, wait.section) for wait in run.waiting()] == [("b", None), ("a", None)]
    run.feed(_event("waiting", "b", ""), 3.0)
    assert [(wait.machine, wait.section) for wait in run.waiting()] == [("a", None)]
    # A wait said again of the same machine and section takes the place of the one open.
    run.feed(_event("waiting", "a", "Type the code GitHub shows"), 4.0)
    assert [(wait.text, wait.until) for wait in run.waits if wait.machine == "a" and wait.section is None] == [
        (SIGN_IN, 4.0),
        ("Type the code GitHub shows", None),
    ]


def test_next_steps_are_said_once_in_todays_order_and_an_empty_line_takes_one_away() -> None:
    run = model.Run(DEPLOY, 0.0)
    lines = [
        ("beta", "github", "aw login beta --only github"),
        ("beta", "claude", "aw login beta --only claude"),
        ("alpha", "editor", "aw code alpha"),
        ("alpha", "claude", "aw login alpha --only claude"),
        ("alpha", "vm", "aw deploy alpha --only configure"),
        (None, "init_check", "aw init"),
        ("alpha", "unknown-matter", "aw status"),
        ("beta", "tailscale", "aw status"),
    ]
    for machine, matter, line in lines:
        run.feed(_event("next", machine, line, matter=matter), 1.0)
    run.feed(_event("next", "alpha", "aw code alpha --project app", matter="editor"), 2.0)
    run.feed(_event("next", "beta", "", matter="claude"), 2.0)
    run.feed(_event("next", "gamma", "", matter="claude"), 2.0)
    said = [
        "aw init",
        "aw deploy alpha --only configure",
        "aw login alpha --only claude",
        "aw code alpha --project app",
        "aw status",
        "aw login beta --only github",
    ]
    assert run.next_steps() == said
    # As today's function orders today's records of the same lines, the run's own under a key no machine has.
    todays = {
        "claude_vm_next": {"": {"init_check": "aw init"}, "beta": {"github": "aw login beta --only github"}},
        "code_next": {
            "alpha": {
                "editor": "aw code alpha --project app",
                "claude": "aw login alpha --only claude",
                "vm": "aw deploy alpha --only configure",
                "unknown-matter": "aw status",
            },
            "beta": {"tailscale": "aw status"},
        },
    }
    assert todays_next_steps.next_steps(todays) == said
    assert run.next_records()[0] == {"machine": "beta", "matter": "github", "line": "aw login beta --only github"}
    assert ("beta", "claude") not in {(record["machine"], record["matter"]) for record in run.next_records()}


@pytest.mark.parametrize(
    ("event", "refusal"),
    [
        (_event("stage", "beta", "build"), "'build' is no stage of deploy"),
        (_event("stage", None, "configure", outcome="succeeded"), "ends done, skipped or failed"),
        (_event("step", "alpha", "claude", stage="build"), "'build' is no stage of deploy"),
        (_event("step", "alpha", "common", stage="configure"), "'common' is no step of stage 'configure'"),
        (_event("step", "alpha", "github", stage="editor"), "'github' is no step of stage 'editor'"),
        (_event("step", "alpha", "claude", stage="sign-in", outcome="ok"), "ends done, skipped or failed"),
        (_event("child", "beta", "ssh"), "'child' is no kind of event"),
        (_event("next", "alpha", "aw login alpha"), "names its matter"),
        (_event("end", "alpha", "", outcome="done"), "an end says its outcome"),
    ],
)
def test_an_event_the_declaration_or_the_job_does_not_have_is_refused_with_nothing_kept(event, refusal) -> None:
    run = model.Run(DEPLOY, 0.0, ["alpha"])
    run.feed(_event("stage", "alpha", "configure"), 1.0)
    run.feed(_event("waiting", "alpha", SIGN_IN), 1.0)
    run.feed(_event("next", "alpha", "aw login alpha", matter="claude"), 1.0)
    before = _held(run)
    with pytest.raises(ValueError, match=refusal):
        run.feed(event, 2.0)
    assert _held(run) == before


@pytest.mark.parametrize(
    "event",
    [
        _event("step", "alpha", "claude", stage=7),
        _event("stage", "alpha", "configure", outcome="failed", why=["apt"]),
        _event("waiting", "alpha", "x", section=3),
        _event("end", None, "", outcome="failed", exit="1"),
        _event("end", "alpha", "", outcome="failed", stage=2),
    ],
)
def test_a_value_of_the_wrong_type_is_refused_with_nothing_kept(event) -> None:
    run = model.Run(DEPLOY, 0.0, ["alpha"])
    run.feed(_event("stage", "alpha", "configure"), 1.0)
    before = _held(run)
    with pytest.raises(TypeError):
        run.feed(event, 2.0)
    assert _held(run) == before


def test_a_step_that_names_no_stage_is_a_line_of_work_that_begins_and_ends_nothing() -> None:
    # Layer 1's events (guest/events.py) are steps of a machine with no stage: a task's name, its status and its part.
    run = model.Run(DEPLOY, 0.0, ["alpha"])
    run.feed(_event("stage", "alpha", "configure"), 1.0)
    before = _held(run)
    for now, status in ((2.0, "task"), (3.0, "changed"), (4.0, "failed")):
        run.feed(_event("step", "alpha", "Install baseline packages", status=status, part="common"), now)
    run.feed(_event("step", "beta", "Gathering Facts", status="ok", part=""), 5.0)
    assert run.latest == {
        "alpha": model.Line("Install baseline packages", "configure", None, 4.0),
        "beta": model.Line("Gathering Facts", None, None, 5.0),
    }
    held = _held(run)
    assert held.pop("latest") and held.pop("machines") == ["alpha", "beta"]
    assert held == {key: value for key, value in before.items() if key not in ("latest", "machines")}
    assert run.failures == [] and run.state("configure") == "running"


def test_the_model_is_fed_by_a_jobs_emit_as_the_live_view_will_be(home) -> None:
    made = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "aw deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True
    )
    try:
        run = model.Run(DEPLOY, 0.0, ["alpha"])
        made.listen(lambda event: run.feed(event, event.time))
        made.emit("stage", "alpha", "provision")
        made.emit("stage", "alpha", "provision", outcome="done")
        made.emit("step", "alpha", "claude", stage="sign-in")
        made.emit("waiting", "alpha", "\x1b[1mApprove\x1b[0m the sign-in", page=PAGE)
        made.emit("next", "alpha", "aw login alpha --only claude", matter="claude")
        made.emit("end", "alpha", "signed in", outcome="succeeded")
        assert _states(run) == {"provision": "done", "sign-in": "done"}
        assert run.waits[0].text == "Approve the sign-in" and run.waiting() == []
        assert run.next_steps() == ["aw login alpha --only claude"]
        # The record keeps what an agent reads; the model the same of it.
        assert made.read()["next"] == run.next_records()
        assert made.read()["machine_outcomes"]["alpha"]["outcome"] == run.outcomes["alpha"].outcome
        with pytest.raises(ValueError, match="'build' is no stage of deploy"):
            made.emit("stage", "alpha", "build")
    finally:
        jobs.drop(made)


def test_deploys_stages_are_todays_labels() -> None:
    todays = recipes.RECIPES["deploy"]
    assert [stage.label for stage in DEPLOY.stages] == [stage.key for stage in todays.stages]
    assert [stage.quiet for stage in DEPLOY.stages] == [stage.quiet for stage in todays.stages]
    assert [stage.each_machine for stage in DEPLOY.stages] == [stage.vms for stage in todays.stages]
    for ours, theirs in zip(DEPLOY.stages, todays.stages, strict=True):
        assert [step.label for step in ours.steps] == [step.label for step in theirs.steps], ours.key
    assert DEPLOY.title == "deploy" and DEPLOY.stage("sign-in").step("github").label == "GitHub"
    assert DEPLOY.stage("build") is None and DEPLOY.stage("configure").step("common") is None


@pytest.mark.parametrize(
    "make",
    [
        lambda: Step("Common", "common"),
        lambda: Step("common", ""),
        lambda: Step("common", "two\nlines"),
        lambda: Stage("a-", "a"),
        lambda: Stage("a", "a", steps=(Step("b", "b"), Step("b", "c"))),
        lambda: Stage("a", "a", steps=[Step("b", "b")]),
        lambda: Stage("a", "a", quiet=1),
        lambda: Declared("deploy", ()),
        lambda: Declared("deploy", (Stage("a", "a"), Stage("a", "b"))),
        lambda: model.Run(object(), 0.0),
        lambda: model.Run(DEPLOY, 0.0, [None]),
    ],
)
def test_a_declaration_is_held_to_its_form(make) -> None:
    with pytest.raises((TypeError, ValueError)):
        make()


def test_the_model_imports_nothing_of_its_drawing_nor_todays_model_and_reads_nothing(home) -> None:
    # The case of tests/unit/test_claude_vm.py's test_a_model_imports_nothing_of_its_drawing, for the new engine's
    # model: runs/view.py draws it with tui/, never the reverse.
    for name in ("model.py", "stages.py"):
        tree = ast.parse((RUNS / name).read_text())
        imported = {
            node.module if isinstance(node, ast.ImportFrom) else alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not {each for each in imported if each and each.startswith(("claude_vm.tui", "claude_vm.run.model"))}
        # It reads no clock, file, process or terminal: every time is given to it.
        assert imported.isdisjoint({"time", "os", "sys", "io", "pathlib", "termios", "subprocess", "asyncio"}), name
    loaded = imports_of(module="claude_vm.runs.model", home=home)
    assert not {name for name in loaded if name.startswith("claude_vm.tui")}
    assert loaded.isdisjoint({"claude_vm.run.model", "claude_vm.run.recipes", "claude_vm.ansible"})
    assert "claude_vm.run.next_steps" in loaded
