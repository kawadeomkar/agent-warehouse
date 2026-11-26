"""A run as a person reads it, from a verb's declared stages and its events (lib/claude_vm/runs/model.py and
runs/stages.py; docs/architecture/engine.md §16.8).

Canned lists of events, as a run emits them, and what the model holds after each: a deploy of one machine, of three,
one with a machine failing in configure, one waiting for a sign-in, and one a person cancelled. NEXT STEPS is said once
and in today's order (run/next_steps.py's). The rules with a test of their own: an event of a stage the declaration has
not, of a step its stage does not declare, or after the run's end is refused with nothing kept; a wait is ended by its
own machine and section alone, and at the cancel; a step that names no stage (layer 1's events) is a line of work and
begins or ends nothing; deploy's stages are today's labels; and the model imports nothing of its drawing, nor today's
model, and reads nothing. One run is fed by a real job's emit, as the live view will be.

Review 29's rules (review/code/C8.md), each with a test: a stage that ended reads failed where a step of it failed; a
machine that has ended reads no part of every machine's begun after its end; the stage a machine reached is its own last
stage event, as the job's record keeps it; a failed run cuts short what is still open (stopped) and says its own why;
a report kept whole, in order, each time it is said; the run's end ending the host's and the quiet stages; a machine's
end ending its open steps; one machine's wait for you never another's mark; a refused step's machine never learned; a
machine's failed end said though another machine failed before; a replaced NEXT STEPS line kept in its place; and one
key for a machine, its identity, with the name a person chose kept beside it.

Its second reading's (review/code/C8.md, "The fix, read again"), each with a test: an event that names a machine by the
name the run keeps for another key, or whose name is another key, refused with nothing kept (A); a machine's failed end
said where it cut short a part still open, though something of it failed before (B); and a machine's own begin keeping
its time in a part of every machine's it reads, and a step never beginning again a stage that ended for its machine
(8), held over every order of one stage's events that keeps each begin before its end.

Its third reading's (review/code/C8.md, "The second fix, read again"), with a test: a machine's failed end right after a
failed step of the stage it is in, still open, says nothing twice, where a step it cuts short is still said (B2).
"""

from __future__ import annotations

import ast
import copy
import itertools

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
# Machines as the job's record names them: by identity (agent-warehouse-<SMBIOS UUID>).
ALPHA = "agent-warehouse-4f1c0000-0000-4000-8000-000000000001"
BETA = "agent-warehouse-4f1c0000-0000-4000-8000-000000000002"
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
            "names": dict(run.names),
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
    # beta's own stage events name no stage, so its end names none, as the job's record has it.
    assert other.failures == [model.Failure(None, None, "beta", "its address never came", 3.0, 3.0)]
    third = model.Run(declared, 0.0)
    third.feed(_event("stage", None, "configure"), 1.0)
    third.feed(_event("end", None, "RuntimeError: the loop broke", outcome="failed", exit=1), 4.0)
    assert third.failures == [model.Failure("configure", None, None, "RuntimeError: the loop broke", 4.0, 4.0)]
    # What the run's failure left open was cut short: stopped, never failed.
    assert third.state("configure") == "stopped"


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


