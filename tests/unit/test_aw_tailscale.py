"""Tailscale's join, layer 2's step vpn-join (lib/claude_vm/guest/layer2/tailscale.py), and its package, layer 1's
part tailscale (roles/tailscale/tasks/system.yml; docs/architecture/engine.md §16.10).

Every machine is F30's stand-in (tests/unit/aw_machine.py), reached through B3's runner and the stand-in ssh, as root by
its sudo, its tailscale tests/molecule/fake_tailscale.sh, the node's state files in the machine's home; the keys
test_tailscale.py's canned ones, never a real one. test_tailscale.py is unchanged and still holds today's role.

- Its cases of the join, carried against the step: a first join sends the key once, on tailscale up's standard input,
  and records the node key of the login it made, and a second run sends nothing; a VM whose record says it joined
  before is not sent the key, and under the method key that fails, naming why; a refused key is a report, the marker
  gone, the key not sent again; an OAuth client's secret with no tags is never sent; an expired login made by hand is
  left alone; a Tailscale the role did not install, by dpkg or outside it, is reported and left; off is left as it is,
  nothing logged out and nothing removed; the settings of the role's own login are held by one tailscale set; an up
  that ends Unreachable sends the key never again in its run, and what follows is decided from a settled state; a join
  that ran out of time is marked pending, an earlier refusal still in Health not taken for its own, and a refusal
  named only in Health after the timeout is one; a join waiting for an admin is recorded, and a name already taken,
  a node still starting and a daemon that does not answer are said; a join still under way is recorded only from a
  sign-in, never from a node key alone; and a run that hands no record fails by name before the machine is read.
- The package: the system entry reads what is installed and installs only where Tailscale is the VPN and the role's,
  with the log switch's drop-in written before the package; the play reaches it by the part tailscale alone, and no
  image does.
- The invariants with tests of their own: the key reaches no argument, file, event or difference, and joins the run's
  redaction list before anything is read; one function reads its value; every scenario and harness that runs the role
  keeps the fake first on PATH, molecule/system/ and F30's stand-in among them.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import threading
from pathlib import Path

import pytest
import yaml
from aw_machine import TOOLS, Machine, filed, machine, machine_path, ssh_stand_in, target
from harness import FAKES, REPO
from test_tailscale import KEYS, NODE

from claude_vm.fleet import passthrough, registry, secrets
from claude_vm.guest import system, wiring
from claude_vm.guest.layer2 import step, tailscale
from claude_vm.guest.layer2.tailscale import VpnJoin
from claude_vm.runs import job as jobs
from claude_vm.secret import Secret
from claude_vm.ssh import runner

SOURCE = REPO / "lib" / "claude_vm" / "guest" / "layer2" / "tailscale.py"
ROLE = REPO / "roles" / "tailscale"
STEP = "vpn-join"
ON_STDIN = "[key on stdin]"
RESENT = "[key resent to a node with a login]"
UP = "up --auth-key=file:/dev/stdin"


@pytest.fixture
def job(home: Path):
    """A job of this process's own, as a run that starts the runner's children has; dropped after the test."""
    from claude_vm.fleet import paths

    made = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", None, ["alpha"], {"kind": "terminal"}, True
    )
    yield made
    jobs.drop(made)


@pytest.fixture
def reach(tmp_path, engine_guard, monkeypatch):
    """A maker of stand-in machines, each reached through the stand-in ssh, first on this test's PATH: the node's
    state seeded from ts (fake_tailscale.sh's files), the role's joined marker of marker (a node key's hex, or
    "pending"), the role's installed marker unless ours is false, the package unless package is false, and a Tailscale
    outside dpkg where foreign. The fake's state is the one folder whether a command runs as the account or as root."""
    root = tmp_path / "machines"
    made: list[Machine] = []
    foreign = tmp_path / "foreign"
    monkeypatch.setattr(tailscale, "FOREIGN", (str(foreign / "tailscaled"),))
    engine_guard.first_on_path(ssh_stand_in(root, made))

    def make(
        name: str = "alpha",
        *,
        ts: dict | None = None,
        marker: str | None = None,
        ours: bool = True,
        package: bool = True,
        outside: bool = False,
    ) -> Machine:
        one = machine(root, name, ts=ts or {}, marker=None if marker == tailscale.PENDING else marker)
        filed(one)
        (one.root_home / ".tailscale-fake").symlink_to(one.home / ".tailscale-fake")
        if marker == tailscale.PENDING:
            (_markers(one) / tailscale.JOINED).write_text("pending\n")
        if not ours:
            (_markers(one) / tailscale.INSTALLED).unlink()
        if not package:
            (one.home / "dpkg-status").unlink()
        if outside:
            foreign.mkdir(exist_ok=True)
            (foreign / "tailscaled").write_text("")
        made.append(one)
        ssh_stand_in(root, made)
        return one

    return make


def _markers(made: Machine) -> Path:
    return made.home / "markers"


def _joined(made: Machine) -> str | None:
    path = _markers(made) / tailscale.JOINED
    return path.read_text().strip() if path.exists() else None


def _state(made: Machine, name: str) -> str | None:
    path = made.home / ".tailscale-fake" / name
    return path.read_text() if path.exists() else None


def _ts_calls(made: Machine) -> list[str]:
    path = made.home / ".tailscale-fake" / "calls.log"
    return path.read_text().splitlines() if path.exists() else []


def _settings(made: Machine, key: str | None = None, **over: object) -> dict[str, object]:
    return {
        "vpn": "tailscale",
        "tailscale_auth_method": "auto",
        "tailscale_ssh": False,
        "tailscale_accept_routes": False,
        "tailscale_accept_dns": False,
        "tailscale_advertise_tags": (),
        "tailscale_login_server": "",
        "tailscale_hostname": "",
        "tailscale_up_args": (),
        "vm_label": "claude",
        "tailscale_marker_dir": str(_markers(made)),
        "vm_name": made.name,
        "tailscale_joined_before": False,
        "tailscale_sign_in_follows": False,
        **({} if key is None else {"tailscale_auth_key": Secret(key)}),
        **over,
    }


def _ctx(job: jobs.Job, made: Machine, settings) -> step.Context:
    return step.Context(
        job, target(made), made.identity, settings, dict(os.environ), threading.Event(), become_exe="sudo"
    )


def _run(job: jobs.Job, made: Machine, key: str | None = None, *, joined: VpnJoin | None = None, **over) -> step.Ran:
    joined = VpnJoin() if joined is None else joined
    return asyncio.run(step.run_steps(_ctx(job, made, _settings(made, key, **over)), [joined]))


def _heard(job: jobs.Job) -> list[jobs.Event]:
    heard: list[jobs.Event] = []
    job.listen(heard.append)
    return heard


def _said(heard: list[jobs.Event]) -> tuple[list[str], dict[str, str]]:
    """The reports said, and each NEXT STEPS matter's last line."""
    reports = [event.text for event in heard if event.kind == "report"]
    nexts = {event.data["matter"]: event.text for event in heard if event.kind == "next"}
    return reports, nexts


def _leaks(job: jobs.Job, heard: list[jobs.Event], *machines: Machine) -> list[str]:
    """Every canned key found in an event, a file of the run's folder, a call of the stand-in ssh, sudo or tailscale,
    or a file of the machine's but the node's own key file."""
    places = {"events": json.dumps([[event.text, dict(event.data)] for event in heard])}
    for path in sorted(job.folder.rglob("*")):
        if path.is_file():
            places[str(path)] = path.read_bytes().decode("utf-8", "replace")
    for made in machines:
        places[f"{made.name} ssh"] = json.dumps(made.calls())
        places[f"{made.name} sudo"] = json.dumps(made.sudo_calls())
        for path in sorted(made.directory.rglob("*")):
            if path.is_file() and not path.is_symlink():
                places[str(path)] = path.read_bytes().decode("utf-8", "replace")
    return [f"{name} in {where}" for where, said in places.items() for name, key in KEYS.items() if key in said]


# ------------------------------------------------------------------ the join ----


def test_a_first_join_sends_the_key_once_records_the_login_and_a_second_run_sends_nothing(job, reach):
    made = reach()
    heard = _heard(job)
    joined = VpnJoin()
    assert _run(job, made, KEYS["good"], joined=joined) == step.Ran("succeeded", ((STEP, True),))
    calls = _ts_calls(made)
    assert calls.count(ON_STDIN) == 1 and RESENT not in calls, calls
    [up] = [call for call in calls if call.startswith("up ")]
    flags = "--reset --hostname=alpha --ssh=false --accept-routes=false --accept-dns=false"
    assert up == f"{UP} --timeout={tailscale.JOIN_SECONDS}s {flags}"
    # The role's own login, by the node key status says, written once the state was read again.
    assert _joined(made) == f"nodekey:{_state(made, 'nodekey')}"
    assert joined.seen == {made.identity: True}
    reports, nexts = _said(heard)
    assert len(reports) == 1 and reports[0].startswith("alpha: joined Tailscale "), reports
    assert reports[0].endswith(" as alpha.tail1234.ts.net (100.101.102.103)."), reports
    assert nexts == {"tailscale": ""}
    # The second run, its record now saying it joined, reads a node with a login, its own: nothing sent, nothing set.
    again = VpnJoin()
    assert _run(job, made, KEYS["good"], joined=again, tailscale_joined_before=True) == step.Ran(
        "succeeded", ((STEP, False),)
    )
    later = _ts_calls(made)[len(calls) :]
    assert _ts_calls(made).count(ON_STDIN) == 1 and not [call for call in later if call.startswith(("up", "set"))]
    assert again.seen == {made.identity: True}
    last = _said(heard)[0][-1]
    assert last.startswith("alpha: on Tailscale "), last
    assert last.endswith(" as alpha.tail1234.ts.net (100.101.102.103)."), last


def test_a_node_with_a_login_is_never_sent_the_key_whatever_its_record_says(job, reach):
    # Its record lost (joined_before false), a node with a login of the role's: HaveNodeKey alone holds the key back.
    made = reach(ts={"nodekey": NODE, "state": "Running", "prefs-hostname": "alpha"}, marker=NODE)
    assert _run(job, made, KEYS["good"]).outcome == "succeeded"
    assert ON_STDIN not in _ts_calls(made) and RESENT not in _ts_calls(made)


@pytest.mark.parametrize("method", ["auto", "web"])
def test_a_vm_that_joined_before_is_not_sent_the_key_again(job, reach, method):
    made = reach()
    heard = _heard(job)
    joined = VpnJoin()
    assert _run(job, made, KEYS["good"], joined=joined, tailscale_joined_before=True, tailscale_auth_method=method) == (
        step.Ran("succeeded", ((STEP, False),))
    )
    assert not [call for call in _ts_calls(made) if call.startswith("up")]
    assert _joined(made) is None and joined.seen == {made.identity: False}
    reports, nexts = _said(heard)
    if method == "auto":
        assert reports == [
            "alpha: Tailscale is installed but not signed in. aw login alpha --only tailscale signs it in from your"
            " browser; vault_tailscale_auth_key is not sent again to a VM that joined before."
        ]
    assert nexts["tailscale"] == reports[0]


def test_under_the_method_key_a_vm_that_joined_before_fails_naming_why(job, reach):
    made = reach()
    ran = _run(job, made, KEYS["good"], tailscale_joined_before=True, tailscale_auth_method="key")
    assert ran.outcome == "failed"
    assert "had a Tailscale login before, as its record in Proxmox keeps" in ran.why
    assert "aw login alpha --only tailscale" in ran.why
    assert not [call for call in _ts_calls(made) if call.startswith("up")]


@pytest.mark.parametrize(
    ("key", "why"),
    [
        ("expired", "invalid key: API key does not exist"),
        ("made-up", "invalid key: unable to validate API key"),
    ],
)
def test_a_refused_key_is_a_report_sent_once_and_its_marker_gone(job, reach, key, why):
    made = reach()
    heard = _heard(job)
    joined = VpnJoin()
    given = KEYS.get(key, "tskey-auth-kMadeUp0CNTRL-TestOnly00000000000000")
    assert _run(job, made, given, joined=joined) == step.Ran("succeeded", ((STEP, False),))
    assert _ts_calls(made).count(ON_STDIN) == 1
    assert _joined(made) is None and joined.seen == {made.identity: False}
    reports, nexts = _said(heard)
    assert reports == [
        f"alpha: Tailscale refused vault_tailscale_auth_key ({why}) - it has expired, been used up or been revoked. Put"
        " a new one in the vault (reusable, pre-approved, tagged) and run aw deploy alpha --tags tailscale, or sign in"
        " by the browser: aw login alpha --only tailscale"
    ]
    assert nexts == {"tailscale": reports[0]}
    assert given not in json.dumps(reports)


def test_an_oauth_client_s_secret_with_no_tags_is_never_sent(job, reach):
    made = reach()
    heard = _heard(job)
    assert _run(job, made, KEYS["client"]).outcome == "succeeded"
    assert not [call for call in _ts_calls(made) if call.startswith("up")]
    assert "an OAuth client secret needs the tags it may apply" in _said(heard)[0][0]
    # With its tags, it is sent: the stand-in joins it as it joins any key that is not one of the canned refusals.
    other = reach("beta")
    _run(job, other, KEYS["client"], tailscale_advertise_tags=("tag:claude-vm",), vm_name="beta")
    assert _ts_calls(other).count(ON_STDIN) == 1


def test_an_expired_login_made_by_hand_is_left_alone(job, reach):
    made = reach(ts={"expired": NODE})
    heard = _heard(job)
    joined = VpnJoin()
    assert _run(job, made, KEYS["good"], joined=joined) == step.Ran("succeeded", ((STEP, False),))
    assert [call.split(" ")[0] for call in _ts_calls(made)] == ["status"]
    # A login on the Tailscale the role installed, any, is seen, as today's tailscale_login_seen says: the record keeps
    # that it had one, so that its key is never sent there again.
    assert _joined(made) is None and joined.seen == {made.identity: True}
    reports, _ = _said(heard)
    assert reports == [
        "alpha: signed out of Tailscale (its key expired). It was signed in outside this project, so it is left as it"
        " is: sign it in again on the VM, the way it was signed in."
    ]


def test_the_role_s_own_expired_login_is_seen_and_not_sent_the_key(job, reach):
    made = reach(ts={"expired": NODE}, marker=NODE)
    joined = VpnJoin()
    assert _run(job, made, KEYS["good"], joined=joined).outcome == "succeeded"
    assert ON_STDIN not in _ts_calls(made) and joined.seen == {made.identity: True}


@pytest.mark.parametrize("how", ["dpkg", "outside"])
def test_a_tailscale_the_role_did_not_install_is_reported_and_left(job, reach, how):
    if how == "dpkg":
        made = reach(ours=False, ts={"nodekey": NODE, "state": "Running"})
    else:
        made = reach(ours=False, package=False, outside=True)
    heard = _heard(job)
    joined = VpnJoin()
    assert _run(job, made, KEYS["good"], joined=joined) == step.Ran("succeeded", ((STEP, False),))
    assert [call for call in _ts_calls(made) if not call.startswith(("status", "debug"))] == []
    assert joined.seen == {made.identity: False} and _joined(made) is None
    reports, nexts = _said(heard)
    assert reports[0].startswith("alpha: ") and reports[0].endswith("outside this project, so it is left as it is.")
    assert nexts == {"tailscale": ""}


def test_off_leaves_tailscale_as_it_is_and_logs_nothing_out(job, reach):
    made = reach(ts={"nodekey": NODE, "state": "Running"}, marker=NODE)
    heard = _heard(job)
    joined = VpnJoin()
    before = sorted(path.name for path in _markers(made).iterdir())
    assert _run(job, made, KEYS["good"], joined=joined, vpn="none") == step.Ran("succeeded", ((STEP, False),))
    # Not even read: no status, no logout, no down; the markers and the package as they were; the record not written.
    assert _ts_calls(made) == []
    assert sorted(path.name for path in _markers(made).iterdir()) == before
    assert (made.home / "dpkg-status").read_text() == "installed" and _state(made, "nodekey") == NODE
    assert joined.seen == {}
    reports, _ = _said(heard)
    assert reports == [
        "alpha: vpn is not tailscale, so the Tailscale this project installed here is left as it is, unmanaged: no run"
        ' changes it until vpn is tailscale again. Taking it off is by hand ("Turning it off" in'
        " docs/guides/tailscale.md)."
    ]


def test_the_settings_of_the_role_s_own_login_are_held_by_one_tailscale_set(job, reach):
    made = reach(ts={"nodekey": NODE, "state": "Running", "prefs-hostname": "alpha", "prefs-dns": "false"}, marker=NODE)
    heard = _heard(job)
    assert _run(job, made, None, tailscale_ssh=True) == step.Ran("succeeded", ((STEP, True),))
    sets = [call for call in _ts_calls(made) if call.startswith("set")]
    assert sets == ["set --ssh=true --accept-risk=lose-ssh"]
    assert _state(made, "prefs-ssh") == "true"
    assert _said(heard)[0][0].endswith("Changed: tailscale set --ssh=true --accept-risk=lose-ssh.")
    # A login made by hand is never set.
    other = reach("beta", ts={"nodekey": NODE, "state": "Running", "prefs-hostname": "beta"})
    _run(job, other, None, tailscale_ssh=True, vm_name="beta")
    assert not [call for call in _ts_calls(other) if call.startswith("set")]


def test_a_sign_in_that_ended_after_its_run_is_recorded_as_the_role_s(job, reach):
    made = reach(ts={"nodekey": NODE, "state": "Running", "prefs-hostname": "alpha"}, marker=tailscale.PENDING)
    joined = VpnJoin()
    assert _run(job, made, KEYS["good"], joined=joined) == step.Ran("succeeded", ((STEP, True),))
    assert _joined(made) == f"nodekey:{NODE}" and joined.seen == {made.identity: True}
    assert ON_STDIN not in _ts_calls(made)


@pytest.mark.parametrize(
    ("ts", "adopted"),
    [
        ({"nodekey": NODE, "state": "NeedsMachineAuth", "prefs-hostname": "alpha"}, True),
        # HaveNodeKey alone, its login expired: one made by hand under the marker would be taken for the role's.
        ({"expired": NODE}, False),
    ],
    ids=["needs-machine-auth", "node-key-alone"],
)
def test_a_join_still_under_way_is_recorded_only_from_a_sign_in_never_from_a_node_key_alone(job, reach, ts, adopted):
    made = reach(ts=ts, marker=tailscale.PENDING)
    joined = VpnJoin()
    assert _run(job, made, KEYS["good"], joined=joined) == step.Ran("succeeded", ((STEP, adopted),))
    assert _joined(made) == (f"nodekey:{NODE}" if adopted else tailscale.PENDING)
    assert joined.seen == {made.identity: True}
    assert ON_STDIN not in _ts_calls(made) and RESENT not in _ts_calls(made)


@pytest.fixture
def quick(monkeypatch):
    """A join's timeout of one second, which the fake's slow and late keys, and a join waiting for an admin, sleep; a
    settle of two reads with no pause between them."""
    monkeypatch.setattr(tailscale, "JOIN_SECONDS", 1)
    monkeypatch.setattr(tailscale, "SETTLE_SECONDS", 2)
    monkeypatch.setattr(tailscale, "PAUSE", 0.0)


LATE = (
    "alpha: Tailscale did not join with vault_tailscale_auth_key (timeout waiting for Tailscale service to enter a"
    ' Running state; check health with "tailscale status"). aw deploy alpha --tags tailscale tries again, or sign in by'
    " the browser: aw login alpha --only tailscale"
)


@pytest.mark.parametrize(
    "ts",
    [{}, {"loginerror": "invalid key: API key does not exist"}],
    ids=["late", "stale-error"],
)
def test_a_join_that_ran_out_of_time_is_still_under_way_and_marked_pending(job, reach, quick, ts):
    # tailscaled goes on with a join whose --timeout ran out, and the login it ends in is the role's. An earlier
    # key's refusal still in Health is not this join's: read as one, the late join would be forgotten, and the login
    # it ends in taken for one made by hand.
    made = reach(ts=ts)
    heard = _heard(job)
    joined = VpnJoin()
    assert _run(job, made, KEYS["late"], joined=joined).outcome == "succeeded"
    assert _ts_calls(made).count(ON_STDIN) == 1
    assert _joined(made) == tailscale.PENDING and joined.seen == {made.identity: True}
    reports, nexts = _said(heard)
    assert reports == [LATE] and nexts == {"tailscale": LATE}


def test_a_refusal_named_only_in_health_after_a_timeout_is_a_refusal_and_its_marker_gone(job, reach, quick):
    # Under way by a pending marker of an earlier run, the node with no login is sent the key; the slow key's
    # refusal comes back as the timeout, its words in Health alone.
    made = reach(marker=tailscale.PENDING)
    heard = _heard(job)
    joined = VpnJoin()
    assert _run(job, made, KEYS["slow"], joined=joined).outcome == "succeeded"
    assert _ts_calls(made).count(ON_STDIN) == 1
    assert _joined(made) is None and joined.seen == {made.identity: False}
    [report] = _said(heard)[0]
    assert report.startswith(
        "alpha: Tailscale refused vault_tailscale_auth_key (invalid key: API key does not exist) - it has expired,"
    ), report


def test_waiting_for_an_admin_is_recorded_and_said(job, reach, quick):
    made = reach(ts={"needs-approval": ""})
    heard = _heard(job)
    joined = VpnJoin()
    assert _run(job, made, KEYS["good"], joined=joined) == step.Ran("succeeded", ((STEP, True),))
    assert _joined(made) == f"nodekey:{_state(made, 'nodekey')}" and joined.seen == {made.identity: True}
    assert _said(heard)[0] == [
        "alpha: signed in to Tailscale and waiting for an admin to approve it at"
        " https://login.tailscale.com/admin/machines; it connects by itself once approved."
    ]


def test_a_name_already_taken_is_said_with_the_admin_page(job, reach):
    made = reach(ts={"name-taken": ""})
    heard = _heard(job)
    assert _run(job, made, KEYS["good"]) == step.Ran("succeeded", ((STEP, True),))
    [report] = _said(heard)[0]
    assert " as alpha-1.tail1234.ts.net (100.101.102.103)." in report, report
    assert report.endswith(
        "A device named alpha is already in your tailnet, likely the VM this one replaced: delete it at"
        " https://login.tailscale.com/admin/machines, then rename this one to alpha there."
    ), report


def test_a_node_still_starting_is_reported_not_changed(job, reach, quick):
    # The role's own login, its hostname drifted: nothing is set, and no key sent, until it is settled.
    running = {"nodekey": NODE, "state": "Running", "prefs-touched": "", "prefs-loggedout": "false"}
    made = reach(ts={**running, "prefs-wantrunning": "true", "prefs-hostname": "drifted", "starting": ""}, marker=NODE)
    heard = _heard(job)
    assert _run(job, made, KEYS["good"]) == step.Ran("succeeded", ((STEP, False),))
    assert [call for call in _ts_calls(made) if call.startswith(("set", "up"))] == []
    assert _said(heard)[0] == [
        "alpha: Tailscale is still connecting (Starting) after 2 seconds, so nothing was changed. aw deploy alpha"
        " --tags tailscale reads it again."
    ]


def test_a_daemon_the_role_started_that_does_not_answer_fails_by_name(job, reach, quick):
    made = reach(ts={"daemon-down": ""})
    ran = _run(job, made, KEYS["good"])
    assert ran.outcome == "failed"
    assert ran.why.endswith(
        "tailscaled is not running on alpha (failed to connect to local tailscaled; it doesn't appear to be running);"
        " journalctl -u tailscaled on it says why."
    ), ran.why
    assert ON_STDIN not in _ts_calls(made)


def test_a_state_still_settling_is_read_again_until_it_is_settled(job, reach, monkeypatch):
    monkeypatch.setattr(tailscale, "PAUSE", 0.01)
    made = reach(ts={"nostate-reads": "3"})
    assert _run(job, made, KEYS["good"]).outcome == "succeeded"
    calls = _ts_calls(made)
    # Three NoState reads, the settled one, and only then the key.
    assert calls.index(next(call for call in calls if call.startswith("up"))) >= 4, calls
    assert calls.count(ON_STDIN) == 1


def test_a_state_that_never_settles_sends_nothing(job, reach, monkeypatch):
    monkeypatch.setattr(tailscale, "PAUSE", 0.0)
    monkeypatch.setattr(tailscale, "SETTLE_SECONDS", 2)
    made = reach(ts={"nostate-reads": "50"})
    heard = _heard(job)
    assert _run(job, made, KEYS["good"]).outcome == "succeeded"
    assert not [call for call in _ts_calls(made) if call.startswith("up")]
    assert "Tailscale is still connecting (NoState) after 2 seconds, so nothing was changed." in _said(heard)[0][0]


@pytest.mark.parametrize("ran", [True, False], ids=["up-ran", "up-never-ran"])
def test_an_up_that_ends_unreachable_is_never_sent_again_in_its_run(job, reach, monkeypatch, ran):
    real = tailscale._run
    ups: list[list[str]] = []

    async def lost(ctx, words, limits=tailscale.QUICK, stdin=b""):
        if list(words[:3]) == list(tailscale.UP):
            ups.append(list(words))
            if ran:
                await real(ctx, words, limits, stdin)
            return runner.Unreachable("Connection to the machine closed by remote host.")
        return await real(ctx, words, limits, stdin)

    monkeypatch.setattr(tailscale, "_run", lost)
    made = reach()
    joined = VpnJoin()
    result = _run(job, made, KEYS["good"], joined=joined)
    assert len(ups) == 1
    assert _ts_calls(made).count(ON_STDIN) == (1 if ran else 0)
    if ran:
        # The state read again: the login there is the role's, and recorded.
        assert result == step.Ran("succeeded", ((STEP, True),))
        assert _joined(made) == f"nodekey:{_state(made, 'nodekey')}" and joined.seen == {made.identity: True}
    else:
        assert result.outcome == "failed" and "the key is not sent again in this run" in result.why
        assert joined.seen == {made.identity: False}
        # The next run decides from a settled state, as any first join: NeedsLogin, no node key, never joined.
        monkeypatch.setattr(tailscale, "_run", real)
        assert _run(job, made, KEYS["good"]).outcome == "succeeded"
        assert _ts_calls(made).count(ON_STDIN) == 1


# ------------------------------------------------------------------ refused before the machine is read ----


@pytest.mark.parametrize("vpn", ["tailscale", "none"])
def test_a_run_that_hands_no_record_fails_by_name_before_the_machine_is_read(job, reach, vpn):
    # Read as "never joined", a missing record would send the fleet's key to any node that says it has no login.
    made = reach()
    settings = _settings(made, KEYS["good"], vpn=vpn)
    del settings["tailscale_joined_before"]
    ran = asyncio.run(step.run_steps(_ctx(job, made, settings), [VpnJoin()]))
    assert ran.outcome == "failed" and "the run's settings hold no tailscale_joined_before" in ran.why, ran
    assert made.calls() == [] and _ts_calls(made) == []


@pytest.mark.parametrize(
    ("over", "said"),
    [
        ({"tailscale_hostname": "not_a_label"}, "which Tailscale refuses as a machine name"),
        ({"tailscale_auth_method": "key"}, "tailscale_auth_method is key, but tailscale_auth_key is empty"),
        ({"tailscale_auth_method": "password"}, "not one of auto, key, web"),
    ],
)
def test_settings_tailscale_cannot_take_are_refused_before_the_machine_is_read(job, reach, over, said):
    made = reach()
    ran = _run(job, made, None, **over)
    assert ran.outcome == "failed" and said in ran.why
    assert made.calls() == [] and _ts_calls(made) == []


def test_a_machine_without_the_package_fails_naming_layer_1_s_part(job, reach):
    made = reach(package=False)
    ran = _run(job, made, KEYS["good"])
    assert ran.outcome == "failed" and "layer 1's tailscale part installs" in ran.why
    assert "aw deploy alpha --tags tailscale" in ran.why and _ts_calls(made) == []


def test_the_check_says_what_would_change_and_changes_nothing(job, reach):
    made = reach()
    [checked] = asyncio.run(step.check_steps(_ctx(job, made, _settings(made, KEYS["good"])), [VpnJoin()]))
    assert (checked.name, checked.part) == (STEP, "tailscale")
    assert "joined with tailscale_auth_key" in repr(checked)
    assert KEYS["good"] not in repr(checked)
    assert not [call for call in _ts_calls(made) if call.startswith(("up", "set"))] and _joined(made) is None


# ------------------------------------------------------------------ the key ----


def test_the_key_joins_the_run_s_redaction_list_before_anything_is_read(job, reach, monkeypatch):
    held, read = [], tailscale.read
    key = Secret(KEYS["good"])

    async def reading(ctx, want):
        held.append(any(each is key for each in ctx.job.redacted))
        return await read(ctx, want)

    monkeypatch.setattr(tailscale, "read", reading)
    made = reach()
    settings = {**_settings(made), "tailscale_auth_key": key}
    assert asyncio.run(step.run_steps(_ctx(job, made, settings), [VpnJoin()])).outcome == "succeeded"
    assert held and all(held), held


def test_no_key_reaches_an_argument_a_file_an_event_or_a_difference(job, reach, monkeypatch):
    # Every case of this file's that hands tailscale a key, run once more: the key on tailscale up's standard input
    # alone, never in calls.log's words, the run's folder, the stand-ins' calls or a file of the machine's.
    monkeypatch.setattr(tailscale, "PAUSE", 0.01)
    machines = [
        (reach("first"), KEYS["good"], {}),
        (reach("tagged"), KEYS["tagged"], {"tailscale_advertise_tags": ("tag:claude-vm",)}),
        (reach("expired"), KEYS["expired"], {}),
        (reach("denied"), KEYS["denied"], {"tailscale_advertise_tags": ("tag:nope",)}),
        (reach("client"), KEYS["client"], {}),
        (reach("before"), KEYS["good"], {"tailscale_joined_before": True}),
        (reach("hand", ts={"expired": NODE}), KEYS["good"], {}),
        (reach("theirs", ours=False), KEYS["good"], {}),
        (reach("off"), KEYS["good"], {"vpn": "none"}),
    ]
    heard = _heard(job)
    for made, key, over in machines:
        _run(job, made, key, vm_name=made.name, **over)
        [checked] = asyncio.run(
            step.check_steps(_ctx(job, made, _settings(made, key, vm_name=made.name, **over)), [VpnJoin()])
        )
        assert key not in repr(checked)
    assert _ts_calls(machines[0][0]).count(ON_STDIN) == 1
    assert _leaks(job, heard, *(made for made, _, _ in machines)) == []


def test_one_function_reads_the_key_s_value():
    tree = ast.parse(SOURCE.read_text())
    readers = [
        function.name
        for function in ast.walk(tree)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and node.attr == "reveal"
    ]
    assert readers == ["_key"]
    # And the key reaches a command as the runner's Secret alone, on its standard input: the one place it is handed on.
    text = SOURCE.read_text()
    assert text.count("want.key, ") == 1 and '[want.key, "\\n"]' in text
    assert "--auth-key=file:/dev/stdin" in text and not re.search(r"--auth-?key=(?!file:/dev/stdin)", text)


def test_the_settings_read_are_the_rows_the_option_the_secret_and_what_the_run_sets():
    every = {row.name for row in registry.rows()} | {
        option.name
        for option in passthrough.options()
        if option.through is None and option.refused != passthrough.ANY_VALUE
    }
    assert all(registry.row(name).name == name for name in tailscale.ROWS)
    named = {
        node.value
        for node in ast.walk(ast.parse(SOURCE.read_text()))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in every
    }
    run_set = {name for name in tailscale.RUN_SET if name in every}
    # The step's part is the VPN's choice too (vpn's value tailscale), and the name of its matter.
    assert named - {VpnJoin.part} == {*tailscale.ROWS, *tailscale.OPTIONS, *run_set, *({tailscale.SECRET} & every)}
    assert passthrough.option("tailscale_marker_dir").refused is None
    for name in ("tailscale_joined_before", "tailscale_sign_in_follows"):
        assert passthrough.option(name).refused == passthrough.SET_BY_A_RUN, name
    assert secrets.row(tailscale.SECRET) is not None
    assert registry.row("tailscale_auth_method").choices == tailscale.METHODS
    assert registry.row("vpn").choices == ("none", tailscale.CHOSEN)
    assert (
        tailscale.OPTIONS["tailscale_marker_dir"]
        == yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())["tailscale_marker_dir"]
    )
    role_vars = yaml.safe_load((ROLE / "vars" / "main.yml").read_text())
    assert tailscale.FOREIGN == tuple(role_vars["tailscale_foreign_paths"])


