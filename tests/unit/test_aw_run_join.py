"""A VM a run is making is named by a stand-in key until its identity is known, and one event joins the two
(lib/claude_vm/runs/job.py's stand_in and its `join` event, runs/model.py's JOINED, runs/live.py;
docs/architecture/engine.md §16.8).

A run that makes a VM: its events under the stand-in from the first, the first with the VM's name, then the join, then
the identity's, read as one row with its parts, its times and one outcome; at every event the model holds exactly what
the same run keyed by the identity from its first event holds, each thing the join moves (JOINED) in its place, for a
run that goes on, one whose failure is said before the join and its failed end after it (said once, by B2's rule and by
the end's "nothing of it failed" alike), one that ended before its join, and one whose NEXT STEPS lines and reports
stand among another machine's, with a section's wait open across the join; each refused join, and each event that names
a stand-in wrongly, changes nothing, the names and the order of the events counted included, and a join to an identity
the run keeps anything for is refused whichever of what a join moves keeps it. The job's record keeps a machine not yet
made in `making`, by its stand-in with its name, and `machines` identities alone; the join moves what the record keeps
under the stand-in to the identity, in its place, a section's wait among it, and keeps the VM's name under its identity
in `names`, written and read back across it, at each event the record of the same run keyed by the identity; each
refusal writes nothing and is heard by nobody, a stand-in's nameless first wait among them; a run killed before its join
leaves a record that says what it was making. The live view draws the VM's row by its name from its first event (from
the record's `making` where it is made later, and from its `names` after the join), the join changes nothing it draws,
frame for frame and line for line off a terminal, and nothing is drawn twice; its refusal line and its -v stream say the
VM by its name alone, never a key.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import re
import select
import signal
import subprocess
import sys
from collections.abc import Iterator

import harness
import pytest
from aw_kit import environment
from harness import REPO

from claude_vm import checks
from claude_vm.fleet import paths
from claude_vm.pve import client
from claude_vm.runs import job as jobs
from claude_vm.runs import live, model
from claude_vm.runs.stages import Declared, Stage, Step
from claude_vm.runs.view import View
from claude_vm.schema import patterns
from claude_vm.text import plain_text

LIB = REPO / "lib"
# Machines by identity (agent-warehouse-<SMBIOS UUID>), as the job's record names them; alpha's is known only once its
# clone gives it one.
ALPHA = "agent-warehouse-4f1c0000-0000-4000-8000-0000000000a1"
BETA = "agent-warehouse-4f1c0000-0000-4000-8000-0000000000b2"
GAMMA = "agent-warehouse-4f1c0000-0000-4000-8000-0000000000c3"
DELTA = "agent-warehouse-4f1c0000-0000-4000-8000-0000000000d4"
# alpha's stand-in, and another machine's, as job.stand_in() makes them.
MADE = "making:5e1f0000000000a1"
OTHER = "making:5e1f0000000000c3"
# The section of the next VMID on the host pve (runs/locks.py's section), whose wait a run that makes a VM may meet.
NEXTID = "nextid-pve"

# A deploy whose provision is said step by step, as the workflow that makes a VM says it.
MAKING = Declared(
    "deploy",
    (
        Stage("template", "template", each_machine=False),
        Stage("provision", "provision", steps=(Step("clone", "clone"), Step("start", "start"))),
        Stage("configure", "configure"),
        Stage("sign-in", "sign in", steps=(Step("claude", "Claude"),)),
    ),
)


def _event(kind: str, machine: str | None, text: str, **data: object) -> jobs.Event:
    """An event as runs/job.py's emit hands it to a listener; the model reads no time of the event's own."""
    return jobs.Event(0.0, kind, machine, text, data)


# ---------------------------------------------------------------- the runs: (now, kind, machine, text, data) ----

# alpha made beside beta: a wait for another run open across the join and cleared after it, a line of work, a report
# and NEXT STEPS lines under the stand-in, the join while the clone's step runs, then the identity's events going on in
# the same parts.
MADE_AND_CONFIGURED = [
    (1.0, "stage", None, "template", {}),
    (1.0, "stage", BETA, "provision", {}),
    (1.0, "stage", MADE, "provision", {"name": "alpha"}),
    (2.0, "step", MADE, "clone", {"stage": "provision"}),
    (2.0, "waiting", MADE, "waiting for another run to leave the template", {"section": "template-pve"}),
    (3.0, "step", MADE, "Cloning from the template", {}),
    (3.5, "report", MADE, "alpha is cloned", {}),
    (3.5, "next", MADE, "aw login alpha --only claude", {"matter": "claude"}),
    (3.5, "stage", BETA, "provision", {"outcome": "done"}),
    (3.8, "join", ALPHA, MADE, {}),
    (4.0, "step", ALPHA, "clone", {"stage": "provision", "outcome": "done"}),
    (5.0, "waiting", ALPHA, "", {"section": "template-pve"}),
    (5.0, "step", ALPHA, "start", {"stage": "provision"}),
    (6.0, "waiting", ALPHA, "Approve the start", {}),
    (7.0, "waiting", ALPHA, "", {}),
    (8.0, "step", ALPHA, "start", {"stage": "provision", "outcome": "done"}),
    (8.0, "stage", ALPHA, "provision", {"outcome": "done"}),
    (8.0, "report", ALPHA, "alpha is up at 192.0.2.61", {}),
    (8.0, "stage", None, "template", {"outcome": "done"}),
    (9.0, "stage", ALPHA, "configure", {}),
    (9.5, "step", ALPHA, "Install baseline packages", {"status": "task", "part": "common", "name": "alpha"}),
    (10.0, "next", ALPHA, "aw login alpha --only github", {"matter": "github"}),
    (11.0, "stage", ALPHA, "configure", {"outcome": "done"}),
    (11.0, "end", ALPHA, "ready", {"outcome": "succeeded"}),
    (11.0, "end", BETA, "ready", {"outcome": "succeeded"}),
    (12.0, "end", None, "", {"outcome": "succeeded", "exit": 0}),
]
# A step that failed before the join, its stage still open, then the machine's failed end after it: B2's rule reads the
# failure's machine, so it is said once.
STEP_FAILED_BEFORE = [
    (1.0, "stage", MADE, "provision", {"name": "alpha"}),
    (1.0, "step", MADE, "clone", {"stage": "provision"}),
    (2.0, "step", MADE, "clone", {"stage": "provision", "outcome": "failed", "why": "the clone's task failed"}),
    (2.5, "join", ALPHA, MADE, {}),
    (3.0, "end", ALPHA, "the clone's task failed", {"outcome": "failed"}),
    (3.0, "end", None, "the clone's task failed", {"outcome": "failed", "exit": 1}),
]
# A stage that failed before the join, then the machine's failed end after it: the end's "nothing of it failed" reads
# the failure's machine, so it is said once.
STAGE_FAILED_BEFORE = [
    (1.0, "stage", MADE, "provision", {"name": "alpha"}),
    (2.0, "stage", MADE, "provision", {"outcome": "failed", "why": "no VMID was free"}),
    (2.5, "join", ALPHA, MADE, {}),
    (3.0, "end", ALPHA, "no VMID was free", {"outcome": "failed"}),
    (3.0, "end", None, "no VMID was free", {"outcome": "failed", "exit": 1}),
]
# A machine that ended before its join, its outcome so far: a stage of every machine's begun after its end is none of
# its own, by the order its end came in, which the join keeps.
ENDED_BEFORE = [
    (1.0, "stage", None, "template", {}),
    (1.0, "stage", MADE, "provision", {"name": "alpha"}),
    (2.0, "end", MADE, "stopped before its UUID was read", {"outcome": "cancelled"}),
    (2.5, "join", ALPHA, MADE, {}),
    (3.0, "stage", None, "configure", {}),
    (4.0, "stage", None, "configure", {"outcome": "done"}),
    (4.0, "stage", None, "template", {"outcome": "done"}),
    (5.0, "end", None, "", {"outcome": "cancelled", "exit": 130}),
]
# alpha's NEXT STEPS lines and reports among beta's, and a section's wait of alpha's open across the join: the join
# keeps each where it stood, never moved to the end, and moves the section's wait too.
INTERLEAVED = [
    (1.0, "stage", BETA, "provision", {}),
    (1.0, "stage", MADE, "provision", {"name": "alpha"}),
    (1.5, "next", BETA, "aw login beta --only claude", {"matter": "claude"}),
    (1.5, "next", MADE, "aw login alpha --only claude", {"matter": "claude"}),
    (1.5, "next", BETA, "aw login beta --only github", {"matter": "github"}),
    (2.0, "report", BETA, "beta is cloned", {}),
    (2.0, "report", MADE, "alpha is cloned", {}),
    (2.0, "report", BETA, "beta is up at 192.0.2.62", {}),
    (2.5, "waiting", MADE, "waiting for another run to leave the next VMID", {"section": "nextid-pve"}),
    (3.0, "join", ALPHA, MADE, {}),
    (3.5, "waiting", ALPHA, "", {"section": "nextid-pve"}),
    (4.0, "next", ALPHA, "aw login alpha --only github", {"matter": "github"}),
    (5.0, "stage", ALPHA, "provision", {"outcome": "done"}),
    (5.0, "stage", BETA, "provision", {"outcome": "done"}),
    (6.0, "end", ALPHA, "ready", {"outcome": "succeeded"}),
    (6.0, "end", BETA, "ready", {"outcome": "succeeded"}),
    (7.0, "end", None, "", {"outcome": "succeeded", "exit": 0}),
]
RUNS = {
    "made-and-configured": MADE_AND_CONFIGURED,
    "step-failed-before": STEP_FAILED_BEFORE,
    "stage-failed-before": STAGE_FAILED_BEFORE,
    "ended-before": ENDED_BEFORE,
    "interleaved": INTERLEAVED,
}


def _keyed(script, *, joined: bool) -> Iterator[tuple[float, jobs.Event]]:
    """script's events as a run emits them: by the stand-in until the join and by the identity after it (joined); or
    by the identity from the first event, an empty report of the run's in the join's place, which keeps nothing and is
    counted as an event, so that the two runs count their events alike."""
    for now, kind, machine, text, data in script:
        if kind == "join":
            yield now, (_event(kind, machine, text, **data) if joined else _event("report", None, ""))
        else:
            yield now, _event(kind, machine if joined or machine != MADE else ALPHA, text, **data)


def _whole(run: model.Run, renamed: dict[str, str] | None = None) -> dict[str, object]:
    """Everything the model keeps by machine, and the order of the events counted, as plain values in their order, each
    key renamed where asked: what a join moves (model.JOINED), and what a refused event must leave as it was."""

    def key(found: str | None) -> str | None:
        return (renamed or {}).get(found, found) if found is not None else None

    parts = {
        (stage, step): [
            (key(machine), part.began, part.ended, part.outcome, part.why, part.order)
            for machine, part in found.parts.items()
        ]
        for stage, progress in run.stages.items()
        for step, found in [(None, progress), *progress.steps.items()]
    }
    return {
        "machines": [key(machine) for machine in run.machines],
        "names": [(key(machine), name) for machine, name in run.names.items()],
        "parts": parts,
        "ends": [(key(machine), order) for machine, order in run._book.ends.items()],
        "reached": [(key(machine), stage) for machine, stage in run._reached.items()],
        "waits": [{**vars(wait), "machine": key(wait.machine), "data": dict(wait.data)} for wait in run.waits],
        "reports": [dataclasses.replace(report, machine=key(report.machine)) for report in run.reports],
        "failures": [dataclasses.replace(failure, machine=key(failure.machine)) for failure in run.failures],
        "outcomes": [(key(machine), end) for machine, end in run.outcomes.items()],
        "latest": [(key(machine), line) for machine, line in run.latest.items()],
        "next": [{**record, "machine": key(record["machine"])} for record in run.next_records()],
        "order": run._book.order,
        "end": (run.stopping, run.ended, run.outcome, run.exit, run.why),
    }


def _held(run: model.Run) -> dict[str, object]:
    """_whole, with each stand-in joined: what a refused event leaves as it was."""
    return {**_whole(run), "joined": dict(run._joined)}


# ---------------------------------------------------------------- the stand-in ----


def test_a_stand_in_is_of_a_form_no_identity_and_no_vms_name_takes() -> None:
    made = {jobs.stand_in() for _ in range(200)}
    assert len(made) == 200
    for key in (*made, MADE, OTHER):
        assert jobs.is_stand_in(key) and plain_text(key) == key, key
        assert not checks.HOST_KEY_ALIAS_RE.fullmatch(key), key
        assert not patterns.full("vm_name", key) and not client.VM_NAME.fullmatch(key), key
        assert not patterns.full("job_id", key), key
    for key in (ALPHA, "alpha", "making-5e1f0000000000a1", MADE + "\n", MADE.upper(), "making:5e1f", "", None, 7):
        assert not jobs.is_stand_in(key), key


# ---------------------------------------------------------------- the model ----


def test_a_run_that_makes_a_vm_reads_one_row_from_its_first_event_through_its_join() -> None:
    run = model.Run(MAKING, 0.0, {BETA: "beta"})
    for now, event in _keyed(MADE_AND_CONFIGURED, joined=True):
        run.feed(event, now)
        # One row for alpha from its first event on, said by its name; never two, never a key drawn.
        assert len(run.machines) == len(set(run.machines)) <= 2
        assert sorted(run.name(machine) for machine in run.machines) in (["beta"], ["alpha", "beta"])
    assert run.machines == [BETA, ALPHA] and run.names == {BETA: "beta", ALPHA: "alpha"}
    assert run._joined == {MADE: ALPHA} and not run._holds(MADE)
    # Its parts kept across the join: its time in provision from its first event to its end, the clone's under the
    # stand-in and the start's under the identity.
    assert run.seconds(12.0, "provision", None, ALPHA) == 7.0
    assert run.seconds(12.0, "provision", "clone", ALPHA) == 2.0
    assert run.seconds(12.0, "provision", "start", ALPHA) == 3.0
    assert run.marks("provision", "clone") == {BETA: "to come", ALPHA: "done"}
    # One outcome, at the stage its own events reached; the waits ended by the identity's clears; nothing failed.
    assert run.outcomes == {ALPHA: model.MachineEnd("succeeded", "configure", "ready", 11.0), BETA: run.outcomes[BETA]}
    assert [(wait.machine, wait.until) for wait in run.waits] == [(ALPHA, 5.0), (ALPHA, 7.0)] and run.failures == []
    assert [report.machine for report in run.reports] == [ALPHA, ALPHA]
    assert run.next_steps() == ["aw login alpha --only claude", "aw login alpha --only github"]


@pytest.mark.parametrize("script", RUNS.values(), ids=RUNS.keys())
def test_at_each_event_a_joined_run_holds_what_the_run_keyed_by_its_identity_from_its_first_event_holds(script) -> None:
    joined = model.Run(MAKING, 0.0, {BETA: "beta"})
    keyed = model.Run(MAKING, 0.0, {BETA: "beta"})
    for (now, event), (_, same) in zip(_keyed(script, joined=True), _keyed(script, joined=False), strict=True):
        joined.feed(event, now)
        keyed.feed(same, now)
        # Before the join the stand-in stands where the identity will; after it, the two runs are one.
        assert _whole(joined, {MADE: ALPHA}) == _whole(keyed), f"after {event}"
    assert _whole(joined) == _whole(keyed) and not joined._holds(MADE)


def test_the_join_keeps_a_vms_next_steps_lines_and_reports_where_they_stood_among_another_machines() -> None:
    run = model.Run(MAKING, 0.0, {BETA: "beta"})
    for now, event in _keyed(INTERLEAVED, joined=True):
        run.feed(event, now)
        if event.kind == "join":
            break
    assert [(record["machine"], record["matter"]) for record in run.next_records()] == [
        (BETA, "claude"),
        (ALPHA, "claude"),
        (BETA, "github"),
    ]
    assert [report.text for report in run.reports] == ["beta is cloned", "alpha is cloned", "beta is up at 192.0.2.62"]
    assert [report.machine for report in run.reports] == [BETA, ALPHA, BETA]
    assert [(wait.machine, wait.section, wait.until) for wait in run.waits] == [(ALPHA, "nextid-pve", None)]


@pytest.mark.parametrize("script", [STEP_FAILED_BEFORE, STAGE_FAILED_BEFORE], ids=["a-step", "a-stage"])
def test_a_failure_said_before_the_join_is_not_said_again_at_the_machines_failed_end_after_it(script) -> None:
    run = model.Run(MAKING, 0.0)
    for now, event in _keyed(script, joined=True):
        run.feed(event, now)
    why = next(data["why"] for *_, data in script if "why" in data)
    assert [(failure.stage, failure.machine, failure.why) for failure in run.failures] == [("provision", ALPHA, why)]
    assert run.outcomes[ALPHA].outcome == "failed" and run.outcomes[ALPHA].stage == "provision"
    assert run.mark(ALPHA, "provision") == "failed"


def test_a_machine_that_ended_before_its_join_keeps_its_outcome_and_reads_nothing_begun_after_its_end() -> None:
    run = model.Run(MAKING, 0.0)
    for now, event in _keyed(ENDED_BEFORE, joined=True):
        run.feed(event, now)
        if event.text == "configure" and not event.data:
            assert run.marks("configure") == {ALPHA: "to come"} and run.state("configure") == "running"
    assert run.outcomes == {ALPHA: model.MachineEnd("cancelled", "provision", "stopped before its UUID was read", 2.0)}
    assert run.mark(ALPHA, "provision") == "stopped" and run.mark(ALPHA, "configure") == "to come"


def test_a_vm_being_made_may_be_given_with_its_name_and_never_without_one() -> None:
    run = model.Run(MAKING, 0.0, {MADE: "alpha"})
    run.feed(_event("stage", MADE, "provision"), 1.0)
    run.feed(_event("join", ALPHA, MADE), 2.0)
    assert run.machines == [ALPHA] and run.name(ALPHA) == "alpha" and run.mark(ALPHA, "provision") == "running"
    for given in ([MADE], {MADE: None}, {MADE: ""}):
        with pytest.raises(ValueError, match="a machine not yet made, given with no name"):
            model.Run(MAKING, 0.0, given)


def _primed() -> model.Run:
    """A run making alpha (its stand-in named by its first event), beside beta, given by its identity, and gamma, named
    first by an event of its own."""
    run = model.Run(MAKING, 0.0, {BETA: "beta"})
    run.feed(_event("stage", MADE, "provision", name="alpha"), 1.0)
    run.feed(_event("waiting", MADE, "Approve the clone"), 1.0)
    run.feed(_event("next", MADE, "aw login alpha --only claude", matter="claude"), 1.0)
    run.feed(_event("report", GAMMA, "gamma is up", name="gamma"), 1.0)
    return run


@pytest.mark.parametrize(
    ("event", "refusal"),
    [
        # An identity the run keeps for another machine: given, or named first by an event.
        (_event("join", BETA, MADE), "keeps 'agent-warehouse-[^']*b2' for another machine"),
        (_event("join", GAMMA, MADE), "keeps 'agent-warehouse-[^']*c3' for another machine"),
        # A key that is no stand-in: a name, an identity.
        (_event("join", ALPHA, "alpha"), "'alpha' is none"),
        (_event("join", ALPHA, BETA), "is none"),
        # A stand-in the run does not know.
        (_event("join", ALPHA, OTHER), "is no machine of the run's"),
        # No identity, or a stand-in for one.
        (_event("join", None, MADE), "a join names the machine's identity"),
        (_event("join", OTHER, MADE), "a join names the machine's identity"),
        # A stand-in's first event with no name.
        (_event("stage", OTHER, "provision"), "for the first time"),
        (_event("report", OTHER, "made", name=""), "for the first time"),
        # A section's wait as its first event (runs/locks.py's section, where another run holds the section).
        (_event("waiting", OTHER, "waiting for another run to leave the next VMID", section=NEXTID), "first time"),
    ],
)
def test_a_join_or_a_stand_ins_event_refused_before_the_join_changes_nothing(event, refusal) -> None:
    run = _primed()
    before = _held(run)
    with pytest.raises(ValueError, match=refusal):
        run.feed(event, 2.0)
    assert _held(run) == before
    # The run goes on: the right join is taken after it.
    run.feed(_event("join", ALPHA, MADE), 3.0)
    assert run._joined == {MADE: ALPHA} and run.machines == [BETA, ALPHA, GAMMA]


@pytest.mark.parametrize(
    ("event", "refusal"),
    [
        # A stand-in joined twice: to another identity, and to its own again.
        (_event("join", DELTA, MADE), "joined to 'agent-warehouse-[^']*a1' already"),
        (_event("join", ALPHA, MADE), "joined to 'agent-warehouse-[^']*a1' already"),
        # Any event that names the stand-in once it is joined, even with its name.
        (_event("stage", MADE, "provision", outcome="done"), "was joined to"),
        (_event("step", MADE, "start", stage="provision", name="alpha"), "was joined to"),
        (_event("waiting", MADE, ""), "was joined to"),
        (_event("report", MADE, "alpha is up", name="alpha"), "was joined to"),
        (_event("next", MADE, "aw status", matter="claude"), "was joined to"),
        (_event("end", MADE, "ready", outcome="succeeded"), "was joined to"),
    ],
)
def test_a_join_or_an_event_of_a_stand_in_refused_after_its_join_changes_nothing(event, refusal) -> None:
    run = _primed()
    run.feed(_event("join", ALPHA, MADE), 2.0)
    before = _held(run)
    with pytest.raises(ValueError, match=refusal):
        run.feed(event, 3.0)
    assert _held(run) == before


def _out_of(found: list, key: str) -> None:
    """key's own taken out of found, a list of what is kept by machine, each with its `machine`; it was there."""
    left = [each for each in found if each.machine != key]
    assert len(left) < len(found), key
    found[:] = left