def test_a_declined_run_says_no_failure_and_what_is_open_reads_stopped_at_its_end() -> None:
    # C38: the person's answer ended the run (destroy's no, exit 1): no failure is said of it, its own why among them,
    # where a run that fails with exit 1 says its why as one.
    why = "the deletion was not confirmed"
    for outcome, failures in (("declined", []), ("failed", [("configure", why)])):
        run = model.Run(DEPLOY, 0.0, ["alpha"])
        run.feed(_event("stage", None, "configure"), 1.0)
        run.feed(_event("waiting", "alpha", SIGN_IN, page=PAGE), 2.0)
        run.feed(_event("end", None, why, outcome=outcome, exit=1), 3.0)
        assert [(failure.stage, failure.why) for failure in run.failures] == failures
        assert _states(run) == {"configure": "stopped"} and run.waiting() == []
        assert (run.outcome, run.exit, run.why) == (outcome, 1, why)
    run = model.Run(DEPLOY, 0.0, ["alpha", "beta"])
    run.feed(_event("stage", "alpha", "configure"), 1.0)
    run.feed(_event("end", "alpha", "not confirmed", outcome="declined"), 2.0)
    assert run.outcomes["alpha"] == model.MachineEnd("declined", "configure", "not confirmed", 2.0)
    assert run.failures == [] and run.marks("configure")["alpha"] == "stopped"


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
    # Its stage is its own last stage event's, none here, as the job's record keeps it; the host's template is no
    # machine's to end; beta still waits for you.
    assert run.outcomes["alpha"] == model.MachineEnd("cancelled", None, "the machine went away", 3.0)
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
        # A machine not given, and its name, never learned from an event that is refused.
        (_event("step", "omega", "common", stage="configure", name="o"), "'common' is no step of stage 'configure'"),
        (_event("step", "omega", "claude", stage="sign-in", outcome="ok"), "ends done, skipped or failed"),
        (_event("stage", "omega", "build", name="o"), "'build' is no stage of deploy"),
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
        _event("step", "alpha", "Gathering Facts", name=7),
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
        made.emit("next", "alpha", "aw status", matter="claude")
        made.emit("next", "alpha", "aw login alpha --only github", matter="github")
        # A line said again for its machine and matter takes the first one's place, in the record and here.
        made.emit("next", "alpha", "aw login alpha --only claude", matter="claude")
        made.emit("end", "alpha", "signed in", outcome="succeeded")
        assert _states(run) == {"provision": "done", "sign-in": "done"}
        assert run.waits[0].text == "Approve the sign-in" and run.waiting() == []
        assert run.next_steps() == ["aw login alpha --only claude", "aw login alpha --only github"]
        # The record keeps what an agent reads; the model the same of it.
        assert made.read()["next"] == run.next_records()
        assert [record["matter"] for record in run.next_records()] == ["claude", "github"]
        ended = made.read()["machine_outcomes"]["alpha"]
        assert (ended["outcome"], ended["stage"]) == (run.outcomes["alpha"].outcome, run.outcomes["alpha"].stage)
        with pytest.raises(ValueError, match="'build' is no stage of deploy"):
            made.emit("stage", "alpha", "build")
    finally:
        jobs.drop(made)


# ---------------------------------------------------------------- review 29's rules ----


def test_a_stage_that_ended_reads_failed_where_a_step_of_it_failed() -> None:
    # C8.md 1: a sign-in workflow says each step's end, then the stage's end done; the failed step wins, as today's.
    run = model.Run(DEPLOY, 0.0, ["alpha"])
    run.feed(_event("step", "alpha", "claude", stage="sign-in"), 1.0)
    run.feed(_event("step", "alpha", "claude", stage="sign-in", outcome="failed", why="the code expired"), 2.0)
    assert run.state("sign-in") == "running" and run.mark("alpha", "sign-in") == "running"
    run.feed(_event("step", "alpha", "github", stage="sign-in"), 2.0)
    run.feed(_event("step", "alpha", "github", stage="sign-in", outcome="done"), 3.0)
    run.feed(_event("stage", "alpha", "sign-in", outcome="done"), 3.0)
    assert run.state("sign-in") == "failed" and run.mark("alpha", "sign-in") == "failed"
    assert run.marks("sign-in", "claude") == {"alpha": "failed"} and run.marks("sign-in", "github") == {"alpha": "done"}
    # Said once, by its step.
    assert [(each.stage, each.step, each.machine) for each in run.failures] == [("sign-in", "claude", "alpha")]
    # Begun and ended for every machine: the machine whose step failed reads failed, the other done; the stage failed.
    fleet = model.Run(DEPLOY, 0.0, ["alpha", "beta"])
    fleet.feed(_event("stage", None, "sign-in"), 1.0)
    for machine, outcome in (("alpha", "failed"), ("beta", "done")):
        fleet.feed(_event("step", machine, "claude", stage="sign-in"), 1.0)
        fleet.feed(_event("step", machine, "claude", stage="sign-in", outcome=outcome, why="expired"), 2.0)
    fleet.feed(_event("stage", None, "sign-in", outcome="done"), 3.0)
    assert fleet.state("sign-in") == "failed" and fleet.marks("sign-in") == {"alpha": "failed", "beta": "done"}