# ------------------------------------------------------------------ the package, layer 1's ----


def _tasks(name: str) -> list[dict]:
    return yaml.safe_load((ROLE / "tasks" / name).read_text())


def test_the_system_entry_reads_what_is_installed_and_installs_only_where_chosen_and_ours():
    tasks = _tasks("system.yml")
    assert [task["ansible.builtin.include_tasks"] for task in tasks] == ["ownership.yml", "install.yml"]
    assert "when" not in tasks[0]
    assert tasks[1]["when"] == ["tailscale_wanted | bool", "tailscale_ours | bool"]
    # main.yml's include of install.yml is the same, under the same conditions.
    main = {task.get("ansible.builtin.include_tasks"): task.get("when") for task in _tasks("main.yml")}
    assert main["install.yml"] == tasks[1]["when"]
    # The entry's spec, every one of its options main's too, as main has it.
    specs = yaml.safe_load((ROLE / "meta" / "argument_specs.yml").read_text())["argument_specs"]
    entry = specs["system"]["options"]
    assert set(entry) == {"tailscale_wanted", "tailscale_upload_logs", "tailscale_daemon_flags", "tailscale_marker_dir"}
    for name, spec in entry.items():
        kept = {key: value for key, value in specs["main"]["options"][name].items() if key != "description"}
        assert {key: value for key, value in spec.items() if key != "description"} == kept, name


