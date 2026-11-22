"""The locks each verb's run takes (lib/claude_vm/runs/lock_sets.py, and runs/__init__.py's run, which hands its kind
to runs/locks.py's take; docs/architecture/engine.md §16.8).

Every verb of the table takes the kind its column gives, or for deploy, host and vault the kind its words come to, as
the dispatch hands them: a table of each verb, and of each verb's words that matter. Every row whose column is words
has its function here, and every function a row, so that a verb added later without one fails; a function that comes
to no kind is refused. deploy --only template takes the whole fleet and is refused while a run lives, and deploy
--check takes no lock beside a run of the whole fleet; vault --rotate and host <name> are refused through exclusive
while a run holds the fleet's lock, and are no runs, which runs.run refuses before anything is made; a write under
store.locked, settings' and a vault's put, takes no lock of a run, goes on beside a run of the whole fleet, and waits
for another such write. The short sections are named once, in the order they nest; the table holds no control, and no
word makes a run take no lock. Each run here is a child process, started as test_aw_locks.py starts one.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from aw_kit import environment
from harness import REPO
from test_aw_locks import LIB, Run, _named, runs_started

from claude_vm import runs, store
from claude_vm.cli import before, habits, inverse, parse
from claude_vm.fleet import paths
from claude_vm.runs import lock_sets, locks
from claude_vm.schema import words
from claude_vm.schema.words import BEFORE, VERBS

# Each verb's kind, where its column gives it whatever its words.
KINDS_OF = {
    "help": "none",
    "init": "fleet",
    "install": "fleet",
    "code": "machines",
    "login": "machines",
    "status": "none",
    "settings": "none",
    "destroy": "machines",
    "release": "machines",
    "logs": "none",
    "cancel": "none",
    "mcp": "none",
}
# The verbs whose kind is worked out from their words, each with the words that matter and the kind they come to.
WORKED_OUT = [
    (["deploy"], "machines"),
    (["deploy", "alpha", "beta"], "machines"),
    (["deploy", "--check"], "none"),
    (["deploy", "alpha", "--check"], "none"),
    (["deploy", "--only", "template"], "machines"),
    (["deploy", "--only", "provision"], "machines"),
    (["deploy", "alpha", "--only", "configure"], "machines"),
    (["host"], "none"),
    (["host", "--json"], "none"),
    (["host", "nuc"], "fleet"),
    (["host", "nuc", "--remove"], "fleet"),
    (["vault"], "none"),
    (["vault", "alpha"], "none"),
    (["vault", "alpha", "--remove"], "none"),
    (["vault", "--list"], "none"),
    (["vault", "--rotate"], "fleet"),
]

# A lock held by a process of the test's under store.locked, as a write of the fleet's files holds it, until the test
# closes standard input.
LOCKED = r"""
import sys
sys.path.insert(0, sys.argv[1])
from claude_vm import store
with store.locked(sys.argv[2]):
    print("locked", flush=True)
    sys.stdin.read()
"""


def _handed(typed: list[str]) -> dict[str, object]:
    """A verb's values as the dispatch hands them to its handler: read, the habits applied, the run's own words out."""
    reading = parse.read(typed)
    values, _ = habits.names(VERBS[reading.verb], reading.values)
    return before.run_words(reading.before, values, {})[1]


@pytest.fixture
def state(home: Path) -> Path:
    """The state folder of the test's own fleet home, as fleet/paths.py names it."""
    return paths.state(paths.home(REPO))


@pytest.fixture
def start(home: Path, state: Path, tmp_path: Path) -> Iterator[Callable[..., Run]]:
    """Starts a run in a child process, as test_aw_locks.py's start does."""
    with runs_started(home, state, tmp_path) as starting:
        yield starting


def _under_way(who: str) -> str:
    return (
        f"error: runs on machines of this fleet are under way, and {who} needs the whole fleet:"
        f" `{inverse.line('logs', {})}` lists them"
    )


# ---------------------------------------------------------------- the table ----


def test_each_verb_takes_the_kind_its_column_gives_whatever_its_words() -> None:
    assert {name: verb.locks for name, verb in VERBS.items() if verb.locks != lock_sets.WORDS} == KINDS_OF
    for name, kind in KINDS_OF.items():
        assert lock_sets.locks_for(name, {}) == kind, name
    assert lock_sets.locks_for("init", _handed(["init", "--check"])) == lock_sets.FLEET
    assert lock_sets.locks_for("settings", _handed(["settings", "vm_cores", "--value=4"])) == lock_sets.NONE