def test_a_machine_that_has_ended_reads_no_part_of_every_machines_begun_after_its_end() -> None:
    # C8.md 2: beta fails in configure and ends; the aliases are then begun and ended for every machine, at the same
    # time as beta's end: the order the events came in decides.
    run = model.Run(DEPLOY, 0.0, ["alpha", "beta"])
    for now, event in (
        (0.5, _event("stage", None, "provision")),
        (0.6, _event("end", "gamma", "its address never came", outcome="failed")),
        (1.0, _event("stage", None, "provision", outcome="done")),
        (1.0, _event("stage", "alpha", "configure")),
        (1.0, _event("stage", "beta", "configure")),
        (2.0, _event("stage", "beta", "configure", outcome="failed", why="apt failed")),
        (2.0, _event("end", "beta", "apt failed", outcome="failed")),
        (2.0, _event("stage", None, "aliases")),
    ):
        run.feed(event, now)
    # gamma ended while provision ran for every machine: that part was its own too, ended with it.
    assert run.marks("provision") == {"alpha": "done", "beta": "done", "gamma": "failed"}
    assert run.marks("aliases") == {"alpha": "running", "beta": "to come", "gamma": "to come"}
    assert run.seconds(3.0, "aliases", machine="beta") == 0.0 and not run.progress("aliases").open_for("beta")
    run.feed(_event("stage", None, "aliases", outcome="done"), 3.0)
    assert run.marks("aliases") == {"alpha": "done", "beta": "to come", "gamma": "to come"}
    assert run.outcomes["beta"].stage == "configure" and run.mark("beta", "configure") == "failed"


def test_the_stage_a_machine_reached_is_its_own_last_stage_event_as_the_jobs_record_keeps_it(home) -> None:
    # C8.md 5: deploy's golden template is the host's, begun after every machine's own stages; a step's start begins
    # its stage, but is no stage event of the machine's.
    made = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "aw deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True
    )
    try:
        run = model.Run(DEPLOY, 0.0, ["alpha"])
        made.listen(lambda event: run.feed(event, event.time))
        for stage in ("provision", "configure", "aliases", "editor"):
            made.emit("stage", "alpha", stage)
            made.emit("stage", "alpha", stage, outcome="done")
        made.emit("step", "alpha", "claude", stage="sign-in")
        made.emit("stage", None, "golden")
        made.emit("end", "alpha", "ready", outcome="succeeded")
        assert run.outcomes["alpha"].stage == made.read()["machine_outcomes"]["alpha"]["stage"] == "editor"
    finally:
        jobs.drop(made)


def test_a_failed_run_cuts_short_what_is_still_open_and_says_its_own_why() -> None:
    # C8.md 7: alpha fails in configure; beta is still configuring when the run ends failed of its own error.
    why = "an error the run did not handle: RuntimeError: the loop broke"
    run = model.Run(DEPLOY, 0.0, ["alpha", "beta"])
    run.feed(_event("stage", "alpha", "configure"), 1.0)
    run.feed(_event("stage", "beta", "configure"), 1.0)
    run.feed(_event("step", "beta", "Install baseline packages", status="task", part="common"), 1.5)
    run.feed(_event("stage", "alpha", "configure", outcome="failed", why="apt failed"), 2.0)
    run.feed(_event("end", "alpha", "apt failed", outcome="failed"), 2.0)
    run.feed(_event("end", None, why, outcome="failed", exit=1), 3.0)
    # beta was cut short, not failed; the run's own why is said, once, as the run's.
    assert run.marks("configure") == {"alpha": "failed", "beta": "stopped"}
    assert [(failure.stage, failure.machine, failure.why) for failure in run.failures] == [
        ("configure", "alpha", "apt failed"),
        ("configure", None, why),
    ]
    assert run.failures[-1] == model.Failure("configure", None, None, why, 3.0, 3.0)
    # A run that fails only because its machines did, with nothing left open, says nothing twice.
    done = model.Run(DEPLOY, 0.0, ["alpha"])
    done.feed(_event("stage", "alpha", "configure"), 1.0)
    done.feed(_event("stage", "alpha", "configure", outcome="failed", why="apt failed"), 2.0)
    done.feed(_event("end", "alpha", "apt failed", outcome="failed"), 2.0)
    done.feed(_event("end", None, "exit 1", outcome="failed", exit=1), 2.0)
    assert [failure.why for failure in done.failures] == ["apt failed"]
    # A stage ended failed for every machine after one machine's failure says why it ended what was still open.
    fleet = model.Run(DEPLOY, 0.0, ["alpha", "beta"])
    fleet.feed(_event("stage", "alpha", "configure"), 1.0)
    fleet.feed(_event("stage", "beta", "configure"), 1.0)
    fleet.feed(_event("stage", "alpha", "configure", outcome="failed", why="apt failed"), 2.0)
    fleet.feed(_event("stage", None, "configure", outcome="failed", why="the run stopped configuring"), 3.0)
    assert fleet.marks("configure") == {"alpha": "failed", "beta": "failed"}
    assert [(failure.machine, failure.why) for failure in fleet.failures] == [
        ("alpha", "apt failed"),
        (None, "the run stopped configuring"),
    ]