def test_the_log_switch_is_a_drop_in_written_before_the_package_and_its_row_reaches_it():
    tasks = _tasks("install.yml")
    names = [task["name"] for task in tasks]
    drop_in = names.index("install | Keep tailscaled from uploading its logs, from its first start")
    package = names.index("install | Install Tailscale from its repository")
    assert drop_in < package
    assert "Environment=TS_NO_LOGS_NO_SUPPORT=true" in tasks[drop_in]["ansible.builtin.copy"]["content"]
    # The system entry reaches install.yml whole, and the row is the variable the drop-in reads.
    assert wiring.TABLE["tailscale"]["tailscale_upload_logs"] == wiring.Source(wiring.SETTING, "tailscale_upload_logs")


def test_the_play_reaches_the_entry_by_the_part_tailscale_alone_and_no_image_does():
    play = yaml.safe_load(system.PLAY.read_text())[0]
    [imported] = play["tasks"]
    assert imported["ansible.builtin.import_role"] == {"name": "tailscale", "tasks_from": "system"}
    assert imported["tags"] == ["tailscale"]
    assert "tailscale" not in [role["role"] for role in play["roles"]]
    # An image never carries the VPN (card F20): the role has no image entry.
    specs = yaml.safe_load((ROLE / "meta" / "argument_specs.yml").read_text())["argument_specs"]
    assert "image" not in specs and not (ROLE / "tasks" / "image.yml").exists()
    # Off never reaches the role's other files: the entry includes neither names.yml nor any of the join's.
    text = (ROLE / "tasks" / "system.yml").read_text()
    for other in ("names.yml", "state.yml", "connect.yml", "settings.yml", "report.yml", "unmanaged.yml"):
        assert other not in text.replace("main.yml's 1.x move (names.yml)", ""), other