def _parts_out(run: model.Run, key: str) -> None:
    taken = [progress.parts.pop(key) for progress in run._progresses() if key in progress.parts]
    assert taken, key


def _next_out(run: model.Run, key: str) -> None:
    taken = [run._next.pop(found) for found in list(run._next) if found[0] == key]
    assert taken, key


# How a test takes a key out of each of what a join moves (model.JOINED), that one alone; each fails where the run did
# not keep the key there, so the run below is held to keep it in every one.
TAKEN_OUT = {
    "machines": lambda run, key: run.machines.remove(key),
    "names": lambda run, key: run.names.pop(key),
    "parts": _parts_out,
    "ends": lambda run, key: run._book.ends.pop(key),
    "reached": lambda run, key: run._reached.pop(key),
    "waits": lambda run, key: _out_of(run.waits, key),
    "reports": lambda run, key: _out_of(run.reports, key),
    "failures": lambda run, key: _out_of(run.failures, key),
    "outcomes": lambda run, key: run.outcomes.pop(key),
    "latest": lambda run, key: run.latest.pop(key),
    "next": _next_out,
}


def _keeping_delta() -> model.Run:
    """A run making alpha, beside delta, which it keeps in every one of what a join moves: its row and name, its parts,
    the stage it reached, a wait, a report, a failure, its latest line, a NEXT STEPS line and its end."""
    run = model.Run(MAKING, 0.0)
    run.feed(_event("stage", DELTA, "provision", name="delta"), 1.0)
    run.feed(_event("step", DELTA, "clone", stage="provision"), 1.0)
    run.feed(_event("step", DELTA, "Cloning from the template"), 1.5)
    run.feed(_event("waiting", DELTA, "Approve the clone"), 1.5)
    run.feed(_event("report", DELTA, "delta is cloned"), 1.5)
    run.feed(_event("next", DELTA, "aw login delta --only claude", matter="claude"), 1.5)
    run.feed(_event("step", DELTA, "clone", stage="provision", outcome="failed", why="the clone's task failed"), 2.0)
    run.feed(_event("end", DELTA, "the clone's task failed", outcome="failed"), 2.0)
    run.feed(_event("stage", MADE, "provision", name="alpha"), 3.0)
    return run