def test_a_report_is_kept_whole_and_in_order_each_time_it_is_said() -> None:
    # C8.md 3: several lines, a control character, a tab, a last line break or none, a megabyte, and one said again.
    texts = [
        "alpha: first line\n\tsecond line\x07 with a bell\n",
        "no last line break",
        "x" * 1_000_000 + "\nthe end",
        "alpha: first line\n\tsecond line\x07 with a bell\n",
    ]
    run = model.Run(DEPLOY, 0.0, ["alpha"])
    for now, text in enumerate(texts, 1):
        run.feed(_event("report", "alpha", text), float(now))
    assert [report.text for report in run.reports] == texts
    assert [(report.machine, report.at) for report in run.reports] == [("alpha", float(now)) for now in (1, 2, 3, 4)]


@pytest.mark.parametrize(
    ("outcome", "closed"),
    [("succeeded", "done"), ("failed", "stopped"), ("cancelled", "stopped"), ("declined", "stopped")],
)
def test_the_runs_end_ends_every_stage_still_open_the_hosts_and_the_quiet_ones_too(outcome, closed) -> None:
    # C8.md 3: nothing runs once the run has ended, the template, the golden template and the quiet finish among them.
    run = model.Run(DEPLOY, 0.0, ["alpha"])
    run.feed(_event("stage", None, "template"), 1.0)
    run.feed(_event("stage", "alpha", "configure"), 1.0)
    run.feed(_event("step", "alpha", "claude", stage="sign-in"), 2.0)
    run.feed(_event("stage", None, "finish"), 2.0)
    run.feed(_event("stage", None, "golden"), 2.0)
    run.feed(_event("end", None, "the end", outcome=outcome, exit={"succeeded": 0, "failed": 1}.get(outcome, 130)), 3.0)
    begun = ("template", "configure", "sign-in", "finish", "golden")
    assert _states(run) == dict.fromkeys(begun, closed) and run.running() == []
    assert run.state("sign-in", "claude") == closed and run.marks("sign-in", "claude") == {"alpha": closed}


@pytest.mark.parametrize(
    ("outcome", "closed"),
    [("succeeded", "done"), ("failed", "failed"), ("cancelled", "stopped"), ("declined", "stopped")],
)
def test_a_machines_end_ends_its_open_steps_as_its_outcome_says(outcome, closed) -> None:
    # C8.md 3: what of a machine is open ends at its end, its steps too; another machine's are left as they are.
    run = model.Run(DEPLOY, 0.0, ["alpha", "beta"])
    run.feed(_event("step", "alpha", "claude", stage="sign-in"), 1.0)
    run.feed(_event("step", "beta", "claude", stage="sign-in"), 1.0)
    run.feed(_event("end", "alpha", "gone", outcome=outcome), 2.0)
    assert run.marks("sign-in", "claude") == {"alpha": closed, "beta": "running"}
    assert run.marks("sign-in") == {"alpha": closed, "beta": "running"}


def test_one_machines_wait_for_you_is_never_another_machines_mark() -> None:
    # C8.md 3: two machines at one stage, one waiting for you; and at one step.
    run = model.Run(DEPLOY, 0.0, ["alpha", "beta"])
    run.feed(_event("stage", None, "configure"), 1.0)
    run.feed(_event("waiting", "alpha", "Answer the question"), 2.0)
    assert run.marks("configure") == {"alpha": "waiting", "beta": "running"}
    run.feed(_event("step", "alpha", "claude", stage="sign-in"), 3.0)
    run.feed(_event("step", "beta", "claude", stage="sign-in"), 3.0)
    run.feed(_event("waiting", "alpha", ""), 3.0)
    run.feed(_event("waiting", "beta", SIGN_IN), 4.0)
    assert run.marks("sign-in", "claude") == {"alpha": "running", "beta": "waiting"}
    assert run.marks("sign-in") == {"alpha": "running", "beta": "waiting"}


def test_a_machines_failed_end_is_said_though_another_machine_failed_before() -> None:
    # C8.md 3: each failure said once, none lost.
    run = model.Run(DEPLOY, 0.0, ["alpha", "beta"])
    run.feed(_event("stage", "alpha", "configure"), 1.0)
    run.feed(_event("stage", "alpha", "configure", outcome="failed", why="apt failed"), 2.0)
    run.feed(_event("stage", "beta", "provision"), 2.0)
    run.feed(_event("end", "beta", "its address never came", outcome="failed"), 3.0)
    assert [(failure.stage, failure.machine, failure.why) for failure in run.failures] == [
        ("configure", "alpha", "apt failed"),
        ("provision", "beta", "its address never came"),
    ]
    assert run.mark("beta", "provision") == "failed"