@pytest.mark.parametrize(("typed", "kind"), WORKED_OUT, ids=[" ".join(typed) for typed, _ in WORKED_OUT])
def test_a_verb_s_kind_is_worked_out_from_its_words_as_the_dispatch_hands_them(typed, kind) -> None:
    assert lock_sets.locks_for(typed[0], _handed(typed)) == kind


def test_every_verb_s_column_is_one_of_four_and_every_words_row_has_its_function_here() -> None:
    assert words.LOCKS == (*lock_sets.KINDS, lock_sets.WORDS)
    assert lock_sets.KINDS == locks.KINDS == ("machines", "fleet", "none")
    assert all(verb.locks in words.LOCKS for verb in VERBS.values())
    by_words = {name for name, verb in VERBS.items() if verb.locks == lock_sets.WORDS}
    assert by_words == set(lock_sets.BY_WORDS) == {"deploy", "host", "vault"}
    for name, function in lock_sets.BY_WORDS.items():
        assert function is getattr(lock_sets, name) and function.__doc__, name
    # Every verb of WORKED_OUT is one whose column says words, and each comes to each of its kinds somewhere.
    assert {typed[0] for typed, _ in WORKED_OUT} == by_words


def test_a_verb_added_with_words_and_no_function_fails_and_a_function_that_comes_to_no_kind_is_refused(
    monkeypatch,
) -> None:
    monkeypatch.setitem(VERBS, "probe", dataclasses.replace(VERBS["status"], name="probe", locks=lock_sets.WORDS))
    with pytest.raises(LookupError, match="no function here"):
        lock_sets.locks_for("probe", {})
    monkeypatch.setitem(lock_sets.BY_WORDS, "probe", lambda values: "everything")
    with pytest.raises(ValueError, match="no kind of lock"):
        lock_sets.locks_for("probe", {})
    for wrong in ("control", "list", ""):
        with pytest.raises(ValueError, match="no verb of the table"):
            lock_sets.locks_for(wrong, {})


def test_the_table_holds_no_control_and_no_word_makes_a_run_take_no_lock() -> None:
    # control is the add-on's (H22), which adds its row with the fleet's lock; --no-lock is gone (the clean slate's
    # decision 8): with the kernel's locks nothing is left stale, and a run under it was invisible to clean.
    assert "control" not in VERBS
    assert [option.long for option in BEFORE if option.long and "lock" in option.long] == []
    assert "no_lock" not in {field.name for field in dataclasses.fields(before.RunWords)}
    assert "none" not in locks.POLICIES


# ---------------------------------------------------------------- the short sections ----


def test_the_short_sections_are_named_once_in_the_order_they_nest() -> None:
    assert list(lock_sets.SECTIONS) == [lock_sets.NEXTID, lock_sets.TEMPLATE] == ["nextid", "template"]
    assert all(why.strip() for why in lock_sets.SECTIONS.values())
    assert lock_sets.section_name(lock_sets.NEXTID, "pve") == "nextid-pve"
    assert lock_sets.section_name(lock_sets.TEMPLATE, "nuc") == "template-nuc"
    for word, host in (("desk", "pve"), ("vmid", "pve"), ("template", "PVE"), ("template", "a@b"), ("nextid", "")):
        with pytest.raises(ValueError):
            lock_sets.section_name(word, host)
    # Each is a section's name locks.section takes, and no word that is a kind or a lock file of a run's is one.
    assert all(locks.SECTION.fullmatch(lock_sets.section_name(word, "pve")) for word in lock_sets.SECTIONS)
    assert not set(lock_sets.SECTIONS) & {*lock_sets.KINDS, locks.FLEET, locks.MACHINE.rstrip("-")}


# ---------------------------------------------------------------- the kinds, as runs take them ----


def test_deploy_only_template_takes_the_whole_fleet_and_deploy_check_goes_on_beside_it(start, state) -> None:
    template = _handed(["deploy", "--only", "template"])
    alpha = start("alpha", machines=[("pve", "alpha")], steps=[{}])
    alpha.first("taken")
    # Refused while any run lives, as today's one lock refuses it.
    refused = start("refused", verb="deploy", values=template, command="aw deploy --only=template")
    said = refused.first("refused")
    assert (said["refused"], said["lock"]) == (_under_way("this run"), "fleet")
    assert refused.wait() == 1
    alpha.go(0)
    assert alpha.wait() == 0
    whole = start("whole", verb="deploy", values=template, command="aw deploy --only=template", steps=[{}])
    held = whole.first("taken")
    assert held["locks"] == ["fleet:exclusive"]
    # A dry run takes no lock, and goes on beside it; a run on a machine is refused, naming it.
    dry = start("dry", verb="deploy", values=_handed(["deploy", "alpha", "--check"]), machines=[("pve", "alpha")])
    assert dry.first("taken")["locks"] == [] and dry.wait() == 0
    named = start("named", machines=[("pve", "alpha")])
    holder = _named(held["taken"], "aw deploy --only=template", state)
    assert named.first("refused")["refused"] == f"error: the fleet is {holder}"
    assert named.wait() == 1
    whole.go(0)
    assert whole.wait() == 0