def _kept_in(run: model.Run, kept: str | None) -> model.Run:
    """run with delta taken out of every one of what a join moves but kept (None: all of them)."""
    for name, take in TAKEN_OUT.items():
        if name != kept:
            take(run, DELTA)
    return run


def test_a_test_takes_a_key_out_of_each_of_what_a_join_moves_and_out_of_them_all_the_run_keeps_nothing_of_it() -> None:
    assert set(TAKEN_OUT) == set(model.JOINED)
    run = _kept_in(_keeping_delta(), None)
    assert not run._holds(DELTA)
    # Then delta may be alpha's identity: nothing of the run's would be two machines' (the join is taken).
    run.feed(_event("join", DELTA, MADE), 4.0)
    assert run._joined == {MADE: DELTA} and run.name(DELTA) == "alpha"


@pytest.mark.parametrize("kept", model.JOINED)
def test_a_join_to_an_identity_the_run_keeps_anything_for_is_refused_whichever_of_what_a_join_moves_keeps_it(
    kept,
) -> None:
    run = _kept_in(_keeping_delta(), kept)
    assert run._holds(DELTA), kept
    before = _held(run)
    with pytest.raises(ValueError, match="for another machine"):
        run.feed(_event("join", DELTA, MADE), 4.0)
    assert _held(run) == before