def test_a_machine_is_keyed_by_its_identity_and_keeps_the_name_a_person_chose() -> None:
    # The ruling on C8.md 4: one key for every event of a machine, its identity, as job.json's machines and layer 1's
    # steps have it; the name beside it, given with the machines or said by an event (layer 1's `name`).
    run = model.Run(DEPLOY, 0.0, {ALPHA: "alpha"})
    run.feed(_event("stage", ALPHA, "configure"), 1.0)
    run.feed(_event("step", ALPHA, "Install baseline packages", status="task", part="common", name="alpha"), 2.0)
    run.feed(_event("waiting", ALPHA, SIGN_IN), 3.0)
    run.feed(_event("stage", ALPHA, "configure", outcome="done"), 4.0)
    run.feed(_event("end", ALPHA, "ready", outcome="succeeded"), 4.0)
    assert run.machines == [ALPHA] and run.names == {ALPHA: "alpha"} and run.name(ALPHA) == "alpha"
    assert run.marks("configure") == {ALPHA: "done"} and list(run.latest) == [ALPHA]
    assert [wait.machine for wait in run.waits] == [ALPHA] and list(run.outcomes) == [ALPHA]
    # Given identities alone, an event's name says it; a machine never named reads as its key.
    other = model.Run(DEPLOY, 0.0, [ALPHA, BETA])
    other.feed(_event("step", BETA, "Gathering Facts", status="ok", part="", name="beta"), 1.0)
    assert other.machines == [ALPHA, BETA] and other.name(BETA) == "beta" and other.name(ALPHA) == ALPHA
    # NEXT STEPS by the name a person chose, as today's by VM name, whatever the identities' order.
    named = model.Run(DEPLOY, 0.0, {BETA: "alpha", ALPHA: "beta"})
    named.feed(_event("next", ALPHA, "aw login beta --only claude", matter="claude"), 1.0)
    named.feed(_event("next", BETA, "aw login alpha --only claude", matter="claude"), 1.0)
    assert named.next_steps() == ["aw login alpha --only claude", "aw login beta --only claude"]


# ---------------------------------------------------------------- review 29's second reading ----


def test_an_event_never_names_a_machine_by_the_name_the_run_keeps_for_another_key() -> None:
    # A: every emitter passes the identity. An event keyed by the name the run keeps for another key, or whose name is
    # another key of the run's, would make one machine two rows drawn under one name: refused, with nothing kept.
    run = model.Run(DEPLOY, 0.0, {ALPHA: "alpha"})
    run.feed(_event("stage", ALPHA, "configure"), 1.0)
    run.feed(_event("waiting", ALPHA, SIGN_IN), 1.0)
    before = _held(run)
    for event in (
        _event("stage", "alpha", "configure"),
        _event("stage", "alpha", "configure", outcome="failed", why="apt failed"),
        _event("step", "alpha", "claude", stage="sign-in"),
        _event("step", "alpha", "Gathering Facts", status="ok", part="", name="alpha"),
        _event("waiting", "alpha", SIGN_IN),
        _event("report", "alpha", "alpha is up"),
        _event("next", "alpha", "aw login alpha", matter="claude"),
        _event("end", "alpha", "apt failed", outcome="failed"),
    ):
        with pytest.raises(ValueError, match="'alpha' is the name of a machine the run keys by"):
            run.feed(event, 2.0)
        assert _held(run) == before, event
    with pytest.raises(ValueError, match="is the key of another machine of the run"):
        run.feed(_event("step", BETA, "Gathering Facts", status="ok", part="", name=ALPHA), 2.0)
    assert _held(run) == before
    assert [run.name(machine) for machine in run.machines] == ["alpha"]
    # A name said by an event is kept the same way.
    said = model.Run(DEPLOY, 0.0, [ALPHA, BETA])
    said.feed(_event("step", ALPHA, "Gathering Facts", status="ok", part="", name="alpha"), 1.0)
    with pytest.raises(ValueError, match="'alpha' is the name of a machine the run keys by"):
        said.feed(_event("waiting", "alpha", SIGN_IN), 2.0)
    # The other order: a machine an emitter keyed by its name is taken while nothing says whose name that is; then the
    # identity's event that would name it so is refused, not drawn as a second row of the same name.
    first = model.Run(DEPLOY, 0.0, [ALPHA])
    first.feed(_event("waiting", "alpha", SIGN_IN), 1.0)
    before = _held(first)
    with pytest.raises(ValueError, match="'alpha' is the key of another machine of the run"):
        first.feed(_event("step", ALPHA, "Gathering Facts", status="ok", part="", name="alpha"), 2.0)
    assert _held(first) == before and [first.name(machine) for machine in first.machines] == [ALPHA, "alpha"]
    # A machine keyed by its name throughout, that name its own, is one row, as the canned runs key theirs.
    plain = model.Run(DEPLOY, 0.0, ["alpha"])
    plain.feed(_event("step", "alpha", "Gathering Facts", status="ok", part="", name="alpha"), 1.0)
    plain.feed(_event("stage", "alpha", "configure"), 1.0)
    assert plain.machines == ["alpha"] and plain.names == {"alpha": "alpha"}