# ------------------------------------------------------------------ every harness keeps the fake first ----


def test_f30_s_stand_in_machines_run_the_fake_tailscale_first_on_path(tmp_path):
    made = machine(tmp_path / "machines", "alpha")
    assert "tailscale" in TOOLS
    first = machine_path(made).split(os.pathsep)[0]
    assert first == str(made.bin)
    assert (made.bin / "tailscale").resolve().read_text() == (FAKES / "fake_tailscale.sh").read_text()


def test_molecule_system_runs_the_part_and_keeps_tailscale_off_the_network():
    # molecule/system/ runs playbooks/system.yml, which imports the role: a scenario of it that test_tailscale.py's
    # guard does not see, since its files name the play and not the role.
    scenario = REPO / "molecule" / "system"
    converge = ast.parse((scenario / "converge.py").read_text())
    values = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in converge.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in ("FLEET", "RUN")
    }
    fleet, run = values["FLEET"], dict(each.split("=", 1) for each in values["RUN"])
    chosen = {**fleet, **run}.get("vpn", registry.row("vpn").default)
    assert "tailscale" in system.PARTS
    if chosen == "tailscale":
        prepare = (scenario / "prepare.yml").read_text()
        assert "fake_tailscale.sh" in prepare and "dest: /usr/local/bin/tailscale" in prepare
        assert str({**fleet, **run}.get("tailscale_upload_logs")).lower() == "false"
    else:
        # Tailscale not the VPN: the entry reads what is installed, and installs nothing.
        assert chosen == "none"
