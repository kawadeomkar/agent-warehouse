"""A run over several machines: side by side under a bound, each machine with its own events and outcome
(lib/claude_vm/runs/fleet_run.py; docs/architecture/engine.md §16.8).

In this process, each run on a job of the test's own, made in its fleet home, with the run's cancel flag the test's:
thirty machines never more than twenty at once (the setting run_forks's default, as today's forks) and every free turn
taken, and a bound given as five holds five; one that fails leaves twenty-nine done, none of them cut short, and the
run failed; once the run is cancelled no machine begins and those under way finish, and with the run cancelled before
it, none begins; the runner cancelled (the stop's grace ended) cancels every machine under way and waits for each
before it raises, nothing left on the loop, and so does the runner's own record refused, the error raised as itself;
the exit record holds thirty outcomes, each with the stage its own last stage event named; a stand-in's end kept under
its identity once joined, and by its name where it failed at once or never began; one at a time never two at once; and
a bound under one, a machine named twice and a stand-in with no name refused before anything begins. In a child
process made by runs.run as a verb makes one: a real Ctrl-C while twenty machines are under way lets none of the other
ten begin, and the run's record is cancelled with thirty outcomes.

Every wait here is held to an event of the run's, never to a sleep: a wave of machines is let go only once as many as
the bound lets in are under way, which is only seen once every task that was ready has had its turn, so that one let
in past the bound is counted; a hang is failed by a bound of a minute that no run under load comes near.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import harness
import pytest
from aw_kit import environment
from harness import REPO
from test_processes import _until

from claude_vm.fleet import paths, registry
from claude_vm.runs import fleet_run
from claude_vm.runs import job as jobs
from claude_vm.runs.fleet_run import NOT_BEGUN, Ended

LIB = REPO / "lib"
THIRTY = [f"m{number:02}" for number in range(30)]
# A hang is failed, never waited out: no run here under load comes near it.
HANG = 60
# A machine's identity, the key its every event names it by once its stand-in is joined.
IDENTITY = "agent-warehouse-00000000-0000-4000-8000-000000000001"


@pytest.fixture
def own(home: Path) -> Iterator[jobs.Job]:
    """A job of this process's own, in the test's fleet home, dropped at the end."""
    made = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "aw deploy", "pve", THIRTY, {"kind": "terminal"}, True
    )
    try:
        yield made
    finally:
        jobs.drop(made)


def _context(job: jobs.Job) -> SimpleNamespace:
    """What fleet_run asks of a run's Context: its job, its emit and its cancel flag. Made on the loop it is used on."""
    return SimpleNamespace(job=job, emit=job.emit, cancelled=asyncio.Event())


def _outcomes(job: jobs.Job) -> dict[str, Ended]:
    """The machines' outcomes as the job's record keeps them."""
    return {key: Ended(**ended) for key, ended in (job.read().get("machine_outcomes") or {}).items()}


class Waves:
    """Bodies let go a wave at a time: each wave once as many machines are under way as the bound lets in, or as are
    left. The test sees a wave full only after every task that was ready when its last machine came in has had its
    turn, so that a machine the runner let in past the bound is under way by then, and counted."""

    def __init__(self, most: int, total: int) -> None:
        self.most, self.total = most, total
        self.under_way = self.begun = self.most_seen = 0
        self.full = asyncio.Event()
        self.go = asyncio.Event()
        self.waves: list[int] = []

    def _expected(self) -> int:
        return min(self.most, self.total - sum(self.waves))

    async def body(self, machine: str) -> str:
        self.under_way += 1
        self.begun += 1
        self.most_seen = max(self.most_seen, self.under_way)
        if self.under_way == self._expected():
            self.full.set()
        go = self.go
        await go.wait()
        self.under_way -= 1
        return f"{machine} done"

    async def let_go(self) -> None:
        while sum(self.waves) < self.total:
            await self.full.wait()
            self.full.clear()
            self.waves.append(self.under_way)
            go, self.go = self.go, asyncio.Event()
            go.set()