def test_a_machines_failed_end_is_said_where_it_cut_short_a_part_still_open() -> None:
    # B: alpha's Claude sign-in failed (said; deploy goes on); then alpha fails and ends while the editor reaches it.
    # The end cut the editor short, which reads failed: its why is said, as the run's end and a stage's end for every
    # machine say what they cut short.
    run = model.Run(DEPLOY, 0.0, ["alpha"])
    run.feed(_event("stage", "alpha", "sign-in"), 1.0)
    run.feed(_event("step", "alpha", "claude", stage="sign-in"), 1.0)
    run.feed(_event("step", "alpha", "claude", stage="sign-in", outcome="failed", why="the code expired"), 2.0)
    run.feed(_event("stage", "alpha", "sign-in", outcome="done"), 3.0)
    run.feed(_event("stage", "alpha", "editor"), 4.0)
    run.feed(_event("step", "alpha", "reach", stage="editor"), 4.0)
    run.feed(_event("end", "alpha", "the editor could not reach it", outcome="failed"), 9.0)
    assert run.marks("editor") == {"alpha": "failed"} and run.marks("editor", "reach") == {"alpha": "failed"}
    assert [(failure.stage, failure.step, failure.machine, failure.why) for failure in run.failures] == [
        ("sign-in", "claude", "alpha", "the code expired"),
        ("editor", None, "alpha", "the editor could not reach it"),
    ]
    # A part of every machine's that it reads is cut short too, and another machine's goes on.
    fleet = model.Run(DEPLOY, 0.0, ["alpha", "beta"])
    fleet.feed(_event("step", "alpha", "claude", stage="sign-in"), 1.0)
    fleet.feed(_event("step", "alpha", "claude", stage="sign-in", outcome="failed", why="the code expired"), 2.0)
    fleet.feed(_event("stage", "alpha", "sign-in", outcome="done"), 2.0)
    fleet.feed(_event("stage", None, "editor"), 3.0)
    fleet.feed(_event("end", "alpha", "the editor could not reach it", outcome="failed", stage="editor"), 5.0)
    assert fleet.marks("editor") == {"alpha": "failed", "beta": "running"}
    assert [(failure.stage, failure.machine, failure.why) for failure in fleet.failures] == [
        ("sign-in", "alpha", "the code expired"),
        ("editor", "alpha", "the editor could not reach it"),
    ]
    # A step still open is cut short, though its stage had ended for it (a step begins no ended stage again).
    step = model.Run(DEPLOY, 0.0, ["alpha"])
    step.feed(_event("stage", "alpha", "sign-in"), 1.0)
    step.feed(_event("stage", "alpha", "sign-in", outcome="failed", why="Claude refused"), 2.0)
    step.feed(_event("step", "alpha", "github", stage="sign-in"), 3.0)
    step.feed(_event("end", "alpha", "gh hung", outcome="failed"), 4.0)
    assert step.marks("sign-in", "github") == {"alpha": "failed"}
    assert [(failure.step, failure.why) for failure in step.failures] == [(None, "Claude refused"), (None, "gh hung")]
    # Where its end cut nothing short, the failure that ended it was said already: nothing is said twice.
    said = model.Run(DEPLOY, 0.0, ["alpha"])
    said.feed(_event("stage", "alpha", "configure"), 1.0)
    said.feed(_event("stage", "alpha", "configure", outcome="failed", why="apt failed"), 2.0)
    said.feed(_event("end", "alpha", "apt failed", outcome="failed"), 2.0)
    assert [failure.why for failure in said.failures] == ["apt failed"]