# ---------------------------------------------------------------- the job's record ----


def _create(machines: list[str]) -> jobs.Job:
    return jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "aw deploy alpha beta", "pve", machines, {"kind": "terminal"}, True
    )


@contextlib.contextmanager
def _job(machines: tuple[str, ...] = (BETA,)) -> Iterator[jobs.Job]:
    """A job of this process's own, in the test's fleet home, dropped at the end."""
    made = _create(list(machines))
    try:
        yield made
    finally:
        jobs.drop(made)


def _read_back(made: jobs.Job) -> dict:
    """The record as another reader finds it, by the job's ID."""
    found = jobs.find(made.folder.parent, made.id)
    assert found is not None
    return found.read()


def test_the_record_keeps_a_vm_not_yet_made_in_making_and_its_join_moves_it_to_its_identity(home) -> None:
    with _job() as made:
        heard: list[jobs.Event] = []
        made.listen(heard.append)
        assert [_read_back(made)[field] for field in ("machines", "making", "names")] == [[BETA], {}, {}]
        made.emit("stage", MADE, "provision", name="alpha")
        assert [_read_back(made)[field] for field in ("machines", "making", "names")] == [[BETA], {MADE: "alpha"}, {}]
        made.emit("next", BETA, "aw login beta --only claude", matter="claude")
        made.emit("next", MADE, "aw login alpha --only claude", matter="claude")
        made.emit("next", MADE, "aw login alpha --only github", matter="github")
        made.emit("end", BETA, "ready", outcome="succeeded")
        made.emit("waiting", MADE, "Approve the clone")
        before = _read_back(made)
        assert before["waiting"]["machine"] == MADE
        assert [step["machine"] for step in before["next"]] == [BETA, MADE, MADE]
        made.emit("join", ALPHA, MADE)
        after = _read_back(made)
        assert after["machines"] == [BETA, ALPHA] and after["making"] == {}
        # The VM's name, which `making` kept, kept under its identity: a reader after the join says it by its name.
        assert after["names"] == {ALPHA: "alpha"}
        assert after["waiting"] == {**before["waiting"], "machine": ALPHA}
        assert after["next"] == [
            {"machine": BETA, "matter": "claude", "line": "aw login beta --only claude"},
            {"machine": ALPHA, "matter": "claude", "line": "aw login alpha --only claude"},
            {"machine": ALPHA, "matter": "github", "line": "aw login alpha --only github"},
        ]
        # The identity's events go on in the record: a line removed, its wait cleared, its end at the stage its
        # stand-in's events reached.
        made.emit("next", ALPHA, "", matter="claude")
        made.emit("waiting", ALPHA, "")
        made.emit("end", ALPHA, "ready", outcome="succeeded")
        ended = _read_back(made)
        assert [step["matter"] for step in ended["next"] if step["machine"] == ALPHA] == ["github"]
        assert ended["waiting"] is None and list(ended["machine_outcomes"]) == [BETA, ALPHA]
        assert ended["machine_outcomes"][ALPHA] == {"outcome": "succeeded", "stage": "provision", "why": "ready"}
        assert ended["names"] == {ALPHA: "alpha"} and MADE not in repr(ended)
        assert [event.kind for event in heard].count("join") == 1