def test_vault_rotate_and_host_name_are_refused_through_exclusive_while_a_run_holds_the_fleet(start, state) -> None:
    alpha = start("alpha", machines=[("pve", "alpha")], steps=[{}])
    alpha.first("taken")
    held_before = dict(locks.locks_held())
    for typed in (["vault", "--rotate"], ["host", "nuc"], ["host", "nuc", "--remove"]):
        verb, values = typed[0], _handed(typed)
        # No run: its own handler holds the fleet's lock for its one write, and makes no job.
        assert lock_sets.locks_for(verb, values) == lock_sets.FLEET and not VERBS[verb].changes
        command = inverse.line(verb, values)
        with pytest.raises(locks.Held) as refused, locks.exclusive(state, command):
            pytest.fail(f"{command} took the fleet for its write beside a run")
        assert refused.value.sentence == _under_way(f"`{command}`")
    assert locks.locks_held() == held_before
    alpha.go(0)
    assert alpha.wait() == 0
    # With no run, the write holds the fleet: a run is refused meanwhile, and goes on after it.
    with locks.exclusive(state, "aw vault --rotate"):
        assert start("during", machines=[("pve", "alpha")]).wait() == 1
    after = start("after", machines=[("pve", "alpha")])
    assert after.first("taken")["locks"] == ["fleet:shared", "machine-pve@alpha:exclusive"]
    assert after.wait() == 0


def test_a_verb_whose_run_is_no_job_is_refused_by_runs_run_before_anything_is_made(state) -> None:
    def body(ctx: object) -> None:
        pytest.fail("the body of a verb that is no run")

    for verb, values in (("host", {"name": "nuc"}), ("vault", {"rotate": True}), ("status", {}), ("control", {})):
        draft = runs.Draft(state / paths.JOBS, verb, f"aw {verb}", None, [], {"kind": "terminal"}, True, values=values)
        with pytest.raises(ValueError, match="no verb whose run is a job"):
            runs.run(draft, body)  # type: ignore[arg-type]
    assert not (state / paths.JOBS).exists()


def test_settings_writes_and_a_vault_s_put_take_no_lock_of_a_run_and_wait_on_store_locked(home, start, state) -> None:
    for typed in (
        ["settings", "vm_cores", "--value=4"],
        ["settings", "vm_cores", "--reset"],
        ["vault", "alpha"],
        ["vault", "alpha", "--remove"],
    ):
        assert lock_sets.locks_for(typed[0], _handed(typed)) == lock_sets.NONE, typed
    # A run of the whole fleet holds the fleet's lock exclusively: a write under store.locked goes on beside it.
    whole = start("whole", kind="fleet", command="aw init", steps=[{}])
    assert whole.first("taken")["locks"] == ["fleet:exclusive"]
    fleet = paths.fleet_dir(paths.home(REPO))
    fleet.mkdir(mode=0o700, parents=True, exist_ok=True)
    written = fleet / "written"
    with store.locked(written):
        store.replace(written, "vm_cores: 4\n", 0o600)
    assert written.read_text() == "vm_cores: 4\n"
    # Two such writes wait for each other: the second goes on once the first has let go.
    holder = subprocess.Popen(
        [sys.executable, "-I", "-c", LOCKED, str(LIB), str(written)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=environment(home),
    )
    entered = threading.Event()

    def second() -> None:
        with store.locked(written):
            entered.set()

    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "locked"
        waiting = threading.Thread(target=second, daemon=True)
        waiting.start()
        assert not entered.wait(0.5), "a second write went on while the first held its lock"
        assert holder.stdin is not None
        holder.stdin.close()
        assert holder.wait(timeout=30) == 0
        assert entered.wait(30)
        waiting.join(30)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=30)
        for stream in (holder.stdin, holder.stdout):
            if stream is not None and not stream.closed:
                stream.close()
    whole.go(0)
    assert whole.wait() == 0