def test_a_machines_own_begin_keeps_its_time_and_a_step_never_begins_again_a_stage_ended_for_it() -> None:
    # 8 (C8.md 8): under the ruling a verb emits a machine's own stage beside every machine's. alpha's own begin, while
    # every machine's part it reads runs, takes that part's start: its time goes on.
    run = model.Run(DEPLOY, 0.0, ["alpha", "beta"])
    run.feed(_event("stage", None, "aliases"), 6.5)
    assert run.seconds(10.5, "aliases", machine="alpha") == 4.0
    run.feed(_event("stage", "alpha", "aliases"), 14.0)
    assert run.seconds(14.0, "aliases", machine="alpha") == 7.5 and run.mark("alpha", "aliases") == "running"
    run.feed(_event("stage", "alpha", "aliases", outcome="done"), 15.0)
    assert run.seconds(20.0, "aliases", machine="alpha") == 8.5 and run.seconds(20.0, "aliases", machine="beta") == 13.5
    # Begun again where it had ended: a new start.
    run.feed(_event("stage", None, "aliases", outcome="done"), 16.0)
    run.feed(_event("stage", "beta", "aliases"), 18.0)
    assert run.seconds(20.0, "aliases", machine="beta") == 2.0
    # T1c: a stage that ended failed for alpha, then a step of it: the step runs, the stage is not begun again, and it
    # reads failed, its time its own, its failure said once.
    ended = model.Run(DEPLOY, 0.0, ["alpha"])
    ended.feed(_event("stage", "alpha", "sign-in"), 1.0)
    ended.feed(_event("stage", "alpha", "sign-in", outcome="failed", why="Claude refused"), 3.0)
    ended.feed(_event("step", "alpha", "github", stage="sign-in"), 4.0)
    assert ended.mark("alpha", "sign-in") == "failed" and ended.marks("sign-in", "github") == {"alpha": "running"}
    ended.feed(_event("step", "alpha", "github", stage="sign-in", outcome="done"), 6.0)
    ended.feed(_event("stage", "alpha", "sign-in", outcome="done"), 6.0)
    assert ended.state("sign-in") == "failed" and ended.mark("alpha", "sign-in") == "failed"
    assert ended.seconds(10.0, "sign-in", machine="alpha") == 2.0
    assert [(failure.stage, failure.why) for failure in ended.failures] == [("sign-in", "Claude refused")]
    # Ended for every machine, the same: a step of one machine's begins it again for none.
    every = model.Run(DEPLOY, 0.0, ["alpha", "beta"])
    every.feed(_event("stage", None, "editor"), 1.0)
    every.feed(_event("stage", None, "editor", outcome="failed", why="the run stopped the editors"), 2.0)
    every.feed(_event("step", "alpha", "window", stage="editor"), 3.0)
    assert every.marks("editor") == {"alpha": "failed", "beta": "failed"} and every.state("editor") == "failed"
    assert every.marks("editor", "window") == {"alpha": "running", "beta": "to come"}