def test_a_vm_that_ended_before_its_join_moves_its_outcome_and_one_never_joined_stays_under_its_stand_in(home) -> None:
    with _job(machines=()) as made:
        made.emit("stage", MADE, "provision", name="alpha")
        made.emit("stage", OTHER, "provision", name="gamma")
        made.emit("end", MADE, "stopped", outcome="cancelled")
        made.emit("end", OTHER, "no VMID was free", outcome="failed")
        made.emit("join", ALPHA, MADE)
        record = _read_back(made)
        assert record["machines"] == [ALPHA] and record["making"] == {OTHER: "gamma"}
        assert record["names"] == {ALPHA: "alpha"}
        assert list(record["machine_outcomes"].items()) == [
            (ALPHA, {"outcome": "cancelled", "stage": "provision", "why": "stopped"}),
            (OTHER, {"outcome": "failed", "stage": "provision", "why": "no VMID was free"}),
        ]


def _kept_by_machine(record: dict, renamed: dict[str, str]) -> dict[str, object]:
    """What the record keeps by machine, in its order, each key renamed where asked; a wait's time, the wall clock's,
    left out."""

    def key(found: object) -> object:
        return renamed.get(found, found) if isinstance(found, str) else found

    waiting = record["waiting"]
    return {
        "waiting": None if waiting is None else {**waiting, "machine": key(waiting["machine"]), "since": None},
        "next": [{**step, "machine": key(step["machine"])} for step in record["next"]],
        "machine_outcomes": [(key(machine), ended) for machine, ended in record["machine_outcomes"].items()],
        "end": [record.get(field) for field in ("outcome", "exit", "why")],
    }