def _waves(job: jobs.Job, most: int, machines: list[str], *, one_at_a_time: bool = False):
    async def scenario():
        ctx = _context(job)
        waves = Waves(most, len(machines))
        letting = asyncio.ensure_future(waves.let_go())
        if one_at_a_time:
            running = fleet_run.one_at_a_time(ctx, machines, waves.body)  # type: ignore[arg-type]
        else:
            running = fleet_run.each(ctx, machines, waves.body, most)  # type: ignore[arg-type]
        ended = await asyncio.wait_for(running, HANG)
        await asyncio.wait_for(letting, HANG)
        return waves, ended

    return asyncio.run(scenario())


# ---------------------------------------------------------------- the bound ----


@pytest.mark.parametrize("most", [registry.row("run_forks").default, 5])
def test_thirty_machines_are_never_more_than_the_bound_at_once_and_every_free_turn_is_taken(own, most) -> None:
    assert registry.row("run_forks").default == 20
    waves, ended = _waves(own, most, THIRTY)
    assert waves.most_seen == most
    # Each wave as full as the bound and what is left let it be: a turn left free would hang the run, failed by HANG.
    assert waves.waves == [most] * (30 // most) + ([30 % most] if 30 % most else [])
    assert waves.begun == 30
    assert list(ended) == THIRTY
    assert ended == {machine: Ended("succeeded", None, f"{machine} done") for machine in THIRTY}
    assert fleet_run.status(ended) == 0


def test_one_at_a_time_is_never_two_at_once(own) -> None:
    waves, ended = _waves(own, 1, THIRTY[:5], one_at_a_time=True)
    assert (waves.most_seen, waves.waves) == (1, [1] * 5)
    assert list(ended) == THIRTY[:5]


# ---------------------------------------------------------------- a failure, and the exit record ----


def test_one_that_fails_leaves_twenty_nine_done_none_cut_short_and_the_run_failed(own) -> None:
    failing = "m07"
    finished: list[str] = []

    async def scenario():
        ctx = _context(own)
        failed = asyncio.Event()
        own.listen(lambda event: event.kind == "end" and event.machine == failing and failed.set())

        async def body(machine: str) -> str:
            ctx.emit("stage", machine, "provision")
            if machine == failing:
                raise RuntimeError(f"{machine}: the clone failed")
            # Every machine under way when the other failed goes on past its failure, to its own end.
            await failed.wait()
            ctx.emit("stage", machine, "configure")
            finished.append(machine)
            return f"{machine} configured"

        return await asyncio.wait_for(fleet_run.each(ctx, THIRTY, body, 20), HANG)  # type: ignore[arg-type]

    ended = asyncio.run(scenario())
    expected = {
        machine: (
            Ended("failed", "provision", f"{machine}: the clone failed")
            if machine == failing
            else Ended("succeeded", "configure", f"{machine} configured")
        )
        for machine in THIRTY
    }
    assert ended == expected
    assert sorted(finished) == [machine for machine in THIRTY if machine != failing]
    assert fleet_run.status(ended) == 1
    # The exit record: thirty outcomes, each with the stage the machine's own last stage event named.
    assert _outcomes(own) == expected


# ---------------------------------------------------------------- a cancel ----


def test_once_the_run_is_cancelled_no_machine_begins_and_those_under_way_finish(own) -> None:
    begun: list[str] = []

    async def scenario():
        ctx = _context(own)
        full = asyncio.Event()

        async def body(machine: str) -> str:
            begun.append(machine)
            if len(begun) == 20:
                full.set()
            await ctx.cancelled.wait()
            return "finished"

        running = asyncio.ensure_future(fleet_run.each(ctx, THIRTY, body, 20))  # type: ignore[arg-type]
        await asyncio.wait_for(full.wait(), HANG)
        ctx.cancelled.set()
        return await asyncio.wait_for(running, HANG)

    ended = asyncio.run(scenario())
    assert begun == THIRTY[:20]
    expected = {
        **{machine: Ended("succeeded", None, "finished") for machine in THIRTY[:20]},
        **{machine: Ended("cancelled", None, NOT_BEGUN) for machine in THIRTY[20:]},
    }
    assert ended == expected and list(ended) == THIRTY
    assert _outcomes(own) == expected
    assert fleet_run.status(ended) == 130


def test_a_run_cancelled_before_it_begins_no_machine(own) -> None:
    async def scenario():
        ctx = _context(own)
        ctx.cancelled.set()

        async def body(machine: str) -> None:
            pytest.fail(f"{machine} began once the run was cancelled")

        return await fleet_run.each(ctx, THIRTY[:3], body, 20)  # type: ignore[arg-type]

    assert asyncio.run(scenario()) == {machine: Ended("cancelled", None, NOT_BEGUN) for machine in THIRTY[:3]}


def test_the_runner_cancelled_cancels_every_machine_under_way_and_waits_for_each_before_it_raises(own) -> None:
    # The stop's grace ended: the ladder cancels the body, and so the runner. Nothing of the run is left on the loop,
    # where a question would run on past the loop's end and say a traceback after the summary (C11.md L5).
    closed: list[str] = []

    async def scenario():
        ctx = _context(own)
        full = asyncio.Event()
        never = asyncio.Event()
        begun: list[str] = []

        async def body(machine: str) -> None:
            begun.append(machine)
            if len(begun) == 20:
                full.set()
            try:
                await never.wait()
            finally:
                closed.append(machine)

        running = asyncio.ensure_future(fleet_run.each(ctx, THIRTY, body, 20))  # type: ignore[arg-type]
        await asyncio.wait_for(full.wait(), HANG)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        # Already, with no turn of the loop's since: every machine under way ended, and no task but this one left.
        assert sorted(closed) == THIRTY[:20]
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(scenario())
    expected = {
        **{machine: Ended("cancelled", None, fleet_run.STOPPED) for machine in THIRTY[:20]},
        **{machine: Ended("cancelled", None, NOT_BEGUN) for machine in THIRTY[20:]},
    }
    assert _outcomes(own) == expected


def test_the_runner_s_own_record_refused_cancels_every_machine_and_waits_for_each_before_it_raises_it(own) -> None:
    # A machine's end the record refuses (the job dropped under the run) is the runner's error, not the machine's: every
    # other machine under way is cancelled and waited for, and the error raised as itself, never a group of errors.
    closed: list[str] = []
    refused = jobs.Gone("the job was dropped")

    async def scenario():
        def emit(kind: str, machine: str | None, text: str, **data: object) -> jobs.Event:
            if kind == "end" and machine == "m19":
                raise refused
            return own.emit(kind, machine, text, **data)

        ctx = SimpleNamespace(job=own, emit=emit, cancelled=asyncio.Event())
        never = asyncio.Event()
        begun: list[str] = []

        async def body(machine: str) -> None:
            begun.append(machine)
            # The twentieth to begin ends at once, every other under way by then.
            if len(begun) == 20:
                return None
            try:
                await never.wait()
            finally:
                closed.append(machine)

        with pytest.raises(jobs.Gone) as raised:
            await asyncio.wait_for(fleet_run.each(ctx, THIRTY, body, 20), HANG)  # type: ignore[arg-type]
        assert raised.value is refused
        # Already, with no turn of the loop's since: every machine under way ended, and no task but this one left.
        assert sorted(closed) == THIRTY[:19]
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(scenario())
    assert _outcomes(own) == {
        **{machine: Ended("cancelled", None, fleet_run.STOPPED) for machine in THIRTY[:19]},
        **{machine: Ended("cancelled", None, NOT_BEGUN) for machine in THIRTY[20:]},
    }


# ---------------------------------------------------------------- machines not yet made ----


def test_a_stand_in_s_end_is_kept_under_its_identity_once_joined_and_by_its_name_where_it_never_began(own) -> None:
    joined, failing, left = jobs.stand_in(), jobs.stand_in(), jobs.stand_in()
    names = {joined: "alpha", failing: "beta", left: "gamma"}

    async def scenario():
        ctx = _context(own)

        async def body(machine: str) -> str:
            if machine == failing:
                # It fails before its first event: its end says its name. And the run is stopped meanwhile.
                ctx.cancelled.set()
                raise RuntimeError("beta: no free VMID")
            ctx.emit("stage", machine, "provision", name=names[machine])
            ctx.emit("join", IDENTITY, machine)
            ctx.emit("stage", IDENTITY, "configure")
            return "alpha made"

        machines = [joined, failing, left]
        return await fleet_run.one_at_a_time(ctx, machines, body, names=names)  # type: ignore[arg-type]

    ended = asyncio.run(scenario())
    expected = {
        IDENTITY: Ended("succeeded", "configure", "alpha made"),
        failing: Ended("failed", None, "beta: no free VMID"),
        left: Ended("cancelled", None, NOT_BEGUN),
    }
    assert ended == expected and list(ended) == [IDENTITY, failing, left]
    record = own.read()
    assert {key: Ended(**value) for key, value in record["machine_outcomes"].items()} == expected
    assert record["making"] == {failing: "beta", left: "gamma"}
    assert IDENTITY in record["machines"]


# ---------------------------------------------------------------- refused before anything begins ----


@pytest.mark.parametrize(
    ("machines", "most", "names", "refused"),
    [
        (["a", "b"], 0, None, "at least one at a time"),
        (["a", "b"], True, None, "at least one at a time"),
        (["a", "b"], 2.0, None, "at least one at a time"),
        (["a", "b", "a"], 2, None, "'a' is named twice"),
        ([jobs.stand_in()], 2, None, "its name is given"),
        ([jobs.stand_in()], 2, {"other": "alpha"}, "its name is given"),
    ],
)
def test_what_is_refused_before_anything_begins(own, machines, most, names, refused) -> None:
    async def scenario() -> None:
        async def body(machine: str) -> None:
            pytest.fail(f"{machine} began")

        with pytest.raises(ValueError, match=refused):
            await fleet_run.each(_context(own), machines, body, most, names=names)  # type: ignore[arg-type]

    asyncio.run(scenario())
    assert not own.read().get("machine_outcomes")


def test_a_string_is_no_list_of_machines(own) -> None:
    async def scenario() -> None:
        with pytest.raises(TypeError, match="never one string"):
            await fleet_run.each(_context(own), "alpha", lambda machine: None, 2)  # type: ignore[arg-type]

    asyncio.run(scenario())


# ---------------------------------------------------------------- at a real Ctrl-C ----

# A verb's run made by runs.run: thirty machines, twenty at once, each waiting for the run's stop; the twentieth to
# begin says so in the test's folder, and the machines begun are written there once the runner has returned.
RUN = r"""
import json, sys
from pathlib import Path
lib, folder, jobs_dir = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, lib)
from claude_vm import runs
from claude_vm.runs import fleet_run

MACHINES = [f"m{number:02}" for number in range(30)]


async def body(ctx):
    begun = []

    async def machine(key):
        begun.append(key)
        if len(begun) == 20:
            (folder / "full").write_text("")
        await ctx.cancelled.wait()
        return "finished"

    ended = await fleet_run.each(ctx, MACHINES, machine, 20)
    (folder / "begun.json").write_text(json.dumps(begun))
    return fleet_run.status(ended)


draft = runs.Draft(jobs_dir, "deploy", "aw deploy", "pve", MACHINES, {"kind": "terminal"}, True, values={})
sys.exit(runs.run(draft, body))
"""


def test_a_ctrl_c_lets_no_other_machine_begin_and_the_record_holds_thirty_outcomes(home, tmp_path) -> None:
    jobs_dir = paths.jobs_dir(paths.home(REPO))
    with open(tmp_path / "err", "wb") as err:
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", RUN, str(LIB), str(tmp_path), str(jobs_dir)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err,
            env=environment(home),
            preexec_fn=harness._default_signals,  # noqa: PLW1509 - only signal, safe beside xdist's thread
        )
    try:
        _until(lambda: (tmp_path / "full").exists() or process.poll() is not None, "twenty machines under way", HANG)
        assert process.poll() is None, (tmp_path / "err").read_text()
        # Sent while every machine waits on the run's stop and nothing forks.
        os.kill(process.pid, signal.SIGINT)
        assert process.wait(timeout=HANG) == 130, (tmp_path / "err").read_text()
    finally:
        if process.poll() is None:
            harness._end(process)
    assert json.loads((tmp_path / "begun.json").read_text()) == THIRTY[:20]
    (made,) = jobs.listed(jobs_dir)
    record = made.read()
    assert (record["outcome"], record["why"], record["exit"]) == ("cancelled", "Ctrl-C", 130)
    assert record["machine_outcomes"] == {
        **{machine: {"outcome": "succeeded", "stage": None, "why": "finished"} for machine in THIRTY[:20]},
        **{machine: {"outcome": "cancelled", "stage": None, "why": NOT_BEGUN} for machine in THIRTY[20:]},
    }