def _orders_of_one_stage() -> list[list[jobs.Event]]:
    """Every order of one stage's events that keeps each begin before its end: the sign-in begun and ended for every
    machine, begun and ended for alpha alone, and alpha's Claude step begun and ended, each ending done or failed
    (90 orders of six events, eight ways of ending each: 720)."""
    orders = []
    for endings in itertools.product(("done", "failed"), repeat=3):
        pairs = []
        parts = ((None, "stage", "sign-in"), ("alpha", "stage", "sign-in"), ("alpha", "step", "claude"))
        for (machine, kind, text), ending in zip(parts, endings, strict=True):
            where = {"stage": "sign-in"} if kind == "step" else {}
            ends = {"outcome": ending} | ({"why": f"{kind} {ending}"} if ending == "failed" else {})
            pairs.append((_event(kind, machine, text, **where), _event(kind, machine, text, **where, **ends)))
        for order in itertools.permutations(range(6)):
            if all(order.index(2 * pair) < order.index(2 * pair + 1) for pair in range(3)):
                orders.append([pairs[number // 2][number % 2] for number in order])
    return orders


def test_over_every_order_of_one_stages_events_a_machines_time_goes_on_and_a_step_begins_no_ended_stage() -> None:
    # 8, over made-up orders as the reviewer's were: after each event, for alpha and for beta (which has no event of its
    # own and reads every machine's part), in the stage and in its step: while a machine's mark stays under way, the
    # start its time is read from never moves; and a step's start never changes the mark of a stage that had ended for
    # its machine (nor its time there).
    orders = _orders_of_one_stage()
    assert len(orders) == 720
    under_way = ("running", "waiting")
    checked = {"time": 0, "step": 0}
    for order in orders:
        run = model.Run(DEPLOY, 0.0, ["alpha", "beta"])
        for number, event in enumerate(order, 1):
            now = float(number)
            # Each machine's mark in the stage and its step, and the start its time there is read from.
            before = {
                (machine, step): (
                    run.mark(machine, "sign-in", step),
                    now - 1 - run.seconds(now - 1, "sign-in", step, machine),
                )
                for machine in ("alpha", "beta")
                for step in (None, "claude")
            }
            ended = run.seconds(now - 1, "sign-in", machine="alpha")
            run.feed(event, now)
            where = f"event {number} of {[(each.kind, each.machine, dict(each.data)) for each in order]}"
            for (machine, step), (mark, start) in before.items():
                after = run.mark(machine, "sign-in", step)
                if mark in under_way and after in under_way:
                    checked["time"] += 1
                    assert now - run.seconds(now, "sign-in", step, machine) == start, f"{machine} {step}: {where}"
            if event.kind == "step" and "outcome" not in event.data and before[("alpha", None)][0] not in under_way:
                if before[("alpha", None)][0] != "to come":
                    checked["step"] += 1
                    assert run.mark("alpha", "sign-in") == before[("alpha", None)][0], where
                    assert run.seconds(now, "sign-in", machine="alpha") == ended, where
    # Neither rule held vacuously: each was asked of many orders.
    assert checked["time"] > 1000 and checked["step"] > 100, checked


# ---------------------------------------------------------------- review 29's third reading ----

# configure with a step for each role, as layer 1's runner will declare it (G4's).
CONFIGURE = Declared(
    "deploy", (Stage("configure", "configure", steps=(Step("common", "common"), Step("dev-tools", "dev tools"))),)
)


@pytest.mark.parametrize(
    ("declared", "stage", "step"), [(CONFIGURE, "configure", "common"), (DEPLOY, "sign-in", "claude")]
)
@pytest.mark.parametrize("begun", ["by its stage", "by the step"])
def test_a_machines_failed_end_right_after_a_failed_step_of_its_open_stage_says_nothing_twice(
    declared, stage, step, begun
) -> None:
    # B2: G4's ordinary order. A step ends failed, said; then its machine ends failed, the step's stage still open for
    # it, with the same words. The end cuts the stage short, which reads failed, and says nothing again.
    why = "Install baseline packages: E: Unable to locate package nope"
    run = model.Run(declared, 0.0, ["alpha"])
    if begun == "by its stage":
        run.feed(_event("stage", "alpha", stage), 1.0)
    run.feed(_event("step", "alpha", step, stage=stage), 1.0)
    run.feed(_event("step", "alpha", step, stage=stage, outcome="failed", why=why), 5.0)
    assert run.progress(stage).open_for("alpha")
    run.feed(_event("end", "alpha", why, outcome="failed", stage=stage), 5.0)
    assert run.failures == [model.Failure(stage, step, "alpha", why, 4.0, 5.0)]
    assert run.mark("alpha", stage) == "failed" and not run.progress(stage).open_for("alpha")
    assert run.outcomes["alpha"] == model.MachineEnd("failed", stage, why, 5.0)
    # In a stage begun for every machine, the same, and the other machine goes on.
    fleet = model.Run(declared, 0.0, ["alpha", "beta"])
    fleet.feed(_event("stage", None, stage), 1.0)
    for machine in ("alpha", "beta"):
        fleet.feed(_event("step", machine, step, stage=stage), 1.0)
    fleet.feed(_event("step", "alpha", step, stage=stage, outcome="failed", why=why), 5.0)
    fleet.feed(_event("end", "alpha", why, outcome="failed", stage=stage), 5.0)
    assert [(each.stage, each.step, each.machine, each.why) for each in fleet.failures] == [(stage, step, "alpha", why)]
    assert fleet.marks(stage) == {"alpha": "failed", "beta": "running"}
    # A step it cuts short is said though a step of its stage failed before: that step had said nothing.
    other = next(each.key for each in declared.stage(stage).steps if each.key != step)
    more = model.Run(declared, 0.0, ["alpha"])
    more.feed(_event("step", "alpha", step, stage=stage), 1.0)
    more.feed(_event("step", "alpha", step, stage=stage, outcome="failed", why=why), 5.0)
    more.feed(_event("step", "alpha", other, stage=stage), 5.0)
    more.feed(_event("end", "alpha", "the connection was lost", outcome="failed"), 7.0)
    assert [(each.step, each.why) for each in more.failures] == [(step, why), (None, "the connection was lost")]
    assert more.marks(stage, other) == {"alpha": "failed"}


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
        lambda: model.Run(DEPLOY, 0.0, {ALPHA: 7}),
        # One machine, one row: no machine given by another's name, and no name another machine's key.
        lambda: model.Run(DEPLOY, 0.0, {ALPHA: "alpha", "alpha": None}),
        lambda: model.Run(DEPLOY, 0.0, {"alpha": None, ALPHA: "alpha"}),
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