def _recorded(script, *, joined: bool) -> tuple[list[dict[str, object]], dict]:
    """script emitted through a job's emit: after each event, what the record keeps by machine, by the stand-in's key
    read as the identity's until the join (never after it); and the record at the end."""
    seen = []
    with _job() as made:
        renamed = {MADE: ALPHA}
        for _, event in _keyed(script, joined=joined):
            made.emit(event.kind, event.machine, event.text, **event.data)
            if event.kind == "join":
                renamed = {}
            seen.append(_kept_by_machine(_read_back(made), renamed))
        return seen, _read_back(made)


@pytest.mark.parametrize("script", RUNS.values(), ids=RUNS.keys())
def test_at_each_event_a_joined_runs_record_keeps_what_the_record_of_the_run_keyed_by_its_identity_keeps(
    script, home
) -> None:
    joined, last = _recorded(script, joined=True)
    keyed, same = _recorded(script, joined=False)
    for at, (found, expected) in enumerate(zip(joined, keyed, strict=True)):
        assert found == expected, f"after {script[at]}"
    # But for the machines a join names and the names it keeps: the identity among the job's machines, its name kept.
    assert (last["machines"], last["making"], last["names"]) == ([BETA, ALPHA], {}, {ALPHA: "alpha"})
    assert (same["machines"], same["making"], same["names"]) == ([BETA], {}, {})
    assert MADE not in repr(last)


def test_a_job_names_its_machines_by_identity_never_by_a_stand_in(home) -> None:
    with pytest.raises(ValueError, match="a job's machines are identities"):
        _create([BETA, MADE])
    assert not any(paths.jobs_dir(paths.home(REPO)).glob("*/job.json"))


BEFORE_THE_JOIN = [
    ("stage", OTHER, "provision", {}, "for the first time"),
    # A section's wait as its first event, as runs/locks.py's section emits it where another run holds the section.
    ("waiting", OTHER, "waiting for another run to leave the next VMID", {"section": NEXTID}, "for the first time"),
    ("stage", OTHER, "provision", {"name": ""}, "for the first time"),
    ("join", BETA, MADE, {}, "has named already"),
    ("join", GAMMA, MADE, {}, "has named already"),
    ("join", ALPHA, "alpha", {}, "'alpha' is none"),
    ("join", ALPHA, OTHER, {}, "is no machine this run is making"),
    ("join", None, MADE, {}, "a join names the machine's identity"),
    ("join", OTHER, MADE, {}, "a join names the machine's identity"),
]
AFTER_THE_JOIN = [
    ("join", DELTA, MADE, {}, "already: a stand-in is joined once"),
    ("stage", MADE, "provision", {"outcome": "done"}, "was joined to"),
    ("waiting", MADE, "", {}, "was joined to"),
    ("next", MADE, "aw status", {"matter": "claude"}, "was joined to"),
    ("end", MADE, "ready", {"outcome": "succeeded"}, "was joined to"),
]


@pytest.mark.parametrize(
    ("joined", "kind", "machine", "text", "data", "refusal"),
    [(False, *case) for case in BEFORE_THE_JOIN] + [(True, *case) for case in AFTER_THE_JOIN],
)
def test_a_refused_event_of_a_vm_being_made_writes_nothing_and_nobody_hears_it(
    joined, kind, machine, text, data, refusal, home
) -> None:
    with _job() as made:
        made.emit("stage", MADE, "provision", name="alpha")
        made.emit("waiting", MADE, "Approve the clone")
        made.emit("report", GAMMA, "gamma is up", name="gamma")
        if joined:
            made.emit("join", ALPHA, MADE)
        heard: list[jobs.Event] = []
        made.listen(heard.append)
        before = made.record.read_bytes()
        with pytest.raises(ValueError, match=refusal):
            made.emit(kind, machine, text, **data)
        assert made.record.read_bytes() == before and heard == []
        # The run goes on: what it holds of its machines is as it was.
        if not joined:
            made.emit("join", ALPHA, MADE)
        made.emit("end", ALPHA, "ready", outcome="succeeded")
        record = _read_back(made)
        assert record["machines"] == [BETA, ALPHA] and record["making"] == {}
        assert record["machine_outcomes"][ALPHA]["stage"] == "provision"


# A run in a child process that is making alpha: its first event and a wait under the stand-in, then it says its job's
# ID and waits on its standard input, killed there.
CHILD = """
import sys
sys.path.insert(0, {lib!r})
from pathlib import Path
from claude_vm.runs import job as jobs

made = jobs.create(Path(sys.argv[1]), "deploy", "aw deploy alpha beta", "pve", [{beta!r}], {{"kind": "terminal"}}, True)
made.emit("stage", {made!r}, "provision", name="alpha")
made.emit("waiting", {made!r}, "waiting for another run to leave the next VMID", section="nextid-pve")
print(made.id, flush=True)
sys.stdin.readline()
"""


def test_a_run_killed_before_its_join_leaves_a_record_that_says_what_it_was_making(home) -> None:
    jobs_dir = paths.jobs_dir(paths.home(REPO))
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", CHILD.format(lib=str(LIB), beta=BETA, made=MADE), str(jobs_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment(home),
        preexec_fn=harness._default_signals,
    )
    try:
        assert process.stdout is not None
        if not select.select([process.stdout], [], [], 60)[0]:
            pytest.fail("the run made no job within a minute")
        said = process.stdout.readline().split()
        if not said:
            pytest.fail(f"the run made no job: {process.communicate(timeout=30)[1]}")
        found = jobs.find(jobs_dir, said[0])
        assert found is not None and jobs.state(found) == "running"
        # While the run waits on its stdin: nothing forks.
        process.send_signal(signal.SIGKILL)
        assert process.wait(timeout=60) == -signal.SIGKILL
    finally:
        if process.poll() is None:
            harness._end(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    record = found.read()
    assert jobs.state(found) == "lost"
    assert record["machines"] == [BETA] and record["making"] == {MADE: "alpha"} and record["names"] == {}
    assert (record["waiting"]["machine"], record["waiting"]["section"]) == (MADE, "nextid-pve")


# ---------------------------------------------------------------- the live view ----


class Clock:
    """The view's clock, set by the test."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _off_a_terminal(monkeypatch: pytest.MonkeyPatch, script, *, joined: bool) -> tuple[str, str]:
    """script said by a live view off a terminal, through a job of this process's, each event at its time on the view's
    clock: what it said on its stream, the clock times it read from the wall made one, and on standard error."""
    stream, errors = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)
    clock = Clock()
    with _job() as made:
        live.Live(made, MAKING, stream, names={BETA: "beta"}, clock=clock, title="deploy alpha beta")
        for now, event in _keyed(script, joined=joined):
            clock.now = now
            made.emit(event.kind, event.machine, event.text, **event.data)
    return re.sub(r"\b\d\d:\d\d\b", "HH:MM", stream.getvalue()), errors.getvalue()


@pytest.mark.parametrize("script", RUNS.values(), ids=RUNS.keys())
def test_off_a_terminal_a_vm_being_made_is_said_by_its_name_and_its_join_changes_nothing_said(
    script, home, monkeypatch
) -> None:
    said, errors = _off_a_terminal(monkeypatch, script, joined=True)
    keyed, _ = _off_a_terminal(monkeypatch, script, joined=False)
    assert errors == "" and said == keyed
    assert MADE not in said and ALPHA not in said and "alpha" in said
    # Nothing drawn twice: the run's two machines, one of them alpha.
    assert "2 machines" in said and "3 machines" not in said


def test_the_frame_is_the_same_just_before_and_just_after_the_join_with_one_row_for_the_vm() -> None:
    run = model.Run(MAKING, 0.0, {BETA: "beta"})
    view = View(run, started_at=0.0, title="deploy alpha beta")
    seen = []
    for now, event in _keyed(MADE_AND_CONFIGURED, joined=True):
        if event.kind == "join":
            before = view.frame(now, 100, 40, "*", False)
            run.feed(event, now)
            assert view.frame(now, 100, 40, "*", False) == before
            assert view.above(100, False) == [] and view.ended_lines(now) == []
            seen = before
        else:
            run.feed(event, now)
            view.above(100, False)
            view.ended_lines(now)
        frame = view.frame(now, 100, 40, "*", False)
        assert not any(MADE in row or ALPHA in row for row in frame), frame
        for row in frame:
            assert row.count("alpha ") <= 1, row
    # At the join, alpha's mark beside beta's, its latest line of work and its wait for another run, under its name.
    assert any("beta ✓ · alpha *" in row for row in seen), seen
    assert any("Cloning from the template" in row and "alpha" in row for row in seen), seen
    assert any("waiting for another run to leave the template" in row and "alpha" in row for row in seen), seen


def test_a_view_made_after_a_vm_being_made_was_first_named_draws_it_by_its_name_from_the_record(
    home, monkeypatch
) -> None:
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)
    with _job() as made:
        made.emit("stage", MADE, "provision", name="alpha")
        view = live.Live(made, MAKING, io.StringIO(), names={BETA: "beta"}, clock=Clock())
        assert view.run.machines == [BETA, MADE] and view.run.name(MADE) == "alpha"
        # Its later events need not say its name again: the view's model has it.
        made.emit("step", MADE, "clone", stage="provision")
        made.emit("join", ALPHA, MADE)
        made.emit("step", ALPHA, "clone", stage="provision", outcome="done")
        assert view.run.machines == [BETA, ALPHA] and view.run.name(ALPHA) == "alpha"
        assert view.run.mark(ALPHA, "provision", "clone") == "done"
    assert errors.getvalue() == ""


def test_a_view_made_after_the_join_draws_the_vm_by_the_name_the_record_keeps_under_its_identity(home) -> None:
    with _job() as made:
        made.emit("stage", MADE, "provision", name="alpha")
        made.emit("join", ALPHA, MADE)
        view = live.Live(made, MAKING, io.StringIO(), names={BETA: "beta"}, clock=Clock())
        assert view.run.machines == [BETA, ALPHA] and view.run.names == {BETA: "beta", ALPHA: "alpha"}
        made.emit("end", None, "", outcome="succeeded", exit=0)


def _said_through_a_join(monkeypatch: pytest.MonkeyPatch, events, *, verbosity: int) -> tuple[str, str]:
    """events emitted through a job of this process's that a live view says off a terminal: what it said on its stream
    and on standard error."""
    stream, errors = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)
    with _job() as made:
        live.Live(made, MAKING, stream, verbosity=verbosity, names={BETA: "beta"}, clock=Clock())
        for kind, machine, text, data in events:
            made.emit(kind, machine, text, **data)
    return stream.getvalue(), errors.getvalue()


# A step of alpha's its stage does not declare, before its join and after it: emit takes each, the model refuses each.
UNDECLARED = [
    ("stage", MADE, "provision", {"name": "alpha"}),
    ("step", MADE, "resize", {"stage": "provision"}),
    ("join", ALPHA, MADE, {}),
    ("step", ALPHA, "resize", {"stage": "provision"}),
    ("end", ALPHA, "ready", {"outcome": "succeeded"}),
    ("end", None, "", {"outcome": "succeeded", "exit": 0}),
]


@pytest.mark.parametrize("verbosity", [0, 1], ids=["lines", "v"])
def test_the_line_of_an_event_the_view_left_out_says_the_vm_by_its_name_before_its_join_and_after_it(
    verbosity, home, monkeypatch
) -> None:
    _, errors = _said_through_a_join(monkeypatch, UNDECLARED, verbosity=verbosity)
    refused = errors.splitlines()
    assert len(refused) == 2, errors
    for line in refused:
        assert line.startswith("aw: the live view left out an event it could not take (step alpha: resize): "), line
    assert MADE not in errors and ALPHA not in errors


def test_with_a_v_each_line_through_a_join_says_the_vm_by_its_name_alone(home, monkeypatch) -> None:
    events = [
        ("stage", BETA, "provision", {}),
        ("stage", MADE, "provision", {"name": "alpha"}),
        ("step", MADE, "clone", {"stage": "provision"}),
        ("join", ALPHA, MADE, {}),
        ("step", ALPHA, "clone", {"stage": "provision", "outcome": "done"}),
        ("end", ALPHA, "ready", {"outcome": "succeeded"}),
        ("end", BETA, "ready", {"outcome": "succeeded"}),
        ("end", None, "", {"outcome": "succeeded", "exit": 0}),
    ]
    said, errors = _said_through_a_join(monkeypatch, events, verbosity=1)
    assert said.splitlines()[: len(events)] == [
        "stage beta: provision",
        "stage alpha: provision · name=alpha",
        "step alpha: clone · stage=provision",
        "join alpha",
        "step alpha: clone · stage=provision · outcome=done",
        "end alpha: ready · outcome=succeeded",
        "end beta: ready · outcome=succeeded",
        "end · outcome=succeeded · exit=0",
    ]
    assert errors == "" and MADE not in said and ALPHA not in said and BETA not in said


def test_with_a_v_the_join_is_one_line_of_its_own_and_never_says_the_stand_in() -> None:
    assert live.streamed(_event("join", ALPHA, MADE), "alpha") == "join alpha"
    # With no name known, the key the event names: the identity, never the stand-in its text is.
    assert live.streamed(_event("join", ALPHA, MADE)) == f"join {ALPHA}"
